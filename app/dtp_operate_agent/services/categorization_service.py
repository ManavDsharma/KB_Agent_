"""
Category Matrix (issue / categorization / routing) access.

The matrix is small master data, so it is read whole and cached for a
short time; searches and lookups then run in memory.
"""

from typing import Any, Dict, List, Optional

from config import (
    CATEGORY_TABLE_NAME,
    DIRECTORY_CACHE_TTL_SECONDS,
    MAX_TEXT_CHARS,
    get_dynamodb_resource,
)

from services.common import (
    ServiceError,
    TTLCache,
    audit,
    build_update,
    ensure_writable,
    guarded,
    limit_value,
    norm,
    project,
    local_date,
    resolve_choice,
    scalar,
    scan_items,
    to_plain,
    yes_no,
)

from services.ids import normalize_issue_id

from services.schema import (
    BOOLEAN_FIELDS,
    ISSUE_ENUM_FIELDS,
    ISSUE_LIST_FIELDS,
    ISSUE_UPDATABLE_FIELDS,
    ISSUE_VIEW_FIELDS,
    resolve_field_names,
)


def _is_active(value: Any) -> bool:
    if value is None:
        return True

    return str(value).strip().lower() not in {"false", "no", "n", "0", "inactive"}


class CategorizationService:

    def __init__(self, table=None):

        self._table = table

        self._cache = TTLCache(DIRECTORY_CACHE_TTL_SECONDS)

    # --------------------------------------------------------
    # Access
    # --------------------------------------------------------

    @property
    def table(self):

        if self._table is None:

            if not CATEGORY_TABLE_NAME:
                raise ServiceError(
                    "unavailable",
                    "The category matrix is not configured.",
                )

            self._table = get_dynamodb_resource().Table(CATEGORY_TABLE_NAME)

        return self._table

    def _all(self) -> List[Dict[str, Any]]:
        return self._cache.get_or_load(
            "all",
            lambda: [to_plain(item) for item in scan_items(self.table)],
        )

    def _fetch(self, issue_id: str) -> Optional[Dict[str, Any]]:

        item = self.table.get_item(Key={"issue_id": issue_id}).get("Item")

        return to_plain(item) if item else None

    def _not_found(self, issue_id: str) -> ServiceError:
        return ServiceError(
            "not_found",
            f"{issue_id} was not found.",
            issue_id=issue_id,
        )

    # --------------------------------------------------------
    # Reference data (also used by the incident service)
    # --------------------------------------------------------

    def _values(self, field: str) -> List[str]:
        return sorted(
            {
                str(item[field]).strip()
                for item in self._all()
                if item.get(field)
            }
        )

    def category_names(self) -> List[str]:
        return self._safe_or_empty("category_name")

    def sub_category_names(self) -> List[str]:
        return self._safe_or_empty("sub_category_name")

    def team_ids(self) -> List[str]:
        return self._safe_or_empty("team_id")

    def _safe_or_empty(self, field: str) -> List[str]:
        """Reference values, or [] when the matrix cannot be read."""

        try:
            return self._values(field)

        except Exception:
            return []

    def team_for(
        self,
        category: Optional[str],
        sub_category: Optional[str],
    ) -> Optional[str]:
        """Owning team when every matching matrix row agrees on one."""

        try:
            rows = self._all()

        except Exception:
            return None

        teams = set()

        for row in rows:

            if category and norm(row.get("category_name", "")) != norm(category):
                continue

            if sub_category and norm(row.get("sub_category_name", "")) != norm(sub_category):
                continue

            if row.get("team_id"):
                teams.add(row["team_id"])

        return teams.pop() if len(teams) == 1 else None

    # --------------------------------------------------------
    # Reads
    # --------------------------------------------------------

    @guarded
    def get_issue(
        self,
        issue_id: str,
        fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:

        key = normalize_issue_id(issue_id)

        item = self._fetch(key)

        if not item:
            raise self._not_found(key)

        selected = resolve_field_names("issue", fields) if fields else []

        if fields and not selected:
            raise ServiceError(
                "invalid_value",
                "Unknown field(s): " + ", ".join(map(str, fields)),
            )

        if selected:
            data = project(item, ["issue_id", *selected], keep_empty=True, text_limit=MAX_TEXT_CHARS)

        else:
            data = project(item, ISSUE_VIEW_FIELDS, text_limit=MAX_TEXT_CHARS)

        return {"ok": True, "issue_id": item["issue_id"], "issue": data}

    @guarded
    def list_categories(self) -> Dict[str, Any]:

        tree: Dict[str, set] = {}

        for item in self._all():

            name = item.get("category_name")

            if not name:
                continue

            tree.setdefault(str(name).strip(), set())

            if item.get("sub_category_name"):
                tree[str(name).strip()].add(str(item["sub_category_name"]).strip())

        return {
            "ok": True,
            "count": len(tree),
            "categories": [
                {"name": name, "sub_categories": sorted(subs)}
                for name, subs in sorted(tree.items())
            ],
        }

    @guarded
    def search_issues(
        self,
        text: Optional[str] = None,
        category_name: Optional[str] = None,
        sub_category_name: Optional[str] = None,
        issue_type: Optional[str] = None,
        impact: Optional[str] = None,
        urgency: Optional[str] = None,
        affected_service: Optional[str] = None,
        team_id: Optional[str] = None,
        limit: Optional[int] = None,
        include_inactive: bool = False,
    ) -> Dict[str, Any]:

        filters = {
            "category_name": category_name,
            "sub_category_name": sub_category_name,
            "issue_type": issue_type,
            "impact": impact,
            "urgency": urgency,
            "affected_service": affected_service,
            "team_id": team_id,
        }

        tokens = norm(text).split() if text else []

        matches = []

        for item in self._all():

            if not include_inactive and not _is_active(item.get("active")):
                continue

            if any(
                value and not self._field_matches(item.get(field), value)
                for field, value in filters.items()
            ):
                continue

            if tokens:

                haystack = norm(
                    " ".join(
                        str(item.get(field, ""))
                        for field in (
                            "issue",
                            "issue_description",
                            "category_name",
                            "sub_category_name",
                            "affected_service",
                        )
                    )
                )

                if not all(token in haystack.split() or token in haystack for token in tokens):
                    continue

            matches.append(item)

        cap = limit_value(limit)

        return {
            "ok": True,
            "count": min(len(matches), cap),
            "total": len(matches),
            "issues": [
                project(item, ISSUE_LIST_FIELDS, text_limit=120)
                for item in matches[:cap]
            ],
        }

    @staticmethod
    def _field_matches(actual: Any, wanted: str) -> bool:

        actual_norm = norm(actual or "")

        wanted_norm = norm(wanted)

        return bool(wanted_norm) and (
            actual_norm == wanted_norm or wanted_norm in actual_norm
        )

    # --------------------------------------------------------
    # Update
    # --------------------------------------------------------

    @guarded
    def update_issue(
        self,
        issue_id: str,
        changes: Dict[str, Any],
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:

        ensure_writable()

        if not isinstance(changes, dict) or not changes:
            raise ServiceError("missing_fields", "Say which field to change and the new value.")

        key = normalize_issue_id(issue_id)

        current = self._fetch(key)

        if not current:
            raise self._not_found(key)

        clean: Dict[str, Any] = {}

        for raw_field, raw_value in changes.items():

            field = self._updatable_field(raw_field)

            value = scalar(raw_value, field)

            if field in BOOLEAN_FIELDS:
                value = yes_no(value, current.get(field), field)

            elif field in ISSUE_ENUM_FIELDS:
                value = resolve_choice(value, self._values(field), field)

            elif value in (None, ""):
                raise ServiceError("invalid_value", f"{field} cannot be empty.")

            clean[field] = value

        stored = dict(clean)

        stored["updated_on"] = local_date()

        if actor:
            stored["updated_by"] = actor

        expression, names, values = build_update(stored)

        names["#pk"] = "issue_id"

        self.table.update_item(
            Key={"issue_id": current["issue_id"]},
            UpdateExpression=expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(#pk)",
        )

        self._cache.clear()

        audit(
            "update_issue",
            actor,
            issue_id=current["issue_id"],
            changes={field: [current.get(field), value] for field, value in clean.items()},
        )

        return {
            "ok": True,
            "issue_id": current["issue_id"],
            "changes": {
                field: {"from": current.get(field), "to": value}
                for field, value in clean.items()
            },
        }

    @staticmethod
    def _updatable_field(raw_field: Any) -> str:

        names = resolve_field_names("issue", [raw_field])

        if not names or names[0] not in ISSUE_UPDATABLE_FIELDS:
            raise ServiceError(
                "invalid_field",
                f"'{raw_field}' cannot be updated. Updatable fields: "
                + ", ".join(sorted(ISSUE_UPDATABLE_FIELDS)),
            )

        return names[0]
