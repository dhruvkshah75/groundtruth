import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { highlightedFacts } from "./beliefGraphAppearance";
import {
  BrainCircuit,
  ChevronRight,
  CircleAlert,
  Database,
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
  X,
} from "lucide-react";
import { api } from "./api";
import { useMotionPresence } from "./useMotionPresence";
import { displayText, formatTimestamp } from "./display";
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

const formatTime = formatTimestamp;

function relevantAuditEvent(factId: string, events: AuditEvent[]): AuditEvent | null {
  return events.find((event) => event.input_fact_ids.includes(factId) || event.output_fact_ids.includes(factId)) ?? null;
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

function GraphControls({ state, scope, onScopeChange }: {
  state: AppState | null;
  scope: GraphScope;
  onScopeChange: (scope: GraphScope) => void;
}) {
  return <div className="graph-workspace-controls" aria-label="Belief graph controls">
    <section className="belief-sidebar-section" aria-labelledby="graph-view-title">
      <h2 id="graph-view-title">Graph view</h2>
      <div className="belief-scope" data-scope={scope} role="group" aria-label="Belief scope">
        <button className={scope === "active" ? "active" : ""} aria-pressed={scope === "active"} type="button" onClick={() => onScopeChange("active")}>Active beliefs</button>
        <button className={scope === "history" ? "active" : ""} aria-pressed={scope === "history"} type="button" onClick={() => onScopeChange("history")}>Belief history</button>
      </div>
    </section>
    <section className="belief-sidebar-section" aria-labelledby="graph-summary-title">
      <div className="belief-section-heading"><h2 id="graph-summary-title">Current graph</h2><span>world v{state?.environment.world_version ?? "–"}</span></div>
      <div className="belief-counts"><div><strong>{state?.graph.node_count ?? 0}</strong><span>Active nodes</span></div><div><strong>{state?.graph.edge_count ?? 0}</strong><span>Active facts</span></div></div>
    </section>
    <section className="belief-sidebar-section belief-legend" aria-labelledby="graph-legend-title">
      <h2 id="graph-legend-title">Legend</h2>
      <ul><li><i className="node" />Entity or value node</li><li><i className="edge" />Active stored fact</li><li><i className="edge superseded" />Superseded fact in history</li><li><i className="changed" />New or revised fact</li></ul>
    </section>
  </div>;
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
    <aside className={`belief-details ${open ? "open" : ""}`} aria-label="Selected belief details" aria-hidden={!open} ref={(element) => { if (element) element.inert = !open; }}>
      <div className="belief-panel-content t-panel-slide" data-open={open}>
      <header>
        <div><p>Details inspector</p><small>{fact ? "Selected stored fact" : nodeId ? "Selected graph node" : "Select a graph node or edge"}</small></div>
        <button type="button" onClick={onClose} aria-label="Close details"><X size={17} /></button>
      </header>
      {fact ? (
        <div className="belief-details-body">
          <div className="belief-detail-title">
            <div><span className={`belief-state ${fact.active ? "active" : "superseded"}`}>{fact.active ? "Active fact" : "Superseded fact"}</span><h2>{displayText(fact.predicate)}</h2></div>
            <GitCommitHorizontal size={22} aria-hidden="true" />
          </div>
          <p className="belief-detail-note">This is a persisted belief with source and observation provenance. It does not expose hidden model reasoning.</p>

          <dl className="belief-claim">
            <div><dt>Subject</dt><dd>{displayText(fact.source)}</dd></div>
            <div><dt>Predicate</dt><dd>{displayText(fact.predicate)}</dd></div>
            <div><dt>Object</dt><dd>{displayText(fact.target)}</dd></div>
          </dl>

          <section className="belief-detail-section">
            <h3>Provenance</h3>
            <dl className="belief-provenance">
              <div><dt>Source Agent</dt><dd>{displayText(fact.sourceAgent)}</dd></div>
              <div><dt>Confidence</dt><dd><span className="belief-confidence"><i style={{ width: `${Math.round(fact.confidence * 100)}%` }} /></span>{Math.round(fact.confidence * 100)}%</dd></div>
              <div><dt>Observed</dt><dd>{formatTime(fact.observedAt)}</dd></div>
            </dl>
          </section>

          <section className="belief-detail-section">
            <h3>Spatial context</h3>
            <div className="belief-context-grid">
              <div><span>Location</span><strong>{displayText(fact.context?.location ?? "Not recorded")}</strong></div>
              <div><span>Reference frame</span><strong>{displayText(fact.context?.frame_of_reference ?? "Not recorded")}</strong></div>
              <div><span>Observer</span><strong>{displayText(fact.context?.observer ?? "Not recorded")}</strong></div>
              <div><span>World version</span><strong>{fact.context?.world_version ?? "Not recorded"}</strong></div>
            </div>
          </section>

          {(auditEvent || fact.supersededBy) && (
            <section className="belief-detail-section belief-revision">
              <h3>Revision</h3>
              <div>
                <History size={15} aria-hidden="true" />
                <p>{displayText(auditEvent?.reason ?? "This belief has been superseded.")}</p>
                {auditEvent && <small>Policy: {displayText(auditEvent.policy_rule)}</small>}
              </div>
            </section>
          )}

          <button className="belief-list-button" type="button" onClick={onOpenList}><ListTree size={15} />Open accessible fact list</button>
        </div>
      ) : nodeId ? (
        <div className="belief-details-body">
          <div className="belief-detail-title">
            <div><span className="belief-state active">Graph node</span><h2>{displayText(nodeId)}</h2></div>
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
                  <strong>{displayText(connectedFact.source === nodeId ? connectedFact.predicate : `is ${connectedFact.predicate} of`)}</strong>
                  <span>{displayText(connectedFact.source === nodeId ? connectedFact.target : connectedFact.source)}</span>
                  <small>{displayText(connectedFact.sourceAgent)} · {Math.round(connectedFact.confidence * 100)}%</small>
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
      </div>
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
  const dialogRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const { present, phase } = useMotionPresence(open, "--modal-close-dur");

  useEffect(() => {
    if (!present) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    closeButtonRef.current?.focus();

    return () => {
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, [present]);

  if (!present) return null;
  const keepFocusInDialog = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Tab") return;
    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  return (
    <div className="belief-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialogRef} className={`belief-dialog t-modal ${phase === "open" ? "is-open" : phase === "exit" ? "is-closing" : ""}`} role="dialog" aria-modal="true" aria-labelledby="fact-list-title" onKeyDown={keepFocusInDialog}>
        <header><div><h2 id="fact-list-title">Stored belief facts</h2><p>Keyboard-accessible view of the facts currently shown in the graph.</p></div><button ref={closeButtonRef} type="button" onClick={onClose} aria-label="Close fact list"><X size={18} /></button></header>
        <div className="belief-fact-list">
          {facts.length ? facts.map((fact) => (
            <button
              type="button"
              className={fact.factId === selectedFactId ? "selected" : ""}
              key={fact.factId}
              onClick={() => { onSelect(fact.factId); onClose(); }}
            >
              <span className={`belief-state ${fact.active ? "active" : "superseded"}`}>{fact.active ? "Active" : "Superseded"}</span>
              <strong>{displayText(fact.source)} <em>{displayText(fact.predicate)}</em> {displayText(fact.target)}</strong>
              <small>{displayText(fact.sourceAgent)} · {Math.round(fact.confidence * 100)}% · {formatTime(fact.observedAt)}</small>
              <ChevronRight size={16} aria-hidden="true" />
            </button>
          )) : <p className="belief-dialog-empty">No facts in the current view.</p>}
        </div>
      </section>
    </div>
  );
}

function RecentChanges({ events, open, onClose }: { events: AuditEvent[]; open: boolean; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  const recent = [...events].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return <dialog ref={dialogRef} className="belief-dialog belief-recent-dialog" aria-labelledby="recent-changes-title" onCancel={(event) => { event.preventDefault(); onClose(); }} onClose={onClose} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <header><div><h2 id="recent-changes-title">Recent changes</h2><p>Recorded belief revisions and audit events.</p></div><button type="button" onClick={onClose} aria-label="Close recent changes"><X size={18} /></button></header>
    <div className="belief-recent-body">{recent.length ? <ol className="belief-change-list">{recent.map((event) => <li key={event.event_id}><i aria-hidden="true" /><div><p>{displayText(event.reason)}</p><small>{displayText(event.policy_rule)} · {formatTime(event.created_at)}</small></div></li>)}</ol> : <p className="belief-dialog-empty">No belief revisions have been recorded.</p>}</div>
  </dialog>;
}

export default function BeliefGraphPage({ embedded = false, workspaceState, workspaceHealth, sidebarTarget }: {
  embedded?: boolean;
  workspaceState?: AppState | null;
  workspaceHealth?: ApiHealth | null;
  sidebarTarget?: HTMLElement | null;
}) {
  const [state, setState] = useState<AppState | null>(null);
  const [health, setHealth] = useState<ApiHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [scope, setScope] = useState<GraphScope>("active");
  const [recentOpen, setRecentOpen] = useState(false);
  const [selectedFactId, setSelectedFactId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [highlightedFactIds, setHighlightedFactIds] = useState<Set<string>>(new Set());
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [factListOpen, setFactListOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const detailsPresence = useMotionPresence(detailsOpen, "--panel-close-dur");
  const [interactionMode, setInteractionMode] = useState<"orbit" | "pan">("orbit");
  const [demoAction, setDemoAction] = useState<DemoAction>(null);
  const [cameraAction, setCameraAction] = useState<CameraAction>({ id: 0, type: "reset" });
  const knownActiveIds = useRef<Set<string> | null>(null);
  const knownAuditIds = useRef<Set<string> | null>(null);
  const highlightTimer = useRef<number | null>(null);

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
    if (workspaceState) { setState(workspaceState); setLoading(false); }
    if (workspaceHealth) setHealth(workspaceHealth);
  }, [workspaceState, workspaceHealth]);

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
      if (event.key === "Escape") {
        setFactListOpen(false);
        setRecentOpen(false);
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
  const facts = allFacts;
  const displayedHighlights = useMemo(
    () => highlightedFacts(highlightedFactIds, state?.ledger ?? [], state?.audit_events ?? []),
    [highlightedFactIds, state],
  );
  const nodes = useMemo(() => {
    const values = new Set<string>();
    facts.forEach((fact) => { values.add(fact.source); values.add(fact.target); });
    if (scope === "active") state?.graph.nodes.forEach((node) => values.add(node));
    return [...values].sort();
  }, [facts, scope, state]);
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
    <div className={`belief-shell ${embedded ? "belief-embedded" : ""} ${detailsPresence.present ? "details-visible" : ""} ${detailsOpen ? "details-open" : ""}`}>
      {!embedded && <GraphRail />}
      {embedded && sidebarTarget && createPortal(<GraphControls state={state} scope={scope} onScopeChange={setScope} />, sidebarTarget)}
      {!embedded && <aside className="belief-sidebar"><GraphControls state={state} scope={scope} onScopeChange={setScope} /></aside>}

      <section className="belief-main" aria-label="Belief network">
        <header className="belief-topbar">
          <div><div className="belief-title-row"><h1>Stored beliefs</h1><span>Live graph</span></div><p>Select a node or relationship to inspect its evidence.</p></div>
          <div className="belief-top-actions">
            <span><RefreshCw size={13} className={loading ? "spin" : ""} />{lastUpdated ? `Refreshed ${lastUpdated.toLocaleTimeString()}` : "Waiting for graph"}</span>
            {!embedded && <><button className="belief-demo-button" type="button" onClick={() => void prepareScenarioA()} disabled={demoAction !== null}>
              {demoAction === "prepare-a" ? <LoaderCircle size={15} className="spin" /> : <Database size={15} />}
              {demoAction === "prepare-a" ? "Loading baseline…" : "1 · Load Scenario A"}
            </button>
            <button className="belief-demo-button primary" type="button" onClick={() => void runScenarioA()} disabled={health?.status !== "ok" || demoAction !== null || !state?.suggested_question || !state.environment.scenario_name?.startsWith("Scenario A")} title={health?.status === "degraded" ? "Configure the live LLM or switch to offline mode to run the check." : undefined}>
              {demoAction === "run-a" ? <LoaderCircle size={15} className="spin" /> : <Play size={15} />}
              {demoAction === "run-a" ? "Checking LiDAR…" : "2 · Run LiDAR check"}
            </button>
            </>}
            <button type="button" onClick={() => setRecentOpen(true)}><History size={15} /><span>Recent changes</span></button>
            <button type="button" onClick={() => setFactListOpen(true)}><ListTree size={15} />Fact list</button>
          </div>
        </header>

        {error && <div className="belief-error" role="alert"><CircleAlert size={17} /><span>{error}</span><button type="button" onClick={() => void refresh()}><RefreshCw size={14} />Retry</button></div>}

        <section className="belief-stage" aria-label="Interactive three-dimensional belief graph">
          {loading ? (
            <div className="belief-stage-state" role="status"><LoaderCircle size={24} className="spin" /><strong>Loading the current belief graph</strong><span>Reading active facts from the Agent API.</span></div>
          ) : nodes.length && facts.length ? (
            <BeliefGraphCanvas
              facts={facts}
              nodes={nodes}
              interactionMode={interactionMode}
              selectedFactId={selectedFactId}
              selectedNodeId={selectedNodeId}
              highlightedFactIds={displayedHighlights}
              cameraAction={cameraAction}
              onSelectFact={selectFact}
              onSelectNode={selectNode}
            />
          ) : (
            <div className="belief-stage-state"><BrainCircuit size={27} /><strong>No stored beliefs yet</strong><span>Load a scenario to seed stored beliefs. A question changes this graph only when it creates or revises a persisted fact.</span></div>
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
        </section>
      </section>

      <FactDetails
        fact={selectedFact}
        auditEvent={selectedAuditEvent}
        nodeId={selectedNodeId}
        nodeFacts={selectedNodeFacts}
        open={detailsOpen && detailsPresence.phase === "open"}
        onClose={() => setDetailsOpen(false)}
        onOpenList={() => setFactListOpen(true)}
        onSelectFact={selectFact}
      />

      {!embedded && detailsOpen && <button className="belief-mobile-backdrop" type="button" aria-label="Close open panel" onClick={() => setDetailsOpen(false)} />}
      <RecentChanges events={state?.audit_events ?? []} open={recentOpen} onClose={() => setRecentOpen(false)} />
      <AccessibleFactList facts={facts} selectedFactId={selectedFactId} open={factListOpen} onSelect={selectFact} onClose={() => setFactListOpen(false)} />
    </div>
  );
}
