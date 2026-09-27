# Unsupported Intent Recovery Proposal

## Status

**Discussion draft.** This is a proposed later enhancement, not part of
GT-04's required implementation. GT-04 must retain its current fail-closed
behaviour: a validated `unsupported` intent creates no memory, sensor, or
action operation.

## The limitation

`unsupported` currently has two meanings that look identical at the planning
boundary:

1. The user genuinely asked for something the agent cannot verify, such as
   room temperature when no temperature sensor or fact source exists.
2. The user asked a supported question, but the LLM classified it incorrectly.
   For example, "Is anything in front of me?" could be classified as
   `unsupported` even though `lidar_scan` supports a route-status check.

The second case is a false negative. It is safe because the agent makes no
unsupported claim, but it makes the agent less useful.

## Safety invariant

The proposed improvement must never turn uncertain language into a sensor
call or a factual answer merely because text contains a familiar keyword.

In particular:

- only a validated, allowed intent can create an execution plan;
- a capability registry remains the sole authority for available sensors and
  actions;
- no recovery path can invent a capability, SQL query, entity ID, evidence ID,
  or final environmental conclusion;
- ambiguous recovery ends in clarification or uncertainty, with no execution.

## Proposed approach: bounded unsupported-intent review

Add a later, explicit review step that runs only after a provider returns the
valid `unsupported` intent.

```text
provider returns unsupported
        |
        v
deterministic coverage check against documented phrase families
        |
        +-- no strong match --> unsupported outcome, no operations
        |
        +-- one supported candidate --> constrained reconsideration request
        |                            to the provider
        |                               |
        |                               +-- valid supported intent --> normal
        |                               |   entity resolution and plan build
        |                               |
        |                               +-- unsupported/invalid/ambiguous -->
        |                                   clarification or safe uncertainty
        |
        +-- several candidates --> clarification, no operations
```

The coverage check is deliberately narrow and deterministic. It may use a
small, reviewed map of intent families, for example:

| Phrase family | Candidate intent | Required capability |
| --- | --- | --- |
| route, forward, ahead, blocking | `current_route_status` | `lidar_scan` |
| looks like, see, colour of object | `current_object_perception` | `camera_detect` |
| where are you, current location | `current_robot_pose` | `robot_pose` |
| before, earlier, historical | `historical_fact_lookup` | Tier 1 history |
| why changed, evidence history | `audit_explanation` | Tier 1 audit retrieval |

It is not a general natural-language parser and must not choose an entity or
execute a plan. It only identifies a reason to re-check an `unsupported`
classification.

The reconsideration request must restrict the provider to the candidate intent
or a short candidate set plus `unsupported`. It should include the original
question and no database contents, sensor readings, or secrets.

Example:

```text
Question: "Is anything blocking me?"
Initial provider intent: unsupported
Coverage check: one route-status candidate; lidar_scan is advertised
Reconsideration choices: current_route_status or unsupported
Provider reconsideration: current_route_status, ["front route"]
Result: continue through normal validation, entity resolution, and plan build
```

If the provider continues to return `unsupported`, the agent remains
unsupported. If the phrase map finds both a route and object-perception
candidate, the agent asks the user to clarify rather than selecting one.

## Why this is useful

This improves recall for the exact capability set the project can demonstrate
without weakening grounding. It also creates an evaluation metric that is
useful in a presentation:

- supported-question false-negative rate before and after review;
- number of unsupported requests correctly refused;
- number of recovery attempts that resulted in a validated plan;
- proof that no recovery path executed a tool without a validated intent.

The project can show both reliability and honest limits: it recovers from a
bounded classification error but still refuses when the evidence plan is not
safe.

## Alternatives considered

### Automatically plan from keywords

Rejected. A keyword such as "forward" can occur in unrelated wording, and
automatic planning would bypass the LLM/contract boundary.

### Retry the LLM indefinitely

Rejected. It is costly, non-deterministic, and makes failures hard to explain
or test.

### Use a second unrestricted LLM to judge the first

Not recommended for the core project. It adds cost and variability while still
not guaranteeing grounded behaviour.

### Use a deterministic intent classifier for every question

Potential future option for a small command grammar or emergency route checks.
It is useful only for explicitly supported wording; it does not replace the
LLM's flexibility. It should use the same intent enum and PlanBuilder.

## Decisions needed from the team

1. Is false-negative recovery worth implementing after GT-04 and core
   integration are complete?
2. Which phrase families are precise enough to count as a strong match?
3. Should a single recovered candidate trigger constrained reconsideration, or
   a user clarification first?
4. Should the recovery path be limited to read-only sensor intents initially?
5. Which test cases and metrics should appear in the final evaluation report?

## Recommended rollout

1. Implement GT-04 exactly as specified, including safe `unsupported`.
2. Finish Tier 1/Tier 3 integration and collect a small set of known supported
   paraphrases that the provider misclassifies.
3. Add this review stage as a separate issue with unit and integration tests.
4. Start with `current_route_status`, `current_object_perception`, and
   `current_robot_pose`; do not include environment actions.
5. Keep every recovery decision and final no-execution outcome in structured
   logs for the evaluator dashboard.
