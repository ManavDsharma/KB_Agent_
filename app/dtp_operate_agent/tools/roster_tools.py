from strands import tool
 
from services.roster_service import RosterService
 
 
# ============================================================
# SERVICE
# ============================================================
 
_service = RosterService()
 
 
# ============================================================
# FIND EMPLOYEE
# ============================================================
 
@tool
def find_employee(
    search_term: str,
):
    """
    Find an employee using UID, employee ID, name or email.
    """
 
    return _service.find_employee(
        search_term
    )
 
 
# ============================================================
# SEARCH EMPLOYEES
# ============================================================
 
@tool
def search_employees(
    search_term: str,
    limit: int = None,
):
    """
    Search employees.
    """
 
    return _service.search_employees(
        search_term=search_term,
        limit=limit,
    )
 
 
# ============================================================
# TEAM ROSTER
# ============================================================
 
@tool
def get_team_roster(
    team_id: str,
):
    """
    Retrieve the roster for a team.
    """
 
    return _service.get_team_roster(
        team_id
    )
 
 
# ============================================================
# TEAM DAY AVAILABILITY
# ============================================================
 
@tool
def get_team_day_availability(
    team_name: str,
    date: str,
):
    """
    Retrieve team availability for a date.
    """
 
    return _service.get_team_day_availability(
        team_name=team_name,
        date=date,
    )
 
 
# ============================================================
# EMPLOYEE DAY AVAILABILITY
# ============================================================
 
@tool
def get_employee_day_availability(
    uid: str,
    date: str,
):
    """
    Retrieve employee availability for a date.
    """
 
    return _service.get_employee_day_availability(
        uid=uid,
        date=date,
    )
 
 
# ============================================================
# EMPLOYEE MONTH AVAILABILITY
# ============================================================
 
@tool
def get_employee_month_availability(
    uid: str,
    year: int,
    month: int,
):
    """
    Retrieve employee availability for a month.
    """
 
    return _service.get_employee_month_availability(
        uid=uid,
        year=year,
        month=month,
    )
 
 
# ============================================================
# EMPLOYEE WEEK AVAILABILITY
# ============================================================
 
@tool
def get_employee_week_availability(
    uid: str,
    year: int,
    month: int,
    start_day: int,
):
    """
    Retrieve employee availability for a seven-day period.
    """
 
    return _service.get_employee_week_availability(
        uid=uid,
        year=year,
        month=month,
        start_day=start_day,
    )
 
 
# ============================================================
# UPDATE EMPLOYEE DAY
# ============================================================
 
@tool
def update_employee_day(
    uid: str,
    date: str,
    availability: dict,
):
    """
    Update availability for one employee on one date.
    """
 
    return _service.update_employee_day(
        uid=uid,
        date=date,
        availability=availability,
    )
 
 
# ============================================================
# UPDATE EMPLOYEE DAYS
# ============================================================
 
@tool
def update_employee_days(
    uid: str,
    updates: dict,
):
    """
    Update availability for multiple employee dates.
    """
 
    return _service.update_employee_days(
        uid=uid,
        updates=updates,
    )
 
 
# ============================================================
# UPDATE TEAM DAY
# ============================================================
 
@tool
def update_team_day(
    team_name: str,
    date: str,
    availability: dict,
):
    """
    Update availability for all employees in a team on a date.
    """
 
    return _service.update_team_day(
        team_name=team_name,
        date=date,
        availability=availability,
    )
 
 