from datetime import datetime
from decimal import Decimal
 
from botocore.exceptions import ClientError
 
from config import (
    AWS_REGION,
    INCIDENT_TABLE_NAME,
    DEFAULT_RESULT_LIMIT,
    MAX_RESULT_LIMIT,
    get_boto3_session,
)
 
 
class IncidentService:
 
    ALLOWED_UPDATE_FIELDS = {
        "description",
        "short_description",
        "category",
        "sub_category",
        "urgency",
        "priority",
        "team_id",
        "assigned_to",
        "opened_by",
        "opened_for",
        "state",
        "assignment_sla",
        "resolution_sla",
        "assignment_duration",
        "has_assignment_sla_breach",
        "resolution_duration",
        "has_resolution_sla_breach",
        "working_notes",
        "updated_at",
        "closed_at",
        "url",
        "source_system",
    }
 
    SUMMARY_FIELDS = [
        "ticket_id",
        "short_description",
        "category",
        "sub_category",
        "urgency",
        "priority",
        "team_id",
        "assigned_to",
        "state",
        "created_at",
        "updated_at",
        "has_assignment_sla_breach",
        "has_resolution_sla_breach",
    ]
 
    def __init__(self):
 
        session = get_boto3_session()
 
        dynamodb = session.resource(
            "dynamodb",
            region_name=AWS_REGION,
        )
 
        self.table = dynamodb.Table(
            INCIDENT_TABLE_NAME
        )
 
    # ========================================================
    # DECIMAL CONVERSION
    # ========================================================
 
    def _convert_decimal(
        self,
        value,
    ):
 
        if isinstance(
            value,
            Decimal,
        ):
 
            if value % 1 == 0:
                return int(value)
 
            return float(value)
 
        if isinstance(
            value,
            dict,
        ):
 
            return {
                key: self._convert_decimal(
                    val
                )
                for key, val in value.items()
            }
 
        if isinstance(
            value,
            list,
        ):
 
            return [
                self._convert_decimal(
                    item
                )
                for item in value
            ]
 
        return value
 
    # ========================================================
    # LIMIT
    # ========================================================
 
    def _limit(
        self,
        limit,
    ):
 
        if limit is None:
            return DEFAULT_RESULT_LIMIT
 
        return max(
            1,
            min(
                int(limit),
                MAX_RESULT_LIMIT,
            ),
        )
 
    # ========================================================
    # COMPACT INCIDENT
    # ========================================================
 
    def _compact_incident(
        self,
        item,
    ):
 
        return {
            field: self._convert_decimal(
                item[field]
            )
            for field in self.SUMMARY_FIELDS
            if field in item
        }
 
    # ========================================================
    # GET INCIDENT
    # ========================================================
 
    def get_incident(
        self,
        ticket_id,
    ):
 
        try:
 
            response = self.table.get_item(
                Key={
                    "ticket_id": ticket_id
                }
            )
 
            item = response.get(
                "Item"
            )
 
            if not item:
 
                return {
                    "found": False,
                    "ticket_id": ticket_id,
                    "message": (
                        f"Incident {ticket_id} "
                        "was not found."
                    ),
                }
 
            return {
                "found": True,
                "incident": self._convert_decimal(
                    item
                ),
            }
 
        except ClientError as exc:
 
            raise RuntimeError(
                f"Unable to retrieve incident "
                f"{ticket_id}: {exc}"
            ) from exc
 
    # ========================================================
    # SCAN ALL
    # ========================================================
 
    def scan_all(self):
 
        items = []
 
        scan_kwargs = {}
 
        while True:
 
            response = self.table.scan(
                **scan_kwargs
            )
 
            items.extend(
                response.get(
                    "Items",
                    [],
                )
            )
 
            last_key = response.get(
                "LastEvaluatedKey"
            )
 
            if not last_key:
                break
 
            scan_kwargs[
                "ExclusiveStartKey"
            ] = last_key
 
        return items
 
    # ========================================================
    # PARSE DATE
    # ========================================================
 
    def parse_created_at(
        self,
        value,
    ):
 
        if not value:
            return None
 
        value = str(value).strip()
 
        formats = [
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%SZ",
        ]
 
        for fmt in formats:
 
            try:
 
                return datetime.strptime(
                    value,
                    fmt,
                )
 
            except ValueError:
 
                continue
 
        return None
 
    # ========================================================
    # LIST BY DATE
    # ========================================================
 
    def list_incidents_by_date(
        self,
        date,
        limit=None,
    ):
 
        target_date = datetime.strptime(
            date,
            "%Y-%m-%d",
        ).date()
 
        incidents = self.scan_all()
 
        results = []
 
        for incident in incidents:
 
            created_at = self.parse_created_at(
                incident.get("created_at")
            )
 
            if (
                created_at
                and created_at.date()
                == target_date
            ):
 
                results.append(
                    self._compact_incident(
                        incident
                    )
                )
 
        results = results[
            : self._limit(limit)
        ]
 
        return {
            "date": date,
            "count": len(results),
            "incidents": results,
        }
 
    # ========================================================
    # LIST BY DATE RANGE
    # ========================================================
 
    def list_incidents_by_date_range(
        self,
        start_date,
        end_date,
        limit=None,
    ):
 
        start = datetime.strptime(
            start_date,
            "%Y-%m-%d",
        ).date()
 
        end = datetime.strptime(
            end_date,
            "%Y-%m-%d",
        ).date()
 
        if start > end:
 
            raise ValueError(
                "start_date cannot be after end_date."
            )
 
        incidents = self.scan_all()
 
        results = []
 
        for incident in incidents:
 
            created_at = self.parse_created_at(
                incident.get("created_at")
            )
 
            if not created_at:
                continue
 
            created_date = created_at.date()
 
            if (
                start
                <= created_date
                <= end
            ):
 
                results.append(
                    self._compact_incident(
                        incident
                    )
                )
 
        results = results[
            : self._limit(limit)
        ]
 
        return {
            "start_date": start_date,
            "end_date": end_date,
            "count": len(results),
            "incidents": results,
        }
 
    # ========================================================
    # SEARCH
    # ========================================================
 
    def search_incidents(
        self,
        category=None,
        sub_category=None,
        urgency=None,
        priority=None,
        team_id=None,
        state=None,
        limit=None,
    ):
 
        incidents = self.scan_all()
 
        filters = {
            "category": category,
            "sub_category": sub_category,
            "urgency": urgency,
            "priority": priority,
            "team_id": team_id,
            "state": state,
        }
 
        results = []
 
        max_results = self._limit(
            limit
        )
 
        for incident in incidents:
 
            matched = True
 
            for field, expected in filters.items():
 
                if expected is None:
                    continue
 
                actual = incident.get(
                    field
                )
 
                if (
                    str(actual)
                    .strip()
                    .lower()
                    != str(expected)
                    .strip()
                    .lower()
                ):
 
                    matched = False
                    break
 
            if matched:
 
                results.append(
                    self._compact_incident(
                        incident
                    )
                )
 
                if (
                    len(results)
                    >= max_results
                ):
 
                    break
 
        return {
            "count": len(results),
            "incidents": results,
        }
 
    # ========================================================
    # SLA BREACHES
    # ========================================================
 
    def _to_bool(
        self,
        value,
    ):
 
        if isinstance(
            value,
            bool,
        ):
 
            return value
 
        if isinstance(
            value,
            (int, float),
        ):
 
            return value != 0
 
        return str(
            value
        ).strip().lower() in {
            "true",
            "yes",
            "1",
        }
 
    def list_sla_breaches(
        self,
        breach_type="all",
        limit=None,
    ):
 
        valid_types = {
            "all",
            "assignment",
            "resolution",
        }
 
        breach_type = (
            breach_type
            .lower()
            .strip()
        )
 
        if breach_type not in valid_types:
 
            raise ValueError(
                "breach_type must be "
                "'all', 'assignment', "
                "or 'resolution'."
            )
 
        incidents = self.scan_all()
 
        results = []
 
        max_results = self._limit(
            limit
        )
 
        for incident in incidents:
 
            assignment = self._to_bool(
                incident.get(
                    "has_assignment_sla_breach",
                    False,
                )
            )
 
            resolution = self._to_bool(
                incident.get(
                    "has_resolution_sla_breach",
                    False,
                )
            )
 
            if breach_type == "all":
 
                matched = (
                    assignment
                    or resolution
                )
 
            elif breach_type == "assignment":
 
                matched = assignment
 
            else:
 
                matched = resolution
 
            if matched:
 
                results.append(
                    self._compact_incident(
                        incident
                    )
                )
 
                if (
                    len(results)
                    >= max_results
                ):
 
                    break
 
        return {
            "breach_type": breach_type,
            "count": len(results),
            "incidents": results,
        }
 
    # ========================================================
    # UPDATE
    # ========================================================
 
    def update_incident(
        self,
        ticket_id,
        field,
        value,
    ):
 
        field = str(
            field
        ).strip()
 
        if field not in self.ALLOWED_UPDATE_FIELDS:
 
            raise ValueError(
                f"Field '{field}' "
                "cannot be updated."
            )
 
        current = self.get_incident(
            ticket_id
        )
 
        if not current.get(
            "found"
        ):
 
            return current
 
        old_value = current[
            "incident"
        ].get(
            field
        )
 
        try:
 
            response = self.table.update_item(
 
                Key={
                    "ticket_id": ticket_id
                },
 
                UpdateExpression=(
                    "SET #field = :value"
                ),
 
                ExpressionAttributeNames={
                    "#field": field
                },
 
                ExpressionAttributeValues={
                    ":value": value
                },
 
                ReturnValues="UPDATED_NEW",
            )
 
            updated_value = response.get(
                "Attributes",
                {},
            ).get(
                field
            )
 
            return {
                "success": True,
                "ticket_id": ticket_id,
                "field": field,
                "previous_value": old_value,
                "new_value": self._convert_decimal(
                    updated_value
                ),
                "knowledge_base_sync": (
                    "DynamoDB was updated. "
                    "Knowledge Base synchronization "
                    "is handled asynchronously by "
                    "the existing synchronization process."
                ),
            }
 
        except ClientError as exc:
 
            raise RuntimeError(
                f"Unable to update incident "
                f"{ticket_id}: {exc}"
            ) from exc
 
 