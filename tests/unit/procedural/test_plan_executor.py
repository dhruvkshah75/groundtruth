"""Tests for the PlanExecutor."""

from datetime import UTC, datetime

import pytest

from src.contracts import (
    FactAssertion,
    FactQuery,
    IntentRequest,
    ObservationRequest,
    SpatialContext,
)
from src.declarative.memory_repository import MemoryRepository
from src.procedural.execution_plan import (
    AuditChainOperation,
    ExecutionPlan,
    MemoryQueryOperation,
    ObservationOperation,
)
from src.procedural.plan_executor import PlanExecutor
from src.sensorimotor.mock_environment import MockEnvironment
from src.sensorimotor.world_models import RobotState, WorldState


@pytest.fixture
def memory():
    with MemoryRepository(":memory:") as repo:
        yield repo


@pytest.fixture
def env():
    world = WorldState(
        robot=RobotState(
            robot_id="robot_1",
            x_cm=0.0,
            y_cm=0.0,
            direction="north",
            location="room_1",
            frame_of_reference="robot_base",
        )
    )
    return MockEnvironment(world=world)


@pytest.fixture
def executor(memory, env):
    return PlanExecutor(memory_repository=memory, environment=env)


def test_execute_route_status_plan(executor, memory):
    memory.record_fact(
        FactAssertion(
            subject="route_A",
            predicate="status_is",
            object="clear",
            source_agent="human",
            confidence_score=1.0,
            observed_at=datetime.now(UTC),
            context=SpatialContext(),
        )
    )

    plan = ExecutionPlan(
        intent=IntentRequest(
            intent="current_route_status",
            entity_mentions=["route A"],
            user_question="Is route A clear?",
        ),
        resolved_entity_ids=["route_A"],
        memory_operations=[
            MemoryQueryOperation(
                query=FactQuery(subject="route_A", predicate="status_is"),
                purpose="Test memory query",
            )
        ],
        observation_operations=[
            ObservationOperation(
                request=ObservationRequest(
                    capability="lidar_scan", target="route_A", parameters={"direction": "front"}
                ),
                purpose="Test lidar scan",
            )
        ],
        current_state_verifiable=True,
        plan_reason="Testing route status plan execution.",
    )

    result = executor.execute(plan)

    assert len(result.memory_results) == 1
    assert result.memory_results[0].operation_index == 0
    assert len(result.memory_results[0].facts) == 1
    assert result.memory_results[0].facts[0].subject == "route_A"

    assert len(result.observation_results) == 1
    assert result.observation_results[0].operation_index == 0
    assert result.observation_results[0].result.capability == "lidar_scan"


def test_execute_historical_lookup_plan(executor, memory):
    plan = ExecutionPlan(
        intent=IntentRequest(
            intent="historical_fact_lookup",
            entity_mentions=["route A"],
            user_question="History of route A?",
        ),
        resolved_entity_ids=["route_A"],
        memory_operations=[
            MemoryQueryOperation(
                query=FactQuery(subject="route_A", active_only=False), purpose="Test memory query"
            )
        ],
        current_state_verifiable=True,
        plan_reason="Testing historical lookup plan execution.",
    )

    result = executor.execute(plan)

    assert len(result.memory_results) == 1
    assert len(result.observation_results) == 0
    assert len(result.audit_results) == 0


def test_execute_audit_lookup_plan(executor, memory):
    fact1 = memory.record_fact(
        FactAssertion(
            subject="route_A",
            predicate="status_is",
            object="clear",
            source_agent="human",
            confidence_score=1.0,
            observed_at=datetime.now(UTC),
            context=SpatialContext(),
        )
    )

    plan = ExecutionPlan(
        intent=IntentRequest(
            intent="audit_explanation", entity_mentions=["route A"], user_question="Why?"
        ),
        resolved_entity_ids=["route_A"],
        memory_operations=[
            MemoryQueryOperation(query=FactQuery(subject="route_A"), purpose="Find facts")
        ],
        audit_operations=[
            AuditChainOperation(
                entity_id="route_A", fact_ids_from_memory_operation=0, purpose="Audit facts"
            )
        ],
        current_state_verifiable=True,
        plan_reason="Testing audit lookup plan execution.",
    )

    result = executor.execute(plan)

    assert len(result.audit_results) == 1
    assert fact1.fact_id in result.audit_results[0].trails


def test_execute_robot_pose_plan(executor):
    plan = ExecutionPlan(
        intent=IntentRequest(intent="current_robot_pose", user_question="Where am I?"),
        observation_operations=[
            ObservationOperation(
                request=ObservationRequest(capability="robot_pose"), purpose="Test robot pose"
            )
        ],
        current_state_verifiable=True,
        plan_reason="Testing robot pose plan execution.",
    )

    result = executor.execute(plan)

    assert len(result.memory_results) == 0
    assert len(result.observation_results) == 1
    assert result.observation_results[0].result.capability == "robot_pose"


def test_unavailable_observations(executor):
    plan = ExecutionPlan(
        intent=IntentRequest(intent="current_route_status", user_question="Where am I?"),
        observation_operations=[
            ObservationOperation(
                request=ObservationRequest(capability="lidar_scan", parameters={"direction": "up"}),
                purpose="Test lidar scan fail",
            )
        ],
        current_state_verifiable=True,
        plan_reason="Testing observation failure.",
    )

    result = executor.execute(plan)

    assert len(result.observation_results) == 1
    assert result.observation_results[0].result.capability == "lidar_scan"
    assert getattr(result.observation_results[0].result, "reason", None) == "sensor_unavailable"
