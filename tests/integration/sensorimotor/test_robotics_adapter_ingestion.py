"""Integration test verifying end-to-end telemetry ingestion from RecordedRoboticsSensorAdapter.

Demonstrates that external recorded robotics data flows through:
1. RecordedRoboticsSensorAdapter (coordinate transforms, clock mapping, deterministic UUIDs)
2. MemoryRepository (Tier 1 SQLite durable observation ledger)
3. TemporalBeliefIngestionService (Tier 2 belief revisions and audit trails)
producing identical logical facts and revisions as simulated sensor evidence.
"""

from __future__ import annotations

from pathlib import Path

from src.contracts.models import FactQuery, ObservationRequest
from src.contracts.simulation import (
    TemporalObservationQuery,
)
from src.declarative.active_graph import GraphSyncedRepository
from src.declarative.memory_repository import MemoryRepository
from src.procedural.temporal_belief_ingestion import TemporalBeliefIngestionService
from src.sensorimotor.robotics_adapter import RecordedRoboticsSensorAdapter

FIXTURE_PATH = Path("tests/fixtures/phase_two/robotics_recording.json")


def test_robotics_telemetry_ingestion_and_belief_revisions() -> None:
    """Verifies recorded external robot telemetry updates SQLite facts and belief graphs."""
    adapter = RecordedRoboticsSensorAdapter.from_file(FIXTURE_PATH)

    with MemoryRepository(":memory:") as repo:
        synced_repo = GraphSyncedRepository.create(repo)
        ingestion_service = TemporalBeliefIngestionService(synced_repo, repo)

        # ---------------------------------------------------------------------
        # 1. Ingest Sample 0 (t=0s): Camera detection of parcel_01 at packing_station
        # ---------------------------------------------------------------------
        sample0 = adapter.play_next()
        assert sample0.sample_sequence == 0
        assert sample0.sim_time_ms == 0

        # Ingest into beliefs (which also records into the ledger)
        res0 = ingestion_service.ingest_sample(sample0)
        assert res0.status == "applied"
        assert len(res0.facts_created) == 1

        # Verify parcel_01 is believed to be at packing_station
        facts0 = repo.query_facts(FactQuery(subject="parcel_01", active_only=True))
        assert len(facts0) == 1
        assert facts0[0].predicate == "located_at"
        assert facts0[0].object == "packing_station"

        # Verify NetworkX projection reflects the location belief
        graph0 = synced_repo.get_graph()
        assert graph0.has_node("parcel_01")
        assert graph0.has_node("packing_station")

        # ---------------------------------------------------------------------
        # 2. Ingest Sample 1 (t=5s): Clear front LiDAR scan
        # ---------------------------------------------------------------------
        sample1 = adapter.play_next()
        res1 = ingestion_service.ingest_sample(sample1)
        assert res1.status in ("applied", "no_change")

        # ---------------------------------------------------------------------
        # 3. Ingest Sample 2 (t=7s): Camera detection of parcel_01 at inspection_station
        # ---------------------------------------------------------------------
        sample2 = adapter.play_next()
        assert sample2.sample_sequence == 2
        assert sample2.sim_time_ms == 7000
        res2 = ingestion_service.ingest_sample(sample2)
        assert res2.status == "applied"

        # Verify revision: parcel_01 moved to inspection_station
        facts2 = repo.query_facts(FactQuery(subject="parcel_01", active_only=True))
        assert len(facts2) == 1
        assert facts2[0].object == "inspection_station"

        # Verify previous fact was superseded
        all_facts2 = repo.query_facts(FactQuery(subject="parcel_01", active_only=False))
        assert len(all_facts2) == 2
        superseded = [f for f in all_facts2 if f.superseded_by is not None]
        assert len(superseded) == 1
        assert superseded[0].object == "packing_station"

        # ---------------------------------------------------------------------
        # 4. Ingest Sample 3 (t=10s): LiDAR obstacle detection on route_B
        # ---------------------------------------------------------------------
        sample3 = adapter.play_next()
        assert sample3.sample_sequence == 3
        res3 = ingestion_service.ingest_sample(sample3)
        assert res3.status == "applied"

        route_b_facts = repo.query_facts(FactQuery(subject="route_B", active_only=True))
        assert any(f.predicate == "status_is" and f.object == "blocked" for f in route_b_facts)

        # ---------------------------------------------------------------------
        # 5. Ingest remaining samples (ambient light, pose, empty scan, outage, clear)
        # ---------------------------------------------------------------------
        remaining = adapter.play_all()
        for s in remaining:
            ingestion_service.ingest_sample(s)

        # Verify route_B cleared after sample 8
        final_route_b = repo.query_facts(FactQuery(subject="route_B", active_only=True))
        assert any(f.predicate == "status_is" and f.object == "clear" for f in final_route_b)

        # ---------------------------------------------------------------------
        # 6. Verify Observation Ledger Queries (Tier 1 SQLite)
        # ---------------------------------------------------------------------
        query_all = repo.query_observations(
            TemporalObservationQuery(run_id=adapter.run_id, mode="samples")
        )
        assert len(query_all) == adapter.total_records

        latest_camera = repo.get_latest_sample(
            run_id=adapter.run_id,
            capability="camera_detect",
        )
        assert latest_camera is not None
        assert latest_camera.sim_time_ms == 20000


def test_idempotent_reingestion() -> None:
    """Verifies that re-ingesting identical recorded messages produces idempotent behavior."""
    adapter = RecordedRoboticsSensorAdapter.from_file(FIXTURE_PATH)
    samples = adapter.play_all()

    with MemoryRepository(":memory:") as repo:
        synced_repo = GraphSyncedRepository.create(repo)
        service = TemporalBeliefIngestionService(synced_repo, repo)

        # First pass
        for s in samples:
            service.ingest_sample(s)

        initial_facts = repo.query_facts(FactQuery(active_only=True))

        # Second pass with exact same samples
        for s in samples:
            service.ingest_sample(s)

        final_facts = repo.query_facts(FactQuery(active_only=True))
        assert len(initial_facts) == len(final_facts)


def test_distinct_camera_views_are_stored_and_replay_idempotently() -> None:
    adapter = RecordedRoboticsSensorAdapter.from_file(FIXTURE_PATH, auto_advance=False)
    broad = adapter.sample()
    request = ObservationRequest(capability="camera_detect", target="parcel_01")
    targeted = adapter.sample(request)
    assert broad.sample_id != targeted.sample_id
    assert broad.result.observation_id != targeted.result.observation_id
    assert targeted == adapter.sample(request)

    with MemoryRepository(":memory:") as repo:
        service = TemporalBeliefIngestionService(GraphSyncedRepository.create(repo), repo)
        assert service.ingest_sample(broad).status != "rejected"
        assert service.ingest_sample(targeted).status != "rejected"
        assert service.ingest_sample(targeted).status == "duplicate_ignored"
