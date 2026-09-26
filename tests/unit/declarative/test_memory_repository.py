"""Tests for MemoryRepository basic CRUD operations."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from src.contracts import FactAssertion, FactQuery, SpatialContext, StoredFact
from src.declarative.memory_repository import MemoryRepository

_OBS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _assertion(
    subject: str = "route_A",
    predicate: str = "status_is",
    obj: str = "clear",
    source_agent: str = "sensor_01",
    confidence: float = 0.9,
    observed_at: datetime = _OBS,
    context: SpatialContext | None = None,
    evidence: dict | None = None,
) -> FactAssertion:
    return FactAssertion(
        subject=subject,
        predicate=predicate,
        object=obj,
        source_agent=source_agent,
        confidence_score=confidence,
        observed_at=observed_at,
        context=context or SpatialContext(),
        evidence=evidence or {},
    )


# ---------------------------------------------------------------------------
# record_fact round-trip
# ---------------------------------------------------------------------------


def test_record_fact_returns_stored_fact() -> None:
    with MemoryRepository() as repo:
        fa = _assertion()
        sf = repo.record_fact(fa)
        assert isinstance(sf, StoredFact)


def test_stored_fact_has_uuid_fact_id() -> None:
    with MemoryRepository() as repo:
        sf = repo.record_fact(_assertion())
        assert isinstance(sf.fact_id, UUID)


def test_stored_fact_has_tz_aware_created_at() -> None:
    with MemoryRepository() as repo:
        sf = repo.record_fact(_assertion())
        assert sf.created_at.tzinfo is not None


def test_stored_fact_has_tz_aware_observed_at() -> None:
    with MemoryRepository() as repo:
        sf = repo.record_fact(_assertion())
        assert sf.observed_at.tzinfo is not None


def test_stored_fact_superseded_by_is_none() -> None:
    with MemoryRepository() as repo:
        sf = repo.record_fact(_assertion())
        assert sf.superseded_by is None


def test_stored_fact_preserves_fields() -> None:
    with MemoryRepository() as repo:
        fa = _assertion(subject="box_01", predicate="location_is", obj="shelf_B")
        sf = repo.record_fact(fa)
        assert sf.subject == "box_01"
        assert sf.predicate == "location_is"
        assert sf.object == "shelf_B"
        assert sf.source_agent == "sensor_01"
        assert sf.confidence_score == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# context and evidence JSON round-trip
# ---------------------------------------------------------------------------


def test_context_json_round_trip() -> None:
    ctx = SpatialContext(location="room_101", observer="robot_01", world_version=3)
    with MemoryRepository() as repo:
        fa = _assertion(context=ctx)
        repo.record_fact(fa)
        results = repo.query_facts(FactQuery(active_only=False))
        assert len(results) == 1
        stored_ctx = results[0].context
        assert stored_ctx.location == "room_101"
        assert stored_ctx.observer == "robot_01"
        assert stored_ctx.world_version == 3


def test_evidence_json_round_trip() -> None:
    evidence = {"source": "lidar", "scan_id": 42, "quality": "high"}
    with MemoryRepository() as repo:
        fa = _assertion(evidence=evidence)
        repo.record_fact(fa)
        results = repo.query_facts(FactQuery(active_only=False))
        assert results[0].evidence == evidence


# ---------------------------------------------------------------------------
# query_facts
# ---------------------------------------------------------------------------


def test_query_facts_active_only_excludes_none() -> None:
    """An empty store returns an empty list."""
    with MemoryRepository() as repo:
        results = repo.query_facts(FactQuery())
        assert results == []


def test_query_facts_returns_inserted_fact() -> None:
    with MemoryRepository() as repo:
        sf = repo.record_fact(_assertion())
        results = repo.query_facts(FactQuery(active_only=False))
        assert len(results) == 1
        assert results[0].fact_id == sf.fact_id


def test_query_facts_subject_filter() -> None:
    with MemoryRepository() as repo:
        repo.record_fact(_assertion(subject="route_A"))
        repo.record_fact(_assertion(subject="route_B"))
        results = repo.query_facts(FactQuery(subject="route_A", active_only=False))
        assert len(results) == 1
        assert results[0].subject == "route_A"


def test_query_facts_predicate_filter() -> None:
    with MemoryRepository() as repo:
        repo.record_fact(_assertion(predicate="status_is"))
        repo.record_fact(_assertion(predicate="location_is"))
        results = repo.query_facts(FactQuery(predicate="location_is", active_only=False))
        assert len(results) == 1
        assert results[0].predicate == "location_is"


def test_query_facts_source_filter() -> None:
    with MemoryRepository() as repo:
        repo.record_fact(_assertion(source_agent="sensor_01"))
        repo.record_fact(_assertion(source_agent="sensor_02"))
        results = repo.query_facts(FactQuery(source_agent="sensor_02", active_only=False))
        assert len(results) == 1
        assert results[0].source_agent == "sensor_02"


def test_query_facts_context_location_filter() -> None:
    ctx_a = SpatialContext(location="room_101")
    ctx_b = SpatialContext(location="room_202")
    with MemoryRepository() as repo:
        repo.record_fact(_assertion(context=ctx_a))
        repo.record_fact(_assertion(context=ctx_b))
        results = repo.query_facts(
            FactQuery(active_only=False, context=SpatialContext(location="room_101"))
        )
        assert len(results) == 1
        assert results[0].context.location == "room_101"


def test_query_facts_context_observer_filter() -> None:
    ctx = SpatialContext(observer="robot_01")
    with MemoryRepository() as repo:
        repo.record_fact(_assertion(context=ctx))
        repo.record_fact(_assertion(context=SpatialContext(observer="robot_02")))
        results = repo.query_facts(
            FactQuery(active_only=False, context=SpatialContext(observer="robot_01"))
        )
        assert len(results) == 1
        assert results[0].context.observer == "robot_01"


def test_two_conflicting_assertions_coexist() -> None:
    """Two facts with the same subject/predicate but different objects both persist."""
    with MemoryRepository() as repo:
        repo.record_fact(_assertion(obj="clear"))
        repo.record_fact(_assertion(obj="blocked"))
        results = repo.query_facts(FactQuery(active_only=False))
        objects = {r.object for r in results}
        assert objects == {"clear", "blocked"}


def test_query_facts_deterministic_ordering() -> None:
    """Results are ordered by created_at ascending."""
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, tzinfo=UTC)
    t3 = datetime(2026, 1, 3, tzinfo=UTC)
    times = [t1, t2, t3]
    idx = 0

    def sequential_clock() -> datetime:
        nonlocal idx
        t = times[idx]
        idx += 1
        return t

    ids = [uuid4(), uuid4(), uuid4()]
    id_idx = 0

    def sequential_uuid() -> UUID:
        nonlocal id_idx
        u = ids[id_idx]
        id_idx += 1
        return u

    with MemoryRepository(clock=sequential_clock, uuid_factory=sequential_uuid) as repo:
        repo.record_fact(_assertion(subject="A"))
        repo.record_fact(_assertion(subject="B"))
        repo.record_fact(_assertion(subject="C"))
        results = repo.query_facts(FactQuery(active_only=False))
        subjects = [r.subject for r in results]
        assert subjects == ["A", "B", "C"]


def test_query_facts_returns_stored_fact_instances() -> None:
    """query_facts must return StoredFact objects, never raw dicts or Row objects."""
    with MemoryRepository() as repo:
        repo.record_fact(_assertion())
        results = repo.query_facts(FactQuery(active_only=False))
        for item in results:
            assert isinstance(item, StoredFact)


# ---------------------------------------------------------------------------
# extra_context filter
# ---------------------------------------------------------------------------


def test_query_facts_extra_context_filter_matches() -> None:
    """extra_context keys in the query filter must match stored facts exactly."""
    ctx_match = SpatialContext(extra_context={"lighting": "bright", "floor": 2})
    ctx_no_match = SpatialContext(extra_context={"lighting": "dim", "floor": 2})
    with MemoryRepository() as repo:
        repo.record_fact(_assertion(subject="A", context=ctx_match))
        repo.record_fact(_assertion(subject="B", context=ctx_no_match))
        results = repo.query_facts(
            FactQuery(
                active_only=False,
                context=SpatialContext(extra_context={"lighting": "bright"}),
            )
        )
        assert len(results) == 1
        assert results[0].subject == "A"


def test_query_facts_extra_context_partial_key_matches() -> None:
    """A query with a subset of extra_context keys matches any fact that has those keys."""
    ctx = SpatialContext(extra_context={"zone": "alpha", "floor": 3})
    with MemoryRepository() as repo:
        repo.record_fact(_assertion(subject="A", context=ctx))
        repo.record_fact(_assertion(subject="B", context=SpatialContext()))
        # Query only on the 'zone' key
        results = repo.query_facts(
            FactQuery(
                active_only=False,
                context=SpatialContext(extra_context={"zone": "alpha"}),
            )
        )
        assert len(results) == 1
        assert results[0].subject == "A"


# ---------------------------------------------------------------------------
# audit chain corruption / cycle detection
# ---------------------------------------------------------------------------


def test_get_audit_chain_raises_on_cycle() -> None:
    """get_audit_chain must raise ValueError when corrupt superseded_by links form a cycle."""
    with MemoryRepository() as repo:
        f1 = repo.record_fact(_assertion(subject="A"))
        f2 = repo.record_fact(_assertion(subject="B"))

        # Manually create a cycle: f1 -> f2 -> f1 by bypassing the public API
        # (disable FK checks so we can write arbitrary links for this corruption test)
        repo._conn.execute("PRAGMA foreign_keys = OFF")
        repo._conn.execute(
            "UPDATE facts SET superseded_by = ? WHERE fact_id = ?",
            (str(f2.fact_id), str(f1.fact_id)),
        )
        repo._conn.execute(
            "UPDATE facts SET superseded_by = ? WHERE fact_id = ?",
            (str(f1.fact_id), str(f2.fact_id)),
        )
        repo._conn.commit()
        repo._conn.execute("PRAGMA foreign_keys = ON")

        with pytest.raises(ValueError, match="Corrupt audit chain detected"):
            repo.get_audit_chain(f1.fact_id)
