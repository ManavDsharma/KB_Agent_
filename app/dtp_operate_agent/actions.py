"""
Direct execution of routed intents (no model involved).

Every handler:
    resolve the target (explicit ID, or from session state)
    -> answer from the session cache when it is fresh, else read DynamoDB
    -> update the session state
    -> return a small Reply

Reads fetch the standard field set once and cache it, so follow-up
questions about the same record cost nothing. Writes go through the
services, which validate values against the data; a clear request is
executed at once and confirmed briefly.
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from context import (
    focus_employee,
    focus_incident,
    focus_issue,
    focus_team,
)

from memory.state import SessionState

from responses import (
    Reply,
    error_reply,
    field_reply,
    list_reply,
    message_confirmation,
    question_reply,
    record_reply,
    table_reply,
    text_reply,
    update_reply,
)

from presentation import (
    EMPLOYEE_SECTIONS,
    INCIDENT_SECTIONS,
    ISSUE_SECTIONS,
    after_update_suggestions,
    employee_card,
    employee_suggestions,
    incident_card,
    incident_suggestions,
    issue_card,
    issue_suggestions,
    list_suggestions,
    team_suggestions,
)

from router import Intent

from services import (
    get_categorization_service,
    get_incident_service,
    get_roster_service,
)

from services.ids import issue_key, ticket_key_of

from services.schema import (
    EMPLOYEE_VIEW_FIELDS,
    INCIDENT_LIST_FIELDS,
    INCIDENT_VIEW_FIELDS,
    ISSUE_VIEW_FIELDS,
)

logger = logging.getLogger("dtp.actions")

_EMPLOYEE_SHOW = [
    "emp_id",
    "employee_id",
    "team_name",
    "team_id",
    "team_email_id",
    "level",
    "shift",
    "shift_start_time",
    "shift_end_time",
    "active",
]

_AVAILABILITY_WORDS = {"Y": "Available", "N": "Not available", "NA": "Non-working day"}

_INCIDENT_SHOW = [name for name in INCIDENT_VIEW_FIELDS if name not in ("ticket_id", "url", "source_system")]

Loaded = Union[Reply, Tuple[str, Dict[str, Any]]]


# ============================================================
# ERRORS
# ============================================================

def _failure(
    result: Dict[str, Any],
    state: SessionState,
    entity_type: Optional[str] = None,
    key: Optional[str] = None,
    from_context: bool = False,
) -> Reply:

    if result.get("error") == "not_found" and from_context and entity_type and key:
        state.clear_focus(entity_type, key)

    return error_reply(result.get("message") or "I couldn't complete that.")


# ============================================================
# INCIDENTS
# ============================================================

def _load_incident(
    state: SessionState,
    ref: str,
    fields: Optional[List[str]],
    from_context: bool,
) -> Loaded:
    """(ticket_id, data) for the requested fields, or an error Reply."""

    service = get_incident_service()

    key = ticket_key_of(ref) or str(ref).lower()

    entity = state.find("incident", key)

    wanted = list(fields) if fields else list(INCIDENT_VIEW_FIELDS)

    cached = state.cached_fields(entity, wanted)

    if cached is not None:
        return entity.id, cached

    result = service.get_incident(ref)

    if not result.get("ok"):
        return _failure(result, state, "incident", key, from_context)

    data: Dict[str, Any] = {name: None for name in INCIDENT_VIEW_FIELDS}

    data.update(result["incident"])

    # Long text is not in the standard set; fetch it only when asked for.
    extras = [name for name in (fields or []) if name in ("description", "working_notes")]

    if extras:

        more = service.get_incident(ref, extras)

        if more.get("ok"):
            data.update(more["incident"])

    focus_incident(state, result["ticket_id"], data)

    return result["ticket_id"], {name: data.get(name) for name in (fields or list(data))}


def _incident_view(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    loaded = _load_incident(state, intent.entity_ref, None, intent.from_context)

    if isinstance(loaded, Reply):
        return loaded

    ticket_id, data = loaded

    state.last_operation = f"read incident {ticket_id}"

    pairs = [(name, data.get(name)) for name in _INCIDENT_SHOW if data.get(name) not in (None, "")]

    if data.get("description"):
        pairs.append(("description", data["description"]))

    return record_reply(
        ticket_id,
        pairs,
        kind="incident",
        entity_id=ticket_id,
        card=incident_card(ticket_id, data),
        layout=INCIDENT_SECTIONS,
    ).suggest(incident_suggestions())


def _incident_fields(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    loaded = _load_incident(state, intent.entity_ref, intent.fields, intent.from_context)

    if isinstance(loaded, Reply):
        return loaded

    ticket_id, data = loaded

    state.last_operation = f"read incident {ticket_id}"

    return field_reply(
        ticket_id,
        [(name, data.get(name)) for name in intent.fields],
        kind="incident",
    ).suggest(incident_suggestions(asked=intent.fields))


# ============================================================
# ISSUES (CATEGORY MATRIX)
# ============================================================

def _load_issue(
    state: SessionState,
    ref: str,
    fields: Optional[List[str]],
    from_context: bool,
) -> Loaded:

    service = get_categorization_service()

    key = issue_key(ref)

    entity = state.find("issue", key)

    wanted = list(fields) if fields else list(ISSUE_VIEW_FIELDS)

    cached = state.cached_fields(entity, wanted)

    if cached is not None:
        return entity.id, cached

    result = service.get_issue(ref)

    if not result.get("ok"):
        return _failure(result, state, "issue", key, from_context)

    data: Dict[str, Any] = {name: None for name in ISSUE_VIEW_FIELDS}

    data.update(result["issue"])

    focus_issue(state, result["issue_id"], data)

    return result["issue_id"], {name: data.get(name) for name in (fields or list(data))}


def _issue_view(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    loaded = _load_issue(state, intent.entity_ref, None, intent.from_context)

    if isinstance(loaded, Reply):
        return loaded

    issue_id, data = loaded

    state.last_operation = f"read issue {issue_id}"

    pairs = [
        (name, data.get(name))
        for name in ISSUE_VIEW_FIELDS
        if name != "issue_id" and data.get(name) not in (None, "")
    ]

    return record_reply(
        issue_id,
        pairs,
        kind="issue",
        entity_id=issue_id,
        card=issue_card(issue_id, data),
        layout=ISSUE_SECTIONS,
    ).suggest(issue_suggestions())


def _issue_fields(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    loaded = _load_issue(state, intent.entity_ref, intent.fields, intent.from_context)

    if isinstance(loaded, Reply):
        return loaded

    issue_id, data = loaded

    state.last_operation = f"read issue {issue_id}"

    return field_reply(
        issue_id,
        [(name, data.get(name)) for name in intent.fields],
        kind="issue",
    ).suggest(issue_suggestions(asked=intent.fields))


def _entity_view(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    if intent.entity_type == "issue":
        return _issue_view(intent, state, actor)

    return _incident_view(intent, state, actor)


def _entity_fields(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    if intent.entity_type == "issue":
        return _issue_fields(intent, state, actor)

    return _incident_fields(intent, state, actor)


# ============================================================
# UPDATE / DELETE
# ============================================================

def _entity_update(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    entity_type = intent.entity_type

    ref = intent.entity_ref

    if entity_type == "issue":

        result = get_categorization_service().update_issue(ref, intent.changes, actor)

        identifier = result.get("issue_id")

        key = issue_key(identifier or ref)

    else:

        result = get_incident_service().update_incident(ref, intent.changes, actor)

        identifier = result.get("ticket_id")

        key = ticket_key_of(identifier or ref) or str(ref).lower()

    if result.get("ok"):

        if entity_type == "issue":
            focus_issue(state, identifier)

        else:
            focus_incident(state, identifier)

        state.apply_changes(
            entity_type,
            key,
            {name: change.get("to") for name, change in result["changes"].items() if name != "working_notes"},
        )

        state.clear_pending()

        state.last_operation = f"update {entity_type} {identifier}"

        return update_reply(
            identifier,
            result["changes"],
            result.get("unchanged", ()),
            kind=entity_type,
        ).suggest(after_update_suggestions(entity_type))

    if result.get("error") in ("invalid_value", "ambiguous_value") and len(intent.changes) == 1:

        state.set_pending(
            "update_slots",
            expects="value",
            entity_type=entity_type,
            entity_ref=ref,
            field=next(iter(intent.changes)),
        )

    return _failure(result, state, entity_type, key, intent.from_context)


def _entity_delete(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    result = get_incident_service().delete_incident(intent.entity_ref, actor)

    key = ticket_key_of(intent.entity_ref) or str(intent.entity_ref).lower()

    if not result.get("ok"):
        return _failure(result, state, "incident", key, intent.from_context)

    state.clear_focus("incident", ticket_key_of(result["ticket_id"]) or key)

    state.last_operation = f"delete incident {result['ticket_id']}"

    return message_confirmation(
        f"Deleted incident {result['ticket_id']}.",
        entity=result["ticket_id"],
        operation="delete",
        kind="incident",
    )


# ============================================================
# EMPLOYEES
# ============================================================

def _option_label(option: Dict[str, Any]) -> str:

    team = option.get("team_name") or option.get("team_id") or ""

    extra = ", ".join(str(part) for part in (team, option.get("emp_id")) if part)

    return f"{option.get('employee_name', option.get('ref'))}" + (f" ({extra})" if extra else "")


def _person(
    intent: Intent,
    state: SessionState,
) -> Union[Reply, Tuple[str, str, Dict[str, Any]]]:
    """(ref, name, data) for the employee the intent refers to."""

    service = get_roster_service()

    if intent.entity_ref and intent.fields:

        entity = state.find("employee", str(intent.entity_ref).lower())

        if entity is not None and state.cached_fields(entity, intent.fields) is not None:
            return entity.id, entity.label, {**entity.data}

    reference = intent.entity_ref or intent.query

    result = service.get_roster_entry(reference)

    if not result.get("ok"):

        if result.get("error") == "ambiguous":

            options = result.get("options") or []

            state.set_pending(
                "choose_employee",
                options=options,
                then={
                    "route": intent.route,
                    "operation": intent.operation,
                    "fields": intent.fields,
                    "date": intent.date,
                },
            )

            return question_reply(
                result.get("message", "Which employee do you mean?"),
                [_option_label(option) for option in options],
            )

        return _failure(result, state, "employee", str(reference).lower(), intent.from_context)

    data: Dict[str, Any] = {name: None for name in EMPLOYEE_VIEW_FIELDS}

    data.update(result["employee"])

    focus_employee(state, result["ref"], data.get("employee_name") or "", data)

    return result["ref"], data.get("employee_name") or result["ref"], data


def _employee_view(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    intent.fields = []

    person = _person(intent, state)

    if isinstance(person, Reply):
        return person

    ref, name, data = person

    state.last_operation = f"read roster {name}"

    pairs = [(field, data.get(field)) for field in _EMPLOYEE_SHOW if data.get(field) not in (None, "")]

    layout = list(EMPLOYEE_SECTIONS)

    summary = data.get("availability_summary")

    if isinstance(summary, dict):

        off = ", ".join(str(day) for day in summary.get("off_days", [])) or "none"

        key = f"availability_{summary.get('month')}"

        text = f"{summary.get('working_days')} working days; off on: {off}"

        if summary.get("non_working_days"):
            text += f"; {summary['non_working_days']} weekend / non-working days"

        pairs.append((key, text))

        layout.append(("Availability", [key]))

    return record_reply(
        name,
        pairs,
        kind="employee",
        entity_id=ref,
        card=employee_card(name, ref, data),
        layout=layout,
    ).suggest(employee_suggestions())


def _employee_fields(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    person = _person(intent, state)

    if isinstance(person, Reply):
        return person

    ref, name, data = person

    state.last_operation = f"read roster {name}"

    return field_reply(
        name,
        [(field, data.get(field)) for field in intent.fields],
        kind="employee",
    ).suggest(employee_suggestions())


def _employee_availability(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    intent.fields = []

    person = _person(intent, state)

    if isinstance(person, Reply):
        return person

    ref, name, _ = person

    result = get_roster_service().get_employee_availability(ref, intent.date or "today")

    if not result.get("ok"):
        return _failure(result, state)

    day, value = next(iter(result["availability"].items()))

    state.last_operation = f"read availability {name} {day}"

    if result.get("no_roster_for"):
        return text_reply(f"{name} has no roster for {result['no_roster_for'][0]}, so availability on {day} is unknown.")

    if value in (None, ""):
        return text_reply(f"No availability is recorded for {name} on {day}.")

    code = str(value).strip().upper()

    available = code == "Y"

    word = _AVAILABILITY_WORDS.get(code, str(value))

    if code == "NA":
        text = f"**{name}** isn't scheduled on {day}: it's a weekend / non-working day."

    else:
        text = f"**{name}** is {'**available**' if available else '**not available**'} on {day}."

    tone = "success" if available else "info" if code == "NA" else "warning"

    return Reply(
        text=text,
        structured={
            "type": "field",
            "title": name,
            "entity": {"kind": "employee", "id": ref},
            "field": "availability",
            "label": f"Availability on {day}",
            "value": word,
            "text": text,
            "tone": tone,
        },
    ).suggest(employee_suggestions(asked_availability=True))


def _employee_delete(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    intent.fields = []

    person = _person(intent, state)

    if isinstance(person, Reply):
        return person

    ref, name, _ = person

    result = get_roster_service().delete_roster_entry(ref, actor)

    if not result.get("ok"):
        return _failure(result, state)

    state.clear_focus("employee", str(ref).lower())

    state.last_operation = f"delete roster {name}"

    return message_confirmation(
        f"Deleted the roster entry for {name} ({result['month']}).",
        entity=name,
        operation="delete",
        kind="employee",
    )


def _team_availability(intent: Intent, state: SessionState, actor: Optional[str]) -> Optional[Reply]:
    """Everyone on a team for one day, grouped. None = not a team: ask the model."""

    result = get_roster_service().get_team_availability(intent.query, intent.date or "today")

    if not result.get("ok"):

        # Unknown team name (the router only guessed it): let the model try.
        if result.get("error") == "not_found" and "query" in result:
            return None

        return error_reply(result.get("message") or "I couldn't complete that.")

    day = result["date"]

    team = result.get("team_name") or result.get("team_id")

    groups: Dict[str, List[str]] = {
        "Available": [],
        "Not available": [],
        "Non-working day": [],
        "No data": [],
    }

    for member in result["members"]:

        value = str(member.get("availability") or "").strip().upper()

        label = _AVAILABILITY_WORDS.get(value, "No data")

        groups[label].append(member.get("name") or member.get("ref"))

    focus_team(state, result.get("team_id"), result.get("team_name") or "")

    state.last_operation = f"read team availability {team} {day}"

    lines = [
        f"- {label} ({len(names)}): {', '.join(names)}"
        for label, names in groups.items()
        if names
    ]

    title = f"{team} - availability on {day}"

    total = len(result["members"])

    if len(groups["Non-working day"]) == total:
        lead = f"**{day}** is a weekend / non-working day for **{team}**, so nobody is scheduled:"

    else:
        lead = f"On **{day}**, {len(groups['Available'])} of {total} in **{team}** are available:"

    text = f"{lead}\n\n" + "\n".join(lines)

    return Reply(
        text=text,
        structured={
            "type": "table",
            "title": title,
            "summary": lead,
            "columns": ["Name", "Shift", "Availability"],
            "rows": [
                {
                    "Name": member.get("name"),
                    "Shift": member.get("shift"),
                    "Availability": _AVAILABILITY_WORDS.get(str(member.get("availability") or "").upper(), "No data"),
                }
                for member in result["members"]
            ],
            "shown": total,
            "total": total,
            "text": text,
            "tone": "info",
        },
    ).suggest(team_suggestions(str(team)))


# ============================================================
# LISTS
# ============================================================

def _incident_table(title: str, result: Dict[str, Any]) -> Reply:

    if not result.get("ok"):
        return error_reply(result.get("message") or "I couldn't complete that.")

    rows = result["incidents"]

    if not rows:
        return text_reply(f"No incidents found for {title.lower()}.")

    footer = (
        f"Showing {result['count']} of {result['total']}."
        if result["total"] > result["count"]
        else ""
    )

    return table_reply(
        title,
        INCIDENT_LIST_FIELDS,
        rows,
        footer,
        total=result["total"],
    ).suggest(list_suggestions([row["ticket_id"] for row in rows if row.get("ticket_id")]))


def _sla_list(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    kind = intent.query or "all"

    result = get_incident_service().list_sla_breaches(kind)

    title = "SLA breaches" if kind == "all" else f"{kind.capitalize()} SLA breaches"

    return _incident_table(title, result)


def _incident_list(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    result = get_incident_service().list_incidents_by_date(intent.date or "today")

    return _incident_table(f"Incidents on {result.get('date', intent.date)}", result)


def _category_list(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    result = get_categorization_service().list_categories()

    if not result.get("ok"):
        return error_reply(result.get("message") or "I couldn't complete that.")

    return list_reply(
        "Categories",
        [
            f"{item['name']} ({len(item['sub_categories'])} sub-categories)"
            for item in result["categories"]
        ],
    )


# ============================================================
# CLARIFICATION
# ============================================================

def _clarify(intent: Intent, state: SessionState, actor: Optional[str]) -> Reply:

    if intent.pending:
        state.set_pending(**intent.pending)

    return question_reply(intent.clarification or "Could you clarify?", intent.options or None)


# ============================================================
# DISPATCH
# ============================================================

_HANDLERS: Dict[str, Callable[[Intent, SessionState, Optional[str]], Reply]] = {
    "category_list": _category_list,
    "entity_view": _entity_view,
    "entity_fields": _entity_fields,
    "entity_update": _entity_update,
    "entity_delete": _entity_delete,
    "employee_view": _employee_view,
    "employee_fields": _employee_fields,
    "employee_availability": _employee_availability,
    "employee_delete": _employee_delete,
    "team_availability": _team_availability,
    "sla_list": _sla_list,
    "incident_list": _incident_list,
    "clarify": _clarify,
}


def can_execute(intent: Intent) -> bool:
    return intent.route in _HANDLERS


def execute(intent: Intent, state: SessionState, actor: Optional[str]) -> Optional[Reply]:
    """Run a routed intent. None means "not something I can answer": use the
    model. Raises only on programming errors."""

    return _HANDLERS[intent.route](intent, state, actor)
