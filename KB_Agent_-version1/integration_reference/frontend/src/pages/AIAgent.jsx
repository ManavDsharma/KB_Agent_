import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Bot, CheckCircle2, ExternalLink, RotateCcw, Send, User } from "lucide-react";
import Topbar from "../components/layout/Topbar";
import { createAgentSocket } from "../services/agentSocket";

const INTRO_SUGGESTIONS = [
  "List SLA breaches",
  "Show incidents created today",
  "List all categories",
];

function newSessionId() {
  return crypto.randomUUID();
}

function introMessage() {
  return {
    id: "intro",
    role: "agent",
    content: null, // rendered specially below
  };
}

let messageId = 0;

/* ------------------------------------------------------------------ */
/* Small presentation helpers                                          */
/* ------------------------------------------------------------------ */

// Tone -> badge colours. The agent sends a tone with every badge and message.
const BADGE_TONE = {
  critical: "bg-red-100 text-red-700",
  error: "bg-red-100 text-red-700",
  warning: "bg-amber-100 text-amber-700",
  success: "bg-green-100 text-green-700",
  info: "bg-brand-soft text-brand",
  neutral: "bg-surface-muted text-ink-secondary",
};

// Colour recognisable table cells (availability, priority, state).
function cellTone(value) {
  const v = String(value ?? "").toLowerCase();
  if (v === "available") return "success";
  if (v === "not available") return "warning";
  if (v === "no data" || v === "non-working day") return "neutral";
  if (/^p1|critical/.test(v)) return "critical";
  if (/^p2|^high$/.test(v)) return "warning";
  if (/^p3|^medium$/.test(v)) return "info";
  if (/^(closed|resolved)$/.test(v)) return "success";
  if (/^(in progress|new|open)$/.test(v)) return "info";
  return null;
}

function Badge({ label, tone = "neutral" }) {
  return (
    <span
      className={`inline-block whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ${
        BADGE_TONE[tone] || BADGE_TONE.neutral
      }`}
    >
      {label}
    </span>
  );
}

// Inline Markdown the agent uses: **bold** and `code`. Built as React nodes
// (never dangerouslySetInnerHTML), so message text can't inject markup.
function Inline({ text }) {
  const parts = String(text ?? "").split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={i} className="font-semibold">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={i} className="rounded bg-surface px-1 py-0.5 text-xs">
          {part.slice(1, -1)}
        </code>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

// The Markdown subset the agent produces: paragraphs, "- " bullets, "1. " lists.
function Markdown({ text }) {
  const blocks = String(text ?? "").split(/\n{2,}/);
  return (
    <div className="space-y-2">
      {blocks.map((block, i) => {
        const lines = block.split("\n");
        if (lines.every((l) => /^\s*-\s+/.test(l))) {
          return (
            <ul key={i} className="list-disc space-y-0.5 pl-5">
              {lines.map((l, j) => (
                <li key={j}>
                  <Inline text={l.replace(/^\s*-\s+/, "")} />
                </li>
              ))}
            </ul>
          );
        }
        if (lines.every((l) => /^\s*\d+\.\s+/.test(l))) {
          return (
            <ol key={i} className="list-decimal space-y-0.5 pl-5">
              {lines.map((l, j) => (
                <li key={j}>
                  <Inline text={l.replace(/^\s*\d+\.\s+/, "")} />
                </li>
              ))}
            </ol>
          );
        }
        return (
          <p key={i}>
            {lines.map((l, j) => (
              <span key={j}>
                {j > 0 && <br />}
                <Inline text={l} />
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}

function TypingDots() {
  return (
    <div className="flex items-center gap-1 px-1 py-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 animate-pulse rounded-full bg-ink-muted"
          style={{ animationDelay: `${i * 150}ms` }}
        />
      ))}
    </div>
  );
}

function AgentAvatar() {
  return (
    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand">
      <Bot size={16} strokeWidth={1.75} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* One renderer per structured.type (see the agent's response contract) */
/* ------------------------------------------------------------------ */

const BUBBLE = "rounded-lg rounded-tl-none border border-surface-border bg-surface-muted px-4 py-3 text-sm text-ink";

function TextBubble({ structured, content }) {
  return (
    <div className={`max-w-xl ${BUBBLE}`}>
      <Markdown text={structured?.text ?? content} />
    </div>
  );
}

function FieldBubble({ structured }) {
  const accent =
    structured.tone === "success"
      ? "border-l-4 border-l-green-500"
      : structured.tone === "warning"
        ? "border-l-4 border-l-amber-500"
        : "";
  return (
    <div className={`max-w-xl ${BUBBLE} ${accent}`}>
      <Markdown text={structured.text} />
    </div>
  );
}

function ErrorBubble({ structured, content }) {
  return (
    <div className="flex max-w-xl gap-2 rounded-lg rounded-tl-none border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      <AlertTriangle size={16} strokeWidth={1.75} className="mt-0.5 shrink-0" />
      <span>{structured?.message ?? content}</span>
    </div>
  );
}

function ListCard({ structured }) {
  return (
    <div className={`max-w-xl ${BUBBLE}`}>
      <p className="mb-1 font-semibold">{structured.title}</p>
      <ul className="list-disc space-y-0.5 pl-5">
        {structured.items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function UpdateConfirmation({ structured }) {
  const changes = structured.changes ?? [];
  return (
    <div className="max-w-xl rounded-lg rounded-tl-none border border-green-200 bg-green-50 px-4 py-3 text-sm text-ink">
      {changes.length ? (
        <>
          <p className="flex items-center gap-1.5 font-semibold text-green-800">
            <CheckCircle2 size={16} strokeWidth={1.75} />
            Updated {structured.entity?.id}
          </p>
          <ul className="mt-2 space-y-1">
            {changes.map((c) => (
              <li key={c.field} className="flex flex-wrap items-center gap-2">
                <span className="w-32 text-ink-secondary">{c.label}</span>
                <span className="text-ink-muted line-through">{String(c.from ?? "—")}</span>
                <span className="text-ink-muted">→</span>
                <Badge label={String(c.to ?? "—")} tone="success" />
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="flex items-center gap-1.5 text-green-800">
          <CheckCircle2 size={16} strokeWidth={1.75} />
          <Inline text={structured.text} />
        </p>
      )}
    </div>
  );
}

function RecordCard({ structured }) {
  return (
    <div className="w-full max-w-2xl overflow-hidden rounded-lg rounded-tl-none border border-surface-border bg-surface text-sm text-ink">
      <div className="space-y-1 px-4 pb-2 pt-3">
        <p className="text-base font-semibold">{structured.title}</p>
        {structured.subtitle && <p className="text-ink-secondary">{structured.subtitle}</p>}
        {structured.badges?.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {structured.badges.map((b) => (
              <Badge key={b.label} label={b.label} tone={b.tone} />
            ))}
          </div>
        )}
      </div>

      {structured.summary && (
        <div className="px-4 pb-3">
          <Markdown text={structured.summary} />
        </div>
      )}

      <div className="grid grid-cols-1 border-t border-surface-border sm:grid-cols-2">
        {structured.sections?.map((section, i) => (
          <div key={i} className="border-b border-surface-border px-4 py-3 sm:odd:border-r">
            {section.title && (
              <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-muted">
                {section.title}
              </p>
            )}
            <dl className="space-y-1.5">
              {section.items.map((item) => {
                const value = String(item.value);
                const tone = cellTone(value);
                const long = value.length > 22;
                return (
                  <div key={item.label} className={long ? "" : "flex items-baseline justify-between gap-3"}>
                    <dt className="text-ink-secondary">{item.label}</dt>
                    <dd className={`break-words ${long ? "mt-0.5" : "text-right font-medium"}`}>
                      {tone ? <Badge label={value} tone={tone} /> : value}
                    </dd>
                  </div>
                );
              })}
            </dl>
          </div>
        ))}
      </div>

      {structured.link?.url && (
        <div className="px-4 py-2.5">
          <a
            href={structured.link.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs font-medium text-brand hover:underline"
          >
            {structured.link.label}
            <ExternalLink size={12} strokeWidth={1.75} />
          </a>
        </div>
      )}
    </div>
  );
}

function TableCard({ structured }) {
  return (
    <div className="w-full max-w-2xl overflow-hidden rounded-lg rounded-tl-none border border-surface-border bg-surface text-sm text-ink">
      <div className="px-4 pb-2 pt-3">
        {structured.summary ? <Markdown text={structured.summary} /> : <p className="font-semibold">{structured.title}</p>}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr className="border-y border-surface-border text-[11px] uppercase tracking-wide text-ink-muted">
              {structured.columns.map((c) => (
                <th key={c} className="whitespace-nowrap px-4 py-2 font-semibold">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {structured.rows.map((row, i) => (
              <tr key={i} className="border-b border-surface-border last:border-0">
                {structured.columns.map((c) => {
                  const value = row[c] ?? "—";
                  const tone = cellTone(value);
                  return (
                    <td key={c} className="px-4 py-2 align-top">
                      {tone ? <Badge label={String(value)} tone={tone} /> : String(value)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {structured.total > structured.shown && (
        <p className="px-4 py-2 text-xs text-ink-muted">
          Showing {structured.shown} of {structured.total}
        </p>
      )}
    </div>
  );
}

function QuestionBubble({ structured, onSend, disabled }) {
  return (
    <div className={`max-w-xl ${BUBBLE}`}>
      <Markdown text={structured.question ?? structured.text} />
      {structured.options?.length > 0 && (
        <div className="mt-3 flex flex-col gap-1.5">
          {structured.options.map((option) => (
            <button
              key={option.send}
              type="button"
              disabled={disabled}
              onClick={() => onSend(option.send, option.label)}
              className="rounded-md border border-surface-border bg-surface px-3 py-2 text-left text-sm text-ink transition-colors hover:border-brand hover:text-brand disabled:opacity-50"
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Picks the renderer for one agent message. Unknown types (or a reply with no
// `structured` at all, e.g. an older agent) fall back to the Markdown text.
function AgentContent({ message, onSend, disabled }) {
  const s = message.structured;
  switch (s?.type) {
    case "record":
      return <RecordCard structured={s} />;
    case "table":
      return <TableCard structured={s} />;
    case "field":
      return <FieldBubble structured={s} />;
    case "list":
      return <ListCard structured={s} />;
    case "update_confirmation":
      return <UpdateConfirmation structured={s} />;
    case "question":
      return <QuestionBubble structured={s} onSend={onSend} disabled={disabled} />;
    case "error":
      return <ErrorBubble structured={s} content={message.content} />;
    default:
      return <TextBubble structured={s} content={message.content} />;
  }
}

// Tap-to-send follow-ups the agent suggests for its latest answer.
function Suggestions({ items, onSend, disabled }) {
  if (!items?.length) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <button
          key={item.send}
          type="button"
          disabled={disabled}
          onClick={() => onSend(item.send, item.label)}
          className="rounded-full border border-surface-border bg-surface px-3.5 py-1.5 text-xs font-medium text-ink-secondary transition-colors hover:border-brand hover:text-brand disabled:opacity-50"
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function AIAgentPage() {
  const [messages, setMessages] = useState([introMessage()]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [sessionId, setSessionId] = useState(newSessionId);

  const socketRef = useRef(null);
  const threadEndRef = useRef(null);

  useEffect(() => {
    const socket = createAgentSocket({
      onOpen: () => setConnected(true),
      onClose: () => setConnected(false),
      onError: () => setConnected(false),
      onMessage: (data) => {
        if (data.type === "error") {
          setWaiting(false);
          setMessages((prev) => [
            ...prev,
            {
              id: ++messageId,
              role: "agent",
              content: data.message,
              structured: { type: "error", message: data.message },
              source: "fallback",
            },
          ]);
          return;
        }
        if (data.type !== "response") return;
        setWaiting(false);
        setMessages((prev) => [
          ...prev,
          {
            id: ++messageId,
            role: "agent",
            content: data.response,
            structured: data.structured,
            source: data.source,
          },
        ]);
      },
    });
    socketRef.current = socket;
    return () => socket.close();
  }, [sessionId]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, waiting]);

  // Follow-up chips belong to the newest agent message only.
  const lastMessage = messages[messages.length - 1];
  const chipSource = useMemo(() => {
    if (lastMessage?.role !== "agent") return null;
    return lastMessage.id === "intro" ? INTRO_SUGGESTIONS.map((s) => ({ label: s, send: s })) : lastMessage.structured?.suggestions;
  }, [lastMessage]);

  // `prompt` is what is sent to the agent; `label` (optional) is what the user
  // sees in their own bubble, e.g. when they tap an option or a suggestion.
  const sendPrompt = (prompt, label) => {
    const text = prompt.trim();
    if (!text) return;

    setMessages((prev) => [...prev, { id: ++messageId, role: "user", content: label ?? text }]);
    setInput("");

    const sent = socketRef.current?.sendPrompt(text, sessionId);
    if (!sent) {
      setMessages((prev) => [
        ...prev,
        {
          id: ++messageId,
          role: "agent",
          content: "I'm not connected right now — try again in a moment.",
          structured: { type: "error", message: "I'm not connected right now — try again in a moment." },
          source: "fallback",
        },
      ]);
      return;
    }
    setWaiting(true);
  };

  const handleReset = () => {
    setMessages([introMessage()]);
    setWaiting(false);
    setSessionId(newSessionId());
  };

  return (
    <>
      <Topbar
        eyebrow="Support Ops"
        title="AI Agent"
        action={
          <span
            className={`rounded-full px-3 py-1 text-xs font-medium ${
              connected ? "bg-brand-soft text-brand" : "bg-surface-muted text-ink-muted"
            }`}
          >
            {connected ? "dtp_agent" : "Offline"}
          </span>
        }
      />

      <main className="flex flex-1 flex-col overflow-hidden bg-surface">
        <div className="flex items-center justify-between border-b border-surface-border px-8 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-brand-soft text-brand">
              <Bot size={18} strokeWidth={1.75} />
            </div>
            <div>
              <p className="text-sm font-semibold text-ink">AI Support Agent</p>
              <p className="text-xs text-ink-muted">
                {connected ? "Connected" : "Disconnected — reconnecting…"}
              </p>
            </div>
          </div>
          <button
            onClick={handleReset}
            className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink"
            title="Reset conversation"
          >
            <RotateCcw size={14} strokeWidth={1.75} />
            Reset
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-auto px-8 py-6">
          {messages.map((m) =>
            m.id === "intro" ? (
              <div key="intro" className="flex gap-3">
                <AgentAvatar />
                <div className="max-w-xl rounded-lg rounded-tl-none border border-surface-border bg-surface-muted px-4 py-3 text-sm text-ink">
                  <p>Hello! I&apos;m your DTP operations assistant.</p>
                  <p className="mt-2">
                    I can look up and update <strong>incidents</strong> and requests, check{" "}
                    <strong>category-matrix issues</strong>, and tell you who is on shift or available. How can I
                    assist you today?
                  </p>
                </div>
              </div>
            ) : m.role === "user" ? (
              <div key={m.id} className="flex flex-row-reverse gap-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-muted text-ink-secondary">
                  <User size={16} strokeWidth={1.75} />
                </div>
                <div className="max-w-xl rounded-lg rounded-tr-none bg-brand px-4 py-3 text-sm text-white">
                  {m.content}
                </div>
              </div>
            ) : (
              <div key={m.id} className="flex gap-3">
                <AgentAvatar />
                <div className="min-w-0 flex-1">
                  <AgentContent message={m} onSend={sendPrompt} disabled={waiting || !connected || m !== lastMessage} />
                </div>
              </div>
            ),
          )}

          {waiting && (
            <div className="flex gap-3">
              <AgentAvatar />
              <div className="rounded-lg rounded-tl-none border border-surface-border bg-surface-muted px-3 py-2">
                <TypingDots />
              </div>
            </div>
          )}

          {!waiting && (
            <div className="ml-11">
              <Suggestions items={chipSource} onSend={sendPrompt} disabled={!connected} />
            </div>
          )}

          <div ref={threadEndRef} />
        </div>

        <div className="border-t border-surface-border px-8 py-4">
          <form
            className="flex items-center gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              sendPrompt(input);
            }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={connected ? "Type a message..." : "Connecting to agent..."}
              className="flex-1 rounded-md border border-surface-border bg-surface px-3.5 py-2.5 text-sm text-ink placeholder:text-ink-muted outline-none focus:border-brand"
            />
            <button
              type="submit"
              disabled={!input.trim() || waiting}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-brand text-white transition-colors hover:bg-brand-hover disabled:opacity-50"
              aria-label="Send"
            >
              <Send size={16} strokeWidth={1.75} />
            </button>
          </form>
        </div>
      </main>
    </>
  );
}
