export type GraphLabel = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  priority: number;
  kind: "node" | "edge";
};

export type LabelPlacement = { x: number; y: number; visible: boolean };

/** Place screen-space labels without changing the graph's nodes or relationships. */
export function placeGraphLabels(labels: GraphLabel[], width: number, height: number): Map<string, LabelPlacement> {
  const result = new Map<string, LabelPlacement>();
  const occupied: { left: number; top: number; right: number; bottom: number }[] = [];
  for (const label of [...labels].sort((a, b) => b.priority - a.priority || a.id.localeCompare(b.id))) {
    const offsets = label.kind === "node"
      ? [[0, 0], [0, -24], [0, 24], [-40, 0], [40, 0], [-40, -28], [40, -28], [-40, 28], [40, 28], [0, -52], [0, 52]]
      : [[0, 0], [0, -22], [0, 22], [-30, -18], [30, 18]];
    if (label.kind === "node") {
      for (const ring of [1, 2, 3]) {
        const horizontal = (label.width / 2 + 10) * ring;
        const vertical = (label.height + 6) * ring;
        offsets.push([0, -vertical], [0, vertical], [-horizontal, 0], [horizontal, 0],
          [-horizontal, -vertical], [horizontal, -vertical], [-horizontal, vertical], [horizontal, vertical]);
      }
    }
    let placed = false;
    for (const [dx, dy] of offsets) {
      const x = Math.max(12 + label.width / 2, Math.min(width - 12 - label.width / 2, label.x + dx));
      const y = Math.max(52 + label.height / 2, Math.min(height - 68 - label.height / 2, label.y + dy));
      const box = { left: x - label.width / 2, top: y - label.height / 2, right: x + label.width / 2, bottom: y + label.height / 2 };
      if (box.left < 0 || box.right > width || box.top < 0 || box.bottom > height) continue;
      if (occupied.some((other) => box.left < other.right + 4 && box.right + 4 > other.left && box.top < other.bottom + 4 && box.bottom + 4 > other.top)) continue;
      occupied.push(box);
      result.set(label.id, { x: x - label.x, y: y - label.y, visible: true });
      placed = true;
      break;
    }
    if (!placed) result.set(label.id, { x: 0, y: 0, visible: false });
  }
  return result;
}
