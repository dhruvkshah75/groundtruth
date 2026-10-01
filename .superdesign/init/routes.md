# Routes and API

The frontend is a single-page Vite application at `/`. There is no client-side router. Vite proxies `/api/*` to the Python server at `http://127.0.0.1:8765`.

Backend routes implemented in `src/web/server.py`:

- `GET /api/health` — actual API/provider health.
- `GET /api/state` — session conversation, graph, ledger, world and capabilities.
- `POST /api/ask` — execute one question with the composed Python agent.
- `POST /api/scenarios/scenario-a` — reset and seed map/LiDAR scenario.
- `POST /api/scenarios/scenario-b` — reset and seed three-perspective scenario.
- `POST /api/reset` — reset only the current browser session.

The frontend request and type contracts are included here for design context.

### `frontend/src/api.ts`

```ts
import type { ApiErrorPayload, ApiHealth, AppState } from "./types";

const SESSION_KEY = "groundtruth-session-id";

function getSessionId(): string {
  let id = sessionStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        "X-Session-ID": getSessionId(),
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
      cache: "no-store",
    });
  } catch {
    throw new Error("Cannot reach the Python agent API. Start the GroundTruth backend and retry.");
  }

  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload as Partial<ApiErrorPayload> | null;
    throw new Error(detail?.error || `The agent API returned HTTP ${response.status}.`);
  }
  return payload as T;
}

export const api = {
  health: () => request<ApiHealth>("/api/health"),
  state: () => request<AppState>("/api/state"),
  ask: (question: string) =>
    request<AppState>("/api/ask", {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
  loadScenario: (scenario: "scenario-a" | "scenario-b") =>
    request<AppState>(`/api/scenarios/${scenario}`, {
      method: "POST",
      body: "{}",
    }),
  reset: () => request<AppState>("/api/reset", { method: "POST", body: "{}" }),
};

```

### `frontend/src/types.ts`

```ts
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
  status: "ok";
  service: string;
  provider: string;
  provider_mode: string;
};

export type ApiErrorPayload = { error: string; code: string };

```

### `frontend/vite.config.ts`

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});

```
