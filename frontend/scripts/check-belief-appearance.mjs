import assert from "node:assert/strict";
import { factColor, highlightedFacts } from "../src/beliefGraphAppearance.ts";

assert.equal(factColor(true, false, false, true), 0xe0bd7d, "revised facts use the legend's amber color");
assert.equal(factColor(true, true, false, false), 0x78e6d8, "selection must not look like a revision");
assert.equal(factColor(true, true, false, true), 0xe0bd7d, "a selected revision remains amber");
assert.equal(factColor(true, false, false, false), 0x4f8f83);
assert.equal(factColor(false, false, false, false), 0x666b69);

const transient = new Set(["new"]);
const ids = highlightedFacts(transient, [{ superseded_by: "successor" }, { superseded_by: null }], [
  { event_type: "belief_revision", output_fact_ids: ["audited"] },
  { event_type: "other", output_fact_ids: ["unrelated"] },
]);
assert.deepEqual([...ids].sort(), ["audited", "new", "successor"]);
assert.deepEqual([...transient], ["new"], "input set is not mutated");
console.log("Belief graph appearance checks passed.");
