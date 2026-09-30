"""
Offline stand-ins for DynamoDB and the Bedrock Knowledge Base, and
sample data shaped like the real tables.
"""

import calendar
import copy
from datetime import timedelta
from typing import Any, Dict, List, Optional

from botocore.exceptions import ClientError

from services import set_service, reset_services
from services.assignment_service import AssignmentService
from services.categorization_service import CategorizationService
from services.common import today
from services.incident_service import IncidentService
from services.knowledge_base_service import KnowledgeBaseService
from services.roster_service import RosterService


def client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Op")


# ============================================================
# FAKE DYNAMODB TABLE
# ============================================================

def _evaluate(condition, item: Dict[str, Any]) -> bool:
    """Evaluate a boto3 condition object (Attr(...).eq(...) & ...)."""

    expression = condition.get_expression()

    operator = expression["operator"]

    values = expression["values"]

    if operator == "AND":
        return all(_evaluate(part, item) for part in values)

    if operator == "OR":
        return any(_evaluate(part, item) for part in values)

    actual = item.get(values[0].name)

    if operator == "=":
        return actual == values[1]

    if operator == "begins_with":
        return isinstance(actual, str) and actual.startswith(values[1])

    if operator == "BETWEEN":
        return actual is not None and values[1] <= actual <= values[2]

    if operator == "IN":
        return actual in values[1]

    raise NotImplementedError(operator)


class FakeTable:
    """Enough of the boto3 Table API for the services."""

    def __init__(self, key: str, items: List[Dict[str, Any]], page_size: int = 2):

        self.key = key

        self.items = {item[key]: copy.deepcopy(item) for item in items}

        self.page_size = page_size

        self.calls: List[str] = []

        self.fail_with: Optional[ClientError] = None

    # ---- helpers --------------------------------------------------

    def _maybe_fail(self, name: str) -> None:

        self.calls.append(name)

        if self.fail_with is not None:
            raise self.fail_with

    def count(self, name: str) -> int:
        return self.calls.count(name)

    @staticmethod
    def _resolve(name: str, names: Dict[str, str]) -> str:
        return names.get(name, name)

    def _check(self, condition: Optional[str], item, names) -> None:

        if not condition:
            return

        for clause in condition.split(" AND "):

            clause = clause.strip()

            attribute = self._resolve(clause[clause.index("(") + 1: -1], names)

            if clause.startswith("attribute_not_exists"):
                ok = item is None or attribute not in item

            else:
                ok = item is not None and attribute in item

            if not ok:
                raise client_error("ConditionalCheckFailedException")

    # ---- API --------------------------------------------------------

    def get_item(self, Key, ProjectionExpression=None, ExpressionAttributeNames=None, **_):

        self._maybe_fail("get_item")

        item = self.items.get(Key[self.key])

        if item is None:
            return {}

        item = copy.deepcopy(item)

        if ProjectionExpression:

            names = ExpressionAttributeNames or {}

            wanted = [self._resolve(part.strip(), names) for part in ProjectionExpression.split(",")]

            item = {name: item[name] for name in wanted if name in item}

        return {"Item": item}

    def scan(self, FilterExpression=None, ProjectionExpression=None, ExpressionAttributeNames=None, ExclusiveStartKey=None, **_):

        self._maybe_fail("scan")

        keys = list(self.items)

        start = keys.index(ExclusiveStartKey[self.key]) + 1 if ExclusiveStartKey else 0

        page = keys[start: start + self.page_size]

        found = []

        for key in page:

            item = copy.deepcopy(self.items[key])

            if FilterExpression is not None and not _evaluate(FilterExpression, item):
                continue

            if ProjectionExpression:

                names = ExpressionAttributeNames or {}

                wanted = [self._resolve(part.strip(), names) for part in ProjectionExpression.split(",")]

                item = {name: item[name] for name in wanted if name in item}

            found.append(item)

        response: Dict[str, Any] = {"Items": found}

        if start + self.page_size < len(keys):
            response["LastEvaluatedKey"] = {self.key: page[-1]}

        return response

    def put_item(self, Item, ConditionExpression=None, ExpressionAttributeNames=None, **_):

        self._maybe_fail("put_item")

        self._check(ConditionExpression, self.items.get(Item[self.key]), ExpressionAttributeNames or {})

        self.items[Item[self.key]] = copy.deepcopy(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames=None, ExpressionAttributeValues=None, ConditionExpression=None, **_):

        self._maybe_fail("update_item")

        names = ExpressionAttributeNames or {}

        values = ExpressionAttributeValues or {}

        item = self.items.get(Key[self.key])

        self._check(ConditionExpression, item, names)

        staged = copy.deepcopy(item)

        for assignment in UpdateExpression[len("SET "):].split(", "):

            left, right = (part.strip() for part in assignment.split(" = "))

            path = [self._resolve(part, names) for part in left.split(".")]

            target = staged

            for step in path[:-1]:

                if not isinstance(target.get(step), dict):
                    raise client_error(
                        "ValidationException",
                        "The document path provided in the update expression is invalid for update",
                    )

                target = target[step]

            target[path[-1]] = copy.deepcopy(values[right])

        self.items[Key[self.key]] = staged

        return {"Attributes": copy.deepcopy(staged)}

    def delete_item(self, Key, ConditionExpression=None, ExpressionAttributeNames=None, ReturnValues=None, **_):

        self._maybe_fail("delete_item")

        item = self.items.get(Key[self.key])

        self._check(ConditionExpression, item, ExpressionAttributeNames or {})

        removed = self.items.pop(Key[self.key])

        return {"Attributes": copy.deepcopy(removed)} if ReturnValues == "ALL_OLD" else {}


# ============================================================
# FAKE KNOWLEDGE BASE
# ============================================================

class FakeKBClient:

    def __init__(self, results: Optional[List[Dict[str, Any]]] = None):

        self.results = results or []

        self.requests: List[Dict[str, Any]] = []

        self.fail_with: Optional[ClientError] = None

    def retrieve(self, **kwargs):

        self.requests.append(kwargs)

        if self.fail_with is not None:
            raise self.fail_with

        return {"retrievalResults": copy.deepcopy(self.results)}


# ============================================================
# SAMPLE DATA (shaped like the real tables)
# ============================================================

def sample_incidents() -> List[Dict[str, Any]]:

    now = today().isoformat()

    return [
        {
            "ticket_id": "INC0000258",
            "url": "https://abc.com/itsm/ticket/INC0000258",
            "source_system": "Phone",
            "description": (
                "Reported by Simran Gupta (Engineering department, simran.gupta@abc.com): "
                "The IGA platform's database becomes unavailable. "
            ) * 8,
            "short_description": "Database Failure - Database",
            "category": "Infrastructure",
            "sub_category": "Database",
            "urgency": "High",
            "priority": "P1-Critical",
            "team_id": "Infrastructure",
            "assigned_to": "varun.nambiar@abc.com",
            "opened_by": "simran.gupta@abc.com",
            "opened_for": "simran.gupta@abc.com",
            "state": "Closed",
            "assignment_sla": 60,
            "resolution_sla": 720,
            "assignment_duration": 26,
            "has_assignment_sla_breach": "FALSE",
            "resolution_duration": 308,
            "has_resolution_sla_breach": "FALSE",
            "working_notes": "Resolved after applying the standard fix for this issue type; ticket closed.",
            "created_at": "2025-03-14 10:10",
            "updated_at": "2025-03-14 18:56",
            "closed_at": "2025-03-14 18:56",
        },
        {
            "ticket_id": "REQ0000162",
            "url": "https://abc.com/itsm/ticket/REQ0000162",
            "source_system": "Phone",
            "description": "Reported by Deepika Bansal (Marketing department): A request to unlock a user's account.",
            "short_description": "Unlock Account - User Administration",
            "category": "Administration",
            "sub_category": "User Administration",
            "urgency": "High",
            "priority": "P3-Medium",
            "team_id": "Service Desk",
            "assigned_to": "amit.verma@abc.com",
            "opened_by": "ishaan.gupta@abc.com",
            "opened_for": "deepika.bansal@abc.com",
            "state": "Closed",
            "assignment_sla": "1440",
            "resolution_sla": "4320",
            "assignment_duration": 830,
            "has_assignment_sla_breach": "FALSE",
            "resolution_duration": 6010,
            "has_resolution_sla_breach": "TRUE",
            "working_notes": "Request actioned and confirmed complete by the requester.",
            "created_at": "2026-02-10 16:09",
            "updated_at": "2026-02-11 19:04",
            "closed_at": "2026-02-14 21:39",
        },
        {
            "ticket_id": "INC0000259",
            "short_description": "Password reset failing",
            "description": "Users cannot reset passwords",
            "category": "Access",
            "sub_category": "Password",
            "urgency": "Low",
            "priority": "P3-Medium",
            "team_id": "Service Desk",
            "opened_by": "simran.gupta@abc.com",
            "state": "New",
            "assignment_sla": 240,
            "resolution_sla": 1440,
            "has_assignment_sla_breach": "FALSE",
            "has_resolution_sla_breach": "FALSE",
            "created_at": f"{now} 09:00",
        },
        {
            "ticket_id": "INC0000260",
            "short_description": "VPN down for remote users",
            "description": "VPN gateway timeouts",
            "category": "Network",
            "sub_category": "VPN",
            "urgency": "Medium",
            "priority": "P2-High",
            "team_id": "Infrastructure",
            "assigned_to": "rohit.malhotra@abc.com",
            "opened_by": "simran.gupta@abc.com",
            "state": "In Progress",
            "has_assignment_sla_breach": "TRUE",
            "has_resolution_sla_breach": "FALSE",
            "created_at": f"{now} 08:00",
        },
    ]


def sample_issues() -> List[Dict[str, Any]]:

    return [
        {
            "issue_id": "Issue-1",
            "category_name": "Identity Lifecycle",
            "category_description": "Identity actions triggered by a lifecycle change (Joiner, Mover, Leaver).",
            "sub_category_name": "Joiner",
            "sub_category_description": "Identity actions required when a new employee joins.",
            "issue": "New Identity Creation",
            "issue_description": "A request to create a new digital identity and user account for a new hire.",
            "issue_type": "Service Request",
            "impact": "Low",
            "urgency": "Medium",
            "affected_service": "HR-to-IGA Identity Lifecycle Process",
            "business_impact": "If delayed, the new hire cannot access systems on day one.",
            "team_id": "Identity Operations",
            "standard_request_id": "",
            "active": "Yes",
            "created_on": "2026-09-10",
            "updated_on": "2026-09-10",
            "updated_by": "Categorization Matrix Import",
        },
        {
            "issue_id": "Issue-2",
            "category_name": "Identity Lifecycle",
            "sub_category_name": "Leaver",
            "issue": "Leaver Account Removal",
            "issue_description": "Remove access for employees who have left.",
            "issue_type": "Service Request",
            "impact": "High",
            "urgency": "High",
            "affected_service": "HR-to-IGA Identity Lifecycle Process",
            "team_id": "Identity Operations",
            "standard_request_id": "",
            "active": "Yes",
        },
        {
            "issue_id": "Issue-3",
            "category_name": "Infrastructure",
            "sub_category_name": "Database",
            "issue": "Database Outage",
            "issue_description": "The production database is down.",
            "issue_type": "Incident",
            "impact": "High",
            "urgency": "High",
            "affected_service": "Core DB",
            "team_id": "Infrastructure",
            "standard_request_id": "",
            "active": "Yes",
        },
    ]


def month_days(**overrides: str) -> Dict[str, str]:
    """Day-of-month -> Y/N for the current month; every day "Y" by default."""

    current = today()

    days = {
        str(day): "Y"
        for day in range(1, calendar.monthrange(current.year, current.month)[1] + 1)
    }

    days.update(overrides)

    return days


def previous_month():
    return (today().replace(day=1) - timedelta(days=1)).replace(day=1)


ACCESS_TEAM = {
    "team_id": "Access Management",
    "team_name": "Access Management Team",
    "team_email_id": "accessmanagement@abc.com",
}

INFRA_TEAM = {
    "team_id": "Infrastructure",
    "team_name": "Infrastructure Team",
    "team_email_id": "infra@abc.com",
}


def roster_row(uid, emp_id, name, email, team, level, shift, start, end, year, month, days):
    """One roster row: one employee for one month."""

    return {
        "UID": uid,
        "emp_id": emp_id,
        "employee_name": name,
        "employee_id": email,
        **team,
        "year": year,
        "month": month,
        "days_availability": days,
        "level": level,
        "shift": shift,
        "shift_start_time": start,
        "shift_end_time": end,
        "active": "Yes",
        "created_on": "2026-09-22",
        "updated_on": "2026-09-22",
        "updated_by": "System - Roster Import",
    }


def sample_roster(extra: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    Rohit has two monthly rows (this month and last month); everyone
    else has this month only. UIDs are unique per row, emp_id per person.
    """

    current = today()

    previous = previous_month()

    people = [
        roster_row("1", "1", "Rohit Malhotra", "rohit.malhotra@abc.com", ACCESS_TEAM, "L2", "S1",
                   "06:00:00 AM", "02:00:00 PM", current.year, current.month,
                   month_days(**{"5": "N", "6": "N", str(current.day): "Y"})),
        roster_row("11", "1", "Rohit Malhotra", "rohit.malhotra@abc.com", ACCESS_TEAM, "L2", "S1",
                   "06:00:00 AM", "02:00:00 PM", previous.year, previous.month, {"1": "Y", "2": "N"}),
        roster_row("3", "3", "Priya Nair", "priya.nair@abc.com", ACCESS_TEAM, "L1", "S2",
                   "02:00:00 PM", "10:00:00 PM", current.year, current.month,
                   month_days(**{str(current.day): "N"})),
        roster_row("4", "4", "Varun Nambiar", "varun.nambiar@abc.com", INFRA_TEAM, "L3", "S1",
                   "06:00:00 AM", "02:00:00 PM", current.year, current.month, month_days()),
    ]

    return people + (extra or [])


def second_rohit() -> Dict[str, Any]:

    current = today()

    return roster_row(
        "2", "2", "Rohit Sharma", "rohit.sharma@abc.com", INFRA_TEAM, "L2", "S2",
        "02:00:00 PM", "10:00:00 PM", current.year, current.month, month_days(),
    )


class World:
    """All three fake tables, the KB, and services wired to them."""

    def __init__(self, extra_people=None, kb_results=None, page_size=2):

        self.incidents = FakeTable("ticket_id", sample_incidents(), page_size)

        self.issues = FakeTable("issue_id", sample_issues(), page_size)

        self.roster = FakeTable("UID", sample_roster(extra_people), page_size)

        self.kb = FakeKBClient(kb_results)

        self.categories = CategorizationService(table=self.issues)

        self.people = RosterService(table=self.roster)

        self.incident_service = IncidentService(
            table=self.incidents,
            catalog=self.categories,
            people=self.people,
        )

        self.knowledge = KnowledgeBaseService(client=self.kb)

        self.assignment = AssignmentService(self.categories, self.people)

    def install(self) -> "World":

        reset_services()

        set_service("categorization", self.categories)

        set_service("roster", self.people)

        set_service("incident", self.incident_service)

        set_service("knowledge", self.knowledge)

        set_service("assignment", self.assignment)

        return self

    def close(self) -> None:
        reset_services()
