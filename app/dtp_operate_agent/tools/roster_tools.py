from typing import Any, Dict, List, Optional

from strands import tool

from services import get_roster_service
from tools.common import actor_of, out

# Roster facts: one row per employee per month; availability per day is
# Y (available) or N (not available / leave). An employee is referred to by
# name, e-mail, employee number or the `ref` (emp:12) from the session context.


@tool
def search_roster(
    query: Optional[str] = None,
    team: Optional[str] = None,
    limit: int = 10,
) -> str:
    """Find employees by name, e-mail or employee number, optionally within a
    team (ID or name); with only `team` it lists the team's members. One entry
    per person, each with a `ref`. If several match, ask which one."""

    return out(
        get_roster_service().search_roster(query, team, limit),
        ["employees"],
    )


@tool
def get_roster_entry(
    employee: str,
    fields: Optional[List[str]] = None,
) -> str:
    """An employee's roster details this month (team, level, shift, timings,
    status) with a summary of days off. `employee`: name, e-mail, number or
    `ref` (use it for he/she/his/her). `fields`: e.g. shift, level, email."""

    return out(get_roster_service().get_roster_entry(employee, fields))


@tool
def get_employee_availability(
    employee: str,
    start_date: str = "today",
    end_date: Optional[str] = None,
) -> str:
    """One employee's availability for a date or range (max 31 days): Y
    available, N not available, null nothing recorded; `no_roster_for` lists
    months without a roster. Dates: YYYY-MM-DD, today, tomorrow, yesterday.
    For a whole team use get_team_availability instead."""

    return out(
        get_roster_service().get_employee_availability(employee, start_date, end_date)
    )


@tool
def get_team_availability(
    team: str,
    date: str = "today",
) -> str:
    """Availability (Y/N) of every member of a team on one date, in one call.
    `team`: ID or name. Dates: YYYY-MM-DD, today, tomorrow, yesterday."""

    return out(get_roster_service().get_team_availability(team, date), ["members"])


@tool(context=True)
def set_employee_availability(
    employee: str,
    availability: Dict[str, Any],
    tool_context=None,
) -> str:
    """Set availability for dates: {"2026-10-01": "N"}. Y/available/working =
    available; N/leave/off = not available. The month must have a roster row."""

    return out(
        get_roster_service().set_employee_availability(
            employee,
            availability,
            actor=actor_of(tool_context),
        )
    )


@tool(context=True)
def set_team_availability(
    team: str,
    date: str,
    availability: Any,
    tool_context=None,
) -> str:
    """Set the same availability (Y/available or N/leave/off) for every member
    of a team on one date."""

    return out(
        get_roster_service().set_team_availability(
            team,
            date,
            availability,
            actor=actor_of(tool_context),
        )
    )


@tool(context=True)
def update_roster_entry(
    employee: str,
    changes: Dict[str, Any],
    year: Optional[int] = None,
    month: Optional[int] = None,
    tool_context=None,
) -> str:
    """Update roster details, e.g. {"shift": "S2"}. Shift, level and team change
    one month's row (this month unless year/month are given); name, employee
    number and e-mail change all rows. Fields: employee_name, emp_id,
    employee_id (e-mail), team_id, team_name, team_email_id, level, shift,
    shift_start_time, shift_end_time, active (Yes/No)."""

    return out(
        get_roster_service().update_roster_entry(
            employee,
            changes,
            actor=actor_of(tool_context),
            year=year,
            month=month,
        )
    )


@tool(context=True)
def create_roster_entry(
    employee_name: Optional[str] = None,
    employee_id: Optional[str] = None,
    emp_id: Optional[str] = None,
    team_id: Optional[str] = None,
    level: Optional[str] = None,
    shift: Optional[str] = None,
    shift_start_time: Optional[str] = None,
    shift_end_time: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    tool_context=None,
) -> str:
    """Create an employee's roster row for a month (default this month). Pass
    only what the user gave. Required: employee_name, employee_id (e-mail),
    emp_id, team_id (an existing team); the result lists what is `missing`:
    ask for exactly that. An existing employee only needs emp_id."""

    return out(
        get_roster_service().create_roster_entry(
            {
                "employee_name": employee_name,
                "employee_id": employee_id,
                "emp_id": emp_id,
                "team_id": team_id,
                "level": level,
                "shift": shift,
                "shift_start_time": shift_start_time,
                "shift_end_time": shift_end_time,
                "year": year,
                "month": month,
            },
            actor=actor_of(tool_context),
        )
    )


@tool(context=True)
def delete_roster_entry(
    employee: str,
    year: Optional[int] = None,
    month: Optional[int] = None,
    tool_context=None,
) -> str:
    """Delete one monthly roster row (given month, else this month, else the
    only one; otherwise the result asks which month). Only when the user
    clearly asks. `employee` must match exactly one person."""

    return out(
        get_roster_service().delete_roster_entry(
            employee,
            actor=actor_of(tool_context),
            year=year,
            month=month,
        )
    )
