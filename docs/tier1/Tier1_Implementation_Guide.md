# Tier 1 Implementation Guide

## What Tier 1 is responsible for

Tier 1 is the evidence memory of GroundTruth. It remembers claims, sources, times, confidence, revisions, and the active relationship graph.

It is deliberately **not** a sensor layer and not an LLM layer. Tier 1 never reads LiDAR, sees camera pixels, calls Groq, or decides that one source should win a conflict. It only stores and returns evidence accurately.

For the full system design, read [Architecture Decisions](../Architecture_Decisions.md) first.

## The two Tier 1 stores

### SQLite: permanent evidence ledger

SQLite is the authoritative source for every assertion the agent has received. It survives restarts and answers questions about sources, history, timestamps, and belief changes.

Example records:

```text
fact_map_001:   route_A status_is clear
                source=static_map, observed=09:00, confidence=0.90

fact_lidar_002: route_A status_is blocked
                source=lidar_sensor, observed=10:00, confidence=0.99
```

The second record does not erase the first one. It changes the current operational state by linking the old record to the new one and adding an audit event describing why.

### NetworkX: active relationship cache

NetworkX contains only active relationships:

```text
robot_01 -- located_at --> room_101
box_01   -- located_at --> room_101
route_A  -- status_is --> blocked
```

It is useful for relationship traversal and visualisation. It is not permanent and it is not allowed to become a second source of truth.

## Source-of-truth and cache rule

```text
SQLite write succeeds
    ↓
mark graph cache stale
    ↓
rebuild graph from active SQLite facts only when needed
```

Do not update a graph edge first and hope to update SQLite later. If SQLite fails, there is no valid new memory state. After restart, rebuild the graph from SQLite.

## Fact lifecycle

### 1. Store an assertion

Tier 2 sends Tier 1 a validated `FactAssertion`.

```text
subject: route_A
predicate: status_is
object: blocked
source_agent: lidar_sensor
confidence_score: 0.99
observed_at: 10:00 UTC
```

Tier 1 assigns a UUID, records the ingestion time, persists the claim, and returns `StoredFact`.

### 2. Preserve a revision

When Tier 2 decides that a new claim replaces an active belief:

```text
1. Insert replacement fact.
2. Set predecessor.superseded_by to replacement fact ID.
3. Insert audit event with reason and policy rule.
4. Commit one transaction.
5. Invalidate graph cache.
```

The assertion itself stays historically intact. Only its active status changes through the revision link.

### 3. Return evidence, not conclusions

Tier 1 returns facts, aliases, graph paths, and audit history. It must not say “LiDAR wins” or decide that a path is safe. Those are Tier 2 policy decisions.

## Suggested storage shape

Start with three simple tables.

### `facts`

```text
fact_id             UUID/TEXT primary key
subject             canonical entity ID
predicate           controlled predicate string
object              canonical value/entity ID
source_agent        source identifier
confidence_score    0.0 to 1.0
observed_at         timezone-aware UTC time
created_at          ingestion time
context_json        scope/perspective metadata
evidence_json       raw evidence reference/details
superseded_by       nullable successor fact ID
```

### `audit_events`

```text
event_id
old_fact_id
new_fact_id
event_type
reason
policy_rule
created_at
```

### `aliases`

```text
alias
canonical_entity_id
```

An alias may map to more than one entity. This is useful because `the box` may be ambiguous and must not silently become `box_01`.

## Public Tier 1 API

Tier 2 uses public methods and Pydantic contracts. It must never execute SQL directly.

```python
record_fact(assertion: FactAssertion) -> StoredFact
query_facts(query: FactQuery) -> list[StoredFact]
record_revision(revision: BeliefRevision) -> AuditEvent
get_audit_chain(fact_id: UUID) -> AuditTrail
resolve_entity(mention: str) -> EntityResolution
get_active_graph() -> nx.MultiDiGraph
```

The implementation can use different method names, but equivalent capabilities are required.

## Query routing: SQLite or graph?

There is no fixed sequence where Tier 1 always tries graph and then database. The query itself chooses the storage path.

| Request | Use | Example |
| --- | --- | --- |
| Exact active fact | SQLite index | “What is route_A status?” |
| Historical fact | SQLite | “What did the map say at 09:00?” |
| Revision/audit explanation | SQLite | “Why was the map belief replaced?” |
| Active relationship traversal | NetworkX | “What is currently in room_101?” |
| Relationship plus provenance | Graph, then SQLite | “How is robot_01 connected to box_01, and why?” |

SQLite is best for exact values and history. NetworkX is best for moving through many active relationships. If NetworkX identifies a useful active edge, its `fact_id` can be used to load detailed provenance from SQLite.

## Entity resolution

Tier 1 is responsible for converting user-friendly names into canonical identifiers.

```text
"front route" -> route_ahead
"blue box" -> box_01
"the box" -> box_01, box_02
```

The return must make ambiguity explicit:

```text
resolved:  one canonical ID
ambiguous: several candidate IDs
missing:   no known ID
```

Tier 2 decides how to speak to the user. For an ambiguous result it should ask a clarification question; Tier 1 must not choose a candidate on its own.

## Indexes and performance

The first important indexes should support:

```text
active fact lookup:  subject + predicate + superseded_by
history lookup:      subject + predicate + observed_at
source timeline:     source_agent + observed_at
```

The project does not need a distributed database. SQLite is local and fast enough for the expected fact count. Keep transactions short and route writes through the Tier 1 memory service so dashboard reads and writes do not compete unnecessarily.

## Minimum tests

1. Store and retrieve a complete fact.
2. Reject invalid confidence, timestamps, and incomplete facts.
3. Store two conflicting sourced claims without overwriting either.
4. Record a revision and expose predecessor, successor, reason, and policy rule.
5. Query active facts separately from historical facts.
6. Resolve aliases as one match, many matches, and no match.
7. Rebuild the active graph after invalidating its cache.
8. Verify every active graph edge points to an active SQLite fact.
