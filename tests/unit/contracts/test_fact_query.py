"""Validation tests for safe Tier 1 fact queries."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.contracts.models import FactQuery, SpatialContext


def test_fact_query_defaults_to_active_facts() -> None:
    query = FactQuery(subject="route_A", predicate="status_is")

    assert query.active_only is True
    assert query.source_agent is None
    assert query.as_of is None


def test_fact_query_accepts_history_and_context_filters() -> None:
    query = FactQuery(
        subject="route_A",
        predicate="status_is",
        source_agent="static_map",
        active_only=False,
        as_of=datetime(2026, 9, 26, 9, 0, tzinfo=UTC),
        context=SpatialContext(location="room_101"),
    )

    assert query.active_only is False
    assert query.context is not None
    assert query.context.location == "room_101"


def test_fact_query_rejects_naive_historical_time() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        FactQuery(as_of=datetime(2026, 9, 26, 9, 0))


def test_fact_query_rejects_empty_filter_value() -> None:
    with pytest.raises(ValidationError):
        FactQuery(subject="")
