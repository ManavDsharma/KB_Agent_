# DTP Operate Agent — Architecture (Version 2)

How the system works **now**. For the history and the reasons behind each decision see
[DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md); for the rules when changing it see [CLAUDE.MD](CLAUDE.MD).
Local tooling, deployment notes and settings: [local_dev/README.md](local_dev/README.md).

---

## 1. Purpose

A conversational IT-service-management operations agent. Users ask in natural language; the agent
reads and changes structured data and searches semantic knowledge:

* **Incidents / requests** (`INC0000258`, `REQ0000162`) — read, search, list by date, SLA breaches,
  create, update, delete
* **Category Matrix issues** (`Issue-1`) — read, search, categories, update
* **Roster** — employees, teams, shifts, per-day availability; create, update, delete
* **Knowledge Base** — definitions, FAQs, how-tos, similar past incidents/categorisations
* **Hybrid** — "who should handle this issue and who is available?"

Stack: Python, Strands Agents, Amazon Bedrock (model + Knowledge Base), Amazon Bedrock AgentCore
Runtime and Memory, DynamoDB, boto3. Frontend is external.

---

## 2. High-level architecture

```text
Frontend  ──►  backend  ──►  AgentCore Runtime  ──►  main.py  (invoke)
 prompt          actor_id (authenticated user)          │
 renders         runtimeSessionId (one per chat)        ▼
 structured                                      SessionStore ── (actor_id, session_id) ──► SessionRuntime
                                                                                              │  state (SessionState)
                                                                                              │  agents (lazy, per tool domain)
                                                                                              ▼
                                                                                   agent.process_user_query
                                                                                              │
                                                              ┌───────────────────────────────┴───────────────┐
                                                              ▼                                               ▼
                                                    router.py (deterministic)                         classify_domain (domains.py)
                                                              │ clear + structured                            │ tool group
                                                              ▼                                               ▼
                                                    actions.py ── services ──► DynamoDB          Strands agent (tools of the domain)
                                                    (0 model tokens)                                  │  hooks.py (loop control,
                                                       │         │  None = "not mine"                 │   state updates)
                                                       │         └──────────► falls back ────────────►│  tools/ ──► services ──► DynamoDB / KB
                                                       │ multi-part write                             ▼
                                                       ▼                                     Reply { response, structured }
                                              plans.py  (planner.py: 1 small call, no tools)
                                              grounded draft → dry-run → execute | confirm | ask
```

The same diagram for writes only is in section 3a.

`{"response": "<markdown>", "structured": {"type": "text|field|table|list|update_confirmation|error", …}}`
is returned to the caller (types: `text | record | field | table | list | update_confirmation | question |
error`, each with `tone` and optional tap-to-send `suggestions`; see
[local_dev/FRONTEND_CONTRACT.md](local_dev/FRONTEND_CONTRACT.md)); token/route metadata is only logged.

---

## 3. Request flow

1. **`main.py`** validates the payload, resolves identity (session from the runtime context, actor from
   the payload), fetches the `SessionRuntime` and calls `process_user_query`. It logs one line:
   `route domain direct tools tokens_in tokens_out cache_read cache_write ms error` (never the prompt).
2. **Route** (`router.py`): first any *pending clarification*, then explicit IDs, then references
   resolved from session state, then the operation (read / update / delete). Result: an `Intent`.
3. **Direct execution** (`actions.py`) for clear intents: read from the session cache when fresh, else
   from DynamoDB via services; update the session state; return a `Reply`. A handler returning `None`
   means "not something I can answer" (e.g. unknown team) → model.
4. **Model path** for everything else: `classify_domain` picks a tool group, the domain's Strands agent
   runs with `hooks.AgentGuard`, and tool results update the same session state.
5. **Bookkeeping**: the turn is added to the session's recent exchanges; the state is mirrored into each
   agent's state so AgentCore can persist it.

If a *read* handler crashes the model answers instead; a crashed *write* is never retried behind the
user's back.

---

## 3a. Multi-part writes: understand with the model, execute deterministically

The fast path (`router.py`) claims a write only when it understands **all** of it: one record, one
`<field> to <value>`. "Set both priority and urgency to High", "close INC1 and INC2", "mark Rohit and Priya
unavailable on 2nd August" are *write-shaped but not fully understood*, so the router returns the `plan` route
(it never half-parses them: the old parser kept the first field and silently dropped the rest, which produced
the endless "What should the priority be?" loop).

```text
message ─► router ─┬─ strict fast path (0 tokens): one record, one explicit change  ──► execute
                   ├─ plan ─► planner.extract   one call, NO tools, ~2.1 K chars, structured output
                   │            │
                   │            ▼  plans.build_plan  grounding: every ID / name / field / value / date must be
                   │            │                    in the user's message; every named record must be covered
                   │            ▼  plans.validate    each op through its service with dry_run (maps "high" →
                   │            │                    "P2-High", resolves people, computes from → to; writes nothing)
                   │            ▼  tier
                   │               execute  one record, explicit change (several fields on it are fine)
                   │               confirm  several records / people / days, or an inferred year / past date
                   │                        → preview + [Confirm] [Cancel]; "yes" runs it, no model
                   │               ask      something missing / ambiguous → ONE question, plan kept in session
                   │                        state; the answer fills the slot ("INC0000259", "High", "Sharma")
                   └─ llm (tool agent): reads, reasoning, create, delete, and any rejected draft
```

| Piece | Role |
| --- | --- |
| `planner.py` | `PlanDraft` schema (ops: `update` fields / `availability` dates+value, with targets), ~1.2 K-char prompt, one structured-output call with no tools; never raises (failure = no draft) |
| `plan_text.py` | Pure helpers: `grounded_value`, `field_grounded`, `name_grounded`, `date_grounded`, `parse_dates_reply`, `is_yes/is_no`, `pick_option`, `apply_answer` |
| `plans.py` | `build_plan` → `validate` → `process` (tiers) → `_execute`; entry points `handle_plan / handle_resume / handle_confirm / handle_cancel` |
| `services/*` | `dry_run=True` on `update_incident`, `update_issue`, `set_employee_availability`, `update_roster_entry`; `parse_user_date` in `common.py` |

**Why the model cannot cause a wrong write.** It sees only the user's message, today's date and the IDs in
context, and has no tools. Whatever it returns is untrusted: a value, field, ID, person or date the user did
not write is rejected, and so is a draft that drops a record the user named. Dates are parsed by code
(`parse_user_date`), never by the model. A year the user did not give is guessed as the current year and
*shown* in the preview. The preview shows resolved values, so the user sees "P2-High" and "Rohit Malhotra",
not their own words. Execution re-validates, so changed data cannot slip through between preview and "yes".

**"Them" and "both of them".** Every list the user is shown (the deterministic SLA / by-date lists, and the tool
agent's `search_incidents` / `list_*` / `search_category_matrix` / `search_roster` results with two or more rows)
is remembered in `SessionState.last_list` (up to 20 IDs). "Update their priority as HIGH for both of them" then
means those records. It never means the single record in focus (that would silently change one of two) and it
expires after `MUTATION_CONTEXT_MAX_TURNS`; with no recent list the agent asks "Which incidents do you mean?".
Because the targets came from context rather than typed IDs, the preview is always shown ("These are the records
from the list I just showed"), and records that already have the value are named, not dropped.

**Plans live in `SessionState.pending`** (`plan_confirm` / `plan_slots`), are JSON-serialisable (mirrored
into AgentCore like the rest of the state), capped at `PLAN_MAX_OPERATIONS` (20), and expire after
`PENDING_MAX_TURNS` (3). Any unrelated message drops them. Sessions stay isolated by `(actor_id, session_id)`.

**Tokens.** The planner call costs ~2.1 K characters (~530 tokens) once, against ~6.3 K characters for *each*
call of the roster tool agent, which used one call per person. Confirm / cancel / answers cost nothing.
Planner usage is added to the request's logged `usage`; the tier is logged in `reason` (`tier=execute|confirm|
ask|fallback|error|noop`). `PLANNER_MODEL_ID` can point to a cheaper model once `evaluate.py --live` shows its
accuracy is enough.

---

## 4. Components

| Module | Responsibility |
| --- | --- |
| `main.py` | Transport only: payload validation, identity, session store, logging, response shape |
| `agent.py` | Orchestration: routing → direct or model; prompt assembly (static + cache point + per-turn context); Bedrock model with prompt/tool caching; error mapping |
| `router.py` | Deterministic `Intent` from text + session state. Conservative: reasoning/ambiguous → `llm`; a write it cannot fully read (several fields/records/days) → `plan`; yes/cancel and answers to a plan's question |
| `actions.py` | Executes intents: entity view/fields, update, delete, employee, team availability, lists, clarifications, plans |
| `planner.py` / `plans.py` / `plan_text.py` | Multi-part writes: one small tool-less model call → grounded plan → dry-run → execute / confirm / ask (section 3a) |
| `domains.py` | Chooses the tool group for a model request (`roster`, `incident`, `category`, `handlers`, `all`) |
| `hooks.py` | Strands hooks: refuse duplicate tool calls, tool-call budget + hard stop, feed results into state |
| `context.py` | `focus_*` helpers and `absorb_tool_result` — keeps the state in step on both paths |
| `responses.py` | `Reply` builders (text, record, field, table, list, update confirmation, question, error) |
| `presentation.py` | Conversational sentences for lookups, entity cards (badges, grouped sections, link), tone mapping, follow-up suggestions — all deterministic, 0 tokens |
| `memory/state.py` | `SessionState`: focus per entity type, field cache, pending clarification, recent exchanges, snapshot |
| `memory/store.py` | `SessionStore`/`SessionRuntime`: `(actor_id, session_id)` → state + lazy agents; TTL and size bounded |
| `memory/session.py` | AgentCore Memory session manager (persists session events; long-term retrieval optional) |
| `services/` | DynamoDB and KB access, validation, error mapping — the only place that touches AWS |
| `tools/` | Thin, typed, token-lean wrappers over services, grouped into domains |
| `config.py` | Environment-driven configuration and shared boto3 clients |
| `local_dev/dev/` | `chat.py`, `check_aws.py`, `check_roster.py`, `run_local.py`, `invoke_example.py`, `evaluate.py` (+ `eval_cases.json`) — local tooling, never deployed |
| `local_dev/tests/` | 542 offline tests: fake DynamoDB/KB, scripted model — never deployed |

### Services (`services/`)

* `common.py` — `ServiceError`, `guarded` (turns any failure into `{"ok": False, "error", "message"}`),
  `TTLCache`, paginated `scan_items`, `resolve_choice` (map "P2" → "P2-High"), date helpers,
  `ensure_writable` (`READ_ONLY_MODE`), `yes_no` / `same_type` (write in the column's existing format)
* `ids.py` — parsing/candidates for `INC0000258`, `INC-0000258`, `incident 258`, `Issue 9`
* `schema.py` — field lists, natural-language aliases, labels; the single source for the router and services
* `incident_service.py`, `categorization_service.py`, `roster_service.py`, `knowledge_base_service.py`,
  `assignment_service.py` (issue → team → availability)
* `services/__init__.py` — lazy registry (`get_*_service`, `set_service` for tests)

---

## 5. Data model (real tables)

| Table | Key | Facts the code depends on |
| --- | --- | --- |
| Incident | `ticket_id` | INC and REQ share the table; timestamps `YYYY-MM-DD HH:MM`; breach flags are text `TRUE`/`FALSE`; assignees/openers are e-mail addresses; `team_id` holds a team name |
| Category matrix | `issue_id` | `active` is `Yes`/`No`; dates `YYYY-MM-DD`; `team_id` holds a team name |
| Roster | `UID` (unique per row) | **One row per employee per month** (`year`, `month`). `emp_id` = employee number, `employee_id` = the employee's **e-mail**, `days_availability` = `{"1": "Y", "2": "N", "6": "NA", …}` keyed by day of month (`Y` available, `N` not available, `NA` weekend / non-working) |

A **person** is the set of their monthly rows, referenced as `emp:<emp_id>`. Availability on
`2026-09-30` reads the person's 2026-09 row, day `30`. A month with no row means "no roster", never
"not available".

---

## 6. Session memory

Three kinds of state, never mixed:

| State | Where | Purpose |
| --- | --- | --- |
| Operational facts | DynamoDB | Source of truth |
| **Session state** | `SessionState` in process (mirrored into agent state) | Short-term: what "it / he / that team" mean, **the last list shown (what "them / both of them" mean)**, cached fields, pending question or plan, last two exchanges |
| **AgentCore Memory** | Managed | Persists the session's events so it can survive a restart; long-term retrieval is **off** by default (`ENABLE_LTM_RETRIEVAL`) because it adds tokens to every prompt |

Rules: an explicit ID beats context; a **mutation may only use context from the last 8 turns**;
reference resolution picks the entity type that actually has the requested field (e.g. `impact` →
issue) and prefers the most recently discussed; cached records expire after 60 s and are corrected
after every write; pending clarifications expire after 3 turns. Sessions are isolated by
`(actor_id, session_id)`; idle sessions expire; the store is size-bounded.

The model does **not** receive the conversation replay. It receives a compact context block (entities,
last operation, pending question, two short exchanges) appended after the cached static prompt, and a
10-message sliding window.

---

## 7. Token strategy

Every model call re-sends the system prompt and the tool definitions, and a request makes several calls.

1. **Most requests never reach the model** (lookups, follow-ups, updates, deletes, clarifications,
   lists, team availability). A multi-part write costs one planner call (~530 tokens, no tools); its
   confirmation and every answer to its questions cost nothing.
2. **Domain-scoped tools**: a request carries only its group's definitions (roster/incident/category/
   handlers ≈ 2.5–4.5 KB vs ≈ 9 KB for all 22); unclear or mixed requests get all.
3. **Lean definitions**: short descriptions, schema noise stripped, ~1.3 KB prompt.
4. **Prompt/tool caching** (Claude models, `PROMPT_CACHING=auto`): static prefix cached; per-turn context
   comes after the cache point. Cache lasts ~5 minutes; `tokens_in` excludes cached tokens.
5. **Lean tool output**: `fields=`, compact JSON, columnar lists, no availability maps, truncated long text.
6. **Loop control**: a request may make at most 8 tool calls; identical calls are refused.

Fixed per-call cost (prompt + definitions, characters): roster 5.8 K, incident 5.3 K, category/handlers
3.8 K, all 10.5 K (was 16.5 K).

---

## 7a. Knowledge Base vs DynamoDB

| Question type | Source |
| --- | --- |
| Exact record, current state, filtering, availability, any create/update/delete | DynamoDB via services |
| Definitions, FAQs, how-to, "similar incidents", troubleshooting | Bedrock Knowledge Base (`search_knowledge_base`, narrowest `source`) |
| "Who should handle X?" | Hybrid tool: category matrix → owning team → team availability |

KB retrieval is filtered to one data source (when the IDs are configured), drops low scores and
duplicates, truncates text, and returns references (`INC…`, `Issue-N`) so details can be fetched from
DynamoDB. The KB is never the authority for current values (its sync is asynchronous).

---

## 8. Mutations

*Understand → validate → execute → brief confirmation.* No approval ceremony when target and change
are clear (one record, explicit change: executed at once, several fields on it included); a question is
asked only for a missing/ambiguous target, field or value; a **preview with Confirm / Cancel** is shown only
for batches (several records, people or days) and inferred values (a year the user did not give, a past
date). See section 3a.

* Values are mapped onto values already in the data; unknown values return the allowed list.
* Computed fields (`has_*_breach`, durations, `created_at`, `updated_at`, `closed_at`) are read-only;
  closing a ticket stamps `closed_at`; `working_notes` are appended.
* Writes keep each column's format (`Yes`/`No`, text numbers, table date formats) and are conditional
  (`attribute_exists` / `attribute_not_exists`); multi-field updates are one atomic write; roster
  availability writes set single days (no whole-map rewrites).
* Deletes: incident (by ID), roster **one monthly row**; issues cannot be deleted. `ENABLE_DELETE_OPERATIONS`
  and IAM control them.
* Audit log per write (`dtp.audit`: actor, target, old → new); `READ_ONLY_MODE` blocks every write.
* A write is never executed twice in one request and never retried by the model after a failed direct write.

---

## 9. Error handling and observability

* AWS/validation problems → `{"ok": False, "error", "message"}` from services → short user-safe text.
  Table names, ARNs and stack traces are never returned; details go to logs.
* Model errors (throttling, context overflow, loop limit, startup) map to friendly messages.
* Logs: one structured `request …` line per request; `tool_call name ms ok`; `audit …`; no prompt text.

---

## 10. Deployment and running

**What is deployed:** only `app/dtp_operate_agent/` (code, `pyproject.toml`, `Dockerfile`, `.dockerignore`),
as a container on **Amazon Bedrock AgentCore Runtime** (linux/arm64, port 8080, entrypoint `main.py`).
Tests, dev tools, docs and `.env` live in `local_dev/` and are not part of it.

**Environment:** `ENVIRONMENT` defaults to `production` (sessions required, no `.env` read); the local tools
set `local`. Required: `BEDROCK_MODEL_ID`, `KNOWLEDGE_BASE_ID`, `INCIDENT_TABLE_NAME`,
`CATEGORY_TABLE_NAME`, `ROSTER_TABLE_NAME`, `AWS_REGION`; no credentials (the runtime role is used). IAM:
DynamoDB Get/Scan/Put/Update [+Delete], Bedrock invoke, `bedrock:Retrieve`, AgentCore Memory. Full list in
[local_dev/README.md](local_dev/README.md).

**Calling it:**

```text
Browser ──WebSocket──► FastAPI backend (routes/agent_ws.py)
                         │ actor_id (authenticated user), session_id (one per chat)
                         ▼
                agent_service ──► agent_client_agentcore ──► bedrock-agentcore invoke_agent_runtime
                                   (boto3 call runs in a thread; payload {"prompt", "actor_id"};
                                    runtimeSessionId >= 33 chars)
                         ▼
        {"type": "response", "response": md, "structured": {...}, "source": "agent" | "fallback"}
```

The backend passes `structured` through untouched; the React page renders it (cards, badges, tables,
confirmations, option buttons, suggestion chips) and falls back to the Markdown. On any failure the backend
sends a friendly fallback message and logs the details. The reference versions of these files are in
`integration_reference/`.

**Local:** `python local_dev/dev/run_local.py` serves `/ping` and `/invocations` on 8080 (session in the
`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header); `chat.py --demo` needs no AWS; `chat.py --read-only`
uses real AWS without writing; `check_aws.py` is the preflight.

**Startup** does no AWS work; services and agents are created on first use.

---

## 11. Design principles

1. Transport, routing, actions, services and tools stay separate; `main.py` stays thin.
2. Deterministic when the request is clear; the model for ambiguity, reasoning and semantics.
3. Business rules and validation live in Python (services), not in prompts.
4. Tools are small and token-lean; adding a tool or a word to a description is a recurring cost.
5. Structured responses; the Markdown always remains usable.
6. Session state, AgentCore Memory and database facts are different things.
7. `actor_id + session_id` isolation is absolute; mutations never rely on stale context.
8. Failures are contained, explained briefly, and logged in detail.

---

## 12. Decision summary

| Decision | Reason |
| --- | --- |
| One agent per tool domain (was supervisor + 4 specialists) | ~4× fewer model calls; smaller per-call prompt |
| Deterministic router + structured session state | 0-token answers; reliable follow-ups |
| State-based reference resolution (not text rewriting) | Scoped, expiring, type-aware |
| Services own validation and error mapping | Consistent data, safe messages |
| Roster modelled as monthly rows / persons | Matches the real table |
| Domain-scoped tool definitions + caching | Cut the dominant per-call token cost |
| Multi-part writes: planner (understands) + deterministic plan engine (validates, executes) | The fast path used to half-parse them; a tool-using agent costs one call per record and decides for itself when to ask |
| Three write tiers: execute / confirm / ask | Single clear change stays instant; batches and inferred values are previewed; questions only for what is truly missing |
| Offline tests with fakes and a scripted model | Fast, safe regression net (542 tests) |
| `READ_ONLY_MODE`, `check_aws.py`, `chat.py` | Safe testing against real AWS |

---

## 13. Future improvements

* Verify prompt caching on the real model; tune the domain keywords from real `domain=all` cases.
* Indexes (GSIs) for date / team / state / priority / breach filters.
* Shared team naming (or a mapping) between the category matrix and the roster.
* Optional cheaper model for the model path (measure accuracy first).
* Pydantic response models; request correlation IDs across services.
