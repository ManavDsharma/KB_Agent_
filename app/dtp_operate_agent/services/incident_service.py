"""
Incident / request access (DynamoDB).

Design notes
    * Reads return only the fields asked for. A default "view" holds
      the short fields; long text (description, working notes) is only
      returned when requested and is truncated.
    * Filtering runs server-side where DynamoDB can do it.
    * Values written for priority / state / category / people are
      mapped onto the values already used in the data, so "P2" becomes
      "P2-High" instead of creating a new spelling.
    * Nothing here raises AWS exceptions; see services.common.guarded.
"""

from typing import Any, Dict, List, Optional

from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from config import (
    DEFAULT_INCIDENT_STATE,
    DEFAULT_SOURCE_SYSTEM,
    ENABLE_DELETE_OPERATIONS,
    FACET_CACHE_TTL_SECONDS,
    INCIDENT_ID_PREFIX,
    INCIDENT_ID_WIDTH,
    INCIDENT_TABLE_NAME,
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
    local_timestamp,
    norm,
    project,
    resolve_choice,
    resolve_date,
    same_type,
    scalar,
    scan_items,
    to_plain,
)

from services.ids import find_ticket_ref, ticket_candidates

from services.schema import (
    INCIDENT_LIST_FIELDS,
    INCIDENT_NUMERIC_FIELDS,
    INCIDENT_PERSON_FIELDS,
    INCIDENT_READ_ONLY_FIELDS,
    INCIDENT_TEXT_FIELDS,
    INCIDENT_VIEW_FIELDS,
    resolve_field_names,
)


# Fields the agent may set when creating an incident.
CREATE_FIELDS = [
    "short_description",
    "description",
    "category",
    "sub_category",
    "urgency",
    "priority",
    "team_id",
    "assigned_to",
    "opened_by",
    "opened_for",
]

# Nothing else is asked from the user. Team defaults from the
# category matrix; state, timestamps and source are set here.
REQUIRED_CREATE_FIELDS = [
    "short_description",
    "category",
    "urgency",
    "priority",
]

# Fields whose values must match values already in use.
ENUM_FIELDS = {
    "priority",
    "urgency",
    "state",
    "category",
    "sub_category",
    "team_id",
}

_FACET_FIELDS = [
    "priority",
    "urgency",
    "state",
    "category",
    "sub_category",
    "team_id",
    "assigned_to",
    "opened_by",
    "opened_for",
]

_TRUTHY = [True, "true", "True", "TRUE", "yes", "Yes", "YES", "Y", "y", "1", 1]

_BREACH_FIELDS = {
    "assignment": "has_assignment_sla_breach",
    "resolution": "has_resolution_sla_breach",
}

SEARCH_SCAN_CAP = 300


def _truthy(value: Any) -> bool:

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value != 0

    return str(value).strip().lower() in {"true", "yes", "y", "1"}


class IncidentService:

    def __init__(self, table=None, catalog=None, people=None):
        """
        table    injected table (tests); default is the configured one
        catalog  CategorizationService, for category / team reference data
        people   RosterService, for resolving names to assignees
        """

        self._table = table

        self.catalog = catalog

        self.people = people

        self._facet_cache = TTLCache(FACET_CACHE_TTL_SECONDS)

    # --------------------------------------------------------
    # Access
    # --------------------------------------------------------

    @property
    def table(self):

        if self._table is None:

            if not INCIDENT_TABLE_NAME:
                raise ServiceError(
                    "unavailable",
                    "The incident table is not configured.",
                )

            self._table = get_dynamodb_resource().Table(INCIDENT_TABLE_NAME)

        return self._table

    def _fetch(self, raw_id: Any) -> Optional[Dict[str, Any]]:
        """Read one incident, trying the common spellings of its key."""

        text = str(raw_id).strip()

        if not text:
            raise ServiceError("missing_fields", "A ticket ID is required.")

        ref = find_ticket_ref(text)

        keys = ticket_candidates(ref[0], ref[1]) if ref else [text]

        for key in keys:

            item = self.table.get_item(Key={"ticket_id": key}).get("Item")

            if item:
                return to_plain(item)

        return None

    def _not_found(self, raw_id: Any) -> ServiceError:
        return ServiceError(
            "not_found",
            f"{str(raw_id).strip()} was not found.",
            ticket_id=str(raw_id).strip(),
        )

    # --------------------------------------------------------
    # Reference values
    # --------------------------------------------------------

    def _facets(self) -> Dict[str, List[str]]:
        """Distinct values in use per field (cached)."""

        def load():

            found = {field: set() for field in _FACET_FIELDS}

            for item in scan_items(self.table, fields=_FACET_FIELDS):

                for field in _FACET_FIELDS:

                    value = item.get(field)

                    if value not in (None, ""):
                        found[field].add(str(value).strip())

            return {field: sorted(values) for field, values in found.items()}

        return self._facet_cache.get_or_load("facets", load)

    def _allowed(self, field: str) -> List[str]:
        """Values in use plus the master data for the field."""

        values = set(self._facets().get(field, []))

        if self.catalog is not None:

            if field == "category":
                values |= set(self.catalog.category_names())

            elif field == "sub_category":
                values |= set(self.catalog.sub_category_names())

            elif field == "team_id":
                values |= set(self.catalog.team_ids())

        return sorted(values)

    def _observed_people(self) -> List[str]:

        facets = self._facets()

        return sorted(
            set(facets.get("assigned_to", []))
            | set(facets.get("opened_by", []))
            | set(facets.get("opened_for", []))
        )

    def _resolve_person(self, value: Any, actor: Optional[str]) -> str:
        """Person name / email / "me" -> the identifier used in the data."""

        text = str(value).strip()

        if norm(text) in {"me", "myself", "self"}:

            if not actor:
                raise ServiceError(
                    "invalid_value",
                    "I don't know who 'me' is. Please give a name or email.",
                )

            text = actor

        if "@" in text:
            return text

        tokens = norm(text).split()

        if not tokens:
            raise ServiceError("invalid_value", "A person name or email is required.")

        matches = []

        if self.people is not None:

            try:
                matches = self.people.match_people(text)

            except Exception:
                matches = []

        observed = self._observed_people()

        def observed_for(words: List[str]) -> List[str]:
            return [item for item in observed if all(word in norm(item) for word in words)]

        if len(matches) > 1:

            names = [person.get("employee_name") or person.get("ref") for person in matches]

            raise ServiceError(
                "ambiguous_value",
                f"Several people match '{text}': {', '.join(map(str, names[:6]))}. Which one?",
                options=names[:6],
            )

        if len(matches) == 1:

            person = matches[0]

            # The roster keeps the person's e-mail in employee_id, which is
            # what incidents store in assigned_to / opened_by.
            if "@" in str(person.get("employee_id", "")):
                return str(person["employee_id"])

            same = observed_for(norm(person.get("employee_name", "")).split())

            if len(same) == 1:
                return same[0]

            return str(person.get("employee_name") or text)

        same = observed_for(tokens)

        if len(same) == 1:
            return same[0]

        if len(same) > 1:
            raise ServiceError(
                "ambiguous_value",
                f"Several people match '{text}': {', '.join(same[:6])}. Which one?",
                options=same[:6],
            )

        raise ServiceError(
            "invalid_value",
            f"I couldn't find anyone matching '{text}'. Please give the email or full name.",
        )

    # --------------------------------------------------------
    # Reads
    # --------------------------------------------------------

    @guarded
    def get_incident(
        self,
        ticket_id: str,
        fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:

        item = self._fetch(ticket_id)

        if not item:
            raise self._not_found(ticket_id)

        selected = resolve_field_names("incident", fields) if fields else []

        if fields and not selected:
            raise ServiceError(
                "invalid_value",
                "Unknown field(s): " + ", ".join(map(str, fields)),
            )

        if selected:

            data = project(
                item,
                ["ticket_id", *selected],
                keep_empty=True,
                text_limit=MAX_TEXT_CHARS,
            )

        else:

            data = project(
                item,
                [*INCIDENT_VIEW_FIELDS, "description"],
                text_limit=300,
            )

        return {"ok": True, "ticket_id": item["ticket_id"], "incident": data}

    def _query(
        self,
        condition=None,
        list_fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
        python_filter=None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Filtered scan -> newest-first compact rows."""

        items = scan_items(
            self.table,
            filter_expression=condition,
            max_items=None if python_filter else SEARCH_SCAN_CAP,
        )

        items = [to_plain(item) for item in items]

        if python_filter:
            items = [item for item in items if python_filter(item)]

        items.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)

        cap = limit_value(limit)

        result = {
            "ok": True,
            "count": min(len(items), cap),
            "total": len(items),
            "incidents": [
                project(item, list_fields or INCIDENT_LIST_FIELDS, text_limit=80)
                for item in items[:cap]
            ],
        }

        if extra:
            result.update(extra)

        return result

    @guarded
    def list_incidents_by_date(
        self,
        date: str,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:

        start = resolve_date(date)

        if end_date:

            end = resolve_date(end_date)

            if start > end:
                raise ServiceError(
                    "invalid_value",
                    "The start date cannot be after the end date.",
                )

            condition = Attr("created_at").between(start, end + "~")

            label = {"start_date": start, "end_date": end}

        else:

            condition = Attr("created_at").begins_with(start)

            label = {"date": start}

        return self._query(condition, limit=limit, extra=label)

    @guarded
    def list_sla_breaches(
        self,
        breach_type: str = "all",
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:

        kind = str(breach_type or "all").strip().lower()

        if kind not in {"all", "assignment", "resolution"}:
            raise ServiceError(
                "invalid_value",
                "breach_type must be 'all', 'assignment' or 'resolution'.",
            )

        wanted = (
            list(_BREACH_FIELDS.values())
            if kind == "all"
            else [_BREACH_FIELDS[kind]]
        )

        condition = None

        for field in wanted:

            clause = Attr(field).is_in(_TRUTHY)

            condition = clause if condition is None else condition | clause

        return self._query(
            condition,
            list_fields=[*INCIDENT_LIST_FIELDS, *wanted],
            limit=limit,
            python_filter=lambda item: any(_truthy(item.get(field)) for field in wanted),
            extra={"breach_type": kind},
        )

    @guarded
    def search_incidents(
        self,
        category: Optional[str] = None,
        sub_category: Optional[str] = None,
        urgency: Optional[str] = None,
        priority: Optional[str] = None,
        state: Optional[str] = None,
        team_id: Optional[str] = None,
        assigned_to: Optional[str] = None,
        text: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:

        exact = {
            "category": category,
            "sub_category": sub_category,
            "urgency": urgency,
            "priority": priority,
            "state": state,
            "team_id": team_id,
        }

        condition = None

        for field, value in exact.items():

            if not value:
                continue

            try:
                canonical = resolve_choice(value, self._allowed(field), field)

            except ServiceError as exc:

                return {
                    "ok": True,
                    "count": 0,
                    "total": 0,
                    "incidents": [],
                    "note": exc.message,
                }

            clause = Attr(field).eq(canonical)

            condition = clause if condition is None else condition & clause

        person_tokens = norm(assigned_to).split() if assigned_to else []

        text_tokens = norm(text).split() if text else []

        python_filter = None

        if person_tokens or text_tokens:

            def python_filter(item):

                if person_tokens:

                    person = norm(item.get("assigned_to", ""))

                    if not all(token in person for token in person_tokens):
                        return False

                if text_tokens:

                    haystack = norm(
                        f"{item.get('short_description', '')} {item.get('description', '')}"
                    )

                    if not all(token in haystack for token in text_tokens):
                        return False

                return True

        return self._query(condition, limit=limit, python_filter=python_filter)

    # --------------------------------------------------------
    # Create
    # --------------------------------------------------------

    def _next_ticket_number(self) -> int:

        prefix = INCIDENT_ID_PREFIX.upper()

        highest = 0

        for item in scan_items(self.table, fields=["ticket_id"]):

            ref = find_ticket_ref(str(item.get("ticket_id", "")))

            if ref and ref[0] == prefix:
                highest = max(highest, int(ref[1]))

        return highest + 1

    @guarded
    def create_incident(
        self,
        fields: Dict[str, Any],
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:

        ensure_writable()

        supplied = {
            key: scalar(fields.get(key), key)
            for key in CREATE_FIELDS
            if fields.get(key) not in (None, "")
        }

        missing = [
            field for field in REQUIRED_CREATE_FIELDS
            if not supplied.get(field)
        ]

        if missing:
            raise ServiceError(
                "missing_fields",
                "Missing required information: " + ", ".join(missing) + ".",
                missing=missing,
            )

        record: Dict[str, Any] = dict(supplied)

        for field in ENUM_FIELDS & set(record):
            record[field] = resolve_choice(record[field], self._allowed(field), field)

        if not record.get("team_id") and self.catalog is not None:

            team = self.catalog.team_for(
                record.get("category"),
                record.get("sub_category"),
            )

            if team:
                record["team_id"] = team

        for field in INCIDENT_PERSON_FIELDS & set(record):
            record[field] = self._resolve_person(record[field], actor)

        if not record.get("opened_by") and actor:
            record["opened_by"] = actor

        now = local_timestamp()

        record.setdefault("description", record["short_description"])

        try:

            record["state"] = resolve_choice(
                DEFAULT_INCIDENT_STATE,
                self._allowed("state"),
                "state",
            )

        except ServiceError:
            record["state"] = DEFAULT_INCIDENT_STATE

        record["created_at"] = now

        record["updated_at"] = now

        record["source_system"] = DEFAULT_SOURCE_SYSTEM

        prefix = INCIDENT_ID_PREFIX.upper()

        number = self._next_ticket_number()

        for _ in range(5):

            ticket_id = f"{prefix}{str(number).zfill(INCIDENT_ID_WIDTH)}"

            try:

                self.table.put_item(
                    Item={"ticket_id": ticket_id, **record},
                    ConditionExpression="attribute_not_exists(#pk)",
                    ExpressionAttributeNames={"#pk": "ticket_id"},
                )

                break

            except ClientError as exc:

                if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                    raise

                number += 1

        else:
            raise ServiceError(
                "conflict",
                "Could not reserve a ticket number. Please try again.",
            )

        audit("create_incident", actor, ticket_id=ticket_id, record=record)

        return {
            "ok": True,
            "ticket_id": ticket_id,
            "incident": project(
                {"ticket_id": ticket_id, **record},
                INCIDENT_VIEW_FIELDS,
                text_limit=120,
            ),
        }

    # --------------------------------------------------------
    # Update
    # --------------------------------------------------------

    @staticmethod
    def _updatable_field(raw_field: Any) -> str:

        names = resolve_field_names("incident", [raw_field])

        if len(names) != 1:
            raise ServiceError(
                "invalid_field",
                f"'{raw_field}' is not an incident field I can update.",
            )

        field = names[0]

        if field in INCIDENT_READ_ONLY_FIELDS:
            raise ServiceError(
                "invalid_field",
                f"{field} is maintained automatically and cannot be changed.",
            )

        return field

    def _clean_value(
        self,
        field: str,
        value: Any,
        current: Dict[str, Any],
        actor: Optional[str],
    ) -> Any:

        value = scalar(value, field)

        if field in INCIDENT_PERSON_FIELDS:
            return self._resolve_person(value, actor)

        if field in INCIDENT_NUMERIC_FIELDS:

            try:
                number = int(float(str(value).strip()))

            except ValueError:
                raise ServiceError("invalid_value", f"{field} must be a number of minutes.")

            return same_type(current.get(field), number)

        if field in ENUM_FIELDS:
            return resolve_choice(value, self._allowed(field), field)

        if field in INCIDENT_TEXT_FIELDS:

            if value in (None, ""):
                raise ServiceError("invalid_value", f"{field} cannot be empty.")

            if field == "working_notes":

                stamp = f"[{local_timestamp()}{' ' + actor if actor else ''}] {value}"

                existing = current.get("working_notes")

                return f"{existing}\n{stamp}" if existing else stamp

        return value

    @guarded
    def update_incident(
        self,
        ticket_id: str,
        changes: Dict[str, Any],
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:

        ensure_writable()

        if not isinstance(changes, dict) or not changes:
            raise ServiceError("missing_fields", "Say which field to change and the new value.")

        current = self._fetch(ticket_id)

        if not current:
            raise self._not_found(ticket_id)

        clean: Dict[str, Any] = {}

        requested: Dict[str, Any] = {}

        for raw_field, raw_value in changes.items():

            field = self._updatable_field(raw_field)

            requested[field] = raw_value

            clean[field] = self._clean_value(field, raw_value, current, actor)

        applied = {
            field: value
            for field, value in clean.items()
            if field == "working_notes" or current.get(field) != value
        }

        unchanged = [field for field in clean if field not in applied]

        if applied:

            stored = dict(applied)

            now = local_timestamp()

            stored["updated_at"] = now

            # Closing a ticket stamps its closing time, as the ITSM does.
            if (
                "state" in applied
                and norm(applied["state"]) == "closed"
                and not current.get("closed_at")
            ):
                stored["closed_at"] = now

            expression, names, values = build_update(stored)

            names["#pk"] = "ticket_id"

            self.table.update_item(
                Key={"ticket_id": current["ticket_id"]},
                UpdateExpression=expression,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ConditionExpression="attribute_exists(#pk)",
            )

            audit(
                "update_incident",
                actor,
                ticket_id=current["ticket_id"],
                changes={f: [current.get(f), v] for f, v in applied.items()},
            )

        return {
            "ok": True,
            "ticket_id": current["ticket_id"],
            "changes": {
                field: {
                    "from": None if field == "working_notes" else current.get(field),
                    "to": (
                        str(requested[field])
                        if field == "working_notes"
                        else value
                    ),
                }
                for field, value in applied.items()
            },
            "unchanged": unchanged,
        }

    # --------------------------------------------------------
    # Delete
    # --------------------------------------------------------

    @guarded
    def delete_incident(
        self,
        ticket_id: str,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:

        ensure_writable()

        if not ENABLE_DELETE_OPERATIONS:
            raise ServiceError("not_allowed", "Delete operations are disabled.")

        current = self._fetch(ticket_id)

        if not current:
            raise self._not_found(ticket_id)

        self.table.delete_item(
            Key={"ticket_id": current["ticket_id"]},
            ConditionExpression="attribute_exists(#pk)",
            ExpressionAttributeNames={"#pk": "ticket_id"},
        )

        audit("delete_incident", actor, ticket_id=current["ticket_id"], snapshot=current)

        return {
            "ok": True,
            "deleted": True,
            "ticket_id": current["ticket_id"],
            "short_description": current.get("short_description"),
        }
