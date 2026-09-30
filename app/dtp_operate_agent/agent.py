"""
Agent orchestration.

    process_user_query(user_input, runtime)
        1. route the message deterministically (router.py)
        2. clear, structured requests are executed directly (actions.py):
           no model call, no tokens
        3. everything else goes to a Strands agent that carries only the
           tools of the request's domain (domains.py; "all" when unclear)

The LLM agents are created only when a request needs them. They do not
replay the whole conversation: the structured session state
(memory/state.py) supplies "what are we talking about" in a few lines
of the system prompt, and the conversation window is kept small. Every
model call re-sends the system prompt and the tool definitions, so both
are kept lean and cached.
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.models import BedrockModel
from strands.models.model import CacheConfig
from strands.types.exceptions import (
    ContextWindowOverflowException,
    MaxTokensReachedException,
    ModelThrottledException,
)

from actions import can_execute, execute
from config import (
    BEDROCK_MODEL_ID,
    CONVERSATION_WINDOW_SIZE,
    LLM_MAX_TOKENS,
    PROMPT_CACHING,
    get_boto3_session,
    get_boto_client_config,
    validate_config,
    APP_TIMEZONE,
)
from domains import classify_domain
from hooks import AgentGuard, AgentLoopLimitExceeded
from memory.session import create_memory_session_manager
from memory.store import SessionRuntime
from responses import Reply, error_reply, text_reply
from router import DeterministicRouter
from services.common import today
from tools import all_tools

logger = logging.getLogger("dtp.agent")


# ============================================================
# PROMPT
# ============================================================

SYSTEM_PROMPT = """You are the DTP Operate assistant for IT service-management data: incidents/requests (INC0000258, REQ0000162), Category Matrix issues (Issue-N) and the roster (employees, teams, shifts, availability Y=available N=not available).

- Exact or current facts and every create/update/delete: use the data tools, never the knowledge base or memory. Definitions, how-tos, similar past incidents: search_knowledge_base with the narrowest source.
- The session context below says what it/this/that/he/his/her mean: use its IDs and never ask for what it lists. Convert relative dates with the date given.
- Fewest tool calls: pass `fields`; for a whole team use get_team_availability, never one call per person; never repeat a call; stop once you can answer.
- If target and change are clear, do it at once with no confirmation, then confirm in one or two lines, e.g. "Updated INC0000258" then "- Priority: P1-Critical → P2-High". If target, field or value is missing, or several records match, ask ONE short question. If a tool reports an invalid or ambiguous value, offer the allowed options. For create, ask only for what the tool says is missing.
- Be concise (under about 150 words) and conversational: a short sentence first, then "- " bullets if needed; **bold** for IDs and key values; no headings, tables, raw JSON, tool names or reasoning. For lists show at most 10 rows and say how many more there are. If a tool fails, say briefly why."""


def caching_enabled() -> bool:

    if PROMPT_CACHING in ("on", "true", "1", "yes"):
        return True

    if PROMPT_CACHING in ("off", "false", "0", "no"):
        return False

    return "claude" in (BEDROCK_MODEL_ID or "").lower()


def build_system_prompt(runtime: SessionRuntime):
    """Static instructions (cacheable) followed by the per-turn context."""

    state = runtime.state

    dynamic = (
        f"Today: {today().isoformat()} ({APP_TIMEZONE}). User: {state.actor_id}.\n"
        f"{state.context_block()}"
    )

    if caching_enabled():

        return [
            {"text": SYSTEM_PROMPT},
            {"cachePoint": {"type": "default"}},
            {"text": dynamic},
        ]

    return [{"text": SYSTEM_PROMPT + "\n\n" + dynamic}]


# ============================================================
# AGENT
# ============================================================

def create_model() -> BedrockModel:

    options: Dict[str, Any] = {}

    if caching_enabled():

        # Cache the tool schemas. The system prompt carries its own
        # cache point (static instructions | per-turn context).
        options["cache_config"] = CacheConfig(
            strategy="auto",
            tools_ttl=True,
            system_prompt_ttl=False,
        )

    return BedrockModel(
        model_id=BEDROCK_MODEL_ID,
        boto_session=get_boto3_session(),
        boto_client_config=get_boto_client_config(),
        temperature=0.0,
        max_tokens=LLM_MAX_TOKENS,
        **options,
    )


def build_agent(runtime: SessionRuntime, domain: str = "all") -> Agent:
    """
    Create the LLM agent for one (actor, session, tool domain).

    Each domain has its own agent id, so AgentCore Memory keeps their
    conversations apart; they all share the session state.
    """

    validate_config()

    state = runtime.state

    guard = AgentGuard(state)

    agent = Agent(
        model=create_model(),
        agent_id=f"dtp-{domain}",
        name=f"DTPOperateAgent-{domain}",
        system_prompt=build_system_prompt(runtime),
        tools=all_tools(domain),
        conversation_manager=SlidingWindowConversationManager(
            window_size=CONVERSATION_WINDOW_SIZE,
            should_truncate_results=True,
        ),
        hooks=[guard],
        session_manager=create_memory_session_manager(
            state.session_id,
            state.actor_id,
        ),
        state={"actor_id": state.actor_id},
        callback_handler=None,
    )

    # Tools read the caller from the agent state for audit fields.
    agent.state.set("actor_id", state.actor_id)

    # A restarted runtime restores the mirrored session state.
    if state.turn <= 1:

        try:

            saved = agent.state.get("session_state")

            if saved and not state.focus:
                state.load_dict(saved)

        except Exception:
            logger.exception("could not restore session state")

    runtime.guards[domain] = guard

    return agent


# ============================================================
# RESULT HELPERS
# ============================================================

_USAGE_KEYS = {
    "input_tokens": "inputTokens",
    "output_tokens": "outputTokens",
    "total_tokens": "totalTokens",
    "cache_read_tokens": "cacheReadInputTokens",
    "cache_write_tokens": "cacheWriteInputTokens",
}


def _usage_snapshot(agent: Agent) -> Dict[str, int]:

    try:

        raw = agent.event_loop_metrics.accumulated_usage or {}

        return {name: int(raw.get(key, 0) or 0) for name, key in _USAGE_KEYS.items()}

    except Exception:
        return {name: 0 for name in _USAGE_KEYS}


def _usage_delta(before: Dict[str, int], after: Dict[str, int]) -> Dict[str, int]:
    return {name: max(after[name] - before[name], 0) for name in _USAGE_KEYS}


def get_final_response(result: Any) -> str:

    text = str(result).strip() if result is not None else ""

    return text or "I don't have an answer for that."


def _partial_text(agent: Agent) -> str:
    """Text of the last assistant message (Strands keeps a cut-off answer there)."""

    try:

        message = agent.messages[-1]

        if message.get("role") != "assistant":
            return ""

        # Strands replaces a cut-off tool call with an internal note; that is
        # not an answer, so it is dropped.
        return "".join(
            block.get("text", "")
            for block in message.get("content", [])
            if isinstance(block, dict)
            and "tool use was incomplete" not in block.get("text", "")
        ).strip()

    except Exception:
        return ""


def _mirror_state(runtime: SessionRuntime) -> None:
    """Keep a copy in the agent state so AgentCore can persist it."""

    snapshot = runtime.state.to_dict()

    for agent in runtime.agents.values():

        try:
            agent.state.set("session_state", snapshot)

        except Exception:
            logger.debug("could not mirror session state", exc_info=True)


# ============================================================
# LLM PATH
# ============================================================

def _run_llm(
    user_input: str,
    runtime: SessionRuntime,
    meta: Dict[str, Any],
) -> Reply:

    # Send only the tool definitions this request can use.
    domain = classify_domain(user_input, runtime.state)

    meta["domain"] = domain

    try:
        agent = runtime.get_agent(build_agent, domain)

    except Exception:

        logger.exception("agent initialization failed")

        meta["error"] = "agent_init"

        return error_reply(
            "The assistant is unavailable right now. Please try again in a moment."
        )

    agent.system_prompt = build_system_prompt(runtime)

    before = _usage_snapshot(agent)

    guard = runtime.guards.get(domain)

    try:

        result = agent(user_input)

    except ContextWindowOverflowException:

        agent.messages.clear()

        meta["error"] = "context_overflow"

        return error_reply(
            "That conversation got too long, so I reset it. Please repeat your last request."
        )

    except ModelThrottledException:

        meta["error"] = "throttled"

        return error_reply("The assistant is busy right now. Please try again in a few seconds.")

    except MaxTokensReachedException:

        meta["error"] = "max_tokens"

        # The model ran into the output cap. Show what it had written rather
        # than throwing it away; only a cut-off tool call has no text.
        partial = _partial_text(agent)

        if partial:
            return text_reply(
                partial + "\n\n_(The answer was cut off. Ask for the rest, or for less at a time.)_"
            )

        return error_reply("The answer got too long to finish. Please ask for less at a time.")

    except Exception as exc:

        if isinstance(exc, AgentLoopLimitExceeded) or getattr(guard, "limit_hit", False):

            meta["error"] = "loop_limit"

            return error_reply(
                "I couldn't finish that within the allowed number of steps. "
                "Could you narrow the request?"
            )

        logger.exception("agent execution failed")

        meta["error"] = type(exc).__name__

        return error_reply(
            "Something went wrong while processing your request. Please try again."
        )

    finally:

        meta["usage"] = _usage_delta(before, _usage_snapshot(agent))

        meta["tools"] = [call["name"] for call in getattr(guard, "calls", [])]

    return text_reply(get_final_response(result))


# ============================================================
# ENTRY POINT
# ============================================================

_ZERO_USAGE = {name: 0 for name in _USAGE_KEYS}


def process_user_query(
    user_input: str,
    runtime: SessionRuntime,
) -> Tuple[Reply, Dict[str, Any]]:
    """
    Answer one message.

    Returns (reply, metadata). The metadata (route, tokens, tools,
    latency) is for logging only and is never sent to the frontend.
    """

    started = time.perf_counter()

    text = (user_input or "").strip()

    meta: Dict[str, Any] = {
        "route": "empty",
        "direct": False,
        "usage": dict(_ZERO_USAGE),
        "tools": [],
    }

    if not text:
        return text_reply("Please provide a request."), meta

    state = runtime.state

    with runtime.lock:

        state.begin_turn()

        intent = DeterministicRouter.route(text, state)

        meta["route"] = intent.route

        meta["reason"] = intent.reason

        reply: Optional[Reply] = None

        if can_execute(intent):

            try:

                reply = execute(intent, state, state.actor_id)

                # None: the request turned out not to be one the direct
                # path can answer (e.g. an unknown team name).
                meta["direct"] = reply is not None

            except Exception:

                logger.exception("direct execution failed route=%s", intent.route)

                # A failed read can still be answered by the model; a
                # failed write is never retried behind the user's back.
                if intent.operation in ("update", "delete"):

                    meta["direct"] = True

                    meta["error"] = "direct_failed"

                    reply = error_reply(
                        "I couldn't complete that change. Please check it and try again."
                    )

        if reply is None:

            meta["route"] = "llm"

            reply = _run_llm(text, runtime, meta)

        state.remember_turn(text, reply.text)

        _mirror_state(runtime)

    meta["latency_ms"] = int((time.perf_counter() - started) * 1000)

    return reply, meta
