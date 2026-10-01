# GroundTruth: Current Agent and Scenario Guide

This document describes the code that is currently on `main`. It is a plain-language guide for teammates who need to run, explain, or present the project without reading every source file.

> **Current status:** the browser UI and the Python agent are connected, and the composed deterministic agent runs both demonstration scenarios against real Tier 1 and Tier 3 components. The current provider is `RuleBasedIntentProvider`. There is **no live LLM or LLM function-calling/ReAct loop in the app yet**. That is Project Milestone 2 and is tracked by [GT-07, issue #17](https://github.com/dhruvkshah75/groundtruth/issues/17).

## 1. What GroundTruth is for

GroundTruth demonstrates how an agent should answer questions about a robot and its environment using evidence from different sources. It keeps these responsibilities separate:

- what the system has recorded before;
- what the mock robot senses now;
- how the system compares those claims and decides what it can safely report.

For example, a static map may say a route is clear while a fresh LiDAR observation detects an obstacle. The system keeps the source and history of both claims, applies a deterministic policy to the conflict, and records a revision. For an object, the user may expect red, the camera may currently see brown under yellow light, and a maintenance record may say the object was painted blue. These are presented as separate perspectives.

## 2. Current implementation at a glance

| Area | What currently runs |
| --- | --- |
| Browser UI | React + TypeScript + Vite in `frontend/`; chat, scenario presets, reset, and a system inspector. |
| Local API | Python standard-library HTTP server in `src/web/server.py`. It serves the built UI and JSON endpoints. |
| Agent | `GroundedAgent` in `src/agent.py` composes Tier 1 memory/graph, Tier 2 planning/execution/evaluation, and Tier 3 mock sensors. |
| Intent provider | `RuleBasedIntentProvider`: local keyword/rule logic, not an LLM. |
| Memory | SQLite `MemoryRepository`; the web demo creates an in-memory database for each browser session. |
| Active graph | NetworkX graph derived from active SQLite facts; it can be rebuilt from the repository. |
| Environment | Deterministic `MockEnvironment` with LiDAR, camera, ambient-light, and pose capabilities. |
| Evidence decision | Deterministic Python in `EpistemicEvaluator`; it selects supported conclusions and records justified revisions. |
| LLM/ReAct | Not implemented in the current app. See [GT-07](https://github.com/dhruvkshah75/groundtruth/issues/17). |

The Streamlit dashboard has been removed. The React app is the UI to use and maintain.

## 3. The three layers in simple terms

### Tier 1 — Declarative memory: recorded claims and their history

Tier 1 stores facts in SQLite. A fact is a claim with a subject, relationship, value, source, confidence, observation time, and context.

Example:

```text
subject: route_A
predicate: status_is
object: clear
source_agent: static_map
confidence_score: 0.95
context: room_101, robot_base
```

The fact says what the static map claimed. It does not prove that the route is clear now. SQLite keeps older facts when a later fact replaces them. A revision marks the old fact as superseded, stores the replacement, and records an audit event explaining why.

The active NetworkX graph is a projection of active facts for relationship views. It is not a second source of truth. SQLite remains authoritative, and the graph can be rebuilt from SQLite.

In the browser app, each session uses SQLite `:memory:`. This is real SQLite behavior, but the data exists only while that server process/session is alive; it is not persistent storage across server restarts.

### Tier 2 — Procedural layer: decide what evidence is needed and compare it

Tier 2 connects the user request to memory and sensors. The current process is deterministic:

1. `RuleBasedIntentProvider` classifies the wording into an allowed intent.
2. `IntentPlanner` validates the provider's raw response against the shared `IntentRequest` contract. It preserves the user's original question and has bounded repair/reconsideration behavior.
3. Entity resolution maps user wording such as “front route” or “red box” to a canonical ID such as `route_A` or `box_01`. Ambiguous or missing entities stop safely; the agent does not choose a random match.
4. `CapabilityValidator` checks that the requested sensor exists and is registered with the right kind.
5. `PlanBuilder` turns the validated intent into required typed memory and observation operations.
6. `CompositionService` and `PlanExecutor` execute those operations through repository and environment interfaces.
7. `EpistemicEvaluator` compares the actual returned facts and observations. It creates the final status, answer, perspectives, and, where justified, a Tier 1 revision/audit event.
8. `GroundedAgent` returns the result and current graph/facts to the web service.

The rule-based provider is a small deterministic classifier. It checks phrases such as “route”, “clear”, “color”, “history”, and “why”. It is useful for repeatable tests and the current local demo, but it does not understand arbitrary wording like a language model and does not perform LLM reasoning.

### Tier 3 — Sensorimotor environment: a deterministic mock world

Tier 3 owns the simulated robot, room, obstacles, objects, and light. It computes observations from that world. It does not read the user's question, query SQLite, or decide which source is true.

Currently advertised sensor capabilities are:

- `lidar_scan` — measures a front obstacle or reports a clear scan;
- `camera_detect` — returns visible object IDs and apparent color;
- `ambient_light` — returns light intensity and color cast;
- `robot_pose` — returns robot position and direction.

The mock camera has a documented deterministic rule: a configured red object under yellow light appears brown. This is a testable simulation rule, not a physical color-science model. Test/configuration helpers can replace the world, set the pose, or configure objects and obstacles; these helpers are not public robot action tools.

## 4. Terms used in the code and UI

| Term | Plain meaning | Example |
| --- | --- | --- |
| **Provider** | The component that interprets or structures a user's question. | Today: `RuleBasedIntentProvider`; planned in GT-07: a real LLM adapter. |
| **LLM/model** | A language model that can interpret flexible wording. | No LLM is connected in the current app. |
| **ReAct** | A bounded reason, act, observe cycle: request an allowed operation, receive its result, then respond. | Planned milestone: model selects a constrained intent; Python executes required operations and sends real results back. |
| **Intent** | The category of the user's request; not the answer and not a Python function call. | `current_route_status` means “ask about a route now”. |
| **Entity** | The thing being discussed. | `route_A` or `box_01`. |
| **Alias / canonical ID** | A user-friendly phrase / the stable internal name it resolves to. | “front route” → `route_A`. |
| **Capability** | A sensor or action the environment says it supports. | `lidar_scan` is a sensor capability. |
| **Plan operation** | A typed evidence request created by deterministic Python. | Query active route facts; request a fresh LiDAR observation. |
| **Evidence ID** | A stable ID for one stored fact or observation. | Used to trace which memory claim or sensor reading supports a result. |
| **Source / provenance** | Who supplied a claim and what record supports it. | `bot_02`, ticket `MAINT-4091`. |
| **Observation** | A sensor result from one particular world state. | LiDAR saw an obstacle 12 cm ahead in `room_101`. |
| **Spatial context** | Where, when, and from whose point of view a fact or reading applies. | `room_101`, observer `robot_1`, frame `robot_base`, world version. |
| **Frame of reference** | The coordinate viewpoint used for a measurement. | `robot_base` means “in coordinates relative to the robot”. |
| **Belief revision** | A new claim becomes active and supersedes an older claim. | Current LiDAR evidence replaces the active “route is clear” belief. |
| **Audit event** | A record of why a revision happened and which rule was applied. | `lidar_overrides_map`. |
| **Graph projection** | A rebuildable NetworkX view of active SQLite facts. | An active edge `route_A —status_is→ blocked`. |

For a fact such as `route_A status_is blocked`, `route_A` is the **entity**, `status_is` is the **relationship**, and `blocked` is the **value**. “12 cm” alone has no useful meaning unless it is attached to a sensor, robot, location, time/world version, and frame of reference.

## 5. Current request flow from the browser

```text
User types a question or loads a preset
  -> React calls the local Python HTTP API
  -> X-Session-ID selects that browser's AgentSession
  -> AgentSession calls GroundedAgent.ask(question)
  -> rule-based provider proposes an allowed intent
  -> deterministic planner validates and builds the evidence plan
  -> executor queries SQLite and/or calls MockEnvironment.observe(...)
  -> EpistemicEvaluator applies the evidence policy and may revise memory
  -> API returns answer, status, facts, graph, sensor telemetry, revisions, and environment
  -> React displays those returned values in chat and the system inspector
```

There is currently **no call to a model API anywhere in this path**. The answer comes from deterministic Python templates and evaluation rules. The frontend does not invent replacement facts when the API fails; it displays the API error.

### Frontend/API routes

| Route | Purpose |
| --- | --- |
| `GET /api/health` | Reports that the API is running and names the provider actually wired in (`RuleBasedIntentProvider`, deterministic local demo). This does not mean an LLM is available. |
| `GET /api/state` | Returns the current session's chat history, graph, ledger, audit events, environment, and capabilities. |
| `POST /api/ask` | Runs one user question through the current deterministic agent. |
| `POST /api/scenarios/scenario-a` | Resets and seeds Scenario A in the backend session. |
| `POST /api/scenarios/scenario-b` | Resets and seeds Scenario B in the backend session. |
| `POST /api/reset` | Clears that session's agent, in-memory ledger, and chat history. |

The browser keeps a session UUID in `sessionStorage` and sends it using `X-Session-ID`. The server maintains separate in-memory agent/SQLite/environment state for each session, with a local demo limit of 128 sessions. Restarting the Python server loses these ephemeral sessions. Scenario buttons call backend endpoints; the scenarios are seeded server-side, not painted into the UI as fake answers.

## 6. Scenario A — static map versus live LiDAR

### What is set up

When **Load Scenario A** is pressed, the Python service resets that browser session and seeds the real in-memory backend components:

- `robot_1` is at `(0 cm, 0 cm)` in `room_101`, facing north, with frame `robot_base`.
- A static-map fact says `route_A status_is clear`, source `static_map`, confidence `0.95`, map version `v1.0`, and evidence source `building_blueprints`.
- The mock world contains active obstacle `obs_01` at `(0 cm, 12 cm)` in the same room.
- The UI places a suggested question in the composer: “Is the front route clear to move forward?” The assignment's test question is: “Is your route clear? Justify your response by inspecting your internal system layers.”

These are controlled backend fixtures. The LiDAR reading itself is computed from the robot pose and obstacle coordinates; it is not a string hardcoded into the browser response.

### What happens when asked

1. The current rule-based provider recognizes route/clear/forward wording and proposes `current_route_status` for the route.
2. The planner validates the intent. The resolver maps the route mention to `route_A`.
3. The deterministic plan requests active route-status memory and a fresh `lidar_scan` for the front direction.
4. SQLite returns the static-map claim. Tier 3 calculates an obstacle directly ahead and returns `blocked` at `12.0 cm`, along with an observation ID and spatial context.
5. `EpistemicEvaluator` verifies that the stored claim and observation apply to the same location and frame. Since the static-map claim says clear and the fresh sensor says blocked, the evaluator applies `lidar_overrides_map`.
6. Tier 1 records the LiDAR-backed blocked claim, marks the old clear claim superseded, and writes an audit event. The graph projection refreshes from active facts and shows the active route as blocked.
7. The deterministic evaluator returns an answer explaining the conflict and revision. React displays the answer, status, audit/revision details, sensor telemetry, ledger, graph, and environment values.

The expected answer is equivalent to:

> No, my static mapping says it is clear, but my live LiDAR readings indicate a physical obstruction at 12.0 cm right now. I have downgraded my map confidence and updated my belief graph.

This wording is currently generated by deterministic Python, not by an LLM. The “updated my belief graph” claim is supported by the repository revision and derived graph state; it should not be reported if the write did not happen.

### What the 12 cm means

The measurement is the forward distance from `robot_1`'s sensor origin to `obs_01` in the simulated `room_101`, in centimetres. The robot faces north; `robot_base` identifies the robot-relative frame. If the robot were in another room, facing another direction, or at another position, the result could differ. The number is not a global coordinate or a measurement from the user's position.

## 7. Scenario B — user, camera, and history

### What is set up

When **Load Scenario B** is pressed, the service resets the session and seeds:

- `robot_1` in `room_101` at `(0 cm, 0 cm)`, facing north.
- Visible object `box_01`, intrinsic/configured color red, at `(10 cm, 10 cm)`.
- Yellow ambient light with intensity `0.8`.
- A Tier 1 fact for `box_01`: `painted_color_is blue`, sourced from `bot_02`, confidence `1.0`, with evidence `action=painted_blue` and ticket `MAINT-4091`.
- A suggested question in the composer. The assignment asks: “What color does the user think the object is, what color do you register it as, and what does your data history say its true state is?”

The red object and yellow lighting are backend environment configuration. The camera computes the apparent color `brown` from that configuration. The blue value is a separate historical assertion from SQLite; it is not supplied by the camera.

### What happens when asked

1. The rule-based provider recognizes the color/perspective wording and proposes `current_object_perception` for the box.
2. The deterministic plan requests a camera observation for the resolved object.
3. The camera returns the actual observed object ID (`box_01`), apparent color (`brown`), observation ID, and lighting in observation context.
4. The evaluator extracts the expected color from the user's wording and queries historical color claims for the **object ID returned by the camera**. It does not guess the history subject from an unrelated text match.
5. The evaluator keeps three perspectives separate and returns them to the UI:
   - **User expectation:** red, because that is the target color stated in the question.
   - **Current egocentric camera view:** brown under yellow lighting.
   - **Historical/third-party record:** the latest stored color claim says blue, from `bot_02`.
6. React shows the distinct perspective cards, sensor telemetry, provenance/ledger facts, and environment lighting/object state.

The historical blue statement means **“the database contains a sourced record saying blue”**. It does not prove the object is physically blue now. If there is no historical color fact, the agent reports that no record was found; it must not invent blue or `bot_02`.

As with Scenario A, the current final wording and perspective extraction are deterministic Python; no LLM makes the response today.

## 8. What the UI shows

- **Left panel:** API connection/provider details, Scenario A and B presets, reset, and the three layer names.
- **Center:** conversation, returned answer/status, perspective cards, revision summary, and question composer.
- **Right system inspector:** Tier 2 result/plan reason/verification, audit events and sensor telemetry; perspectives; Tier 1 active graph and full SQLite ledger; Tier 3 world version, pose, light, obstacles, objects, and advertised sensors.
- **Layout controls:** the left panel can be hidden/shown; the right inspector can be hidden/shown and resized on wide screens. The UI is responsive on smaller screens.

The inspector reports facts returned by the backend. “No observation”, “no history”, and “no executable plan” are explicit empty states rather than substitutes filled with example values.

## 9. What is implemented and what is next

### Implemented on current `main`

- Shared typed contracts for intents, facts, observations, spatial context, and grounded results.
- SQLite fact storage, entity aliases/resolution, history, atomic revisions, and audit trails.
- NetworkX active-fact projection and cache consistency support.
- Tier 2 intent validation, one bounded malformed-response repair, capability checks, deterministic plan creation, plan execution, unsupported-intent review, and safe outcomes.
- Deterministic mock LiDAR, camera, ambient light, and pose behavior.
- `GroundedAgent` composition, deterministic evidence evaluation, Scenario A revision, and Scenario B perspective separation.
- React/TypeScript UI connected to a local session-scoped Python API; API and frontend integration tests.
- No Streamlit UI or Streamlit runtime dependency.

### Not implemented yet

- A real Groq/Llama/other LLM adapter in the application.
- LLM structured/function calling, a model-to-tool-result ReAct loop, and model-generated grounded response wording.
- A public robot movement/action execution API, physical hardware integration, learned vision, or persistent multi-user session storage.

The next planned task is [GT-07, issue #17](https://github.com/dhruvkshah75/groundtruth/issues/17), which covers the real LLM ReAct loop, React provider/status/trace updates, and refreshed current-system documentation. Until it is implemented and tested, present the existing app as a **deterministic end-to-end prototype of the layer integration and Scenario A/B evidence flow**, not as completion of the LLM/ReAct milestone.

## 10. Where to read the code and run it

| Concern | Main files |
| --- | --- |
| Shared data shapes | `src/contracts/models.py` |
| Tier 1 repository and graph | `src/declarative/memory_repository.py`, `active_graph.py`, `consistency.py` |
| Tier 2 plan and evaluation | `src/procedural/intent_planner.py`, `plan_builder.py`, `plan_executor.py`, `epistemic_evaluator.py` |
| Tier 3 world and sensors | `src/sensorimotor/world_models.py`, `mock_environment.py` |
| Agent composition | `src/composition.py`, `src/agent.py` |
| Web service/API | `src/web/service.py`, `src/web/server.py` |
| React UI | `frontend/src/App.tsx`, `api.ts`, `types.ts`, `app.css` |
| Scenario/API tests | `tests/integration/test_epistemic_scenarios.py`, `tests/integration/test_web_api.py`, `tests/unit/web/test_service.py` |

Follow [Local Development Setup](Local_Development_Setup.md) to run the app. Automated tests use local deterministic components and do not need an LLM API key or internet connection.
