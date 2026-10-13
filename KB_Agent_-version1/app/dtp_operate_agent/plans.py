"""
Plans: how a multi-part write request becomes safe changes.

    planner draft ──► build_plan ──► validate ──► tier ──► execute
    (model, no tools)  (grounding)   (services,    execute | confirm | ask
                                      dry run)

* **Grounding** (`build_plan`): everything in the draft must be in the user's
  own message (IDs, names, fields, values, dates), and every record the user
  named must be covered. Otherwise the draft is rejected and nothing happens
  here (the ordinary tool agent takes over).
* **Validation**: each operation is run through its service with `dry_run`, so
  values are mapped onto real ones ("high" -> "P2-High"), people resolved and
  the from -> to change known *before* anything is written.
* **Tiers**: one record, explicit change -> execute at once. Several records or
  days, or anything inferred (a year the user did not give, a past date) ->
  show the preview and wait for Confirm. Something truly missing or ambiguous
  -> ask ONE question with buttons and keep the plan, so the answer completes
  it instead of starting over.

A plan lives in the session state (`pending`), is bounded, and expires after
a few turns. The model is never involved after the draft.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import planner
from config import PLAN_MAX_OPERATIONS
from context import focus_employee, focus_incident, focus_issue
from memory.state import SessionState
from plan_text import (
    date_grounded,
    expand_range,
    field_grounded,
    grounded_value,
    has_plural_reference,
    is_pronoun,
    name_grounded,
    person_label,
)
from presentation import after_update_suggestions
from responses import (
    Reply,
    batch_reply,
    confirm_reply,
    error_reply,
    question_reply,
    text_reply,
    update_reply,
)
from router import Intent
from services import get_categorization_service, get_incident_service, get_roster_service
from services.common import ServiceError, parse_user_date, today
from services.ids import (
    find_all_issue_refs,
    find_all_ticket_refs,
    issue_key,
    ticket_key_of,
    typed_ticket_ids,
)
from services.schema import (
    EMPLOYEE_UPDATABLE_FIELDS,
    INCIDENT_PERSON_FIELDS,
    INCIDENT_READ_ONLY_FIELDS,
    ISSUE_UPDATABLE_FIELDS,
    label as field_label,
    resolve_field_names,
)

logger = logging.getLogger("dtp.plans")

Plan = Dict[str, Any]

_AVAILABILITY_WORDS = {"Y": "Available", "N": "Not available", "NA": "Non-working day"}


# ============================================================
# BUILD: grounding the planner's draft in the user's message
# ============================================================

def _updatable(record: str, field: str) -> bool:

    if record == "incident":
        return field not in INCIDENT_READ_ONLY_FIELDS

    if record == "issue":
        return field in ISSUE_UPDATABLE_FIELDS

    return field in EMPLOYEE_UPDATABLE_FIELDS


def _ground_target(record: str, target: str, text: str) -> Optional[str]:
    """The target as the services should receive it, if the user wrote it."""

    if record == "incident":

        refs = typed_ticket_ids(target)

        if refs and refs[0][0] in find_all_ticket_refs(text):
            return refs[0][1]

        return None

    if record == "issue":

        refs = find_all_issue_refs(target)

        if refs and refs[0] in find_all_issue_refs(text):
            return refs[0]

        return None

    return target.strip() if name_grounded(target, text) else None


def _default_targets(
    record: str,
    state: SessionState,
    defaults: List[str],
    text: str,
) -> Tuple[List[str], bool]:
    """
    (targets, from_a_list) for "it / he / that one" and "them / both of them".

    "Them" is the records of the last list shown, never the single record
    that happens to be in focus (that would silently change one of two).
    With no recent list there is nobody to pick: the user is asked.
    """

    if has_plural_reference(text) and not defaults:

        group = state.get_list(record, state.mutation_max_turns)

        return (list(group["ids"]), True) if group else ([], False)

    if defaults:
        return list(defaults), False

    entity = state.get_focus(record, state.mutation_max_turns)

    return ([entity.id] if entity is not None else []), False


def _build_ops(
    planned: Any,
    text: str,
    state: SessionState,
    defaults: List[str],
) -> Any:
    """Operations for one planned op (one per target), or a reason string."""

    record = planned.record

    action = planned.action

    if action == "availability" and record != "employee":
        return "availability is only for employees"

    changes: Dict[str, str] = {}

    if action == "update":

        for raw_field, raw_value in planned.changes.items():

            names = resolve_field_names(record, [raw_field])

            if len(names) != 1 or not _updatable(record, names[0]):
                return f"field '{raw_field}' is not updatable"

            field = names[0]

            if not field_grounded(record, field, text):
                return f"field '{field}' is not in the request"

            value = str(raw_value or "").strip()

            if value and not grounded_value(value, text):
                return f"value '{value}' is not in the request"

            changes[field] = value

    # ---- targets ---------------------------------------------------
    explicit = [target for target in planned.targets if target and not is_pronoun(target)]

    targets: List[Optional[str]] = []

    group = False

    for target in explicit:

        grounded = _ground_target(record, target, text)

        if grounded is None:
            return f"'{target}' is not in the request"

        if grounded not in targets:
            targets.append(grounded)

    if not targets:

        # The user named nobody ("it", "he"), or the model dropped an ID.
        mentioned = (
            typed_ticket_ids(text) if record == "incident"
            else [(None, ref) for ref in find_all_issue_refs(text)] if record == "issue"
            else []
        )

        if len(mentioned) > 1:
            return "several records are named but no target was given"

        if mentioned:
            targets = [mentioned[0][1]]

        else:

            found, group = _default_targets(record, state, defaults, text)

            targets = list(found) or [None]

    # ---- availability ----------------------------------------------
    dates: List[str] = []

    inferred = False

    value = ""

    if action == "availability":

        for raw in planned.dates:

            try:
                iso, guessed = parse_user_date(raw)

            except ServiceError:
                return f"date '{raw}' could not be read"

            if not date_grounded(iso, text):
                return f"date '{raw}' is not in the request"

            inferred = inferred or guessed

            if iso not in dates:
                dates.append(iso)

        if planned.until and dates:

            try:

                end, guessed = parse_user_date(planned.until)

                if not date_grounded(end, text):
                    return f"date '{planned.until}' is not in the request"

                dates = expand_range(dates[0], end)

                inferred = inferred or guessed

            except ServiceError as exc:
                return exc.message

        value = str(planned.value or "").strip()

        if value and not grounded_value(value, text):
            return f"value '{value}' is not in the request"

    past = any(day < today().isoformat() for day in dates)

    return [
        {
            "action": action,
            "record": record,
            "target": target,
            "changes": dict(changes),
            "dates": list(dates),
            "value": value,
            "inferred": inferred,
            "past": past,
            "group": group,
        }
        for target in targets
    ]


def build_plan(
    draft: Any,
    text: str,
    state: SessionState,
    defaults: Optional[List[str]] = None,
) -> Tuple[Optional[Plan], str]:
    """(plan, "") or (None, reason). A rejected draft changes nothing."""

    ops: List[Dict[str, Any]] = []

    for planned in draft.ops:

        built = _build_ops(planned, text, state, list(defaults or []))

        if isinstance(built, str):
            return None, built

        for op in built:

            if op not in ops:
                ops.append(op)

    if not ops:
        return None, "no operations"

    if len(ops) > PLAN_MAX_OPERATIONS:
        return None, "too_many"

    # Every record the user named must be part of the plan: a model that
    # drops "INC2" from "close INC1 and INC2" must not cause a partial change.
    covered = {ticket_key_of(op["target"]) for op in ops if op["record"] == "incident" and op["target"]}

    for key in find_all_ticket_refs(text):

        if key not in covered:
            return None, f"{key} is named but not covered"

    covered_issues = {op["target"] for op in ops if op["record"] == "issue" and op["target"]}

    for ref in find_all_issue_refs(text):

        if ref not in covered_issues:
            return None, f"{ref} is named but not covered"

    return {"text": text[:300], "ops": ops}, ""


# ============================================================
# SLOTS: what is still missing
# ============================================================

def _next_slot(plan: Plan) -> Optional[Dict[str, Any]]:

    for op in plan["ops"]:

        if op["target"] is None:
            return {"kind": "target", "record": op["record"], "action": op["action"]}

    for index, op in enumerate(plan["ops"]):

        if op["action"] == "update":

            if not op["changes"]:

                targets: List[Any] = []

                for other in plan["ops"]:

                    if other["record"] == op["record"] and other["target"] not in targets:
                        targets.append(other["target"])

                return {"kind": "changes", "record": op["record"], "targets": targets}

            for field, value in op["changes"].items():

                if not value:
                    return {"kind": "value", "op": index, "field": field}

        else:

            if not op["dates"]:
                return {"kind": "date"}

            if not op["value"]:
                return {"kind": "avail_value"}

    return None


def _target_label(op: Dict[str, Any]) -> str:
    return str(op["target"] or "it")


def _ask(
    plan: Plan,
    need: Dict[str, Any],
    question: str,
    state: SessionState,
    options=None,
    sends=None,
) -> Reply:

    state.set_pending("plan_slots", plan=plan, need=need)

    return question_reply(question, options, sends)


def _ask_slot(plan: Plan, need: Dict[str, Any], state: SessionState) -> Reply:

    kind = need["kind"]

    if kind == "target":

        if need.get("action") == "availability":
            return _ask(plan, need, "Whose availability should I change? Please share their name.", state)

        words = {
            "incident": "Which incident should I update? Please share its ID.",
            "issue": "Which issue should I update? Please share its ID.",
            "employee": "Which employee should I update? Please share their name.",
        }

        # "update their priority": there is no recent list to take them from.
        if has_plural_reference(plan.get("text", "")):

            words = {
                "incident": "Which incidents do you mean? Please share their IDs.",
                "issue": "Which issues do you mean? Please share their IDs.",
                "employee": "Which employees do you mean? Please share their names.",
            }

        return _ask(plan, need, words[need["record"]], state)

    if kind == "changes":

        shown = ", ".join(str(target) for target in need["targets"][:3])

        return _ask(plan, need, f"What would you like to change for {shown}?", state)

    if kind == "value":

        op = plan["ops"][need["op"]]

        return _ask(
            plan,
            need,
            f"What should the {field_label(need['field']).lower()} be for {_target_label(op)}?",
            state,
        )

    if kind == "date":

        return _ask(plan, need, "For which date(s)?", state)

    return _ask(
        plan,
        need,
        "Should I mark them available or not available?",
        state,
        ["Available", "Not available"],
        ["available", "not available"],
    )


# ============================================================
# VALIDATE: dry run through the services
# ============================================================

def _call(op: Dict[str, Any], actor: Optional[str], dry_run: bool) -> Dict[str, Any]:

    if op["action"] == "availability":

        return get_roster_service().set_employee_availability(
            op["target"],
            {day: op["value"] for day in op["dates"]},
            actor,
            dry_run=dry_run,
        )

    if op["record"] == "incident":
        return get_incident_service().update_incident(op["target"], op["changes"], actor, dry_run=dry_run)

    if op["record"] == "issue":
        return get_categorization_service().update_issue(op["target"], op["changes"], actor, dry_run=dry_run)

    return get_roster_service().update_roster_entry(op["target"], op["changes"], actor, dry_run=dry_run)


def _word(code: Any) -> str:

    if code in (None, ""):
        return "not set"

    return _AVAILABILITY_WORDS.get(str(code).strip().upper(), str(code))


def _changes(op: Dict[str, Any], result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The real from -> to changes of a result (labels ready to show)."""

    shown = []

    for name, change in (result.get("changes") or {}).items():

        before, after = change.get("from"), change.get("to")

        if op["action"] == "availability":

            if before == after:
                continue

            shown.append({"field": name, "label": name, "from": _word(before), "to": _word(after)})

        else:

            if before == after and name != "working_notes":
                continue

            shown.append({"field": name, "label": field_label(name), "from": before, "to": after})

    return shown


def _item(op: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:

    if op["record"] == "incident":
        identifier = result.get("ticket_id") or op["target"]

    elif op["record"] == "issue":
        identifier = result.get("issue_id") or op["target"]

    else:
        identifier = result.get("ref") or op["target"]

    label = (result.get("employee_name") or identifier) if op["record"] == "employee" else identifier

    return {
        "kind": op["record"],
        "id": identifier,
        "label": label,
        "changes": _changes(op, result),
    }


def _ask_from_error(
    plan: Plan,
    index: int,
    op: Dict[str, Any],
    result: Dict[str, Any],
    state: SessionState,
) -> Optional[Reply]:
    """A fixable problem becomes one question with buttons; None = hard error."""

    error = result.get("error")

    options = result.get("options") or []

    if error == "ambiguous" and options and all(isinstance(item, dict) for item in options):

        need = {"kind": "choice", "op": index, "person": True, "options": options}

        return _ask(
            plan,
            need,
            f"Several employees match '{op['target']}'. Which one?",
            state,
            [person_label(item) for item in options],
        )

    if (
        op["action"] == "availability"
        and error == "invalid_value"
        and "valid availability" in str(result.get("message", ""))
    ):

        plan["ops"][index]["value"] = ""

        return _ask(
            plan,
            {"kind": "avail_value"},
            f"I couldn't tell whether '{op['value']}' means available or not available. Which one?",
            state,
            ["Available", "Not available"],
            ["available", "not available"],
        )

    choices = options or result.get("allowed") or []

    if error in ("ambiguous_value", "invalid_value") and choices and op["action"] == "update":

        field = result.get("field")

        if not field:

            people = [name for name in op["changes"] if name in INCIDENT_PERSON_FIELDS]

            field = people[0] if len(people) == 1 else None

        if field and all(isinstance(item, str) for item in choices):

            shown = field_label(field).lower()

            wrong = op["changes"].get(field, "")

            question = (
                f"Which {shown} do you mean for {_target_label(op)}?"
                if error == "ambiguous_value"
                else f"'{wrong}' isn't a valid {shown}. Which one should I use?"
            )

            return _ask(
                plan,
                {"kind": "choice", "op": index, "field": field, "options": list(choices[:8])},
                question,
                state,
                list(choices[:8]),
            )

    return None


def validate(
    plan: Plan,
    state: SessionState,
    actor: Optional[str],
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[Reply]]:
    """
    (results, None) when every operation is valid, else (None, reply): a
    question that keeps the plan, or an error. Nothing is written here.
    """

    need = _next_slot(plan)

    if need is not None:
        return None, _ask_slot(plan, need, state)

    results: List[Dict[str, Any]] = []

    errors: List[Tuple[str, str]] = []

    for index, op in enumerate(plan["ops"]):

        result = _call(op, actor, dry_run=True)

        if result.get("ok"):
            results.append(result)
            continue

        asked = _ask_from_error(plan, index, op, result, state)

        if asked is not None:
            return None, asked

        errors.append((_target_label(op), str(result.get("message") or "That could not be done.")))

    if errors:

        if len(plan["ops"]) == 1:
            return None, error_reply(errors[0][1])

        return None, error_reply(
            "I didn't change anything.\n\n" + "\n".join(f"- {label}: {message}" for label, message in errors)
        )

    return results, None


# ============================================================
# TIERS AND EXECUTION
# ============================================================

def _needs_confirmation(changed: List[Tuple[Dict[str, Any], Dict[str, Any]]]) -> bool:
    """Several records or days, or anything the user did not state exactly."""

    if len(changed) >= 2:
        return True

    cells = sum(len(_changes(op, result)) for op, result in changed if op["action"] == "availability")

    if cells >= 2:
        return True

    # "Them" came from the last list, not from an ID the user typed: say so first.
    return any(op.get("inferred") or op.get("past") or op.get("group") for op, _ in changed)


def _notes(plan: Plan) -> List[str]:

    notes: List[str] = []

    if any(op.get("group") for op in plan["ops"]):
        notes.append("These are the records from the list I just showed.")

    inferred = sorted({day[:4] for op in plan["ops"] if op.get("inferred") for day in op["dates"]})

    if inferred:
        notes.append(f"No year was given, so I used {', '.join(inferred)}.")

    past = sorted(
        {day for op in plan["ops"] if op.get("past") for day in op["dates"] if day < today().isoformat()}
    )

    if past:
        notes.append(
            f"{', '.join(past[:3])}{'…' if len(past) > 3 else ''} {'is' if len(past) == 1 else 'are'} in the past."
        )

    return notes


def _remember(state: SessionState, op: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Keep the session context and cache in step with a successful write."""

    changes = {
        name: change.get("to")
        for name, change in (result.get("changes") or {}).items()
        if name != "working_notes" and op["action"] == "update"
    }

    if op["record"] == "incident":

        entity = focus_incident(state, result["ticket_id"])

        state.apply_changes("incident", entity.key, changes)

    elif op["record"] == "issue":

        focus_issue(state, result["issue_id"])

        state.apply_changes("issue", issue_key(result["issue_id"]), changes)

    elif result.get("ref"):

        focus_employee(state, result["ref"], str(result.get("employee_name") or ""))


def _already(same: List[str]) -> str:
    """"INC1 already has those values." for records that needed no change."""

    return f"{', '.join(same[:4])} already {'has' if len(same) == 1 else 'have'} those values."


def _execute(
    plan: Plan,
    changed: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    state: SessionState,
    actor: Optional[str],
    same: Optional[List[str]] = None,
) -> Reply:

    items: List[Dict[str, Any]] = []

    failed: List[Tuple[str, str]] = []

    last: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None

    for op, _ in changed:

        result = _call(op, actor, dry_run=False)

        if not result.get("ok"):
            failed.append((_target_label(op), str(result.get("message") or "That could not be done.")))
            continue

        items.append(_item(op, result))

        _remember(state, op, result)

        last = (op, result)

    state.clear_pending()

    state.last_operation = f"update {len(items)} record(s)"

    if not items:
        return error_reply("I couldn't apply the changes. " + (failed[0][1] if failed else ""))

    if len(items) == 1 and not failed and last is not None:

        op, result = last

        item = items[0]

        shown = {change["field"]: {"from": change["from"], "to": change["to"]} for change in item["changes"]}

        kind = op["record"]

        reply = update_reply(str(item["label"]), shown, result.get("unchanged", ()), kind=kind)

        if kind in ("incident", "issue"):
            reply.suggest(after_update_suggestions(kind))

    else:
        reply = batch_reply(items, failed)

    # Records the user addressed that already had the values must not vanish from the answer.
    if same:

        reply.text += "\n\n" + _already(same)

        reply.structured["text"] = reply.text

    return reply


def process(
    plan: Plan,
    state: SessionState,
    actor: Optional[str],
    confirmed: bool = False,
) -> Tuple[Reply, str]:
    """Validate a plan and act on it. Returns (reply, tier)."""

    results, reply = validate(plan, state, actor)

    if reply is not None:
        return reply, ("ask" if state.pending else "error")

    changed = [
        (op, result)
        for op, result in zip(plan["ops"], results or [])
        if _changes(op, result)
    ]

    if not changed:

        state.clear_pending()

        names = ", ".join(_target_label(op) for op in plan["ops"][:3])

        verb = "has" if len(plan["ops"]) == 1 else "have"

        return text_reply(f"No change needed: {names} already {verb} those values."), "noop"

    # Records the user addressed that need no change must not vanish silently.
    same = [_target_label(op) for op in plan["ops"] if all(op is not other for other, _ in changed)]

    if not confirmed and _needs_confirmation(changed):

        state.set_pending("plan_confirm", plan=plan)

        notes = _notes(plan)

        if same:
            notes.append(_already(same))

        return confirm_reply([_item(op, result) for op, result in changed], notes), "confirm"

    return _execute(plan, changed, state, actor, same), "execute"


# ============================================================
# ENTRY POINTS (called from actions.py)
# ============================================================

def _context_line(state: SessionState) -> str:

    bits = []

    for record in ("incident", "issue", "employee"):

        entity = state.get_focus(record, state.mutation_max_turns)

        if entity is not None:
            bits.append(f"{record} {entity.label if record == 'employee' and entity.label else entity.id}")

    return "; ".join(bits)


def _why_category(why: str) -> str:
    """A rejected draft's reason as a fixed label (safe to log: no user words)."""

    if "not covered" in why:
        return "dropped record"

    if why.startswith(("field", "value", "date")):
        return why.split(" ", 1)[0] + " not in request"

    if "is not in the request" in why:
        return "target not in request"

    if why == "no operations":
        return "no plan"

    return "unusable draft"


def handle_plan(intent: Intent, state: SessionState, actor: Optional[str]) -> Optional[Reply]:
    """
    Understand a write request with the planner and act on it. None = nothing
    was understood well enough (planner unavailable, nothing recognised, or
    its answer was not grounded in the message): the tool agent answers instead.
    """

    state.clear_pending()

    text = intent.query or ""

    draft, usage = planner.extract(text, today().isoformat(), _context_line(state))

    intent.usage = usage

    if draft is None or not draft.ops:

        intent.reason = "tier=fallback (no plan)"

        return None

    # The router's entity_ref is only "the record in focus": it must not stand in
    # for "them". Targets the user was asked for (intent.targets) always count.
    defaults = list(intent.targets) or (
        [intent.entity_ref] if intent.entity_ref and not has_plural_reference(text) else []
    )

    plan, why = build_plan(draft, text, state, defaults)

    if plan is None:

        # The detail names what the user wrote (IDs, people): debug only. The
        # request log gets just the category.
        logger.debug("plan rejected: %s", why)

        if why == "too_many":

            intent.reason = "tier=error (too many)"

            return error_reply(
                f"That's more than {PLAN_MAX_OPERATIONS} changes at once. Please split it into smaller requests."
            )

        intent.reason = f"tier=fallback ({_why_category(why)})"

        return None

    reply, tier = process(plan, state, actor)

    intent.reason = f"tier={tier}"

    return reply


def handle_resume(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:
    """The user answered a plan's question: continue with the completed plan."""

    plan = (intent.pending or {}).get("plan") or {"ops": []}

    state.clear_pending()

    reply, tier = process(plan, state, actor)

    intent.reason = f"tier={tier}"

    return reply


def handle_confirm(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:
    """The user confirmed: validate once more (data may have changed) and apply."""

    plan = (state.pending or {}).get("plan")

    state.clear_pending()

    if not plan:
        return text_reply("There is nothing waiting for confirmation.")

    reply, tier = process(plan, state, actor, confirmed=True)

    intent.reason = f"tier={tier}"

    return reply


def handle_cancel(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    state.clear_pending()

    return text_reply("Okay, I won't change anything.")
