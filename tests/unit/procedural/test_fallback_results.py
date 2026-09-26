"""Tests for safe planning outcomes."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.contracts import GroundedResult, IntentRequest
from src.procedural import ExecutionPlan, PlanningFallback, PlanningOutcome


def test_planning_fallback_keeps_an_ambiguous_entity_result_structured() -> None:
    fallback = PlanningFallback(
        category="ambiguous_entity",
        reason="The box could refer to more than one known object.",
        clarification_candidates=["box_01", "box_02"],
        safe_result=GroundedResult(
            uncertainty=True,
            uncertainty_reason="The requested object is ambiguous.",
        ),
    )
    outcome = PlanningOutcome(fallback=fallback)

    assert outcome.plan is None
    assert outcome.fallback.clarification_candidates == ["box_01", "box_02"]


def test_planning_fallback_rejects_a_resolved_result() -> None:
    with pytest.raises(ValidationError, match="uncertain"):
        PlanningFallback(
            category="unsupported_intent",
            reason="The request is outside the supported intent set.",
            safe_result=GroundedResult(
                conclusion="A resolved answer",
                evidence_ids=[uuid4()],
                policy_rule="not_applicable",
                uncertainty=False,
            ),
        )


def test_planning_fallback_rejects_an_uncertain_conclusion() -> None:
    with pytest.raises(ValidationError, match="conclusion"):
        PlanningFallback(
            category="unsupported_intent",
            reason="The request is unsupported.",
            safe_result=GroundedResult(
                conclusion="The route is clear.",
                uncertainty=True,
                uncertainty_reason="The request is unsupported.",
            ),
        )


def test_planning_fallback_rejects_uncertain_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="evidence_ids"):
        PlanningFallback(
            category="unsupported_intent",
            reason="The request is unsupported.",
            safe_result=GroundedResult(
                evidence_ids=[uuid4()],
                uncertainty=True,
                uncertainty_reason="The request is unsupported.",
            ),
        )


def test_planning_fallback_rejects_uncertain_conflicting_ids() -> None:
    with pytest.raises(ValidationError, match="conflicting_ids"):
        PlanningFallback(
            category="unsupported_intent",
            reason="The request is unsupported.",
            safe_result=GroundedResult(
                conflicting_ids=[uuid4()],
                uncertainty=True,
                uncertainty_reason="The request is unsupported.",
            ),
        )


def test_planning_fallback_rejects_uncertain_policy_rule() -> None:
    with pytest.raises(ValidationError, match="policy_rule"):
        PlanningFallback(
            category="unsupported_intent",
            reason="The request is unsupported.",
            safe_result=GroundedResult(
                policy_rule="not_applicable",
                uncertainty=True,
                uncertainty_reason="The request is unsupported.",
            ),
        )


def test_ambiguous_entity_requires_two_candidates() -> None:
    with pytest.raises(ValidationError, match="at least two"):
        PlanningFallback(
            category="ambiguous_entity",
            reason="The object is ambiguous.",
            clarification_candidates=["box_01"],
            safe_result=GroundedResult(
                uncertainty=True,
                uncertainty_reason="The object is ambiguous.",
            ),
        )


def test_ambiguous_entity_candidates_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="unique"):
        PlanningFallback(
            category="ambiguous_entity",
            reason="The object is ambiguous.",
            clarification_candidates=["box_01", "box_01"],
            safe_result=GroundedResult(
                uncertainty=True,
                uncertainty_reason="The object is ambiguous.",
            ),
        )


def test_ambiguous_entity_candidates_cannot_be_empty() -> None:
    with pytest.raises(ValidationError, match="cannot be empty"):
        PlanningFallback(
            category="ambiguous_entity",
            reason="The object is ambiguous.",
            clarification_candidates=["box_01", " "],
            safe_result=GroundedResult(
                uncertainty=True,
                uncertainty_reason="The object is ambiguous.",
            ),
        )


def test_unrelated_fallback_rejects_clarification_candidates() -> None:
    with pytest.raises(ValidationError, match="only valid"):
        PlanningFallback(
            category="missing_entity",
            reason="The object is unknown.",
            clarification_candidates=["box_01", "box_02"],
            safe_result=GroundedResult(
                uncertainty=True,
                uncertainty_reason="The object is unknown.",
            ),
        )


def test_planning_outcome_requires_one_result() -> None:
    plan = ExecutionPlan(
        intent=IntentRequest(intent="current_robot_pose", user_question="Where are you?"),
        current_state_verifiable=False,
        plan_reason="This plan exists only for validation coverage.",
    )

    with pytest.raises(ValidationError, match="exactly one"):
        PlanningOutcome()

    with pytest.raises(ValidationError, match="exactly one"):
        PlanningOutcome(
            plan=plan,
            fallback=PlanningFallback(
                category="unsupported_intent",
                reason="The request is unsupported.",
                safe_result=GroundedResult(
                    uncertainty=True,
                    uncertainty_reason="The request is unsupported.",
                ),
            ),
        )
