"""Replay engine supporting world rerun and evidence-only replay modes (P2-12)."""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from src.agent import GroundedAgent
from src.contracts import FactAssertion, SensorObservation
from src.declarative.memory_repository import MemoryRepository
from src.evaluation.models import ReplayBundle, ScenarioEvaluationLog
from src.evaluation.scenarios import ALL_SCENARIO_BUILDERS
from src.procedural.temporal_belief_ingestion import (
    StaticRouteMap,
    StationZoneMap,
    TemporalBeliefIngestionService,
)
from src.sensorimotor.mock_environment import MockEnvironment
from src.sensorimotor.simulation_engine import SimulationEngine
from src.sensorimotor.warehouse_sensors import WarehouseSensors
from src.sensorimotor.world_models import (
    AmbientLight,
    Obstacle,
    RobotState,
    SimulatedObject,
    WorldState,
)

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


def _build_mock_world_from_metadata(metadata: dict) -> MockEnvironment:
    """Instantiate MockEnvironment with any simulated objects, light, or obstacles."""
    mock_data = metadata.get("mock_world", {})
    world = WorldState(
        robot=RobotState(
            robot_id="robot_1",
            x_cm=0.0,
            y_cm=0.0,
            direction="north",
            location=mock_data.get("robot_location", "room_101"),
            frame_of_reference=mock_data.get("robot_frame", "robot_base"),
        ),
        light=AmbientLight(
            intensity=0.8,
            color_cast=mock_data.get("light_color_cast", "white"),
        ),
    )

    for obj in mock_data.get("objects", []):
        world.objects[obj["object_id"]] = SimulatedObject(
            object_id=obj["object_id"],
            label=obj.get("label", "object"),
            intrinsic_color=obj.get("intrinsic_color", "red"),
            location=obj.get("location", "room_101"),
            x_cm=obj.get("x_cm", 0.0),
            y_cm=obj.get("y_cm", 0.0),
        )

    for obs in mock_data.get("obstacles", []):
        world.obstacles[obs["obstacle_id"]] = Obstacle(
            obstacle_id=obs["obstacle_id"],
            x_cm=obs.get("x_cm", 0.0),
            y_cm=obs.get("y_cm", 0.0),
            location=obs.get("location", "room_101"),
            active=obs.get("active", True),
        )

    return MockEnvironment(world=world)


def replay_evidence(bundle: ReplayBundle) -> list[ScenarioEvaluationLog]:
    """Execute evidence-only replay: feed recorded samples into fresh memory."""
    validate_bundle(bundle)

    repo = MemoryRepository(":memory:")
    zone_map = StationZoneMap(zones=bundle.static_map.zones)
    route_map = StaticRouteMap(routes=bundle.static_map.routes)

    # Register single aliases
    for alias, target in bundle.metadata.get("aliases", {}).items():
        repo.add_alias(alias, target)

    # Register multi-aliases (for ambiguity scenarios)
    for alias, targets in bundle.metadata.get("aliases_multi", {}).items():
        for target in targets:
            repo.add_alias(alias, target)

    # Pre-seed initial facts
    for fact_dict in bundle.metadata.get("initial_facts", []):
        repo.record_fact(FactAssertion.model_validate(fact_dict))

    # Auto-register targets and detected objects as aliases
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

    env = _build_mock_world_from_metadata(bundle.metadata)
    agent = GroundedAgent.create(memory=repo, environment=env)
    logs: list[ScenarioEvaluationLog] = []

    ingested_sample_ids: set[UUID] = set()
    ingested_facts: list[UUID] = []
    ingested_revisions: list[UUID] = []

    # Sort samples by arrival sequence/sim_time
    samples_by_time = sorted(
        bundle.recorded_samples, key=lambda s: (s.sim_time_ms, s.sample_sequence)
    )

    # Obstacles timeline for Scenario 1
    obstacles_timeline = bundle.metadata.get("world_obstacles_timeline", {})

    try:
        for expected in bundle.expected_evaluations:
            # Progressively ingest samples up to query_sim_time_ms
            for sample in samples_by_time:
                if (
                    sample.sim_time_ms <= expected.query_sim_time_ms
                    and sample.sample_id not in ingested_sample_ids
                ):
                    res = ingestion.ingest_sample(sample)
                    ingested_sample_ids.add(sample.sample_id)
                    for f in res.facts_created:
                        ingested_facts.append(f.fact_id)
                    for r in res.revisions_created:
                        ingested_revisions.append(r.event_id)

            # Update environment obstacles according to timeline if applicable
            if obstacles_timeline:
                # Find latest obstacle configuration up to current query_sim_time_ms
                latest_t = max(t for t in obstacles_timeline if t <= expected.query_sim_time_ms)
                env._world.obstacles.clear()
                for obs in obstacles_timeline[latest_t]:
                    env._world.obstacles[obs["obstacle_id"]] = Obstacle(
                        obstacle_id=obs["obstacle_id"],
                        x_cm=obs.get("x_cm", 0.0),
                        y_cm=obs.get("y_cm", 0.0),
                        location=obs.get("location", "room_101"),
                        active=obs.get("active", True),
                    )

            response = agent.ask(expected.prompt)
            answer_text = response.answer
            actual_status = response.status

            passed = True
            failure_reason = None

            if actual_status != expected.expected_status:
                passed = False
                failure_reason = (
                    f"Status mismatch: expected {expected.expected_status}, got {actual_status}."
                )

            if expected.expected_location is not None:
                if expected.expected_location.lower() not in answer_text.lower():
                    passed = False
                    loc = expected.expected_location
                    failure_reason = f"Expected location '{loc}' not in answer: {answer_text}"

            if expected.expected_revisions_count is not None:
                actual_revs = len(ingested_revisions)
                if actual_revs != expected.expected_revisions_count:
                    passed = False
                    exp_revs = expected.expected_revisions_count
                    failure_reason = f"Expected {exp_revs} revisions, found {actual_revs}."

            setup_desc = (
                "Fresh SQLite memory initialized; recorded observation envelopes ingested "
                "without world access."
            )
            timeline = [
                f"sim_time={s.sim_time_ms}ms: {s.request.capability}"
                for s in bundle.recorded_samples
            ]
            log = ScenarioEvaluationLog(
                scenario_id=bundle.scenario_id,
                scenario_title=bundle.metadata.get("title", bundle.scenario_id),
                conflict_description=expected.conflict_or_uncertainty_rationale,
                setup=setup_desc,
                input_timeline=timeline,
                prompt=expected.prompt,
                expected_outcome={
                    "status": expected.expected_status,
                    "location": expected.expected_location,
                    "revisions_count": expected.expected_revisions_count,
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
                failure_reason=failure_reason,
            )
            logs.append(log)

        # Ingest any remaining samples after evaluations
        for sample in samples_by_time:
            if sample.sample_id not in ingested_sample_ids:
                res = ingestion.ingest_sample(sample)
                ingested_sample_ids.add(sample.sample_id)
                for f in res.facts_created:
                    ingested_facts.append(f.fact_id)
                for r in res.revisions_created:
                    ingested_revisions.append(r.event_id)
    finally:
        agent.close()

    return logs


def replay_world(bundle: ReplayBundle) -> list[ScenarioEvaluationLog]:
    """Execute world rerun: run simulation engine, sensors, and query agent."""
    validate_bundle(bundle)

    scene = bundle.audience_truth.scenario
    engine = SimulationEngine(scene, epoch_utc=bundle.epoch_utc, id_factory=lambda: bundle.run_id)
    sensors = WarehouseSensors(engine.snapshot)
    if bundle.recorded_samples:
        _ = sensors.sample(bundle.recorded_samples[0].request)

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

    env = _build_mock_world_from_metadata(bundle.metadata)
    agent = GroundedAgent.create(memory=repo, environment=env)
    logs: list[ScenarioEvaluationLog] = []

    ingested_sample_ids: set[UUID] = set()
    ingested_facts: list[UUID] = []
    ingested_revisions: list[UUID] = []

    samples_by_time = sorted(
        bundle.recorded_samples, key=lambda s: (s.sim_time_ms, s.sample_sequence)
    )
    obstacles_timeline = bundle.metadata.get("world_obstacles_timeline", {})

    try:
        for expected in bundle.expected_evaluations:
            for sample in samples_by_time:
                if (
                    sample.sim_time_ms <= expected.query_sim_time_ms
                    and sample.sample_id not in ingested_sample_ids
                ):
                    res = ingestion.ingest_sample(sample)
                    ingested_sample_ids.add(sample.sample_id)
                    for f in res.facts_created:
                        ingested_facts.append(f.fact_id)
                    for r in res.revisions_created:
                        ingested_revisions.append(r.event_id)

            if obstacles_timeline:
                latest_t = max(t for t in obstacles_timeline if t <= expected.query_sim_time_ms)
                env._world.obstacles.clear()
                for obs in obstacles_timeline[latest_t]:
                    env._world.obstacles[obs["obstacle_id"]] = Obstacle(
                        obstacle_id=obs["obstacle_id"],
                        x_cm=obs.get("x_cm", 0.0),
                        y_cm=obs.get("y_cm", 0.0),
                        location=obs.get("location", "room_101"),
                        active=obs.get("active", True),
                    )

            response = agent.ask(expected.prompt)
            answer_text = response.answer
            actual_status = response.status

            passed = True
            failure_reason = None

            if actual_status != expected.expected_status:
                passed = False
                failure_reason = (
                    f"Status mismatch: expected {expected.expected_status}, got {actual_status}."
                )

            if expected.expected_location is not None:
                if expected.expected_location.lower() not in answer_text.lower():
                    passed = False
                    loc = expected.expected_location
                    failure_reason = f"Expected location '{loc}' not in answer: {answer_text}"

            setup_desc = "SimulationEngine initialized; sensors, odometry and ingestion executed."
            timeline = [
                f"sim_time={s.sim_time_ms}ms: {s.request.capability}"
                for s in bundle.recorded_samples
            ]
            log = ScenarioEvaluationLog(
                scenario_id=bundle.scenario_id,
                scenario_title=bundle.metadata.get("title", bundle.scenario_id),
                conflict_description=expected.conflict_or_uncertainty_rationale,
                setup=setup_desc,
                input_timeline=timeline,
                prompt=expected.prompt,
                expected_outcome={
                    "status": expected.expected_status,
                    "location": expected.expected_location,
                },
                actual_outcome={
                    "status": actual_status,
                    "answer": answer_text,
                },
                recorded_sample_ids=[s.sample_id for s in bundle.recorded_samples],
                recorded_fact_ids=ingested_facts,
                revision_ids=ingested_revisions,
                policy_applied="simulation_world_rerun",
                passed=passed,
                mode="world_rerun",
                failure_reason=failure_reason,
            )
            logs.append(log)

        for sample in samples_by_time:
            if sample.sample_id not in ingested_sample_ids:
                res = ingestion.ingest_sample(sample)
                ingested_sample_ids.add(sample.sample_id)
                for f in res.facts_created:
                    ingested_facts.append(f.fact_id)
                for r in res.revisions_created:
                    ingested_revisions.append(r.event_id)
    finally:
        agent.close()

    return logs


def run_scenario(
    scenario_id: str,
    mode: Literal["world_rerun", "evidence_only", "both"] = "both",
) -> list[ScenarioEvaluationLog]:
    """Run an individual scenario by identifier in one or both replay modes."""
    if scenario_id not in ALL_SCENARIO_BUILDERS:
        raise ValueError(
            f"Unknown scenario_id: {scenario_id}. Available: {list(ALL_SCENARIO_BUILDERS.keys())}"
        )

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
    """Run all 10 evaluation scenarios sequentially."""
    logs: list[ScenarioEvaluationLog] = []
    for scenario_id in ALL_SCENARIO_BUILDERS:
        LOGGER.info("Running scenario: %s [mode=%s]", scenario_id, mode)
        scen_logs = run_scenario(scenario_id, mode=mode)
        logs.extend(scen_logs)
    return logs
