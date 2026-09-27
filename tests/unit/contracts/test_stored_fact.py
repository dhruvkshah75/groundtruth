"""Validation tests for stored Tier 1 facts."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.contracts.models import StoredFact


def make_stored_fact(**changes: object) -> StoredFact:
    """Build a valid stored fact, allowing each test to override one field."""
    values: dict[str, object] = {
        "subject": "route_A",
        "predicate": "status_is",
        "object": "clear",
        "source_agent": "static_map",
        "confidence_score": 0.90,
        "observed_at": datetime(2026, 9, 26, 9, 0, tzinfo=UTC),
        "fact_id": uuid4(),
        "created_at": datetime(2026, 9, 26, 9, 0, 1, tzinfo=UTC),
    }
    values.update(changes)
    return StoredFact(**values)


def test_stored_fact_contains_tier_one_storage_details() -> None:
    fact = make_stored_fact()

    assert fact.fact_id is not None
    assert fact.created_at.tzinfo is not None
    assert fact.superseded_by is None


def test_stored_fact_accepts_a_successor_fact_uuid() -> None:
    successor_id = uuid4()
    fact = make_stored_fact(superseded_by=successor_id)

    assert fact.superseded_by == successor_id


def test_stored_fact_rejects_naive_created_at() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_stored_fact(created_at=datetime(2026, 9, 26, 9, 0, 1))


def test_stored_fact_rejects_invalid_fact_id() -> None:
    with pytest.raises(ValidationError):
        make_stored_fact(fact_id="not-a-uuid")
