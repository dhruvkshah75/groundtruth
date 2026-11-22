"""Integration tests for DefaultSimulationExporter and CLI runner (P2-12 / WD-05)."""

import json
from pathlib import Path
from uuid import uuid4

from src.contracts.simulation_api import (
    SimulationDisruptRequest,
    SimulationExportBundle,
    SimulationLoadRequest,
)
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

    disruption = service.disrupt_simulation(
        key,
        SimulationDisruptRequest.model_validate(
            {
                "expected": {"run_id": loaded.run_id, "generation": loaded.generation},
                "request_id": uuid4(),
                "disruption": {"kind": "lighting", "color_cast": "yellow", "intensity": 0.5},
            }
        ),
    )
    runtime = service.get_session(key).require_simulation()
    for _ in range(5):
        runtime.step()
    bundle = service.export_simulation(key)
    assert isinstance(bundle, SimulationExportBundle)
    assert bundle.schema_version == 1
    assert bundle.run_id == loaded.run_id
    assert bundle.scenario.scenario_id == "warehouse_inspection_v1"
    assert bundle.presenter_events == [disruption.event]
    assert bundle.sim_time_ms == 500
    assert isinstance(bundle.observations, list)
    assert isinstance(bundle.belief_changes, list)

    # Redaction checks: no secret credentials or session database paths leaked
    bundle_dict = bundle.model_dump(mode="json")
    bundle_json = json.dumps(bundle_dict)
    assert "api_key" not in bundle_dict
    assert "session_id" not in bundle_dict
    assert key not in bundle_json
    assert str(tmp_path) not in bundle_json

    bundle_path = tmp_path / "export.json"
    bundle_path.write_text(bundle_json, encoding="utf-8")
    assert cli_main(["--bundle", str(bundle_path), "--output-dir", str(tmp_path / "out")]) != 0

    camera = next(s for s in bundle.observations if s.request.capability == "camera_detect")
    expectations_path = tmp_path / "expectations.json"
    expectations_path.write_text(
        json.dumps(
            [
                {
                    "query_id": "exported_parcel",
                    "prompt": "Where is parcel_01 now?",
                    "query_sim_time_ms": 0,
                    "expected_status": "verified",
                    "expected_location": "packing_station",
                    "expected_response_evidence_sample_ids": [str(camera.sample_id)],
                    "conflict_or_uncertainty_rationale": "Verify exported camera evidence.",
                }
            ]
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "verified"
    assert (
        cli_main(
            [
                "--bundle",
                str(bundle_path),
                "--expectations",
                str(expectations_path),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )
    results = json.loads((output_dir / "evaluation_summary.json").read_text(encoding="utf-8"))
    assert results["passed"] == 2
    assert {log["mode"] for log in results["logs"]} == {"evidence_only", "world_rerun"}

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
