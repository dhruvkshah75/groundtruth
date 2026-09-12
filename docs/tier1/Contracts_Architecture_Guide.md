# Tier 1 Contracts Architecture Guide

## Status and purpose

**Status: proposed team decision - review before implementation.**

This guide defines the shared data contracts for GroundTruth. Its purpose is to prevent an implementation of Tier 1, Tier 2, or Tier 3 from becoming tied to one specific environment such as pathfinding, a grid world, or LiDAR.

The proposal is intentionally environment-independent. The initial mock environment may implement route, LiDAR, camera, and lighting examples because they are required by the assignment, but the contracts must remain usable for another simulator or real hardware later.

## What a contract is

A contract is a strict agreement between code modules:

> A caller sends data in an agreed shape; the receiving module validates it and returns data in an agreed shape.

The project uses Pydantic models for this agreement. A contract is **not** a database model, an LLM prompt, sensor code, or business logic. It only defines valid data.

```text
Tier 2 calls Tier 1 with a FactQuery contract
Tier 1 returns StoredFact contract objects

Tier 2 calls Tier 3 with an ObservationRequest contract
Tier 3 returns a SensorObservation contract object
```

This lets the implementation behind a layer change without requiring every other layer to change.

## Non-negotiable layer boundaries

```text
                 FactAssertion / FactQuery / StoredFact
Tier 1 <-------------------------------------------------> Tier 2
SQLite + NetworkX                                    Policy + LLM tools

                 ObservationRequest / SensorObservation
Tier 3 <-------------------------------------------------> Tier 2
Mock world / future hardware                         Policy + LLM tools
```

Tier 1 and Tier 3 must **never call or import each other directly**. Tier 2 is the only orchestrator allowed to use both interfaces.

| Layer | It may do | It must not do |
| --- | --- | --- |
| `contracts/` | Define Pydantic models, validation, shared types | Import SQLite, NetworkX, the environment, LLM SDKs, or run policy |
| Tier 1 | Store facts, retrieve facts, supersede facts, build active graph, return audit chains | Read a sensor, import `MockEnvironment`, make LLM decisions |
| Tier 2 | Choose tools, compare evidence, apply policy, request revisions, explain results | Execute raw SQL, mutate graph internals, invent sensor readings |
| Tier 3 | Simulate/read the world and return observations | Import memory code, write SQLite rows, decide which belief wins |

## The core distinction: observation versus fact

An observation is raw or normalized evidence from a source. A fact is an epistemic claim that the agent may remember.

```text
Tier 3 observation:
  "The front sensor reported a nearest object at 12 cm."

Tier 2 interpretation:
  "Under the current safety policy, this supports a claim that the route segment is blocked."

Tier 1 fact:
  "route_segment_A status_is blocked, asserted by lidar_sensor."
```

Tier 3 does not decide whether an observation overrides memory. Tier 1 does not know how LiDAR works. Tier 2 performs the comparison and asks Tier 1 to persist any justified revision.

## Proposed contract models

The final code belongs in `src/contracts/`. This section shows the intended responsibilities and fields; the contracts owner may make small implementation improvements without weakening the boundary rules.

### 1. `FactAssertion`: an incoming claim before it is stored

```python
class FactAssertion(BaseModel):
    subject: str
    predicate: str
    object: str
    source_agent: str
    confidence_score: float
    observed_at: datetime
    context: dict[str, JsonValue] = Field(default_factory=dict)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
```

| Field | Meaning | Example |
| --- | --- | --- |
| `subject` | The entity the claim concerns | `box_01` |
| `predicate` | The relationship/claim kind | `perceived_color_is` |
| `object` | The claimed value or related entity | `brown` |
| `source_agent` | Who asserted it | `camera_sensor` |
| `confidence_score` | Reliability of this specific assertion, constrained to `0.0..1.0` | `0.82` |
| `observed_at` | Time the source observed/asserted it, in timezone-aware UTC | `2026-09-13T10:15:00Z` |
| `context` | Scope of the assertion: perspective, location, reference frame, or world version | `{"location": "room_101"}` |
| `evidence` | Supporting details or references to raw observations | `{"observation_id": "..."}` |

`FactAssertion` deliberately does not contain `fact_id`, `created_at`, `superseded_by`, SQL row IDs, NetworkX edges, or LLM reasoning text. Tier 1 adds storage-specific details only after accepting the claim.

### 2. `StoredFact`: a fact persisted by Tier 1

```python
class StoredFact(FactAssertion):
    fact_id: UUID
    created_at: datetime
    superseded_by: UUID | None = None
```

The database generates `fact_id` and `created_at`. A fact is active when `superseded_by` is `None`; old facts remain in the database for historical explanation.

### 3. `FactQuery`: a safe request to Tier 1

```python
class FactQuery(BaseModel):
    subject: str | None = None
    predicate: str | None = None
    active_only: bool = True
    as_of: datetime | None = None
    context_filters: dict[str, JsonValue] = Field(default_factory=dict)
```

Tier 2 uses this instead of SQL. For example, it can ask for active beliefs about `box_01` or discover what was believed at a previous time. Tier 1 decides how to execute the query using SQLite and/or NetworkX.

### 4. `ObservationRequest`: a generic request to Tier 3

```python
class ObservationRequest(BaseModel):
    capability: str
    target: str | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
```

`capability` is intentionally generic. The first mock environment might support `lidar_scan`, `camera_detect`, `ambient_light`, and `robot_pose`, but the contract does not force any particular capability.

### 5. `SensorObservation`: data returned by Tier 3

```python
class SensorObservation(BaseModel):
    observation_id: UUID
    sensor: str
    capability: str
    observed_at: datetime
    confidence_score: float
    measurements: dict[str, JsonValue]
    context: dict[str, JsonValue] = Field(default_factory=dict)
```

Examples of valid observations:

```json
{
  "capability": "lidar_scan",
  "sensor": "lidar_sensor",
  "measurements": {"nearest_distance_cm": 12}
}
```

```json
{
  "capability": "camera_detect",
  "sensor": "front_camera",
  "measurements": {
    "object_id": "box_01",
    "apparent_color": "brown",
    "bounding_box": [120, 80, 260, 300]
  }
}
```

The first environment calculates these values from a simulated world. A future 3D simulator or physical robot can return the same contract through an adapter.

### 6. `BeliefRevision` and `AuditEvent`: proof of a change of mind

```python
class BeliefRevision(BaseModel):
    old_fact_id: UUID
    new_fact_id: UUID
    reason: str
    source_priority_rule: str
    revised_at: datetime

class AuditEvent(BaseModel):
    event_id: UUID
    event_type: str
    input_fact_ids: list[UUID]
    output_fact_ids: list[UUID]
    reason: str
    created_at: datetime
```

These make the project auditable. They show that the agent did not merely output a new answer: it had evidence, applied a declared rule, and created a traceable revision.

## Generic facts, not pathfinding facts

The triple pattern is generic:

```text
subject + predicate + object
```

| Domain | Example |
| --- | --- |
| Navigation | `route_segment_A status_is blocked` |
| Vision | `box_01 perceived_color_is brown` |
| Historical maintenance | `box_01 painted_color_is blue` |
| Location | `robot_01 located_at room_101` |
| Lighting | `room_101 ambient_light_is yellow` |

Do not create contract models such as `PathStatus`, `LidarObstacleFact`, or `CameraColourFact` as the universal interface. Those would tie the project to one environment. Domain-specific classes may exist inside Tier 3 adapters, but they must be converted to the generic shared contracts at the boundary.

Use a small, reviewed predicate vocabulary (for example `status_is`, `located_at`, `perceived_color_is`, `painted_color_is`) to avoid accidental incompatible spellings. Keep it a registry/constants file rather than a closed enum in version 1, so future environments can add capabilities without redesigning all contracts.

## Context and evidence are different

`context` answers: **where, for whom, and under what scope does the claim apply?**

```json
{
  "location": "room_101",
  "observer": "robot_01",
  "frame_of_reference": "robot_base",
  "world_version": 4
}
```

`evidence` answers: **what supports the claim?**

```json
{
  "observation_id": "b9f1...",
  "sensor": "front_camera",
  "ambient_light": "yellow",
  "bounding_box": [120, 80, 260, 300]
}
```

`context` is not the LLM prompt context window. It is factual scope metadata that prevents an observation in one place, time, or perspective from being treated as a universal fact.

## Information flow example

```text
1. User: "Is route_segment_A clear?"

2. Tier 2 -> Tier 1:
   FactQuery(subject="route_segment_A", predicate="status_is")

3. Tier 1 -> Tier 2:
   StoredFact(object="clear", source_agent="static_map", confidence_score=0.90)

4. Tier 2 -> Tier 3:
   ObservationRequest(capability="lidar_scan", target="route_segment_A")

5. Tier 3 -> Tier 2:
   SensorObservation(measurements={"nearest_distance_cm": 12}, confidence_score=0.99)

6. Tier 2 applies the conflict policy. It creates a generic FactAssertion
   only if that observation is relevant, fresh, and strong enough.

7. Tier 2 -> Tier 1:
   revise_belief(old_map_fact, new_lidar_fact, declared_reason)

8. Tier 1 stores both facts, links the old fact to the replacement, records
   the audit event, and updates its active NetworkX graph projection.
```

The same flow works for colour, position, lighting, and future capabilities. Some questions need only Tier 1 (for example, an audit query); some need only Tier 3; some need both.

## Direct retrieval, not RAG

Tier 1 uses direct, structured retrieval:

```text
FactQuery -> SQLite indexed query / NetworkX graph query -> StoredFact list
```

This is more precise and auditable than RAG for facts. The LLM receives only a focused memory snapshot through a tool call, not a dump of the entire database.

RAG is optional later for unstructured documents such as a maintenance manual or incident report. If added, retrieved text must be treated as another sourced, lower-priority evidence input; it must not replace the fact/audit system.

## Proposed implementation order

1. Create `src/contracts/` and implement the contract models with Pydantic v2.
2. Add validation tests for confidence range, timezone-aware timestamps, and JSON-compatible metadata.
3. Refactor the current `DatabaseManager` so it accepts `FactAssertion` and returns `StoredFact`.
4. Implement `record_fact`, active-fact lookup, transactional supersession, and audit-chain lookup.
5. Build the active NetworkX graph projection from Tier 1 facts.
6. Implement Tier 3 adapters that return `SensorObservation` only.
7. Implement Tier 2 tool calls and conflict policy using the contracts.

## Team review checklist

Approve this guide only if the team agrees that:

- [ ] Pydantic v2 is the shared contract mechanism.
- [ ] Contracts are generic and contain no hardcoded pathfinding assumptions.
- [ ] Tier 1 and Tier 3 are completely isolated and never import/call each other.
- [ ] Tier 2 is the sole orchestrator between memory and environment.
- [ ] Facts are append-only; revisions preserve historical assertions.
- [ ] Source, confidence, observation time, context, and evidence are stored with every remembered claim.
- [ ] Tier 2 uses structured `FactQuery` retrieval rather than raw SQL or RAG for normal memory access.
- [ ] Conflict-resolution policy is kept out of contracts and implemented in Tier 2.
- [ ] Contract changes require team review because they affect every layer.
