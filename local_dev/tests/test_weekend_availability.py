"""
Availability values in the roster: Y (available), N (not available, e.g. leave)
and NA (not scheduled: weekend / non-working day).
"""

import unittest

import agent
from services.common import today
from tests.fakes import World
from tests.test_conversations import Chat, ConversationTestCase


def set_days(world, uid, **days):
    """Replace one roster row's availability map."""

    world.roster.items[uid]["days_availability"] = dict(days)


class SummaryAndReadTests(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.svc = self.world.people

    def test_summary_counts_each_kind_of_day(self):

        set_days(self.world, "1", **{"1": "Y", "2": "NA", "3": "N", "4": "Y", "5": "NA", "6": "NA"})

        summary = self.svc.get_roster_entry("Rohit")["employee"]["availability_summary"]

        self.assertEqual(summary["working_days"], 2)
        self.assertEqual(summary["off_days"], [3])
        self.assertEqual(summary["non_working_days"], 3)

    def test_weekends_are_not_counted_as_leave(self):

        set_days(self.world, "1", **{"1": "Y", "2": "NA", "3": "NA"})

        summary = self.svc.get_roster_entry("Rohit")["employee"]["availability_summary"]

        self.assertEqual(summary["off_days"], [])

    def test_a_row_with_only_weekends_still_has_a_summary(self):

        set_days(self.world, "1", **{"6": "NA", "7": "NA"})

        self.assertIsNotNone(self.svc.get_roster_entry("Rohit")["employee"]["availability_summary"])

    def test_na_is_returned_as_recorded(self):

        day = today()

        set_days(self.world, "1", **{str(day.day): "NA"})

        result = self.svc.get_employee_availability("Rohit", "today")

        self.assertEqual(result["availability"], {day.isoformat(): "NA"})
        self.assertNotIn("no_roster_for", result)


class WriteWordingTests(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.svc = self.world.people
        self.day = today()

    def written(self, wording):

        self.svc.set_employee_availability("Rohit", {"today": wording})

        return self.world.roster.items["1"]["days_availability"][str(self.day.day)]

    def test_words_map_to_the_right_code(self):

        cases = {
            "available": "Y", "working": "Y", "Y": "Y",
            "leave": "N", "off": "N", "unavailable": "N", "sick leave": "N", "N": "N",
            "weekend": "NA", "NA": "NA", "n/a": "NA", "week off": "NA", "non-working day": "NA", "not applicable": "NA",
        }

        for wording, code in cases.items():
            self.assertEqual(self.written(wording), code, wording)

    def test_leave_and_weekend_are_different(self):

        self.assertNotEqual(self.written("leave"), self.written("weekend"))

    def test_unknown_wording_lists_all_three_values(self):

        result = self.svc.set_employee_availability("Rohit", {"today": "sailing"})

        self.assertEqual(result["error"], "invalid_value")

        for expected in ("Y (available)", "N (not available", "NA (weekend"):
            self.assertIn(expected, result["message"])

    def test_the_roster_already_using_na_is_understood(self):

        set_days(self.world, "1", **{"1": "Y", "2": "NA", "3": "N"})

        self.assertEqual(self.written("weekend"), "NA")

    def test_change_report_shows_the_old_and_new_code(self):

        set_days(self.world, "1", **{str(self.day.day): "NA"})

        result = self.svc.set_employee_availability("Rohit", {"today": "available"})

        self.assertEqual(result["changes"][self.day.isoformat()], {"from": "NA", "to": "Y"})


class ConversationTests(ConversationTestCase):

    def make_today(self, uid, code):
        self.world.roster.items[uid]["days_availability"][str(today().day)] = code

    def test_employee_on_a_weekend(self):

        self.make_today("1", "NA")

        text = self.chat.say("Is Rohit available today?")

        self.assertIn("isn't scheduled", text)
        self.assertIn("weekend / non-working day", text)
        self.assertNotIn("not available", text)

        structured = self.chat.last.structured

        self.assertEqual((structured["value"], structured["tone"]), ("Non-working day", "info"))

    def test_leave_is_still_not_available(self):

        self.make_today("1", "N")

        text = self.chat.say("Is Rohit available today?")

        self.assertIn("is **not available**", text)
        self.assertEqual(self.chat.last.structured["tone"], "warning")

    def test_team_with_a_weekend_member_and_a_leave_member(self):

        self.make_today("1", "Y")
        self.make_today("3", "NA")
        self.world.roster.items["11"]["days_availability"] = {}

        text = self.chat.say("Who is available today in Access Management?")

        self.assertIn("Available (1): Rohit Malhotra", text)
        self.assertIn("Non-working day (1): Priya Nair", text)
        self.assertNotIn("Not available", text)

        rows = {row["Name"]: row["Availability"] for row in self.chat.last.structured["rows"]}

        self.assertEqual(rows, {"Rohit Malhotra": "Available", "Priya Nair": "Non-working day"})

    def test_a_weekend_for_the_whole_team(self):

        self.make_today("1", "NA")
        self.make_today("3", "NA")

        text = self.chat.say("Who is available today in Access Management?")

        self.assertIn("non-working day", text)
        self.assertIn("nobody is scheduled", text)
        self.assertEqual(self.llm_calls, [])

    def test_roster_card_mentions_weekends(self):

        set_days(self.world, "1", **{"1": "Y", "2": "NA", "3": "N", "4": "Y", "5": "NA"})

        text = self.chat.say("Show Rohit's roster")

        self.assertIn("2 working days; off on: 3; 2 weekend / non-working days", text)

    def test_the_model_is_told_what_na_means(self):

        self.assertIn("NA=weekend", agent.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
