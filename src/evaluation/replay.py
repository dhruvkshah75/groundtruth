"""Replay engine implementing evidence-only replay and physical world rerun (P2-12)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

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
from src.contracts.simulation import (
    LightingEvent,
    MoveEvent,
    SensorAvailabilityEvent,
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
from src.sensorimotor.simulation_engine import EVENT, SimulationEngine
from src.sensorimotor.warehouse_sensors import WarehouseSensors
from src.sensorimotor.world_models import RobotState, WorldState

LOGGER = logging.getLogger("groundtruth.evaluation.replay")


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

    for sample in bundle.recorded_samples:
        if sample.run_id != run_id:
            raise ValueError(
                f"Sample {sample.sample_id} run_id {sample.run_id} does not match bundle {run_id}"
            )
        if sample.sample_id in seen_ids:
            raise ValueError(f"Duplicate sample_id detected in bundle: {sample.sample_id}")
        seen_ids.add(sample.sample_id)

        if sample.sample_sequence < last_seq:
            LOGGER.warning(
                "Sample sequence decreased: %d < %d (indicates out-of-order arrival)",
                sample.sample_sequence,
                last_seq,
            )
        last_seq = sample.sample_sequence


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


def replay_evidence(bundle: ReplayBundle) -> list[ScenarioEvaluationLog]:
    """Execute evidence-only replay strictly isolated from mock world / live simulator."""
    validate_bundle(bundle)

    repo = MemoryRepository(":memory:")
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

    # Auto-register targets and detected objects
    for s in bundle.recorded_samples:
        if s.request.target:
            repo.add_alias(s.request.target, s.request.target)
        if isinstance(s.result, SensorObservation) and isinstance(s.result.measurements, dict):
            objs = s.result.measurements.get("objects", [])
            for obj in objs:
                oid = (
                    obj.get("object_id")
                    if isinstance(obj, dict)
                    else getattr(obj, "object_id", None)
                )
                if oid:
                    repo.add_alias(oid, oid)

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
                    res = ingestion.ingest_sample(sample)
                    ingested_sample_indices.add(idx)
                    ingested_sample_ids.append(sample.sample_id)
                    for f in res.facts_created:
                        ingested_facts.append(f.fact_id)
                    for r in res.revisions_created:
                        ingested_revisions.append(r.event_id)

            env.set_time(expected.query_sim_time_ms)
            response = agent.ask(expected.prompt)

            passed = True
            failure_reasons: list[str] = []

            actual_status = getattr(response, "status", None)
            if actual_status != expected.expected_status:
                passed = False
                failure_reasons.append(
                    f"Status mismatch: expected {expected.expected_status}, got {actual_status}."
                )

            answer_text = getattr(response, "answer", "") or getattr(response, "explanation", "")

            if expected.expected_location is not None:
                loc_in_answer = expected.expected_location.lower() in answer_text.lower()
                loc_in_active_facts = any(
                    f.object.lower() == expected.expected_location.lower()
                    for f in repo.query_facts(FactQuery(active_only=True))
                    if f.superseded_by is None
                )
                if not loc_in_answer and not loc_in_active_facts:
                    passed = False
                    loc = expected.expected_location
                    failure_reasons.append(
                        f"Expected location '{loc}' not in answer and not among active facts: "
                        f"{answer_text}"
                    )

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
                    "status": expected.expected_status,
                    "location": expected.expected_location,
                    "revisions_count": expected.expected_revisions_count,
                    "active_facts": expected.expected_active_facts,
                },
                actual_outcome={
                    "status": actual_status,
                    "answer": answer_text,
                    "revisions_count": len(ingested_revisions),
                },
                recorded_sample_ids=[s.sample_id for s in bundle.recorded_samples],
                recorded_fact_ids=ingested_facts,
                revision_ids=ingested_revisions,
                policy_applied="temporal_evidence_evaluation",
                passed=passed,
                mode="evidence_only",
                failure_reason="; ".join(failure_reasons) if failure_reasons else None,
            )
            logs.append(log)

        # Ingest any remaining samples after evaluations
        for idx, sample in enumerate(bundle.recorded_samples):
            if idx not in ingested_sample_indices:
                res = ingestion.ingest_sample(sample)
                ingested_sample_indices.add(idx)
                for f in res.facts_created:
                    ingested_facts.append(f.fact_id)
                for r in res.revisions_created:
                    ingested_revisions.append(r.event_id)
    finally:
        agent.close()

    return logs


def replay_world(bundle: ReplayBundle) -> list[ScenarioEvaluationLog]:
    """Execute world rerun: advance simulation engine with fixed ticks, sample through

    WarehouseSensors, ingest resulting real sensor samples into ledger, and evaluate outcomes.
    """
    validate_bundle(bundle)

    scene = bundle.audience_truth.scenario.model_copy(deep=True)
    engine = SimulationEngine(scene, epoch_utc=bundle.epoch_utc, id_factory=lambda: bundle.run_id)

    # Queue presenter inputs if any
    for inp in bundle.presenter_inputs:
        if isinstance(inp, dict) and inp.get("kind") in (
            "move",
            "lighting",
            "sensor_availability",
        ):
            event = EVENT.validate_python(inp)
            engine.queue_event(event)
        elif isinstance(inp, (MoveEvent, LightingEvent, SensorAvailabilityEvent)):
            engine.queue_event(inp)

    engine.play()

    sensors = WarehouseSensors(engine.snapshot)

    # Phase 1: Physically capture real samples as simulation time advances forward
    samples_by_capture_time = sorted(
        enumerate(bundle.recorded_samples),
        key=lambda x: (x[1].sim_time_ms, x[1].sample_sequence),
    )
    real_samples: dict[int, ObservationSample] = {}
    for orig_idx, sample in samples_by_capture_time:
        while engine.snapshot().status.sim_time_ms < sample.sim_time_ms:
            engine.advance()
        if engine.snapshot().robot_pose != sample.observer_pose:
            engine._snap.robot_pose = sample.observer_pose
        captured = sensors.sample(sample.request)
        real_samples[orig_idx] = captured.model_copy(
            update={
                "sample_id": sample.sample_id,
                "sample_sequence": sample.sample_sequence,
            }
        )

    repo = MemoryRepository(":memory:")
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

    # Auto-register targets and detected objects
    for s in bundle.recorded_samples:
        if s.request.target:
            repo.add_alias(s.request.target, s.request.target)
        if isinstance(s.result, SensorObservation) and isinstance(s.result.measurements, dict):
            objs = s.result.measurements.get("objects", [])
            for obj in objs:
                oid = (
                    obj.get("object_id")
                    if isinstance(obj, dict)
                    else getattr(obj, "object_id", None)
                )
                if oid:
                    repo.add_alias(oid, oid)

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
            for idx, sample in enumerate(bundle.recorded_samples):
                should_ingest = False
                if expected.after_sample_sequence is not None:
                    should_ingest = sample.sample_sequence <= expected.after_sample_sequence
                else:
                    should_ingest = sample.sim_time_ms <= expected.query_sim_time_ms

                if should_ingest and idx not in ingested_sample_indices:
                    real_sample = real_samples[idx]
                    res = ingestion.ingest_sample(real_sample)
                    ingested_sample_indices.add(idx)
                    ingested_sample_ids.append(real_sample.sample_id)
                    for f in res.facts_created:
                        ingested_facts.append(f.fact_id)
                    for r in res.revisions_created:
                        ingested_revisions.append(r.event_id)

            env.set_time(expected.query_sim_time_ms)
            response = agent.ask(expected.prompt)

            passed = True
            failure_reasons = []

            actual_status = getattr(response, "status", None)
            if actual_status != expected.expected_status:
                passed = False
                failure_reasons.append(
                    f"Status mismatch: expected {expected.expected_status}, got {actual_status}."
                )

            answer_text = getattr(response, "answer", "") or getattr(response, "explanation", "")

            if expected.expected_location is not None:
                loc_in_answer = expected.expected_location.lower() in answer_text.lower()
                loc_in_active_facts = any(
                    f.object.lower() == expected.expected_location.lower()
                    for f in repo.query_facts(FactQuery(active_only=True))
                    if f.superseded_by is None
                )
                if not loc_in_answer and not loc_in_active_facts:
                    passed = False
                    loc = expected.expected_location
                    failure_reasons.append(
                        f"Expected location '{loc}' not in answer and not among active facts: "
                        f"{answer_text}"
                    )

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
                    "SimulationEngine advanced with fixed ticks; "
                    "real WarehouseSensors sampled and ingested."
                ),
                input_timeline=[
                    f"seq={s.sample_sequence} sim_time={s.sim_time_ms}ms: {s.request.capability}"
                    for s in bundle.recorded_samples
                ],
                prompt=expected.prompt,
                expected_outcome={
                    "status": expected.expected_status,
                    "location": expected.expected_location,
                    "revisions_count": expected.expected_revisions_count,
                    "active_facts": expected.expected_active_facts,
                },
                actual_outcome={
                    "status": actual_status,
                    "answer": answer_text,
                    "revisions_count": len(ingested_revisions),
                },
                recorded_sample_ids=[s.sample_id for s in bundle.recorded_samples],
                recorded_fact_ids=ingested_facts,
                revision_ids=ingested_revisions,
                policy_applied="simulation_world_rerun",
                passed=passed,
                mode="world_rerun",
                failure_reason="; ".join(failure_reasons) if failure_reasons else None,
            )
            logs.append(log)

        # Ingest any remaining samples after evaluations
        for idx, sample in enumerate(bundle.recorded_samples):
            if idx not in ingested_sample_indices:
                real_sample = real_samples[idx]
                res = ingestion.ingest_sample(real_sample)
                ingested_sample_indices.add(idx)
                for f in res.facts_created:
                    ingested_facts.append(f.fact_id)
                for r in res.revisions_created:
                    ingested_revisions.append(r.event_id)
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
