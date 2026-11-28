export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ToolCallMetric = { name: string; ok: boolean; ms: number; args?: Record<string, unknown>; error?: string };
export type TurnMetrics = {
  latency_ms: number;
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  est_cost_usd: number;
  models: string[];
  tool_calls: ToolCallMetric[];
};
export type Approval = {
  approval_id: number;
  tool_calls: { id: string; name: string; args: Record<string, unknown> }[];
  reasons: string[];
};
export type TurnResult = {
  thread_id: string;
  status: "completed" | "awaiting_approval" | "escalated" | "blocked" | "error" | "active";
  reply: string;
  approval: Approval | null;
  flags: string[];
  metrics: TurnMetrics;
};
export type HistoryMessage = {
  role: "human" | "ai" | "tool" | "system";
  content: string;
  tool_calls?: { name: string; args: Record<string, unknown> }[];
  name?: string;
  status?: string;
};
export type Thread = { thread_id: string; title: string; status: string; created_at: string; updated_at: string };
export type ApprovalRow = {
  id: number;
  thread_id: string;
  thread_title: string;
  tool_calls: { id: string; name: string; args: Record<string, unknown> }[];
  risk_reasons: string[];
  status: "pending" | "approved" | "rejected";
  requested_by: string;
  reviewed_by: string | null;
  review_note: string | null;
  created_at: string;
  decided_at: string | null;
};
export type Ticket = {
  id: number;
  customer_id: number;
  subject: string;
  category: string;
  priority: string;
  status: string;
  assignee: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
};
export type MetricsSummary = {
  window_hours: number;
  runs: {
    runs: number;
    completed: number;
    awaiting_approval: number;
    escalated: number;
    blocked: number;
    errors: number;
    p50_latency_ms: number | null;
    p95_latency_ms: number | null;
    avg_llm_calls: number;
    input_tokens: number;
    output_tokens: number;
    est_cost_usd: number;
  };
  tools: { tool: string; calls: number; failures: number; avg_ms: number }[];
  approvals: Record<string, number>;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(apiKey: string, path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "X-API-Key": apiKey, ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  threads: (key: string) => request<Thread[]>(key, "/v1/threads"),
  thread: (key: string, id: string) =>
    request<{ thread_id: string; messages: HistoryMessage[] }>(key, `/v1/threads/${id}`),
  approvals: (key: string, status = "pending") => request<ApprovalRow[]>(key, `/v1/approvals?status=${status}`),
  decide: (key: string, id: number, decision: "approve" | "reject", note?: string) =>
    request<TurnResult>(key, `/v1/approvals/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, note: note || null }),
    }),
  tickets: (key: string) => request<Ticket[]>(key, "/v1/tickets?limit=100"),
  metrics: (key: string, hours = 168) => request<MetricsSummary>(key, `/v1/metrics/summary?hours=${hours}`),
};

export type StreamEvent =
  | { event: "thread"; data: { thread_id: string } }
  | { event: "token"; data: { text: string } }
  | { event: "tool_calls"; data: { name: string; args: Record<string, unknown> }[] }
  | { event: "tool_result"; data: { name: string; status: string; preview: string } }
  | { event: "done"; data: TurnResult };

/** POST /v1/chat/stream and yield parsed Server-Sent Events. */
export async function* chatStream(
  apiKey: string,
  message: string,
  threadId: string | null,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const res = await fetch(`${API_URL}/v1/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
    body: JSON.stringify({ message, thread_id: threadId }),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (!data.length) continue;
      try {
        yield { event, data: JSON.parse(data.join("\n")) } as StreamEvent;
      } catch {
        /* ignore malformed frame */
      }
    }
  }
}
