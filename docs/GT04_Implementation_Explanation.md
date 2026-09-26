# GT-04: Intent Planner and Plan Builder

## What this task adds

GT-04 adds the planning part of Tier 2. It takes a user's question, determines
which supported kind of request it represents, and describes the information
needed to handle it.

The result is a typed plan or a safe explanation of why planning stopped. The
plan is data for a later executor. GT-04 does not query the database, call a
sensor, move the robot, or produce a factual answer from evidence.

## Where this fits in the project

The project has three layers:

1. **Tier 1 stores facts and their history.** It can look up facts and audit
   records.
2. **Tier 2 decides what information is needed.** GT-04 implements this
   planning step.
3. **Tier 3 advertises sensors and actions.** GT-04 checks the advertised
   capability list when making a plan.

The flow implemented by GT-04 is:

```text
User question
    ↓
IntentPlanner asks the provider for one intent
    ↓
IntentPlanner validates the reply and may do bounded recovery
    ↓
EntityResolver maps a mentioned name to a canonical ID, when needed
    ↓
PlanBuilder creates a typed plan from fixed rules
    ↓
Later executor may run the plan and collect results
```

The last step belongs to later work. The IntentPlanner and PlanBuilder do not
run the operations themselves.

## The main parts

### IntentProvider

`src/procedural/intent_provider.py` defines the narrow interface used by the
planner. It has separate requests for:

- an initial intent proposal;
- one repair after malformed output; and
- one reconsideration when a valid `unsupported` reply may be a false negative.

The provider returns structured data that the planner treats as untrusted.
Tests use a fake provider with prepared replies, so they do not need a live LLM
or API key. A future provider adapter could call a model, but it must still
return data that meets the shared contract.

### IntentPlanner

`src/procedural/intent_planner.py` handles the provider lifecycle:

1. It rejects a question that is empty after trimming whitespace.
2. It requests one intent proposal.
3. It validates the reply against the shared `IntentRequest` contract. The
   intent must be one of the approved values, extra fields are rejected, and
   the returned question must match the user's original question.
4. If the reply is malformed, it sends one bounded repair request. If the
   repaired reply is also invalid, it returns a structured safe fallback.
5. It sends a valid `unsupported` intent to the bounded review described
   below.

The planner does not ask for repeated repairs. This puts a firm limit on model
retries and ensures invalid output cannot turn into an unstructured plan.
Provider outages also produce a safe fallback. Unexpected programming errors
are not silently labeled as provider outages.

#### Example: malformed reply

The user asks, “Can I move forward?” The provider returns `route_check`, which
is not in the approved intent list. The planner asks for one repair. If the
repair returns `current_route_status` with the same original question, the
planner accepts it. If the repair is still malformed, planning stops with an
`invalid_provider_output_after_repair` fallback and no operations.

### Bounded review of `unsupported`

An `unsupported` intent is a valid response, so it does not enter the malformed
reply repair path. However, a model may misunderstand a supported phrase. The
`IntentCoverageReviewer` in
`src/procedural/intent_coverage_reviewer.py` checks the question against a
small set of documented wording rules and the available sensor capabilities.

For example, “Is anything blocking me?” can match the route-status wording
rule. That rule can suggest `current_route_status` only when `lidar_scan` is
advertised as a sensor. The reviewer does not call LiDAR or create a plan; it
only returns possible intent categories and rule IDs.

- **No candidate:** keep the request unsupported and return no plan.
- **One candidate:** ask the provider once to reconsider between that
  candidate and `unsupported`.
- **Several candidates:** return clarification choices and the matched rule
  IDs. Do not ask the provider to guess and do not make a plan.

The reconsidered reply must pass the normal intent and question checks. It may
choose only the reviewed candidate or `unsupported`. Any other reply fails
safely without another repair or reconsideration.

#### Example: recovered route question

The user asks, “Is anything blocking me?” The first provider reply is
`unsupported`. The reviewer finds the route-status rule and confirms that
`lidar_scan` is advertised as a sensor. The provider gets one reconsideration
with the choices `current_route_status` and `unsupported`. If it returns
`current_route_status`, the intent continues through entity resolution and
PlanBuilder like any other valid route question.

### EntityResolver

`src/procedural/entity_resolver.py` defines the interface Tier 2 uses to map a
user-facing name to a canonical entity ID. For example, it may map “the box”
to `box_01`. The resolver is injected, so procedural code does not depend on a
concrete Tier 1 repository.

PlanBuilder requires one unambiguous entity for entity-based requests. It
does not choose the first of several mentions or candidates:

- one resolved ID can be used in the plan;
- several possible IDs produce an `ambiguous_entity` fallback with candidates
  for clarification;
- no match or no required mention produces a safe fallback; and
- malformed resolver data, such as blank, padded, or duplicate IDs, produces
  an `invalid_configuration` fallback.

Current robot pose does not require an entity mention because its sensor
request targets the robot itself.

### CapabilityValidator

`src/procedural/capability_validator.py` checks the capability descriptors
provided to Tier 2. Names must match exactly, duplicate names are invalid, and
the descriptor kind must match the requested operation. For example,
`lidar_scan` must be advertised as a sensor. A missing sensor and a capability
registered with the wrong kind are treated as different situations.

The registry is treated as fixed input. It is not used to dynamically call
methods or execute tools.

### PlanBuilder

`src/procedural/plan_builder.py` maps the validated intent to required typed
operations using fixed Python rules. It does not use the provider's wording to
choose a sensor, query, or action.

| Intent | PlanBuilder result |
| --- | --- |
| `current_route_status` | Resolve the route, request active memory facts with `status_is`, and add a LiDAR observation if `lidar_scan` is available as a sensor. If it is absent, keep the memory query but mark current status unverifiable. |
| `current_object_perception` | Resolve the object and request `camera_detect` if available. If the camera is absent, mark current perception unverifiable; historical data is not treated as a current camera result. |
| `historical_fact_lookup` | Resolve the entity and request facts with `active_only=false`. Do not request a sensor. |
| `audit_explanation` | Resolve the entity, request its active facts, and specify that the later executor should retrieve an audit chain for every fact returned by that memory query. The plan checks that the audit operation points to an existing query for the same entity. |
| `current_robot_pose` | Request the `robot_pose` sensor if available. Do not use stored memory as a substitute for a current pose. |
| `environment_action` | Return a safe unsupported fallback because GT-04 has no approved safe action name to plan. |
| `unsupported` | Return a safe unsupported fallback with no operations. |

When an observation capability is absent, PlanBuilder can still return the
available context while marking the current state unverifiable. A wrong-kind
capability is an invalid configuration and does not get treated as simple
sensor absence.

#### Example: route-status plan

Suppose the user asks, “Can I move forward?”, the provider selects
`current_route_status`, and EntityResolver maps “front route” to `route_ahead`.
If `lidar_scan` is advertised, PlanBuilder returns data describing:

1. an active memory query for `route_ahead` with predicate `status_is`; and
2. a fresh LiDAR observation request targeting `route_ahead`.

The returned plan does not contain a LiDAR reading. A later executor would run
these requests and pass their results to later evidence-handling components.

## Plans and safe fallbacks

`src/procedural/execution_plan.py` defines the typed operations held in an
`ExecutionPlan`: memory queries, observation requests, audit-chain lookups,
and a future action placeholder. These models describe permitted work; they
do not contain database connections, sensor objects, functions, or provider
objects. An action placeholder, if used later, must require a safety precheck.

`src/procedural/fallback_results.py` defines `PlanningOutcome` and
`PlanningFallback`. An outcome contains exactly one of a plan or a fallback.
A fallback is uncertain and cannot contain a conclusion, evidence IDs,
conflicting IDs, or a policy rule. It can retain entity clarification
candidates, intent clarification choices, and coverage-rule IDs where those
are relevant.

Examples of fallback reasons include invalid provider output, provider
unavailability, unsupported requests, missing or ambiguous entities, unknown
capabilities, unavailable required capabilities, and invalid configuration.
These outcomes let later response code explain why the system cannot safely
continue without inventing facts.

## How the LLM and deterministic code divide the work

The provider helps recognize the request category from flexible language. It
does not choose tools freely, make a database query, decide what the evidence
means, or write the final factual answer.

Python validates the provider reply, applies the retry and recovery limits,
resolves names through an injected interface, checks capabilities, and builds
the plan from fixed mappings. A later executor is responsible for running
approved operations. A later evidence resolver and response layer are
responsible for drawing and explaining conclusions from returned evidence.

## Main implementation files

- `src/procedural/intent_provider.py` — provider interface and provider
  availability error.
- `src/procedural/intent_planner.py` — proposal validation, one repair, and
  bounded unsupported review.
- `src/procedural/intent_coverage_reviewer.py` — documented wording rules,
  capability gates, and rule IDs.
- `src/procedural/entity_resolver.py` — injected entity-resolution interface.
- `src/procedural/capability_validator.py` — capability registry checks.
- `src/procedural/plan_builder.py` — fixed intent-to-operation mapping.
- `src/procedural/execution_plan.py` — typed, non-executing plan models.
- `src/procedural/fallback_results.py` — safe planning outcomes and
  clarification data.
- `tests/unit/procedural/` — offline tests using fake providers, resolvers,
  and capability registries.

## Testing and project boundary

GT-04 tests check normal and failure paths, retry limits, unsupported recovery,
entity resolution, each plan type, capability absence and kind mismatches,
safe fallback contents, and invalid audit references. Boundary tests check
that procedural code does not import Tier 1 repositories, Tier 3 environments,
provider SDKs, or the later EvidenceResolver.

The tests use fakes and run without a live LLM, database lookup, sensor call,
or action. An executor, evidence comparison, memory writes, and final
user-facing answer are later integration work, outside this GT-04 scope.
