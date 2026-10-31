import assert from "node:assert/strict";
import { placeGraphLabels } from "../src/beliefGraphLabels.ts";

const labels = Array.from({ length: 12 }, (_, index) => ({
  id: `node:${index}`, kind: "node", x: 240 + index % 3 * 32, y: 180 + Math.floor(index / 3) * 28,
  width: 90, height: 26, priority: 3,
}));
const before = JSON.stringify(labels);
const placed = placeGraphLabels(labels, 640, 480);
const boxes = labels.filter((label) => placed.get(label.id)?.visible).map((label) => {
  const offset = placed.get(label.id);
  return { left: label.x + offset.x - label.width / 2, right: label.x + offset.x + label.width / 2,
    top: label.y + offset.y - label.height / 2, bottom: label.y + offset.y + label.height / 2 };
});
assert.ok(boxes.length >= 8, "restore readable names even in a crowded cluster");
for (let i = 0; i < boxes.length; i++) {
  const box = boxes[i];
  assert.ok(box.left >= 0 && box.right <= 640 && box.top >= 52 && box.bottom <= 412, "labels stay inside the stage and clear of its controls");
  for (const other of boxes.slice(i + 1)) {
    assert.ok(box.right <= other.left || box.left >= other.right || box.bottom <= other.top || box.top >= other.bottom, "visible labels must not overlap");
  }
}
assert.equal(JSON.stringify(labels), before, "label layout must not mutate graph data");
const crowded = placeGraphLabels([
  { id: "ordinary", kind: "edge", x: 90, y: 90, width: 140, height: 30, priority: 1 },
  { id: "selected", kind: "edge", x: 90, y: 90, width: 140, height: 30, priority: 4 },
], 180, 170);
assert.ok(crowded.get("selected").visible, "the selected relationship takes priority in a small viewport");
assert.equal(crowded.get("ordinary").visible, false, "suppress a colliding label instead of obscuring the selected relationship");
console.log("Belief labels preserve graph data, prevent overlap, and prioritize selected detail.");
