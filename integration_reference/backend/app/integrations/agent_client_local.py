"""Calls dtp_agent running locally (python main.py -> http://localhost:8080).

NOTE: this file was rebuilt from the contract in agent_service / agent_ws
(`invoke(prompt, session_id, actor_id) -> {"response", "structured", "metadata"}`
and `settings.agent_local_url`), because the original was not shared. If yours
has extra behaviour, keep it and just add the two changes below:
  * pass `actor_id` in the payload
  * return `structured` alongside `response`

The local server reads the session from the same header AgentCore sets.
"""

import httpx

from app.config import settings

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


class AgentClientError(Exception):
    pass


def _agent_error(body: dict) -> dict:
    """The agent's own error messages are already user-safe: show them."""
    message = body.get("message") or body.get("error") or "The agent could not complete that request."
    return {
        "response": message,
        "structured": {"type": "error", "message": message, "text": message, "tone": "error"},
        "metadata": {},
    }


async def invoke(prompt: str, session_id: str, actor_id: str | None = None) -> dict:
    payload = {"prompt": prompt}
    if actor_id:
        payload["actor_id"] = actor_id

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                settings.agent_local_url,
                json=payload,
                headers={SESSION_HEADER: session_id},
            )
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        raise AgentClientError(f"Local agent invocation failed: {exc}") from exc

    if "error" in body:
        return _agent_error(body)

    return {
        "response": body.get("response", ""),
        "structured": body.get("structured"),
        "metadata": body.get("metadata", {}),
    }
