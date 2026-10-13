# Frontend contract

What the agent sends and how a chat UI should render it. A working implementation for your React app is
[../integration_reference/frontend/src/pages/AIAgent.jsx](../integration_reference/frontend/src/pages/AIAgent.jsx).

## 1. Envelope

The runtime returns one JSON object per user message:

```json
{ "response": "<markdown>", "structured": { "type": "...", "text": "<same markdown>", "tone": "info" } }
```

* `response` is always complete Markdown. A minimal client can show only this.
* `structured` lets a richer client render cards, badges and buttons. `structured.text === response`.
* On a bad request the object is `{"error": "Invalid request", "message": "..."}` (or
  `"Agent execution failed"`); show `message`.
* Send the authenticated user as `actor_id` and keep one runtime session ID per chat (see README).

Suggested WebSocket wrapper (your backend adds it; the agent does not care):

```json
{ "type": "agent_message", "message_id": "…", "session_id": "…", "payload": { "response": "…", "structured": { … } } }
```

## 2. Types

Every `structured` has `type`, `text` and `tone` (`info | success | warning | error`). Any of them may
also have `suggestions`. Unknown types: render `text` as Markdown.

| type | When | Key fields |
| --- | --- | --- |
| `text` | Free-form answers (mostly from the language model) | `text` (Markdown), `format: "markdown"` |
| `record` | An incident / issue / employee, or several fields of one | `title`, `subtitle`, `badges[]`, `summary`, `sections[]`, `fields[]`, `link`, `entity` |
| `field` | One fact ("Who is assigned to it?") | `text` (a sentence), `label`, `value`, `entity` |
| `table` | Lists (SLA breaches, incidents on a date, team availability) | `title`, `summary?`, `columns[]`, `rows[]` (objects keyed by column), `shown`, `total`, `footer` |
| `list` | Simple bullet list (categories) | `title`, `items[]` |
| `update_confirmation` | After a create/update/delete | `entity`, `changes[] {label, from, to}` or `message`, `operation?`. After a **batch** (several records): `message` (one line), `items[] {kind, id, label, changes[]}`, `failed[] {label, message}`; `text` always holds the complete list |
| `question` | The agent needs an answer | `question`, `options[] {label, send}` (optional), `clarification: true`. A **preview** of a multi-part change also has `confirmation: true`, `plan[] {kind, id, label, changes[]}`, `notes[]`; its options are **Confirm** (`send: "yes"`) and **Cancel** (`send: "cancel"`), and `question` already contains the whole preview |
| `error` | Something failed (message is safe to show) | `message` |

Shared shapes:

```json
"entity":  { "kind": "incident | issue | employee", "id": "INC0000258" }
"badges":  [ { "label": "P1-Critical", "tone": "critical | warning | info | success | neutral" } ]
"sections":[ { "title": "People", "items": [ { "field": "assigned_to", "label": "Assigned to", "value": "…" } ] } ]
"link":    { "label": "Open in ITSM", "url": "https://…" }          // or null
"suggestions": [ { "label": "Is it breached?", "send": "Is it breached?" } ]
```

### Example: `record` (incident)

```json
{
  "response": "Here's **INC0000258** — Database Failure - Database. It's **P1-Critical** and **Closed**, assigned to varun.nambiar@abc.com.\n\n- Short description: …",
  "structured": {
    "type": "record", "tone": "info",
    "title": "INC0000258", "subtitle": "Database Failure - Database",
    "entity": { "kind": "incident", "id": "INC0000258" },
    "badges": [ { "label": "P1-Critical", "tone": "critical" }, { "label": "Closed", "tone": "success" } ],
    "summary": "Here's **INC0000258** — …",
    "sections": [ { "title": "People", "items": [ { "field": "assigned_to", "label": "Assigned to", "value": "varun.nambiar@abc.com" } ] }, "…" ],
    "link": { "label": "Open in ITSM", "url": "https://abc.com/itsm/ticket/INC0000258" },
    "suggestions": [ { "label": "Who is assigned to it?", "send": "Who is assigned to it?" }, "…" ]
  }
}
```

## 3. Rendering rules

1. **Cards for `record`**: title + subtitle + badges on top, the `summary` sentence, then `sections`
   as a responsive grid (skip nothing; empty sections are never sent). Long values get their own line.
2. **Badges**: colour by `tone` (`critical`/`error` red, `warning` amber, `success` green, `info` blue,
   `neutral` grey). In tables, colour recognisable cell values the same way (Available / Not available,
   priorities, states).
3. **`suggestions`** are chips under the message. Tapping one **sends `send` as the user's next message**
   (show `label` as the user bubble). They are always things the agent understands in the current context.
4. **`question.options`**: buttons; tapping sends `send` (the option number). Also allow typing an answer.
5. **`update_confirmation`**: success styling, show `from → to` per change. For a batch, list `items`
   (warning styling when `failed` is not empty); a client that ignores `items` shows `text`, which has it all.
   A **preview** (`question` with `confirmation: true`) needs nothing special: render it as any question with
   its two option buttons (a client that supports `plan` may lay it out as a table of `from → to`).
6. **`error`**: red styling, no technical detail (none is sent).
7. **Markdown subset** in `text`: `**bold**`, `_italic_`, `` `code` ``, `- ` bullets, `1. ` lists, blank-line
   paragraphs. No headings, tables or HTML. Always escape text before inserting into the page.
8. **Typing indicator** while waiting; disable input while a message is in flight.
9. **Context is server-side**: do not resend history. Use one session ID per chat; a new chat = new ID.

## 4. Conversational behaviour to expect

* Lookups answer in a sentence first (`INC0000258 is assigned to varun…`), with a card for full records.
* Follow-ups ("it", "his", "that team") work within a session; suggestions steer users to them.
* When information is missing the agent asks one short question (with buttons when the choices are known).
* A single, explicit update or delete runs immediately and is confirmed briefly (`Updated INC0000258` +
  `from → to`); that includes several fields on that one record ("set priority and urgency to High").
* A request that changes **several records, people or days**, or relies on something the user did not state
  (a year, a past date), gets a **preview with Confirm / Cancel** first. Nothing is written until the user
  taps Confirm (or types "yes"); anything else drops the preview.
* When something is missing the agent asks **one specific question** (buttons when the choices are known) and
  keeps what it already understood, so the answer completes the request instead of starting over.

## 5. Not yet available

* **Token streaming / progress messages** ("Checking the roster…") for model-generated answers; the
  contract above is one message per turn. Adding an event stream (`status`, `delta`, `final`) is the
  next step if a typing effect is wanted.
