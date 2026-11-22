"""Physical replay uses recorded runtime evidence and the real patrol controller."""

from types import SimpleNamespace
from uuid import NAMESPACE_DNS, uuid5

import pytest

from src.contracts import ObservationRequest
from src.evaluation.models import ExpectedQueryEvaluation
from src.evaluation.replay import replay_world
from src.evaluation.scenarios import scenario_09_delayed_older_observation
from src.web.simulation_runtime import SimulationRuntime


def test_world_rerun_matches_recording_and_rejects_changed_pose(monkeypatch) -> None:
    bundle = scenario_09_delayed_older_observation()
    bundle.metadata = {}
    bundle.presenter_inputs = []
    runtime = SimulationRuntime(
        bundle.audience_truth.scenario,
        epoch_utc=bundle.epoch_utc,
        ingest=lambda _sample: None,
        id_factory=lambda: bundle.run_id,
    )
    samples = []
    for sequence in range(2):
        for _ in range(5):
            runtime.step()
        sample = runtime.sensors.sample(ObservationRequest(capability="robot_pose"))
        samples.append(
            sample.model_copy(
                update={
                    "sample_id": uuid5(NAMESPACE_DNS, f"physical-replay-{sequence}"),
                    "sample_sequence": sequence,
                }
            )
        )
    bundle.recorded_samples = samples
    bundle.expected_evaluations = [
        ExpectedQueryEvaluation(
            query_id="physical_pose",
            prompt="Where is the robot?",
            query_sim_time_ms=samples[-1].sim_time_ms,
            after_sample_sequence=1,
            expected_status="verified",
            conflict_or_uncertainty_rationale="Verify recorded patrol poses.",
        )
    ]

    class FakeAgent:
        def interpret(self, _prompt):
            return None

        def capture(self, _prompt, _intent, **_kwargs):
            return lambda: SimpleNamespace(status="verified", answer="")

        def close(self):
            pass

    monkeypatch.setattr("src.evaluation.replay.GroundedAgent.create", lambda **_: FakeAgent())
    assert all(log.passed for log in replay_world(bundle))

    sample = bundle.recorded_samples[0]
    changed_pose = sample.observer_pose.model_copy(update={"x_cm": sample.observer_pose.x_cm + 1})
    bundle.recorded_samples[0] = sample.model_copy(update={"observer_pose": changed_pose})
    logs = replay_world(bundle)
    assert any(
        not log.passed and "differs from recorded evidence" in (log.failure_reason or "")
        for log in logs
    )


def test_synthetic_fault_injection_is_not_a_physical_recording() -> None:
    with pytest.raises(ValueError, match="Synthetic evidence scenarios"):
        replay_world(scenario_09_delayed_older_observation())
