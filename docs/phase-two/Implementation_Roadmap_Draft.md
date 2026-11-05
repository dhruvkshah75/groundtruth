# Phase 2: Dynamic Warehouse Simulation — Implementation Roadmap Draft

**Discussion draft — decisions pending; no implementation authorized**

This document preserves the proposed implementation plan for group discussion. Architecture choices, numeric defaults, ownership lanes, milestones, and issue drafts below are proposals, not approved requirements. Saving or publishing this document does not authorize implementation or creation of GitHub issues. Once discussion is complete, revise and approve the plan before creating the respective issues and starting work. Member A–D are placeholders, and P2-01–P2-14 are planning identifiers rather than existing GitHub issue numbers.

## 1. Outcome and architecture

Build a 2D warehouse simulation in which a robot follows a scripted patrol while parcels, carts, obstacles, and lighting change independently. The robot periodically samples its sensors; GroundTruth maintains timestamped beliefs and answers questions about observations, changes, and uncertainty.

This develops the direction discussed in [Dynamic Simulation Plan](Dynamic_Simulation_Plan.md) and [Simulation Demo UI](Simulation_Demo_UI.md). This roadmap does not supersede documentation of currently implemented behavior.

### Proposed presentation release

- A warehouse with shelves, inspection/packing stations, several identifiable parcels, a moving cart, and a robot patrol.
- A repeatable 30-simulation-second scenario, approximately one presentation minute at default 0.5× playback, plus selected presenter-triggered disruptions.
- Camera visibility, range and occlusion; front LiDAR; pose and lighting observations.
- Continuous sensing, temporal memory, belief revisions, and readable temporal answers.
- A live UI showing the actual world, sensor observations, beliefs, and questions.
- Replay, automated evaluation logs, and a documented adapter for external robotics observations.

Live hardware, general LLM navigation, learned object recognition, and a free-form world editor are proposed follow-up work.

### Proposed shared implementation decisions

These defaults would be finalized through discussion and Issue P2-01 so members and their AI agents can build compatible components.

| Area | Proposed decision |
| --- | --- |
| World ownership | Python owns simulation state. Frontend animation interpolates backend snapshots; it cannot advance the authoritative world. |
| Time | Fixed **100 ms simulation steps**, scheduled at **0.5× playback by default** (0.25× and 1× available). Use integer simulation milliseconds and a run ID. |
| Playback | Shared `playback_rate` setting: 0.25, 0.5 (default), or 1.0; 30 simulation seconds take approximately 120, 60, or 30 wall-clock seconds, excluding pauses. |
| Sensor cadence | Camera, front LiDAR, pose and lighting sampled every **500 ms of simulation time**. No LLM calls in this loop. |
| First state | Loading a scenario creates a paused run at t=0 and takes an initial sensor sample. |
| Robot | Scripted cardinal-direction waypoint patrol, with turns at waypoints and a sensor-based stop/resume controller. |
| Visibility | Configurable camera field of view and range; shelf/obstacle occlusion. Start with a 90° field of view and 350 cm range. |
| Motion | Start with 30 cm/s robot speed and a 50 cm LiDAR stopping threshold; physics also prevents physical overlap. |
| Freshness | A sample older than **1,000 simulation ms** cannot establish current state. A successful scan missing an object makes its position “last seen,” even if the previous sighting is recent. |
| Storage | Store sampled observations, not render frames. Create belief revisions only for meaningful changes. |
| Identity | Stable simulator IDs; ambiguous human descriptions still require clarification. Disclose that visual re-identification is simulated. |
| Questions | World keeps running. Capture evidence at a defined simulation time and label the answer with that time. |
| Replay | Fixed scenario configuration and inputs reproduce logical observations and belief transitions; random UUID equality is unnecessary. |
| Existing behavior | Preserve original Scenario A/B and existing chat workflows. Visibility simulation is enabled for Phase 2 worlds rather than silently changing legacy presets. |

Playback scales wall-clock scheduling, not physical robot speed or simulation semantics. Keep physics steps at 100 simulation ms, sensors at 500 simulation ms, and freshness at 1,000 simulation ms. At 0.5×, steps are nominally every 200 wall-clock ms and sensor samples every 1 wall-clock second. Pausing freezes simulation-based freshness; manual step advances 100 simulation ms at any rate. Rate changes do not reset/jump time or skip/duplicate events or samples. Render smoothly between received snapshots; keep UI polling in wall time. Space major events apart and include stable inspection periods. Test equivalent logical outcomes across rates using an injected monotonic clock, not sleeps. LLM latency remains wall time and answers retain capture-time labels.

Maintain the existing tier boundaries: Tier 3 produces observations; Tier 2 interprets them and requests belief updates; Tier 1 stores evidence and history. The audience’s world view and scripted future events are never supplied to the LLM as evidence.

## 2. Team ownership and delivery sequence

Assign each member a lane after discussion. Everyone writes the tests for their own component; evaluation is shared work rather than something left to one member at the end.

| Lane | Primary ownership | Main issues |
| --- | --- | --- |
| **Member A** | World engine, sensors, patrol, robotics boundary | P2-02, 03, 06, 13 |
| **Member B** | Observation storage, temporal beliefs, replay/evaluation | P2-04, 05, 12 |
| **Member C** | Shared interfaces, runtime/concurrency, agent questions, API | P2-01, 07, 08, 09 |
| **Member D** | Simulation workspace, graph/timeline, presentation integration | P2-10, 11, 14 |

Proposed GitHub milestones, measured from the eventual implementation start:

| Milestone | Target | Exit condition |
| --- | --- | --- |
| **M1 — Contracts and foundations** | Days 1–3 | Shared types and fixtures merged; engine, sensor, storage and UI skeleton work independently. |
| **M2 — First integrated dynamic agent** | Days 4–6 | Patrol and sensing run independently of questions; visible parcel relocation updates memory and the UI. |
| **M3 — Complete demo behavior** | Days 7–9 | Occlusion, route changes, lighting, disruptions and temporal questions work together. |
| **M4 — Evaluation and presentation release** | Days 10–12 | Replay and evaluation pass; demo rehearsed; documentation and backup ready. Day 13 is contingency. |

**Day 5 integration target:** one moving parcel → sensor sample → observation record → belief revision → displayed graph/timeline update. Add complexity to this working path.

After plan approval, create the issues up front. Members may develop against P2-01 fixtures while dependencies are in progress, but dependent issues close only after integration passes.

## 3. Proposed GitHub issue drafts

Use `P2-01` through `P2-14` as planning identifiers; replace dependency references with actual GitHub issue numbers after creation. Suggested labels are `phase-two` plus the relevant lane: `simulation`, `memory`, `agent`, `api`, `ui`, `evaluation`, or `integration`.

### P2-01 — Define Phase 2 contracts, scenario specification and test fixtures

**Owner:** C, with review from all members  
**Milestone:** M1 · **Dependencies:** None

**Work**

- Publish typed Python contracts and matching TypeScript fixtures for simulation status, sampled observations, temporal belief summaries, controls and disruption requests.
- Define a common warehouse layout: shelves, packing and inspection stations, two named route segments, several parcels and a cart.
- Define the default 30-simulation-second event sequence (approximately 60 presentation seconds at 0.5×), with stable viewing periods between major changes: visible parcel relocation, cart blocking/clearing a route, lighting change and temporary object occlusion.
- Define a `SensorSource` interface using the existing observation request/result contracts. Keep simulator-only world snapshots outside this interface.
- Document simulation time, wall-clock playback, acquisition UTC and database storage time separately; include validated playback_rate in config/status and shared fixtures.

**Acceptance criteria**

- [ ] All four members can build against the same validated example payloads.
- [ ] Fixture events occur at integer simulation times with stable ordering; playback_rate defaults to 0.5 and accepts only 0.25, 0.5 or 1.0.
- [ ] The initial layout supports visible movement, occlusion and a safe patrol.
- [ ] Contracts document which data is audience-only and which the agent may consume.

### P2-02 — Implement the deterministic warehouse simulation engine

**Owner:** A  
**Milestone:** M1 · **Dependencies:** P2-01

**Work**

- Add fixed-step advancement, scripted object/cart motion and deterministic event application. Carry playback_rate in engine config/status; rate changes affect future runtime scheduling only, preserving tick size, event ordering and current run/time.
- Support paused/running/completed states, step, reset and detached world snapshots.
- Implement static geometry and collision boundaries. Stop the default scenario at 30 simulation seconds without looping automatically.
- Keep advancement independent of HTTP polling, user questions and sensor requests.

**Acceptance criteria**

- [ ] Identical starting state and inputs produce identical logical world states.
- [ ] Reading state or taking a sensor observation never advances time.
- [ ] Pausing freezes simulation time; one step advances exactly 100 ms.
- [ ] Reset creates a new run at t=0.
- [ ] World version advances consistently when physical state changes.
- [ ] Rate changes preserve logical state and 100 ms manual steps; replaying equal simulation-time inputs yields equal outcomes across supported rates.

### P2-03 — Add visibility-aware camera and timestamped sensor samples

**Owner:** A  
**Milestone:** M1 · **Dependencies:** P2-01, P2-02

**Work**

- Add Phase 2 camera field of view, distance limits and line-of-sight occlusion.
- Return visible object IDs, apparent colors and simulated position estimates with pose/frame context.
- Attach run ID and simulation time to samples; preserve timezone-aware observation timestamps.
- Support camera/LiDAR outages and explicit failure results. Sample timing depends on simulation time, never directly on playback rate.
- Provide front-LiDAR range/distance behavior suitable for the patrol controller.

**Acceptance criteria**

- [ ] Objects behind the robot, beyond range, in another room or behind a shelf are not detected.
- [ ] A successful empty scan is distinguishable from a sensor failure.
- [ ] Hidden object coordinates and explanations such as “behind shelf 2” do not leak into observations.
- [ ] Robot rotation changes the visible region.
- [ ] Original Scenario A/B tests remain valid.

### P2-04 — Store sensor observations and expose temporal retrieval

**Owner:** B  
**Milestone:** M1 · **Dependencies:** P2-01

**Work**

- Add an additive observation ledger to the existing SQLite repository.
- Store observation ID, run ID, capability/source, simulation time, observation UTC time, reception/storage time, context and payload.
- Add repository methods for latest observation, last sighting, bounded object history and observations up to a requested simulation time.
- Keep sampled evidence separate from active belief facts.
- Preserve existing database snapshots and old sessions. Playback rate does not change stored simulation capture times or temporal query ordering.

**Acceptance criteria**

- [ ] Duplicate observation IDs are idempotent.
- [ ] Delayed observations remain queryable without becoming the latest state.
- [ ] Histories are isolated by run and entity.
- [ ] Empty scans and sensor failures are retrievable as evidence.
- [ ] Existing snapshots restore successfully; new tables are initialized after restoring an older database image.
- [ ] Existing `FactQuery.as_of` keeps its storage-history meaning; simulation-time queries use the new interface.

### P2-05 — Implement temporal belief updates, last-seen state and uncertainty

**Owner:** B  
**Milestone:** M2 · **Dependencies:** P2-03, P2-04

**Work**

- Add a Tier 2 observation-ingestion service using interfaces to sensors and memory.
- Convert observed parcel positions into supported location relationships, retaining precise positions in observation evidence.
- Record revisions when an observed location changes; suppress repeated identical facts and sub-threshold position jitter.
- Track last sighting, latest scan, visibility and freshness separately.
- Apply the same ingestion policy to periodic samples and question-triggered samples.
- Update route beliefs only when the scan’s pose and mapped segment support that route.

**Acceptance criteria**

- [ ] Visible relocation creates an evidence-backed revision and preserves the earlier record.
- [ ] Repeated identical scans refresh last-seen information without duplicating graph facts.
- [ ] Missing detection never fabricates a new location or a disappearance.
- [ ] Stale, wrong-context and out-of-order samples do not overwrite newer applicable beliefs.
- [ ] A route scan cannot update another route simply because both are “ahead” at different poses.
- [ ] Graph edges remain identifiable as current observations or last-known beliefs; active does not automatically mean fresh.

### P2-06 — Implement the scripted patrol and reactive safety controller

**Owner:** A  
**Milestone:** M2 · **Dependencies:** P2-02, P2-03

**Work**

- Execute waypoint patrol with cardinal turns and pose updates.
- Use latest fresh LiDAR observations for deterministic stop/resume decisions.
- Stop if required safety observations are unavailable or stale.
- Keep physical collision prevention in the simulator; record safety decisions from actual sensor evidence.
- Expose patrol state: moving, turning, blocked, waiting for sensor, or complete.

**Acceptance criteria**

- [ ] A cart entering the robot’s path causes a sensor-backed stop.
- [ ] A later clear scan permits resumption.
- [ ] A sensor outage causes a safe stop.
- [ ] No LLM request is needed for routine patrol or stopping.
- [ ] Robot pose changes affect subsequent camera/LiDAR readings.

### P2-07 — Run simulation and sensing without blocking on LLM answers

**Owner:** C  
**Milestone:** M2 · **Dependencies:** P2-02, P2-04, P2-05; integrate P2-06

**Work**

- Add a service-owned background scheduler; do not start a new worker on every request.
- Schedule fixed steps using a monotonic wall-clock accumulator scaled by playback_rate (default 0.5); preserve fractional progress during rate changes and prevent skipped/duplicated ticks. Schedule periodic acquisition against simulation time as a separate responsibility. There is no world-change notification to the agent.
- Split query handling into interpretation, short locked evidence collection/evaluation, and unlocked answer synthesis.
- Serialize questions per session, while allowing simulation ticks between LLM calls.
- Protect reset/delete with a session generation token so old answers cannot attach to a new run.
- Checkpoint simulation and belief state at one wall-clock-second intervals and on pause/control operations; restore saved runs paused.

**Acceptance criteria**

- [ ] A deliberately slow fake LLM does not freeze the world or sensor loop.
- [ ] Each answer retains its evidence-capture time even when the world advances.
- [ ] Repeated play requests do not create duplicate workers or samples.
- [ ] Reset/delete during an answer prevents stale results from being appended.
- [ ] Shutdown stops workers before closing repositories.
- [ ] Separate sessions cannot mutate each other.
- [ ] With no viewer activity for 60 seconds, a run pauses and checkpoints rather than running indefinitely.

### P2-08 — Add temporal object questions and evidence-based explanations

**Owner:** C  
**Milestone:** M3 · **Dependencies:** P2-04, P2-05, P2-07

**Work**

- Add an `object_location_query` intent/tool with modes `current`, `last_seen`, `at_time`, `history`, and `changes`; require a simulation time for `at_time`.
- Extend both live and explicit offline providers.
- Retrieve temporal evidence through typed repository operations.
- Give answer synthesis structured sightings, revisions, uncertainty and capture time; retain existing review/fallback behavior.
- Support “where was it before and what changed?” through the history/changes modes, without requiring general multi-step planning.

**Acceptance criteria**

- [ ] Answer current location, last sighting, earlier observations and change explanations.
- [ ] A blind interval produces a qualified last-seen answer.
- [ ] An at-time query returns relevant preceding evidence with its age and limitations.
- [ ] Answers never invent exact movement time, path or physical cause.
- [ ] Ambiguous parcel descriptions trigger clarification.
- [ ] Existing route-status, perspective and audit questions continue to work.
- [ ] The displayed answer identifies the simulation time its evidence describes.

### P2-09 — Expose simulation controls and lightweight live-state APIs

**Owner:** C  
**Milestone:** M2–M3 · **Dependencies:** P2-01, P2-07; integrate P2-06

**Work**

Add session-scoped endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/simulation/load` | Load a named warehouse preset into a paused run. |
| `POST /api/simulation/control` | Accept `play`, `pause`, `step`, `reset`, or `set_rate` (playback_rate: 0.25, 0.5, 1.0). |
| `POST /api/simulation/disrupt` | Apply one validated presenter disruption. |
| `GET /api/simulation/state` | Return a lightweight, versioned live snapshot. |
| `GET /api/simulation/export` | Export the run’s replay/evaluation bundle. |

- Separate audience world state from robot observations/beliefs in response types.
- Include run ID, simulation time, snapshot sequence, selected playback_rate, patrol/sensor status and recent timeline changes.
- Keep the existing chat endpoint; append capture-time metadata to relevant answers.
- Do not return the entire ledger and chat history on every live-state poll.

**Acceptance criteria**

- [ ] Controls are validated, session-isolated and safe on repeated requests.
- [ ] Step is accepted only while paused.
- [ ] Disruptions include parcel relocation, cart blockage/clearance, lighting and sensor outage.
- [ ] Disruptions alter the world or sensor availability; they do not directly edit beliefs.
- [ ] Invalid requests do not partially modify a run.
- [ ] Old endpoints and restored legacy sessions remain functional.

### P2-10 — Build the live 2D warehouse workspace and presenter controls

**Owner:** D  
**Milestone:** M2–M3 · **Dependencies:** P2-01; live integration P2-09

**Work**

- Add a `/simulation` workspace using React and SVG.
- Render shelves, stations, parcels, cart, robot, patrol motion and camera field of view.
- Poll the lightweight state endpoint every 250 wall-clock ms while visible, independently of playback rate; interpolate between received positions without predicting future events.
- Add play/pause/step/reset, 0.25× / 0.5× / 1× playback controls (default 0.5×), scenario selection and selected disruption controls. Interpolate smoothly between received snapshots and clearly label simulation time versus wall time.
- Clearly label the audience world view, sensor state and provider mode.

**Acceptance criteria**

- [ ] The UI renders backend state; animation cannot change authoritative coordinates.
- [ ] Stale responses from a previous run are ignored.
- [ ] Polls do not overlap, and unmounting cleans up timers and requests.
- [ ] Disconnection freezes interpolation and shows a stale/disconnected state.
- [ ] Controls work with the keyboard and on the presentation screen.
- [ ] Existing workspace navigation remains usable.

### P2-11 — Connect temporal beliefs, graph updates and questions to the demo

**Owner:** D  
**Milestone:** M3 · **Dependencies:** P2-05, P2-08, P2-09, P2-10

**Work**

- Add a focused belief panel and chronological observation/revision timeline.
- Reuse the existing belief graph and highlight genuine changes from backend facts.
- Mark last-seen/stale knowledge without erasing historical evidence.
- Add suggested temporal questions and render answers with their capture times.
- Expose source records and audit details on expansion.

**Acceptance criteria**

- [ ] Moving a visible parcel updates the observation, belief and graph through the backend.
- [ ] An unseen move changes the audience view but does not reveal the new position in beliefs or answers.
- [ ] The last known location is visually distinguishable from currently verified information.
- [ ] Prior facts remain inspectable after revisions.
- [ ] A delayed answer is visibly associated with its earlier capture time.

### P2-12 — Implement deterministic replay and ten evaluation scenarios

**Owner:** B, with scenario contributions from A/C/D  
**Milestone:** M3–M4 · **Dependencies:** P2-02 through P2-09

**Work**

- Export scenario configuration, timed presenter inputs, sampled observations and belief transitions.
- Keep simulator events separate from agent evidence.
- Support rerunning the scenario and replaying recorded observations into a fresh belief store.
- Produce readable JSON/Markdown evaluation logs with setup, expected result, actual evidence, policy and pass/fail.

**Required scenarios in the proposed release**

1. Map-clear versus cart-blocked route, followed by clearance.
2. Parcel relocation visible to the robot.
3. Parcel relocation while outside view.
4. Shelf occlusion and later reacquisition.
5. Ambiguous descriptions of similar parcels.
6. User expectation versus lighting-affected perception versus history.
7. Wrong-frame or wrong-route evidence.
8. Sensor outage and stale current-state knowledge.
9. Delayed older observation after a newer sighting.
10. Brief movement occurring entirely between sensor samples.

**Acceptance criteria**

- [ ] Replays reproduce logical beliefs and revision relationships.
- [ ] Hidden simulator events cannot appear in evidence-only answers.
- [ ] All ten logs state the conflict or uncertainty being tested.
- [ ] Deterministic CI tests require neither API credentials nor real-time sleeps.
- [ ] Existing Scenario A/B remain in the regression suite.

### P2-13 — Define and test the external robotics sensor adapter

**Owner:** A  
**Milestone:** M3 · **Dependencies:** P2-01, P2-03

**Work**

- Implement a recorded-message adapter behind the shared `SensorSource` interface.
- Document required identity, timestamp, coordinate-frame, position and failure fields.
- Define how external timestamps map to run time and how invalid or mismatched frames are rejected.
- Document what the other robotics team must supply for a future live connection.

**Acceptance criteria**

- [ ] Recorded external-style observations pass through the same ingestion service.
- [ ] Missing identity or invalid timing/frame data produces explicit limitations or rejection.
- [ ] No simulator world access is required.
- [ ] Documentation distinguishes the demonstrated adapter from unimplemented live hardware integration.

### P2-14 — Package, rehearse and document the presentation release

**Owner:** D coordinates; all members contribute  
**Milestone:** M4 · **Dependencies:** P2-06 through P2-13

**Work**

- Rehearse the 30-simulation-second default scenario at 0.5× (approximately one presentation minute) and questions, allowing answers immediately afterward when provider latency requires it.
- Prepare a longer interactive demonstration with presenter disruptions.
- Record a clearly labelled backup and document explicit offline mode.
- Update architecture documentation, setup instructions, limitations, evaluation results and member contributions.
- Produce the architecture verification report explaining layer boundaries, observation flow, temporal policy and LLM involvement.

**Acceptance criteria**

- [ ] A fresh checkout can run the demo using documented commands.
- [ ] Three clean rehearsals at default 0.5× reproduce the expected observations and beliefs; verify slower/faster playback and pause/step controls.
- [ ] Backend tests/lint, frontend build and graph checks pass.
- [ ] Measured scan cadence, revision latency and answer latency are reported separately.
- [ ] Demo shows visible relocation, a safety stop/resume, occlusion/uncertainty and a temporal explanation.
- [ ] No presentation claim implies real visual tracking, live robotics integration or guaranteed real-time performance unless demonstrated.

## 4. Proposed integration rules and release acceptance

After approval, use branches such as `codex/p2-02-simulation-engine`, one issue per PR where practical, and link dependencies in the PR description. Shared-interface changes require review from affected lanes. Merge through pull requests rather than directly pushing to protected `main`.

Each issue includes its own meaningful tests and a short verification example. After P2-01 merges, members can use its fixtures for parallel work; fixture-backed UI and fake adapters must be replaced by real integration before closing dependent issues.

The proposed presentation release is complete when:

- The simulation evolves and the robot observes without user questions.
- Slow LLM calls do not stop simulation progress.
- Visible changes revise beliefs; unseen changes do not leak into them.
- Questions distinguish observed state, last-seen state and history.
- Safety decisions cite relevant sensor evidence.
- The UI makes those distinctions clear during the warehouse demonstration.
- Replay, ten evaluation logs, setup documentation, the architecture report and a labelled backup are ready.
