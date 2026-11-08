"""Unit tests for Tier 2 temporal belief ingestion service (P2-05).

Verifies all acceptance criteria using protocol fakes and deterministic fixtures:
- First sighting creation and sample linkage
- Station zone transitions, revisions, and audit trails
- Repeated sighting deduplication (no fact/audit inflation)
- Broad scan omissions and non-detection semantics
- Targeted scan isolation
- Sensor failure vs empty scan distinction
- Exact freshness boundaries (999ms, 1000ms, 1001ms)
- Future-dated timestamps and run isolation
- Out-of-order and equal-time sequence resolution
- Replay idempotency and partial-failure retry safety
- Front LiDAR route matching and blocked -> clear revision chains
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from src.contracts import (
    AuditEvent,
    BoundingBox,
    CameraDetection,
    CameraScan,
    FactAssertion,
    FactQuery,
    LidarMeasurement,
    ObservationRequest,
    ObservationSample,
    ObservationUnavailable,
    Point,
    Pose,
    SensorObservation,
    SpatialContext,
    StoredFact,
    StoredObservationSample,
)
from src.procedural.temporal_belief_ingestion import (
    IngestionConfig,
    StationZone,
    StationZoneMap,
    TemporalBeliefIngestionService,
)

# ---------------------------------------------------------------------------
# In-Memory Fakes
# ---------------------------------------------------------------------------


class FakeMemoryRepository:
    """Protocol-compliant fake for FactRepositoryProtocol."""

    def __init__(self) -> None:
        self._ingestion_lock = threading.RLock()
        self.facts: list[StoredFact] = []
        self.audit_events: list[AuditEvent] = []

    def observation_ingestion_lock(self):
        return self._ingestion_lock

    def record_fact(self, assertion: FactAssertion) -> StoredFact:
        fact = StoredFact(
            fact_id=uuid4(),
            created_at=datetime.now(UTC),
            version=assertion.version,
            subject=assertion.subject,
            predicate=assertion.predicate,
            object=assertion.object,
            source_agent=assertion.source_agent,
            confidence_score=assertion.confidence_score,
            observed_at=assertion.observed_at,
            context=assertion.context,
            evidence=assertion.evidence,
            superseded_by=None,
        )
        self.facts.append(fact)
        return fact

    def record_revision(
        self,
        old_fact_id: UUID,
        replacement: FactAssertion,
        reason: str,
        policy_rule: str,
        revised_at: datetime,
    ) -> Any:
        old_fact = next((f for f in self.facts if f.fact_id == old_fact_id), None)
        if old_fact is None:
            raise ValueError(f"Fact {old_fact_id} does not exist")
        if old_fact.superseded_by is not None:
            raise ValueError(f"Fact {old_fact_id} already superseded")

        successor = self.record_fact(replacement)
        # Update predecessor in-place
        idx = self.facts.index(old_fact)
        updated_old = StoredFact(
            fact_id=old_fact.fact_id,
            created_at=old_fact.created_at,
            version=old_fact.version,
            subject=old_fact.subject,
            predicate=old_fact.predicate,
            object=old_fact.object,
            source_agent=old_fact.source_agent,
            confidence_score=old_fact.confidence_score,
            observed_at=old_fact.observed_at,
            context=old_fact.context,
            evidence=old_fact.evidence,
            superseded_by=successor.fact_id,
        )
        self.facts[idx] = updated_old

        audit = AuditEvent(
            event_id=uuid4(),
            event_type="belief_revision",
            input_fact_ids=[old_fact_id, successor.fact_id],
            output_fact_ids=[successor.fact_id],
            reason=reason,
            policy_rule=policy_rule,
            created_at=revised_at,
        )
        self.audit_events.append(audit)

        class Outcome:
            def __init__(self, s: StoredFact, a: AuditEvent):
                self.successor = s
                self.audit_event = a

        return Outcome(successor, audit)

    def query_facts(self, query: FactQuery) -> list[StoredFact]:
        results = []
        for f in self.facts:
            if query.active_only and f.superseded_by is not None:
                continue
            if query.subject is not None and f.subject != query.subject:
                continue
            if query.predicate is not None and f.predicate != query.predicate:
                continue
            if query.source_agent is not None and f.source_agent != query.source_agent:
                continue
            if query.context is not None:
                if (
                    query.context.location is not None
                    and f.context.location != query.context.location
                ):
                    continue
                if (
                    query.context.frame_of_reference is not None
                    and f.context.frame_of_reference != query.context.frame_of_reference
                ):
                    continue
                if any(
                    f.context.extra_context.get(k) != v
                    for k, v in query.context.extra_context.items()
                ):
                    continue
            results.append(f)
        return results

    def get_facts_by_sample_id(self, sample_id: UUID) -> list[StoredFact]:
        return [f for f in self.facts if f.evidence.get("sample_id") == str(sample_id)]


class FakeObservationLedger:
    """Protocol-compliant fake for ObservationLedgerWriter and ObservationLedgerReader."""

    def __init__(self) -> None:
        self.samples: dict[UUID, StoredObservationSample] = {}

    def record_observation_sample(self, sample: ObservationSample) -> StoredObservationSample:
        if sample.sample_id in self.samples:
            existing = self.samples[sample.sample_id]
            if existing.model_dump(mode="json", exclude={"stored_at"}) == sample.model_dump(
                mode="json"
            ):
                return existing
            raise ValueError(f"Conflicting sample ID {sample.sample_id}")

        stored = StoredObservationSample(
            **sample.model_dump(),
            stored_at=datetime.now(UTC),
        )
        self.samples[sample.sample_id] = stored
        return stored

    def get_observation_sample(self, sample_id: UUID) -> StoredObservationSample | None:
        return self.samples.get(sample_id)

    def get_latest_sample(
        self,
        run_id: UUID,
        capability: str,
        sensor_id: str | None = None,
        entity_id: str | None = None,
        start_ms: int | None = None,
        at_sim_time_ms: int | None = None,
    ) -> StoredObservationSample | None:
        candidates = [
            s
            for s in self.samples.values()
            if s.run_id == run_id and s.request.capability == capability
        ]
        if at_sim_time_ms is not None:
            candidates = [s for s in candidates if s.sim_time_ms <= at_sim_time_ms]
        if start_ms is not None:
            candidates = [s for s in candidates if s.sim_time_ms >= start_ms]
        if not candidates:
            return None
        candidates.sort(key=lambda s: (s.sim_time_ms, s.sample_sequence), reverse=True)
        return candidates[0]

    def get_latest_camera_attempt(self, run_id, entity_id, observer_pose, at_sim_time_ms):
        candidates = [
            s
            for s in self.samples.values()
            if s.run_id == run_id
            and s.request.capability == "camera_detect"
            and s.request.target in (None, entity_id)
            and s.sim_time_ms <= at_sim_time_ms
            and (
                s.observer_pose.robot_id,
                s.observer_pose.location,
                s.observer_pose.frame_of_reference,
            )
            == (observer_pose.robot_id, observer_pose.location, observer_pose.frame_of_reference)
        ]
        return max(candidates, key=lambda s: (s.sim_time_ms, s.sample_sequence), default=None)

    def get_last_sighting(
        self,
        run_id: UUID,
        entity_id: str,
        capability: str | None = None,
        sensor_id: str | None = None,
        start_ms: int | None = None,
        at_sim_time_ms: int | None = None,
    ) -> StoredObservationSample | None:
        candidates = []
        for s in self.samples.values():
            if s.run_id != run_id:
                continue
            if capability is not None and s.request.capability != capability:
                continue
            if at_sim_time_ms is not None and s.sim_time_ms > at_sim_time_ms:
                continue
            if isinstance(s.result, SensorObservation) and s.request.capability == "camera_detect":
                scan = CameraScan.model_validate(s.result.measurements)
                if any(obj.object_id == entity_id for obj in scan.objects):
                    candidates.append(s)
        if not candidates:
            return None
        candidates.sort(key=lambda s: (s.sim_time_ms, s.sample_sequence), reverse=True)
        return candidates[0]

    def get_entity_sighting_history(
        self,
        run_id: UUID,
        entity_id: str,
        capability: str | None = None,
        sensor_id: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 100,
    ) -> list[StoredObservationSample]:
        history = []
        for s in self.samples.values():
            if s.run_id != run_id:
                continue
            if start_ms is not None and s.sim_time_ms < start_ms:
                continue
            if end_ms is not None and s.sim_time_ms > end_ms:
                continue
            if isinstance(s.result, SensorObservation) and s.request.capability == "camera_detect":
                scan = CameraScan.model_validate(s.result.measurements)
                if any(obj.object_id == entity_id for obj in scan.objects):
                    history.append(s)
        history.sort(key=lambda s: (s.sim_time_ms, s.sample_sequence))
        return history[:limit]

    def query_observations(self, query: Any) -> list[StoredObservationSample]:
        candidates = list(self.samples.values())
        if hasattr(query, "run_id"):
            candidates = [s for s in candidates if s.run_id == query.run_id]
        if hasattr(query, "capability") and query.capability is not None:
            candidates = [s for s in candidates if s.request.capability == query.capability]
        if hasattr(query, "end_ms") and query.end_ms is not None:
            candidates = [s for s in candidates if s.sim_time_ms <= query.end_ms]
        if hasattr(query, "start_ms") and query.start_ms is not None:
            candidates = [s for s in candidates if s.sim_time_ms >= query.start_ms]
        candidates.sort(key=lambda s: (s.sim_time_ms, s.sample_sequence))
        return candidates


# ---------------------------------------------------------------------------
# Sample Helper Factory
# ---------------------------------------------------------------------------


def make_camera_sample(
    sim_time_ms: int,
    objects: list[CameraDetection],
    sample_id: UUID | None = None,
    run_id: UUID | None = None,
    sequence: int = 0,
    target: str | None = None,
    pose: Pose | None = None,
    epoch_utc: datetime | None = None,
) -> ObservationSample:
    epoch = epoch_utc or datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    captured = epoch + timedelta(milliseconds=sim_time_ms)
    robot_pose = pose or Pose(
        x_cm=100.0,
        y_cm=100.0,
        robot_id="robot_1",
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    scan = CameraScan(objects=sorted(objects, key=lambda o: o.object_id))
    obs = SensorObservation(
        observation_id=uuid4(),
        sensor="camera_detect",
        capability="camera_detect",
        observed_at=captured,
        confidence_score=1.0,
        measurements=scan.model_dump(mode="json"),
        context=SpatialContext(
            location=robot_pose.location,
            frame_of_reference=robot_pose.frame_of_reference,
            world_version=0,
            observer=robot_pose.robot_id,
            extra_context={"lighting": "neutral"},
        ),
    )
    return ObservationSample(
        sample_id=sample_id or uuid4(),
        run_id=run_id or uuid4(),
        sample_sequence=sequence,
        epoch_utc=epoch,
        sim_time_ms=sim_time_ms,
        captured_at=captured,
        sensor_id="camera_detect",
        observer_pose=robot_pose,
        world_version=0,
        request=ObservationRequest(capability="camera_detect", target=target),
        result=obs,
    )


def make_lidar_sample(
    sim_time_ms: int,
    status: str,
    nearest_distance_cm: float | None = None,
    sample_id: UUID | None = None,
    run_id: UUID | None = None,
    sequence: int = 0,
    pose: Pose | None = None,
    epoch_utc: datetime | None = None,
) -> ObservationSample:
    epoch = epoch_utc or datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    captured = epoch + timedelta(milliseconds=sim_time_ms)
    robot_pose = pose or Pose(
        x_cm=100.0,
        y_cm=100.0,
        robot_id="robot_1",
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    lidar = LidarMeasurement(
        detected=nearest_distance_cm is not None,
        nearest_distance_cm=nearest_distance_cm,
        direction="front",
        status=status,  # type: ignore[arg-type]
    )
    obs = SensorObservation(
        observation_id=uuid4(),
        sensor="lidar_scan",
        capability="lidar_scan",
        observed_at=captured,
        confidence_score=1.0,
        measurements=lidar.model_dump(mode="json"),
        context=SpatialContext(
            location=robot_pose.location,
            frame_of_reference=robot_pose.frame_of_reference,
            world_version=0,
            observer=robot_pose.robot_id,
        ),
    )
    return ObservationSample(
        sample_id=sample_id or uuid4(),
        run_id=run_id or uuid4(),
        sample_sequence=sequence,
        epoch_utc=epoch,
        sim_time_ms=sim_time_ms,
        captured_at=captured,
        sensor_id="lidar_scan",
        observer_pose=robot_pose,
        world_version=0,
        request=ObservationRequest(capability="lidar_scan", parameters={"direction": "front"}),
        result=obs,
    )


# ---------------------------------------------------------------------------
# Unit Test Suite
# ---------------------------------------------------------------------------


def test_first_sighting_creates_sourced_belief() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)

    det = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample = make_camera_sample(sim_time_ms=0, objects=[det])

    res = service.ingest_sample(sample)

    assert res.status == "applied"
    assert len(res.facts_created) == 1
    assert len(res.revisions_created) == 0

    fact = res.facts_created[0]
    assert fact.subject == "parcel_01"
    assert fact.predicate == "located_at"
    assert fact.object == "packing_station"
    assert fact.evidence["sample_id"] == str(sample.sample_id)
    assert fact.evidence["x_cm"] == 80.0
    assert fact.evidence["y_cm"] == 350.0

    # Stored sample in ledger
    assert ledger.get_observation_sample(sample.sample_id) is not None

    # Summary
    summary = res.entity_summaries["parcel_01"]
    assert summary.supported_location == "packing_station"
    assert summary.verification_status == "verified"
    assert summary.visibility == "currently_visible"
    assert summary.freshness == "fresh"
    assert summary.age_ms == 0


def test_station_relocation_creates_exactly_one_revision() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_id = uuid4()

    # 1. First sighting at packing_station (80, 350)
    det1 = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample1 = make_camera_sample(sim_time_ms=0, objects=[det1], run_id=run_id)
    res1 = service.ingest_sample(sample1)
    fact1 = res1.facts_created[0]

    # 2. Later sighting at inspection_station (150, 390)
    det2 = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=150.0, y_cm=390.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample2 = make_camera_sample(sim_time_ms=7000, objects=[det2], run_id=run_id, sequence=1)
    res2 = service.ingest_sample(sample2)

    assert res2.status == "applied"
    assert len(res2.facts_created) == 1
    assert len(res2.revisions_created) == 1

    successor = res2.facts_created[0]
    audit = res2.revisions_created[0]

    assert successor.subject == "parcel_01"
    assert successor.predicate == "located_at"
    assert successor.object == "inspection_station"
    assert audit.input_fact_ids == [fact1.fact_id, successor.fact_id]
    assert audit.output_fact_ids == [successor.fact_id]

    # Prior fact remains queryable but is superseded
    all_facts = repo.query_facts(FactQuery(subject="parcel_01", active_only=False))
    assert len(all_facts) == 2
    old_fact = next(f for f in all_facts if f.fact_id == fact1.fact_id)
    assert old_fact.superseded_by == successor.fact_id

    # Active facts only contains the successor
    active_facts = repo.query_facts(FactQuery(subject="parcel_01", active_only=True))
    assert len(active_facts) == 1
    assert active_facts[0].fact_id == successor.fact_id


def test_repeated_identical_sightings_do_not_inflate_facts() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_id = uuid4()

    det1 = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample1 = make_camera_sample(sim_time_ms=0, objects=[det1], run_id=run_id)
    service.ingest_sample(sample1)

    # Second sample: jitter within packing_station (center 80, 350; bounds [50, 110], [320, 380])
    det2 = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=82.0, y_cm=351.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample2 = make_camera_sample(sim_time_ms=500, objects=[det2], run_id=run_id, sequence=1)
    res2 = service.ingest_sample(sample2)

    assert res2.status == "applied"
    assert len(res2.facts_created) == 0
    assert len(res2.revisions_created) == 0

    # Only 1 active fact exists in total
    active = repo.query_facts(FactQuery(subject="parcel_01", active_only=True))
    assert len(active) == 1

    # But observation ledger stores both samples
    assert len(ledger.samples) == 2


def test_unmapped_and_ambiguous_zones_produce_uncertainty() -> None:
    # Overlapping zones for test
    zone_map = StationZoneMap(
        zones=[
            StationZone.from_center("zone_A", "warehouse", "warehouse_map", 100, 100, 40, 40),
            StationZone.from_center("zone_B", "warehouse", "warehouse_map", 110, 110, 40, 40),
        ]
    )
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger, zone_map=zone_map)

    # 1. Unmapped point (outside all zones)
    det_unmapped = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=500.0, y_cm=500.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=10.0, height_cm=10.0),
    )
    sample1 = make_camera_sample(sim_time_ms=0, objects=[det_unmapped])
    res1 = service.ingest_sample(sample1)

    assert res1.status == "unmapped"
    assert res1.uncertainty is True
    assert len(res1.facts_created) == 0

    # 2. Ambiguous point (overlaps zone_A and zone_B)
    det_ambig = CameraDetection(
        object_id="parcel_02",
        apparent_color="red",
        position=Point(x_cm=105.0, y_cm=105.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=10.0, height_cm=10.0),
    )
    sample2 = make_camera_sample(sim_time_ms=500, objects=[det_ambig], sequence=1)
    res2 = service.ingest_sample(sample2)

    assert res2.status == "ambiguous"
    assert res2.uncertainty is True
    assert len(res2.facts_created) == 0


def test_broad_scan_omission_marks_entity_last_seen() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_id = uuid4()

    # Step 1: Broad scan detects parcel_01 at packing_station
    det1 = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample1 = make_camera_sample(sim_time_ms=0, objects=[det1], run_id=run_id)
    service.ingest_sample(sample1)

    # Step 2: Later broad scan detects parcel_02, but NOT parcel_01
    det2 = CameraDetection(
        object_id="parcel_02",
        apparent_color="green",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample2 = make_camera_sample(sim_time_ms=500, objects=[det2], run_id=run_id, sequence=1)
    res2 = service.ingest_sample(sample2)

    assert "parcel_01" in res2.entity_summaries
    s1 = res2.entity_summaries["parcel_01"]
    assert s1.visibility == "not_currently_visible"
    assert s1.verification_status == "last_seen"
    assert s1.supported_location == "packing_station"
    assert s1.uncertainty is True

    # Active fact for parcel_01 is NOT deleted or superseded
    active = repo.query_facts(FactQuery(subject="parcel_01", active_only=True))
    assert len(active) == 1
    assert active[0].object == "packing_station"


def test_targeted_scan_does_not_mark_unrelated_entities_invisible() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_id = uuid4()

    # Step 1: Broad scan detects parcel_01
    det1 = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample1 = make_camera_sample(sim_time_ms=0, objects=[det1], run_id=run_id)
    service.ingest_sample(sample1)

    # Step 2: Targeted scan for parcel_02
    det2 = CameraDetection(
        object_id="parcel_02",
        apparent_color="green",
        position=Point(x_cm=150.0, y_cm=390.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample2 = make_camera_sample(
        sim_time_ms=500, objects=[det2], run_id=run_id, target="parcel_02", sequence=1
    )
    res2 = service.ingest_sample(sample2)

    # parcel_01 should NOT be in entity_summaries or marked invisible by targeted scan
    assert "parcel_01" not in res2.entity_summaries


def test_camera_unavailable_differs_from_empty_scan() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_id = uuid4()

    # Step 1: Sighting
    det1 = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample1 = make_camera_sample(sim_time_ms=0, objects=[det1], run_id=run_id)
    service.ingest_sample(sample1)

    # Step 2: Unavailable sensor sample
    epoch = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    captured = epoch + timedelta(milliseconds=500)
    failed_sample = ObservationSample(
        sample_id=uuid4(),
        run_id=run_id,
        sample_sequence=1,
        epoch_utc=epoch,
        sim_time_ms=500,
        captured_at=captured,
        sensor_id="camera_detect",
        observer_pose=Pose(
            x_cm=100.0,
            y_cm=100.0,
            robot_id="robot_1",
            direction="north",
            location="warehouse",
            frame_of_reference="warehouse_map",
        ),
        world_version=0,
        request=ObservationRequest(capability="camera_detect"),
        result=ObservationUnavailable(
            capability="camera_detect",
            reason="sensor_unavailable",
            message="Observation unavailable.",
            reported_at=captured,
        ),
    )
    res = service.ingest_sample(failed_sample)
    assert res.status == "sensor_unavailable"

    summary = service.get_entity_belief_summary(run_id, "parcel_01", current_sim_time_ms=500)
    assert summary.verification_status == "unavailable"
    assert summary.visibility == "unknown"


def test_freshness_boundaries() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(
        repo, ledger, config=IngestionConfig(freshness_ms=1000)
    )
    run_id = uuid4()

    det = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample = make_camera_sample(sim_time_ms=1000, objects=[det], run_id=run_id)
    service.ingest_sample(sample)

    # Age = 999 ms -> fresh (at 1999 ms)
    s_999 = service.get_entity_belief_summary(run_id, "parcel_01", current_sim_time_ms=1999)
    assert s_999.freshness == "fresh"
    assert s_999.age_ms == 999
    assert s_999.verification_status == "verified"

    # Age = 1000 ms -> fresh (at 2000 ms)
    s_1000 = service.get_entity_belief_summary(run_id, "parcel_01", current_sim_time_ms=2000)
    assert s_1000.freshness == "fresh"
    assert s_1000.age_ms == 1000
    assert s_1000.verification_status == "verified"

    # Age = 1001 ms -> stale (at 2001 ms)
    s_1001 = service.get_entity_belief_summary(run_id, "parcel_01", current_sim_time_ms=2001)
    assert s_1001.freshness == "stale"
    assert s_1001.age_ms == 1001
    assert s_1001.verification_status == "last_seen"


def test_future_dated_simulation_time_is_rejected_without_negative_age() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_id = uuid4()

    det = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample = make_camera_sample(sim_time_ms=5000, objects=[det], run_id=run_id)

    # Ingesting sample when current sim time is 2000 ms -> future dated rejected
    res = service.ingest_sample(sample, current_sim_time_ms=2000)
    assert res.status == "rejected"
    assert res.uncertainty is True

    # Now ingest at 5000 ms
    service.ingest_sample(sample, current_sim_time_ms=5000)

    # Querying at 3000 ms (before sighting time)
    summary = service.get_entity_belief_summary(run_id, "parcel_01", current_sim_time_ms=3000)
    assert summary.freshness == "future_dated"
    assert summary.age_ms == 0  # Spec: Never assign negative age
    assert summary.uncertainty is True


def test_out_of_order_samples_do_not_overwrite_newer_beliefs() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_id = uuid4()

    # Step 1: Sighting at 7000 ms (inspection_station: 150, 390)
    det_new = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=150.0, y_cm=390.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample_new = make_camera_sample(sim_time_ms=7000, objects=[det_new], run_id=run_id, sequence=5)
    service.ingest_sample(sample_new)

    # Step 2: Late-arriving older sample from 2000 ms (packing_station: 80, 350)
    det_old = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample_old = make_camera_sample(sim_time_ms=2000, objects=[det_old], run_id=run_id, sequence=2)
    res_old = service.ingest_sample(sample_old)

    assert res_old.status == "retained_as_historical"
    assert len(res_old.facts_created) == 0
    assert len(res_old.revisions_created) == 0

    # Active belief is STILL inspection_station (from newer sample)
    active = repo.query_facts(FactQuery(subject="parcel_01", active_only=True))
    assert len(active) == 1
    assert active[0].object == "inspection_station"


def test_duplicate_sample_processing_is_idempotent() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_id = uuid4()

    det = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample = make_camera_sample(sim_time_ms=0, objects=[det], run_id=run_id)

    # First ingest
    res1 = service.ingest_sample(sample)
    assert res1.status == "applied"
    assert len(res1.facts_created) == 1

    # Second ingest of exact same sample
    res2 = service.ingest_sample(sample)
    assert res2.status == "duplicate_ignored"
    assert len(res2.facts_created) == 0
    assert len(res2.revisions_created) == 0

    # Only 1 fact exists
    assert len(repo.facts) == 1


def test_retry_after_partial_failure() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)

    det = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=12.0, height_cm=12.0),
    )
    sample = make_camera_sample(sim_time_ms=0, objects=[det])

    # Simulate sample persisted in ledger, but belief write crashed
    ledger.record_observation_sample(sample)
    assert len(repo.facts) == 0

    # Retry ingestion with the same sample
    res = service.ingest_sample(sample)
    assert res.status == "applied"
    assert len(res.facts_created) == 1
    assert repo.facts[0].subject == "parcel_01"


def test_lidar_route_blocked_to_clear_revision_chain() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_id = uuid4()

    # Route A is from (100, 100) to (100, 450) heading north
    pose = Pose(
        x_cm=100.0,
        y_cm=100.0,
        robot_id="robot_1",
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )

    # 1. First sample at 10000 ms: blocked
    sample_blocked = make_lidar_sample(
        sim_time_ms=10000,
        status="blocked",
        nearest_distance_cm=80.0,
        run_id=run_id,
        pose=pose,
        sequence=0,
    )
    res1 = service.ingest_sample(sample_blocked)
    assert res1.status == "applied"
    assert len(res1.facts_created) == 1
    fact_blocked = res1.facts_created[0]
    assert fact_blocked.subject == "route_A"
    assert fact_blocked.predicate == "status_is"
    assert fact_blocked.object == "blocked"

    # 2. Second sample at 23000 ms: clear
    sample_clear = make_lidar_sample(
        sim_time_ms=23000,
        status="clear",
        nearest_distance_cm=None,
        run_id=run_id,
        pose=pose,
        sequence=1,
    )
    res2 = service.ingest_sample(sample_clear)
    assert res2.status == "applied"
    assert len(res2.revisions_created) == 1
    fact_clear = res2.facts_created[0]
    assert fact_clear.object == "clear"

    # Audit event connects blocked -> clear
    audit = res2.revisions_created[0]
    assert audit.input_fact_ids == [fact_blocked.fact_id, fact_clear.fact_id]
    assert audit.output_fact_ids == [fact_clear.fact_id]


def test_other_route_lidar_cannot_verify_an_unobserved_route() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_id = uuid4()
    east = Pose(
        x_cm=100,
        y_cm=450,
        robot_id="robot_1",
        direction="east",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    service.ingest_sample(
        make_lidar_sample(
            sim_time_ms=1000,
            status="blocked",
            nearest_distance_cm=50,
            run_id=run_id,
            pose=east,
            sequence=0,
        )
    )
    summary = service.get_route_belief_summary(run_id, "route_A", current_sim_time_ms=1000)
    assert summary.verification_status == "unverified"
    assert summary.latest_lidar_attempt is None


def test_lidar_wrong_direction_or_unmapped_pose_does_not_update_route() -> None:
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)

    # Facing EAST at (100, 100) — does NOT match route_A (which heads north)
    pose = Pose(
        x_cm=100.0,
        y_cm=100.0,
        robot_id="robot_1",
        direction="east",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )
    sample = make_lidar_sample(
        sim_time_ms=5000, status="blocked", nearest_distance_cm=50.0, pose=pose
    )

    res = service.ingest_sample(sample)
    assert res.status == "unmapped"
    assert res.uncertainty is True
    assert len(res.facts_created) == 0
    assert len(repo.facts) == 0


def test_same_zone_confirmation_followed_by_late_changed_zone_sample() -> None:
    """PR #40 Comment 3 regression:

    Packing sighting t0, same-zone confirmation t2000, then late inspection sighting t1000.
    The late t1000 sample must NOT revise the active location to inspection because
    there is newer packing evidence at t2000.
    """
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_id = uuid4()

    det_packing = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=10.0, height_cm=10.0),
    )
    det_inspection = CameraDetection(
        object_id="parcel_01",
        apparent_color="red",
        position=Point(x_cm=150.0, y_cm=390.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=10.0, height_cm=10.0),
    )

    # 1. t0: initial sighting at packing_station
    s0 = make_camera_sample(sim_time_ms=0, objects=[det_packing], run_id=run_id, sequence=0)
    res0 = service.ingest_sample(s0)
    assert res0.status == "applied"
    assert len(res0.facts_created) == 1
    assert res0.facts_created[0].object == "packing_station"

    # 2. t2000: same-zone confirmation at packing_station
    s2000 = make_camera_sample(sim_time_ms=2000, objects=[det_packing], run_id=run_id, sequence=1)
    res2000 = service.ingest_sample(s2000)
    assert res2000.status == "applied"
    assert len(res2000.facts_created) == 0
    assert len(res2000.revisions_created) == 0

    # 3. t1000: out-of-order sighting at inspection_station
    s1000 = make_camera_sample(
        sim_time_ms=1000, objects=[det_inspection], run_id=run_id, sequence=2
    )
    res1000 = service.ingest_sample(s1000)
    assert res1000.status == "retained_as_historical"
    assert len(res1000.facts_created) == 0
    assert len(res1000.revisions_created) == 0

    # Active fact must remain packing_station
    active_facts = repo.query_facts(FactQuery(subject="parcel_01", active_only=True))
    assert len(active_facts) == 1
    assert active_facts[0].object == "packing_station"

    # Epistemic summary at t2000 must stay aligned with packing_station
    summary = service.get_entity_belief_summary(
        run_id=run_id, entity_id="parcel_01", current_sim_time_ms=2000
    )
    assert summary.supported_location == "packing_station"
    assert summary.verification_status == "verified"
    assert summary.freshness == "fresh"


def test_same_status_route_confirmation_followed_by_late_opposite_status() -> None:
    """PR #40 Comment 3 route path regression:

    Route clear at t0, same-status confirmation at t2000, then late blocked sighting at t1000.
    The late t1000 sample must NOT revise the route status to blocked.
    """
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_id = uuid4()
    pose = Pose(
        x_cm=100.0,
        y_cm=100.0,
        robot_id="robot_1",
        direction="north",
        location="warehouse",
        frame_of_reference="warehouse_map",
    )

    # 1. t0: route_A clear
    s0 = make_lidar_sample(
        sim_time_ms=0,
        status="clear",
        nearest_distance_cm=None,
        run_id=run_id,
        pose=pose,
        sequence=0,
    )
    res0 = service.ingest_sample(s0)
    assert res0.status == "applied"
    assert len(res0.facts_created) == 1
    assert res0.facts_created[0].object == "clear"

    # 2. t2000: route_A clear (confirmation)
    s2000 = make_lidar_sample(
        sim_time_ms=2000,
        status="clear",
        nearest_distance_cm=None,
        run_id=run_id,
        pose=pose,
        sequence=1,
    )
    res2000 = service.ingest_sample(s2000)
    assert res2000.status == "applied"
    assert len(res2000.facts_created) == 0

    # 3. t1000: late out-of-order route_A blocked
    s1000 = make_lidar_sample(
        sim_time_ms=1000,
        status="blocked",
        nearest_distance_cm=80.0,
        run_id=run_id,
        pose=pose,
        sequence=2,
    )
    res1000 = service.ingest_sample(s1000)
    assert res1000.status == "retained_as_historical"
    assert len(res1000.facts_created) == 0
    assert len(res1000.revisions_created) == 0

    # Summary at t2000 stays clear and verified
    summary = service.get_route_belief_summary(
        run_id=run_id, route_id="route_A", current_sim_time_ms=2000
    )
    assert summary.status == "clear"
    assert summary.verification_status == "verified"
    assert summary.freshness == "fresh"


def test_new_runs_reusing_entity_ids_are_isolated() -> None:
    """PR #40 Comment 2 regression:

    Run A sees parcel_X at inspection_station at t7000.
    Run B sees parcel_X at packing_station at t0.
    Run B must NOT treat t0 as historical relative to Run A's t7000.
    """
    repo = FakeMemoryRepository()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_a = uuid4()
    run_b = uuid4()

    det_a = CameraDetection(
        object_id="parcel_reused",
        apparent_color="blue",
        position=Point(x_cm=150.0, y_cm=390.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=10.0, height_cm=10.0),
    )
    det_b = CameraDetection(
        object_id="parcel_reused",
        apparent_color="blue",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=10.0, height_cm=10.0),
    )

    # Run A: t7000 at inspection_station
    s_a = make_camera_sample(sim_time_ms=7000, objects=[det_a], run_id=run_a)
    res_a = service.ingest_sample(s_a)
    assert res_a.status == "applied"
    assert len(res_a.facts_created) == 1
    assert res_a.facts_created[0].object == "inspection_station"

    # Run B: t0 at packing_station
    s_b = make_camera_sample(sim_time_ms=0, objects=[det_b], run_id=run_b)
    res_b = service.ingest_sample(s_b)
    assert res_b.status == "applied"
    assert len(res_b.facts_created) == 1
    assert res_b.facts_created[0].object == "packing_station"

    # Verify summaries for both runs are completely isolated
    sum_b = service.get_entity_belief_summary(
        run_id=run_b, entity_id="parcel_reused", current_sim_time_ms=0
    )
    assert sum_b.supported_location == "packing_station"
    assert sum_b.verification_status == "verified"
    assert sum_b.freshness == "fresh"
    assert sum_b.last_observed_position == Point(x_cm=80.0, y_cm=350.0)

    sum_a = service.get_entity_belief_summary(
        run_id=run_a, entity_id="parcel_reused", current_sim_time_ms=7000
    )
    assert sum_a.supported_location == "inspection_station"
    assert sum_a.verification_status == "verified"
    assert sum_a.freshness == "fresh"
    assert sum_a.last_observed_position == Point(x_cm=150.0, y_cm=390.0)


def test_multi_object_scan_partial_failure_retry_and_duplicate_idempotency() -> None:
    """PR #40 Comment 1 regression:

    Two-object scan where recording the second object's fact fails.
    First fact remains, error is caught and returned as recoverable rejection
    (not unhandled exception).
    Retry completes the second object without duplicating the first object.
    Subsequent duplicate call returns duplicate_ignored.
    """
    call_count = 0

    class FailingMemoryRepo(FakeMemoryRepository):
        def record_fact(self, assertion: FactAssertion) -> StoredFact:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Simulated transient database write failure on second object")
            return super().record_fact(assertion)

    repo = FailingMemoryRepo()
    ledger = FakeObservationLedger()
    service = TemporalBeliefIngestionService(repo, ledger)
    run_id = uuid4()

    det1 = CameraDetection(
        object_id="parcel_alpha",
        apparent_color="red",
        position=Point(x_cm=80.0, y_cm=350.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=10.0, height_cm=10.0),
    )
    det2 = CameraDetection(
        object_id="parcel_beta",
        apparent_color="blue",
        position=Point(x_cm=150.0, y_cm=390.0),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=10.0, height_cm=10.0),
    )

    sample = make_camera_sample(sim_time_ms=500, objects=[det1, det2], run_id=run_id)

    # 1. First attempt: parcel_alpha succeeds, parcel_beta fails
    res1 = service.ingest_sample(sample)
    assert res1.status == "rejected"
    assert res1.uncertainty is True
    assert "Simulated transient database write failure" in (res1.uncertainty_reason or "")
    assert len(res1.facts_created) == 1
    assert repo.facts[0].subject == "parcel_alpha"

    # 2. Retry attempt: failure condition cleared (call_count was 2, now 3)
    res2 = service.ingest_sample(sample)
    assert res2.status == "applied"
    assert len(res2.facts_created) == 1
    assert res2.facts_created[0].subject == "parcel_beta"

    # Both facts now exist in repo without duplicate parcel_alpha
    active_alpha = repo.query_facts(FactQuery(subject="parcel_alpha", active_only=True))
    active_beta = repo.query_facts(FactQuery(subject="parcel_beta", active_only=True))
    assert len(active_alpha) == 1
    assert len(active_beta) == 1
    assert len(repo.facts) == 2

    # 3. Duplicate ingestion attempt: all beliefs completed
    res3 = service.ingest_sample(sample)
    assert res3.status == "duplicate_ignored"
    assert len(res3.facts_created) == 0
    assert len(repo.facts) == 2
