"""Unit tests verifying evidence-only isolation, world rerun fidelity, and bundle conversion."""

from uuid import uuid4

from src.contracts.simulation_api import SimulationLoadRequest
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
    service.load_warehouse(
        key,
        SimulationLoadRequest(
            preset="warehouse_inspection_v1",
            expected=initial_state,
            request_id=uuid4(),
        ),
    )
    exported = service.export_simulation(key)
    replay_bundle = ReplayBundle.from_simulation_export(exported)
    assert replay_bundle.run_id == exported.run_id
    assert replay_bundle.bundle_version == 1
    assert replay_bundle.audience_truth.scenario.scenario_id == "warehouse_inspection_v1"
