import { useEffect, useRef, useState, type FormEvent } from "react";
import type { ChatMessage } from "../types";

const LABELS: Record<string, string> = {
  order_status: "Order status",
  return_request: "Return",
  inventory: "Inventory",
  policy_question: "Policy (RAG)",
  escalation: "Escalated",
  guardrail: "Guardrail",
  out_of_scope: "Out of scope",
  greeting: "Greeting",
};

interface Props {
  messages: ChatMessage[];
  busy: boolean;
  error: string | null;
  onSend: (text: string) => void;
  onSelect: (index: number) => void;
  selected: number | null;
}

export function ChatPanel({ messages, busy, error, onSend, onSelect, selected }: Props) {
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const value = text.trim();
    if (!value || busy) return;
    onSend(value);
    setText("");
  };

  return (
    <section className="chat" aria-label="Chat with the support agent">
      <div className="messages">
        {messages.length === 0 && (
          <div className="empty">
            <h2>Ask about an order, a return, stock or store policy</h2>
            <p>Pick a scenario on the right, or type your own question. All customers and orders are synthetic.</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div
              className={`bubble ${m.meta ? "clickable" : ""} ${selected === i ? "selected" : ""}`}
              onClick={() => m.meta && onSelect(i)}
              role={m.meta ? "button" : undefined}
              tabIndex={m.meta ? 0 : undefined}
              onKeyDown={(e) => m.meta && e.key === "Enter" && onSelect(i)}
            >
              <p>{m.content}</p>
              {m.meta && (
                <div className="chips">
                  <span className={`chip intent-${m.meta.intent}`}>{LABELS[m.meta.intent] ?? m.meta.intent}</span>
                  {m.meta.tool_calls.map((t, j) => (
                    <span key={j} className={`chip tool ${t.ok ? "ok" : "fail"}`}>
                      {t.name} {t.ok ? "✓" : "✗"}
                    </span>
                  ))}
                  {m.meta.refused && <span className="chip warn">Refused · {m.meta.guardrail}</span>}
                  {m.meta.ticket_id && <span className="chip esc">Ticket {m.meta.ticket_id}</span>}
                  <span className="chip muted">{m.meta.latency_ms} ms</span>
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div className="msg assistant">
            <div className="bubble typing" aria-live="polite">Thinking…</div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      {error && <div className="error" role="alert">{error}</div>}
      <form className="composer" onSubmit={submit}>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Type a message, e.g. Where is my order OP-1003?"
          maxLength={1000}
          aria-label="Message"
        />
        <button type="submit" disabled={busy || !text.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
