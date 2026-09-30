import unittest

from services.ids import (
    find_issue_ref,
    find_ticket_ref,
    issue_key,
    normalize_issue_id,
    ticket_candidates,
    ticket_key_of,
)
from services.schema import (
    EMPLOYEE_ALIASES,
    INCIDENT_ALIASES,
    ISSUE_ALIASES,
    detect_fields,
    resolve_field_names,
)


class IdentifierTests(unittest.TestCase):

    def test_issue_formats(self):

        for text in ("Issue-9", "Issue9", "Issue 9", "issue_9", "show ISSUE 9 please"):
            self.assertEqual(find_issue_ref(text)[0], "Issue-9", text)

    def test_issue_not_confused_with_words(self):
        self.assertIsNone(find_issue_ref("what is the issue with printing"))

    def test_ticket_formats(self):

        for text in (
            "INC0000258",
            "INC-0000258",
            "INC 0000258",
            "inc0000258",
            "show me incident 0000258",
        ):
            prefix, digits, _ = find_ticket_ref(text)
            self.assertEqual((prefix, int(digits)), ("INC", 258), text)

    def test_request_formats(self):

        for text in ("REQ0000162", "REQ-0000162", "REQ 0000162", "request 162"):
            prefix, digits, _ = find_ticket_ref(text)
            self.assertEqual((prefix, int(digits)), ("REQ", 162), text)

    def test_no_false_ticket(self):

        self.assertIsNone(find_ticket_ref("since 2024 nothing happened"))
        self.assertIsNone(find_ticket_ref("Show me Issue-9"))

    def test_keys_ignore_hyphen_and_padding(self):

        self.assertEqual(ticket_key_of("INC-0000258"), ticket_key_of("inc258"))
        self.assertEqual(issue_key("Issue 09"), issue_key("issue-9"))

    def test_candidates_cover_common_spellings(self):

        keys = ticket_candidates("INC", "258")

        self.assertIn("INC0000258", keys)
        self.assertIn("INC-0000258", keys)
        self.assertEqual(keys[0], "INC258")

    def test_typed_form_tried_first(self):
        self.assertEqual(ticket_candidates("INC", "0000258")[0], "INC0000258")

    def test_normalize_issue_id(self):

        self.assertEqual(normalize_issue_id("9"), "Issue-9")
        self.assertEqual(normalize_issue_id("Issue9"), "Issue-9")


class FieldDetectionTests(unittest.TestCase):

    def test_incident_fields(self):

        self.assertEqual(detect_fields("who is assigned to it", INCIDENT_ALIASES), ["assigned_to"])
        self.assertEqual(detect_fields("what is the status", INCIDENT_ALIASES), ["state"])
        self.assertEqual(detect_fields("who opened it", INCIDENT_ALIASES), ["opened_by"])

    def test_sla_and_breach(self):

        self.assertEqual(
            detect_fields("what is the sla", INCIDENT_ALIASES),
            ["assignment_sla", "resolution_sla"],
        )
        self.assertEqual(
            detect_fields("is it breached", INCIDENT_ALIASES),
            ["has_assignment_sla_breach", "has_resolution_sla_breach"],
        )

    def test_longest_phrase_wins(self):

        self.assertEqual(detect_fields("sub category", INCIDENT_ALIASES), ["sub_category"])
        self.assertEqual(detect_fields("short description", INCIDENT_ALIASES), ["short_description"])
        self.assertEqual(detect_fields("business impact", ISSUE_ALIASES), ["business_impact"])

    def test_bare_word_issue_is_not_a_field(self):
        """The old router treated the word 'issue' as the issue-name field."""

        self.assertEqual(detect_fields("show me ENT", ISSUE_ALIASES), [])

    def test_issue_fields(self):

        self.assertEqual(detect_fields("which team handles it", ISSUE_ALIASES), ["team_id"])
        self.assertEqual(detect_fields("what type of request is it", ISSUE_ALIASES), ["issue_type"])

    def test_employee_fields(self):

        self.assertEqual(detect_fields("what is his shift", EMPLOYEE_ALIASES), ["shift"])
        self.assertEqual(
            detect_fields("shift timings", EMPLOYEE_ALIASES),
            ["shift", "shift_start_time", "shift_end_time"],
        )

    def test_resolve_field_names(self):

        self.assertEqual(resolve_field_names("incident", ["assignee"]), ["assigned_to"])
        self.assertEqual(resolve_field_names("incident", ["priority", "nonsense"]), ["priority"])
        self.assertEqual(resolve_field_names("issue", ["team"]), ["team_id"])


if __name__ == "__main__":
    unittest.main()
