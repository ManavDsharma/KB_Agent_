"""
AgentCore Memory integration.

Conversation events are persisted to AgentCore Memory (keyed by
memory_id + actor_id + session_id) so a session can survive a runtime
restart. Long-term memory retrieval is off by default because it adds
tokens to every prompt; enable it with ENABLE_LTM_RETRIEVAL=true.
"""

import logging
from typing import Optional

from config import AWS_REGION, ENABLE_LTM_RETRIEVAL, get_memory_id

logger = logging.getLogger("dtp.memory")


def create_memory_session_manager(
    session_id: Optional[str],
    actor_id: Optional[str],
):
    """
    Create the AgentCore Memory session manager, or None.

    Memory problems never stop the agent: without a manager the agent
    still works, it just does not persist the session.
    """

    if not session_id or not actor_id:
        return None

    memory_id = get_memory_id()

    if not memory_id:
        logger.info("AgentCore Memory not configured; session is not persisted.")
        return None

    try:

        from bedrock_agentcore.memory.integrations.strands.config import (
            AgentCoreMemoryConfig,
            RetrievalConfig,
        )

        from bedrock_agentcore.memory.integrations.strands.session_manager import (
            AgentCoreMemorySessionManager,
        )

        retrieval_config = None

        if ENABLE_LTM_RETRIEVAL:

            retrieval_config = {
                f"/users/{actor_id}/preferences": RetrievalConfig(
                    top_k=3,
                    relevance_score=0.5,
                ),
                f"/summaries/{actor_id}/{session_id}": RetrievalConfig(
                    top_k=3,
                    relevance_score=0.5,
                ),
            }

        config = AgentCoreMemoryConfig(
            memory_id=memory_id,
            session_id=session_id,
            actor_id=actor_id,
            retrieval_config=retrieval_config,
            # Do not reload old tool payloads into a restored session.
            filter_restored_tool_context=True,
        )

        return AgentCoreMemorySessionManager(config, AWS_REGION)

    except Exception:

        logger.exception("AgentCore Memory initialization failed")

        return None
