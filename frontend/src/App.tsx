import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState } from "react";
import {
  ArrowUp, BrainCircuit, Check, ChevronDown, CircleAlert, Database,
  FlaskConical, LoaderCircle, MessageSquareText, Network, PanelLeftClose,
  PanelLeftOpen, Plus, RotateCcw, ScanLine, Sparkles, X,
} from "lucide-react";
import { api } from "./api";
import type { AgentResponse, ApiHealth, AppState, Fact } from "./types";

const BeliefGraphPage = lazy(() => import("./BeliefGraphPage"));
type WorkspaceTab = "chat" | "inspector" | "graph";
type Action = "ask" | "scenario-a" | "scenario-b" | "reset" | "new-chat" | null;
const tabs = [
  { id: "chat", label: "Chat", icon: MessageSquareText },
  { id: "inspector", label: "System inspector", icon: ScanLine },
  { id: "graph", label: "Belief graph", icon: Network },
] as const;
const tabFromPath = (): WorkspaceTab => window.location.pathname === "/graph"
  ? "graph" : window.location.pathname === "/inspector" ? "inspector" : "chat";

type RobotPoseSummary = {
  robotId: string;
  xCm: number;
  yCm: number;
  direction: string;
  location: string | null;
  frameOfReference: string | null;
};

function getRobotPoseSummary(response: AgentResponse): RobotPoseSummary | null {
  if (response.react_trace?.tool_name !== "current_robot_pose") return null;

  const observation = response.sensor_telemetry.find((item) => item.capability === "robot_pose");
  if (!observation || typeof observation.measurements !== "object" || observation.measurements === null) {
    return null;
  }

  const measurements = observation.measurements as Record<string, unknown>;
  if (
    typeof measurements.robot_id !== "string"
    || typeof measurements.x_cm !== "number"
    || typeof measurements.y_cm !== "number"
    || typeof measurements.direction !== "string"
  ) {
    return null;
  }

  const context = typeof observation.context === "object" && observation.context !== null
    ? observation.context as Record<string, unknown>
    : {};

  return {
    robotId: measurements.robot_id,
    xCm: measurements.x_cm,
    yCm: measurements.y_cm,
    direction: measurements.direction,
    location: typeof context.location === "string" ? context.location : null,
    frameOfReference: typeof context.frame_of_reference === "string"
      ? context.frame_of_reference
      : null,
  };
}

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
  const [activeTab, setActiveTab] = useState<WorkspaceTab>(tabFromPath);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 760);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = Number(window.localStorage.getItem("groundtruth-sidebar-width"));
    return Number.isFinite(saved) && saved >= 200 && saved <= 400 ? saved : 232;
  });
  const sidebarDrag = useRef<{ x: number; width: number } | null>(null);
  const resizeSidebar = (width: number) => {
    const next = Math.max(200, Math.min(400, window.innerWidth - 500, width));
    setSidebarWidth(next);
    window.localStorage.setItem("groundtruth-sidebar-width", String(next));
  };
  const [loadedScenario, setLoadedScenario] = useState<"scenario-a" | "scenario-b" | null>(null);
  const [graphSidebarTarget, setGraphSidebarTarget] = useState<HTMLDivElement | null>(null);
  const conversationEnd = useRef<HTMLDivElement>(null);
  const questionInput = useRef<HTMLTextAreaElement>(null);
  const sidebarToggle = useRef<HTMLButtonElement>(null);

  const refresh = useCallback(async () => {
    try {
      const apiHealth = await api.health();
      setHealth(apiHealth);
      const currentState = await api.state();
      setState(currentState);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not connect to the Python Agent API.");
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    const onPopState = () => setActiveTab(tabFromPath());
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && window.innerWidth <= 760) {
        setSidebarOpen(false);
        sidebarToggle.current?.focus();
      }
    };
    const compact = window.matchMedia("(max-width: 760px)");
    const onBreakpoint = () => setSidebarOpen(!compact.matches);
    window.addEventListener("popstate", onPopState);
    window.addEventListener("keydown", onEscape);
    compact.addEventListener("change", onBreakpoint);
    return () => {
      window.removeEventListener("popstate", onPopState);
      window.removeEventListener("keydown", onEscape);
      compact.removeEventListener("change", onBreakpoint);
    };
  }, []);
  useEffect(() => {
    if (activeTab === "chat") conversationEnd.current?.scrollIntoView({ block: "end" });
  }, [state?.history.length, activeTab, action]);

  const selectTab = (tab: WorkspaceTab) => {
    setActiveTab(tab);
    window.history.pushState({}, "", tab === "chat" ? "/" : `/${tab}`);
    if (window.innerWidth <= 760) setSidebarOpen(false);
  };
  const runQuestion = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!question.trim() || health?.status !== "ok" || action) return;
    setAction("ask");
    setError(null);
    try {
      setState(await api.ask(question.trim()));
      setQuestion("");
      requestAnimationFrame(() => questionInput.current?.focus());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The Agent request failed.");
    } finally { setAction(null); }
  };
  const loadScenario = async (scenario: "scenario-a" | "scenario-b") => {
    if (!health || action) return;
    setAction(scenario);
    setError(null);
    try {
      const updatedState = await api.loadScenario(scenario);
      setState(updatedState);
      setQuestion(updatedState.suggested_question);
      setLoadedScenario(scenario);
      if (window.innerWidth <= 760) setSidebarOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The scenario could not be loaded.");
    } finally { setAction(null); }
  };
  const newChat = async () => {
    if (!health || action) return;
    setAction("new-chat");
    setError(null);
    try {
      setState(await api.newChat());
      setQuestion("");
      selectTab("chat");
      requestAnimationFrame(() => questionInput.current?.focus());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start a new chat.");
    } finally { setAction(null); }
  };
  const resetAgent = async () => {
    if (!health || action) return;
    setAction("reset");
    setError(null);
    try {
      setState(await api.reset());
      setQuestion("");
      setLoadedScenario(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Agent state could not be reset.");
    } finally { setAction(null); }
  };
  const busy = action !== null;
  const backendReachable = health?.status === "ok" || health?.status === "degraded";
  const providerAvailable = health?.status === "ok";

  return (
    <div className={`workspace ${sidebarOpen ? "sidebar-visible" : "sidebar-hidden"}`} style={{ "--sidebar-width": `${sidebarWidth}px` } as React.CSSProperties}>
      {sidebarOpen && <button className="workspace-backdrop" type="button" aria-label="Close sidebar" onClick={() => setSidebarOpen(false)} />}
      <aside className="workspace-sidebar" aria-label="Agent workspace" style={!sidebarOpen ? { visibility: "hidden" } : undefined}>
        <div className="workspace-brand">
          <BrainCircuit size={25} strokeWidth={1.6} aria-hidden="true" />
          <span>GroundTruth</span>
          <button type="button" className="icon-button" aria-label="Hide sidebar" onClick={() => { setSidebarOpen(false); requestAnimationFrame(() => sidebarToggle.current?.focus()); }}><PanelLeftClose size={18} /></button>
        </div>
        <div className="workspace-new-chat">
          <button type="button" className="new-chat-button" onClick={() => void newChat()} disabled={!backendReachable || busy}>
            {action === "new-chat" ? <LoaderCircle size={18} className="spin" /> : <Plus size={18} />}<span>New chat</span>
          </button>
        </div>
        <nav className="workspace-nav" aria-label="Workspace tabs">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button key={id} type="button" className={`workspace-nav-item ${activeTab === id ? "active" : ""}`} aria-current={activeTab === id ? "page" : undefined} onClick={() => selectTab(id)}>
              <Icon size={19} strokeWidth={1.6} /><span>{label}</span>
            </button>
          ))}
        </nav>
        {activeTab === "chat" && <div className="workspace-scenarios" role="group" aria-label="Scenario presets">
          {(["scenario-a", "scenario-b"] as const).map((scenario, index) => (
            <button key={scenario} type="button" className={`workspace-nav-item ${loadedScenario === scenario ? "scenario-loaded" : ""}`} onClick={() => void loadScenario(scenario)} disabled={!backendReachable || busy} aria-pressed={loadedScenario === scenario}>
              {action === scenario ? <LoaderCircle size={18} className="spin" /> : <FlaskConical size={18} strokeWidth={1.6} />}
              <span>Load Scenario {index === 0 ? "A" : "B"}</span>
              {loadedScenario === scenario && <Check size={15} className="scenario-check" />}
            </button>
          ))}
        </div>}
        {activeTab === "graph" && <div ref={setGraphSidebarTarget} className="workspace-graph-controls" />}
        <div className="workspace-sidebar-footer">
          <button type="button" className="workspace-nav-item" onClick={() => void resetAgent()} disabled={!backendReachable || busy}>
            {action === "reset" ? <LoaderCircle size={18} className="spin" /> : <RotateCcw size={18} strokeWidth={1.6} />}<span>Reset Agent state</span>
          </button>

        </div>
      </aside>
      {sidebarOpen && <div className="workspace-sidebar-resizer" role="separator" tabIndex={0} aria-label="Resize sidebar" aria-orientation="vertical" aria-valuemin={200} aria-valuemax={Math.max(200, Math.min(400, window.innerWidth - 500))} aria-valuenow={sidebarWidth}
        onPointerDown={(event) => { sidebarDrag.current = { x: event.clientX, width: sidebarWidth }; event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={(event) => { if (sidebarDrag.current) resizeSidebar(sidebarDrag.current.width + event.clientX - sidebarDrag.current.x); }}
        onPointerUp={() => { sidebarDrag.current = null; }} onPointerCancel={() => { sidebarDrag.current = null; }}
        onKeyDown={(event) => { if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); resizeSidebar(sidebarWidth + (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 40 : 16)); } }} />}
      <main className={`workspace-main view-${activeTab}`}>
        <header className="workspace-header">
          <div className="workspace-heading">
            <button ref={sidebarToggle} type="button" className="icon-button" aria-label={sidebarOpen ? "Hide sidebar" : "Show sidebar"} aria-expanded={sidebarOpen} onClick={() => setSidebarOpen((open) => !open)}><PanelLeftOpen size={19} /></button>
            <h1>{tabs.find((tab) => tab.id === activeTab)?.label}</h1>
            {activeTab === "chat" && <span className="workspace-agent-name">Agent</span>}
          </div>

        </header>
        {health?.status === "degraded" && health.error && <div className="error-banner" role="status"><CircleAlert size={17} /><span>{health.error}</span></div>}
        {error && <div className="error-banner" role="alert"><CircleAlert size={17} /><span>{error}</span><button type="button" onClick={() => void refresh()}>Retry connection</button><button type="button" aria-label="Dismiss error" onClick={() => setError(null)}><X size={16} /></button></div>}
        {activeTab === "chat" && <section className="chat-workspace" aria-label="Chat">
          <div className="conversation" role="log" aria-label="Conversation" aria-busy={action === "ask"}>
            <div className={`conversation-inner ${!state?.history.length ? "is-empty" : ""}`}>
              {state?.history.length ? state.history.map((response, index) => <ConversationTurn key={`${response.question}-${index}`} response={response} />) : (
                <div className="empty-conversation">
                  <div className="welcome-mark"><BrainCircuit size={34} strokeWidth={1.4} /></div>
                  <h2>What would you like to know?</h2>
                  <p>Ask about the current world.<br />Explore the evidence behind every answer.</p>
                </div>
              )}
              {action === "ask" && <div className="working-indicator" role="status"><LoaderCircle size={16} className="spin" />Running the Agent cycle…</div>}
              <div ref={conversationEnd} />
            </div>
          </div>
          <form className="composer-wrap" onSubmit={(event) => void runQuestion(event)}>
            <label className="sr-only" htmlFor="agent-question">Ask the cognitive Agent</label>
            <div className="composer">
              <textarea ref={questionInput} id="agent-question" rows={2} value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); }
              }} placeholder="Ask anything about the current world…" disabled={!providerAvailable || busy} />
              <div className="composer-footer">
                <button className="send-button" type="submit" aria-label="Send message" disabled={!providerAvailable || busy || !question.trim()}>{action === "ask" ? <LoaderCircle size={18} className="spin" /> : <ArrowUp size={18} />}</button>
              </div>
            </div>
            <p className="composer-hint">Enter to send <span>·</span> Shift + Enter for a new line</p>
          </form>
        </section>}
        {activeTab === "inspector" && <Inspector state={state} />}
        {activeTab === "graph" && <Suspense fallback={<div className="route-loading">Loading belief graph…</div>}><BeliefGraphPage embedded workspaceState={state} workspaceHealth={health} sidebarTarget={graphSidebarTarget} /></Suspense>}
      </main>
    </div>
  );
}

function ConversationTurn({ response }: { response: AgentResponse }) {
  const [showTrace, setShowTrace] = useState(false);
  const trace = response.react_trace;
  const pose = getRobotPoseSummary(response);

  return (
    <article className="conversation-turn">
      <div className="user-message">{response.question}</div>
      <div className="assistant-message">
        <div className="agent-heading">
          <div className="agent-icon"><BrainCircuit size={16} /></div>
          <div><strong>Agent</strong><span>Grounded reasoning assistant</span></div>
          <div className="status-badges-group">
            {trace && (
              <span className={`trace-source-badge source-${trace.explanation_source}`}>
                {trace.explanation_source === "llm"
                  ? "ReAct LLM"
                  : trace.explanation_source === "rule_based"
                  ? "Rule-based"
                  : "Deterministic Fallback"}
              </span>
            )}
            <span className={`status-badge status-${response.status}`}>{response.status.replaceAll("_", " ")}</span>
          </div>
        </div>

        {pose ? (
          <div className="pose-summary" aria-label="Current robot pose">
            <div className="pose-summary-heading">
              <strong>Current robot pose</strong>
              <span>{pose.robotId}</span>
            </div>
            <div className="pose-summary-grid">
              <PoseValue label="Position">x={pose.xCm.toFixed(1)} cm · y={pose.yCm.toFixed(1)} cm</PoseValue>
              <PoseValue label="Facing">{pose.direction}</PoseValue>
              <PoseValue label="Location">{pose.location ?? "Not recorded"}</PoseValue>
              <PoseValue label="Frame of reference">{pose.frameOfReference ?? "Not recorded"}</PoseValue>
            </div>
          </div>
        ) : (
          <p className="answer-text">{response.answer}</p>
        )}
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
        {response.audit_trails.length > 0 && <AuditTrailInline response={response} />}
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
        {trace && (
          <div className="react-trace-container">
            <button
              type="button"
              className="react-trace-toggle"
              onClick={() => setShowTrace((open) => !open)}
              aria-expanded={showTrace}
            >
              <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                <Sparkles size={12} />
                <span>ReAct Tool Cycle: <code>{trace.tool_name}</code></span>
              </span>
              <ChevronDown size={13} className={`trace-chevron ${showTrace ? "open" : ""}`} />
            </button>
            {showTrace && (
              <div className="react-trace-details">
                <div className="react-step-row">
                  <span className="step-label"><span>1.</span><span>Proposed Tool</span></span>
                  <div className="step-content">
                    <code>{trace.tool_name}({JSON.stringify(trace.arguments)})</code>
                    <small>Call ID: {trace.tool_call_id}</small>
                  </div>
                </div>
                <div className="react-step-row">
                  <span className="step-label"><span>2.</span><span>Approved Operations</span></span>
                  <div className="step-content">
                    {trace.approved_operations.length > 0 ? (
                      <div className="ops-list">
                        {trace.approved_operations.map((op, i) => (
                          <span key={i} className="op-tag"><Check size={11} /> {op}</span>
                        ))}
                      </div>
                    ) : (
                      <span className="empty-detail">No external operations required</span>
                    )}
                  </div>
                </div>
                <div className="react-step-row">
                  <span className="step-label"><span>3.</span><span>Executed Values &amp; Evidence</span></span>
                  <div className="step-content">
                    {trace.operations && trace.operations.length > 0 ? (
                      <div className="trace-ops-detail">
                        {trace.operations.map((op, i) => (
                          <div key={i} className="trace-op-item">
                            <div className="trace-op-header">
                              <span className={`op-status-badge status-${op.status}`}>
                                {op.ran ? <Check size={10} /> : null} {op.name} ({op.status})
                              </span>
                              {op.evidence_ids.length > 0 && (
                                <span className="trace-op-ids">
                                  IDs: {op.evidence_ids.map((id) => (
                                    <code key={id} className="id-tag">{shortId(id)}</code>
                                  ))}
                                </span>
                              )}
                            </div>
                            <div className="trace-op-summary">{op.summary}</div>
                          </div>
                        ))}
                      </div>
                    ) : response.revisions.length > 0 ? (
                      <span className="highlight-tag">{response.revisions.length} belief revision recorded</span>
                    ) : response.sensor_telemetry.length > 0 ? (
                      <span className="highlight-tag">{response.sensor_telemetry.length} live sensor reading(s)</span>
                    ) : (
                      <span className="highlight-tag">Active memory facts verified</span>
                    )}
                  </div>
                </div>
                <div className="react-step-row">
                  <span className="step-label"><span>4.</span><span>Response Synthesis</span></span>
                  <div className="step-content">
                    <span className="model-tag">Source: {trace.explanation_source} {trace.model ? `(${trace.model})` : ""}</span>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

      </div>
    </article>
  );
}

function AuditTrailInline({ response }: { response: AgentResponse }) {
  const facts = [
    ...new Map(
      response.audit_trails
        .flatMap((trail) => trail.facts)
        .map((fact) => [fact.fact_id, fact]),
    ).values(),
  ].sort((left, right) => left.created_at.localeCompare(right.created_at));
  const events = [
    ...new Map(
      response.audit_trails
        .flatMap((trail) => trail.events)
        .map((event) => [event.event_id, event]),
    ).values(),
  ];

  return (
    <section className="audit-trail-inline" aria-label="Retrieved belief history">
      <div className="audit-trail-heading">
        <Database size={15} />
        <strong>Belief history retrieved</strong>
        <span>{events.length} revision {events.length === 1 ? "event" : "events"}</span>
      </div>
      <div className="audit-fact-chain">
        {facts.map((fact, index) => (
          <div className={`audit-fact ${fact.superseded_by ? "superseded" : "active"}`} key={fact.fact_id}>
            <span>{fact.superseded_by ? "Previous belief" : "Current belief"}</span>
            <strong>{fact.subject} <code>{fact.predicate}</code> {fact.object}</strong>
            <small>{fact.source_agent} · {Math.round(fact.confidence_score * 100)}% confidence</small>
            {index < facts.length - 1 && <i aria-hidden="true">→</i>}
          </div>
        ))}
      </div>
      {events.map((event) => (
        <div className="audit-event-explanation" key={event.event_id}>
          <p>{event.reason}</p>
          <small>Policy: <code>{event.policy_rule}</code> · {formatTime(event.created_at)}</small>
        </div>
      ))}
    </section>
  );
}

function PoseValue({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="pose-value">
      <span>{label}</span>
      <strong>{children}</strong>
    </div>
  );
}

function Inspector({ state }: { state: AppState | null }) {
  const latest = state?.history.at(-1) ?? null;
  return (
    <section className="inspector" aria-label="System inspector">
      <header className="inspector-header">
        <div><h2>Inside the Agent</h2><p>Reasoning, evidence, and the current world state.</p></div>
      </header>
      <div className="inspector-content">
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
                <InspectorCard label="Active belief graph" className="table-card">
                  {state.graph.edges.length ? (
                    <div className="table-scroll"><table><thead><tr><th>Subject</th><th>Predicate</th><th>Object</th><th>Source</th></tr></thead><tbody>
                      {state.graph.edges.map((edge) => <tr key={edge.key}><td>{edge.source}</td><td>{edge.predicate}</td><td>{edge.target}</td><td>{edge.source_agent}</td></tr>)}
                    </tbody></table></div>
                  ) : <p className="empty-detail">No active edges in the graph.</p>}
                </InspectorCard>
                <LedgerTable facts={state.ledger} />
              </>
            ) : <p className="empty-detail">Agent state has not been retrieved.</p>}
          </div>
        </details>

        <details className="inspector-section" open>
          <summary><span>Tier 2 · reasoning &amp; outcome</span><ChevronDown size={16} /></summary>
          <div className="inspector-section-body">
            <ValueCard label="Response status">{latest?.status ?? "No Agent response yet."}</ValueCard>
            <ValueCard label="Plan rationale">{latest?.plan_reason ?? "No plan has run yet."}</ValueCard>
            <ValueCard label="Plan verification">{latest?.plan_verifiable === true ? "Verifiable" : latest?.plan_verifiable === false ? "Unverifiable" : "No executable plan was created."}</ValueCard>
            {latest?.react_trace && (
              <InspectorCard label="ReAct Execution Cycle">
                <div className="trace-kv">
                  <div><strong>Proposed Tool:</strong> <code>{latest.react_trace.tool_name}</code></div>
                  <div><strong>Arguments:</strong> <code>{JSON.stringify(latest.react_trace.arguments)}</code></div>
                  <div><strong>Approved Operations:</strong> {latest.react_trace.approved_operations.join(", ") || "None"}</div>
                  <div><strong>Source:</strong> {latest.react_trace.explanation_source} {latest.react_trace.model ? `(${latest.react_trace.model})` : ""}</div>
                  {latest.react_trace.operations && latest.react_trace.operations.length > 0 && (
                    <div style={{ marginTop: "8px" }}>
                      <strong style={{ fontSize: "11px", display: "block", marginBottom: "5px" }}>
                        Executed Operations &amp; Evidence:
                      </strong>
                      <div className="trace-ops-detail">
                        {latest.react_trace.operations.map((op, idx) => (
                          <div key={idx} className="trace-op-item">
                            <div className="trace-op-header">
                              <span className={`op-status-badge status-${op.status}`}>
                                {op.ran ? <Check size={10} /> : null} {op.name}
                              </span>
                              {op.evidence_ids.length > 0 && (
                                <span className="trace-op-ids">
                                  {op.evidence_ids.map((id) => (
                                    <code key={id} className="id-tag">{shortId(id)}</code>
                                  ))}
                                </span>
                              )}
                            </div>
                            <div className="trace-op-summary">{op.summary}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </InspectorCard>
            )}
            <InspectorCard label="Belief revisions and audit events">
              {state?.audit_events.length ? state.audit_events.map((event) => <AuditEventView key={event.event_id} event={event} />) : <p className="empty-detail">No audit events have been recorded.</p>}
            </InspectorCard>
            <ValueCard label="Sensor telemetry">
              {latest?.sensor_telemetry.length ? <JsonValue value={latest.sensor_telemetry} /> : "No observation has been returned yet."}
            </ValueCard>
            {latest?.audit_trails.map((trail) => (
              <InspectorCard key={trail.root_fact_id} label={`Audit chain · ${shortId(trail.root_fact_id)}`}>
                <p className="empty-detail">{trail.facts.length} related fact(s), {trail.events.length} revision event(s).</p>
              </InspectorCard>
            ))}
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
        <details className="inspector-section">
          <summary><span>Perspectives</span><ChevronDown size={16} /></summary>
          <div className="inspector-section-body">
            {latest?.perspectives
              ? Object.entries(latest.perspectives).map(([key, value]) => <ValueCard key={key} label={PERSPECTIVE_LABELS[key] ?? key}>{value}</ValueCard>)
              : <p className="empty-detail">No perspective result has been returned yet.</p>}
          </div>
        </details>
      </div>
    </section>
  );
}

function InspectorCard({ label, children, className = "" }: { label: string; children: React.ReactNode; className?: string }) {
  const contentRef = useRef<HTMLDivElement>(null);
  const contentId = useId();
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);

  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    const measure = () => {
      if (content.getBoundingClientRect().width > 0) setOverflows(content.scrollHeight > 180);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(content);
    measure();
    return () => observer.disconnect();
  }, [children]);

  return (
    <div className={`inspector-card ${className}`}>
      <p className="value-label">{label}</p>
      <div id={contentId} className={`inspector-card-preview ${overflows && !expanded ? "collapsed" : ""}`} aria-hidden={overflows && !expanded ? true : undefined}>
        <div ref={contentRef}>{children}</div>
      </div>
      {overflows && <button type="button" className="inspector-card-toggle" aria-expanded={expanded} aria-controls={contentId} onClick={() => setExpanded((value) => !value)}>
        {expanded ? "Show less" : "Show more"}<ChevronDown size={15} className={expanded ? "expanded" : ""} />
      </button>}
    </div>
  );
}

function ValueCard({ label, children }: { label: string; children: React.ReactNode }) {
  return <InspectorCard label={label}><div className="value-content">{children}</div></InspectorCard>;
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
    <InspectorCard label={`SQLite evidence ledger · ${facts.length} fact(s)`} className="table-card">
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
    </InspectorCard>
  );
}

export default App;
