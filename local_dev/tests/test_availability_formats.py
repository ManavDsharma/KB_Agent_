"""
days_availability as it may really be stored: a DynamoDB map, JSON text,
zero-padded keys, lowercase / spelled-out values, a list.
"""

import json
import unittest

from services.roster_service import decode_days, key_for_day, normalize_code
from services.common import today
from tests.fakes import World
from tests.test_conversations import ConversationTestCase


class DecodeTests(unittest.TestCase):

    def test_map(self):
        codes, _, kind = decode_days({"1": "Y", "2": "N", "3": "NA"})
        self.assertEqual(codes, {1: "Y", 2: "N", 3: "NA"})
        self.assertEqual(kind, "map")

    def test_json_text(self):
        codes, _, kind = decode_days('{"1": "Y", "2": "N"}')
        self.assertEqual(codes, {1: "Y", 2: "N"})
        self.assertEqual(kind, "string")

    def test_single_quoted_text(self):
        codes, _, kind = decode_days("{'1': 'Y', '2': 'N'}")
        self.assertEqual(codes, {1: "Y", 2: "N"})
        self.assertEqual(kind, "string")

    def test_bytes(self):
        self.assertEqual(decode_days(b'{"4": "NA"}')[0], {4: "NA"})

    def test_padded_keys_and_loose_values(self):
        codes, _, _ = decode_days({"01": "y", "02": " Yes ", "03": "n/a", "04": "No"})
        self.assertEqual(codes, {1: "Y", 2: "Y", 3: "NA", 4: "N"})

    def test_list_starts_at_day_one(self):
        self.assertEqual(decode_days(["Y", "N"])[0], {1: "Y", 2: "N"})

    def test_garbage_gives_no_days(self):
        for raw in (None, "", "not json", 42, "[1, 2", {"abc": "Y"}):
            self.assertEqual(decode_days(raw)[0], {}, raw)

    def test_missing_versus_empty(self):
        self.assertEqual(decode_days(None)[2], "missing")
        self.assertEqual(decode_days({})[2], "map")
        self.assertEqual(decode_days("")[2], "string")

    def test_key_keeps_the_existing_spelling(self):
        self.assertEqual(key_for_day({"05": "Y"}, 5), "05")
        self.assertEqual(key_for_day({"1": "Y"}, 5), "5")

    def test_normalize_code(self):
        self.assertEqual(
            [normalize_code(v) for v in ("y", "YES", "n", "No", "na", "N/A", "X")],
            ["Y", "Y", "N", "N", "NA", "NA", "X"],
        )


class StoredAsTextTests(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.svc = self.world.people
        self.day = today()

    @property
    def row(self):
        # the fake table replaces the stored item on every write
        return self.world.roster.items["1"]

    def store_text(self, **days):
        self.row["days_availability"] = json.dumps(days)

    def test_reads_text_column(self):
        self.store_text(**{str(self.day.day): "N"})

        result = self.svc.get_employee_availability("Rohit", "today")

        self.assertEqual(result["availability"], {self.day.isoformat(): "N"})
        self.assertNotIn("no_roster_for", result)

    def test_reads_padded_lowercase(self):
        self.row["days_availability"] = {f"{self.day.day:02d}": "y"}

        result = self.svc.get_employee_availability("Rohit", "today")

        self.assertEqual(result["availability"], {self.day.isoformat(): "Y"})

    def test_summary_from_text(self):
        self.store_text(**{"1": "Y", "2": "N", "3": "NA"})

        summary = self.svc.get_roster_entry("Rohit")["employee"]["availability_summary"]

        self.assertEqual((summary["working_days"], summary["off_days"], summary["non_working_days"]), (1, [2], 1))

    def test_team_reads_text(self):
        self.store_text(**{str(self.day.day): "Y"})

        result = self.svc.get_team_availability("Access Management", "today")

        names = [m["name"] for m in result["members"] if m["availability"] == "Y"]

        self.assertIn("Rohit Malhotra", names)

    def test_write_keeps_text_and_other_days(self):
        self.store_text(**{"1": "Y", "2": "Y"})

        result = self.svc.set_employee_availability("Rohit", {"today": "leave"})

        self.assertTrue(result.get("ok", True), result)

        stored = self.row["days_availability"]

        self.assertIsInstance(stored, str)

        parsed = json.loads(stored)

        self.assertEqual(parsed[str(self.day.day)], "N")

        if self.day.day not in (1, 2):
            self.assertEqual((parsed["1"], parsed["2"]), ("Y", "Y"))

    def test_write_reports_previous_value_from_text(self):
        self.store_text(**{str(self.day.day): "NA"})

        result = self.svc.set_employee_availability("Rohit", {"today": "available"})

        self.assertEqual(result["changes"][self.day.isoformat()], {"from": "NA", "to": "Y"})

    def test_write_keeps_padded_key(self):
        self.row["days_availability"] = {f"{self.day.day:02d}": "Y"}

        self.svc.set_employee_availability("Rohit", {"today": "leave"})

        self.assertEqual(self.row["days_availability"], {f"{self.day.day:02d}": "N"})

    def test_empty_text_column_can_be_written(self):
        self.row["days_availability"] = ""

        self.svc.set_employee_availability("Rohit", {"today": "leave"})

        self.assertEqual(json.loads(self.row["days_availability"]), {str(self.day.day): "N"})

    def test_missing_column_is_created(self):
        del self.row["days_availability"]

        result = self.svc.set_employee_availability("Rohit", {"today": "leave"})

        self.assertNotIn("error", result)
        self.assertEqual(self.row["days_availability"][str(self.day.day)], "N")

    def test_text_write_retries_after_a_concurrent_change(self):
        self.store_text(**{"1": "Y"})

        table = self.world.roster
        original = table.update_item
        state = {"raced": False}
        world = self.world

        def racing(*args, **kwargs):
            if not state["raced"]:
                state["raced"] = True
                world.roster.items["1"]["days_availability"] = json.dumps({"1": "Y", "9": "N"})
            return original(*args, **kwargs)

        table.update_item = racing

        self.svc.set_employee_availability("Rohit", {"today": "leave"})

        parsed = json.loads(self.row["days_availability"])

        self.assertEqual(parsed[str(self.day.day)], "N")
        self.assertEqual(parsed["9"], "N")

    def test_team_write_on_text_rows(self):
        for uid in ("1", "3"):
            self.world.roster.items[uid]["days_availability"] = "{}"

        result = self.svc.set_team_availability("Access Management", "today", "leave")

        self.assertTrue(result["ok"], result)

        for uid in ("1", "3"):
            self.assertEqual(json.loads(self.world.roster.items[uid]["days_availability"]), {str(self.day.day): "N"})


class ConversationTests(ConversationTestCase):

    def test_availability_question_with_text_storage(self):
        self.world.roster.items["1"]["days_availability"] = json.dumps({str(today().day): "N"})

        text = self.chat.say("Is Rohit available today?")

        self.assertIn("not available", text)
        self.assertEqual(self.llm_calls, [])

    def test_availability_question_with_padded_keys(self):
        self.world.roster.items["1"]["days_availability"] = {f"{today().day:02d}": "Y"}

        text = self.chat.say("Is Rohit available today?")

        self.assertIn("available", text)
        self.assertNotIn("no roster", text.lower())


if __name__ == "__main__":
    unittest.main()
