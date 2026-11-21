"""Integration tests for DefaultSimulationExporter and CLI runner (P2-12 / WD-05)."""

import json
from pathlib import Path
from uuid import uuid4

from src.contracts.simulation_api import SimulationExportBundle, SimulationLoadRequest
from src.evaluation.cli import main as cli_main
from src.evaluation.exporter import DefaultSimulationExporter
from src.providers import ProviderConfig
from src.web.service import AgentService
from src.web.session_store import SessionStore


def test_default_simulation_exporter_via_service(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite")
    key = str(uuid4())

    service = AgentService(
        ProviderConfig(mode="rule-based", api_key=None),
        store=store,
        background=False,
    )
    exporter = DefaultSimulationExporter(session_lookup=service.get_session)
    service._exporter = exporter

    # Read initial pending state and load simulation scenario
    initial_state = service.read_live_simulation(key)
    loaded = service.load_warehouse(
        key,
        SimulationLoadRequest(
            preset="warehouse_inspection_v1",
            expected=initial_state,
            request_id=uuid4(),
        ),
    )
    assert loaded.state == "paused"

    # Export simulation
    bundle = service.export_simulation(key)
    assert isinstance(bundle, SimulationExportBundle)
    assert bundle.schema_version == 1
    assert bundle.run_id == loaded.run_id
    assert bundle.scenario.scenario_id == "warehouse_inspection_v1"
    assert isinstance(bundle.observations, list)
    assert isinstance(bundle.belief_changes, list)

    # Redaction checks: no secret credentials or session database paths leaked
    bundle_dict = bundle.model_dump(mode="json")
    bundle_json = json.dumps(bundle_dict)
    assert "api_key" not in bundle_dict
    assert "session_id" not in bundle_dict
    assert key not in bundle_json
    assert str(tmp_path) not in bundle_json

    service.shutdown()
    store.close()


def test_cli_runner_single_scenario(tmp_path: Path) -> None:
    out_dir = tmp_path / "eval_out"
    exit_code = cli_main(
        [
            "--scenario",
            "scenario_02_visible_parcel_relocation",
            "--mode",
            "evidence_only",
            "--output-dir",
            str(out_dir),
            "--format",
            "both",
        ]
    )
    assert exit_code == 0
    assert (out_dir / "evaluation_summary.json").exists()
    assert (out_dir / "evaluation_report.md").exists()

    summary = json.loads((out_dir / "evaluation_summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] == 2
    assert summary["failed"] == 0
