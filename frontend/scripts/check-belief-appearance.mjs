import assert from "node:assert/strict";
import { factColor, highlightedFacts, visibleGraphNodes } from "../src/beliefGraphAppearance.ts";

assert.deepEqual(visibleGraphNodes("active", [{ source: "parcel", target: "inspection" }], [], [
  { subject: "parcel", object: "packing", superseded_by: "next" },
]), ["inspection", "packing", "parcel"]);

assert.equal(factColor(true, false, false, true), 0xa06a1c, "revised facts use the legend's amber color");
assert.equal(factColor(true, true, false, false), 0x765184, "selection must not look like a revision");
assert.equal(factColor(true, true, false, true), 0xa06a1c, "a selected revision remains amber");
assert.equal(factColor(true, false, false, false), 0x8b7596);
assert.equal(factColor(false, false, false, false), 0x817a85);

const transient = new Set(["new"]);
const ids = highlightedFacts(transient, [{ superseded_by: "successor" }, { superseded_by: null }], [
  { event_type: "belief_revision", output_fact_ids: ["audited"] },
  { event_type: "other", output_fact_ids: ["unrelated"] },
]);
assert.deepEqual([...ids].sort(), ["audited", "new", "successor"]);
assert.deepEqual([...transient], ["new"], "input set is not mutated");
console.log("Belief graph appearance checks passed.");
