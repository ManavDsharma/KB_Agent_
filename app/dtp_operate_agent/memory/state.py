"""
Structured short-term session state.

This is what makes follow-ups ("who opened it?", "set priority to P2",
"is he available today?") work without replaying the conversation to
the model. It records which incident / issue / employee / team is
being discussed, a small cache of the fields already fetched for them,
an optional pending clarification, and the last few exchanges.

It is per (actor_id, session_id), lives in process memory, and is
mirrored into the agent state so AgentCore can persist it.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from config import (
    ENTITY_CACHE_TTL_SECONDS,
    MUTATION_CONTEXT_MAX_TURNS,
)

ENTITY_TYPES = ("incident", "issue", "employee", "team")

PENDING_MAX_TURNS = 3

RECENT_TURNS = 2

_MAX_CACHED_FIELDS = 30


@dataclass
class Entity:
    type: str
    id: str
    key: str
    label: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    fetched_at: float = 0.0
    turn: int = 0


class SessionState:

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        clock: Callable[[], float] = time.monotonic,
    ):

        self.actor_id = actor_id

        self.session_id = session_id

        self.clock = clock

        self.turn = 0

        self.focus: Dict[str, Entity] = {}

        self.last_type: Optional[str] = None

        self.last_operation: Optional[str] = None

        self.pending: Optional[Dict[str, Any]] = None

        self.recent: Deque[Tuple[str, str]] = deque(maxlen=RECENT_TURNS)

    # --------------------------------------------------------
    # Turns
    # --------------------------------------------------------

    def begin_turn(self) -> None:

        self.turn += 1

        if self.pending and self.turn - self.pending.get("turn", 0) > PENDING_MAX_TURNS:
            self.pending = None

    def remember_turn(self, user_text: str, reply_text: str) -> None:

        self.recent.append(
            (
                " ".join(str(user_text).split())[:100],
                " ".join(str(reply_text).split())[:120],
            )
        )

    # --------------------------------------------------------
    # Focus
    # --------------------------------------------------------

    def set_focus(
        self,
        type: str,
        id: str,
        key: str,
        label: str = "",
        data: Optional[Dict[str, Any]] = None,
    ) -> Entity:
        """Make an entity the current one of its type."""

        entity = self.focus.get(type)

        if entity is None or entity.key != key:
            entity = Entity(type=type, id=id, key=key)

        entity.id = id

        entity.turn = self.turn

        if label:
            entity.label = label

        if data:

            entity.data.update(data)

            if len(entity.data) > _MAX_CACHED_FIELDS:
                for name in list(entity.data)[: len(entity.data) - _MAX_CACHED_FIELDS]:
                    entity.data.pop(name, None)

            entity.fetched_at = self.clock()

        self.focus[type] = entity

        self.last_type = type

        return entity

    def get_focus(
        self,
        type: Optional[str] = None,
        max_age_turns: Optional[int] = None,
    ) -> Optional[Entity]:
        """
        The current entity of a type (default: the most recent one).

        `max_age_turns` limits how stale the context may be. Mutations
        pass MUTATION_CONTEXT_MAX_TURNS so an old target is never
        modified because of a stray "it".
        """

        entity = self.focus.get(type or self.last_type or "")

        if entity is None:
            return None

        if max_age_turns is not None and self.turn - entity.turn > max_age_turns:
            return None

        return entity

    def find(self, type: str, key: str) -> Optional[Entity]:

        entity = self.focus.get(type)

        return entity if entity and entity.key == key else None

    def cached_fields(
        self,
        entity: Optional[Entity],
        fields: List[str],
        ttl: float = ENTITY_CACHE_TTL_SECONDS,
    ) -> Optional[Dict[str, Any]]:
        """The requested fields if all are cached and still fresh."""

        if entity is None or not entity.fetched_at:
            return None

        if self.clock() - entity.fetched_at > ttl:
            return None

        if any(name not in entity.data for name in fields):
            return None

        return {name: entity.data[name] for name in fields}

    def apply_changes(self, type: str, key: str, changes: Dict[str, Any]) -> None:
        """Keep the cache correct after a successful write."""

        entity = self.find(type, key)

        if entity is None:
            return

        entity.data.update(changes)

        entity.fetched_at = self.clock()

    def invalidate(self, type: str, key: str) -> None:

        entity = self.find(type, key)

        if entity is not None:
            entity.fetched_at = 0.0

    def clear_focus(self, type: str, key: str) -> None:

        entity = self.find(type, key)

        if entity is None:
            return

        del self.focus[type]

        if self.last_type == type:
            self.last_type = next(iter(self.focus), None)

    # --------------------------------------------------------
    # Pending clarification
    # --------------------------------------------------------

    def set_pending(self, kind: str, **payload: Any) -> None:

        self.pending = {"kind": kind, "turn": self.turn, **payload}

    def clear_pending(self) -> None:

        self.pending = None

    # --------------------------------------------------------
    # Prompt context
    # --------------------------------------------------------

    def context_block(self) -> str:
        """Compact description of the conversation state for the model."""

        lines = []

        for type in ENTITY_TYPES:

            entity = self.focus.get(type)

            if entity is None:
                continue

            detail = entity.label or ""

            if type == "incident":

                bits = [
                    str(entity.data[name])
                    for name in ("priority", "state", "assigned_to")
                    if entity.data.get(name)
                ]

                detail = ", ".join(bits + ([detail] if detail else []))

            marker = " (latest)" if type == self.last_type else ""

            lines.append(
                f"- {type} {entity.id}{marker}" + (f": {detail}" if detail else "")
            )

        if self.last_operation:
            lines.append(f"- last operation: {self.last_operation}")

        if self.pending:
            lines.append(f"- awaiting user reply: {self.pending.get('kind')}")

        if self.recent:

            lines.append("Recent exchanges:")

            for question, answer in self.recent:
                lines.append(f"  U: {question}")
                lines.append(f"  A: {answer}")

        if not lines:
            return "Session context: nothing discussed yet."

        return (
            "Session context (resolve it/this/that/he/his/her from here; "
            "never ask for what is listed):\n" + "\n".join(lines)
        )

    # --------------------------------------------------------
    # Persistence (mirrored into agent.state)
    # --------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:

        return {
            "turn": self.turn,
            "last_type": self.last_type,
            "last_operation": self.last_operation,
            "pending": self.pending,
            "focus": {
                type: {
                    "id": entity.id,
                    "key": entity.key,
                    "label": entity.label,
                    "data": {
                        name: value
                        for name, value in entity.data.items()
                        if isinstance(value, (str, int, float, bool)) or value is None
                    },
                    "turn": entity.turn,
                }
                for type, entity in self.focus.items()
            },
            "recent": [list(item) for item in self.recent],
        }

    def load_dict(self, payload: Dict[str, Any]) -> None:
        """Restore a mirrored snapshot. Cached data is treated as stale."""

        if not isinstance(payload, dict):
            return

        self.turn = int(payload.get("turn", 0))

        self.last_type = payload.get("last_type")

        self.last_operation = payload.get("last_operation")

        self.pending = payload.get("pending")

        self.focus = {}

        for type, raw in (payload.get("focus") or {}).items():

            self.focus[type] = Entity(
                type=type,
                id=raw.get("id", ""),
                key=raw.get("key", ""),
                label=raw.get("label", ""),
                data=dict(raw.get("data") or {}),
                fetched_at=0.0,
                turn=int(raw.get("turn", 0)),
            )

        self.recent.clear()

        for item in payload.get("recent") or []:

            if isinstance(item, (list, tuple)) and len(item) == 2:
                self.recent.append((str(item[0]), str(item[1])))

    @property
    def mutation_max_turns(self) -> int:
        return MUTATION_CONTEXT_MAX_TURNS
