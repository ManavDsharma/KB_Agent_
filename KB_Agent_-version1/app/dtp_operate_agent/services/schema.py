"""
Field catalogue shared by the router, the services and the response
builders: which fields exist on each record type, what people call
them, and how to display them.
"""

import re
from typing import Dict, Iterable, List, Tuple


# ============================================================
# FIELD LISTS
# ============================================================

INCIDENT_FIELDS = [
    "ticket_id",
    "url",
    "source_system",
    "description",
    "short_description",
    "category",
    "sub_category",
    "urgency",
    "priority",
    "team_id",
    "assigned_to",
    "opened_by",
    "opened_for",
    "state",
    "assignment_sla",
    "resolution_sla",
    "assignment_duration",
    "has_assignment_sla_breach",
    "resolution_duration",
    "has_resolution_sla_breach",
    "working_notes",
    "created_at",
    "updated_at",
    "closed_at",
]

# Everything except the long free-text fields. This is what a
# "show me <ticket>" answer and the session cache hold.
INCIDENT_VIEW_FIELDS = [
    "ticket_id",
    "short_description",
    "category",
    "sub_category",
    "priority",
    "urgency",
    "state",
    "team_id",
    "assigned_to",
    "opened_by",
    "opened_for",
    "assignment_sla",
    "resolution_sla",
    "assignment_duration",
    "resolution_duration",
    "has_assignment_sla_breach",
    "has_resolution_sla_breach",
    "created_at",
    "updated_at",
    "closed_at",
    "url",
    "source_system",
]

INCIDENT_LIST_FIELDS = [
    "ticket_id",
    "short_description",
    "priority",
    "state",
    "assigned_to",
    "team_id",
    "created_at",
]

# Fields that are computed by the ITSM / SLA process or by the
# service itself. The agent never writes them.
INCIDENT_READ_ONLY_FIELDS = {
    "ticket_id",
    "created_at",
    "updated_at",
    "closed_at",
    "assignment_duration",
    "resolution_duration",
    "has_assignment_sla_breach",
    "has_resolution_sla_breach",
}

INCIDENT_TEXT_FIELDS = {
    "description",
    "short_description",
    "working_notes",
    "url",
    "source_system",
}

INCIDENT_NUMERIC_FIELDS = {
    "assignment_sla",
    "resolution_sla",
}

INCIDENT_PERSON_FIELDS = {
    "assigned_to",
    "opened_by",
    "opened_for",
}

ISSUE_FIELDS = [
    "issue_id",
    "category_name",
    "category_description",
    "sub_category_name",
    "sub_category_description",
    "issue",
    "issue_description",
    "issue_type",
    "impact",
    "urgency",
    "affected_service",
    "business_impact",
    "team_id",
    "standard_request_id",
    "active",
    "created_on",
    "updated_on",
    "updated_by",
]

ISSUE_VIEW_FIELDS = [
    "issue_id",
    "issue",
    "issue_description",
    "category_name",
    "sub_category_name",
    "issue_type",
    "impact",
    "urgency",
    "affected_service",
    "business_impact",
    "team_id",
    "standard_request_id",
    "active",
]

ISSUE_LIST_FIELDS = [
    "issue_id",
    "issue",
    "category_name",
    "sub_category_name",
    "issue_type",
    "team_id",
]

ISSUE_UPDATABLE_FIELDS = {
    "category_name",
    "category_description",
    "sub_category_name",
    "sub_category_description",
    "issue",
    "issue_description",
    "issue_type",
    "impact",
    "urgency",
    "affected_service",
    "business_impact",
    "team_id",
    "standard_request_id",
    "active",
}

# Enumerated (observed) values are validated for these.
ISSUE_ENUM_FIELDS = {
    "issue_type",
    "impact",
    "urgency",
    "team_id",
}

# Roster rows are one per employee per month (year, month). `emp_id` is
# the employee number; `employee_id` holds the employee's e-mail address,
# which is also what incidents store in assigned_to / opened_by.
EMPLOYEE_VIEW_FIELDS = [
    "UID",
    "emp_id",
    "employee_id",
    "employee_name",
    "team_id",
    "team_name",
    "team_email_id",
    "level",
    "shift",
    "shift_start_time",
    "shift_end_time",
    "year",
    "month",
    "active",
]

EMPLOYEE_UPDATABLE_FIELDS = {
    "employee_name",
    "emp_id",
    "employee_id",
    "team_id",
    "team_name",
    "team_email_id",
    "level",
    "shift",
    "shift_start_time",
    "shift_end_time",
    "active",
}

BOOLEAN_FIELDS = {"active"}


# ============================================================
# NATURAL-LANGUAGE ALIASES
# ============================================================

_BREACH = ["has_assignment_sla_breach", "has_resolution_sla_breach"]

INCIDENT_ALIASES: Dict[str, List[str]] = {
    "assigned to": ["assigned_to"],
    "assignee": ["assigned_to"],
    "assigned": ["assigned_to"],
    "owner": ["assigned_to"],
    "owned by": ["assigned_to"],
    "who owns": ["assigned_to"],
    "handled by": ["assigned_to"],
    "who is handling": ["assigned_to"],
    "opened by": ["opened_by"],
    "raised by": ["opened_by"],
    "created by": ["opened_by"],
    "reported by": ["opened_by"],
    "who opened": ["opened_by"],
    "who raised": ["opened_by"],
    "who created": ["opened_by"],
    "who reported": ["opened_by"],
    "reporter": ["opened_by"],
    "opened for": ["opened_for"],
    "raised for": ["opened_for"],
    "on behalf of": ["opened_for"],
    "priority": ["priority"],
    "urgency": ["urgency"],
    "state": ["state"],
    "status": ["state"],
    "sub category": ["sub_category"],
    "subcategory": ["sub_category"],
    "category": ["category"],
    "team id": ["team_id"],
    "team": ["team_id"],
    "short description": ["short_description"],
    "summary": ["short_description"],
    "title": ["short_description"],
    "description": ["description"],
    "working notes": ["working_notes"],
    "work notes": ["working_notes"],
    "notes": ["working_notes"],
    "assignment sla": ["assignment_sla"],
    "resolution sla": ["resolution_sla"],
    "sla": ["assignment_sla", "resolution_sla"],
    "assignment duration": ["assignment_duration"],
    "resolution duration": ["resolution_duration"],
    "sla breach": _BREACH,
    "sla breaches": _BREACH,
    "breached": _BREACH,
    "breach": _BREACH,
    "created at": ["created_at"],
    "created on": ["created_at"],
    "created": ["created_at"],
    "opened at": ["created_at"],
    "opened on": ["created_at"],
    "updated at": ["updated_at"],
    "updated on": ["updated_at"],
    "last updated": ["updated_at"],
    "closed at": ["closed_at"],
    "closed on": ["closed_at"],
    "close date": ["closed_at"],
    "when was it closed": ["closed_at"],
    "closed": ["state"],
    "resolved": ["state"],
    "url": ["url"],
    "link": ["url"],
    "source system": ["source_system"],
}

ISSUE_ALIASES: Dict[str, List[str]] = {
    "category description": ["category_description"],
    "sub category description": ["sub_category_description"],
    "sub category name": ["sub_category_name"],
    "sub category": ["sub_category_name"],
    "subcategory": ["sub_category_name"],
    "category name": ["category_name"],
    "category": ["category_name"],
    "issue description": ["issue_description"],
    "description": ["issue_description"],
    "issue name": ["issue"],
    "issue title": ["issue"],
    "title": ["issue"],
    "issue type": ["issue_type"],
    "type of request": ["issue_type"],
    "request type": ["issue_type"],
    "type": ["issue_type"],
    "business impact": ["business_impact"],
    "impact": ["impact"],
    "urgency": ["urgency"],
    "affected service": ["affected_service"],
    "service": ["affected_service"],
    "team id": ["team_id"],
    "team": ["team_id"],
    "handles": ["team_id"],
    "handled by": ["team_id"],
    "owner": ["team_id"],
    "standard request id": ["standard_request_id"],
    "standard request": ["standard_request_id"],
    "active": ["active"],
    "enabled": ["active"],
}

EMPLOYEE_ALIASES: Dict[str, List[str]] = {
    "shift start time": ["shift_start_time"],
    "shift start": ["shift_start_time"],
    "start time": ["shift_start_time"],
    "shift end time": ["shift_end_time"],
    "shift end": ["shift_end_time"],
    "end time": ["shift_end_time"],
    "shift timings": ["shift", "shift_start_time", "shift_end_time"],
    "shift timing": ["shift", "shift_start_time", "shift_end_time"],
    "shift times": ["shift", "shift_start_time", "shift_end_time"],
    "shift time": ["shift", "shift_start_time", "shift_end_time"],
    "timings": ["shift", "shift_start_time", "shift_end_time"],
    "shift": ["shift"],
    "level": ["level"],
    "grade": ["level"],
    "team email": ["team_email_id"],
    "team email id": ["team_email_id"],
    "team name": ["team_name"],
    "team id": ["team_id"],
    "team": ["team_name"],
    "email": ["employee_id"],
    "mail": ["employee_id"],
    "employee id": ["emp_id"],
    "emp id": ["emp_id"],
    "uid": ["UID"],
    "active": ["active"],
}

ALIASES_BY_TYPE = {
    "incident": INCIDENT_ALIASES,
    "issue": ISSUE_ALIASES,
    "employee": EMPLOYEE_ALIASES,
}

# ============================================================
# LABELS
# ============================================================

LABELS = {
    "ticket_id": "Ticket",
    "issue_id": "Issue",
    "UID": "UID",
    "emp_id": "Employee ID",
    "employee_id": "Email",
    "employee_name": "Name",
    "team_id": "Team",
    "team_name": "Team name",
    "team_email_id": "Team email",
    "shift_start_time": "Shift start",
    "shift_end_time": "Shift end",
    "assignment_sla": "Assignment SLA (min)",
    "resolution_sla": "Resolution SLA (min)",
    "assignment_duration": "Assignment duration (min)",
    "resolution_duration": "Resolution duration (min)",
    "has_assignment_sla_breach": "Assignment SLA breached",
    "has_resolution_sla_breach": "Resolution SLA breached",
    "issue": "Issue",
    "issue_type": "Issue type",
    "url": "URL",
}


def label(field: str) -> str:
    if field in LABELS:
        return LABELS[field]

    return field.replace("_", " ").capitalize()


# ============================================================
# ALIAS DETECTION
# ============================================================

def find_field_spans(
    text: str,
    aliases: Dict[str, List[str]],
) -> List[Tuple[int, int, List[str]]]:
    """
    Locate alias phrases in text.

    Longer phrases win and their span is masked, so "sub category"
    is not also read as "category". Matching is on whole words.

    Returns (start, end, fields) tuples ordered by position.
    """

    work = text.lower().replace("_", " ")

    spans = []

    for phrase in sorted(aliases, key=len, reverse=True):

        pattern = re.compile(
            r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"
        )

        for match in pattern.finditer(work):

            if work[match.start():match.end()].strip() == "":
                continue

            spans.append(
                (match.start(), match.end(), list(aliases[phrase]))
            )

            work = (
                work[: match.start()]
                + " " * (match.end() - match.start())
                + work[match.end():]
            )

    spans.sort(key=lambda item: item[0])

    return spans


def detect_fields(
    text: str,
    aliases: Dict[str, List[str]],
) -> List[str]:
    """Distinct field names mentioned in text, in order of appearance."""

    fields: List[str] = []

    for _, _, names in find_field_spans(text, aliases):

        for name in names:

            if name not in fields:
                fields.append(name)

    return fields


def resolve_field_names(
    entity_type: str,
    names: Iterable[str],
) -> List[str]:
    """
    Turn model/user supplied field names into real field names.

    Accepts real names ("assigned_to") and aliases ("assignee", "sla").
    Unknown names are dropped.
    """

    aliases = ALIASES_BY_TYPE.get(entity_type, {})

    known = {
        "incident": set(INCIDENT_FIELDS),
        "issue": set(ISSUE_FIELDS),
        "employee": set(EMPLOYEE_VIEW_FIELDS),
    }.get(entity_type, set())

    resolved: List[str] = []

    for raw in names or []:

        name = str(raw).strip()

        if not name:
            continue

        candidates = [name]

        if name not in known:

            key = name.lower().replace("_", " ")

            candidates = aliases.get(key, [])

            if not candidates:
                candidates = [
                    field for field in known
                    if field.lower() == name.lower()
                ]

        for field in candidates:

            if field in known and field not in resolved:
                resolved.append(field)

    return resolved
