from datetime import datetime
from decimal import Decimal
 
from botocore.exceptions import ClientError
 
from config import (
    AWS_REGION,
    CATEGORY_TABLE_NAME,
    DEFAULT_RESULT_LIMIT,
    MAX_RESULT_LIMIT,
    get_boto3_session,
)
 
 
class CategorizationService:
 
    ALLOWED_UPDATE_FIELDS = {
        "category_name",
        "category_description",
        "sub_category_name",
        "sub_category_description",
        "issue",
        "issue_description",
        "issue_type",
        "impact",
        "urgency",
        "affected_service",
        "business_impact",
        "team_id",
        "standard_request_id",
        "active",
    }
 
    def __init__(self):
 
        session = get_boto3_session()
 
        dynamodb = session.resource(
            "dynamodb",
            region_name=AWS_REGION,
        )
 
        self.table = dynamodb.Table(
            CATEGORY_TABLE_NAME
        )
 
    # ========================================================
    # NORMALIZE ISSUE ID
    # ========================================================
 
    def normalize_issue_id(
        self,
        issue_id,
    ):
 
        value = str(
            issue_id
        ).strip()
 
        if value.lower().startswith(
            "issue-"
        ):
 
            number = value.split(
                "-",
                1,
            )[1]
 
            return f"Issue-{number}"
 
        if value.lower().startswith(
            "issue"
        ):
 
            number = value[
                len("issue"):
            ].strip(
                " -_"
            )
 
            return f"Issue-{number}"
 
        return f"Issue-{value}"
 
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
    # GET ISSUE
    # ========================================================
 
    def get_issue(
        self,
        issue_id,
    ):
 
        issue_id = self.normalize_issue_id(
            issue_id
        )
 
        try:
 
            response = self.table.get_item(
                Key={
                    "issue_id": issue_id
                }
            )
 
            item = response.get(
                "Item"
            )
 
            if not item:
 
                return {
                    "found": False,
                    "issue_id": issue_id,
                    "message": (
                        f"{issue_id} "
                        "was not found."
                    ),
                }
 
            return {
                "found": True,
                "issue": self._convert_decimal(
                    item
                ),
            }
 
        except ClientError as exc:
 
            raise RuntimeError(
                f"Unable to retrieve "
                f"{issue_id}: {exc}"
            ) from exc
 
    # ========================================================
    # GET ISSUE FIELD
    # ========================================================
 
    def get_issue_field(
        self,
        issue_id,
        field,
    ):
 
        result = self.get_issue(
            issue_id
        )
 
        if not result.get(
            "found"
        ):
 
            return result
 
        issue = result[
            "issue"
        ]
 
        return {
            "found": True,
            "issue_id": issue[
                "issue_id"
            ],
            "field": field,
            "value": issue.get(
                field
            ),
        }
 
    # ========================================================
    # GET ALL CATEGORIES
    # ========================================================
 
    def get_all_category_names(self):
 
        items = self.scan_all()
 
        categories = sorted(
            {
                str(
                    item.get(
                        "category_name"
                    )
                ).strip()
 
                for item in items
 
                if item.get(
                    "category_name"
                )
            }
        )
 
        return {
            "count": len(categories),
            "categories": categories,
        }
 
    # ========================================================
    # SCAN
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
    # FIND ISSUE BY NAME
    # ========================================================
 
    def find_issue_by_name(
        self,
        issue_name,
    ):
 
        search_text = str(
            issue_name
        ).strip().lower()
 
        items = self.scan_all()
 
        results = []
 
        for item in items:
 
            issue = str(
                item.get(
                    "issue",
                    "",
                )
            ).strip().lower()
 
            if search_text in issue:
 
                results.append(
                    self._convert_decimal(
                        item
                    )
                )
 
        results = results[
            :MAX_RESULT_LIMIT
        ]
 
        return {
            "count": len(results),
            "issues": results,
        }
 
    # ========================================================
    # SEARCH
    # ========================================================
 
    def search_issues(
        self,
        category_name=None,
        sub_category_name=None,
        issue_type=None,
        impact=None,
        urgency=None,
        affected_service=None,
        team_id=None,
        limit=None,
    ):
 
        items = self.scan_all()
 
        filters = {
            "category_name": category_name,
            "sub_category_name": sub_category_name,
            "issue_type": issue_type,
            "impact": impact,
            "urgency": urgency,
            "affected_service": affected_service,
            "team_id": team_id,
        }
 
        results = []
 
        max_results = self._limit(
            limit
        )
 
        for item in items:
 
            matched = True
 
            for field, expected in filters.items():
 
                if expected is None:
                    continue
 
                actual = item.get(
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
                    self._convert_decimal(
                        item
                    )
                )
 
                if (
                    len(results)
                    >= max_results
                ):
 
                    break
 
        return {
            "count": len(results),
            "issues": results,
        }
 
    # ========================================================
    # UPDATE FIELD
    # ========================================================
 
    def update_issue_field(
        self,
        issue_id,
        field,
        value,
    ):
 
        issue_id = self.normalize_issue_id(
            issue_id
        )
 
        if field not in self.ALLOWED_UPDATE_FIELDS:
 
            raise ValueError(
                f"Field '{field}' "
                "cannot be updated."
            )
 
        current = self.get_issue(
            issue_id
        )
 
        if not current.get(
            "found"
        ):
 
            return current
 
        previous_value = current[
            "issue"
        ].get(
            field
        )
 
        response = self.table.update_item(
 
            Key={
                "issue_id": issue_id
            },
 
            UpdateExpression=(
                "SET #field = :value, "
                "#updated = :updated"
            ),
 
            ExpressionAttributeNames={
                "#field": field,
                "#updated": "updated_on",
            },
 
            ExpressionAttributeValues={
                ":value": value,
                ":updated": datetime.utcnow().isoformat(),
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
            "issue_id": issue_id,
            "field": field,
            "previous_value": (
                previous_value
            ),
            "new_value": self._convert_decimal(
                updated_value
            ),
        }
 
    # ========================================================
    # UPDATE ISSUE
    # ========================================================
 
    def update_issue(
        self,
        issue_id,
        updates,
    ):
 
        issue_id = self.normalize_issue_id(
            issue_id
        )
 
        if not isinstance(
            updates,
            dict,
        ):
 
            raise ValueError(
                "updates must be a dictionary."
            )
 
        results = []
 
        for field, value in updates.items():
 
            result = self.update_issue_field(
                issue_id=issue_id,
                field=field,
                value=value,
            )
 
            results.append(
                result
            )
 
            if not result.get(
                "success",
                False,
            ):
 
                return result
 
        return {
            "success": True,
            "issue_id": issue_id,
            "updates": results,
        }
 
 