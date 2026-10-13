# DTP Operate Agent — Development Log

What we built, what broke, what we changed and why. Read this to understand *why the code is the way
it is*; read [ARCHITECTURE.md](ARCHITECTURE.md) for *how it works now* and [CLAUDE.MD](CLAUDE.MD) for the
rules to follow when changing it.

Last updated: 2026-10-09.

---

## 1. Timeline at a glance

| Version | What it was | Outcome |
| --- | --- | --- |
| **V1** | The code base as found: supervisor + 4 specialist agents, a regex router, raw DynamoDB scans, Markdown answers | Worked for demos; several silent bugs, high token cost, unsafe writes, no tests |
| **V2.0** | Refactor into a production-style conversational ITSM agent: session state, deterministic-first routing, CRUD, validation, KB routing, error handling, tests | 303 offline tests; built against *assumed* table shapes |
| **V2.1** | Aligned with the **real** table schemas you supplied (monthly roster rows, `Y/N`, e-mail in `employee_id`, `Yes/No`, date formats) | 333 tests; local tooling added (`chat`, `check_aws`, `invoke_example`, `READ_ONLY_MODE`) |
| **V2.2** | Token optimisation after the first real run showed **24,623 input tokens** for one question | 362 tests; fixed per-call cost cut 65–78 % for focused requests |
| **V2.3** | Conversational, well-formatted responses for a chat frontend (cards, badges, sentences, suggestion chips); output-cap fix | 395 tests |
| **V2.4** | Deployment readiness: deployable folder separated from local tooling; integration with the separate React/FastAPI application | 395 tests |
| **V2.5** | Roster availability now understands `NA` (weekend / non-working day) as well as `Y` / `N` | 410 tests |
| **V2.6** | Availability read from `days_availability` however it is stored (DynamoDB map **or JSON text**, padded keys, loose values) | 433 tests |
| **V2.7** | **Multi-part writes**: a small tool-less planner understands them; a deterministic plan engine validates (dry run), tiers (execute / confirm / ask) and executes. `dev/evaluate.py` added | 542 tests, 35 evaluation cases |

---

## 2. Version 1 — the code base as found

### What it was
* `main.py` (AgentCore entrypoint) → `agent.py` (~2,500 lines) with everything in one file.
* A **supervisor** Strands agent with **four specialist agents as tools** (Incident, Categorization,
  Roster, Knowledge). One simple question = supervisor call → specialist call → tool → specialist →
  supervisor: ~4 LLM calls, each re-sending prompts and tool schemas.
* A regex `DeterministicRouter` for `Issue-9` / `INC…` lookups, plus a text-rewriting
  `resolve_contextual_reference()` that replaced "it/its" with the last ID.
* Tools were thin wrappers returning **whole DynamoDB items**; services did **full-table scans** and
  filtered in Python.
* Answers were Markdown built from raw dicts. In-memory `_supervisors` / `_session_contexts` dicts.
* Docs described a structured `{"type": ...}` response contract that the code did not implement.

### What was good (and kept)
Deterministic-first routing, DynamoDB for facts vs Knowledge Base for semantics, session isolation by
`(actor_id, session_id)`, a thin `main.py`, and clear design principles in the docs.

### Failures found in V1

Verified by reading the code and by running the real router on sample questions.

| # | Symptom | Root cause | Impact |
| --- | --- | --- | --- |
| 1 | "Show me Issue-9" returned only the issue's *name* field | The bare word "issue" was an alias for the field `issue`; longest-alias-wins picked it for every short question ("Which team owns Issue-9?" too) | The main deterministic route never returned the full record |
| 2 | "What is the priority of INC-1?" always said "field not found" | `get_incident` returns `{"found":…, "incident":{…}}` but the code looked for `priority` at the top level | Every direct incident field question failed, and it never fell back to the LLM |
| 3 | Direct answers were unreadable | The dict was rendered as a 2-row table with the whole record as one JSON string | Not the promised structured response |
| 4 | Read questions treated as updates | Substring matching: "set" in "asset", "update" in "updated" | Wasted LLM calls |
| 5 | Multi-field questions silently dropped fields | Only the first matching alias was used | Wrong partial answers |
| 6 | "incident 1123" looked up `INC1123` | No key-format handling; exact `get_item` only | "Supports both formats" claim untrue |
| 7 | "it" rewritten in *any* sentence, forever | Global regex on `\bit\b`, no expiry | Wrong context, wrong targets |
| 8 | ~4 LLM calls per simple query | Supervisor + specialists; specialists keep their own history | High cost and latency |
| 9 | Roster search returned every employee's full availability map | Raw items returned to the model | Thousands of wasted tokens |
| 10 | Full-table scans for every list/search; `limit` applied after the scan | Python-side filtering | Cost grows with table size |
| 11 | Updates took free text; computed fields (`has_*_breach`, durations, `updated_at`) were writable | No validation, wide allow-list | "P2" vs "P2-High" data drift; LLM could overwrite computed data |
| 12 | Roster edits could lose data | Read whole map → modify → write whole map | Lost updates under concurrency |
| 13 | KB query polluted | "Find incidents similar to…" prepended to the embedding text | Worse retrieval |
| 14 | Direct-path turns never reached the model's history or AgentCore Memory | Direct path bypassed the supervisor | "Summarise the above" had nothing |
| 15 | Unsafe identity fallbacks | `"default-user"` / `"local-session"` shared across users when headers were missing; `actor_id` from the payload | Cross-user state sharing risk |
| 16 | Internals leaked to users | `str(exc)` returned on errors | Table names / ARNs exposed |
| 17 | Config drift | Dead `router.py` and `memory/session.py` duplicates; four spellings of the memory env var; `validate_config()` at import | Confusion, fragile startup |
| 18 | No tests, no token metrics in production | Metadata computed but discarded in `main.py` | Nothing to catch regressions |

---

## 3. Version 2.0 — the refactor

### Goal (from the task spec)
A production-quality conversational ITSM operations agent: correct **session memory**, **token
efficiency**, correct **routing** (DynamoDB vs Knowledge Base vs both), **safe mutations** with a
simple *understand → validate → execute → confirm* flow (no approval ceremony), robust ambiguity and
error handling, and tests.

### What we built and why

| Decision | Why |
| --- | --- |
| **Structured session state** (`memory/state.py`): current incident / issue / employee / team, a field cache, a pending clarification, last two exchanges | Solves "it / his / that team" *without* replaying history to the model; the model gets a few lines of context |
| **Deterministic-first, but conservative** (`router.py` + `actions.py`) | Clear structured requests cost 0 tokens and are predictable; anything analytical, multi-part or ambiguous goes to the model |
| **References resolved from state, not by rewriting text** | The old rewrite hit every "it"; state-based resolution is scoped, expires, and picks the entity type by which one actually has the field |
| **Mutation context limited to 8 turns** | A stray "it" must never modify a target discussed long ago |
| **Pending clarifications** ("Update it." → "What would you like to update for INC…?" → "Set priority to P2.") | Ask only for what is missing, then complete the original request |
| **One agent instead of supervisor + specialists** | ~4× fewer model calls; one tool loop; no duplicated histories |
| **Services own validation** (`services/`) | Values are mapped onto what the data already uses ("P2" → "P2-High"); invalid values return the allowed list; computed fields are read-only; writes are conditional and audited |
| **Services never raise AWS errors** (`guarded`, `ServiceError`) | Users get short safe messages; details go to logs |
| **Token-lean tools** (`fields=`, columnar lists, compact JSON, no availability maps) | The model only receives what it needs |
| **Strands hooks** (`hooks.py`) | Refuse duplicate tool calls (a write never runs twice), cap tool calls, feed tool results into the session state so both paths share one memory |
| **Composite hybrid tool** (`find_handlers_for_issue`) | "Who should handle X?" is one deterministic call (matrix → team → availability) instead of three LLM steps |
| **KB routing by data source + score cut-off + dedupe + truncation** | Fewer, more relevant chunks; raw query text (no padding) |
| **`{"response": markdown, "structured": {...}}`** | Backward compatible for the frontend while delivering the promised structured contract |
| **Lazy creation** of services and agents; startup config check only warns | Faster cold start; no AWS work at import |
| **Identity hardening** | Non-local requests need a session; missing actor → `anon-<session>`; errors never leak internals |
| **Offline test suite** with fake DynamoDB (evaluates real boto3 conditions), fake KB and a **scripted Strands model** | The real agent loop, hooks and tools are tested with no AWS |

### Bugs found *while* building V2.0 (and fixed)

| Bug | Found by | Fix |
| --- | --- | --- |
| "Explain **why** INC… is in that state" was answered by the deterministic path | Integration test | The "explain/describe" exemption from the reasoning guard was unnecessary; removed |
| A field asked for explicitly but absent on the record (e.g. `assigned_to` on an unassigned ticket) was silently dropped | Service test | `project(keep_empty=True)` now reports `None` |
| Pending "value" reply swallowed a fresh command ("set state to Closed") | Router probe | New commands cancel the pending question |
| Choice replies like "the one in Infrastructure" not understood | Conversation test | Filler words ignored when matching candidates |
| No-context "Who opened it?" fell through to the model (and a test tried to reach AWS) | Slow test run | Asks which ticket instead; tests now run with fake credentials and a dead endpoint as a safety net |
| Test output was flooded with expected error logs and tracebacks | Test run | The `dtp` loggers are silenced in tests (audit assertions still work) |

Result: **303 offline tests**.

---

## 4. Version 2.1 — reality check against the real schemas

We built V2.0 from assumptions. You then supplied real rows for the three tables, which disproved
several of them.

| Real data | Assumed | Consequence if not fixed | Change |
| --- | --- | --- | --- |
| Roster: **one row per employee per month** (`year`, `month`); `days_availability` keyed **day-of-month** with **`Y`/`N`** | One row per employee, keyed by full date | Availability lookups would return nothing | Person = set of monthly rows (`emp:<emp_id>`); availability reads the right month/day; a missing month is "no roster", never "not available"; edits target one month, identity fields change all rows |
| `emp_id` = employee number; **`employee_id` = the e-mail**; `UID` unique per row | `employee_email` field | Name → assignee resolution would fail | Incident assignees resolve to the roster e-mail |
| `active` = **`Yes`/`No`** text; SLA minutes may be text; breach flags `TRUE`/`FALSE` text | Booleans / numbers | Writing changes column types | Writes keep each column's existing format (`yes_no`, `same_type`) |
| Incident timestamps `YYYY-MM-DD HH:MM`; roster/matrix dates `YYYY-MM-DD` | ISO-Z timestamps | Mixed formats in the tables | Written in each table's own format; closing a ticket stamps `closed_at` |
| `urgency` = `High`; `team_id` holds **team names** | `1-High`, `INFRA` | Validation rejected real values | Values validated against data already in the tables |
| `.env` used `INCIDENT_KB_DATA_SOURCE_ID` / `CATEGORY_KB_DATA_SOURCE_ID`; memory var `MEMORY_DTP_OPERATE_AGENT_ID` | `KB_DATA_SOURCE_*_ID` | KB routing silently off | Both spellings accepted; memory stays off locally (the name is not the AgentCore one) |

Other changes in V2.1:
* **Real-data tooling** (`local_dev/dev/`, moved out of the app folder in V2.4): `chat.py` (`--demo` needs no AWS, `--read-only`
  for real tables), `check_aws.py` (read-only preflight: credentials, key schema and attribute *types*
  of sample rows, KB data sources, one 10-token model call), `invoke_example.py` (local and AgentCore
  client).
* **`READ_ONLY_MODE`**: every write is refused before any AWS call — the safe way to test against
  real tables.
* Bug caught along the way: a **backspace character** slipped into a regex (a `\b` mangled by shell
  quoting in a patch script), silently disabling month detection. Found by a failing test; all files
  are now checked for control characters.

Result: **333 tests**.

---

## 5. Version 2.2 — token optimisation

### Observation
First real run: `tokens_in=24623` for one question
(`tools=get_team_roster, get_employee_availability ×3`).

### Diagnosis
`tokens_in` is the **sum over every model call** of the request, and *each call re-sends the system
prompt and all tool definitions*. That request made ≥3 calls of ~8 K tokens. Contributing factors:

1. **Tool definitions dominated** (25 tools sent on every call).
2. **The model looped**: one roster call + one availability call per person, although
   `get_team_availability` answers a whole team in one call.
3. **Caching unverified**: `--meta` did not show cache tokens (cached tokens are excluded from
   `tokens_in`), so we could not tell whether caching worked.
4. **Schema noise**: Strands adds `"description": "Parameter x"` to every argument and
   `"default": null` to every optional one.

### Changes
* **Domain-scoped tool sets** (`domains.py`, `tools/__init__.py`): roster / incident / category /
  handlers get only their tools; unclear or mixed requests get all. One agent per domain (distinct
  `agent_id` so AgentCore Memory keeps them apart), all sharing the session state.
* **"Who is available today in <team>" answered without the model** (deterministic route, unknown team
  falls back to the model).
* **Lean definitions**: tools 25 → 22 (`get_team_roster` → `search_roster(team=…)`,
  `find_similar_*` → `search_knowledge_base(source=…)`), short descriptions, schema noise stripped via
  the supported `tool_spec` setter, prompt ~1.9 K → ~1.3 K characters.
* Prompt tells the model to use `get_team_availability` for a team and never loop per person.
* Conversation window 14 → 10; recent exchanges 3 → 2.
* Observability: `cache_read`, `cache_write` and `domain` in `dev/chat.py --meta` and the request log.

### Result (fixed part of every model call: prompt + tool definitions, characters)

| Request | Before | After |
| --- | --- | --- |
| Roster | 16.5 K | 5.8 K (−65 %) |
| Incident | 16.5 K | 5.3 K (−69 %) |
| Category / "who should handle" | 16.5 K | 3.8–3.9 K (−77…78 %) |
| Unclear or mixed | 16.5 K | 10.5 K (−37 %) |

Sizes are measured in characters (no tokenizer offline); real token counts must be read from the next
run. Result: **362 tests**.

### Still to verify on real AWS
Does caching activate (`cache_read > 0` on a repeat within ~5 minutes)? Which questions still land on
`domain=all`?

---

## 5b. Version 2.3 — a response a chat frontend can render well

### Trigger
The frontend will receive the JSON over a WebSocket and must "look good and feel conversational". V2.2
answers were correct but plain: Markdown bullet lists (`**INC…** — Assigned to: x`), one generic
`table` type for records, no hints about what to ask next.

### Also fixed on the way
"That answer was too long" appeared for ordinary questions: the output cap was 1,500 tokens and hitting it
raised an error that discarded the partial answer. The cap is now 4,000 (a ceiling, not a cost), a cut-off
answer is shown with a note, and the prompt asks for ≤ ~150 words with at most 10 list rows.

### Changes (agent side, all deterministic — 0 tokens)
* **Conversational sentences** for lookups: `INC0000258 is assigned to varun…`, `Rohit Malhotra works the S1
  shift.`, `… is available on 2026-09-30.` (`presentation.py`).
* **Entity cards** (`type: "record"`): title, subtitle, coloured **badges** (priority, state, SLA breached),
  a summary sentence, grouped **sections** (People / Classification / SLA / Timeline / Details …), and a link.
* **Follow-up suggestions**: tap-to-send chips per context ("Is it breached?", "Is he available tomorrow?").
  A test taps every chip and asserts the router answers it without the model.
* **Clarification buttons**: `type: "question"` with `options[] {label, send}`.
* Every reply now carries `type`, `text` (same Markdown as `response`) and `tone`; updates/deletes keep the
  brief specified wording (`Updated INC… / - Priority: A → B`).
* Model answers declare `format: "markdown"`, and the prompt asks for a short sentence first, bold IDs,
  no headings/tables.

### Frontend side
* [FRONTEND_CONTRACT.md](local_dev/FRONTEND_CONTRACT.md): the envelope, every type with fields,
  tones, rendering rules and a suggested WebSocket wrapper.
* A throw-away reference chat page was built to check the contract visually (it surfaced a query-string bug
  in its server and two layout issues). It was **removed in V2.4**: the real frontend is a separate
  application, and the same contract is now implemented there (see V2.4).

### Not done yet
Token **streaming** and progress messages for model-generated answers (a typing effect). Needs an event
stream (`status` / `delta` / `final`) and matching support in the WebSocket bridge.

Result: **395 tests**.

---

## 5c. Version 2.4 — deployment readiness and the real frontend

### Trigger
The agent was ready to push, but `app/dtp_operate_agent/` still held tests, dev tools, docs and a `.env` with
AWS keys; and the real application (React page + FastAPI WebSocket bridge + AgentCore client) still spoke the
V1 contract (plain text, no actor).

### Deployable folder
* **Separated** into `app/dtp_operate_agent/` (deployed) and `local_dev/` (tests, dev tools, docs, `.env`).
  The `.env` moved out so it can never be packaged; `config.py` still finds it for local runs.
* **`ENVIRONMENT` now defaults to `production`.** It used to default to `local`, which would have let a
  deployed agent accept requests without a session (shared `local-session`). The dev tools set `local`.
* `pyproject.toml`: runtime dependencies only, version-pinned to what the code needs (Strands ≥ 1.57 for
  `CacheConfig` and hooks, bedrock-agentcore ≥ 1.24, boto3 ≥ 1.42).
* `Dockerfile` (linux/arm64, port 8080, non-root, `opentelemetry-instrument python main.py`) that installs
  dependencies straight from `pyproject.toml`, and `.dockerignore`. **Not built here** — Docker Desktop was
  not running.
* Verified: the app imports with no test/dev modules on the path; all 395 tests pass from `local_dev/`.

### Integration with the separate application (`integration_reference/`)
| File | Why |
| --- | --- |
| `AIAgent.jsx` | Was rendering `content` as plain text. Now renders every `structured` type in the app's own Tailwind tokens, plus suggestion chips and option buttons; intro text was for RPA bots, now ITSM |
| `agentSocket.js` | Never reconnected: a backend restart left the chat offline until reload |
| `agent_ws.py` | Actor was never sent; unbounded prompt; `str(exc)` reached the browser; duplicate imports |
| `agent_service.py` | Dropped `structured`; fallback text described bots; leaked the exception text in `error` |
| `agent_client_agentcore.py` | Sent no `actor_id`; **blocking boto3 call inside `async def` froze every WebSocket while one answer was generated** (now `asyncio.to_thread`); 60 s read timeout too short for model answers |
| `agent_client_local.py` | Original not shared: rebuilt from the contract (needs merging) |

Checked by bundling the **real `AIAgent.jsx`** with a mock socket that replays real agent payloads and
rendering it in a headless browser: every response type rendered without errors. (That test found a
duplicate-React problem in the test harness only, not in the page.)

### Open items
* The backend has no authentication in what was shared, so `actor_id` comes from a proxy header or a fixed
  default (`dtp-web-user`). Replace `_actor_for` in `agent_ws.py` with the signed-in user; until then every
  change is audited under that one name.
* Streaming/progress messages are still not available.

---

## 5d. Version 2.5 — `NA` in the availability map

### Trigger
The roster's `days_availability` uses three values, not two: `Y` (available), `N` (not available, e.g. leave)
and `NA` (not scheduled: weekend / non-working day). The code only knew `Y` and `N`.

### What would have gone wrong
* An `NA` day was reported as "No data" instead of "weekend".
* The month summary ignored weekends, and "off days" could not tell leave from a weekend.
* Writing "weekend" was rejected (the roster's vocabulary contained `NA`, so the Y/N synonym mapping was off).
* A whole team's weekend answered "0 of N available" instead of "nobody is scheduled".

### Changes
* Values are `Y` → *Available*, `N` → *Not available*, `NA` → *Non-working day* everywhere (single employee,
  team table, roster card, tool outputs).
* Wording is mapped to the right code: available/working → `Y`; leave/off/unavailable/sick → `N`;
  weekend/NA/n/a/week off/non-working → `NA`; anything else is rejected with the three valid values.
* The roster card now says e.g. `20 working days; off on: 3, 12; 8 weekend / non-working days`.
* The model prompt and the tool descriptions explain `NA`; the reference React page colours *Non-working day*
  neutral.

Result: **410 tests**.

---

## 5b. V2.6 — availability on the real `days_availability` column

**Report:** "The code is not working for employee availability" — the column holds `{"1": "Y", "2": "N", ...}`
(day of month -> Y available / N not available / NA weekend).

### Cause (most likely; live data could not be inspected)
The reader accepted only a DynamoDB *map*. If the column is stored as **JSON text** (very common when rows
are loaded from a CSV/export), every row looked empty, so availability was "no data". Writes and the
vocabulary scan had the same blind spot.

### Changes
* `decode_days()` reads a map, JSON text, single-quoted text, bytes or a list; keys may be zero-padded
  (`"05"`); values are normalised (`y`, `Yes`, `n/a` -> `Y` / `NA`). Garbage yields "no days", never an error.
* Days are handled as integers everywhere (summary, employee availability, team availability, writes).
* Writes keep the column's own format: map -> per-key atomic `SET`; JSON text -> read-merge-write guarded by
  `#days = :old` with retry; missing column -> created in the roster's prevailing style; new roster rows
  follow the same style. Existing key spelling (`"05"`) is preserved.
* `dev/check_roster.py` (read-only) prints the stored type, a raw sample and what was understood, per row.

Result: **433 tests** (`tests/test_availability_formats.py`; the fake table now evaluates `#attr = :value`).

---

## 5c. V2.7 — multi-part writes: planner, plans and tiers

### Trigger
A chat transcript: *"Set both Priority and urgency of the INC… to High"* → "What should the priority be?" →
*"Set priority and urgency to high"* → "Which incident would you like to update?" → *the ticket ID* → "What
would you like to update for INC…?" — a loop that never wrote anything. And a request the agent was expected to
handle: *"update employee2 and employee1 availability for 2nd August as false"*.

### Diagnosis (reproduced by running the real router on the three messages)

| # | Gap | Evidence |
| --- | --- | --- |
| 1 | An update could hold **one field on one record**. `_update_intent` read the first field name and looked for `to <value>` right after it | "priority **and urgency** of ENT to High": no value found after "priority" → "What should the priority be?" (the value, the second field and "both" were dropped) |
| 2 | The router **claimed requests it only half understood**. The fast path took everything after "to" as the value, and "close INC1 and INC2" acted on INC1 only | breaks rule 2 ("only claim what is unambiguous") |
| 3 | A clarification **forgot** what had been understood: pending kept `changes` only when complete, never the ID the user had named | turn 2 had no target, turn 3 had no changes: the loop |
| 4 | While waiting for a value, any message containing "set" counted as a *new* request, and the ID from turn 1 had never become context | turn 2 → "Which incident?" |
| 5 | **Every roster write went to the full tool agent** (`_route_employee` returns `None` for updates): one model call per person, ~6.3 K characters each, capped at 8 tool calls, with a prompt that tells the model to "ask ONE short question" when unsure | the source of vague follow-ups |
| 6 | No tests for multi-field / multi-record / multi-day writes, or for clarification-with-memory | nothing failed in CI |

### Decision: agentic only where understanding is needed
| Deterministic | One small model call | Tool agent (unchanged) |
| --- | --- | --- |
| ID/name/value/date resolution, validation, writes, yes/cancel, answers to questions, replies | Turning a free-form write into operations: how many, which records, which fields and values, what "both" refers to | Reads, reasoning, Knowledge Base, create, delete, any rejected draft |

We rejected (a) more regexes (golden rule 2; "both X and Y", "A to X and B to Y", "mark …" never end) and (b)
sending everything to the tool agent (a call per record; it decides for itself when to ask; validation in the
prompt). The planner (`planner.py`) has **no tools and sees no record data**; `plans.py` treats its output as
untrusted.

### What was built
* `router.py`: `_multi_change` (several field mentions before the value, collective words, a "value" that holds
  another request or an ID), several IDs in one message, write verbs with no pattern (`mark`, `make`, `bump`,
  `raise`, `lower`, `escalate`…), roster writes, long writes → `plan`. "Show INC1 and INC2" / "delete INC1 and
  INC2" now go to the model instead of acting on the first ID. Yes / cancel and plan answers are routed in
  `_route_pending`.
* `planner.py`: `PlanDraft` schema, ~1.2 K-char prompt, structured output via Strands, never raises.
* `plans.py`: grounding → slots → dry-run validation → tiers → execution → replies. `plan_text.py`: pure helpers.
* Services: `dry_run=True` on `update_incident`, `update_issue`, `set_employee_availability`,
  `update_roster_entry`; `parse_user_date` ("2nd August", ISO, relative) in code, never by the model.
* `actions._clarify` now remembers the record the user named; `responses.confirm_reply` / `batch_reply`
  (additive: `question.confirmation/plan/notes`, `update_confirmation.items/failed`; the old question bubble shows
  the whole preview).
* Config: `PLANNER_MODEL_ID` (defaults to the main model), `PLANNER_MAX_TOKENS`, `PLANNER_MAX_WORDS`,
  `PLAN_MAX_OPERATIONS`.

### Protection against hallucination (each one has a test with a *wrong* draft)
Invented value ("High" → "P1-Critical"), invented field, invented or mistyped ID, a person's invented surname,
a date the model computed itself, a read-only field, and a draft that drops a record the user named all end in
**no write** and a fallback to the tool agent. Values and dates must be in the user's message; services still
validate everything (`allowed` values, people, months with no roster).

### Token cost
Planner: ~2.1 K characters (~530 tokens) once per multi-part write (prompt 1,243 + schema 870). The roster tool
agent: ~6.3 K characters per call, one call per person plus the answer. Confirm, cancel and every answer to a
question: 0 tokens. Single clear updates stay on the 0-token fast path (asserted in tests and in `evaluate.py`).

### Verified / not yet verified
* Verified offline: 542 tests, 35 evaluation cases; the evaluator itself was checked to *fail* on wrong
  expectations (it exits 1 and says why).
* **Not verified on real Bedrock**: how accurately the model fills `PlanDraft`, whether structured output works
  with the configured model/region, and the real token and latency numbers. Run `python dev/evaluate.py --live`
  (uses in-memory sample data; only the planner call reaches Bedrock).

### Follow-up (same day): "update their priority as HIGH for both of them"
A second transcript: the user asked for "the two new incidents", the agent listed them, and then
*"update their priority as HIGH for both of them"* got **"Which incident would you like to update?"**.

| # | Cause | Fix |
| --- | --- | --- |
| 1 | The session remembered **one** record per type. A list of two set no context, so "them" had nothing to point at | `SessionState.last_list`: filled from the deterministic lists and from the tool agent's multi-row results (`absorb_tool_result`); also in the model's context line and in the persisted snapshot |
| 2 | My own guard looked for "both" only *before* the value; here it came after ("as HIGH **for both of them**"), so the old one-field path still claimed the message | `_multi_change` also checks the tail of the value and plural words ("them", "their", "both of", "the two", "all the incidents"); a bare "both" (as in "both priority and urgency") deliberately does not count |
| 3 | A single focused record could have stood in for "them" and silently changed one of two | `_default_targets`: "them" = the last list only; none recent -> "Which incidents do you mean?"; the router's single `entity_ref` is ignored for plural messages |
| 4 | When only one of the named records needed a change, the answer omitted the other | the reply (and the preview) says "INC… already has those values" |
| 5 | The first message, "give me two new incidents", was read as *create* by the tool agent | one line in the system prompt (a READ, create only if asked); the prompt was tightened elsewhere to stay under its 1,500-character budget. Not verified on the real model |

Because the targets come from context rather than from typed IDs, this case always shows the preview ("These are
the records from the list I just showed") before writing. Tests: `ListReferenceTests`, `ListMemoryTests`,
`PluralRoutingTests`; four evaluation cases.

### Limits and open items
* "The two newest incidents" is still answered by the tool agent. A deterministic "latest N" would need a scan
  ordered by creation time (the current `_query` scans the first `SEARCH_SCAN_CAP` items, not the newest), so it
  waits for a date index (GSI). Check the tool agent's answer when recency matters.
* Gating is by write verbs: "Rohit and Priya will be off on 2nd August" has no verb, so it goes to the tool
  agent (which still works, it just asks more). Add verbs, or widen the gate, if `domain=all` logs show misses.
* Team-wide writes ("set the whole team unavailable"), delete of several records, and mixed read+write messages
  still use the tool agent.
* A year the user did not give is assumed to be the current one and shown in the preview.
* Dry-run plus write reads each record twice (cheap `get_item`s); batch size is capped at 20 operations.

---

## 6. Process lessons

* **Assumed schemas are the biggest risk.** Verify against real rows early (`dev/check_aws.py` exists
  for this).
* **Every model call pays for the whole prompt + tool list.** Adding a tool is a recurring cost;
  measure with `--meta`.
* **Never let tests reach AWS.** The test package sets fake credentials and a dead endpoint.
* **Shell quoting corrupts regexes.** Patch files with the editor tools or a script file, not inline
  heredocs containing backslashes; scan for control characters afterwards.
* **Keep scratch files out of the repo and out of OneDrive.** A patch script was once written to a
  wrong relative path; the stray folder was removed.
* **Keep deployables clean.** A folder that gets pushed must not contain tests, tools, docs or secrets.
* **Blocking calls in `async def` stall everything.** Run boto3 in a thread.
* **A router that half-understands is worse than one that declines.** Claiming a message and acting on the part
  it parsed (first field, first ID) produced both the question loop and silent partial changes. Check that the
  whole message is accounted for, or hand it on.
* **A clarification must keep what it already knows.** Store the partly filled request and fill the missing slot
  from the answer; never re-parse the answer as a fresh sentence.
* **Never trust a model's extraction: ground it.** Every value, ID, name and date it returns must be in the
  user's message, and every record the user named must be covered.
* **Reproduce a reported conversation with the real code before designing a fix.** Running the router on the
  three messages showed the cause in minutes.
* **Old docs drift.** `CLAUDE.MD` / `ARCHITECTURE.md` described V1 after V2 shipped; they are now
  rewritten and must be updated with every architectural change.

---

## 7. Open questions and pending decisions

| # | Question | Why it matters | Current behaviour |
| --- | --- | --- | --- |
| 1 | Category-matrix `team_id` ("Identity Operations") vs roster `team_id` ("Access Management"): same teams under different names? | "Who should handle X?" cannot reach the roster if names differ | Reports the team is not in the roster instead of guessing; needs a shared naming or a mapping |
| 2 | "Delete the roster entry" — one month or all of an employee's rows? | Destructive | Deletes **one monthly row** (given month, else this month, else the only row, else asks) |
| 3 | Are `<br>` / `&#xA;` in your sample data real or export artifacts? | Display cleanliness | Not cleaned; will strip at read time if real |
| 4 | Is `UID` text or a number in DynamoDB? | Key type on reads/writes/creates | Code keeps whatever type it reads back and creates new UIDs in the same type; `check_aws.py` shows the real type |
| 5 | Where does the agent container run — EC2 / ECS / AgentCore Runtime only? | Session/identity/memory behaviour differ | Docs assume AgentCore Runtime; ECS would host the frontend/API in front of it |
| 6 | Prompt caching active on your model? | Biggest remaining token lever | Unverified; check `cache_read` in `--meta` |

---

## 8. Known limits and next steps

* Direct routing is intentionally narrow and English-only; everything else goes to the model.
* Session state is in process memory; it survives a runtime restart only if an LLM turn mirrored it to
  AgentCore Memory.
* Searches still scan whole tables (filtered server-side where possible). Add GSIs (date, team, state,
  priority, breach flag) when tables grow.
* No separate cheaper model for routing/extraction yet (would cut cost further; needs accuracy checks).
* Category-matrix create and issue delete are deliberately not implemented.
* Writes run without a confirmation step (by requirement); mitigations are validation, read-only
  computed fields, dedupe, a tool-call budget, audit logs, `READ_ONLY_MODE` and IAM scoping.
