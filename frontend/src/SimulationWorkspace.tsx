import { useId, useState } from "react";
import { createPortal } from "react-dom";
import { Activity, Camera, Check, CircleAlert, Map, Pause, Play, SkipForward, SlidersHorizontal, WifiOff } from "lucide-react";
import { api } from "./api";
import type {
  CameraDetection, SimulationControlRequest, SimulationDisruptRequest,
  SimulationStateResponse, StoredObservationSample,
} from "./simulationTypes";
import { useSimulation } from "./useSimulation";
import WarehouseMap, { warehouseLabel } from "./WarehouseMap";
import "./simulation-workspace.css";

const rates = [0.25, 0.5, 1] as const;
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
        </ul> : <p className="sim-subtle">{detections ? "No parcels detected in this capture." : "Awaiting a camera sample."}</p>}
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

export default function SimulationWorkspace({ sessionId, chatName, presenterTarget }: {
  sessionId: string; chatName: string; presenterTarget: HTMLElement | null;
}) {
  const { state, connection, connectionError, pending, notice, run } = useSimulation(sessionId);
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
    void run("Applying control…", async (expected, options) => ({
      state: await api.controlSimulation({ action, expected, playback_rate: rate }, options),
      message: messages[action],
    }));
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
      return { state: receipt.state, message: `Disruption scheduled for ${simTime(receipt.event.at_ms)}.` };
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
      <div className="sim-rates">
        {rates.map((rate) => <button type="button" key={rate} className="sim-button"
          aria-pressed={(state?.playback_rate ?? 0.5) === rate}
          onClick={() => control("set_rate", rate)} disabled={!canAdjust}>{rate}×</button>)}
      </div>
    </div>
    {loaded && state && <DisruptionControls key={state.run_id} state={state}
      disabled={!canAdjust || state.state === "completed"} submit={disrupt} />}
  </section>;

  return <section className="simulation-workspace" aria-label="Warehouse simulation">
    <div className="sim-content">
      <div className="sim-heading">
        <div><h2>Warehouse inspection</h2><p>Session · {chatName}</p></div>
        <div className={`sim-chip ${live && active ? "success" : !live || state?.state === "error" ? "warn" : ""}`} role="status">
          {connection === "disconnected" ? <WifiOff size={15} /> : active && live ? <Play size={13} /> : <Activity size={15} />}{status}
        </div>
      </div>
      <div className="sim-toolbar">
        <div className="sim-clock">
          <span>Simulation time</span>
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
        {pending ? <span>{pending}</span> : notice && <span className={notice.error ? "sim-warning" : ""}>
          {notice.error ? <CircleAlert size={15} /> : <Check size={15} />}{notice.text}
        </span>}
      </div>
      {connection === "disconnected" && <p className="sim-banner" role="alert">{connectionError} {state?.audience ? "Showing the last snapshot as stale." : "Retrying the connection."}</p>}
      {state?.state === "error" && <p className="sim-banner" role="alert">{state.persistence_error || state.error || "The warehouse runtime reported an error."}</p>}
      <div className="sim-layout">
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
          {state?.audience ? <WarehouseMap key={`${state.generation}:${state.run_id}`} state={state} live={live} />
            : <div className="sim-map-empty">
              <Map size={28} strokeWidth={1.4} aria-hidden="true" />
              <p>{live ? "Load the warehouse preset to see the room and robot." : "Waiting for the warehouse backend."}</p>
            </div>}
        </section>
        <aside id="simulation-evidence" className="sim-evidence" aria-label="Simulation evidence">
          <ObservationPanel state={state} stale={stale} />
          <BeliefPanel state={state} stale={stale} />
        </aside>
      </div>
    </div>
  </section>;
}
