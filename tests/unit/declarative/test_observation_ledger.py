"""Unit tests for the Tier 1 observation ledger and temporal evidence queries.

Fulfills all acceptance criteria for [P2-04] Issue #24:
- Persist ObservationSample envelopes (success, empty scan, outage, targeted failure).
- Strict idempotence and duplicate rejection on altered content.
- Multi-object detection indexing and per-entity discoverability.
- Order by simulation capture time (not database insertion time).
- Deterministic tie-breaking by sample_sequence.
- Cross-run isolation.
- Inclusive time interval boundaries and edge-case query validation.
- Preservation of positive sightings despite subsequent empty scans and outages.
- Atomic transaction rollback on failure.
- Zero side-effects on facts, audit events, and active belief graph.
- Snapshot serialization and legacy database backward compatibility.
- Protocol conformance for Tier 2 consumption without raw SQL.
- Downstream handoff example.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from src.contracts import (
    BoundingBox,
    CameraDetection,
    CameraScan,
    FactQuery,
    ObservationLedger,
    ObservationLedgerReader,
    ObservationLedgerWriter,
    ObservationRequest,
    ObservationSample,
    ObservationUnavailable,
    Point,
    Pose,
    SensorObservation,
    SpatialContext,
    StoredObservationSample,
    TemporalObservationQuery,
)
from src.declarative import ActiveBeliefGraph, MemoryRepository

# ---------------------------------------------------------------------------
# Fixture & Model Helpers
# ---------------------------------------------------------------------------

EPOCH = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)


def make_pose(
    x_cm: float = 100.0,
    y_cm: float = 200.0,
    direction: str = "north",
    location: str = "dock",
    robot_id: str = "robot_01",
) -> Pose:
    return Pose(
        robot_id=robot_id,
        x_cm=x_cm,
        y_cm=y_cm,
        direction=direction,
        location=location,
        frame_of_reference="warehouse_map",
    )


def make_detection(
    object_id: str,
    x_cm: float = 120.0,
    y_cm: float = 220.0,
    apparent_color: str = "cardboard_brown",
    width_cm: float = 40.0,
    height_cm: float = 30.0,
) -> CameraDetection:
    return CameraDetection(
        object_id=object_id,
        apparent_color=apparent_color,
        position=Point(x_cm=x_cm, y_cm=y_cm),
        frame_of_reference="warehouse_map",
        bounding_box=BoundingBox(width_cm=width_cm, height_cm=height_cm),
    )


def make_sample(
    sample_id: UUID | None = None,
    run_id: UUID | None = None,
    sample_sequence: int = 0,
    sim_time_ms: int = 1000,
    capability: str = "camera_detect",
    target: str | None = None,
    detections: list[CameraDetection] | None = None,
    unavailable_reason: str | None = None,
    epoch_utc: datetime = EPOCH,
    observer_pose: Pose | None = None,
    world_version: int = 1,
) -> ObservationSample:
    pose = observer_pose or make_pose()
    captured_at = epoch_utc + timedelta(milliseconds=sim_time_ms)
    sid = sample_id or uuid4()
    rid = run_id or uuid4()

    request = ObservationRequest(capability=capability, target=target)

    if unavailable_reason is not None:
        result = ObservationUnavailable(
            capability=capability,
            reason=unavailable_reason,
            reported_at=captured_at,
            message="Observation unavailable.",
        )
    else:
        context = SpatialContext(
            observer=pose.robot_id,
            location=pose.location,
            frame_of_reference=pose.frame_of_reference,
            world_version=world_version,
        )
        if capability == "camera_detect":
            objs = sorted(detections or [], key=lambda d: d.object_id)
            scan = CameraScan(objects=objs)
            measurements = scan.model_dump(mode="json")
        elif capability == "lidar_scan":
            request = ObservationRequest(capability="lidar_scan", parameters={"direction": "front"})
            measurements = {
                "detected": True,
                "nearest_distance_cm": 150.0,
                "direction": "front",
                "status": "blocked",
            }
        elif capability == "robot_pose":
            measurements = {
                "robot_id": pose.robot_id,
                "x_cm": pose.x_cm,
                "y_cm": pose.y_cm,
                "direction": pose.direction,
                "frame_of_reference": pose.frame_of_reference,
            }
        elif capability == "ambient_light":
            measurements = {"intensity": 500.0, "color_cast": "white"}
        else:
            raise ValueError(f"Unsupported capability: {capability}")

        result = SensorObservation(
            observation_id=uuid4(),
            sensor=capability,
            capability=capability,
            observed_at=captured_at,
            confidence_score=1.0,
            context=context,
            measurements=measurements,
        )

    return ObservationSample(
        sample_id=sid,
        run_id=rid,
        sample_sequence=sample_sequence,
        epoch_utc=epoch_utc,
        sim_time_ms=sim_time_ms,
        captured_at=captured_at,
        sensor_id=capability,
        observer_pose=pose,
        world_version=world_version,
        request=request,
        result=result,
    )


# ---------------------------------------------------------------------------
# Acceptance Tests
# ---------------------------------------------------------------------------


def test_record_and_get_observation_sample_roundtrip() -> None:
    """Store multi-object camera scan, single detection, and retrieve intact."""
    repo = MemoryRepository()
    run_id = uuid4()
    det1 = make_detection("box_01", 110, 210)
    det2 = make_detection("box_02", 150, 250)

    sample = make_sample(
        run_id=run_id,
        sim_time_ms=1000,
        detections=[det1, det2],
    )

    stored = repo.record_observation_sample(sample)

    assert isinstance(stored, StoredObservationSample)
    assert stored.sample_id == sample.sample_id
    assert stored.run_id == sample.run_id
    assert stored.sim_time_ms == 1000
    assert stored.stored_at.tzinfo is not None

    fetched = repo.get_observation_sample(sample.sample_id)
    assert fetched is not None
    assert fetched.sample_id == sample.sample_id
    assert fetched.stored_at == stored.stored_at
    assert isinstance(fetched.result, SensorObservation)
    scan = CameraScan.model_validate(fetched.result.measurements)
    assert [o.object_id for o in scan.objects] == ["box_01", "box_02"]


def test_roundtrip_empty_scan_and_outages() -> None:
    """Ensure empty scan, targeted failure, and sensor outage store and restore correctly."""
    repo = MemoryRepository()
    run_id = uuid4()

    # Empty scan
    empty_sample = make_sample(run_id=run_id, sim_time_ms=1000, detections=[])
    stored_empty = repo.record_observation_sample(empty_sample)
    assert isinstance(stored_empty.result, SensorObservation)
    scan = CameraScan.model_validate(stored_empty.result.measurements)
    assert scan.objects == []

    # Targeted failure
    target_fail = make_sample(
        run_id=run_id,
        sim_time_ms=1500,
        target="box_99",
        unavailable_reason="target_not_visible",
    )
    stored_tf = repo.record_observation_sample(target_fail)
    assert isinstance(stored_tf.result, ObservationUnavailable)
    assert stored_tf.result.reason == "target_not_visible"

    # Outage
    outage_sample = make_sample(
        run_id=run_id,
        sim_time_ms=2000,
        unavailable_reason="sensor_unavailable",
    )
    stored_outage = repo.record_observation_sample(outage_sample)
    assert isinstance(stored_outage.result, ObservationUnavailable)
    assert stored_outage.result.reason == "sensor_unavailable"


def test_idempotent_insert_identical_sample() -> None:
    """Inserting the same sample_id with identical content returns the original stored record."""
    repo = MemoryRepository()
    run_id = uuid4()
    det = make_detection("parcel_01")
    sample = make_sample(run_id=run_id, detections=[det])

    first = repo.record_observation_sample(sample)
    second = repo.record_observation_sample(sample)

    assert first.sample_id == second.sample_id
    assert first.stored_at == second.stored_at

    # Verify no duplicate rows
    sample_count = repo._conn.execute(
        "SELECT COUNT(*) FROM observation_samples WHERE sample_id = ?",
        (str(sample.sample_id),),
    ).fetchone()[0]
    assert sample_count == 1

    detection_count = repo._conn.execute(
        "SELECT COUNT(*) FROM observation_detections WHERE sample_id = ?",
        (str(sample.sample_id),),
    ).fetchone()[0]
    assert detection_count == 1


def test_duplicate_sample_id_different_content_rejected() -> None:
    """Reusing the same sample_id with different content must raise ValueError."""
    repo = MemoryRepository()
    sample_id = uuid4()
    run_id = uuid4()

    sample_a = make_sample(sample_id=sample_id, run_id=run_id, sim_time_ms=1000)
    sample_b = make_sample(sample_id=sample_id, run_id=run_id, sim_time_ms=2000)

    repo.record_observation_sample(sample_a)

    with pytest.raises(ValueError, match="already exists with different content"):
        repo.record_observation_sample(sample_b)

    # Ensure original sample was not mutated
    stored = repo.get_observation_sample(sample_id)
    assert stored is not None
    assert stored.sim_time_ms == 1000


def test_multi_object_camera_scan_discoverability() -> None:
    """One scan with three objects is discoverable in each object's history."""
    repo = MemoryRepository()
    run_id = uuid4()

    det1 = make_detection("parcel_A")
    det2 = make_detection("parcel_B")
    det3 = make_detection("parcel_C")

    sample = make_sample(
        run_id=run_id,
        sim_time_ms=1000,
        detections=[det1, det2, det3],
    )
    repo.record_observation_sample(sample)

    for entity_id in ("parcel_A", "parcel_B", "parcel_C"):
        last_sighting = repo.get_last_sighting(run_id, entity_id)
        assert last_sighting is not None
        assert last_sighting.sample_id == sample.sample_id

        history = repo.get_entity_sighting_history(run_id, entity_id)
        assert len(history) == 1
        assert history[0].sample_id == sample.sample_id

    # Unknown entity returns None / empty history
    assert repo.get_last_sighting(run_id, "unknown_entity") is None
    assert repo.get_entity_sighting_history(run_id, "unknown_entity") == []


def test_late_insertion_does_not_replace_latest() -> None:
    """Late insertion at t=500 after t=1500 does not replace the latest observation or sighting."""
    repo = MemoryRepository()
    run_id = uuid4()
    det = make_detection("target_box")

    # Newer sample arrives first at t=1500
    sample_1500 = make_sample(
        run_id=run_id,
        sample_sequence=2,
        sim_time_ms=1500,
        detections=[det],
    )
    repo.record_observation_sample(sample_1500)

    # Delayed sample arrives later but has t=500
    sample_500 = make_sample(
        run_id=run_id,
        sample_sequence=1,
        sim_time_ms=500,
        detections=[det],
    )
    repo.record_observation_sample(sample_500)

    latest = repo.get_latest_sample(run_id, "camera_detect")
    assert latest is not None
    assert latest.sample_id == sample_1500.sample_id
    assert latest.sim_time_ms == 1500

    last_sighting = repo.get_last_sighting(run_id, "target_box")
    assert last_sighting is not None
    assert last_sighting.sample_id == sample_1500.sample_id
    assert last_sighting.sim_time_ms == 1500

    # History remains in chronological simulation-time order
    history = repo.get_entity_sighting_history(run_id, "target_box")
    assert [s.sim_time_ms for s in history] == [500, 1500]


def test_equal_time_samples_deterministic_tie_breaking() -> None:
    """Equal-time samples have documented deterministic tie-breaking by sequence."""
    repo = MemoryRepository()
    run_id = uuid4()

    s1 = make_sample(run_id=run_id, sample_sequence=1, sim_time_ms=1000)
    s2 = make_sample(run_id=run_id, sample_sequence=2, sim_time_ms=1000)

    repo.record_observation_sample(s1)
    repo.record_observation_sample(s2)

    latest = repo.get_latest_sample(run_id, "camera_detect")
    assert latest is not None
    assert latest.sample_id == s2.sample_id
    assert latest.sample_sequence == 2

    # Query samples in ascending order returns s1 then s2
    samples = repo.query_samples(run_id=run_id)
    assert [s.sample_sequence for s in samples] == [1, 2]


def test_two_runs_isolated() -> None:
    """Two runs with the same object IDs and elapsed times never mix results."""
    repo = MemoryRepository()
    run_a = uuid4()
    run_b = uuid4()

    det = make_detection("common_box")
    sa = make_sample(run_id=run_a, sim_time_ms=1000, detections=[det])
    sb = make_sample(run_id=run_b, sim_time_ms=1000, detections=[det])

    repo.record_observation_sample(sa)
    repo.record_observation_sample(sb)

    a_sighting = repo.get_last_sighting(run_a, "common_box")
    assert a_sighting is not None
    assert a_sighting.sample_id == sa.sample_id
    assert a_sighting.run_id == run_a

    b_sighting = repo.get_last_sighting(run_b, "common_box")
    assert b_sighting is not None
    assert b_sighting.sample_id == sb.sample_id
    assert b_sighting.run_id == run_b

    a_history = repo.get_entity_sighting_history(run_a, "common_box")
    assert len(a_history) == 1
    assert a_history[0].run_id == run_a


def test_inclusive_interval_and_at_time_boundaries() -> None:
    """Inclusive interval and at-time boundaries, empty history, and query validation."""
    repo = MemoryRepository()
    run_id = uuid4()

    times = [1000, 2000, 3000, 4000]
    for seq, t in enumerate(times):
        repo.record_observation_sample(
            make_sample(run_id=run_id, sample_sequence=seq, sim_time_ms=t)
        )

    # Inclusive bounds [2000, 3000]
    matched = repo.query_samples(run_id=run_id, start_ms=2000, end_ms=3000)
    assert [s.sim_time_ms for s in matched] == [2000, 3000]

    # At-time boundary for latest
    latest_at_2000 = repo.get_latest_sample(run_id, "camera_detect", at_sim_time_ms=2000)
    assert latest_at_2000 is not None
    assert latest_at_2000.sim_time_ms == 2000

    latest_at_2500 = repo.get_latest_sample(run_id, "camera_detect", at_sim_time_ms=2500)
    assert latest_at_2500 is not None
    assert latest_at_2500.sim_time_ms == 2000

    # Query before first sample
    assert repo.get_latest_sample(run_id, "camera_detect", at_sim_time_ms=500) is None

    # Invalid query bounds
    with pytest.raises(ValueError, match="start_ms must not exceed end_ms"):
        repo.query_samples(run_id=run_id, start_ms=3000, end_ms=2000)

    with pytest.raises(ValueError, match="start_ms must be non-negative"):
        repo.query_samples(run_id=run_id, start_ms=-100)

    with pytest.raises(ValueError, match="limit must be between 1 and 1000"):
        repo.query_samples(run_id=run_id, limit=0)


def test_empty_scan_and_failure_do_not_erase_positive_sighting() -> None:
    """A later empty scan or failure does not erase or fabricate a positive sighting."""
    repo = MemoryRepository()
    run_id = uuid4()
    det = make_detection("parcel_42")

    # t=1000: positive sighting
    repo.record_observation_sample(
        make_sample(run_id=run_id, sample_sequence=1, sim_time_ms=1000, detections=[det])
    )

    # t=1500: healthy camera, empty scan (0 detections)
    repo.record_observation_sample(
        make_sample(run_id=run_id, sample_sequence=2, sim_time_ms=1500, detections=[])
    )

    # t=2000: sensor outage
    repo.record_observation_sample(
        make_sample(
            run_id=run_id,
            sample_sequence=3,
            sim_time_ms=2000,
            unavailable_reason="sensor_unavailable",
        )
    )

    # Last positive sighting of parcel_42 remains at t=1000
    sighting = repo.get_last_sighting(run_id, "parcel_42")
    assert sighting is not None
    assert sighting.sim_time_ms == 1000

    # Latest camera sample is the outage at t=2000
    latest_camera = repo.get_latest_sample(run_id, "camera_detect")
    assert latest_camera is not None
    assert latest_camera.sim_time_ms == 2000
    assert isinstance(latest_camera.result, ObservationUnavailable)

    # Latest camera sample at t=1800 is the empty scan at t=1500
    camera_at_1800 = repo.get_latest_sample(run_id, "camera_detect", at_sim_time_ms=1800)
    assert camera_at_1800 is not None
    assert camera_at_1800.sim_time_ms == 1500
    assert isinstance(camera_at_1800.result, SensorObservation)


def test_atomic_rollback_on_failure() -> None:
    """Stored sample and detection associations roll back together on failure."""
    repo = MemoryRepository()
    run_id = uuid4()
    det = make_detection("box_fail")
    sample = make_sample(run_id=run_id, detections=[det])

    # Intercept with a trigger that causes an abort on detection insert
    repo._conn.execute(
        """
        CREATE TRIGGER test_fail_trigger
        BEFORE INSERT ON observation_detections
        BEGIN
            SELECT RAISE(ABORT, 'Simulated write failure');
        END;
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="Simulated write failure"):
        repo.record_observation_sample(sample)

    # Drop trigger and verify no partial parent sample was committed
    repo._conn.execute("DROP TRIGGER test_fail_trigger")
    assert repo.get_observation_sample(sample.sample_id) is None
    sample_count = repo._conn.execute("SELECT COUNT(*) FROM observation_samples").fetchone()[0]
    assert sample_count == 0
    det_count = repo._conn.execute("SELECT COUNT(*) FROM observation_detections").fetchone()[0]
    assert det_count == 0


def test_observation_writes_do_not_add_facts_or_graph_edges() -> None:
    """Observation-only writes do not add facts/revisions/graph edges."""
    repo = MemoryRepository()
    graph = ActiveBeliefGraph(repo)

    run_id = uuid4()
    det = make_detection("box_clean")
    sample = make_sample(run_id=run_id, detections=[det])

    repo.record_observation_sample(sample)

    facts_count = repo._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    audit_count = repo._conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    alias_count = repo._conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]

    assert facts_count == 0
    assert audit_count == 0
    assert alias_count == 0
    g = graph.get_graph()
    assert g.number_of_nodes() == 0
    assert g.number_of_edges() == 0


def test_snapshot_export_and_restore_with_observations() -> None:
    """New sample snapshots export/restore exactly; existing fact query and graph still pass."""
    repo1 = MemoryRepository()
    run_id = uuid4()
    det = make_detection("snap_parcel")
    sample = make_sample(run_id=run_id, detections=[det])

    repo1.record_observation_sample(sample)
    image = repo1.export_snapshot()

    repo2 = MemoryRepository()
    repo2.restore_snapshot(image)

    fetched = repo2.get_observation_sample(sample.sample_id)
    assert fetched is not None
    assert fetched.sample_id == sample.sample_id
    assert fetched.sim_time_ms == sample.sim_time_ms

    sighting = repo2.get_last_sighting(run_id, "snap_parcel")
    assert sighting is not None
    assert sighting.sample_id == sample.sample_id


def test_backward_compatibility_with_legacy_database_and_snapshot(tmp_path) -> None:
    """Old database file and old serialized snapshot initialize additively, keeping facts."""
    db_file = tmp_path / "legacy.db"

    # Step 1: Create a database with only the legacy tables
    raw_conn = sqlite3.connect(str(db_file))
    raw_conn.execute("PRAGMA foreign_keys = ON")
    raw_conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS facts (
            fact_id TEXT PRIMARY KEY,
            version TEXT NOT NULL,
            subject TEXT NOT NULL CHECK(subject != ''),
            predicate TEXT NOT NULL CHECK(predicate != ''),
            object TEXT NOT NULL CHECK(object != ''),
            source_agent TEXT NOT NULL CHECK(source_agent != ''),
            confidence_score REAL NOT NULL,
            observed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            context_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            superseded_by TEXT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            old_fact_id TEXT NOT NULL,
            new_fact_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            policy_rule TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS aliases (
            alias TEXT NOT NULL,
            canonical_entity_id TEXT NOT NULL,
            UNIQUE(alias, canonical_entity_id)
        );
        INSERT INTO facts VALUES (
            'f0000000-0000-0000-0000-000000000001', 'v1', 'parcel_legacy',
            'location_is', 'shelf_1', 'static_map', 1.0,
            '2026-11-05T12:00:00+00:00', '2026-11-05T12:00:00+00:00',
            '{}', '{}', NULL
        );
        """
    )
    raw_conn.commit()
    legacy_image = raw_conn.serialize()
    raw_conn.close()

    # Step 2: Open legacy db file with MemoryRepository
    repo_file = MemoryRepository(db_path=str(db_file))
    facts = repo_file.query_facts(FactQuery(active_only=False))
    assert len(facts) == 1
    assert facts[0].subject == "parcel_legacy"

    # Verify new tables exist and can record observations
    s = make_sample()
    repo_file.record_observation_sample(s)
    assert repo_file.get_observation_sample(s.sample_id) is not None
    repo_file.close()

    # Step 3: Deserializing legacy snapshot into a fresh repository upgrades additively
    repo_mem = MemoryRepository()
    repo_mem.restore_snapshot(legacy_image)
    legacy_facts = repo_mem.query_facts(FactQuery(active_only=False))
    assert len(legacy_facts) == 1
    assert legacy_facts[0].subject == "parcel_legacy"

    # Verify observation write succeeds on the deserialized legacy image
    s2 = make_sample()
    repo_mem.record_observation_sample(s2)
    assert repo_mem.get_observation_sample(s2.sample_id) is not None
    repo_mem.close()


def test_protocol_conformance_and_temporal_queries() -> None:
    """Verify repository satisfies ledger protocols and handles temporal queries."""
    repo = MemoryRepository()
    assert isinstance(repo, ObservationLedger)
    assert isinstance(repo, ObservationLedgerReader)
    assert isinstance(repo, ObservationLedgerWriter)

    run_id = uuid4()
    det = make_detection("protocol_box")
    s1 = make_sample(run_id=run_id, sample_sequence=0, sim_time_ms=1000, detections=[det])
    s2 = make_sample(run_id=run_id, sample_sequence=1, sim_time_ms=2000, detections=[det])
    repo.record_observation_sample(s1)
    repo.record_observation_sample(s2)

    reader: ObservationLedgerReader = repo

    # Query mode: samples
    q_samples = TemporalObservationQuery(
        run_id=run_id,
        mode="samples",
        start_ms=500,
        end_ms=2500,
    )
    res_samples = reader.query_observations(q_samples)
    assert len(res_samples) == 2

    # Query mode: latest
    q_latest = TemporalObservationQuery(
        run_id=run_id,
        mode="latest",
        capability="camera_detect",
        end_ms=1500,
    )
    res_latest = reader.query_observations(q_latest)
    assert len(res_latest) == 1
    assert res_latest[0].sim_time_ms == 1000

    # Query mode: last_seen
    q_last_seen = TemporalObservationQuery(
        run_id=run_id,
        mode="last_seen",
        entity_id="protocol_box",
    )
    res_last_seen = reader.query_observations(q_last_seen)
    assert len(res_last_seen) == 1
    assert res_last_seen[0].sim_time_ms == 2000

    # Query mode: history
    q_history = TemporalObservationQuery(
        run_id=run_id,
        mode="history",
        entity_id="protocol_box",
    )
    res_history = reader.query_observations(q_history)
    assert len(res_history) == 2


def test_fixture_driven_downstream_handoff_example() -> None:
    """Downstream handoff example required by Issue #24.

    Scenario:
    - store sightings at two stations (t=1000 at packing_01, t=2000 at inspection_01);
    - then an empty scan at t=3000;
    - then an outage at t=4000;
    - query last sighting, full history, and latest camera status;
    - verify distinct timestamps and evidence IDs.
    """
    repo = MemoryRepository()
    run_id = uuid4()

    pose_st1 = make_pose(x_cm=100.0, y_cm=100.0, direction="north")
    pose_st2 = make_pose(x_cm=300.0, y_cm=300.0, direction="east")

    det_parcel = make_detection("parcel_handoff")

    # 1. Sighting at station 1 (t=1000)
    s1 = make_sample(
        run_id=run_id,
        sample_sequence=1,
        sim_time_ms=1000,
        observer_pose=pose_st1,
        detections=[det_parcel],
    )
    repo.record_observation_sample(s1)

    # 2. Sighting at station 2 (t=2000)
    s2 = make_sample(
        run_id=run_id,
        sample_sequence=2,
        sim_time_ms=2000,
        observer_pose=pose_st2,
        detections=[det_parcel],
    )
    repo.record_observation_sample(s2)

    # 3. Empty scan (t=3000)
    s3 = make_sample(
        run_id=run_id,
        sample_sequence=3,
        sim_time_ms=3000,
        detections=[],
    )
    repo.record_observation_sample(s3)

    # 4. Outage (t=4000)
    s4 = make_sample(
        run_id=run_id,
        sample_sequence=4,
        sim_time_ms=4000,
        unavailable_reason="sensor_unavailable",
    )
    repo.record_observation_sample(s4)

    # Verification:
    # A. Last sighting of parcel_handoff remains t=2000 at station 2
    last_sighting = repo.get_last_sighting(run_id, "parcel_handoff")
    assert last_sighting is not None
    assert last_sighting.sample_id == s2.sample_id
    assert last_sighting.sim_time_ms == 2000
    assert last_sighting.observer_pose.x_cm == 300.0

    # B. Sighting history has exactly 2 records with distinct timestamps
    history = repo.get_entity_sighting_history(run_id, "parcel_handoff")
    assert len(history) == 2
    assert [h.sim_time_ms for h in history] == [1000, 2000]
    assert [h.sample_id for h in history] == [s1.sample_id, s2.sample_id]
    assert history[0].sample_id != history[1].sample_id

    # C. Latest camera status is the outage at t=4000
    latest_status = repo.get_latest_sample(run_id, "camera_detect")
    assert latest_status is not None
    assert latest_status.sample_id == s4.sample_id
    assert latest_status.sim_time_ms == 4000
    assert isinstance(latest_status.result, ObservationUnavailable)
    assert latest_status.result.reason == "sensor_unavailable"
