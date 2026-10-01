import assert from "node:assert/strict";
import { graphPositions } from "../src/beliefGraphLayout.ts";

const twoNodePositions = graphPositions(
  ["route_A", "blocked"],
  [{ source: "route_A", target: "blocked" }],
);
const blocked = twoNodePositions.get("blocked");
assert.ok(blocked, "the second node must receive a position");
assert.ok(
  Math.abs(blocked.x) > 0.1 || Math.abs(blocked.z) > 0.1,
  "a two-node graph must not collapse onto the vertical axis",
);

const presentationNodes = ["robot", "room", "route", "box", "camera", "lidar", "light"];
const presentationPositions = graphPositions(presentationNodes, [
  { source: "robot", target: "room" },
  { source: "robot", target: "route" },
  { source: "camera", target: "box" },
  { source: "lidar", target: "route" },
  { source: "light", target: "room" },
]);
for (const axis of ["x", "y", "z"]) {
  const values = [...presentationPositions.values()].map((point) => point[axis]);
  assert.ok(Math.max(...values) - Math.min(...values) > 1, `layout must use the ${axis.toUpperCase()} axis`);
}

console.log("belief graph layout uses X, Y, and Z space");
