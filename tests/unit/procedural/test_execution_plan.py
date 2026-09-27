"""Tests for typed, non-executing planning data."""

import pytest
from pydantic import ValidationError

from src.contracts import FactQuery, IntentRequest, ObservationRequest
from src.procedural import (
    ActionPlaceholder,
    AuditChainOperation,
    ExecutionPlan,
    MemoryQueryOperation,
    ObservationOperation,
)


def test_execution_plan_keeps_memory_and_observation_operations_as_data() -> None:
    intent = IntentRequest(
        intent="current_route_status",
        entity_mentions=["front route"],
        user_question="Can I move forward?",
    )
    plan = ExecutionPlan(
        intent=intent,
        resolved_entity_ids=["route_ahead"],
        memory_operations=[
            MemoryQueryOperation(
                query=FactQuery(subject="route_ahead", predicate="status_is"),
                purpose="Read the active route status.",
            )
        ],
        observation_operations=[
            ObservationOperation(
                request=ObservationRequest(capability="lidar_scan", target="route_ahead"),
                purpose="Check the route with a fresh LiDAR scan.",
            )
        ],
        current_state_verifiable=True,
        plan_reason="A current route check needs memory and a fresh observation.",
    )

    assert plan.resolved_entity_ids == ["route_ahead"]
    assert plan.memory_operations[0].query.active_only is True
    assert plan.observation_operations[0].request.capability == "lidar_scan"


def test_audit_operation_must_reference_an_existing_memory_query() -> None:
    with pytest.raises(ValidationError, match="existing memory operation"):
        ExecutionPlan(
            intent=IntentRequest(
                intent="audit_explanation",
                entity_mentions=["the box"],
                user_question="Why do you believe this about the box?",
            ),
            audit_operations=[
                AuditChainOperation(
                    entity_id="box_01",
                    fact_ids_from_memory_operation=0,
                    purpose="Read audit trails for matching facts.",
                )
            ],
            current_state_verifiable=True,
            plan_reason="The test checks invalid audit references.",
        )


def test_audit_operation_entity_must_match_referenced_query() -> None:
    with pytest.raises(ValidationError, match="must match its memory query subject"):
        ExecutionPlan(
            intent=IntentRequest(
                intent="audit_explanation",
                entity_mentions=["the box"],
                user_question="Why do you believe this about the box?",
            ),
            memory_operations=[
                MemoryQueryOperation(
                    query=FactQuery(subject="route_ahead"),
                    purpose="Find active facts.",
                )
            ],
            audit_operations=[
                AuditChainOperation(
                    entity_id="box_01",
                    fact_ids_from_memory_operation=0,
                    purpose="Read audit trails for matching facts.",
                )
            ],
            current_state_verifiable=True,
            plan_reason="The test checks that audit targets match fact queries.",
        )


def test_action_placeholder_rejects_a_missing_safety_precheck() -> None:
    with pytest.raises(ValidationError):
        ActionPlaceholder(action_name="move_robot", requires_safety_precheck=False)
