"""
End-to-end conversations on the deterministic path, against fake tables.

Nothing here may reach the model: `_run_llm` is replaced by a stub that
records any call, and most tests assert it was never used.
"""

import unittest
from unittest import mock

import agent
from memory.store import SessionRuntime
from responses import text_reply
from tests.fakes import World, client_error, second_rohit


class Chat:
    """One user conversation."""

    def __init__(self, world, actor="tester", session="s1"):

        self.runtime = SessionRuntime(actor, session)

        self.world = world

        self.replies = []

    def say(self, text):

        reply, meta = agent.process_user_query(text, self.runtime)

        self.last = reply

        self.meta = meta

        self.replies.append(reply)

        return reply.text


class ConversationTestCase(unittest.TestCase):

    def make_world(self, **kwargs):

        self.world = World(**kwargs).install()

        self.llm_calls = []

        def fake_llm(user_input, runtime, meta):

            self.llm_calls.append(user_input)

            return text_reply("LLM ANSWER")

        patcher = mock.patch.object(agent, "_run_llm", fake_llm)

        patcher.start()

        self.addCleanup(patcher.stop)

        self.addCleanup(self.world.close)

        return Chat(self.world)

    def setUp(self):
        self.chat = self.make_world()


class IncidentConversationTests(ConversationTestCase):

    def test_spec_incident_follow_ups(self):

        chat = self.chat

        text = chat.say("Show me INC0000258")

        self.assertIn("INC0000258", text)
        self.assertIn("P1-Critical", text)

        self.assertIn("varun.nambiar@abc.com", chat.say("Who is assigned to it?"))
        self.assertIn("P1-Critical", chat.say("What is its priority?"))

        sla = chat.say("What is the SLA?")

        self.assertIn("60", sla)
        self.assertIn("720", sla)

        breached = chat.say("Is it breached?")

        self.assertIn("Assignment SLA breached: No", breached)
        self.assertIn("Resolution SLA breached: No", breached)

        self.assertIn("simran.gupta@abc.com", chat.say("Who opened it?"))
        self.assertIn("60", chat.say("What about the SLA?"))

        # REQ0000162 breached its resolution SLA (the stored value is the text "TRUE").
        chat.say("Show me REQ0000162")

        self.assertIn("Resolution SLA breached: Yes", chat.say("Is it breached?"))
        self.assertIn("ishaan.gupta@abc.com", chat.say("Who opened it?"))

        self.assertEqual(self.llm_calls, [])

    def test_follow_ups_reuse_the_fetched_record(self):

        self.chat.say("Show me INC0000258")

        for question in ("Who is assigned to it?", "What is its priority?", "What is the SLA?", "Is it breached?"):
            self.chat.say(question)

        self.assertEqual(self.world.incidents.count("get_item"), 1)

    def test_direct_answers_cost_no_tokens(self):

        self.chat.say("Show me INC0000258")

        self.assertTrue(self.chat.meta["direct"])
        self.assertEqual(self.chat.meta["usage"]["total_tokens"], 0)

    def test_answer_contains_only_what_was_asked(self):

        self.chat.say("Show me INC0000258")

        answer = self.chat.say("Who is assigned to INC0000258?")

        self.assertIn("varun.nambiar@abc.com", answer)
        self.assertNotIn("Database failure", answer)
        self.assertNotIn("P1-Critical", answer)

    def test_id_spellings(self):

        for spelling in ("INC0000258", "INC-0000258", "INC 0000258", "incident 258"):

            chat = Chat(self.world)

            self.assertIn("P1-Critical", chat.say(f"Show me {spelling}"), spelling)

    def test_request_status(self):

        self.assertIn("Closed", self.chat.say("What is the status of REQ0000162?"))

    def test_unassigned_ticket(self):

        answer = self.chat.say("Who is assigned to INC0000259?")

        self.assertIn("isn't assigned to anyone yet", answer)

    def test_not_found_and_recovery(self):

        self.assertIn("INC0009999 was not found", self.chat.say("Show me INC0009999"))
        self.assertFalse(self.chat.last.ok)

        self.assertIn("P1-Critical", self.chat.say("Show me INC0000258"))

    def test_a_missing_ticket_is_not_kept_as_context(self):

        self.chat.say("Show me INC0009999")

        self.assertIn("ID", self.chat.say("What is its priority?"))

    def test_new_ticket_replaces_the_old_one(self):

        self.chat.say("Show me INC0000258")
        self.chat.say("Show me INC0000260")

        self.assertIn("P2-High", self.chat.say("What is its priority?"))

    def test_lists(self):

        table = self.chat.say("List SLA breaches")

        self.assertIn("INC0000260", table)
        self.assertIn("REQ0000162", table)
        self.assertNotIn("INC0000258", table)
        self.assertEqual(self.chat.last.structured["type"], "table")

        self.assertIn("INC0000259", self.chat.say("incidents today"))

        self.assertEqual(self.llm_calls, [])


class IssueConversationTests(ConversationTestCase):

    def test_spec_category_matrix_follow_ups(self):

        chat = self.chat

        text = chat.say("What is Issue-1?")

        self.assertIn("Identity Lifecycle", text)
        self.assertIn("New Identity Creation", text)
        self.assertIn("Joiner", text)

        self.assertIn("Identity Operations", chat.say("Which team handles it?"))
        self.assertIn("Service Request", chat.say("What type of request is it?"))
        self.assertIn("Low", chat.say("What is its impact?"))

        self.assertEqual(self.world.issues.count("get_item"), 1)
        self.assertEqual(self.llm_calls, [])

    def test_issue_formats(self):

        for spelling in ("Issue-1", "Issue1", "Issue 1"):
            self.assertIn("Identity Lifecycle", Chat(self.world).say(f"Show me {spelling}"))

    def test_incident_and_issue_context_do_not_mix(self):

        chat = self.chat

        chat.say("Show me INC0000258")
        chat.say("Show me Issue-1")

        self.assertIn("Identity Operations", chat.say("Which team handles it?"))
        self.assertIn("Low", chat.say("What is its impact?"))
        self.assertIn("varun.nambiar@abc.com", chat.say("Who is assigned to the incident?"))

    def test_categories(self):

        self.assertIn("Identity Lifecycle", self.chat.say("list all categories"))


class RosterConversationTests(ConversationTestCase):

    def test_spec_roster_follow_ups(self):

        chat = self.chat

        text = chat.say("Show Rohit's roster.")

        self.assertIn("Rohit Malhotra", text)
        self.assertIn("rohit.malhotra@abc.com", text)
        self.assertIn("Shift: S1", text)
        self.assertIn("working days", text)
        self.assertNotIn("days_availability", text)

        self.assertIn("S1", chat.say("What is his shift?"))
        self.assertIn("is **available**", chat.say("Is he available today?"))

        timings = chat.say("What are his shift timings?")

        self.assertIn("06:00:00 AM", timings)
        self.assertIn("02:00:00 PM", timings)

        self.assertIn("rohit.malhotra@abc.com", chat.say("What is his email?"))

        self.assertEqual(self.llm_calls, [])

    def test_availability_by_name(self):

        self.assertIn("is **available**", self.chat.say("Is Rohit available today?"))
        self.assertIn("is **not available**", self.chat.say("Is Priya available today?"))

    def test_a_month_without_a_roster_row_is_explained(self):

        answer = self.chat.say("Is Rohit available on 2099-01-01?")

        self.assertIn("no roster for 2099-01", answer)
        self.assertNotIn("Not available", answer)

    def test_a_day_with_nothing_recorded(self):

        from tests.fakes import previous_month

        # Rohit's previous-month row only records days 1 and 2.
        previous = previous_month()

        answer = self.chat.say(f"Is Rohit available on {previous.year}-{previous.month:02d}-15?")

        self.assertIn("No availability is recorded", answer)

    def test_unknown_employee(self):

        self.assertIn("No employee matches", self.chat.say("Show Zed's roster"))

    def test_directory_is_not_rescanned_per_question(self):

        self.chat.say("Show Rohit's roster.")
        self.chat.say("What is his shift?")
        self.chat.say("Is Priya available today?")

        self.assertEqual(self.world.roster.count("scan"), 2)

    def test_multiple_matches_ask_which_then_continue(self):

        chat = self.make_world(extra_people=[second_rohit()])

        text = chat.say("What is Rohit's shift?")

        self.assertIn("Which one", text)
        self.assertIn("Rohit Malhotra", text)
        self.assertIn("Rohit Sharma", text)
        self.assertTrue(chat.last.structured["clarification"])
        self.assertEqual(len(chat.last.structured["options"]), 2)

        self.assertIn("S2", chat.say("2"))

        # Now "he" means the chosen person.
        self.assertIn("Rohit Sharma", chat.say("What is his shift?"))

        self.assertEqual(self.llm_calls, [])

    def test_choosing_by_team_name(self):

        chat = self.make_world(extra_people=[second_rohit()])

        chat.say("Show Rohit's roster")

        self.assertIn("Rohit Sharma", chat.say("the one in Infrastructure"))


class ClarificationTests(ConversationTestCase):

    def test_ambiguous_read_without_context(self):

        for question, expected in (
            ("Show me the incident.", "ID"),
            ("What is its priority?", "ID"),
            ("What is his shift?", "Which employee"),
        ):
            answer = Chat(self.world).say(question)

            self.assertIn(expected, answer, question)

        self.assertEqual(self.llm_calls, [])

    def test_clarification_does_not_hide_a_later_real_question(self):

        chat = self.chat

        chat.say("Show me the incident.")

        self.assertIn("P1-Critical", chat.say("Show me INC0000258"))


class UpdateConversationTests(ConversationTestCase):

    def stored(self, ticket="INC0000258"):
        return self.world.incidents.items[ticket]

    def test_spec_update_flow(self):

        chat = self.chat

        chat.say("Show INC0000258.")

        self.assertEqual(chat.say("Update it."), "What would you like to update for INC0000258?")

        answer = chat.say("Set priority to P2.")

        self.assertIn("Updated INC0000258", answer)
        self.assertIn("P1-Critical → P2-High", answer)
        self.assertEqual(self.stored()["priority"], "P2-High")
        self.assertEqual(chat.last.structured["type"], "update_confirmation")
        self.assertEqual(chat.last.structured["changes"][0]["to"], "P2-High")

        # The session cache follows the write.
        self.assertIn("P2-High", chat.say("What is its priority?"))

        self.assertEqual(self.llm_calls, [])

    def test_clear_update_is_executed_without_confirmation(self):

        answer = self.chat.say("Update INC0000258 priority to P2.")

        self.assertIn("Updated INC0000258", answer)
        self.assertNotIn("sure", answer.lower())
        self.assertEqual(self.stored()["priority"], "P2-High")
        self.assertEqual(self.world.incidents.count("update_item"), 1)

    def test_update_it_after_reading(self):

        self.chat.say("Show INC0000259")

        answer = self.chat.say("Change its urgency to high")

        self.assertIn("Low → High", answer)
        self.assertEqual(self.stored("INC0000259")["urgency"], "High")
        self.assertEqual(self.stored("INC0000258")["urgency"], "High")

    def test_update_without_context_asks_which(self):

        chat = self.chat

        self.assertEqual(chat.say("Set the priority to P2."), "Which incident would you like to update?")
        self.assertEqual(self.world.incidents.count("update_item"), 0)

        answer = chat.say("INC0000258")

        self.assertIn("P1-Critical → P2-High", answer)

    def test_ambiguous_mutations(self):

        self.assertEqual(self.chat.say("Update the ticket."), "Which ticket would you like to update?")
        self.assertEqual(self.chat.say("Update INC0000258."), "What would you like to update for INC0000258?")
        self.assertEqual(self.world.incidents.count("update_item"), 0)

    def test_field_then_value(self):

        chat = self.chat

        self.assertEqual(chat.say("Update INC0000258 priority"), "What should the priority be for INC0000258?")
        self.assertIn("P3-Medium", chat.say("P3"))

    def test_invalid_value_is_explained_and_can_be_corrected(self):

        chat = self.chat

        answer = chat.say("Update INC0000258 priority to P9")

        self.assertFalse(chat.last.ok)
        self.assertIn("not a valid priority", answer)
        self.assertIn("P2-High", answer)
        self.assertEqual(self.stored()["priority"], "P1-Critical")

        self.assertIn("P1-Critical → P3-Medium", chat.say("P3"))

    def test_assignment(self):

        answer = self.chat.say("Assign INC0000259 to Rohit")

        self.assertIn("rohit.malhotra@abc.com", answer)
        self.assertEqual(self.stored("INC0000259")["assigned_to"], "rohit.malhotra@abc.com")

    def test_ambiguous_assignee_asks(self):

        chat = self.make_world(extra_people=[second_rohit()])

        answer = chat.say("Assign INC0000259 to Rohit")

        self.assertIn("Several people match", answer)
        self.assertNotIn("assigned_to", self.world.incidents.items["INC0000259"])

    def test_close_it(self):

        self.chat.say("Show INC0000260")

        self.assertIn("Closed", self.chat.say("close it"))

    def test_issue_update(self):

        chat = self.chat

        chat.say("Show me Issue-1")

        answer = chat.say("Change its impact to High")

        self.assertIn("Low → High", answer)
        self.assertEqual(self.world.issues.items["Issue-1"]["impact"], "High")
        self.assertEqual(self.world.issues.items["Issue-1"]["updated_by"], "tester")

    def test_stale_context_is_never_modified(self):

        chat = self.chat

        chat.say("Show INC0000258")

        for _ in range(12):
            chat.say("Show INC0000259") if False else chat.say("list all categories")

        self.assertEqual(chat.say("Change its priority to P2"), "Which incident would you like to update?")
        self.assertEqual(self.stored()["priority"], "P1-Critical")

    def test_a_failed_update_reports_the_problem(self):

        self.world.incidents.fail_with = client_error("AccessDeniedException", "not authorized to perform dynamodb:UpdateItem on arn:aws:...")

        chat = Chat(self.world)

        self.world.incidents.fail_with = None
        chat.say("Show INC0000258")
        self.world.incidents.fail_with = client_error("AccessDeniedException", "not authorized to perform dynamodb:UpdateItem on arn:aws:...")

        answer = chat.say("Set priority to P2")

        self.assertFalse(chat.last.ok)
        self.assertNotIn("AccessDenied", answer)
        self.assertNotIn("arn:", answer)
        self.assertIn("not permitted", answer)

    def test_multi_change_goes_to_the_model(self):

        self.chat.say("Show INC0000258")

        self.chat.say("Set priority to P2 and assign it to Rohit")

        self.assertEqual(self.llm_calls, ["Set priority to P2 and assign it to Rohit"])
        self.assertEqual(self.stored()["priority"], "P1-Critical")


class DeleteConversationTests(ConversationTestCase):

    def test_delete_roster_entry(self):

        answer = self.chat.say("Delete the roster entry for Rohit.")

        from services.common import today

        self.assertEqual(
            answer,
            f"Deleted the roster entry for Rohit Malhotra ({today().year}-{today().month:02d}).",
        )
        self.assertNotIn("1", self.world.roster.items)
        self.assertIn("11", self.world.roster.items)  # last month's row is untouched
        self.assertEqual(self.chat.last.structured["operation"], "delete")

        # Rohit's only remaining row is deleted next, then he is gone.
        self.assertIn("Deleted the roster entry for Rohit Malhotra", self.chat.say("Delete the roster entry for Rohit."))
        self.assertIn("No employee matches", self.chat.say("Delete the roster entry for Rohit."))

    def test_deleting_a_specific_month_goes_to_the_model(self):

        self.chat.say("Delete the roster entry for Rohit for 2026-08")

        self.assertEqual(len(self.llm_calls), 1)
        self.assertIn("1", self.world.roster.items)

    def test_delete_refuses_to_guess_between_people(self):

        chat = self.make_world(extra_people=[second_rohit()])

        answer = chat.say("Delete the roster entry for Rohit")

        self.assertIn("Which one", answer)
        self.assertIn("1", self.world.roster.items)
        self.assertIn("2", self.world.roster.items)

        chat.say("2")

        self.assertNotIn("2", self.world.roster.items)
        self.assertIn("1", self.world.roster.items)

    def test_delete_from_context(self):

        chat = self.chat

        chat.say("Show Rohit's roster")

        self.assertIn("Deleted the roster entry for Rohit Malhotra", chat.say("Delete him from the roster"))

    def test_delete_incident(self):

        chat = self.chat

        self.assertEqual(chat.say("Delete INC0000259"), "Deleted incident INC0000259.")
        self.assertNotIn("INC0000259", self.world.incidents.items)

        # The deleted ticket is no longer "it".
        self.assertIn("Which", chat.say("Delete it"))

    def test_delete_without_target(self):

        self.assertIn("Whose roster entry", self.chat.say("Delete his roster entry"))
        self.assertIn("Which incident", self.chat.say("Delete it"))

    def test_delete_disabled(self):

        with mock.patch("services.incident_service.ENABLE_DELETE_OPERATIONS", False):

            answer = self.chat.say("Delete INC0000259")

        self.assertIn("disabled", answer)
        self.assertIn("INC0000259", self.world.incidents.items)

    def test_deleting_issues_is_not_supported(self):

        self.assertIn("isn't supported", self.chat.say("Delete Issue-1"))
        self.assertIn("Issue-1", self.world.issues.items)


class SessionIsolationTests(ConversationTestCase):

    def test_sessions_do_not_share_context(self):

        alice = Chat(self.world, "alice", "s1")
        alice_again = Chat(self.world, "alice", "s2")
        bob = Chat(self.world, "bob", "s1")

        alice.say("Show me INC0000258")

        self.assertIn("P1-Critical", alice.say("What is its priority?"))
        self.assertIn("ID", alice_again.say("What is its priority?"))
        self.assertIn("ID", bob.say("What is its priority?"))

    def test_pending_questions_are_private_to_the_session(self):

        alice = Chat(self.world, "alice", "s1")
        bob = Chat(self.world, "bob", "s1")

        alice.say("Show me INC0000258")
        alice.say("Update it.")

        bob.say("Set priority to P2.")

        self.assertEqual(self.world.incidents.items["INC0000258"]["priority"], "P1-Critical")

    def test_updates_record_who_made_them(self):

        Chat(self.world, "alice", "s1").say("Update Issue-1 impact to High")

        self.assertEqual(self.world.issues.items["Issue-1"]["updated_by"], "alice")


class FailureTests(ConversationTestCase):

    def test_aws_failures_are_user_friendly(self):

        for code in ("AccessDeniedException", "ProvisionedThroughputExceededException", "InternalServerError"):

            chat = Chat(self.world)

            self.world.incidents.fail_with = client_error(code, "arn:aws:dynamodb:ap-south-1:123456789012:table/prod-secret")

            answer = chat.say("Show me INC0000258")

            self.assertFalse(chat.last.ok, code)
            self.assertEqual(chat.last.structured["type"], "error")
            self.assertNotIn("arn:", answer)
            self.assertNotIn("123456789012", answer)
            self.assertNotIn("Traceback", answer)
            self.assertNotIn(code, answer)

        self.world.incidents.fail_with = None

    def test_unexpected_read_bug_falls_back_to_the_model(self):

        with mock.patch.object(agent, "execute", side_effect=RuntimeError("bug")):

            answer = self.chat.say("Show me INC0000258")

        self.assertEqual(answer, "LLM ANSWER")
        self.assertEqual(self.chat.meta["route"], "llm")

    def test_unexpected_write_bug_is_not_retried_by_the_model(self):

        with mock.patch.object(agent, "execute", side_effect=RuntimeError("bug")):

            answer = self.chat.say("Update INC0000258 priority to P2")

        self.assertIn("couldn't complete that change", answer)
        self.assertEqual(self.llm_calls, [])

    def test_empty_input(self):

        reply, _ = agent.process_user_query("   ", self.chat.runtime)

        self.assertEqual(reply.text, "Please provide a request.")


class ModelRoutingTests(ConversationTestCase):

    def test_open_questions_go_to_the_model(self):

        for question in (
            "Why did INC0000258 breach SLA?",
            "Who should handle a new Identity Lifecycle issue?",
            "Which employees in Access Management will be on leave next week?",
            "Create a new incident for a database failure",
            "How do I reset a password?",
        ):
            self.assertEqual(self.chat.say(question), "LLM ANSWER", question)
            self.assertEqual(self.chat.meta["route"], "llm")

        self.assertEqual(len(self.llm_calls), 5)


class PromptSizeTests(ConversationTestCase):
    """The model is told what is being discussed, not the whole conversation."""

    def test_context_stays_small_however_long_the_session(self):

        chat = self.chat

        chat.say("Show me INC0000258")

        sizes = []

        for index in range(40):

            chat.say("Who is assigned to it?")

            sizes.append(len(agent.build_system_prompt(chat.runtime)[-1]["text"]))

        self.assertLess(max(sizes), 1500)
        self.assertEqual(sizes[5], sizes[-1])

    def test_static_prompt_is_a_cache_prefix(self):

        chat = self.chat

        first = agent.build_system_prompt(chat.runtime)

        chat.say("Show me INC0000258")

        second = agent.build_system_prompt(chat.runtime)

        self.assertEqual(first[0], second[0])
        self.assertEqual(second[1], {"cachePoint": {"type": "default"}})
        self.assertNotEqual(first[2], second[2])
        self.assertIn("INC0000258", second[2]["text"])

    def test_prompt_never_contains_record_dumps(self):

        self.chat.say("Show me INC0000258")

        dynamic = agent.build_system_prompt(self.chat.runtime)[-1]["text"]

        self.assertNotIn("Primary database node", dynamic)
        self.assertNotIn("working_notes", dynamic)


if __name__ == "__main__":
    unittest.main()
