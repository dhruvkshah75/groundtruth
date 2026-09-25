"""Validation tests for the remaining cross-tier contracts."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.contracts.models import (
    CapabilityDescriptor,
    EntityResolution,
    GroundedResult,
    IntentRequest,
    ObservationRequest,
    ObservationUnavailable,
    SensorObservation,
)


def test_entity_resolution_supports_all_three_outcomes() -> None:
    resolved = EntityResolution(
        mention="blue box", status="resolved", canonical_entity_id="box_01"
    )
    ambiguous = EntityResolution(
        mention="the box", status="ambiguous", candidates=["box_01", "box_02"]
    )
    missing = EntityResolution(mention="green crate", status="missing")

    assert resolved.canonical_entity_id == "box_01"
    assert ambiguous.candidates == ["box_01", "box_02"]
    assert missing.candidates == []


def test_entity_resolution_rejects_inconsistent_outcome() -> None:
    with pytest.raises(ValidationError):
        EntityResolution(mention="the box", status="ambiguous", candidates=["box_01"])


def test_capability_and_observation_request_are_validated() -> None:
    descriptor = CapabilityDescriptor(
        name="lidar_scan",
        kind="sensor",
        description="Measures nearby obstacle distance.",
        parameters=["direction"],
    )
    request = ObservationRequest(
        capability="lidar_scan", target="route_A", parameters={"direction": "front"}
    )

    assert descriptor.kind == "sensor"
    assert request.parameters == {"direction": "front"}


def test_sensor_observation_requires_measurements_and_aware_time() -> None:
    observation = SensorObservation(
        observation_id=uuid4(),
        sensor="lidar_sensor",
        capability="lidar_scan",
        observed_at=datetime(2026, 9, 26, 10, 0, tzinfo=UTC),
        confidence_score=0.99,
        measurements={"nearest_distance_cm": 12},
    )

    assert observation.measurements["nearest_distance_cm"] == 12

    with pytest.raises(ValidationError):
        SensorObservation(
            observation_id=uuid4(),
            sensor="lidar_sensor",
            capability="lidar_scan",
            observed_at=datetime(2026, 9, 26, 10, 0),
            confidence_score=0.99,
            measurements={},
        )


def test_observation_unavailable_has_structured_reason() -> None:
    unavailable = ObservationUnavailable(
        capability="read_temperature",
        reason="unsupported_capability",
        message="The mock environment has no temperature sensor.",
        reported_at=datetime(2026, 9, 26, 10, 0, tzinfo=UTC),
    )

    assert unavailable.reason == "unsupported_capability"


def test_intent_request_rejects_unknown_intent() -> None:
    with pytest.raises(ValidationError):
        IntentRequest(intent="read_weather", user_question="What is the weather?")


def test_grounded_result_requires_evidence_for_resolved_claim() -> None:
    result = GroundedResult(
        conclusion="route_A is blocked",
        evidence_ids=[uuid4()],
        policy_rule="fresh_direct_sensor_over_static_map",
        uncertainty=False,
    )

    assert result.uncertainty is False

    with pytest.raises(ValidationError, match="evidence_ids"):
        GroundedResult(
            conclusion="route_A is blocked",
            policy_rule="fresh_direct_sensor_over_static_map",
            uncertainty=False,
        )


def test_grounded_result_requires_reason_when_uncertain() -> None:
    with pytest.raises(ValidationError, match="uncertainty_reason"):
        GroundedResult(uncertainty=True)
