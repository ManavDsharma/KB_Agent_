from strands import tool
 
from services.categorization_service import (
    CategorizationService,
)
 
# ============================================================
# SERVICE
# ============================================================
 
_service = CategorizationService()
 
 
# ============================================================
# GET ISSUE
# ============================================================
 
@tool
def get_issue(
    issue_id: str,
):
    """
    Retrieve a complete categorization issue.
    """
 
    return _service.get_issue(
        issue_id
    )
 
 
# ============================================================
# GET ISSUE FIELD
# ============================================================
 
@tool
def get_issue_field(
    issue_id: str,
    field: str,
):
    """
    Retrieve one field from an issue.
    """
 
    return _service.get_issue_field(
        issue_id,
        field,
    )
 
 
# ============================================================
# GET CATEGORY NAMES
# ============================================================
 
@tool
def get_all_category_names():
    """
    Return all active category names.
    """
 
    return _service.get_all_category_names()
 
 
# ============================================================
# FIND ISSUE BY NAME
# ============================================================
 
@tool
def find_issue_by_name(
    issue_name: str,
):
    """
    Find categorization issues by issue name.
    """
 
    return _service.find_issue_by_name(
        issue_name
    )
 
 
# ============================================================
# SEARCH ISSUES
# ============================================================
 
@tool
def search_issues(
    category_name: str = None,
    sub_category_name: str = None,
    issue_type: str = None,
    impact: str = None,
    urgency: str = None,
    affected_service: str = None,
    team_id: str = None,
    limit: int = None,
):
    """
    Search categorization issues.
    """
 
    return _service.search_issues(
        category_name=category_name,
        sub_category_name=sub_category_name,
        issue_type=issue_type,
        impact=impact,
        urgency=urgency,
        affected_service=affected_service,
        team_id=team_id,
        limit=limit,
    )
 
 
# ============================================================
# UPDATE ISSUE FIELD
# ============================================================
 
@tool
def update_issue_field(
    issue_id: str,
    field: str,
    value,
):
    """
    Update a single categorization field.
    """
 
    return _service.update_issue_field(
        issue_id=issue_id,
        field=field,
        value=value,
    )
 
 
# ============================================================
# UPDATE ISSUE
# ============================================================
 
@tool
def update_issue(
    issue_id: str,
    updates: dict,
):
    """
    Update multiple categorization fields.
    """
 
    return _service.update_issue(
        issue_id=issue_id,
        updates=updates,
    )
 
 