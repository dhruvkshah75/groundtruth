# Application layout

The one-page React shell is implemented by `frontend/src/App.tsx`; responsive dark theme styles are in `frontend/src/app.css`.

## App shell and page components

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

## Complete stylesheet

```css
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap");

:root {
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #e8e8e8;
  background: #202020;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
  --surface: #202020;
  --surface-raised: #292929;
  --surface-sidebar: #1b1b1b;
  --surface-rail: #171717;
  --line: rgb(255 255 255 / 8%);
  --line-strong: rgb(255 255 255 / 12%);
  --text: #ededed;
  --muted: #929292;
  --quiet: #666;
  --accent: #61d8ca;
  --accent-dark: #102322;
  --danger: #f19b94;
}

* { box-sizing: border-box; }
html, body, #root { width: 100%; min-width: 320px; min-height: 100%; margin: 0; }
body { min-height: 100vh; background: var(--surface); }
button, textarea { font: inherit; }
button { color: inherit; }
button:focus-visible, textarea:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
button:disabled { cursor: not-allowed; opacity: .48; }

.app-shell { display: grid; grid-template-columns: 64px 270px minmax(380px, 1fr) 350px; min-height: 100vh; background: var(--surface); }
.icon-rail { position: relative; z-index: 30; display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 15px 0; border-right: 1px solid var(--line); background: var(--surface-rail); }
.rail-button { display: grid; width: 40px; height: 40px; place-items: center; border: 0; border-radius: 10px; color: #777; background: transparent; cursor: pointer; transition: background .16s ease, color .16s ease; }
.rail-button:hover, .rail-button.active { color: #ededed; background: #2a2a2a; }
.rail-scenarios { margin-top: 2px; }

.control-panel { display: flex; flex-direction: column; min-width: 0; padding: 22px 17px; overflow-y: auto; border-right: 1px solid var(--line); background: var(--surface-sidebar); }
.brand-block { padding: 0 1px 19px; border-bottom: 1px solid var(--line); }
.eyebrow, .architecture-section h2, .section-heading h2 { margin: 0; color: var(--muted); font-size: 10px; font-weight: 600; letter-spacing: .15em; line-height: 1.4; text-transform: uppercase; }
.brand-block h1 { margin: 4px 0 13px; color: var(--text); font-size: 18px; font-weight: 600; letter-spacing: -.035em; }
.connection-line { display: flex; align-items: center; gap: 8px; color: #b0b0b0; font-size: 11px; }
.connection-dot { width: 7px; height: 7px; flex: 0 0 auto; border-radius: 50%; background: #858585; }
.connection-line.connected .connection-dot { background: #69d9b0; box-shadow: 0 0 8px rgb(105 217 176 / 34%); }
.connection-line.disconnected .connection-dot { background: #d9ad6b; }
.provider-note { margin: 8px 0 0; color: var(--quiet); font-size: 11px; line-height: 1.55; }
.scenario-section { padding: 19px 0 20px; border-bottom: 1px solid var(--line); }
.section-heading { display: flex; align-items: center; gap: 8px; margin: 0 0 11px; color: var(--muted); }
.scenario-button { width: 100%; margin: 0 0 8px; padding: 11px 12px; border: 1px solid var(--line-strong); border-radius: 9px; background: #242424; text-align: left; cursor: pointer; transition: border-color .16s ease, background .16s ease; }
.scenario-button:hover:not(:disabled) { border-color: rgb(97 216 202 / 40%); background: #2b2b2b; }
.scenario-title { display: block; color: var(--text); font-size: 12px; font-weight: 500; }
.scenario-description { display: block; margin-top: 5px; color: var(--quiet); font-size: 10px; line-height: 1.5; }
.reset-button { display: flex; width: 100%; min-height: 36px; align-items: center; justify-content: center; gap: 8px; margin-top: 3px; border: 1px solid var(--line); border-radius: 8px; color: #aaa; background: transparent; font-size: 11px; cursor: pointer; }
.reset-button:hover:not(:disabled) { color: var(--text); background: #292929; }
.architecture-section { padding: 19px 0 0; }
.architecture-section ol { display: grid; gap: 15px; margin: 13px 0 0; padding: 0; list-style: none; }
.architecture-section li { display: flex; align-items: flex-start; gap: 12px; font-size: 11px; line-height: 1.5; }
.architecture-section li > span { color: var(--accent); font-size: 10px; font-weight: 600; }
.architecture-section strong { display: block; color: #d0d0d0; font-size: 11px; font-weight: 500; }
.architecture-section small { display: block; margin-top: 2px; color: var(--quiet); font-size: 10px; }

.conversation-column { display: flex; min-width: 0; min-height: 100vh; flex-direction: column; }
.top-bar { display: flex; height: 62px; flex: 0 0 62px; align-items: center; justify-content: space-between; gap: 12px; padding: 0 25px; border-bottom: 1px solid var(--line); }
.top-title { margin: 0; color: #dedede; font-size: 13px; font-weight: 500; }
.top-title span { padding: 0 4px; color: #666; }
.top-subtitle { margin: 3px 0 0; color: var(--quiet); font-size: 10px; }
.provider-chip { display: inline-flex; align-items: center; gap: 7px; color: #888; font-size: 10px; white-space: nowrap; }
.provider-chip svg { color: var(--accent); }
.mobile-panel-button { display: none; align-items: center; gap: 6px; padding: 7px 9px; border: 1px solid var(--line); border-radius: 7px; color: #bbb; background: transparent; font-size: 10px; }
.conversation { display: flex; min-height: 0; flex: 1; flex-direction: column; }
.conversation-inner { width: 100%; max-width: 820px; flex: 1; align-self: center; padding: 40px 38px 24px; }
.empty-conversation { max-width: 720px; margin: 7vh auto 0; }
.agent-heading { display: flex; align-items: center; gap: 10px; }
.agent-icon { display: grid; width: 28px; height: 28px; flex: 0 0 auto; place-items: center; border: 1px solid rgb(97 216 202 / 24%); border-radius: 8px; color: var(--accent); background: rgb(97 216 202 / 8%); }
.agent-heading strong { display: block; color: #dedede; font-size: 12px; font-weight: 500; }
.agent-heading span:not(.status-badge) { display: block; margin-top: 2px; color: var(--quiet); font-size: 10px; }
.empty-card { margin-top: 21px; padding: 17px 18px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface-raised); }
.empty-card p { margin: 0; color: #d5d5d5; font-size: 13px; line-height: 1.6; }
.empty-card > span { display: block; max-width: 610px; margin-top: 5px; color: #858585; font-size: 11px; line-height: 1.7; }
.conversation-turn { display: flex; flex-direction: column; gap: 25px; margin: 0 auto 43px; }
.user-message { max-width: 78%; align-self: flex-end; padding: 13px 17px; border: 1px solid rgb(255 255 255 / 4%); border-radius: 16px; color: #dedede; background: #303030; font-size: 13px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.assistant-message { min-width: 0; }
.assistant-message .agent-heading { position: relative; }
.status-badge { margin-left: auto; padding: 5px 8px; border: 1px solid rgb(97 216 202 / 20%); border-radius: 999px; color: #a4e8df; background: rgb(97 216 202 / 8%); font-size: 9px !important; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; }
.status-grounded_conflict_resolved { color: #edc185; border-color: rgb(237 193 133 / 22%); background: rgb(237 193 133 / 8%); }
.status-unverifiable, .status-unsupported, .status-no_data { color: #e7a09b; border-color: rgb(231 160 155 / 20%); background: rgb(231 160 155 / 8%); }
.answer-text { margin: 15px 0 0 38px; color: #cdcdcd; font-size: 13px; line-height: 1.9; white-space: pre-wrap; overflow-wrap: anywhere; }
.perspective-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; margin: 19px 0 0 38px; }
.perspective-card { min-width: 0; padding: 12px; border: 1px solid var(--line); border-radius: 9px; background: #242424; }
.perspective-card span { color: #888; font-size: 9px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; }
.perspective-card p { margin: 7px 0 0; color: #c9c9c9; font-size: 11px; line-height: 1.65; overflow-wrap: anywhere; }
.revision-inline { margin: 16px 0 0 38px; padding: 12px 13px; border: 1px solid rgb(237 193 133 / 16%); border-radius: 9px; background: rgb(237 193 133 / 5%); }
.revision-heading { display: flex; align-items: center; gap: 7px; color: #e4bf8d; font-size: 11px; }
.revision-inline p { margin: 7px 0 0; color: #b7a98f; font-size: 10px; line-height: 1.6; }
.revision-inline code { color: #e0cba9; }
.working-indicator { display: flex; align-items: center; gap: 9px; padding: 12px 0; color: #9e9e9e; font-size: 11px; }
.composer-wrap { position: sticky; bottom: 0; z-index: 2; width: 100%; max-width: 850px; align-self: center; padding: 16px 38px 18px; background: linear-gradient(180deg, rgb(32 32 32 / 0%), #202020 17%); }
.composer { padding: 9px; border: 1px solid var(--line-strong); border-radius: 17px; background: #292929; transition: border-color .16s ease; }
.composer:focus-within { border-color: rgb(97 216 202 / 47%); }
.composer textarea { display: block; width: 100%; resize: vertical; min-height: 50px; max-height: 180px; padding: 6px 9px; border: 0; outline: 0; color: var(--text); background: transparent; font-size: 12px; line-height: 1.6; }
.composer textarea::placeholder { color: #737373; }
.composer-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.composer-provider { display: inline-flex; align-items: center; gap: 6px; padding: 0 7px; color: #777; font-size: 9px; }
.send-button { display: inline-flex; min-height: 31px; align-items: center; gap: 7px; padding: 0 11px; border: 0; border-radius: 8px; color: var(--accent-dark); background: var(--accent); font-size: 10px; font-weight: 600; cursor: pointer; transition: background .15s ease; }
.send-button:hover:not(:disabled) { background: #83e7dc; }
.composer-hint { margin: 7px 2px 0; color: #626262; font-size: 9px; text-align: right; }
.error-banner { display: flex; align-items: center; gap: 9px; margin: 12px 20px 0; padding: 10px 12px; border: 1px solid rgb(241 155 148 / 22%); border-radius: 8px; color: #e4aaa5; background: rgb(241 155 148 / 7%); font-size: 11px; }
.error-banner span { flex: 1; }
.error-banner button { display: grid; width: 25px; height: 25px; place-items: center; border: 0; color: inherit; background: transparent; cursor: pointer; }

.inspector { display: flex; min-width: 0; max-height: 100vh; flex-direction: column; overflow-y: auto; border-left: 1px solid var(--line); background: var(--surface-sidebar); }
.inspector.desktop-hidden { display: none; }
.inspector-header { position: sticky; top: 0; z-index: 1; display: flex; min-height: 68px; align-items: center; justify-content: space-between; padding: 13px 17px; border-bottom: 1px solid var(--line); background: var(--surface-sidebar); }
.inspector-header h2 { margin: 0; color: #e5e5e5; font-size: 12px; font-weight: 500; }
.inspector-header p { margin: 4px 0 0; color: var(--quiet); font-size: 9px; }
.inspector-close { display: none; width: 29px; height: 29px; place-items: center; border: 0; border-radius: 7px; color: #999; background: transparent; cursor: pointer; }
.inspector-content { display: grid; gap: 9px; padding: 13px; }
.inspector-section { min-width: 0; border: 1px solid var(--line); border-radius: 9px; background: #202020; }
.inspector-section > summary { display: flex; min-height: 40px; align-items: center; justify-content: space-between; gap: 8px; padding: 0 12px; color: #d4d4d4; font-size: 10px; font-weight: 500; list-style: none; cursor: pointer; }
.inspector-section > summary::-webkit-details-marker { display: none; }
.inspector-section[open] > summary svg { transform: rotate(180deg); }
.inspector-section > summary svg { color: #777; transition: transform .15s ease; }
.inspector-section-body { display: grid; gap: 8px; padding: 10px; border-top: 1px solid var(--line); }
.inspector-card { min-width: 0; padding: 10px; border-radius: 7px; background: #272727; }
.value-label { margin: 0 0 6px; color: #777; font-size: 9px; font-weight: 600; letter-spacing: .06em; line-height: 1.4; text-transform: uppercase; }
.value-content { min-width: 0; color: #b8b8b8; font-size: 10px; line-height: 1.65; overflow-wrap: anywhere; }
.empty-detail { margin: 0; color: #777; font-size: 10px; line-height: 1.65; }
.json-value { max-height: 260px; margin: 0; overflow: auto; color: #a9bcb6; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 9px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
.count-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.count-grid .inspector-card { min-width: 0; }
.table-card { padding-bottom: 5px; }
.table-scroll { width: 100%; max-height: 270px; overflow: auto; }
table { width: 100%; border-collapse: collapse; color: #aaa; font-size: 9px; text-align: left; }
th { position: sticky; top: 0; z-index: 1; color: #777; background: #272727; font-size: 8px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; }
th, td { padding: 7px 5px; border-bottom: 1px solid var(--line); vertical-align: top; }
td { overflow-wrap: anywhere; }
td small { display: block; margin-top: 3px; color: #6d6d6d; font-size: 8px; }
.audit-event + .audit-event { margin-top: 11px; padding-top: 10px; border-top: 1px solid var(--line); }
.audit-rule { display: flex; align-items: center; gap: 6px; color: var(--accent); }
.audit-rule code { overflow-wrap: anywhere; font-size: 9px; }
.audit-event p { margin: 6px 0; color: #bcbcbc; font-size: 10px; line-height: 1.6; }
.audit-event small { color: #777; font-size: 8px; line-height: 1.5; }
.mobile-backdrop { display: none; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }

@media (max-width: 1399px) {
  .app-shell { grid-template-columns: 64px 250px minmax(360px, 1fr); }
  .inspector { position: fixed; z-index: 21; inset: 0 0 0 auto; width: min(390px, calc(100vw - 64px)); max-height: 100dvh; transform: translateX(102%); transition: transform .2s ease; box-shadow: -18px 0 45px rgb(0 0 0 / 28%); }
  .inspector.inspector-open { transform: translateX(0); }
  .inspector-close { display: grid; }
  .mobile-panel-button { display: inline-flex; }
  .provider-chip { margin-left: auto; }
  .mobile-backdrop { position: fixed; z-index: 19; inset: 0; display: block; border: 0; background: rgb(0 0 0 / 55%); }
}

@media (max-width: 1199px) {
  .app-shell { grid-template-columns: 64px minmax(0, 1fr); }
  .control-panel { position: fixed; z-index: 21; inset: 0 auto 0 64px; display: none; width: min(286px, calc(100vw - 74px)); box-shadow: 18px 0 45px rgb(0 0 0 / 28%); }
  .control-panel.mobile-open { display: flex; }
}

@media (max-width: 700px) {
  .app-shell { grid-template-columns: 54px minmax(0, 1fr); }
  .icon-rail { padding-top: 11px; }
  .rail-button { width: 36px; height: 36px; }
  .control-panel { left: 54px; width: min(286px, calc(100vw - 64px)); }
  .top-bar { height: 58px; flex-basis: 58px; padding: 0 13px; }
  .top-title { font-size: 11px; }
  .top-subtitle { font-size: 9px; }
  .provider-chip { display: none; }
  .conversation-inner { padding: 30px 17px 20px; }
  .empty-conversation { margin-top: 4vh; }
  .user-message { max-width: 90%; font-size: 12px; }
  .answer-text, .perspective-cards, .revision-inline { margin-left: 0; }
  .perspective-cards { grid-template-columns: 1fr; }
  .composer-wrap { padding: 12px 12px 14px; }
  .composer-hint { display: none; }
  .send-button span { display: none; }
  .send-button { width: 33px; justify-content: center; padding: 0; }
  .inspector { width: calc(100vw - 54px); }
  .mobile-panel-button { font-size: 9px; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; }
}

```
