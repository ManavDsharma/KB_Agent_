"""
How answers look to the user.

Turns raw fields into what a chat UI can show well, without any model call:

    * a conversational sentence for a lookup ("INC0000258 is assigned to ...")
    * an entity card: title, subtitle, coloured badges, grouped sections, link
    * follow-up suggestions the user can tap ("Is it breached?")

The Markdown text of a reply stays complete on its own, so a client that
only renders Markdown still looks good; a richer client renders the
structured card instead.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.schema import label as field_label

Pair = Tuple[str, Any]


# ============================================================
# TONES (badge / message colours)
# ============================================================

def priority_tone(value: Any) -> str:

    text = str(value or "").lower()

    if text.startswith("p1") or "critical" in text:
        return "critical"

    if text.startswith("p2") or "high" in text:
        return "warning"

    if text.startswith("p3") or "medium" in text:
        return "info"

    return "neutral"


def state_tone(value: Any) -> str:

    text = str(value or "").lower()

    if any(word in text for word in ("closed", "resolved", "complete", "done")):
        return "success"

    if any(word in text for word in ("hold", "pending", "waiting")):
        return "warning"

    if any(word in text for word in ("cancel", "reject")):
        return "neutral"

    return "info"


def _truthy(value: Any) -> bool:

    if isinstance(value, bool):
        return value

    return str(value).strip().lower() in {"true", "yes", "y", "1"}


def _has(value: Any) -> bool:
    return value not in (None, "", [], {})


# ============================================================
# SECTIONS (which fields belong together)
# ============================================================

INCIDENT_SECTIONS = [
    ("People", ["assigned_to", "opened_by", "opened_for", "team_id"]),
    ("Classification", ["category", "sub_category", "urgency"]),
    (
        "SLA",
        [
            "assignment_sla",
            "resolution_sla",
            "assignment_duration",
            "resolution_duration",
            "has_assignment_sla_breach",
            "has_resolution_sla_breach",
        ],
    ),
    ("Timeline", ["created_at", "updated_at", "closed_at"]),
    ("Details", ["description"]),
]

ISSUE_SECTIONS = [
    ("Classification", ["category_name", "sub_category_name", "issue_type"]),
    ("Impact", ["impact", "urgency", "affected_service", "business_impact"]),
    ("Routing", ["team_id", "standard_request_id", "active"]),
    ("Details", ["issue_description"]),
]

EMPLOYEE_SECTIONS = [
    ("Role", ["team_name", "team_id", "level", "shift", "shift_start_time", "shift_end_time"]),
    ("Contact", ["employee_id", "emp_id", "team_email_id"]),
]


def group_sections(
    pairs: Sequence[Pair],
    layout: Sequence[Tuple[str, Sequence[str]]],
    format_value,
) -> List[Dict[str, Any]]:
    """Fields grouped for a card; empty sections and empty values are left out."""

    values = dict(pairs)

    sections = []

    for title, names in layout:

        items = [
            {"field": name, "label": field_label(name), "value": format_value(name, values[name])}
            for name in names
            if _has(values.get(name))
        ]

        if items:
            sections.append({"title": title, "items": items})

    return sections


# ============================================================
# ENTITY CARDS
# ============================================================

def incident_card(ticket_id: str, data: Dict[str, Any]) -> Dict[str, Any]:

    badges = []

    if _has(data.get("priority")):
        badges.append({"label": str(data["priority"]), "tone": priority_tone(data["priority"])})

    if _has(data.get("state")):
        badges.append({"label": str(data["state"]), "tone": state_tone(data["state"])})

    breached = [
        label
        for label, key in (("Assignment", "has_assignment_sla_breach"), ("Resolution", "has_resolution_sla_breach"))
        if _truthy(data.get(key))
    ]

    if breached:
        badges.append({"label": f"{' & '.join(breached)} SLA breached", "tone": "critical"})

    bits = []

    if _has(data.get("priority")):
        bits.append(f"**{data['priority']}**")

    if _has(data.get("state")):
        bits.append(f"**{data['state']}**")

    lead = f"Here's **{ticket_id}**"

    if _has(data.get("short_description")):
        lead += f" — {data['short_description']}"

    lead += "."

    if bits:
        lead += f" It's {' and '.join(bits)}"

        lead += (
            f", assigned to {data['assigned_to']}."
            if _has(data.get("assigned_to"))
            else ", and not assigned to anyone yet."
        )

    link = (
        {"label": "Open in ITSM", "url": data["url"]}
        if _has(data.get("url")) and str(data["url"]).startswith("http")
        else None
    )

    return {
        "kind": "incident",
        "id": ticket_id,
        "subtitle": data.get("short_description"),
        "badges": badges,
        "lead": lead,
        "link": link,
    }


def issue_card(issue_id: str, data: Dict[str, Any]) -> Dict[str, Any]:

    badges = []

    for key in ("impact", "urgency"):

        if _has(data.get(key)):
            badges.append({"label": f"{field_label(key)}: {data[key]}", "tone": priority_tone(data[key])})

    if _has(data.get("issue_type")):
        badges.append({"label": str(data["issue_type"]), "tone": "info"})

    where = " › ".join(str(data[key]) for key in ("category_name", "sub_category_name") if _has(data.get(key)))

    lead = f"**{issue_id}**"

    if _has(data.get("issue")):
        lead += f" is **{data['issue']}**"

    if where:
        lead += f", under {where}"

    if _has(data.get("team_id")):
        lead += f", owned by **{data['team_id']}**"

    return {
        "kind": "issue",
        "id": issue_id,
        "subtitle": data.get("issue"),
        "badges": badges,
        "lead": lead + ".",
        "link": None,
    }


def employee_card(name: str, ref: str, data: Dict[str, Any]) -> Dict[str, Any]:

    badges = []

    if _has(data.get("level")):
        badges.append({"label": str(data["level"]), "tone": "info"})

    if _has(data.get("shift")):
        badges.append({"label": f"Shift {data['shift']}", "tone": "neutral"})

    if str(data.get("active", "Yes")).strip().lower() in {"no", "false", "n"}:
        badges.append({"label": "Inactive", "tone": "warning"})

    parts = []

    if _has(data.get("level")):
        parts.append(f"level **{data['level']}**")

    if _has(data.get("shift")):
        parts.append(f"on the **{data['shift']}** shift")

    team = data.get("team_name") or data.get("team_id")

    lead = f"**{name}**"

    if parts or team:
        lead += " is " + " ".join(parts) if parts else " is"

        if team:
            lead += f" in {team}"

    return {
        "kind": "employee",
        "id": ref,
        "subtitle": team,
        "badges": badges,
        "lead": lead + ".",
        "link": None,
    }


# ============================================================
# ONE-FIELD ANSWERS AS SENTENCES
# ============================================================

_INCIDENT_SENTENCES = {
    "assigned_to": ("{id} is assigned to **{v}**.", "{id} isn't assigned to anyone yet."),
    "opened_by": ("{id} was opened by **{v}**.", "No opener is recorded for {id}."),
    "opened_for": ("{id} was opened for **{v}**.", "No requester is recorded for {id}."),
    "priority": ("{id} has priority **{v}**.", "{id} has no priority set."),
    "urgency": ("The urgency of {id} is **{v}**.", "{id} has no urgency set."),
    "state": ("{id} is **{v}**.", "{id} has no state recorded."),
    "category": ("{id} is in the **{v}** category.", "{id} has no category."),
    "sub_category": ("{id} is in the **{v}** sub-category.", "{id} has no sub-category."),
    "team_id": ("{id} is handled by **{v}**.", "{id} has no team assigned."),
    "created_at": ("{id} was created on **{v}**.", "No creation time is recorded for {id}."),
    "updated_at": ("{id} was last updated on **{v}**.", "{id} has not been updated."),
    "closed_at": ("{id} was closed on **{v}**.", "{id} is not closed yet."),
}

_ISSUE_SENTENCES = {
    "team_id": ("{id} is owned by **{v}**.", "{id} has no owning team."),
    "issue_type": ("{id} is a **{v}**.", "{id} has no type."),
    "impact": ("The impact of {id} is **{v}**.", "{id} has no impact set."),
    "urgency": ("The urgency of {id} is **{v}**.", "{id} has no urgency set."),
    "category_name": ("{id} is in the **{v}** category.", "{id} has no category."),
    "sub_category_name": ("{id} is in the **{v}** sub-category.", "{id} has no sub-category."),
    "affected_service": ("{id} affects **{v}**.", "{id} has no affected service."),
}

_EMPLOYEE_SENTENCES = {
    "shift": ("{id} works the **{v}** shift.", "No shift is recorded for {id}."),
    "level": ("{id} is level **{v}**.", "No level is recorded for {id}."),
    "team_name": ("{id} is in **{v}**.", "{id} has no team recorded."),
    "team_id": ("{id} is in **{v}**.", "{id} has no team recorded."),
    "employee_id": ("{id}'s e-mail is **{v}**.", "No e-mail is recorded for {id}."),
    "emp_id": ("{id}'s employee ID is **{v}**.", "No employee ID is recorded for {id}."),
}

_SENTENCES = {
    "incident": _INCIDENT_SENTENCES,
    "issue": _ISSUE_SENTENCES,
    "employee": _EMPLOYEE_SENTENCES,
}


def field_sentence(kind: str, entity: str, field: str, value: Any) -> Optional[str]:
    """A natural sentence for a single-field answer, or None to use the plain form."""

    template = _SENTENCES.get(kind, {}).get(field)

    if template is None:
        return None

    if not _has(value):
        return template[1].format(id=f"**{entity}**")

    return template[0].format(id=f"**{entity}**", v=value)


# ============================================================
# FOLLOW-UP SUGGESTIONS
# ============================================================

def _chips(*texts: str) -> List[Dict[str, str]]:
    return [{"label": text, "send": text} for text in texts]


_INCIDENT_FOLLOW_UPS = [
    ("assigned_to", "Who is assigned to it?"),
    ("has_assignment_sla_breach", "Is it breached?"),
    ("priority", "What is its priority?"),
    ("opened_by", "Who opened it?"),
]


def incident_suggestions(asked: Sequence[str] = ()) -> List[Dict[str, str]]:

    unasked = [text for field, text in _INCIDENT_FOLLOW_UPS if field not in asked]

    return _chips(*unasked[:3], "Update it")


def issue_suggestions(asked: Sequence[str] = ()) -> List[Dict[str, str]]:

    pool = [
        ("team_id", "Which team handles it?"),
        ("issue_type", "What type of request is it?"),
        ("impact", "What is its impact?"),
    ]

    return _chips(*[text for field, text in pool if field not in asked][:3])


def employee_suggestions(asked_availability: bool = False) -> List[Dict[str, str]]:

    if asked_availability:
        return _chips("Is he available tomorrow?", "Show his roster")

    return _chips("Is he available today?", "Is he available tomorrow?", "What are his shift timings?")


def team_suggestions(team: str) -> List[Dict[str, str]]:

    return _chips(f"Who is available tomorrow in {team}?", f"Who is on leave today in {team}?")


def after_update_suggestions(kind: str) -> List[Dict[str, str]]:

    if kind == "issue":
        return _chips("Show it", "Which team handles it?")

    return _chips("Show it", "Who is assigned to it?")


def list_suggestions(ids: Sequence[str]) -> List[Dict[str, str]]:
    return _chips(*[f"Show {item}" for item in ids[:3]])
