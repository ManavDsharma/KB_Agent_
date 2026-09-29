import os
import logging
 
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
 
logger = logging.getLogger(__name__)
 
AWS_REGION = os.getenv(
    "AWS_REGION",
    "ap-south-1",
)
 
 
def get_memory_id():
    """
    AgentCore injects the Memory ID into the runtime environment
    when the memory is associated with the AgentCore project.
    """
 
    # Check possible normalized environment variable names.
    possible_names = [
        "MEMORY_DtpOperateAgentMemory_ID",
        "MEMORY_DTPOPERATEAGENTMEMORY_ID",
        "MEMORY_DTP_OPERATE_AGENT_MEMORY_ID"
    ]
 
    for name in possible_names:
        value = os.getenv(name)
 
        if value:
            logger.info(
                "AgentCore Memory ID found using %s",
                name,
            )
            return value
 
    # Useful during troubleshooting.
    logger.warning(
        "AgentCore Memory ID was not found. "
        "Available MEMORY environment variables: %s",
        [
            key
            for key in os.environ
            if "MEMORY" in key.upper()
        ],
    )
 
    return None
 
 
def get_memory_session_manager(
    session_id: str,
    actor_id: str,
):
    """
    Create an AgentCore Memory session manager.
    """
 
    memory_id = get_memory_id()
 
    if not memory_id:
        return None
 
    if not session_id:
        logger.warning(
            "session_id is missing"
        )
        return None
 
    if not actor_id:
        logger.warning(
            "actor_id is missing"
        )
        return None
 
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
 
    memory_config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=actor_id,
        retrieval_config=retrieval_config,
    )
 
    logger.info(
        "Creating AgentCore Memory session manager "
        "memory_id=%s session_id=%s actor_id=%s",
        memory_id,
        session_id,
        actor_id,
    )
 
    return AgentCoreMemorySessionManager(
        memory_config,
        AWS_REGION,
    )
 
 