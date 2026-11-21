# Warehouse belief lifecycle demo

**Author** Dhruv Shah  
**Status** Proposed  
**Date** 2026-11-21

## Overview

The three-minute recording follows one parcel through four stages. The robot sees it at a station, loses sight of it, sees it in an aisle outside any station, and finds it at a second station. The patrol updates beliefs before the presenter asks a question. A cart then demonstrates why LiDAR can override the expected route clearance and stop the patrol.

## The problem

The warehouse preset already ingests observations automatically. At 9.5 seconds the parcel was correctly reported as last seen, while its unsuperseded NetworkX edge still looked like a live location relationship. At 25.5 seconds the camera observed the parcel outside every named station, but the evidence summary paired those fresh coordinates with the old station label and marked the result verified. The panel and temporal question evaluator also calculate verification separately, so they can disagree about the applicable scan or freshness threshold.

## Goals and scope

- Present verified, last seen, unverified, and sensor unavailable as distinct states. Every displayed location must agree with its evidence, timestamp, and sample ID.
- Keep simulator truth on the audience map. Use acquired samples, the known static map, and stored evidence for robot claims. A missed detection never establishes a move, its cause, or the time it happened.
- Show camera coverage at acquisition time, the parcel's evidence history, and the patrol decision that stopped the robot. Preserve the existing scenario A and B behavior.
- Ship one repeatable preset, one browser rehearsal flow, and a recording script. Park seeded missed detections, new navigation, whole-zone absence claims, replay export, and inventory reconciliation.

## Critical user journeys

1. **Parcel moves out of view.** Given a camera sighting at the packing station, when the parcel moves while the robot is facing away, the audience map can display the move while the robot shows **Current location unknown · Last seen at packing station**. When a later scan sees the parcel in the aisle or at the inspection station, the card changes automatically. The graph and evidence history show the relationship and sample that apply at each stage.
2. **Cart blocks the route.** Given a route the map expects to be clear, when LiDAR measures insufficient forward clearance, the robot stops. The interface and answer identify the controller decision and its LiDAR sample. If LiDAR becomes unavailable, the interface shows that the controller is waiting for evidence. When the cart clears and the controller obtains applicable clearance, the robot resumes under the existing rules.
3. **Presenter checks the explanation.** Given the completed sequence, the presenter can ask about the current location, an earlier sighting, the stop, and the reason for the belief change. Each answer cites the relevant record, including its simulation time. The presenter can reset and reproduce the same order without chat or manual disruption timing.

## Technical plan

```text
Scenario and patrol → sensor samples → SQLite observations and station revisions
                                      → shared location assessment → evidence, graph, chat
Patrol controller   → actual decision → live patrol panel and stop explanation
Simulator snapshot  → audience map
```

- **Location evidence.** Use a run-scoped assessment at the captured simulation time. Return `verification_status`, `current_location` or null, `last_sighting` or null, and the reason and sample for lost verification. A sighting groups coordinates, frame, simulation time, sample ID, and a nullable station ID resolved from that same detection. Where the geometry overlaps, show the verified coordinates and leave the station unset. Order observations by `(sim_time_ms, sample_sequence)`, keep the loaded scenario's freshness policy, and ignore observations from another run or an incompatible observer context. A relevant sensor outage gets its own status. A `target_not_visible` response does not imply an outage.
- **Persistence.** Use the existing observation ledger for aisle sightings. The current station is derived from the applicable sighting. Keep the existing atomic station revision when a positive sighting establishes a different station. For A → aisle → B, the station chain retains A and B and the observation timeline shows the aisle between them. The revision explains the new sighting at B and leaves the unobserved transit unspecified. The assessor can read existing runs without migrating stored facts.
- **Visibility.** Use the last known point, the relevant sample's pose, sensor configuration, and known static shelf geometry to explain outside view or known occlusion. An unobstructed geometric estimate can support “this scan did not detect the parcel near its last known point.” It cannot establish that the whole zone was empty. If the point or occluder information is incomplete, report that visibility is uncertain. The camera sector in the audience view is tied to the sampled pose and labelled with the capture time.
- **Graph.** Keep SQLite as the source of record and NetworkX as its relationship projection. At the API read boundary, annotate a warehouse station edge as **verified** when the assessment supports that station, **last known** during a lapse in verification, or **earlier station** when a newer off-station sighting establishes a different position. Link annotations to the assessment's evidence. Show aisle coordinates in the entity readout. Keep the raw stored edge and revision chain available for inspection.
- **Patrol.** Surface a bounded decision record captured from the controller's actual tick, including decision time, reason, measured distance or remaining clearance where available, and the applicable LiDAR sample ID. Keep the current decision and latest stop decision in the run's checkpoint. Show the latest stop as historical after resumption. The map's expectation comes from configured route data and the route belief history. Never infer clearance from an outage or a newer unrelated capture.

## Alternatives considered

Writing an unknown-location successor on every missed scan would put visibility problems into the station revision chain and create repeated writes. A read-time assessment retains the evidence for the last known station and allows verification to expire on the simulation clock. Adding a coordinate fact for every frame would mix repeated observations with meaningful station changes. The observation ledger already stores timestamped coordinates. Removing NetworkX would simplify this demo's read path, but the assignment explicitly calls for a graph and the existing graph can show which relationships are currently supported.

## Detailed implementation plan

| Issue | Frontend changes | Start and merge dependencies | Suggested owner |
|---|---|---|---|
| WD-01 · Fix and define parcel location assessment | **No** | Start first. Publish the evidence shape for the other issues. | A |
| WD-02 · Use shared evidence in chat and graph API | **Types only** | Start on the WD-01 shape. Merge after WD-01 and the patrol decision interface from WD-03. | A, after WD-01 |
| WD-03 · Expose why the patrol stopped | **Generated types only** | Start independently. Publish the patrol response fields for WD-02 and WD-04. | B |
| WD-04 · Show world, camera, and beliefs in the demo UI | **Yes** | Start against the agreed contracts. Finish against WD-02 and WD-03. | Dhruv |
| WD-05 · Ship and rehearse a repeatable preset | **Types and browser tests only** | Start designing and stepping the scenario now. Finish browser checks after WD-04. | C |

Dhruv owns all visible UI changes. Teammates can edit shared frontend types, regenerate contracts, and add browser tests.

### WD-01 · Fix and define parcel location assessment

**Deliverable** A single deterministic location assessment that derives its station and coordinates from the same camera detection. It runs on the existing patrol samples without chat activity.

**Implementation and files**

- `src/procedural/temporal_belief_ingestion.py` — Update the existing entity summary and extract the policy for assessment from `last_positive_sighting` and the applicable later attempt so other read paths can reuse it. Preserve the station revision path and same-zone deduplication. Resolve the named station from the sighting's position and frame each time.
- `src/web/service.py` — Reuse one factory for the loaded scenario's station map and observation context in ingestion and evidence reads. Supply camera configuration and static shelf footprints through that boundary. The assessor receives evidence and configured geometry only.
- `src/contracts/simulation.py` and `src/sensorimotor/warehouse_sensors.py` — Move the shared range, angle, and line-of-sight geometry into pure functions next to `Point` and `Rectangle` in the shared simulation module. Use them in the camera sensor and the assessor, keeping world snapshot access inside the sensor. Preserve existing projection behavior.
- `tests/integration/test_temporal_belief_ingestion.py`, `tests/unit/procedural/test_temporal_belief_ingestion.py`, `tests/unit/sensorimotor/test_warehouse_sensors.py` — Reproduce the off-station bug first. Cover stale and missing scans, failed versus empty scans, out-of-order and duplicate samples, overlapping zones, known shelf occlusion, and cross-run isolation.

**Acceptance** A fresh aisle detection verifies only its sampled position. An empty scan or expired sighting retains the last sighting and creates no station revision. A later B sighting produces one A-to-B station revision linked to the new sample. The full path runs without asking the agent a question.

### WD-02 · Use shared evidence in chat and graph API

**Deliverable** Warehouse answers, entity evidence, and graph snapshots agree at the same run and simulation time. The supported recording prompts use named entities or explicit times.

**Implementation and files**

- `src/agent.py`, `src/procedural/epistemic_evaluator.py`, `src/procedural/plan_builder.py`, `src/procedural/plan_executor.py` — Have warehouse current and last-seen queries use the WD-01 assessment through the existing memory/evidence operations. Replace the separate warehouse freshness calculation and broad “latest camera” lookup with the same applicable attempt used by the assessor. Use the existing `at_time` operation for “Where was parcel_01 last seen before 8 seconds?” at the demo's actual checkpoint. Keep `changes` for the observed transition and link the audit rule and sample. Route stop questions read WD-03's recorded stop decision without taking a fresh scan to reconstruct it. Keep existing live route-status and legacy Scenario A/B behavior.
- `src/contracts/models.py`, `src/procedural/rule_based_provider.py`, `src/providers/tools.py` — Add a narrow, typed intent for explaining the most recent patrol stop. Map “Why did the robot stop?” with the offline provider and the live tool schema. Add offline/live routing coverage for the exact scripted prompts, including the bounded historical lookup. Use the actual resulting simulation timestamp when writing the script.
- `src/web/service.py`, `frontend/src/types.ts` — Return the assessment through the existing evidence response and annotate warehouse edges in `/api/state`. Add typed evidence and edge fields while retaining legacy fields for other callers. Scope current annotations to the same run and evidence cutoff as the snapshot. The graph API can decorate the existing projection without storing transient verification in SQLite or altering graph algorithms.
- `tests/unit/procedural/test_temporal_questions.py`, `tests/unit/procedural/test_plan_builder.py`, `tests/unit/procedural/test_intent_provider.py`, `tests/unit/providers/test_groq_provider.py`, `tests/integration/test_warehouse_parcel_resolution.py` — Verify the four demo prompts and sample citations, history cutoffs, stop reasons after resumption, absence of invented transit details, and agreement between answer, graph metadata, and the evidence response.

**Acceptance** At a paused checkpoint, asking where the parcel is returns the same location and source sample as the evidence endpoint. The historical answer gives the confirmed earlier sighting, and the change explanation cites the replacement evidence. A stop question cites the actual recorded decision or says that no stop has been recorded. It creates no belief change.

### WD-03 · Expose why the patrol stopped

**Deliverable** A run-scoped readout of the controller's decision and LiDAR evidence. Existing controller rules continue to govern motion.

**Implementation and files**

- `src/web/simulation_runtime.py` — Capture the latest `PatrolDecisionRecord` from the tick and the latest stop decision on entry into a stop episode. Save and restore both with optional checkpoint fields. A reset clears them. Keep the evidence keyed to the sample used in the decision, including when later scans arrive. Expose a typed read for the stop record that WD-02 can capture with the question.
- `src/contracts/simulation_api.py`, `src/web/service.py`, `scripts/generate_simulation_types.py`, `frontend/src/simulationTypes.ts` — Add optional current and latest-stop summaries to the patrol state, serialize through the existing poll, and regenerate the TypeScript contracts. Include `run_id`, decision time, state, policy reason, sample ID/time where applicable, and measured distance or remaining clearance. Keep the change backward-compatible with older checkpoints and optional fields.
- `tests/integration/test_patrol_integration.py`, `tests/integration/test_simulation_api.py`, `tests/integration/test_session_simulation.py` — Verify blocked motion, wait on outage, clearance-based recovery, the exact decision sample, and restore/reset behavior.

**Acceptance** The reported reason matches the controller behavior. A block retains its original decision evidence even after newer samples arrive. An outage is visibly distinct from a clear reading. Removing the cart leads to resumption only after the controller accepts a new applicable reading.

### WD-04 · Show world, camera, and beliefs in the demo UI

**Deliverable** The presenter can show what happened in the warehouse, what the robot saw, and why its belief changed from the existing workspace.

**Implementation and files**

- `frontend/src/SimulationWorkspace.tsx` — Give the parcel one readable current status with a separately labelled last sighting when needed. Show verified station or coordinates with the source time/ID and include known-visibility reasons. Keep historical detections and revision events inspectable. Show the patrol reason and open the exact decision sample through the evidence panel. Provide clear loading and unavailable states.
- `frontend/src/SimulationWorkspace.tsx` — Add the demo preset choice to the existing load controls after WD-05 publishes the preset identifier. Reuse reload, play, pause, and step.
- `frontend/src/WarehouseMap.tsx`, `frontend/src/simulationGeometry.ts`, `frontend/src/simulation-workspace.css` — Label actual parcel positions as the audience's world view. Reuse the existing camera sector, anchor it to the latest relevant sample's observer pose, and label range and capture time. Show known shelves and use the evidence summary for any claimed occlusion. Make status text readable in the recording and use text as well as visual styling to convey status.
- `frontend/src/BeliefGraphPage.tsx`, `frontend/src/BeliefGraphCanvas.tsx` — Carry edge verification metadata through the graph display model. Show verified, last known, and earlier station in the inspector and legend, with a visible distinction for the selected edge. Preserve the existing active/superseded history controls.
- `frontend/scripts/check-belief-appearance.mjs` — Check the selected edge's verification display and the fallback for legacy edges without warehouse metadata.

**Acceptance** With no chat open, an audience member can see the true parcel move while the belief card remains last seen. The aisle sighting has coordinates and no current station label. The graph edge and UI text distinguish that sighting from the stored earlier station. The cart stop exposes the actual decision evidence, including with keyboard-only interaction.

### WD-05 · Ship and rehearse a repeatable preset

**Deliverable** A named demo preset plus a three-minute recording script. Loading the preset stages the full lifecycle and cart event on the simulation clock.

**Implementation and files**

- `src/web/simulation_presets.py`, `src/web/service.py`, `src/contracts/simulation_api.py`, `frontend/src/simulationTypes.ts` — Register `warehouse_belief_demo_v1`, route the existing load API to it, and regenerate types. Keep the current preset as a separate option. Reset and reload use the selected scenario and a fresh run identity. Regenerate after WD-03 lands.
- `tests/fixtures/phase_two/warehouse_belief_demo.json` (new) — Configure one parcel, two stations, a shelf where useful, and a simple route. Schedule A → out of view → aisle reacquisition → B → cart obstruction → clearance. Tune positions and simulation-time windows by stepping the actual engine and camera. Give the controller a clear reading on the same mapped route before the cart blocks it. Keep B visible at the paused question checkpoint. Use explicit checkpoints rather than wall-clock delays in tests.
- `tests/integration/test_warehouse_belief_demo.py` (new), `frontend/scripts/check-simulation-browser.mjs` — Assert stage order and evidence over two runs with fresh IDs. Use the real preset and browser API. Check world/belief divergence, off-station display, cart evidence, and at least one answer matched against displayed evidence. Keep outage as a separate test case so it does not interrupt the main recording sequence.
- `docs/phase-two/Warehouse_Belief_Demo_Script.md` (new) — Write exact controls, simulation checkpoints, recording prompts, sample panels to open, and reset instructions. Reserve roughly 30 seconds for the setup, 90 for the parcel lifecycle, and 60 for the cart and evidence-backed answers. Pause between beats. The demonstration should fit within 180 seconds even with the pauses.

**Acceptance** Two consecutive rehearsals from reset reach the same stages in the same order. The presenter needs only load, play/pause, and the scripted questions. The robot completes the required belief transitions before the first question. A timed browser rehearsal fits the three-minute recording.

## Verification plan

- Run `uv run --locked pytest -q`, `uv run --locked ruff check .`, `uv run --locked ruff format --check .`, and `uv run --locked python scripts/generate_simulation_types.py --check`.
- From `frontend/`, run `npm run build`, `npm run test:graph`, `npm run test:display`, and `npm run test:simulation` against the local offline-provider server. Run the tier boundary tests as part of the backend suite and update the code-review graph after the implementation lands.
- Rehearse the initial scenario and reset once in the browser. Verify that opening chat or switching graph views adds no extra observation or station revision. A valid result uses the same captured evidence across all visible views and the answer. Exercise legacy Scenario A/B and an older checkpoint through existing tests.

*Drafted with EGM.*
