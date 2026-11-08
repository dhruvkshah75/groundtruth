import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Activity, ArrowUp, Camera, Check, CircleAlert, LoaderCircle, Map, Pause, Play, SkipForward, SlidersHorizontal, WifiOff, X } from "lucide-react";
import { api, ApiRequestError } from "./api";
import type {
  CameraDetection, SimulationControlRequest, SimulationDisruptRequest,
  SimulationStateResponse, StoredObservationSample,
} from "./simulationTypes";
import { useSimulation } from "./useSimulation";
import type { AgentResponse, SimulationEvidence } from "./types";
import { displayText } from "./display";
import WarehouseMap, { warehouseLabel } from "./WarehouseMap";
import "./simulation-workspace.css";

const rates = [0.25, 0.5, 1, 2] as const;
const patrolLabels = {
  moving: "Moving", turning: "Turning", blocked: "Blocked",
  waiting_for_sensor: "Waiting for sensor", complete: "Patrol complete", stalled: "Stalled",
};
const choices = [
  ["cart_block", "Block route with cart"], ["cart_clear", "Clear cart from route"],
  ["relocate", "Move parcel to station"],
  ["yellow", "Set yellow lighting"], ["neutral", "Restore neutral lighting"],
  ["camera_off", "Disable camera"], ["camera_on", "Enable camera"],
  ["lidar_off", "Disable LiDAR"], ["lidar_on", "Enable LiDAR"],
] as const;

type Choice = typeof choices[number][0];
type PresenterDisruption = SimulationDisruptRequest["disruption"];
const simTime = (ms: number) => `${(ms / 1_000).toFixed(3)} s`;

function captureAge(state: SimulationStateResponse, sample: StoredObservationSample) {
  const age = (state.sim_time_ms ?? sample.sim_time_ms) - sample.sim_time_ms;
  const freshness = state.audience?.snapshot.config.freshness_ms ?? 0;
  return `Captured at ${simTime(sample.sim_time_ms)}${age > freshness ? " · old sample" : ""}`;
}

function ObservationPanel({ state, stale }: { state: SimulationStateResponse | null; stale: boolean }) {
  const camera = state?.observation.latest.camera_detect;
  const lidar = state?.observation.latest.lidar_scan;
  const objects = camera && "measurements" in camera.result ? camera.result.measurements.objects : null;
  const detections = Array.isArray(objects) ? objects as CameraDetection[] : null;
  const distance = lidar && "measurements" in lidar.result ? lidar.result.measurements.nearest_distance_cm : null;
  const reason = camera && "reason" in camera.result ? camera.result.reason : null;
  return <section className="sim-panel" aria-labelledby="robot-observation-title">
    <div className="sim-panel-heading">
      <h2 id="robot-observation-title"><Camera size={17} aria-hidden="true" />Robot observation</h2>
      {stale && state?.run_id && <span className="sim-chip warn">Stale</span>}
    </div>
    <div className="sim-observation">
      <h3>Camera {detections && <span className="sim-count">{detections.length}</span>}</h3>
      {camera && state && <p className="sim-subtle sim-time">{captureAge(state, camera)}</p>}
      {camera?.request.target && <p className="sim-subtle">Target · {warehouseLabel(camera.request.target)}</p>}
      {reason ? <p className="sim-warning"><CircleAlert size={15} />{reason === "target_not_visible" ? "Target not visible" : "Camera unavailable"}</p>
        : detections?.length ? <ul className="sim-list sim-detections">
          {detections.map(({ object_id, apparent_color, position }) => <li key={object_id}>
            <strong>{warehouseLabel(object_id)}</strong>
            <span>{apparent_color} · {position.x_cm.toFixed(0)}, {position.y_cm.toFixed(0)} cm</span>
          </li>)}
        </ul> : <p className="sim-subtle">{detections ? "No objects detected in this capture." : "Awaiting a camera sample."}</p>}
    </div>
    <div className="sim-observation">
      <h3>Front LiDAR</h3>
      {lidar && state && <p className="sim-subtle sim-time">{captureAge(state, lidar)}</p>}
      <p className={lidar && "reason" in lidar.result ? "sim-warning" : ""}>
        {!lidar ? "Awaiting a LiDAR sample." : "reason" in lidar.result
          ? <><CircleAlert size={15} />LiDAR unavailable</>
          : typeof distance === "number" ? `Obstacle at ${distance.toFixed(0)} cm` : "No obstacle in range"}
      </p>
    </div>
    {state && (camera || lidar) && <details className="sim-details">
      <summary>Capture details</summary>
      <ul className="sim-list">
        {[camera, lidar].map((sample) => sample && <li key={sample.sample_id}>
          <strong>{sample === camera ? "Camera" : "LiDAR"} · sample {sample.sample_sequence}</strong>
          <span>{sample.observer_pose.frame_of_reference} · world {sample.world_version} · {sample.observer_pose.direction}</span>
        </li>)}
      </ul>
    </details>}
  </section>;
}

function BeliefPanel({ state, stale }: { state: SimulationStateResponse | null; stale: boolean }) {
  const changes = state?.belief.recent_changes ?? [];
  const latest = changes.at(-1);
  return <section className="sim-panel" aria-labelledby="sim-belief-title">
    <div className="sim-panel-heading">
      <h2 id="sim-belief-title"><Activity size={17} aria-hidden="true" />Agent belief</h2>
      {stale && state?.run_id && <span className="sim-chip warn">Stale</span>}
    </div>
    <div className="sim-observation">
      <p>{latest ? `${changes.length} recent evidence-backed ${changes.length === 1 ? "change" : "changes"}` : "No belief changes recorded for this run."}</p>
      {latest && <p className="sim-subtle sim-time">Latest evidence · {simTime(latest.sim_time_ms)}</p>}
    </div>
    {latest && <details className="sim-details">
      <summary>Evidence references</summary>
      <ul className="sim-list">
        {[...changes].reverse().map((change, index) => <li key={`${change.fact_id}-${index}`}>
          <strong>{change.revision_event_id ? "Revision" : "Fact"} · {simTime(change.sim_time_ms)}</strong>
          <span>Sample {change.sample_sequence} · <code>{change.fact_id}</code></span>
        </li>)}
      </ul>
    </details>}
  </section>;
}

function EvidenceTimeline({ sessionId, state, entity, onFocusFact }: { sessionId: string; state: SimulationStateResponse; entity: string; onFocusFact: (factId: string) => void }) {
  const [evidence, setEvidence] = useState<SimulationEvidence | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!state.run_id || !entity) { setEvidence(null); return; }
    let active = true;
    let timer: number;
    let controller: AbortController;
    setEvidence(null);
    setError("");
    const poll = async () => {
      controller = new AbortController();
      try {
        const result = await api.simulationEvidence({ run_id: state.run_id!, generation: state.generation }, entity, { sessionId, signal: controller.signal });
        if (active) { setEvidence(result); setError(""); }
      } catch (cause) {
        if (active && !controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Could not load evidence.");
      } finally { if (active) timer = window.setTimeout(poll, 1500); }
    };
    void poll();
    return () => { active = false; window.clearTimeout(timer); controller?.abort(); };
  }, [sessionId, state.run_id, state.generation, entity]);
  const facts = evidence?.facts ?? [];
  const revisions = (evidence?.events ?? []).map((event) => {
    const next = facts.find((fact) => event.output_fact_ids.includes(fact.fact_id));
    const previous = facts.find((fact) => event.input_fact_ids.includes(fact.fact_id));
    const time = next?.evidence.sim_time_ms;
    return { event, next, previous, time: typeof time === "number" ? time : null };
  });
  const timeline = [
    ...(evidence?.samples ?? []).map((sample) => ({ key: sample.sample_id, time: sample.sim_time_ms, sample, revision: null })),
    ...revisions.filter((revision) => revision.time !== null).map((revision) => ({ key: revision.event.event_id, time: revision.time!, sample: null, revision })),
  ].sort((a, b) => a.time - b.time || Number(!!a.revision) - Number(!!b.revision));
  const summary = evidence?.summary;
  const last = summary?.last_positive_sighting ?? summary?.last_positive_lidar_sample;
  return <section className="sim-panel sim-timeline" aria-labelledby="sim-timeline-title">
    <div className="sim-panel-heading"><h2 id="sim-timeline-title"><Activity size={17} />Belief and evidence</h2></div>
    <div className="sim-timeline-body">
      {!entity ? <p>Select an entity to inspect its evidence.</p> : error ? <p role="alert" className="sim-warning">{error}</p> : !evidence ? <p>Loading evidence…</p> : <>
        <h3>{warehouseLabel(entity)}</h3>
        <p>Stored belief · {summary?.supported_location ? displayText(summary.supported_location) : summary?.status ? displayText(summary.status) : "No stored location or route status"}</p>
        <p>Verification · {displayText(summary?.verification_status ?? "unverified")}{summary?.visibility && ` · ${displayText(summary.visibility)}`} · {displayText(summary?.freshness ?? "unverified")}</p>
        {last && <p>Last observed · {simTime(last.sim_time_ms)} · {displayText(last.sensor_id)}</p>}
        {summary?.uncertainty_reason && <p>{displayText(summary.uncertainty_reason)}</p>}
        <p className="sim-subtle">Run {evidence.run_id} · as of {simTime(evidence.sim_time_ms)}</p>
        <details className="sim-timeline-details"><summary>Observation and revision timeline ({timeline.length})</summary>
          {timeline.length ? <ol className="sim-timeline-list">{timeline.map(({ key, time, sample, revision }) => <li key={key}>
            <strong>{simTime(time)} · {revision ? "Belief revision" : displayText(sample!.sensor_id)}</strong>
            {revision ? <><p>{revision.previous && revision.next && <>{displayText(revision.previous.object)} → {displayText(revision.next.object)} · </>}{displayText(revision.event.reason)}</p><p>Rule · {displayText(revision.event.policy_rule)}</p><div className="sim-timeline-actions">{revision.previous && <button type="button" className="sim-history-button" onClick={() => onFocusFact(revision.previous!.fact_id)}>Focus earlier belief</button>}{revision.next && <button type="button" className="sim-history-button" onClick={() => onFocusFact(revision.next!.fact_id)}>Focus updated belief</button>}</div></>
              : <><p>{"reason" in sample!.result ? displayText(sample!.result.reason) : sample!.request.capability === "camera_detect" && Array.isArray(sample!.result.measurements.objects) && !(sample!.result.measurements.objects as CameraDetection[]).some((item) => item.object_id === entity) ? "Not detected in this camera capture" : "Sensor observation recorded"}</p><p>Recorded · {sample!.stored_at}</p></>}
            <details><summary>Raw record</summary><pre>{JSON.stringify(revision?.event ?? sample, null, 2)}</pre></details>
          </li>)}</ol> : <p>No observations or revisions recorded for this selection.</p>}
          {evidence.truncated && <p>Showing a bounded selection of the recorded history.</p>}
        </details>
      </>}
    </div>
  </section>;
}

function SimulationQuestions({ sessionId, state, renderAnswer, entity, onSelectEntity }: {
  sessionId: string; state: SimulationStateResponse; renderAnswer: (response: AgentResponse) => ReactNode; entity: string; onSelectEntity: (entity: string) => void;
}) {
  const entities = state.audience?.snapshot.entities ?? [];
  const routes = state.audience?.routes ?? [];
  const [question, setQuestion] = useState("");
  const [answers, setAnswers] = useState<AgentResponse[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyOverflow, setHistoryOverflow] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const request = useRef<AbortController | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const answerViewport = useRef<HTMLDivElement>(null);
  const answerList = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    let active = true;
    void api.state(sessionId).then((snapshot) => {
      if (!active || snapshot.simulation_status?.run_id !== state.run_id || snapshot.simulation_status?.generation !== state.generation) return;
      setAnswers((current) => {
        const known = new Set(current.map((item) => item.simulation_evidence?.request_id));
        return [...snapshot.history.filter((item) => item.simulation_evidence?.run_id === state.run_id && !known.has(item.simulation_evidence.request_id)), ...current];
      });
    }).catch(() => { /* A question can still be asked if history could not be loaded. */ });
    return () => { active = false; };
  }, [sessionId, state.run_id, state.generation]);
  useLayoutEffect(() => {
    const field = input.current;
    if (!field) return;
    const fit = () => { field.style.height = "auto"; field.style.height = `${field.scrollHeight}px`; };
    fit();
    let width = field.clientWidth;
    const observer = new ResizeObserver(() => { if (field.clientWidth !== width) { width = field.clientWidth; fit(); } });
    observer.observe(field);
    return () => observer.disconnect();
  }, [question]);
  useEffect(() => {
    const element = dialog.current;
    if (historyOpen && element && !element.open) element.showModal();
    if (!historyOpen && element?.open) element.close();
  }, [historyOpen]);
  useLayoutEffect(() => {
    const viewport = answerViewport.current;
    const list = answerList.current;
    if (!viewport || !list) return;
    const check = () => {
      if (viewport.scrollHeight > viewport.clientHeight + 1 || answers.length > 8) setHistoryOverflow(true);
    };
    const observer = new ResizeObserver(check);
    observer.observe(viewport);
    observer.observe(list);
    check();
    viewport.scrollTop = 0;
    return () => observer.disconnect();
  }, [answers]);

  async function ask(text: string) {
    if (!text.trim() || request.current || !state.run_id) return;
    const controller = new AbortController();
    request.current = controller;
    setPending(true);
    setError("");
    try {
      const options = { sessionId, signal: controller.signal };
      const response = await api.ask(text.trim(), options);
      const latest = response.answer;
      const current = await api.simulationState(options);
      if (controller.signal.aborted) return;
      if (!latest) throw new Error("The backend did not return the answer for this request. Restart the backend and try again.");
      if (current.run_id !== state.run_id || current.generation !== state.generation || latest.simulation_evidence?.run_id !== state.run_id) {
        throw new Error("The run changed before the answer arrived. Ask again in the current run.");
      }
      setAnswers((previous) => previous.some((item) => item.simulation_evidence?.request_id === latest.simulation_evidence?.request_id) ? previous : [...previous, latest]);
      setQuestion("");
    } catch (cause) {
      if (!controller.signal.aborted) {
        const message = cause instanceof ApiRequestError && cause.code === "provider_request_too_large"
          ? "Request limit exceeded."
          : cause instanceof ApiRequestError && cause.code === "provider_rate_limited"
            ? "Rate limit exceeded."
            : cause instanceof ApiRequestError && cause.code === "provider_unavailable"
            ? "Answer service unavailable."
            : cause instanceof Error ? cause.message : "The question could not be answered.";
        setError(message);
      }
    } finally {
      if (!controller.signal.aborted) { request.current = null; setPending(false); }
    }
  }

  return <section className="sim-panel sim-questions" aria-labelledby={`${id}-title`}>
    <div className="sim-panel-heading"><h2 id={`${id}-title`}>Ask about the run</h2></div>
    <div className="sim-question-body">
      <label className="sim-question-entity" htmlFor={`${id}-entity`}>Entity
        <select id={`${id}-entity`} value={entity} onChange={(event) => onSelectEntity(event.target.value)}>
          {entities.map((item) => <option value={item.entity_id} key={item.entity_id}>{warehouseLabel(item.entity_id)}</option>)}
          {routes.map((route) => <option value={route.route_id} key={route.route_id}>{warehouseLabel(route.route_id)}</option>)}
        </select>
      </label>
      <div className="sim-question-suggestions" role="group" aria-label="Suggested questions">
        {[`Where is ${entity} now?`, `When was ${entity} last seen?`, `Show the history of ${entity}`, `How did ${entity} change?`].map((text) =>
          <button type="button" className="sim-button" key={text} disabled={!entity || pending} onClick={() => setQuestion(text)}>{warehouseLabel(text)}</button>)}
      </div>
      <form className="sim-question-form" onSubmit={(event) => { event.preventDefault(); void ask(question); }}>
        <label className="sr-only" htmlFor={`${id}-question`}>Your question</label>
        <div className="composer">
          <textarea ref={input} id={`${id}-question`} rows={1} value={question} disabled={pending} placeholder="Ask about the current world…" onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); }
          }} />
          <button className="send-button" type="submit" aria-label="Ask" disabled={pending || !question.trim()}>{pending ? <LoaderCircle size={18} className="spin" /> : <ArrowUp size={18} />}</button>
        </div>
      </form>
      {error && <p role="alert" className="sim-inline-error"><CircleAlert size={15} aria-hidden="true" />{error}</p>}
      {historyOverflow && <button type="button" className="sim-history-button" onClick={() => setHistoryOpen(true)}>Show all questions ({answers.length})</button>}
      {answers.length > 0 && <div ref={answerViewport} className="sim-question-answer" aria-live="polite"><div ref={answerList}>
        {answers.slice(-8).reverse().map((answer) => <div key={answer.simulation_evidence?.request_id}>{renderAnswer(answer)}</div>)}
      </div></div>}
    </div>
    {createPortal(<dialog ref={dialog} className="sim-history-dialog" aria-labelledby={`${id}-history-title`} onClose={() => setHistoryOpen(false)} onClick={(event) => { if (event.target === event.currentTarget) dialog.current?.close(); }}>
      <header><div><h2 id={`${id}-history-title`}>Questions and answers</h2><p>Responses captured during this simulation run.</p></div><button type="button" aria-label="Close question history" onClick={() => dialog.current?.close()}><X size={18} /></button></header>
      <div className="sim-history-list">{answers.map((answer) => <div key={answer.simulation_evidence?.request_id}>{renderAnswer(answer)}</div>)}</div>
    </dialog>, document.body)}
  </section>;
}

function DisruptionControls({ state, disabled, submit }: {
  state: SimulationStateResponse;
  disabled: boolean;
  submit: (disruption: PresenterDisruption) => void;
}) {
  const fieldId = useId();
  const [choice, setChoice] = useState<Choice>("cart_block");
  const [parcelId, setParcelId] = useState("");
  const [stationId, setStationId] = useState("");
  const entities = state.audience?.snapshot.entities ?? [];
  const parcels = entities.filter((entity) => entity.kind === "parcel");
  const stations = entities.filter((entity) => entity.kind === "station");
  const parcel = parcels.find((entity) => entity.entity_id === parcelId) ?? parcels[0];
  const station = stations.find((entity) => entity.entity_id === stationId) ?? stations[0];

  function send() {
    if (choice === "relocate") {
      if (!parcel || !station || !state.audience) return;
      submit({
        kind: "relocate_parcel", entity_id: parcel.entity_id, duration_ms: (state.audience.snapshot.config.step_ms ?? 100) * 10,
        to: { x_cm: station.footprint.x_cm, y_cm: station.footprint.y_cm },
      });
    } else if (choice === "cart_block" || choice === "cart_clear") {
      submit({ kind: choice });
    } else if (choice === "neutral" || choice === "yellow") {
      submit({ kind: "lighting", color_cast: choice, intensity: choice === "yellow" ? 0.8 : 1 });
    } else {
      submit({ kind: "sensor_availability", capability: choice.startsWith("camera") ? "camera_detect" : "lidar_scan",
        available: choice.endsWith("_on") });
    }
  }

  return <details className="sim-disrupt">
    <summary><SlidersHorizontal size={16} aria-hidden="true" />Presenter disruptions</summary>
    <form onSubmit={(event) => { event.preventDefault(); send(); }}>
      <label><span id={`${fieldId}-disruption`}>Disruption</span>
        <select aria-labelledby={`${fieldId}-disruption`} disabled={disabled} value={choice} onChange={(event) => setChoice(event.target.value as Choice)}>
          {choices.map(([id, title]) => <option key={id} value={id}>{title}</option>)}
        </select>
      </label>
      {choice === "relocate" && <>
        <label><span id={`${fieldId}-parcel`}>Parcel</span><select aria-labelledby={`${fieldId}-parcel`} disabled={disabled} value={parcel?.entity_id ?? ""} onChange={(event) => setParcelId(event.target.value)}>
          {parcels.map((item) => <option key={item.entity_id} value={item.entity_id}>{warehouseLabel(item.entity_id)}</option>)}
        </select></label>
        <label><span id={`${fieldId}-destination`}>Destination</span><select aria-labelledby={`${fieldId}-destination`} disabled={disabled} value={station?.entity_id ?? ""} onChange={(event) => setStationId(event.target.value)}>
          {stations.map((item) => <option key={item.entity_id} value={item.entity_id}>{warehouseLabel(item.entity_id)}</option>)}
        </select></label>
      </>}
      <button className="sim-button" disabled={disabled || (choice === "relocate" && (!parcel || !station))}>Schedule disruption</button>
    </form>
  </details>;
}

export default function SimulationWorkspace({ sessionId, chatName, presenterTarget, renderAnswer, focus, onFocus }: {
  sessionId: string; chatName: string; presenterTarget: HTMLElement | null; renderAnswer: (response: AgentResponse) => ReactNode;
  focus: { runId: string; entity: string; factId?: string } | null; onFocus: (focus: { runId: string; entity: string; factId?: string }) => void;
}) {
  const { state, connection, connectionError, pending, notice, run } = useSimulation(sessionId);
  const [entity, setEntity] = useState("");
  useEffect(() => { setEntity(state?.audience?.snapshot.entities[0]?.entity_id ?? ""); }, [state?.run_id, state?.generation]);
  useEffect(() => {
    if (focus && focus.runId === state?.run_id && (state?.audience?.snapshot.entities.some((item) => item.entity_id === focus.entity) || state?.audience?.routes.some((item) => item.route_id === focus.entity))) setEntity(focus.entity);
  }, [focus, state?.run_id, state?.audience]);
  function selectEntity(value: string) {
    setEntity(value);
    if (state?.run_id) onFocus({ runId: state.run_id, entity: value });
  }
  const layout = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = layout.current;
    if (!element) return;
    const observer = new ResizeObserver(() => element.parentElement?.style.setProperty("--simulation-column-height", `${element.offsetHeight}px`));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const live = connection === "live";
  const stale = !live || state?.state === "error";
  const loaded = !!state?.run_id;
  const blocked = !live || !!pending;
  const active = state?.state === "running";
  const canAdjust = loaded && !blocked && state?.state !== "error";
  const status = connection === "disconnected" ? "Backend disconnected"
    : connection === "hidden" ? "Updates suspended"
    : connection === "refreshing" ? "Refreshing state"
    : connection === "loading" ? "Connecting to backend"
    : state?.state === "error" ? "Runtime error"
    : active ? "Running" : state?.state === "paused" ? "Paused"
    : state?.state === "completed" ? "Completed" : "No run loaded";

  function control(action: Exclude<SimulationControlRequest["action"], "reset">, rate?: typeof rates[number]) {
    const messages = { play: "Playback started.", pause: "Playback paused.", step: "Step confirmed.",
      set_rate: `Rate set to ${rate}×.` };
    void run("Applying control…", async (expected, options) => {
      try {
        return { state: await api.controlSimulation({ action, expected, playback_rate: rate }, options), message: messages[action] };
      } catch (error) {
        if (action === "set_rate" && rate === 2 && error instanceof ApiRequestError && error.code === "invalid_request") {
          throw new Error("The backend rejected 2× playback. Restart the backend to load the latest version, then try again.");
        }
        throw error;
      }
    });
  }

  function load() {
    const request_id = crypto.randomUUID();
    void run("Loading warehouse…", async (expected, options) => ({
      state: await api.loadSimulation({ preset: "warehouse_inspection_v1", expected, request_id }, options),
      message: "Warehouse loaded.",
    }));
  }

  function disrupt(disruption: PresenterDisruption) {
    const request_id = crypto.randomUUID();
    void run("Scheduling disruption…", async (expected, options) => {
      const receipt = await api.disruptSimulation({ expected, request_id, disruption }, options);
      return { state: receipt.state, message: `Disruption scheduled for ${simTime(receipt.event.at_ms)}.${receipt.state.state === "paused" ? " Play or step to apply it." : ""}` };
    });
  }

  const playStep = <div className="sim-actions" role="group" aria-label="Playback controls">
    {active
      ? <button key="playback" type="button" className="sim-button primary" onClick={() => control("pause")} disabled={!canAdjust}><Pause size={16} />Pause</button>
      : <button key="playback" type="button" className="sim-button primary" onClick={() => control("play")} disabled={!canAdjust || state?.state !== "paused"}><Play size={16} />Play</button>}
    <button type="button" className="sim-button" onClick={() => control("step")} disabled={!canAdjust || state?.state !== "paused"}><SkipForward size={16} />Step</button>
  </div>;

  const presenter = <section className="sim-presenter" aria-label="Presenter controls">
    <button type="button" className="sim-button" onClick={load} disabled={blocked || active}>
      <Map size={16} />{loaded ? "Reload preset" : "Load preset"}
    </button>
    <div className="sim-rate-field" role="group" aria-label="Playback speed">
      <span>Playback speed</span>
      <div className="sim-rates" style={{ "--selected-rate": rates.indexOf(state?.playback_rate ?? 0.5) } as React.CSSProperties}>
        {rates.map((rate) => <button type="button" key={rate} className="sim-button"
          aria-pressed={(state?.playback_rate ?? 0.5) === rate}
          onClick={() => control("set_rate", rate)} disabled={!canAdjust}>{rate}×</button>)}
      </div>
    </div>
    {loaded && state && <DisruptionControls key={state.run_id} state={state}
      disabled={!canAdjust || state.state === "completed"} submit={disrupt} />}
    {state?.state === "completed" && <p className="sim-presenter-message">Run finished. Reload the preset to schedule disruptions.</p>}
    {presenterTarget && (pending || notice) && <div className={`sim-presenter-message ${notice?.error ? "sim-warning" : ""}`} role="status">
      {pending || notice?.text}
    </div>}
  </section>;

  return <section className="simulation-workspace" aria-label="Warehouse simulation">
    <div className="sim-content">
      <div className="sim-heading">
        <div><h2>Warehouse inspection</h2><p>Session · {chatName}</p></div>
        {!(live && state?.state === "completed") && <div className={`sim-chip ${live && active ? "success" : !live || state?.state === "error" ? "warn" : ""}`} role="status">
          {connection === "disconnected" ? <WifiOff size={15} /> : active && live ? <Play size={13} /> : <Activity size={15} />}{status}
        </div>}
      </div>
      <div className="sim-toolbar">
        <div className="sim-clock">
          <span className="sim-clock-label">Simulation time</span>
          <strong>{state?.sim_time_ms == null ? "—" : simTime(state.sim_time_ms)}</strong>
        </div>
        {playStep}
      </div>
      {presenterTarget ? createPortal(<><h2>Presenter controls</h2><p>30 s ≈ 1 presentation min at 0.5×</p>{presenter}</>, presenterTarget)
        : <details className="sim-presenter-inline" open={!loaded || undefined}>
          <summary>Presenter controls</summary>
          <p>30 s ≈ 1 presentation min at 0.5×</p>
          {presenter}
        </details>}
      <div className="sim-feedback" role="status">
        {pending ? <span>{pending}</span> : notice && (state?.state !== "completed" || notice.error) && <span className={notice.error ? "sim-warning" : ""}>
          {notice.error ? <CircleAlert size={15} /> : <Check size={15} />}{notice.text}
        </span>}
      </div>
      {connection === "disconnected" && <p className="sim-banner" role="alert">{connectionError} {state?.audience ? "Showing the last snapshot as stale." : "Retrying the connection."}</p>}
      {state?.state === "error" && <p className="sim-banner" role="alert">{state.persistence_error || state.error || "The warehouse runtime reported an error."}</p>}
      <div className={`sim-workspace-columns${loaded ? " has-questions" : ""}`}>
      <div className="sim-layout" ref={layout}>
        <section className="sim-panel sim-map-panel" aria-labelledby="audience-world-title">
          <div className="sim-panel-heading">
            <h2 id="audience-world-title"><Map size={17} aria-hidden="true" />Audience world</h2>
            <div className="sim-map-status">
              {stale && state?.audience && <span className="sim-chip warn">Stale snapshot</span>}
              {state?.patrol && <span className={`sim-chip ${["blocked", "stalled", "waiting_for_sensor"].includes(state.patrol.state) ? "warn" : ""}`}>
                Patrol · {patrolLabels[state.patrol.state]}
              </span>}
              {loaded && <a className="sim-evidence-link" href="#simulation-evidence">Evidence</a>}
            </div>
          </div>
          {state?.audience ? <WarehouseMap key={`${state.generation}:${state.run_id}`} state={state} live={live} selectedEntity={entity} onSelectEntity={selectEntity} />
            : <div className="sim-map-empty">
              <Map size={28} strokeWidth={1.4} aria-hidden="true" />
              <p>{live ? "Load the warehouse preset to see the room and robot." : "Waiting for the warehouse backend."}</p>
            </div>}
        </section>
        <aside id="simulation-evidence" className="sim-evidence" aria-label="Simulation evidence">
          <ObservationPanel state={state} stale={stale} />
          <BeliefPanel state={state} stale={stale} />
          {loaded && state && <EvidenceTimeline sessionId={sessionId} state={state} entity={entity} onFocusFact={(factId) => { if (state.run_id) onFocus({ runId: state.run_id, entity, factId }); }} />}
        </aside>
      </div>
      {loaded && state && <SimulationQuestions key={`${sessionId}:${state.generation}:${state.run_id}`} sessionId={sessionId} state={state} renderAnswer={renderAnswer} entity={entity} onSelectEntity={selectEntity} />}
      </div>
    </div>
  </section>;
}
