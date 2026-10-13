"""
Planner: turns a write request the fast path cannot fully understand
("set both priority and urgency to High", "mark employee1 and employee2 as
unavailable on 2nd August", "close INC1 and INC2") into structured operations.

It is the only place a model is used to *understand* a write, and it is kept
deliberately small and unable to act:

    * one call, no tools, no record data, a ~1 KB prompt: a few hundred tokens
    * it must copy IDs, names, values and dates as the user wrote them
    * its output is never trusted: plans.py checks that everything it names is
      really in the user's message, resolves and validates it through the
      services, and executes nothing the user did not say

If the call fails or returns nothing usable, the caller falls back to the
ordinary tool agent: a planner failure never changes data.
"""

import logging
from functools import lru_cache
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from config import (
    PLANNER_MAX_TOKENS,
    PLANNER_MODEL_ID,
    get_boto3_session,
    get_boto_client_config,
)

logger = logging.getLogger("dtp.planner")


# ============================================================
# OUTPUT SCHEMA (kept small: it is sent to the model as a tool definition)
# ============================================================

class PlannedOp(BaseModel):

    action: Literal["update", "availability"]

    record: Literal["incident", "issue", "employee"]

    targets: List[str] = Field(default_factory=list)

    changes: Dict[str, str] = Field(default_factory=dict)

    dates: List[str] = Field(default_factory=list)

    until: Optional[str] = None

    value: Optional[str] = None


class PlanDraft(BaseModel):

    ops: List[PlannedOp] = Field(default_factory=list)


# ============================================================
# PROMPT
# ============================================================

PLANNER_PROMPT = """Extract the data changes the user asks for as operations. Copy IDs, names, values and dates exactly as the user wrote them; never invent, complete or correct them.
- action "update": set fields. record "incident" (INC/REQ tickets: priority, urgency, state, assigned_to, category, sub_category, team_id, short_description, description, working_notes, assignment_sla, resolution_sla), "issue" (Issue-N: category_name, sub_category_name, issue, issue_description, issue_type, impact, urgency, affected_service, business_impact, team_id, active) or "employee" (roster: shift, level, team_id, shift_start_time, shift_end_time, active). changes = {field: new value}. "close"/"resolve" mean state Closed/Resolved.
- action "availability" (record "employee"): mark people available / not available. value as written (e.g. "false", "leave", "available"); dates as written; for "3rd to 5th August" give dates ["3rd August"] and until "5th August".
- targets: every ID or name the user gave for that change; [] when the user says it/this/that/he/she/his/her/them/their/both of them.
- One operation per distinct change; "both X and Y to High" is ONE operation with two fields; several targets share one operation.
- Not a change request, or you are unsure: return no operations."""


# ============================================================
# MODEL
# ============================================================

@lru_cache(maxsize=1)
def _planner_model():
    """One shared model client (created on first use; tests replace this)."""

    from strands.models import BedrockModel

    return BedrockModel(
        model_id=PLANNER_MODEL_ID,
        boto_session=get_boto3_session(),
        boto_client_config=get_boto_client_config(),
        temperature=0.0,
        max_tokens=PLANNER_MAX_TOKENS,
    )


_USAGE_KEYS = {
    "input_tokens": "inputTokens",
    "output_tokens": "outputTokens",
    "total_tokens": "totalTokens",
    "cache_read_tokens": "cacheReadInputTokens",
    "cache_write_tokens": "cacheWriteInputTokens",
}


def _usage(result) -> Dict[str, int]:

    try:

        raw = result.metrics.accumulated_usage or {}

        return {name: int(raw.get(key, 0) or 0) for name, key in _USAGE_KEYS.items()}

    except Exception:
        return {name: 0 for name in _USAGE_KEYS}


def extract(
    text: str,
    today: str,
    context: str = "",
) -> Tuple[Optional[PlanDraft], Dict[str, int]]:
    """
    (draft, token usage) for a request, or (None, usage) when the model could
    not produce one. Never raises.
    """

    from strands import Agent

    prompt = f"Today: {today}."

    if context:
        prompt += f" Context: {context}."

    prompt += f"\nRequest: {text}"

    try:

        agent = Agent(
            model=_planner_model(),
            system_prompt=PLANNER_PROMPT,
            tools=[],
            callback_handler=None,
        )

        result = agent(prompt, structured_output_model=PlanDraft)

        draft = result.structured_output

        return (draft if isinstance(draft, PlanDraft) else None), _usage(result)

    except Exception:

        logger.exception("planner call failed")

        return None, {}
