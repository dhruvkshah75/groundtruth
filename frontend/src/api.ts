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
