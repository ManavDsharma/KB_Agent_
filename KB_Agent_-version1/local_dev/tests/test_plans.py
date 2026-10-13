"""
Multi-part write requests: routing, the planner call, grounding, the three
tiers (execute / confirm / ask) and what is actually written.

The planner (a model) is replaced by a stub that returns the draft a model
might return, including wrong ones, so the safety checks are exercised.
"""

import unittest
from unittest import mock

import agent
import planner
import plans
from memory.state import SessionState
from memory.store import SessionRuntime
from planner import PlanDraft, PlannedOp
from responses import text_reply
from router import DeterministicRouter
from services import common
from services.common import today
from tests.fakes import World, second_rohit
from tests.scripted_model import ScriptedModel


def update(record="incident", **kwargs):
    return PlannedOp(action="update", record=record, **kwargs)


def availability(**kwargs):
    return PlannedOp(action="availability", record="employee", **kwargs)


class StubPlanner:
    """Stands in for the planner model: returns the draft it is given."""

    def __init__(self, ops=None, usage=None):

        self.ops = ops

        self.usage = usage or {}

        self.calls = []

    def __call__(self, text, today_text, context=""):

        self.calls.append({"text": text, "context": context})

        if self.ops is None:
            return None, dict(self.usage)

        return PlanDraft(ops=list(self.ops)), dict(self.usage)


class Chat:

    def __init__(self, actor="tester", session="s1"):

        self.runtime = SessionRuntime(actor, session)

    def say(self, text):

        reply, meta = agent.process_user_query(text, self.runtime)

        self.last = reply

        self.meta = meta

        return reply.text


def a_working_day():
    """A day of this month that is neither today nor one of Rohit's leave days."""

    return 2 if today().day != 2 else 3


class PlanTestCase(unittest.TestCase):

    def start(self, ops=None, usage=None, extra_people=None):

        self.world = World(extra_people=extra_people).install()

        self.addCleanup(self.world.close)

        self.llm_calls = []

        def fake_llm(user_input, runtime, meta):

            self.llm_calls.append(user_input)

            return text_reply("LLM ANSWER")

        patcher = mock.patch.object(agent, "_run_llm", fake_llm)

        patcher.start()

        self.addCleanup(patcher.stop)

        self.planner = StubPlanner(ops, usage)

        planner_patch = mock.patch.object(planner, "extract", self.planner)

        planner_patch.start()

        self.addCleanup(planner_patch.stop)

        self.chat = Chat()

        return self.chat

    def incident(self, ticket="INC0000259"):
        return self.world.incidents.items[ticket]

    def day_value(self, uid, day):
        return self.world.roster.items[uid]["days_availability"][str(day)]

    def iso(self, day):
        current = today()
        return f"{current.year}-{current.month:02d}-{day:02d}"


# ============================================================
# ROUTING
# ============================================================

class PlanRoutingTests(unittest.TestCase):

    def route(self, text, **focus):

        state = SessionState("actor", "session")

        state.begin_turn()

        for kind, (entity_id, key) in focus.items():
            state.set_focus(kind, entity_id, key)

        state.begin_turn()

        return DeterministicRouter.route(text, state)

    def test_the_screenshot_message_goes_to_the_planner(self):

        intent = self.route("Set both Priority and urgency of the INC20261007101849134675 to High")

        self.assertEqual(intent.route, "plan")
        self.assertEqual(intent.entity_ref, "INC20261007101849134675")
        self.assertEqual(intent.query, "Set both Priority and urgency of the INC20261007101849134675 to High")

    def test_the_second_screenshot_message_uses_context(self):

        intent = self.route(
            "Set priority and urgency to high",
            incident=("INC20261007101849134675", "INC20261007101849134675"),
        )

        self.assertEqual(intent.route, "plan")

    def test_several_fields_in_other_phrasings(self):

        for text in (
            "Set priority to High and urgency to Low on INC0000258",
            "Update INC0000258 priority High, urgency High",
            "Set urgency and priority of INC0000258 to High",
            "Change both priority and urgency of INC0000258 to Low",
        ):
            self.assertEqual(self.route(text).route, "plan", text)

    def test_several_records(self):

        self.assertEqual(self.route("Close INC0000259 and INC0000260").route, "plan")
        self.assertEqual(self.route("Set the impact of Issue-1 and Issue-2 to High").route, "plan")

    def test_roster_writes(self):

        for text in (
            "update employee2 and employee1 availability for 2nd august as false",
            "mark Rohit and Priya as unavailable on 2026-08-02",
            "Change Rohit's shift to S2",
            "Set Priya on leave from 3rd to 5th August",
        ):
            intent = self.route(text)

            self.assertEqual(intent.route, "plan", text)
            self.assertEqual(intent.entity_type, "employee", text)

    def test_a_write_the_fast_path_has_no_pattern_for(self):

        intent = self.route("Mark INC0000259 as resolved")

        self.assertEqual(intent.route, "plan")
        self.assertEqual(intent.entity_ref, "INC0000259")

    def test_a_long_write_is_planned_not_dropped(self):

        text = (
            "Please update INC0000259 so that the priority is High and the urgency is High "
            "and also assign it to Rohit and set the state to In Progress for now"
        )

        self.assertGreater(len(text.split()), 24)
        self.assertEqual(self.route(text).route, "plan")

    # ---- what must NOT be taken -------------------------------------

    def test_single_clear_updates_stay_on_the_fast_path(self):

        for text, changes in (
            ("Update INC0000258 priority to P2", {"priority": "P2"}),
            ("Set the state of INC0000258 to Closed", {"state": "Closed"}),
            ("Set the status of INC0000258 to resolved", {"state": "resolved"}),
            ("Assign INC0000258 to Rohit", {"assigned_to": "Rohit"}),
            ("Set short description of INC0000258 to Printer Jam on Floor 3", {"short_description": "Printer Jam on Floor 3"}),
        ):
            intent = self.route(text)

            self.assertEqual((intent.route, intent.changes), ("entity_update", changes), text)

    def test_a_value_that_holds_another_request_is_not_a_value(self):
        """The fast path used to take everything after 'to' as the new priority."""

        for text in (
            "Set priority to High on INC0000259 and mark Rohit unavailable today",
            "Set priority to High and set state to Closed on INC0000259",
        ):
            self.assertEqual(self.route(text).route, "plan", text)

    def test_reads_are_never_planned(self):

        for text in (
            "Show me INC0000258",
            "Who is assigned to INC0000258?",
            "What is the priority and urgency of INC0000258?",
            "Show INC0000258 and INC0000259",
            "Is Rohit available today?",
            "Who is available today in Access Management?",
            "What was updated on INC0000258?",
        ):
            self.assertNotEqual(self.route(text).route, "plan", text)

    def test_reasoning_and_destructive_requests_are_not_planned(self):

        for text in (
            "How should I set the priority of INC0000258?",
            "Why was the priority of INC0000258 changed?",
            "Delete INC0000258 and INC0000259",
            "Delete the roster entry for Rohit",
            "Create a new incident for a database failure",
        ):
            self.assertNotEqual(self.route(text).route, "plan", text)

    def test_several_records_are_never_half_handled(self):

        self.assertEqual(self.route("Show INC0000258 and INC0000259").route, "llm")
        self.assertEqual(self.route("Delete INC0000258 and INC0000259").route, "llm")

    def test_confirmation_replies(self):

        state = SessionState("actor", "session")

        state.begin_turn()

        state.set_pending("plan_confirm", plan={"ops": []})

        state.begin_turn()

        self.assertEqual(DeterministicRouter.route("yes", state).route, "plan_execute")

        state.set_pending("plan_confirm", plan={"ops": []})

        self.assertEqual(DeterministicRouter.route("cancel", state).route, "plan_cancel")

        state.set_pending("plan_confirm", plan={"ops": []})

        # anything else is a new request, and the preview is dropped
        self.assertNotIn(DeterministicRouter.route("show INC0000258", state).route, ("plan_execute", "plan_cancel"))
        self.assertIsNone(state.pending)


# ============================================================
# THE SCREENSHOT CONVERSATION
# ============================================================

class ScreenshotTests(PlanTestCase):

    def test_both_fields_are_set_in_one_write(self):
        """'Set both Priority and urgency of the INC… to High' used to loop on questions."""

        chat = self.start(
            [update(targets=["INC0000259"], changes={"priority": "High", "urgency": "High"})],
            usage={"input_tokens": 700, "output_tokens": 90, "total_tokens": 790},
        )

        text = chat.say("Set both Priority and urgency of the INC0000259 to High")

        self.assertIn("Updated INC0000259", text)
        self.assertIn("Priority: P3-Medium → P2-High", text)
        self.assertIn("Urgency: Low → High", text)

        self.assertEqual(self.incident()["priority"], "P2-High")
        self.assertEqual(self.incident()["urgency"], "High")
        self.assertEqual(self.world.incidents.count("update_item"), 1)

        self.assertEqual(self.llm_calls, [])
        self.assertEqual(chat.last.structured["type"], "update_confirmation")
        self.assertEqual(chat.meta["reason"], "tier=execute")

    def test_planner_tokens_are_counted_and_nothing_else_is_spent(self):

        chat = self.start(
            [update(targets=["INC0000259"], changes={"priority": "High", "urgency": "High"})],
            usage={"input_tokens": 700, "output_tokens": 90, "total_tokens": 790},
        )

        chat.say("Set both Priority and urgency of the INC0000259 to High")

        self.assertEqual(chat.meta["usage"]["input_tokens"], 700)
        self.assertEqual(chat.meta["usage"]["output_tokens"], 90)
        self.assertEqual(len(self.planner.calls), 1)

    def test_follow_ups_work_after_a_plan(self):

        chat = self.start([update(targets=["INC0000259"], changes={"priority": "High", "urgency": "High"})])

        chat.say("Set both Priority and urgency of the INC0000259 to High")

        self.assertIn("P2-High", chat.say("What is its priority?"))
        self.assertEqual(self.llm_calls, [])

    def test_no_target_then_the_id_completes_the_same_plan(self):
        """The old flow asked 'Which incident?' and then forgot what to change."""

        chat = self.start([update(targets=[], changes={"priority": "High", "urgency": "Low"})])

        question = chat.say("Set priority to High and urgency to Low")

        self.assertEqual(question, "Which incident should I update? Please share its ID.")
        self.assertEqual(self.world.incidents.count("update_item"), 0)

        text = chat.say("INC0000259")

        self.assertIn("Updated INC0000259", text)
        self.assertEqual((self.incident()["priority"], self.incident()["urgency"]), ("P2-High", "Low"))
        self.assertEqual(len(self.planner.calls), 1)

    def test_missing_values_are_asked_one_at_a_time(self):

        chat = self.start([update(targets=["INC0000259"], changes={"priority": "", "urgency": ""})])

        self.assertEqual(
            chat.say("Set priority and urgency of INC0000259"),
            "What should the priority be for INC0000259?",
        )

        self.assertEqual(chat.say("High"), "What should the urgency be for INC0000259?")

        self.assertIn("Updated INC0000259", chat.say("Low"))

        self.assertEqual((self.incident()["priority"], self.incident()["urgency"]), ("P2-High", "Low"))

    def test_context_supplies_the_target(self):

        chat = self.start([update(targets=[], changes={"priority": "High", "urgency": "High"})])

        chat.say("Show INC0000259")

        text = chat.say("Set priority and urgency to high")

        self.assertIn("Updated INC0000259", text)
        self.assertEqual(self.planner.calls[0]["context"], "incident INC0000259")

    def test_a_named_record_is_remembered_while_asking(self):
        """The ID in 'Update INC… priority' stays 'it' for the next message."""

        chat = self.start()

        self.assertEqual(chat.say("Update INC0000259 priority"), "What should the priority be for INC0000259?")

        self.assertEqual(self.world.incidents.count("get_item"), 0)

        self.assertIn("P2-High", chat.say("High") + chat.say("What is its priority?"))

    def test_an_invalid_value_offers_the_valid_ones(self):

        chat = self.start([update(targets=["INC0000259"], changes={"priority": "Urgent", "urgency": "High"})])

        question = chat.say("Set priority to Urgent and urgency to High on INC0000259")

        self.assertIn("isn't a valid priority", question)

        labels = [option["label"] for option in chat.last.structured["options"]]

        self.assertEqual(labels, ["P1-Critical", "P2-High", "P3-Medium"])
        self.assertEqual(self.world.incidents.count("update_item"), 0)

        self.assertIn("Updated INC0000259", chat.say("2"))

        self.assertEqual((self.incident()["priority"], self.incident()["urgency"]), ("P2-High", "High"))

    def test_the_fast_path_is_untouched_for_a_single_clear_change(self):

        chat = self.start([update(targets=["INC0000259"], changes={"priority": "Low"})])

        chat.say("Update INC0000259 priority to P2")

        self.assertEqual(self.planner.calls, [])
        self.assertEqual(chat.meta["route"], "entity_update")
        self.assertEqual(chat.meta["usage"]["total_tokens"], 0)
        self.assertEqual(self.incident()["priority"], "P2-High")


# ============================================================
# CONFIRMATION TIER
# ============================================================

class ConfirmationTests(PlanTestCase):

    def test_several_incidents_wait_for_confirmation(self):

        chat = self.start([update(targets=["INC0000259", "INC0000260"], changes={"state": "Closed"})])

        text = chat.say("Close INC0000259 and INC0000260")

        self.assertIn("Here's what I'll change", text)
        self.assertIn("INC0000259", text)
        self.assertIn("INC0000260", text)
        self.assertEqual(self.world.incidents.count("update_item"), 0)

        structured = chat.last.structured

        # A client that only renders `question` + its buttons still shows the whole preview.
        self.assertIn("INC0000259", structured["question"])
        self.assertIn("INC0000260", structured["question"])

        self.assertEqual((structured["type"], structured["confirmation"]), ("question", True))
        self.assertEqual(
            [(option["label"], option["send"]) for option in structured["options"]],
            [("Confirm", "yes"), ("Cancel", "cancel")],
        )
        self.assertEqual(len(structured["plan"]), 2)

        done = chat.say("yes")

        self.assertIn("Updated 2 records", done)
        self.assertEqual(self.incident("INC0000259")["state"], "Closed")
        self.assertEqual(self.incident("INC0000260")["state"], "Closed")
        self.assertTrue(self.incident("INC0000259")["closed_at"])

        self.assertEqual(self.llm_calls, [])

    def test_the_confirm_buttons_are_understood(self):
        """Every option the agent offers must be something the router handles."""

        chat = self.start([update(targets=["INC0000259", "INC0000260"], changes={"state": "Closed"})])

        chat.say("Close INC0000259 and INC0000260")

        for option in chat.last.structured["options"]:

            state = SessionState("a", "b")

            state.begin_turn()

            state.set_pending("plan_confirm", plan={"ops": []})

            state.begin_turn()

            self.assertIn(
                DeterministicRouter.route(option["send"], state).route,
                ("plan_execute", "plan_cancel"),
            )

    def test_cancel_changes_nothing(self):

        chat = self.start([update(targets=["INC0000259", "INC0000260"], changes={"state": "Closed"})])

        chat.say("Close INC0000259 and INC0000260")

        self.assertIn("won't change anything", chat.say("cancel"))
        self.assertEqual(self.world.incidents.count("update_item"), 0)

        # and the plan is gone: "yes" no longer applies anything
        chat.say("yes")

        self.assertEqual(self.world.incidents.count("update_item"), 0)

    def test_an_unrelated_message_drops_the_preview(self):

        chat = self.start([update(targets=["INC0000259", "INC0000260"], changes={"state": "Closed"})])

        chat.say("Close INC0000259 and INC0000260")

        chat.say("Show INC0000258")

        chat.say("yes")

        self.assertEqual(self.world.incidents.count("update_item"), 0)

    def test_a_preview_expires(self):

        chat = self.start([update(targets=["INC0000259", "INC0000260"], changes={"state": "Closed"})])

        chat.say("Close INC0000259 and INC0000260")

        # PENDING_MAX_TURNS unanswered turns
        chat.runtime.state.turn += 10

        chat.say("yes")

        self.assertEqual(self.world.incidents.count("update_item"), 0)

    def test_confirmation_is_validated_again(self):
        """Data may change between the preview and the 'yes'."""

        chat = self.start([update(targets=["INC0000259", "INC0000260"], changes={"state": "Closed"})])

        chat.say("Close INC0000259 and INC0000260")

        self.world.incidents.items["INC0000260"]["state"] = "Closed"

        text = chat.say("yes")

        self.assertIn("Updated INC0000259", text)
        self.assertIn("INC0000260 already has those values", text)
        self.assertEqual(self.world.incidents.count("update_item"), 1)

    def test_sessions_are_isolated(self):

        first = self.start([update(targets=["INC0000259", "INC0000260"], changes={"state": "Closed"})])

        second = Chat(actor="other", session="s2")

        first.say("Close INC0000259 and INC0000260")

        second.say("yes")

        self.assertEqual(self.world.incidents.count("update_item"), 0)
        self.assertEqual(self.llm_calls, ["yes"])

        first.say("yes")

        self.assertEqual(self.world.incidents.count("update_item"), 2)

    def test_read_only_mode_stops_before_the_preview(self):

        chat = self.start([update(targets=["INC0000259", "INC0000260"], changes={"state": "Closed"})])

        with mock.patch.object(common, "READ_ONLY_MODE", True):

            text = chat.say("Close INC0000259 and INC0000260")

        self.assertIn("read-only", text)
        self.assertFalse(chat.last.ok)
        self.assertIsNone(chat.runtime.state.pending)
        self.assertEqual(self.world.incidents.count("update_item"), 0)

    def test_one_bad_record_blocks_the_whole_plan(self):

        chat = self.start([update(targets=["INC0000259", "INC0009999"], changes={"state": "Closed"})])

        text = chat.say("Close INC0000259 and INC0009999")

        self.assertIn("didn't change anything", text)
        self.assertIn("INC0009999", text)
        self.assertEqual(self.world.incidents.count("update_item"), 0)

    def test_a_failed_write_is_reported_per_record(self):
        """Validation passed, then one write fails: say which, never claim success for it."""

        chat = self.start([update(targets=["INC0000259", "INC0000260"], changes={"state": "Closed"})])

        chat.say("Close INC0000259 and INC0000260")

        real = plans._call

        def flaky(op, actor, dry_run):

            if not dry_run and op["target"] == "INC0000260":
                return {"ok": False, "error": "conflict", "message": "The record changed. Please try again."}

            return real(op, actor, dry_run)

        with mock.patch.object(plans, "_call", flaky):

            text = chat.say("yes")

        self.assertIn("Updated 1 record", text)
        self.assertIn("Couldn't update", text)
        self.assertIn("INC0000260: The record changed", text)
        self.assertEqual(self.incident("INC0000259")["state"], "Closed")
        self.assertEqual(self.incident("INC0000260")["state"], "In Progress")
        self.assertEqual(chat.last.structured["tone"], "warning")

    def test_nothing_to_change_is_said_plainly(self):

        chat = self.start([update(targets=["INC0000259"], changes={"priority": "P3-Medium", "urgency": "Low"})])

        text = chat.say("Set priority to P3-Medium and urgency to Low on INC0000259")

        self.assertIn("No change needed", text)
        self.assertEqual(self.world.incidents.count("update_item"), 0)


# ============================================================
# ROSTER: THE "employee2 and employee1" REQUEST
# ============================================================

class RosterPlanTests(PlanTestCase):

    def test_two_people_one_day_is_previewed_then_applied(self):

        day = a_working_day()

        chat = self.start([
            availability(targets=["Rohit", "Priya"], dates=[self.iso(day)], value="false"),
        ])

        text = chat.say(f"update Rohit and Priya availability for {self.iso(day)} as false")

        self.assertIn("Here's what I'll change", text)
        self.assertIn("Rohit Malhotra", text)
        self.assertIn("Priya Nair", text)
        self.assertIn("Available → Not available", text)

        # nothing is written before the user confirms
        self.assertEqual(self.world.roster.count("update_item"), 0)

        applied = chat.say("yes")

        self.assertIn("Updated 2 records", applied)
        self.assertEqual(self.day_value("1", day), "N")
        self.assertEqual(self.day_value("3", day), "N")

        # nobody else was touched
        self.assertEqual(self.day_value("4", day), "Y")

        self.assertEqual(self.llm_calls, [])
        self.assertEqual(len(self.planner.calls), 1)

    def test_one_person_today_is_applied_at_once(self):

        chat = self.start([availability(targets=["Rohit"], dates=["today"], value="unavailable")])

        text = chat.say("mark Rohit unavailable today")

        self.assertIn("Updated Rohit Malhotra", text)
        self.assertIn("Available → Not available", text)
        self.assertEqual(self.day_value("1", today().day), "N")
        self.assertEqual(chat.meta["reason"], "tier=execute")

    def test_a_year_the_user_did_not_give_needs_confirmation(self):
        """'2nd august' says nothing about the year: show which one was used."""

        chat = self.start([availability(targets=["Rohit"], dates=["2nd " + today().strftime("%B")], value="unavailable")])

        text = chat.say(f"mark Rohit unavailable on 2nd {today().strftime('%B')}")

        self.assertIn("No year was given", text)
        self.assertIn(str(today().year), text)
        self.assertEqual(self.world.roster.count("update_item"), 0)

        chat.say("yes")

        self.assertEqual(self.day_value("1", 2), "N")

    def test_a_missing_roster_month_is_reported_before_anything_is_written(self):

        chat = self.start([availability(targets=["Rohit", "Priya"], dates=["2nd August 2020"], value="false")])

        text = chat.say("update Rohit and Priya availability for 2nd August 2020 as false")

        self.assertIn("no roster row", text.lower())
        self.assertEqual(self.world.roster.count("update_item"), 0)

    def test_several_days_for_one_person(self):

        chat = self.start([
            availability(targets=["Rohit"], dates=[f"3rd {today().strftime('%B')}"], until=f"4th {today().strftime('%B')}", value="leave"),
        ])

        text = chat.say(f"mark Rohit on leave from 3rd to 4th {today().strftime('%B')}")

        self.assertIn("Here's what I'll change", text)

        chat.say("yes")

        self.assertEqual((self.day_value("1", 3), self.day_value("1", 4)), ("N", "N"))

    def test_an_ambiguous_name_asks_which_one_and_keeps_the_plan(self):

        chat = self.start(
            [availability(targets=["Rohit"], dates=["today"], value="unavailable")],
            extra_people=[second_rohit()],
        )

        question = chat.say("mark Rohit unavailable today")

        self.assertIn("Several employees match 'Rohit'", question)

        labels = [option["label"] for option in chat.last.structured["options"]]

        self.assertEqual(len(labels), 2)
        self.assertEqual(self.world.roster.count("update_item"), 0)

        text = chat.say("Sharma")

        self.assertIn("Updated Rohit Sharma", text)
        self.assertEqual(self.day_value("2", today().day), "N")
        self.assertEqual(self.day_value("1", today().day), "Y")
        self.assertEqual(len(self.planner.calls), 1)

    def test_a_missing_date_is_asked(self):

        chat = self.start([availability(targets=["Rohit"], dates=[], value="unavailable")])

        self.assertEqual(chat.say("mark Rohit unavailable"), "For which date(s)?")

        text = chat.say("today")

        self.assertIn("Updated Rohit Malhotra", text)

    def test_a_missing_value_is_asked_with_buttons(self):

        chat = self.start([availability(targets=["Rohit"], dates=["today"], value="")])

        chat.say("change Rohit availability today")

        self.assertEqual(
            [option["label"] for option in chat.last.structured["options"]],
            ["Available", "Not available"],
        )

        # Rohit is available today, so "Available" is no change...
        self.assertIn("No change needed", chat.say("available"))

    def test_pronoun_uses_the_person_discussed(self):

        chat = self.start([availability(targets=[], dates=["today"], value="unavailable")])

        chat.say("What is the shift of Rohit?")

        text = chat.say("mark him unavailable today")

        self.assertIn("Updated Rohit Malhotra", text)
        self.assertEqual(self.day_value("1", today().day), "N")

    def test_shift_change_for_one_person(self):

        chat = self.start([update("employee", targets=["Rohit"], changes={"shift": "S2"})])

        text = chat.say("Change Rohit's shift to S2")

        self.assertIn("Updated Rohit Malhotra", text)
        self.assertIn("Shift: S1 → S2", text)
        self.assertEqual(self.world.roster.items["1"]["shift"], "S2")


# ============================================================
# "THEM" / "BOTH OF THEM": THE LAST LIST
# ============================================================

class ListReferenceTests(PlanTestCase):
    """After 'show me the two new incidents', 'their priority for both of them' means those two."""

    PHRASE = "update their priority as HIGH for both of them"

    def listed(self, ops):
        """A chat that has just been shown today's two incidents (INC0000259, INC0000260)."""

        chat = self.start(ops)

        chat.say("incidents today")

        self.assertEqual(chat.runtime.state.last_list["ids"], ["INC0000259", "INC0000260"])

        return chat

    def test_them_means_the_records_just_listed(self):

        chat = self.listed([update(targets=[], changes={"urgency": "HIGH"})])

        text = chat.say("update their urgency as HIGH for both of them")

        self.assertIn("Here's what I'll change", text)
        self.assertIn("INC0000259", text)
        self.assertIn("INC0000260", text)
        self.assertIn("records from the list I just showed", text)
        self.assertEqual(self.world.incidents.count("update_item"), 0)

        self.assertEqual(chat.meta["route"], "plan")
        self.assertEqual(self.llm_calls, [])

        self.assertIn("Updated 2 records", chat.say("yes"))
        self.assertEqual(self.incident("INC0000259")["urgency"], "High")
        self.assertEqual(self.incident("INC0000260")["urgency"], "High")

        # nobody else was touched
        self.assertEqual(self.world.incidents.items["INC0000258"]["urgency"], "High")
        self.assertEqual(self.world.incidents.count("update_item"), 2)

    def test_the_exact_phrase_from_the_report(self):
        """It used to ask 'Which incident would you like to update?'."""

        chat = self.listed([update(targets=[], changes={"priority": "HIGH"})])

        text = chat.say(self.PHRASE)

        self.assertNotIn("Which incident", text)
        self.assertEqual(chat.meta["route"], "plan")
        self.assertIn("confirm", chat.meta["reason"])

    def test_a_record_that_needs_no_change_is_not_dropped_silently(self):

        # INC0000260 is already P2-High
        chat = self.listed([update(targets=[], changes={"priority": "HIGH"})])

        text = chat.say(self.PHRASE)

        self.assertIn("INC0000259", text)
        self.assertIn("INC0000260 already has those values", text)

        # asked for confirmation even though only one record changes
        self.assertEqual(chat.last.structured["confirmation"], True)

        chat.say("yes")

        self.assertEqual(self.incident("INC0000259")["priority"], "P2-High")
        self.assertEqual(self.world.incidents.count("update_item"), 1)

    def test_without_a_list_it_asks_for_the_ids_and_keeps_the_plan(self):

        chat = self.start([update(targets=[], changes={"urgency": "HIGH"})])

        self.assertEqual(
            chat.say("update their urgency as HIGH for both of them"),
            "Which incidents do you mean? Please share their IDs.",
        )

        text = chat.say("INC0000259 and INC0000260")

        self.assertIn("Here's what I'll change", text)

        chat.say("yes")

        self.assertEqual(self.incident("INC0000259")["urgency"], "High")
        self.assertEqual(self.incident("INC0000260")["urgency"], "High")
        self.assertEqual(len(self.planner.calls), 1)

    def test_two_named_records_where_one_already_matches(self):
        """One write is a single clear change: it runs, and the answer still mentions both."""

        chat = self.start([update(targets=[], changes={"priority": "HIGH"})])

        chat.say(self.PHRASE)

        text = chat.say("INC0000259 and INC0000260")

        self.assertIn("Updated INC0000259", text)
        self.assertIn("INC0000260 already has those values", text)
        self.assertEqual(self.incident("INC0000259")["priority"], "P2-High")

    def test_them_is_never_the_one_record_in_focus(self):
        """Changing one of two records silently would be worse than asking."""

        chat = self.start([update(targets=[], changes={"priority": "HIGH"})])

        chat.say("Show INC0000259")

        self.assertEqual(chat.say(self.PHRASE), "Which incidents do you mean? Please share their IDs.")
        self.assertEqual(self.world.incidents.count("update_item"), 0)

    def test_an_old_list_is_not_used(self):

        chat = self.listed([update(targets=[], changes={"priority": "HIGH"})])

        chat.runtime.state.turn += 20

        self.assertIn("Which incidents do you mean", chat.say(self.PHRASE))
        self.assertEqual(self.world.incidents.count("update_item"), 0)

    def test_a_list_from_the_model_path_counts_too(self):
        """The tool agent's list_incidents / search results feed the same memory."""

        from context import absorb_tool_result

        chat = self.start([update(targets=[], changes={"priority": "HIGH"})])

        chat.runtime.state.begin_turn()

        absorb_tool_result(
            chat.runtime.state,
            "search_incidents",
            {},
            {"ok": True, "total": 2, "incidents": [{"ticket_id": "INC0000259"}, {"ticket_id": "INC0000260"}]},
        )

        text = chat.say(self.PHRASE)

        self.assertIn("INC0000259", text)
        self.assertIn("INC0000260", text)

    def test_another_session_has_its_own_list(self):

        first = self.listed([update(targets=[], changes={"priority": "HIGH"})])

        second = Chat(actor="other", session="s2")

        self.assertIn("Which incidents do you mean", second.say(self.PHRASE))
        self.assertIsNone(second.runtime.state.last_list)

        self.assertIn("INC0000259", first.say(self.PHRASE))

    def test_close_them(self):

        chat = self.listed([update(targets=[], changes={"state": "Closed"})])

        text = chat.say("close them")

        self.assertIn("Here's what I'll change", text)

        chat.say("yes")

        self.assertEqual(self.incident("INC0000259")["state"], "Closed")
        self.assertEqual(self.incident("INC0000260")["state"], "Closed")


class ListMemoryTests(unittest.TestCase):

    def test_a_list_needs_two_records(self):

        state = SessionState("a", "b")

        state.begin_turn()

        state.set_list("incident", ["INC1"])

        self.assertIsNone(state.get_list())

        state.set_list("incident", ["INC1", "INC2", None, ""])

        self.assertEqual(state.get_list("incident")["ids"], ["INC1", "INC2"])
        self.assertIsNone(state.get_list("employee"))

    def test_age_limit(self):

        state = SessionState("a", "b")

        state.begin_turn()

        state.set_list("incident", ["INC1", "INC2"])

        state.turn += 9

        self.assertIsNone(state.get_list(max_age_turns=8))
        self.assertIsNotNone(state.get_list())

    def test_it_survives_a_restart_and_reaches_the_model_context(self):

        state = SessionState("a", "b")

        state.begin_turn()

        state.set_list("incident", ["INC1", "INC2"])

        restored = SessionState("a", "b")

        restored.load_dict(state.to_dict())

        self.assertEqual(restored.last_list["ids"], ["INC1", "INC2"])
        self.assertIn("last list (incidents, = them/both): INC1, INC2", restored.context_block())

    def test_one_row_focuses_many_rows_make_a_list(self):

        from context import absorb_tool_result

        state = SessionState("a", "b")

        state.begin_turn()

        absorb_tool_result(state, "list_incidents_by_date", {}, {"ok": True, "total": 1, "incidents": [{"ticket_id": "INC1"}]})

        self.assertEqual(state.focus["incident"].id, "INC1")
        self.assertIsNone(state.get_list())

        absorb_tool_result(state, "search_roster", {}, {"ok": True, "total": 2, "employees": [{"ref": "emp:1"}, {"ref": "emp:3"}]})

        self.assertEqual(state.get_list("employee")["ids"], ["emp:1", "emp:3"])


class PluralRoutingTests(unittest.TestCase):

    def route(self, text, listed=True):

        state = SessionState("a", "b")

        state.begin_turn()

        if listed:
            state.set_list("incident", ["INC20261007101849134675", "REQ0000180"])

        state.begin_turn()

        return DeterministicRouter.route(text, state)

    def test_the_reported_phrase(self):

        for listed in (True, False):

            intent = self.route("update their priority as HIGH for both of them", listed)

            self.assertEqual(intent.route, "plan", listed)

    def test_other_ways_to_say_it(self):

        for text in (
            "set priority to High for both of them",
            "change the priority of both of them to P2",
            "close them",
            "set their state to Closed",
            "assign them to Rohit",
            "update the priority of those two to High",
        ):
            self.assertEqual(self.route(text).route, "plan", text)

    def test_both_fields_is_not_both_records(self):
        """'both priority and urgency' is about fields: the focused record is still 'it'."""

        from plan_text import has_plural_reference

        self.assertFalse(has_plural_reference("set both priority and urgency to High"))
        self.assertTrue(has_plural_reference("set priority for both of them"))
        self.assertTrue(has_plural_reference("close all the incidents"))

    def test_reads_about_them_are_not_writes(self):

        for text in ("show them", "what is their priority?", "who opened them?"):
            self.assertNotEqual(self.route(text).route, "plan", text)


# ============================================================
# NOTHING IS INVENTED
# ============================================================

class HallucinationTests(PlanTestCase):
    """A wrong draft must never become a write; the tool agent answers instead."""

    def assert_rejected(self, text, ops):

        chat = self.start(ops)

        chat.say(text)

        self.assertEqual(self.llm_calls, [text], text)
        self.assertEqual(self.world.incidents.count("update_item"), 0, text)
        self.assertEqual(self.world.roster.count("update_item"), 0, text)
        self.assertIsNone(chat.runtime.state.pending, text)

        self.assertTrue(chat.meta["reason"].startswith("tier=fallback"), chat.meta["reason"])

    def test_a_value_the_user_never_wrote(self):

        self.assert_rejected(
            "Set priority to High and urgency to Low on INC0000259",
            [update(targets=["INC0000259"], changes={"priority": "P1-Critical", "urgency": "Low"})],
        )

    def test_a_field_the_user_never_mentioned(self):

        self.assert_rejected(
            "Set priority to High and urgency to Low on INC0000259",
            [update(targets=["INC0000259"], changes={"priority": "High", "urgency": "Low", "category": "Network"})],
        )

    def test_a_record_the_user_never_named(self):

        self.assert_rejected(
            "Set priority to High and urgency to Low on INC0000259",
            [update(targets=["INC0000999"], changes={"priority": "High", "urgency": "Low"})],
        )

    def test_a_dropped_record_cannot_cause_a_partial_change(self):

        self.assert_rejected(
            "Close INC0000259 and INC0000260",
            [update(targets=["INC0000259"], changes={"state": "Closed"})],
        )

    def test_a_person_with_an_invented_surname(self):

        self.assert_rejected(
            "mark Rohit unavailable today",
            [availability(targets=["Rohit Malhotra"], dates=["today"], value="unavailable")],
        )

    def test_the_logged_reason_has_no_user_words(self):
        """The request log gets a fixed category, never the names or values the user wrote."""

        chat = self.start([availability(targets=["Rohit Malhotra"], dates=["today"], value="unavailable")])

        chat.say("mark Rohit unavailable today")

        self.assertEqual(chat.meta["reason"], "tier=fallback (target not in request)")

    def test_a_date_the_model_worked_out_itself(self):

        self.assert_rejected(
            "mark Rohit unavailable tomorrow",
            [availability(targets=["Rohit"], dates=["2026-12-25"], value="unavailable")],
        )

    def test_a_read_only_field(self):

        self.assert_rejected(
            "Set priority to High and closed at to yesterday on INC0000259",
            [update(targets=["INC0000259"], changes={"priority": "High", "closed_at": "yesterday"})],
        )

    def test_a_planner_with_no_answer_falls_back(self):

        self.assert_rejected(
            "Set both priority and urgency of INC0000259 to High",
            None,
        )

    def test_an_empty_plan_falls_back(self):

        self.assert_rejected(
            "Set both priority and urgency of INC0000259 to High",
            [],
        )

    def test_too_many_operations(self):

        tickets = [f"INC{1000000 + number}" for number in range(25)]

        draft = PlanDraft(ops=[update(targets=tickets, changes={"state": "Closed"})])

        plan, why = plans.build_plan(draft, "close " + " ".join(tickets), SessionState("a", "b"))

        self.assertIsNone(plan)
        self.assertEqual(why, "too_many")


# ============================================================
# THE PLANNER CALL ITSELF
# ============================================================

class PlannerCallTests(unittest.TestCase):

    def draft(self, text="Set priority to High on INC0000259", script=None, **kwargs):

        model = ScriptedModel(
            script
            or [
                {
                    "tools": [
                        (
                            "PlanDraft",
                            {
                                "ops": [
                                    {
                                        "action": "update",
                                        "record": "incident",
                                        "targets": ["INC0000259"],
                                        "changes": {"priority": "High"},
                                    }
                                ]
                            },
                        )
                    ]
                }
            ]
        )

        with mock.patch.object(planner, "_planner_model", lambda: model):

            result = planner.extract(text, "2026-10-09", **kwargs)

        return result, model

    def test_structured_output_is_parsed(self):

        (draft, usage), _ = self.draft()

        self.assertEqual(draft.ops[0].targets, ["INC0000259"])
        self.assertEqual(draft.ops[0].changes, {"priority": "High"})
        self.assertGreater(usage["input_tokens"], 0)

    def test_the_call_carries_no_tools_but_its_own_schema(self):
        """Token budget: no domain tools, a short prompt, today's date and the request."""

        _, model = self.draft(context="incident INC0000259")

        request = model.requests[0]

        self.assertEqual(request["tools"], ["PlanDraft"])
        self.assertEqual(request["system_prompt"], planner.PLANNER_PROMPT)

        sent = str(request["last_message"])

        self.assertIn("Today: 2026-10-09", sent)
        self.assertIn("incident INC0000259", sent)
        self.assertIn("Request: Set priority to High on INC0000259", sent)

    def test_prompt_and_schema_stay_small(self):

        import json

        self.assertLess(len(planner.PLANNER_PROMPT), 1800)
        self.assertLess(len(json.dumps(PlanDraft.model_json_schema())), 2200)

    def test_a_failing_model_gives_no_draft_and_never_raises(self):

        model = ScriptedModel([{"raise": RuntimeError("model down")}])

        with mock.patch.object(planner, "_planner_model", lambda: model):

            draft, usage = planner.extract("x", "2026-10-09")

        self.assertIsNone(draft)
        self.assertEqual(usage, {})

    def test_no_model_configured_gives_no_draft(self):

        draft, _ = planner.extract("x", "2026-10-09")

        self.assertIsNone(draft)


# ============================================================
# SERVICES: DRY RUN WRITES NOTHING
# ============================================================

class DryRunTests(unittest.TestCase):

    def setUp(self):

        self.world = World().install()

        self.addCleanup(self.world.close)

    def test_incident_dry_run_previews_without_writing(self):

        result = self.world.incident_service.update_incident(
            "INC0000259", {"priority": "high", "urgency": "High"}, "tester", dry_run=True
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["changes"]["priority"], {"from": "P3-Medium", "to": "P2-High"})
        self.assertEqual(self.world.incidents.count("update_item"), 0)
        self.assertEqual(self.world.incidents.items["INC0000259"]["priority"], "P3-Medium")

    def test_dry_run_still_validates(self):

        result = self.world.incident_service.update_incident(
            "INC0000259", {"priority": "Urgent"}, "tester", dry_run=True
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_value")
        self.assertEqual(result["allowed"], ["P1-Critical", "P2-High", "P3-Medium"])

    def test_issue_dry_run(self):

        result = self.world.categories.update_issue("Issue-1", {"impact": "High"}, "tester", dry_run=True)

        self.assertEqual(result["changes"], {"impact": {"from": "Low", "to": "High"}})
        self.assertEqual(self.world.issues.count("update_item"), 0)

    def test_availability_dry_run(self):

        day = a_working_day()

        iso = f"{today().year}-{today().month:02d}-{day:02d}"

        result = self.world.people.set_employee_availability("Rohit", {iso: "leave"}, "tester", dry_run=True)

        self.assertEqual(result["changes"][iso], {"from": "Y", "to": "N"})
        self.assertEqual(self.world.roster.count("update_item"), 0)

    def test_roster_entry_dry_run(self):

        result = self.world.people.update_roster_entry("Rohit", {"shift": "S2"}, "tester", dry_run=True)

        self.assertEqual(result["changes"]["shift"], {"from": "S1", "to": "S2"})
        self.assertEqual(self.world.roster.count("update_item"), 0)
        self.assertEqual(self.world.roster.items["1"]["shift"], "S1")

    def test_dry_run_respects_read_only_mode(self):

        with mock.patch.object(common, "READ_ONLY_MODE", True):

            result = self.world.incident_service.update_incident(
                "INC0000259", {"priority": "High"}, "tester", dry_run=True
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_allowed")


if __name__ == "__main__":
    unittest.main()
