"""WebSocket bridge between the frontend chat UI and agent_service.

One connection per page load. Each incoming message is {"prompt", "session_id"}
(the session_id is generated client-side once per mount so dtp_agent's
conversation memory persists across turns); each outgoing message is
agent_service.invoke_agent()'s result tagged with a "type" the frontend
switches on:

    {"type": "response", "response": <markdown>, "structured": {...}, "source": "agent" | "fallback", ...}
    {"type": "error", "message": "..."}
"""

import logging
import re

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.config import settings
from app.services import agent_service

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_PROMPT_CHARS = 2000
DEFAULT_SESSION_ID = "default-session-0000000000000000"
_SESSION_ID = re.compile(r"^[\w.:@-]{8,128}$")


def _actor_for(websocket: WebSocket) -> str:
    """Who is chatting. The agent scopes conversation state by (actor, session)
    and records the actor on every change it makes (updated_by / audit log).

    This must come from your authentication, never from the message body.
    Until the app has sign-in, an upstream proxy header or a fixed default is
    used; replace this with the authenticated user's e-mail / ID.
    """
    header = websocket.headers.get("x-user-email") or websocket.headers.get("x-forwarded-email")
    return header or getattr(settings, "agent_default_actor", None) or "dtp-web-user"


class AgentTestRequest(BaseModel):
    prompt: str = "Say hello and confirm that you are running."


@router.post("/api/agent/test")
async def test_agent(request: AgentTestRequest):
    return await agent_service.invoke_agent(
        prompt=request.prompt,
        session_id="backend-test-session-12345678901234567890",
        actor_id="backend-test-user",
    )


@router.get("/api/agent/health")
async def agent_health():
    """Powers the Sidebar's AI Agent status dot — a liveness check, not a
    full invocation (invoking the real agent just to check health would be
    wasteful, especially in agentcore mode)."""
    if settings.agent_mode == "agentcore":
        return {"mode": "agentcore", "configured": bool(settings.agentcore_runtime_arn)}

    ping_url = settings.agent_local_url.rsplit("/invocations", 1)[0] + "/ping"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(ping_url)
        reachable = response.status_code == 200
    except Exception:
        reachable = False
    return {"mode": "local", "configured": True, "reachable": reachable}


@router.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):
    await websocket.accept()
    actor_id = _actor_for(websocket)

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception:
                await websocket.send_json({"type": "error", "message": "Malformed message"})
                continue

            if not isinstance(data, dict):
                await websocket.send_json({"type": "error", "message": "Malformed message"})
                continue

            prompt = str(data.get("prompt") or "").strip()
            if not prompt:
                continue
            if len(prompt) > MAX_PROMPT_CHARS:
                await websocket.send_json(
                    {"type": "error", "message": f"Please keep messages under {MAX_PROMPT_CHARS} characters."}
                )
                continue

            session_id = str(data.get("session_id") or "")
            if not _SESSION_ID.match(session_id):
                session_id = DEFAULT_SESSION_ID

            result = await agent_service.invoke_agent(prompt, session_id, actor_id)
            await websocket.send_json({"type": "response", **result})
    except WebSocketDisconnect:
        logger.info("Agent WebSocket client disconnected")
