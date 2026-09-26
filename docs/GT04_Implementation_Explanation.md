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

## Terms used in this explanation

| Term | Meaning in this project |
| --- | --- |
| **Intent** | A short, approved label for the kind of task the user is asking for. For example, `current_route_status` means the question is about the route now. It does not contain an answer or a tool call. |
| **Intent option** | One of the fixed values the provider is allowed to return. The provider cannot invent a new option. |
| **Entity mention** | A name or description from the user's words, such as “the box” or “front route”. It is not yet a trusted database identifier. |
| **Canonical entity ID** | Tier 1's stable identifier for a known entity, such as `box_01` or `route_ahead`. Plans use this ID instead of the original wording. |
| **Provider** | The interface through which IntentPlanner asks for an intent proposal, one repair, or one constrained reconsideration. Tests use a fake provider; a future adapter may call an LLM. |
| **Raw provider output** | The untrusted data returned by the provider before Pydantic checks it. It may be malformed, incomplete, or contain a disallowed intent. |
| **Capability** | A named Tier 3 ability, such as the `lidar_scan` sensor. A descriptor says its exact name and whether it is a sensor or action. |
| **Evidence operation** | Typed data describing one future request to Tier 1 or Tier 3. It describes work; it is not the result of that work. |
| **Fallback** | A structured safe stopping result. It says why planning cannot continue and may include clarification choices. It does not assert a fact about the environment. |
| **Coverage rule** | A small deterministic wording check used only after the provider returns valid `unsupported`. It can suggest a candidate intent, but it cannot create a plan or invoke a sensor. |

## The approved intent options

The shared `IntentRequest` contract allows exactly these intent values. The
provider proposes one; IntentPlanner validates it; PlanBuilder decides which
typed operations that value requires.

| Intent value | What it means | Entity mention expected? | PlanBuilder behavior |
| --- | --- | --- | --- |
| `current_route_status` | Ask about the present status of a route or path. | Yes, one route mention, such as “front route”. | Resolve the route; request active `status_is` memory and fresh LiDAR when available. |
| `current_object_perception` | Ask about a currently visible object's appearance or perception. | Yes, one object mention, such as “the box”. | Resolve the object; request `camera_detect` when available. |
| `historical_fact_lookup` | Ask what the system knew about an entity in the past. | Yes, one relevant entity mention. | Resolve it; request memory with `active_only=false`; do not request a sensor. |
| `audit_explanation` | Ask why a stored fact or belief was recorded or changed. | Yes, one relevant entity mention in the current contract. | Resolve it; query active facts for that entity and request an audit chain for every fact returned. |
| `current_robot_pose` | Ask where the robot is now. | No. The robot-pose capability targets the robot itself. | Request `robot_pose` when available; do not substitute old memory for a current pose. |
| `environment_action` | Ask the robot to do something, such as move. | The current contract has no safe action-name field. | Return an `unsupported_intent` fallback; do not create or execute an action. |
| `unsupported` | The provider says it cannot classify the request as one of the supported tasks. | No entity is required at this stage. | IntentPlanner runs the bounded coverage review. If the provider reaches PlanBuilder with this value, it becomes a no-plan `unsupported_intent` fallback. |

The entity requirement is intent-specific. For example, “Where are you now?”
can be planned without an entity mention because `robot_pose` means the
robot's pose. “What colour is the box?” needs the box to be resolved before a
camera request can be formed. If an entity-based intent has no mention, the
builder returns `missing_entity`; if it has several independent mentions,
this checkpoint does not guess which one was meant.

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

#### What counts as a valid intent reply?

The provider returns data, not a paragraph that the rest of the system must
interpret. A simplified valid reply for “Can I move forward?” could be:

```json
{
  "intent": "current_route_status",
  "entity_mentions": ["front route"],
  "user_question": "Can I move forward?"
}
```

`intent` must be one of the shared contract's allowed values. The provider
does not return a sensor name, a database query, or an answer such as “Yes,
the route is clear.” `entity_mentions` contains the user-facing names that
later need resolution. The planner checks that `user_question` is still the
original question, so the provider cannot silently substitute a different
request.

For example, `{ "intent": "read_weather" }` is invalid because
`read_weather` is not an allowed intent and the required fields are missing.
The planner gives the provider one repair opportunity. By contrast,
`unsupported` is an allowed value: it means “I do not recognize this as a
supported task,” so it goes through the separate bounded review below rather
than the malformed-output repair path.

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

### Unsupported review: full decision flow

It helps to separate three things that can otherwise sound alike:

1. **`unsupported` is an intent value.** It is the provider's valid
   classification of the user's request. It is not a malformed response and
   does not by itself mean a sensor or memory query should run.
2. **The coverage review is a bounded second look.** It checks whether the
   original wording resembles one of the documented supported request types
   and whether the capability required for that type is available.
3. **A fallback is the planner's safe stopping result.** It contains no plan
   and makes no claim about the physical environment.

The coverage reviewer checks fixed phrase families. For example, route words
such as “blocked” or “ahead” can suggest `current_route_status`, but only when
`lidar_scan` is registered as a sensor. Object-appearance words require
`camera_detect`; pose words require `robot_pose`. History and audit wording
can be considered only when the corresponding Tier 1 interface is configured
as available. Each match has a stable rule ID, such as `route_status_v1`.
These rules only identify candidates; the provider must still return a valid,
restricted intent before PlanBuilder can create a plan.

| Review result | What IntentPlanner does | Result if planning stops |
| --- | --- | --- |
| No candidate | Does not call the provider again. | `unsupported_after_review`; no intent choices, no matched rule IDs, no plan. |
| More than one candidate | Does not ask the provider to choose. | `unsupported_after_review`; includes the candidate intent names in `intent_clarification_choices` and the matching IDs in `matched_rule_ids`; no plan. |
| Exactly one candidate; provider chooses it | Validates the reconsidered reply and accepts it only if it selects that candidate with the original question. | No unsupported fallback; the recovered intent continues to entity resolution and PlanBuilder. |
| Exactly one candidate; provider keeps `unsupported` | Stops without another attempt. | `unsupported_after_review`; includes the matched rule ID; no plan. |
| Exactly one candidate; provider returns malformed data or another intent | Rejects the response; does not repair or reconsider again. | `invalid_provider_output_after_unsupported_review`; includes the matched rule ID; no plan. |
| Exactly one candidate; provider is unavailable | Stops safely. | `provider_unavailable`; includes the matched rule ID that led to the reconsideration; no plan. |

#### What is inside an unsupported fallback?

`PlanningFallback` is a structured object, not just a sentence. It contains:

- `category`: a stable reason code for the stopping point;
- `reason`: a short explanation suitable for logs or later response rendering;
- `safe_result`: a `GroundedResult` marked uncertain, with an uncertainty
  reason but no conclusion, evidence IDs, conflicting IDs, or policy rule;
- `intent_clarification_choices`: supported intent names the user can choose
  between, used only when several coverage candidates matched;
- `matched_rule_ids`: the fixed coverage rules that matched, when a review
  produced candidates; and
- `clarification_candidates`: canonical entity IDs, used for ambiguous
entity resolution rather than intent ambiguity.

The fallback model validates that intent choices are approved intent values,
that a clarification has at least two distinct choices, and that the choices
appear only with `unsupported_after_review`. Rule IDs must also be approved
and unique. Entity IDs are kept in their separate field because, for example,
“which box?” asks the user to choose an entity, while “which task did you
mean?” asks the user to choose an intent.

For example, “Is an obstacle ahead and what color is it?” may match both route
status and object perception. The fallback is conceptually:

```text
category: unsupported_after_review
reason: several supported intent categories matched; clarification is needed
intent_clarification_choices:
  - current_route_status
  - current_object_perception
matched_rule_ids:
  - route_status_v1
  - object_perception_v1
safe_result: uncertain, with no conclusion or evidence IDs
plan: absent
```

The later response layer can turn those choices into a question such as “Are
you asking whether the path is blocked, or what the visible object looks
like?” No sensor or database operation is created until the user clarifies
and a valid intent is planned.

If there is no coverage match, the category is still
`unsupported_after_review`, but the clarification-choice and rule-ID lists
are empty. If one candidate is reconsidered and the provider chooses
`unsupported`, the same category is used with the one matched rule ID retained
for diagnostics. If the provider chooses the supported candidate, the
accepted `IntentPlannerOutcome` retains the matched rule ID alongside the
intent as diagnostic context; the plan itself is built next. A provider
outage during reconsideration uses `provider_unavailable`; malformed or
out-of-set output uses
`invalid_provider_output_after_unsupported_review`.

This differs from `unsupported_intent`, which PlanBuilder uses when it receives
an unsupported intent to build. In the normal configured flow, IntentPlanner
reviews valid unsupported replies first, so they usually stop with
`unsupported_after_review`. Both categories mean no plan and no operations.
They identify different stopping points in Tier 2.

The safe fallback protects against unsupported claims: it records that
planning could not establish a supported task, and gives the response layer
enough structured detail to ask for clarification or explain the limitation.
It does not authorize the LLM to answer from general knowledge, infer a
sensor reading, or make up evidence.

#### Example: recovered route question

The user asks, “Is anything blocking me?” The first provider reply is
`unsupported`. The reviewer finds the route-status rule and confirms that
`lidar_scan` is advertised as a sensor. The provider gets one reconsideration
with the choices `current_route_status` and `unsupported`. If it returns
`current_route_status`, the intent continues through entity resolution and
PlanBuilder like any other valid route question.

If the reviewer finds both route and object-perception wording, it does not
make two plans. It returns a fallback containing the choices
`current_route_status` and `current_object_perception`, plus the rule IDs that
matched. A later response layer can use those choices to ask the user which
part they meant. If the reviewer finds no candidate, such as for “What is the
room temperature?”, the planner returns an unsupported fallback without
calling the provider again.

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

#### Example: entity ambiguity

Suppose the provider returns `current_object_perception` with
`entity_mentions: ["the box"]`. If Tier 1's resolver reports that “the box”
could mean either `box_01` or `box_02`, PlanBuilder returns an
`ambiguous_entity` fallback with both canonical IDs. It does not pick the
first item and does not create a camera operation. If the resolver reports no
matching entity, the result is a `missing_entity` fallback with no plan.

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

In simplified form, that plan describes data like this:

```text
intent: current_route_status
resolved_entity_ids: [route_ahead]
memory_operations:
  - query: subject=route_ahead, predicate=status_is, active_only=true
observation_operations:
  - request: capability=lidar_scan, target=route_ahead
current_state_verifiable: true
```

The fields are requests for work. They are not results. For example, the plan
does not say whether the route is clear; only a later sensor observation and
evidence-handling step can support that conclusion.

#### Example: LiDAR is missing

If the same request arrives while `lidar_scan` is not advertised, the builder
keeps the active memory query for context, creates no observation request,
sets `current_state_verifiable` to `false`, and adds a blocking reason such as
“LiDAR is unavailable, so current route status is unverifiable.” The plan may
still be useful for retrieving stored context, but that context cannot stand
in for a fresh reading about what is ahead now.

If `lidar_scan` is present but registered as an `action`, that is a registry
configuration error. The builder returns an `invalid_configuration` fallback
instead of treating the sensor as merely absent.

#### Example: audit explanation

For “Why do you believe this about the box?”, the builder resolves the box to
`box_01` and describes two linked operations:

```text
memory operation 0: query active facts where subject=box_01
audit operation: for every fact returned by memory operation 0,
                 request its audit chain
```

Tier 1's audit method takes a fact ID, so the later executor first runs the
memory query, then uses each returned fact's ID to retrieve its audit chain.
The plan checks that the audit operation refers to an existing memory
operation and that both target the same entity. The current GT-04 code only
describes these steps; it does not run them or write the final explanation.

#### Example: historical lookup

For “What did you know about the box earlier?”, the builder resolves
`"the box"` to `box_01` and creates a memory query with
`active_only=false`. This asks Tier 1 to include superseded facts, so the
executor can inspect stored history. No camera or LiDAR request is added,
because the question asks about stored past information rather than the live
environment.

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

The two outcomes represent different situations:

```text
PlanningOutcome(plan=...)      → planning succeeded; later code may execute it
PlanningOutcome(fallback=...)  → planning stopped; there are no operations
```

For instance, a resolved `current_route_status` request with LiDAR available
can produce a plan. An ambiguous entity, an invalid provider response after
its one repair, or an unsupported question produces a fallback. The fallback
does not claim anything about the physical world; it records why the agent
cannot safely continue and, when relevant, what the user can clarify.

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
