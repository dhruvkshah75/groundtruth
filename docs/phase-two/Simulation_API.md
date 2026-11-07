# Simulation HTTP API (P2-09)

[Issue #33](https://github.com/dhruvkshah75/groundtruth/issues/33) ·
[Implementation PR #45](https://github.com/dhruvkshah75/groundtruth/pull/45)

The API exposes the named warehouse run owned by a browser session. P2-07
[session runtime](Session_Runtime.md) (issue [#31](https://github.com/dhruvkshah75/groundtruth/issues/31),
merged in `686cab7`, [PR #43](https://github.com/dhruvkshah75/groundtruth/pull/43))
owns scheduling, locking, acquisition and checkpoints. P2-06
[patrol and safety](Patrol_Controller.md) (issue [#29](https://github.com/dhruvkshah75/groundtruth/issues/29),
merged in `3849789`, [PR #41](https://github.com/dhruvkshah75/groundtruth/pull/41))
provides the LiDAR-backed controller. The runtime integrates the
[engine](Simulation_Engine.md), [sensors](Warehouse_Sensors.md),
[observation ledger](Observation_Ledger.md) and [belief ingestion](Temporal_Belief_Ingestion.md).
This change is based on `13c7ffe`, which also includes P2-08
[temporal questions](Temporal_Questions.md).

## Routes

All requests require a UUID `X-Session-ID`. POST bodies must be JSON objects with
`Content-Type: application/json` and a `Content-Length` of at most 16,384 bytes.
Responses are JSON with `Cache-Control: no-store`. Each mutation requires
`expected: { run_id, generation }` taken from the latest response for that
session. An unloaded session has `run_id: null` and starts at generation zero.

| Method and path | Request | Successful response |
| --- | --- | --- |
| `GET /api/simulation/state` | Session header | `SimulationStateResponse`, including `pending` before load. |
| `POST /api/simulation/load` | `preset: "warehouse_inspection_v1"`, `expected`, UUID `request_id` | Fresh run paused at zero with initial pose, camera, LiDAR and light acquisitions. |
| `POST /api/simulation/control` | `expected`, `action`, and `playback_rate` for `set_rate` | State captured after applying the command. |
| `POST /api/simulation/disrupt` | `expected`, UUID `request_id`, `disruption`, optional `at_ms` | `{ event, state }` with the accepted engine event and coherent state. |
| `GET /api/simulation/export` | Session header | Version 1 JSON attachment when an exporter is wired. |

`play` and `pause` are idempotent. `step` executes one tick only while paused.
A completed run requires reset before playing again. Pause on a completed run
leaves it completed. Reset creates a fresh run ID and epoch, restores the preset
and its script, and retains the selected playback rate. A new load starts at
0.5×. The only supported rates are 0.25×, 0.5×, 1× and 2×.

### Presenter disruptions

| `disruption.kind` | Fields and behavior |
| --- | --- |
| `relocate_parcel` | `entity_id` of a preset parcel, `to: { x_cm, y_cm }`, and tick-aligned `duration_ms`. Uses linear motion to a footprint-contained destination. |
| `cart_block` / `cart_clear` | No additional fields. Moves `cart_01` over 1,000 simulation ms to (100, 470) / (180, 470) in warehouse-map centimeters. |
| `lighting` | `color_cast` of `neutral` or `yellow`, and numeric `intensity` from 0 to 1 inclusive. |
| `sensor_availability` | `capability` of `camera_detect` or `lidar_scan`, and boolean `available`. |

Omit `at_ms` or send null to select the next tick. An explicit `at_ms` must be
a future aligned simulation tick. The engine assigns the sequence, checks the
whole motion interval against scripted and accepted events, and rejects
same-tick competing changes to lighting or to the same sensor. A different
sensor can change on the same tick. Invalid input leaves the schedule untouched.
The cap is 64 accepted presenter events per run. Engine collision rules still
govern actual cart motion. An accepted event reserves a schedule and does not
guarantee the intended route condition or sensor reading.

Retry a load or disruption with the same UUID and identical original payload.
A retry returns the current state and, for disruptions, the originally assigned
event without applying it again. A different payload with that ID conflicts.
The load receipt lives for the loaded run and disruption receipts live for their
run, including checkpoint restore. New loads and resets invalidate prior run
requests. A step is one action per accepted request and should not be retried
automatically after a transport failure.

## Polling and evidence boundaries

The handler validates HTTP and Pydantic input before calling the service. The
service checks run identity, applies the command through the existing runtime
under its session lock, and captures the state at the same boundary. The
session writer commits the runtime checkpoint and ledger as a pair. Mutations
await the checkpoint result when storage is enabled. JSON response serialization,
socket writes, exporter execution and checkpoint writes run outside the session
lock. The runtime's scheduler is the only background clock.

Order responses by `(generation, revision)` within a session and verify
`run_id` before associating evidence. `snapshot_sequence` counts executed ticks.
`revision` advances for acquisitions and visible changes such as paused rate or
status changes and accepted schedules. Restoring a checkpoint keeps the run ID,
pauses the runtime and durably saves its increased generation before exposing
the session, so clients can accept the restored time even if it precedes the last live poll. Replacing or deleting the
browser session requires a fresh polling baseline. Reads do not advance time,
acquire observations or invoke the provider.

- `audience` contains the actual physical snapshot, map bounds, routes, patrol
  and robot footprint. Map coordinates use centimeters in `warehouse_map`,
  with +x east and +y north. Audience geometry and future scripted inputs stay
  outside agent evidence. The state response excludes the future script.
- `observation.latest` contains at most one persisted attempt per capability
  for this run. A targeted camera sample identifies its target in `request`.
  Use the attempt's capture time, pose, frame, world version and result when
  describing an observation. A sensor outage can precede the next acquisition.
  The previous result remains dated to its original capture, and an unavailable
  result or a missing detection does not prove an object is absent.
- `belief.recent_changes` contains up to 16 evidence-backed fact and revision
  references from runtime ingestion. References can be resolved through the
  existing graph and ledger inspector at `belief.graph_path`. That inspector
  remains an on-demand legacy read. Polling excludes full chat, ledger, graph
  and audit history.

Physics uses 100 simulation ms ticks, periodic acquisition uses 500 simulation ms,
the run lasts 30 simulation seconds and the preset freshness window is 1,000
simulation ms. The controller may read pose and LiDAR between periodic acquisitions.
Playback rate changes wall pacing. Capture UTC is epoch plus simulation time,
and storage UTC records ledger reception. Pausing freezes simulation-time aging.
`llm_ready` describes whether a live provider is configured. Simulation controls,
sensing and state work in explicit offline mode.

## Errors and recovery

Errors have `{ code, error }` and use fixed public messages without exception text.

| HTTP | Codes |
| --- | --- |
| 400 | `invalid_session` for a missing or invalid session UUID. `invalid_request` for malformed bodies, unsupported controls and rejected disruptions. |
| 409 | `stale_run` when the session or generation changed. `state_conflict` for invalid transitions, conflicting request ID reuse or an exhausted input budget. |
| 503 | `runtime_unavailable` for session capacity, failed controls or failed checkpoints. `provider_unavailable` for a provider failure. `export_not_ready` while P2-12 is unwired. |
| 500 | `simulation_failed` for unexpected errors or an invalid injected export. |

After a persistence or runtime failure the state read reports `error` and can
report `persistence_error`. Polls can see in-memory changes while a write is in
flight. A failed write returns 503 and the runtime stops. Subsequent queued
writes for that failed runtime do not commit. Reset or load again to recover.
A process restart can restore the last durable boundary. Existing checkpoint
files remain readable. The extra generation, revision and receipt fields are
additive and require no database schema migration.

Legacy state, ask, reset, new-chat, session deletion and Scenario A/B endpoints
remain available. A static scenario load or legacy reset invalidates a live run.
The server serves the existing SPA shell for direct navigation to
`/simulation`. The warehouse workspace is part of P2-10.

## Try it locally

From a repository checkout, start the backend in explicit offline mode. The
named preset is validated from the
[shared scenario fixture](../../tests/fixtures/phase_two/contracts.json).

```bash
GROUNDTRUTH_PROVIDER_MODE=offline uv run --locked python -m src.web.server
```

In another terminal, use `curl` and `jq` with a fresh session ID.

```bash
base=http://127.0.0.1:8765/api/simulation
session=$(uv run --locked python -c 'from uuid import uuid4; print(uuid4())')
request_id() { uv run --locked python -c 'from uuid import uuid4; print(uuid4())'; }
get() { curl -fsS -H "X-Session-ID: $session" "$base/$1"; }
post() {
  curl -fsS -H "X-Session-ID: $session" -H 'Content-Type: application/json' \
    -d "$2" "$base/$1"
}

expected=$(get state | jq -c '{run_id,generation}')
load=$(jq -nc --arg id "$(request_id)" --argjson expected "$expected" \
  '{preset:"warehouse_inspection_v1", request_id:$id, $expected}')
run=$(post load "$load")
expected=$(jq -c '{run_id,generation}' <<<"$run")

event=$(jq -nc --arg id "$(request_id)" --argjson expected "$expected" \
  '{request_id:$id, $expected, disruption:{kind:"sensor_availability",
    capability:"lidar_scan", available:false}}')
post disrupt "$event" | jq '.event'
post control "$(jq -nc --argjson expected "$expected" '{action:"step", $expected}')" \
  | jq '{state,sim_time_ms,snapshot_sequence,revision}'
curl -sS -H "X-Session-ID: $session" "$base/export" | jq .
```

[Generated TypeScript types](../../frontend/src/simulationTypes.ts) and
[examples](../../frontend/src/simulationFixtures.ts) mirror the
[Pydantic contracts](../../src/contracts/simulation_api.py). The
[browser client](../../frontend/src/api.ts) exposes matching methods and typed
HTTP errors.

## Downstream handoff

- [P2-10 / #34](https://github.com/dhruvkshah75/groundtruth/issues/34) can use
  `api.simulationState()` and the typed mutation methods for the map, controls
  and observation panels. Keep polling and mutation responses ordered together.
- [P2-11 / #35](https://github.com/dhruvkshah75/groundtruth/issues/35) can use the
  compact change references to connect the graph, audit history and answers.
- [P2-12 / #36](https://github.com/dhruvkshah75/groundtruth/issues/36) implements
  the real replay and evaluation backend. Inject a
  [SimulationExporter](../../src/web/simulation_export.py) into `AgentService`.
  Its target supplies a run-scoped session locator, generation, capture boundary,
  exclusive sample-sequence watermark, detached scenario and accepted inputs in
  submission order. The envelope validates the run, clock, scenario, input list
  and evidence references. Only that envelope is written to the attachment.
  P2-12 owns gathering complete historical observations and belief transitions,
  and verifying deterministic replay. The default exporter is unwired and
  responds with 503 `export_not_ready`.

## Verification

The [real HTTP acceptance suite](../../tests/integration/test_simulation_api.py)
covers session and request races, lifecycle, input validation, observed motion,
sensor outages, evidence separation, blocked providers, persistence failures,
restore and export delegation. No live provider or client credentials are
required.

| Check | Result |
| --- | --- |
| `uv run --locked pytest -q` | 648 passed in 15.64 s in a clean source copy without local provider configuration. |
| `uv run --locked ruff check .` and `uv run --locked ruff format --check .` | Passed. |
| `uv run --locked python scripts/generate_simulation_types.py --check` | Generated files match Python and the shared examples. |
| `cd frontend && npm ci && npm run build && npm run test:graph && npm run test:display` | Install, type-check, build and both frontend test scripts passed. |
| Runnable curl and jq examples | State, load, disruption and step returned 200. The unwired export returned 503 `export_not_ready`. |
| Browser smoke test against the production build | Direct `/simulation` rendered the existing SPA shell. Assets, `/api/health` and `/api/state` returned 200. |
