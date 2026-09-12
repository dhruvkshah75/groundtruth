# Grounded Query Guardrails

## Purpose

Users may ask any natural-language question. GroundTruth must not treat every question as a fact about the simulated world. It may only make a grounded world claim when approved memory or environment evidence supports it.

```text
The LLM may understand any question.
The agent may verify only questions supported by its registered tools and memory.
```

If evidence is unavailable, the correct response is:

> “I cannot verify that with my available sensors or memory.”

This is a required anti-hallucination behavior, not a failure.

## Query flow

```text
User question
    ↓
Tier 2 creates a validated QueryPlan
    ↓
Python checks the plan against approved capabilities
    ↓
Tier 2 calls Tier 1, Tier 3, both, or neither
    ↓
Tier 1/Tier 3 return evidence contracts
    ↓
Tier 2 creates an evidence-bound answer
```

## Query-plan contract

```python
class QueryPlan(BaseModel):
    kind: Literal["observe", "memory", "audit", "action", "unsupported"]
    capability: str | None = None
    subject: str | None = None
    predicate: str | None = None
    reason: str
```

| Plan kind | Meaning | Usual target |
| --- | --- | --- |
| `observe` | Needs current environmental evidence | Tier 3 sensor |
| `memory` | Needs active or historical fact(s) | Tier 1 SQLite/graph |
| `audit` | Needs explanation of a belief revision | Tier 1 SQLite history |
| `action` | Requests a permitted environment action | Tier 3 action API |
| `unsupported` | No available evidence path exists | No tool call |

The LLM may propose a plan, but Python/Pydantic must validate it before any tool executes.

## Capability allowlist

Tier 2 maintains an explicit registry of what the current environment can do. The initial mock environment may register:

```python
SUPPORTED_CAPABILITIES = {
    "lidar_scan",
    "camera_detect",
    "ambient_light",
    "robot_pose",
}
```

It may also register supported Tier 1 query operations:

```python
SUPPORTED_MEMORY_QUERIES = {
    "active_facts",
    "historical_facts",
    "audit_chain",
}
```

The registry is extensible. A future temperature sensor can add `read_temperature`; it is not a reason to hardcode temperature into every contract now.

If an LLM proposes a capability that is not registered, such as `read_weather` or `read_temperature` before a temperature sensor exists, Tier 2 rejects the plan and returns an uncertainty response. It must never call an imaginary sensor.

## Responsibilities by tier

### Tier 2: route and validate the request

Tier 2 must:

1. Turn the user request into a `QueryPlan`.
2. Check that requested capabilities are in the allowlist.
3. Query only the required memory and/or sensor tools.
4. Compare evidence when more than one source is relevant.
5. Return `unsupported` when no valid evidence path exists.
6. Include evidence identifiers in factual environment answers.

Tier 2 must not invent a sensor result, access SQLite directly, or allow the LLM to bypass the capability registry.

### Tier 1: return only real stored evidence

Tier 1 must:

1. Accept only validated `FactQuery` requests.
2. Return matching `StoredFact` objects with fact IDs, sources, confidence, and timestamps.
3. Return an empty result when it has no matching memory.
4. Preserve historical/audit facts without manufacturing a conclusion.

Tier 1 must not guess missing facts or convert an empty query result into a positive claim.

### Tier 3: return only real simulated observations

Tier 3 must:

1. Accept only registered `ObservationRequest` capabilities.
2. Calculate data from the current simulated world state.
3. Return a `SensorObservation` or a structured "capability unavailable" result.

Tier 3 must not decide which belief is true or write facts to Tier 1.

## Examples

| User question | Valid plan | Evidence source | Expected behavior |
| --- | --- | --- | --- |
| “What is in front of you?” | `observe: lidar_scan` | Tier 3 LiDAR | Return current distance/obstacle evidence. |
| “What colour is box_01?” | `observe: camera_detect` | Tier 3 camera | Return current camera observation. |
| “Why is route_A blocked?” | `audit` | Tier 1 audit chain | Explain map/sensor facts and revision reason. |
| “Where was robot_01 earlier?” | `memory` | Tier 1 history | Return matching historical facts. |
| “What is Delhi's weather?” | `unsupported` | None | State that no weather source exists. |
| “Who will win the World Cup?” | `unsupported` | None | Do not present an ungrounded prediction as agent knowledge. |

## Evidence-bound answers

Tier 2 should construct factual responses from returned evidence, not from an LLM guess. A response contract can require supporting identifiers:

```python
class AgentResponse(BaseModel):
    answer: str
    evidence_ids: list[UUID]
    uncertainty: bool = False
```

Examples:

```text
Grounded: “Route A is blocked according to LiDAR observation obs_123 at 10:30 UTC.”
Unsupported: “I cannot verify the room temperature because I have no temperature sensor or stored temperature fact.”
```

For an environment claim, `evidence_ids` should not be empty. For an unsupported response, `uncertainty=True` and no invented evidence is allowed.

## Minimum tests

1. A supported LiDAR request invokes the registered sensor and includes its observation ID in the answer.
2. An unsupported capability is rejected without invoking Tier 3.
3. An empty Tier 1 result produces an uncertainty answer, not a guessed fact.
4. An audit question returns stored fact IDs and revision information.
5. An LLM-generated plan containing an unknown capability is rejected by Python validation.
