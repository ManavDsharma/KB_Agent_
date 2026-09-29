from strands import tool
 
from services.incident_service import IncidentService
 
 
# ============================================================
# SERVICE
# ============================================================
 
_service = IncidentService()
 
 
# ============================================================
# GET INCIDENT
# ============================================================
 
@tool
def get_incident(
    ticket_id: str,
):
    """
    Retrieve a complete incident using its ticket ID.
 
    Example:
        INC-1001
    """
 
    return _service.get_incident(
        ticket_id
    )
 
 
# ============================================================
# LIST INCIDENTS BY DATE
# ============================================================
 
@tool
def list_incidents_by_date(
    date: str,
    limit: int = None,
):
    """
    List incidents created on a specific date.
 
    Date format:
        YYYY-MM-DD
    """
 
    return _service.list_incidents_by_date(
        date=date,
        limit=limit,
    )
 
 
# ============================================================
# LIST INCIDENTS BY DATE RANGE
# ============================================================
 
@tool
def list_incidents_by_date_range(
    start_date: str,
    end_date: str,
    limit: int = None,
):
    """
    List incidents created between two dates.
 
    Date format:
        YYYY-MM-DD
    """
 
    return _service.list_incidents_by_date_range(
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
 
 
# ============================================================
# SEARCH INCIDENTS
# ============================================================
 
@tool
def search_incidents(
    category: str = None,
    sub_category: str = None,
    urgency: str = None,
    priority: str = None,
    team_id: str = None,
    state: str = None,
    limit: int = None,
):
    """
    Search incidents using structured filters.
    """
 
    return _service.search_incidents(
        category=category,
        sub_category=sub_category,
        urgency=urgency,
        priority=priority,
        team_id=team_id,
        state=state,
        limit=limit,
    )
 
 
# ============================================================
# SLA BREACHES
# ============================================================
 
@tool
def list_sla_breaches(
    breach_type: str = "all",
    limit: int = None,
):
    """
    List incidents with SLA breaches.
 
    breach_type:
        all
        assignment
        resolution
    """
 
    return _service.list_sla_breaches(
        breach_type=breach_type,
        limit=limit,
    )
 
 
# ============================================================
# UPDATE INCIDENT
# ============================================================
 
@tool
def update_incident(
    ticket_id: str,
    field: str,
    value,
):
    """
    Update an allowed incident field.
 
    The caller should provide:
        ticket_id
        field
        value
    """
 
    return _service.update_incident(
        ticket_id=ticket_id,
        field=field,
        value=value,
    )
 
 