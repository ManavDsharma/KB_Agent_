# DTP Operate Agent — local development and deployment notes

Everything in this folder is for **local work only and is never deployed**. The only folder pushed to
Amazon Bedrock AgentCore Runtime is `app/dtp_operate_agent/`.

```text
app/dtp_operate_agent/     DEPLOYABLE agent: code, pyproject.toml, Dockerfile, .dockerignore
local_dev/                 this folder — tests, dev tools, .env, docs (not deployed)
integration_reference/     mirror of your separate frontend / backend files that changed for this agent
```

## What is in `local_dev/`

| Path | Purpose |
| --- | --- |
| `tests/` | 410 offline tests (fake DynamoDB / Knowledge Base, scripted model, no network) |
| `dev/chat.py` | Chat in the terminal (`--demo` = no AWS; `--read-only` = real AWS, nothing written) |
| `dev/check_aws.py` | Read-only preflight against real AWS (credentials, table keys/types, KB, model) |
| `dev/run_local.py` | Host the agent locally exactly like AgentCore (`/ping`, `/invocations` on :8080) |
| `dev/invoke_example.py` | Example client (local server or deployed runtime) |
| `.env` | Your local settings and AWS keys (loaded only when `ENVIRONMENT=local`) |
| `FRONTEND_CONTRACT.md` | The response contract the chat UI renders |

## Commands (run from `local_dev/`)

```bash
cd local_dev
python -m unittest discover -s tests -t . -v      # unit tests
python dev/chat.py --demo --meta                   # try it with sample data, no AWS
python dev/check_aws.py                            # preflight (read-only)
python dev/chat.py --read-only --meta              # real AWS, nothing can be written
python dev/run_local.py                            # serve like AgentCore on :8080
```

`ENVIRONMENT` defaults to **`production`** in the agent (safe for deployment: sessions are required, no
`.env` is read). The dev tools set `ENVIRONMENT=local` themselves. For real tables use
`READ_ONLY_MODE=true` (add it to `local_dev/.env`) or restored copies of the tables.

```bash
curl http://localhost:8080/ping
curl -X POST http://localhost:8080/invocations -H "Content-Type: application/json" \
  -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: my-local-session-0000000000000000001" \
  -d '{"prompt": "Show me INC0000258", "actor_id": "you@abc.com"}'
```

## Deploying `app/dtp_operate_agent/`

The folder is self-contained: `main.py` (entrypoint) + modules, `pyproject.toml` (runtime dependencies
only), `Dockerfile` (linux/arm64, port 8080) and `.dockerignore`. It contains no tests, no `.env` and no
docs. Nothing else is needed.

**Container** (untested here — Docker was not running when this was prepared):

```bash
cd app/dtp_operate_agent
docker build --platform linux/arm64 -t dtp-operate-agent .
```

Push the image to ECR and point the AgentCore Runtime at it, or use your existing `agentcore` deployment
flow with this folder as the source.

**Environment variables to set on the runtime** (no credentials — the runtime's IAM role is used):

| Variable | Notes |
| --- | --- |
| `AWS_REGION` | e.g. `ap-south-1` |
| `BEDROCK_MODEL_ID` | model / inference-profile ID with access in that region |
| `KNOWLEDGE_BASE_ID` | Bedrock Knowledge Base |
| `INCIDENT_TABLE_NAME`, `CATEGORY_TABLE_NAME`, `ROSTER_TABLE_NAME` | DynamoDB tables |
| `INCIDENT_KB_DATA_SOURCE_ID`, `CATEGORY_KB_DATA_SOURCE_ID`, `ROSTER_KB_DATA_SOURCE_ID` | optional; route KB searches to one data source |
| `MEMORY_DTPOPERATEAGENTMEMORY_ID` | injected by AgentCore when a memory is attached |
| `READ_ONLY_MODE` | `true` for a first, harmless deployment |
| `ENABLE_DELETE_OPERATIONS` | `false` if the role must not delete |

Other settings (token limits, caching, timeouts, session bounds) have safe defaults; the full list is in
`app/dtp_operate_agent/config.py`.

**IAM for the runtime role:** DynamoDB `GetItem`, `Scan`, `PutItem`, `UpdateItem` (+ `DeleteItem`) on the
three tables; `bedrock:InvokeModel` / `InvokeModelWithResponseStream` on the model; `bedrock:Retrieve` on
the Knowledge Base; AgentCore Memory access when a memory is attached. Logging to CloudWatch is standard.

**Identity:** the calling backend passes `actor_id` (the signed-in user) and a `runtimeSessionId`
(≥ 33 characters, one per chat). In production a request without a session is rejected and a missing
`actor_id` gets an isolated anonymous identity.

## Your frontend and backend

`integration_reference/` contains only the files that changed in your separate application, at their
original relative paths:

| File | Change |
| --- | --- |
| `frontend/src/pages/AIAgent.jsx` | Renders the agent's structured responses (cards, badges, tables, confirmations, option buttons, suggestion chips); ITSM intro text |
| `frontend/src/services/agentSocket.js` | Automatic reconnect with backoff |
| `backend/app/routes/agent_ws.py` | Passes the actor, validates input, no internals sent to the browser |
| `backend/app/services/agent_service.py` | Passes `actor_id` and `structured`; ITSM fallback text |
| `backend/app/integrations/agent_client_agentcore.py` | Sends `actor_id`; returns `structured`; boto3 call runs off the event loop |
| `backend/app/integrations/agent_client_local.py` | Rebuilt from the contract (the original was not shared) — merge with yours |

## Known limits

Direct routing is narrow and English-only; searches still scan tables (filtered server-side where
possible); session state is in process memory; no token streaming yet. See `DEVELOPMENT_LOG.md` at the
repository root for history and open questions.
