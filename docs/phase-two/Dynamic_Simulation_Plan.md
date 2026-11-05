# Phase Two: Dynamic Simulation and Time-Aware Beliefs

**Status: simulation proposal; P2-01 contracts and fixtures implemented separately.** This plan responds to the professor's feedback about a changing environment and possible integration with another robotics team. It extends the current three-tier system and complements the required groundedness and perspective scenarios. The separate [demo plan](Simulation_Demo_UI.md) describes how to present it.

## Goal

Build a repeatable 2D simulated environment that changes independently over time. A robot periodically observes only what its simulated sensors can see. GroundTruth maintains sourced, time-aware beliefs from those observations and answers questions about what it observed, what changed, and what it cannot currently verify.

The project does not need a complete 3D reconstruction or a physically accurate replica of reality. The first version is a controlled 2D model, with explicit simplifications, that makes temporal grounding and partial observation visible.

The [shared contract reference](Shared_Simulation_Contracts.md) documents the implemented data definitions and fixture checks. The engine, sensor geometry, runtime and demo below remain proposed.

## Starting point

The existing mock environment has 2D positions, robot pose, camera and LiDAR observations, spatial context, and world versions. Configuration helpers can change its world, but they are not an independently running simulation or public robot movement actions. The existing camera's visibility behavior is not yet the field-of-view and occlusion model proposed here. See the [Tier 3 guide](../tier3/Tier3_Implementation_Guide.md) and [current system overview](../Implemented_System_Overview.md).

Tier 1 already stores sourced facts and revisions; Tier 2 plans and evaluates evidence; the React UI shows answers and a belief graph. Treat the following as work to add, not functionality already demonstrated. Preserve the existing Scenario A and B workflows.

## Separation of world and robot

```text
Independent simulation clock -> world state evolves -> visual world for audience
                                            |
Robot-owned periodic loop -> sensor API ----+-> observations
                               -> observation validation and change detection
                               -> Tier 1 evidence, belief revisions and history
User question -> Tier 2 -> evidence and temporal evaluation -> grounded answer
```

The environment does not notify the agent of changes. The simulator keeps an authoritative state for rendering and calculating sensor readings; the agent must access the world only through sensor observations. The audience may see the world and the agent's belief side by side, clearly labelled, but the agent cannot use the audience view as evidence.

A periodic loop is an appropriate first model of robot sensing. Use a configurable interval, initially 500 simulation milliseconds, and allow a question to request a fresh scan when supported. The LLM need not run for each frame: deterministic code handles acquisition and change detection; the LLM can interpret user questions and explain supported results. This lowers cost and latency. Sampling still means movement between scans can be missed.

## Proposed world and sensors

- A small 2D room or grid with a robot, tables, a red cup and an obstacle. Positions use the existing coordinate conventions where practical.
- A fixed simulation timestep, with pause, play, reset and manual step. Script object motion independently from the robot; optionally permit presenter-controlled changes.
- Camera observations include object identity when available, apparent color, estimated position, observation time, robot pose and spatial context. Add a configurable field of view, range and simple occlusion.
- Keep the existing LiDAR route example and add moving obstacles after the cup scenario works. Optional missed detections and measurement noise come later.
- Simulation time must be explicit and advance consistently when paused, stepped or replayed. Distinguish observation time from storage time; handle delayed or out-of-order observations without silently treating them as latest.
- Stable object IDs supplied by the simulator are an initial simplification, not real visual re-identification. Two similar objects without reliable identity require uncertainty or clarification.

## Presentation pacing and playback time

Default to **0.5× playback** so the audience can follow movement, observations, and graph changes. A 30-second simulated scenario takes approximately **60 seconds of presentation time**, excluding pauses and question latency. Offer **0.25×**, **0.5×**, and **1×** controls, plus pause and manual step.

| Quantity | Simulation-time setting | Presentation time at 0.5× |
| --- | --- | --- |
| Scenario duration | 30 seconds | Approximately 60 seconds |
| Fixed physics step | 100 ms | Nominally every 200 ms |
| Sensor interval | 500 ms | Nominally every 1 second |
| Freshness limit | 1,000 ms | Reached after 2 seconds of playback without newer evidence |

Playback rate is a presentation/runtime setting, separate from robot speed in cm per simulation second. Changing rate changes how quickly fixed steps are scheduled against a monotonic wall clock; it does not change physics step size, event times, sensing cadence, acquisition timestamps, freshness calculations, or the logical outcome at the same simulation time. Validate a shared `playback_rate` field against the supported values and include the selected rate in configuration/status snapshots. Rate changes must not reset, jump, duplicate, or skip simulation ticks. A paused manual step always advances exactly 100 simulation ms at any rate.

Interpolate only between received backend snapshots for smooth frontend motion. Rendering and the proposed 250 ms live-state polling remain wall-time operations, independent of playback rate; they cannot advance world time or create observations. Pausing also freezes simulation-based freshness. Actual LLM/network latency remains wall time and is not slowed by playback controls; answers retain their evidence-capture simulation time.

Space major scripted changes apart and leave stable viewing periods between them. At 0.25× the same scenario takes approximately two minutes; at 1× it takes approximately 30 seconds. These are nominal playback durations, not hard real-time guarantees. Verify pacing with an injected monotonic clock, and replay the same simulation-time inputs across rates to check equivalent observations and belief transitions without wall-clock sleeps.

## Observation, memory and answers

On each sensing interval, request observations through Tier 3, validate source, time, frame and entity, then compare them with applicable previous observations. Record meaningful changes and policy decisions with provenance in Tier 1. Avoid writing every identical frame: define a change threshold and optional periodic heartbeat or retention rule. Preserve old observations and revision links; do not overwrite history.

Maintain last observed position and time separately from whether the object is currently visible. A newer sighting can update an active position belief. No detection does not by itself prove that an object moved: it may be outside the field of view, occluded, or missed. Apply a documented freshness rule and label a stale observation as **last seen**, rather than asserting it is current. Record evidence actually observed; two positions do not prove the path, exact movement time, or cause.

Example: the camera sees a cup on table A at t=0, then at table B at t=2. The agent can report both observations and their order. If it is not visible at t=4, it can say it was last seen at table B at t=2 and its current position is unverified. A movement outside view cannot be inferred from simulator truth.

Useful queries include “Where is the cup now?”, “Where was it at the start?”, “What changed and when did you observe it?”, “When did you last see it?”, and “Why did your belief change?” Existing answer synthesis may explain a structured timeline, but must not add unobserved events or certainty.

## Cases and expected behavior

| Situation | Expected result |
| --- | --- |
| Cup moves within view | A later scan observes the new position; preserve the earlier observation and record a justified update. |
| Cup moves while outside view | Retain the last sighting as history; current location is unverified. |
| Cup moves and returns between scans | The movement may be missed; do not claim it was detected. |
| Cup is occluded | Report not currently visible; do not infer disappearance or movement solely from absence. |
| Similar cups or uncertain identity | Do not merge identities without support; clarify or report uncertainty. |
| Stale, delayed or failed sensor | Preserve the last valid observation and qualify or refuse current-state claims. |
| Robot rotates or moves | Compute visibility relative to its pose and the relevant coordinate frame. |
| Conflicting observations | Compare time, context and source; retain provenance and explain the policy used. |

## Milestones and acceptance

1. **Independent 2D world and sensors:** scripted movement, fixed clock, controls, camera view/range and a visible distinction between actual world and sensor readings. Accept when a cup can move without an agent call and visibility changes the returned observation.
2. **Periodic sensing and temporal memory:** robot-owned loop, timestamped observations, freshness and revision history. Accept when a visible move changes belief and an unseen move does not leak into it.
3. **Temporal questions:** add supported current, historical, last-seen and change-explanation questions, including clear uncertainty and evidence attribution. Accept when blind intervals and stale evidence cannot be presented as certain current knowledge.
4. **Replay and evaluation:** deterministic fixtures and tests for the table above, including pause/step and sensor failure. Compare logical outcomes and revision relationships rather than random IDs. Produce readable logs, including the ten distinct conflict scenarios required by the project brief.
5. **Robotics integration boundary:** define an adapter that translates an external robot's observations into the same contracts. Agree sensor types, rates, clock alignment, coordinate frames, IDs, confidence and transport with the other team; first test using recorded or synthetic messages. A live robot remains dependent on their interfaces and readiness.

## Tradeoffs and scope

The main benefit is a demonstrable agent operating with changing, partial evidence, rather than a single static preset. The costs are time synchronization, visibility, identity matching, storage volume, concurrency and reproducibility. Begin with a single robot, a few objects, deterministic scripted motion and manual stepping; do not promise real perception, navigation or real-time performance from a 2D mock.

Before implementing, choose a lifecycle for the simulation and sensing loops per UI session, including safe start/stop/reset and cleanup. Prevent overlapping loops and inconsistent snapshots. Define a deterministic offline replay for automated testing and a fallback for a live demo. The LLM and any review step remain probabilistic; they do not replace evidence checks.
