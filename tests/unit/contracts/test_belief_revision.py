"""Validation tests for deterministic belief revision requests."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.contracts.models import BeliefRevision


def make_revision(**changes: object) -> BeliefRevision:
    """Build a valid revision, allowing tests to override one field."""
    values: dict[str, object] = {
        "old_fact_id": uuid4(),
        "new_fact_id": uuid4(),
        "reason": "LiDAR detected an obstacle 12 cm ahead.",
        "policy_rule": "fresh_direct_sensor_over_static_map",
        "revised_at": datetime(2026, 9, 26, 10, 0, tzinfo=UTC),
    }
    values.update(changes)
    return BeliefRevision(**values)


def test_belief_revision_accepts_stored_fact_identifiers() -> None:
    revision = make_revision()

    assert revision.old_fact_id != revision.new_fact_id
    assert revision.policy_rule == "fresh_direct_sensor_over_static_map"


def test_belief_revision_rejects_invalid_fact_identifier() -> None:
    with pytest.raises(ValidationError):
        make_revision(old_fact_id="not-a-uuid")


def test_belief_revision_rejects_empty_policy_rule() -> None:
    with pytest.raises(ValidationError):
        make_revision(policy_rule="")


def test_belief_revision_rejects_naive_revision_time() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_revision(revised_at=datetime(2026, 9, 26, 10, 0))
