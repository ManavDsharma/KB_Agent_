"""
Helpers shared by the DynamoDB and Knowledge Base services.

Services never raise raw AWS exceptions to callers. Expected problems
(not found, invalid value, ambiguity) become ServiceError and every
public service method is wrapped in `guarded`, which turns errors into
small result dicts:

    {"ok": False, "error": "<kind>", "message": "<user-safe text>"}
"""

import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import wraps
from typing import Any, Callable, Dict, Iterable, List, Optional

from botocore.exceptions import BotoCoreError, ClientError

from config import (
    APP_TIMEZONE,
    DEFAULT_RESULT_LIMIT,
    MAX_RESULT_LIMIT,
    READ_ONLY_MODE,
)

logger = logging.getLogger("dtp.services")

audit_logger = logging.getLogger("dtp.audit")


# ============================================================
# ERRORS
# ============================================================

class ServiceError(Exception):
    """An expected, user-explainable failure."""

    def __init__(self, error: str, message: str, **details: Any):
        super().__init__(message)

        self.error = error

        self.message = message

        self.details = details

    def to_result(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": self.error,
            "message": self.message,
            **self.details,
        }


_CLIENT_ERRORS = {
    "ConditionalCheckFailedException": (
        "conflict",
        "The record changed or no longer exists. Please try again.",
    ),
    "AccessDeniedException": (
        "access_denied",
        "The agent is not permitted to perform this operation.",
    ),
    "AccessDenied": (
        "access_denied",
        "The agent is not permitted to perform this operation.",
    ),
    "UnrecognizedClientException": (
        "access_denied",
        "The agent could not authenticate with the data store.",
    ),
    "ExpiredTokenException": (
        "access_denied",
        "The agent credentials expired. Please try again shortly.",
    ),
    "ProvisionedThroughputExceededException": (
        "throttled",
        "The data store is busy. Please try again in a moment.",
    ),
    "ThrottlingException": (
        "throttled",
        "The service is busy. Please try again in a moment.",
    ),
    "RequestLimitExceeded": (
        "throttled",
        "The data store is busy. Please try again in a moment.",
    ),
    "ResourceNotFoundException": (
        "unavailable",
        "The data store could not be found.",
    ),
    "ValidationException": (
        "invalid",
        "The data store rejected the request.",
    ),
}


def client_error_result(action: str, exc: ClientError) -> Dict[str, Any]:
    """Map an AWS error to a safe result and log the details."""

    error = exc.response.get("Error", {})

    code = error.get("Code", "Unknown")

    logger.error(
        "aws_error action=%s code=%s message=%s",
        action,
        code,
        str(error.get("Message", ""))[:300],
    )

    kind, message = _CLIENT_ERRORS.get(
        code,
        (
            "unavailable",
            "The data store is temporarily unavailable. Please try again.",
        ),
    )

    return {"ok": False, "error": kind, "message": message}


def guarded(func: Callable) -> Callable:
    """Wrap a service method so it always returns a result dict."""

    @wraps(func)
    def wrapper(*args, **kwargs):

        try:
            return func(*args, **kwargs)

        except ServiceError as exc:
            return exc.to_result()

        except ClientError as exc:
            return client_error_result(func.__name__, exc)

        except BotoCoreError as exc:

            logger.error(
                "aws_error action=%s type=%s",
                func.__name__,
                type(exc).__name__,
            )

            return {
                "ok": False,
                "error": "unavailable",
                "message": (
                    "The data store is temporarily unavailable. "
                    "Please try again."
                ),
            }

        except Exception:

            logger.exception("service_failure action=%s", func.__name__)

            return {
                "ok": False,
                "error": "internal",
                "message": "Something went wrong while processing that request.",
            }

    return wrapper


# ============================================================
# VALUES
# ============================================================

def to_plain(value: Any) -> Any:
    """DynamoDB Decimal / Set values -> plain JSON types."""

    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)

    if isinstance(value, dict):
        return {key: to_plain(val) for key, val in value.items()}

    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]

    if isinstance(value, (set, frozenset)):
        return sorted(to_plain(item) for item in value)

    return value


def truncate(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1].rstrip() + "…"

    return value


def project(
    item: Dict[str, Any],
    fields: Iterable[str],
    keep_empty: bool = False,
    text_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Copy selected fields out of an item.

    Empty values are dropped, except with keep_empty (used when the
    caller asked for specific fields): then a missing or empty field is
    reported as None so "unassigned" is not mistaken for "not returned".
    """

    result = {}

    for field in fields:

        if field not in item and not keep_empty:
            continue

        value = to_plain(item.get(field))

        if value in ("", [], {}):
            value = None

        if not keep_empty and value is None:
            continue

        if text_limit:
            value = truncate(value, text_limit)

        result[field] = value

    return result


def norm(value: Any) -> str:
    """Lower-case alphanumeric tokens joined by single spaces."""

    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


_TRUE = {"true", "yes", "y", "1", "active", "enabled", "on"}

_FALSE = {"false", "no", "n", "0", "inactive", "disabled", "off"}


def coerce_bool(value: Any, field: str = "value") -> bool:
    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()

    if text in _TRUE:
        return True

    if text in _FALSE:
        return False

    raise ServiceError(
        "invalid_value",
        f"{field} must be yes/no (true/false).",
    )


def yes_no(value: Any, existing: Any = None, field: str = "value") -> Any:
    """
    A yes/no value written in the format the column already uses.

    The tables store "Yes"/"No" text; a boolean is written only when the
    existing value is a boolean.
    """

    flag = coerce_bool(value, field)

    if isinstance(existing, bool):
        return flag

    return "Yes" if flag else "No"


def same_type(existing: Any, value: Any) -> Any:
    """Write a number as text when the column holds text ("60"), and vice versa."""

    if isinstance(existing, str) and isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)

    return value


def scalar(value: Any, field: str, max_chars: int = 4000) -> Any:
    """Reject lists/dicts and over-long text for scalar fields."""

    if isinstance(value, (dict, list, tuple, set)):
        raise ServiceError(
            "invalid_value",
            f"{field} must be a single value.",
        )

    if isinstance(value, str):

        value = value.strip()

        if len(value) > max_chars:
            raise ServiceError(
                "invalid_value",
                f"{field} is too long (max {max_chars} characters).",
            )

    return value


def limit_value(limit: Optional[int]) -> int:
    if limit is None:
        return DEFAULT_RESULT_LIMIT

    try:
        return max(1, min(int(limit), MAX_RESULT_LIMIT))

    except (TypeError, ValueError):
        return DEFAULT_RESULT_LIMIT


def resolve_choice(
    value: Any,
    allowed: List[str],
    field: str,
) -> str:
    """
    Map free text onto one of the values already used in the data.

    "p2" -> "P2-High", "closed" -> "Closed". Ambiguous or unknown
    values raise ServiceError listing the valid options. When nothing
    is known about the field the value is accepted as typed.
    """

    text = str(value).strip()

    if not allowed:
        return text

    target = norm(text)

    if not target:
        raise ServiceError("invalid_value", f"A value for {field} is required.")

    exact = [item for item in allowed if norm(item) == target]

    if exact:
        return exact[0]

    tokens = target.split()

    by_prefix = [
        item for item in allowed
        if norm(item).split()[: len(tokens)] == tokens
    ]

    if len(by_prefix) == 1:
        return by_prefix[0]

    by_tokens = [
        item for item in allowed
        if set(tokens) <= set(norm(item).split())
    ]

    if len(by_tokens) == 1:
        return by_tokens[0]

    options = by_prefix or by_tokens

    if options:
        raise ServiceError(
            "ambiguous_value",
            f"'{text}' matches several {field} values: "
            f"{', '.join(options[:8])}.",
            field=field,
            options=options[:8],
        )

    raise ServiceError(
        "invalid_value",
        f"'{text}' is not a valid {field}. "
        f"Valid values: {', '.join(allowed[:15])}.",
        field=field,
        allowed=allowed[:20],
    )


# ============================================================
# TIME
# ============================================================

def _timezone():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(APP_TIMEZONE)

    except Exception:

        if APP_TIMEZONE == "Asia/Kolkata":
            return timezone(timedelta(hours=5, minutes=30))

        return timezone.utc


def today() -> date:
    return datetime.now(_timezone()).date()


def now_local() -> datetime:
    return datetime.now(_timezone())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_timestamp() -> str:
    """Incident timestamps are stored as local 'YYYY-MM-DD HH:MM'."""

    return now_local().strftime("%Y-%m-%d %H:%M")


def local_date() -> str:
    """Roster / category rows record created_on / updated_on as 'YYYY-MM-DD'."""

    return today().isoformat()


def ensure_writable() -> None:
    """Every write goes through this, so READ_ONLY_MODE is absolute."""

    if READ_ONLY_MODE:
        raise ServiceError(
            "not_allowed",
            "The agent is in read-only mode, so nothing was changed.",
        )


_DATE_WORDS = {
    "today": 0,
    "now": 0,
    "tomorrow": 1,
    "yesterday": -1,
}


def resolve_date(value: Any = None) -> str:
    """'today', 'tomorrow', 'yesterday' or YYYY-MM-DD -> YYYY-MM-DD."""

    text = str(value or "today").strip().lower()

    if text in _DATE_WORDS:
        return (today() + timedelta(days=_DATE_WORDS[text])).isoformat()

    for pattern in ("%Y-%m-%d", "%Y/%m/%d"):

        try:
            return datetime.strptime(text, pattern).date().isoformat()

        except ValueError:
            continue

    raise ServiceError(
        "invalid_value",
        "Dates must be YYYY-MM-DD, 'today', 'tomorrow' or 'yesterday'.",
    )


# ============================================================
# CACHE
# ============================================================

class TTLCache:
    """Small thread-safe time-based cache."""

    def __init__(self, ttl_seconds: float):
        self.ttl = ttl_seconds

        self._values: Dict[Any, Any] = {}

        self._lock = threading.Lock()

    def get_or_load(self, key: Any, loader: Callable[[], Any]) -> Any:

        with self._lock:

            entry = self._values.get(key)

            if entry and time.monotonic() - entry[0] < self.ttl:
                return entry[1]

        value = loader()

        with self._lock:
            self._values[key] = (time.monotonic(), value)

        return value

    def clear(self) -> None:

        with self._lock:
            self._values.clear()


# ============================================================
# DYNAMODB
# ============================================================

def scan_items(
    table,
    filter_expression=None,
    fields: Optional[Iterable[str]] = None,
    max_items: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Paginated scan.

    Use either `filter_expression` (server-side filtering) or `fields`
    (projection), not both.
    """

    kwargs: Dict[str, Any] = {}

    if filter_expression is not None:
        kwargs["FilterExpression"] = filter_expression

    if fields:

        names = {f"#p{index}": field for index, field in enumerate(fields)}

        kwargs["ProjectionExpression"] = ", ".join(names)

        kwargs["ExpressionAttributeNames"] = names

    items: List[Dict[str, Any]] = []

    while True:

        response = table.scan(**kwargs)

        items.extend(response.get("Items", []))

        if max_items and len(items) >= max_items:
            return items[:max_items]

        last_key = response.get("LastEvaluatedKey")

        if not last_key:
            return items

        kwargs["ExclusiveStartKey"] = last_key


def build_update(changes: Dict[str, Any]):
    """SET expression for top-level attributes (names are placeholders)."""

    names: Dict[str, str] = {}

    values: Dict[str, Any] = {}

    parts = []

    for index, (field, value) in enumerate(changes.items()):

        names[f"#f{index}"] = field

        values[f":v{index}"] = value

        parts.append(f"#f{index} = :v{index}")

    return "SET " + ", ".join(parts), names, values


def audit(action: str, actor: Optional[str], **details: Any) -> None:
    """Record who changed what. Values are truncated."""

    safe = {
        key: truncate(str(value), 300)
        for key, value in details.items()
    }

    audit_logger.info(
        "audit action=%s actor=%s details=%s",
        action,
        actor or "unknown",
        safe,
    )
