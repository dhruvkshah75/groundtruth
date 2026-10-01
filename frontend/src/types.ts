export type SpatialContext = {
  location: string | null;
  frame_of_reference: string | null;
  world_version: number | null;
  observer: string | null;
  extra_context: Record<string, unknown>;
};

export type Fact = {
  fact_id: string;
  subject: string;
  predicate: string;
  object: string;
  source_agent: string;
  confidence_score: number;
  observed_at: string;
  created_at: string;
  superseded_by: string | null;
  context: SpatialContext;
  evidence: Record<string, unknown>;
};

export type GraphEdge = {
  source: string;
  target: string;
  key: string;
  fact_id: string;
  predicate: string;
  source_agent: string;
  confidence_score: number;
  observed_at: string | null;
  context: SpatialContext | null;
};

export type AuditEvent = {
  event_id: string;
  event_type: string;
  input_fact_ids: string[];
  output_fact_ids: string[];
  reason: string;
  policy_rule: string;
  created_at: string;
};

export type Revision = {
  successor: Fact;
  audit_event: AuditEvent;
};

export type ReActTrace = {
  tool_name: string;
  tool_call_id: string;
  arguments: Record<string, unknown>;
  approved_operations: string[];
  execution_summary?: Record<string, unknown> | null;
  explanation_source: "llm" | "deterministic_fallback" | "rule_based";
  model?: string | null;
};

export type AgentResponse = {
  question: string;
  answer: string;
  status: string;
  perspectives: Record<string, string> | null;
  revisions: Revision[];
  audit_events: AuditEvent[];
  audit_trails: Array<{
    root_fact_id: string;
    facts: Fact[];
    events: AuditEvent[];
  }>;
  active_facts: Fact[];
  sensor_telemetry: Array<Record<string, unknown>>;
  plan_verifiable: boolean | null;
  plan_reason: string | null;
  react_trace?: ReActTrace | null;
};

export type WorldState = {
  world_version: number;
  scenario_name: string | null;
  robot: {
    robot_id: string;
    x_cm: number;
    y_cm: number;
    direction: string;
    location: string;
    frame_of_reference: string;
  };
  light: { intensity: number; color_cast: string };
  obstacles: Record<string, Record<string, unknown>>;
  objects: Record<string, Record<string, unknown>>;
};

export type AppState = {
  history: AgentResponse[];
  suggested_question: string;
  graph: {
    nodes: string[];
    edges: GraphEdge[];
    node_count: number;
    edge_count: number;
  };
  active_facts: Fact[];
  ledger: Fact[];
  audit_events: AuditEvent[];
  environment: WorldState;
  capabilities: Array<{
    name: string;
    kind: "sensor" | "action";
    description: string;
    parameters: string[];
  }>;
};

export type ApiHealth = {
  status: "ok" | "degraded" | "error";
  service: string;
  provider: string;
  provider_mode: string;
  llm_ready?: boolean;
  model?: string | null;
  error?: string | null;
};

export type ApiErrorPayload = { error: string; code: string };
