"""
Language helpers for plans: pure functions with no state and no AWS.

They are the checks that keep a model's output honest. Whatever the planner
returns must be *grounded* in the user's own message: an ID, name, field or
value the user never wrote is rejected, not guessed. They also read the short
answers to a plan's questions ("yes", "2", "the 3rd") without a model.
"""

import copy
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config import PLAN_MAX_OPERATIONS
from services.common import MONTH_NAMES, ServiceError, norm, parse_user_date, today
from services.ids import find_all_issue_refs, typed_ticket_ids
from services.schema import ALIASES_BY_TYPE, detect_fields

# Verbs that make a message a request to change something.
WRITE_VERB = re.compile(
    r"\b(update|change|modify|edit|set|assign|reassign|close|resolve|reopen|re-open|"
    r"mark|make|put|move|switch|rename|enable|disable|activate|deactivate|"
    r"bump|raise|lower|escalate|downgrade|upgrade)\b",
    re.IGNORECASE,
)

MAX_RANGE_DAYS = 31

_YES = {
    "yes", "y", "yep", "yeah", "yup", "confirm", "confirmed", "ok", "okay", "sure",
    "go ahead", "proceed", "do it", "apply", "apply it", "approve", "approved",
    "looks good", "correct", "that s right", "that is right", "go for it",
}

_NO = {
    "no", "n", "nope", "cancel", "cancel it", "stop", "abort", "never mind", "nevermind",
    "dont", "don t", "do not", "discard", "skip", "forget it", "no thanks",
}

_POLITE = {"please", "thanks", "thank", "you", "now", "then"}


def _short_answer(raw: str) -> str:
    """Lower-case words of a short reply, politeness removed."""

    words = [word for word in norm(raw).split() if word not in _POLITE]

    return " ".join(words) if len(words) <= 4 else ""


def is_yes(raw: str) -> bool:
    return _short_answer(raw) in _YES


def is_no(raw: str) -> bool:
    return _short_answer(raw) in _NO


# ============================================================
# GROUNDING
# ============================================================

def _stem_match(a: str, b: str) -> bool:
    """Same word, or the same stem ("close" / "closed")."""

    if a == b:
        return True

    shortest = min(len(a), len(b))

    if shortest < 4:
        return False

    common = 0

    while common < shortest and a[common] == b[common]:
        common += 1

    return common >= max(4, shortest - 2)


def grounded_value(value: Any, text: str) -> bool:
    """Is every word of `value` something the user actually wrote?"""

    wanted = norm(value).split()

    written = norm(text).split()

    if not wanted:
        return False

    return all(any(_stem_match(word, other) for other in written) for word in wanted)


_STATE_WORDS = re.compile(
    r"\b(close|closed|closing|resolve|resolved|resolving|reopen|reopened|re-open|cancel|cancelled|hold)\b",
    re.IGNORECASE,
)

_ASSIGN_WORDS = re.compile(
    r"\b(assign|assigned|reassign|reassigned|give|hand|handover|transfer)\b",
    re.IGNORECASE,
)

_TEAM_WORD = re.compile(r"\bteam\b", re.IGNORECASE)

_ACTIVE_WORDS = re.compile(
    r"\b(active|inactive|activate|deactivate|enable|disable|enabled|disabled)\b",
    re.IGNORECASE,
)


def field_grounded(record: str, field: str, text: str) -> bool:
    """Did the user's message actually refer to this field?"""

    if field in detect_fields(text, ALIASES_BY_TYPE.get(record, {})):
        return True

    if field in ("team_id", "team_name") and _TEAM_WORD.search(text):
        return True

    if field == "active" and _ACTIVE_WORDS.search(text):
        return True

    if record == "incident":

        if field == "state" and _STATE_WORDS.search(text):
            return True

        if field == "assigned_to" and _ASSIGN_WORDS.search(text):
            return True

    return False


def name_grounded(name: str, text: str) -> bool:
    """A person's name appears in the message as written (word for word)."""

    wanted = norm(name)

    return bool(wanted) and f" {wanted} " in f" {norm(text)} "


_PRONOUNS = {
    "it", "its", "this", "that", "he", "she", "him", "her", "his", "hers", "they", "them",
    "same", "their", "theirs", "these", "those", "both", "all", "each",
}


def is_pronoun(target: str) -> bool:
    return norm(target) in _PRONOUNS


_RECORD_WORDS = r"(?:incidents|tickets|requests|issues|employees|people|records)"

# "them", "their", "both of them", "the two": the records of the last list.
# A bare "both" is not here: "set both priority and urgency" is about fields.
_PLURAL_REF = re.compile(
    r"\b(?:them|their|theirs|these|those|both of|all of|each of|the two|the three|the four|"
    rf"both {_RECORD_WORDS}|all (?:the |of the )?{_RECORD_WORDS})\b",
    re.IGNORECASE,
)

# "... to High for both", "... on all of them" at the end of a value.
PLURAL_TAIL = re.compile(
    r"\s(?:for|on|of)\s+(?:both|all|each|them|these|those|the two|the three)\b",
    re.IGNORECASE,
)


def has_plural_reference(text: str) -> bool:
    """Does the message refer to several earlier records ("update their priority")?"""

    return bool(_PLURAL_REF.search(text))


# ============================================================
# DATES
# ============================================================

def expand_range(start_iso: str, end_iso: str) -> List[str]:
    """Inclusive list of ISO dates; ServiceError when reversed or too long."""

    first = date.fromisoformat(start_iso)

    last = date.fromisoformat(end_iso)

    if last < first:
        raise ServiceError("invalid_value", "The start date cannot be after the end date.")

    if (last - first).days + 1 > MAX_RANGE_DAYS:
        raise ServiceError("invalid_value", f"Please ask for at most {MAX_RANGE_DAYS} days at a time.")

    return [(first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)]


_RANGE_SPLIT = re.compile(r"\s+(?:to|until|till|through|thru)\s+|\s+[-–]\s+", re.IGNORECASE)

_LIST_SPLIT = re.compile(r"\s*(?:,|&|\band\b)\s*", re.IGNORECASE)


def parse_dates_reply(raw: str) -> Optional[Tuple[List[str], bool]]:
    """
    "2nd August", "3rd to 5th August", "1st and 4th", "2026-08-02" ->
    (ISO dates, year_was_guessed). None when it is not only dates.
    """

    text = raw.strip().strip(".!?")

    if not text or len(text.split()) > 10:
        return None

    try:

        parts = _RANGE_SPLIT.split(text)

        if len(parts) == 2:

            first, second = parts[0].strip(), parts[1].strip()

            # "3rd to 5th August": the month belongs to both ends.
            if not re.search(r"[A-Za-z]{3,}", first):

                month_year = re.search(r"([A-Za-z]{3,9}(?:\s+\d{4})?)\s*$", second)

                if month_year:
                    first = f"{first} {month_year.group(1)}"

            start, guessed_a = parse_user_date(first)

            end, guessed_b = parse_user_date(second)

            return expand_range(start, end), guessed_a or guessed_b

        found: List[str] = []

        guessed = False

        pieces = [piece.strip() for piece in _LIST_SPLIT.split(text) if piece.strip()]

        # "1st and 4th August": the month belongs to every day in the list.
        suffix = ""

        for piece in reversed(pieces):

            tail = re.search(r"([A-Za-z]{3,9}(?:\s+\d{4})?)\s*$", piece)

            if tail and MONTH_NAMES.get(tail.group(1).split()[0].lower()):
                suffix = tail.group(1)
                break

        for piece in pieces:

            if suffix and not re.search(r"[A-Za-z]{3,}", piece):
                piece = f"{piece} {suffix}"

            iso, flag = parse_user_date(piece)

            guessed = guessed or flag

            if iso not in found:
                found.append(iso)

        return (found, guessed) if found else None

    except ServiceError:
        return None


_RELATIVE_DAYS = {"today": 0, "tomorrow": 1, "yesterday": -1}


def date_grounded(iso: str, text: str) -> bool:
    """
    Is this date really in the message? It is when the ISO text is there, a
    relative word resolves to it, or its day number and month name both are
    ("2nd August"). A date a model worked out for itself is rejected.
    """

    low = text.lower()

    if iso in low:
        return True

    wanted = date.fromisoformat(iso)

    for word, delta in _RELATIVE_DAYS.items():

        if re.search(rf"\b{word}\b", low) and (today() + timedelta(days=delta)) == wanted:
            return True

    if re.search(r"\bday after tomorrow\b", low) and (today() + timedelta(days=2)) == wanted:
        return True

    words = re.findall(r"[a-z]+", low)

    has_month = any(MONTH_NAMES.get(word) == wanted.month for word in words)

    has_day = re.search(rf"(?<![\d-])0?{wanted.day}(?:st|nd|rd|th)?(?![\d-])", low) is not None

    return has_month and has_day


# ============================================================
# ANSWERS TO A PLAN'S QUESTIONS
# ============================================================

def apply_answer(
    plan: Dict[str, Any],
    need: Dict[str, Any],
    raw: str,
    ignore: Sequence[str] = (),
) -> Optional[Dict[str, Any]]:
    """
    Fill the one thing a plan asked for from the user's short reply, without
    a model. Returns the updated plan, or None when the reply is not an
    answer (then it is treated as a new request).
    """

    text = raw.strip()

    if not text or text.endswith("?"):
        return None

    updated = copy.deepcopy(plan)

    ops = updated["ops"]

    kind = need.get("kind")

    if kind == "target":

        record = need["record"]

        if record == "incident":
            targets = [typed for _, typed in typed_ticket_ids(text)]

        elif record == "issue":
            targets = find_all_issue_refs(text)

        else:
            targets = names_in_reply(text)

        if not targets:
            return None

        rebuilt: List[Dict[str, Any]] = []

        for op in ops:

            if op["target"] is None and op["record"] == record:

                for target in targets:

                    clone = copy.deepcopy(op)

                    clone["target"] = target

                    rebuilt.append(clone)

            else:
                rebuilt.append(op)

        if len(rebuilt) > PLAN_MAX_OPERATIONS:
            return None

        updated["ops"] = rebuilt

    elif kind == "value":

        shortcut = re.match(r"^\s*(?:please\s+)?(?:set|make|change)\s+it\s+(?:to|as)\b", text, re.IGNORECASE)

        if len(text.split()) > 12 or (WRITE_VERB.search(text) and not shortcut):
            return None

        value = clean_reply_value(text)

        if not value:
            return None

        ops[need["op"]]["changes"][need["field"]] = value

    elif kind == "avail_value":

        if len(text.split()) > 4 or WRITE_VERB.search(text):
            return None

        value = clean_reply_value(text)

        for op in ops:

            if op["action"] == "availability" and not op.get("value"):
                op["value"] = value

    elif kind == "date":

        parsed = parse_dates_reply(text)

        if parsed is None:
            return None

        dates, guessed = parsed

        for op in ops:

            if op["action"] == "availability" and not op["dates"]:

                op["dates"] = list(dates)

                op["inferred"] = bool(guessed)

                op["past"] = any(day < today().isoformat() for day in dates)

    elif kind == "choice":

        chosen = pick_option(text, need.get("options") or [], ignore)

        if chosen is None:
            return None

        op = ops[need["op"]]

        if need.get("person"):
            op["target"] = chosen.get("ref") or str(chosen.get("UID"))

        else:
            op["changes"][need["field"]] = str(chosen)

    else:
        return None

    return updated


# ============================================================
# CHOICES
# ============================================================

_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3}


def _haystack(option: Any) -> str:

    if isinstance(option, dict):

        return " ".join(
            str(option.get(name, "")).lower()
            for name in ("ref", "employee_name", "emp_id", "team_id", "team_name")
        )

    return str(option).lower()


def pick_option(raw: str, options: Sequence[Any], ignore: Sequence[str] = ()) -> Optional[Any]:
    """The option a short reply points at: a number, "the second", or its words."""

    text = raw.strip().lower().strip(".!? ")

    if text.isdigit():
        return options[int(text) - 1] if 1 <= int(text) <= len(options) else None

    for word, index in _ORDINALS.items():

        if re.search(rf"\b{word}\b", text) and index <= len(options):
            return options[index - 1]

    words = [
        word for word in re.split(r"[^a-z0-9@._-]+", text)
        if word and word not in ignore
    ]

    matches = [
        option for option in options
        if words and all(word in _haystack(option) for word in words)
    ]

    return matches[0] if len(matches) == 1 else None


def person_label(option: Dict[str, Any]) -> str:
    """"Rohit Malhotra (Access Management Team, 1)" for an ambiguous-person option."""

    team = option.get("team_name") or option.get("team_id") or ""

    extra = ", ".join(str(part) for part in (team, option.get("emp_id")) if part)

    return f"{option.get('employee_name', option.get('ref'))}" + (f" ({extra})" if extra else "")


_NAME_FILLER = re.compile(
    r"^(?:it(?:'s| is)|its|for|the|employee|person|engineer|please|about|name is)\s+",
    re.IGNORECASE,
)


def names_in_reply(raw: str) -> List[str]:
    """People named in a short reply: "Rohit", "Rohit and Priya"."""

    text = raw.strip().strip(".!?")

    if not text or len(text.split()) > 8 or WRITE_VERB.search(text) or "?" in raw:
        return []

    names = []

    for piece in _LIST_SPLIT.split(text):

        piece = piece.strip()

        while True:
            stripped = _NAME_FILLER.sub("", piece)

            if stripped == piece:
                break

            piece = stripped

        if piece and re.fullmatch(r"[A-Za-z][A-Za-z.'@_-]*(?:\s+[A-Za-z][A-Za-z.'@_-]*){0,2}", piece):
            names.append(piece)

    return names


def clean_reply_value(raw: str) -> str:
    """A reply that is just a value: "High", "to High.", "it should be P2"."""

    value = raw.strip().strip("\"'`").rstrip(".!?, ").strip("\"'`")

    value = re.sub(r"^(?:set it to|set to|make it|change it to|it should be|should be|it is|to|as|=|:|is)\s+", "", value, flags=re.IGNORECASE)

    return value.strip()
