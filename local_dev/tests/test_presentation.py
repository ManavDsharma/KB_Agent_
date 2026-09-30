"""
What the frontend receives: cards, badges, sentences, follow-up chips.
"""

import unittest

import presentation
from router import DeterministicRouter
from tests.test_conversations import Chat, ConversationTestCase
from tests.fakes import second_rohit

ALLOWED_TYPES = {"text", "record", "field", "table", "list", "update_confirmation", "question", "error"}
ALLOWED_TONES = {"info", "success", "warning", "error"}


class ToneTests(unittest.TestCase):

    def test_priority_tone(self):

        self.assertEqual(presentation.priority_tone("P1-Critical"), "critical")
        self.assertEqual(presentation.priority_tone("P2-High"), "warning")
        self.assertEqual(presentation.priority_tone("P3-Medium"), "info")
        self.assertEqual(presentation.priority_tone("P4-Low"), "neutral")
        self.assertEqual(presentation.priority_tone(None), "neutral")

    def test_state_tone(self):

        self.assertEqual(presentation.state_tone("Closed"), "success")
        self.assertEqual(presentation.state_tone("Resolved"), "success")
        self.assertEqual(presentation.state_tone("In Progress"), "info")
        self.assertEqual(presentation.state_tone("On Hold"), "warning")
        self.assertEqual(presentation.state_tone("Cancelled"), "neutral")


class IncidentCardTests(ConversationTestCase):

    def test_incident_view_is_a_card(self):

        self.chat.say("Show me INC0000258")

        card = self.chat.last.structured

        self.assertEqual(card["type"], "record")
        self.assertEqual(card["entity"], {"kind": "incident", "id": "INC0000258"})
        self.assertEqual(card["title"], "INC0000258")
        self.assertEqual(card["subtitle"], "Database Failure - Database")

        badges = {badge["label"]: badge["tone"] for badge in card["badges"]}

        self.assertEqual(badges["P1-Critical"], "critical")
        self.assertEqual(badges["Closed"], "success")
        self.assertEqual(card["link"], {"label": "Open in ITSM", "url": "https://abc.com/itsm/ticket/INC0000258"})

        titles = [section["title"] for section in card["sections"]]

        self.assertEqual(titles, ["People", "Classification", "SLA", "Timeline", "Details"])

        people = {item["label"]: item["value"] for item in card["sections"][0]["items"]}

        self.assertEqual(people["Assigned to"], "varun.nambiar@abc.com")

    def test_the_card_leads_with_a_sentence(self):

        text = self.chat.say("Show me INC0000258")

        self.assertTrue(text.startswith("Here's **INC0000258** — Database Failure - Database."))
        self.assertIn("**P1-Critical** and **Closed**, assigned to varun.nambiar@abc.com.", text)
        self.assertEqual(self.chat.last.structured["summary"], text.split("\n\n")[0])

    def test_markdown_alone_is_still_complete(self):
        """A client that only renders Markdown loses nothing."""

        text = self.chat.say("Show me INC0000258")

        for expected in ("Assigned to: varun.nambiar@abc.com", "Assignment SLA (min): 60", "Created at: 2025-03-14 10:10"):
            self.assertIn(expected, text)

    def test_an_sla_breach_gets_a_red_badge(self):

        self.chat.say("Show me REQ0000162")

        badges = {badge["label"]: badge["tone"] for badge in self.chat.last.structured["badges"]}

        self.assertEqual(badges["Resolution SLA breached"], "critical")

    def test_an_unassigned_ticket_says_so(self):

        text = self.chat.say("Show me INC0000259")

        self.assertIn("not assigned to anyone yet", text)

    def test_no_empty_sections(self):

        self.chat.say("Show me INC0000259")

        for section in self.chat.last.structured["sections"]:
            self.assertTrue(section["items"], section["title"])



class FieldAnswerTests(ConversationTestCase):

    def test_single_field_answers_are_sentences(self):

        chat = self.chat

        chat.say("Show me INC0000258")

        cases = {
            "Who is assigned to it?": "**INC0000258** is assigned to **varun.nambiar@abc.com**.",
            "What is its priority?": "**INC0000258** has priority **P1-Critical**.",
            "Who opened it?": "**INC0000258** was opened by **simran.gupta@abc.com**.",
            "What is the status?": "**INC0000258** is **Closed**.",
        }

        for question, sentence in cases.items():

            self.assertEqual(chat.say(question), sentence, question)

            structured = chat.last.structured

            self.assertEqual(structured["type"], "field")
            self.assertEqual(structured["entity"], {"kind": "incident", "id": "INC0000258"})
            self.assertEqual(structured["text"], sentence)

    def test_issue_and_employee_sentences(self):

        chat = self.chat

        chat.say("Show me Issue-1")

        self.assertEqual(chat.say("Which team handles it?"), "**Issue-1** is owned by **Identity Operations**.")
        self.assertEqual(chat.say("What type of request is it?"), "**Issue-1** is a **Service Request**.")

        chat.say("Show Rohit's roster")

        self.assertEqual(chat.say("What is his shift?"), "**Rohit Malhotra** works the **S1** shift.")

    def test_availability_answer(self):

        chat = self.chat

        chat.say("Show Rohit's roster")

        chat.say("Is he available today?")

        structured = chat.last.structured

        self.assertEqual((structured["type"], structured["value"], structured["tone"]), ("field", "Available", "success"))

        chat.say("Is Priya available today?")

        self.assertEqual(chat.last.structured["tone"], "warning")

    def test_multi_field_answers_are_a_record(self):

        self.chat.say("Show me INC0000258")

        self.chat.say("What is the SLA?")

        structured = self.chat.last.structured

        self.assertEqual(structured["type"], "record")
        self.assertIn("Here are the details for **INC0000258**", structured["summary"])
        self.assertEqual({f["label"] for f in structured["fields"]}, {"Assignment SLA (min)", "Resolution SLA (min)"})


class EmployeeAndTeamCardTests(ConversationTestCase):

    def test_employee_card(self):

        text = self.chat.say("Show Rohit's roster")

        card = self.chat.last.structured

        self.assertEqual(card["entity"], {"kind": "employee", "id": "emp:1"})
        self.assertEqual({b["label"] for b in card["badges"]}, {"L2", "Shift S1"})
        self.assertTrue(text.startswith("**Rohit Malhotra** is level **L2** on the **S1** shift in Access Management Team."))
        self.assertEqual([s["title"] for s in card["sections"]], ["Role", "Contact", "Availability"])

    def test_team_availability_has_a_summary(self):

        text = self.chat.say("Who is available today in Access Management?")

        self.assertTrue(text.startswith("On **"))
        self.assertIn("1 of 2 in **Access Management Team** are available", text)
        self.assertEqual(self.chat.last.structured["shown"], 2)


class SuggestionTests(ConversationTestCase):
    """Chips must be things the agent actually understands."""

    def assert_chips_work(self, first_message, expected_at_least=1):

        chat = self.chat

        chat.say(first_message)

        suggestions = chat.last.structured.get("suggestions", [])

        self.assertGreaterEqual(len(suggestions), expected_at_least, first_message)

        for chip in suggestions:

            self.assertEqual(set(chip), {"label", "send"})

            fresh = Chat(self.world)

            fresh.say(first_message)

            reply = fresh.say(chip["send"])

            self.assertNotEqual(fresh.meta["route"], "llm", f"{first_message!r} -> chip {chip['send']!r}")
            self.assertTrue(fresh.meta["direct"], chip["send"])
            self.assertTrue(reply)

        return suggestions

    def test_incident_chips(self):

        chips = self.assert_chips_work("Show me INC0000258", 3)

        self.assertIn("Update it", [chip["send"] for chip in chips])

    def test_chips_do_not_repeat_the_question_just_answered(self):

        chat = self.chat

        chat.say("Show me INC0000258")
        chat.say("Who is assigned to it?")

        self.assertNotIn("Who is assigned to it?", [c["send"] for c in chat.last.structured["suggestions"]])

    def test_issue_chips(self):
        self.assert_chips_work("Show me Issue-1", 3)

    def test_employee_chips(self):

        self.assert_chips_work("Show Rohit's roster", 3)
        self.assert_chips_work("What is Rohit's shift?", 1)

    def test_availability_chips(self):

        chat = self.chat

        chat.say("Show Rohit's roster")
        chat.say("Is he available today?")

        for chip in chat.last.structured["suggestions"]:

            fresh = Chat(self.world)

            fresh.say("Show Rohit's roster")
            fresh.say("Is he available today?")
            fresh.say(chip["send"])

            self.assertTrue(fresh.meta["direct"], chip["send"])

    def test_team_chips(self):
        self.assert_chips_work("Who is available today in Access Management?", 2)

    def test_update_chips(self):

        chat = self.chat

        chat.say("Show me INC0000258")
        chat.say("Set priority to P2")

        chips = chat.last.structured["suggestions"]

        self.assertEqual([c["send"] for c in chips], ["Show it", "Who is assigned to it?"])

        for chip in chips:

            chat.say(chip["send"])

            self.assertTrue(chat.meta["direct"], chip["send"])

    def test_list_chips_open_the_rows(self):

        chat = self.chat

        chat.say("List SLA breaches")

        chips = chat.last.structured["suggestions"]

        self.assertTrue(chips)

        for chip in chips:

            self.assertTrue(chip["send"].startswith("Show "))

            chat.say(chip["send"])

            self.assertEqual(chat.last.structured["type"], "record")

    def test_no_chips_on_errors_or_model_answers(self):

        self.chat.say("Show me INC0009999")

        self.assertNotIn("suggestions", self.chat.last.structured)


class QuestionTests(ConversationTestCase):

    def test_options_are_tap_targets(self):

        chat = self.make_world(extra_people=[second_rohit()])

        chat.say("What is Rohit's shift?")

        structured = chat.last.structured

        self.assertEqual(structured["type"], "question")
        self.assertTrue(structured["clarification"])
        self.assertEqual([o["send"] for o in structured["options"]], ["1", "2"])
        self.assertIn("Rohit Sharma", structured["options"][1]["label"])

        # Tapping an option sends its value.
        chat.say(structured["options"][1]["send"])

        self.assertIn("Rohit Sharma", chat.last.text)

    def test_plain_questions_have_no_options(self):

        chat = self.chat

        chat.say("Update it.")

        self.assertEqual(chat.last.structured["type"], "question")
        self.assertNotIn("options", chat.last.structured)


class ContractTests(ConversationTestCase):
    """Every direct reply obeys the same envelope."""

    MESSAGES = [
        "Show me INC0000258", "Who is assigned to it?", "What is the SLA?", "Update it.",
        "Set priority to P2.", "Show me Issue-1", "Which team handles it?", "Show Rohit's roster",
        "Is he available today?", "Who is available today in Access Management?", "List SLA breaches",
        "incidents today", "list all categories", "Show me INC0009999", "Show me the incident.",
        "Delete INC0000259",
    ]

    def test_every_reply_has_type_text_and_tone(self):

        chat = Chat(self.world)

        for message in self.MESSAGES:

            chat.say(message)

            structured = chat.last.structured

            self.assertIn(structured["type"], ALLOWED_TYPES, message)
            self.assertIn(structured["tone"], ALLOWED_TONES, message)
            self.assertEqual(structured["text"], chat.last.text, message)

    def test_payload_shape(self):

        self.chat.say("Show me INC0000258")

        payload = self.chat.last.as_payload()

        self.assertEqual(set(payload), {"response", "structured"})
        self.assertEqual(payload["response"], payload["structured"]["text"])

    def test_update_and_error_tones(self):

        chat = self.chat

        chat.say("Update INC0000258 priority to P2")

        self.assertEqual(chat.last.structured["tone"], "success")
        self.assertEqual(chat.last.structured["entity"], {"kind": "incident", "id": "INC0000258"})

        chat.say("Show me INC0009999")

        self.assertEqual(chat.last.structured["tone"], "error")

    def test_model_answers_declare_markdown(self):

        chat = self.chat

        chat.say("Why did INC0000258 breach its SLA?")

        self.assertEqual(chat.last.structured, {"type": "text", "format": "markdown", "text": "LLM ANSWER", "tone": "info"})


if __name__ == "__main__":
    unittest.main()
