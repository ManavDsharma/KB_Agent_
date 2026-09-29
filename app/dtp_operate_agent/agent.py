import json
import os
import re
from typing import Any, Dict, Optional, Tuple
 
from strands import Agent
from strands.models import BedrockModel
 
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
 
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
 
from config import (
    BEDROCK_MODEL_ID,
    get_boto3_session,
    validate_config,
)
 
 
# ============================================================
# CONFIGURATION
# ============================================================
 
validate_config()
 
AWS_SESSION = get_boto3_session()
 
AWS_REGION = (
    AWS_SESSION.region_name
    or os.getenv("AWS_REGION")
    or "ap-south-1"
)
 
 
# ============================================================
# AGENTCORE MEMORY
# ============================================================
 
# AgentCore automatically injects:
#
# MEMORY_<MEMORY_NAME>_ID
#
# For:
#
# DtpOperateAgentMemory
#
# the runtime variable is:
#
# MEMORY_DTPOPERATEAGENTMEMORY_ID
#
# AgentCore Memory is separate from the short-term
# deterministic session context implemented below.
 
MEMORY_ENV_VAR = (
    "MEMORY_DTPOPERATEAGENTMEMORY_ID"
)
 
 
def get_memory_id() -> Optional[str]:
    """
    Get the AgentCore Memory ID.
 
    Local execution:
        Returns None if the variable is not present.
 
    AgentCore Runtime:
        Returns the injected Memory ID.
    """
 
    memory_id = os.getenv(
        MEMORY_ENV_VAR
    )
 
    if memory_id:
        return memory_id.strip()
 
    return None
 
 
def create_memory_session_manager(
    session_id: Optional[str],
    actor_id: Optional[str],
) -> Optional[
    AgentCoreMemorySessionManager
]:
    """
    Create the AgentCore Memory session manager.
 
    Memory is enabled only when:
        - session_id exists
        - actor_id exists
        - AgentCore has injected the Memory ID
 
    If memory initialization fails, the application continues
    without memory instead of failing the complete agent.
    """
 
    if not session_id:
        return None
 
    if not actor_id:
        return None
 
    memory_id = get_memory_id()
 
    if not memory_id:
        print(
            "[AgentCore Memory] Memory ID not configured."
        )
        return None
 
    try:
 
        retrieval_config = {
            f"/summaries/{actor_id}/{session_id}": (
                RetrievalConfig(
                    top_k=3,
                    relevance_score=0.5,
                )
            ),
 
            f"/users/{actor_id}/preferences": (
                RetrievalConfig(
                    top_k=3,
                    relevance_score=0.5,
                )
            ),
        }
 
        memory_config = AgentCoreMemoryConfig(
            memory_id=memory_id,
            session_id=session_id,
            actor_id=actor_id,
            retrieval_config=retrieval_config,
        )
 
        session_manager = (
            AgentCoreMemorySessionManager(
                memory_config,
                AWS_REGION,
            )
        )
 
        print(
            "[AgentCore Memory] Enabled | "
            f"session={session_id} | "
            f"actor={actor_id}"
        )
 
        return session_manager
 
    except Exception as exc:
 
        # Memory must never prevent the core
        # incident agent from starting.
 
        print(
            "[AgentCore Memory] "
            f"Initialization failed: {exc}"
        )
 
        return None
 
 
# ============================================================
# INCIDENT TOOLS
# ============================================================
 
from tools.incident_tools import (
    get_incident,
    list_incidents_by_date,
    list_incidents_by_date_range,
    search_incidents,
    list_sla_breaches,
    update_incident,
)
 
 
# ============================================================
# CATEGORIZATION TOOLS
# ============================================================
 
from tools.categorization_tools import (
    get_issue,
    get_issue_field,
    get_all_category_names,
    find_issue_by_name,
    search_issues,
    update_issue_field,
    update_issue,
)
 
 
# ============================================================
# ROSTER TOOLS
# ============================================================
 
from tools.roster_tools import (
    find_employee,
    search_employees,
    get_team_roster,
    get_team_day_availability,
    get_employee_day_availability,
    get_employee_month_availability,
    get_employee_week_availability,
    update_employee_day,
    update_employee_days,
    update_team_day,
)
 
 
# ============================================================
# KNOWLEDGE BASE TOOLS
# ============================================================
 
from tools.knowledge_tools import (
    search_knowledge_base,
    find_similar_incidents,
    find_similar_categorizations,
)
 
 
# ============================================================
# SESSION METRICS
# ============================================================
 
SESSION_METRICS = {
    "requests": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "direct_requests": 0,
    "llm_requests": 0,
}
 
 
# ============================================================
# USAGE EXTRACTION
# ============================================================
 
def extract_usage(
    result: Any,
) -> Dict[str, int]:
    """
    Extract token usage from a Strands result.
 
    Metrics failures must never break the request.
    """
 
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
 
    try:
 
        metrics = getattr(
            result,
            "metrics",
            None,
        )
 
        if metrics is None:
            return usage
 
        accumulated_usage = getattr(
            metrics,
            "accumulated_usage",
            None,
        )
 
        if accumulated_usage is None:
            return usage
 
        if isinstance(
            accumulated_usage,
            dict,
        ):
 
            usage["input_tokens"] = int(
                accumulated_usage.get(
                    "inputTokens",
                    accumulated_usage.get(
                        "input_tokens",
                        0,
                    ),
                )
                or 0
            )
 
            usage["output_tokens"] = int(
                accumulated_usage.get(
                    "outputTokens",
                    accumulated_usage.get(
                        "output_tokens",
                        0,
                    ),
                )
                or 0
            )
 
            usage["total_tokens"] = int(
                accumulated_usage.get(
                    "totalTokens",
                    accumulated_usage.get(
                        "total_tokens",
                        usage["input_tokens"]
                        + usage["output_tokens"],
                    ),
                )
                or 0
            )
 
            usage["cache_read_tokens"] = int(
                accumulated_usage.get(
                    "cacheReadInputTokens",
                    accumulated_usage.get(
                        "cache_read_input_tokens",
                        0,
                    ),
                )
                or 0
            )
 
            usage["cache_write_tokens"] = int(
                accumulated_usage.get(
                    "cacheWriteInputTokens",
                    accumulated_usage.get(
                        "cache_write_input_tokens",
                        0,
                    ),
                )
                or 0
            )
 
        else:
 
            def get_metric(
                *names,
            ):
 
                for name in names:
 
                    value = getattr(
                        accumulated_usage,
                        name,
                        None,
                    )
 
                    if value is not None:
                        return value
 
                return 0
 
            usage["input_tokens"] = int(
                get_metric(
                    "inputTokens",
                    "input_tokens",
                )
                or 0
            )
 
            usage["output_tokens"] = int(
                get_metric(
                    "outputTokens",
                    "output_tokens",
                )
                or 0
            )
 
            usage["total_tokens"] = int(
                get_metric(
                    "totalTokens",
                    "total_tokens",
                )
                or (
                    usage["input_tokens"]
                    + usage["output_tokens"]
                )
            )
 
            usage["cache_read_tokens"] = int(
                get_metric(
                    "cacheReadInputTokens",
                    "cache_read_input_tokens",
                )
                or 0
            )
 
            usage["cache_write_tokens"] = int(
                get_metric(
                    "cacheWriteInputTokens",
                    "cache_write_input_tokens",
                )
                or 0
            )
 
    except Exception:
        pass
 
    return usage
 
 
# ============================================================
# ENTITY TYPE
# ============================================================
 
def infer_entity_type(
    entity_id: Optional[str],
) -> Optional[str]:
    """
    Determine entity type from an identifier.
 
    Supported:
 
        Issue-9
        Issue9
 
        INC00001123
        INC-00001123
 
        REQ00012112
        REQ-00012112
    """
 
    if not entity_id:
        return None
 
    value = str(
        entity_id
    ).strip()
 
    if re.fullmatch(
        r"issue-?\d+",
        value,
        re.IGNORECASE,
    ):
        return "issue"
 
    if re.fullmatch(
        r"(?:inc|req)-?\d+",
        value,
        re.IGNORECASE,
    ):
        return "incident"
 
    return None
 
 
# ============================================================
# DETERMINISTIC ROUTER
# ============================================================
 
class DeterministicRouter:
    """
    Deterministic routing layer.
 
    Used before the LLM whenever the request can be
    reliably identified.
 
    Supported issue IDs:
 
        Issue-9
        Issue9
 
    Supported incident/request IDs:
 
        INC00001123
        INC-00001123
        REQ00012112
        REQ-00012112
 
    Also supports natural language forms:
 
        incident 1123
        incident-1123
        inc 1123
        inc-1123
        request 12112
        request-12112
        req 12112
        req-12112
    """
 
    # --------------------------------------------------------
    # ISSUE
    # --------------------------------------------------------
 
    ISSUE_PATTERN = re.compile(
        r"\bissue[\s_-]*(\d+)\b",
        re.IGNORECASE,
    )
 
    # --------------------------------------------------------
    # INCIDENT / REQUEST
    # --------------------------------------------------------
 
    # Full IDs.
    #
    # Examples:
    #
    # INC00001123
    # INC-00001123
    # REQ00012112
    # REQ-00012112
 
    INCIDENT_FULL_ID_PATTERN = re.compile(
        r"\b(?:INC|REQ)-?\d+\b",
        re.IGNORECASE,
    )
 
    # Natural-language forms.
    #
    # Examples:
    #
    # incident 1123
    # incident-1123
    # inc 1123
    # inc-1123
    # request 12112
    # request-12112
    # req 12112
    # req-12112
 
    INCIDENT_WORD_PATTERN = re.compile(
        r"\b(?:incident|inc|request|req)"
        r"[\s_-]*(\d+)\b",
        re.IGNORECASE,
    )
 
    # --------------------------------------------------------
    # ISSUE FIELDS
    # --------------------------------------------------------
 
    ISSUE_FIELD_ALIASES = {
        "business impact": "business_impact",
        "business_impact": "business_impact",
 
        "affected service": "affected_service",
        "affected_service": "affected_service",
 
        "issue description": "issue_description",
        "description": "issue_description",
 
        "sub_category_name": "sub_category_name",
        "sub_category": "sub_category_name",
        "sub category": "sub_category_name",
        "subcategory": "sub_category_name",
 
        "category_name": "category_name",
        "category": "category_name",
 
        "issue type": "issue_type",
        "issue_type": "issue_type",
 
        "impact": "impact",
        "urgency": "urgency",
 
        "issue": "issue",
 
        "team_id": "team_id",
        "team id": "team_id",
        "team": "team_id",
 
        "active": "active",
    }
 
    # --------------------------------------------------------
    # INCIDENT FIELDS
    # --------------------------------------------------------
 
    INCIDENT_FIELD_ALIASES = {
        "short description": "short_description",
        "short_description": "short_description",
 
        "sub category": "sub_category",
        "sub_category": "sub_category",
 
        "assignment sla": "assignment_sla",
        "resolution sla": "resolution_sla",
 
        "working notes": "working_notes",
 
        "assigned to": "assigned_to",
        "assigned_to": "assigned_to",
 
        "description": "description",
 
        "category": "category",
 
        "urgency": "urgency",
        "priority": "priority",
 
        "team id": "team_id",
        "team_id": "team_id",
        "team": "team_id",
 
        "state": "state",
    }
 
    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------
 
    UPDATE_KEYWORDS = (
        "update",
        "change",
        "modify",
        "set",
        "edit",
    )
 
    # --------------------------------------------------------
    # CATEGORY LIST
    # --------------------------------------------------------
 
    CATEGORY_LIST_KEYWORDS = (
        "all categories",
        "category names",
        "list categories",
        "categories available",
        "available categories",
    )
 
    # ========================================================
    # ISSUE ID
    # ========================================================
 
    @classmethod
    def extract_issue_id(
        cls,
        text: str,
    ) -> Optional[str]:
 
        match = cls.ISSUE_PATTERN.search(
            text
        )
 
        if not match:
            return None
 
        return (
            f"Issue-{match.group(1)}"
        )
 
    # ========================================================
    # INCIDENT / REQUEST ID
    # ========================================================
 
    @classmethod
    def extract_incident_id(
        cls,
        text: str,
    ) -> Optional[str]:
        """
        Extract an incident/request ID.
 
        IMPORTANT:
        The actual ID format is preserved.
 
        Examples:
 
            INC00001123 -> INC00001123
            INC-00001123 -> INC-00001123
 
            REQ00012112 -> REQ00012112
            REQ-00012112 -> REQ-00012112
 
        Natural language:
 
            incident 1123 -> INC1123
            inc-1123 -> INC-1123
 
            request 12112 -> REQ12112
            req-12112 -> REQ-12112
        """
 
        # ----------------------------------------------------
        # First check for a full explicit ID.
        # ----------------------------------------------------
 
        full_match = (
            cls.INCIDENT_FULL_ID_PATTERN.search(
                text
            )
        )
 
        if full_match:
 
            value = full_match.group(
                0
            )
 
            # Preserve the exact identifier format.
            #
            # INC00001123 stays INC00001123.
            # INC-00001123 stays INC-00001123.
            # REQ00012112 stays REQ00012112.
            # REQ-00012112 stays REQ-00012112.
 
            return value.upper()
 
        # ----------------------------------------------------
        # Then support natural-language forms.
        # ----------------------------------------------------
 
        word_match = (
            cls.INCIDENT_WORD_PATTERN.search(
                text
            )
        )
 
        if not word_match:
            return None
 
        matched_text = word_match.group(
            0
        )
 
        number = word_match.group(
            1
        )
 
        prefix_match = re.match(
            r"(incident|inc|request|req)",
            matched_text,
            re.IGNORECASE,
        )
 
        if not prefix_match:
            return None
 
        prefix = (
            prefix_match.group(1).lower()
        )
 
        # Request / REQ
        if prefix in (
            "request",
            "req",
        ):
 
            # Preserve whether the user typed
            # a hyphen.
            if "-" in matched_text:
                return f"REQ-{number}"
 
            return f"REQ{number}"
 
        # Incident / INC
        if "-" in matched_text:
            return f"INC-{number}"
 
        return f"INC{number}"
 
    # ========================================================
    # ISSUE FIELD
    # ========================================================
 
    @classmethod
    def detect_issue_field(
        cls,
        text: str,
    ) -> Optional[str]:
 
        lower = text.lower()
 
        aliases = sorted(
            cls.ISSUE_FIELD_ALIASES.items(),
            key=lambda item: len(
                item[0]
            ),
            reverse=True,
        )
 
        for alias, field in aliases:
 
            if alias in lower:
                return field
 
        return None
 
    # ========================================================
    # INCIDENT FIELD
    # ========================================================
 
    @classmethod
    def detect_incident_field(
        cls,
        text: str,
    ) -> Optional[str]:
 
        lower = text.lower()
 
        aliases = sorted(
            cls.INCIDENT_FIELD_ALIASES.items(),
            key=lambda item: len(
                item[0]
            ),
            reverse=True,
        )
 
        for alias, field in aliases:
 
            if alias in lower:
                return field
 
        return None
 
    # ========================================================
    # UPDATE REQUEST
    # ========================================================
 
    @classmethod
    def is_update_request(
        cls,
        text: str,
    ) -> bool:
 
        lower = text.lower()
 
        return any(
            keyword in lower
            for keyword in cls.UPDATE_KEYWORDS
        )
 
    # ========================================================
    # CATEGORY LIST REQUEST
    # ========================================================
 
    @classmethod
    def is_category_list_request(
        cls,
        text: str,
    ) -> bool:
 
        lower = text.lower()
 
        return any(
            keyword in lower
            for keyword in cls.CATEGORY_LIST_KEYWORDS
        )
 
    # ========================================================
    # ROUTE
    # ========================================================
 
    @classmethod
    def route(
        cls,
        text: str,
    ) -> Dict[str, Any]:
 
        # ----------------------------------------------------
        # Category list
        # ----------------------------------------------------
 
        if cls.is_category_list_request(
            text
        ):
 
            return {
                "route": "category_list",
                "entity": None,
                "entity_type": None,
                "field": None,
                "operation": "read",
            }
 
        # ----------------------------------------------------
        # Entity extraction
        # ----------------------------------------------------
 
        issue_id = (
            cls.extract_issue_id(
                text
            )
        )
 
        incident_id = (
            cls.extract_incident_id(
                text
            )
        )
 
        is_update = (
            cls.is_update_request(
                text
            )
        )
 
        # ----------------------------------------------------
        # ISSUE
        # ----------------------------------------------------
 
        if issue_id:
 
            field = (
                cls.detect_issue_field(
                    text
                )
            )
 
            if is_update and field:
 
                return {
                    "route": "issue_update_field",
                    "entity": issue_id,
                    "entity_type": "issue",
                    "field": field,
                    "operation": "update",
                }
 
            if field:
 
                return {
                    "route": "issue_field",
                    "entity": issue_id,
                    "entity_type": "issue",
                    "field": field,
                    "operation": "read",
                }
 
            if is_update:
 
                return {
                    "route": "issue_update",
                    "entity": issue_id,
                    "entity_type": "issue",
                    "field": None,
                    "operation": "update",
                }
 
            return {
                "route": "issue",
                "entity": issue_id,
                "entity_type": "issue",
                "field": None,
                "operation": "read",
            }
 
        # ----------------------------------------------------
        # INCIDENT / REQUEST
        # ----------------------------------------------------
 
        if incident_id:
 
            field = (
                cls.detect_incident_field(
                    text
                )
            )
 
            if field:
 
                return {
                    "route": "incident_field",
                    "entity": incident_id,
                    "entity_type": "incident",
                    "field": field,
                    "operation": (
                        "update"
                        if is_update
                        else "read"
                    ),
                }
 
            if is_update:
 
                return {
                    "route": "incident_update",
                    "entity": incident_id,
                    "entity_type": "incident",
                    "field": None,
                    "operation": "update",
                }
 
            return {
                "route": "incident",
                "entity": incident_id,
                "entity_type": "incident",
                "field": None,
                "operation": "read",
            }
 
        # ----------------------------------------------------
        # SUPERVISOR
        # ----------------------------------------------------
 
        return {
            "route": "supervisor",
            "entity": None,
            "entity_type": None,
            "field": None,
            "operation": "unknown",
        }
 
 
# ============================================================
# SHORT-TERM SESSION CONTEXT
# ============================================================
 
def get_last_entity(
    session_context: Optional[
        Dict[str, Any]
    ],
) -> Tuple[
    Optional[str],
    Optional[str],
]:
    """
    Return the last known entity type and ID.
    """
 
    if not session_context:
        return None, None
 
    entity_type = (
        session_context.get(
            "last_entity_type"
        )
    )
 
    entity_id = (
        session_context.get(
            "last_entity_id"
        )
    )
 
    if not entity_id:
        return None, None
 
    if not entity_type:
        entity_type = infer_entity_type(
            entity_id
        )
 
    return (
        entity_type,
        entity_id,
    )
 
 
def resolve_contextual_reference(
    user_input: str,
    session_context: Optional[
        Dict[str, Any]
    ],
) -> str:
    """
    Resolve immediate conversational references
    to the last known Issue or Incident.
 
    Examples:
 
        Show me Issue-9
 
        What is its impact?
 
        -> What is Issue-9 impact?
 
 
        Show me INC00001123
 
        What is its priority?
 
        -> What is INC00001123 priority?
 
 
        Show me REQ-00012112
 
        What is its state?
 
        -> What is REQ-00012112 state?
    """
 
    if not user_input:
        return user_input
 
    entity_type, entity_id = (
        get_last_entity(
            session_context
        )
    )
 
    if not entity_id:
        return user_input
 
    text = user_input.strip()
 
    # --------------------------------------------------------
    # If the user explicitly supplied an entity, don't replace
    # anything with the previous entity.
    # --------------------------------------------------------
 
    explicit_issue = (
        DeterministicRouter.extract_issue_id(
            text
        )
    )
 
    explicit_incident = (
        DeterministicRouter.extract_incident_id(
            text
        )
    )
 
    if explicit_issue or explicit_incident:
        return text
 
    resolved = text
 
    # --------------------------------------------------------
    # its
    #
    # "What is its impact?"
    # ->
    # "What is Issue-9 impact?"
    # --------------------------------------------------------
 
    resolved = re.sub(
        r"\bits\b",
        entity_id,
        resolved,
        flags=re.IGNORECASE,
    )
 
    # --------------------------------------------------------
    # it
    #
    # "Update it"
    # ->
    # "Update Issue-9"
    # --------------------------------------------------------
 
    resolved = re.sub(
        r"\bit\b",
        entity_id,
        resolved,
        flags=re.IGNORECASE,
    )
 
    # --------------------------------------------------------
    # that issue
    # --------------------------------------------------------
 
    if entity_type == "issue":
 
        resolved = re.sub(
            r"\bthat\s+issue\b",
            entity_id,
            resolved,
            flags=re.IGNORECASE,
        )
 
        resolved = re.sub(
            r"\bthis\s+issue\b",
            entity_id,
            resolved,
            flags=re.IGNORECASE,
        )
 
        resolved = re.sub(
            r"\bthe\s+issue\b",
            entity_id,
            resolved,
            flags=re.IGNORECASE,
        )
 
    # --------------------------------------------------------
    # that incident
    # --------------------------------------------------------
 
    if entity_type == "incident":
 
        resolved = re.sub(
            r"\bthat\s+incident\b",
            entity_id,
            resolved,
            flags=re.IGNORECASE,
        )
 
        resolved = re.sub(
            r"\bthis\s+incident\b",
            entity_id,
            resolved,
            flags=re.IGNORECASE,
        )
 
        resolved = re.sub(
            r"\bthe\s+incident\b",
            entity_id,
            resolved,
            flags=re.IGNORECASE,
        )
 
        # Ticket terminology
        resolved = re.sub(
            r"\bthat\s+ticket\b",
            entity_id,
            resolved,
            flags=re.IGNORECASE,
        )
 
        resolved = re.sub(
            r"\bthis\s+ticket\b",
            entity_id,
            resolved,
            flags=re.IGNORECASE,
        )
 
        resolved = re.sub(
            r"\bthe\s+ticket\b",
            entity_id,
            resolved,
            flags=re.IGNORECASE,
        )
 
    # --------------------------------------------------------
    # Log context resolution.
    # This is backend logging only.
    # --------------------------------------------------------
 
    if resolved != text:
 
        print(
            "[Context Resolver] "
            f"'{text}' -> '{resolved}' "
            f"| entity={entity_id}"
        )
 
    return resolved
 
 
def update_session_context(
    session_context: Optional[
        Dict[str, Any]
    ],
    metadata: Optional[
        Dict[str, Any]
    ],
) -> None:
    """
    Update short-term runtime context after a successful
    entity-related request.
 
    Example:
 
        entity = Issue-9
 
    stores:
 
        last_entity_type = issue
        last_entity_id   = Issue-9
 
    Example:
 
        entity = INC00001123
 
    stores:
 
        last_entity_type = incident
        last_entity_id   = INC00001123
    """
 
    if session_context is None:
        return
 
    if not metadata:
        return
 
    entity = metadata.get(
        "entity"
    )
 
    if not entity:
        return
 
    entity_type = (
        metadata.get(
            "entity_type"
        )
        or infer_entity_type(
            entity
        )
    )
 
    if not entity_type:
        return
 
    session_context[
        "last_entity_type"
    ] = entity_type
 
    session_context[
        "last_entity_id"
    ] = entity
 
    session_context[
        "last_field"
    ] = metadata.get(
        "field"
    )
 
    session_context[
        "last_route"
    ] = metadata.get(
        "route"
    )
 
    print(
        "[Session Context] Updated | "
        f"type={entity_type} | "
        f"entity={entity} | "
        f"field={metadata.get('field')}"
    )
 
 
# ============================================================
# MODEL
# ============================================================
 
def create_model() -> BedrockModel:
 
    return BedrockModel(
        model_id=BEDROCK_MODEL_ID,
        boto_session=AWS_SESSION,
        temperature=0.0,
        use_native_token_count=True,
    )
 
 
# ============================================================
# SUPERVISOR SYSTEM PROMPT
# ============================================================
 
SUPERVISOR_SYSTEM_PROMPT = """
You are the Enterprise Incident Management Supervisor.
 
Use the correct specialist:
 
- Incident: incidents, tickets, SLA and incident updates.
- Categorization: issues, categories and categorization updates.
- Roster: employees, teams and availability.
- Knowledge: semantic knowledge and similar records.
 
Rules:
- Use DynamoDB for structured facts.
- Use Knowledge Base for semantic retrieval.
- Never invent data.
- Use update tools for updates.
- If data is not found, say so.
- Return only the useful answer.
- Do not expose reasoning, tool calls or internal details.
"""
 
 
# ============================================================
# INCIDENT SPECIALIST
# ============================================================
 
def build_incident_specialist():
 
    return Agent(
        model=create_model(),
        name="IncidentSpecialist",
        system_prompt="""
You handle incident and ticket operations.
 
Use DynamoDB tools for exact data.
 
Capabilities:
- Get incident
- Search incidents
- List incidents by date
- List incidents by date range
- List SLA breaches
- Update incident
 
Rules:
- Never invent data.
- Use tools for exact information.
- Clearly state when no record exists.
- Return concise answers.
- Use update tools for updates.
""",
        tools=[
            get_incident,
            search_incidents,
            list_incidents_by_date,
            list_incidents_by_date_range,
            list_sla_breaches,
            update_incident,
        ],
    )
 
 
# ============================================================
# CATEGORIZATION SPECIALIST
# ============================================================
 
def build_categorization_specialist():
 
    return Agent(
        model=create_model(),
        name="CategorizationSpecialist",
        system_prompt="""
You handle issue and categorization operations.
 
Use DynamoDB tools for exact data.
 
Capabilities:
- Get issue
- Get issue field
- Get category names
- Find issue
- Search issues
- Update issue field
- Update issue
 
Rules:
- Never invent data.
- Use tools for exact information.
- Clearly state when no record exists.
- Return concise answers.
- Use update tools for updates.
""",
        tools=[
            get_issue,
            get_issue_field,
            get_all_category_names,
            find_issue_by_name,
            search_issues,
            update_issue_field,
            update_issue,
        ],
    )
 
 
# ============================================================
# ROSTER SPECIALIST
# ============================================================
 
def build_roster_specialist():
 
    return Agent(
        model=create_model(),
        name="RosterSpecialist",
        system_prompt="""
You handle employee, team and availability operations.
 
Use DynamoDB tools for exact data.
 
Capabilities:
- Find employee
- Search employees
- Get team roster
- Get team day availability
- Get employee day availability
- Get employee month availability
- Get employee week availability
- Update employee day
- Update employee days
- Update team day
 
Rules:
- Never invent employee or availability data.
- Use DynamoDB tools.
- Clearly state when no record exists.
- Return concise answers.
""",
        tools=[
            find_employee,
            search_employees,
            get_team_roster,
            get_team_day_availability,
            get_employee_day_availability,
            get_employee_month_availability,
            get_employee_week_availability,
            update_employee_day,
            update_employee_days,
            update_team_day,
        ],
    )
 
 
# ============================================================
# KNOWLEDGE SPECIALIST
# ============================================================
 
def build_knowledge_specialist():
 
    return Agent(
        model=create_model(),
        name="KnowledgeSpecialist",
        system_prompt="""
You handle semantic knowledge retrieval.
 
Use the Bedrock Knowledge Base.
 
Capabilities:
- Search knowledge base
- Find similar incidents
- Find similar categorizations
 
Rules:
- Use retrieved content.
- Do not invent information.
- Distinguish retrieved information from assumptions.
- Return concise answers.
""",
        tools=[
            search_knowledge_base,
            find_similar_incidents,
            find_similar_categorizations,
        ],
    )
 
  
# ============================================================
# SUPERVISOR
# ============================================================
 
def build_agent(
    session_id: Optional[str] = None,
    actor_id: Optional[str] = None,
):
 
    incident_specialist = (
        build_incident_specialist()
    )
 
    categorization_specialist = (
        build_categorization_specialist()
    )
 
    roster_specialist = (
        build_roster_specialist()
    )
 
    knowledge_specialist = (
        build_knowledge_specialist()
    )
 
    memory_session_manager = (
        create_memory_session_manager(
            session_id=session_id,
            actor_id=actor_id,
        )
    )
 
    supervisor = Agent(
        model=create_model(),
        name="EnterpriseIncidentSupervisor",
        system_prompt=SUPERVISOR_SYSTEM_PROMPT,
        tools=[
            incident_specialist,
            categorization_specialist,
            roster_specialist,
            knowledge_specialist,
        ],
        session_manager=memory_session_manager,
    )
 
    return supervisor
 
 
# ============================================================
# RESPONSE EXTRACTION
# ============================================================
 
def get_final_response(
    result: Any,
) -> str:
 
    if result is None:
        return ""
 
    output = getattr(
        result,
        "output",
        None,
    )
 
    if output is not None:
 
        if isinstance(
            output,
            str,
        ):
            return output.strip()
 
        if isinstance(
            output,
            dict,
        ):
 
            if "text" in output:
 
                return str(
                    output["text"]
                ).strip()
 
            if "content" in output:
 
                content = output[
                    "content"
                ]
 
                if isinstance(
                    content,
                    list,
                ):
 
                    texts = []
 
                    for item in content:
 
                        if isinstance(
                            item,
                            dict,
                        ):
 
                            if "text" in item:
 
                                texts.append(
                                    str(
                                        item["text"]
                                    )
                                )
 
                    if texts:
 
                        return "\n".join(
                            texts
                        ).strip()
 
        return str(
            output
        ).strip()
 
    structured_output = getattr(
        result,
        "structured_output",
        None,
    )
 
    if structured_output is not None:
 
        if isinstance(
            structured_output,
            str,
        ):
            return structured_output.strip()
 
        return json.dumps(
            structured_output,
            ensure_ascii=False,
            default=str,
        )
 
    message = getattr(
        result,
        "message",
        None,
    )
 
    if message is not None:
 
        if isinstance(
            message,
            str,
        ):
            return message.strip()
 
        if isinstance(
            message,
            dict,
        ):
 
            content = message.get(
                "content"
            )
 
            if isinstance(
                content,
                list,
            ):
 
                texts = []
 
                for item in content:
 
                    if isinstance(
                        item,
                        dict,
                    ):
 
                        if "text" in item:
 
                            texts.append(
                                str(
                                    item["text"]
                                )
                            )
 
                if texts:
 
                    return "\n".join(
                        texts
                    ).strip()
 
            if content is not None:
 
                return str(
                    content
                ).strip()
 
        return str(
            message
        ).strip()
 
    return str(
        result
    ).strip()
 
 
# ============================================================
# MARKDOWN FORMATTING
# ============================================================
 
def format_dict_as_markdown(
    data: Dict[str, Any],
) -> str:
 
    if not data:
        return "No data found."
 
    lines = [
        "| Field | Value |",
        "|---|---|",
    ]
 
    for key, value in data.items():
 
        if isinstance(
            value,
            (dict, list),
        ):
 
            value = json.dumps(
                value,
                ensure_ascii=False,
                default=str,
            )
 
        value = str(
            value
        )
 
        value = (
            value
            .replace(
                "|",
                "\\|",
            )
            .replace(
                "\n",
                "<br>",
            )
        )
 
        lines.append(
            f"| {key} | {value} |"
        )
 
    return "\n".join(
        lines
    )
 
 
def format_result_as_markdown(
    result: Any,
) -> str:
 
    if result is None:
        return "No data found."
 
    if isinstance(
        result,
        dict,
    ):
 
        return format_dict_as_markdown(
            result
        )
 
    if isinstance(
        result,
        list,
    ):
 
        if not result:
            return "No records found."
 
        # ----------------------------------------------------
        # List of dictionaries -> Markdown table
        # ----------------------------------------------------
 
        if all(
            isinstance(
                item,
                dict,
            )
            for item in result
        ):
 
            keys = []
 
            for item in result:
 
                for key in item.keys():
 
                    if key not in keys:
                        keys.append(
                            key
                        )
 
            if not keys:
                return "No records found."
 
            header = (
                "| "
                + " | ".join(
                    str(key)
                    for key in keys
                )
                + " |"
            )
 
            separator = (
                "| "
                + " | ".join(
                    "---"
                    for _ in keys
                )
                + " |"
            )
 
            rows = []
 
            for item in result:
 
                values = []
 
                for key in keys:
 
                    value = item.get(
                        key,
                        "",
                    )
 
                    if isinstance(
                        value,
                        (dict, list),
                    ):
 
                        value = json.dumps(
                            value,
                            ensure_ascii=False,
                            default=str,
                        )
 
                    value = str(
                        value
                    )
 
                    value = (
                        value
                        .replace(
                            "|",
                            "\\|",
                        )
                        .replace(
                            "\n",
                            "<br>",
                        )
                    )
 
                    values.append(
                        value
                    )
 
                rows.append(
                    "| "
                    + " | ".join(
                        values
                    )
                    + " |"
                )
 
            return "\n".join(
                [
                    header,
                    separator,
                    *rows,
                ]
            )
 
        # ----------------------------------------------------
        # List of simple values -> bullets
        # ----------------------------------------------------
 
        return "\n".join(
            f"- {item}"
            for item in result
        )
 
    return str(
        result
    )
 
 
def format_response(
    response: str,
) -> str:
 
    if not response:
        return "No response returned."
 
    return response.strip()
 
 
# ============================================================
# DIRECT ROUTE EXECUTION
# ============================================================
 
def execute_direct_route(
    route: Dict[str, Any],
    user_input: str,
) -> Tuple[
    Optional[str],
    Optional[Dict[str, Any]],
]:
 
    route_name = route.get(
        "route"
    )
 
    entity = route.get(
        "entity"
    )
 
    field = route.get(
        "field"
    )
 
    # ========================================================
    # CATEGORY LIST
    # ========================================================
 
    if route_name == "category_list":
 
        result = get_all_category_names()
 
        return (
            format_result_as_markdown(
                result
            ),
            {
                "route": route_name,
                "direct": True,
                "entity": None,
                "entity_type": None,
                "field": None,
            },
        )
 
    # ========================================================
    # ISSUE
    # ========================================================
 
    if route_name == "issue":
 
        result = get_issue(
            entity
        )
 
        return (
            format_result_as_markdown(
                result
            ),
            {
                "route": route_name,
                "direct": True,
                "entity": entity,
                "entity_type": "issue",
                "field": None,
            },
        )
 
    # ========================================================
    # ISSUE FIELD
    # ========================================================
 
    if route_name == "issue_field":
 
        result = get_issue_field(
            entity,
            field,
        )
 
        return (
            format_result_as_markdown(
                result
            ),
            {
                "route": route_name,
                "direct": True,
                "entity": entity,
                "entity_type": "issue",
                "field": field,
            },
        )
 
    # ========================================================
    # INCIDENT
    # ========================================================
 
    if route_name == "incident":
 
        result = get_incident(
            entity
        )
 
        return (
            format_result_as_markdown(
                result
            ),
            {
                "route": route_name,
                "direct": True,
                "entity": entity,
                "entity_type": "incident",
                "field": None,
            },
        )
 
    # ========================================================
    # INCIDENT FIELD
    # ========================================================
 
    if route_name == "incident_field":
 
        # Updates must go through the supervisor.
        if route.get(
            "operation"
        ) != "read":
 
            return None, None
 
        result = get_incident(
            entity
        )
 
        # ----------------------------------------------------
        # Exact field
        # ----------------------------------------------------
 
        if isinstance(
            result,
            dict,
        ):
 
            if field in result:
 
                return (
                    format_result_as_markdown(
                        {
                            field: result.get(
                                field
                            )
                        }
                    ),
                    {
                        "route": route_name,
                        "direct": True,
                        "entity": entity,
                        "entity_type": "incident",
                        "field": field,
                    },
                )
 
            # ------------------------------------------------
            # Case-insensitive field matching
            # ------------------------------------------------
 
            for key, value in result.items():
 
                if (
                    str(key).lower()
                    == str(field).lower()
                ):
 
                    return (
                        format_result_as_markdown(
                            {
                                key: value
                            }
                        ),
                        {
                            "route": route_name,
                            "direct": True,
                            "entity": entity,
                            "entity_type": "incident",
                            "field": field,
                        },
                    )
 
            return (
                f"Field `{field}` was not found "
                f"for `{entity}`.",
                {
                    "route": route_name,
                    "direct": True,
                    "entity": entity,
                    "entity_type": "incident",
                    "field": field,
                },
            )
 
        return (
            format_result_as_markdown(
                result
            ),
            {
                "route": route_name,
                "direct": True,
                "entity": entity,
                "entity_type": "incident",
                "field": field,
            },
        )
 
    # ========================================================
    # UPDATE ROUTES
    # ========================================================
 
    # Updates intentionally go to the supervisor.
    #
    # Examples:
    #
    # Update Issue-9 impact to High
    #
    # Update INC00001123 priority to High
    #
    # Update REQ-00012112 state to Closed
    #
    # The LLM/specialist interprets the new value and calls
    # the appropriate update tool.
 
    return None, None
 
 
# ============================================================
# PROCESS USER QUERY
# ============================================================
 
def process_user_query(
    user_input: str,
    supervisor,
    session_context: Optional[
        Dict[str, Any]
    ] = None,
) -> Tuple[
    str,
    Dict[str, Any],
]:
 
    if not user_input or not user_input.strip():
 
        return (
            "Please provide a request.",
            {},
        )
 
    SESSION_METRICS[
        "requests"
    ] += 1
 
    if session_context is None:
        session_context = {}
 
    # ========================================================
    # RESOLVE CONTEXT
    # ========================================================
 
    effective_input = (
        resolve_contextual_reference(
            user_input,
            session_context,
        )
    )
 
    # ========================================================
    # DETERMINE ROUTE
    # ========================================================
 
    route = DeterministicRouter.route(
        effective_input
    )
 
    route_name = route.get(
        "route"
    )
 
    # ========================================================
    # DIRECT ROUTE
    # ========================================================
 
    try:
 
        direct_response, direct_metadata = (
            execute_direct_route(
                route,
                effective_input,
            )
        )
 
        if direct_response is not None:
 
            SESSION_METRICS[
                "direct_requests"
            ] += 1
 
            metadata = {
                "route": route_name,
                "direct": True,
                "entity": (
                    direct_metadata.get(
                        "entity"
                    )
                    if direct_metadata
                    else route.get(
                        "entity"
                    )
                ),
                "entity_type": (
                    direct_metadata.get(
                        "entity_type"
                    )
                    if direct_metadata
                    else route.get(
                        "entity_type"
                    )
                ),
                "field": (
                    direct_metadata.get(
                        "field"
                    )
                    if direct_metadata
                    else route.get(
                        "field"
                    )
                ),
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                },
            }
 
            update_session_context(
                session_context,
                metadata,
            )
 
            return (
                format_response(
                    direct_response
                ),
                metadata,
            )
 
    except Exception as exc:
 
        print(
            "[Direct Route] Failed: "
            f"{exc}"
        )
 
    # ========================================================
    # SUPERVISOR / LLM
    # ========================================================
 
    SESSION_METRICS[
        "llm_requests"
    ] += 1
 
    try:
 
        # IMPORTANT:
        #
        # Pass the context-resolved input to the supervisor.
        #
        # Example:
        #
        # User:
        #     Update its impact to Low
        #
        # Effective:
        #     Update Issue-9 impact to Low
        #
        # Therefore the supervisor receives the actual entity.
 
        result = supervisor(
            effective_input
        )
 
        usage = extract_usage(
            result
        )
 
        SESSION_METRICS[
            "input_tokens"
        ] += usage[
            "input_tokens"
        ]
 
        SESSION_METRICS[
            "output_tokens"
        ] += usage[
            "output_tokens"
        ]
 
        SESSION_METRICS[
            "total_tokens"
        ] += usage[
            "total_tokens"
        ]
 
        SESSION_METRICS[
            "cache_read_tokens"
        ] += usage[
            "cache_read_tokens"
        ]
 
        SESSION_METRICS[
            "cache_write_tokens"
        ] += usage[
            "cache_write_tokens"
        ]
 
        response = get_final_response(
            result
        )
 
        metadata = {
            "route": route_name,
            "direct": False,
            "entity": route.get(
                "entity"
            ),
            "entity_type": route.get(
                "entity_type"
            ),
            "field": route.get(
                "field"
            ),
            "usage": usage,
        }
 
        update_session_context(
            session_context,
            metadata,
        )
 
        return (
            format_response(
                response
            ),
            metadata,
        )
 
    except Exception as exc:
 
        print(
            "[Supervisor] Agent execution failed: "
            f"{exc}"
        )
 
        return (
            "I was unable to process the request. "
            "Please try again.",
            {
                "route": route_name,
                "direct": False,
                "entity": route.get(
                    "entity"
                ),
                "entity_type": route.get(
                    "entity_type"
                ),
                "field": route.get(
                    "field"
                ),
                "error": str(exc),
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                },
            },
        )
 
 
# ============================================================
# SESSION METRICS
# ============================================================
 
def print_session_metrics():
 
    print(
        "\nSession Metrics"
    )
 
    print(
        "-" * 60
    )
 
    for key, value in SESSION_METRICS.items():
 
        print(
            f"{key:25}: {value}"
        )
 
 
# ============================================================
# REQUEST METRICS
# ============================================================
 
def print_request_metrics(
    metadata: Dict[str, Any],
):
 
    print(
        "\nRequest Metrics"
    )
 
    print(
        "-" * 60
    )
 
    print(
        f"{'route':25}: "
        f"{metadata.get('route')}"
    )
 
    print(
        f"{'direct':25}: "
        f"{metadata.get('direct')}"
    )
 
    print(
        f"{'entity':25}: "
        f"{metadata.get('entity')}"
    )
 
    print(
        f"{'entity_type':25}: "
        f"{metadata.get('entity_type')}"
    )
 
    print(
        f"{'field':25}: "
        f"{metadata.get('field')}"
    )
 
    usage = metadata.get(
        "usage",
        {},
    )
 
    print(
        f"{'input_tokens':25}: "
        f"{usage.get('input_tokens', 0)}"
    )
 
    print(
        f"{'output_tokens':25}: "
        f"{usage.get('output_tokens', 0)}"
    )
 
    print(
        f"{'total_tokens':25}: "
        f"{usage.get('total_tokens', 0)}"
    )
 
    print(
        f"{'cache_read_tokens':25}: "
        f"{usage.get('cache_read_tokens', 0)}"
    )
 
    print(
        f"{'cache_write_tokens':25}: "
        f"{usage.get('cache_write_tokens', 0)}"
    )
 