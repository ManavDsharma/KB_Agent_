import logging
from typing import Any, Dict, Tuple
 
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
 
from agent import build_agent, process_user_query
 
 
# ============================================================
# LOGGING
# ============================================================
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
 
logger = logging.getLogger("incident-management-agent")
 
 
# ============================================================
# AGENTCORE APPLICATION
# ============================================================
 
app = BedrockAgentCoreApp()
 
 
# ============================================================
# SESSION-AWARE SUPERVISOR CACHE
# ============================================================
 
_supervisors: Dict[Tuple[str, str], Any] = {}
 
 
def get_supervisor(
    session_id: str,
    actor_id: str,
):
    """
    Return a supervisor associated with the current conversation.
 
    The combination of actor_id + session_id ensures that
    different users and sessions do not share the same
    in-memory Agent/Memory session.
    """
 
    cache_key = (
        actor_id,
        session_id,
    )
 
    if cache_key not in _supervisors:
 
        logger.info(
            "Creating supervisor for actor_id=%s session_id=%s",
            actor_id,
            session_id,
        )
 
        _supervisors[cache_key] = build_agent(
            session_id=session_id,
            actor_id=actor_id,
        )
 
    return _supervisors[cache_key]
 
 
# ============================================================
# SHORT-TERM SESSION CONTEXT
# ============================================================
 
# This context is intentionally separate from AgentCore Memory.
#
# Purpose:
#   Resolve immediate references such as:
#
#       "it"
#       "its"
#       "that issue"
#       "that incident"
#
# Example:
#
#   User:
#       Show me Issue-9
#
#   Context:
#       last_entity_type = issue
#       last_entity_id   = Issue-9
#
#   User:
#       What is its impact?
#
#   Context resolver:
#       What is Issue-9 impact?
#
# This avoids an LLM call for simple contextual follow-ups.
#
# AgentCore Memory remains responsible for longer-lived
# conversation memory, summaries and preferences.
 
_session_contexts: Dict[
    Tuple[str, str],
    Dict[str, Any],
] = {}
 
 
def get_session_context(
    session_id: str,
    actor_id: str,
) -> Dict[str, Any]:
    """
    Return the short-term context for a specific
    actor + AgentCore session.
    """
 
    cache_key = (
        actor_id,
        session_id,
    )
 
    if cache_key not in _session_contexts:
 
        logger.info(
            "Creating short-term session context "
            "for actor_id=%s session_id=%s",
            actor_id,
            session_id,
        )
 
        _session_contexts[cache_key] = {
            "last_entity_type": None,
            "last_entity_id": None,
            "last_field": None,
            "last_route": None,
        }
 
    return _session_contexts[cache_key]
 
 
# ============================================================
# REQUEST VALIDATION
# ============================================================
 
def validate_payload(
    payload: Any,
) -> str:
    """
    Validate and extract the user prompt from the
    AgentCore invocation payload.
    """
 
    if payload is None:
 
        raise ValueError(
            "Request payload cannot be empty."
        )
 
    if not isinstance(
        payload,
        dict,
    ):
 
        raise ValueError(
            "Request payload must be a JSON object."
        )
 
    prompt = payload.get(
        "prompt"
    )
 
    if prompt is None:
 
        raise ValueError(
            "Missing required field: 'prompt'."
        )
 
    if not isinstance(
        prompt,
        str,
    ):
 
        raise ValueError(
            "Field 'prompt' must be a string."
        )
 
    prompt = prompt.strip()
 
    if not prompt:
 
        raise ValueError(
            "Field 'prompt' cannot be empty."
        )
 
    return prompt
 
 
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
 
    actor_id is optional for local testing.
 
    The AgentCore session_id is obtained from RequestContext
    whenever available.
 
    IMPORTANT:
    Token consumption and internal routing metadata are NOT
    returned to the frontend.
    """
 
    try:
 
        # ----------------------------------------------------
        # SESSION ID
        # ----------------------------------------------------
 
        session_id = getattr(
            context,
            "session_id",
            None,
        )
 
        if not session_id:
 
            session_id = payload.get(
                "session_id",
                "local-session",
            )
 
        # ----------------------------------------------------
        # ACTOR ID
        # ----------------------------------------------------
 
        actor_id = payload.get(
            "actor_id",
            None,
        )
 
        if not actor_id:
 
            actor_id = getattr(
                context,
                "actor_id",
                None,
            )
 
        # Local fallback.
        #
        # Production callers should provide a stable
        # actor/user identifier.
 
        if not actor_id:
 
            actor_id = "default-user"
 
        logger.info(
            "Received AgentCore request. "
            "session_id=%s actor_id=%s",
            session_id,
            actor_id,
        )
 
        # ----------------------------------------------------
        # VALIDATE PROMPT
        # ----------------------------------------------------
 
        user_input = validate_payload(
            payload
        )
 
        # ----------------------------------------------------
        # SESSION-AWARE SUPERVISOR
        # ----------------------------------------------------
 
        supervisor = get_supervisor(
            session_id=session_id,
            actor_id=actor_id,
        )
 
        # ----------------------------------------------------
        # SHORT-TERM SESSION CONTEXT
        # ----------------------------------------------------
 
        session_context = get_session_context(
            session_id=session_id,
            actor_id=actor_id,
        )
 
        # ----------------------------------------------------
        # PROCESS REQUEST
        # ----------------------------------------------------
 
        response, _metadata = process_user_query(
            user_input,
            supervisor,
            session_context=session_context,
        )
 
        # ----------------------------------------------------
        # USER RESPONSE
        # ----------------------------------------------------
        #
        # Keep the AgentCore transport response minimal.
        #
        # The frontend should render the "response" field
        # as Markdown.
        #
        # No:
        # - token usage
        # - routing information
        # - internal metrics
        # - actor/session information
        #
        # is exposed to the user.
        # ----------------------------------------------------
 
        return {
            "response": response,
        }
 
    except ValueError as exc:
 
        logger.warning(
            "Invalid AgentCore request: %s",
            exc,
        )
 
        return {
            "error": "Invalid request",
            "message": str(exc),
        }
 
    except Exception as exc:
 
        logger.exception(
            "Agent execution failed",
        )
 
        return {
            "error": "Agent execution failed",
            "message": str(exc),
        }
 
 
# ============================================================
# LOCAL RUNTIME
# ============================================================
 
if __name__ == "__main__":
    app.run()
 
 