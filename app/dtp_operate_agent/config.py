import os
from pathlib import Path
 
import boto3
from dotenv import load_dotenv
 
 
# ============================================================
# BASE DIRECTORY
# ============================================================
 
BASE_DIR = Path(__file__).resolve().parent
 
 
# ============================================================
# LOCAL ENVIRONMENT
# ============================================================
 
ENVIRONMENT = os.getenv(
    "ENVIRONMENT",
    "local",
).lower()
 
 
# Load .env only when running locally.
#
# In AgentCore, environment variables are provided by the
# runtime configuration / deployment configuration and the
# AWS execution role provides credentials.
if ENVIRONMENT in ("local", "ec2"):
 
    ENV_FILE = BASE_DIR / ".env"
 
    if ENV_FILE.exists():
        load_dotenv(
            dotenv_path=ENV_FILE,
            override=False,
        )
 
 
# ============================================================
# AWS CONFIGURATION
# ============================================================
 
AWS_REGION = os.getenv(
    "AWS_REGION",
    "ap-south-1",
)
 
# ============================================================
# AGENTCORE MEMORY
# ============================================================
 
# This is primarily useful for local testing.
#
# In AgentCore Runtime, the preferred approach is to provide
# the memory ID through an environment variable configured by
# AgentCore.
#
# The actual memory/session.py module reads the environment
# variable directly.
 
AGENTCORE_MEMORY_ID = os.getenv(
    "MEMORY_DTP_OPERATE_AGENT_ID"
)
 
# Optional local credentials.
#
# DO NOT configure these for AgentCore production deployment.
AWS_ACCESS_KEY_ID = os.getenv(
    "AWS_ACCESS_KEY_ID"
)
 
AWS_SECRET_ACCESS_KEY = os.getenv(
    "AWS_SECRET_ACCESS_KEY"
)
 
AWS_SESSION_TOKEN = os.getenv(
    "AWS_SESSION_TOKEN"
)
 
 
# ============================================================
# BEDROCK CONFIGURATION
# ============================================================
 
BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID"
)
 
KNOWLEDGE_BASE_ID = os.getenv(
    "KNOWLEDGE_BASE_ID"
)
 
 
# ============================================================
# DYNAMODB TABLES
# ============================================================
 
INCIDENT_TABLE_NAME = os.getenv(
    "INCIDENT_TABLE_NAME"
)
 
CATEGORY_TABLE_NAME = os.getenv(
    "CATEGORY_TABLE_NAME"
)
 
ROSTER_TABLE_NAME = os.getenv(
    "ROSTER_TABLE_NAME"
)
 
 
# ============================================================
# RESULT LIMITS
# ============================================================
 
DEFAULT_RESULT_LIMIT = int(
    os.getenv(
        "DEFAULT_RESULT_LIMIT",
        "20",
    )
)
 
MAX_RESULT_LIMIT = int(
    os.getenv(
        "MAX_RESULT_LIMIT",
        "50",
    )
)
 
DEFAULT_KB_RESULTS = int(
    os.getenv(
        "DEFAULT_KB_RESULTS",
        "5",
    )
)
 
MAX_KB_RESULTS = int(
    os.getenv(
        "MAX_KB_RESULTS",
        "10",
    )
)
 
 
# ============================================================
# AWS SESSION
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
 
    kwargs = {
        "region_name": AWS_REGION
    }
 
    # Only explicitly provide credentials when they exist.
    #
    # This allows AgentCore IAM role credentials to work
    # automatically in production.
 
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
 
        kwargs["aws_access_key_id"] = (
            AWS_ACCESS_KEY_ID
        )
 
        kwargs["aws_secret_access_key"] = (
            AWS_SECRET_ACCESS_KEY
        )
 
        if AWS_SESSION_TOKEN:
 
            kwargs["aws_session_token"] = (
                AWS_SESSION_TOKEN
            )
 
    return boto3.Session(**kwargs)
 
 
# ============================================================
# CONFIGURATION VALIDATION
# ============================================================
 
def validate_config():
    """
    Validate application configuration.
 
    AWS credentials are deliberately NOT validated here because
    AgentCore obtains credentials from its IAM execution role.
    """
 
    required = {
        "AWS_REGION": AWS_REGION,
        "BEDROCK_MODEL_ID": BEDROCK_MODEL_ID,
        "KNOWLEDGE_BASE_ID": KNOWLEDGE_BASE_ID,
        "INCIDENT_TABLE_NAME": INCIDENT_TABLE_NAME,
        "CATEGORY_TABLE_NAME": CATEGORY_TABLE_NAME,
        "ROSTER_TABLE_NAME": ROSTER_TABLE_NAME,
    }
 
    missing = [
        name
        for name, value in required.items()
        if not value
    ]
 
    if missing:
 
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
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
 
        raise RuntimeError(
            "Unable to locate AWS credentials."
        )
 
    sts = session.client(
        "sts",
        region_name=AWS_REGION,
    )
 
    identity = sts.get_caller_identity()
 
    print("\nAWS Configuration")
    print(
        f"Region       : {AWS_REGION}"
    )
    print(
        f"AWS Account  : {identity.get('Account')}"
    )
    print(
        f"AWS ARN      : {identity.get('Arn')}"
    )
 
 
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
 
            response = dynamodb.describe_table(
                TableName=table_name
            )
 
            status = response[
                "Table"
            ].get(
                "TableStatus"
            )
 
            print(
                f"[OK] {config_name}: "
                f"{table_name} "
                f"({status})"
            )
 
        except Exception as exc:
 
            raise RuntimeError(
                f"Unable to validate DynamoDB table "
                f"'{table_name}' "
                f"for configuration '{config_name}' "
                f"in region '{AWS_REGION}': "
                f"{exc}"
            ) from exc
 
 