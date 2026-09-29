import re
from typing import Any, Dict, Optional
 
 
class DeterministicRouter:
 
    ISSUE_PATTERN = re.compile(
        r"\bissue[\s\-_]*(\d+)\b",
        re.IGNORECASE,
    )
 
    INCIDENT_PATTERN = re.compile(
        r"\b(?:incident|inc)[\s\-_]*(\d+)\b",
        re.IGNORECASE,
    )
 
    ISSUE_FIELD_ALIASES = {
        "impact": "impact",
        "urgency": "urgency",
        "category": "category_name",
        "category name": "category_name",
        "subcategory": "sub_category_name",
        "sub category": "sub_category_name",
        "sub_category": "sub_category_name",
        "issue": "issue",
        "description": "issue_description",
        "issue description": "issue_description",
        "issue type": "issue_type",
        "affected service": "affected_service",
        "business impact": "business_impact",
        "team": "team_id",
        "team id": "team_id",
        "active": "active",
    }
 
    INCIDENT_FIELD_ALIASES = {
        "description": "description",
        "short description": "short_description",
        "category": "category",
        "sub category": "sub_category",
        "urgency": "urgency",
        "priority": "priority",
        "team": "team_id",
        "team id": "team_id",
        "assigned to": "assigned_to",
        "state": "state",
        "assignment sla": "assignment_sla",
        "resolution sla": "resolution_sla",
        "working notes": "working_notes",
    }
 
    UPDATE_KEYWORDS = (
        "update",
        "change",
        "modify",
        "set",
        "edit",
    )
 
    CATEGORY_LIST_KEYWORDS = (
        "all categories",
        "category names",
        "list categories",
        "available categories",
    )
 
    @classmethod
    def extract_issue_id(
        cls,
        text: str,
    ) -> Optional[str]:
 
        match = cls.ISSUE_PATTERN.search(text)
 
        if not match:
            return None
 
        return f"Issue-{match.group(1)}"
 
    @classmethod
    def extract_incident_id(
        cls,
        text: str,
    ) -> Optional[str]:
 
        match = cls.INCIDENT_PATTERN.search(text)
 
        if not match:
            return None
 
        return f"INC-{match.group(1)}"
 
    @classmethod
    def detect_issue_field(
        cls,
        text: str,
    ) -> Optional[str]:
 
        text = text.lower()
 
        aliases = sorted(
            cls.ISSUE_FIELD_ALIASES.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        )
 
        for alias, field in aliases:
 
            if alias in text:
                return field
 
        return None
 
    @classmethod
    def detect_incident_field(
        cls,
        text: str,
    ) -> Optional[str]:
 
        text = text.lower()
 
        aliases = sorted(
            cls.INCIDENT_FIELD_ALIASES.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        )
 
        for alias, field in aliases:
 
            if alias in text:
                return field
 
        return None
 
    @classmethod
    def is_update_request(
        cls,
        text: str,
    ) -> bool:
 
        text = text.lower()
 
        return any(
            keyword in text
            for keyword in cls.UPDATE_KEYWORDS
        )
 
    @classmethod
    def is_category_list_request(
        cls,
        text: str,
    ) -> bool:
 
        text = text.lower()
 
        return any(
            keyword in text
            for keyword in cls.CATEGORY_LIST_KEYWORDS
        )
 
    @classmethod
    def route(
        cls,
        text: str,
    ) -> Dict[str, Any]:
 
        issue_id = cls.extract_issue_id(text)
 
        incident_id = cls.extract_incident_id(text)
 
        update_request = cls.is_update_request(
            text
        )
 
        if cls.is_category_list_request(
            text
        ):
 
            return {
                "route": "category_list",
                "entity": None,
                "field": None,
                "operation": "read",
            }
 
        if issue_id:
 
            field = cls.detect_issue_field(
                text
            )
 
            if update_request:
 
                if field:
 
                    return {
                        "route": "issue_update_field",
                        "entity": issue_id,
                        "field": field,
                        "operation": "update",
                    }
 
                return {
                    "route": "issue_update",
                    "entity": issue_id,
                    "field": None,
                    "operation": "update",
                }
 
            if field:
 
                return {
                    "route": "issue_field",
                    "entity": issue_id,
                    "field": field,
                    "operation": "read",
                }
 
            return {
                "route": "issue",
                "entity": issue_id,
                "field": None,
                "operation": "read",
            }
 
        if incident_id:
 
            field = cls.detect_incident_field(
                text
            )
 
            if update_request:
 
                return {
                    "route": "incident_update",
                    "entity": incident_id,
                    "field": field,
                    "operation": "update",
                }
 
            if field:
 
                return {
                    "route": "incident_field",
                    "entity": incident_id,
                    "field": field,
                    "operation": "read",
                }
 
            return {
                "route": "incident",
                "entity": incident_id,
                "field": None,
                "operation": "read",
            }
 
        return {
            "route": "supervisor",
            "entity": None,
            "field": None,
            "operation": "unknown",
        }
 
 