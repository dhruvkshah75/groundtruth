export type GraphPoint = { x: number; y: number; z: number };
export type GraphLink = { source: string; target: string };

export function graphPositions(nodes: string[], facts: GraphLink[], previous?: ReadonlyMap<string, GraphPoint>): Map<string, GraphPoint> {
  const degrees = new Map(nodes.map((node) => [node, 0]));
  for (const fact of facts) {
    degrees.set(fact.source, (degrees.get(fact.source) ?? 0) + 1);
    degrees.set(fact.target, (degrees.get(fact.target) ?? 0) + 1);
  }

  const ordered = [...nodes].sort((left, right) => {
    const degreeDifference = (degrees.get(right) ?? 0) - (degrees.get(left) ?? 0);
    return degreeDifference;
  });
  const positions = new Map<string, GraphPoint>();
  if (!ordered.length) return positions;

  positions.set(ordered[0], { x: 0, y: 0, z: 0 });
  const outer = ordered.slice(1);
  outer.forEach((node, index) => {
    const total = Math.max(outer.length, 1);
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    const y = 1 - ((index + 0.5) / total) * 2;
    const radiusAtY = Math.sqrt(Math.max(0, 1 - y * y));
    const angle = (index + 0.5) * goldenAngle;
    const distance = 4.4 + (index % 3) * 0.3;
    positions.set(node, {
      x: Math.cos(angle) * radiusAtY * distance,
      y: y * distance * 0.78,
      z: Math.sin(angle) * radiusAtY * distance,
    });
  });
  if (!previous?.size) return positions;

  // Keep surviving nodes in place as observations add or revise relationships.
  const retained = new Map<string, GraphPoint>();
  for (const node of nodes) {
    const point = previous.get(node);
    if (point) retained.set(node, point);
  }
  const candidates = [...positions.values()];
  for (const node of nodes) {
    if (retained.has(node)) continue;
    const distance = (point: GraphPoint) => Math.min(...[...retained.values()].map(
      (other) => (point.x - other.x) ** 2 + (point.y - other.y) ** 2 + (point.z - other.z) ** 2,
    ));
    const best = candidates.reduce((left, right) => distance(right) > distance(left) ? right : left);
    retained.set(node, best);
    candidates.splice(candidates.indexOf(best), 1);
  }
  return retained;
}
