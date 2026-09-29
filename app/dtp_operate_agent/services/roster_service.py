from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional
 
from botocore.exceptions import ClientError
 
from config import (
    AWS_REGION,
    ROSTER_TABLE_NAME,
    DEFAULT_RESULT_LIMIT,
    MAX_RESULT_LIMIT,
    get_boto3_session,
)
 
 
class RosterService:
 
    # ========================================================
    # INITIALIZATION
    # ========================================================
 
    def __init__(self):
 
        session = get_boto3_session()
 
        dynamodb = session.resource(
            "dynamodb",
            region_name=AWS_REGION,
        )
 
        self.table = dynamodb.Table(
            ROSTER_TABLE_NAME
        )
 
    # ========================================================
    # LIMIT
    # ========================================================
 
    def _limit(
        self,
        limit: Optional[int],
    ) -> int:
 
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
    # NORMALIZE DATE
    # ========================================================
 
    def _normalize_date(
        self,
        value: str,
    ) -> str:
 
        value = str(value).strip()
 
        try:
 
            parsed = datetime.strptime(
                value,
                "%Y-%m-%d",
            )
 
            return parsed.strftime(
                "%Y-%m-%d"
            )
 
        except ValueError as exc:
 
            raise ValueError(
                "Date must be in YYYY-MM-DD format."
            ) from exc
 
    # ========================================================
    # SCAN ALL
    # ========================================================
 
    def scan_all(self) -> List[Dict[str, Any]]:
 
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
    # FIND EMPLOYEE
    # ========================================================
 
    def find_employee(
        self,
        search_term: str,
    ) -> Dict[str, Any]:
 
        search_term = str(
            search_term
        ).strip().lower()
 
        if not search_term:
 
            raise ValueError(
                "search_term cannot be empty."
            )
 
        items = self.scan_all()
 
        matches = []
 
        for item in items:
 
            searchable_values = [
                item.get("UID"),
                item.get("uid"),
                item.get("emp_id"),
                item.get("employee_name"),
                item.get("employee_email"),
                item.get("team_id"),
                item.get("team_name"),
            ]
 
            searchable_text = " ".join(
                str(value)
                for value in searchable_values
                if value is not None
            ).lower()
 
            if search_term in searchable_text:
 
                matches.append(item)
 
        matches = matches[
            : self._limit(None)
        ]
 
        return {
            "search_term": search_term,
            "count": len(matches),
            "employees": matches,
        }
 
    # ========================================================
    # SEARCH EMPLOYEES
    # ========================================================
 
    def search_employees(
        self,
        search_term: str,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
 
        search_term = str(
            search_term
        ).strip().lower()
 
        if not search_term:
 
            raise ValueError(
                "search_term cannot be empty."
            )
 
        max_results = self._limit(
            limit
        )
 
        items = self.scan_all()
 
        results = []
 
        for item in items:
 
            searchable_values = [
                item.get("UID"),
                item.get("uid"),
                item.get("emp_id"),
                item.get("employee_name"),
                item.get("employee_email"),
                item.get("team_id"),
                item.get("team_name"),
            ]
 
            searchable_text = " ".join(
                str(value)
                for value in searchable_values
                if value is not None
            ).lower()
 
            if search_term in searchable_text:
 
                results.append(item)
 
                if len(results) >= max_results:
                    break
 
        return {
            "search_term": search_term,
            "count": len(results),
            "employees": results,
        }
 
    # ========================================================
    # GET TEAM ROSTER
    # ========================================================
 
    def get_team_roster(
        self,
        team_id: str,
    ) -> Dict[str, Any]:
 
        team_id = str(
            team_id
        ).strip()
 
        if not team_id:
 
            raise ValueError(
                "team_id cannot be empty."
            )
 
        items = self.scan_all()
 
        employees = []
 
        for item in items:
 
            item_team_id = str(
                item.get(
                    "team_id",
                    "",
                )
            ).strip()
 
            if (
                item_team_id.lower()
                == team_id.lower()
            ):
 
                employees.append(item)
 
        return {
            "team_id": team_id,
            "count": len(employees),
            "employees": employees,
        }
 
    # ========================================================
    # FIND TEAM EMPLOYEE
    # ========================================================
 
    def _find_team_employee(
        self,
        team_name: str,
    ) -> List[Dict[str, Any]]:
 
        team_name = str(
            team_name
        ).strip().lower()
 
        items = self.scan_all()
 
        results = []
 
        for item in items:
 
            current_team_name = str(
                item.get(
                    "team_name",
                    "",
                )
            ).strip().lower()
 
            current_team_id = str(
                item.get(
                    "team_id",
                    "",
                )
            ).strip().lower()
 
            if (
                team_name == current_team_name
                or team_name == current_team_id
            ):
 
                results.append(item)
 
        return results
 
    # ========================================================
    # GET TEAM DAY AVAILABILITY
    # ========================================================
 
    def get_team_day_availability(
        self,
        team_name: str,
        date: str,
    ) -> Dict[str, Any]:
 
        date = self._normalize_date(
            date
        )
 
        employees = self._find_team_employee(
            team_name
        )
 
        results = []
 
        for employee in employees:
 
            availability = (
                self._get_employee_day_from_item(
                    employee,
                    date,
                )
            )
 
            results.append(
                {
                    "UID": employee.get("UID"),
                    "emp_id": employee.get("emp_id"),
                    "employee_name": employee.get(
                        "employee_name"
                    ),
                    "employee_email": employee.get(
                        "employee_email"
                    ),
                    "team_id": employee.get(
                        "team_id"
                    ),
                    "team_name": employee.get(
                        "team_name"
                    ),
                    "date": date,
                    "availability": availability,
                }
            )
 
        return {
            "team_name": team_name,
            "date": date,
            "count": len(results),
            "employees": results,
        }
 
    # ========================================================
    # INTERNAL DAY AVAILABILITY
    # ========================================================
 
    def _get_employee_day_from_item(
        self,
        item: Dict[str, Any],
        target_date: str,
    ) -> Any:
 
        availability = item.get(
            "days_availability"
        )
 
        if availability is None:
            return None
 
        if isinstance(
            availability,
            dict,
        ):
 
            return availability.get(
                target_date
            )
 
        return None
 
    # ========================================================
    # GET EMPLOYEE
    # ========================================================
 
    def _get_employee_by_uid(
        self,
        uid: str,
    ) -> Optional[Dict[str, Any]]:
 
        uid = str(
            uid
        ).strip()
 
        if not uid:
            return None
 
        try:
 
            response = self.table.get_item(
                Key={
                    "UID": uid
                }
            )
 
            return response.get(
                "Item"
            )
 
        except ClientError as exc:
 
            raise RuntimeError(
                f"Unable to retrieve employee "
                f"'{uid}': {exc}"
            ) from exc
 
    # ========================================================
    # GET EMPLOYEE DAY AVAILABILITY
    # ========================================================
 
    def get_employee_day_availability(
        self,
        uid: str,
        date: str,
    ) -> Dict[str, Any]:
 
        date = self._normalize_date(
            date
        )
 
        employee = self._get_employee_by_uid(
            uid
        )
 
        if not employee:
 
            return {
                "found": False,
                "UID": uid,
                "date": date,
                "message": (
                    f"Employee '{uid}' "
                    "was not found."
                ),
            }
 
        availability = (
            self._get_employee_day_from_item(
                employee,
                date,
            )
        )
 
        return {
            "found": True,
            "UID": uid,
            "emp_id": employee.get(
                "emp_id"
            ),
            "employee_name": employee.get(
                "employee_name"
            ),
            "employee_email": employee.get(
                "employee_email"
            ),
            "team_id": employee.get(
                "team_id"
            ),
            "team_name": employee.get(
                "team_name"
            ),
            "date": date,
            "availability": availability,
        }
 
    # ========================================================
    # GET EMPLOYEE MONTH AVAILABILITY
    # ========================================================
 
    def get_employee_month_availability(
        self,
        uid: str,
        year: int,
        month: int,
    ) -> Dict[str, Any]:
 
        year = int(year)
        month = int(month)
 
        if month < 1 or month > 12:
 
            raise ValueError(
                "month must be between 1 and 12."
            )
 
        employee = self._get_employee_by_uid(
            uid
        )
 
        if not employee:
 
            return {
                "found": False,
                "UID": uid,
                "year": year,
                "month": month,
                "message": (
                    f"Employee '{uid}' "
                    "was not found."
                ),
            }
 
        availability = employee.get(
            "days_availability",
            {},
        )
 
        month_results = {}
 
        if isinstance(
            availability,
            dict,
        ):
 
            prefix = f"{year:04d}-{month:02d}-"
 
            for day, value in availability.items():
 
                day_string = str(day)
 
                if day_string.startswith(
                    prefix
                ):
 
                    month_results[
                        day_string
                    ] = value
 
        return {
            "found": True,
            "UID": uid,
            "emp_id": employee.get(
                "emp_id"
            ),
            "employee_name": employee.get(
                "employee_name"
            ),
            "year": year,
            "month": month,
            "count": len(month_results),
            "availability": month_results,
        }
 
    # ========================================================
    # GET EMPLOYEE WEEK AVAILABILITY
    # ========================================================
 
    def get_employee_week_availability(
        self,
        uid: str,
        year: int,
        month: int,
        start_day: int,
    ) -> Dict[str, Any]:
 
        year = int(year)
        month = int(month)
        start_day = int(start_day)
 
        if month < 1 or month > 12:
 
            raise ValueError(
                "month must be between 1 and 12."
            )
 
        if start_day < 1 or start_day > 31:
 
            raise ValueError(
                "start_day must be between 1 and 31."
            )
 
        employee = self._get_employee_by_uid(
            uid
        )
 
        if not employee:
 
            return {
                "found": False,
                "UID": uid,
                "year": year,
                "month": month,
                "start_day": start_day,
                "message": (
                    f"Employee '{uid}' "
                    "was not found."
                ),
            }
 
        availability = employee.get(
            "days_availability",
            {},
        )
 
        results = {}
 
        if isinstance(
            availability,
            dict,
        ):
 
            for day_offset in range(7):
 
                try:
 
                    current_date = date(
                        year,
                        month,
                        start_day,
                    )
 
                except ValueError as exc:
 
                    raise ValueError(
                        f"Invalid date: "
                        f"{year:04d}-{month:02d}-{start_day:02d}"
                    ) from exc
 
                current_date = current_date + timedelta(
                    days=day_offset
                )
 
                date_string = (
                    current_date.strftime(
                        "%Y-%m-%d"
                    )
                )
 
                results[
                    date_string
                ] = availability.get(
                    date_string
                )
 
        return {
            "found": True,
            "UID": uid,
            "emp_id": employee.get(
                "emp_id"
            ),
            "employee_name": employee.get(
                "employee_name"
            ),
            "year": year,
            "month": month,
            "start_day": start_day,
            "count": len(results),
            "availability": results,
        }
 
    # ========================================================
    # UPDATE EMPLOYEE DAY
    # ========================================================
 
    def update_employee_day(
        self,
        uid: str,
        date: str,
        availability: dict,
    ) -> Dict[str, Any]:
 
        date = self._normalize_date(
            date
        )
 
        if not isinstance(
            availability,
            dict,
        ):
 
            raise ValueError(
                "availability must be a dictionary."
            )
 
        employee = self._get_employee_by_uid(
            uid
        )
 
        if not employee:
 
            return {
                "success": False,
                "found": False,
                "UID": uid,
                "date": date,
                "message": (
                    f"Employee '{uid}' "
                    "was not found."
                ),
            }
 
        current_days = employee.get(
            "days_availability",
            {},
        )
 
        if not isinstance(
            current_days,
            dict,
        ):
 
            current_days = {}
 
        current_days = dict(
            current_days
        )
 
        previous_value = current_days.get(
            date
        )
 
        current_days[
            date
        ] = availability
 
        try:
 
            response = self.table.update_item(
 
                Key={
                    "UID": uid
                },
 
                UpdateExpression=(
                    "SET #days = :days"
                ),
 
                ExpressionAttributeNames={
                    "#days": "days_availability"
                },
 
                ExpressionAttributeValues={
                    ":days": current_days
                },
 
                ReturnValues="UPDATED_NEW",
            )
 
            updated_days = response.get(
                "Attributes",
                {},
            ).get(
                "days_availability",
                current_days,
            )
 
            return {
                "success": True,
                "UID": uid,
                "date": date,
                "previous_value": previous_value,
                "new_value": availability,
                "days_availability": updated_days,
            }
 
        except ClientError as exc:
 
            raise RuntimeError(
                f"Unable to update availability "
                f"for employee '{uid}': {exc}"
            ) from exc
 
    # ========================================================
    # UPDATE EMPLOYEE DAYS
    # ========================================================
 
    def update_employee_days(
        self,
        uid: str,
        updates: dict,
    ) -> Dict[str, Any]:
 
        if not isinstance(
            updates,
            dict,
        ):
 
            raise ValueError(
                "updates must be a dictionary."
            )
 
        results = []
 
        for target_date, availability in updates.items():
 
            result = self.update_employee_day(
                uid=uid,
                date=target_date,
                availability=availability,
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
            "UID": uid,
            "count": len(results),
            "updates": results,
        }
 
    # ========================================================
    # UPDATE TEAM DAY
    # ========================================================
 
    def update_team_day(
        self,
        team_name: str,
        date: str,
        availability: dict,
    ) -> Dict[str, Any]:
 
        date = self._normalize_date(
            date
        )
 
        if not isinstance(
            availability,
            dict,
        ):
 
            raise ValueError(
                "availability must be a dictionary."
            )
 
        employees = self._find_team_employee(
            team_name
        )
 
        if not employees:
 
            return {
                "success": False,
                "team_name": team_name,
                "date": date,
                "message": (
                    f"No employees found "
                    f"for team '{team_name}'."
                ),
            }
 
        results = []
 
        for employee in employees:
 
            uid = employee.get(
                "UID"
            )
 
            if not uid:
                continue
 
            result = self.update_employee_day(
                uid=uid,
                date=date,
                availability=availability,
            )
 
            results.append(
                result
            )
 
        return {
            "success": True,
            "team_name": team_name,
            "date": date,
            "count": len(results),
            "updates": results,
        }
 
 