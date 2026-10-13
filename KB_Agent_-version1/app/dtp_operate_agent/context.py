"""
Keeping the session state in step with what actually happened.

Both execution paths feed the same state:
    * the deterministic path calls the focus_* helpers directly
    * the LLM path goes through `absorb_tool_result`, called from a hook
      after every tool call

So "it", "his" and "that team" mean the same thing whichever path
answered the previous message.
"""

from typing import Any, Dict, Optional

from memory.state import SessionState
from services.common import norm
from services.ids import issue_key, ticket_key_of
from tools.common import iter_rows


# ============================================================
# FOCUS HELPERS
# ============================================================

def focus_incident(
    state: SessionState,
    ticket_id: str,
    data: Optional[Dict[str, Any]] = None,
):

    key = ticket_key_of(ticket_id) or str(ticket_id).lower()

    return state.set_focus(
        "incident",
        str(ticket_id),
        key,
        label=str((data or {}).get("short_description", ""))[:60],
        data=data,
    )


def focus_issue(
    state: SessionState,
    issue_id: str,
    data: Optional[Dict[str, Any]] = None,
):

    label = " / ".join(
        str(part)
        for part in (
            (data or {}).get("category_name"),
            (data or {}).get("sub_category_name"),
        )
        if part
    )

    return state.set_focus(
        "issue",
        str(issue_id),
        issue_key(issue_id),
        label=label[:80],
        data=data,
    )


def focus_employee(
    state: SessionState,
    ref: str,
    name: str = "",
    data: Optional[Dict[str, Any]] = None,
):
    """`ref` is the person reference (emp:<emp_id>), stable across months."""

    return state.set_focus(
        "employee",
        str(ref),
        str(ref).lower(),
        label=name,
        data=data,
    )


def focus_team(state: SessionState, team_id: Any, name: str = ""):

    if not team_id:
        return None

    return state.set_focus(
        "team",
        str(team_id),
        norm(team_id),
        label=name,
    )


# ============================================================
# TOOL RESULTS (LLM path)
# ============================================================

def absorb_tool_result(
    state: SessionState,
    tool_name: str,
    args: Dict[str, Any],
    data: Any,
) -> None:
    """Update the focus and cache from a tool result."""

    if not isinstance(data, dict) or data.get("ok") is False or "error" in data:
        return

    if tool_name in ("get_incident", "create_incident", "update_incident"):

        ticket_id = data.get("ticket_id")

        if not ticket_id:
            return

        record = dict(data.get("incident") or {})

        entity = focus_incident(state, ticket_id, record or None)

        if tool_name == "update_incident":

            changes = {
                name: change.get("to")
                for name, change in (data.get("changes") or {}).items()
                if name != "working_notes"
            }

            state.apply_changes("incident", entity.key, changes)

        state.last_operation = f"{tool_name.split('_')[0]} incident {ticket_id}"

    elif tool_name == "delete_incident":

        key = ticket_key_of(str(data.get("ticket_id", "")))

        if key:
            state.clear_focus("incident", key)

        state.last_operation = f"delete incident {data.get('ticket_id')}"

    elif tool_name in ("get_issue", "update_issue"):

        issue_id = data.get("issue_id")

        if not issue_id:
            return

        record = dict(data.get("issue") or {})

        entity = focus_issue(state, issue_id, record or None)

        if tool_name == "update_issue":

            state.apply_changes(
                "issue",
                entity.key,
                {name: change.get("to") for name, change in (data.get("changes") or {}).items()},
            )

        state.last_operation = f"{tool_name.split('_')[0]} issue {issue_id}"

    elif tool_name == "search_incidents" or tool_name == "list_incidents_by_date" or tool_name == "list_sla_breaches":

        rows = iter_rows(data.get("incidents"))

        if data.get("total") == 1 and len(rows) == 1 and rows[0].get("ticket_id"):
            focus_incident(state, rows[0]["ticket_id"], rows[0])

        else:
            # "them" / "both of them" in the next message mean these records.
            state.set_list("incident", [row.get("ticket_id") for row in rows])

    elif tool_name == "search_category_matrix":

        rows = iter_rows(data.get("issues"))

        if data.get("total") == 1 and len(rows) == 1 and rows[0].get("issue_id"):
            focus_issue(state, rows[0]["issue_id"], rows[0])

        else:
            state.set_list("issue", [row.get("issue_id") for row in rows])

    elif tool_name == "find_handlers_for_issue":

        rows = iter_rows(data.get("issues"))

        if len(rows) == 1 and rows[0].get("issue_id"):
            focus_issue(state, rows[0]["issue_id"], rows[0])

        focus_team(state, data.get("team_id"))

    elif tool_name == "search_roster":

        rows = iter_rows(data.get("employees"))

        if data.get("total") == 1 and len(rows) == 1 and rows[0].get("ref"):
            focus_employee(state, rows[0]["ref"], rows[0].get("employee_name", ""), rows[0])

        else:
            state.set_list("employee", [row.get("ref") for row in rows])

    elif tool_name in (
        "get_roster_entry",
        "update_roster_entry",
        "get_employee_availability",
        "set_employee_availability",
        "create_roster_entry",
    ):

        ref = data.get("ref")

        if not ref:
            return

        record = dict(data.get("employee") or {})

        focus_employee(
            state,
            ref,
            str(data.get("employee_name") or record.get("employee_name") or ""),
            record or None,
        )

        if tool_name == "update_roster_entry":

            state.apply_changes(
                "employee",
                str(ref).lower(),
                {name: change.get("to") for name, change in (data.get("changes") or {}).items()},
            )

        if tool_name.startswith(("update", "set", "create")):
            state.last_operation = f"{tool_name.split('_')[0]} roster {data.get('employee_name') or ref}"

    elif tool_name == "delete_roster_entry":

        if data.get("ref"):
            state.clear_focus("employee", str(data["ref"]).lower())

        state.last_operation = f"delete roster {data.get('employee_name') or data.get('ref')}"

    elif tool_name in ("get_team_roster", "get_team_availability", "set_team_availability"):

        focus_team(state, data.get("team_id"), str(data.get("team_name") or ""))
