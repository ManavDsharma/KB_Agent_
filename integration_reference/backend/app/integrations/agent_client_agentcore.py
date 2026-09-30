"""Calls dtp_agent once it's deployed to a real Amazon Bedrock AgentCore
Runtime. Real implementation, built ahead of deployment — will raise
AgentClientError (caught by agent_service's fallback) until
AGENTCORE_RUNTIME_ARN is actually set, which is expected until then.

boto3's bedrock-agentcore client and its invoke_agent_runtime operation shape
were confirmed directly against the installed SDK (boto3>=1.42), not assumed.

The agent replies with {"response": <markdown>, "structured": {...}}. Both are
passed through: `response` is always complete Markdown, `structured` lets the
chat UI render cards, badges, buttons and suggestions.
"""

import asyncio
import json

import boto3
from botocore.config import Config

from app.config import settings

_client = None


class AgentClientError(Exception):
    pass


def _get_client():
    global _client
    if _client is None:
        kwargs = {
            "region_name": settings.resolved_aws_region,
            # A model-backed answer can take a while; don't cut it off at
            # botocore's 60 s default read timeout.
            "config": Config(read_timeout=120, retries={"max_attempts": 2, "mode": "standard"}),
        }
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            kwargs["aws_access_key_id"] = settings.aws_access_key_id
            kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        _client = boto3.client("bedrock-agentcore", **kwargs)
    return _client


def _invoke_blocking(kwargs: dict) -> dict:
    response = _get_client().invoke_agent_runtime(**kwargs)
    return json.loads(response["response"].read())


def _agent_error(body: dict) -> dict:
    """The agent's own error messages are already user-safe: show them."""
    message = body.get("message") or body.get("error") or "The agent could not complete that request."
    return {
        "response": message,
        "structured": {"type": "error", "message": message, "text": message, "tone": "error"},
        "metadata": {},
    }


async def invoke(prompt: str, session_id: str, actor_id: str | None = None) -> dict:
    if not settings.agentcore_runtime_arn:
        raise AgentClientError("AGENTCORE_RUNTIME_ARN is not configured")

    # AgentCore requires runtimeSessionId to be at least 33 characters — a
    # standard UUID (36 chars) clears that; anything shorter needs padding.
    runtime_session_id = session_id if len(session_id) >= 33 else session_id.ljust(33, "0")

    # actor_id identifies the signed-in user. The agent scopes its session
    # state by (actor_id, session_id) and records it on every change it makes.
    payload = {"prompt": prompt}
    if actor_id:
        payload["actor_id"] = actor_id

    kwargs = {
        "agentRuntimeArn": settings.agentcore_runtime_arn,
        "runtimeSessionId": runtime_session_id,
        "contentType": "application/json",
        "accept": "application/json",
        "payload": json.dumps(payload).encode("utf-8"),
    }
    if settings.agentcore_runtime_qualifier:
        kwargs["qualifier"] = settings.agentcore_runtime_qualifier

    try:
        # boto3 is blocking; run it off the event loop so one slow agent
        # answer doesn't freeze every other WebSocket connection.
        body = await asyncio.to_thread(_invoke_blocking, kwargs)
    except Exception as exc:  # botocore raises various ClientError subtypes
        raise AgentClientError(f"AgentCore Runtime invocation failed: {exc}") from exc

    if "error" in body:
        return _agent_error(body)

    return {
        "response": body.get("response", ""),
        "structured": body.get("structured"),
        "metadata": body.get("metadata", {}),
    }
