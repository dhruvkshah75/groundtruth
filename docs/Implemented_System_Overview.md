# GroundTruth: implemented system overview

This page explains the parts of GroundTruth that are implemented and merged, how they fit together, and what is still waiting for integration. It is written for a teammate who knows the project is a robot agent but has not read every issue or source file.

## The project in one paragraph

GroundTruth is designed to answer questions about a robot and its environment using traceable evidence. A stored statement is treated as a claim from a source, not automatically as truth. The system separates durable memory, planning, and physical-world observations so that an old record cannot silently stand in for a fresh sensor reading. Its goal is to be able to say what evidence supports an answer, when it was observed, and why a belief changed.

## What is merged

| Work | What it provides | GitHub status |
| --- | --- | --- |
| Shared contracts | Typed messages shared across tiers: facts, context, entity resolution, intents, observations, and grounded results. | [PR #5](https://github.com/dhruvkshah75/groundtruth/pull/5), [issue #1](https://github.com/dhruvkshah75/groundtruth/issues/1) complete |
| Tier 1 SQLite ledger (GT-03) | Durable fact storage, exact queries, revisions, audit trails, and alias resolution. | [PR #8](https://github.com/dhruvkshah75/groundtruth/pull/8), [issue #3](https://github.com/dhruvkshah75/groundtruth/issues/3) complete |
| Tier 1 graph projection (GT-05) | Disposable NetworkX view of active facts, with cache controls and consistency diagnostics. | [PR #11](https://github.com/dhruvkshah75/groundtruth/pull/11), [issue #6](https://github.com/dhruvkshah75/groundtruth/issues/6) complete |
| Tier 2 planning (GT-04) | Validates a proposed intent, resolves entities, makes a fixed evidence plan, and returns safe fallbacks. | [PR #7](https://github.com/dhruvkshah75/groundtruth/pull/7), [issue #4](https://github.com/dhruvkshah75/groundtruth/issues/4) complete |

The current source tree does **not** yet contain the Tier 3 mock environment described by [issue #2](https://github.com/dhruvkshah75/groundtruth/issues/2). That issue is still open. Likewise, the merged planning code creates plans but does not yet connect those plans to an executor, a real sensor environment, a final answer renderer, or a live LLM provider.

## A few terms

- **Tier 1 — declarative memory:** the evidence ledger. It stores what was asserted, by whom, when, and in what context.
- **Tier 2 — procedural planning:** turns a user question into a validated intent and a typed list of required evidence operations.
- **Tier 3 — sensorimotor environment:** will own the simulated robot world and compute observations. The GT-02 implementation is not in the current source tree yet.
- **Contract:** a validated data shape shared across components. For example, `FactAssertion` describes a claim before it is stored, while `StoredFact` adds its durable ID and revision status.
- **Entity:** the thing a question or fact concerns, identified internally by a stable canonical ID such as `route_ahead` or `box_01`. A user may refer to it by an alias such as “front route.”
- **Intent:** the kind of request, not the answer and not a tool call. Examples include `current_route_status` and `historical_fact_lookup`.
- **ExecutionPlan:** typed instructions saying which evidence to request. In this implementation it is data only; it does not itself execute database queries or sensors.
- **Evidence ID:** the ID of a stored fact or observation that supports a later answer. A plan or fallback must not invent one.
- **Context:** the scope in which a fact or observation applies. It can include location, observer, world version, and frame of reference. For example, “12 cm ahead” only has meaning relative to a robot, its pose, and a coordinate frame.
- **Revision/audit event:** a record that a new claim replaced an older active claim, including why and under which policy rule. The older claim stays in history.

One useful distinction is **entity versus value**. In a fact such as `route_ahead status_is blocked`, `route_ahead` is the entity, `status_is` is the relationship, and `blocked` is the value. The entity is the thing being discussed; the value is what a source claimed about it.

## How the implemented pieces fit

```text
Shared contracts
      ↑                       ↑
      │                       │
Tier 1: SQLite ledger     Tier 2: intent and plan
      │                       │
      └── NetworkX cache      └── future connection to Tier 3
```

The arrows describe shared data formats and planned handoffs, not a complete running end-to-end agent. The merged code supplies the contract, memory, graph, and planning components. Application wiring and execution are still future work.

### Shared contracts

The Pydantic models in `src/contracts/models.py` are the common vocabulary. They validate required fields and reject malformed data at component boundaries. A `SpatialContext`, for example, can say that a reading belongs to `room_101`, was made by `robot_01`, used `robot_base` as its frame of reference, and came from `world_version=4`.

The contract layer does not store facts, read sensors, call an LLM, or decide which source is correct.

For example, the shared `IntentRequest` is intentionally small: it contains an allowed `intent`, zero or more user-facing `entity_mentions`, and the original `user_question`. It does not contain SQL, a Python method name, or an arbitrary list of tools. This keeps the provider's output easy to validate and limits what it can ask the rest of the program to do.

### Tier 1: SQLite stores claims and history

`MemoryRepository` in `src/declarative/memory_repository.py` is the durable evidence store. Its main operations are:

- `record_fact(assertion)` stores a new claim and returns a `StoredFact`.
- `query_facts(query)` returns exact matches as typed facts; it can retrieve active or historical facts.
- `record_revision(...)` atomically stores a replacement, marks the previous fact as superseded, and records an audit event.
- `get_audit_chain(fact_id)` returns the facts and events needed to trace a belief’s history.
- `add_alias(...)` and `resolve_entity(...)` map user-facing names to canonical IDs or report ambiguity/missing entities.

A stored fact keeps several different kinds of information together:

| Field | What it tells us |
| --- | --- |
| `subject`, `predicate`, `object` | The claim itself, such as `route_ahead status_is blocked`. |
| `source_agent` | Who or what supplied the claim, such as a map, user, or sensor. |
| `confidence_score` | The source's confidence value; it is not, by itself, a truth decision. |
| `observed_at` | When the source says it observed the claim. |
| `created_at` | When the repository stored the claim. This can differ from `observed_at`. |
| `context` | The place, observer, world version, frame of reference, and optional extra scope for which it applies. |
| `evidence` | Supporting source data retained with the claim. |
| `superseded_by` | Empty for an active claim; otherwise points to its replacement fact. |

This distinction helps answer questions such as “When did the sensor see this?” (`observed_at`) versus “When did the agent learn it?” (`created_at`). Historical facts can also be queried without presenting them as the current state.

Example: if a map says a route is clear and a later LiDAR-derived claim says it is blocked, both claims remain in SQLite. A revision links the old claim to the new one and records the reason. The old claim is no longer active, but it is still available for historical questions and audit explanations. Tier 1 preserves evidence; it does not itself decide whether LiDAR should win.

Here is the same history in simplified form:

| Fact ID | Claim | Source | Active status |
| --- | --- | --- | --- |
| `fact_map_01` | `route_ahead status_is clear` | `static_map` | Superseded by `fact_lidar_02` |
| `fact_lidar_02` | `route_ahead status_is blocked` | `lidar_sensor` | Active |

The IDs above are explanatory labels, not literal UUIDs from the database. A historical query can return both rows; an active query returns the second one. The revision audit can explain why the old claim stopped being active.

### Tier 1: NetworkX projects active facts

`ActiveBeliefGraph` in `src/declarative/active_graph.py` builds an in-memory `MultiDiGraph` from active `StoredFact` records. Each fact becomes a directed edge from its subject to its object, keyed by that fact’s ID and carrying source, time, confidence, context, and other metadata.

It is a **rebuildable cache**, not a second database. Multiple active claims between the same entities are kept as separate edges. `get_graph()` and `rebuild()` return deep copies so a caller’s edits do not alter the internal cache. `check_graph_consistency` in `src/declarative/consistency.py` compares the graph with active and, when checking stale edges, superseded facts supplied by the caller.

For the example above, the active graph contains an edge for `fact_lidar_02` from `route_ahead` to `blocked`. It does not contain the old `fact_map_01` edge because that fact is superseded. SQLite still has both rows. If two unsuperseded sources make different claims, the graph keeps both edges; it does not choose a winner or erase a disagreement.

Known integration follow-up: cache invalidation is automatic only when writes go through `GraphSyncedRepository`. A direct write to the still-public `MemoryRepository` can bypass it. The team recorded this in [issue #13](https://github.com/dhruvkshah75/groundtruth/issues/13) for the application-connection work. That issue also tracks a documentation correction for the consistency-check call and formatting cleanup from PR #11.

### Tier 2: a provider proposes; Python validates and plans

`IntentProvider` is a small interface. A future real LLM adapter could implement it, while tests use a fake provider with prepared responses. The provider returns raw, untrusted data. It does not receive permission to query memory, call sensors, or execute actions.

`IntentPlanner` checks the provider’s response against the shared `IntentRequest` contract. It preserves the original user question, asks for at most one repair if the response is malformed, and returns a structured safe fallback if the repaired response is still invalid. A provider outage is represented separately from bad structured output.

The allowed intent names are:

| Intent | Meaning in the current planner |
| --- | --- |
| `current_route_status` | Ask about the present status of one route/entity. The plan includes active status memory and a LiDAR request if that capability is advertised. |
| `current_object_perception` | Ask about an object’s current appearance. The plan requests camera observation when available. |
| `historical_fact_lookup` | Ask what was recorded about an entity over time. The plan queries history and requests no sensor. |
| `audit_explanation` | Ask why a belief changed. The plan gets matching fact IDs from memory, then requests their audit chains. |
| `current_robot_pose` | Ask for the robot’s present pose. The plan requests the `robot_pose` observation. |
| `environment_action` | Ask the robot to do something. It currently stops safely because the plan contract has no approved action name/safety execution flow. |
| `unsupported` | The provider says the request does not fit the available supported categories. It enters the bounded review described below. |

The allowed list is a contract-level limit; an arbitrary provider label such as `read_weather` is invalid data. It gets the one repair attempt. A valid `unsupported` value is different: it is valid structured data, so it proceeds to the unsupported-request review.

`EntityResolver` is injected into `PlanBuilder`. It resolves a mention to one canonical ID, returns a clarification fallback for multiple candidates, or returns a safe no-plan result when no entity is found. The builder does not guess which ambiguous entity the user meant.

`CapabilityValidator` checks exact advertised capability names and kinds. It prevents a plan from requesting an imaginary sensor or mistaking an action for a sensor. It validates descriptors; it does not run them.

Example plan for “Can I move forward?” after the provider proposes `current_route_status` for `front route` and the resolver maps it to `route_ahead`:

```text
intent: current_route_status
resolved entity: route_ahead
memory operation: query active route_ahead status_is facts
observation operation: request lidar_scan for route_ahead (if advertised)
```

If LiDAR is absent, the plan can retain the memory lookup but marks current status unverifiable. That prevents an old map claim from being presented as a fresh measurement. This example describes the plan produced by code; no executor currently runs these operations.

The provider's proposal is conceptually shaped like this:

```json
{
  "intent": "current_route_status",
  "entity_mentions": ["front route"],
  "user_question": "Can I move forward?"
}
```

This is a sample of the **provider-to-planner data**, not a complete answer. The planner checks that the intent is allowed and the question has not been changed. `PlanBuilder` then resolves `front route` to a canonical ID and creates the memory and observation request objects. The later executor—when implemented—would perform those requests and pass the resulting evidence to a policy/resolution stage.

Some intents create different plans:

| User asks | Intent | Planned evidence |
| --- | --- | --- |
| “What did we record about route A earlier?” | `historical_fact_lookup` | Historical facts for the resolved route; no sensor request. |
| “Why did the route change from clear to blocked?” | `audit_explanation` | An active fact lookup followed by audit-chain requests for facts returned by that lookup. |
| “Where are you right now?” | `current_robot_pose` | A `robot_pose` observation; memory is not substituted for current pose. |
| “Move forward.” | `environment_action` | Safe no-plan fallback until an allowed action name and safety-gated execution path exist. |

These are plan shapes only. They do not mean the underlying history, sensor, or action interface is already connected to a full running agent.

### Why observation context matters

Suppose a future Tier 3 sensor reports “obstacle 12 cm ahead.” The number alone is incomplete. A usable observation needs to be tied to enough context to interpret it, such as:

```text
location: room_101
observer: robot_01
frame_of_reference: robot_base
world_version: 4
measurement: nearest obstacle is 12 cm in front
```

`robot_base` means the measurement is relative to the robot's own coordinate frame. `room_101` tells us where that robot is; `world_version=4` identifies which simulated world state was observed. These example values illustrate the contract's purpose. GT-02 is still open, so the current code does not yet calculate or emit this LiDAR result.

### Unsupported-intent recovery

The code includes a deliberately bounded recovery step for false negatives: the provider may label a supported question `unsupported` even though the agent has a relevant capability. `IntentCoverageReviewer` checks a small set of documented wording rules and checks whether the required capability/data interface is available. It only identifies candidate intent categories; it cannot resolve an entity, make a plan, or call a tool.

The flow is:

1. No wording rule matches: return an uncertain `unsupported_after_review` fallback with no operations.
2. More than one category matches: return an uncertain fallback with the possible intent choices and matching rule IDs; the user needs to clarify.
3. Exactly one category matches: ask the provider once more, restricted to that category or `unsupported`.
4. If reconsideration is invalid, outside the allowed choices, unavailable, or remains `unsupported`, return a safe fallback. Only a valid allowed supported intent continues to normal entity resolution and planning.

The rules are a small allowlist of wording clues, not a hidden general-purpose classifier:

| Wording family | Candidate intent | Required availability |
| --- | --- | --- |
| `route`, `path`, `ahead`, `forward`, `blocked`, `blocking`, `obstacle` | `current_route_status` | `lidar_scan` registered as a sensor |
| `colour`/`color`, `appears`, `looks like`, `visible object` | `current_object_perception` | `camera_detect` registered as a sensor |
| `where are you`, `current location`, `current position` | `current_robot_pose` | `robot_pose` registered as a sensor |
| `earlier`, `before`, `historical`, `history` | `historical_fact_lookup` | History interface marked available |
| `audit`, `why changed`, `why believe`, `evidence history` | `audit_explanation` | Audit interface marked available |

Word/phrase boundaries are used so a larger word containing a clue does not automatically count as a match. History and audit candidates are ignored unless their corresponding availability flags are enabled.

Example: “Is anything blocking me?” is initially classified as `unsupported`. If the coverage rules identify only route status and `lidar_scan` is advertised, the provider is asked to reconsider between `current_route_status` and `unsupported`. If the question matches both route and object-perception rules, the system does not choose one; it returns clarification choices.

This improves recovery for known supported phrasings, but it is not a general natural-language parser. A genuinely unsupported question such as “What is the room temperature?” remains unsupported when there is no matching supported capability. The feature cannot invent tools or evidence.

All planning fallbacks are structured uncertain results: they have no conclusion, evidence IDs, conflicting IDs, or policy rule. Entity ambiguity candidates and intent-category clarification choices are separate fields.

Common outcomes are:

| Situation | Planner outcome | Why it is safe |
| --- | --- | --- |
| Entity alias maps to one ID | Continue with the canonical ID. | Memory and observation requests refer to a stable entity. |
| “The box” maps to two IDs | Return `ambiguous_entity` with both candidate IDs. | The planner does not guess which box was meant. |
| No known entity matches | Return a no-plan missing-entity fallback. | No tool request is built for a guessed entity. |
| Provider returns malformed intent twice (initial plus one repair) | Return `invalid_provider_output_after_repair`. | Invalid provider data cannot flow into planning or cause unbounded retries. |
| Provider returns a well-formed unsupported intent and no wording rule matches | Return `unsupported_after_review`. | A valid but unsupported request stays unsupported; no operations are created. |
| Unsupported wording matches multiple intent families | Return clarification choices and rule IDs. | The system asks for clarification instead of choosing a category. |

Fallbacks are typed values for later display or logging. They are not natural-language answers, and they do not claim that a sensor or repository was queried.

### What happens to the LLM in the current code

There is no concrete Groq, Llama, or other LLM SDK adapter in the merged source. Tests inject fake providers. When a real adapter is added, it will propose/repair/reconsider an intent only. Deterministic Python will still validate it, resolve entities, check capabilities, and build the evidence plan. The actual evidence retrieval, comparison policy, belief revision decision, and final user-facing response are not implemented as an end-to-end flow yet.

## Current implementation boundaries

| Implemented now | Not implemented/connected yet |
| --- | --- |
| Shared Pydantic contracts and validation. | Tier 3 `MockEnvironment` and real sensor computations; [GT-02 issue #2](https://github.com/dhruvkshah75/groundtruth/issues/2) remains open. |
| SQLite facts, exact queries, aliases, atomic revisions, and audit trails. | Runtime wiring that sends a user request through every tier. |
| Active-fact graph projection and graph/SQLite consistency reports. | An executor that performs the plan’s memory and observation operations. |
| Intent validation, one malformed-output repair, entity resolution boundary, capability checks, deterministic plan construction, and bounded unsupported-intent review. | Evidence resolution/policy that compares actual memory and sensor results and decides whether to record a belief revision. |
| Unit and Tier 1 integration tests using fakes or SQLite. | A production LLM adapter, final response generation/guard, dashboard, and public robot actions. |

An `ExecutionPlan` should therefore be read as “these are the approved evidence requests,” not “the robot has already queried memory, scanned with LiDAR, or acted.”

## One request, from start to finish (as components exist today)

For “Can I move forward?”, the current pieces can be described in this order:

1. **Intent proposal:** an injected provider returns a raw structure that proposes `current_route_status` and mentions “front route.” In tests, that provider is a fake; no production LLM is connected.
2. **Intent validation:** `IntentPlanner` checks the structure against `IntentRequest`, verifies the original question, and accepts the value only if it is in the fixed intent set. If malformed, it allows one repair attempt.
3. **Entity resolution:** the planning path passes “front route” through the injected resolver. A single result becomes its canonical ID, such as `route_ahead`; multiple or missing results stop planning safely.
4. **Capability validation:** the builder checks whether `lidar_scan` is advertised as a sensor. A missing sensor means the current status cannot be verified; a wrong-kind or invalid registry is treated as a configuration problem.
5. **Plan construction:** `PlanBuilder` creates an active-memory query and, when available, a LiDAR observation request. These are typed data objects.
6. **Current stopping point:** the components return the intent or `PlanningOutcome`. No executor runs the operations, no LiDAR is read, no evidence is compared, and no user-facing factual response is generated by the current implementation.

This separation matters for presentations: the team has implemented the **validated planning boundary**, not yet the end-to-end robot response loop.

## Implementation map

- `src/contracts/models.py` — cross-tier data contracts.
- `src/declarative/memory_repository.py` and `schema.py` — SQLite evidence ledger and schema.
- `src/declarative/active_graph.py` and `consistency.py` — active graph projection and consistency diagnostics.
- `src/procedural/intent_provider.py` — provider interface; no LLM SDK.
- `src/procedural/intent_planner.py` and `intent_coverage_reviewer.py` — intent validation and bounded unsupported review.
- `src/procedural/entity_resolver.py`, `capability_validator.py`, and `plan_builder.py` — narrow boundaries and deterministic planning.
- `src/procedural/execution_plan.py` and `fallback_results.py` — typed plans and safe no-plan outcomes.

## Verification note

After PR #11 was merged, the full test suite on `main` passed: **223 tests**. Ruff lint passed in the review snapshot. Ruff formatting still reported unformatted PR files (and three pre-existing files on `main`); that cleanup and the cache-invalidation integration are tracked in [issue #13](https://github.com/dhruvkshah75/groundtruth/issues/13). These results describe that merged snapshot, not an ongoing CI guarantee.

## Reading order

For more detail, continue with [Architecture Decisions](Architecture_Decisions.md), the [Tier 1 guide](tier1/Tier1_Implementation_Guide.md), and the [Tier 2 guide](tier2/Tier2_Implementation_Guide.md). The original assignment context is in [I, Agent Masterplan](I_Agent_Masterplan.md).
