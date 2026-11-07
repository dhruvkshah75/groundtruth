"""Integration tests for TemporalBeliefIngestionService against real SQLite and Graph projections.

Verifies end-to-end integration:
- Live MemoryRepository (Tier 1 SQLite) + ActiveBeliefGraph / GraphSyncedRepository
- Phase 2 shared fixtures (contracts.json), including 22-second camera and 23-second LiDAR
- Synchronized NetworkX active graph projection
- Complete audit trail persistence and inspection
- Concurrent duplicate ingestion thread-safety
- Snapshot export and restore persistence
- Playback rate and pause invariance
"""

from __future__ import annotations

import concurrent.futures
import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.contracts import (
    CameraDetection,
    FactQuery,
    ObservationRequest,
    ObservationSample,
    SimulationSnapshot,
    TemporalObservationQuery,
)
from src.declarative.active_graph import GraphSyncedRepository
from src.declarative.memory_repository import MemoryRepository
from src.procedural.temporal_belief_ingestion import (
    TemporalBeliefIngestionService,
)
from src.sensorimotor.warehouse_sensors import WarehouseSensors

FIXTURES_PATH = Path(__file__).parents[1] / "fixtures" / "phase_two" / "contracts.json"


@pytest.fixture
def phase2_fixtures() -> dict:
    with FIXTURES_PATH.open(encoding="utf-8") as f:
        return json.load(f)["valid"]


def test_real_sqlite_and_graph_projection_lifecycle() -> None:
    """Verifies that facts and revisions update both SQLite and the NetworkX projection."""
    with MemoryRepository(":memory:") as repo:
        synced_repo = GraphSyncedRepository.create(repo)
        service = TemporalBeliefIngestionService(synced_repo, repo)

        with FIXTURES_PATH.open(encoding="utf-8") as f:
            valid_data = json.load(f)["valid"]

        # Sample 0 at 0s: parcel_01 at packing_station (80, 350)
        sample0 = ObservationSample.model_validate(valid_data["samples"][0])
        res0 = service.ingest_sample(sample0)

        assert res0.status == "applied"
        assert len(res0.facts_created) == 1

        # Check SQLite
        active_facts = repo.query_facts(FactQuery(subject="parcel_01", active_only=True))
        assert len(active_facts) == 1
        assert active_facts[0].object == "packing_station"

        # Check NetworkX Graph Projection
        graph0 = synced_repo.get_graph()
        assert graph0.has_node("parcel_01")
        assert graph0.has_node("packing_station")
        outgoing0 = synced_repo.graph.get_outgoing("parcel_01")
        assert len(outgoing0) == 1
        assert outgoing0[0]["object"] == "packing_station"
        assert outgoing0[0]["predicate"] == "located_at"

        # Sample 1 at 7s: parcel_01 moved to inspection_station (150, 390)
        sample1 = ObservationSample.model_validate(valid_data["samples"][1])
        res1 = service.ingest_sample(sample1)

        assert res1.status == "applied"
        assert len(res1.facts_created) == 1
        assert len(res1.revisions_created) == 1

        # Check SQLite audit chain
        successor_fact = res1.facts_created[0]
        trail = repo.get_audit_chain(successor_fact.fact_id)
        assert len(trail.facts) == 2
        assert len(trail.events) == 1
        assert trail.events[0].policy_rule == "camera_observation_revision"

        # Check Graph Projection updated to new active belief
        graph1 = synced_repo.get_graph()
        outgoing1 = synced_repo.graph.get_outgoing("parcel_01")
        assert len(outgoing1) == 1
        assert outgoing1[0]["object"] == "inspection_station"
        assert not graph1.has_edge("parcel_01", "packing_station")


def test_integration_with_phase2_fixture_samples(phase2_fixtures: dict) -> None:
    """Ingests official Phase 2 fixture samples including 22s camera and 23s LiDAR."""
    with MemoryRepository(":memory:") as repo:
        service = TemporalBeliefIngestionService(repo, repo)
        samples = [ObservationSample.model_validate(s) for s in phase2_fixtures["samples"]]

        # 0: 0s - camera broad scan (parcel_01 at packing, parcel_02 outside station)
        res0 = service.ingest_sample(samples[0])
        assert res0.status == "applied"
        assert len(res0.facts_created) == 1
        assert res0.facts_created[0].object == "packing_station"

        # 1: 7s - camera targeted scan (parcel_01 at inspection station)
        res1 = service.ingest_sample(samples[1])
        assert res1.status == "applied"
        assert res1.facts_created[0].object == "inspection_station"

        # 2: 22s - camera broad scan (detects parcel_02 only; parcel_01 omitted)
        res2 = service.ingest_sample(samples[2])
        assert "parcel_01" in res2.entity_summaries
        s_parcel1 = res2.entity_summaries["parcel_01"]
        assert s_parcel1.verification_status == "last_seen"
        assert s_parcel1.visibility == "not_currently_visible"
        assert s_parcel1.supported_location == "inspection_station"

        # 3: 22s - targeted camera request for parcel_01 (target_not_visible)
        res3 = service.ingest_sample(samples[3])
        assert res3.status == "sensor_unavailable"

        # 4: 22.5s - broad camera scan (sensor_unavailable outage)
        res4 = service.ingest_sample(samples[4])
        assert res4.status == "sensor_unavailable"

        # 5: 23s - LiDAR scan (clear on route_A)
        res5 = service.ingest_sample(samples[5])
        assert res5.status == "applied"
        assert res5.facts_created[0].subject == "route_A"
        assert res5.facts_created[0].object == "clear"

        # 8: 23.5s - healthy empty camera scan
        res8 = service.ingest_sample(samples[8])
        assert res8.status == "no_detection"

        # Check raw ledger preservation
        query = TemporalObservationQuery(run_id=samples[0].run_id, mode="samples")
        stored_samples = repo.query_observations(query)
        assert len(stored_samples) == 7


def test_concurrent_duplicate_ingestion_thread_safety() -> None:
    """Verifies that concurrent duplicate ingestion cannot create duplicate revisions or facts."""
    with MemoryRepository(":memory:") as repo:
        service = TemporalBeliefIngestionService(repo, repo)

        with FIXTURES_PATH.open(encoding="utf-8") as f:
            valid_data = json.load(f)["valid"]

        sample0 = ObservationSample.model_validate(valid_data["samples"][0])

        # Execute 8 concurrent ingestion calls for the exact same sample
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(service.ingest_sample, sample0) for _ in range(8)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        # Exactly 1 result was "applied" and created a fact, others "duplicate_ignored"
        applied = [r for r in results if r.status == "applied"]
        ignored = [r for r in results if r.status == "duplicate_ignored"]

        assert len(applied) == 1
        assert len(ignored) == 7

        # Exactly 1 active fact in SQLite
        facts = repo.query_facts(FactQuery(subject="parcel_01", active_only=True))
        assert len(facts) == 1


def test_sqlite_snapshot_export_and_restore() -> None:
    """Verifies snapshot export and restore maintains facts, revisions, and observation markers."""
    with MemoryRepository(":memory:") as repo:
        service = TemporalBeliefIngestionService(repo, repo)

        with FIXTURES_PATH.open(encoding="utf-8") as f:
            valid_data = json.load(f)["valid"]

        sample0 = ObservationSample.model_validate(valid_data["samples"][0])
        sample1 = ObservationSample.model_validate(valid_data["samples"][1])

        service.ingest_sample(sample0)
        service.ingest_sample(sample1)

        # Export database snapshot
        snapshot_bytes = repo.export_snapshot()

    # Restore into fresh repository
    with MemoryRepository(":memory:") as restored_repo:
        restored_repo.restore_snapshot(snapshot_bytes)
        restored_service = TemporalBeliefIngestionService(restored_repo, restored_repo)

        # Active belief is inspection_station
        active = restored_repo.query_facts(FactQuery(subject="parcel_01", active_only=True))
        assert len(active) == 1
        assert active[0].object == "inspection_station"

        # Replaying sample1 on restored repository is detected as duplicate
        res = restored_service.ingest_sample(sample1)
        assert res.status == "duplicate_ignored"
        assert len(res.facts_created) == 0


def test_playback_rate_and_pause_independence() -> None:
    """Verifies that simulation decisions depend on sim_time_ms, not playback pacing or rate."""
    with MemoryRepository(":memory:") as repo:
        service = TemporalBeliefIngestionService(repo, repo)

        with FIXTURES_PATH.open(encoding="utf-8") as f:
            valid_data = json.load(f)["valid"]

        sample0 = ObservationSample.model_validate(valid_data["samples"][0])
        service.ingest_sample(sample0)

        # At simulation time 500ms, regardless of whether wall clock paused or playback
        # was 0.25x vs 1.0x:
        for _ in (0.25, 0.5, 1.0):
            summary = service.get_entity_belief_summary(
                run_id=sample0.run_id,
                entity_id="parcel_01",
                current_sim_time_ms=500,
            )
            assert summary.freshness == "fresh"
            assert summary.age_ms == 500
            assert summary.verification_status == "verified"
            assert summary.supported_location == "packing_station"


def test_integration_same_zone_confirmation_and_late_out_of_order_sample(
    phase2_fixtures: dict,
) -> None:
    """PR #40 Comment 3 regression with real SQLite ledger and active graph."""
    with MemoryRepository(":memory:") as repo:
        synced_repo = GraphSyncedRepository.create(repo)
        service = TemporalBeliefIngestionService(synced_repo, repo)

        samples = phase2_fixtures["samples"]
        # sample[0]: sim_time_ms = 0, parcel_01 at packing_station (80, 350)
        s0 = ObservationSample.model_validate(samples[0])
        res0 = service.ingest_sample(s0)
        assert res0.status == "applied"
        assert len(res0.facts_created) == 1
        assert res0.facts_created[0].object == "packing_station"

        # Construct same-zone confirmation at t2000 ms
        s2000_data = deepcopy(samples[0])
        s2000_data["sample_id"] = "11111111-1111-1111-1111-111111111111"
        s2000_data["sim_time_ms"] = 2000
        s2000_data["sample_sequence"] = 10
        s2000_data["captured_at"] = "2026-01-01T00:00:02Z"
        s2000_data["result"]["observed_at"] = "2026-01-01T00:00:02Z"
        s2000 = ObservationSample.model_validate(s2000_data)
        res2000 = service.ingest_sample(s2000)
        assert res2000.status == "applied"
        assert len(res2000.facts_created) == 0
        assert len(res2000.revisions_created) == 0

        # Construct late out-of-order sighting at t1000 ms with inspection_station coordinates
        s1000_data = deepcopy(samples[1])  # sample[1] has inspection_station coordinates (150, 390)
        s1000_data["sample_id"] = "22222222-2222-2222-2222-222222222222"
        s1000_data["run_id"] = str(s0.run_id)
        s1000_data["sim_time_ms"] = 1000
        s1000_data["sample_sequence"] = 5
        s1000_data["captured_at"] = "2026-01-01T00:00:01Z"
        s1000_data["result"]["observed_at"] = "2026-01-01T00:00:01Z"
        s1000 = ObservationSample.model_validate(s1000_data)

        res1000 = service.ingest_sample(s1000)
        # Must be retained as historical evidence because t2000 packing evidence is newer!
        assert res1000.status == "retained_as_historical"
        assert len(res1000.facts_created) == 0
        assert len(res1000.revisions_created) == 0

        # Verify active SQLite fact remains packing_station
        active_facts = repo.query_facts(FactQuery(subject="parcel_01", active_only=True))
        assert len(active_facts) == 1
        assert active_facts[0].object == "packing_station"

        # Verify epistemic summary as of t2000 reports packing_station
        summary = service.get_entity_belief_summary(
            run_id=s0.run_id, entity_id="parcel_01", current_sim_time_ms=2000
        )
        assert summary.supported_location == "packing_station"
        assert summary.verification_status == "verified"
        assert summary.freshness == "fresh"


def test_integration_reused_entity_across_runs_sqlite_isolation(
    phase2_fixtures: dict,
) -> None:
    """PR #40 Comment 2 regression with real SQLite repository."""
    with MemoryRepository(":memory:") as repo:
        service = TemporalBeliefIngestionService(repo, repo)
        samples = phase2_fixtures["samples"]

        # Run A: sample[1] at t7000 at inspection_station
        s_run_a = ObservationSample.model_validate(samples[1])
        res_a = service.ingest_sample(s_run_a)
        assert res_a.status == "applied"
        assert res_a.facts_created[0].object == "inspection_station"

        # Run B: sample at t0 reusing the same entity ID (parcel_01) at packing_station
        s_run_b_data = deepcopy(samples[0])
        s_run_b_data["sample_id"] = "33333333-3333-3333-3333-333333333333"
        s_run_b_data["run_id"] = "44444444-4444-4444-4444-444444444444"
        s_run_b_data["sim_time_ms"] = 0
        s_run_b = ObservationSample.model_validate(s_run_b_data)

        res_b = service.ingest_sample(s_run_b)
        # Must be applied as a new run's initial fact, NOT retained_as_historical!
        assert res_b.status == "applied"
        assert len(res_b.facts_created) == 1
        assert res_b.facts_created[0].object == "packing_station"

        # Run B summary at t0
        sum_b = service.get_entity_belief_summary(
            run_id=s_run_b.run_id, entity_id="parcel_01", current_sim_time_ms=0
        )
        assert sum_b.supported_location == "packing_station"
        assert sum_b.verification_status == "verified"
        assert sum_b.freshness == "fresh"

        # Run A summary at t7000
        sum_a = service.get_entity_belief_summary(
            run_id=s_run_a.run_id, entity_id="parcel_01", current_sim_time_ms=7000
        )
        assert sum_a.supported_location == "inspection_station"
        assert sum_a.verification_status == "verified"
        assert sum_a.freshness == "fresh"


def test_integration_partial_failure_retry_across_restore(phase2_fixtures: dict) -> None:
    """PR #40 Comment 1 regression with real SQLite export/restore snapshot."""
    samples = phase2_fixtures["samples"]
    # sample[0] has parcel_01 (80, 350 -> packing_station) and parcel_02 (220, 260 -> unmapped)
    # Modify sample to have parcel_01 and parcel_03 (inspection_station)
    sample_data = deepcopy(samples[0])
    sample_data["sample_id"] = "55555555-5555-5555-5555-555555555555"
    sample_data["result"]["measurements"]["objects"] = [
        {
            "object_id": "parcel_01",
            "apparent_color": "red",
            "bounding_box": {"width_cm": 10.0, "height_cm": 10.0},
            "position": {"x_cm": 80.0, "y_cm": 350.0},
            "frame_of_reference": "warehouse_map",
        },
        {
            "object_id": "parcel_03",
            "apparent_color": "green",
            "bounding_box": {"width_cm": 10.0, "height_cm": 10.0},
            "position": {"x_cm": 150.0, "y_cm": 390.0},
            "frame_of_reference": "warehouse_map",
        },
    ]
    sample = ObservationSample.model_validate(sample_data)

    with MemoryRepository(":memory:") as repo:
        service = TemporalBeliefIngestionService(repo, repo)
        # Store raw sample in ledger
        repo.record_observation_sample(sample)
        # Record only parcel_01 fact to simulate crash after first fact
        det1 = CameraDetection.model_validate(sample_data["result"]["measurements"]["objects"][0])
        service._record_initial_entity_location(sample, det1, "packing_station")

        # Verify only parcel_01 fact exists
        facts = repo.query_facts(FactQuery(active_only=True))
        assert len(facts) == 1
        assert facts[0].subject == "parcel_01"

        snapshot_bytes = repo.export_snapshot()

    # Restore snapshot in new repository (clean crash recovery)
    with MemoryRepository(":memory:") as restored_repo:
        restored_repo.restore_snapshot(snapshot_bytes)
        restored_service = TemporalBeliefIngestionService(restored_repo, restored_repo)

        # Retry ingestion of the exact same sample
        retry_res = restored_service.ingest_sample(sample)
        assert retry_res.status == "applied"
        # Only parcel_03 was newly created on retry
        assert len(retry_res.facts_created) == 1
        assert retry_res.facts_created[0].subject == "parcel_03"
        assert retry_res.facts_created[0].object == "inspection_station"

        # Now both parcel_01 and parcel_03 exist as active beliefs
        active_p1 = restored_repo.query_facts(FactQuery(subject="parcel_01", active_only=True))
        active_p3 = restored_repo.query_facts(FactQuery(subject="parcel_03", active_only=True))
        assert len(active_p1) == 1
        assert len(active_p3) == 1
        all_restored = restored_repo.query_facts(FactQuery(active_only=True))
        assert len(all_restored) == 2

        # Re-ingesting again returns duplicate_ignored
        dup_res = restored_service.ingest_sample(sample)
        assert dup_res.status == "duplicate_ignored"
        assert len(dup_res.facts_created) == 0


def test_integration_warehouse_sensors_to_ledger_to_belief_lifecycle(
    phase2_fixtures: dict,
) -> None:
    """Verifies complete end-to-end integration:

    WarehouseSensors -> ObservationLedger -> TemporalBeliefIngestionService -> Active Graph.
    """
    snapshot = SimulationSnapshot.model_validate(phase2_fixtures["snapshots"][0])
    sensors = WarehouseSensors(lambda: snapshot)

    with MemoryRepository(":memory:") as repo:
        synced_repo = GraphSyncedRepository.create(repo)
        service = TemporalBeliefIngestionService(synced_repo, repo)

        # 1. Acquire camera sample via WarehouseSensors
        req_cam = ObservationRequest(capability="camera_detect")
        sample_cam = sensors.sample(req_cam)
        assert isinstance(sample_cam, ObservationSample)
        assert sample_cam.request.capability == "camera_detect"

        # 2. Ingest into Tier 2 service
        res_cam = service.ingest_sample(sample_cam)
        assert res_cam.status == "applied"
        assert len(res_cam.facts_created) >= 1

        # 3. Verify SQLite Observation Ledger recorded the sample
        stored_s = repo.get_observation_sample(sample_cam.sample_id)
        assert stored_s is not None
        assert stored_s.sample_id == sample_cam.sample_id

        # 4. Verify Active Graph projection
        graph = synced_repo.get_graph()
        assert graph.has_node("parcel_01")
        assert graph.has_node("packing_station")

        # 5. Acquire LiDAR sample via WarehouseSensors
        req_lidar = ObservationRequest(capability="lidar_scan", parameters={"direction": "front"})
        sample_lidar = sensors.sample(req_lidar)
        assert sample_lidar.request.capability == "lidar_scan"

        res_lidar = service.ingest_sample(sample_lidar)
        assert res_lidar.status in ("applied", "unmapped")
        if res_lidar.status == "applied":
            assert res_lidar.route_summary is not None
            assert res_lidar.route_summary.status in ("clear", "blocked")
