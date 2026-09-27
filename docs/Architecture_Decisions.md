# GroundTruth Architecture Decisions

## What this document is

This is the current implementation guide for GroundTruth. Read it before building Tier 1 or Tier 2.

The original [I, Agent masterplan](I_Agent_Masterplan.md) explains the assignment vision. The [alternatives analysis](Potential%20Issues%20vs%20Alternatives.md) records risks and possible designs. This document records the design selected for implementation so teammates do not make conflicting decisions.

## The problem we are solving

An LLM can write a convincing answer even when an old map, a current sensor, and a user statement disagree. GroundTruth prevents that by separating:

```text
what has been claimed before       -> Tier 1 memory
what is observed right now         -> Tier 3 environment
how evidence is compared/used      -> Tier 2 policy and orchestration
```

The agent must be able to say not only what it believes, but also which evidence caused that belief and why a previous belief was replaced.

## The central decision

> The LLM is a bounded language interface. It is not the authority on facts.

The LLM may understand a user's wording and write a friendly explanation. Deterministic Python code retrieves evidence, validates tool use, chooses the operational belief, records revisions, and refuses unsupported claims.

This is important because the LLM can be probabilistic. Given the same sentence twice, it may produce slightly different wording or a different tool plan. SQLite queries, sensor calculations, and conflict rules must instead produce repeatable results that can be tested and audited.

## Architecture at a glance

```text
                         ┌────────────────────────────────────┐
User question ─────────► │ Tier 2: LLM + deterministic policy │
                         │ understand, validate, compare      │
                         └──────────────┬─────────────────────┘
                                        │
                 ┌──────────────────────┴──────────────────────┐
                 ▼                                             ▼
┌─────────────────────────────────┐       ┌─────────────────────────────────┐
│ Tier 1: evidence memory         │       │ Tier 3: mock environment        │
│ SQLite facts/audits              │       │ robot, rooms, obstacles, boxes  │
│ NetworkX active graph cache      │       │ sensors and permitted actions   │
└─────────────────────────────────┘       └─────────────────────────────────┘
```

Tier 1 and Tier 3 must never import or call each other. Tier 2 is the only bridge.

## Approved decision 1: SQLite is the source of truth

SQLite is the permanent evidence ledger. It stores every assertion the agent receives:

```text
09:00  static map: route_A is clear
10:00  LiDAR: route_A is blocked at 12 cm
11:00  LiDAR: route_A is clear again after the obstacle moved
```

The database keeps all three assertions, including their source, confidence, observed time, and revision links. The old map assertion is not deleted just because the current route is blocked. It explains why the agent initially believed the route was clear and shows that the blockage may have been temporary.

SQLite is chosen because it is local, durable, simple to test with `:memory:`, and supports exact indexed queries. A database query is normally much faster than an LLM call.

### Required storage rule

Facts are append-only assertions. A revision works like this:

```text
1. Insert the new LiDAR assertion.
2. Link the old map assertion to the new fact using `superseded_by`.
3. Insert an audit event explaining the policy rule.
4. Commit all changes in one SQLite transaction.
```

The historical assertion fields must not be rewritten to make history fit the latest answer. The controlled supersession link is the only update needed to show that an old fact is no longer operationally active.

## Approved decision 2: NetworkX is a derived cache, not another database

NetworkX is useful because the active beliefs naturally form a graph:

```text
robot_01 ── located_at ──► room_101
box_01   ── located_at ──► room_101
route_A  ── status_is ──► blocked
```

Nodes are entities or values, such as `robot_01`, `room_101`, `box_01`, and `blocked`. Edges are active fact relationships, such as `located_at` or `status_is`. Each edge carries the active fact ID, source, confidence, and observation time.

The graph is not the permanent truth. It contains only currently active relationships and is stored in process memory. If the program stops, the graph can be rebuilt from SQLite.

### Cache lifecycle

```text
SQLite fact/revision transaction succeeds
    ↓
Graph cache is marked stale
    ↓
Next graph traversal/dashboard request rebuilds active edges from SQLite
    ↓
Later graph requests reuse the rebuilt graph
```

This avoids a second source of truth and prevents a crash from leaving a permanent graph/database mismatch.

## Approved decision 3: structured retrieval, not core RAG

RAG searches text for semantically similar passages. It is useful for a long manual or an incident report, but it is unsafe as the main fact system.

For example, a RAG search for “is the route clear?” might retrieve a semantically similar old map sentence saying the route was clear, while the correct active fact is a newer LiDAR observation saying blocked. Similarity is not provenance, freshness, or truth.

GroundTruth instead uses structured fields:

```text
subject = route_A
predicate = status_is
active_only = true
context = current robot/location/world version
```

SQLite then retrieves exact matching facts. This is deterministic and auditable.

### Handling natural language without RAG

Natural language may say `the box`, `blue box`, or `box_01`. Tier 1 keeps a small alias/entity registry:

```text
"front route" -> route_ahead
"blue box"    -> box_01
"the box"     -> [box_01, box_02]
```

One match means the agent can continue. Multiple matches mean Tier 2 asks a clarification question. No match means the agent reports no matching known entity. A controlled lexical candidate search may be added later, but it must never silently choose an ambiguous entity.

RAG may be added later only to suggest candidates from unstructured documents. It must not automatically select a fact, decide a conflict, or replace a live observation.

## Approved decision 4: intent-first planning

The LLM should not freely invent an arbitrary sequence of database and sensor calls. It produces a small `IntentRequest` from an allowed list:

```text
current_route_status
current_object_perception
historical_fact_lookup
audit_explanation
current_robot_pose
environment_action
unsupported
```

For example:

```text
User: “Can I move forward?”
LLM: current_route_status(route_ahead)
```

The deterministic Python `PlanBuilder` expands this intent into mandatory evidence operations:

```text
current_route_status
    -> query active route status in Tier 1
    -> request fresh lidar_scan in Tier 3 when it is available
```

This is safer than trusting an LLM-created tool list. The LLM cannot accidentally answer a present-tense safety question using only an old static map because Python requires a fresh relevant sensor reading.

## Approved decision 5: default-deny fallback behavior

The agent must never fall back from a failed plan to a normal ungrounded LLM answer.

| Failure | Deterministic response |
| --- | --- |
| LLM returns malformed structured output | Give it one bounded repair attempt using validation errors. A second failure returns uncertainty. |
| LLM requests an unknown capability | Reject it. Do not call an imaginary sensor. |
| User entity has no known match | Report no matching known entity/evidence. |
| Entity has multiple plausible matches | Ask the user to clarify. |
| A current-state request has no fresh sensor capability | Explain that current state cannot be verified. |
| Tier 1 returns no relevant facts | Return no-evidence/uncertain result. |
| Evidence conflicts | Apply deterministic policy and record the outcome. |

An answer about the simulated environment is valid only when it carries real fact IDs or observation IDs. No evidence ID means the response must be uncertain or unsupported.

## Approved decision 6: deterministic conflict policy

Tier 2 owns conflict policy. Tier 1 stores and retrieves claims; Tier 3 observes the world; neither decides which claim wins.

For a current dynamic-state question, Tier 2 applies this sequence:

```text
1. Are the facts about the same entity and context?
2. Is there a fresh direct observation for this dynamic question?
3. Is the observation relevant to the user’s requested perspective/location?
4. Compare source type, freshness, and confidence.
5. Select an operational belief or preserve separate perspectives.
6. Record the decision rule and evidence IDs.
```

Initial source ordering for comparable claims is:

```text
fresh direct sensor > user assertion > maintenance/history record > static map
```

This is not a universal rule. A camera’s apparent colour under yellow lighting and a maintenance record of painted colour are different claims and should be presented separately, not forced into a conflict.

## Deterministic versus LLM work

| Step | LLM-assisted | Deterministic Python |
| --- | --- | --- |
| Interpret flexible user wording | Yes | Validates allowed intent only |
| Propose an intent | Yes | Rejects unknown/malformed intent |
| Resolve entity aliases | No | Yes |
| Build exact tool operations | No | Yes |
| Retrieve facts and observations | No | Yes |
| Compare evidence and revise beliefs | No | Yes |
| Produce natural language | Yes, optional | Template fallback for tests |

Deterministic code is not magic: it cannot prove a broken real sensor correct or understand every sentence perfectly. It gives a safe guarantee for supported cases: the same evidence and policy always produce the same operational result, and missing evidence never turns into an invented fact.

## The exact flow after a user question

```text
1. Receive user question.
2. LLM/function-calling adapter produces IntentRequest.
3. Pydantic validates fields and allowed intent values.
4. Tier 1 resolves user mentions to one canonical entity, many candidates, or none.
5. PlanBuilder creates an ExecutionPlan with mandatory FactQuery/ObservationRequest steps.
6. Capability validator checks that Tier 3 supports each requested capability.
7. Executor calls Tier 1 and Tier 3 through their public APIs.
8. EvidenceResolver creates GroundedResult: conclusion, alternatives, evidence IDs, policy rule, uncertainty.
9. If warranted, Tier 2 asks Tier 1 to record the new fact and revision/audit event.
10. LLM or deterministic template turns GroundedResult into a response.
11. ResponseGuard rejects an unsupported factual answer without evidence IDs.
```

## When SQLite and NetworkX are used

There is no fixed “graph first, database fallback” rule. Tier 1 chooses based on what the question needs.

| User need | Best path | Why |
| --- | --- | --- |
| “What is route_A’s active status?” | Indexed SQLite lookup | It is an exact fact lookup, and SQLite knows which fact is active. |
| “What did you believe at 10:00?” | SQLite historical query | The active graph intentionally does not retain old beliefs. |
| “Why did you change your mind?” | SQLite fact + audit query | Source, timestamp, revision reason, and old facts are stored there. |
| “What active objects are in room_101?” | NetworkX traversal | It follows active `located_at` edges efficiently. |
| “How is robot_01 related to box_01?” | NetworkX traversal, then SQLite | Graph finds the current connection; SQLite provides provenance for explanation. |
| “Show current beliefs in the dashboard.” | NetworkX graph | It is already a visual model of active relationships. |

For this project size, SQLite exact lookups and NetworkX in-memory traversals are very fast. LLM inference is usually the slower operation, which is why Tier 2 sends only a small focused evidence result to the LLM.

## Performance and consistency rules

- Index exact active fact lookups, source/time queries, and historical fact queries.
- Keep write transactions short.
- Route writes through one Tier 1 memory service; do not let the dashboard or Tier 3 write directly.
- Invalidate, rather than eagerly rebuild, the graph after every write.
- Use a fresh in-memory SQLite database for each automated test.
- Return a compact `MemorySnapshot` or `GroundedResult`, never the full database.

## Scope order

Build in this order:

1. Shared Pydantic contracts and public interfaces.
2. SQLite fact ledger, audit events, aliases, revisions, and indexes.
3. Tier 1 exact/history queries and graph rebuild tests.
4. Tier 2 intent validation, PlanBuilder, and response guard using mocked Tier 1/Tier 3 responses.
5. Tier 3 mock LiDAR/camera environment.
6. Deterministic Scenario A and Scenario B tests.
7. LLM tool-calling adapter and optional dashboard.

Confidence decay, compound plans, `derive` queries, RAG over documents, learned trust, and real hardware are later enhancements, not core dependencies.
