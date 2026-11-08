import assert from "node:assert/strict";
import { displayText, environmentTableData, fieldLabel, formatTimestamp, isTimestampField } from "../src/display.ts";

const records = {
  obs_01: { obstacle_id: "obs_01", x_cm: 0, active: false, radius_cm: null },
  obs_02: { obstacle_id: "obs_02", y_cm: 12, bounding_box: { width_cm: 10 } },
};
const before = structuredClone(records);
const result = environmentTableData(records, "obstacle_id");
assert.deepEqual(result.columns, ["x_cm", "active", "radius_cm", "y_cm", "bounding_box.width_cm"]);
assert.equal(result.rows[0].fields.active, false);
assert.equal(result.rows[0].fields.radius_cm, null);
assert.equal(result.rows[1].fields["bounding_box.width_cm"], 10);
assert.equal(fieldLabel("bounding_box.width_cm"), "Bounding box width (cm)");
assert.equal(displayText("lidar_scan robot_1"), "lidar scan robot 1");
assert.equal(displayText("Front LiDAR at sim_time 21500ms observed route_A"), "Front LiDAR at sim time 21.500 s observed route A");
assert.equal(displayText("at simulation time 500 ms"), "at simulation time 0.500 s");
assert.deepEqual(records, before);
assert.deepEqual(environmentTableData({}, "object_id"), { rows: [], columns: [] });
assert.deepEqual(environmentTableData({ key: { object_id: "different_id" } }, "object_id").columns, ["object_id"]);
assert.deepEqual(environmentTableData({ reading: { observation_id: "internal", fact_id: "internal-fact", sensor_id: "camera_01", context: { evidence_ids: ["internal"], location: "room_1" } } }, "").columns, ["sensor_id", "context.location"]);
assert.equal(isTimestampField("observed_at"), true);
assert.equal(isTimestampField("context.created_at"), true);
assert.equal(isTimestampField("color_cast"), false);
assert.equal(formatTimestamp(null), "Not recorded");
assert.equal(formatTimestamp("invalid timestamp"), "invalid timestamp");
const timestamp = "2026-11-01T08:10:04.239682Z";
assert.equal(formatTimestamp(timestamp), new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }).format(new Date(timestamp)));
console.log("Inspector display checks passed.");
