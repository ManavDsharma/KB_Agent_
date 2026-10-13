import unittest

from memory.state import SessionState
from router import DeterministicRouter, extract_employee_query


def make_state(**focus):
    """A session with some entities already discussed."""

    state = SessionState("actor", "session")

    state.begin_turn()

    for type, (entity_id, key) in focus.items():
        state.set_focus(type, entity_id, key)

    return state


def route(text, state=None):

    state = state or make_state()

    state.begin_turn()

    intent = DeterministicRouter.route(text, state)

    if intent.pending:
        state.set_pending(**intent.pending)

    return intent


class ExplicitIdentifierTests(unittest.TestCase):

    def test_issue_forms_route_to_full_view(self):
        """Regression: the word 'issue' inside 'Issue-9' used to be read as a field."""

        for text in ("Show me Issue-9", "Show me Issue9", "Show me Issue 9", "What is Issue-1?"):

            intent = route(text)

            self.assertEqual(intent.route, "entity_view", text)
            self.assertEqual(intent.entity_type, "issue")

    def test_issue_field(self):

        intent = route("What is the impact of Issue-9?")

        self.assertEqual((intent.route, intent.fields), ("entity_fields", ["impact"]))
        self.assertEqual(intent.entity_ref, "Issue-9")

    def test_definition_question_reads_the_record(self):

        self.assertEqual(route("What does Issue-1 represent?").route, "entity_view")

    def test_incident_forms(self):

        for text in ("Show me INC0000258", "Show me INC-0000258", "Show me INC 0000258"):

            intent = route(text)

            self.assertEqual((intent.route, intent.entity_type), ("entity_view", "incident"), text)

    def test_request_ids(self):

        intent = route("What is the status of REQ0000162?")

        self.assertEqual((intent.route, intent.fields), ("entity_fields", ["state"]))
        self.assertEqual(intent.entity_ref, "REQ0000162")

    def test_field_questions(self):

        self.assertEqual(route("Who is assigned to INC0000258?").fields, ["assigned_to"])
        self.assertEqual(route("INC0000258 priority").fields, ["priority"])
        self.assertEqual(
            route("Is INC0000258 breached?").fields,
            ["has_assignment_sla_breach", "has_resolution_sla_breach"],
        )

    def test_reasoning_goes_to_the_model(self):

        for text in (
            "Why did INC0000258 breach SLA?",
            "How do I fix INC0000258?",
            "Show me incidents similar to INC0000258",
        ):
            self.assertEqual(route(text).route, "llm", text)

    def test_explain_why_is_reasoning_not_a_definition(self):

        self.assertEqual(route("Explain why INC0000258 is in that state").route, "llm")
        self.assertEqual(route("Open INC0000259 and explain why that is the right state").route, "llm")
        self.assertEqual(route("Explain Issue-1").route, "entity_view")

    def test_two_entities_go_to_the_model(self):
        self.assertEqual(route("Compare INC0000258 with Issue-1").route, "llm")

    def test_sentence_that_merely_names_a_record(self):
        self.assertEqual(route("INC0000258 was mentioned in the meeting yesterday by Priya").route, "llm")

    def test_words_containing_update_verbs_do_not_trigger_updates(self):

        self.assertEqual(route("Show me the asset details for Issue-9").route, "entity_view")
        self.assertEqual(route("What was updated on INC0000258?").route, "llm")


class ListRouteTests(unittest.TestCase):

    def test_sla_breaches(self):

        self.assertEqual(route("List SLA breaches").route, "sla_list")
        self.assertEqual(route("Show all breached incidents").query, "all")
        self.assertEqual(route("Show me assignment SLA breaches").query, "assignment")

    def test_incidents_by_date(self):

        for text, date in (
            ("incidents today", "today"),
            ("Show incidents on 2026-09-30", "2026-09-30"),
            ("yesterday's incidents", "yesterday"),
        ):
            intent = route(text)

            self.assertEqual((intent.route, intent.date), ("incident_list", date), text)

    def test_category_list(self):
        self.assertEqual(route("list all categories").route, "category_list")

    def test_filtered_lists_go_to_the_model(self):
        self.assertEqual(route("Show all incidents assigned to Infrastructure").route, "llm")


class ContextTests(unittest.TestCase):

    def setUp(self):
        self.state = make_state(incident=("INC0000258", "INC258"))

    def test_pronoun_follow_ups(self):

        cases = {
            "Who is assigned to it?": ["assigned_to"],
            "What is its priority?": ["priority"],
            "What is the SLA?": ["assignment_sla", "resolution_sla"],
            "Is it breached?": ["has_assignment_sla_breach", "has_resolution_sla_breach"],
            "Who opened it?": ["opened_by"],
            "What about the SLA?": ["assignment_sla", "resolution_sla"],
            "and the priority?": ["priority"],
        }

        for text, fields in cases.items():

            intent = route(text, self.state)

            self.assertEqual(intent.route, "entity_fields", text)
            self.assertEqual(intent.fields, fields, text)
            self.assertEqual(intent.entity_ref, "INC0000258", text)
            self.assertTrue(intent.from_context)

    def test_show_it(self):

        intent = route("Show it", self.state)

        self.assertEqual((intent.route, intent.entity_ref), ("entity_view", "INC0000258"))

    def test_the_incident_phrase(self):
        self.assertEqual(route("Show me the incident.", self.state).entity_ref, "INC0000258")

    def test_explicit_id_overrides_context(self):

        intent = route("Who opened INC0000999?", self.state)

        self.assertEqual(intent.entity_ref, "INC0000999")
        self.assertFalse(intent.from_context)

    def test_field_picks_the_right_entity_type(self):
        """'impact' only exists on issues, so it resolves to the issue even
        though the incident was discussed last."""

        state = make_state(issue=("Issue-1", "issue1"))
        state.begin_turn()
        state.set_focus("incident", "INC0000258", "INC258")

        intent = route("What is its impact?", state)

        self.assertEqual((intent.entity_type, intent.entity_ref), ("issue", "Issue-1"))

    def test_latest_entity_wins_for_shared_fields(self):

        state = make_state(issue=("Issue-1", "issue1"))
        state.begin_turn()
        state.set_focus("incident", "INC0000258", "INC258")

        self.assertEqual(route("What is its urgency?", state).entity_type, "incident")

    def test_issue_follow_ups(self):

        state = make_state(issue=("Issue-1", "issue1"))

        self.assertEqual(route("Which team handles it?", state).fields, ["team_id"])
        self.assertEqual(route("What type of request is it?", state).fields, ["issue_type"])

    def test_generic_it_in_open_questions_goes_to_the_model(self):
        self.assertEqual(route("How do I fix it?", self.state).route, "llm")


class NoContextTests(unittest.TestCase):

    def test_show_the_incident(self):

        intent = route("Show me the incident.")

        self.assertEqual(intent.route, "clarify")
        self.assertIn("ID", intent.clarification)

    def test_pronoun_without_context(self):

        for text in ("What is its priority?", "Who opened it?", "Is it breached?"):

            intent = route(text)

            self.assertEqual(intent.route, "clarify", text)
            self.assertIn("ticket ID", intent.clarification)

        self.assertIn("issue ID", route("What is its impact?").clarification)

    def test_update_the_ticket(self):

        intent = route("Update the ticket.")

        self.assertEqual(intent.clarification, "Which ticket would you like to update?")

    def test_set_priority_without_target(self):

        intent = route("Set the priority to P2.")

        self.assertEqual(intent.clarification, "Which incident would you like to update?")
        self.assertEqual(intent.pending["field"], "priority")
        self.assertEqual(intent.pending["changes"], {"priority": "P2"})

    def test_update_it_without_context(self):
        self.assertEqual(route("Update it.").clarification, "Which incident would you like to update?")


class UpdateTests(unittest.TestCase):

    def setUp(self):
        self.state = make_state(incident=("INC0000258", "INC258"))

    def test_clear_updates_execute(self):

        cases = {
            "Update INC0000258 priority to P2": {"priority": "P2"},
            "Change its priority to P2": {"priority": "P2"},
            "Set priority to P2.": {"priority": "P2"},
            "Set the state of INC0000258 to Closed": {"state": "Closed"},
            "Assign it to Rohit": {"assigned_to": "Rohit"},
            "Assign INC0000258 to rohit.m@abc.com": {"assigned_to": "rohit.m@abc.com"},
            "close it": {"state": "Closed"},
        }

        for text, changes in cases.items():

            intent = route(text, self.state)

            self.assertEqual(intent.route, "entity_update", text)
            self.assertEqual(intent.changes, changes, text)
            self.assertEqual(intent.entity_ref, "INC0000258", text)

    def test_value_keeps_its_case(self):
        self.assertEqual(route("Set short description of it to Printer Jam on Floor 3", self.state).changes,
                         {"short_description": "Printer Jam on Floor 3"})

    def test_update_it_asks_what(self):

        intent = route("Update it.", self.state)

        self.assertEqual(intent.clarification, "What would you like to update for INC0000258?")
        self.assertEqual(self.state.pending["expects"], "field")

    def test_update_with_id_only(self):
        self.assertEqual(
            route("Update INC0000258.").clarification,
            "What would you like to update for INC0000258?",
        )

    def test_field_without_value_asks_for_the_value(self):

        intent = route("Update INC0000258 priority")

        self.assertEqual(intent.clarification, "What should the priority be for INC0000258?")

    def test_several_changes_go_to_the_planner(self):

        for text in (
            "Set priority to P2 and assign it to Rohit",
            "Set state to Closed and urgency to Low",
        ):
            intent = route(text, self.state)

            self.assertEqual(intent.route, "plan", text)
            self.assertEqual(intent.query, text)
            self.assertEqual(intent.entity_ref, "INC0000258", text)

    def test_ambiguous_field_goes_to_the_model(self):
        self.assertEqual(route("Update the sla of it to 60", self.state).route, "llm")

    def test_stale_context_is_not_modified(self):
        """A target discussed long ago must not be changed by a stray 'it'."""

        for _ in range(20):
            self.state.begin_turn()

        intent = route("Change its priority to P2", self.state)

        self.assertEqual(intent.route, "clarify")

    def test_reading_stale_context_is_still_fine(self):

        for _ in range(20):
            self.state.begin_turn()

        self.assertEqual(route("What is its priority?", self.state).route, "entity_fields")

    def test_create_goes_to_the_model(self):

        self.assertEqual(route("Create a new incident for a database failure").route, "llm")


class PendingTests(unittest.TestCase):

    def test_update_it_then_set_priority(self):

        state = make_state(incident=("INC0000258", "INC258"))

        route("Update it.", state)

        intent = route("Set priority to P2.", state)

        self.assertEqual((intent.route, intent.entity_ref, intent.changes), ("entity_update", "INC0000258", {"priority": "P2"}))

    def test_field_then_value(self):

        state = make_state(incident=("INC0000258", "INC258"))

        route("Update INC0000258 priority", state)

        intent = route("P2", state)

        self.assertEqual((intent.route, intent.changes), ("entity_update", {"priority": "P2"}))

    def test_target_then_completes_the_update(self):

        state = make_state()

        route("Set the priority to P2.", state)

        intent = route("INC0000258", state)

        self.assertEqual(
            (intent.route, intent.entity_ref, intent.changes),
            ("entity_update", "INC0000258", {"priority": "P2"}),
        )

    def test_assign_answer(self):

        state = make_state(incident=("INC0000258", "INC258"))

        route("Update it.", state)

        self.assertEqual(route("Assign it to Rohit", state).changes, {"assigned_to": "Rohit"})

    def test_new_command_replaces_a_pending_value(self):

        state = make_state(incident=("INC0000258", "INC258"))

        route("Update INC0000258 priority", state)

        intent = route("Set state to Closed", state)

        self.assertEqual(intent.changes, {"state": "Closed"})

    def test_a_question_drops_the_pending_update(self):

        state = make_state(incident=("INC0000258", "INC258"))

        route("Update it.", state)

        intent = route("Who opened it?", state)

        self.assertEqual(intent.route, "entity_fields")
        self.assertIsNone(state.pending)

    def test_pending_expires(self):

        state = make_state(incident=("INC0000258", "INC258"))

        route("Update it.", state)

        for _ in range(5):
            state.begin_turn()

        self.assertIsNone(state.pending)


class EmployeeTests(unittest.TestCase):

    def test_name_extraction(self):

        cases = {
            "Show Rohit's roster.": "Rohit",
            "Show me Rohit Malhotra's shift": "Rohit Malhotra",
            "roster details for Priya Nair": "Priya Nair",
            "Is Rohit available tomorrow?": "Rohit",
            "Delete the roster entry for Rohit.": "Rohit",
            "Remove Rohit Kumar from the roster": "Rohit Kumar",
            "What is rohit.m@abc.com's level": "rohit.m@abc.com",
        }

        for text, name in cases.items():
            self.assertEqual(extract_employee_query(text), name, text)

    def test_no_name(self):

        self.assertIsNone(extract_employee_query("Show me the roster"))
        self.assertIsNone(extract_employee_query("What is his shift?"))

    def test_roster_request(self):

        intent = route("Show Rohit's roster.")

        self.assertEqual((intent.route, intent.query), ("employee_view", "Rohit"))

    def test_availability(self):

        intent = route("Is Rohit available tomorrow?")

        self.assertEqual((intent.route, intent.date), ("employee_availability", "tomorrow"))

    def test_pronoun_follow_ups(self):

        state = make_state(employee=("U001", "u001"))

        intent = route("What is his shift?", state)

        self.assertEqual((intent.route, intent.fields, intent.entity_ref), ("employee_fields", ["shift"], "U001"))

        intent = route("Is he available today?", state)

        self.assertEqual((intent.route, intent.date), ("employee_availability", "today"))

    def test_pronoun_without_employee(self):

        self.assertEqual(route("What is his shift?").route, "clarify")

    def test_date_ranges_go_to_the_model(self):

        self.assertEqual(route("Is Rohit available next week?").route, "llm")

    def test_team_ranges_go_to_the_model(self):

        self.assertEqual(route("Which employees in Access Management are available next week?").route, "llm")
        self.assertEqual(route("Who is available in Access Management in September?").route, "llm")

    def test_delete(self):

        intent = route("Delete the roster entry for Rohit.")

        self.assertEqual((intent.route, intent.query), ("employee_delete", "Rohit"))

    def test_delete_without_target(self):

        self.assertEqual(route("Delete his roster entry").route, "clarify")

    def test_delete_uses_recent_employee(self):

        state = make_state(employee=("U001", "u001"))

        intent = route("Delete him from the roster", state)

        self.assertEqual((intent.route, intent.entity_ref), ("employee_delete", "U001"))

    def test_choosing_between_candidates(self):

        state = make_state()

        options = [
            {"UID": "U001", "employee_name": "Rohit Malhotra", "team_id": "IAM", "team_name": "Access Management", "emp_id": "E001"},
            {"UID": "U002", "employee_name": "Rohit Sharma", "team_id": "INFRA", "team_name": "Infrastructure", "emp_id": "E002"},
        ]

        then = {"route": "employee_fields", "operation": "read", "fields": ["shift"], "date": "today"}

        for reply, uid in (("2", "U002"), ("the first one", "U001"), ("Sharma", "U002"), ("infrastructure", "U002")):

            state.set_pending("choose_employee", options=options, then=then)

            intent = DeterministicRouter.route(reply, state)

            self.assertEqual((intent.route, intent.entity_ref, intent.fields), ("employee_fields", uid, ["shift"]), reply)


if __name__ == "__main__":
    unittest.main()
