"""
Deterministic router.

Turns a user message plus the session state into an Intent, without
calling a model. It is deliberately conservative: it only claims a
request when the target and the wish are unambiguous, and returns the
"llm" route for anything open-ended, multi-part or analytical.

Order of work
    1. answer a pending clarification ("Update it." -> "Set priority to P2")
    2. explicit identifiers (INC0000258, Issue-1)
    3. references resolved from session state (it, its, he, this ticket)
    4. what the user wants: read, read some fields, update, delete
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from config import PLANNER_MAX_WORDS

from memory.state import SessionState

from plan_text import (
    PLURAL_TAIL,
    WRITE_VERB,
    apply_answer,
    has_plural_reference,
    is_no,
    is_yes,
    pick_option,
)

from services.ids import (
    find_all_issue_refs,
    find_all_ticket_refs,
    find_issue_ref,
    find_ticket_ref,
    ticket_key,
)

from services.schema import (
    ALIASES_BY_TYPE,
    EMPLOYEE_ALIASES,
    detect_fields,
    find_field_spans,
    label as field_label,
)


MAX_WORDS = 24


# ============================================================
# INTENT
# ============================================================

@dataclass
class Intent:
    route: str = "llm"
    operation: str = "unknown"
    entity_type: Optional[str] = None
    entity_ref: Optional[str] = None
    query: Optional[str] = None
    fields: List[str] = field(default_factory=list)
    changes: Dict[str, str] = field(default_factory=dict)
    date: Optional[str] = None
    from_context: bool = False
    reason: str = ""
    clarification: Optional[str] = None
    options: List[str] = field(default_factory=list)
    pending: Optional[Dict[str, Any]] = None
    # route "plan": default targets for "it / them" and the planner's token use
    targets: List[str] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)


def _llm(reason: str) -> Intent:
    return Intent(route="llm", reason=reason)


def _plan(
    raw: str,
    entity_type: Optional[str] = None,
    entity_ref: Optional[str] = None,
    from_context: bool = False,
    reason: str = "write request",
) -> Intent:
    """A write the fast path cannot fully understand: the planner reads it."""

    return Intent(
        route="plan",
        operation="update",
        entity_type=entity_type,
        entity_ref=entity_ref,
        query=raw,
        from_context=from_context,
        reason=reason,
    )


def _ask(question: str, pending: Optional[Dict[str, Any]] = None, reason: str = "") -> Intent:
    return Intent(
        route="clarify",
        clarification=question,
        pending=pending,
        reason=reason or "clarification",
    )


# ============================================================
# PATTERNS
# ============================================================

_REASONING = re.compile(
    r"\b(why|how|compare|comparison|similar|recommend|should|trend|"
    r"analy[sz]e|summari[sz]e|difference|troubleshoot|steps?|fix|"
    r"workaround|solution|root cause|best)\b",
    re.IGNORECASE,
)

_UPDATE = re.compile(
    r"\b(update|change|modify|edit|set|assign|reassign|close|resolve)\b",
    re.IGNORECASE,
)

_DELETE = re.compile(r"\b(delete|remove|drop)\b", re.IGNORECASE)

# "both", "all", "each": the sentence covers more than one thing.
_COLLECTIVE = re.compile(r"\b(both|all|each|every|everyone|everybody|respectively)\b", re.IGNORECASE)

# What follows a field name in "set priority to P2" / "priority should be P2".
_VALUE_AFTER_FIELD = re.compile(
    r"^\s*(?:(?:of|for|on|in)\s+(?:ENT|it|this|that|the\s+"
    r"(?:incident|ticket|request|issue)))?\s*"
    r"(?:to|as|=|:|is|should be)\s+(?P<value>.+?)\s*$",
    re.IGNORECASE,
)

# A roster change: availability, leave, shift ...
_ROSTER_WRITE = re.compile(
    r"\b(available|availability|unavailable|leave|off duty|day off|days off|shift|shifts|"
    r"roster|rota|schedule|level|timings?)\b",
    re.IGNORECASE,
)

_CREATE = re.compile(
    r"\b(create|raise|log|open|add|register)\s+(?:a\s+|an\s+|new\s+|another\s+)+"
    r"(?:incident|ticket|request|roster|employee|entry|record)",
    re.IGNORECASE,
)

_SHOW = re.compile(
    r"^\s*(?:please\s+)?(?:can you\s+|could you\s+)?"
    r"(?:show|get|display|open|view|fetch|find|give|tell|details|info|lookup|look up|"
    r"check|describe|explain|what(?:'s| is| are| does)|whats|who is)\b",
    re.IGNORECASE,
)

_DEFINITION = re.compile(
    r"\b(explain|describe|represent|meaning of|what does .* mean)\b",
    re.IGNORECASE,
)

_IT = re.compile(r"\b(it|its)\b", re.IGNORECASE)

_REF_INCIDENT = re.compile(
    r"\b(?:this|that|the|same)\s+(incident|ticket|request)\b", re.IGNORECASE
)

_REF_ISSUE = re.compile(r"\b(?:this|that|the|same)\s+issue\b", re.IGNORECASE)

_REF_EMPLOYEE = re.compile(
    r"\b(?:he|she|his|her|him|hers)\b|"
    r"\b(?:this|that|the)\s+(?:employee|person|engineer|member|user)\b",
    re.IGNORECASE,
)

_REF_ONE = re.compile(
    r"\b(?:this|that|the previous|the last|the same)\s+one\b", re.IGNORECASE
)

_ABOUT = re.compile(r"^\s*(?:and\s+)?(?:what|how)\s+about\b", re.IGNORECASE)

_CATEGORY_LIST = (
    "all categories",
    "category names",
    "list categories",
    "list the categories",
    "categories available",
    "available categories",
    "show categories",
    "show me categories",
)

_SLA_LIST = re.compile(
    r"^\s*(?:please\s+)?(?:list|show|display|get|give|what are|which are)?\s*(?:me\s+)?"
    r"(?:all\s+|the\s+|any\s+)*(?:(?P<kind>assignment|resolution)\s+)?"
    r"(?:sla\s+breach(?:es|ed)?|breached\s+(?:sla\s+)?(?:incidents|tickets)"
    r"|(?:incidents|tickets)\s+(?:with|having)\s+(?:an?\s+)?(?:(?:assignment|resolution)\s+)?sla\s+breach(?:es)?)"
    r"(?:\s+(?:incidents|tickets))?\s*[?.!]*$",
    re.IGNORECASE,
)

_DATE_LIST = re.compile(
    r"^\s*(?:please\s+)?(?:list|show|display|get|give)?\s*(?:me\s+)?(?:all\s+|the\s+)*"
    r"(?:(?:incidents|tickets|requests)\s+(?:(?:created|raised|opened|logged)\s+)?"
    r"(?:on\s+|for\s+|from\s+|of\s+)?(?P<d1>today|yesterday|\d{4}-\d{2}-\d{2})"
    r"|(?P<d2>today|yesterday)['’]s\s+(?:incidents|tickets|requests))\s*[?.!]*$",
    re.IGNORECASE,
)

# A specific month (roster rows are monthly) needs the model's care.
_MONTH_WORDS = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|"
    r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b|"
    r"\b\d{4}-\d{2}(?!-\d)\b",
    re.IGNORECASE,
)

_RANGE_WORDS = re.compile(
    r"\b(week|weeks|month|weekend|next|last|from|between|until|through|days)\b",
    re.IGNORECASE,
)

_AVAILABILITY = re.compile(
    r"\b(available|availability|free|on leave|off duty|working)\b",
    re.IGNORECASE,
)

# "Who is available today in Access Management?" - a whole-team question.
_TEAM_QUESTION = re.compile(
    r"\b(who|whom|anyone|any one|everyone|which (?:employees|people|members|engineers|team members))\b",
    re.IGNORECASE,
)

# "from" is fine here ("anyone from Access Management"), unlike in _RANGE_WORDS.
_TEAM_RANGE = re.compile(
    r"\b(week|weeks|month|weekend|next|last|between|until|through|days)\b",
    re.IGNORECASE,
)

_AVAILABILITY_QUESTION = re.compile(
    r"\b(available|availability|free|working|on leave|off|absent|unavailable|on duty)\b",
    re.IGNORECASE,
)

_TEAM_AFTER_PREPOSITION = re.compile(
    r"\b(?:in|from|of|within|for)\s+(?:the\s+)?(?P<team>[A-Za-z][A-Za-z0-9&/ -]{1,50})",
    re.IGNORECASE,
)

_TEAM_STOP = re.compile(
    r"\b(?:team|today|tomorrow|yesterday|on|available|free|working|please|this|next|now)\b|[?.!,]",
    re.IGNORECASE,
)

_DATE_TOKEN = re.compile(
    r"\b(today|tomorrow|yesterday|\d{4}-\d{2}-\d{2})\b", re.IGNORECASE
)

_ROSTER_TOPIC = re.compile(
    r"\b(roster|shift|availability|available|schedule|rota|level|timings?)\b",
    re.IGNORECASE,
)

_NAME = r"[A-Za-z][A-Za-z.'@_-]*"

_NAME_POSSESSIVE = re.compile(
    rf"({_NAME}(?:\s+{_NAME})?)['’]s\s+"
    r"(?:roster|shift|availability|schedule|details|level|team|email|timings?|rota)\b",
    re.IGNORECASE,
)

_NAME_FOR = re.compile(
    r"\b(?:roster|shift|availability|schedule|details|info|information|rota)\s+"
    rf"(?:entry\s+|record\s+|details\s+|info\s+)?(?:for|of)\s+({_NAME}(?:\s+{_NAME})?)",
    re.IGNORECASE,
)

_NAME_REMOVE = re.compile(
    rf"\b(?:delete|remove|drop)\s+({_NAME}(?:\s+{_NAME})?)\s+from\s+(?:the\s+)?roster\b",
    re.IGNORECASE,
)

_NAME_AVAILABLE = re.compile(
    rf"\b(?:is|are|was)\s+({_NAME}(?:\s+{_NAME})?)\s+"
    r"(?:available|free|working|on leave|off)\b",
    re.IGNORECASE,
)

_NAME_STOP = {
    "show", "me", "get", "display", "give", "what", "whats", "what's", "is",
    "are", "was", "find", "tell", "the", "of", "for", "and", "please", "about",
    "who", "details", "detail", "info", "see", "view", "fetch", "list", "check",
    "his", "her", "their", "a", "an", "to", "us", "can", "you", "could", "then",
    "he", "she", "him", "it", "its", "they", "them", "this", "that",
    "employee", "person", "delete", "remove", "update", "change", "set",
}

# Words that appear in an answer like "the one in Infrastructure".
_CHOICE_FILLER = {
    "one", "in", "from", "on", "team", "named", "called", "with", "i", "mean",
    "meant", "choose", "pick", "select", "want", "please", "yes", "ok", "okay",
    "at", "by", "under", "belongs", "belonging", "works", "working",
}

_NAME_TAIL = {
    "today", "tomorrow", "yesterday", "on", "in", "for", "this", "next", "is",
    "please", "now", "available", "free", "shift", "roster", "details",
    "availability", "schedule", "at", "from", "entry", "record",
}


# ============================================================
# HELPERS
# ============================================================

def _clean(text: str) -> str:
    return " ".join(str(text).split())


def _mask(text: str, span: Optional[Tuple[int, int]]) -> str:
    """Replace an identifier with ENT so field words inside it are ignored."""

    if not span:
        return text

    return text[: span[0]] + "ENT" + text[span[1]:]


def _strip_value(value: str) -> str:

    value = value.strip()

    value = re.sub(r"^(?:to|as|=|:|is)\s+", "", value, flags=re.IGNORECASE)

    return value.strip().strip("\"'`").rstrip(".!?, ").strip("\"'`").strip()


def _clean_name(name: str) -> Optional[str]:
    """Drop leading filler and trailing time words from a captured name."""

    tokens = name.split()

    while tokens and tokens[0].lower() in _NAME_STOP:
        tokens.pop(0)

    while tokens and tokens[-1].lower() in _NAME_TAIL:
        tokens.pop()

    if not tokens:
        return None

    return " ".join(tokens).rstrip(".'’")


def extract_employee_query(text: str) -> Optional[str]:
    """Employee name / email mentioned in text, if any."""

    for pattern in (_NAME_POSSESSIVE, _NAME_FOR, _NAME_REMOVE, _NAME_AVAILABLE):

        match = pattern.search(text)

        if match:

            name = _clean_name(match.group(1))

            if name:
                return name

    return None


def _ordered_types(state: SessionState, allowed: Tuple[str, ...]) -> List[str]:
    """Types with a focus entity, most recently touched first."""

    types = [
        type for type in state.focus
        if type in allowed
    ]

    types.sort(
        key=lambda type: (
            state.focus[type].turn,
            1 if type == state.last_type else 0,
        ),
        reverse=True,
    )

    return types


def _typed_reference(text: str) -> Optional[str]:
    """incident / issue named by a phrase like "the ticket"."""

    if _REF_ISSUE.search(text):
        return "issue"

    if _REF_INCIDENT.search(text):
        return "incident"

    return None


def _entity_word(text: str) -> str:
    """The user's own word for the record: ticket / request / incident."""

    match = _REF_INCIDENT.search(text) or re.search(
        r"\b(ticket|request|incident)\b", text, re.IGNORECASE
    )

    if not match:
        return "incident"

    return match.group(match.lastindex or 0).lower()


# ============================================================
# ROUTER
# ============================================================

class DeterministicRouter:

    # --------------------------------------------------------
    # Entry point
    # --------------------------------------------------------

    @classmethod
    def route(cls, text: str, state: SessionState) -> Intent:

        raw = _clean(text)

        if not raw:
            return _llm("empty")

        if state.pending:

            pending_intent = cls._route_pending(raw, state)

            if pending_intent is not None:
                return pending_intent

        return cls._route_fresh(raw, state)

    @classmethod
    def _route_fresh(cls, raw: str, state: SessionState) -> Intent:

        low = raw.lower()

        if any(phrase in low for phrase in _CATEGORY_LIST):
            return Intent(route="category_list", operation="read")

        if len(raw.split()) > MAX_WORDS or raw.count("?") > 1 or ";" in raw:

            # A long write ("update A to X and B to Y ...") is read by the
            # planner; anything else long is for the model.
            if cls._write_shaped(raw) and cls._has_write_target(raw):
                return _plan(raw, reason="long write request")

            return _llm("complex")

        if _REASONING.search(raw):
            return _llm("reasoning")

        if _CREATE.search(raw):
            return _llm("create")

        ticket = find_ticket_ref(raw)

        issue = find_issue_ref(raw)

        if ticket and issue:
            return _llm("multiple entities")

        operation = cls._operation(raw)

        # Several records in one message ("close INC1 and INC2"): never act on
        # just the first one.
        if (ticket or issue) and (
            len(find_all_ticket_refs(raw)) > 1 or len(find_all_issue_refs(raw)) > 1
        ):

            if operation != "delete" and cls._write_shaped(raw):
                return _plan(raw, reason="several records")

            return _llm("multiple records")

        # A write the fast path has no pattern for ("mark INC1 as resolved").
        if (ticket or issue) and operation is None and cls._write_shaped(raw):

            return _plan(
                raw,
                "issue" if issue else "incident",
                issue[0] if issue else f"{ticket[0]}{ticket[1]}",
                reason="write request",
            )

        if not ticket and not issue:

            listing = cls._route_listing(raw)

            if listing is not None:
                return listing

            team_intent = cls._route_team_availability(raw)

            if team_intent is not None:
                return team_intent

            # "close them", "set their priority to High": the records of the last list.
            if has_plural_reference(raw) and cls._write_shaped(raw):

                group = state.get_list(None, state.mutation_max_turns)

                if group is not None:
                    return _plan(raw, group["type"], reason="records from the last list")

                if cls._has_write_target(raw):
                    return _plan(raw, reason="several records, none listed")

            # Roster changes ("mark Rohit and Priya unavailable on 2nd August").
            if _ROSTER_WRITE.search(raw) and cls._write_shaped(raw):
                return _plan(raw, "employee", reason="roster write")

            employee = cls._route_employee(raw, state, operation)

            if employee is not None:
                return employee

        if ticket:

            return cls._route_entity(
                raw,
                state,
                operation,
                "incident",
                f"{ticket[0]}{ticket[1]}",
                ticket[2],
                explicit=True,
            )

        if issue:

            return cls._route_entity(
                raw,
                state,
                operation,
                "issue",
                issue[0],
                issue[1],
                explicit=True,
            )

        return cls._route_context(raw, state, operation)

    @staticmethod
    def _write_shaped(raw: str) -> bool:
        """A request to change something, short enough for the planner."""

        if not WRITE_VERB.search(raw):
            return False

        if _REASONING.search(raw) or _DELETE.search(raw) or _CREATE.search(raw):
            return False

        return "?" not in raw and len(raw.split()) <= PLANNER_MAX_WORDS

    @staticmethod
    def _has_write_target(raw: str) -> bool:
        """Does a long write name something to change (a record, a field, the roster)?"""

        return bool(
            find_ticket_ref(raw)
            or find_issue_ref(raw)
            or _ROSTER_WRITE.search(raw)
            or any(detect_fields(raw, aliases) for aliases in ALIASES_BY_TYPE.values())
        )

    @staticmethod
    def _route_team_availability(raw: str) -> Optional[Intent]:
        """
        "Which employees are available today in Access Management?"

        Answered from the roster without the model. The team name is only a
        guess at this point; if it turns out not to be a team the request
        falls through to the model.
        """

        if not (_TEAM_QUESTION.search(raw) and _AVAILABILITY_QUESTION.search(raw)):
            return None

        if (
            extract_employee_query(raw)
            or _REF_EMPLOYEE.search(raw)
            or _TEAM_RANGE.search(raw)
            or _MONTH_WORDS.search(raw)
        ):
            return None

        without_date = _DATE_TOKEN.sub(" ", raw)

        team = None

        for match in _TEAM_AFTER_PREPOSITION.finditer(without_date):

            candidate = _TEAM_STOP.split(match.group("team"), 1)[0].strip(" -&/")

            if candidate and len(candidate.split()) <= 4:
                team = candidate
                break

        if not team:
            return None

        date_match = _DATE_TOKEN.search(raw)

        return Intent(
            route="team_availability",
            operation="read",
            entity_type="team",
            query=team,
            date=date_match.group(1).lower() if date_match else "today",
        )

    @staticmethod
    def _route_listing(raw: str) -> Optional[Intent]:
        """Whole-table questions with a fixed shape: SLA breaches, by date."""

        match = _SLA_LIST.match(raw)

        if match:

            return Intent(
                route="sla_list",
                operation="read",
                entity_type="incident",
                query=(match.group("kind") or "all").lower(),
            )

        match = _DATE_LIST.match(raw)

        if match:

            return Intent(
                route="incident_list",
                operation="read",
                entity_type="incident",
                date=(match.group("d1") or match.group("d2")).lower(),
            )

        return None

    @staticmethod
    def _operation(raw: str) -> Optional[str]:

        if _DELETE.search(raw):
            return "delete"

        if _UPDATE.search(raw):
            return "update"

        return None

    # --------------------------------------------------------
    # Entity (explicit identifier or resolved reference)
    # --------------------------------------------------------

    @classmethod
    def _route_entity(
        cls,
        raw: str,
        state: SessionState,
        operation: Optional[str],
        entity_type: str,
        entity_ref: str,
        span: Optional[Tuple[int, int]],
        explicit: bool,
    ) -> Intent:

        masked = _mask(raw, span)

        if operation == "delete":
            return cls._delete_intent(entity_type, entity_ref, explicit)

        if operation == "update":

            return cls._update_intent(
                raw,
                masked,
                state,
                entity_type,
                entity_ref,
                from_context=not explicit,
            )

        aliases = ALIASES_BY_TYPE[entity_type]

        fields = detect_fields(masked, aliases)

        base = dict(
            operation="read",
            entity_type=entity_type,
            entity_ref=entity_ref,
            from_context=not explicit,
        )

        if fields:

            if explicit and not cls._simple_read(raw, masked):
                return _llm("read with extra context")

            return Intent(route="entity_fields", fields=fields, **base)

        if cls._simple_read(raw, masked) or (
            entity_type == "issue" and _DEFINITION.search(raw)
        ):
            return Intent(route="entity_view", **base)

        return _llm("entity mentioned without a clear request")

    @staticmethod
    def _simple_read(raw: str, masked: str) -> bool:
        """A short look-up rather than a sentence that merely names a record."""

        if _SHOW.search(raw):
            return True

        remainder = re.sub(r"\bENT\b", "", masked).strip(" ?.!")

        return len(remainder.split()) <= 3

    # --------------------------------------------------------
    # References resolved from session state
    # --------------------------------------------------------

    @classmethod
    def _route_context(
        cls,
        raw: str,
        state: SessionState,
        operation: Optional[str],
    ) -> Intent:

        typed = _typed_reference(raw)

        generic = bool(_IT.search(raw) or _REF_ONE.search(raw) or _ABOUT.search(raw))

        if not typed and not generic:

            # A bare field word after some context: "priority?", "and the SLA?"
            short_field = len(raw.split()) <= 5 and any(
                detect_fields(raw, aliases)
                for aliases in (ALIASES_BY_TYPE["incident"], ALIASES_BY_TYPE["issue"])
            )

            if not short_field or operation:
                return cls._route_bare_mutation(raw, state, operation)

        mutation = operation in ("update", "delete")

        max_age = state.mutation_max_turns if mutation else None

        allowed = ("incident", "issue")

        candidates = [
            type for type in _ordered_types(state, allowed)
            if state.get_focus(type, max_age) is not None
        ]

        if typed:
            candidates = [type for type in candidates if type == typed]

        chosen = None

        for type in candidates:

            if detect_fields(raw, ALIASES_BY_TYPE[type]):
                chosen = type
                break

        if chosen is None and candidates:
            chosen = candidates[0]

        if chosen is None:
            return cls._no_context(raw, state, operation, typed)

        entity = state.get_focus(chosen, max_age)

        return cls._route_entity(
            raw,
            state,
            operation,
            chosen,
            entity.id,
            None,
            explicit=False,
        )

    @classmethod
    def _route_bare_mutation(
        cls,
        raw: str,
        state: SessionState,
        operation: Optional[str],
    ) -> Intent:
        """
        "Set the priority to P2." with no identifier and no "it".

        The field tells us the record type; recent context supplies the
        target. Anything else is left to the model.
        """

        if operation != "update":
            return _llm("no entity reference")

        types = [
            type for type, aliases in (
                ("incident", ALIASES_BY_TYPE["incident"]),
                ("issue", ALIASES_BY_TYPE["issue"]),
            )
            if detect_fields(raw, aliases)
        ]

        if not types:
            return _llm("update without a field")

        recent = [
            type for type in _ordered_types(state, ("incident", "issue"))
            if state.get_focus(type, state.mutation_max_turns) is not None
        ]

        for type in recent:

            if type in types:

                return cls._update_intent(
                    raw,
                    raw,
                    state,
                    type,
                    state.get_focus(type).id,
                    from_context=True,
                )

        type = types[0] if len(types) == 1 else "incident"

        return cls._update_intent(raw, raw, state, type, None, from_context=True)

    @classmethod
    def _no_context(
        cls,
        raw: str,
        state: SessionState,
        operation: Optional[str],
        typed: Optional[str],
    ) -> Intent:
        """A reference with nothing to refer to."""

        if operation == "update":

            type = typed or "incident"

            return cls._update_intent(raw, raw, state, type, None, from_context=True)

        if operation == "delete":

            word = _entity_word(raw)

            return _ask(f"Which {word} should I delete? Please share its ID.")

        incident_fields = detect_fields(raw, ALIASES_BY_TYPE["incident"])

        issue_fields = detect_fields(raw, ALIASES_BY_TYPE["issue"])

        if typed or _SHOW.search(raw) or incident_fields or issue_fields:

            issue_only = typed == "issue" or (issue_fields and not incident_fields and not typed)

            word = "issue" if issue_only else _entity_word(raw)

            return _ask(
                f"Which {word} do you mean? Please share the "
                f"{'issue' if issue_only else 'ticket'} ID.",
                reason="no context",
            )

        return _llm("reference without context")

    # --------------------------------------------------------
    # Update
    # --------------------------------------------------------

    @staticmethod
    def _multi_change(masked: str, aliases: Dict[str, List[str]]) -> bool:
        """
        Does the sentence ask for more than the one "<field> to <value>" the
        fast path can read? Field words inside the value ("status to resolved")
        do not count.
        """

        spans = find_field_spans(masked, aliases)

        head = masked

        if spans:

            end = spans[0][1]

            match = _VALUE_AFTER_FIELD.match(masked[end:])

            if match:

                value_start = end + match.start("value")

                head = masked[:value_start]

                others = [span for span in spans[1:] if span[0] < value_start]

                # A "value" that holds another request ("High on ENT and mark
                # Rohit unavailable") is not a value.
                value = match.group("value")

                if WRITE_VERB.search(value) or re.search(r"\bENT\b", value):
                    return True

                # "... as HIGH for both of them": the value is "HIGH", the rest names several records.
                if PLURAL_TAIL.search(" " + value):
                    return True

            else:
                others = spans[1:]

            if others:
                return True

        if _COLLECTIVE.search(head) or has_plural_reference(head):
            return True

        return len(re.findall(r"\b(?:to|as)\b", masked, re.IGNORECASE)) >= 2 and bool(
            re.search(r"\b(?:and|also|then)\b|,", masked, re.IGNORECASE)
        )

    @classmethod
    def _update_intent(
        cls,
        raw: str,
        masked: str,
        state: SessionState,
        entity_type: str,
        entity_ref: Optional[str],
        from_context: bool,
    ) -> Intent:

        aliases = ALIASES_BY_TYPE[entity_type]

        # Only claim a request that is fully understood. Several fields, "both",
        # or several "to <value>" parts go to the planner, never half-parsed.
        if cls._multi_change(masked, aliases):

            return _plan(
                raw,
                entity_type,
                entity_ref,
                from_context=from_context,
                reason="multiple changes",
            )

        changes: Dict[str, str] = {}

        missing_field = False

        target_field: Optional[str] = None

        assign = re.search(
            r"\b(?:re)?assign(?:ed)?\b(?:\s+(?:ENT|it|this|that|the\s+\w+))?"
            r"(?:\s+to\s+(?P<value>.+?))?\s*[.!?]*$",
            masked,
            re.IGNORECASE,
        )

        close = re.search(r"\b(close|resolve)\b", masked, re.IGNORECASE)

        if assign and entity_type == "incident":

            target_field = "assigned_to"

            value = _strip_value(assign.group("value") or "")

            if value:
                changes["assigned_to"] = value

        else:

            spans = [
                (start, end, names)
                for start, end, names in find_field_spans(masked, aliases)
                if len(names) == 1
            ]

            multi = [
                names
                for _, _, names in find_field_spans(masked, aliases)
                if len(names) > 1
            ]

            if spans:

                start, end, names = spans[0]

                target_field = names[0]

                remainder = masked[end:]

                match = _VALUE_AFTER_FIELD.match(remainder)

                if match:

                    value = _strip_value(match.group("value"))

                    if value:
                        changes[target_field] = value

            elif multi:
                return _llm("ambiguous field")

            elif close and entity_type == "incident":

                target_field = "state"

                changes["state"] = (
                    "Closed" if close.group(1).lower() == "close" else "Resolved"
                )

            else:
                missing_field = True

        entity_word = "issue" if entity_type == "issue" else _entity_word(raw)

        # ---- no target -------------------------------------------------
        if not entity_ref:

            return _ask(
                f"Which {entity_word} would you like to update?",
                pending={
                    "kind": "update_slots",
                    "expects": "entity",
                    "entity_type": entity_type,
                    "entity_ref": None,
                    "field": target_field,
                    "changes": changes,
                },
                reason="update without target",
            )

        # ---- target and change known ----------------------------------
        if changes:

            if len(" ".join(changes.values()).split()) > 40:
                return _llm("long value")

            return Intent(
                route="entity_update",
                operation="update",
                entity_type=entity_type,
                entity_ref=entity_ref,
                changes=changes,
                from_context=from_context,
            )

        # ---- something missing ----------------------------------------
        if target_field and not missing_field:

            return _ask(
                f"What should the {field_label(target_field).lower()} be for {entity_ref}?",
                pending={
                    "kind": "update_slots",
                    "expects": "value",
                    "entity_type": entity_type,
                    "entity_ref": entity_ref,
                    "field": target_field,
                },
                reason="update without value",
            )

        return _ask(
            f"What would you like to update for {entity_ref}?",
            pending={
                "kind": "update_slots",
                "expects": "field",
                "entity_type": entity_type,
                "entity_ref": entity_ref,
            },
            reason="update without field",
        )

    # --------------------------------------------------------
    # Delete
    # --------------------------------------------------------

    @staticmethod
    def _delete_intent(
        entity_type: str,
        entity_ref: str,
        explicit: bool,
    ) -> Intent:

        if entity_type != "incident":

            return _ask(
                "Deleting category matrix issues isn't supported. "
                "I can update the issue or mark it inactive instead.",
                reason="unsupported delete",
            )

        return Intent(
            route="entity_delete",
            operation="delete",
            entity_type="incident",
            entity_ref=entity_ref,
            from_context=not explicit,
        )

    # --------------------------------------------------------
    # Employees
    # --------------------------------------------------------

    @classmethod
    def _route_employee(
        cls,
        raw: str,
        state: SessionState,
        operation: Optional[str],
    ) -> Optional[Intent]:

        name = extract_employee_query(raw)

        pronoun = bool(_REF_EMPLOYEE.search(raw))

        roster_topic = bool(_ROSTER_TOPIC.search(raw))

        fields = detect_fields(raw, EMPLOYEE_ALIASES)

        wants_availability = bool(_AVAILABILITY.search(raw))

        if (name or pronoun) and _MONTH_WORDS.search(raw):
            return None

        if operation == "delete":
            return cls._delete_employee(raw, state, name, pronoun)

        if operation == "update":
            return None

        if _RANGE_WORDS.search(raw) and (name or pronoun):
            return None

        date_match = _DATE_TOKEN.search(raw)

        date = date_match.group(1).lower() if date_match else "today"

        if name:

            base = dict(operation="read", entity_type="employee", query=name, date=date)

            if wants_availability:
                return Intent(route="employee_availability", **base)

            if fields:
                return Intent(route="employee_fields", fields=fields, **base)

            if roster_topic:
                return Intent(route="employee_view", **base)

            return None

        if pronoun:

            entity = state.get_focus("employee")

            if entity is None:

                if roster_topic or fields or wants_availability:
                    return _ask("Which employee do you mean? Please share their name.", reason="no context")

                return None

            base = dict(
                operation="read",
                entity_type="employee",
                entity_ref=entity.id,
                from_context=True,
                date=date,
            )

            if wants_availability:
                return Intent(route="employee_availability", **base)

            if fields:
                return Intent(route="employee_fields", fields=fields, **base)

            if roster_topic or _SHOW.search(raw):
                return Intent(route="employee_view", **base)

        return None

    @classmethod
    def _delete_employee(
        cls,
        raw: str,
        state: SessionState,
        name: Optional[str],
        pronoun: bool,
    ) -> Optional[Intent]:

        roster_words = re.search(r"\broster\b|\bemployee\b", raw, re.IGNORECASE)

        if name and roster_words:

            return Intent(
                route="employee_delete",
                operation="delete",
                entity_type="employee",
                query=name,
            )

        if pronoun or (roster_words and not name):

            entity = state.get_focus("employee", state.mutation_max_turns)

            if entity is None:
                return _ask("Whose roster entry should I delete?", reason="no context")

            return Intent(
                route="employee_delete",
                operation="delete",
                entity_type="employee",
                entity_ref=entity.id,
                from_context=True,
            )

        return None

    # --------------------------------------------------------
    # Pending clarification
    # --------------------------------------------------------

    @classmethod
    def _route_pending(cls, raw: str, state: SessionState) -> Optional[Intent]:
        """
        Interpret a reply to a question the router asked.

        Returns None when the reply is not an answer (a fresh request),
        in which case the pending question is dropped.
        """

        pending = state.pending

        kind = pending.get("kind")

        if kind == "update_slots":
            intent = cls._answer_update(raw, state, pending)

        elif kind == "choose_employee":
            intent = cls._answer_choice(raw, pending)

        elif kind == "plan_confirm":
            intent = cls._answer_confirmation(raw)

        elif kind == "plan_slots":
            intent = cls._answer_plan(raw, pending)

        else:
            intent = None

        if intent is None:
            state.clear_pending()

        return intent

    @staticmethod
    def _answer_confirmation(raw: str) -> Optional[Intent]:
        """"yes" / "cancel" to a preview. Anything else is a new request."""

        if is_yes(raw):
            return Intent(route="plan_execute", operation="update", reason="confirmed")

        if is_no(raw):
            return Intent(route="plan_cancel", operation="update", reason="cancelled")

        return None

    @staticmethod
    def _answer_plan(raw: str, pending: Dict[str, Any]) -> Optional[Intent]:
        """The reply to a question a plan asked (a target, value, date or choice)."""

        need = pending.get("need") or {}

        if need.get("kind") == "changes":

            # "What would you like to change for INC1, INC2?" -> "priority High"
            if raw.rstrip().endswith("?"):
                return None

            named = WRITE_VERB.search(raw) or any(
                detect_fields(raw, aliases) for aliases in ALIASES_BY_TYPE.values()
            )

            if not named:
                return None

            intent = _plan(raw, need.get("record"), reason="plan answer")

            intent.targets = [str(target) for target in need.get("targets") or []]

            return intent

        plan = apply_answer(
            pending.get("plan") or {"ops": []},
            need,
            raw,
            ignore=_NAME_STOP | _CHOICE_FILLER,
        )

        if plan is None:
            return None

        return Intent(
            route="plan_resume",
            operation="update",
            pending={"plan": plan},
            reason="plan answer",
        )

    @classmethod
    def _answer_update(
        cls,
        raw: str,
        state: SessionState,
        pending: Dict[str, Any],
    ) -> Optional[Intent]:

        entity_type = pending["entity_type"]

        entity_ref = pending.get("entity_ref")

        expects = pending.get("expects")

        # ---- the user names the record ---------------------------------
        if expects == "entity":

            ticket = find_ticket_ref(raw)

            issue = find_issue_ref(raw)

            if entity_type == "incident" and ticket:
                entity_ref = f"{ticket[0]}{ticket[1]}"

            elif entity_type == "issue" and issue:
                entity_ref = issue[0]

            else:
                return None

            changes = pending.get("changes") or {}

            if changes:

                return Intent(
                    route="entity_update",
                    operation="update",
                    entity_type=entity_type,
                    entity_ref=entity_ref,
                    changes=changes,
                )

            return _ask(
                f"What would you like to update for {entity_ref}?",
                pending={
                    "kind": "update_slots",
                    "expects": "field",
                    "entity_type": entity_type,
                    "entity_ref": entity_ref,
                },
            )

        # A new identifier means a new request.
        if find_ticket_ref(raw) or find_issue_ref(raw):
            return None

        if raw.rstrip().endswith("?"):
            return None

        aliases = ALIASES_BY_TYPE[entity_type]

        # ---- the user gives the value for a known field -----------------
        if expects == "value":

            shortcut = re.match(
                r"^\s*(?:please\s+)?(?:set|change|make)\s+it\s+(?:to|as)\s+(?P<value>.+)$",
                raw,
                re.IGNORECASE,
            )

            # "set state to Closed" is a new request, not the value.
            if not shortcut and _UPDATE.search(raw):
                return None

            value = _strip_value(shortcut.group("value") if shortcut else raw)

            value = re.sub(
                r"^(?:it should be|should be|it is|to)\s+",
                "",
                value,
                flags=re.IGNORECASE,
            )

            if not value or len(value.split()) > 12:
                return None

            return Intent(
                route="entity_update",
                operation="update",
                entity_type=entity_type,
                entity_ref=entity_ref,
                changes={pending["field"]: value},
                from_context=True,
            )

        # ---- the user gives field (and usually value) --------------------
        assign = re.match(
            r"^\s*(?:please\s+)?(?:re)?assign(?:\s+(?:it|this|that))?\s+to\s+(?P<value>.+)$",
            raw,
            re.IGNORECASE,
        )

        if assign and entity_type == "incident":

            return Intent(
                route="entity_update",
                operation="update",
                entity_type=entity_type,
                entity_ref=entity_ref,
                changes={"assigned_to": _strip_value(assign.group("value"))},
                from_context=True,
            )

        spans = [
            span for span in find_field_spans(raw, aliases) if len(span[2]) == 1
        ]

        if not spans:
            return None

        start, end, names = spans[0]

        field_name = names[0]

        value = _strip_value(raw[end:])

        value = re.sub(r"^(?:of\s+\w+\s+)?", "", value)

        if not value:

            return _ask(
                f"What should the {field_label(field_name).lower()} be for {entity_ref}?",
                pending={
                    "kind": "update_slots",
                    "expects": "value",
                    "entity_type": entity_type,
                    "entity_ref": entity_ref,
                    "field": field_name,
                },
            )

        return Intent(
            route="entity_update",
            operation="update",
            entity_type=entity_type,
            entity_ref=entity_ref,
            changes={field_name: value},
            from_context=True,
        )

    @staticmethod
    def _answer_choice(raw: str, pending: Dict[str, Any]) -> Optional[Intent]:
        """Pick one of the candidates offered for an ambiguous employee."""

        options = pending.get("options") or []

        chosen = pick_option(raw, options, _NAME_STOP | _CHOICE_FILLER)

        if chosen is None:
            return None

        then = dict(pending.get("then") or {})

        then.update(
            entity_type="employee",
            entity_ref=chosen.get("ref") or chosen.get("UID"),
            query=None,
        )

        return Intent(**then)


def intent_key(intent: Intent) -> Optional[str]:
    """Comparison key for an incident reference (helper for callers)."""

    if intent.entity_type == "incident" and intent.entity_ref:

        ref = find_ticket_ref(intent.entity_ref)

        if ref:
            return ticket_key(ref[0], ref[1])

    return intent.entity_ref
