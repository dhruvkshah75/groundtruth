"""Boundary and integration tests for Tier 1 declarative memory."""

import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.contracts import (
    AuditEvent,
    AuditTrail,
    EntityResolution,
    FactAssertion,
    FactQuery,
    SpatialContext,
    StoredFact,
)
from src.declarative.memory_repository import MemoryRepository

_OBS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _assertion(
    subject: str = "route_A",
    predicate: str = "status_is",
    obj: str = "clear",
    source_agent: str = "sensor_01",
    confidence: float = 0.9,
    observed_at: datetime = _OBS,
) -> FactAssertion:
    return FactAssertion(
        subject=subject,
        predicate=predicate,
        object=obj,
        source_agent=source_agent,
        confidence_score=confidence,
        observed_at=observed_at,
        context=SpatialContext(),
        evidence={},
    )


# ---------------------------------------------------------------------------
# Tier isolation: no Tier 2 / Tier 3 imports
# ---------------------------------------------------------------------------


def test_memory_repository_does_not_import_procedural() -> None:
    """Importing memory_repository must not pull in src.procedural."""
    # The module is already imported; just verify procedural is NOT in sys.modules
    # as a side-effect of importing declarative.
    import src.declarative.memory_repository  # noqa: F401

    for key in sys.modules:
        assert not key.startswith("src.procedural"), (
            f"Tier 1 must not import Tier 2 code; found: {key}"
        )


def test_memory_repository_does_not_import_networkx() -> None:
    """Tier 1 must not import networkx (reserved for Tier 3 graph projections)."""
    import src.declarative.memory_repository  # noqa: F401

    assert "networkx" not in sys.modules, "Tier 1 must not depend on networkx"


# ---------------------------------------------------------------------------
# Public API return types
# ---------------------------------------------------------------------------


def test_record_fact_returns_stored_fact_not_dict() -> None:
    with MemoryRepository() as repo:
        result = repo.record_fact(_assertion())
        assert isinstance(result, StoredFact)
        assert not isinstance(result, dict)


def test_query_facts_returns_list_of_stored_facts() -> None:
    with MemoryRepository() as repo:
        repo.record_fact(_assertion())
        results = repo.query_facts(FactQuery(active_only=False))
        assert isinstance(results, list)
        for item in results:
            assert isinstance(item, StoredFact)


def test_get_audit_chain_returns_audit_trail() -> None:
    with MemoryRepository() as repo:
        sf = repo.record_fact(_assertion())
        trail = repo.get_audit_chain(sf.fact_id)
        assert isinstance(trail, AuditTrail)


def test_resolve_entity_returns_entity_resolution() -> None:
    with MemoryRepository() as repo:
        result = repo.resolve_entity("anything")
        assert isinstance(result, EntityResolution)


def test_record_revision_audit_event_is_audit_event() -> None:
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="clear"))
        outcome = repo.record_revision(
            old_fact_id=old.fact_id,
            replacement=_assertion(obj="blocked"),
            reason="sensor update",
            policy_rule="sensor_wins",
            revised_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        assert isinstance(outcome.audit_event, AuditEvent)


# ---------------------------------------------------------------------------
# as_of temporal queries
# ---------------------------------------------------------------------------


def test_as_of_excludes_facts_learned_later() -> None:
    """A fact created after as_of must not appear in the result."""
    t_early = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    t_late = datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
    as_of = datetime(2026, 1, 1, 10, 30, tzinfo=UTC)

    times = [t_early, t_late]
    idx = 0

    def seq_clock() -> datetime:
        nonlocal idx
        t = times[idx]
        idx += 1
        return t

    uuids = [uuid4(), uuid4()]
    uid_idx = 0

    def seq_uuid():
        nonlocal uid_idx
        u = uuids[uid_idx]
        uid_idx += 1
        return u

    with MemoryRepository(clock=seq_clock, uuid_factory=seq_uuid) as repo:
        early_fact = repo.record_fact(_assertion(subject="fact_early"))
        _late_fact = repo.record_fact(_assertion(subject="fact_late"))

        results = repo.query_facts(FactQuery(active_only=False, as_of=as_of))
        fact_ids = {r.fact_id for r in results}
        assert early_fact.fact_id in fact_ids
        assert _late_fact.fact_id not in fact_ids


def test_active_as_of_does_not_leak_future_supersession() -> None:
    """A fact active at T must not appear superseded when queried as_of=T,
    even if it was superseded at T+1."""
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    t1 = datetime(2026, 1, 1, 10, 30, tzinfo=UTC)

    clock_times = [t0, t1]
    c_idx = 0

    def seq_clock() -> datetime:
        nonlocal c_idx
        t = clock_times[c_idx % len(clock_times)]
        c_idx += 1
        return t

    with MemoryRepository(clock=seq_clock) as repo:
        old = repo.record_fact(_assertion(obj="clear"))  # created_at = t0

        # Revise at t1
        outcome = repo.record_revision(
            old_fact_id=old.fact_id,
            replacement=_assertion(obj="blocked"),
            reason="lidar update",
            policy_rule="lidar_wins",
            revised_at=t1,
        )

        # Query as_of t0 – old fact should appear as active (supersession is at t1)
        as_of_t0 = t0 + timedelta(seconds=1)
        results = repo.query_facts(
            FactQuery(subject="route_A", predicate="status_is", active_only=True, as_of=as_of_t0)
        )
        fact_ids = {r.fact_id for r in results}
        assert old.fact_id in fact_ids, "old fact must be active as-of t0"
        assert outcome.successor.fact_id not in fact_ids, "successor must not exist as-of t0"


def test_unfiltered_active_query_returns_all_active_facts() -> None:
    """FactQuery() with defaults (active_only=True, no filters) returns all active facts."""
    with MemoryRepository() as repo:
        f1 = repo.record_fact(_assertion(subject="route_A"))
        f2 = repo.record_fact(_assertion(subject="route_B"))
        f3 = repo.record_fact(_assertion(subject="route_C"))

        results = repo.query_facts(FactQuery())
        fact_ids = {r.fact_id for r in results}
        assert f1.fact_id in fact_ids
        assert f2.fact_id in fact_ids
        assert f3.fact_id in fact_ids


# ---------------------------------------------------------------------------
# get_audit_chain
# ---------------------------------------------------------------------------


def test_get_audit_chain_unknown_fact_raises_value_error() -> None:
    with MemoryRepository() as repo:
        with pytest.raises(ValueError, match="does not exist"):
            repo.get_audit_chain(uuid4())


def test_get_audit_chain_contains_root_fact() -> None:
    with MemoryRepository() as repo:
        sf = repo.record_fact(_assertion())
        trail = repo.get_audit_chain(sf.fact_id)
        assert trail.root_fact_id == sf.fact_id
        fact_ids = {f.fact_id for f in trail.facts}
        assert sf.fact_id in fact_ids


def test_get_audit_chain_includes_both_facts_after_revision() -> None:
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="clear"))
        outcome = repo.record_revision(
            old_fact_id=old.fact_id,
            replacement=_assertion(obj="blocked"),
            reason="lidar update",
            policy_rule="lidar_wins",
            revised_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        trail = repo.get_audit_chain(old.fact_id)
        fact_ids = {f.fact_id for f in trail.facts}
        assert old.fact_id in fact_ids
        assert outcome.successor.fact_id in fact_ids
        assert len(trail.events) == 1
