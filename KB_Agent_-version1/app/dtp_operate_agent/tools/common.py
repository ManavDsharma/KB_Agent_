"""
Shared helpers for the tool layer.

Tool results are what the model reads, so they are kept small:
compact JSON, no redundant "ok": true, and lists of records sent as
columns + rows instead of repeating every key.
"""

import json
from typing import Any, Dict, List, Optional


def _columnar(rows: List[Dict[str, Any]]) -> Dict[str, Any]:

    columns: List[str] = []

    for row in rows:
        for name in row:
            if name not in columns:
                columns.append(name)

    return {
        "cols": columns,
        "rows": [[row.get(name) for name in columns] for row in rows],
    }


def out(result: Dict[str, Any], table_keys: Optional[List[str]] = None) -> str:
    """Serialize a service result for the model."""

    result = dict(result)

    if result.get("ok") is True:
        result.pop("ok")

    for key in table_keys or []:

        rows = result.get(key)

        if isinstance(rows, list) and rows and all(isinstance(row, dict) for row in rows):
            result[key] = _columnar(rows)

    return json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)


def actor_of(tool_context) -> Optional[str]:
    """Who is asking, for audit fields. Set on the agent at creation."""

    try:
        return tool_context.agent.state.get("actor_id")

    except Exception:
        return None


def iter_rows(value: Any) -> List[Dict[str, Any]]:
    """Rows from either a list of dicts or the columnar form."""

    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]

    if isinstance(value, dict) and "cols" in value and "rows" in value:

        return [
            dict(zip(value["cols"], row))
            for row in value["rows"]
        ]

    return []
