# CLAUDE.md — DTP Operate Agent (Version 2)

Instructions for Claude Code working in this repository. Read [ARCHITECTURE.md](ARCHITECTURE.md) for how
the system works and [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md) for why it is this way (and which mistakes
not to repeat).

## 1. What this is

A production conversational ITSM operations agent (incidents/requests, Category Matrix issues, roster and
availability, Knowledge Base search) on Python + Strands Agents + Amazon Bedrock + **AgentCore Runtime** +
AgentCore Memory + DynamoDB. Code lives in `app/dtp_operate_agent/`. It works against **real AWS data**:
treat every change as production-affecting.

## 2. Commands (run from `local_dev/`)

```bash
cd local_dev
python -m unittest discover -s tests -t . -v     # 395 offline tests — run after every change
python dev/chat.py --demo --meta                  # try conversations with in-memory sample data, no AWS
python dev/check_aws.py                           # read-only preflight against real AWS
python dev/chat.py --read-only --meta             # real AWS, nothing can be written
python dev/run_local.py                           # host like AgentCore (/ping, /invocations on :8080)
```

The agent's `ENVIRONMENT` defaults to `production`; the dev tools set `local` themselves. Real-table
testing: set `READ_ONLY_MODE=true` (in `local_dev/.env`), or point the three `*_TABLE_NAME` variables at
restored copies. Never test writes on live tables first. Never commit or print `.env` values.

## 3. Layout

```text
app/dtp_operate_agent/   THE DEPLOYABLE FOLDER — the only thing pushed to AgentCore Runtime
  main.py                transport: payload, identity, sessions, logging
  agent.py               process_user_query: route → direct | model; prompt; Bedrock model + caching
  router.py              deterministic Intent from text + session state
  actions.py             direct execution of intents (0 model tokens); None = "use the model"
  domains.py             which tool group a model request gets (roster/incident/category/handlers/all)
  hooks.py               Strands hooks: duplicate refusal, tool budget, state updates
  context.py             focus_* helpers, absorb_tool_result
  responses.py           Reply: Markdown `response` + machine-readable `structured`
  presentation.py        sentences, entity cards (badges/sections), follow-up suggestions (0 tokens)
  memory/                state.py (SessionState), store.py (SessionStore), session.py (AgentCore Memory)
  services/              ALL AWS access, validation, error mapping (common, ids, schema, *_service)
  tools/                 thin typed wrappers over services; DOMAIN_TOOLS in tools/__init__.py
  config.py              environment configuration
  pyproject.toml, Dockerfile, .dockerignore

local_dev/               NEVER DEPLOYED: tests/ (fakes.py, scripted_model.py, test_*.py), dev/ tools,
                         .env, README.md, FRONTEND_CONTRACT.md
integration_reference/   mirror of the separate frontend/backend files changed for this agent
```

Keep the deployable folder free of tests, dev tools, `.env` and docs; tests reach the app through a path
set in `local_dev/tests/__init__.py`.

Layering: `main.py` (transport) → `agent.py` (orchestration) → `router.py`/`actions.py`/`domains.py` →
`services/` (the only code that touches AWS) ← `tools/` (model-facing wrappers). Ask of every change:
*which layer owns this?* Do not put DynamoDB calls in `main.py`, `agent.py`, `router.py` or tools.

## 4. Golden rules (do not break these)

1. **Session isolation.** State is keyed by `(actor_id, session_id)`. Never share an agent, state or
   memory session across pairs. Non-local requests without a session are rejected; a missing actor becomes
   `anon-<session>`. Never return raw exceptions to callers.
2. **Deterministic first, conservative.** Only claim a request in `router.py` when target and intent are
   unambiguous. Reasoning words (why/how/compare/similar/…), multi-part or multi-change messages go to the
   model. Do not encode the application in regexes.
3. **Context is not a rewrite.** References ("it", "his", "that team") are resolved from `SessionState`;
   explicit IDs win; a mutation may only use context from the last `MUTATION_CONTEXT_MAX_TURNS` (8) turns.
4. **Services own correctness.** Validation, value mapping ("P2" → "P2-High"), read-only computed fields,
   conditional writes, audit logging and AWS-error mapping happen in `services/`, never in prompts.
   Service methods return `{"ok": …}` dicts and never raise AWS errors (`guarded`).
5. **Writes are direct but safe.** Clear requests execute without a confirmation step (product decision);
   ask only for missing/ambiguous information. Keep `ensure_writable()` (`READ_ONLY_MODE`) on every write,
   and keep writes in the column's existing format (`yes_no`, `same_type`, table date formats).
6. **Tokens are a budget.** Every model call re-sends the system prompt and the tool definitions.
   * Adding a tool or lengthening a description is a recurring cost: keep descriptions short; put the tool
     in the right `DOMAIN_TOOLS` group(s); check `local_dev/tests/test_token_budget.py`.
   * Tools return only what is needed (`fields=`, compact JSON, columnar lists, no availability maps).
   * Do not replay conversation history to the model; extend `SessionState` instead.
   * Prefer a deterministic route for structured, frequent questions.
7. **Real schemas, not assumptions.** See §5. Verify with `dev/check_aws.py` before changing data logic.
8. **Response contract.** `invoke` returns `{"response": "<markdown>", "structured": {"type": …}}`; keep
   `response` complete and working on its own. `structured` follows `local_dev/FRONTEND_CONTRACT.md`
   (types, `tone`, `suggestions`); change that document with any change to it. Every suggestion chip must be
   something the router handles (`local_dev/tests/test_presentation.py` taps them all). Token/route metadata is logged,
   never returned.
9. **No AWS at import, no secrets in code.** Services and agents are created lazily; use environment
   variables and the AWS role. Do not log prompts or credentials.
10. **Tests stay offline.** `local_dev/tests/__init__.py` sets fake credentials and a dead endpoint so an accidental
    AWS call fails fast. Use `tests/fakes.py` and `tests/scripted_model.py`.

## 5. Data facts (verified against real rows)

* **Incident** `ticket_id` (INC/REQ, 7 digits); timestamps `YYYY-MM-DD HH:MM`; breach flags text
  `TRUE`/`FALSE`; assignee/opener are e-mails; `team_id` holds a team **name**.
* **Category matrix** `issue_id` (`Issue-N`); `active` `Yes`/`No`; dates `YYYY-MM-DD`; `team_id` = team name.
* **Roster** `UID` unique **per row**; **one row per employee per month** (`year`, `month`); `emp_id` =
  employee number; **`employee_id` = e-mail**; `days_availability` = `{"1": "Y", "2": "N"}` keyed by
  **day of month**. A person = their monthly rows = `emp:<emp_id>`. Missing month ⇒ "no roster", not "N".
* Category-matrix and roster team names may differ ("Identity Operations" vs "Access Management"); never
  guess a mapping — report it (open question in the log).
* Roster delete removes **one monthly row**; issues cannot be deleted.

## 6. How to make common changes

| Change | Touch | Also |
| --- | --- | --- |
| New field on a record | `services/schema.py` (lists, aliases, labels), the service's allow-list | tests in `test_services.py` |
| New deterministic question | `router.py` (Intent) → `actions.py` (handler) | conversation test; router test incl. negatives |
| New tool | `tools/*_tools.py`, `tools/__init__.py` (`_every_tool` + `DOMAIN_TOOLS`), `MUTATING_TOOL_NAMES` if it writes | update tool count in tests; run `test_token_budget.py`; short docstring |
| New domain keyword | `domains.py` | `DomainClassificationTests` |
| New setting | `config.py` + README table | test if behaviour changes |
| Change response shape | `responses.py` | keep `response` Markdown; update frontend contract notes |
| Architectural change | code + tests | **update ARCHITECTURE.md and append to DEVELOPMENT_LOG.md in the same change** |

Before a significant change: read the relevant module and its tests, check existing tool signatures (never
assume an argument from a name), consider session/memory and frontend-contract impact, prefer incremental
change, and explain trade-offs before large refactors.

## 7. Testing priorities

Any routing change must cover: ID spellings (`Issue-9`/`Issue9`/`Issue 9`, `INC0000258`/`INC-0000258`/
`INC 0000258`/`incident 258`, `REQ…`), pronoun follow-ups on the right entity type, clarification flows
(update without target / field / value), ambiguity (several employees), stale context (no mutation),
reasoning/multi-change going to the model, and **negative cases** that must *not* be taken. Also test
session isolation (actor A/B × session 1/2), AWS failure → safe message, and duplicate-call/loop limits.

## 8. Environment and platform pitfalls

* Windows: the shell is Git Bash/PowerShell. **Do not patch files with inline heredocs that contain
  backslashes** (a `\b` was once turned into a backspace character and silently broke a regex). Use the
  editor tools or a script file, and scan for control characters afterwards.
* Do not write scratch files inside the repo or OneDrive; use the session scratchpad. Remove generated
  `__pycache__` directories when finishing (the root `.gitignore` does not cover them).
* Strands specifics learned the hard way: tool definitions include filler text that `tools/__init__.py`
  strips; `tokens_in` excludes cached tokens; each domain agent needs its own `agent_id`; hooks can raise
  to hard-stop a loop.

## 9. Do not

* Reintroduce the supervisor-plus-specialists design without measuring token cost and accuracy.
* Add a confirmation step to updates/deletes (product decision) — add validation instead.
* Send the whole conversation, whole records, or availability maps to the model.
* Silently change data formats, key types or tool contracts consumed elsewhere.
* Restructure the repository for aesthetics. The system is deployed; improve incrementally and
  evidence-first.

## 10. Goal

Predictability, low latency, low token use, correct tool execution, good session behaviour, isolation,
maintainability and production reliability — in that spirit, not maximum abstraction. Keep this file,
ARCHITECTURE.md and DEVELOPMENT_LOG.md accurate; stale docs already caused one round of misdirected work.
