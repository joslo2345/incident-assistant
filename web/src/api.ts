// Thin client for the agent API's people routes (services/agent/src/agent/web.py).
// Every request carries the CSRF header; the session cookie is HttpOnly and sent automatically.

export type Role = "viewer" | "approver" | "admin";
export interface Me {
  username: string;
  display_name: string;
  role: Role;
  can_approve: boolean;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      "X-Requested-With": "ia-ui",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* not JSON */
    }
    if (res.status === 401) window.dispatchEvent(new Event("ia:logged-out"));
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>("GET", path),
  post: <T,>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
};

export interface IncidentRow {
  incident_id: string;
  title: string;
  status: string;
  severity: string;
  suspected_failure_type: string;
  node_id: string;
  gpu_indices: number[];
  opened_at: string;
  run_id: string | null;
  root_cause: string | null;
  confidence: number | null;
  action: string | null;
  pending: number;
}

export interface Evidence {
  evidence_id: string;
  detector: string;
  component: { kind: string; node_id: string; gpu_index: number | null };
  signal: string;
  window_start: string;
  observed_value: number | null;
  threshold: number | null;
  description: string;
}

export interface Citation {
  chunk_id: string;
  doc_id: string;
}

export interface Diagnosis {
  root_cause: string;
  confidence: number;
  summary: string;
  evidence_ids: string[];
  citations: Citation[];
  recommended_action: {
    action: string;
    target: { kind: string; node_id: string; gpu_index: number | null } | null;
    rationale: string;
  };
  model: string;
  trace_id: string;
  created_at: string;
}

export interface Approval {
  request_id: string;
  created_at: string;
  action: string;
  node_id: string;
  gpu_index: number | null;
  reason: string;
  requested_by: string;
  status: "pending" | "approved" | "rejected";
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
}

export interface Followup {
  run_id: string;
  started_at: string;
  status: string;
  question: string;
  asked_by: string | null;
  answer: { answer: string; citations: Citation[]; evidence_ids: string[] } | null;
  latency_ms: number | null;
}

export interface IncidentDetail {
  incident: {
    incident_id: string;
    title: string;
    status: string;
    severity: string;
    suspected_failure_type: string;
    created_at: string;
    components: { kind: string; node_id: string; gpu_index: number | null }[];
    evidence: Evidence[];
  };
  detector_run: string;
  diagnosis: { run_id: string; diagnosis: Diagnosis; latency_ms: number; model: string } | null;
  approvals: Approval[];
  followups: Followup[];
}

export interface TraceStep {
  seq: number;
  kind: "model" | "tool";
  name: string;
  latency_ms: number;
  input: unknown;
  output: unknown;
  is_error: boolean;
  input_tokens: number;
  output_tokens: number;
}

export interface Run {
  run_id: string;
  status: string;
  model: string;
  steps: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number | null;
  trace: TraceStep[];
}

export interface Chunk {
  chunk_id: string;
  title: string;
  heading: string;
  text: string;
}

export interface MetricPoint {
  t: string;
  temp_max_c: number | null;
  power_avg_w: number | null;
  power_limit_w: number | null;
  util_avg_pct: number | null;
  throttled_samples: number | null;
  samples: number | null;
}
