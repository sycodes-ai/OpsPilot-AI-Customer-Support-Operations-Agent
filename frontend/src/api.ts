import type { AgentReply, ChatMessage, Health, Metrics } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep the default message */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function sendMessage(message: string, history: ChatMessage[], sessionId: string): Promise<AgentReply> {
  const trimmed = history.slice(-10).map((m) => ({ role: m.role, content: m.content }));
  return request<AgentReply>("/api/chat", {
    method: "POST",
    body: JSON.stringify({ message, history: trimmed, session_id: sessionId }),
  });
}

export const getHealth = () => request<Health>("/api/health");
export const getMetrics = () => request<Metrics>("/api/metrics");
