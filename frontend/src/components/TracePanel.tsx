import type { AgentReply } from "../types";

export function TracePanel({ reply }: { reply: AgentReply | null }) {
  if (!reply) {
    return <p className="hint">Send a message to see how the agent handled it: intent, tool calls, sources and latency. Click any reply to inspect it.</p>;
  }
  return (
    <div className="trace">
      <dl className="facts">
        <div><dt>Intent</dt><dd>{reply.intent}</dd></div>
        <div><dt>Mode</dt><dd>{reply.mode}</dd></div>
        <div><dt>Latency</dt><dd>{reply.latency_ms} ms</dd></div>
        <div><dt>Request</dt><dd className="mono">{reply.request_id}</dd></div>
      </dl>

      {(reply.refused || reply.escalated || reply.error) && (
        <div className="flags">
          {reply.refused && <p className="flag warn">Blocked by guardrail: <b>{reply.guardrail}</b>. No tool was called.</p>}
          {reply.escalated && <p className="flag esc">Escalated to a human agent · ticket <b>{reply.ticket_id}</b></p>}
          {reply.error && <p className="flag err">Handled error: {reply.error}</p>}
        </div>
      )}

      <h3>Tool calls ({reply.tool_calls.length})</h3>
      {reply.tool_calls.length === 0 && <p className="hint">No tools were needed for this turn.</p>}
      {reply.tool_calls.map((t, i) => (
        <details key={i} className={`tool-card ${t.ok ? "ok" : "fail"}`} open={i === reply.tool_calls.length - 1}>
          <summary>
            <span className="mono">{t.name}</span>
            <span>{t.ok ? "success" : String(t.result.error ?? "failed")} · {t.latency_ms} ms</span>
          </summary>
          <p className="label">Arguments (personal data masked)</p>
          <pre>{JSON.stringify(t.args, null, 2)}</pre>
          <p className="label">Result</p>
          <pre>{JSON.stringify(t.result, null, 2).slice(0, 1500)}</pre>
        </details>
      ))}

      <h3>Sources</h3>
      {reply.sources.length === 0 ? (
        <p className="hint">No knowledge-base sources were used.</p>
      ) : (
        <ul className="sources">
          {reply.sources.map((s) => (
            <li key={s} className="mono">{s}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
