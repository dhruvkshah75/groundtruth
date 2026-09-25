# Architecture Decision Analysis: GroundTruth

Analysis of trade-offs in the current proposed design, alternatives, and edge cases to resolve before implementing Tier 2/3.

---

## 1. SQLite + NetworkX as Dual Storage

**Current proposal:** SQLite for the permanent provenance log, NetworkX for the live active-belief graph.

**Pros**
- Zero network latency, ACID safety, single-file portability.
- Clean separation: history (SQL) vs. live view (graph).

**Risks**
- Two sources of truth must stay in sync — a crash between writing SQLite and updating the graph causes silent drift, with no repair mechanism defined.
- Rebuilding the full graph on every insert doesn't scale past a small fact count.
- SQLite is single-writer — concurrent sensor writes or a live dashboard reader will hit `database is locked`.

**Alternatives**
- SQLite only, simulating traversal via recursive/adjacency SQL.
- An embedded graph database (e.g. Kùzu) as the single source of truth.
- **Event-sourcing**: SQLite is the only authoritative store (append-only log); NetworkX is a disposable cache, always rebuildable from SQLite, never treated as truth.

```python
# Event-sourcing pattern: graph is derived, never authoritative
class DatabaseManager:
    def record_fact(self, assertion: FactAssertion) -> StoredFact:
        stored = self._insert_fact(assertion)   # SQLite = source of truth
        self._graph_cache = None                # invalidate derived cache
        return stored

    def get_active_graph(self) -> "networkx.DiGraph":
        if self._graph_cache is None:
            self._graph_cache = self._rebuild_graph_from_sqlite()
        return self._graph_cache

    def _rebuild_graph_from_sqlite(self):
        import networkx as nx
        g = nx.DiGraph()
        for row in self.get_active_memories():  # WHERE superseded_by IS NULL
            g.add_edge(row["subject"], row["object"], predicate=row["predicate"],
                       confidence=row["confidence_score"], fact_id=row["fact_id"])
        return g
```

---

## 2. Strict Three-Tier Isolation

**Current proposal:** Tier 1 and Tier 3 never call each other; Tier 2 is the sole orchestrator.

**Pros**
- Prevents merge conflicts across a 4-person team.
- Enforces a clean ports-and-adapters style architecture.

**Risks**
- Every interaction round-trips through Tier 2, adding LLM latency even for trivial checks.
- Safety-critical reactive behavior (e.g. "dodge now") shouldn't have to wait on an LLM reasoning loop.

**Alternative:** Split Tier 2 into a fast deterministic path and a slow LLM path.

```python
class Tier2Orchestrator:
    SAFETY_THRESHOLD_CM = 15

    def handle_observation(self, obs: SensorObservation):
        # Fast path: no LLM, deterministic safety check
        if obs.capability == "lidar_scan":
            distance = obs.measurements.get("nearest_distance_cm")
            if distance is not None and distance < self.SAFETY_THRESHOLD_CM:
                return self._emergency_stop(obs)

        # Slow path: full ReAct reasoning for ambiguous/novel conflicts
        return self._llm_reason(obs)

    def _emergency_stop(self, obs: SensorObservation):
        return AgentResponse(
            answer="Obstacle too close — halting.",
            evidence_ids=[obs.observation_id],
            uncertainty=False,
        )
```

---

## 3. Pydantic Contracts as the Only Inter-Tier Language

**Pros:** Type safety, testability, prevents tight coupling between tiers.

**Risks**
- Contract changes ripple across all three tiers — a bottleneck once the team is moving fast.
- `context` / `evidence` as loose `dict[str, JsonValue]` invites silent key-naming inconsistency (`"location"` vs `"loc"`).

**Alternative:** Version contracts explicitly, and give common `context` keys a typed sub-schema with a fallback dict for genuine extensions.

```python
from pydantic import BaseModel, Field
from typing import Literal

class SpatialContext(BaseModel):
    location: str | None = None
    frame_of_reference: str | None = None
    world_version: int | None = None

class FactAssertionV1(BaseModel):
    version: Literal["v1"] = "v1"
    subject: str
    predicate: str
    object: str
    source_agent: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    context: SpatialContext = Field(default_factory=SpatialContext)
    extra_context: dict[str, "JsonValue"] = Field(default_factory=dict)  # true escape hatch
```

---

## 4. Direct Structured Retrieval Instead of RAG

**Pros:** Deterministic, auditable, no hallucinated retrieval.

**Risks**
- Doesn't tolerate fuzzy input — `"box_01"` vs `"Box_01"` vs `"the box"` silently fails to match.
- If the LLM has to guess the exact subject string to query, you've reintroduced hallucination risk through the query itself.

**Alternative:** Keep direct retrieval as primary, add a small canonicalization/alias layer — not full RAG.

```python
ALIAS_TABLE = {
    "the box": "box_01",
    "box": "box_01",
    "front sensor": "lidar_sensor",
}

def canonicalize(term: str) -> str:
    key = term.strip().lower()
    return ALIAS_TABLE.get(key, term)

def resolve_query(raw_subject: str) -> FactQuery:
    return FactQuery(subject=canonicalize(raw_subject))
```

RAG stays reserved strictly for unstructured content (e.g. a maintenance manual) and is never the fallback when structured lookup returns empty.

---

## 5. Capability Allowlist / "Unsupported" Fallback

**Pros:** The strongest anti-hallucination mechanism in the design.

**Risks**
- Too narrow an allowlist makes the agent feel useless — everything returns "unsupported."
- `QueryPlan` is a single `kind` enum — can't represent a compound question where only part is answerable.

**Alternative:** Allow a list of sub-plans so compound questions get partial, honestly-labeled answers.

```python
class QueryPlan(BaseModel):
    kind: Literal["observe", "memory", "audit", "action", "unsupported"]
    capability: str | None = None
    subject: str | None = None
    predicate: str | None = None
    reason: str

class CompoundQueryPlan(BaseModel):
    sub_plans: list[QueryPlan]

def answer_compound(plan: CompoundQueryPlan) -> str:
    parts = []
    for sub in plan.sub_plans:
        if sub.kind == "unsupported":
            parts.append(f"Cannot verify: {sub.reason}")
        else:
            parts.append(execute_plan(sub))  # existing single-plan logic
    return " ".join(parts)
```

---

## 6. Confidence Decay / Doxastic Fading (Stretch Feature)

**Risk:** A single decay function applied to everything will incorrectly erode confidence in facts that are true for a long time (e.g. `wall_position`) at the same rate as facts that change often (e.g. `robot_location`).

**Alternative:** Per-predicate decay rates with a floor, not a single global decay curve.

```python
import math
from datetime import datetime, timezone

DECAY_RATE_PER_HOUR = {
    "located_at": 0.15,          # moving objects — decays fast
    "wall_position": 0.001,      # static facts — barely decays
    "perceived_color_is": 0.05,
}
DEFAULT_DECAY_RATE = 0.05
MIN_CONFIDENCE_FLOOR = 0.1

def decayed_confidence(original: float, predicate: str, observed_at: datetime) -> float:
    rate = DECAY_RATE_PER_HOUR.get(predicate, DEFAULT_DECAY_RATE)
    hours_elapsed = (datetime.now(timezone.utc) - observed_at).total_seconds() / 3600
    decayed = original * math.exp(-rate * hours_elapsed)
    return max(decayed, MIN_CONFIDENCE_FLOOR)
```
## 7. Derived / Computed Queries (Not Yet Covered)

**Gap identified:** Questions like *"what was the shortest path to your current position?"* don't fit any of the five existing `QueryPlan` kinds (`observe`, `memory`, `audit`, `action`, `unsupported`). They require **computing something new from stored facts**, not returning a fact directly.

### Why this is a distinct category

- It's not `memory` — there's no single stored row that answers it; it requires reconstructing a sequence of facts over time and running a computation (e.g. a graph traversal) on top of them.
- It's not `observe` — no live sensor reading answers it.
- It's not `audit` — audit explains *why a belief changed*, not *what the optimal path was*.

### Hidden ambiguity in this kind of question

The same question can silently split into two very different answers:

| Interpretation | Answerable from | Notes |
|---|---|---|
| "What path did you **actually** take" (descriptive/historical) | Tier 1 alone — walk the chain of `located_at` facts over time | Fully groundable |
| "What is the **shortest/optimal** path" (planning/normative) | Requires a route/cost graph from Tier 3 | May be `unsupported` if no such map exists |

If the agent doesn't separate these, it risks presenting a computed guess about optimality as a grounded fact — the exact hallucination the guardrails exist to prevent.

### Proposed fix: add a `derive` plan kind

```python
class QueryPlan(BaseModel):
    kind: Literal["observe", "memory", "audit", "action", "derive", "unsupported"]
    capability: str | None = None
    subject: str | None = None
    predicate: str | None = None
    derivation: str | None = None   # e.g. "shortest_path", "path_taken", "distance_traveled"
    reason: str
```

`derive` plans are computed deterministically by Tier 2 over Tier 1 data — never guessed by the LLM, and never computed inside Tier 1 itself (Tier 1's role stays "store and return facts," not "run algorithms").

```python
def handle_derive_plan(plan: QueryPlan, db: DatabaseManager) -> AgentResponse:
    if plan.derivation == "path_taken":
        history = db.query_facts(FactQuery(
            subject="robot_01", predicate="located_at", active_only=False
        ))
        ordered = sorted(history, key=lambda f: f.created_at)
        route = [f.object for f in ordered]
        return AgentResponse(
            answer=f"Path taken: {' -> '.join(route)}",
            evidence_ids=[f.fact_id for f in ordered],
            uncertainty=False,
        )

    if plan.derivation == "shortest_path":
        if not env.has_capability("route_graph"):
            return AgentResponse(
                answer="I cannot compute the shortest path — no route map is available.",
                evidence_ids=[],
                uncertainty=True,
            )
        # else: run nx.shortest_path on the environment's route graph
```

### Edge cases

- **Sparse history:** infrequent position logging makes "path taken" an interpolation, not a certainty — should lower confidence rather than present it as exact.
- **Historical vs. active facts:** derivation queries need `active_only=False` on `FactQuery` — most existing examples only query current state, so this must be explicit.
- **Optimality requires a cost model Tier 1 doesn't own.** "Shortest" implies distance/weight data, which is environment (Tier 3) knowledge, not epistemic fact storage (Tier 1). Missing this data means an honest partial `unsupported`, not a best-effort LLM estimate.
- **Scope creep risk:** every new derivation type (path taken, distance traveled, average confidence over time, etc.) is tempting to bolt directly into Tier 2's orchestration code. Decide early whether these live in a dedicated `src/derivations/` module with their own tests, rather than growing ad hoc inside the orchestrator.

---

## Summary Table (updated)

| # | Decision | Main Risk | Suggested Mitigation |
|---|----------|-----------|----------------------|
| 1 | SQLite + NetworkX | Sync drift, rebuild cost | Treat graph as derived cache only |
| 2 | Strict tier isolation | LLM latency on trivial/safety cases | Fast deterministic path + slow LLM path |
| 3 | Pydantic contracts | Ripple effect on changes | Version contracts, typed sub-schemas |
| 4 | No RAG | Fuzzy input fails silently | Small alias/canonicalization table |
| 5 | Capability allowlist | All-or-nothing unsupported answers | Compound `QueryPlan` with sub-plans |
| 6 | Confidence decay | Wrong decay rate erodes stable facts | Per-predicate decay rates + floor |
| 7 | Derived/computed queries | No plan kind covers computation over facts | Add `derive` plan kind, split descriptive vs. optimal interpretations |