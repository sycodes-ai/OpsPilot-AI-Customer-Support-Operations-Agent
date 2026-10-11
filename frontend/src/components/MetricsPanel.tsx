import { useEffect, useState } from "react";
import { getMetrics } from "../api";
import type { Metrics } from "../types";

export function MetricsPanel({ refreshKey }: { refreshKey: number }) {
  const [m, setM] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      getMetrics()
        .then((d) => alive && (setM(d), setError(null)))
        .catch((e: Error) => alive && setError(e.message));
    load();
    const id = window.setInterval(load, 15000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [refreshKey]);

  if (error) return <p className="error">{error}</p>;
  if (!m) return <p className="hint">Loading metrics…</p>;

  const maxTool = Math.max(1, ...Object.values(m.tool_calls));
  const pct = (v: number) => `${Math.round(v * 100)}%`;

  return (
    <div className="metrics">
      <div className="tiles">
        <div className="tile"><span>{m.total_turns}</span><small>Turns (7 days)</small></div>
        <div className="tile"><span>{m.latency_ms.p50} ms</span><small>Latency p50</small></div>
        <div className="tile"><span>{m.latency_ms.p95} ms</span><small>Latency p95</small></div>
        <div className="tile"><span>{pct(m.refusal_rate)}</span><small>Refused by guardrail</small></div>
        <div className="tile"><span>{pct(m.escalation_rate)}</span><small>Escalated to human</small></div>
        <div className="tile"><span>{m.errors}</span><small>Errors</small></div>
      </div>

      <h3>Tool usage</h3>
      {Object.keys(m.tool_calls).length === 0 && <p className="hint">No tool calls yet.</p>}
      {Object.entries(m.tool_calls).map(([name, count]) => (
        <div key={name} className="bar-row">
          <span className="mono">{name}</span>
          <div className="bar"><div style={{ width: `${(count / maxTool) * 100}%` }} /></div>
          <span>{count}{m.tool_failures[name] ? ` (${m.tool_failures[name]} failed)` : ""}</span>
        </div>
      ))}

      <h3>Recent turns</h3>
      <table>
        <thead>
          <tr><th>Intent</th><th>Tools</th><th>ms</th><th>Message (masked)</th></tr>
        </thead>
        <tbody>
          {m.recent.map((r, i) => (
            <tr key={i} className={r.refused ? "row-warn" : r.escalated ? "row-esc" : ""}>
              <td>{r.intent}</td>
              <td className="mono">{r.tools.join(", ") || "–"}</td>
              <td>{r.latency_ms}</td>
              <td>{r.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
