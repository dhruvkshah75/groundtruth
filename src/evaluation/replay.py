"""Replay engine implementing evidence-only replay and physical world rerun (P2-12)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from itertools import count
from typing import Any, Literal
from uuid import UUID, uuid5

from src.agent import GroundedAgent
from src.contracts import (
    FactAssertion,
    FactQuery,
    ObservationRequest,
    ObservationSample,
    ObservationUnavailable,
    SensorObservation,
    StoredFact,
    TemporalObservationQuery,
)
from src.declarative.memory_repository import MemoryRepository
from src.evaluation.models import (
    ReplayBundle,
    ScenarioEvaluationLog,
)
from src.evaluation.scenarios import ALL_SCENARIO_BUILDERS
from src.procedural.temporal_belief_ingestion import (
    StaticRouteMap,
    StationZoneMap,
    TemporalBeliefIngestionService,
)
from src.sensorimotor.mock_environment import MockEnvironment
from src.sensorimotor.simulation_engine import EVENT
from src.sensorimotor.warehouse_sensors import WarehouseSensors
from src.sensorimotor.world_models import RobotState, WorldState


def validate_bundle(bundle: ReplayBundle) -> None:
    """Validate replay bundle version, identity references, and sample ordering."""
    if bundle.bundle_version != 1:
        raise ValueError(f"Unsupported bundle version: {bundle.bundle_version}. Supported: 1.")
    if bundle.schema_version != 1:
        raise ValueError(f"Unsupported schema version: {bundle.schema_version}. Supported: 1.")

    if not bundle.scenario_id:
        raise ValueError("bundle scenario_id must not be empty.")

    run_id = bundle.run_id
    seen_ids: set[UUID] = set()
    last_seq = -1
    last_time = -1
    delayed = False

    for sample in bundle.recorded_samples:
        if sample.run_id != run_id:
            raise ValueError(
                f"Sample {sample.sample_id} run_id {sample.run_id} does not match bundle {run_id}"
            )
        if sample.sample_id in seen_ids:
            raise ValueError(f"Duplicate sample_id detected in bundle: {sample.sample_id}")
        seen_ids.add(sample.sample_id)

        if sample.sample_sequence <= last_seq:
            raise ValueError("Sample sequences must increase in arrival order")
        delayed |= sample.sim_time_ms < last_time
        last_seq = sample.sample_sequence
        last_time = sample.sim_time_ms

    previous_count = 0
    for expected in bundle.expected_evaluations:
        cutoff = expected.after_sample_sequence
        if delayed and cutoff is None:
            raise ValueError("Delayed samples require after_sample_sequence for each query")
        visible = [
            sample
            for sample in bundle.recorded_samples
            if (
                sample.sample_sequence <= cutoff
                if cutoff is not None
                else sample.sim_time_ms <= expected.query_sim_time_ms
            )
        ]
        if len(visible) < previous_count:
            raise ValueError("Evaluation queries cannot rewind sample arrivals")
        if any(sample.sim_time_ms > expected.query_sim_time_ms for sample in visible):
            raise ValueError("Evaluation query includes evidence captured in the future")
        previous_count = len(visible)


def _replay_repository(bundle: ReplayBundle) -> MemoryRepository:
    ids = count()
    return MemoryRepository(
        clock=lambda: bundle.epoch_utc,
        uuid_factory=lambda: uuid5(bundle.run_id, f"replay-fact-or-event:{next(ids)}"),
    )


class IsolatedEvidenceEnvironment:
    """Isolated environment interface for evidence-only replay.

    Provides capability definitions but zero live physical/world state. All observations
    are served strictly from the recorded observation ledger (Tier 1 SQLite).
    """

    def __init__(self, repo: MemoryRepository, run_id: UUID, current_sim_time_ms: int = 0) -> None:
        self._repo = repo
        self._run_id = run_id
        self.sim_time_ms = current_sim_time_ms
        self.run_id = run_id
        world = WorldState(
            robot=RobotState(
                robot_id="robot_1",
                x_cm=0.0,
                y_cm=0.0,
                direction="north",
                location="isolated",
                frame_of_reference="isolated",
            )
        )
        mock = MockEnvironment(world=world)
        self._capabilities = mock.get_capabilities()

    def get_capabilities(self) -> list[Any]:
        return [cap.model_copy(deep=True) for cap in self._capabilities]

    def set_time(self, sim_time_ms: int) -> None:
        self.sim_time_ms = sim_time_ms

    def observe(self, request: ObservationRequest) -> SensorObservation | ObservationUnavailable:
        query = TemporalObservationQuery(
            run_id=self._run_id,
            mode="latest",
            capability=request.capability,
            entity_id=request.target,
            end_ms=self.sim_time_ms,
        )
        samples = self._repo.query_observations(query)
        if samples and samples[0].sim_time_ms <= self.sim_time_ms:
            res = samples[0].result
            if isinstance(res, (SensorObservation, ObservationUnavailable)):
                return res
        return ObservationUnavailable(
            capability=request.capability,
            reason="sensor_unavailable",
            reported_at=datetime.now(UTC),
            message="No recorded observation in evidence ledger for this time.",
        )


def _verify_active_fact(
    repo: MemoryRepository, expected_fact: dict[str, Any]
) -> tuple[bool, str | None]:
    """Verify that an active fact exists matching the specified attributes."""
    active_facts = repo.query_facts(FactQuery(active_only=True))
    for f in active_facts:
        if f.superseded_by is not None:
            continue
        match = True
        for k, v in expected_fact.items():
            if getattr(f, k, None) != v:
                match = False
                break
        if match:
            return True, None
    found_summary = [f"{f.subject} {f.predicate} {f.object}" for f in active_facts]
    return (
        False,
        f"Expected active fact {expected_fact} not found among active facts: {found_summary}",
    )


def _verify_unexpected_active_fact(
    repo: MemoryRepository, unexpected_fact: dict[str, Any]
) -> tuple[bool, str | None]:
    """Verify that NO active fact exists matching the specified attributes."""
    active_facts = repo.query_facts(FactQuery(active_only=True))
    for f in active_facts:
        if f.superseded_by is not None:
            continue
        match = True
        for k, v in unexpected_fact.items():
            if getattr(f, k, None) != v:
                match = False
                break
        if match:
            return (
                False,
                f"Unexpected active fact found: {f.subject} {f.predicate} {f.object} "
                f"(expected absence of {unexpected_fact})",
            )
    return True, None


def _get_fact_from_repo(repo: MemoryRepository, fact_id: str | None) -> StoredFact | None:
    """Retrieve stored fact by fact_id directly from the repository's database."""
    if not fact_id:
        return None
    row = repo._conn.execute(
        "SELECT * FROM facts WHERE fact_id = ?",
        (str(fact_id),),
    ).fetchone()
    if row:
        return repo._row_to_stored_fact(row)
    return None


def _verify_revision(
    repo: MemoryRepository, expected_revision: dict[str, Any]
) -> tuple[bool, str | None]:
    """Verify that an audit revision chain exists matching predecessor/successor/rule."""
    pred_obj = expected_revision.get("predecessor_object")
    succ_obj = expected_revision.get("successor_object")
    rule = expected_revision.get("policy_rule")

    rows = repo._conn.execute(
        "SELECT event_id, old_fact_id, new_fact_id, policy_rule FROM audit_events "
        "WHERE event_type IN ('revision', 'belief_revision')"
    ).fetchall()

    for r in rows:
        old_id = r["old_fact_id"]
        new_id = r["new_fact_id"]
        event_rule = r["policy_rule"]

        if rule and event_rule != rule:
            continue

        old_fact = _get_fact_from_repo(repo, old_id)
        new_fact = _get_fact_from_repo(repo, new_id)

        if pred_obj and (not old_fact or old_fact.object != pred_obj):
            continue
        if succ_obj and (not new_fact or new_fact.object != succ_obj):
            continue

        if old_fact and new_fact and old_fact.superseded_by != new_fact.fact_id:
            return False, f"Predecessor fact {old_id} superseded_by does not match {new_id}"

        return True, None

    return (
        False,
        f"Expected revision matching {expected_revision} not found in audit trail.",
    )


def _verify_evidence_sample_id(repo: MemoryRepository, sample_id: UUID) -> tuple[bool, str | None]:
    """Verify that an observation sample exists in the durable ledger."""
    s = repo.get_observation_sample(sample_id)
    if s is None:
        return False, f"Sample {sample_id} not found in observation ledger."
    return True, None


def _register_sample_aliases(repo: MemoryRepository, s: ObservationSample) -> None:
    if s.request.target:
        repo.add_alias(s.request.target, s.request.target)
    if isinstance(s.result, SensorObservation) and isinstance(s.result.measurements, dict):
        objs = s.result.measurements.get("objects", [])
        for obj in objs:
            oid = obj.get("object_id") if isinstance(obj, dict) else getattr(obj, "object_id", None)
            if oid:
                repo.add_alias(oid, oid)


def _response_assertions(response: Any, expected: Any) -> list[str]:
    """Check the response itself, independently of facts present in the ledger."""
    failures: list[str] = []
    answer = getattr(response, "answer", "") or ""
    for text in expected.expected_answer_contains:
        if text.casefold() not in answer.casefold():
            failures.append(f"Expected answer to include '{text}': {answer}")
    if expected.expected_location is not None:
        location = expected.expected_location.replace("_", " ").casefold()
        normalized = answer.replace("_", " ").casefold()
        if not re.search(r"(?<!\w)" + re.escape(location) + r"(?!\w)", normalized):
            failures.append(
                f"Expected location '{expected.expected_location}' not in answer: {answer}"
            )
        assessment = getattr(response, "location_assessment", None)
        if assessment is not None:
            sighting = assessment.current_sighting or assessment.last_sighting
            actual_location = assessment.current_location or (
                sighting.station_id if sighting else None
            )
            if actual_location != expected.expected_location:
                failures.append(
                    f"Location assessment mismatch: expected {expected.expected_location}, "
                    f"got {actual_location}."
                )

    evidence = _response_evidence_ids(response)
    for sample_id in expected.expected_response_evidence_sample_ids:
        if str(sample_id) not in evidence:
            failures.append(f"Sample {sample_id} not cited in response evidence.")
    return failures


def _response_evidence_ids(response: Any) -> set[str]:
    trace = getattr(response, "react_trace", None)
    return {
        str(evidence_id)
        for operation in getattr(trace, "operations", [])
        for evidence_id in operation.get("evidence_ids", [])
    }


def replay_evidence(bundle: ReplayBundle) -> list[ScenarioEvaluationLog]:
    """Execute evidence-only replay strictly isolated from mock world / live simulator."""
    validate_bundle(bundle)

    repo = _replay_repository(bundle)
    zone_map = StationZoneMap(zones=bundle.static_map.zones)
    route_map = StaticRouteMap(routes=bundle.static_map.routes)

    # Register single aliases
    for alias, target in bundle.metadata.get("aliases", {}).items():
        repo.add_alias(alias, target)

    # Register multi-aliases
    for alias, targets in bundle.metadata.get("aliases_multi", {}).items():
        for target in targets:
            repo.add_alias(alias, target)

    # Pre-seed initial facts
    for fact_dict in bundle.metadata.get("initial_facts", []):
        repo.record_fact(FactAssertion.model_validate(fact_dict))

    ingestion = TemporalBeliefIngestionService(
        fact_repository=repo,
        observation_ledger=repo,
        zone_map=zone_map,
        route_map=route_map,
    )

    env = IsolatedEvidenceEnvironment(repo, bundle.run_id, current_sim_time_ms=0)
    agent = GroundedAgent.create(memory=repo, environment=env)
    logs: list[ScenarioEvaluationLog] = []

    ingested_sample_indices: set[int] = set()
    ingested_sample_ids: list[UUID] = []
    ingested_facts: list[UUID] = []
    ingested_revisions: list[UUID] = []

    try:
        for expected in bundle.expected_evaluations:
            # Progressive arrival-order ingestion
            for idx, sample in enumerate(bundle.recorded_samples):
                should_ingest = False
                if expected.after_sample_sequence is not None:
                    should_ingest = sample.sample_sequence <= expected.after_sample_sequence
                else:
                    should_ingest = sample.sim_time_ms <= expected.query_sim_time_ms

                if should_ingest and idx not in ingested_sample_indices:
                    _register_sample_aliases(repo, sample)
                    res = ingestion.ingest_sample(sample)
                    ingested_sample_indices.add(idx)
                    ingested_sample_ids.append(sample.sample_id)
                    for f in res.facts_created:
                        ingested_facts.append(f.fact_id)
                    for r in res.revisions_created:
                        ingested_revisions.append(r.event_id)

            env.set_time(expected.query_sim_time_ms)
            response = agent.capture(
                expected.prompt,
                agent.interpret(expected.prompt),
                sensors=env,
                capture_time_ms=expected.query_sim_time_ms,
                location_reader=ingestion.assess_entity_location,
            )()

            passed = True
            failure_reasons: list[str] = []

            actual_status = getattr(response, "status", None)
            if actual_status != expected.expected_status:
                passed = False
                failure_reasons.append(
                    f"Status mismatch: expected {expected.expected_status}, got {actual_status}."
                )

            answer_text = getattr(response, "answer", "") or getattr(response, "explanation", "")

            response_failures = _response_assertions(response, expected)
            if response_failures:
                passed = False
                failure_reasons.extend(response_failures)

            if expected.expected_revisions_count is not None:
                actual_revs = len(ingested_revisions)
                if actual_revs != expected.expected_revisions_count:
                    passed = False
                    exp_revs = expected.expected_revisions_count
                    failure_reasons.append(f"Expected {exp_revs} revisions, found {actual_revs}.")

            for exp_fact in expected.expected_active_facts:
                ok, err = _verify_active_fact(repo, exp_fact)
                if not ok:
                    passed = False
                    failure_reasons.append(err or "Active fact verification failed")

            for unexp_fact in expected.unexpected_active_facts:
                ok, err = _verify_unexpected_active_fact(repo, unexp_fact)
                if not ok:
                    passed = False
                    failure_reasons.append(err or "Negative active fact assertion failed")

            for exp_rev in expected.expected_revisions:
                ok, err = _verify_revision(repo, exp_rev)
                if not ok:
                    passed = False
                    failure_reasons.append(err or "Revision assertion failed")

            for exp_sid in expected.expected_evidence_sample_ids:
                ok, err = _verify_evidence_sample_id(repo, exp_sid)
                if not ok:
                    passed = False
                    failure_reasons.append(err or "Sample ID assertion failed")

            log = ScenarioEvaluationLog(
                scenario_id=bundle.scenario_id,
                scenario_title=bundle.metadata.get("title", bundle.scenario_id),
                conflict_description=expected.conflict_or_uncertainty_rationale,
                setup="Evidence-only isolated replay over serialized bundle samples.",
                input_timeline=[
                    f"seq={s.sample_sequence} sim_time={s.sim_time_ms}ms: {s.request.capability}"
                    for s in bundle.recorded_samples
                ],
                prompt=expected.prompt,
                expected_outcome={
                    **expected.model_dump(
                        mode="json",
                        exclude_defaults=True,
                        exclude={"prompt", "conflict_or_uncertainty_rationale", "expected_status"},
                    ),
                    "status": expected.expected_status,
                },
                actual_outcome={
                    "status": actual_status,
                    "answer": answer_text,
                    "revisions_count": len(ingested_revisions),
                    "response_evidence_ids": sorted(_response_evidence_ids(response)),
                },
                recorded_sample_ids=list(ingested_sample_ids),
                recorded_fact_ids=list(ingested_facts),
                revision_ids=list(ingested_revisions),
                policy_applied="temporal_evidence_evaluation",
                passed=passed,
                mode="evidence_only",
                failure_reason="; ".join(failure_reasons) if failure_reasons else None,
            )
            logs.append(log)

    finally:
        agent.close()

    return logs


def replay_world(bundle: ReplayBundle) -> list[ScenarioEvaluationLog]:
    """Execute world rerun: advance simulation engine with fixed ticks, sample through

    WarehouseSensors, ingest resulting real sensor samples into ledger, and evaluate outcomes.
    """
    validate_bundle(bundle)
    if bundle.metadata.get("synthetic_evidence"):
        raise ValueError("Synthetic evidence scenarios cannot be replayed as physical recordings")

    from src.web.simulation_runtime import SimulationRuntime

    scene = bundle.audience_truth.scenario.model_copy(deep=True)
    runtime_ids = count()

    def next_runtime_id() -> UUID:
        index = next(runtime_ids)
        return bundle.run_id if index == 0 else uuid5(bundle.run_id, f"runtime:{index}")

    runtime = SimulationRuntime(
        scene,
        epoch_utc=bundle.epoch_utc,
        ingest=lambda _sample: None,
        id_factory=next_runtime_id,
    )
    for inp in bundle.presenter_inputs:
        if isinstance(inp, dict):
            event = EVENT.validate_python(inp)
        else:
            event = inp
        runtime.engine.queue_event(event)

    capture_ids = count()
    sensors = WarehouseSensors(
        runtime.engine.snapshot,
        id_factory=lambda: uuid5(bundle.run_id, f"capture:{next(capture_ids)}"),
    )
    samples_by_capture_time = sorted(
        enumerate(bundle.recorded_samples),
        key=lambda x: (x[1].sim_time_ms, x[1].sample_sequence),
    )
    real_samples: dict[int, ObservationSample] = {}
    mismatches: dict[int, str] = {}
    injected_sample_ids = {UUID(value) for value in bundle.metadata.get("injected_sample_ids", [])}
    if not injected_sample_ids.issubset({sample.sample_id for sample in bundle.recorded_samples}):
        raise ValueError("Injected sample IDs must identify recorded samples")
    for orig_idx, sample in samples_by_capture_time:
        if sample.sample_id in injected_sample_ids:
            real_samples[orig_idx] = sample
            continue
        while runtime.sim_time_ms < sample.sim_time_ms:
            previous_time = runtime.sim_time_ms
            runtime.step()
            if runtime.sim_time_ms <= previous_time:
                raise ValueError("Simulation stopped before the sample capture time")
        if runtime.sim_time_ms != sample.sim_time_ms:
            raise ValueError("Sample capture time does not align with simulation steps")
        captured = sensors.sample(sample.request)
        if captured.observer_pose != sample.observer_pose or captured.result.model_dump(
            exclude={"observation_id"}
        ) != sample.result.model_dump(exclude={"observation_id"}):
            mismatches[orig_idx] = (
                f"Rerun sample {sample.sample_sequence} differs from recorded evidence"
            )
        result = captured.result
        if isinstance(result, SensorObservation) and isinstance(sample.result, SensorObservation):
            result = result.model_copy(update={"observation_id": sample.result.observation_id})
        real_samples[orig_idx] = captured.model_copy(
            update={
                "sample_id": sample.sample_id,
                "sample_sequence": sample.sample_sequence,
                "result": result,
            }
        )

    repo = _replay_repository(bundle)
    zone_map = StationZoneMap(zones=bundle.static_map.zones)
    route_map = StaticRouteMap(routes=bundle.static_map.routes)

    # Register single aliases
    for alias, target in bundle.metadata.get("aliases", {}).items():
        repo.add_alias(alias, target)

    # Register multi-aliases
    for alias, targets in bundle.metadata.get("aliases_multi", {}).items():
        for target in targets:
            repo.add_alias(alias, target)

    # Pre-seed initial facts
    for fact_dict in bundle.metadata.get("initial_facts", []):
        repo.record_fact(FactAssertion.model_validate(fact_dict))

    ingestion = TemporalBeliefIngestionService(
        fact_repository=repo,
        observation_ledger=repo,
        zone_map=zone_map,
        route_map=route_map,
    )

    env = IsolatedEvidenceEnvironment(repo, bundle.run_id, current_sim_time_ms=0)
    agent = GroundedAgent.create(memory=repo, environment=env)
    logs: list[ScenarioEvaluationLog] = []

    ingested_sample_indices: set[int] = set()
    ingested_sample_ids: list[UUID] = []
    ingested_facts: list[UUID] = []
    ingested_revisions: list[UUID] = []

    try:
        for index, expected in enumerate(bundle.expected_evaluations):
            for idx, sample in enumerate(bundle.recorded_samples):
                should_ingest = False
                if expected.after_sample_sequence is not None:
                    should_ingest = sample.sample_sequence <= expected.after_sample_sequence
                else:
                    should_ingest = sample.sim_time_ms <= expected.query_sim_time_ms

                if should_ingest and idx not in ingested_sample_indices:
                    real_sample = real_samples[idx]
                    _register_sample_aliases(repo, real_sample)
                    res = ingestion.ingest_sample(real_sample)
                    ingested_sample_indices.add(idx)
                    ingested_sample_ids.append(real_sample.sample_id)
                    for f in res.facts_created:
                        ingested_facts.append(f.fact_id)
                    for r in res.revisions_created:
                        ingested_revisions.append(r.event_id)

            env.set_time(expected.query_sim_time_ms)
            response = agent.capture(
                expected.prompt,
                agent.interpret(expected.prompt),
                sensors=env,
                capture_time_ms=expected.query_sim_time_ms,
                location_reader=ingestion.assess_entity_location,
            )()

            failure_reasons = [
                mismatches[idx] for idx in sorted(ingested_sample_indices) if idx in mismatches
            ]
            if index == len(bundle.expected_evaluations) - 1:
                failure_reasons.extend(
                    reason
                    for idx, reason in mismatches.items()
                    if idx not in ingested_sample_indices
                )
            passed = not failure_reasons

            actual_status = getattr(response, "status", None)
            if actual_status != expected.expected_status:
                passed = False
                failure_reasons.append(
                    f"Status mismatch: expected {expected.expected_status}, got {actual_status}."
                )

            answer_text = getattr(response, "answer", "") or getattr(response, "explanation", "")

            response_failures = _response_assertions(response, expected)
            if response_failures:
                passed = False
                failure_reasons.extend(response_failures)

            if expected.expected_revisions_count is not None:
                actual_revs = len(ingested_revisions)
                if actual_revs != expected.expected_revisions_count:
                    passed = False
                    exp_revs = expected.expected_revisions_count
                    failure_reasons.append(f"Expected {exp_revs} revisions, found {actual_revs}.")

            for exp_fact in expected.expected_active_facts:
                ok, err = _verify_active_fact(repo, exp_fact)
                if not ok:
                    passed = False
                    failure_reasons.append(err or "Active fact verification failed")

            for unexp_fact in expected.unexpected_active_facts:
                ok, err = _verify_unexpected_active_fact(repo, unexp_fact)
                if not ok:
                    passed = False
                    failure_reasons.append(err or "Negative active fact assertion failed")

            for exp_rev in expected.expected_revisions:
                ok, err = _verify_revision(repo, exp_rev)
                if not ok:
                    passed = False
                    failure_reasons.append(err or "Revision assertion failed")

            for exp_sid in expected.expected_evidence_sample_ids:
                ok, err = _verify_evidence_sample_id(repo, exp_sid)
                if not ok:
                    passed = False
                    failure_reasons.append(err or "Sample ID assertion failed")

            log = ScenarioEvaluationLog(
                scenario_id=bundle.scenario_id,
                scenario_title=bundle.metadata.get("title", bundle.scenario_id),
                conflict_description=expected.conflict_or_uncertainty_rationale,
                setup=(
                    "SimulationRuntime advanced the patrol and captured sensor evidence; "
                    f"{len(injected_sample_ids)} explicitly injected sample(s)."
                ),
                input_timeline=[
                    f"seq={s.sample_sequence} sim_time={s.sim_time_ms}ms: {s.request.capability}"
                    for s in bundle.recorded_samples
                ],
                prompt=expected.prompt,
                expected_outcome={
                    **expected.model_dump(
                        mode="json",
                        exclude_defaults=True,
                        exclude={"prompt", "conflict_or_uncertainty_rationale", "expected_status"},
                    ),
                    "status": expected.expected_status,
                },
                actual_outcome={
                    "status": actual_status,
                    "answer": answer_text,
                    "revisions_count": len(ingested_revisions),
                    "response_evidence_ids": sorted(_response_evidence_ids(response)),
                },
                recorded_sample_ids=list(ingested_sample_ids),
                recorded_fact_ids=list(ingested_facts),
                revision_ids=list(ingested_revisions),
                policy_applied="simulation_world_rerun",
                passed=passed,
                mode="world_rerun",
                failure_reason="; ".join(failure_reasons) if failure_reasons else None,
            )
            logs.append(log)

    finally:
        agent.close()

    return logs


def run_scenario(
    scenario_id: str, mode: Literal["world_rerun", "evidence_only", "both"] = "both"
) -> list[ScenarioEvaluationLog]:
    """Execute a single scenario builder in the requested replay mode."""
    if scenario_id not in ALL_SCENARIO_BUILDERS:
        raise KeyError(f"Unknown scenario ID: {scenario_id}")

    bundle = ALL_SCENARIO_BUILDERS[scenario_id]()
    logs: list[ScenarioEvaluationLog] = []

    if mode in ("evidence_only", "both"):
        logs.extend(replay_evidence(bundle))
    if mode in ("world_rerun", "both"):
        from json import loads
        from pathlib import Path

        recordings = loads(
            (Path(__file__).parent / "fixtures" / "world_recordings.json").read_text()
        )
        if scenario_id in recordings:
            bundle = bundle.model_copy(deep=True)
            bundle.metadata.pop("synthetic_evidence", None)
            bundle.presenter_inputs = []
            recording = recordings[scenario_id]
            if recording.get("injected_sample_ids"):
                bundle.metadata["injected_sample_ids"] = recording["injected_sample_ids"]
            bundle.audience_truth.scenario = type(bundle.audience_truth.scenario).model_validate(
                recording["scenario"]
            )
            bundle.recorded_samples = [
                ObservationSample.model_validate(sample) for sample in recording["samples"]
            ]
        logs.extend(replay_world(bundle))

    return logs


def run_all_scenarios(
    mode: Literal["world_rerun", "evidence_only", "both"] = "both",
) -> list[ScenarioEvaluationLog]:
    """Run all 10 evaluation scenarios in the requested mode."""
    logs: list[ScenarioEvaluationLog] = []
    for scen_id in ALL_SCENARIO_BUILDERS:
        logs.extend(run_scenario(scen_id, mode=mode))
    return logs
