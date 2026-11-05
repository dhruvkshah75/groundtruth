# Phase 2 shared simulation contracts (P2-01)

Implemented scope: validated contracts, a warehouse definition, shared JSON examples,
TypeScript mirrors, a sensor protocol, and contract/fixture tests. This does **not**
implement a running simulator, geometry-based sensor, scheduler, SQL ledger, API,
patrol controller, or simulation UI. See [issue #21](https://github.com/dhruvkshah75/groundtruth/issues/21).

## Source of truth and consumer handoff

- `src/contracts/simulation.py` owns the Python wire models, validation and defaults;
  public models are exported by `src.contracts`.
- `tests/fixtures/phase_two/contracts.json` is the single editable example source.
  `valid` contains configurations, one scenario, statuses, audience snapshots,
  sensor samples, temporal queries and controls. `invalid` contains model/payload
  pairs that must fail Python validation.
- `scripts/generate_simulation_types.py` derives TypeScript types from the Pydantic
  JSON schemas and TypeScript examples from that JSON source. It does not inspect
  `.env`, contact providers or implement runtime functionality.
- `frontend/src/simulationTypes.ts` and `simulationFixtures.ts` are generated. The
  example literal uses TypeScript `satisfies`, so the existing production build
  checks fixture structure. Python enforces numeric bounds, UTC, cross-field rules,
  sorting and other constraints that TypeScript cannot express.

Regenerate after changing contracts/examples:

```bash
uv run --locked python scripts/generate_simulation_types.py
uv run --locked python scripts/generate_simulation_types.py --check
uv run --locked pytest -q tests/unit/contracts
cd frontend
npm run build
```

The Python suite checks generated files are current. Do not hand-edit their fields.
No new runtime/development dependency is required.

| Consumer | Models and responsibilities |
| --- | --- |
| Engine #22 | Read ScenarioDefinition and SimulationConfig; produce SimulationStatus/Snapshot; implement motion/lifecycle later. |
| Sensors #23 | Implement SensorSource; return existing result models; validate camera measurements through CameraScan; construct ObservationSample at acquisition. |
| Memory #24 | Store ObservationSample identities and result variants; query through TemporalObservationQuery; add storage UTC when persisting. |
| Later runtime | Own tick/sampling sequence, pause/reset generations, scheduling and monotonic wall clock; no such loop starts on import. |
| Later UI | Render audience snapshots, sensor samples and graph-derived beliefs separately; do not supply snapshots to the agent as evidence. |

## Four clocks, identities and pacing

1. **Simulation time:** nonnegative integer `sim_time_ms`, local to a run. Physics
   advances in 100 ms steps, sensor acquisition every 500 simulation ms, and the
   default run completes at 30,000 simulation ms. Duration and acquisition interval
   must be multiples of the configured step.
2. **Monotonic wall time:** future runtime scheduling clock. At default 0.5×,
   one simulation step nominally takes 200 wall-clock ms, an acquisition interval
   one wall-clock second, and the default demo about one minute excluding pauses.
   Rates 0.25 / 0.5 / 1.0 are the only supported values. They do not change robot
   speed in cm/simulation-second or sensor/event/freshness simulation times.
3. **Acquisition UTC:** `epoch_utc + sim_time_ms`, including while paused or replayed.
   `captured_at` and successful `observed_at` (or failure `reported_at`) must agree.
   Epoch/capture times must have a timezone; JSON fixtures use UTC `Z`.
4. **Reception/storage UTC:** actual time the ledger receives/writes the sample;
   added by Tier 1, not by this acquisition contract. Delayed storage never changes
   capture time. Existing `FactQuery.as_of` keeps its storage-history meaning.

`run_id` is a UUID for one execution; reset creates a new run, even with the same
scenario. `sample_id` is a UUID for every acquisition attempt, including failures.
`sample_sequence` is a run-wide acquisition order (zero-based, including all sensor
capabilities), assigned by the future runtime. `observation_id` still identifies a
successful legacy sensor result. They are distinct from fact and script identities.
`world_version` identifies physical state, not elapsed time or acquisition order.

A single model validates a single payload; cross-record sequence monotonicity,
UUID uniqueness and idempotence are enforced by the future runtime/ledger. Same-time
samples and the same elapsed time in different runs are valid.

Loading starts paused at t=0; runtime takes the initial sample. Pause does not age
simulation-based evidence. Manual step advances one fixed tick independent of
playback rate. Reset retains configuration and clears run progress, using a new ID.
Completed runs do not loop. Reading/sensing does not advance the clock. Rate changes
preserve current time/state and fractional scheduler progress without skipped ticks.
The schema validates the request, not these future runtime transitions.

A 1,000 ms freshness setting is evaluated in simulation time, not storage or wall
time. A healthy newer scan missing an object means last-seen information even when
its previous sighting is recent; the actual belief policy belongs to P2-05.

## Geometry and scenario data

Coordinates are centimeters in `warehouse_map`, with +x east and +y north. Bounds
start at (0,0). Rectangle footprints use center x/y plus full positive width/height;
robot footprint is a circle with `robot_radius_cm`. SVG screen y increases downwards:
render using `screen_y = (bounds.height_cm - y_cm) * scale` (plus view offset).

For object displacement (dx,dy), robot-relative (forward,right) is:

| Heading | Forward | Right |
| --- | --- | --- |
| north | dy | dx |
| east | dx | -dy |
| south | -dy | -dx |
| west | -dx | dy |

Point/footprint values must be finite. Scenario validation checks unique entity,
route and robot IDs; room membership; footprints/endpoints/waypoints within bounds;
nonzero cardinal routes/patrol legs; a patrol starting at the initial robot pose;
ordered unique event keys; valid movable targets; non-overlapping motion schedules;
and tick-aligned event starts/durations within scenario duration. Collision paths
and actual visibility are not engine-validated here; fixture tests provide explicit
static clearance and line-of-sight consistency checks for the chosen example.

`SceneEntity` contains simulator-only intrinsic color, visible flag, footprint and
solid/opaque flags. Those are not camera detections. Stations are map labels/surfaces,
non-solid and non-opaque; shelves are solid/opaque; parcels are non-solid markers;
the cart has a solid/opaque rectangular footprint in this fixture. These are
intentional 2D abstractions, not manipulation/contact physics.

### Warehouse fixture

Initial lighting is explicitly neutral at intensity 1.0; camera and LiDAR start available.
Those values belong to the scenario, and snapshots also expose sensor availability
for audience controls rather than leaving engine defaults implicit.

Room bounds: 800 × 600 cm. Initial robot: (100,100), north, radius 15 cm.
Patrol: (100,100) → (100,450) → (650,450) → (650,100). Route A is the first
leg and route B the second. This PR supplies the path, not patrol execution.

| Entity | Initial center (cm) | Footprint (cm) | Notes |
| --- | --- | --- | --- |
| parcel_01 | (80,350) | 12 × 12 | Red target; scripted relocation. |
| parcel_02 | (220,260) | 12 × 12 | Similar red parcel; ambiguous descriptions require clarification later. |
| parcel_03 | (600,300) | 16 × 16 | Green; initially beyond camera range. |
| packing_station | (80,350) | 60 × 60 | Non-solid, non-opaque zone label. |
| inspection_station | (150,390) | 60 × 60 | Non-solid, non-opaque zone label. |
| shelf_01 | (300,300) | 30 × 160 | Solid and opaque. |
| shelf_02 | (500,300) | 30 × 160 | Solid and opaque. |
| cart_01 | (180,470) | 40 × 40 | Moves across the front of route A, then clears it. |

Initial nominal robot speed is 30 cm/s, stop threshold 50 cm, camera 90° / 350 cm,
LiDAR range 200 cm. Real stop/turn timing and full patrol visibility must be verified
when #22/#23/P2-06 integrate. This definition is not proof of a completed patrol.

| Simulation interval | Nominal presentation interval at 0.5× | Script / inspection beat |
| --- | --- | --- |
| 0–5 s | 0–10 s | Initial observation and stable inspection. |
| 5–7 s | 10–14 s | parcel_01 moves to inspection station (150,390). |
| 7–10 s | 14–20 s | Hold; inspect the changed sighting/belief. |
| 10–11 s | 20–22 s | Cart moves to (100,470); upcoming LiDAR/patrol should stop before it. |
| 11–14 s | 22–28 s | Hold; explain route evidence. |
| 14 s | 28 s | Yellow lighting (intensity 0.8); red appears brown under the existing mock rule. |
| 18–19 s | 36–38 s | Parcel moves to (250,420). |
| 19–20 s | 38–40 s | Parcel moves to (350,420), around the top of shelf_01. |
| 20–21 s | 40–42 s | Parcel moves to (330,340), behind shelf_01 for the static reference pose. |
| 21–22 s | 42–44 s | Cart clears to (180,470). |
| 22–25 s | 44–50 s | Inspection period; neutral lighting restored at 23 s. |
| 25–27 s | 50–54 s | Parcel moves to (500,450) for later patrol reacquisition. |
| 27–30 s | 54–60 s | Hold; ask temporal questions and inspect evidence. |

All moves are explicit linear segments, each with integer at_ms, positive duration
and a same-time sequence. Tick order is advance → apply due inputs ordered by
(at_ms,sequence) → integrate allowed motion → capture due observations. Sensor-backed
patrol decisions affect subsequent motion. Apply events once per run; an acquisition
at a completed move's endpoint sees the endpoint. There is no event bus telling the
agent which object moved.

The shared audience snapshots and sensor examples are **independent frozen-pose
contract examples**, not an engine-produced replay or promises of exact patrol
observations. They use the reference observer at (100,100), north, except the explicitly
south-facing empty-scan example at 23.5 seconds.
The (330,340) hidden example is in FOV/range but its line of sight intersects shelf_01;
that isolates occlusion rather than confusing it with distance or being behind the
robot. Full temporal/pose integration is a downstream test requirement.

At 22 seconds the broad camera scan still detects parcel_02 at (220,260);
yellow lighting makes its apparent color brown. Only parcel_01 is occluded.
At 23 seconds the north-facing LiDAR ray has no solid hit within its 200 cm range,
so the sample is clear with a null distance. A separate 23.5-second south-facing
camera sample supplies the valid empty-scan example: all parcels remain north of
the observer. Fixture-specific tests check these geometric facts, not just schemas.

## Bounded sensor evidence

`SensorSource.observe(request)` returns the existing `SensorObservation |
ObservationUnavailable`. It has no world getter, script accessor, controls or memory
writes. Existing `MockEnvironment` structurally satisfies it; the Phase 2 geometry
profile is added in #23. Defining the protocol creates no polling loop.

`ObservationSample` wraps the original request/result, run/time/sample metadata,
sensor_id, observer_pose and world_version. Successful result context must agree on
observer, frame, room, version and time. Extra observation context is restricted to
a string lighting value. Known requests accept only camera target / front LiDAR
parameters. Unknown successful capabilities are rejected; unsupported capabilities
may still return the existing structured failure variant.

The Phase 2 camera measurement shape is `CameraScan(objects=[CameraDetection,...])`:
each detection has object_id, apparent_color, position (x/y), frame_of_reference and
positive bounding_box width/height. IDs are unique and sorted. Targeted success uses
the same array shape with exactly the target. Frames must match the acquisition map
frame. This normalizes Phase 2 targeted/broad scans without modifying legacy camera
payloads. Stable object IDs and noise-free simulated position estimates are deliberate
simplifications; this does not implement visual identity tracking.

- Successful empty scan: `SensorObservation` with `objects: []`.
- Target not visible: `ObservationUnavailable`, reason `target_not_visible`, targeted
  camera request; no true position or occluder name.
- Outage: `ObservationUnavailable`, reason `sensor_unavailable`, not a fake clear scan.

All Phase 2 failure messages use the canonical `Observation unavailable.` string.
Typed reason/request metadata retain useful error context without free-form hidden
world hints. This restriction applies only to the new envelope, not legacy errors.
Other successful measurements have typed LiDAR, pose and ambient-light shapes; pose
must match observer_pose. LiDAR blocked denotes a hit, not a safety authorization.

These structural checks prevent accidental extra truth fields; they do not prove
that a sensor measurement is physically correct. #23 must enforce visibility.

## Temporal queries and controls

`TemporalObservationQuery` requires run_id. Capture-time bounds start_ms/end_ms are
inclusive, ordered and nonnegative; limit is 1–1000 (default 100). Modes:

| Mode | Required filter / intended retrieval |
| --- | --- |
| samples | Bounded samples, optional capability/sensor/entity filter. |
| latest | Requires capability; latest attempt at/before end_ms, including failures. |
| last_seen | Requires entity_id; latest positive sighting. |
| history | Requires entity_id; bounded positive sighting history. |

Order by capture sim_time_ms, then sample_sequence, then stable identity if needed.
Failure/non-detection does not erase last positive sighting. Querying capture time
among stored samples differs from reconstructing what had been received at a past
storage time. No retrieval code or SQL is implemented here.

`SimulationControl` accepts play/pause/step/reset with no rate or set_rate with one
supported playback_rate. Config and status snapshots carry the selected rate and
must agree. Snapshot times align to steps; completed state corresponds exactly to
scenario end time. These are future wire payloads, not implemented endpoints.

## Acceptance and checkpoints

| Issue requirement | Verification |
| --- | --- |
| Public models and playback default | Shared exports, valid configurations/rates; invalid rate/field tests. |
| Exact clocks and identity | Capture-time/context consistency, naive-time rejection, separate-run/same-time tests. |
| Geometry and scenario | Bounds, finite shapes, IDs, motion ordering/targets, static patrol clearance and reference-view tests. |
| Empty/failure distinction | Distinct fixture samples and canonical no-leak failure checks. |
| Camera shape and privacy | Sorted unique detections, frame/target checks, extra truth-field rejection. |
| Legacy compatibility | Existing contract suite and A/B/ReAct integration tests; no existing models changed. |
| Python/TypeScript shared examples | Round trips, invalid fixture rejection, generator --check and frontend tsc/build. |
| Clear handoff | This document's consumer map, exact payload sources, lifecycle rules and scope limits. |

Run the targeted checkpoint before broad checks. Full repository tests/lint and the
frontend build remain required before merging. Fake clocks/IDs keep tests offline;
fixtures contain no local credentials or real chat data.
