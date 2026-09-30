"""
Roster (employees, teams, availability) access.

Shape of the data
    * One row per employee per month (year, month), identified by UID.
    * emp_id is the employee number; employee_id holds the employee's
      e-mail address (also what incidents store in assigned_to).
    * days_availability maps the day of the month ("1".."31") to "Y"/"N".

So "is Rohit available on 2026-09-30?" means: find Rohit's row for
2026-09 and read day "30". A person is the set of their monthly rows and
is referred to as "emp:<emp_id>". The availability map is never returned
by search or lookup calls, only the requested days.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from config import (
    DIRECTORY_CACHE_TTL_SECONDS,
    ENABLE_DELETE_OPERATIONS,
    FACET_CACHE_TTL_SECONDS,
    MAX_RESULT_LIMIT,
    ROSTER_TABLE_NAME,
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
    local_date,
    norm,
    project,
    resolve_choice,
    resolve_date,
    scalar,
    scan_items,
    to_plain,
    today,
    yes_no,
)

from services.schema import (
    EMPLOYEE_UPDATABLE_FIELDS,
    EMPLOYEE_VIEW_FIELDS,
    resolve_field_names,
)

MAX_RANGE_DAYS = 31

LIST_FIELDS = [
    "ref",
    "employee_name",
    "emp_id",
    "team_id",
    "team_name",
    "level",
    "shift",
    "active",
]

# Identity fields belong to the person, so they change on every monthly row.
_PERSON_FIELDS = {"employee_name", "emp_id", "employee_id"}

# Text fields checked against the values already in use.
_ENUM_FIELDS = {"level", "shift"}

_CARRY_OVER_FIELDS = [
    "employee_name",
    "employee_id",
    "team_id",
    "team_name",
    "team_email_id",
    "level",
    "shift",
    "shift_start_time",
    "shift_end_time",
]

_YES = {"y", "yes", "available", "avail", "working", "present", "on", "in", "true"}

_NO = {
    "n", "no", "leave", "off", "unavailable", "not available", "absent",
    "holiday", "week off", "weekoff", "sick", "sick leave", "pto",
    "vacation", "false", "not working",
}


# ============================================================
# ROW HELPERS
# ============================================================

def _int(value: Any) -> int:

    try:
        return int(value)

    except (TypeError, ValueError):
        return 0


def _month_key(row: Dict[str, Any]) -> Tuple[int, int]:
    return _int(row.get("year")), _int(row.get("month"))


def _month_label(key: Tuple[int, int]) -> str:
    return f"{key[0]:04d}-{key[1]:02d}"


def person_ref(row: Dict[str, Any]) -> str:
    """Stable reference to a person, shared by all of their monthly rows."""

    emp_id = row.get("emp_id")

    if emp_id not in (None, ""):
        return f"emp:{emp_id}"

    mail = str(row.get("employee_id", "")).strip().lower()

    if mail:
        return f"mail:{mail}"

    return f"uid:{row.get('UID')}"


class RosterService:

    def __init__(self, table=None):

        self._table = table

        self._directory_cache = TTLCache(DIRECTORY_CACHE_TTL_SECONDS)

        self._vocabulary_cache = TTLCache(FACET_CACHE_TTL_SECONDS)

    # --------------------------------------------------------
    # Access
    # --------------------------------------------------------

    @property
    def table(self):

        if self._table is None:

            if not ROSTER_TABLE_NAME:
                raise ServiceError(
                    "unavailable",
                    "The roster table is not configured.",
                )

            self._table = get_dynamodb_resource().Table(ROSTER_TABLE_NAME)

        return self._table

    def _rows(self) -> List[Dict[str, Any]]:
        """Every monthly row, without availability maps (cached)."""

        return self._directory_cache.get_or_load(
            "rows",
            lambda: [
                to_plain(item)
                for item in scan_items(self.table, fields=EMPLOYEE_VIEW_FIELDS)
            ],
        )

    def _refresh(self) -> None:
        self._directory_cache.clear()

    # --------------------------------------------------------
    # People
    # --------------------------------------------------------

    @staticmethod
    def _is_team(row: Dict[str, Any], team: str) -> bool:

        wanted = norm(team)

        return bool(wanted) and wanted in {
            norm(row.get("team_id", "")),
            norm(row.get("team_name", "")),
        }

    def _group(self, rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:

        groups: Dict[str, List[Dict[str, Any]]] = {}

        for row in rows:
            groups.setdefault(person_ref(row), []).append(row)

        return groups

    @staticmethod
    def _representative(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """This month's row when there is one, otherwise the latest."""

        current = (today().year, today().month)

        for row in rows:
            if _month_key(row) == current:
                chosen = row
                break

        else:
            chosen = max(rows, key=_month_key)

        person = dict(chosen)

        person["ref"] = person_ref(chosen)

        return person

    def match_people(
        self,
        query: str,
        team: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        People matching a name, e-mail, employee number or reference.

        One entry per person (their current or latest monthly row, plus
        `ref`), however many months they have on file.
        """

        text = str(query or "").strip()

        if not text:
            return []

        rows = self._rows()

        if team:
            rows = [row for row in rows if self._is_team(row, team)]

        groups = self._group(rows)

        lowered = text.lower()

        exact = [
            ref for ref, members in groups.items()
            if lowered == ref.lower()
            or any(
                lowered in {
                    str(row.get("emp_id", "")).strip().lower(),
                    str(row.get("employee_id", "")).strip().lower(),
                    str(row.get("UID", "")).strip().lower(),
                }
                for row in members
            )
        ]

        if exact:
            return [self._representative(groups[ref]) for ref in exact]

        tokens = norm(text).split()

        if not tokens:
            return []

        def name_tokens(row):
            return norm(
                f"{row.get('employee_name', '')} "
                f"{str(row.get('employee_id', '')).split('@')[0]}"
            ).split()

        matched = [
            ref for ref, members in groups.items()
            if any(
                all(
                    any(word == token or word.startswith(token) for word in name_tokens(row))
                    for token in tokens
                )
                for row in members
            )
        ]

        whole = [
            ref for ref in matched
            if any(
                norm(row.get("employee_name", "")) == norm(text)
                for row in groups[ref]
            )
        ]

        return [self._representative(groups[ref]) for ref in (whole or matched)]

    def _resolve_employee(
        self,
        ref: Any,
        team: Optional[str] = None,
    ) -> Dict[str, Any]:

        text = str(ref or "").strip()

        if not text:
            raise ServiceError("missing_fields", "Which employee?")

        matches = self.match_people(text, team)

        if not matches:
            raise ServiceError(
                "not_found",
                f"No employee matches '{text}'.",
                query=text,
            )

        if len(matches) > 1:

            raise ServiceError(
                "ambiguous",
                f"Several employees match '{text}'. Which one?",
                options=[
                    project(item, ["ref", "employee_name", "emp_id", "team_id", "team_name"])
                    for item in matches[:6]
                ],
            )

        return matches[0]

    def _person_rows(self, person: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [row for row in self._rows() if person_ref(row) == person["ref"]]

    def _row_for(
        self,
        person: Dict[str, Any],
        year: int,
        month: int,
    ) -> Optional[Dict[str, Any]]:

        for row in self._person_rows(person):

            if _month_key(row) == (year, month):
                return row

        return None

    # --------------------------------------------------------
    # Teams
    # --------------------------------------------------------

    def _team_rows(self, team: Any) -> List[Dict[str, Any]]:
        """All monthly rows of a team (matched by id or name)."""

        text = str(team or "").strip()

        if not text:
            raise ServiceError("missing_fields", "Which team?")

        rows = self._rows()

        found = [row for row in rows if self._is_team(row, text)]

        if not found:

            wanted = norm(text)

            found = [row for row in rows if wanted in norm(row.get("team_name", ""))]

            teams = {row.get("team_id") for row in found}

            if len(teams) > 1:
                raise ServiceError(
                    "ambiguous_value",
                    f"'{text}' matches several teams: "
                    + ", ".join(sorted(str(t) for t in teams if t)[:6])
                    + ".",
                )

        if not found:
            raise ServiceError("not_found", f"No team matches '{text}'.", query=text)

        return found

    def _team_ids(self) -> List[str]:
        return sorted({str(row["team_id"]) for row in self._rows() if row.get("team_id")})

    def _values(self, field: str) -> List[str]:
        return sorted({str(row[field]) for row in self._rows() if row.get(field)})

    def _team_defaults(self, team_id: str) -> Dict[str, Any]:
        """team_name / team_email_id shared by the rows of a team."""

        rows = [row for row in self._rows() if norm(row.get("team_id", "")) == norm(team_id)]

        defaults = {}

        for field in ("team_name", "team_email_id"):

            values = {row[field] for row in rows if row.get(field)}

            if len(values) == 1:
                defaults[field] = values.pop()

        return defaults

    # --------------------------------------------------------
    # Availability data
    # --------------------------------------------------------

    def _days(self, uid: Any) -> Dict[str, Any]:
        """The day-of-month -> Y/N map of one monthly row."""

        item = self.table.get_item(
            Key={"UID": uid},
            ProjectionExpression="#d",
            ExpressionAttributeNames={"#d": "days_availability"},
        ).get("Item")

        days = to_plain((item or {}).get("days_availability"))

        return days if isinstance(days, dict) else {}

    @staticmethod
    def _date_range(start: Any, end: Any = None) -> List[str]:

        first = date.fromisoformat(resolve_date(start))

        last = date.fromisoformat(resolve_date(end)) if end else first

        if last < first:
            raise ServiceError(
                "invalid_value",
                "The start date cannot be after the end date.",
            )

        if (last - first).days + 1 > MAX_RANGE_DAYS:
            raise ServiceError(
                "invalid_value",
                f"Please ask for at most {MAX_RANGE_DAYS} days at a time.",
            )

        return [
            (first + timedelta(days=offset)).isoformat()
            for offset in range((last - first).days + 1)
        ]

    def _summary(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Compact view of a month: how many days on / off and which are off."""

        days = self._days(row["UID"])

        working = [key for key, value in days.items() if str(value).upper() == "Y"]

        off = sorted(int(key) for key, value in days.items() if str(value).upper() == "N" and str(key).isdigit())

        if not working and not off:
            return None

        return {
            "month": _month_label(_month_key(row)),
            "working_days": len(working),
            "off_days": off,
        }

    # --------------------------------------------------------
    # Reads
    # --------------------------------------------------------

    @guarded
    def search_roster(
        self,
        query: Optional[str] = None,
        team: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:

        if query:
            people = self.match_people(query, team)

        else:

            rows = self._rows()

            if team:
                rows = [row for row in rows if self._is_team(row, team)]

            people = [self._representative(members) for members in self._group(rows).values()]

        cap = limit_value(limit)

        return {
            "ok": True,
            "count": min(len(people), cap),
            "total": len(people),
            "employees": [project(item, LIST_FIELDS) for item in people[:cap]],
        }

    @guarded
    def get_roster_entry(
        self,
        employee: str,
        fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:

        person = self._resolve_employee(employee)

        selected = resolve_field_names("employee", fields) if fields else []

        if fields and not selected:
            raise ServiceError(
                "invalid_value",
                "Unknown field(s): " + ", ".join(map(str, fields)),
            )

        if selected:

            data = project(person, ["employee_name", *selected], keep_empty=True)

        else:

            data = project(person, EMPLOYEE_VIEW_FIELDS)

            summary = self._summary(person)

            if summary:
                data["availability_summary"] = summary

        return {"ok": True, "ref": person["ref"], "employee": data}

    @guarded
    def get_team_roster(
        self,
        team: str,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:

        rows = self._team_rows(team)

        current = (today().year, today().month)

        this_month = [row for row in rows if _month_key(row) == current]

        chosen = this_month or [row for row in rows if _month_key(row) == max(map(_month_key, rows))]

        cap = min(int(limit or MAX_RESULT_LIMIT), MAX_RESULT_LIMIT * 2)

        people = [self._representative([row]) for row in chosen]

        return {
            "ok": True,
            "team_id": chosen[0].get("team_id"),
            "team_name": chosen[0].get("team_name"),
            "month": _month_label(_month_key(chosen[0])),
            "count": min(len(people), cap),
            "total": len(people),
            "employees": [
                project(item, ["ref", "employee_name", "level", "shift", "active"])
                for item in people[:cap]
            ],
        }

    @guarded
    def get_employee_availability(
        self,
        employee: str,
        start_date: Any = "today",
        end_date: Any = None,
    ) -> Dict[str, Any]:

        person = self._resolve_employee(employee)

        dates = self._date_range(start_date, end_date)

        maps: Dict[Tuple[int, int], Optional[Dict[str, Any]]] = {}

        availability: Dict[str, Any] = {}

        missing = set()

        for iso in dates:

            day = date.fromisoformat(iso)

            key = (day.year, day.month)

            if key not in maps:

                row = self._row_for(person, *key)

                maps[key] = self._days(row["UID"]) if row else None

            if maps[key] is None:
                missing.add(_month_label(key))
                availability[iso] = None

            else:
                availability[iso] = maps[key].get(str(day.day))

        result: Dict[str, Any] = {
            "ok": True,
            "ref": person["ref"],
            "employee_name": person.get("employee_name"),
            "team_id": person.get("team_id"),
            "availability": availability,
        }

        if missing:
            result["no_roster_for"] = sorted(missing)

        return result

    @guarded
    def get_team_availability(
        self,
        team: str,
        when: Any = "today",
    ) -> Dict[str, Any]:

        iso = resolve_date(when)

        day = date.fromisoformat(iso)

        key = (day.year, day.month)

        team_rows = self._team_rows(team)

        rows = [row for row in team_rows if _month_key(row) == key]

        if not rows:
            raise ServiceError(
                "not_found",
                f"The roster has no rows for {team_rows[0].get('team_id')} in {_month_label(key)}.",
            )

        members = [
            {
                "ref": person_ref(row),
                "name": row.get("employee_name"),
                "shift": row.get("shift"),
                "availability": self._days(row["UID"]).get(str(day.day)),
            }
            for row in rows[: MAX_RESULT_LIMIT * 2]
        ]

        return {
            "ok": True,
            "team_id": rows[0].get("team_id"),
            "team_name": rows[0].get("team_name"),
            "date": iso,
            "count": len(members),
            "members": members,
        }

    # --------------------------------------------------------
    # Availability writes
    # --------------------------------------------------------

    def _vocabulary(self) -> List[str]:
        """Text values already used for a day (normally just "Y" and "N")."""

        def load():

            found = set()

            for item in scan_items(self.table, fields=["days_availability"]):

                days = item.get("days_availability")

                if not isinstance(days, dict):
                    continue

                for value in days.values():

                    if isinstance(value, str) and value.strip():
                        found.add(value.strip())

                if len(found) > 30:
                    return []

            return sorted(found)

        return self._vocabulary_cache.get_or_load("vocabulary", load)

    def _clean_day_value(self, value: Any) -> str:
        """User wording ("leave", "available", true) -> the stored value."""

        vocabulary = self._vocabulary()

        binary = {item.upper() for item in vocabulary} <= {"Y", "N"}

        if isinstance(value, bool):
            value = "yes" if value else "no"

        if not isinstance(value, str):
            raise ServiceError("invalid_value", "Unsupported availability value.")

        text = norm(scalar(value, "availability", 100))

        if not text:
            raise ServiceError("invalid_value", "An availability value is required.")

        if binary:

            if text in _YES:
                return "Y"

            if text in _NO:
                return "N"

            raise ServiceError(
                "invalid_value",
                f"'{value}' is not a valid availability. Use Y (available) or N (not available / leave).",
            )

        return resolve_choice(value, vocabulary, "availability")

    def _set_days(
        self,
        uid: Any,
        updates: Dict[str, Any],
        actor: Optional[str],
    ) -> None:
        """Atomically set individual days without rewriting the map."""

        names = {"#days": "days_availability", "#on": "updated_on", "#pk": "UID"}

        values: Dict[str, Any] = {":on": local_date()}

        parts = ["#on = :on"]

        if actor:
            names["#by"] = "updated_by"
            values[":by"] = actor
            parts.append("#by = :by")

        for index, (day, value) in enumerate(updates.items()):

            names[f"#d{index}"] = day

            values[f":v{index}"] = value

            parts.append(f"#days.#d{index} = :v{index}")

        try:

            self.table.update_item(
                Key={"UID": uid},
                UpdateExpression="SET " + ", ".join(parts),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ConditionExpression="attribute_exists(#pk)",
            )

        except ClientError as exc:

            if exc.response.get("Error", {}).get("Code") != "ValidationException":
                raise

            # The row has no availability map yet: create it.
            self.table.update_item(
                Key={"UID": uid},
                UpdateExpression="SET #days = :map, #on = :on",
                ExpressionAttributeNames={
                    "#days": "days_availability",
                    "#on": "updated_on",
                    "#pk": "UID",
                },
                ExpressionAttributeValues={":map": updates, ":on": local_date()},
                ConditionExpression="attribute_exists(#pk) AND attribute_not_exists(#days)",
            )

    @guarded
    def set_employee_availability(
        self,
        employee: str,
        availability: Dict[str, Any],
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:

        ensure_writable()

        if not isinstance(availability, dict) or not availability:
            raise ServiceError(
                "missing_fields",
                "Provide the date(s) and the availability to set.",
            )

        person = self._resolve_employee(employee)

        # Validate everything first so a bad entry changes nothing.
        planned: Dict[Tuple[int, int], Dict[str, Any]] = {}

        rows: Dict[Tuple[int, int], Dict[str, Any]] = {}

        for raw_date, raw_value in availability.items():

            iso = resolve_date(raw_date)

            day = date.fromisoformat(iso)

            key = (day.year, day.month)

            if key not in rows:

                row = self._row_for(person, *key)

                if row is None:
                    raise ServiceError(
                        "not_found",
                        f"{person.get('employee_name')} has no roster row for {_month_label(key)}.",
                    )

                rows[key] = row

            planned.setdefault(key, {})[str(day.day)] = (iso, self._clean_day_value(raw_value))

        changes: Dict[str, Any] = {}

        for key, entries in planned.items():

            uid = rows[key]["UID"]

            current = self._days(uid)

            self._set_days(uid, {day: value for day, (_, value) in entries.items()}, actor)

            for day, (iso, value) in entries.items():
                changes[iso] = {"from": current.get(day), "to": value}

        audit("set_employee_availability", actor, ref=person["ref"], changes=changes)

        return {
            "ok": True,
            "ref": person["ref"],
            "employee_name": person.get("employee_name"),
            "changes": changes,
        }

    @guarded
    def set_team_availability(
        self,
        team: str,
        when: Any,
        availability: Any,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:

        ensure_writable()

        iso = resolve_date(when)

        day = date.fromisoformat(iso)

        value = self._clean_day_value(availability)

        team_rows = self._team_rows(team)

        rows = [row for row in team_rows if _month_key(row) == (day.year, day.month)]

        if not rows:
            raise ServiceError(
                "not_found",
                f"The roster has no rows for {team_rows[0].get('team_id')} in "
                f"{_month_label((day.year, day.month))}.",
            )

        done, failed = [], []

        for row in rows:

            name = row.get("employee_name") or str(row["UID"])

            try:

                self._set_days(row["UID"], {str(day.day): value}, actor)

                done.append(name)

            except Exception as exc:

                failed.append(name)

                if isinstance(exc, ClientError):
                    audit("set_team_availability_failed", actor, uid=row["UID"], code=exc.response.get("Error", {}).get("Code"))

        audit("set_team_availability", actor, team=team, date=iso, value=value, updated=done)

        result: Dict[str, Any] = {
            "ok": not failed,
            "team_id": rows[0].get("team_id"),
            "team_name": rows[0].get("team_name"),
            "date": iso,
            "value": value,
            "updated": len(done),
            "failed": failed,
        }

        if failed:
            result["error"] = "partial"
            result["message"] = f"Could not update: {', '.join(failed)}."

        return result

    # --------------------------------------------------------
    # Row writes
    # --------------------------------------------------------

    def _validated_fields(
        self,
        changes: Dict[str, Any],
        row: Dict[str, Any],
    ) -> Dict[str, Any]:

        clean: Dict[str, Any] = {}

        for raw_field, raw_value in changes.items():

            names = resolve_field_names("employee", [raw_field])

            if len(names) != 1 or names[0] not in EMPLOYEE_UPDATABLE_FIELDS:
                raise ServiceError(
                    "invalid_field",
                    f"'{raw_field}' cannot be updated. Updatable fields: "
                    + ", ".join(sorted(EMPLOYEE_UPDATABLE_FIELDS)),
                )

            field = names[0]

            value = scalar(raw_value, field, 200)

            if field == "active":
                value = yes_no(value, row.get(field), field)

            elif field == "team_id":
                value = resolve_choice(value, self._team_ids(), "team")

            elif field in _ENUM_FIELDS:
                value = resolve_choice(value, self._values(field), field)

            elif value in (None, ""):
                raise ServiceError("invalid_value", f"{field} cannot be empty.")

            elif field == "employee_id" and "@" not in str(value):
                raise ServiceError("invalid_value", "employee_id is the e-mail address.")

            clean[field] = value

        return clean

    def _write_row(
        self,
        row: Dict[str, Any],
        fields: Dict[str, Any],
        actor: Optional[str],
    ) -> None:

        stored = dict(fields)

        stored["updated_on"] = local_date()

        if actor:
            stored["updated_by"] = actor

        expression, names, values = build_update(stored)

        names["#pk"] = "UID"

        self.table.update_item(
            Key={"UID": row["UID"]},
            UpdateExpression=expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(#pk)",
        )

    def _target_row(
        self,
        person: Dict[str, Any],
        year: Optional[int],
        month: Optional[int],
    ) -> Dict[str, Any]:

        if year and month:

            row = self._row_for(person, int(year), int(month))

            if row is None:
                raise ServiceError(
                    "not_found",
                    f"{person.get('employee_name')} has no roster row for "
                    f"{_month_label((int(year), int(month)))}.",
                )

            return row

        return person

    @guarded
    def update_roster_entry(
        self,
        employee: str,
        changes: Dict[str, Any],
        actor: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Change roster details.

        Shift, level, team etc. change the row of one month (this month
        unless year/month are given). Name, employee number and e-mail
        identify the person, so they change on all of their rows.
        """

        ensure_writable()

        if not isinstance(changes, dict) or not changes:
            raise ServiceError("missing_fields", "Say which field to change and the new value.")

        person = self._resolve_employee(employee)

        target = self._target_row(person, year, month)

        clean = self._validated_fields(changes, target)

        if "team_id" in clean:

            for field, value in self._team_defaults(clean["team_id"]).items():
                clean.setdefault(field, value)

        applied = {field: value for field, value in clean.items() if target.get(field) != value}

        if applied:

            identity = {field: value for field, value in applied.items() if field in _PERSON_FIELDS}

            for row in self._person_rows(person):

                fields = dict(identity)

                if row["UID"] == target["UID"]:
                    fields.update(applied)

                if fields:
                    self._write_row(row, fields, actor)

            self._refresh()

            audit(
                "update_roster_entry",
                actor,
                ref=person["ref"],
                month=_month_label(_month_key(target)),
                changes={f: [target.get(f), v] for f, v in applied.items()},
            )

        return {
            "ok": True,
            "ref": person["ref"],
            "employee_name": person.get("employee_name"),
            "month": _month_label(_month_key(target)),
            "changes": {
                field: {"from": target.get(field), "to": value}
                for field, value in applied.items()
            },
            "unchanged": [field for field in clean if field not in applied],
        }

    def _next_uid(self) -> Any:
        """Next free UID, in the same type (number / text) as the existing ones."""

        uids = [row["UID"] for row in self._rows() if row.get("UID") not in (None, "")]

        numbers = [int(str(uid)) for uid in uids if str(uid).isdigit()]

        if not numbers:
            return "1"

        following = max(numbers) + 1

        return following if isinstance(uids[0], int) else str(following)

    @guarded
    def create_roster_entry(
        self,
        fields: Dict[str, Any],
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create the roster row of one employee for one month.

        For an employee who already has rows, only the emp_id (and the
        month) are needed: the rest is carried over from their latest row.
        """

        ensure_writable()

        allowed = EMPLOYEE_UPDATABLE_FIELDS | {"year", "month"}

        supplied = {
            key: scalar(value, key, 200)
            for key, value in (fields or {}).items()
            if key in allowed and value not in (None, "")
        }

        rows = self._rows()

        emp_id = supplied.get("emp_id")

        earlier = (
            [row for row in rows if str(row.get("emp_id", "")).strip().lower() == str(emp_id).strip().lower()]
            if emp_id
            else []
        )

        data: Dict[str, Any] = {}

        if earlier:

            latest = max(earlier, key=_month_key)

            data.update(project(latest, _CARRY_OVER_FIELDS))

        data.update(supplied)

        missing = [
            field for field in ("employee_name", "employee_id", "emp_id", "team_id")
            if not data.get(field)
        ]

        if missing:
            raise ServiceError(
                "missing_fields",
                "Missing required information: " + ", ".join(missing)
                + " (employee_id is the e-mail address).",
                missing=missing,
            )

        if "@" not in str(data["employee_id"]):
            raise ServiceError("invalid_value", "employee_id is the e-mail address.")

        data["team_id"] = resolve_choice(data["team_id"], self._team_ids(), "team")

        for field, value in self._team_defaults(data["team_id"]).items():
            data.setdefault(field, value)

        for field in _ENUM_FIELDS & set(supplied):
            data[field] = resolve_choice(data[field], self._values(field), field)

        current = today()

        year = _int(data.pop("year", current.year))

        month = _int(data.pop("month", current.month))

        if not 1 <= month <= 12 or year < 2000:
            raise ServiceError("invalid_value", "Give a valid year and month.")

        if any(
            _month_key(row) == (year, month)
            and (
                str(row.get("emp_id", "")).strip().lower() == str(data["emp_id"]).strip().lower()
                or str(row.get("employee_id", "")).strip().lower() == str(data["employee_id"]).strip().lower()
            )
            for row in rows
        ):
            raise ServiceError(
                "conflict",
                f"{data['employee_name']} already has a roster row for {_month_label((year, month))}.",
            )

        style = next((row.get("active") for row in rows if "active" in row), "Yes")

        active = yes_no(data.pop("active"), style, "active") if "active" in data else yes_no(True, style)

        uid = self._next_uid()

        record = {
            "UID": uid,
            "year": year,
            "month": month,
            **data,
            "active": active,
            "days_availability": {},
            "created_on": local_date(),
            "updated_on": local_date(),
        }

        if actor:
            record["updated_by"] = actor

        self.table.put_item(
            Item=record,
            ConditionExpression="attribute_not_exists(#pk)",
            ExpressionAttributeNames={"#pk": "UID"},
        )

        self._refresh()

        audit("create_roster_entry", actor, uid=uid, record={k: v for k, v in record.items() if k != "days_availability"})

        return {
            "ok": True,
            "ref": person_ref(record),
            "month": _month_label((year, month)),
            "employee": project(record, EMPLOYEE_VIEW_FIELDS),
        }

    @guarded
    def delete_roster_entry(
        self,
        employee: str,
        actor: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Delete one monthly roster row: the given month, else this month's,
        else the only one the employee has. Never several at once.
        """

        ensure_writable()

        if not ENABLE_DELETE_OPERATIONS:
            raise ServiceError("not_allowed", "Delete operations are disabled.")

        person = self._resolve_employee(employee)

        rows = self._person_rows(person)

        if year and month:

            target = self._target_row(person, year, month)

        else:

            current = (today().year, today().month)

            this_month = [row for row in rows if _month_key(row) == current]

            if this_month:
                target = this_month[0]

            elif len(rows) == 1:
                target = rows[0]

            else:

                months = [_month_label(_month_key(row)) for row in sorted(rows, key=_month_key)]

                raise ServiceError(
                    "ambiguous_month",
                    f"{person.get('employee_name')} has roster rows for {', '.join(months)}. "
                    "Which month should I delete?",
                    options=months,
                )

        response = self.table.delete_item(
            Key={"UID": target["UID"]},
            ConditionExpression="attribute_exists(#pk)",
            ExpressionAttributeNames={"#pk": "UID"},
            ReturnValues="ALL_OLD",
        )

        self._refresh()

        old = to_plain(response.get("Attributes") or target)

        audit(
            "delete_roster_entry",
            actor,
            uid=target["UID"],
            snapshot={k: v for k, v in old.items() if k != "days_availability"},
        )

        return {
            "ok": True,
            "deleted": True,
            "ref": person["ref"],
            "employee_name": person.get("employee_name"),
            "month": _month_label(_month_key(target)),
        }
