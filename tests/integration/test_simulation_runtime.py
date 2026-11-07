"""Manual runtime integration against the actual Phase 2 components."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.contracts.simulation import ScenarioDefinition
from src.declarative.memory_repository import MemoryRepository
from src.procedural.temporal_belief_ingestion import TemporalBeliefIngestionService
from src.web.simulation_runtime import SimulationRuntime


def test_pump_patrol_and_ingestion_are_driven_by_simulation_time():
    data = json.loads((Path(__file__).parents[1] / "fixtures/phase_two/contracts.json").read_text())
    scenario = ScenarioDefinition.model_validate(data["valid"]["scenario"])
    memory = MemoryRepository(":memory:")
    ingest = TemporalBeliefIngestionService(memory)
    samples = []

    def record(sample):
        ingest.ingest_sample(sample)
        samples.append(sample)

    now = [0.0]
    runtime = SimulationRuntime(
        scenario, epoch_utc=datetime(2026, 1, 1, tzinfo=UTC), ingest=record, now=lambda: now[0]
    )
    assert [s.request.capability for s in samples] == [
        "robot_pose",
        "lidar_scan",
        "camera_detect",
        "ambient_light",
    ]
    assert all(s.sim_time_ms == 0 for s in samples)
    runtime.play()
    now[0] = 1.0
    assert runtime.pump() == 5
    assert runtime.engine.snapshot().status.sim_time_ms == 500
    assert runtime.engine.snapshot().robot_pose.y_cm > scenario.robot_pose.y_cm
    assert [s.sample_sequence for s in samples] == list(range(len(samples)))
    assert [s.request.capability for s in samples if s.sim_time_ms == 500] == [
        "robot_pose",
        "lidar_scan",
        "camera_detect",
        "ambient_light",
    ]
    assert all(memory.get_observation_sample(s.sample_id) is not None for s in samples)
    runtime.pause()
    now[0] += 10
    assert runtime.pump() == 0
    runtime.step()
    assert runtime.engine.snapshot().status.sim_time_ms == 600
    memory.close()


def test_ingestion_error_pauses_run_and_propagates():
    data = json.loads((Path(__file__).parents[1] / "fixtures/phase_two/contracts.json").read_text())
    scenario = ScenarioDefinition.model_validate(data["valid"]["scenario"])
    calls = [0]

    def ingest(_sample):
        calls[0] += 1
        if calls[0] > 4:
            raise RuntimeError("ledger failure")

    now = [0.0]
    runtime = SimulationRuntime(
        scenario, epoch_utc=datetime(2026, 1, 1, tzinfo=UTC), ingest=ingest, now=lambda: now[0]
    )
    runtime.play()
    now[0] = 1.0
    with pytest.raises(RuntimeError, match="ledger failure"):
        runtime.pump()
    assert runtime.engine.snapshot().status.state == "paused"
    assert not runtime.clock.running
