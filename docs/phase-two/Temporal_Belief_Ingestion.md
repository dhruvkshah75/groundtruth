# Temporal Belief Ingestion and Epistemic Summaries (P2-05)

- **Issue:** [#27: [P2-05] Ingest sensor observations into time-aware beliefs and auditable revisions](https://github.com/dhruvkshah75/groundtruth/issues/27)
- **Pull Request:** [PR #40: feat(p2-05): ingest sensor observations into time-aware beliefs and auditable revisions](https://github.com/dhruvkshah75/groundtruth/pull/40)
- **Branch:** `fix/issue-27`
- **Lane:** Temporal Memory / Tier 2 Integration
- **Status:** Complete (Review Changes Implemented)
- **Prerequisites:**
  - Issue [#21](https://github.com/dhruvkshah75/groundtruth/issues/21) Contracts (`src/contracts/simulation.py`, `src/contracts/models.py`)
  - Issue [#22](https://github.com/dhruvkshah75/groundtruth/issues/22) Simulation Engine (`src/sensorimotor/simulation_engine.py`)
  - Issue [#23](https://github.com/dhruvkshah75/groundtruth/issues/23) Warehouse Sensors (`src/sensorimotor/warehouse_sensors.py`)
  - Issue [#24](https://github.com/dhruvkshah75/groundtruth/issues/24) Observation Ledger (`src/declarative/memory_repository.py`)
  - Tier 1 Declarative Persistence & Projection (`src/declarative/active_graph.py`)

---

## 1. Overview and System Context

The **Temporal Belief Ingestion Service** (`TemporalBeliefIngestionService` in `src/procedural/temporal_belief_ingestion.py`) serves as the Tier 2 bridge between raw timestamped sensor acquisitions and GroundTruth's explainable belief ledger and active knowledge graph.

```mermaid
flowchart TD
    subgraph Tier 3: Sensors
        S["WarehouseSensors / Robot"] -->|ObservationSample| T2[TemporalBeliefIngestionService]
    end

    subgraph Tier 2: Procedural Logic
        T2 -->|1. Store Raw Sample| OL[Observation Ledger Writer]
        T2 -->|2. Check Per-Effect Idempotence| FIM[Evidence sample_id Marker per Belief]
        T2 -->|3. Evaluate Zone & Route Map| ZM[StationZoneMap & StaticRouteMap]
        T2 -->|4. Scope Predecessors by Run & Room| SC[SpatialContext Scoping]
        T2 -->|5. Order Against Latest Ledger Evidence| OE[Latest Applicable Acquisition]
        T2 -->|6. Formulate Facts / Revisions| FR[FactAssertion & BeliefRevision]
    end

    subgraph Tier 1: Declarative Persistence
        OL --> DB[("SQLite: observation_samples & detections")]
        FR --> MR[("SQLite: facts & audit_events")]
        MR --> AG[Active NetworkX MultiDiGraph Projection]
    end

    subgraph Downstream Consumers
        T2 -->|EntityBeliefSummary| P208[P2-08: Temporal Questions]
        T2 -->|RouteBeliefSummary| P206[P2-06: Patrol & Safety]
        AG -->|Graph Projection| P211[P2-11: UI Timeline & Graph]
    end
```

### Strict Tier 2 Boundaries
As mandated by GroundTruth's three-tier epistemic architecture:
- **No Concrete Tier Imports:** `temporal_belief_ingestion.py` imports no SQLite, NetworkX, `src.declarative`, `src.sensorimotor`, or LLM provider code.
- **Protocol-Driven Interaction:** Consumes `FactRepositoryProtocol`, `ObservationLedgerWriter`, and `ObservationLedgerReader` protocols. Concrete instances (`MemoryRepository`, `GraphSyncedRepository`) are wired at the composition boundary.
- **Evidence Purity:** Never accesses hidden world state (`SimulationSnapshot.entities`, `ScenarioDefinition`, future events) or presenter playback pacing. All reasoning is derived exclusively from validated `ObservationSample` envelopes and static declarative maps.

---

## 2. Deterministic Observation-to-Belief Policies

### A. Station Zone Membership & Location Policy
Camera detections report an object ID, apparent color, bounding box, and coordinate point in map centimeters.
1. **Static Zone Mapping:** Station membership is resolved deterministically against an explicitly configured `StationZoneMap`. Boundaries are inclusive (`x_min <= x <= x_max` and `y_min <= y <= y_max`).
2. **Ambiguity and Boundary Rules:**
   - **Matched (1 zone):** Creates or updates the `located_at` belief for the parcel.
   - **Unmapped (0 zones):** Retains the raw sample in the observation ledger, but creates no station fact. Marks `status="unmapped"` and sets `uncertainty=True`.
   - **Ambiguous (>1 overlapping zones):** Creates no fact. Marks `status="ambiguous"` and sets `uncertainty=True`.
3. **Sub-Zone Jitter & Repeated Sightings:** Repeated sightings confirming the same zone update last-seen and verification metadata without creating duplicate active facts or revision audit events. Coordinate changes within the same station zone do not inflate SQLite or NetworkX.
4. **Inter-Zone Relocation Revisions:** When a newer comparable observation maps the entity to a different station zone, the service executes an atomic Tier 1 belief revision linking predecessor, successor, and a justified audit explanation.

### B. Front LiDAR and Route Status Policy
1. **Pose and Corridor Matching:** Front LiDAR observations are mapped to a named route only when the robot's pose matches the segment geometry within corridor tolerances and the robot's direction matches the route's heading.
2. **Ambiguity Gate:** If zero routes match (e.g. facing perpendicular to route or in unmapped area) or multiple routes match, the observation is retained in the ledger, no route fact is altered, and `uncertainty=True` is returned.
3. **Blocked $\rightarrow$ Clear Auditable Revisions:** When front LiDAR detects an obstacle, route status is revised to `blocked`. When a subsequent scan shows a clear path, route status is atomically revised to `clear`, maintaining a verifiable audit chain.

### C. Run and Room Context Scoping
Simulation time is strictly local to each run (`run_id`).
1. **Fact Scoping:** All active facts and revisions are scoped to `SpatialContext(location=pose.location, frame_of_reference=..., extra_context={"run_id": str(sample.run_id)})`.
2. **Predecessor Query Isolation:** Predecessors are selected using exact run and room filters. When entity IDs are reused across independent runs (e.g. `parcel_01` in Run A at $t=7000$ ms and Run B at $t=0$ ms), Run B's initial sighting is never rejected as historical relative to Run A's timestamps.
3. **Summary Isolation:** `EntityBeliefSummary` and `RouteBeliefSummary` query beliefs exclusively within the requested `run_id`, preventing cross-run ghost facts or misaligned coordinate summaries.

### D. Ordering Against Applicable Acquisition Evidence
1. **Ledger-Aware Ordering:** Out-of-order checks compare incoming samples against the **latest applicable acquisition** in the ledger, not solely the active fact's creation timestamp.
2. **Same-Zone Confirmation Ordering:** A same-zone confirmation at $t=2000$ ms creates no new SQLite fact, but advances the latest accepted sighting evidence in the ledger. A subsequent late-arriving sample at $t=1000$ ms reporting a different station zone is correctly recognized as out-of-order and marked `retained_as_historical`, rather than erroneously overwriting newer evidence.
3. **Route Status Ordering:** Same-status LiDAR confirmations similarly protect route beliefs against delayed out-of-order contradictory scans.

### E. Clock Semantics, Freshness, and Visibility
1. **Simulation-Time Freshness:** Freshness is evaluated using the run's current simulation time (`sim_time_ms`), **never wall-clock receipt time or playback rate**. Default freshness limit is 1,000 simulation ms:
   - $\text{age} \le 1,000 \text{ ms}$: `fresh`, `verified`.
   - $\text{age} > 1,000 \text{ ms}$: `stale`, `last_seen`.
2. **Future-Dated Protection:** Queries or samples where `sim_time_ms` is in the future are rejected as unverifiable. Age is clamped at $\ge 0$ and never assigned a negative value.
3. **Broad Scan Non-Detection (Omission) vs. Outage:**
   - **Healthy Broad Scan Lacking Entity:** Marks entity `visibility="not_currently_visible"` and `verification_status="last_seen"`. Historical location belief is preserved; no disappearance or movement fact is fabricated.
   - **Sensor Outage (`ObservationUnavailable`):** Marks verification as `unavailable`, preserving prior sighting without asserting negative visibility.
   - **Targeted Scan Scope:** A targeted scan for parcel A provides evidence only for parcel A; it never marks parcel B invisible.

---

## 3. Reliability, Idempotency, and Concurrency

### Per-Effect Idempotence Sequence
The ingestion lifecycle enforces belief-level atomicity:
1. **Step 1 (Raw Sample Persistence):** Persists the validated `ObservationSample` via `ObservationLedgerWriter.record_observation_sample()`.
2. **Step 2 (Per-Effect Idempotence Check):** Inspects Tier 1 facts using `get_facts_by_sample_id(sample.sample_id)`. For multi-object scans, already-recorded entities are safely skipped without re-writing.
3. **Step 3 (Recoverable Belief Mutation):** Each belief write is executed under try/except protection. If a database or persistence error occurs midway through a multi-object scan:
   - Completed facts remain safely in Tier 1.
   - The failure does NOT escape as an unhandled exception; it returns `status="rejected"` with `uncertainty=True` and structured error diagnostic messages.
   - On retry (even across database restore or process restarts), unrecorded objects are processed to completion while previously completed objects are untouched.
   - Once all effects are complete, subsequent duplicate ingestion requests return `status="duplicate_ignored"`.

### Thread Synchronization
All ingestion and summary queries use `FactRepositoryProtocol.observation_ingestion_lock()`. `MemoryRepository` owns this reentrant lock and `GraphSyncedRepository` delegates to it. Every ingestion service sharing that repository therefore serializes the complete effect lookup and write sequence. Persisted fact/sample links retain per-effect retry identity across snapshot restore. The runtime must also hold its session lock around other repository operations and snapshot capture/restore. Separate connections to the same database file require external serialization.

---

## 4. Public API & Type Definitions

Defined in `src/procedural/temporal_belief_ingestion.py`:

```python
class TemporalBeliefIngestionService:
    def __init__(
        self,
        fact_repository: FactRepositoryProtocol,
        observation_ledger: ObservationLedgerWriter | None = None,
        zone_map: StationZoneMap | None = None,
        route_map: StaticRouteMap | None = None,
        config: IngestionConfig | None = None,
    ) -> None: ...

    def ingest_sample(
        self,
        sample: ObservationSample,
        current_sim_time_ms: int | None = None,
    ) -> IngestionResult: ...

    def get_entity_belief_summary(
        self,
        run_id: UUID,
        entity_id: str,
        current_sim_time_ms: int,
    ) -> EntityBeliefSummary: ...

    def get_route_belief_summary(
        self,
        run_id: UUID,
        route_id: str,
        current_sim_time_ms: int,
    ) -> RouteBeliefSummary: ...
```

### Core Summary Contracts
- **`IngestionResult`:** Contains `status`, `stored_sample`, `facts_created`, `revisions_created`, `entity_summaries`, `route_summary`, and `uncertainty` flags.
- **`EntityBeliefSummary`:** Detailed report containing `supported_location`, `active_fact_id`, `last_positive_sighting`, `latest_sensor_attempt`, `verification_status` (`verified` | `last_seen` | `unverified` | `unavailable`), `visibility` (`currently_visible` | `not_currently_visible` | `unknown`), `freshness` (`fresh` | `stale` | `future_dated` | `unverified`), and exact observed coordinates.
- **`RouteBeliefSummary`:** Reports `status` (`clear` | `blocked`), supporting LiDAR samples, nearest distance, verification, and freshness.

---

## 5. Runnable Complete Example

This complete, runnable script demonstrates the full pipeline from `WarehouseSensors` acquisition to Tier 1 persistence, belief ingestion, epistemic queries, and active graph synchronization:

```python
from datetime import UTC, datetime
import json
from pathlib import Path
from uuid import UUID

from src.contracts import ObservationRequest, ScenarioDefinition
from src.declarative.active_graph import GraphSyncedRepository
from src.declarative.memory_repository import MemoryRepository
from src.procedural.temporal_belief_ingestion import TemporalBeliefIngestionService
from src.sensorimotor.simulation_engine import SimulationEngine
from src.sensorimotor.warehouse_sensors import WarehouseSensors

fixtures = json.loads(Path("tests/fixtures/phase_two/contracts.json").read_text())
scenario = ScenarioDefinition.model_validate(fixtures["valid"]["scenario"])
engine = SimulationEngine(
    scenario,
    epoch_utc=datetime(2026, 1, 1, tzinfo=UTC),
    id_factory=lambda: UUID(int=1),
)
sensors = WarehouseSensors(engine.snapshot)
with MemoryRepository() as repo:
    graph_repo = GraphSyncedRepository.create(repo)
    service = TemporalBeliefIngestionService(graph_repo, repo)
    first = sensors.sample(ObservationRequest(capability="camera_detect"))
    result = service.ingest_sample(first, current_sim_time_ms=0)
    assert result.status == "applied"
    assert any(
        f.subject == "parcel_01" and f.object == "packing_station" for f in result.facts_created
    )
    # Keep the observer at its reference pose while the scripted parcel moves.
    for _ in range(70):
        engine.step()
    later = sensors.sample(ObservationRequest(capability="camera_detect"))
    result = service.ingest_sample(later, current_sim_time_ms=7000)
    assert len(result.revisions_created) == 1
    summary = service.get_entity_belief_summary(first.run_id, "parcel_01", 7000)
    assert summary.supported_location == "inspection_station"
    assert summary.verification_status == "verified"
    assert repo.get_observation_sample(later.sample_id) is not None
    graph = graph_repo.graph.get_graph()
    assert graph.has_edge("parcel_01", "inspection_station")
    print(summary.supported_location, summary.verification_status)
```

---

## 6. Verification and Checkpoints

All acceptance criteria and PR #40 regression cases are covered by automated unit and integration tests.

### Checkpoint Commands and Results
```bash
# 1. Procedural architectural boundaries
.venv/bin/pytest tests/unit/procedural/test_procedural_boundaries.py
# Outcome: 1 passed in 0.02s (zero forbidden tier or provider imports)

# 2. Temporal belief unit tests (protocol fakes)
.venv/bin/pytest tests/unit/procedural/test_temporal_belief_ingestion.py -v
# Outcome: 18 passed in 0.23s

# 3. Integration tests with SQLite, NetworkX, WarehouseSensors, and Phase 2 fixtures
.venv/bin/pytest tests/integration/test_temporal_belief_ingestion.py -v
# Outcome: 17 passed

# 4. Combined P2-05 verification
.venv/bin/pytest tests/unit/procedural/test_temporal_belief_ingestion.py tests/integration/test_temporal_belief_ingestion.py
# Outcome: 35 passed

# 5. Code quality and formatting checks
.venv/bin/ruff format --check .
# Outcome: 125 files already formatted
.venv/bin/ruff check .
# Outcome: All checks passed!
```

---

## 7. Downstream Integration

- **P2-06 (Patrol and Safety):** Uses pose and front-LiDAR evidence directly for movement decisions. The runtime can present its decision trace alongside `RouteBeliefSummary`.
- **P2-07 (Session Runtime):** Coordinates periodic simulation step updates, invoking `ingest_sample()` on each sensor polling tick.
- **P2-08 (Temporal Questions):** Uses `get_entity_belief_summary()` and `get_route_belief_summary()` to synthesize natural-language answers distinguishing "last seen" from currently verified locations.
- **P2-11 (Timeline & Presentation):** Renders epistemic visibility states and audit trails directly from the SQLite evidence ledger and synchronized NetworkX projection.

### Review regression checks

Camera summaries select the latest broad or matching targeted attempt for the last sighting’s robot, room and map frame through `ObservationLedgerReader.get_latest_camera_attempt()`. Acquisition order uses `(sim_time_ms, sample_sequence)`, including equal-time omissions. These checks prevent an unrelated target or observer from changing an entity’s verification.

Full regression verification passed with 510 tests, localhost access enabled, and developer `.env` loading disabled in the test process. Frontend build, graph checks and inspector display checks passed.
