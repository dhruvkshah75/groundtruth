"""Integration tests executing all ten epistemic evaluation scenarios (P2-12 / WD-05)."""

import pytest

from src.evaluation.replay import run_all_scenarios, run_scenario
from src.evaluation.scenarios import ALL_SCENARIO_BUILDERS


@pytest.mark.parametrize("scenario_id", list(ALL_SCENARIO_BUILDERS.keys()))
def test_each_scenario_passes_evidence_only(scenario_id: str) -> None:
    logs = run_scenario(scenario_id, mode="evidence_only")
    assert len(logs) > 0, f"No logs produced for {scenario_id}"
    for log in logs:
        assert log.passed, f"{scenario_id} [evidence_only] failed: {log.failure_reason}"
        assert log.mode == "evidence_only"
        assert log.conflict_description, "Missing conflict or uncertainty rationale"
        assert log.actual_outcome["status"] == log.expected_outcome["status"]


@pytest.mark.parametrize("scenario_id", list(ALL_SCENARIO_BUILDERS.keys()))
def test_each_scenario_passes_world_rerun(scenario_id: str) -> None:
    logs = run_scenario(scenario_id, mode="world_rerun")
    assert len(logs) > 0, f"No logs produced for {scenario_id}"
    for log in logs:
        assert log.passed, f"{scenario_id} [world_rerun] failed: {log.failure_reason}"
        assert log.mode == "world_rerun"
        assert log.conflict_description, "Missing conflict or uncertainty rationale"
        assert log.actual_outcome["status"] == log.expected_outcome["status"]


def test_run_all_scenarios_deterministic_and_clean() -> None:
    logs = run_all_scenarios(mode="both")
    assert len(logs) == 28, f"Expected 28 evaluation logs across 10 scenarios, got {len(logs)}"
    failures = [log for log in logs if not log.passed]
    assert len(failures) == 0, (
        f"Encountered {len(failures)} failures: {[f.failure_reason for f in failures]}"
    )
