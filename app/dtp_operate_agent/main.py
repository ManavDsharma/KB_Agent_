import logging
import re
import uuid
from typing import Any, Dict, Tuple

from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext

from agent import process_user_query
from config import ENVIRONMENT, LOG_LEVEL, missing_config
from memory.store import SessionStore


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("dtp.main")


# ============================================================
# AGENTCORE APPLICATION
# ============================================================

app = BedrockAgentCoreApp()


# ============================================================
# STARTUP CHECK
# ============================================================

# Cheap and offline: report missing configuration at start instead of
# on the first user request.
_missing = missing_config()

if _missing:
    logger.warning("Missing configuration: %s", ", ".join(_missing))


# ============================================================
# SESSIONS
# ============================================================

# One runtime (session state + lazily created LLM agent) per
# (actor_id, session_id). Different users and different sessions never
# share state. Idle sessions expire and the store is size-bounded.
_sessions = SessionStore()


# ============================================================
# REQUEST VALIDATION
# ============================================================

_ID_PATTERN = re.compile(r"^[\w.@:+=/-]{1,128}$")


def validate_payload(payload: Any) -> str:
    """Validate the invocation payload and return the user prompt."""

    if payload is None:
        raise ValueError("Request payload cannot be empty.")

    if not isinstance(payload, dict):
        raise ValueError("Request payload must be a JSON object.")

    prompt = payload.get("prompt")

    if prompt is None:
        raise ValueError("Missing required field: 'prompt'.")

    if not isinstance(prompt, str):
        raise ValueError("Field 'prompt' must be a string.")

    prompt = prompt.strip()

    if not prompt:
        raise ValueError("Field 'prompt' cannot be empty.")

    return prompt


def _clean_id(value: Any, name: str) -> str:

    text = str(value).strip()

    if not _ID_PATTERN.match(text):
        raise ValueError(f"Field '{name}' is not a valid identifier.")

    return text


def resolve_identity(payload: Dict[str, Any], context: Any) -> Tuple[str, str]:
    """
    Session and actor for this request.

    The AgentCore session ID comes from the runtime context. The actor
    is supplied by the caller. Outside local development a missing
    session is rejected, and a missing actor gets an identity that is
    unique to the session so users can never share state.
    """

    session_id = getattr(context, "session_id", None) or payload.get("session_id")

    if not session_id:

        if ENVIRONMENT != "local":
            raise ValueError("A session ID is required.")

        session_id = "local-session"

    actor_id = payload.get("actor_id") or getattr(context, "actor_id", None)

    if not actor_id:

        actor_id = "local-user" if ENVIRONMENT == "local" else f"anon-{session_id}"

        logger.warning("Request without actor_id; using an isolated anonymous actor.")

    return _clean_id(session_id, "session_id"), _clean_id(actor_id, "actor_id")


# ============================================================
# AGENTCORE ENTRYPOINT
# ============================================================

@app.entrypoint
def invoke(
    payload: Dict[str, Any],
    context: RequestContext,
):
    """
    AgentCore Runtime entrypoint.

    Expected payload:

        {
            "prompt": "What is the impact of Issue-9?",
            "actor_id": "user-123"
        }

    The AgentCore session_id is taken from the RequestContext.

    Response:

        {
            "response": "<Markdown text>",
            "structured": {"type": "table|field|list|text|update_confirmation|error", ...}
        }

    `response` keeps working for existing consumers; `structured` lets
    the frontend render without parsing Markdown. Token usage, routing
    and internal details are logged, never returned.
    """

    request_id = uuid.uuid4().hex[:12]

    try:

        prompt = validate_payload(payload)

        session_id, actor_id = resolve_identity(payload, context)

        runtime = _sessions.get(actor_id, session_id)

        reply, meta = process_user_query(prompt, runtime)

        usage = meta.get("usage", {})

        logger.info(
            "request id=%s session=%s route=%s domain=%s direct=%s tools=%s "
            "tokens_in=%s tokens_out=%s cache_read=%s cache_write=%s ms=%s error=%s",
            request_id,
            session_id,
            meta.get("route"),
            meta.get("domain", "-"),
            meta.get("direct"),
            ",".join(meta.get("tools", [])) or "-",
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("cache_read_tokens", 0),
            usage.get("cache_write_tokens", 0),
            meta.get("latency_ms"),
            meta.get("error", "-"),
        )

        return reply.as_payload()

    except ValueError as exc:

        logger.warning("request id=%s invalid: %s", request_id, exc)

        return {
            "error": "Invalid request",
            "message": str(exc),
        }

    except Exception:

        logger.exception("request id=%s failed", request_id)

        return {
            "error": "Agent execution failed",
            "message": "Something went wrong while processing your request. Please try again.",
        }


# ============================================================
# LOCAL RUNTIME
# ============================================================

if __name__ == "__main__":
    app.run()
