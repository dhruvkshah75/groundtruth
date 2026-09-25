"""Validation tests for Tier 1 audit event and trail contracts."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from src.contracts.models import AuditEvent, AuditTrail, StoredFact


def make_stored_fact(fact_id: UUID | None = None) -> StoredFact:
    """Build one stored fact for audit-trail tests."""
    return StoredFact(
        fact_id=fact_id or uuid4(),
        subject="route_A",
        predicate="status_is",
        object="blocked",
        source_agent="lidar_sensor",
        confidence_score=0.99,
        observed_at=datetime(2026, 9, 26, 10, 0, tzinfo=UTC),
        created_at=datetime(2026, 9, 26, 10, 0, 1, tzinfo=UTC),
    )


def make_audit_event(**changes: object) -> AuditEvent:
    """Build a valid event, allowing tests to override one field."""
    map_fact_id = uuid4()
    lidar_fact_id = uuid4()
    values: dict[str, object] = {
        "event_id": uuid4(),
        "event_type": "belief_revision",
        "input_fact_ids": [map_fact_id, lidar_fact_id],
        "output_fact_ids": [lidar_fact_id],
        "reason": "LiDAR detected an obstacle 12 cm ahead.",
        "policy_rule": "fresh_direct_sensor_over_static_map",
        "created_at": datetime(2026, 9, 26, 10, 0, 2, tzinfo=UTC),
    }
    values.update(changes)
    return AuditEvent(**values)


def test_audit_event_accepts_revision_proof() -> None:
    event = make_audit_event()

    assert event.event_type == "belief_revision"
    assert len(event.input_fact_ids) == 2
    assert len(event.output_fact_ids) == 1


def test_audit_event_rejects_empty_input_facts() -> None:
    with pytest.raises(ValidationError):
        make_audit_event(input_fact_ids=[])


def test_audit_event_rejects_naive_created_time() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_audit_event(created_at=datetime(2026, 9, 26, 10, 0, 2))


def test_audit_trail_contains_requested_root_fact() -> None:
    root_fact = make_stored_fact()
    trail = AuditTrail(
        root_fact_id=root_fact.fact_id,
        facts=[root_fact],
        events=[make_audit_event()],
    )

    assert trail.root_fact_id == root_fact.fact_id
    assert len(trail.facts) == 1
    assert len(trail.events) == 1


def test_audit_trail_rejects_missing_root_fact() -> None:
    with pytest.raises(ValidationError, match="root_fact_id"):
        AuditTrail(root_fact_id=uuid4(), facts=[make_stored_fact()])
