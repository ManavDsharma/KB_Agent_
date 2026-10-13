"""
Preflight check against your REAL AWS resources. Read-only: it never writes.

    python dev/check_aws.py              everything
    python dev/check_aws.py --skip-model skip the (tiny) Bedrock model call

It uses the same configuration as the agent (.env when ENVIRONMENT=local)
and checks, in order:
    1. required settings are present
    2. AWS credentials work (who am I)
    3. each DynamoDB table: exists, key schema, and the attribute names and
       TYPES of one sample row (values are not printed)
    4. the Knowledge Base answers, and which data sources it returns
    5. the Bedrock model answers (one 10-token call)

Every failure prints the likely cause. Paste the output back if something
looks wrong.
"""

import argparse
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app" / "dtp_operate_agent"

sys.path.insert(0, str(APP_DIR))

# The app treats anything but "local" as a deployed environment.
os.environ.setdefault("ENVIRONMENT", "local")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import config  # noqa: E402
from services import schema  # noqa: E402

OK = "[ OK ]"
FAIL = "[FAIL]"
WARN = "[WARN]"

problems = []


def report(status, message):

    print(f"{status} {message}")

    if status == FAIL:
        problems.append(message)


def explain(exc):
    """A short cause for the common AWS errors."""

    code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)

    hints = {
        "AccessDeniedException": "the IAM user/role lacks permission for this action",
        "AccessDenied": "the IAM user/role lacks permission for this action",
        "UnrecognizedClientException": "the access key is invalid",
        "InvalidSignatureException": "the secret key is wrong",
        "ExpiredTokenException": "temporary credentials expired; refresh them",
        "ResourceNotFoundException": "wrong table name or region",
        "ValidationException": "wrong model ID / parameter for this region",
        "ThrottlingException": "throttled; retry",
        "EndpointConnectionError": "no network path to the AWS endpoint (region / proxy?)",
        "NoCredentialsError": "no credentials found (.env or AWS profile)",
    }

    return f"{code}: {hints.get(code, str(exc)[:160])}"


def check_config():

    print("\n== 1. Configuration")

    print(f"     environment={config.ENVIRONMENT} region={config.AWS_REGION} read_only={config.READ_ONLY_MODE}")

    missing = config.missing_config()

    if missing:
        report(FAIL, "missing settings: " + ", ".join(missing))

    else:
        report(OK, "all required settings are present")

    if not config.READ_ONLY_MODE:
        report(WARN, "READ_ONLY_MODE is off: the agent can change data. Set READ_ONLY_MODE=true for first tests.")

    ids = {name: value for name, value in config.KB_DATA_SOURCE_IDS.items() if value}

    print(f"     knowledge-base data sources configured: {', '.join(ids) or 'none (searches are unfiltered)'}")


def check_identity(session):

    print("\n== 2. AWS credentials")

    try:

        identity = session.client("sts", region_name=config.AWS_REGION).get_caller_identity()

        report(OK, f"account {identity['Account']} as {identity['Arn']}")

    except Exception as exc:
        report(FAIL, "cannot authenticate - " + explain(exc))


def describe_type(value):

    kind = next(iter(value))

    if kind == "M":

        inner = value["M"]

        sample = ", ".join(f"{key}:{next(iter(v))}" for key, v in list(inner.items())[:4])

        return f"M({len(inner)} keys; e.g. {sample})"

    if kind == "L":
        return f"L({len(value['L'])})"

    return kind


def check_table(session, label, table_name, expected_key, expected_fields):

    print(f"\n   {label}: {table_name}")

    if not table_name:
        report(FAIL, f"{label}: table name not configured")
        return

    client = session.client("dynamodb", region_name=config.AWS_REGION)

    try:

        table = client.describe_table(TableName=table_name)["Table"]

    except Exception as exc:
        report(FAIL, f"{label}: cannot describe table - " + explain(exc))
        return

    keys = [(item["AttributeName"], item["KeyType"]) for item in table["KeySchema"]]

    types = {item["AttributeName"]: item["AttributeType"] for item in table["AttributeDefinitions"]}

    print(f"     status={table['TableStatus']} items~{table.get('ItemCount')} keys={keys} key types={ {k: types.get(k) for k, _ in keys} }")

    if len(keys) != 1 or keys[0][0] != expected_key:
        report(FAIL, f"{label}: the agent expects a single hash key '{expected_key}' but the table has {keys}")

    else:
        report(OK, f"{label}: key '{expected_key}' ({types.get(expected_key)})")

    try:

        items = client.scan(TableName=table_name, Limit=5).get("Items", [])

    except Exception as exc:
        report(FAIL, f"{label}: cannot read rows (dynamodb:Scan) - " + explain(exc))
        return

    if not items:
        report(WARN, f"{label}: the table is empty")
        return

    sample = items[0]

    print("     sample row attributes (name: type):")

    for name in sorted(sample):
        print(f"       {name}: {describe_type(sample[name])}")

    seen = set().union(*(set(item) for item in items))

    missing = [name for name in expected_fields if name not in seen]

    if missing:
        report(WARN, f"{label}: fields the agent knows about but not present in the first rows: {', '.join(missing)}")

    else:
        report(OK, f"{label}: all expected fields are present")


def check_tables(session):

    print("\n== 3. DynamoDB tables")

    check_table(session, "incidents", config.INCIDENT_TABLE_NAME, "ticket_id", schema.INCIDENT_FIELDS)

    check_table(session, "category matrix", config.CATEGORY_TABLE_NAME, "issue_id", schema.ISSUE_FIELDS)

    check_table(
        session,
        "roster",
        config.ROSTER_TABLE_NAME,
        "UID",
        [*schema.EMPLOYEE_VIEW_FIELDS, "days_availability"],
    )


def check_knowledge_base(session):

    print("\n== 4. Knowledge Base")

    if not config.KNOWLEDGE_BASE_ID:
        report(FAIL, "KNOWLEDGE_BASE_ID is not set")
        return

    client = session.client("bedrock-agent-runtime", region_name=config.AWS_REGION)

    try:

        response = client.retrieve(
            knowledgeBaseId=config.KNOWLEDGE_BASE_ID,
            retrievalQuery={"text": "password reset"},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 5}},
        )

    except Exception as exc:
        report(FAIL, "retrieve failed - " + explain(exc))
        return

    results = response.get("retrievalResults", [])

    report(OK, f"retrieve returned {len(results)} result(s)")

    found = {}

    for result in results:

        source = result.get("metadata", {}).get("x-amz-bedrock-kb-data-source-id", "(no data-source metadata)")

        found[source] = found.get(source, 0) + 1

    print(f"     data sources seen in results: {found or 'none'}")

    configured = {value: name for name, value in config.KB_DATA_SOURCE_IDS.items() if value}

    for source in found:

        if source in configured:
            print(f"     {source} -> configured as '{configured[source]}'")

        elif source != "(no data-source metadata)":
            report(WARN, f"data source {source} is not one of the configured IDs")


def check_model(session):

    print("\n== 5. Bedrock model")

    if not config.BEDROCK_MODEL_ID:
        report(FAIL, "BEDROCK_MODEL_ID is not set")
        return

    client = session.client("bedrock-runtime", region_name=config.AWS_REGION)

    try:

        response = client.converse(
            modelId=config.BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": "Reply with the single word OK."}]}],
            inferenceConfig={"maxTokens": 10, "temperature": 0},
        )

        text = response["output"]["message"]["content"][0].get("text", "").strip()

        usage = response.get("usage", {})

        report(OK, f"model answered {text!r} (tokens in={usage.get('inputTokens')} out={usage.get('outputTokens')})")

    except Exception as exc:
        report(FAIL, f"model call failed for '{config.BEDROCK_MODEL_ID}' - " + explain(exc))


def main():

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("--skip-model", action="store_true")

    args = parser.parse_args()

    check_config()

    session = config.get_boto3_session()

    check_identity(session)

    check_tables(session)

    check_knowledge_base(session)

    if not args.skip_model:
        check_model(session)

    print()

    if problems:
        print(f"{len(problems)} problem(s):")

        for problem in problems:
            print("  -", problem)

        sys.exit(1)

    print("Everything the agent needs is reachable. Next: python dev/chat.py --read-only --meta")


if __name__ == "__main__":
    main()
