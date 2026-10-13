"""
Which tools does this request need?

Every model call carries the definitions of all tools it may use, and a
request makes several calls. So a roster question should not carry the
incident tools, and vice versa. This picks the narrowest tool group when a
request clearly belongs to one area, and "all" whenever it is unclear or
spans areas, so accuracy is not traded for tokens when in doubt.

Cheap, deterministic keyword matching: no model call.
"""

import re

from memory.state import SessionState
from services.ids import find_issue_ref, find_ticket_ref

_ROSTER = re.compile(
    r"\b(roster|rota|shift|shifts|available|availability|unavailable|leave|"
    r"day off|days off|off duty|employee|employees|engineer|engineers|"
    r"team members?|colleagues?|schedule|working today)\b",
    re.IGNORECASE,
)

_INCIDENT = re.compile(
    r"\b(incidents?|tickets?|requests?|sla|breach(?:es|ed)?|priority|urgency|"
    r"assigned|assignee|opened|closed|resolved|state|status|working notes|"
    r"p[1-5])\b",
    re.IGNORECASE,
)

_CATEGORY = re.compile(
    r"\b(issues?|categor(?:y|ies)|sub-?categor(?:y|ies)|matrix|"
    r"categori[sz]ation|categori[sz]e|impact|affected service|standard request)\b",
    re.IGNORECASE,
)

_HANDLERS = re.compile(
    r"\b(who (?:should|can|could|will|would) (?:handle|take|own|be assigned|work on|pick up|look at|fix|resolve)|"
    r"who handles|who takes|who owns|who to assign|which team should|"
    r"recommend(?:ed)? (?:a |an )?(?:team|engineer|assignee))\b",
    re.IGNORECASE,
)

_EMPLOYEE_PRONOUN = re.compile(r"\b(he|she|his|her|him)\b", re.IGNORECASE)

_REFERENCE = re.compile(r"\b(it|its|this|that|the same)\b", re.IGNORECASE)

_FOCUS_DOMAIN = {
    "incident": "incident",
    "issue": "category",
    "employee": "roster",
    "team": "roster",
}


def classify_domain(text: str, state: SessionState) -> str:
    """One of: roster, incident, category, handlers, all."""

    if find_ticket_ref(text):
        matched = {"incident"}

    elif find_issue_ref(text):
        matched = {"category"}

    else:
        matched = set()

    if _ROSTER.search(text):
        matched.add("roster")

    if _INCIDENT.search(text):
        matched.add("incident")

    if _CATEGORY.search(text):
        matched.add("category")

    # "Who should handle a new Identity Lifecycle issue?" needs the
    # issue -> team -> availability chain (and nothing else).
    if _HANDLERS.search(text):

        # A ticket in the question needs the incident tools as well.
        return "handlers" if matched <= {"category", "roster"} else "all"

    if len(matched) == 1:
        return next(iter(matched))

    if matched:
        return "all"

    # No topic words: fall back on what is being discussed ("why is he off?").
    if _EMPLOYEE_PRONOUN.search(text) and state.get_focus("employee") is not None:
        return "roster"

    if _REFERENCE.search(text) and state.last_type in _FOCUS_DOMAIN:
        return _FOCUS_DOMAIN[state.last_type]

    return "all"
