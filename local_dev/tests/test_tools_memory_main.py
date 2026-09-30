import json
import types
import unittest
from unittest import mock

import agent
import main
from hooks import AgentGuard, AgentLoopLimitExceeded
from memory import session as memory_session
from memory.state import SessionState
from responses import text_reply
from tests.fakes import World
from tools import MUTATING_TOOL_NAMES, all_tools
from tools.common import actor_of, iter_rows, out


class ToolRegistryTests(unittest.TestCase):

    def test_tools_are_unique_typed_and_documented(self):

        tools = all_tools()

        names = [tool.tool_spec["name"] for tool in tools]

        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), 22)

        for tool in tools:

            spec = tool.tool_spec

            self.assertTrue(spec["description"].strip(), spec["name"])
            self.assertNotIn("tool_context", spec["inputSchema"]["json"]["properties"], spec["name"])

    def test_mutating_tools_are_all_registered(self):

        names = {tool.tool_spec["name"] for tool in all_tools()}

        self.assertTrue(MUTATING_TOOL_NAMES <= names)

    def test_schema_budget(self):
        """Tool schemas are sent (cached) on every model call; keep them lean."""

        size = sum(len(json.dumps(tool.tool_spec)) for tool in all_tools())

        self.assertLess(size // 4, 3000)

    def test_enumerated_arguments_use_enums(self):

        specs = {tool.tool_spec["name"]: tool.tool_spec for tool in all_tools()}

        breach = specs["list_sla_breaches"]["inputSchema"]["json"]["properties"]["breach_type"]
        source = specs["search_knowledge_base"]["inputSchema"]["json"]["properties"]["source"]

        self.assertEqual(breach["enum"], ["all", "assignment", "resolution"])
        self.assertEqual(source["enum"], ["all", "incident", "roster", "category"])

    def test_no_legacy_duplicate_tools(self):

        names = {tool.tool_spec["name"] for tool in all_tools()}

        self.assertNotIn("find_employee", names)
        self.assertIn("search_roster", names)


class ToolOutputTests(unittest.TestCase):

    def setUp(self):
        self.world = World().install()
        self.addCleanup(self.world.close)

    def test_out_is_compact_json(self):

        text = out({"ok": True, "a": 1, "b": [1, 2]})

        self.assertEqual(text, '{"a":1,"b":[1,2]}')

    def test_errors_keep_their_flag(self):

        self.assertEqual(json.loads(out({"ok": False, "error": "x", "message": "m"}))["ok"], False)

    def test_columnar_rows_round_trip(self):

        rows = [{"a": 1, "b": 2}, {"a": 3}]

        payload = json.loads(out({"rows": rows}, ["rows"]))

        self.assertEqual(payload["rows"], {"cols": ["a", "b"], "rows": [[1, 2], [3, None]]})
        self.assertEqual(iter_rows(payload["rows"]), [{"a": 1, "b": 2}, {"a": 3, "b": None}])

    def test_columnar_is_smaller(self):

        result = self.world.incident_service.search_incidents()

        as_dicts = json.dumps(result["incidents"], separators=(",", ":"))
        as_columns = out(result, ["incidents"])

        self.assertLess(len(as_columns), len(as_dicts))

    def test_roster_tools_never_return_the_availability_map(self):

        from tools import roster_tools

        for text in (
            roster_tools.search_roster(query="rohit"),
            roster_tools.get_roster_entry(employee="Rohit"),
            roster_tools.search_roster(team="Access Management"),
        ):
            self.assertNotIn("days_availability", text)
            self.assertNotIn("2099-01-01", text)

    def test_tools_return_a_string_the_model_can_read(self):

        from tools import incident_tools

        text = incident_tools.get_incident(ticket_id="INC0000258", fields=["priority"])

        self.assertEqual(json.loads(text)["incident"]["priority"], "P1-Critical")

    def test_actor_comes_from_agent_state(self):

        context = types.SimpleNamespace(agent=types.SimpleNamespace(state=types.SimpleNamespace(get=lambda key: "alice")))

        self.assertEqual(actor_of(context), "alice")
        self.assertIsNone(actor_of(None))


class FakeEvent(types.SimpleNamespace):
    pass


class GuardTests(unittest.TestCase):

    def event(self, name, arguments):

        return FakeEvent(tool_use={"name": name, "input": arguments, "toolUseId": "t"}, cancel_tool=False)

    def test_duplicates_are_cancelled(self):

        guard = AgentGuard(SessionState("a", "s"))

        first = self.event("get_incident", {"ticket_id": "INC1"})
        again = self.event("get_incident", {"ticket_id": "INC1"})
        other = self.event("get_incident", {"ticket_id": "INC2"})

        for event in (first, again, other):
            guard._before_tool(event)

        self.assertFalse(first.cancel_tool)
        self.assertTrue(again.cancel_tool)
        self.assertFalse(other.cancel_tool)

    def test_budget_then_hard_stop(self):

        guard = AgentGuard(SessionState("a", "s"), max_tool_calls=3)

        events = [self.event("get_incident", {"ticket_id": f"INC{n}"}) for n in range(10)]

        for event in events[:3]:
            guard._before_tool(event)
            self.assertFalse(event.cancel_tool)

        guard._before_tool(events[3])

        self.assertTrue(events[3].cancel_tool)
        self.assertTrue(guard.limit_hit)

        with self.assertRaises(AgentLoopLimitExceeded):

            for event in events[4:]:
                guard._before_tool(event)

    def test_counters_reset_per_request(self):

        guard = AgentGuard(SessionState("a", "s"), max_tool_calls=1)

        guard._before_tool(self.event("get_incident", {"ticket_id": "INC1"}))
        guard._before_tool(self.event("get_incident", {"ticket_id": "INC2"}))

        self.assertTrue(guard.limit_hit)

        guard._reset(None)

        again = self.event("get_incident", {"ticket_id": "INC1"})

        guard._before_tool(again)

        self.assertFalse(again.cancel_tool)
        self.assertFalse(guard.limit_hit)

    def test_results_update_the_session_state(self):

        state = SessionState("a", "s")

        state.begin_turn()

        guard = AgentGuard(state)

        result = {
            "status": "success",
            "content": [{"text": json.dumps({"ticket_id": "INC0000258", "incident": {"ticket_id": "INC0000258", "priority": "P1"}})}],
        }

        guard._after_tool(
            FakeEvent(
                tool_use={"name": "get_incident", "input": {}},
                result=result,
                duration=0.01,
                exception=None,
                cancel_message=None,
            )
        )

        self.assertEqual(state.get_focus("incident").id, "INC0000258")
        self.assertEqual(guard.calls, [{"name": "get_incident", "ms": 10, "ok": True}])

    def test_failed_and_cancelled_results_are_ignored(self):

        state = SessionState("a", "s")

        guard = AgentGuard(state)

        for result, cancel in (
            ({"status": "error", "content": [{"text": "Error: boom"}]}, None),
            ({"status": "error", "content": [{"text": "Duplicate call"}]}, "Duplicate call"),
            ({"status": "success", "content": [{"text": "not json"}]}, None),
            ({"status": "success", "content": [{"text": json.dumps({"ok": False, "error": "not_found"})}]}, None),
        ):
            guard._after_tool(
                FakeEvent(
                    tool_use={"name": "get_incident", "input": {}},
                    result=result,
                    duration=None,
                    exception=None,
                    cancel_message=cancel,
                )
            )

        self.assertIsNone(state.get_focus())
        self.assertTrue(all(not call["ok"] for call in guard.calls[:2]))


class MemoryManagerTests(unittest.TestCase):

    def create(self, ltm=False, memory_id="mem-1"):

        created = {}

        class FakeManager:

            def __init__(self, config, region):
                created["config"] = config
                created["region"] = region

        with mock.patch.object(memory_session, "get_memory_id", return_value=memory_id), \
                mock.patch.object(memory_session, "ENABLE_LTM_RETRIEVAL", ltm), \
                mock.patch(
                    "bedrock_agentcore.memory.integrations.strands.session_manager.AgentCoreMemorySessionManager",
                    FakeManager,
                ):
            manager = memory_session.create_memory_session_manager("session-1", "actor-1")

        return manager, created

    def test_session_is_persisted_with_identity(self):

        manager, created = self.create()

        config = created["config"]

        self.assertIsNotNone(manager)
        self.assertEqual((config.memory_id, config.session_id, config.actor_id), ("mem-1", "session-1", "actor-1"))
        self.assertTrue(config.filter_restored_tool_context)

    def test_long_term_retrieval_is_off_by_default(self):

        _, created = self.create()

        self.assertIsNone(created["config"].retrieval_config)

    def test_long_term_retrieval_is_scoped_to_the_actor_when_enabled(self):

        _, created = self.create(ltm=True)

        namespaces = list(created["config"].retrieval_config)

        self.assertTrue(all("actor-1" in namespace for namespace in namespaces))

    def test_no_memory_configured(self):

        manager, _ = self.create(memory_id=None)

        self.assertIsNone(manager)

    def test_missing_identity(self):

        self.assertIsNone(memory_session.create_memory_session_manager(None, "actor"))
        self.assertIsNone(memory_session.create_memory_session_manager("session", ""))

    def test_memory_failure_never_breaks_the_agent(self):

        with mock.patch.object(memory_session, "get_memory_id", return_value="mem-1"), \
                mock.patch(
                    "bedrock_agentcore.memory.integrations.strands.session_manager.AgentCoreMemorySessionManager",
                    side_effect=RuntimeError("no network"),
                ):
            self.assertIsNone(memory_session.create_memory_session_manager("s", "a"))


class EntrypointTests(unittest.TestCase):

    def setUp(self):

        self.world = World().install()

        self.addCleanup(self.world.close)

        main._sessions = main.SessionStore()

        # The entrypoint tests never need a model.
        self.llm_calls = []

        def fake_llm(user_input, runtime, meta):
            self.llm_calls.append(user_input)
            return text_reply("LLM ANSWER")

        patcher = mock.patch.object(agent, "_run_llm", fake_llm)

        patcher.start()

        self.addCleanup(patcher.stop)

    def call(self, payload, session_id="session-1"):

        context = types.SimpleNamespace(session_id=session_id)

        return main.invoke(payload, context)

    def test_payload_validation(self):

        for payload, fragment in (
            (None, "empty"),
            ("hi", "JSON object"),
            ({}, "prompt"),
            ({"prompt": 5}, "string"),
            ({"prompt": "   "}, "empty"),
        ):
            result = self.call(payload)

            self.assertEqual(result["error"], "Invalid request", payload)
            self.assertIn(fragment, result["message"])

    def test_response_contract(self):

        result = self.call({"prompt": "Show me INC0000258", "actor_id": "alice"})

        self.assertIn("P1-Critical", result["response"])
        self.assertEqual(result["structured"]["type"], "record")
        self.assertEqual(set(result), {"response", "structured"})

    def test_same_session_keeps_context_across_requests(self):

        self.call({"prompt": "Show me INC0000258", "actor_id": "alice"})

        result = self.call({"prompt": "Who opened it?", "actor_id": "alice"})

        self.assertIn("simran.gupta@abc.com", result["response"])
        self.assertEqual(result["structured"]["type"], "field")

    def test_other_actor_or_session_has_no_context(self):

        self.call({"prompt": "Show me INC0000258", "actor_id": "alice"})

        for actor, session in (("bob", "session-1"), ("alice", "session-2")):

            result = self.call({"prompt": "Who opened it?", "actor_id": actor}, session)

            self.assertNotIn("simran", result["response"], (actor, session))

    def test_missing_session_is_rejected_outside_local(self):

        result = main.invoke({"prompt": "hi", "actor_id": "alice"}, types.SimpleNamespace(session_id=None))

        self.assertEqual(result["error"], "Invalid request")
        self.assertIn("session", result["message"])

    def test_missing_actor_gets_an_isolated_identity(self):

        payload = {"prompt": "Show me INC0000258"}

        self.call(payload, "session-1")

        result = self.call({"prompt": "Who opened it?"}, "session-2")

        self.assertNotIn("simran", result["response"])
        self.assertEqual(self.llm_calls, [])

        self.assertEqual(main.resolve_identity({}, types.SimpleNamespace(session_id="s1"))[1], "anon-s1")

    def test_identifiers_are_validated(self):

        for bad in ("bad id with spaces", "x" * 200, "<script>"):

            with self.assertRaises(ValueError):
                main.resolve_identity({"actor_id": bad}, types.SimpleNamespace(session_id="s1"))

    def test_internal_errors_are_not_leaked(self):

        with mock.patch.object(main, "process_user_query", side_effect=RuntimeError("secret arn:aws:iam::123:role/x")):

            result = self.call({"prompt": "hi", "actor_id": "alice"})

        self.assertEqual(result["error"], "Agent execution failed")
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("arn:", json.dumps(result))

    def test_request_log_has_no_prompt_text(self):

        with self.assertLogs("dtp.main", level="INFO") as logs:

            self.call({"prompt": "Show me INC0000258 my secret question", "actor_id": "alice"})

        line = logs.output[0]

        self.assertIn("route=entity_view", line)
        self.assertIn("tokens_in=0", line)
        self.assertNotIn("secret question", line)


if __name__ == "__main__":
    unittest.main()
