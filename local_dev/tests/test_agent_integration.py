"""
The LLM path: the real Strands agent, hooks and tools, with a scripted
model instead of Bedrock and fake tables instead of DynamoDB.
"""

import json
import unittest
from unittest import mock

import agent
from config import CONVERSATION_WINDOW_SIZE, MAX_TOOL_CALLS_PER_REQUEST
from memory.store import SessionRuntime
from tests.fakes import World, client_error
from tests.scripted_model import ScriptedModel


def tool_results(model):
    """Every tool result the model was shown, as parsed JSON or raw text."""

    seen = []

    for request in model.requests:

        message = request["last_message"] or {}

        for block in message.get("content", []):

            result = block.get("toolResult")

            if result:

                text = "".join(part.get("text", "") for part in result["content"])

                try:
                    seen.append((result["status"], json.loads(text)))

                except ValueError:
                    seen.append((result["status"], text))

    return seen


class LlmPathTestCase(unittest.TestCase):

    def setUp(self):

        self.world = World().install()

        self.addCleanup(self.world.close)

        self.runtime = SessionRuntime("tester", "s1")

    def run_llm(self, text, script, default=None, runtime=None):

        self.model = ScriptedModel(script, default)

        with mock.patch.object(agent, "create_model", lambda: self.model):
            return agent.process_user_query(text, runtime or self.runtime)


class ToolLoopTests(LlmPathTestCase):

    def test_tool_call_then_answer(self):

        reply, meta = self.run_llm(
            "Who is handling INC0000258 and why is it late?",
            [
                {"tools": [("get_incident", {"ticket_id": "INC0000258", "fields": ["assigned_to"]})]},
                {"text": "It is assigned to varun.nambiar@abc.com."},
            ],
        )

        self.assertEqual(reply.text, "It is assigned to varun.nambiar@abc.com.")
        self.assertEqual(reply.structured["type"], "text")
        self.assertEqual(meta["route"], "llm")
        self.assertEqual(meta["tools"], ["get_incident"])
        self.assertEqual(meta["usage"]["total_tokens"], 220)

    def test_the_model_only_sees_the_fields_it_asked_for(self):

        self.run_llm(
            "Who is handling INC0000258 and why is it late?",
            [
                {"tools": [("get_incident", {"ticket_id": "INC0000258", "fields": ["assigned_to"]})]},
                {"text": "done"},
            ],
        )

        (status, payload), = tool_results(self.model)

        self.assertEqual(status, "success")
        self.assertEqual(payload, {"ticket_id": "INC0000258", "incident": {"ticket_id": "INC0000258", "assigned_to": "varun.nambiar@abc.com"}})

    def test_full_records_are_not_sent_by_default(self):

        self.run_llm(
            "Tell me everything about INC0000258 and why it happened",
            [{"tools": [("get_incident", {"ticket_id": "INC0000258"})]}, {"text": "done"}],
        )

        (_, payload), = tool_results(self.model)

        incident = payload["incident"]

        self.assertNotIn("working_notes", incident)
        self.assertLessEqual(len(incident["description"]), 300)

    def test_list_results_are_columnar(self):

        self.run_llm(
            "Which incidents are related to the network and why do they keep failing?",
            [{"tools": [("search_incidents", {"limit": 3})]}, {"text": "done"}],
        )

        (_, payload), = tool_results(self.model)

        self.assertEqual(payload["incidents"]["cols"][0], "ticket_id")
        self.assertEqual(len(payload["incidents"]["rows"]), 3)
        self.assertNotIn("ok", payload)

    def test_the_agent_gets_the_static_prompt_first_and_context_last(self):

        self.run_llm("Why is INC0000258 late?", [{"text": "answer"}])

        blocks = self.model.requests[0]["system_blocks"]

        self.assertEqual(blocks[0]["text"], agent.SYSTEM_PROMPT)
        self.assertEqual(blocks[1], {"cachePoint": {"type": "default"}})
        self.assertIn("Today:", blocks[2]["text"])
        self.assertIn("nothing discussed yet", blocks[2]["text"])

    def test_only_the_relevant_tools_are_offered(self):

        from tools import DOMAIN_TOOLS

        self.run_llm("Why is INC0000258 late?", [{"text": "answer"}])

        self.assertEqual(set(self.model.requests[0]["tools"]), set(DOMAIN_TOOLS["incident"]))
        self.assertLess(len(self.model.requests[0]["tools"]), 22)

    def test_conversation_window_is_small(self):

        self.run_llm("Why is INC0000258 late?", [{"text": "answer"}])

        window = self.runtime.agents["incident"].conversation_manager.window_size

        self.assertEqual(window, CONVERSATION_WINDOW_SIZE)
        self.assertLessEqual(window, 20)


class SessionMemoryAcrossPathsTests(LlmPathTestCase):

    def test_llm_answers_update_the_context_for_direct_follow_ups(self):

        self.run_llm(
            "Who is handling INC0000258 and why is it late?",
            [{"tools": [("get_incident", {"ticket_id": "INC0000258", "fields": ["assigned_to"]})]}, {"text": "varun"}],
        )

        reply, meta = agent.process_user_query("Who opened it?", self.runtime)

        self.assertTrue(meta["direct"])
        self.assertIn("simran.gupta@abc.com", reply.text)

    def test_direct_answers_are_visible_to_the_model_next_turn(self):

        agent.process_user_query("Show me INC0000258", self.runtime)

        self.run_llm("Why did that one breach?", [{"text": "because"}])

        dynamic = self.model.requests[0]["system_blocks"][2]["text"]

        self.assertIn("INC0000258", dynamic)
        self.assertIn("Show me INC0000258", dynamic)

    def test_model_writes_keep_the_cache_correct(self):

        self.run_llm(
            "Please bump INC0000258 down and explain why that is fine",
            [
                {"tools": [("update_incident", {"ticket_id": "INC0000258", "changes": {"priority": "P2"}})]},
                {"text": "done"},
            ],
        )

        gets_after_update = self.world.incidents.count("get_item")

        reply, meta = agent.process_user_query("What is its priority?", self.runtime)

        self.assertTrue(meta["direct"])
        self.assertIn("P2-High", reply.text)
        self.assertEqual(self.world.incidents.count("get_item"), gets_after_update)

    def test_state_is_mirrored_for_persistence(self):

        self.run_llm(
            "Who is handling INC0000258 and why is it late?",
            [{"tools": [("get_incident", {"ticket_id": "INC0000258", "fields": ["state"]})]}, {"text": "closed"}],
        )

        saved = self.runtime.agents["incident"].state.get("session_state")

        self.assertEqual(saved["focus"]["incident"]["id"], "INC0000258")

        # A new runtime for the same session restores it from the agent state.
        fresh = SessionRuntime("tester", "s1")

        fresh.state.turn = 1

        restored_agent = mock.Mock()
        restored_agent.state.get.return_value = saved

        fresh.state.load_dict(saved)

        self.assertEqual(fresh.state.get_focus("incident").id, "INC0000258")

    def test_employee_context_from_tools(self):

        self.run_llm(
            "Find the L2 engineer named Rohit in the roster and tell me about the team",
            [{"tools": [("search_roster", {"query": "Rohit"})]}, {"text": "found"}],
        )

        reply, meta = agent.process_user_query("What is his shift?", self.runtime)

        self.assertTrue(meta["direct"])
        self.assertIn("S1", reply.text)


class WriteToolTests(LlmPathTestCase):

    def test_model_updates_record_the_actor(self):

        with self.assertLogs("dtp.audit", level="INFO") as logs:

            self.run_llm(
                "Set INC0000259 to P2 and assign it to Rohit",
                [
                    {"tools": [("update_incident", {"ticket_id": "INC0000259", "changes": {"priority": "P2", "assigned_to": "Rohit"}})]},
                    {"text": "Updated INC0000259."},
                ],
            )

        stored = self.world.incidents.items["INC0000259"]

        self.assertEqual((stored["priority"], stored["assigned_to"]), ("P2-High", "rohit.malhotra@abc.com"))
        self.assertIn("actor=tester", logs.output[0])

    def test_invalid_value_is_reported_back_to_the_model(self):

        self.run_llm(
            "Set INC0000259 priority to P9 and explain why that is a problem",
            [
                {"tools": [("update_incident", {"ticket_id": "INC0000259", "changes": {"priority": "P9"}})]},
                {"text": "P9 is not valid. Choose one of P1-Critical, P2-High..."},
            ],
        )

        (status, payload), = tool_results(self.model)

        self.assertEqual(payload["error"], "invalid_value")
        self.assertIn("P2-High", payload["message"])
        self.assertEqual(self.world.incidents.items["INC0000259"]["priority"], "P3-Medium")

    def test_create_lists_what_is_missing(self):

        self.run_llm(
            "Create a new incident for a database failure",
            [
                {"tools": [("create_incident", {"short_description": "Database failure"})]},
                {"text": "What category, urgency and priority?"},
            ],
        )

        (_, payload), = tool_results(self.model)

        self.assertEqual(payload["missing"], ["category", "urgency", "priority"])
        self.assertEqual(self.world.incidents.count("put_item"), 0)

    def test_create_then_context(self):

        self.run_llm(
            "Create an incident for a database failure, category Infrastructure, high urgency, P1",
            [
                {"tools": [("create_incident", {
                    "short_description": "Database failure", "category": "Infrastructure",
                    "urgency": "high", "priority": "P1"})]},
                {"text": "Created."},
            ],
        )

        self.assertIn("INC0000261", self.world.incidents.items)

        reply, meta = agent.process_user_query("What is its priority?", self.runtime)

        self.assertIn("P1-Critical", reply.text)

    def test_roster_delete_through_the_model(self):

        self.run_llm(
            "Remove Rohit Malhotra from the roster and notify the team lead about it",
            [{"tools": [("delete_roster_entry", {"employee": "Rohit Malhotra"})]}, {"text": "Deleted."}],
        )

        self.assertNotIn("U001", self.world.roster.items)


class LoopControlTests(LlmPathTestCase):

    def test_duplicate_tool_calls_run_once(self):

        call = ("get_incident", {"ticket_id": "INC0000258", "fields": ["state"]})

        self.run_llm(
            "Why is INC0000258 in that state and what should happen next?",
            [{"tools": [call, call]}, {"text": "done"}],
        )

        results = tool_results(self.model)

        self.assertEqual(self.world.incidents.count("get_item"), 1)
        self.assertEqual([status for status, _ in results], ["success", "error"])
        self.assertIn("Duplicate", results[1][1])

    def test_a_write_is_never_repeated_in_one_turn(self):

        call = ("update_incident", {"ticket_id": "INC0000259", "changes": {"state": "In Progress"}})

        self.run_llm(
            "Start INC0000259 and explain why that is the right state",
            [{"tools": [call]}, {"tools": [call]}, {"text": "done"}],
        )

        self.assertEqual(self.world.incidents.count("update_item"), 1)

    def test_reads_are_allowed_again_after_a_write(self):

        read = ("get_incident", {"ticket_id": "INC0000259", "fields": ["state"]})

        self.run_llm(
            "Start INC0000259 and then show me what changed and why",
            [
                {"tools": [read]},
                {"tools": [("update_incident", {"ticket_id": "INC0000259", "changes": {"state": "In Progress"}})]},
                {"tools": [read]},
                {"text": "done"},
            ],
        )

        results = tool_results(self.model)

        self.assertEqual([status for status, _ in results], ["success", "success", "success"])
        self.assertEqual(results[2][1]["incident"]["state"], "In Progress")

    def test_runaway_loops_are_stopped(self):

        fields = ["priority", "state", "urgency", "category", "team_id", "assigned_to", "opened_by",
                  "description", "short_description", "sub_category", "url", "source_system",
                  "created_at", "updated_at", "closed_at", "opened_for", "assignment_sla",
                  "resolution_sla", "working_notes", "assignment_duration"]

        def forever(number):
            return {"tools": [("get_incident", {"ticket_id": "INC0000258", "fields": [fields[number % len(fields)]]})]}

        reply, meta = self.run_llm(
            "Why is INC0000258 like that and what does it mean for everything else?",
            [],
            default=forever,
        )

        self.assertFalse(reply.ok)
        self.assertIn("allowed number of steps", reply.text)
        self.assertEqual(meta["error"], "loop_limit")
        self.assertEqual(self.world.incidents.count("get_item"), MAX_TOOL_CALLS_PER_REQUEST)

    def test_the_loop_recovers_for_the_next_message(self):

        def forever(number):
            return {"tools": [("get_incident", {"ticket_id": "INC0000258", "fields": [f"f{number}"]})]}

        self.run_llm("Why is INC0000258 like that and what does it mean?", [], default=forever)

        # The session keeps its agent (and model); queue the next answer on it.
        self.model.script.append({"text": "fine"})

        reply, meta = agent.process_user_query("Why is INC0000259 slow and what should we do?", self.runtime)

        self.assertEqual(reply.text, "fine")
        self.assertEqual(meta.get("error"), None)


class FailureHandlingTests(LlmPathTestCase):

    def test_tool_failure_is_safe_for_the_model_and_user(self):

        self.world.incidents.fail_with = client_error("AccessDeniedException", "arn:aws:dynamodb:...:table/prod-secret")

        reply, meta = self.run_llm(
            "Why is INC0000258 late and who can help with it?",
            [
                {"tools": [("get_incident", {"ticket_id": "INC0000258"})]},
                {"text": "I couldn't read that ticket right now."},
            ],
        )

        (status, payload), = tool_results(self.model)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "access_denied")
        self.assertNotIn("prod-secret", json.dumps(payload))
        self.assertEqual(reply.text, "I couldn't read that ticket right now.")

    def test_model_errors_are_user_friendly(self):

        reply, meta = self.run_llm(
            "Why is INC0000258 late and who can help?",
            [{"raise": RuntimeError("secret internal detail arn:aws:bedrock")}],
        )

        self.assertFalse(reply.ok)
        self.assertNotIn("secret", reply.text)
        self.assertNotIn("arn:", reply.text)
        self.assertIn("Something went wrong", reply.text)
        self.assertEqual(meta["error"], "RuntimeError")

    def test_agent_startup_failure_is_friendly(self):

        with mock.patch.object(agent, "validate_config", side_effect=RuntimeError("Missing environment variables: X")):

            reply, meta = agent.process_user_query("Why is INC0000258 late and who can help?", self.runtime)

        self.assertFalse(reply.ok)
        self.assertNotIn("environment", reply.text)
        self.assertEqual(meta["error"], "agent_init")


class KnowledgeAndHybridToolTests(LlmPathTestCase):

    def test_hybrid_lookup_in_one_call(self):

        self.run_llm(
            "Who should handle a new database outage?",
            [
                {"tools": [("find_handlers_for_issue", {"issue": "Database Outage"})]},
                {"text": "Varun Nambiar (Infrastructure) is available."},
            ],
        )

        (_, payload), = tool_results(self.model)

        self.assertEqual(payload["team_id"], "Infrastructure")
        self.assertEqual(payload["members"]["rows"][0][1], "Varun Nambiar")

        self.assertEqual(self.runtime.state.get_focus("team").id, "Infrastructure")

    def test_knowledge_search_is_routed_and_bounded(self):

        world = World(
            kb_results=[
                {"score": 0.9, "content": {"text": "Issue-1 covers new account creation."}},
                {"score": 0.8, "content": {"text": "Issue-2 covers account removal."}},
                {"score": 0.7, "content": {"text": "Issue-3 covers database outages."}},
                {"score": 0.6, "content": {"text": "Issue-4 unrelated."}},
            ]
        ).install()

        self.addCleanup(world.close)

        self.run_llm(
            "Explain what the Identity Lifecycle category is used for in our process",
            [
                {"tools": [("search_knowledge_base", {"query": "Identity Lifecycle category", "source": "category", "max_results": 2})]},
                {"text": "It covers joiners and leavers."},
            ],
        )

        request = world.kb.requests[0]

        self.assertEqual(
            request["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]["equals"]["value"],
            "ds-category",
        )

        (_, payload), = tool_results(self.model)

        self.assertEqual(len(payload["results"]["rows"]), 2)


if __name__ == "__main__":
    unittest.main()


class OutputLimitTests(LlmPathTestCase):
    """The model hitting its output cap must not throw the answer away."""

    def test_a_cut_off_answer_is_shown_with_a_note(self):

        reply, meta = self.run_llm(
            "Why is INC0000258 late and what should we do about it?",
            [{"text": "- Root cause: the database node ran out of memory\n- Fix: restart", "stop": "max_tokens"}],
        )

        self.assertIn("Root cause: the database node ran out of memory", reply.text)
        self.assertIn("cut off", reply.text)
        self.assertTrue(reply.ok)
        self.assertEqual(meta["error"], "max_tokens")

    def test_a_cut_off_tool_call_is_a_short_error(self):

        reply, meta = self.run_llm(
            "Why is INC0000258 late and what should we do about it?",
            [{"tools": [("get_incident", {"ticket_id": "INC0000258"})], "stop": "max_tokens"}],
        )

        self.assertFalse(reply.ok)
        self.assertIn("ask for less", reply.text)

    def test_the_session_still_works_afterwards(self):

        self.run_llm(
            "Why is INC0000258 late and what should we do about it?",
            [{"text": "partial", "stop": "max_tokens"}],
        )

        self.model.script.append({"text": "fine"})

        reply, meta = agent.process_user_query("Why is INC0000259 slow and what should we do?", self.runtime)

        self.assertEqual(reply.text, "fine")

    def test_the_output_cap_leaves_room_for_a_table(self):

        from config import LLM_MAX_TOKENS

        self.assertGreaterEqual(LLM_MAX_TOKENS, 3000)
        self.assertIn("at most 10 rows", agent.SYSTEM_PROMPT)
