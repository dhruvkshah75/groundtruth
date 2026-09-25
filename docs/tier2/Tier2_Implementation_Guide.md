# Tier 2 Implementation Guide

## What Tier 2 is responsible for

Tier 2 is the orchestrator. It receives the user's words, obtains evidence from Tier 1 and Tier 3, applies the belief policy, asks Tier 1 to record justified changes, and gives the user an evidence-backed response.

Tier 2 is the only layer allowed to coordinate both memory and environment. It must not execute raw SQL, edit NetworkX directly, or invent sensor observations.

For the overall design, read [Architecture Decisions](../Architecture_Decisions.md) first.

## Why Tier 2 has both LLM and deterministic code

Natural language is flexible:

```text
“Can I move forward?”
“Is anything blocking me?”
“Is the route ahead clear?”
```

All may mean the same thing. An LLM is useful for recognizing this intent.

However, an LLM is not reliable enough to decide whether a sensor, old map, or user claim is true. Tier 2 therefore uses deterministic Python for all evidence-critical work.

```text
LLM: understand the question, choose an allowed intent, and write explanation
Python: validate requests, retrieve evidence, resolve conflict, record revisions
```

## IntentRequest: the only LLM planning output

The LLM does not freely generate an arbitrary list of database and sensor calls. It returns a limited intent request:

```python
class IntentRequest(BaseModel):
    intent: Literal[
        "current_route_status",
        "current_object_perception",
        "historical_fact_lookup",
        "audit_explanation",
        "current_robot_pose",
        "environment_action",
        "unsupported",
    ]
    entity_mentions: list[str] = Field(default_factory=list)
    user_question: str
```

Example:

```text
User: “Can I go forward?”
IntentRequest: current_route_status, ["route_ahead"]
```

Use function calling or JSON-schema structured output when available. Parse it with Pydantic. If parsing fails, give the LLM one repair attempt containing only the validation error. Do not retry forever.

## Context given to the LLM

The LLM needs enough information to understand its role, but it must not receive the entire database or graph. Tier 1 remains the source of memory; the LLM asks for relevant information through approved operations.

### Before evidence is collected

Give the LLM a small, stable prompt containing:

```text
1. Its role: understand the user question and return a structured IntentRequest.
2. The supported intents and their meanings.
3. The available capability names and short descriptions.
4. The rule that it must not invent observations, facts, tools, or final conclusions.
5. The user's question, clearly separated from system instructions.
```

Example capability context:

```text
lidar_scan: detect nearby obstacles in front of the robot
camera_detect: detect visible objects and their current appearance
robot_pose: report simulated robot location and direction
query_current_memory: read an active belief
query_history: read older facts and audit history
```

The capability list comes from Tier 3's `CapabilityDescriptor` contracts at startup. It is only a list of what may be requested; it is not sensor data and does not let the LLM create new tools.

For the current implementation, the LLM returns an `IntentRequest`, not an unrestricted sequence of tool calls. The deterministic `PlanBuilder` chooses the mandatory operations for that intent. For example, `current_route_status` always includes an active-memory query and a fresh LiDAR request when LiDAR is available.

### After evidence is collected

After Python has validated and run the plan, give the LLM only the relevant returned data:

```text
- requested facts and their source/time/confidence;
- requested sensor observations and their context;
- the selected conclusion or uncertainty result;
- the policy rule used, conflicts found, and evidence IDs.
```

The LLM may turn this into a clear explanation. It must not change the conclusion, policy rule, or evidence IDs. A deterministic template should be used in automated tests.

## Deterministic PlanBuilder

The PlanBuilder converts a validated intent into an `ExecutionPlan`. It decides which evidence is mandatory; the LLM does not.

| Intent | Mandatory evidence plan |
| --- | --- |
| `current_route_status` | Active `status_is` memory query + fresh `lidar_scan` if available |
| `current_object_perception` | Fresh `camera_detect` for the resolved object |
| `historical_fact_lookup` | Historical Tier 1 fact query |
| `audit_explanation` | Tier 1 audit-chain request |
| `current_robot_pose` | Fresh `robot_pose` observation |
| `environment_action` | Allowed action request + deterministic safety pre-check |
| `unsupported` | No tools; uncertainty result |

This protects the agent from incomplete LLM plans. A current route question cannot accidentally be answered from only a static map because Python always adds the fresh LiDAR request when the capability exists.

## Capability validation

Tier 3 declares its available capabilities at application startup, for example:

```text
lidar_scan
camera_detect
ambient_light
robot_pose
move_robot
```

Before executing a plan, Tier 2 checks every requested capability against that registry. Unknown requests, such as `read_weather`, are rejected. The LLM cannot create a sensor by naming it.

## Full request execution flow

```text
1. User sends a question.
2. LLM proposes IntentRequest.
3. Pydantic validates intent shape and allowed values.
4. Tier 1 resolves entity mentions through aliases.
5. If entity is missing or ambiguous, return a grounded no-match/clarification response.
6. PlanBuilder creates mandatory FactQuery and ObservationRequest operations.
7. Capability validator rejects unsupported environment operations.
8. Executor calls Tier 1 and Tier 3 through contracts.
9. EvidenceResolver compares returned facts and observations.
10. If belief changes, Tier 2 requests a Tier 1 revision/audit write.
11. Build GroundedResult.
12. Render it with a deterministic template or LLM explanation.
13. ResponseGuard ensures factual claims include evidence IDs.
```

## Example: current route status

```text
User: “Is route_A clear?”
LLM intent: current_route_status(route_A)
Tier 1: active map fact says clear, confidence 0.90, observed 09:00
Tier 3: LiDAR sees obstacle 12 cm away, confidence 0.99, observed now
Policy: live direct evidence outranks older static map for a current safety question
Tier 1: store blocked fact and audit link
Result: route_A is currently blocked
```

The LLM may later write a natural explanation, but it cannot turn the conclusion into `clear` or remove the evidence IDs.

## EvidenceResolver: deterministic belief selection

The EvidenceResolver should not return prose. It returns structured data:

```text
selected fact/observation
relevant alternatives or conflicts
policy rule used
evidence IDs
uncertainty flag
```

For current dynamic-state questions, it applies:

1. Confirm entity and context match.
2. Require fresh direct evidence when the environment can provide it.
3. Prefer direct observation over an old static claim.
4. Compare freshness, reliability class, and confidence.
5. Preserve distinct perspectives rather than forcing a false conflict.
6. Return a conclusion only if evidence supports it.

Initial comparable-source priority:

```text
fresh direct sensor > user assertion > maintenance/history > static map
```

## Scenario B: perspectives are not automatically conflicts

The user may request a red box; a camera may see brown under yellow light; a maintenance record may state that the box was painted blue yesterday.

Tier 2 must not merge this into one alleged colour. It should return separate statements:

```text
User request: red target
Current camera perception: brown under yellow light
Maintenance history: painted blue
```

Each statement must cite its own source and evidence.

## Failure and fallback behavior

| Situation | Required behavior |
| --- | --- |
| Malformed LLM intent once | Send one repair request with validation error |
| Malformed LLM intent again | Return “I could not determine a safe evidence plan.” |
| Unknown intent/capability | Unsupported; invoke no imaginary tool |
| No matching entity | Report no matching known entity/evidence |
| Several matching entities | Ask user which entity they mean |
| Empty memory and no live capability | Return inability to verify |
| Sensor is unavailable for current-state question | State that the current state cannot be verified |
| Sensor conflicts with memory | Use deterministic policy and create audit-backed revision if warranted |

RAG is not the fallback for these conditions. RAG may suggest candidates later, but it cannot automatically choose a fact or make a belief decision.

## ResponseGuard and GroundedResult

Every factual answer about the environment must be based on a `GroundedResult`:

```python
class GroundedResult(BaseModel):
    conclusion: str | None
    evidence_ids: list[UUID]
    conflicting_ids: list[UUID] = Field(default_factory=list)
    policy_rule: str | None = None
    uncertainty: bool
```

Rules:

```text
evidence_ids present -> factual answer may be rendered
evidence_ids empty   -> response must be uncertain/unsupported
```

Use deterministic response templates in automated tests. The LLM explanation layer is optional and must not alter conclusion, policy rule, or evidence IDs.

## Fast safety path and slower reasoning path

Some events should not wait for the LLM. For example, a LiDAR obstacle below a configured stopping distance can immediately produce a stop action and evidence record.

```text
Fast path: urgent, known rule, deterministic response
Slow path: natural-language questions, multi-source conflicts, explanations
```

Both paths use the same contracts and record evidence through Tier 1.

## Minimum tests

1. Current route intent always requires LiDAR when it is available.
2. Invalid LLM JSON does not invoke memory or environment tools.
3. Unknown capability is rejected before Tier 3 is called.
4. Ambiguous entity creates a clarification result.
5. Missing evidence creates an uncertainty result, not a guessed answer.
6. Map-clear versus LiDAR-blocked follows policy and creates a revision request.
7. Scenario B returns separate source-scoped perspectives.
8. A final factual response without evidence IDs is rejected by ResponseGuard.
