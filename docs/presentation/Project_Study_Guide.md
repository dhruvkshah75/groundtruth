# GroundTruth Project Study Guide

**Purpose:** a shared presentation and revision guide for the GroundTruth team. It explains what the current project does, how one question moves through the agent, what the mock world contains, which contributions are visible in the repository history, and what work remains after the assignment milestones.

**Status date:** 3 October 2026. This guide describes the current `main` branch and the assignment in [`problemStatement.pdf`](../../problemStatement.pdf). Issue and pull request links point to the project's GitHub repository.

For a short presentation version, see [Three-Slide Presentation Outline](Three_Slide_Presentation_Outline.md). For setup and commands, see [Local Development Setup](../Local_Development_Setup.md).

## 1. The project in one minute

GroundTruth is a three-tier agent that answers questions about a robot and its environment using traceable evidence. It is designed to handle disagreements between:

- **stored claims**, such as an old map saying a route is clear;
- **current observations**, such as LiDAR seeing an obstacle 12 cm ahead; and
- **different perspectives**, such as what a user expects, what a camera currently sees, and what a maintenance record says.

The language model helps interpret the user's wording and proposes a permitted task through function calling. Deterministic Python validates the proposal, decides which evidence operations are required, runs those operations, compares the evidence, and controls the trusted answer. The model cannot make up a sensor result or change a belief by itself.

The running demo has a React/TypeScript browser interface, a Python HTTP API, a Groq-compatible live LLM provider, explicit offline mode, a SQLite evidence ledger, a NetworkX active-belief graph, and a deterministic mock robot environment. It includes a chat view and an interactive graph view at `/graph`. Scenario A and Scenario B are seeded backend states, not canned answers supplied by the browser. See the [current system guide](../Implemented_System_Overview.md) and [architecture decisions](../Architecture_Decisions.md).

## 2. The complete flow of one question

The following is the shared flow to explain to the audience, regardless of which team member presents:

```text
Question in React UI
        │
        ▼
Python web API identifies the browser session
        │
        ▼
LLM proposes one function call from the approved intent set
        │
        ▼
IntentPlanner validates the proposal and its arguments
        │
        ▼
EntityResolver + CapabilityValidator + PlanBuilder
        │  create a typed, deterministic evidence plan
        ▼
PlanExecutor calls Tier 1 memory and/or Tier 3 sensors
        │
        ▼
EpistemicEvaluator compares the actual returned evidence
        │  may record a justified belief revision in Tier 1
        ▼
Python sends the verified result back to the model for wording
        │
        ▼
Response guard checks the model text against the deterministic result
        │
        ▼
API returns answer, evidence, telemetry and trace; React renders them
```

### Step by step

1. **The user submits a question.** The React app calls the Python API with the question and a session identifier. The API keeps each browser's agent, memory, environment and conversation history isolated from other sessions.
2. **The model proposes a task.** In live mode, `GroqIntentProvider` sends the question with strict function schemas. The model must choose one of the approved functions and return structured arguments. In explicit offline mode, `RuleBasedIntentProvider` classifies a limited set of common phrases for deterministic demos and tests.
3. **Tier 2 validates the proposal.** `IntentPlanner` checks the tool/intent name, fields and original user question against Pydantic contracts. Malformed output receives one repair attempt. A valid `unsupported` result can receive a bounded wording review and, at most, one constrained reconsideration. There is no unbounded model retry loop.
4. **Tier 2 resolves the subject and builds the plan.** User-facing mentions such as “front route” or “red box” are resolved to canonical IDs such as `route_A` or `box_01`. The capability registry checks that requested sensors are actually available. `PlanBuilder` creates the required memory and observation operations; the model does not get to invent SQL or an arbitrary tool sequence.
5. **The executor gathers evidence.** Tier 1 returns stored claims and history through its public repository interface. Tier 3 calculates an observation from the current mock-world state. Each fact and observation has an ID and context so the system can show where a result came from.
6. **The evaluator applies project policy.** `EpistemicEvaluator` checks relevance and context, separates perspectives, identifies conflicts, and decides whether a revision is justified. It returns a typed evaluation rather than trusting a free-form model answer.
7. **A revision is recorded when appropriate.** For Scenario A, the evaluator asks the Tier 1 repository to atomically store the new sensor-backed fact, supersede the old active claim, and record the audit event. A successful write invalidates the derived graph cache.
8. **The model receives the result.** In live mode, the actual tool execution/evaluation result is sent back as tool context for a concise explanation. Python only accepts a final response that matches its evidence-backed deterministic explanation under the response guard. If the model gives no answer, fails, or changes the meaning, Python returns the safe deterministic text or an API error as appropriate.
9. **The UI displays the returned state.** The conversation shows the answer and tool trace. The system inspector shows Tier 2 plan/outcome, Tier 1 graph and ledger, audit events, sensor telemetry and Tier 3 world state. The separate `/graph` view visualizes active relationships and provenance. The frontend renders backend values; it does not substitute preset answer text after a backend failure.

### What “ReAct” means in this implementation

ReAct means the model participates in a bounded **propose → observe tool result → explain** loop. It is not an unrestricted agent that can call any Python function. The permitted function describes a supported request type, and deterministic code expands it into required operations.

For example, a model can propose `current_route_status` for “front route”. It cannot decide that an old map alone is enough: Python's route plan requests the active route claim and a fresh front LiDAR scan when that capability is available. The model then receives the real results and may explain them, subject to the response guard.

The fixed intent set constrains **what kind of task** can run; it does not require users to use one exact sentence. Live language understanding can map different phrasings into the same allowed intent. The deterministic entity and evidence rules decide whether there is enough information to continue safely.

## 3. What each tier does

| Tier | Plain-language role | Current implementation | It does not do |
|---|---|---|---|
| **Tier 1 — Declarative** | Keeps claims, sources and their history. | SQLite `MemoryRepository`; fact, audit and alias tables; atomic revision; `ActiveBeliefGraph` NetworkX projection; graph consistency checks and write invalidation. | Read sensors, interpret the user's request, or decide which conflicting source wins. |
| **Tier 2 — Procedural** | Decides which evidence is needed and how the results should be interpreted. | Contracts, providers, intent planner, coverage reviewer, entity resolver, capability validator, plan builder/executor, composition service and epistemic evaluator. | Trust raw LLM output, execute an arbitrary model-generated query, or claim facts without evidence. |
| **Tier 3 — Sensorimotor** | Represents the simulated physical world and calculates observations. | `MockEnvironment` with robot pose, rooms, obstacles, objects, ambient light, LiDAR, camera and pose sensors. | Query SQLite, understand natural language, or choose the epistemic policy. |

### Tier 1: memory and graph

SQLite is the authoritative evidence ledger. A stored fact is a sourced claim with a subject, predicate, object, confidence, observation time, storage time, context and supporting evidence. Example:

```text
subject: route_A
predicate: status_is
object: clear
source_agent: static_map
context: room_101 / robot_base
```

This says **the static map claimed that `route_A` was clear**. It is not proof that the route remains clear now. When a later observation contradicts it, the earlier fact remains in SQLite for historical and audit questions. A revision links it to a successor and records the reason and policy rule.

NetworkX is a disposable view of the active SQLite facts. It is rebuilt when stale and can be thrown away and recreated from SQLite. It is useful for viewing/traversing relationships, while SQLite is used for exact queries, provenance, history and audit trails. The graph does not decide which claim is true.

### Tier 2: typed contracts, planning and safety

Pydantic contracts form the shared data language across layers. Examples include `FactAssertion`, `StoredFact`, `FactQuery`, `SpatialContext`, `SensorObservation`, `EntityResolution`, `IntentRequest`, `ExecutionPlan` and `GroundedResult`. Strict fields and validation catch malformed or inconsistent data at boundaries.

The current allowed intents are:

| Intent | Meaning | Typical evidence path |
|---|---|---|
| `current_route_status` | Is a route currently clear or blocked? | Active route memory plus fresh front LiDAR when available. |
| `current_object_perception` | What does the camera currently see about an object? | Resolve one target object, then request camera detection; the evaluator may query history for the object ID returned by the camera. |
| `historical_fact_lookup` | What did memory record about an entity? | Query historical facts (`active_only=false`), with an optional explicit provenance source filter. |
| `audit_explanation` | Why did a belief change or get replaced? | Query the relevant active fact(s), then retrieve the audit chain for those facts. |
| `current_robot_pose` | Where is the robot now and which direction is it facing? | Fresh pose observation; include the observed location and frame when present. |
| `environment_action` | The user requests an action such as moving or picking something up. | Safely refused/no executable plan until the contract and safety authorization define an action. |
| `unsupported` | The request does not map to an allowed task. | Bounded review can catch some false negatives; otherwise a safe no-plan outcome. |

An **entity mention** is the user's phrase for the target record, such as “red box”. A **canonical entity ID** is the stable internal identifier, such as `box_01`. For historical queries, the latest contract also separates an optional `source_agent_mentions` field from `entity_mentions`. This lets the request distinguish *which item is being queried* from *which agent/source's records are requested*. More than one target entity is still rejected safely instead of guessed.

Fallback behavior matters as much as the successful path:

- malformed model output gets one bounded repair;
- valid unsupported output gets only the documented bounded coverage review;
- missing or ambiguous target entities stop safely and do not trigger arbitrary execution;
- unavailable sensors do not become fabricated readings;
- a configured live-provider failure is shown as an error rather than silently switching to offline answers;
- unsupported or uncertain outcomes carry no fabricated evidence IDs;
- the final response is guarded against contradicting deterministic evaluation.

### Tier 3: the mock environment

The environment is a deterministic simulation, not physical hardware. Its world includes a robot, rooms, obstacles, objects and ambient light. It exposes four sensor capabilities:

| Capability | What it computes |
|---|---|
| `lidar_scan` | The nearest active obstacle in the requested direction. The current implementation supports `front`; an obstacle must be in the same room and within a 5 cm lateral tolerance. |
| `camera_detect` | Visible objects and their IDs, apparent colors and bounding boxes. It can inspect one target or return visible objects in the robot's room. |
| `ambient_light` | The configured light intensity and color cast. |
| `robot_pose` | Robot ID, x/y coordinates in centimetres, cardinal direction and spatial context. |

Sensor output includes `SpatialContext`: location, frame of reference, observer and world version. Therefore “12 cm in front” is a robot-relative measurement in a particular room and world state, not a global coordinate and not a measurement from the user's location. Environment configuration helpers advance the world version; observations themselves do not mutate it.

## 4. Why the graph has many nodes

The graph is a model of **relationships in the seeded facility**, not a count of robots, layers, tools or issues. The graph builder creates a directed edge for each active stored fact:

```text
subject --[predicate, fact ID, source, context, confidence]--> object
```

The subject and object become nodes. The predicate, source, confidence, context and fact ID are edge data. Multiple facts can connect the same pair of nodes, so the implementation uses a `MultiDiGraph` and keys each edge by its fact ID.

The preset includes useful context for explaining and visualizing the scenario: robot and room, building and corridors, route and waypoint, sensors mounted on the robot, inventory records, boxes/shelves/lamps, and the focal map/history facts. These relationships allow the inspector and graph page to show more than the single answer to the last question.

The API acceptance test checks **17 nodes and 17 edges** for the neutral seeded baseline, and **17 nodes with at least 20 edges** after loading either Scenario A or Scenario B. This is possible because many relationships reuse the same entities: adding an edge between entities already present does not add new nodes. After Scenario A changes the active route claim from `clear` to `blocked`, the old assertion remains in SQLite's ledger/audit history but is no longer an active graph edge.

**A good short answer if asked “why so many nodes?”**

> The graph includes the facility context that makes the robot's claims explainable: rooms, routes, sensors, objects and their relationships. Each unique subject or object is a node, and each sourced active fact is an edge. Reusing the same entities creates more relationships without creating a new node every time.

## 5. Scenario A — groundedness: static map versus live LiDAR

### Assignment setup

The problem statement says the declarative layer claims the path forward is open, while the sensorimotor layer reports a front LiDAR alert at 12 cm and `blocked`. The test query is: **“Is your route clear? Justify your response by inspecting your internal system layers.”** The expected behavior is to prioritize the relevant live reading, record the belief change, update the active graph and explain the evidence.

### Current mock setup

- Robot `robot_1` is in `room_101`, at `(0, 0)` cm, facing north, using frame `robot_base`.
- SQLite begins with `route_A status_is clear`, from `static_map`, confidence `0.95`, with baseline map evidence.
- Active obstacle `obs_01` is at `(0, 12)` cm in the same room. Another obstacle and other facility entities supply test/context relationships but are not in the forward LiDAR ray.
- The UI preset suggests: “Is the front route clear to move forward?” The assignment's wording is also tested in the integration scenario suite.

### What happens

1. The provider selects `current_route_status` and supplies the route mention.
2. Tier 2 resolves it to `route_A`; its deterministic plan requires active route status from memory and a fresh front LiDAR observation.
3. SQLite returns the clear map claim. The mock LiDAR calculates that `obs_01` is directly ahead at 12 cm in the robot's room/frame.
4. The evaluator checks that claim and observation are comparable in context. The live reading conflicts with the old map claim, so the selected policy is `lidar_overrides_map`.
5. Tier 1 atomically stores a LiDAR-backed `blocked` successor, marks the old clear claim superseded and records an audit event. The write invalidates the graph cache; the rebuilt active graph shows the blocked route. The old claim is still available as history.
6. The answer explains the conflict and the current observation. The UI exposes the plan, executed operations, evidence IDs, audit event, sensor payload, active graph and ledger.

The assignment says “downgrade map confidence.” In the current implementation, the concrete recorded operation is a **supersession**: the map claim remains intact historically while the new sensor-backed claim becomes active. The system does not rewrite the historical map row's confidence in place. This distinction is useful if the examiner asks exactly what changed.

### Presentation takeaway

> The model does not answer from the old map alone. Python requires a fresh sensor check, compares it with the map in the same spatial context, records the new operational belief and leaves an audit trail explaining why.

## 6. Scenario B — perspective: user, camera and history

### Assignment setup

The user asks about a red box. Under yellow lighting, the camera reports it as brown. SQLite contains a third-party maintenance claim that the same object was painted blue by `bot_02`. The query asks what the user expects, what the robot currently sees, and what history records.

### Current mock setup

- `box_01` is intrinsically red in `room_101`.
- Ambient light has a yellow color cast. The mock camera applies a deterministic rule: a red object under yellow cast appears brown.
- SQLite stores `box_01 painted_color_is blue`, sourced from `bot_02`, with a maintenance ticket in its evidence.
- Other objects, a shelf, a lamp and facility relationships are present to make entity resolution, graph display and inspection meaningful.

### What happens

1. The provider selects `current_object_perception` for the user’s target (the suggested query names the red box).
2. Tier 2 resolves the object and plans a camera observation. The camera returns the actual `object_id`, apparent color and lighting context.
3. The evaluator extracts the expected color from the user question, records the camera's current appearance as the egocentric perspective, then uses the camera-returned object ID to query color history.
4. It reports the historical color claim with its provenance source. The `blue` record means *memory contains a sourced claim that the box was painted blue*; it is not automatically proof of the current physical color.
5. The result keeps three perspectives separate: **user expectation = red; camera = brown under yellow light; historical record = blue from `bot_02`**. React renders perspective cards plus telemetry and provenance details.

If the question omits the object name, the agent may stop at entity resolution. That is a useful safety behavior: it should ask which object or return a missing-entity fallback, not guess. If the model returns the wrong intent, the current unsupported review only recovers when its documented candidate rules find a single match; it is not a universal correction mechanism.

### Presentation takeaway

> The agent does not collapse three different claims into one “true color.” It labels who expects red, what the current sensor sees, and what a sourced historical record says.

## 7. Team contributions

The table below combines the issue assignment/ownership, merged PR authorship and local commit history. It distinguishes direct implementation credit from integration work; the project was collaborative, and merged components are used together in the final agent.

| Team member | Main ownership shown by issues/PRs | What that work contributes to the running agent |
|---|---|---|
| **Dhruv Shah** (`@dhruvkshah75`) | Shared cross-tier Pydantic contracts ([issue #1](https://github.com/dhruvkshah75/groundtruth/issues/1), [PR #5](https://github.com/dhruvkshah75/groundtruth/pull/5)); Tier 2 intent planning ([issue #4](https://github.com/dhruvkshah75/groundtruth/issues/4), [PR #7](https://github.com/dhruvkshah75/groundtruth/pull/7)); created the ReAct milestone issue ([#17](https://github.com/dhruvkshah75/groundtruth/issues/17)); authored the [React replacement](https://github.com/dhruvkshah75/groundtruth/commit/9f4876f) and follow-up integration/documentation commits. | Established the shared data contracts and the Tier 2 boundary: validated intent, bounded repair/review, safe fallbacks, entity/capability validation and deterministic plan building. Led the integration of those layers into the runnable UI/API and refined evidence-backed responses and explanation of the project. |
| **Aayush Kushwaha** (`@aayushk543`) | SQLite evidence ledger ([issue #3](https://github.com/dhruvkshah75/groundtruth/issues/3), [PR #8](https://github.com/dhruvkshah75/groundtruth/pull/8)); integrated evaluator/agent/UI work ([PR #16](https://github.com/dhruvkshah75/groundtruth/pull/16)); live LLM/ReAct provider ([assigned issue #17](https://github.com/dhruvkshah75/groundtruth/issues/17), [PR #18](https://github.com/dhruvkshah75/groundtruth/pull/18)). | Built the authoritative claim/history store and atomic revision path; contributed the evaluator and initial composed demo; implemented Groq function calling, actual tool-result round trip, provider configuration and inspectable ReAct traces. |
| **Rajat** (`@RAJAT22012006`) | Deterministic Tier 3 environment ([issue #2](https://github.com/dhruvkshah75/groundtruth/issues/2), [PR #12](https://github.com/dhruvkshah75/groundtruth/pull/12)); plan execution and composition ([issue #10](https://github.com/dhruvkshah75/groundtruth/issues/10), [PR #14](https://github.com/dhruvkshah75/groundtruth/pull/14)). | Built the mock world and its LiDAR/camera/light/pose capabilities; connected typed plans to memory and sensors through the executor/composition boundary so planned work can run and return structured evidence. |
| **Aadi-hash** (`@Aadi-hash`) | NetworkX active-belief projection and consistency checks ([issue #6](https://github.com/dhruvkshah75/groundtruth/issues/6), [PR #11](https://github.com/dhruvkshah75/groundtruth/pull/11)). | Built the lazy, rebuildable graph view over active SQLite facts, including safe defensive copies and checks for graph/database drift. A later cache-invalidation follow-up was tracked by [issue #13](https://github.com/dhruvkshah75/groundtruth/issues/13) and merged in [PR #15](https://github.com/dhruvkshah75/groundtruth/pull/15), authored by Aayush. |

### Dhruv's Tier 2 and contract contribution in more detail

The contracts and intent planner form the handoff point between a probabilistic language model and deterministic evidence operations:

1. **Shared contracts first.** Cross-tier data uses explicit Pydantic models such as `FactAssertion`, `FactQuery`, `SpatialContext`, `SensorObservation`, `IntentRequest` and `GroundedResult`. This gives Tier 1, Tier 2 and Tier 3 the same field names and validation rules.
2. **Provider output is untrusted.** The provider proposes an allowed intent and mention fields. It cannot return an answer that bypasses memory/sensors, raw SQL, or arbitrary Python operations.
3. **Intent validation is bounded.** A malformed output receives exactly one repair attempt. A valid `unsupported` result follows a distinct, bounded coverage review; a single candidate may be reconsidered once. Unknown intent strings are malformed; valid `unsupported` is not.
4. **Planning remains deterministic.** The entity resolver maps user terms to canonical IDs; the capability validator checks exact advertised sensor names and kinds; the plan builder emits typed operations. Missing, ambiguous, malformed or unsafe data leads to a structured no-plan fallback.
5. **Execution references stay explicit.** Audit operations identify which memory result supplies fact IDs; plan validation ensures those references exist and concern the same entity. This prevents a later executor from guessing which fact to audit.
6. **Recent provenance-slot improvement.** Historical lookup now distinguishes the target subject from an optional named source. For “What did maintenance_bot_7 record about the red box?”, `red box` is the entity mention and `maintenance_bot_7` is the source filter. The planner no longer treats those as two competing target entities.

These choices let the LLM handle natural language while Python owns safety, data integrity and evidence-grounded outcomes. The main code references are [`src/contracts/models.py`](../../src/contracts/models.py), [`src/procedural/intent_planner.py`](../../src/procedural/intent_planner.py), [`src/procedural/intent_coverage_reviewer.py`](../../src/procedural/intent_coverage_reviewer.py), [`src/procedural/plan_builder.py`](../../src/procedural/plan_builder.py), and [`src/procedural/execution_plan.py`](../../src/procedural/execution_plan.py).

### Aayush Kushwaha — evidence ledger, evaluator and live provider

- **SQLite evidence ledger (GT-03).** `MemoryRepository` stores sourced assertions, aliases and audit events. Facts carry confidence, observed/created time, context and evidence. Revisions use a transaction so either the successor fact, predecessor link and audit record all succeed, or the database rolls back. Keeping old facts preserves provenance and makes “what did the map say earlier?” answerable. This work is recorded in [issue #3](https://github.com/dhruvkshah75/groundtruth/issues/3) and [PR #8](https://github.com/dhruvkshah75/groundtruth/pull/8).
- **Epistemic evaluation and initial composition.** The evaluator compares returned facts and observations for the core scenarios, performs Scenario A revisions, and builds the three separate Scenario B perspectives. The composed agent and initial dashboard work are described in [PR #16](https://github.com/dhruvkshah75/groundtruth/pull/16). This is the bridge from low-level evidence to a user-facing epistemic result.
- **Groq function-calling/ReAct integration (GT-07).** The provider uses an injectable client and strict approved tool schemas, returns raw structured proposals to the planner, sends actual execution results back in a second model turn, and exposes safe operation traces. The provider mode is explicit: live mode reports missing credentials/failures rather than silently claiming an offline rule result came from an LLM. The work is in [issue #17](https://github.com/dhruvkshah75/groundtruth/issues/17) and [PR #18](https://github.com/dhruvkshah75/groundtruth/pull/18).
- **Why these choices matter.** SQLite is a small local durable ledger with transactions and exact queries; an injected provider client allows offline CI tests; and bounded function calling lets the model participate without handing it raw database or sensor access.

### Rajat — mock sensorimotor world and execution composition

- **Deterministic Tier 3 (GT-02).** `WorldState` describes robot pose, room, obstacles, objects, lighting and version. `MockEnvironment` exposes LiDAR, camera, ambient-light and pose capabilities. LiDAR checks geometry relative to the robot's direction, same room and a 5 cm lateral tolerance; camera reports object IDs, apparent colors and bounding boxes; observation context preserves room/frame/world version. The work is recorded in [issue #2](https://github.com/dhruvkshah75/groundtruth/issues/2) and [PR #12](https://github.com/dhruvkshah75/groundtruth/pull/12).
- **Plan executor and composition (GT-06).** The executor runs typed memory, sensor and audit operations and returns results associated with their plan operation indexes. `CompositionService` wires the planner, builder, Tier 1 repository and Tier 3 environment through their boundaries. This means the plan is both inspectable before execution and traceable after execution. The work is in [issue #10](https://github.com/dhruvkshah75/groundtruth/issues/10) and [PR #14](https://github.com/dhruvkshah75/groundtruth/pull/14).
- **Why these choices matter.** A deterministic mock makes Scenario A/B repeatable without hardware or sensor randomness. Typed requests/results make it clear what a tool was allowed to do and which result belongs to which operation.

### Aadi-hash — active-belief graph projection

- **NetworkX projection (GT-05).** `ActiveBeliefGraph` reads active facts through a Tier 1 interface and builds a directed `MultiDiGraph`. Each stored fact becomes an edge keyed by its fact ID and carries provenance/context metadata. A `MultiDiGraph` preserves separate claims when facts share endpoints or multiple sources disagree. The implementation is recorded in [issue #6](https://github.com/dhruvkshah75/groundtruth/issues/6) and [PR #11](https://github.com/dhruvkshah75/groundtruth/pull/11).
- **Consistency and mutation safety.** The graph is loaded lazily, can be discarded and rebuilt, returns defensive copies, and has consistency checks against SQLite. The graph is a projection instead of a second authoritative database, avoiding two independent stores that could disagree about the active belief.
- **Follow-up ownership.** Automatic invalidation through the application write boundary was tracked later by [issue #13](https://github.com/dhruvkshah75/groundtruth/issues/13) and implemented in [PR #15](https://github.com/dhruvkshah75/groundtruth/pull/15), authored by Aayush. It completes the connection around the original graph work; it should not be misattributed to Aadi.

## 8. Milestones and current status

The assignment PDF names four project milestones. The first two were the foundation highlighted for the mid-semester presentation. The current repository has also implemented the two scenario milestones. Milestone 4's software behavior is present and tested; the team's final course presentation and grading demo are still ahead.

| Assignment milestone | PDF deliverable | Current status on `main` |
|---|---|---|
| **1. Layer Initialization & Data Contracts** | SQLite/NetworkX schema, mock environment primitives, clear JSON contracts across layers. | **Implemented.** Shared Pydantic contracts, SQLite ledger, NetworkX projection, consistency/cache integration and Tier 3 deterministic sensors are in the repository; see issues #1–#6 and merged PRs #5, #8, #11 and #12. |
| **2. ReAct Tool Integration** | LLM function calling; agent reads state, selects a tool and receives tool results back in its context. | **Implemented.** Groq function calling is wired through the agent and React UI, with bounded plans, returned evidence, guarded answer synthesis and offline tests; see [issue #17](https://github.com/dhruvkshah75/groundtruth/issues/17) and [PR #18](https://github.com/dhruvkshah75/groundtruth/pull/18). |
| **3. Groundedness Validation Suite** | Execute Scenario A and automatically update the internal belief network on a sensor conflict. | **Implemented and tested.** Scenario A checks map versus LiDAR, records a revision/audit event and updates the active graph. See [`tests/integration/test_epistemic_scenarios.py`](../../tests/integration/test_epistemic_scenarios.py) and [`tests/integration/test_react_flow.py`](../../tests/integration/test_react_flow.py). |
| **4. Perspective & Final Demo** | Execute Scenario B and report the multi-perspective result cleanly. | **Implemented and tested.** The evaluator keeps user, camera and historical claims separate; React displays them as separate cards. See the same scenario and ReAct integration tests. |

The epistemic scenario file contains **10 scenario-level integration tests**: route conflict, route agreement, unavailable LiDAR, full perspective tracking, neutral lighting, competing sources, ambiguous entity, unknown entity, audit explanation, and a multi-turn cycle. The web API and ReAct suites test the complete app boundary and model/tool loop. The CI workflow checks backend tests/format/lint and the frontend TypeScript production build.

There were no open GitHub issues or pull requests when this guide was prepared on 3 October 2026. Therefore, there is **no uncompleted milestone explicitly listed in the PDF** on the current branch. The assignment also asks for 10 automated test logs; the repository has the 10 automated scenario tests, and the team should save a clean `pytest` run output if the instructor expects a separate log artifact.

## 9. Follow-on work after the mid-semester checkpoint

These are improvement directions, not missing milestones from the assignment's four-item list:

- **Broaden evaluation coverage.** Add test prompts for diverse natural phrasing, missing/multiple entity mentions, source attribution, mixed-intent questions, and unsupported false negatives. Keep safe behavior when a request requires several targets or operations that the current contracts do not express.
- **Expand action support only with safety contracts.** `environment_action` currently stops safely. A later version could define named actions, preconditions, authorization and post-action observation before enabling simulated movement or manipulation.
- **Increase mock-world realism deliberately.** Add configurable sensor noise, occlusion, more rooms and changing world state while preserving deterministic seeds for automated tests. Current color behavior is a simple rule, not a computer-vision model.
- **Decide on persistence and deployment.** The browser demo currently creates session-scoped in-memory SQLite and mock state; restarting the API process clears sessions. Persistent storage, authentication, deployment and multi-user retention are future system decisions.
- **Keep final demonstration evidence reproducible.** Record test output, provider/model configuration without secrets, scenario setup, question, returned evidence IDs, and the UI result so the final grading run can be repeated.
- **Potential stretch ideas.** The masterplan discusses confidence decay, sensor perturbations and graph time travel. These remain optional ideas, not current features or required assignment milestones.

The [evaluation and differentiation plan](../Evaluation_and_Differentiation.md) and [potential issues and alternatives](../Potential%20Issues%20vs%20Alternatives.md) contain additional design considerations. Any expansion should preserve the central rule: missing evidence stays missing; the model does not fill that gap with a plausible story.

## 10. Presentation terms and likely questions

| Term / question | Clear answer |
|---|---|
| **What is an intent?** | A small category for the user's request, such as current route status. It is not the answer and is not an executable operation list. |
| **What is an entity?** | The subject being discussed, such as the red box. A user phrase is resolved to a stable canonical ID before planning. |
| **What is provenance?** | Where a claim came from, such as `static_map`, `lidar_sensor` or `bot_02`. It is recorded with the fact rather than inferred later. |
| **What does 12 cm mean?** | The mock LiDAR's nearest forward obstacle distance from the robot in centimetres, scoped to `room_101`, its pose/frame and world version. |
| **Why use both SQLite and NetworkX?** | SQLite preserves durable claims and history; NetworkX offers a convenient active relationship view. The graph can be rebuilt and never replaces the ledger. |
| **Why are there so many graph nodes?** | The preset includes facility and sensor context so relationships are visible. Each distinct subject/object is a node; each active fact is an edge. The baseline and each scenario use 17 unique graph nodes in the API tests. |
| **Does the model decide the true answer?** | No. It proposes the task and helps word the result. Python validates, gathers evidence, applies policy, records revisions and guards final text. |
| **Is this a physical robot?** | No. Tier 3 is a deterministic mock environment. It demonstrates the software contracts and evidence flow without hardware. |
| **What if there is no data?** | The agent reports uncertainty, missing entity/evidence or an unsupported task. It does not silently make up an answer. |
| **Is Scenario B saying blue is definitely the true color?** | No. Blue is the latest sourced historical claim. Brown is the current camera appearance under yellow light, and red is the user's stated expectation. They answer different questions. |
| **Is every wording supported?** | No. The LLM generalizes across phrasing within the fixed intents, but a novel task, missing target or multi-target request can still stop safely. The coverage reviewer is bounded, not an unrestricted fallback model. |

## 11. Source files and further reading

- Assignment: [`problemStatement.pdf`](../../problemStatement.pdf)
- Current flow and Scenario A/B: [`Implemented_System_Overview.md`](../Implemented_System_Overview.md)
- Architectural choices: [`Architecture_Decisions.md`](../Architecture_Decisions.md)
- Contract definitions: [`src/contracts/models.py`](../../src/contracts/models.py)
- Tier 1 SQLite and graph: [`memory_repository.py`](../../src/declarative/memory_repository.py), [`active_graph.py`](../../src/declarative/active_graph.py), [`consistency.py`](../../src/declarative/consistency.py)
- Tier 2 planning and evaluation: [`intent_planner.py`](../../src/procedural/intent_planner.py), [`plan_builder.py`](../../src/procedural/plan_builder.py), [`plan_executor.py`](../../src/procedural/plan_executor.py), [`epistemic_evaluator.py`](../../src/procedural/epistemic_evaluator.py)
- Tier 3 world and sensors: [`world_models.py`](../../src/sensorimotor/world_models.py), [`mock_environment.py`](../../src/sensorimotor/mock_environment.py)
- Agent composition and provider: [`agent.py`](../../src/agent.py), [`composition.py`](../../src/composition.py), [`groq_provider.py`](../../src/providers/groq_provider.py)
- API and browser app: [`server.py`](../../src/web/server.py), [`service.py`](../../src/web/service.py), [`App.tsx`](../../frontend/src/App.tsx), [`BeliefGraphPage.tsx`](../../frontend/src/BeliefGraphPage.tsx)
- Scenario tests: [`test_epistemic_scenarios.py`](../../tests/integration/test_epistemic_scenarios.py), [`test_react_flow.py`](../../tests/integration/test_react_flow.py), [`test_web_api.py`](../../tests/integration/test_web_api.py)
- Run instructions and CI: [`Local_Development_Setup.md`](../Local_Development_Setup.md), [CI workflow](../../.github/workflows/ci.yml)
- Architecture background: [`I_Agent_Masterplan.md`](../I_Agent_Masterplan.md)
