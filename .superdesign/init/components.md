# Shared UI components

The frontend has no separate component library. Its reusable page components (`ConversationTurn`, `Inspector`, `ValueCard`, `AuditEventView`, `LedgerTable`, `JsonValue`) are currently colocated in `frontend/src/App.tsx`. The full source, including these component implementations, is included below.

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowUp,
  BrainCircuit,
  Check,
  ChevronDown,
  CircleAlert,
  Database,
  FlaskConical,
  LoaderCircle,
  MessageSquareText,
  PanelRightOpen,
  RotateCcw,
  ShieldCheck,
  X,
} from "lucide-react";
import { api } from "./api";
import type { AgentResponse, ApiHealth, AppState, Fact } from "./types";

type Action = "ask" | "scenario-a" | "scenario-b" | "reset" | null;

const PERSPECTIVE_LABELS: Record<string, string> = {
  user_perspective: "User expectation",
  egocentric_perspective: "Live camera view",
  historical_perspective: "Historical record",
};

function formatTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function shortId(value: string): string {
  return value.length > 15 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [health, setHealth] = useState<ApiHealth | null>(null);
  const [question, setQuestion] = useState("");
  const [action, setAction] = useState<Action>(null);
  const [error, setError] = useState<string | null>(null);
  const [wideLayout, setWideLayout] = useState(() => window.innerWidth >= 1400);
  const [inspectorOpen, setInspectorOpen] = useState(() => window.innerWidth >= 1400);
  const [controlsOpen, setControlsOpen] = useState(false);
  const conversationEnd = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const apiHealth = await api.health();
      setHealth(apiHealth);
    } catch (cause) {
      setHealth(null);
      setError(cause instanceof Error ? cause.message : "Could not connect to the Python agent API.");
      return;
    }
    try {
      const currentState = await api.state();
      setState(currentState);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not connect to the Python agent API.");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const updateLayout = () => {
      const isWide = window.innerWidth >= 1400;
      setWideLayout(isWide);
      if (isWide) setInspectorOpen(true);
    };
    window.addEventListener("resize", updateLayout);
    return () => window.removeEventListener("resize", updateLayout);
  }, []);

  useEffect(() => {
    conversationEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [state?.history.length]);

  const runQuestion = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!question.trim() || !health || action) return;
    setAction("ask");
    setError(null);
    try {
      const updatedState = await api.ask(question);
      setState(updatedState);
      setQuestion(updatedState.suggested_question);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The agent request failed.");
    } finally {
      setAction(null);
    }
  };

  const loadScenario = async (scenario: "scenario-a" | "scenario-b") => {
    if (!health || action) return;
    setAction(scenario);
    setError(null);
    try {
      const updatedState = await api.loadScenario(scenario);
      setState(updatedState);
      setQuestion(updatedState.suggested_question);
      setControlsOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The scenario could not be loaded.");
    } finally {
      setAction(null);
    }
  };

  const resetAgent = async () => {
    if (!health || action) return;
    setAction("reset");
    setError(null);
    try {
      const updatedState = await api.reset();
      setState(updatedState);
      setQuestion("");
      setInspectorOpen(false);
      setControlsOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Agent state could not be reset.");
    } finally {
      setAction(null);
    }
  };

  const busy = action !== null;
  const backendReachable = health?.status === "ok";

  return (
    <div className="app-shell">
      <nav className="icon-rail" aria-label="Workspace navigation">
        <button
          className="rail-button active"
          type="button"
          aria-label="Conversation"
          title="Conversation"
          onClick={() => { setInspectorOpen(false); setControlsOpen(false); document.getElementById("agent-question")?.focus(); }}
        >
          <MessageSquareText size={18} strokeWidth={1.8} />
        </button>
        <button
          className={`rail-button ${inspectorOpen ? "active" : ""}`}
          type="button"
          aria-label={inspectorOpen ? "Close system inspector" : "Open system inspector"}
          aria-expanded={inspectorOpen}
          title="System inspector"
          onClick={() => { setControlsOpen(false); setInspectorOpen((open) => !open); }}
        >
          <PanelRightOpen size={18} strokeWidth={1.8} />
        </button>
        <button
          className={`rail-button rail-scenarios ${controlsOpen ? "active" : ""}`}
          type="button"
          aria-label={controlsOpen ? "Close scenario controls" : "Open scenario controls"}
          aria-expanded={controlsOpen}
          title="Scenario controls"
          onClick={() => { setInspectorOpen(false); setControlsOpen((open) => !open); }}
        >
          <FlaskConical size={18} strokeWidth={1.8} />
        </button>
      </nav>

      <aside className={`control-panel ${controlsOpen ? "mobile-open" : ""}`} aria-label="Agent controls">
        <div className="brand-block">
          <p className="eyebrow">GroundTruth</p>
          <h1>I, Agent</h1>
          <div className={`connection-line ${backendReachable ? "connected" : "disconnected"}`}>
            <span className="connection-dot" aria-hidden="true" />
            <span>{backendReachable ? "Python API connected" : error ? "Backend not connected" : "Checking backend connection…"}</span>
          </div>
          <p className="provider-note">
            {health ? `Intent provider: ${health.provider_mode}.` : "Provider details appear after API connection."}
          </p>
        </div>

        <section className="scenario-section" aria-labelledby="scenario-heading">
          <div className="section-heading">
            <FlaskConical size={15} aria-hidden="true" />
            <h2 id="scenario-heading">Scenario presets</h2>
          </div>
          <button
            className="scenario-button"
            type="button"
            onClick={() => void loadScenario("scenario-a")}
            disabled={!backendReachable || busy}
          >
            <span className="scenario-title">{action === "scenario-a" ? "Loading…" : "Load Scenario A"}</span>
            <span className="scenario-description">Static map says clear; mock LiDAR sees the route.</span>
          </button>
          <button
            className="scenario-button"
            type="button"
            onClick={() => void loadScenario("scenario-b")}
            disabled={!backendReachable || busy}
          >
            <span className="scenario-title">{action === "scenario-b" ? "Loading…" : "Load Scenario B"}</span>
            <span className="scenario-description">Compare prompt, camera, and stored history.</span>
          </button>
          <button className="reset-button" type="button" onClick={() => void resetAgent()} disabled={!backendReachable || busy}>
            {action === "reset" ? <LoaderCircle size={15} className="spin" /> : <RotateCcw size={15} />}
            Reset agent state
          </button>
        </section>

        <section className="architecture-section" aria-labelledby="architecture-heading">
          <h2 id="architecture-heading">Architecture</h2>
          <ol>
            <li><span>01</span><div><strong>Declarative</strong><small>Belief graph and SQLite provenance</small></div></li>
            <li><span>02</span><div><strong>Procedural</strong><small>Intent plan and conflict resolution</small></div></li>
            <li><span>03</span><div><strong>Sensorimotor</strong><small>World state and sensor telemetry</small></div></li>
          </ol>
        </section>
      </aside>

      <main className="conversation-column">
        <header className="top-bar">
          <div>
            <p className="top-title">GroundTruth <span>/</span> I, Agent</p>
            <p className="top-subtitle">Epistemic agent workspace</p>
          </div>
          <div className="provider-chip">
            {backendReachable ? <ShieldCheck size={15} /> : <CircleAlert size={15} />}
            <span>{health ? health.provider_mode : "Backend not connected"}</span>
          </div>
          <button className="mobile-panel-button" type="button" onClick={() => { setControlsOpen(false); setInspectorOpen(true); }}>
            <PanelRightOpen size={16} /> Inspect system
          </button>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            <CircleAlert size={17} />
            <span>{error}</span>
            <button type="button" aria-label="Dismiss error" onClick={() => setError(null)}><X size={16} /></button>
          </div>
        )}

        <section className="conversation" aria-label="Conversation">
          <div className="conversation-inner">
            {state?.history.length ? (
              state.history.map((response, index) => (
                <ConversationTurn key={`${response.question}-${index}`} response={response} />
              ))
            ) : (
              <div className="empty-conversation">
                <div className="agent-heading">
                  <div className="agent-icon"><BrainCircuit size={16} /></div>
                  <div><strong>I, Agent</strong><span>Grounded reasoning assistant</span></div>
                </div>
                <div className="empty-card">
                  <p>{state ? "No interaction has run yet." : "Waiting for the Python agent API."}</p>
                  <span>
                    {state
                      ? "Ask a question or load a scenario, then run the epistemic cycle to inspect the response and supporting system state."
                      : "The conversation and system inspector will use live data from the backend once it connects."}
                  </span>
                </div>
              </div>
            )}
            {action === "ask" && (
              <div className="working-indicator" role="status"><LoaderCircle size={16} className="spin" /> Running the agent cycle…</div>
            )}
            <div ref={conversationEnd} />
          </div>

          <form className="composer-wrap" onSubmit={(event) => void runQuestion(event)}>
            <label className="sr-only" htmlFor="agent-question">Ask the cognitive agent</label>
            <div className="composer">
              <textarea
                id="agent-question"
                rows={2}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                placeholder="Ask GroundTruth about the current world…"
                disabled={!backendReachable || busy}
              />
              <div className="composer-footer">
                <span className="composer-provider"><Activity size={13} />{health?.provider ?? "Agent API"}</span>
                <button className="send-button" type="submit" aria-label="Run agent cycle" disabled={!backendReachable || busy || !question.trim()}>
                  {action === "ask" ? <LoaderCircle size={16} className="spin" /> : <ArrowUp size={17} />}
                  <span>Run cycle</span>
                </button>
              </div>
            </div>
            <p className="composer-hint">Enter to run · Shift + Enter for a new line</p>
          </form>
        </section>
      </main>

      <Inspector state={state} open={inspectorOpen} wideLayout={wideLayout} onClose={() => setInspectorOpen(false)} />
      {(controlsOpen || inspectorOpen) && <button className="mobile-backdrop" aria-label="Close panels" onClick={() => { setControlsOpen(false); setInspectorOpen(false); }} />}
    </div>
  );
}

function ConversationTurn({ response }: { response: AgentResponse }) {
  return (
    <article className="conversation-turn">
      <div className="user-message">{response.question}</div>
      <div className="assistant-message">
        <div className="agent-heading">
          <div className="agent-icon"><BrainCircuit size={16} /></div>
          <div><strong>I, Agent</strong><span>Grounded reasoning assistant</span></div>
          <span className={`status-badge status-${response.status}`}>{response.status.replaceAll("_", " ")}</span>
        </div>
        <p className="answer-text">{response.answer}</p>
        {response.perspectives && (
          <div className="perspective-cards">
            {Object.entries(response.perspectives).map(([key, value]) => (
              <div className="perspective-card" key={key}>
                <span>{PERSPECTIVE_LABELS[key] ?? key.replaceAll("_", " ")}</span>
                <p>{value}</p>
              </div>
            ))}
          </div>
        )}
        {response.revisions.length > 0 && (
          <div className="revision-inline">
            <div className="revision-heading"><Check size={15} /><strong>Belief revision recorded</strong></div>
            {response.revisions.map((revision) => (
              <p key={revision.audit_event.event_id}>
                {revision.audit_event.reason} · rule <code>{revision.audit_event.policy_rule}</code>
              </p>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

function Inspector({ state, open, wideLayout, onClose }: { state: AppState | null; open: boolean; wideLayout: boolean; onClose: () => void }) {
  const latest = state?.history.at(-1) ?? null;
  return (
    <aside className={`inspector ${open ? "inspector-open" : ""} ${wideLayout && !open ? "desktop-hidden" : ""}`} aria-label="System inspector" aria-hidden={!open && !wideLayout}>
      <header className="inspector-header">
        <div><h2>System inspector</h2><p>Live backend fields and audit data</p></div>
        <button type="button" className="inspector-close" aria-label="Close system inspector" onClick={onClose}><X size={17} /></button>
      </header>
      <div className="inspector-content">
        <details className="inspector-section" open>
          <summary><span>Tier 2 · reasoning &amp; outcome</span><ChevronDown size={16} /></summary>
          <div className="inspector-section-body">
            <ValueCard label="Response status">{latest?.status ?? "No agent response yet."}</ValueCard>
            <ValueCard label="Plan rationale">{latest?.plan_reason ?? "No plan has run yet."}</ValueCard>
            <ValueCard label="Plan verification">{latest?.plan_verifiable === true ? "Verifiable" : latest?.plan_verifiable === false ? "Unverifiable" : "No executable plan was created."}</ValueCard>
            <div className="inspector-card">
              <p className="value-label">Belief revisions and audit events</p>
              {state?.audit_events.length ? state.audit_events.map((event) => <AuditEventView key={event.event_id} event={event} />) : <p className="empty-detail">No audit events have been recorded.</p>}
            </div>
            <ValueCard label="Sensor telemetry">
              {latest?.sensor_telemetry.length ? <JsonValue value={latest.sensor_telemetry} /> : "No observation has been returned yet."}
            </ValueCard>
            {latest?.audit_trails.map((trail) => (
              <div className="inspector-card" key={trail.root_fact_id}>
                <p className="value-label">Audit chain · {shortId(trail.root_fact_id)}</p>
                <p className="empty-detail">{trail.facts.length} related fact(s), {trail.events.length} revision event(s).</p>
              </div>
            ))}
          </div>
        </details>

        <details className="inspector-section">
          <summary><span>Perspectives</span><ChevronDown size={16} /></summary>
          <div className="inspector-section-body">
            {latest?.perspectives
              ? Object.entries(latest.perspectives).map(([key, value]) => <ValueCard key={key} label={PERSPECTIVE_LABELS[key] ?? key}>{value}</ValueCard>)
              : <p className="empty-detail">No perspective result has been returned yet.</p>}
          </div>
        </details>

        <details className="inspector-section">
          <summary><span>Tier 1 · graph &amp; evidence ledger</span><ChevronDown size={16} /></summary>
          <div className="inspector-section-body">
            {state ? (
              <>
                <div className="count-grid">
                  <ValueCard label="Active nodes">{state.graph.node_count}</ValueCard>
                  <ValueCard label="Active edges">{state.graph.edge_count}</ValueCard>
                </div>
                <ValueCard label="Active graph nodes">{state.graph.nodes.length ? state.graph.nodes.join(", ") : "No active graph nodes."}</ValueCard>
                <div className="inspector-card table-card">
                  <p className="value-label">Active belief graph</p>
                  {state.graph.edges.length ? (
                    <div className="table-scroll"><table><thead><tr><th>Subject</th><th>Predicate</th><th>Object</th><th>Source</th></tr></thead><tbody>
                      {state.graph.edges.map((edge) => <tr key={edge.key}><td>{edge.source}</td><td>{edge.predicate}</td><td>{edge.target}</td><td>{edge.source_agent}</td></tr>)}
                    </tbody></table></div>
                  ) : <p className="empty-detail">No active edges in the graph.</p>}
                </div>
                <LedgerTable facts={state.ledger} />
              </>
            ) : <p className="empty-detail">Agent state has not been retrieved.</p>}
          </div>
        </details>

        <details className="inspector-section">
          <summary><span>Tier 3 · physical environment</span><ChevronDown size={16} /></summary>
          <div className="inspector-section-body">
            {state ? (
              <>
                {state.environment.scenario_name && <ValueCard label="Loaded scenario">{state.environment.scenario_name}</ValueCard>}
                <ValueCard label="World version">{state.environment.world_version}</ValueCard>
                <ValueCard label="Robot pose"><JsonValue value={state.environment.robot} /></ValueCard>
                <ValueCard label="Ambient lighting"><JsonValue value={state.environment.light} /></ValueCard>
                <ValueCard label={`Obstacles (${Object.keys(state.environment.obstacles).length})`}><JsonValue value={state.environment.obstacles} /></ValueCard>
                <ValueCard label={`Objects (${Object.keys(state.environment.objects).length})`}><JsonValue value={state.environment.objects} /></ValueCard>
                <ValueCard label="Advertised sensor capabilities">{state.capabilities.filter((item) => item.kind === "sensor").map((item) => item.name).join(", ") || "No sensor capabilities are advertised."}</ValueCard>
              </>
            ) : <p className="empty-detail">Environment state has not been retrieved.</p>}
          </div>
        </details>
      </div>
    </aside>
  );
}

function ValueCard({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="inspector-card"><p className="value-label">{label}</p><div className="value-content">{children}</div></div>;
}

function JsonValue({ value }: { value: unknown }) {
  return <pre className="json-value">{JSON.stringify(value, null, 2)}</pre>;
}

function AuditEventView({ event }: { event: AgentResponse["audit_events"][number] }) {
  return (
    <div className="audit-event">
      <div className="audit-rule"><Database size={13} /><code>{event.policy_rule}</code></div>
      <p>{event.reason}</p>
      <small>{formatTime(event.created_at)} · input {event.input_fact_ids.map(shortId).join(", ")}</small>
    </div>
  );
}

function LedgerTable({ facts }: { facts: Fact[] }) {
  return (
    <div className="inspector-card table-card">
      <p className="value-label">SQLite evidence ledger · {facts.length} fact(s)</p>
      {facts.length ? (
        <div className="table-scroll"><table><thead><tr><th>Fact</th><th>Claim</th><th>Source</th><th>State</th></tr></thead><tbody>
          {facts.map((fact) => (
            <tr key={fact.fact_id}>
              <td title={fact.fact_id}>{shortId(fact.fact_id)}</td>
              <td>{fact.subject} {fact.predicate} {fact.object}<small>{formatTime(fact.observed_at)}</small></td>
              <td>{fact.source_agent}<small>{Math.round(fact.confidence_score * 100)}% confidence</small></td>
              <td>{fact.superseded_by ? `Superseded by ${shortId(fact.superseded_by)}` : "Active"}</td>
            </tr>
          ))}
        </tbody></table></div>
      ) : <p className="empty-detail">No ledger records have been returned.</p>}
    </div>
  );
}

export default App;

```
