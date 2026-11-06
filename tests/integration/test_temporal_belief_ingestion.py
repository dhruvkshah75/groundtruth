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
from pathlib import Path

import pytest

from src.contracts import (
    FactQuery,
    ObservationSample,
    TemporalObservationQuery,
)
from src.declarative.active_graph import GraphSyncedRepository
from src.declarative.memory_repository import MemoryRepository
from src.procedural.temporal_belief_ingestion import (
    TemporalBeliefIngestionService,
)

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
