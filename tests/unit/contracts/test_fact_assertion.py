"""Validation tests for fact assertions crossing into Tier 1."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.contracts.models import FactAssertion, SpatialContext


def make_assertion(**changes: object) -> FactAssertion:
    """Build a valid assertion, allowing each test to override one field."""
    values: dict[str, object] = {
        "subject": "route_A",
        "predicate": "status_is",
        "object": "blocked",
        "source_agent": "lidar_sensor",
        "confidence_score": 0.99,
        "observed_at": datetime(2026, 9, 26, 10, 0, tzinfo=UTC),
        "context": SpatialContext(location="room_101"),
        "evidence": {"nearest_distance_cm": 12},
    }
    values.update(changes)
    return FactAssertion(**values)


def test_fact_assertion_accepts_valid_claim() -> None:
    assertion = make_assertion()

    assert assertion.version == "v1"
    assert assertion.subject == "route_A"
    assert assertion.evidence == {"nearest_distance_cm": 12}


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_fact_assertion_rejects_confidence_outside_valid_range(confidence: float) -> None:
    with pytest.raises(ValidationError):
        make_assertion(confidence_score=confidence)


def test_fact_assertion_rejects_naive_observed_time() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_assertion(observed_at=datetime(2026, 9, 26, 10, 0))


def test_fact_assertion_rejects_empty_subject() -> None:
    with pytest.raises(ValidationError):
        make_assertion(subject="")
