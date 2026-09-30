"""Orchestrates AI Agent invocation: picks the client by AGENT_MODE, falls
back to a canned response on any failure so the chat page is never broken —
same resilience philosophy as the ticket/reference data fallbacks."""

import logging

from app.config import settings
from app.integrations import agent_client_agentcore, agent_client_local

logger = logging.getLogger(__name__)

_FALLBACK_RESPONSE = (
    "I couldn't reach the assistant just now, so this is a placeholder reply. "
    "Once connected, I can look up and update incidents and requests, "
    "check category-matrix issues, and tell you who is on shift or available."
)

_FALLBACK_STRUCTURED = {
    "type": "error",
    "message": _FALLBACK_RESPONSE,
    "text": _FALLBACK_RESPONSE,
    "tone": "error",
}


async def invoke_agent(prompt: str, session_id: str, actor_id: str | None = None) -> dict:
    client = agent_client_agentcore if settings.agent_mode == "agentcore" else agent_client_local

    try:
        result = await client.invoke(prompt, session_id, actor_id)
        return {
            "response": result["response"],
            # Rich rendering hints for the chat UI (cards, badges, buttons,
            # suggestions). None if the agent didn't send any.
            "structured": result.get("structured"),
            "metadata": result.get("metadata", {}),
            "source": "agent",
        }
    except Exception as exc:
        # Details go to the log, never to the browser.
        logger.warning("Agent invocation failed (mode=%s): %s", settings.agent_mode, exc)
        return {
            "response": _FALLBACK_RESPONSE,
            "structured": _FALLBACK_STRUCTURED,
            "metadata": {},
            "source": "fallback",
        }
