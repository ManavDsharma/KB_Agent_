"""
Identifier parsing.

Users and external systems write the same identifier in several ways
(INC0000258, INC-0000258, "INC 0000258", "incident 258"). These
helpers pull identifiers out of text and produce the DynamoDB keys
worth trying.
"""

import re
from typing import List, Optional, Tuple

# Explicit ticket IDs: INC0000258, INC-0000258, INC 0000258, REQ0000162.
_TICKET_ID = re.compile(
    r"(?<![A-Za-z0-9])(INC|REQ)[\s_-]*(\d+)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# Natural-language forms: "incident 258", "request 162".
_TICKET_WORD = re.compile(
    r"(?<![A-Za-z0-9])(incident|request)[\s_-]*(?:no\.?|number|#)?[\s_-]*(\d+)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

_ISSUE_ID = re.compile(
    r"(?<![A-Za-z0-9])issue[\s_-]*(\d+)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

_WORD_PREFIX = {
    "incident": "INC",
    "request": "REQ",
}


def find_ticket_ref(text: str) -> Optional[Tuple[str, str, Tuple[int, int]]]:
    """
    First incident/request reference in text.

    Returns (prefix, digits_as_typed, (start, end)) or None.
    """

    match = _TICKET_ID.search(text)

    if match:
        return (
            match.group(1).upper(),
            match.group(2),
            match.span(),
        )

    match = _TICKET_WORD.search(text)

    if match:
        return (
            _WORD_PREFIX[match.group(1).lower()],
            match.group(2),
            match.span(),
        )

    return None


def find_issue_ref(text: str) -> Optional[Tuple[str, Tuple[int, int]]]:
    """First Issue-N reference in text: ("Issue-9", (start, end))."""

    match = _ISSUE_ID.search(text)

    if not match:
        return None

    return f"Issue-{int(match.group(1))}", match.span()


def normalize_issue_id(raw) -> str:
    """Issue-9 / Issue9 / issue 9 / 9 -> Issue-9."""

    value = str(raw).strip()

    match = re.search(r"(\d+)\s*$", value)

    if match:
        return f"Issue-{int(match.group(1))}"

    return value


def ticket_key(prefix: str, digits: str) -> str:
    """Comparison key that ignores hyphens and zero padding."""

    return f"{prefix.upper()}{int(digits)}"


def ticket_key_of(value: str) -> Optional[str]:
    """ticket_key for a stored/typed ticket ID, or None."""

    ref = find_ticket_ref(str(value))

    if not ref:
        return None

    return ticket_key(ref[0], ref[1])


def ticket_candidates(prefix: str, digits: str) -> List[str]:
    """
    Keys worth trying, most likely first.

    What the user typed comes first, then zero-padded variants with
    and without the hyphen.
    """

    prefix = prefix.upper()

    number = str(int(digits))

    typed = [
        f"{prefix}{digits}",
        f"{prefix}-{digits}",
    ]

    padded = []

    for width in (7, 8, 6):
        padded.append(f"{prefix}{number.zfill(width)}")
        padded.append(f"{prefix}-{number.zfill(width)}")

    ordered: List[str] = []

    for candidate in typed + padded:
        if candidate not in ordered:
            ordered.append(candidate)

    return ordered


def issue_key(value: str) -> str:
    match = re.search(r"(\d+)\s*$", str(value))

    if match:
        return f"issue{int(match.group(1))}"

    return str(value).strip().lower()
