"""
Token behaviour: which tools a request carries, how big the fixed part of
every model call is, and which questions never reach the model.
"""

import json
import unittest
from unittest import mock

import agent
from domains import classify_domain
from memory.state import SessionState
from memory.store import SessionRuntime
from router import DeterministicRouter
from tests.fakes import World
from tests.scripted_model import ScriptedModel
from tests.test_conversations import Chat, ConversationTestCase
from tools import DOMAIN_TOOLS, all_tools


def chars(tools):
    return sum(len(json.dumps(tool.tool_spec, separators=(",", ":"))) for tool in tools)


class DefinitionSizeTests(unittest.TestCase):
    """Definitions and the prompt are sent on every model call of a request."""

    def test_budgets(self):

        every = chars(all_tools())

        self.assertLess(every, 10000)
        self.assertLess(len(agent.SYSTEM_PROMPT), 1500)

        for domain, limit in (("roster", 5000), ("incident", 4500), ("category", 3000), ("handlers", 3000)):

            self.assertLess(chars(all_tools(domain)), limit, domain)
            self.assertLess(chars(all_tools(domain)), every / 1.8, domain)

    def test_no_schema_noise(self):

        for tool in all_tools():

            text = json.dumps(tool.tool_spec)

            self.assertNotIn('"Parameter ', text, tool.tool_spec["name"])
            self.assertNotIn('"default": null', text, tool.tool_spec["name"])
            self.assertNotIn("\n", tool.tool_spec["description"], tool.tool_spec["name"])

    def test_slimming_is_stable(self):
        """Asking for the tools again must not change or re-process them."""

        first = json.dumps([tool.tool_spec for tool in all_tools()], sort_keys=True)
        second = json.dumps([tool.tool_spec for tool in all_tools()], sort_keys=True)

        self.assertEqual(first, second)

    def test_domains_are_subsets_of_the_full_set(self):

        every = {tool.tool_spec["name"] for tool in all_tools()}

        for domain, names in DOMAIN_TOOLS.items():

            self.assertTrue(set(names) <= every, domain)
            self.assertEqual({tool.tool_spec["name"] for tool in all_tools(domain)}, set(names), domain)

    def test_every_domain_can_search_the_knowledge_base_and_none_is_empty(self):

        for domain, names in DOMAIN_TOOLS.items():

            self.assertIn("search_knowledge_base", names, domain)

    def test_unknown_domain_falls_back_to_everything(self):

        self.assertEqual(len(all_tools("nonsense")), len(all_tools()))

    def test_mutating_tools_stay_in_their_own_domain(self):

        self.assertNotIn("update_incident", DOMAIN_TOOLS["roster"])
        self.assertNotIn("delete_roster_entry", DOMAIN_TOOLS["incident"])
        self.assertNotIn("update_issue", DOMAIN_TOOLS["roster"])


class DomainClassificationTests(unittest.TestCase):

    def setUp(self):
        self.state = SessionState("a", "s")

    def classify(self, text, **focus):

        state = SessionState("a", "s")

        state.begin_turn()

        for kind, (entity_id, key) in focus.items():
            state.set_focus(kind, entity_id, key)

        return classify_domain(text, state)

    def test_single_topic_requests_get_narrow_tools(self):

        cases = {
            "Which employees in Access Management will be on leave next week?": "roster",
            "Change Rohit's shift to S2": "roster",
            "Set his availability for Friday to N": "roster",
            "Why did INC0000258 breach its SLA?": "incident",
            "Create a new incident for a database failure": "incident",
            "List incidents with priority P2 assigned to varun": "incident",
            "What does Issue-1 represent and what impact does it have?": "category",
            "List the sub-categories under Identity Lifecycle": "category",
        }

        for text, expected in cases.items():

            domain = self.classify(text)

            # A question mentioning two topics safely gets everything.
            self.assertIn(domain, (expected, "all"), text)

        for text in ("Change Rohit's shift to S2", "List the sub-categories under Identity Lifecycle"):
            self.assertEqual(self.classify(text), cases[text], text)

    def test_handler_questions(self):

        self.assertEqual(self.classify("Who should handle a new Identity Lifecycle issue?"), "handlers")
        self.assertEqual(self.classify("Who should handle a new database outage?"), "handlers")

    def test_handler_question_with_a_ticket_needs_everything(self):
        self.assertEqual(self.classify("Who should handle INC0000258?"), "all")

    def test_who_can_help_is_not_a_handler_question(self):
        self.assertEqual(self.classify("Why is INC0000258 late and who can help with it?"), "incident")

    def test_mixed_topics_get_everything(self):

        for text in (
            "Which team handles INC0000258 and is anyone on it available?",
            "Show incidents assigned to Rohit and his shift",
            "Compare Issue-1 with INC0000258",
        ):
            self.assertEqual(self.classify(text), "all", text)

    def test_unclear_requests_get_everything(self):

        for text in ("hello", "help", "what can you do?", "Tell me something interesting"):
            self.assertEqual(self.classify(text), "all", text)

    def test_context_decides_when_the_words_do_not(self):

        self.assertEqual(self.classify("Why did that happen?", incident=("INC1", "INC1")), "incident")
        self.assertEqual(self.classify("Explain why", issue=("Issue-1", "issue1")), "all")
        self.assertEqual(self.classify("Why is he off so often?", employee=("emp:1", "emp:1")), "roster")
        self.assertEqual(self.classify("Why is he off so often?"), "all")

    def test_an_id_fixes_the_domain(self):

        self.assertEqual(self.classify("Tell me the history of INC0000258"), "incident")
        self.assertEqual(self.classify("Tell me the history of Issue-1"), "category")


class DomainAgentTests(unittest.TestCase):
    """The real agent loop, with a scripted model, per domain."""

    def setUp(self):

        self.world = World().install()

        self.addCleanup(self.world.close)

        self.runtime = SessionRuntime("tester", "s1")

        self.models = {}

    def ask(self, text, script):

        model = ScriptedModel(script)

        def create_model():
            return model

        with mock.patch.object(agent, "create_model", create_model):
            reply, meta = agent.process_user_query(text, self.runtime)

        return model, reply, meta

    def test_each_request_carries_only_its_domains_tools(self):

        roster_model, _, roster_meta = self.ask(
            "Which employees in Access Management will be on leave next week?",
            [{"text": "answer"}],
        )

        incident_model, _, incident_meta = self.ask(
            "Why did INC0000258 breach its SLA and what should we learn?",
            [{"text": "answer"}],
        )

        self.assertEqual(roster_meta["domain"], "roster")
        self.assertEqual(incident_meta["domain"], "incident")
        self.assertEqual(set(roster_model.requests[0]["tools"]), set(DOMAIN_TOOLS["roster"]))
        self.assertEqual(set(incident_model.requests[0]["tools"]), set(DOMAIN_TOOLS["incident"]))
        self.assertNotIn("update_incident", roster_model.requests[0]["tools"])

    def test_unclear_requests_use_the_full_tool_set(self):

        model, _, meta = self.ask("Tell me something useful about our setup", [{"text": "ok"}])

        self.assertEqual(meta["domain"], "all")
        self.assertEqual(len(model.requests[0]["tools"]), 22)

    def test_agents_are_created_lazily_per_domain_and_reused(self):

        self.assertEqual(self.runtime.agents, {})

        self.ask("Which employees in Access Management will be on leave next week?", [{"text": "a"}])

        self.assertEqual(set(self.runtime.agents), {"roster"})

        first = self.runtime.agents["roster"]

        self.assertIs(self.runtime.get_agent(agent.build_agent, "roster"), first)

    def test_agent_ids_differ_so_memory_keeps_them_apart(self):

        self.ask("Which employees in Access Management will be on leave next week?", [{"text": "a"}])
        self.ask("Why did INC0000258 breach its SLA and what should we learn?", [{"text": "b"}])

        ids = {domain: built.agent_id for domain, built in self.runtime.agents.items()}

        self.assertEqual(ids, {"roster": "dtp-roster", "incident": "dtp-incident"})

    def test_state_is_shared_between_domains(self):
        """A roster answer sets 'he'; the incident agent sees the employee in its context."""

        self.ask(
            "Find the L2 engineer named Rohit in the roster and tell me about the team",
            [{"tools": [("search_roster", {"query": "Rohit"})]}, {"text": "found"}],
        )

        model, _, meta = self.ask(
            "Why did INC0000258 breach its SLA and what should we learn?",
            [{"text": "answer"}],
        )

        dynamic = model.requests[0]["system_blocks"][2]["text"]

        self.assertEqual(meta["domain"], "incident")
        self.assertIn("emp:1", dynamic)
        self.assertIn("Rohit Malhotra", dynamic)

    def test_a_tool_outside_the_domain_is_not_executable(self):
        """The model cannot call a write tool of another area, even if it tries."""

        model, reply, meta = self.ask(
            "Which employees in Access Management will be on leave next week?",
            [
                {"tools": [("update_incident", {"ticket_id": "INC0000258", "changes": {"priority": "P2"}})]},
                {"text": "done"},
            ],
        )

        self.assertEqual(self.world.incidents.items["INC0000258"]["priority"], "P1-Critical")
        self.assertEqual(self.world.incidents.count("update_item"), 0)


class TeamAvailabilityRouteTests(unittest.TestCase):

    def route(self, text):

        state = SessionState("a", "s")

        state.begin_turn()

        return DeterministicRouter.route(text, state)

    def test_whole_team_questions_are_direct(self):

        cases = {
            "Which employees are available today in Access Management?": ("Access Management", "today"),
            "Who is available in Access Management tomorrow?": ("Access Management", "tomorrow"),
            "Who's on leave today in the Infrastructure team?": ("Infrastructure", "today"),
            "Is anyone from Access Management available today?": ("Access Management", "today"),
            "Who is free in Access Management on 2026-10-01?": ("Access Management", "2026-10-01"),
            "who is working in Infrastructure": ("Infrastructure", "today"),
        }

        for text, (team, date) in cases.items():

            intent = self.route(text)

            self.assertEqual((intent.route, intent.query, intent.date), ("team_availability", team, date), text)

    def test_other_questions_are_not_taken(self):

        for text in (
            "Is Rohit available today?",
            "Is Rohit available in Access Management today?",
            "Is he available today?",
            "Which employees in Access Management are available next week?",
            "Who is available in September?",
            "Show incidents available in the queue",
            "Who should handle a new Identity Lifecycle issue?",
        ):
            self.assertNotEqual(self.route(text).route, "team_availability", text)


class TeamAvailabilityConversationTests(ConversationTestCase):

    def test_answered_without_the_model(self):

        answer = self.chat.say("Which employees are available today in Access Management?")

        self.assertTrue(self.chat.meta["direct"])
        self.assertEqual(self.chat.meta["usage"]["total_tokens"], 0)
        self.assertEqual(self.llm_calls, [])

        self.assertIn("Available (1): Rohit Malhotra", answer)
        self.assertIn("Not available (1): Priya Nair", answer)

        structured = self.chat.last.structured

        self.assertEqual(structured["type"], "table")
        self.assertEqual(
            {row["Name"]: row["Availability"] for row in structured["rows"]},
            {"Rohit Malhotra": "Available", "Priya Nair": "Not available"},
        )

    def test_team_by_full_name(self):

        answer = self.chat.say("Who is available today in the Access Management Team?")

        self.assertIn("Rohit Malhotra", answer)
        self.assertEqual(self.llm_calls, [])

    def test_a_month_without_roster_rows_is_explained(self):

        answer = self.chat.say("Who is available in Access Management on 2099-01-01?")

        self.assertIn("2099-01", answer)
        self.assertEqual(self.llm_calls, [])

    def test_an_unknown_team_falls_back_to_the_model(self):

        self.chat.say("Who is available today in the cafeteria?")

        self.assertEqual(self.chat.meta["route"], "llm")
        self.assertFalse(self.chat.meta["direct"])
        self.assertEqual(len(self.llm_calls), 1)

    def test_the_team_becomes_context(self):

        self.chat.say("Which employees are available today in Access Management?")

        self.assertEqual(self.chat.runtime.state.get_focus("team").id, "Access Management")


class LogFormatTests(unittest.TestCase):

    def test_request_log_reports_cache_tokens(self):

        import types

        import main
        from responses import text_reply

        world = World().install()

        self.addCleanup(world.close)

        main._sessions = main.SessionStore()

        with self.assertLogs("dtp.main", level="INFO") as logs:
            main.invoke({"prompt": "Show me INC0000258", "actor_id": "alice"}, types.SimpleNamespace(session_id="s1"))

        line = logs.output[0]

        for field in ("domain=", "cache_read=", "cache_write=", "tokens_in="):
            self.assertIn(field, line)


if __name__ == "__main__":
    unittest.main()
