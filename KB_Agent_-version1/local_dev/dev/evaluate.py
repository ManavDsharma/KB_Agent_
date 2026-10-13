"""
Evaluate how the agent handles write requests: several fields, several
records, several people and days, missing information, and requests it must
not touch. It runs the cases in dev/eval_cases.json through the real agent
flow (router, planner, plan engine, services) against in-memory sample data,
so nothing real can be changed.

    python dev/evaluate.py                 offline: the planner model is replaced by the
                                           draft stored in each case (checks everything
                                           except how well the model understands)
    python dev/evaluate.py --live          the real planner model (Bedrock; needs your
                                           local_dev/.env) reads each message: this is the
                                           run that measures accuracy, tokens and latency
    python dev/evaluate.py --filter roster --verbose
    python dev/evaluate.py --json report.json
    python dev/evaluate.py --list

What is checked per turn: the route taken, the tier (execute / confirm / ask /
error / fallback), the reply type and text, how many times the planner was
called and the tokens spent. After a case: the values really stored in the
sample tables. The tool agent (open-ended questions) is not run: a case that
falls back to it simply reports route=llm.

Exit code 0 = every case passed, 1 = something failed.
"""

import argparse
import importlib.util
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

LOCAL_DEV = Path(__file__).resolve().parent.parent

APP_DIR = LOCAL_DEV.parent / "app" / "dtp_operate_agent"

CASES_FILE = Path(__file__).resolve().parent / "eval_cases.json"

sys.path.insert(0, str(APP_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def parse_args():

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("--live", action="store_true", help="use the real planner model instead of stored drafts")
    parser.add_argument("--filter", default="", help="only cases whose id or category contains this text")
    parser.add_argument("--verbose", action="store_true", help="print every message and reply")
    parser.add_argument("--json", default="", help="also write a JSON report to this file")
    parser.add_argument("--cases", default=str(CASES_FILE), help="case file (default: dev/eval_cases.json)")
    parser.add_argument("--list", action="store_true", help="list the cases and exit")

    return parser.parse_args()


def configure_environment(live: bool) -> None:
    """Sample data only, always. Live mode reaches Bedrock for the planner and nothing else."""

    if live:

        os.environ["ENVIRONMENT"] = "local"

        # The tables are in-memory fakes, so writing is safe whatever .env says.
        os.environ["READ_ONLY_MODE"] = "false"

    else:

        os.environ["ENVIRONMENT"] = "test"
        os.environ.setdefault("BEDROCK_MODEL_ID", "offline")
        os.environ["AWS_ACCESS_KEY_ID"] = "offline"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "offline"
        os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
        os.environ["AWS_ENDPOINT_URL"] = "http://127.0.0.1:9"

        os.environ["READ_ONLY_MODE"] = "false"


def load_fakes():
    """tests/fakes.py without importing the tests package (which would override the AWS settings)."""

    spec = importlib.util.spec_from_file_location("eval_fakes", LOCAL_DEV / "tests" / "fakes.py")

    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    return module


# ============================================================
# CASES
# ============================================================

def template_values(today) -> dict:

    day = 2 if today.day != 2 else 3

    return {
        "day": str(day),
        "iso_day": f"{today.year}-{today.month:02d}-{day:02d}",
        "month_name": today.strftime("%B"),
        "today": today.isoformat(),
    }


def fill(value, values):
    """Replace {day}, {iso_day}, {month_name}, {today} in strings, lists and dicts."""

    if isinstance(value, str):
        return re.sub(r"\{(day|iso_day|month_name|today)\}", lambda match: values[match.group(1)], value)

    if isinstance(value, list):
        return [fill(item, values) for item in value]

    if isinstance(value, dict):
        return {key: fill(item, values) for key, item in value.items()}

    return value


class PlannerSwitch:
    """Offline: return the case's stored draft. Live: call the real planner."""

    def __init__(self, planner, live):

        self.planner = planner

        self.live = live

        self.real = planner.extract

        self.draft = None

        self.calls = 0

    def __call__(self, text, today_text, context=""):

        self.calls += 1

        if self.live:
            return self.real(text, today_text, context)

        if self.draft is None:
            return None, {}

        ops = [self.planner.PlannedOp(**op) for op in self.draft]

        return self.planner.PlanDraft(ops=ops), {}


# ============================================================
# CHECKS
# ============================================================

def check_turn(expect, reply, meta, calls):
    """Problems found in one turn (empty list = pass)."""

    problems = []

    reason = str(meta.get("reason", ""))

    usage = meta.get("usage", {})

    tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))

    if "route" in expect and meta.get("route") != expect["route"]:
        problems.append(f"route was '{meta.get('route')}', expected '{expect['route']}'")

    if "tier" in expect and not reason.startswith(f"tier={expect['tier']}"):
        problems.append(f"tier was '{reason or '-'}', expected 'tier={expect['tier']}'")

    if "type" in expect and reply.structured.get("type") != expect["type"]:
        problems.append(f"reply type was '{reply.structured.get('type')}', expected '{expect['type']}'")

    if "confirmation" in expect and bool(reply.structured.get("confirmation")) != expect["confirmation"]:
        problems.append(f"confirmation was {bool(reply.structured.get('confirmation'))}, expected {expect['confirmation']}")

    if "planner_calls" in expect and calls != expect["planner_calls"]:
        problems.append(f"planner was called {calls} time(s), expected {expect['planner_calls']}")

    if "tokens" in expect and tokens > expect["tokens"]:
        problems.append(f"{tokens} tokens spent, expected at most {expect['tokens']}")

    for text in expect.get("contains", []):

        if text.lower() not in reply.text.lower():
            problems.append(f"reply is missing '{text}'")

    for text in expect.get("not_contains", []):

        if text.lower() in reply.text.lower():
            problems.append(f"reply should not contain '{text}'")

    return problems


def stored_value(world, check, today):
    """The value a data check points at in the in-memory tables."""

    table = {"incidents": world.incidents, "issues": world.issues, "roster": world.roster}[check["table"]]

    row = table.items.get(check["key"])

    if row is None:
        return None

    if "day" in check:

        day = today.day if check["day"] == "today" else int(check["day"])

        return (row.get("days_availability") or {}).get(str(day))

    return row.get(check["field"])


# ============================================================
# RUN
# ============================================================

def run_case(case, modules, live, verbose):

    agent, planner, fakes, switch, today = (
        modules["agent"], modules["planner"], modules["fakes"], modules["switch"], modules["today"],
    )

    from memory.store import SessionRuntime

    values = template_values(today)

    extra = [fakes.second_rohit()] if case.get("world", {}).get("second_rohit") else None

    world = fakes.World(extra_people=extra).install()

    runtime = SessionRuntime("evaluator", f"eval-{case['id']}")

    result = {"id": case["id"], "category": case["category"], "turns": [], "problems": [], "tokens": 0, "ms": 0}

    try:

        for number, turn in enumerate(case["turns"], start=1):

            say = fill(turn["say"], values)

            switch.draft = fill(turn.get("draft"), values) if "draft" in turn else None

            before = switch.calls

            started = time.perf_counter()

            reply, meta = agent.process_user_query(say, runtime)

            elapsed = int((time.perf_counter() - started) * 1000)

            usage = meta.get("usage", {})

            tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))

            problems = check_turn(fill(turn.get("expect", {}), values), reply, meta, switch.calls - before)

            result["tokens"] += tokens

            result["ms"] += elapsed

            result["turns"].append(
                {"say": say, "route": meta.get("route"), "tier": str(meta.get("reason", "")), "tokens": tokens, "ms": elapsed}
            )

            result["problems"].extend(f"turn {number} ('{say}'): {problem}" for problem in problems)

            if verbose:

                print(f"    you> {say}")
                print(f"    [{meta.get('route')} {meta.get('reason', '')} tokens={tokens} ms={elapsed}]")

                for line in reply.text.splitlines():
                    print(f"    bot> {line}")

        for check in fill(case.get("expect_data", []), values):

            actual = stored_value(world, check, today)

            if actual != check["equals"]:

                where = f"{check['table']}[{check['key']}]." + (f"day {check['day']}" if "day" in check else check["field"])

                result["problems"].append(f"data: {where} is {actual!r}, expected {check['equals']!r}")

    finally:
        world.close()

    result["passed"] = not result["problems"]

    return result


def percentile(numbers, share):

    ordered = sorted(numbers)

    return ordered[min(len(ordered) - 1, int(len(ordered) * share))] if ordered else 0


def main():

    args = parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))["cases"]

    if args.filter:
        cases = [case for case in cases if args.filter.lower() in (case["id"] + " " + case["category"]).lower()]

    cases = [
        case for case in cases
        if not (args.live and case.get("offline_only")) and not (not args.live and case.get("live_only"))
    ]

    if args.list:

        for case in cases:
            print(f"{case['id']:<48} {case['category']}")

        return 0

    configure_environment(args.live)

    import agent
    import planner
    from responses import text_reply
    from services.common import today

    fakes = load_fakes()

    switch = PlannerSwitch(planner, args.live)

    planner.extract = switch

    # The tool agent (open questions) is out of scope here: a fallback just says so.
    agent._run_llm = lambda user_input, runtime, meta: text_reply("(handed to the tool agent)")

    modules = {"agent": agent, "planner": planner, "fakes": fakes, "switch": switch, "today": today()}

    mode = "LIVE planner model" if args.live else "offline (stored drafts)"

    print(f"Evaluating {len(cases)} cases - {mode} - sample data, nothing real is touched\n")

    results = []

    for case in cases:

        if args.verbose:
            print(f"{case['id']}  [{case['category']}]")

        result = run_case(case, modules, args.live, args.verbose)

        results.append(result)

        status = "PASS" if result["passed"] else "FAIL"

        print(f"{status}  {case['id']:<46} tokens={result['tokens']:<5} {result['ms']}ms")

        for problem in result["problems"]:
            print(f"        - {problem}")

    passed = sum(1 for result in results if result["passed"])

    print("\n" + "=" * 72)

    print(f"{passed}/{len(results)} passed")

    by_category = {}

    for result in results:

        row = by_category.setdefault(result["category"], [0, 0])

        row[0] += result["passed"]

        row[1] += 1

    for category, (good, total) in by_category.items():
        print(f"  {good}/{total}  {category}")

    planner_turns = [turn for result in results for turn in result["turns"] if turn["route"] in ("plan",) or "fallback" in turn["tier"]]

    if args.live and planner_turns:

        tokens = [turn["tokens"] for turn in planner_turns if turn["tokens"]]

        times = [turn["ms"] for turn in planner_turns]

        if tokens:
            print(f"\nPlanner turns: {len(planner_turns)}   tokens/turn avg {int(statistics.mean(tokens))}   max {max(tokens)}")

        print(f"Latency (planner turns): p50 {percentile(times, 0.5)} ms   p95 {percentile(times, 0.95)} ms")

    if not args.live:
        print("\nOffline run: stored drafts stand in for the model. Run with --live to measure the model.")

    if args.json:

        Path(args.json).write_text(
            json.dumps({"mode": mode, "passed": passed, "total": len(results), "results": results}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"Report written to {args.json}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
