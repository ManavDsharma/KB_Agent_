import os
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from dotenv import load_dotenv


# ============================================================
# BASE DIRECTORY
# ============================================================

BASE_DIR = Path(__file__).resolve().parent


# ============================================================
# LOCAL ENVIRONMENT
# ============================================================

# Anything other than "local" is treated as a deployed environment: a
# request without a session is rejected and no .env file is read. The
# local tools in local_dev/ set ENVIRONMENT=local themselves.
ENVIRONMENT = os.getenv(
    "ENVIRONMENT",
    "production",
).lower()


# Load .env only when running locally.
#
# In AgentCore, environment variables are provided by the
# runtime configuration / deployment configuration and the
# AWS execution role provides credentials. The local .env lives in
# local_dev/ (outside this deployable folder) so it can never be
# packaged with the agent.
if ENVIRONMENT in ("local", "ec2"):

    for ENV_FILE in (
        BASE_DIR / ".env",
        BASE_DIR.parent.parent / "local_dev" / ".env",
    ):

        if ENV_FILE.exists():
            load_dotenv(
                dotenv_path=ENV_FILE,
                override=False,
            )


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


# ============================================================
# AWS CONFIGURATION
# ============================================================

AWS_REGION = os.getenv(
    "AWS_REGION",
    "ap-south-1",
)

# Optional local credentials.
#
# DO NOT configure these for AgentCore production deployment.
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")

AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN")


# ============================================================
# AGENTCORE MEMORY
# ============================================================

# AgentCore injects MEMORY_<MEMORY_NAME>_ID into the runtime.
# For the memory "DtpOperateAgentMemory" that is
# MEMORY_DTPOPERATEAGENTMEMORY_ID.
MEMORY_ID_ENV_NAMES = (
    "MEMORY_DTPOPERATEAGENTMEMORY_ID",
    "MEMORY_DtpOperateAgentMemory_ID",
    "MEMORY_DTP_OPERATE_AGENT_MEMORY_ID",
)


def get_memory_id():
    for name in MEMORY_ID_ENV_NAMES:
        value = os.getenv(name)

        if value and value.strip():
            return value.strip()

    return None


# Session events (short-term memory) are always persisted when a
# memory is configured. Long-term memory retrieval injects extra
# tokens into every prompt, so it is opt-in.
ENABLE_LTM_RETRIEVAL = _bool("ENABLE_LTM_RETRIEVAL", False)


# ============================================================
# BEDROCK CONFIGURATION
# ============================================================

BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID")

KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID")

BEDROCK_READ_TIMEOUT = _int("BEDROCK_READ_TIMEOUT", 60)

BEDROCK_CONNECT_TIMEOUT = _int("BEDROCK_CONNECT_TIMEOUT", 5)

# Upper bound for one model response (a cap, not a cost: only tokens actually
# written are billed). Too low and a long table gets cut off.
LLM_MAX_TOKENS = _int("LLM_MAX_TOKENS", 4000)

# "auto" enables prompt/tool caching for Claude models only.
PROMPT_CACHING = os.getenv("PROMPT_CACHING", "auto").strip().lower()

# Messages kept in the LLM conversation window. References such as
# "it" are resolved from the structured session state, so a small
# window is enough.
CONVERSATION_WINDOW_SIZE = _int("CONVERSATION_WINDOW_SIZE", 10)

# Agent loop control.
MAX_TOOL_CALLS_PER_REQUEST = _int("MAX_TOOL_CALLS_PER_REQUEST", 8)


# ============================================================
# PLANNER (multi-part write requests)
# ============================================================

# A write request the fast path cannot fully understand (several fields,
# several records, several days) is turned into a plan by one small model
# call with no tools. A cheaper model can be set here once its accuracy has
# been measured with local_dev/dev/evaluate.py; by default the main model is used.
PLANNER_MODEL_ID = os.getenv("PLANNER_MODEL_ID") or BEDROCK_MODEL_ID

PLANNER_MAX_TOKENS = _int("PLANNER_MAX_TOKENS", 700)

# Longest message sent to the planner, and the most operations one plan may hold.
PLANNER_MAX_WORDS = _int("PLANNER_MAX_WORDS", 60)

PLAN_MAX_OPERATIONS = _int("PLAN_MAX_OPERATIONS", 20)


# ============================================================
# KNOWLEDGE BASE ROUTING
# ============================================================

# One Bedrock Knowledge Base with three data sources. Providing the
# data-source IDs lets the agent search only the relevant source.
# When an ID is not configured that source is searched unfiltered.
KB_DATA_SOURCE_IDS = {
    "incident": os.getenv("KB_DATA_SOURCE_INCIDENT_ID") or os.getenv("INCIDENT_KB_DATA_SOURCE_ID"),
    "roster": os.getenv("KB_DATA_SOURCE_ROSTER_ID") or os.getenv("ROSTER_KB_DATA_SOURCE_ID"),
    "category": os.getenv("KB_DATA_SOURCE_CATEGORY_ID") or os.getenv("CATEGORY_KB_DATA_SOURCE_ID"),
}

KB_MIN_SCORE = _float("KB_MIN_SCORE", 0.3)

KB_MAX_CHARS_PER_RESULT = _int("KB_MAX_CHARS_PER_RESULT", 600)

# "", "HYBRID" or "SEMANTIC". Empty keeps the Bedrock default.
KB_SEARCH_TYPE = os.getenv("KB_SEARCH_TYPE", "").strip().upper()

DEFAULT_KB_RESULTS = _int("DEFAULT_KB_RESULTS", 3)

MAX_KB_RESULTS = _int("MAX_KB_RESULTS", 8)


# ============================================================
# DYNAMODB TABLES
# ============================================================

INCIDENT_TABLE_NAME = os.getenv("INCIDENT_TABLE_NAME")

CATEGORY_TABLE_NAME = os.getenv("CATEGORY_TABLE_NAME")

ROSTER_TABLE_NAME = os.getenv("ROSTER_TABLE_NAME")


# ============================================================
# RESULT LIMITS
# ============================================================

DEFAULT_RESULT_LIMIT = _int("DEFAULT_RESULT_LIMIT", 10)

MAX_RESULT_LIMIT = _int("MAX_RESULT_LIMIT", 25)

# Longest free-text value (description, notes) returned to the model.
MAX_TEXT_CHARS = _int("MAX_TEXT_CHARS", 800)


# ============================================================
# BUSINESS DEFAULTS
# ============================================================

APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Kolkata")

INCIDENT_ID_PREFIX = os.getenv("INCIDENT_ID_PREFIX", "INC")

INCIDENT_ID_WIDTH = _int("INCIDENT_ID_WIDTH", 7)

DEFAULT_INCIDENT_STATE = os.getenv("DEFAULT_INCIDENT_STATE", "New")

DEFAULT_SOURCE_SYSTEM = os.getenv("DEFAULT_SOURCE_SYSTEM", "DTP Operate Agent")

# Delete needs dynamodb:DeleteItem on the agent role. Disable to make
# the agent read/create/update only.
ENABLE_DELETE_OPERATIONS = _bool("ENABLE_DELETE_OPERATIONS", True)

# Refuse every create / update / delete. Use it when testing against real
# tables (locally, or in a first deployment) so nothing can be modified.
READ_ONLY_MODE = _bool("READ_ONLY_MODE", False)


# ============================================================
# SESSIONS AND CACHES
# ============================================================

SESSION_TTL_SECONDS = _int("SESSION_TTL_SECONDS", 3600)

MAX_SESSIONS = _int("MAX_SESSIONS", 500)

# How long a fetched record may answer follow-up questions without
# being read from DynamoDB again.
ENTITY_CACHE_TTL_SECONDS = _int("ENTITY_CACHE_TTL_SECONDS", 60)

# A mutation may only use conversation context that is this recent.
MUTATION_CONTEXT_MAX_TURNS = _int("MUTATION_CONTEXT_MAX_TURNS", 8)

FACET_CACHE_TTL_SECONDS = _int("FACET_CACHE_TTL_SECONDS", 600)

DIRECTORY_CACHE_TTL_SECONDS = _int("DIRECTORY_CACHE_TTL_SECONDS", 120)


# ============================================================
# LOGGING
# ============================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


# ============================================================
# AWS SESSION / CLIENTS
# ============================================================

def get_boto3_session():
    """
    Create a boto3 session.

    Local:
        Uses AWS credentials from .env if supplied.

    AgentCore:
        No credentials are supplied explicitly.
        boto3 automatically obtains credentials from the
        AgentCore execution role.
    """

    kwargs = {"region_name": AWS_REGION}

    # Only explicitly provide credentials when they exist.
    #
    # This allows AgentCore IAM role credentials to work
    # automatically in production.
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:

        kwargs["aws_access_key_id"] = AWS_ACCESS_KEY_ID

        kwargs["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY

        if AWS_SESSION_TOKEN:
            kwargs["aws_session_token"] = AWS_SESSION_TOKEN

    return boto3.Session(**kwargs)


def get_boto_client_config() -> BotoConfig:
    """Timeouts and retries shared by the AWS clients."""

    return BotoConfig(
        connect_timeout=BEDROCK_CONNECT_TIMEOUT,
        read_timeout=BEDROCK_READ_TIMEOUT,
        retries={
            "max_attempts": 3,
            "mode": "standard",
        },
    )


@lru_cache(maxsize=1)
def get_dynamodb_resource():
    """One shared DynamoDB resource, created on first use."""

    return get_boto3_session().resource(
        "dynamodb",
        region_name=AWS_REGION,
        config=get_boto_client_config(),
    )


@lru_cache(maxsize=1)
def get_kb_client():
    """One shared Bedrock agent-runtime client, created on first use."""

    return get_boto3_session().client(
        "bedrock-agent-runtime",
        region_name=AWS_REGION,
        config=get_boto_client_config(),
    )


# ============================================================
# CONFIGURATION VALIDATION
# ============================================================

def missing_config():
    """Names of required environment variables that are not set."""

    required = {
        "AWS_REGION": AWS_REGION,
        "BEDROCK_MODEL_ID": BEDROCK_MODEL_ID,
        "KNOWLEDGE_BASE_ID": KNOWLEDGE_BASE_ID,
        "INCIDENT_TABLE_NAME": INCIDENT_TABLE_NAME,
        "CATEGORY_TABLE_NAME": CATEGORY_TABLE_NAME,
        "ROSTER_TABLE_NAME": ROSTER_TABLE_NAME,
    }

    return [name for name, value in required.items() if not value]


def validate_config():
    """
    Validate application configuration.

    AWS credentials are deliberately NOT validated here because
    AgentCore obtains credentials from its IAM execution role.
    """

    missing = missing_config()

    if missing:
        raise RuntimeError(
            "Missing environment variables: " + ", ".join(missing)
        )


# ============================================================
# AWS CONNECTION VALIDATION
# ============================================================

def validate_aws_connection():
    """
    Validate AWS connectivity.

    Intended mainly for local development/testing.

    Do not call this on every AgentCore request because it
    introduces an unnecessary STS call.
    """

    session = get_boto3_session()

    credentials = session.get_credentials()

    if credentials is None:
        raise RuntimeError("Unable to locate AWS credentials.")

    sts = session.client(
        "sts",
        region_name=AWS_REGION,
    )

    identity = sts.get_caller_identity()

    print("\nAWS Configuration")
    print(f"Region       : {AWS_REGION}")
    print(f"AWS Account  : {identity.get('Account')}")
    print(f"AWS ARN      : {identity.get('Arn')}")


# ============================================================
# DYNAMODB TABLE VALIDATION
# ============================================================

def validate_dynamodb_tables():
    """
    Validate that the configured DynamoDB tables exist.

    Intended for local startup validation.

    AgentCore should not call this on every request.
    """

    session = get_boto3_session()

    dynamodb = session.client(
        "dynamodb",
        region_name=AWS_REGION,
    )

    tables = {
        "INCIDENT_TABLE_NAME": INCIDENT_TABLE_NAME,
        "CATEGORY_TABLE_NAME": CATEGORY_TABLE_NAME,
        "ROSTER_TABLE_NAME": ROSTER_TABLE_NAME,
    }

    for config_name, table_name in tables.items():

        try:

            response = dynamodb.describe_table(TableName=table_name)

            status = response["Table"].get("TableStatus")

            print(f"[OK] {config_name}: {table_name} ({status})")

        except Exception as exc:

            raise RuntimeError(
                f"Unable to validate DynamoDB table "
                f"'{table_name}' "
                f"for configuration '{config_name}' "
                f"in region '{AWS_REGION}': "
                f"{exc}"
            ) from exc
