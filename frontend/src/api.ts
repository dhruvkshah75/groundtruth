import type { ApiErrorPayload, ApiHealth, AppState } from "./types";

const SESSION_KEY = "groundtruth-session-id";

export function getSessionId(): string {
  let id = sessionStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

export function setSessionId(id: string): void {
  sessionStorage.setItem(SESSION_KEY, id);
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
    throw new Error("Cannot reach the Python Agent API. Start the GroundTruth backend and retry.");
  }

  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload as Partial<ApiErrorPayload> | null;
    if (path === "/api/new-chat" && response.status === 404 && detail?.code === "not_found") {
      throw new Error("New chat requires the updated backend. Restart the Python Agent server to enable it; restarting clears the current in-memory session.");
    }
    throw new Error(detail?.error || `The Agent API returned HTTP ${response.status}.`);
  }
  return payload as T;
}

export const api = {
  health: () => request<ApiHealth>("/api/health"),
  state: (sessionId?: string) => request<AppState>("/api/state", sessionId ? { headers: { "X-Session-ID": sessionId } } : {}),
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
  newChat: () => request<AppState>("/api/new-chat", { method: "POST", body: "{}" }),
  deleteSession: async () => {
    // Older running servers do not implement DELETE. Resetting that session removes
    // its in-memory data; newer servers delete the durable snapshot instead.
    const response = await fetch("/api/session", { method: "DELETE", headers: { "X-Session-ID": getSessionId() }, cache: "no-store" });
    if (response.status === 501) {
      await request<AppState>("/api/reset", { method: "POST", body: "{}" });
      return;
    }
    if (!response.ok) {
      const detail = await response.json().catch(() => null) as Partial<ApiErrorPayload> | null;
      throw new Error(detail?.error || `The Agent API returned HTTP ${response.status}.`);
    }
  },
  reset: () => request<AppState>("/api/reset", { method: "POST", body: "{}" }),
};
