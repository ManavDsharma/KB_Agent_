"""
Talk to the agent from a terminal, without HTTP or AgentCore.

    python dev/chat.py --demo                   no AWS at all: in-memory sample data
    python dev/chat.py --read-only              your real tables and Bedrock, nothing can be changed
    python dev/chat.py --actor you@abc.com      your real tables, writes allowed

Run it from anywhere (python local_dev/dev/chat.py ...). With
--demo the model is never called: clear requests (lookups, follow-ups,
single updates, deletes, clarifications) work exactly as in production, and
open-ended questions (and multi-part writes, which need the planner model)
reply that the model is disabled. To try multi-part writes without AWS use
dev/evaluate.py.

Commands while chatting:  /new  new session   /state  what the agent
remembers   /json  toggle structured output   /quit
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

LOCAL_DEV = Path(__file__).resolve().parent.parent

APP_DIR = LOCAL_DEV.parent / "app" / "dtp_operate_agent"

sys.path.insert(0, str(APP_DIR))

sys.path.insert(0, str(LOCAL_DEV))

# The app treats anything but "local" as a deployed environment.
os.environ.setdefault("ENVIRONMENT", "local")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def parse_args():

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("--demo", action="store_true", help="use in-memory sample data, no AWS")
    parser.add_argument("--read-only", action="store_true", help="refuse every create/update/delete")
    parser.add_argument("--actor", default="local-tester", help="actor_id (recorded as updated_by)")
    parser.add_argument("--meta", action="store_true", help="show route / tokens / tools per answer")

    return parser.parse_args()


def main():

    args = parse_args()

    if args.demo:

        # Nothing may reach AWS in demo mode.
        os.environ["ENVIRONMENT"] = "test"
        os.environ.setdefault("BEDROCK_MODEL_ID", "demo")
        os.environ["AWS_ACCESS_KEY_ID"] = "demo"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "demo"
        os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
        os.environ["AWS_ENDPOINT_URL"] = "http://127.0.0.1:9"

    if args.read_only:
        os.environ["READ_ONLY_MODE"] = "true"

    import agent
    from memory.store import SessionStore
    from responses import text_reply

    if args.demo:

        from tests.fakes import World

        World().install()

        def no_model(user_input, runtime, meta):
            return text_reply("(The model is disabled in demo mode; this question needs it.)")

        agent._run_llm = no_model

        # Multi-part writes need the planner model too: in demo mode they fall back as open questions.
        import planner

        planner.extract = lambda text, today, context="": (None, {})

    store = SessionStore()

    def new_session():
        return store.get(args.actor, f"dev-{uuid.uuid4().hex}")

    runtime = new_session()

    show_json = False

    mode = "demo data" if args.demo else "REAL tables" + (" (read-only)" if args.read_only else " (writes allowed)")

    print(f"DTP Operate Agent - {mode} - actor {args.actor}")
    print("Type a question. /new /state /json /quit\n")

    while True:

        try:
            text = input("you> ").strip()

        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not text:
            continue

        if text == "/quit":
            break

        if text == "/new":
            runtime = new_session()
            print("(new session)\n")
            continue

        if text == "/state":
            print(runtime.state.context_block(), "\n")
            continue

        if text == "/json":
            show_json = not show_json
            print(f"(structured output {'on' if show_json else 'off'})\n")
            continue

        reply, meta = agent.process_user_query(text, runtime)

        print(f"\n{reply.text}\n")

        if show_json:
            print(json.dumps(reply.structured, indent=2, ensure_ascii=False), "\n")

        if args.meta:

            usage = meta.get("usage", {})

            print(
                f"[route={meta.get('route')} direct={meta.get('direct')} domain={meta.get('domain', '-')} "
                f"tokens_in={usage.get('input_tokens', 0)} tokens_out={usage.get('output_tokens', 0)} "
                f"cache_read={usage.get('cache_read_tokens', 0)} cache_write={usage.get('cache_write_tokens', 0)} "
                f"tools={','.join(meta.get('tools', [])) or '-'} ms={meta.get('latency_ms')}]\n"
            )


if __name__ == "__main__":
    main()
