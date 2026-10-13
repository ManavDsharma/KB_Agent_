import re
import unittest
from unittest import mock

from services.common import ServiceError, resolve_choice, resolve_date, today
from tests.fakes import (
    World,
    client_error,
    month_days,
    previous_month,
    second_rohit,
)

TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


def this_month_date(day: int) -> str:

    current = today()

    return f"{current.year}-{current.month:02d}-{day:02d}"


class IncidentReadTests(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.svc = self.world.incident_service

    def test_get_incident_default_view_is_slim(self):

        result = self.svc.get_incident("INC0000258")

        self.assertTrue(result["ok"])

        incident = result["incident"]

        self.assertEqual(incident["priority"], "P1-Critical")
        self.assertNotIn("working_notes", incident)
        self.assertLessEqual(len(incident["description"]), 300)

    def test_only_requested_fields(self):

        result = self.svc.get_incident("INC0000258", ["assignee", "priority"])

        self.assertEqual(
            result["incident"],
            {"ticket_id": "INC0000258", "assigned_to": "varun.nambiar@abc.com", "priority": "P1-Critical"},
        )

    def test_empty_field_is_reported_not_dropped(self):

        result = self.svc.get_incident("INC0000259", ["assigned_to"])

        self.assertEqual(result["incident"], {"ticket_id": "INC0000259", "assigned_to": None})

    def test_id_spellings_resolve_to_the_stored_key(self):

        for spelling in ("INC0000258", "INC-0000258", "inc 0000258", "incident 258", "INC258"):

            self.assertEqual(self.svc.get_incident(spelling)["ticket_id"], "INC0000258", spelling)

    def test_request_ids(self):
        self.assertEqual(self.svc.get_incident("REQ-0000162")["ticket_id"], "REQ0000162")

    def test_not_found(self):

        result = self.svc.get_incident("INC0009999")

        self.assertEqual((result["ok"], result["error"]), (False, "not_found"))
        self.assertIn("INC0009999", result["message"])

    def test_unknown_field_is_rejected(self):
        self.assertEqual(self.svc.get_incident("INC0000258", ["nonsense"])["error"], "invalid_value")

    def test_long_text_is_truncated_on_request(self):

        result = self.svc.get_incident("INC0000258", ["description"])

        self.assertLessEqual(len(result["incident"]["description"]), 800)


class IncidentSearchTests(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.svc = self.world.incident_service

    def test_filters_use_existing_spellings(self):

        result = self.svc.search_incidents(priority="p2")

        self.assertEqual([row["ticket_id"] for row in result["incidents"]], ["INC0000260"])

    def test_pagination_is_followed(self):

        result = self.svc.search_incidents(team_id="infrastructure")

        self.assertEqual(result["total"], 2)

    def test_unknown_value_explains_itself(self):

        result = self.svc.search_incidents(priority="P9")

        self.assertEqual(result["count"], 0)
        self.assertIn("P1-Critical", result["note"])

    def test_results_are_compact_and_newest_first(self):

        rows = self.svc.search_incidents()["incidents"]

        self.assertEqual(rows[0]["ticket_id"], "INC0000259")
        self.assertNotIn("description", rows[0])
        self.assertNotIn("working_notes", rows[0])

    def test_text_and_person_filters(self):

        self.assertEqual(self.svc.search_incidents(text="vpn")["total"], 1)
        self.assertEqual(self.svc.search_incidents(assigned_to="rohit")["total"], 1)

    def test_limit(self):

        result = self.svc.search_incidents(limit=1)

        self.assertEqual((result["count"], result["total"]), (1, 4))

    def test_by_date(self):

        result = self.svc.list_incidents_by_date("today")

        self.assertEqual({row["ticket_id"] for row in result["incidents"]}, {"INC0000259", "INC0000260"})

    def test_by_range(self):

        result = self.svc.list_incidents_by_date("2025-03-01", "2026-02-28")

        self.assertEqual({row["ticket_id"] for row in result["incidents"]}, {"REQ0000162", "INC0000258"})

    def test_bad_range_and_date(self):

        self.assertEqual(self.svc.list_incidents_by_date("2026-09-30", "2026-09-01")["error"], "invalid_value")
        self.assertEqual(self.svc.list_incidents_by_date("30/09/2026")["error"], "invalid_value")

    def test_sla_breaches_read_the_stored_true_false_text(self):

        result = self.svc.list_sla_breaches()

        self.assertEqual({row["ticket_id"] for row in result["incidents"]}, {"REQ0000162", "INC0000260"})

        self.assertEqual(
            [row["ticket_id"] for row in self.svc.list_sla_breaches("resolution")["incidents"]],
            ["REQ0000162"],
        )
        self.assertEqual(
            [row["ticket_id"] for row in self.svc.list_sla_breaches("assignment")["incidents"]],
            ["INC0000260"],
        )

    def test_invalid_breach_type(self):
        self.assertEqual(self.svc.list_sla_breaches("bogus")["error"], "invalid_value")


class IncidentWriteTests(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.svc = self.world.incident_service

    def stored(self, ticket="INC0000258"):
        return self.world.incidents.items[ticket]

    def test_update_maps_value_onto_existing_spelling(self):

        result = self.svc.update_incident("INC0000258", {"priority": "P2"}, actor="tester")

        self.assertTrue(result["ok"])
        self.assertEqual(result["changes"], {"priority": {"from": "P1-Critical", "to": "P2-High"}})
        self.assertEqual(self.stored()["priority"], "P2-High")

    def test_updated_at_uses_the_table_format(self):

        self.svc.update_incident("INC0000258", {"priority": "P2"})

        self.assertRegex(self.stored()["updated_at"], TIMESTAMP)

    def test_alias_field_names(self):

        self.svc.update_incident("INC0000258", {"status": "in progress"})

        self.assertEqual(self.stored()["state"], "In Progress")

    def test_invalid_value_lists_allowed_and_writes_nothing(self):

        result = self.svc.update_incident("INC0000258", {"priority": "P9"})

        self.assertEqual(result["error"], "invalid_value")
        self.assertIn("P2-High", result["message"])
        self.assertEqual(self.stored()["priority"], "P1-Critical")
        self.assertEqual(self.world.incidents.count("update_item"), 0)

    def test_read_only_fields_cannot_change(self):

        for field in ("ticket_id", "created_at", "has_resolution_sla_breach", "updated_at", "closed_at"):

            result = self.svc.update_incident("INC0000258", {field: "x"})

            self.assertEqual(result["error"], "invalid_field", field)

    def test_unknown_field(self):
        self.assertEqual(self.svc.update_incident("INC0000258", {"colour": "red"})["error"], "invalid_field")

    def test_missing_ticket(self):

        self.assertEqual(self.svc.update_incident("INC0009999", {"priority": "P2"})["error"], "not_found")
        self.assertEqual(self.world.incidents.count("update_item"), 0)

    def test_empty_changes(self):
        self.assertEqual(self.svc.update_incident("INC0000258", {})["error"], "missing_fields")

    def test_no_op_update_does_not_write(self):

        result = self.svc.update_incident("INC0000258", {"priority": "P1"})

        self.assertEqual(result["changes"], {})
        self.assertEqual(result["unchanged"], ["priority"])
        self.assertEqual(self.world.incidents.count("update_item"), 0)

    def test_multiple_fields_in_one_write(self):

        self.svc.update_incident("INC0000258", {"priority": "P2", "state": "in progress"})

        self.assertEqual(self.world.incidents.count("update_item"), 1)
        self.assertEqual((self.stored()["priority"], self.stored()["state"]), ("P2-High", "In Progress"))

    def test_one_bad_field_writes_nothing(self):

        result = self.svc.update_incident("INC0000258", {"priority": "P2", "state": "Bogus"})

        self.assertFalse(result["ok"])
        self.assertEqual(self.stored()["priority"], "P1-Critical")

    def test_closing_stamps_closed_at(self):

        self.svc.update_incident("INC0000259", {"state": "closed"})

        self.assertRegex(self.stored("INC0000259")["closed_at"], TIMESTAMP)

    def test_an_existing_closed_at_is_kept(self):

        self.svc.update_incident("INC0000258", {"state": "New"})
        self.svc.update_incident("INC0000258", {"state": "Closed"})

        self.assertEqual(self.stored()["closed_at"], "2025-03-14 18:56")

    def test_assignee_resolved_to_the_roster_email(self):

        result = self.svc.update_incident("INC0000259", {"assigned_to": "Priya"})

        self.assertEqual(result["changes"]["assigned_to"]["to"], "priya.nair@abc.com")

    def test_assignee_by_full_name(self):

        self.svc.update_incident("INC0000259", {"assigned_to": "Varun Nambiar"})

        self.assertEqual(self.stored("INC0000259")["assigned_to"], "varun.nambiar@abc.com")

    def test_email_assignee_is_accepted(self):

        self.svc.update_incident("INC0000259", {"assigned_to": "new.person@abc.com"})

        self.assertEqual(self.stored("INC0000259")["assigned_to"], "new.person@abc.com")

    def test_ambiguous_assignee_asks(self):

        world = World(extra_people=[second_rohit()])

        result = world.incident_service.update_incident("INC0000259", {"assigned_to": "Rohit"})

        self.assertEqual(result["error"], "ambiguous_value")
        self.assertIn("Rohit Sharma", result["message"])

    def test_unknown_assignee(self):

        result = self.svc.update_incident("INC0000259", {"assigned_to": "Zed Nobody"})

        self.assertEqual(result["error"], "invalid_value")

    def test_me_uses_the_actor(self):

        result = self.svc.update_incident("INC0000259", {"assigned_to": "me"}, actor="boss@abc.com")

        self.assertEqual(result["changes"]["assigned_to"]["to"], "boss@abc.com")

    def test_working_notes_are_appended_not_replaced(self):

        self.svc.update_incident("INC0000258", {"working_notes": "Called vendor"}, actor="tester")

        notes = self.stored()["working_notes"]

        self.assertTrue(notes.startswith("Resolved after applying"))
        self.assertIn("Called vendor", notes)

    def test_sla_minutes_must_be_numeric(self):

        self.assertEqual(self.svc.update_incident("INC0000258", {"assignment_sla": "soon"})["error"], "invalid_value")

    def test_numbers_keep_the_column_type(self):
        """INC0000258 stores SLA minutes as numbers, REQ0000162 as text."""

        self.svc.update_incident("INC0000258", {"assignment_sla": "90"})
        self.svc.update_incident("REQ0000162", {"assignment_sla": 90})

        self.assertEqual(self.stored("INC0000258")["assignment_sla"], 90)
        self.assertEqual(self.stored("REQ0000162")["assignment_sla"], "90")

    def test_nested_values_rejected(self):
        self.assertEqual(self.svc.update_incident("INC0000258", {"priority": ["P2"]})["error"], "invalid_value")

    def test_audit_log_is_written(self):

        with self.assertLogs("dtp.audit", level="INFO") as logs:
            self.svc.update_incident("INC0000258", {"priority": "P2"}, actor="tester")

        self.assertIn("update_incident", logs.output[0])
        self.assertIn("tester", logs.output[0])

    # ---- create -----------------------------------------------------

    def test_create_lists_missing_information(self):

        result = self.svc.create_incident({"short_description": "Database failure"})

        self.assertEqual(result["error"], "missing_fields")
        self.assertEqual(result["missing"], ["category", "urgency", "priority"])
        self.assertEqual(self.world.incidents.count("put_item"), 0)

    def test_create_incident(self):

        result = self.svc.create_incident(
            {
                "short_description": "Database failure",
                "category": "infrastructure",
                "sub_category": "database",
                "urgency": "high",
                "priority": "p1",
            },
            actor="tester@abc.com",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["ticket_id"], "INC0000261")

        record = self.stored("INC0000261")

        self.assertEqual(record["category"], "Infrastructure")
        self.assertEqual(record["priority"], "P1-Critical")
        self.assertEqual(record["urgency"], "High")
        self.assertEqual(record["team_id"], "Infrastructure")
        self.assertEqual(record["state"], "New")
        self.assertEqual(record["opened_by"], "tester@abc.com")
        self.assertEqual(record["description"], "Database failure")
        self.assertRegex(record["created_at"], TIMESTAMP)
        self.assertEqual(record["created_at"], record["updated_at"])
        self.assertNotIn("has_assignment_sla_breach", record)

    def test_request_numbers_do_not_shift_incident_numbers(self):

        self.world.incidents.items["REQ0009999"] = {"ticket_id": "REQ0009999"}

        result = self.svc.create_incident(
            {"short_description": "x", "category": "Access", "urgency": "Low", "priority": "P3"}
        )

        self.assertEqual(result["ticket_id"], "INC0000261")

    def test_create_with_invalid_category_is_rejected(self):

        result = self.svc.create_incident(
            {"short_description": "x", "category": "Nonsense", "urgency": "high", "priority": "p1"}
        )

        self.assertEqual(result["error"], "invalid_value")

    def test_ticket_number_collision_is_retried(self):

        real_put = self.world.incidents.put_item
        attempts = []

        def flaky(Item, **kwargs):
            attempts.append(Item["ticket_id"])
            if len(attempts) == 1:
                raise client_error("ConditionalCheckFailedException")
            return real_put(Item=Item, **kwargs)

        self.world.incidents.put_item = flaky

        result = self.svc.create_incident(
            {"short_description": "x", "category": "Access", "urgency": "Low", "priority": "P3"}
        )

        self.assertTrue(result["ok"])
        self.assertEqual(attempts, ["INC0000261", "INC0000262"])

    # ---- delete -----------------------------------------------------

    def test_delete(self):

        result = self.svc.delete_incident("INC-0000259", actor="tester")

        self.assertTrue(result["ok"])
        self.assertNotIn("INC0000259", self.world.incidents.items)

    def test_delete_missing(self):

        self.assertEqual(self.svc.delete_incident("INC0009999")["error"], "not_found")

    def test_delete_can_be_disabled(self):

        with mock.patch("services.incident_service.ENABLE_DELETE_OPERATIONS", False):

            result = self.svc.delete_incident("INC0000259")

        self.assertEqual(result["error"], "not_allowed")
        self.assertIn("INC0000259", self.world.incidents.items)


class ReadOnlyModeTests(unittest.TestCase):
    """READ_ONLY_MODE makes every write fail before anything is touched."""

    def test_no_write_gets_through(self):

        world = World()

        with mock.patch("services.common.READ_ONLY_MODE", True):

            results = [
                world.incident_service.update_incident("INC0000258", {"priority": "P2"}),
                world.incident_service.create_incident({"short_description": "x", "category": "Access", "urgency": "Low", "priority": "P3"}),
                world.incident_service.delete_incident("INC0000259"),
                world.categories.update_issue("Issue-1", {"impact": "High"}),
                world.people.set_employee_availability("Rohit", {"today": "N"}),
                world.people.set_team_availability("Access Management", "today", "N"),
                world.people.update_roster_entry("Rohit", {"shift": "S2"}),
                world.people.create_roster_entry({"employee_name": "A", "employee_id": "a@abc.com", "emp_id": "9", "team_id": "Access Management"}),
                world.people.delete_roster_entry("Rohit"),
            ]

        for result in results:

            self.assertEqual(result["error"], "not_allowed", result)
            self.assertIn("read-only", result["message"])

        for table in (world.incidents, world.issues, world.roster):

            for call in ("put_item", "update_item", "delete_item"):
                self.assertEqual(table.count(call), 0)

    def test_reads_still_work(self):

        with mock.patch("services.common.READ_ONLY_MODE", True):

            self.assertTrue(World().incident_service.get_incident("INC0000258")["ok"])


class CategorizationTests(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.svc = self.world.categories

    def test_get_issue_formats(self):

        for spelling in ("Issue-1", "Issue1", "issue 1", "1"):
            self.assertEqual(self.svc.get_issue(spelling)["issue_id"], "Issue-1", spelling)

    def test_fields_only(self):

        result = self.svc.get_issue("Issue-1", ["team", "type of request"])

        self.assertEqual(
            result["issue"],
            {"issue_id": "Issue-1", "team_id": "Identity Operations", "issue_type": "Service Request"},
        )

    def test_empty_standard_request_id_is_reported(self):

        result = self.svc.get_issue("Issue-1", ["standard request id"])

        self.assertEqual(result["issue"]["standard_request_id"], None)

    def test_not_found(self):
        self.assertEqual(self.svc.get_issue("Issue-99")["error"], "not_found")

    def test_search_by_words_and_filters(self):

        self.assertEqual(self.svc.search_issues(text="leaver")["total"], 1)
        self.assertEqual(self.svc.search_issues(category_name="identity lifecycle")["total"], 2)
        self.assertEqual(self.svc.search_issues(team_id="infrastructure")["issues"][0]["issue_id"], "Issue-3")

    def test_search_rows_are_slim(self):

        row = self.svc.search_issues(text="account")["issues"][0]

        self.assertNotIn("business_impact", row)
        self.assertNotIn("standard_request_id", row)
        self.assertNotIn("category_description", row)

    def test_inactive_issues_hidden_by_default(self):

        self.world.issues.items["Issue-3"]["active"] = "No"
        self.svc._cache.clear()

        self.assertEqual(self.svc.search_issues(team_id="Infrastructure")["total"], 0)
        self.assertEqual(self.svc.search_issues(team_id="Infrastructure", include_inactive=True)["total"], 1)

    def test_category_tree(self):

        result = self.svc.list_categories()

        names = [item["name"] for item in result["categories"]]

        self.assertEqual(names, ["Identity Lifecycle", "Infrastructure"])
        self.assertEqual(result["categories"][0]["sub_categories"], ["Joiner", "Leaver"])

    def test_table_is_read_once_within_the_cache_window(self):

        self.svc.search_issues(text="a")
        self.svc.search_issues(text="b")
        self.svc.list_categories()

        self.assertEqual(self.world.issues.count("scan"), 2)  # one full read = 2 pages

    def test_update_records_audit_fields_and_validates(self):

        result = self.svc.update_issue("Issue-1", {"impact": "high"}, actor="tester")

        self.assertEqual(result["changes"], {"impact": {"from": "Low", "to": "High"}})

        stored = self.world.issues.items["Issue-1"]

        self.assertEqual(stored["updated_by"], "tester")
        self.assertEqual(stored["updated_on"], today().isoformat())

        self.assertEqual(self.svc.update_issue("Issue-1", {"impact": "Extreme"})["error"], "invalid_value")
        self.assertEqual(self.svc.update_issue("Issue-1", {"issue_id": "Issue-5"})["error"], "invalid_field")
        self.assertEqual(self.svc.update_issue("Issue-99", {"impact": "High"})["error"], "not_found")

    def test_update_is_visible_to_the_next_search(self):

        self.svc.search_issues(text="account")

        self.svc.update_issue("Issue-1", {"issue": "Joiner account setup"})

        self.assertEqual(self.svc.search_issues(text="joiner account")["total"], 1)

    def test_active_flag_keeps_the_yes_no_text(self):

        self.svc.update_issue("Issue-1", {"active": "no"})

        self.assertEqual(self.world.issues.items["Issue-1"]["active"], "No")

        self.svc.update_issue("Issue-1", {"active": "true"})

        self.assertEqual(self.world.issues.items["Issue-1"]["active"], "Yes")

    def test_active_flag_stays_boolean_when_the_column_is_boolean(self):

        self.world.issues.items["Issue-1"]["active"] = True

        self.svc.update_issue("Issue-1", {"active": "no"})

        self.assertIs(self.world.issues.items["Issue-1"]["active"], False)


class RosterReadTests(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.svc = self.world.people
        self.today = today()

    def test_search_returns_one_entry_per_person(self):

        result = self.svc.search_roster("rohit")

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["employees"][0]["ref"], "emp:1")

    def test_search_never_returns_availability(self):

        row = self.svc.search_roster()["employees"][0]

        self.assertNotIn("days_availability", row)

    def test_directory_is_read_once(self):

        self.svc.search_roster("rohit")
        self.svc.search_roster("priya")
        self.svc.get_roster_entry("Priya Nair")

        self.assertEqual(self.world.roster.count("scan"), 2)

    def test_lookup_by_name_email_number_and_ref(self):

        for reference in ("Rohit Malhotra", "rohit", "emp:1", "rohit.malhotra@abc.com", "1", "11"):

            self.assertEqual(self.svc.get_roster_entry(reference)["ref"], "emp:1", reference)

    def test_entry_shows_this_months_row_and_a_compact_summary(self):

        entry = self.svc.get_roster_entry("Rohit")["employee"]

        self.assertNotIn("days_availability", entry)
        self.assertEqual(entry["shift"], "S1")
        self.assertEqual(entry["employee_id"], "rohit.malhotra@abc.com")
        self.assertEqual(entry["month"], self.today.month)

        summary = entry["availability_summary"]

        self.assertEqual(summary["month"], f"{self.today.year}-{self.today.month:02d}")
        self.assertEqual(summary["off_days"], [day for day in (5, 6) if day != self.today.day])

    def test_only_requested_fields(self):

        result = self.svc.get_roster_entry("Rohit", ["shift", "email"])

        self.assertEqual(
            result["employee"],
            {"employee_name": "Rohit Malhotra", "shift": "S1", "employee_id": "rohit.malhotra@abc.com"},
        )

    def test_multiple_matches_ask_which(self):

        world = World(extra_people=[second_rohit()])

        result = world.people.get_roster_entry("Rohit")

        self.assertEqual(result["error"], "ambiguous")
        self.assertEqual({option["ref"] for option in result["options"]}, {"emp:1", "emp:2"})

        for reference in ("Rohit Sharma", "rohit.sharma@abc.com", "emp:2"):
            self.assertEqual(world.people.get_roster_entry(reference)["ref"], "emp:2", reference)

    def test_not_found(self):
        self.assertEqual(self.svc.get_roster_entry("Nobody")["error"], "not_found")

    def test_team_roster_by_id_and_name(self):

        for team in ("Access Management", "Access Management Team", "access"):

            result = self.svc.get_team_roster(team)

            self.assertEqual(result["total"], 2, team)
            self.assertEqual({row["employee_name"] for row in result["employees"]}, {"Rohit Malhotra", "Priya Nair"})

        self.assertEqual(self.svc.get_team_roster("Nope")["error"], "not_found")

    def test_availability_today_is_y_or_n(self):

        self.assertEqual(
            self.svc.get_employee_availability("Rohit", "today")["availability"],
            {self.today.isoformat(): "Y"},
        )
        self.assertEqual(
            self.svc.get_employee_availability("Priya", "today")["availability"],
            {self.today.isoformat(): "N"},
        )

    def test_availability_reads_the_day_of_month_of_the_right_monthly_row(self):

        previous = previous_month()

        result = self.svc.get_employee_availability(
            "Rohit", f"{previous.year}-{previous.month:02d}-02"
        )

        self.assertEqual(list(result["availability"].values()), ["N"])

        # ... while this month's row answers this month.
        self.assertEqual(
            list(self.svc.get_employee_availability("Rohit", this_month_date(5))["availability"].values()),
            ["Y" if self.today.day == 5 else "N"],
        )

    def test_a_range_can_cross_months(self):

        previous = previous_month()

        first = f"{previous.year}-{previous.month:02d}-28"

        result = self.svc.get_employee_availability("Rohit", first, this_month_date(2))

        # Last month has a row (nothing recorded for day 28); this month is read from its own row.
        self.assertIsNone(result["availability"][first])
        self.assertEqual(result["availability"][this_month_date(1)], "Y")
        self.assertNotIn("no_roster_for", result)

    def test_month_without_a_roster_row_is_reported(self):

        result = self.svc.get_employee_availability("Rohit", "2099-01-01")

        self.assertEqual(result["availability"], {"2099-01-01": None})
        self.assertEqual(result["no_roster_for"], ["2099-01"])

    def test_range_limit(self):
        self.assertEqual(self.svc.get_employee_availability("Rohit", "2026-01-01", "2026-12-31")["error"], "invalid_value")

    def test_team_availability(self):

        for team in ("Access Management", "Access Management Team"):

            result = self.svc.get_team_availability(team, "today")

            self.assertEqual(
                {row["name"]: row["availability"] for row in result["members"]},
                {"Rohit Malhotra": "Y", "Priya Nair": "N"},
                team,
            )

    def test_team_without_rows_for_the_month(self):

        result = self.svc.get_team_availability("Access Management", "2099-01-01")

        self.assertEqual(result["error"], "not_found")
        self.assertIn("2099-01", result["message"])


class RosterWriteTests(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.svc = self.world.people
        self.today = today()

    def stored(self, uid):
        return self.world.roster.items[uid]

    # ---- availability ----------------------------------------------

    def test_set_availability_changes_only_that_day(self):

        date = this_month_date(20)

        result = self.svc.set_employee_availability("Rohit", {date: "leave"}, actor="tester")

        self.assertEqual(result["changes"], {date: {"from": "Y", "to": "N"}})

        days = self.stored("1")["days_availability"]

        self.assertEqual(days["20"], "N")
        self.assertEqual(days["19"], "Y")
        self.assertEqual(len(days), len(month_days()))
        self.assertEqual(self.stored("1")["updated_by"], "tester")
        self.assertEqual(self.stored("1")["updated_on"], self.today.isoformat())

    def test_wording_is_mapped_to_y_and_n(self):

        date = this_month_date(21)

        for wording, expected in (("Available", "Y"), ("working", "Y"), ("off", "N"), ("Leave", "N"), ("y", "Y"), (True, "Y"), (False, "N")):

            self.svc.set_employee_availability("Rohit", {date: wording})

            self.assertEqual(self.stored("1")["days_availability"]["21"], expected, wording)

    def test_unknown_wording_is_rejected(self):

        result = self.svc.set_employee_availability("Rohit", {this_month_date(21): "Sailing"})

        self.assertEqual(result["error"], "invalid_value")
        self.assertIn("Y (available)", result["message"])

    def test_previous_month_writes_to_the_previous_monthly_row(self):

        previous = previous_month()

        self.svc.set_employee_availability("Rohit", {f"{previous.year}-{previous.month:02d}-03": "N"})

        self.assertEqual(self.stored("11")["days_availability"]["3"], "N")
        self.assertEqual(self.stored("1")["days_availability"]["3"], "Y")

    def test_month_without_a_row_is_refused_and_nothing_is_written(self):

        result = self.svc.set_employee_availability(
            "Rohit",
            {this_month_date(20): "N", "2099-01-01": "N"},
        )

        self.assertEqual(result["error"], "not_found")
        self.assertIn("2099-01", result["message"])
        self.assertEqual(self.world.roster.count("update_item"), 0)

    def test_a_row_without_a_map_gets_one(self):

        del self.world.roster.items["4"]["days_availability"]

        result = self.svc.set_employee_availability("Varun", {"today": "N"})

        self.assertTrue(result["ok"])
        self.assertEqual(self.stored("4")["days_availability"], {str(self.today.day): "N"})

    def test_relative_dates(self):

        result = self.svc.set_employee_availability("Rohit", {"today": "leave"})

        self.assertEqual(list(result["changes"]), [self.today.isoformat()])

    def test_team_availability_updates_each_members_row(self):

        result = self.svc.set_team_availability("Access Management", "today", "leave", actor="tester")

        self.assertEqual((result["ok"], result["updated"]), (True, 2))

        day = str(self.today.day)

        self.assertEqual(self.stored("1")["days_availability"][day], "N")
        self.assertEqual(self.stored("3")["days_availability"][day], "N")
        self.assertEqual(self.stored("4")["days_availability"][day], "Y")
        self.assertEqual(self.stored("11")["days_availability"], {"1": "Y", "2": "N"})

    def test_a_failing_member_is_reported_not_hidden(self):

        real = self.world.roster.update_item

        def flaky(**kwargs):

            if kwargs["Key"]["UID"] == "3":
                raise client_error("ProvisionedThroughputExceededException")

            return real(**kwargs)

        self.world.roster.update_item = flaky

        result = self.svc.set_team_availability("Access Management", "today", "leave")

        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], ["Priya Nair"])
        self.assertIn("Priya Nair", result["message"])

    # ---- row fields -------------------------------------------------

    def test_shift_and_level_change_this_months_row_only(self):

        result = self.svc.update_roster_entry("Rohit", {"shift": "s2", "level": "l3"}, actor="tester")

        self.assertEqual(result["changes"]["shift"], {"from": "S1", "to": "S2"})
        self.assertEqual(result["month"], f"{self.today.year}-{self.today.month:02d}")
        self.assertEqual((self.stored("1")["shift"], self.stored("1")["level"]), ("S2", "L3"))
        self.assertEqual((self.stored("11")["shift"], self.stored("11")["level"]), ("S1", "L2"))
        self.assertEqual(self.stored("1")["updated_by"], "tester")

    def test_another_month_can_be_targeted(self):

        previous = previous_month()

        self.svc.update_roster_entry("Rohit", {"shift": "S2"}, year=previous.year, month=previous.month)

        self.assertEqual(self.stored("11")["shift"], "S2")
        self.assertEqual(self.stored("1")["shift"], "S1")

        self.assertEqual(
            self.svc.update_roster_entry("Rohit", {"shift": "S2"}, year=2099, month=1)["error"],
            "not_found",
        )

    def test_identity_changes_apply_to_every_month(self):

        self.svc.update_roster_entry("Rohit", {"employee_name": "Rohit M Malhotra"})

        self.assertEqual(self.stored("1")["employee_name"], "Rohit M Malhotra")
        self.assertEqual(self.stored("11")["employee_name"], "Rohit M Malhotra")

    def test_moving_team_copies_the_team_details(self):

        self.svc.update_roster_entry("Rohit", {"team_id": "infrastructure"})

        self.assertEqual(self.stored("1")["team_name"], "Infrastructure Team")
        self.assertEqual(self.stored("1")["team_email_id"], "infra@abc.com")

    def test_active_keeps_the_yes_no_text(self):

        self.svc.update_roster_entry("Rohit", {"active": "no"})

        self.assertEqual(self.stored("1")["active"], "No")

    def test_invalid_updates(self):

        self.assertEqual(self.svc.update_roster_entry("Rohit", {"UID": "X"})["error"], "invalid_field")
        self.assertEqual(self.svc.update_roster_entry("Rohit", {"team_id": "Mars"})["error"], "invalid_value")
        self.assertEqual(self.svc.update_roster_entry("Rohit", {"shift": "Lunar"})["error"], "invalid_value")
        self.assertEqual(self.svc.update_roster_entry("Rohit", {"employee_id": "not-an-email"})["error"], "invalid_value")
        self.assertEqual(self.world.roster.count("update_item"), 0)

    # ---- create -----------------------------------------------------

    def test_create_lists_what_is_missing(self):

        result = self.svc.create_roster_entry({"employee_name": "Asha Rao"})

        self.assertEqual(result["missing"], ["employee_id", "emp_id", "team_id"])
        self.assertEqual(self.world.roster.count("put_item"), 0)

    def test_create_new_employee(self):

        result = self.svc.create_roster_entry(
            {
                "employee_name": "Asha Rao",
                "employee_id": "asha.rao@abc.com",
                "emp_id": "9",
                "team_id": "access management",
                "shift": "s1",
            },
            actor="tester",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["ref"], "emp:9")

        record = self.stored("12")  # UIDs are text; the next free one after "11"

        self.assertEqual(record["employee_name"], "Asha Rao")
        self.assertEqual(record["team_name"], "Access Management Team")
        self.assertEqual(record["shift"], "S1")
        self.assertEqual((record["year"], record["month"]), (self.today.year, self.today.month))
        self.assertEqual(record["active"], "Yes")
        self.assertEqual(record["days_availability"], {})
        self.assertEqual(record["created_on"], self.today.isoformat())
        self.assertEqual(record["updated_by"], "tester")

        self.assertEqual(self.svc.get_roster_entry("Asha")["ref"], "emp:9")

    def test_numeric_uids_stay_numeric(self):

        world = World()

        world.roster.items = {int(uid): {**row, "UID": int(uid)} for uid, row in world.roster.items.items()}
        world.people._refresh()

        world.people.create_roster_entry(
            {"employee_name": "Asha Rao", "employee_id": "asha.rao@abc.com", "emp_id": "9", "team_id": "Access Management"}
        )

        self.assertIn(12, world.roster.items)

    def test_new_month_for_an_existing_employee_only_needs_the_number(self):

        month = 1 if self.today.month == 12 else self.today.month + 1
        year = self.today.year + 1 if self.today.month == 12 else self.today.year

        result = self.svc.create_roster_entry({"emp_id": "1", "year": year, "month": month})

        self.assertTrue(result["ok"])

        record = self.stored("12")

        self.assertEqual(
            (record["employee_name"], record["employee_id"], record["team_id"], record["shift"]),
            ("Rohit Malhotra", "rohit.malhotra@abc.com", "Access Management", "S1"),
        )
        self.assertEqual((record["year"], record["month"]), (year, month))

    def test_duplicate_month_is_refused(self):

        result = self.svc.create_roster_entry({"emp_id": "1"})

        self.assertEqual(result["error"], "conflict")
        self.assertEqual(self.world.roster.count("put_item"), 0)

    def test_create_validation(self):

        base = {"employee_name": "Asha", "employee_id": "asha@abc.com", "emp_id": "9", "team_id": "Access Management"}

        self.assertEqual(self.svc.create_roster_entry({**base, "team_id": "Mars"})["error"], "invalid_value")
        self.assertEqual(self.svc.create_roster_entry({**base, "employee_id": "asha"})["error"], "invalid_value")
        self.assertEqual(self.svc.create_roster_entry({**base, "month": 13})["error"], "invalid_value")

    # ---- delete -----------------------------------------------------

    def test_delete_removes_this_months_row_only(self):

        result = self.svc.delete_roster_entry("Rohit Malhotra", actor="tester")

        self.assertTrue(result["ok"])
        self.assertEqual(result["month"], f"{self.today.year}-{self.today.month:02d}")
        self.assertNotIn("1", self.world.roster.items)
        self.assertIn("11", self.world.roster.items)

    def test_delete_takes_the_only_row_when_no_row_is_current(self):

        self.svc.delete_roster_entry("Rohit")

        result = self.svc.delete_roster_entry("Rohit")

        self.assertTrue(result["ok"])
        self.assertEqual(self.svc.get_roster_entry("Rohit")["error"], "not_found")

    def test_delete_asks_which_month_when_it_cannot_tell(self):

        self.svc.delete_roster_entry("Rohit")

        self.svc.create_roster_entry({"emp_id": "1", "year": 2099, "month": 1})

        result = self.svc.delete_roster_entry("Rohit")

        self.assertEqual(result["error"], "ambiguous_month")
        self.assertIn("2099-01", result["message"])
        self.assertIn("11", self.world.roster.items)

        self.assertTrue(self.svc.delete_roster_entry("Rohit", year=2099, month=1)["ok"])

    def test_delete_refuses_ambiguity(self):

        world = World(extra_people=[second_rohit()])

        result = world.people.delete_roster_entry("Rohit")

        self.assertEqual(result["error"], "ambiguous")
        self.assertIn("1", world.roster.items)
        self.assertIn("2", world.roster.items)

    def test_delete_can_be_disabled(self):

        with mock.patch("services.roster_service.ENABLE_DELETE_OPERATIONS", False):

            result = self.svc.delete_roster_entry("Rohit")

        self.assertEqual(result["error"], "not_allowed")
        self.assertIn("1", self.world.roster.items)


class KnowledgeBaseTests(unittest.TestCase):

    def hits(self):

        return [
            {"score": 0.82, "content": {"text": "Incident INC0000123: VPN gateway timeouts fixed by restarting the gateway."}},
            {"score": 0.80, "content": {"text": "Incident INC0000123:  VPN gateway timeouts fixed by restarting the gateway."}},
            {"score": 0.10, "content": {"text": "Unrelated low-score chunk"}},
            {"score": 0.55, "content": {"text": "Issue-3 Database outage. " + "long " * 400}},
            {"score": 0.50, "content": {"text": ""}},
        ]

    def test_search_is_routed_to_one_data_source(self):

        world = World(kb_results=self.hits())

        world.knowledge.search("vpn", "incident")

        request = world.kb.requests[0]

        configuration = request["retrievalConfiguration"]["vectorSearchConfiguration"]

        self.assertEqual(
            configuration["filter"],
            {"equals": {"key": "x-amz-bedrock-kb-data-source-id", "value": "ds-incident"}},
        )

    def test_all_sources_is_unfiltered(self):

        world = World(kb_results=self.hits())

        world.knowledge.search("vpn", "all")

        configuration = world.kb.requests[0]["retrievalConfiguration"]["vectorSearchConfiguration"]

        self.assertNotIn("filter", configuration)

    def test_results_are_filtered_deduplicated_and_truncated(self):

        world = World(kb_results=self.hits())

        result = world.knowledge.search("vpn", "incident", 5)

        self.assertEqual(result["count"], 2)
        self.assertEqual([row["ref"] for row in result["results"]], ["INC0000123", "Issue-3"])
        self.assertTrue(all(len(row["text"]) <= 600 for row in result["results"]))

    def test_result_count_is_bounded(self):

        world = World(kb_results=self.hits() * 5)

        self.assertLessEqual(len(world.knowledge.search("x", "all", 99)["results"]), 8)
        self.assertEqual(len(world.knowledge.search("x", "all", 1)["results"]), 1)

        request = world.kb.requests[-1]

        self.assertLessEqual(request["retrievalConfiguration"]["vectorSearchConfiguration"]["numberOfResults"], 5)

    def test_query_is_not_padded(self):

        world = World(kb_results=self.hits())

        world.knowledge.search("  vpn   keeps disconnecting ", "incident")

        self.assertEqual(world.kb.requests[0]["retrievalQuery"]["text"], "vpn keeps disconnecting")

    def test_no_results(self):

        result = World(kb_results=[]).knowledge.search("anything")

        self.assertEqual(result["count"], 0)
        self.assertIn("note", result)

    def test_validation(self):

        knowledge = World().knowledge

        self.assertEqual(knowledge.search("")["error"], "missing_fields")
        self.assertEqual(knowledge.search("x", "everything")["error"], "invalid_value")

    def test_retrieval_failure_is_friendly(self):

        world = World()

        world.kb.fail_with = client_error("InternalServerException", "opensearch cluster red: secret-detail")

        result = world.knowledge.search("vpn")

        self.assertFalse(result["ok"])
        self.assertNotIn("opensearch", result["message"])
        self.assertNotIn("secret-detail", result["message"])


class AssignmentTests(unittest.TestCase):

    def test_issue_to_team_to_availability(self):

        world = World()

        result = world.assignment.find_handlers("Issue-3")

        self.assertEqual(result["team_id"], "Infrastructure")
        self.assertEqual(
            {member["name"]: member["availability"] for member in result["members"]},
            {"Varun Nambiar": "Y"},
        )

    def test_by_category_words_when_names_line_up(self):

        world = World()

        for row in world.issues.items.values():

            if row["team_id"] == "Identity Operations":
                row["team_id"] = "Access Management"

        world.categories._cache.clear()

        result = world.assignment.find_handlers("Identity Lifecycle")

        self.assertEqual(result["team_id"], "Access Management")
        self.assertEqual(
            {member["name"]: member["availability"] for member in result["members"]},
            {"Rohit Malhotra": "Y", "Priya Nair": "N"},
        )

    def test_a_team_missing_from_the_roster_is_reported_not_hidden(self):
        """The matrix says 'Identity Operations'; the roster has no such team."""

        result = World().assignment.find_handlers("Identity Lifecycle")

        self.assertTrue(result["ok"])
        self.assertEqual(result["team_id"], "Identity Operations")
        self.assertIn("No team matches", result["members_error"])
        self.assertNotIn("members", result)

    def test_several_teams_ask_which(self):

        world = World()

        world.issues.items["Issue-2"]["team_id"] = "Infrastructure"
        world.categories._cache.clear()

        result = world.assignment.find_handlers("Identity Lifecycle")

        self.assertTrue(result["ambiguous"])

    def test_no_match(self):

        self.assertEqual(World().assignment.find_handlers("zzz nothing")["error"], "not_found")


class AwsFailureTests(unittest.TestCase):
    """AWS problems become short user-safe messages; nothing raw leaks."""

    CASES = {
        "AccessDeniedException": "access_denied",
        "ProvisionedThroughputExceededException": "throttled",
        "ResourceNotFoundException": "unavailable",
        "InternalServerError": "unavailable",
    }

    def test_errors_are_mapped_and_sanitized(self):

        for code, kind in self.CASES.items():

            world = World()

            world.incidents.fail_with = client_error(code, "arn:aws:dynamodb:...:table/secret-table denied")

            for result in (
                world.incident_service.get_incident("INC0000258"),
                world.incident_service.search_incidents(),
                world.incident_service.update_incident("INC0000258", {"priority": "P2"}),
            ):

                self.assertFalse(result["ok"], code)
                self.assertEqual(result["error"], kind, code)
                self.assertNotIn("secret-table", result["message"])
                self.assertNotIn("arn:", result["message"])

    def test_roster_failures_are_sanitized_too(self):

        world = World()

        world.roster.fail_with = client_error("AccessDeniedException", "arn:aws:dynamodb:...:table/secret-roster")

        for result in (
            world.people.get_roster_entry("Rohit"),
            world.people.set_employee_availability("Rohit", {"today": "N"}),
        ):

            self.assertEqual(result["error"], "access_denied")
            self.assertNotIn("secret-roster", result["message"])

    def test_conditional_write_failure(self):

        world = World()

        original = world.incidents.update_item

        def lost_race(**kwargs):
            world.incidents.items.pop("INC0000258")
            return original(**kwargs)

        world.incidents.update_item = lost_race

        result = world.incident_service.update_incident("INC0000258", {"state": "New"})

        self.assertEqual(result["error"], "conflict")

    def test_unexpected_exception_is_contained(self):

        world = World()

        def broken(**kwargs):
            raise RuntimeError("secret internals")

        world.incidents.get_item = broken

        result = world.incident_service.get_incident("INC0000258")

        self.assertEqual(result["error"], "internal")
        self.assertNotIn("secret", result["message"])

    def test_missing_table_configuration(self):

        from services.incident_service import IncidentService

        with mock.patch("services.incident_service.INCIDENT_TABLE_NAME", None):

            result = IncidentService().get_incident("INC1")

        self.assertEqual(result["error"], "unavailable")


class ValueHelperTests(unittest.TestCase):

    def test_resolve_choice(self):

        allowed = ["P1-Critical", "P2-High", "P3-Medium"]

        self.assertEqual(resolve_choice("P2", allowed, "priority"), "P2-High")
        self.assertEqual(resolve_choice("p3-medium", allowed, "priority"), "P3-Medium")
        self.assertEqual(resolve_choice("critical", allowed, "priority"), "P1-Critical")
        self.assertEqual(resolve_choice("anything", [], "priority"), "anything")

        with self.assertRaises(ServiceError):
            resolve_choice("P9", allowed, "priority")

        with self.assertRaises(ServiceError) as caught:
            resolve_choice("p2", ["P2-High", "P2-Medium"], "priority")

        self.assertEqual(caught.exception.error, "ambiguous_value")

    def test_resolve_date(self):

        self.assertEqual(resolve_date("today"), today().isoformat())
        self.assertEqual(resolve_date("2026-09-30"), "2026-09-30")

        with self.assertRaises(ServiceError):
            resolve_date("next tuesday")


if __name__ == "__main__":
    unittest.main()
