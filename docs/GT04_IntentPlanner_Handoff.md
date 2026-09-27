# GT-04 Intent Planner Handoff

## Purpose

This document gives an implementation-ready handoff for **GT-04: Tier 2
intent planning, safe plan building, and fallback guardrails**. It includes a
user-requested proposed extension for team review: bounded recovery when an
LLM returns a valid `unsupported` intent for wording that the agent may
actually support.

Read this with:

- [Architecture Decisions](Architecture_Decisions.md)
- [Tier 2 Implementation Guide](tier2/Tier2_Implementation_Guide.md)
- [Unsupported Intent Recovery Proposal](tier2/Unsupported_Intent_Recovery_Proposal.md)
- `src/contracts/models.py`

The issue itself remains the authority if this handoff conflicts with it.

## The problem GT-04 solves

Users ask in flexible language, for example:

```text
"Can I move forward?"
"Is anything blocking me?"
"What colour does the box look like now?"
```

The system needs a repeatable way to decide which evidence is required. A
language model is useful for recognizing that the first two questions are
about route status, but it must not choose tools, create SQL, or decide facts.

GT-04 splits those responsibilities:

```text
LLM/provider: propose one small request category (an intent)
IntentPlanner: validate the proposal or stop safely
PlanBuilder: deterministically map a valid intent to required operations
Later executor: run the approved operations
Later EvidenceResolver: compare evidence and form a conclusion
```

The planner and builder produce data only. They do not query SQLite, call a
sensor, write a revision, or return a factual natural-language answer.

## Essential terminology

| Term | Meaning |
| --- | --- |
| User question | The original natural-language text supplied to Tier 2. It is untrusted input. |
| Provider | A narrow adapter that obtains an intent proposal. In GT-04 tests it is a fake object with pre-arranged responses; later it may call Groq, Llama, or another structured-output model. |
| Raw provider output | Untrusted JSON-like data returned by the provider. It may be malformed or contain invented fields. |
| `IntentRequest` | The shared Pydantic contract that validates the small allowed intent set. |
| IntentPlanner | The Tier 2 component that calls the provider and turns raw output into either a validated `IntentRequest` or a safe failure. |
| PlanBuilder | Deterministic Python that converts a valid intent into the mandatory memory/observation plan. |
| Capability registry | The advertised list of Tier 3 sensors/actions. It is an allowlist. |
| ExecutionPlan | Typed, non-executable data describing operations that later code is permitted to execute. |

## Fixed intent vocabulary

The provider must choose exactly one value from the shared contract:

```text
current_route_status
current_object_perception
historical_fact_lookup
audit_explanation
current_robot_pose
environment_action
unsupported
```

The provider cannot invent `read_weather`, `route_check`, a sensor name, a SQL
query, an entity ID, or a final answer. Pydantic rejects unknown values and
extra fields.

## Base GT-04 flow

```text
1. User asks a non-empty question.
2. IntentPlanner calls provider.propose_intent(question) once.
3. IntentPlanner parses the raw response as IntentRequest.
4. If valid and supported, pass the intent to entity resolution and PlanBuilder.
5. If invalid, call provider.repair_intent(question, bounded_error) once.
6. If repaired output is valid, continue.
7. If repair fails or the provider raises, return a structured no-execution
   failure.
```

The one-repair limit prevents retry loops and prevents a malformed provider
response from becoming an unstructured "best effort" answer.

### Example: normal route request

```text
User: "Can I move forward?"

Raw provider output:
  intent = current_route_status
  entity_mentions = ["front route"]
  user_question = "Can I move forward?"

IntentPlanner:
  validates the response

EntityResolver:
  "front route" -> route_ahead

PlanBuilder:
  1. active FactQuery(subject=route_ahead, predicate=status_is)
  2. fresh ObservationRequest(capability=lidar_scan, target=route_ahead),
     when lidar_scan is advertised

GT-04 output:
  typed ExecutionPlan only
```

## IntentPlanner responsibilities

The IntentPlanner must:

1. Reject an empty user question.
2. Ask the provider for one initial proposal.
3. Validate raw data using `IntentRequest`.
4. Enforce integrity of `user_question`.
5. Make at most one repair request when the initial output is malformed.
6. Return a structured diagnostic and safe failure if the provider is
   unavailable or still invalid.
7. Send a valid `unsupported` response through the bounded recovery review
   described below.
8. Never call Tier 1, Tier 3, an executor, or an action.

### User-question integrity rule

Use the originating user question as the authoritative question. The returned
`IntentRequest.user_question` must equal the original question after leading
and trailing whitespace normalization. A mismatch is invalid provider output
and receives the one normal repair attempt.

This prevents a provider response from silently redirecting planning to a
different question.

### Invalid output versus valid unsupported

| Provider output | Classification | Required planner behaviour |
| --- | --- | --- |
| `read_weather` | Invalid; not in the enum | One repair attempt. |
| Missing `user_question` | Invalid contract shape | One repair attempt. |
| Extra `tool: lidar_scan` field | Invalid because contract forbids extras | One repair attempt. |
| `unsupported` | Valid enum value | Run bounded unsupported review; do not execute a tool directly. |

## Extension: bounded unsupported-intent recovery

### Why it is needed

The base design safely refuses unsupported requests. It can still create a
false negative when a provider misunderstands a supported paraphrase:

```text
User: "Is anything blocking me?"
Provider: unsupported
```

The environment may have `lidar_scan`, so this refusal is safe but unhelpful.
The recovery extension checks this situation without letting keywords bypass
the intent and capability boundaries.

### Recovery flow

```text
Validated provider intent = unsupported
        |
        v
IntentCoverageReviewer examines normalized question using a small,
version-controlled set of documented phrase families and current capabilities
        |
        +-- no strong candidate --> unsupported outcome; no operations
        |
        +-- one strong candidate --> one constrained provider reconsideration
        |                            |
        |                            +-- supported valid intent --> normal
        |                            |   entity resolution and PlanBuilder
        |                            |
        |                            +-- unsupported --> unsupported outcome
        |                            |
        |                            +-- invalid/out-of-set intent --> safe
        |                                provider-output failure
        |
        +-- several candidates --> clarification outcome; no operations
```

This is a **reclassification review**, not a second repair. It happens only
after a structurally valid `unsupported` intent and is allowed at most once.

### Provider interface extension

GT-04's base provider interface has:

```text
propose_intent(user_question)
repair_intent(user_question, validation_error)
```

Add a separate, explicit method for the extension:

```text
reconsider_unsupported(user_question, candidate_intents)
```

The provider receives only the original question and a candidate set, for
example `[current_route_status, unsupported]`. It must return a normal
`IntentRequest` payload. It must not receive sensor results, database facts,
secrets, tool objects, or unrestricted tool instructions.

The returned intent must be either the reviewed candidate or `unsupported`.
Any other intent is a provider violation and causes a safe failure. Do not
attempt another repair or another reconsideration.

### Deterministic coverage rules

The reviewer must use a deliberately small, documented rule table. It does
not resolve entities, create a plan, or invoke a capability.

Initial candidate families:

| Normalized wording family | Candidate intent | Gate |
| --- | --- | --- |
| route, path, ahead, forward, blocked, blocking, obstacle | `current_route_status` | `lidar_scan` is advertised as a sensor |
| colour/color, looks like, appears, visible object | `current_object_perception` | `camera_detect` is advertised as a sensor |
| where are you, current location, current position | `current_robot_pose` | `robot_pose` is advertised as a sensor |
| earlier, before, historical, history | `historical_fact_lookup` | available Tier 1 history interface at integration time |
| why changed, why believe, audit, evidence history | `audit_explanation` | available Tier 1 audit interface at integration time |

These examples require careful refinement before production. Avoid broad terms
such as `see`, `go`, or `what`, which create false candidates. Rules must use
normalized text and deterministic matching. Every rule must be unit tested.

### Recovery examples

#### One candidate

```text
Question: "Is anything blocking me?"
Initial intent: unsupported
Registry: lidar_scan is available as a sensor
Reviewer result: [current_route_status]
Reconsideration choices: [current_route_status, unsupported]
Provider returns: current_route_status, ["front route"]
Outcome: proceed through normal validation, entity resolution, and plan build
```

#### No candidate

```text
Question: "What is the room temperature?"
Initial intent: unsupported
Reviewer result: []
Outcome: unsupported; no operations
```

#### Several candidates

```text
Question: wording matches both route status and object perception
Initial intent: unsupported
Reviewer result: [current_route_status, current_object_perception]
Outcome: clarification outcome; no reconsideration and no operations
```

### Safety requirements for recovery

- Recovery never directly invokes LiDAR, camera, memory, or an action.
- Recovery never creates an `ExecutionPlan` before a second validated intent.
- Recovery never resolves an entity from a keyword.
- Recovery is limited to one reconsideration call.
- Unknown/missing/kind-mismatched capabilities cannot become candidates.
- Ambiguity returns clarification rather than a guess.
- The recovery decision and matching rule identifiers should be retained as
  structured diagnostics for tests and the later dashboard.

## Entity resolution flow

Entity resolution happens after a supported validated intent, including an
intent accepted through recovery.

| Resolver result | Planner/Builder outcome |
| --- | --- |
| `resolved` with one canonical ID | Use canonical ID in the typed plan. |
| `ambiguous` with candidates | Clarification outcome; no operations. |
| `missing` | No-evidence outcome; no operations. |
| No mention for entity-required intent | Missing-entity outcome unless a documented default exists. |
| Several independent mentions | Unsupported/clarification outcome; do not choose the first. |

Tier 2 must use an injected `EntityResolver` Protocol. It must not import a
concrete Tier 1 repository.

## PlanBuilder mapping

The PlanBuilder is deterministic. It knows required operations from the fixed
intent enum, not from the provider's prose.

| Intent | Required typed plan |
| --- | --- |
| `current_route_status` | Resolve one route, add active `FactQuery` for `status_is`, then add `lidar_scan` when advertised. If LiDAR is absent, retain memory for historical context but set `current_state_verifiable=false`. |
| `current_object_perception` | Resolve one object and request `camera_detect`. If unavailable, mark current perception unverifiable. Historical maintenance data is not a substitute. |
| `historical_fact_lookup` | Resolve relevant entity and add `FactQuery(active_only=false)`; do not add sensors. |
| `audit_explanation` | Resolve the relevant entity/fact and add an audit-chain retrieval operation; do not create prose. |
| `current_robot_pose` | Request `robot_pose`; do not claim current pose from memory. |
| `environment_action` | Create only an allowlisted action placeholder with `requires_safety_precheck=true`; execute nothing. The current contract may require this to be unsupported until a safe action name is available. |
| `unsupported` | No memory, observation, or action operations; structured uncertain outcome. |

The plan uses typed data models such as memory-query operations,
observation operations, audit-chain operations, and an action placeholder. It
must not store database connections, environment objects, callables, or
provider objects.

## Capability validation

The capability validator receives a `list[CapabilityDescriptor]` from Tier 3
at startup or a fake list in tests. It must:

- reject duplicate names as invalid configuration;
- require exact capability-name matches;
- require the descriptor kind to match the operation kind;
- treat the registry as immutable input;
- reject unknown requests before any later execution;
- never use dynamic `getattr` dispatch.

`lidar_scan`, `camera_detect`, and `robot_pose` are Tier 3 sensors proposed by
GT-02. GT-04 may use fake descriptors because it is not blocked by GT-02.

## Suggested public/internal outcomes

Use a narrow typed `PlanningOutcome` or an equivalent structure that contains
an optional `ExecutionPlan`, fallback category, diagnostics, candidate IDs for
clarification, and a `GroundedResult` where a user-facing safe uncertainty is
needed.

Required fallback categories:

```text
invalid_provider_output_after_repair
provider_unavailable
unsupported_intent
unsupported_after_review
invalid_provider_output_after_unsupported_review
unknown_capability
required_capability_unavailable
missing_entity
ambiguous_entity
invalid_configuration
```

Every uncertain `GroundedResult` requires `uncertainty_reason`. It must not
contain fabricated evidence IDs or a resolved environmental conclusion.

## Files to create

```text
src/procedural/
  __init__.py
  intent_provider.py          # Provider Protocol and fake-test support
  intent_planner.py           # Initial proposal, repair, and review lifecycle
  intent_coverage_reviewer.py # New bounded unsupported-review rules
  execution_plan.py           # Typed operation and ExecutionPlan data models
  plan_builder.py             # Fixed intent-to-operation mapping
  capability_validator.py     # Registry validation
  fallback_results.py         # Typed safe failures and clarification outcomes

tests/unit/procedural/
  test_intent_planner.py
  test_intent_coverage_reviewer.py
  test_plan_builder.py
  test_capability_validator.py
  test_fallback_results.py
  test_procedural_boundaries.py
```

The `intent_coverage_reviewer.py` module and its test are a proposed scope
extension. Keep its rules isolated so they can be changed, audited, and
evaluated without changing the core planner.

## Test checklist

### Base planner

- Valid first provider response causes no repair call.
- Invalid first response causes exactly one repair call.
- Valid repaired response succeeds.
- Two invalid responses return safe failure.
- Provider exception returns `provider_unavailable`.
- Unknown enum value and extra fields are rejected.
- Explicit `unsupported` does not use the invalid-output repair flow.
- Mismatched returned user question is rejected under the integrity rule.
- Parsing invokes no repository, environment, or executor methods.

### Unsupported recovery

- Valid unsupported with no candidate remains unsupported and calls no tool.
- A single strong route candidate with advertised LiDAR causes exactly one
  constrained reconsideration call.
- A recovered `current_route_status` continues to normal plan build.
- A recovered `unsupported` remains unsupported.
- An invalid reconsideration response produces safe failure with no retry.
- A response outside the restricted candidate set produces safe failure.
- Missing or wrong-kind capability prevents a sensor-dependent candidate.
- Several candidates produce clarification, no reconsideration, and no plan.
- Matching text never directly produces an observation request or execution.
- Rules are case/whitespace-normalized and deterministic.

### Builder, capability, and boundary checks

- Route status always contains an active `status_is` query.
- Route status includes LiDAR only when the registry advertises it correctly.
- Lack of LiDAR marks current route state unverifiable.
- Object perception requires camera; pose requires robot_pose.
- Historical lookup has `active_only=false` and no sensor.
- Unsupported and clarification outcomes contain no operations.
- Duplicate descriptors and kind mismatches are configuration failures.
- Procedural modules import no `sqlite3`, NetworkX, concrete repository,
  concrete `MockEnvironment`, provider SDK, Streamlit, or EvidenceResolver.
- All tests run offline with fake provider/resolver/registry objects.

## Implementation order

1. Re-read shared contracts and keep all inter-tier data in those contracts.
2. Define provider and resolver Protocols plus fake test doubles.
3. Define typed execution-plan and fallback models.
4. Implement capability validation.
5. Implement base IntentPlanner validation and one-repair lifecycle.
6. Implement PlanBuilder's fixed intent mapping.
7. Implement the isolated coverage reviewer and bounded reconsideration path.
8. Add unit tests for happy paths, failure paths, and zero-execution guarantees.
9. Add import/boundary tests.
10. Run `uv run pytest`, `uv run ruff format --check .`, and `uv run ruff check .`.

## Integration after GT-04

After GT-02 and GT-03 exist, composition code can:

1. obtain Tier 3 capabilities at startup;
2. provide Tier 1's public entity resolver to the planner;
3. hand the `ExecutionPlan` to a separate executor;
4. give returned facts and observations to an EvidenceResolver;
5. ask Tier 1 for a revision/audit write only after policy justifies it;
6. render the final evidence-backed `GroundedResult` with a deterministic
   template or a constrained LLM explanation.

Those integration steps are explicitly outside the GT-04 implementation.
