"""
Response building.

Every answer is a Reply: `text` is Markdown (complete on its own, so any
client can show it) and `structured` is a machine-readable object a UI can
render richly. See FRONTEND_CONTRACT.md for the full contract.

    type   text | record | field | table | list | update_confirmation | question | error
           (a question with `confirmation: true` previews a multi-part change;
           an update_confirmation with `items` reports several records at once)
    tone   info | success | warning | error          (message colour)
    suggestions  [{"label", "send"}]                    (tap-to-send follow-ups)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import presentation
from services.schema import label as field_label


@dataclass
class Reply:
    text: str
    structured: Dict[str, Any] = field(default_factory=dict)
    ok: bool = True

    def as_payload(self) -> Dict[str, Any]:
        return {"response": self.text, "structured": self.structured}

    def suggest(self, suggestions: Optional[Sequence[Dict[str, str]]]) -> "Reply":
        """Attach tap-to-send follow-up suggestions."""

        if suggestions:
            self.structured["suggestions"] = list(suggestions)

        return self


# ============================================================
# VALUE FORMATTING
# ============================================================

def fmt(value: Any) -> str:

    if value is None or value == "":
        return "—"

    if isinstance(value, bool):
        return "Yes" if value else "No"

    if isinstance(value, (list, tuple, set)):
        return ", ".join(fmt(item) for item in value) or "—"

    if isinstance(value, dict):
        return "; ".join(f"{key}: {fmt(val)}" for key, val in value.items()) or "—"

    return str(value)


def fmt_field(name: str, value: Any) -> str:

    if name.startswith("has_") and name.endswith("_breach") and value not in (None, ""):

        if isinstance(value, str):
            value = value.strip().lower() in {"true", "yes", "y", "1"}

        return "Yes" if value else "No"

    return fmt(value)


def _entity(kind: Optional[str], entity_id: Optional[str]) -> Optional[Dict[str, str]]:

    if not kind or not entity_id:
        return None

    return {"kind": kind, "id": str(entity_id)}


# ============================================================
# BASIC REPLIES
# ============================================================

def text_reply(text: str) -> Reply:

    return Reply(
        text=text,
        structured={"type": "text", "format": "markdown", "text": text, "tone": "info"},
    )


def error_reply(message: str) -> Reply:

    return Reply(
        text=message,
        structured={"type": "error", "message": message, "text": message, "tone": "error"},
        ok=False,
    )


def question_reply(
    question: str,
    options: Optional[Sequence[str]] = None,
    sends: Optional[Sequence[str]] = None,
) -> Reply:
    """
    A question the user must answer. Options become tap targets; tapping
    one sends `send` (the option number by default) as the next message.
    """

    text = question

    if options:
        text += "\n\n" + "\n".join(
            f"{index}. {option}" for index, option in enumerate(options, start=1)
        )

    structured: Dict[str, Any] = {
        "type": "question",
        "clarification": True,
        "text": text,
        "question": question,
        "tone": "info",
    }

    if options:

        values = list(sends) if sends else [str(index) for index in range(1, len(options) + 1)]

        structured["options"] = [
            {"label": option, "send": value}
            for option, value in zip(options, values)
        ]

    return Reply(text=text, structured=structured)


# ============================================================
# RECORDS AND FIELDS
# ============================================================

def record_reply(
    title: str,
    pairs: Iterable[Tuple[str, Any]],
    kind: Optional[str] = None,
    entity_id: Optional[str] = None,
    card: Optional[Dict[str, Any]] = None,
    layout: Optional[Sequence[Tuple[str, Sequence[str]]]] = None,
    lead: Optional[str] = None,
) -> Reply:
    """
    A record: a sentence, the fields as bullets (Markdown), and a card
    (badges, grouped sections, link) for UIs that render one.
    """

    pairs = list(pairs)

    rows = [(name, fmt_field(name, value)) for name, value in pairs]

    sentence = lead or (card or {}).get("lead") or f"Here are the details for **{title}**."

    bullets = "\n".join(f"- {field_label(name)}: {value}" for name, value in rows)

    text = f"{sentence}\n\n{bullets}"

    sections = (
        presentation.group_sections(pairs, layout, fmt_field)
        if layout
        else [
            {
                "title": None,
                "items": [
                    {"field": name, "label": field_label(name), "value": value}
                    for name, value in rows
                ],
            }
        ]
    )

    structured: Dict[str, Any] = {
        "type": "record",
        "title": title,
        "entity": _entity(kind, entity_id or title),
        "subtitle": (card or {}).get("subtitle"),
        "badges": (card or {}).get("badges", []),
        "summary": sentence,
        "sections": sections,
        "fields": [{"label": field_label(name), "value": value} for name, value in rows],
        "link": (card or {}).get("link"),
        "text": text,
        "tone": "info",
    }

    return Reply(text=text, structured=structured)


def field_reply(
    entity: str,
    pairs: Sequence[Tuple[str, Any]],
    kind: Optional[str] = None,
) -> Reply:
    """Answer to a question about one or more specific fields."""

    if len(pairs) == 1:

        name, value = pairs[0]

        sentence = presentation.field_sentence(kind or "", entity, name, value)

        text = sentence or f"**{entity}** — {field_label(name)}: {fmt_field(name, value)}"

        return Reply(
            text=text,
            structured={
                "type": "field",
                "title": entity,
                "entity": _entity(kind, entity),
                "field": name,
                "label": field_label(name),
                "value": value,
                "text": text,
                "tone": "info",
            },
        )

    return record_reply(
        entity,
        pairs,
        kind=kind,
        entity_id=entity,
        lead=f"Here are the details for **{entity}**:",
    )


# ============================================================
# TABLES AND LISTS
# ============================================================

def table_reply(
    title: str,
    columns: Sequence[str],
    rows: Sequence[Dict[str, Any]],
    footer: str = "",
    total: Optional[int] = None,
) -> Reply:

    header = "| " + " | ".join(field_label(column) for column in columns) + " |"

    divider = "|" + "|".join(" --- " for _ in columns) + "|"

    body = [
        "| "
        + " | ".join(fmt_field(column, row.get(column)).replace("|", "/") for column in columns)
        + " |"
        for row in rows
    ]

    text = f"**{title}**\n\n" + "\n".join([header, divider, *body])

    if footer:
        text += f"\n\n{footer}"

    return Reply(
        text=text,
        structured={
            "type": "table",
            "title": title,
            "columns": [field_label(column) for column in columns],
            "rows": [
                {
                    field_label(column): fmt_field(column, row.get(column))
                    for column in columns
                }
                for row in rows
            ],
            "shown": len(rows),
            "total": total if total is not None else len(rows),
            "footer": footer,
            "text": text,
            "tone": "info",
        },
    )


def list_reply(title: str, items: Sequence[str]) -> Reply:

    text = f"**{title}**\n\n" + "\n".join(f"- {item}" for item in items)

    return Reply(
        text=text,
        structured={"type": "list", "title": title, "items": list(items), "text": text, "tone": "info"},
    )


# ============================================================
# CONFIRMATIONS
# ============================================================

def update_reply(
    entity: str,
    changes: Dict[str, Dict[str, Any]],
    unchanged: Sequence[str] = (),
    kind: Optional[str] = None,
) -> Reply:
    """Short confirmation of a successful update."""

    if not changes:

        names = ", ".join(field_label(name).lower() for name in unchanged)

        text = f"No change needed for {entity}" + (f" ({names} already set)." if names else ".")

        return Reply(
            text=text,
            structured={
                "type": "update_confirmation",
                "entity": _entity(kind, entity),
                "changes": [],
                "message": text,
                "text": text,
                "tone": "info",
            },
        )

    lines = [
        f"- {field_label(name)}: {fmt_field(name, change.get('from'))} → "
        f"{fmt_field(name, change.get('to'))}"
        for name, change in changes.items()
    ]

    text = f"Updated {entity}.\n\n" + "\n".join(lines)

    return Reply(
        text=text,
        structured={
            "type": "update_confirmation",
            "entity": _entity(kind, entity),
            "changes": [
                {
                    "field": name,
                    "label": field_label(name),
                    "from": change.get("from"),
                    "to": change.get("to"),
                }
                for name, change in changes.items()
            ],
            "text": text,
            "tone": "success",
        },
    )


MAX_ITEM_LINES = 10


def _item_line(item: Dict[str, Any]) -> str:
    """"- **INC0000258**: Priority A → B; Urgency C → D" for one planned/applied item."""

    parts = "; ".join(
        f"{change['label']} {fmt(change.get('from'))} → {fmt(change.get('to'))}"
        for change in item.get("changes", [])
    )

    return f"- **{item.get('label') or item.get('id')}**: {parts}" if parts else f"- **{item.get('label') or item.get('id')}**"


def _item_lines(items: Sequence[Dict[str, Any]]) -> str:

    lines = [_item_line(item) for item in items[:MAX_ITEM_LINES]]

    if len(items) > MAX_ITEM_LINES:
        lines.append(f"- …and {len(items) - MAX_ITEM_LINES} more")

    return "\n".join(lines)


def confirm_reply(
    items: Sequence[Dict[str, Any]],
    notes: Sequence[str] = (),
) -> Reply:
    """
    A preview of several changes with Confirm / Cancel. The buttons send
    "yes" / "cancel"; the plan itself waits in the session state.
    """

    preview = "Here's what I'll change:\n\n" + _item_lines(items)

    if notes:
        preview += "\n\n" + "\n".join(f"_{note}_" for note in notes)

    text = preview + "\n\nReply **yes** to apply or **cancel**."

    return Reply(
        text=text,
        structured={
            "type": "question",
            "clarification": True,
            "confirmation": True,
            # A client that renders `question` (and its option buttons) shows the
            # whole preview without knowing about `plan`.
            "question": preview + "\n\nApply these changes?",
            "text": text,
            "tone": "warning" if notes else "info",
            "plan": [dict(item) for item in items],
            "notes": list(notes),
            "options": [
                {"label": "Confirm", "send": "yes"},
                {"label": "Cancel", "send": "cancel"},
            ],
        },
    )


def batch_reply(
    items: Sequence[Dict[str, Any]],
    failed: Sequence[Tuple[str, str]] = (),
) -> Reply:
    """Result of several changes: what was applied, and what could not be."""

    count = len(items)

    lead = (
        f"Updated {count} record{'s' if count != 1 else ''}."
        if items
        else "I couldn't apply any of the changes."
    )

    text = lead

    if items:
        text += "\n\n" + _item_lines(items)

    if failed:
        text += "\n\nCouldn't update:\n" + "\n".join(f"- {label}: {message}" for label, message in failed)

    return Reply(
        text=text,
        structured={
            "type": "update_confirmation",
            "entity": None,
            "message": lead,
            "items": [dict(item) for item in items],
            "failed": [{"label": label, "message": message} for label, message in failed],
            "text": text,
            "tone": "warning" if failed else "success",
        },
        ok=bool(items),
    )


def message_confirmation(
    text: str,
    entity: str = "",
    operation: str = "",
    kind: Optional[str] = None,
) -> Reply:
    """One-line confirmation (delete, create)."""

    return Reply(
        text=text,
        structured={
            "type": "update_confirmation",
            "entity": _entity(kind, entity),
            "operation": operation,
            "message": text,
            "text": text,
            "tone": "success",
        },
    )


def list_rows(rows: List[Dict[str, Any]]) -> List[str]:
    """Column names present in a list of dict rows, in first-seen order."""

    columns: List[str] = []

    for row in rows:
        for name in row:
            if name not in columns:
                columns.append(name)

    return columns
