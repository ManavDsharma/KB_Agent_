"""
How an application calls the agent.

Local server (python main.py is running on port 8080):

    python dev/invoke_example.py --local "Show me INC0000258"

Deployed AgentCore Runtime (needs AWS credentials that may invoke it):

    python dev/invoke_example.py --arn arn:aws:bedrock-agentcore:ap-south-1:123456789012:runtime/NAME "Show me INC0000258"

Both send the same payload:

    {"prompt": "<user message>", "actor_id": "<authenticated user>"}

and the session is identified by the runtime session ID (a header locally,
`runtimeSessionId` on AWS). Reuse the SAME session ID for every message of
one conversation; use a new one for a new chat.
"""

import argparse
import json
import urllib.request
import uuid


def call_local(prompt: str, actor_id: str, session_id: str, port: int = 8080) -> dict:
    """POST to the local AgentCore app (python main.py)."""

    request = urllib.request.Request(
        f"http://localhost:{port}/invocations",
        data=json.dumps({"prompt": prompt, "actor_id": actor_id}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read())


def call_agentcore(arn: str, prompt: str, actor_id: str, session_id: str, region: str = "ap-south-1") -> dict:
    """Invoke the deployed runtime. This is what your backend does."""

    import boto3

    client = boto3.client("bedrock-agentcore", region_name=region)

    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id,  # at least 33 characters; a UUID is fine
        payload=json.dumps({"prompt": prompt, "actor_id": actor_id}).encode("utf-8"),
        qualifier="DEFAULT",
    )

    return json.loads(response["response"].read())


def render(result: dict) -> None:
    """What a frontend does with the answer: switch on structured.type."""

    if "error" in result:
        print(f"[error] {result.get('message')}")
        return

    structured = result.get("structured", {})

    kind = structured.get("type")

    if kind == "table":
        print(structured.get("title"))
        for row in structured.get("rows", []):
            print("  ", " | ".join(str(value) for value in row.values()))

    elif kind == "field":
        print(f"{structured.get('entity')} - {structured.get('label')}: {structured.get('value')}")

    elif kind == "update_confirmation":
        print(result["response"])

    else:
        # text, list, error, or a type this client does not know: the Markdown always works.
        print(result["response"])


def main():

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("prompt")
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--arn")
    parser.add_argument("--actor", default="local-tester")
    parser.add_argument("--session", default=str(uuid.uuid4()))
    parser.add_argument("--region", default="ap-south-1")

    args = parser.parse_args()

    if args.local:
        result = call_local(args.prompt, args.actor, args.session)

    elif args.arn:
        result = call_agentcore(args.arn, args.prompt, args.actor, args.session, args.region)

    else:
        parser.error("use --local or --arn")

    render(result)


if __name__ == "__main__":
    main()
