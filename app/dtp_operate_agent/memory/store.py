"""
In-process store of live sessions.

Keyed by (actor_id, session_id) so different users, and different
sessions of one user, never share state. Idle sessions expire and the
number of sessions is bounded.
"""

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Tuple

from config import MAX_SESSIONS, SESSION_TTL_SECONDS
from memory.state import SessionState


class SessionRuntime:
    """One conversation: its state, a lock, and a lazily built agent."""

    def __init__(self, actor_id: str, session_id: str):

        self.actor_id = actor_id

        self.session_id = session_id

        self.state = SessionState(actor_id, session_id)

        self.lock = threading.RLock()

        self.last_used = time.monotonic()

        # One LLM agent per tool domain (roster / incident / ...), created
        # the first time a request needs it. They share this session's state.
        self._agents: Dict[str, Any] = {}

        # Loop-control hook of each agent (set when the agent is built).
        self.guards: Dict[str, Any] = {}

    def get_agent(self, factory: Callable[["SessionRuntime", str], Any], domain: str = "all"):
        """The LLM agent for a tool domain, created on first use."""

        if domain not in self._agents:
            self._agents[domain] = factory(self, domain)

        return self._agents[domain]

    @property
    def agents(self) -> Dict[str, Any]:
        return self._agents

    @property
    def has_agent(self) -> bool:
        return bool(self._agents)


class SessionStore:

    def __init__(
        self,
        ttl_seconds: float = SESSION_TTL_SECONDS,
        max_sessions: int = MAX_SESSIONS,
        clock: Callable[[], float] = time.monotonic,
    ):

        self.ttl = ttl_seconds

        self.max_sessions = max_sessions

        self.clock = clock

        self._sessions: "OrderedDict[Tuple[str, str], SessionRuntime]" = OrderedDict()

        self._lock = threading.Lock()

    def get(self, actor_id: str, session_id: str) -> SessionRuntime:

        key = (actor_id, session_id)

        with self._lock:

            self._evict()

            runtime = self._sessions.get(key)

            if runtime is None:
                runtime = SessionRuntime(actor_id, session_id)
                self._sessions[key] = runtime

            runtime.last_used = self.clock()

            self._sessions.move_to_end(key)

            return runtime

    def _evict(self) -> None:

        now = self.clock()

        for key in [
            key for key, runtime in self._sessions.items()
            if now - runtime.last_used > self.ttl
        ]:
            del self._sessions[key]

        while len(self._sessions) >= self.max_sessions:
            self._sessions.popitem(last=False)

    def __len__(self) -> int:
        return len(self._sessions)
