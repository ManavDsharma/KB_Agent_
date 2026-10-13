"""
The pure helpers that keep a model's output honest and read short answers:
grounding checks, dates, yes/no, choices. No state, no AWS.
"""

import unittest
from contextlib import contextmanager
from datetime import date
from unittest import mock

import plan_text
from plan_text import (
    apply_answer,
    clean_reply_value,
    date_grounded,
    field_grounded,
    grounded_value,
    is_no,
    is_pronoun,
    is_yes,
    name_grounded,
    names_in_reply,
    parse_dates_reply,
    pick_option,
)
from services import common
from services.common import ServiceError, parse_user_date
from services.ids import find_all_issue_refs, find_all_ticket_refs, typed_ticket_ids

TODAY = date(2026, 10, 9)


@contextmanager
def frozen_today():

    with mock.patch.object(common, "today", return_value=TODAY), mock.patch.object(
        plan_text, "today", return_value=TODAY
    ):
        yield


class DateParsingTests(unittest.TestCase):

    def test_iso_and_relative(self):

        with frozen_today():

            self.assertEqual(parse_user_date("2026-08-02"), ("2026-08-02", False))
            self.assertEqual(parse_user_date("today"), ("2026-10-09", False))
            self.assertEqual(parse_user_date("tomorrow"), ("2026-10-10", False))
            self.assertEqual(parse_user_date("day after tomorrow"), ("2026-10-11", False))

    def test_day_and_month_names_guess_the_year(self):

        with frozen_today():

            for text in ("2nd august", "2 Aug", "August 2", "aug 2nd", "the 2nd of August", "2nd of august"):
                self.assertEqual(parse_user_date(text), ("2026-08-02", True), text)

    def test_explicit_year_is_not_a_guess(self):

        with frozen_today():
            self.assertEqual(parse_user_date("2nd August 2025"), ("2025-08-02", False))

    def test_nonsense_is_rejected_not_guessed(self):

        with frozen_today():

            for text in ("someday", "31st february", "2nd smarch", "", "13/13/2026"):

                with self.assertRaises(ServiceError, msg=text):
                    parse_user_date(text)


class DatesInRepliesTests(unittest.TestCase):

    def test_single_list_and_range(self):

        with frozen_today():

            self.assertEqual(parse_dates_reply("2nd August"), (["2026-08-02"], True))
            self.assertEqual(parse_dates_reply("1st and 4th August"), (["2026-08-01", "2026-08-04"], True))
            self.assertEqual(parse_dates_reply("2026-08-02, 2026-08-04"), (["2026-08-02", "2026-08-04"], False))
            self.assertEqual(
                parse_dates_reply("3rd to 5th August"),
                (["2026-08-03", "2026-08-04", "2026-08-05"], True),
            )
            self.assertEqual(
                parse_dates_reply("2026-08-03 to 2026-08-05")[0],
                ["2026-08-03", "2026-08-04", "2026-08-05"],
            )

    def test_not_a_date_is_not_an_answer(self):

        with frozen_today():

            for text in ("high", "Rohit", "set priority to high", ""):
                self.assertIsNone(parse_dates_reply(text), text)

    def test_reversed_or_huge_ranges_are_not_answers(self):

        with frozen_today():

            self.assertIsNone(parse_dates_reply("5th to 3rd August"))
            self.assertIsNone(parse_dates_reply("1st January to 31st December"))


class GroundingTests(unittest.TestCase):

    def test_values_must_be_written_by_the_user(self):

        text = "Set priority to High on INC0000259"

        self.assertTrue(grounded_value("High", text))
        self.assertTrue(grounded_value("high", text))
        self.assertFalse(grounded_value("P1-Critical", text))
        self.assertFalse(grounded_value("Critical", text))
        self.assertFalse(grounded_value("", text))

    def test_word_stems_count(self):

        self.assertTrue(grounded_value("Closed", "please close it"))
        self.assertTrue(grounded_value("Resolved", "resolve INC1"))
        self.assertFalse(grounded_value("Cancelled", "please close it"))

    def test_fields_must_be_referred_to(self):

        text = "Set both priority and urgency of INC0000259 to High"

        self.assertTrue(field_grounded("incident", "priority", text))
        self.assertTrue(field_grounded("incident", "urgency", text))
        self.assertFalse(field_grounded("incident", "category", text))
        self.assertFalse(field_grounded("incident", "assigned_to", text))

    def test_verbs_stand_for_state_and_assignee(self):

        self.assertTrue(field_grounded("incident", "state", "close INC1"))
        self.assertTrue(field_grounded("incident", "assigned_to", "assign INC1 to Rohit"))
        self.assertFalse(field_grounded("incident", "state", "show INC1"))

    def test_employee_and_issue_fields(self):

        self.assertTrue(field_grounded("employee", "shift", "change Rohit's shift to S2"))
        self.assertTrue(field_grounded("employee", "team_id", "move Rohit to the Infrastructure team"))
        self.assertTrue(field_grounded("issue", "impact", "set the impact of Issue-3 to High"))
        self.assertFalse(field_grounded("issue", "impact", "set the urgency of Issue-3 to High"))

    def test_names_must_appear_word_for_word(self):

        text = "mark Rohit and Priya unavailable"

        self.assertTrue(name_grounded("Rohit", text))
        self.assertFalse(name_grounded("Rohit Malhotra", text))
        self.assertFalse(name_grounded("Roh", text))

    def test_dates_must_be_written_or_relative(self):

        with frozen_today():

            self.assertTrue(date_grounded("2026-08-02", "unavailable on 2nd August"))
            self.assertTrue(date_grounded("2026-08-02", "unavailable on 2026-08-02"))
            self.assertTrue(date_grounded("2026-10-10", "unavailable tomorrow"))
            self.assertTrue(date_grounded("2026-10-09", "unavailable today"))

            # a date the model computed for itself
            self.assertFalse(date_grounded("2026-12-25", "unavailable tomorrow"))
            self.assertFalse(date_grounded("2026-08-03", "unavailable on 2nd August"))
            self.assertFalse(date_grounded("2026-09-02", "unavailable on 2nd August"))

    def test_pronouns(self):

        for word in ("it", "he", "She", "that", "this"):
            self.assertTrue(is_pronoun(word), word)

        self.assertFalse(is_pronoun("INC0000258"))


class AnswerTests(unittest.TestCase):

    def test_yes_and_no(self):

        for text in ("yes", "Yes please", "ok", "Confirm", "go ahead", "do it", "sure", "yep"):
            self.assertTrue(is_yes(text), text)

        for text in ("no", "cancel", "Cancel it", "never mind", "stop", "don't"):
            self.assertTrue(is_no(text), text)

    def test_other_text_is_neither(self):

        for text in (
            "yes but change the date to 3rd August",
            "no wait, make it P1",
            "show INC0000258",
            "set priority to high",
            "",
        ):
            self.assertFalse(is_yes(text), text)
            self.assertFalse(is_no(text), text)

    def test_pick_option(self):

        options = ["P1-Critical", "P2-High", "P3-Medium"]

        self.assertEqual(pick_option("2", options), "P2-High")
        self.assertEqual(pick_option("the third", options), "P3-Medium")
        self.assertEqual(pick_option("medium", options), "P3-Medium")
        self.assertIsNone(pick_option("7", options))
        self.assertIsNone(pick_option("p", options))

    def test_pick_person_by_name(self):

        people = [
            {"ref": "emp:1", "employee_name": "Rohit Malhotra", "team_id": "Access Management"},
            {"ref": "emp:2", "employee_name": "Rohit Sharma", "team_id": "Infrastructure"},
        ]

        self.assertEqual(pick_option("Sharma", people)["ref"], "emp:2")
        self.assertEqual(pick_option("the one in Access Management", people, ["the", "one", "in"])["ref"], "emp:1")
        self.assertIsNone(pick_option("Rohit", people))

    def test_names_in_a_reply(self):

        self.assertEqual(names_in_reply("Rohit"), ["Rohit"])
        self.assertEqual(names_in_reply("it's Rohit Malhotra"), ["Rohit Malhotra"])
        self.assertEqual(names_in_reply("Rohit and Priya"), ["Rohit", "Priya"])
        self.assertEqual(names_in_reply("set priority to high"), [])
        self.assertEqual(names_in_reply("who?"), [])

    def test_value_replies(self):

        self.assertEqual(clean_reply_value("High"), "High")
        self.assertEqual(clean_reply_value("to High."), "High")
        self.assertEqual(clean_reply_value("it should be P2"), "P2")

    def test_apply_answer_fills_each_kind_of_slot(self):

        plan = {
            "text": "x",
            "ops": [
                {"action": "update", "record": "incident", "target": None, "changes": {"priority": ""},
                 "dates": [], "value": "", "inferred": False, "past": False},
            ],
        }

        by_target = apply_answer(plan, {"kind": "target", "record": "incident"}, "INC0000259 and INC0000260")

        self.assertEqual([op["target"] for op in by_target["ops"]], ["INC0000259", "INC0000260"])

        # the original plan is never modified
        self.assertIsNone(plan["ops"][0]["target"])

        by_value = apply_answer(by_target, {"kind": "value", "op": 0, "field": "priority"}, "High")

        self.assertEqual(by_value["ops"][0]["changes"]["priority"], "High")

        self.assertIsNone(apply_answer(by_target, {"kind": "value", "op": 0, "field": "priority"}, "set state to Closed"))
        self.assertIsNone(apply_answer(by_target, {"kind": "target", "record": "incident"}, "what do you mean?"))
        self.assertIsNone(apply_answer(by_target, {"kind": "target", "record": "incident"}, "high"))


class IdHelperTests(unittest.TestCase):

    def test_every_ticket_in_a_message(self):

        self.assertEqual(find_all_ticket_refs("close INC0000259 and INC-0000260"), ["INC259", "INC260"])
        self.assertEqual(find_all_ticket_refs("INC0000259 INC0000259"), ["INC259"])
        self.assertEqual(find_all_ticket_refs("incident 258 and request 162"), ["INC258", "REQ162"])
        self.assertEqual(find_all_ticket_refs("nothing here"), [])

    def test_typed_form_is_kept(self):

        self.assertEqual(typed_ticket_ids("close inc0000259"), [("INC259", "INC0000259")])

    def test_long_ids(self):

        refs = typed_ticket_ids("Set priority of INC20261007101849134675 to High")

        self.assertEqual(refs[0][1], "INC20261007101849134675")

    def test_every_issue(self):

        self.assertEqual(find_all_issue_refs("Issue-1 and issue 3 and Issue1"), ["Issue-1", "Issue-3"])


if __name__ == "__main__":
    unittest.main()
