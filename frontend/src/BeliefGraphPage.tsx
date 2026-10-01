import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BrainCircuit,
  ChevronRight,
  CircleAlert,
  Database,
  Filter,
  GitCommitHorizontal,
  History,
  ListTree,
  LoaderCircle,
  MessageSquareText,
  Minus,
  Move3D,
  Network,
  Orbit,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  X,
} from "lucide-react";
import { api } from "./api";
import { BeliefGraphCanvas, type CameraAction } from "./BeliefGraphCanvas";
import type { ApiHealth, AppState, AuditEvent, Fact, GraphEdge, SpatialContext } from "./types";
import "./belief-graph.css";

type GraphScope = "active" | "history";
type DemoAction = "prepare-a" | "run-a" | null;

export type DisplayFact = {
  factId: string;
  source: string;
  predicate: string;
  target: string;
  sourceAgent: string;
  confidence: number;
  observedAt: string | null;
  createdAt: string | null;
  context: SpatialContext | null;
  active: boolean;
  supersededBy: string | null;
};

function activeFact(edge: GraphEdge): DisplayFact {
  return {
    factId: edge.fact_id,
    source: edge.source,
    predicate: edge.predicate,
    target: edge.target,
    sourceAgent: edge.source_agent,
    confidence: edge.confidence_score,
    observedAt: edge.observed_at,
    createdAt: null,
    context: edge.context,
    active: true,
    supersededBy: null,
  };
}

function ledgerFact(fact: Fact): DisplayFact {
  return {
    factId: fact.fact_id,
    source: fact.subject,
    predicate: fact.predicate,
    target: fact.object,
    sourceAgent: fact.source_agent,
    confidence: fact.confidence_score,
    observedAt: fact.observed_at,
    createdAt: fact.created_at,
    context: fact.context,
    active: fact.superseded_by === null,
    supersededBy: fact.superseded_by,
  };
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function shortId(value: string): string {
  return value.length > 18 ? `${value.slice(0, 9)}…${value.slice(-5)}` : value;
}

function relevantAuditEvent(factId: string, events: AuditEvent[]): AuditEvent | null {
  return events.find((event) => event.input_fact_ids.includes(factId) || event.output_fact_ids.includes(factId)) ?? null;
}

function factMatches(fact: DisplayFact, query: string): boolean {
  if (!query) return true;
  const searchable = [fact.source, fact.predicate, fact.target, fact.factId, fact.sourceAgent].join(" ").toLowerCase();
  return searchable.includes(query.toLowerCase());
}

function GraphRail() {
  return (
    <nav className="belief-rail" aria-label="Workspace navigation">
      <a className="belief-rail-link" href="/" aria-label="Conversation" title="Conversation">
        <MessageSquareText size={18} strokeWidth={1.8} />
      </a>
      <a className="belief-rail-link active" href="/graph" aria-current="page" aria-label="Belief graph" title="Belief graph">
        <span className="belief-rail-indicator" aria-hidden="true" />
        <Network size={18} strokeWidth={1.8} />
      </a>
    </nav>
  );
}

function Sidebar({
  state,
  health,
  scope,
  query,
  open,
  searchRef,
  onScopeChange,
  onQueryChange,
  onClose,
}: {
  state: AppState | null;
  health: ApiHealth | null;
  scope: GraphScope;
  query: string;
  open: boolean;
  searchRef: React.RefObject<HTMLInputElement>;
  onScopeChange: (scope: GraphScope) => void;
  onQueryChange: (value: string) => void;
  onClose: () => void;
}) {
  const recentEvents = [...(state?.audit_events ?? [])]
    .sort((left, right) => right.created_at.localeCompare(left.created_at))
    .slice(0, 3);
  const connected = health?.status === "ok" || health?.status === "degraded";

  return (
    <aside className={`belief-sidebar ${open ? "mobile-open" : ""}`} aria-label="Belief graph controls">
      <button className="belief-mobile-close" type="button" onClick={onClose} aria-label="Close graph controls"><X size={17} /></button>
      <header className="belief-brand">
        <p>GroundTruth</p>
        <h1>I, Agent</h1>
        <div className={`belief-connection ${connected ? "connected" : "disconnected"}`}>
          <span aria-hidden="true" />
          {connected ? "Agent API connected" : "Backend not connected"}
        </div>
        <small>{health ? `Intent provider: ${health.provider_mode}.` : "Checking provider status…"}</small>
      </header>

      <section className="belief-sidebar-section" aria-labelledby="graph-view-title">
        <h2 id="graph-view-title">Graph view</h2>
        <div className="belief-scope" role="group" aria-label="Belief scope">
          <button className={scope === "active" ? "active" : ""} type="button" onClick={() => onScopeChange("active")}>Active beliefs</button>
          <button className={scope === "history" ? "active" : ""} type="button" onClick={() => onScopeChange("history")}>Belief history</button>
        </div>
        <label className="belief-search" htmlFor="belief-search">
          <Search size={14} aria-hidden="true" />
          <input
            ref={searchRef}
            id="belief-search"
            type="search"
            placeholder="Find node, predicate, fact ID"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
          />
          <kbd>/</kbd>
        </label>
      </section>

      <section className="belief-sidebar-section" aria-labelledby="graph-summary-title">
        <div className="belief-section-heading">
          <h2 id="graph-summary-title">Current graph</h2>
          <span>world v{state?.environment.world_version ?? "–"}</span>
        </div>
        <div className="belief-counts">
          <div><strong>{state?.graph.node_count ?? 0}</strong><span>Active nodes</span></div>
          <div><strong>{state?.graph.edge_count ?? 0}</strong><span>Active facts</span></div>
        </div>
      </section>

      <section className="belief-sidebar-section" aria-labelledby="graph-changes-title">
        <div className="belief-section-heading">
          <h2 id="graph-changes-title">Recent changes</h2>
          <span className="belief-live"><i aria-hidden="true" />Live</span>
        </div>
        {recentEvents.length ? (
          <ol className="belief-change-list">
            {recentEvents.map((event) => (
              <li key={event.event_id}>
                <i aria-hidden="true" />
                <div><p>{event.reason}</p><small>{event.policy_rule} · {formatTime(event.created_at)}</small></div>
              </li>
            ))}
          </ol>
        ) : <p className="belief-sidebar-empty">No belief revisions have been recorded.</p>}
      </section>

      <section className="belief-sidebar-section belief-legend" aria-labelledby="graph-legend-title">
        <h2 id="graph-legend-title">Legend</h2>
        <ul>
          <li><i className="node" />Entity or value node</li>
          <li><i className="edge" />Active stored fact</li>
          <li><i className="edge superseded" />Superseded fact in history</li>
          <li><i className="changed" />New or revised fact</li>
        </ul>
      </section>
    </aside>
  );
}

function FactDetails({
  fact,
  auditEvent,
  nodeId,
  nodeFacts,
  open,
  onClose,
  onOpenList,
  onSelectFact,
}: {
  fact: DisplayFact | null;
  auditEvent: AuditEvent | null;
  nodeId: string | null;
  nodeFacts: DisplayFact[];
  open: boolean;
  onClose: () => void;
  onOpenList: () => void;
  onSelectFact: (factId: string) => void;
}) {
  return (
    <aside className={`belief-details ${open ? "open" : ""}`} aria-label="Selected belief details">
      <header>
        <div><p>Details inspector</p><small>{fact ? "Selected stored fact" : nodeId ? "Selected graph node" : "Select a graph node or edge"}</small></div>
        <button type="button" onClick={onClose} aria-label="Close details"><X size={17} /></button>
      </header>
      {fact ? (
        <div className="belief-details-body">
          <div className="belief-detail-title">
            <div><span className={`belief-state ${fact.active ? "active" : "superseded"}`}>{fact.active ? "Active fact" : "Superseded fact"}</span><h2>{fact.predicate.replaceAll("_", " ")}</h2></div>
            <GitCommitHorizontal size={22} aria-hidden="true" />
          </div>
          <p className="belief-detail-note">This is a persisted belief with source and observation provenance. It does not expose hidden model reasoning.</p>

          <dl className="belief-claim">
            <div><dt>Subject</dt><dd>{fact.source}</dd></div>
            <div><dt>Predicate</dt><dd>{fact.predicate}</dd></div>
            <div><dt>Object</dt><dd>{fact.target}</dd></div>
          </dl>

          <section className="belief-detail-section">
            <h3>Provenance</h3>
            <dl className="belief-provenance">
              <div><dt>Fact ID</dt><dd title={fact.factId}>{shortId(fact.factId)}</dd></div>
              <div><dt>Source agent</dt><dd>{fact.sourceAgent}</dd></div>
              <div><dt>Confidence</dt><dd><span className="belief-confidence"><i style={{ width: `${Math.round(fact.confidence * 100)}%` }} /></span>{Math.round(fact.confidence * 100)}%</dd></div>
              <div><dt>Observed</dt><dd>{formatTime(fact.observedAt)}</dd></div>
            </dl>
          </section>

          <section className="belief-detail-section">
            <h3>Spatial context</h3>
            <div className="belief-context-grid">
              <div><span>Location</span><strong>{fact.context?.location ?? "Not recorded"}</strong></div>
              <div><span>Reference frame</span><strong>{fact.context?.frame_of_reference ?? "Not recorded"}</strong></div>
              <div><span>Observer</span><strong>{fact.context?.observer ?? "Not recorded"}</strong></div>
              <div><span>World version</span><strong>{fact.context?.world_version ?? "Not recorded"}</strong></div>
            </div>
          </section>

          {(auditEvent || fact.supersededBy) && (
            <section className="belief-detail-section belief-revision">
              <h3>Revision</h3>
              <div>
                <History size={15} aria-hidden="true" />
                <p>{auditEvent?.reason ?? `Superseded by ${shortId(fact.supersededBy ?? "")}`}</p>
                {auditEvent && <small>Policy: {auditEvent.policy_rule}</small>}
              </div>
            </section>
          )}

          <button className="belief-list-button" type="button" onClick={onOpenList}><ListTree size={15} />Open accessible fact list</button>
        </div>
      ) : nodeId ? (
        <div className="belief-details-body">
          <div className="belief-detail-title">
            <div><span className="belief-state active">Graph node</span><h2>{nodeId}</h2></div>
            <Network size={22} aria-hidden="true" />
          </div>
          <p className="belief-detail-note">
            This node appears in {nodeFacts.length} stored {nodeFacts.length === 1 ? "fact" : "facts"}. Select a relationship to inspect its source, confidence, time, and spatial context.
          </p>
          <section className="belief-detail-section">
            <h3>Connected beliefs</h3>
            <div className="belief-node-facts">
              {nodeFacts.map((connectedFact) => (
                <button key={connectedFact.factId} type="button" onClick={() => onSelectFact(connectedFact.factId)}>
                  <strong>{connectedFact.source === nodeId ? connectedFact.predicate : `is ${connectedFact.predicate} of`}</strong>
                  <span>{connectedFact.source === nodeId ? connectedFact.target : connectedFact.source}</span>
                  <small>{connectedFact.sourceAgent} · {Math.round(connectedFact.confidence * 100)}%</small>
                  <ChevronRight size={14} aria-hidden="true" />
                </button>
              ))}
            </div>
          </section>
          <button className="belief-list-button" type="button" onClick={onOpenList}><ListTree size={15} />Open accessible fact list</button>
        </div>
      ) : (
        <div className="belief-no-selection">
          <Network size={28} />
          <p>Select a graph node or edge, or use the fact list to inspect stored evidence.</p>
        </div>
      )}
    </aside>
  );
}

function AccessibleFactList({
  facts,
  selectedFactId,
  open,
  onSelect,
  onClose,
}: {
  facts: DisplayFact[];
  selectedFactId: string | null;
  open: boolean;
  onSelect: (factId: string) => void;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div className="belief-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="belief-dialog" role="dialog" aria-modal="true" aria-labelledby="fact-list-title">
        <header><div><h2 id="fact-list-title">Stored belief facts</h2><p>Keyboard-accessible view of the facts currently shown in the graph.</p></div><button type="button" onClick={onClose} aria-label="Close fact list"><X size={18} /></button></header>
        <div className="belief-fact-list">
          {facts.length ? facts.map((fact) => (
            <button
              type="button"
              className={fact.factId === selectedFactId ? "selected" : ""}
              key={fact.factId}
              onClick={() => { onSelect(fact.factId); onClose(); }}
            >
              <span className={`belief-state ${fact.active ? "active" : "superseded"}`}>{fact.active ? "Active" : "Superseded"}</span>
              <strong>{fact.source} <em>{fact.predicate}</em> {fact.target}</strong>
              <small>{fact.sourceAgent} · {Math.round(fact.confidence * 100)}% · {formatTime(fact.observedAt)}</small>
              <ChevronRight size={16} aria-hidden="true" />
            </button>
          )) : <p className="belief-dialog-empty">No facts match the current view and search.</p>}
        </div>
      </section>
    </div>
  );
}

export default function BeliefGraphPage() {
  const [state, setState] = useState<AppState | null>(null);
  const [health, setHealth] = useState<ApiHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [scope, setScope] = useState<GraphScope>("active");
  const [query, setQuery] = useState("");
  const [selectedFactId, setSelectedFactId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [highlightedFactIds, setHighlightedFactIds] = useState<Set<string>>(new Set());
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [factListOpen, setFactListOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [interactionMode, setInteractionMode] = useState<"orbit" | "pan">("orbit");
  const [demoAction, setDemoAction] = useState<DemoAction>(null);
  const [cameraAction, setCameraAction] = useState<CameraAction>({ id: 0, type: "reset" });
  const knownActiveIds = useRef<Set<string> | null>(null);
  const knownAuditIds = useRef<Set<string> | null>(null);
  const highlightTimer = useRef<number | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async (initial = false) => {
    try {
      const [nextHealth, nextState] = await Promise.all([api.health(), api.state()]);
      const nextIds = new Set(nextState.graph.edges.map((edge) => edge.fact_id));
      const nextAuditIds = new Set(nextState.audit_events.map((event) => event.event_id));
      if (knownActiveIds.current) {
        const changed = new Set([...nextIds].filter((id) => !knownActiveIds.current?.has(id)));
        nextState.audit_events.forEach((event) => {
          if (!knownAuditIds.current?.has(event.event_id)) {
            event.output_fact_ids.forEach((id) => { if (nextIds.has(id)) changed.add(id); });
          }
        });
        if (changed.size) {
          if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current);
          setHighlightedFactIds(changed);
          highlightTimer.current = window.setTimeout(() => {
            setHighlightedFactIds(new Set());
            highlightTimer.current = null;
          }, 5500);
        }
      }
      knownActiveIds.current = nextIds;
      knownAuditIds.current = nextAuditIds;
      setHealth(nextHealth);
      setState(nextState);
      setError(null);
      setLastUpdated(new Date());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load the belief graph.");
    } finally {
      if (initial) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh(true);
    const interval = window.setInterval(() => void refresh(), 4000);
    return () => {
      window.clearInterval(interval);
      if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current);
    };
  }, [refresh]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "/" && document.activeElement?.tagName !== "INPUT") {
        event.preventDefault();
        setSidebarOpen(true);
        window.setTimeout(() => searchRef.current?.focus(), 0);
      }
      if (event.key === "Escape") {
        setFactListOpen(false);
        setSidebarOpen(false);
        setDetailsOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  const allFacts = useMemo(
    () => scope === "active"
      ? (state?.graph.edges ?? []).map(activeFact)
      : (state?.ledger ?? []).map(ledgerFact),
    [scope, state],
  );
  const facts = useMemo(() => allFacts.filter((fact) => factMatches(fact, query.trim())), [allFacts, query]);
  const nodes = useMemo(() => {
    const values = new Set<string>();
    facts.forEach((fact) => { values.add(fact.source); values.add(fact.target); });
    if (!query.trim() && scope === "active") state?.graph.nodes.forEach((node) => values.add(node));
    return [...values].sort();
  }, [facts, query, scope, state]);
  const selectedFact = facts.find((fact) => fact.factId === selectedFactId)
    ?? allFacts.find((fact) => fact.factId === selectedFactId)
    ?? null;
  const selectedAuditEvent = selectedFact && state
    ? relevantAuditEvent(selectedFact.factId, state.audit_events)
    : null;
  const selectedNodeFacts = selectedNodeId
    ? facts.filter((fact) => fact.source === selectedNodeId || fact.target === selectedNodeId)
    : [];

  const selectFact = useCallback((factId: string) => {
    setSelectedFactId(factId);
    setSelectedNodeId(null);
    setDetailsOpen(true);
  }, []);

  const selectNode = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
    setSelectedFactId(null);
    setDetailsOpen(true);
  }, []);

  const prepareScenarioA = async () => {
    if (demoAction) return;
    setDemoAction("prepare-a");
    setError(null);
    try {
      const nextState = await api.loadScenario("scenario-a");
      knownActiveIds.current = new Set(nextState.graph.edges.map((edge) => edge.fact_id));
      knownAuditIds.current = new Set(nextState.audit_events.map((event) => event.event_id));
      setHighlightedFactIds(new Set());
      setSelectedFactId(null);
      setSelectedNodeId(null);
      setDetailsOpen(false);
      setState(nextState);
      setLastUpdated(new Date());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Scenario A could not be prepared.");
    } finally {
      setDemoAction(null);
    }
  };

  const runScenarioA = async () => {
    if (demoAction || !state?.suggested_question) return;
    setDemoAction("run-a");
    setError(null);
    try {
      const nextState = await api.ask(state.suggested_question);
      const previousIds = knownActiveIds.current ?? new Set<string>();
      const nextIds = new Set(nextState.graph.edges.map((edge) => edge.fact_id));
      const changed = new Set([...nextIds].filter((id) => !previousIds.has(id)));
      knownActiveIds.current = nextIds;
      knownAuditIds.current = new Set(nextState.audit_events.map((event) => event.event_id));
      setHighlightedFactIds(changed);
      if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current);
      highlightTimer.current = window.setTimeout(() => {
        setHighlightedFactIds(new Set());
        highlightTimer.current = null;
      }, 5500);
      setState(nextState);
      setLastUpdated(new Date());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The Scenario A LiDAR check failed.");
    } finally {
      setDemoAction(null);
    }
  };

  const moveCamera = (type: CameraAction["type"]) => {
    setCameraAction((current) => ({ id: current.id + 1, type }));
  };

  return (
    <div className="belief-shell">
      <GraphRail />
      <Sidebar
        state={state}
        health={health}
        scope={scope}
        query={query}
        open={sidebarOpen}
        searchRef={searchRef}
        onScopeChange={setScope}
        onQueryChange={setQuery}
        onClose={() => setSidebarOpen(false)}
      />

      <main className="belief-main">
        <header className="belief-topbar">
          <div><div className="belief-title-row"><h1>Network / Belief Graph</h1><span>Observability</span></div><p>Stored beliefs and provenance from the active NetworkX graph. No hidden model reasoning.</p></div>
          <div className="belief-top-actions">
            <span><RefreshCw size={13} className={loading ? "spin" : ""} />{lastUpdated ? `Refreshed ${lastUpdated.toLocaleTimeString()}` : "Waiting for graph"}</span>
            <button className="belief-demo-button" type="button" onClick={() => void prepareScenarioA()} disabled={demoAction !== null}>
              {demoAction === "prepare-a" ? <LoaderCircle size={15} className="spin" /> : <Database size={15} />}
              {demoAction === "prepare-a" ? "Loading baseline…" : "1 · Load Scenario A"}
            </button>
            <button className="belief-demo-button primary" type="button" onClick={() => void runScenarioA()} disabled={demoAction !== null || !state?.suggested_question || !state.environment.scenario_name?.startsWith("Scenario A")}>
              {demoAction === "run-a" ? <LoaderCircle size={15} className="spin" /> : <Play size={15} />}
              {demoAction === "run-a" ? "Checking LiDAR…" : "2 · Run LiDAR check"}
            </button>
            <button className="belief-filter-button" type="button" onClick={() => setSidebarOpen(true)}><Filter size={15} />Filters</button>
            <button type="button" onClick={() => setFactListOpen(true)}><ListTree size={15} />Fact list</button>
          </div>
        </header>

        {error && <div className="belief-error" role="alert"><CircleAlert size={17} /><span>{error}</span><button type="button" onClick={() => void refresh()}><RefreshCw size={14} />Retry</button></div>}

        <section className="belief-stage" aria-label="Interactive three-dimensional belief graph">
          <div className="belief-grid" aria-hidden="true" />
          {loading ? (
            <div className="belief-stage-state" role="status"><LoaderCircle size={24} className="spin" /><strong>Loading the current belief graph</strong><span>Reading active facts from the agent API.</span></div>
          ) : nodes.length && facts.length ? (
            <BeliefGraphCanvas
              facts={facts}
              nodes={nodes}
              interactionMode={interactionMode}
              selectedFactId={selectedFactId}
              selectedNodeId={selectedNodeId}
              highlightedFactIds={highlightedFactIds}
              cameraAction={cameraAction}
              onSelectFact={selectFact}
              onSelectNode={selectNode}
            />
          ) : (
            <div className="belief-stage-state"><BrainCircuit size={27} /><strong>{query ? "No beliefs match this search" : "No stored beliefs yet"}</strong><span>{query ? "Try another entity, predicate, source, or fact ID." : "Load a scenario to seed stored beliefs. A question changes this graph only when it creates or revises a persisted fact."}</span>{query && <button type="button" onClick={() => setQuery("")}>Clear search</button>}</div>
          )}

          <div className="belief-orbit-hint">
            {interactionMode === "orbit"
              ? "Drag to orbit · Right drag to pan · Scroll to zoom"
              : "Drag to pan across X and Y · Right drag to orbit · Scroll to zoom"}
          </div>
          <div className="belief-camera-controls" aria-label="Graph camera controls">
            <button
              className={interactionMode === "orbit" ? "active" : ""}
              type="button"
              aria-label="Use orbit controls"
              aria-pressed={interactionMode === "orbit"}
              title="Orbit in 3D"
              onClick={() => setInteractionMode("orbit")}
            ><Orbit size={15} /></button>
            <button
              className={interactionMode === "pan" ? "active" : ""}
              type="button"
              aria-label="Use pan controls"
              aria-pressed={interactionMode === "pan"}
              title="Pan across X and Y"
              onClick={() => setInteractionMode("pan")}
            ><Move3D size={15} /></button>
            <span aria-hidden="true" />
            <button type="button" aria-label="Zoom out" title="Zoom out" onClick={() => moveCamera("zoom-out")}><Minus size={15} /></button>
            <button type="button" aria-label="Zoom in" title="Zoom in" onClick={() => moveCamera("zoom-in")}><Plus size={15} /></button>
            <span aria-hidden="true" />
            <button type="button" aria-label="Reset graph view" title="Reset view" onClick={() => moveCamera("reset")}><RotateCcw size={15} /></button>
          </div>
          <div className="belief-webgl-note">Three.js view · keyboard fact list available</div>
        </section>
      </main>

      <FactDetails
        fact={selectedFact}
        auditEvent={selectedAuditEvent}
        nodeId={selectedNodeId}
        nodeFacts={selectedNodeFacts}
        open={detailsOpen}
        onClose={() => { setDetailsOpen(false); setSelectedFactId(null); setSelectedNodeId(null); }}
        onOpenList={() => setFactListOpen(true)}
        onSelectFact={selectFact}
      />

      {(sidebarOpen || detailsOpen) && <button className="belief-mobile-backdrop" type="button" aria-label="Close open panel" onClick={() => { setSidebarOpen(false); setDetailsOpen(false); }} />}
      <AccessibleFactList facts={facts} selectedFactId={selectedFactId} open={factListOpen} onSelect={selectFact} onClose={() => setFactListOpen(false)} />
    </div>
  );
}
