"""
Unit tests. They run offline: DynamoDB and the Knowledge Base are
replaced by fakes (tests/fakes.py) and no model is called.

Run from the local_dev directory:

    python -m unittest discover -s tests -t . -v
"""

import logging
import os
import sys
from pathlib import Path

# Expected failures (AWS errors, invalid values) are logged by design;
# keep the test output readable.
logging.getLogger("dtp").setLevel(logging.CRITICAL)

# Configuration must be in place before `config` is imported.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("AWS_REGION", "ap-south-1")
os.environ.setdefault("BEDROCK_MODEL_ID", "anthropic.claude-test")
os.environ.setdefault("KNOWLEDGE_BASE_ID", "kb-test")
os.environ.setdefault("INCIDENT_TABLE_NAME", "incidents-test")
os.environ.setdefault("CATEGORY_TABLE_NAME", "categories-test")
os.environ.setdefault("ROSTER_TABLE_NAME", "roster-test")
os.environ.setdefault("KB_DATA_SOURCE_INCIDENT_ID", "ds-incident")
os.environ.setdefault("KB_DATA_SOURCE_ROSTER_ID", "ds-roster")
os.environ.setdefault("KB_DATA_SOURCE_CATEGORY_ID", "ds-category")

# Safety net: a test must never reach AWS. Fake credentials plus an
# endpoint nothing listens on make an accidental call fail immediately.
os.environ["AWS_ACCESS_KEY_ID"] = "test"
os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
os.environ["AWS_ENDPOINT_URL"] = "http://127.0.0.1:9"

# The code under test is the deployable app, one level up and across.
APP_DIR = Path(__file__).resolve().parents[2] / "app" / "dtp_operate_agent"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


# The planner is the one place besides the tool agent that calls a model. A test
# that needs it patches `planner.extract` or `planner._planner_model`; any other
# test that reaches it gets a fast "no draft" instead of a network attempt.
import planner  # noqa: E402


def _no_model():
    raise RuntimeError("tests never call a model")


planner._planner_model = _no_model
