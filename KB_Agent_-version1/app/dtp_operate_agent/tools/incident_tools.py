from typing import Any, Dict, List, Literal, Optional

from strands import tool

from services import get_incident_service
from tools.common import actor_of, out

# Tool descriptions are sent to the model on every call: keep them short.


@tool
def get_incident(
    ticket_id: str,
    fields: Optional[List[str]] = None,
) -> str:
    """Get an incident/request by ID (INC0000258, REQ0000162).

    Pass `fields` (priority, state, assigned_to, opened_by, sla, breached,
    description, working_notes...) to return only what is needed.
    """

    return out(get_incident_service().get_incident(ticket_id, fields))


@tool
def search_incidents(
    category: Optional[str] = None,
    sub_category: Optional[str] = None,
    urgency: Optional[str] = None,
    priority: Optional[str] = None,
    state: Optional[str] = None,
    team_id: Optional[str] = None,
    assigned_to: Optional[str] = None,
    text: Optional[str] = None,
    limit: int = 10,
) -> str:
    """Find incidents by filters (combined), newest first. Values match existing
    ones ("P2" finds "P2-High"); `text` matches words in the description."""

    return out(
        get_incident_service().search_incidents(
            category=category,
            sub_category=sub_category,
            urgency=urgency,
            priority=priority,
            state=state,
            team_id=team_id,
            assigned_to=assigned_to,
            text=text,
            limit=limit,
        ),
        ["incidents"],
    )


@tool
def list_incidents_by_date(
    date: str,
    end_date: Optional[str] = None,
    limit: int = 10,
) -> str:
    """List incidents created on a date (YYYY-MM-DD, today, yesterday) or through end_date."""

    return out(
        get_incident_service().list_incidents_by_date(date, end_date, limit),
        ["incidents"],
    )


@tool
def list_sla_breaches(
    breach_type: Literal["all", "assignment", "resolution"] = "all",
    limit: int = 10,
) -> str:
    """List incidents that breached their assignment and/or resolution SLA."""

    return out(
        get_incident_service().list_sla_breaches(breach_type, limit),
        ["incidents"],
    )


@tool(context=True)
def create_incident(
    short_description: Optional[str] = None,
    category: Optional[str] = None,
    urgency: Optional[str] = None,
    priority: Optional[str] = None,
    description: Optional[str] = None,
    sub_category: Optional[str] = None,
    team_id: Optional[str] = None,
    assigned_to: Optional[str] = None,
    opened_by: Optional[str] = None,
    opened_for: Optional[str] = None,
    tool_context=None,
) -> str:
    """Create an incident. Pass only what the user gave; if information is
    missing the result lists it (`missing`): ask for exactly that."""

    return out(
        get_incident_service().create_incident(
            {
                "short_description": short_description,
                "category": category,
                "urgency": urgency,
                "priority": priority,
                "description": description,
                "sub_category": sub_category,
                "team_id": team_id,
                "assigned_to": assigned_to,
                "opened_by": opened_by,
                "opened_for": opened_for,
            },
            actor=actor_of(tool_context),
        )
    )


@tool(context=True)
def update_incident(
    ticket_id: str,
    changes: Dict[str, Any],
    tool_context=None,
) -> str:
    """Update incident fields, e.g. {"priority": "P2"}. Run it when target and
    change are clear. Values map to existing ones; a bad value returns the
    allowed list. Fields: priority, urgency, state, category, sub_category,
    team_id, assigned_to, opened_by, opened_for, short_description,
    description, working_notes (appended), assignment_sla, resolution_sla."""

    return out(
        get_incident_service().update_incident(
            ticket_id,
            changes,
            actor=actor_of(tool_context),
        )
    )


@tool(context=True)
def delete_incident(
    ticket_id: str,
    tool_context=None,
) -> str:
    """Delete an incident. Only when the user clearly asks."""

    return out(
        get_incident_service().delete_incident(
            ticket_id,
            actor=actor_of(tool_context),
        )
    )
