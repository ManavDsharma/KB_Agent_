"""
Agent loop control and session bookkeeping (Strands hooks).

Per request it:
    * refuses a repeated identical tool call (the answer is already in
      the conversation, and a repeated write must not run twice)
    * caps the number of tool calls, then stops the loop for good
    * feeds every tool result into the session state, so the LLM path
      keeps "it / he / that team" up to date
    * records tool name, duration and outcome for the request log
"""

import json
import logging
from typing import Any, Dict, List, Optional

from strands.hooks import (
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from config import MAX_TOOL_CALLS_PER_REQUEST
from context import absorb_tool_result
from memory.state import SessionState
from tools import MUTATING_TOOL_NAMES

logger = logging.getLogger("dtp.agent")

# Calls beyond the limit are refused for this many more attempts before
# the loop is aborted outright.
HARD_LIMIT_MARGIN = 4


class AgentLoopLimitExceeded(RuntimeError):
    """The model kept calling tools past the allowed budget."""


def _result_data(result: Any) -> Optional[Dict[str, Any]]:
    """The JSON object a tool returned, if it returned one."""

    try:

        if result.get("status") != "success":
            return None

        text = "".join(
            block.get("text", "")
            for block in result.get("content", [])
            if isinstance(block, dict)
        )

        data = json.loads(text)

        return data if isinstance(data, dict) else None

    except Exception:
        return None


class AgentGuard(HookProvider):

    def __init__(
        self,
        state: SessionState,
        max_tool_calls: int = MAX_TOOL_CALLS_PER_REQUEST,
    ):

        self.state = state

        self.max_tool_calls = max_tool_calls

        self.calls: List[Dict[str, Any]] = []

        self.limit_hit = False

        self._seen = set()

        self._count = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:

        registry.add_callback(BeforeInvocationEvent, self._reset)

        registry.add_callback(BeforeToolCallEvent, self._before_tool)

        registry.add_callback(AfterToolCallEvent, self._after_tool)

    # --------------------------------------------------------

    def _reset(self, event: BeforeInvocationEvent) -> None:

        self.calls = []

        self.limit_hit = False

        self._seen = set()

        self._count = 0

    def _before_tool(self, event: BeforeToolCallEvent) -> None:

        name = event.tool_use.get("name", "")

        arguments = event.tool_use.get("input") or {}

        signature = name + json.dumps(arguments, sort_keys=True, default=str)

        if signature in self._seen:

            event.cancel_tool = (
                "Duplicate call: this exact request already ran in this turn. "
                "Use the earlier result."
            )

            return

        self._seen.add(signature)

        self._count += 1

        if self._count > self.max_tool_calls + HARD_LIMIT_MARGIN:

            self.limit_hit = True

            raise AgentLoopLimitExceeded("tool call budget exhausted")

        if self._count > self.max_tool_calls:

            self.limit_hit = True

            event.cancel_tool = (
                "Tool-call limit reached for this request. Answer with what you "
                "have, or ask the user to narrow the request."
            )

    def _after_tool(self, event: AfterToolCallEvent) -> None:

        name = event.tool_use.get("name", "")

        duration_ms = int((event.duration or 0) * 1000)

        data = None if event.cancel_message else _result_data(event.result)

        failed = (
            event.exception is not None
            or event.result.get("status") != "success"
            or (isinstance(data, dict) and data.get("ok") is False)
        )

        self.calls.append(
            {
                "name": name,
                "ms": duration_ms,
                "ok": not failed,
            }
        )

        logger.info(
            "tool_call name=%s ms=%d ok=%s",
            name,
            duration_ms,
            not failed,
        )

        if data is None:
            return

        try:

            absorb_tool_result(
                self.state,
                name,
                event.tool_use.get("input") or {},
                data,
            )

        except Exception:
            logger.exception("session state update failed for tool %s", name)

        # A write changes what a repeated read would return.
        if name in MUTATING_TOOL_NAMES and not failed:
            self._seen.clear()
