import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { cameraSector, headingRotation, interpolatePoint, screenPoint } from "./simulationGeometry";
import { POLL_MS } from "./useSimulation";
import type { SceneEntity, SimulationStateResponse } from "./simulationTypes";

export function warehouseLabel(id: string) {
  return id.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

type Props = { state: SimulationStateResponse; live: boolean };

export default function WarehouseMap({ state, live }: Props) {
  const id = useId();
  const [reducedMotion, setReducedMotion] = useState(() => matchMedia("(prefers-reduced-motion: reduce)").matches);
  const previous = useRef<SimulationStateResponse | null>(null);
  const [transition, setTransition] = useState<{ from: SimulationStateResponse; fraction: number } | null>(null);
  const running = live && state.state === "running" && !reducedMotion;

  useEffect(() => {
    const query = matchMedia("(prefers-reduced-motion: reduce)");
    const change = () => setReducedMotion(query.matches);
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);

  useLayoutEffect(() => {
    const from = previous.current;
    previous.current = running ? state : null;
    const sameRun = from?.run_id === state.run_id && from?.generation === state.generation;
    if (!running || !from?.audience || !sameRun || from.snapshot_sequence === state.snapshot_sequence) {
      setTransition(null);
      return;
    }
    let frame: number;
    const start = performance.now();
    setTransition({ from, fraction: 0 });
    const draw = (now: number) => {
      const fraction = (now - start) / POLL_MS;
      if (fraction >= 1) setTransition(null);
      else {
        setTransition({ from, fraction });
        frame = requestAnimationFrame(draw);
      }
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [state, running]);

  if (!state.audience) return null;
  const { bounds, snapshot, patrol, robot_radius_cm } = state.audience;
  const { width_cm: width, height_cm: height } = bounds;
  const from = running ? transition?.from.audience?.snapshot : null;
  const robot = from && transition
    ? { ...snapshot.robot_pose, ...interpolatePoint(from.robot_pose, snapshot.robot_pose, transition.fraction) }
    : snapshot.robot_pose;
  const robotPoint = screenPoint(robot, height);
  const cameraFov = snapshot.config.camera_fov_deg ?? 90;
  const cameraRange = snapshot.config.camera_range_cm ?? 350;
  const entities = [...snapshot.entities].sort((a, b) => Number(a.kind === "parcel") - Number(b.kind === "parcel"));

  function position(entity: SceneEntity) {
    const previousFootprint = from?.entities.find((item) => item.entity_id === entity.entity_id)?.footprint;
    return screenPoint(previousFootprint && transition
      ? interpolatePoint(previousFootprint, entity.footprint, transition.fraction)
      : entity.footprint, height);
  }

  return <>
    <div className="warehouse-stage">
      <svg className="warehouse-map" viewBox={`-44 -36 ${width + 88} ${height + 84}`}
        role="img" aria-labelledby={`${id}-title ${id}-desc`}>
        <title id={`${id}-title`}>Top-down audience map of the warehouse</title>
        <desc id={`${id}-desc`}>North is up. The grid is in centimeters. The camera sector shows range and heading. The map objects list below provides coordinates.</desc>
        <defs>
          <pattern id={`${id}-grid`} width="50" height="50" patternUnits="userSpaceOnUse">
            <path d="M 50 0 H 0 V 50" className="warehouse-grid-line" />
          </pattern>
          <clipPath id={`${id}-bounds`}><rect width={width} height={height} /></clipPath>
        </defs>
        <rect className="warehouse-floor" width={width} height={height} />
        <rect width={width} height={height} fill={`url(#${id}-grid)`} />
        <g clipPath={`url(#${id}-bounds)`}>
          <polyline className="warehouse-route" points={patrol.map((point) => screenPoint(point, height).toArray().join(",")).join(" ")} />
          {snapshot.sensor_availability.camera_detect && <path className="warehouse-sector"
            d={cameraSector(robot, height, cameraFov, cameraRange)} />}
          {entities.map((entity) => {
            const { x, y } = position(entity);
            const { width_cm: w, height_cm: h } = entity.footprint;
            return <g key={entity.entity_id} transform={`translate(${x},${y})`}
              className={`warehouse-object warehouse-${entity.kind}`} data-entity={entity.entity_id}
              data-color={entity.intrinsic_color}>
              <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={entity.kind === "station" ? 5 : 2} />
              {entity.kind === "shelf" && [-h / 4, 0, h / 4].map((line) =>
                <line key={line} x1={-w / 2} x2={w / 2} y1={line} y2={line} />)}
            </g>;
          })}
          <g data-robot transform={`translate(${robotPoint.x},${robotPoint.y})`}>
            <circle className="warehouse-robot" r={robot_radius_cm} />
            <path className="warehouse-heading" d="M 2 -7 L 14 0 L 2 7 Z" transform={`rotate(${headingRotation(robot.direction)})`} />
          </g>
        </g>
        {entities.map((entity) => {
          const { x, y } = position(entity);
          const parcel = entity.kind === "parcel";
          const right = x <= width - 120;
          return <text className="warehouse-object-label" key={entity.entity_id}
            x={parcel ? x + (right ? 12 : -12) : x}
            y={parcel ? y - 11 : y + entity.footprint.height_cm / 2 + 27}
            textAnchor={parcel ? (right ? "start" : "end") : "middle"}>
            {warehouseLabel(entity.entity_id).replace(" station", "")}
          </text>;
        })}
        <rect className="warehouse-outline" width={width} height={height} />
        <text className="warehouse-axis" x="0" y={height + 34}>0</text>
        <text className="warehouse-axis" x={width} y={height + 34} textAnchor="end">{width} cm E</text>
        <text className="warehouse-axis" x="0" y="-12">{height} cm N</text>
        <path className="warehouse-north" d={`M ${width - 38} -9 l 8 -16 l 8 16 z`} />
        <text className="warehouse-axis" x={width} y="-9" textAnchor="end">N</text>
      </svg>
    </div>
    <div className="warehouse-legend">
      <span><i className="key-robot" />Robot</span>
      {snapshot.sensor_availability.camera_detect
        ? <span><i className="key-sector" />Camera range (geometry)</span>
        : <span>Camera offline in world</span>}
      <span><i className="key-route" />Patrol path</span>
      <span><i className="key-station" />Station</span>
      <span>{cameraFov}° · {cameraRange} cm</span>
    </div>
    <details className="sim-details">
      <summary>Map objects and coordinates</summary>
      <ul className="sim-list">
        <li><strong>Robot</strong><span>{snapshot.robot_pose.x_cm.toFixed(0)}, {snapshot.robot_pose.y_cm.toFixed(0)} cm · {snapshot.robot_pose.direction}</span></li>
        {entities.map(({ entity_id, kind, footprint }) => <li key={entity_id}>
          <strong>{warehouseLabel(entity_id)}</strong>
          <span>{kind} · {footprint.x_cm.toFixed(0)}, {footprint.y_cm.toFixed(0)} cm</span>
        </li>)}
      </ul>
    </details>
  </>;
}
