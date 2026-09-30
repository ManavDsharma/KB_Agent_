from typing import Any, Dict, List, Optional

from strands import tool

from services import get_categorization_service
from tools.common import actor_of, out


@tool
def get_issue(
    issue_id: str,
    fields: Optional[List[str]] = None,
) -> str:
    """Get a Category Matrix issue (Issue-1): category, sub-category, type,
    impact, urgency, service, owning team. Pass `fields` (team_id,
    issue_type, impact...) to return only what is needed."""

    return out(get_categorization_service().get_issue(issue_id, fields))


@tool
def search_category_matrix(
    text: Optional[str] = None,
    category_name: Optional[str] = None,
    sub_category_name: Optional[str] = None,
    issue_type: Optional[str] = None,
    impact: Optional[str] = None,
    urgency: Optional[str] = None,
    affected_service: Optional[str] = None,
    team_id: Optional[str] = None,
    limit: int = 10,
) -> str:
    """Find Category Matrix issues by words or filters (combined). `text`
    matches issue, description, category, sub-category and service."""

    return out(
        get_categorization_service().search_issues(
            text=text,
            category_name=category_name,
            sub_category_name=sub_category_name,
            issue_type=issue_type,
            impact=impact,
            urgency=urgency,
            affected_service=affected_service,
            team_id=team_id,
            limit=limit,
        ),
        ["issues"],
    )


@tool
def list_categories() -> str:
    """List all categories with their sub-categories."""

    return out(get_categorization_service().list_categories())


@tool(context=True)
def update_issue(
    issue_id: str,
    changes: Dict[str, Any],
    tool_context=None,
) -> str:
    """Update Category Matrix fields, e.g. {"impact": "High"}. Run it when target
    and change are clear. Fields: category_name, sub_category_name, issue,
    issue_description, issue_type, impact, urgency, affected_service,
    business_impact, team_id, standard_request_id, active."""

    return out(
        get_categorization_service().update_issue(
            issue_id,
            changes,
            actor=actor_of(tool_context),
        )
    )
