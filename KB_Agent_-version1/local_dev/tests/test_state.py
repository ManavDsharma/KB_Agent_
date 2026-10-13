import unittest

from memory.state import SessionState
from memory.store import SessionStore


class FakeClock:

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FocusTests(unittest.TestCase):

    def setUp(self):

        self.clock = FakeClock()

        self.state = SessionState("actor", "session", clock=self.clock)

        self.state.begin_turn()

    def test_focus_tracks_latest_type(self):

        self.state.set_focus("incident", "INC1", "INC1")
        self.state.set_focus("issue", "Issue-1", "issue1")

        self.assertEqual(self.state.last_type, "issue")
        self.assertEqual(self.state.get_focus().id, "Issue-1")
        self.assertEqual(self.state.get_focus("incident").id, "INC1")

    def test_new_entity_replaces_old_and_resets_cache(self):

        self.state.set_focus("incident", "INC1", "INC1", data={"priority": "P1"})
        self.state.set_focus("incident", "INC2", "INC2")

        entity = self.state.get_focus("incident")

        self.assertEqual(entity.id, "INC2")
        self.assertEqual(entity.data, {})

    def test_cache_hit_needs_every_field_and_freshness(self):

        entity = self.state.set_focus("incident", "INC1", "INC1", data={"priority": "P1", "state": "New"})

        self.assertEqual(self.state.cached_fields(entity, ["priority"]), {"priority": "P1"})
        self.assertIsNone(self.state.cached_fields(entity, ["priority", "assigned_to"]))

        self.clock.now += 61

        self.assertIsNone(self.state.cached_fields(entity, ["priority"], ttl=60))

    def test_writes_keep_the_cache_correct(self):

        entity = self.state.set_focus("incident", "INC1", "INC1", data={"priority": "P1"})

        self.clock.now += 100

        self.state.apply_changes("incident", "INC1", {"priority": "P2"})

        self.assertEqual(self.state.cached_fields(entity, ["priority"]), {"priority": "P2"})

    def test_invalidate_and_clear(self):

        entity = self.state.set_focus("incident", "INC1", "INC1", data={"priority": "P1"})

        self.state.invalidate("incident", "INC1")

        self.assertIsNone(self.state.cached_fields(entity, ["priority"]))

        self.state.clear_focus("incident", "INC1")

        self.assertIsNone(self.state.get_focus("incident"))

    def test_mutation_context_ages_out(self):

        self.state.set_focus("incident", "INC1", "INC1")

        for _ in range(10):
            self.state.begin_turn()

        self.assertIsNotNone(self.state.get_focus("incident"))
        self.assertIsNone(self.state.get_focus("incident", max_age_turns=8))


class PendingTests(unittest.TestCase):

    def test_pending_expires_after_a_few_turns(self):

        state = SessionState("a", "s")

        state.begin_turn()

        state.set_pending("update_slots", expects="field")

        for _ in range(3):
            state.begin_turn()

        self.assertIsNotNone(state.pending)

        state.begin_turn()

        self.assertIsNone(state.pending)


class PromptContextTests(unittest.TestCase):

    def test_context_block_is_compact(self):

        state = SessionState("a", "s")

        state.begin_turn()

        state.set_focus("incident", "INC0000258", "INC258", label="Database failure",
                        data={"priority": "P1-Critical", "state": "Closed", "description": "x" * 5000})

        block = state.context_block()

        self.assertIn("INC0000258", block)
        self.assertIn("P1-Critical", block)
        self.assertNotIn("x" * 50, block)
        self.assertLess(len(block), 500)

    def test_empty_context(self):

        self.assertIn("nothing discussed", SessionState("a", "s").context_block())

    def test_recent_exchanges_are_bounded(self):

        state = SessionState("a", "s")

        for index in range(10):
            state.remember_turn(f"question {index} " + "q" * 500, "answer " + "a" * 500)

        block = state.context_block()

        self.assertEqual(block.count("U: "), 2)
        self.assertLess(len(block), 1500)


class SnapshotTests(unittest.TestCase):

    def test_round_trip(self):

        state = SessionState("a", "s")

        state.begin_turn()

        state.set_focus("incident", "INC1", "INC1", label="x", data={"priority": "P1", "big": {"nested": 1}})
        state.set_pending("update_slots", expects="value", field="priority")
        state.remember_turn("hello", "hi")

        restored = SessionState("a", "s")

        restored.load_dict(state.to_dict())

        entity = restored.get_focus("incident")

        self.assertEqual((entity.id, entity.data), ("INC1", {"priority": "P1"}))
        self.assertEqual(restored.pending["field"], "priority")
        self.assertEqual(list(restored.recent), [("hello", "hi")])

        # Restored data is stale until it is read again.
        self.assertIsNone(restored.cached_fields(entity, ["priority"]))


class StoreTests(unittest.TestCase):

    def test_sessions_are_isolated(self):

        store = SessionStore()

        a1 = store.get("actor-a", "s1")
        a2 = store.get("actor-a", "s2")
        b1 = store.get("actor-b", "s1")

        a1.state.set_focus("incident", "INC1", "INC1")

        self.assertIsNone(a2.state.get_focus("incident"))
        self.assertIsNone(b1.state.get_focus("incident"))
        self.assertIs(store.get("actor-a", "s1"), a1)
        self.assertEqual(len({id(a1), id(a2), id(b1)}), 3)

    def test_idle_sessions_expire(self):

        clock = FakeClock()

        store = SessionStore(ttl_seconds=100, clock=clock)

        first = store.get("a", "s")

        clock.now += 101

        self.assertIsNot(store.get("a", "s"), first)

    def test_store_is_bounded(self):

        store = SessionStore(max_sessions=3)

        for index in range(10):
            store.get("a", f"s{index}")

        self.assertLessEqual(len(store), 3)

    def test_agent_is_created_lazily_and_once(self):

        runtime = SessionStore().get("a", "s")

        created = []

        self.assertFalse(runtime.has_agent)

        first = runtime.get_agent(lambda rt, domain: created.append(domain) or object(), "roster")
        second = runtime.get_agent(lambda rt, domain: created.append(domain) or object(), "roster")
        other = runtime.get_agent(lambda rt, domain: created.append(domain) or object(), "incident")

        self.assertIs(first, second)
        self.assertIsNot(first, other)
        self.assertEqual(created, ["roster", "incident"])


if __name__ == "__main__":
    unittest.main()
