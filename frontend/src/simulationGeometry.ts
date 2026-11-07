import { MathUtils, Vector2 } from "three";
import type { Point, Pose } from "./simulationTypes";

const heading: Record<Pose["direction"], number> = { east: 0, north: 90, west: 180, south: 270 };

export function screenPoint(point: Point, height: number) {
  return new Vector2(point.x_cm, height - point.y_cm);
}

export function interpolatePoint(previous: Point, next: Point, fraction: number): Point {
  const point = new Vector2(previous.x_cm, previous.y_cm)
    .lerp(new Vector2(next.x_cm, next.y_cm), MathUtils.clamp(fraction, 0, 1));
  return { x_cm: point.x, y_cm: point.y };
}

export function headingRotation(direction: Pose["direction"]) {
  return -heading[direction];
}

export function cameraSector(pose: Pose, height: number, fov: number, range: number) {
  const center = screenPoint(pose, height);
  const angle = MathUtils.degToRad(headingRotation(pose.direction));
  const half = MathUtils.degToRad(fov / 2);
  const point = (offset: number) => center.clone().add(new Vector2(range, 0).rotateAround(new Vector2(), angle + offset));
  if (fov >= 360) {
    return `M ${center.x - range} ${center.y} a ${range} ${range} 0 1 0 ${2 * range} 0 a ${range} ${range} 0 1 0 ${-2 * range} 0 Z`;
  }
  const start = point(-half);
  const end = point(half);
  return `M ${center.x} ${center.y} L ${start.x} ${start.y} A ${range} ${range} 0 ${fov > 180 ? 1 : 0} 1 ${end.x} ${end.y} Z`;
}
