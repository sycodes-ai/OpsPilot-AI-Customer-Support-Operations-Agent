export interface ToolCall {
  name: string;
  args: Record<string, string>;
  ok: boolean;
  result: Record<string, unknown>;
  latency_ms: number;
}

export interface AgentReply {
  reply: string;
  intent: string;
  mode: string;
  request_id: string;
  tool_calls: ToolCall[];
  sources: string[];
  refused: boolean;
  escalated: boolean;
  ticket_id: string | null;
  guardrail: string | null;
  latency_ms: number;
  error: string | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  meta?: AgentReply;
}

export interface Health {
  status: string;
  mode: string;
  model: string | null;
  documents: number;
}

export interface Metrics {
  window_hours: number;
  total_turns: number;
  intents: Record<string, number>;
  modes: Record<string, number>;
  tool_calls: Record<string, number>;
  tool_failures: Record<string, number>;
  refusal_rate: number;
  escalation_rate: number;
  errors: number;
  latency_ms: { p50: number; p95: number; max: number };
  recent: {
    ts: string | null;
    intent: string;
    mode: string;
    tools: string[];
    refused: boolean;
    escalated: boolean;
    latency_ms: number;
    error: boolean;
    message: string;
  }[];
}
