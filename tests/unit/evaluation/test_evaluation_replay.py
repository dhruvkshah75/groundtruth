"""Unit tests verifying evidence-only isolation, world rerun fidelity, and bundle conversion."""

import json
from pathlib import Path
from uuid import uuid4

from src.contracts.simulation import ObservationSample
from src.contracts.simulation_api import SimulationDisruptRequest, SimulationLoadRequest
from src.evaluation.exporter import DefaultSimulationExporter
from src.evaluation.models import ReplayBundle
from src.evaluation.replay import (
    replay_evidence,
    replay_world,
)
from src.evaluation.scenarios import (
    scenario_01_map_clear_vs_cart_blocked,
    scenario_09_delayed_older_observation,
)
from src.providers import ProviderConfig
from src.web.service import AgentService
from src.web.session_store import SessionStore


def test_evidence_only_replay_isolation() -> None:
    """Evidence-only replay operates entirely through SQLite ledger with no live world state."""
    bundle = scenario_01_map_clear_vs_cart_blocked()
    logs = replay_evidence(bundle)
    assert len(logs) == 3
    assert all(log.passed for log in logs)
    assert all(log.mode == "evidence_only" for log in logs)


def test_world_rerun_catches_broken_physical_timeline() -> None:
    """World rerun executes through real SimulationEngine and WarehouseSensors.

    If the physical cart is never placed in the route, the LiDAR scan will detect clear
    instead of blocked, and world rerun must catch the failure.
    """
    bundle = scenario_01_map_clear_vs_cart_blocked()
    recordings = json.loads(
        (Path(__file__).parents[3] / "src/evaluation/fixtures/world_recordings.json").read_text()
    )
    bundle.metadata.pop("synthetic_evidence")
    bundle.presenter_inputs = []
    recording = recordings[bundle.scenario_id]
    bundle.audience_truth.scenario = type(bundle.audience_truth.scenario).model_validate(
        recording["scenario"]
    )
    bundle.recorded_samples = [ObservationSample.model_validate(s) for s in recording["samples"]]
    # Mutate physical scene: move cart far away so route_A is NOT blocked at t=0
    cart = next(e for e in bundle.audience_truth.scenario.entities if e.entity_id == "cart_01")
    cart.footprint.x_cm = 600.0
    cart.footprint.y_cm = 500.0

    logs = replay_world(bundle)
    # It must fail because physical reality does not match the assertion
    assert any(not log.passed for log in logs)


def test_delayed_sample_arrival_preserves_latest_belief() -> None:
    """Delayed older observation arriving after a newer one does not overwrite active belief."""
    bundle = scenario_09_delayed_older_observation()
    logs = replay_evidence(bundle)
    assert len(logs) == 1
    log = logs[0]
    assert log.passed
    assert log.actual_outcome["status"] == "verified"


def test_export_then_replay_roundtrip(tmp_path) -> None:
    """An export from SimulationExportBundle must convert to ReplayBundle and replay cleanly."""
    store = SessionStore(tmp_path / "sessions.sqlite")
    key = str(uuid4())
    service = AgentService(
        ProviderConfig(mode="rule-based", api_key=None),
        store=store,
        background=False,
    )
    exporter = DefaultSimulationExporter(session_lookup=service.get_session)
    service._exporter = exporter
    initial_state = service.read_live_simulation(key)
    loaded = service.load_warehouse(
        key,
        SimulationLoadRequest(
            preset="warehouse_inspection_v1",
            expected=initial_state,
            request_id=uuid4(),
        ),
    )
    service.disrupt_simulation(
        key,
        SimulationDisruptRequest.model_validate(
            {
                "expected": {"run_id": loaded.run_id, "generation": loaded.generation},
                "request_id": uuid4(),
                "disruption": {"kind": "cart_block"},
            }
        ),
    )
    exported = service.export_simulation(key)
    replay_bundle = ReplayBundle.from_simulation_export(exported)
    assert replay_bundle.run_id == exported.run_id
    assert replay_bundle.bundle_version == 1
    assert replay_bundle.audience_truth.scenario.scenario_id == "warehouse_inspection_v1"
    assert exported.observations
    assert [s.sample_id for s in replay_bundle.recorded_samples] == [
        s.sample_id for s in exported.observations
    ]
    assert replay_bundle.static_map.zones
    assert replay_bundle.presenter_inputs == [
        event.model_dump(mode="json") for event in exported.presenter_events
    ]
    assert replay_bundle.presenter_inputs
    service.shutdown()
    store.close()


def test_response_assertions_require_answer_and_response_evidence() -> None:
    from types import SimpleNamespace

    from src.evaluation.models import ExpectedQueryEvaluation
    from src.evaluation.replay import _response_assertions

    sample_id = uuid4()
    expected = ExpectedQueryEvaluation(
        query_id="location",
        prompt="Where is the parcel?",
        query_sim_time_ms=0,
        expected_status="verified",
        expected_location="packing_station",
        expected_response_evidence_sample_ids=[sample_id],
        conflict_or_uncertainty_rationale="Check the returned location and citation.",
    )
    response = SimpleNamespace(
        answer="The parcel is at inspection station.",
        react_trace=SimpleNamespace(operations=[]),
        location_assessment=None,
    )
    failures = _response_assertions(response, expected)
    assert len(failures) == 2
    response.answer = "The parcel is at packing station."
    response.react_trace.operations = [{"evidence_ids": [str(sample_id)]}]
    assert _response_assertions(response, expected) == []
    response.location_assessment = SimpleNamespace(
        current_location="inspection_station", current_sighting=None, last_sighting=None
    )
    assert any(
        "assessment mismatch" in failure for failure in _response_assertions(response, expected)
    )


def test_physical_replay_is_independent_of_playback_rate() -> None:
    recordings = json.loads(
        (Path(__file__).parents[3] / "src/evaluation/fixtures/world_recordings.json").read_text()
    )
    outcomes = []
    for rate in (0.25, 0.5, 1.0):
        bundle = scenario_01_map_clear_vs_cart_blocked()
        recording = recordings[bundle.scenario_id]
        bundle.metadata.pop("synthetic_evidence")
        bundle.presenter_inputs = []
        bundle.audience_truth.scenario = type(bundle.audience_truth.scenario).model_validate(
            recording["scenario"]
        )
        bundle.audience_truth.scenario.config.playback_rate = rate
        bundle.recorded_samples = [
            ObservationSample.model_validate(s) for s in recording["samples"]
        ]
        logs = replay_world(bundle)
        assert all(log.passed for log in logs), [log.failure_reason for log in logs]
        outcomes.append([log.actual_outcome for log in logs])
    assert outcomes[0] == outcomes[1] == outcomes[2]


def test_map_only_baseline_compares_actual_map_and_recorded_lidar() -> None:
    from src.evaluation.cli import map_only_baseline

    assert map_only_baseline() == ("clear", "blocked")
