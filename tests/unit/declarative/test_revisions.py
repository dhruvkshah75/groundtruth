"""Tests for atomic belief revision in MemoryRepository."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from src.contracts import AuditEvent, FactAssertion, FactQuery, SpatialContext, StoredFact
from src.declarative.memory_repository import MemoryRepository, RevisionOutcome

_OBS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_REVISED_AT = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)


def _assertion(
    subject: str = "route_A",
    predicate: str = "status_is",
    obj: str = "clear",
    source_agent: str = "sensor_01",
    confidence: float = 0.9,
) -> FactAssertion:
    return FactAssertion(
        subject=subject,
        predicate=predicate,
        object=obj,
        source_agent=source_agent,
        confidence_score=confidence,
        observed_at=_OBS,
        context=SpatialContext(),
        evidence={},
    )


def _lidar_assertion() -> FactAssertion:
    return FactAssertion(
        subject="route_A",
        predicate="status_is",
        object="lidar_blocked",
        source_agent="lidar_01",
        confidence_score=0.99,
        observed_at=_OBS,
        context=SpatialContext(),
        evidence={"scan_id": 7},
    )


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_revision_returns_revision_outcome() -> None:
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="map_clear"))
        outcome = repo.record_revision(
            old_fact_id=old.fact_id,
            replacement=_lidar_assertion(),
            reason="LiDAR scan contradicts map",
            policy_rule="lidar_overrides_map",
            revised_at=_REVISED_AT,
        )
        assert isinstance(outcome, RevisionOutcome)


def test_revision_successor_is_stored_fact() -> None:
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="map_clear"))
        outcome = repo.record_revision(
            old_fact_id=old.fact_id,
            replacement=_lidar_assertion(),
            reason="LiDAR scan contradicts map",
            policy_rule="lidar_overrides_map",
            revised_at=_REVISED_AT,
        )
        assert isinstance(outcome.successor, StoredFact)
        assert outcome.successor.object == "lidar_blocked"
        assert outcome.successor.superseded_by is None


def test_revision_predecessor_superseded_by_points_to_successor() -> None:
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="map_clear"))
        outcome = repo.record_revision(
            old_fact_id=old.fact_id,
            replacement=_lidar_assertion(),
            reason="LiDAR scan contradicts map",
            policy_rule="lidar_overrides_map",
            revised_at=_REVISED_AT,
        )
        # Re-query the old fact
        results = repo.query_facts(FactQuery(active_only=False))
        old_stored = next(f for f in results if f.fact_id == old.fact_id)
        assert old_stored.superseded_by == outcome.successor.fact_id


def test_active_query_returns_only_successor() -> None:
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="map_clear"))
        outcome = repo.record_revision(
            old_fact_id=old.fact_id,
            replacement=_lidar_assertion(),
            reason="LiDAR scan contradicts map",
            policy_rule="lidar_overrides_map",
            revised_at=_REVISED_AT,
        )
        active = repo.query_facts(FactQuery(subject="route_A", predicate="status_is"))
        assert len(active) == 1
        assert active[0].fact_id == outcome.successor.fact_id


def test_historical_query_returns_both_facts() -> None:
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="map_clear"))
        repo.record_revision(
            old_fact_id=old.fact_id,
            replacement=_lidar_assertion(),
            reason="LiDAR scan contradicts map",
            policy_rule="lidar_overrides_map",
            revised_at=_REVISED_AT,
        )
        all_facts = repo.query_facts(
            FactQuery(subject="route_A", predicate="status_is", active_only=False)
        )
        assert len(all_facts) == 2


def test_audit_event_has_correct_ids_and_metadata() -> None:
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="map_clear"))
        outcome = repo.record_revision(
            old_fact_id=old.fact_id,
            replacement=_lidar_assertion(),
            reason="LiDAR scan contradicts map",
            policy_rule="lidar_overrides_map",
            revised_at=_REVISED_AT,
        )
        ev = outcome.audit_event
        assert isinstance(ev, AuditEvent)
        assert old.fact_id in ev.input_fact_ids
        assert outcome.successor.fact_id in ev.input_fact_ids
        assert ev.output_fact_ids == [outcome.successor.fact_id]
        assert ev.reason == "LiDAR scan contradicts map"
        assert ev.policy_rule == "lidar_overrides_map"
        assert ev.event_type == "belief_revision"


# ---------------------------------------------------------------------------
# error cases
# ---------------------------------------------------------------------------


def test_revision_unknown_fact_id_raises_value_error() -> None:
    with MemoryRepository() as repo:
        with pytest.raises(ValueError, match="does not exist"):
            repo.record_revision(
                old_fact_id=uuid4(),
                replacement=_lidar_assertion(),
                reason="LiDAR says blocked",
                policy_rule="lidar_overrides_map",
                revised_at=_REVISED_AT,
            )


def test_revision_already_superseded_raises_value_error() -> None:
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="map_clear"))
        outcome = repo.record_revision(
            old_fact_id=old.fact_id,
            replacement=_lidar_assertion(),
            reason="LiDAR says blocked",
            policy_rule="lidar_overrides_map",
            revised_at=_REVISED_AT,
        )
        with pytest.raises(ValueError, match="already superseded"):
            repo.record_revision(
                old_fact_id=old.fact_id,
                replacement=_lidar_assertion(),
                reason="second attempt",
                policy_rule="lidar_overrides_map",
                revised_at=_REVISED_AT,
            )
        # Silence unused variable warning
        _ = outcome


def test_revision_empty_reason_raises_value_error() -> None:
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion())
        with pytest.raises(ValueError, match="reason"):
            repo.record_revision(
                old_fact_id=old.fact_id,
                replacement=_lidar_assertion(),
                reason="   ",
                policy_rule="lidar_overrides_map",
                revised_at=_REVISED_AT,
            )


def test_revision_empty_policy_rule_raises_value_error() -> None:
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion())
        with pytest.raises(ValueError, match="policy_rule"):
            repo.record_revision(
                old_fact_id=old.fact_id,
                replacement=_lidar_assertion(),
                reason="good reason",
                policy_rule="",
                revised_at=_REVISED_AT,
            )


# ---------------------------------------------------------------------------
# rollback test
# ---------------------------------------------------------------------------


def test_revision_rollback_on_audit_insert_failure() -> None:
    """If the audit event INSERT fails, no successor row must remain and
    the predecessor must still be active (superseded_by IS NULL).

    sqlite3.Connection.execute is read-only in Python 3.12, so we inject
    failure through a thin proxy that wraps the real connection.
    """
    import sqlite3 as _sqlite3

    class _FailOnAuditInsert:
        """Proxy that raises on INSERT INTO audit_events, delegates all else."""

        def __init__(self, real_conn: _sqlite3.Connection) -> None:
            self._real = real_conn
            self.triggered = False

        def execute(self, sql: str, params: Any = ()) -> Any:
            if "INSERT INTO audit_events" in sql:
                self.triggered = True
                raise _sqlite3.OperationalError("simulated audit insert failure")
            return self._real.execute(sql, params)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="map_clear"))
        old_fact_id_str = str(old.fact_id)

        real_conn = repo._conn
        proxy = _FailOnAuditInsert(real_conn)
        repo._conn = proxy  # type: ignore[assignment]

        with pytest.raises(_sqlite3.OperationalError, match="simulated audit insert failure"):
            repo.record_revision(
                old_fact_id=old.fact_id,
                replacement=_lidar_assertion(),
                reason="LiDAR says blocked",
                policy_rule="lidar_overrides_map",
                revised_at=_REVISED_AT,
            )

        assert proxy.triggered, "proxy must have intercepted the audit INSERT"

        # Restore the real connection for verification queries
        repo._conn = real_conn  # type: ignore[assignment]

        # Predecessor must still be active
        row = real_conn.execute(
            "SELECT superseded_by FROM facts WHERE fact_id = ?", (old_fact_id_str,)
        ).fetchone()
        assert row is not None
        assert row["superseded_by"] is None, "predecessor must not be modified after rollback"

        # No successor row should exist
        count = real_conn.execute(
            "SELECT COUNT(*) FROM facts WHERE superseded_by IS NULL AND fact_id != ?",
            (old_fact_id_str,),
        ).fetchone()[0]
        assert count == 0, "no partial successor row should remain after rollback"


def test_revision_rollback_on_predecessor_link_failure() -> None:
    """If the predecessor UPDATE fails (before audit insert), no successor row must
    remain, the predecessor must still be active, and no audit event must exist.

    This exercises the rollback path for Step 2 (linking predecessor to successor).
    """
    import sqlite3 as _sqlite3

    class _FailOnPredecessorUpdate:
        """Proxy that raises on UPDATE facts SET superseded_by, delegates all else."""

        def __init__(self, real_conn: _sqlite3.Connection) -> None:
            self._real = real_conn
            self.triggered = False

        def execute(self, sql: str, params: Any = ()) -> Any:
            if "UPDATE facts SET superseded_by" in sql:
                self.triggered = True
                raise _sqlite3.OperationalError("simulated predecessor link failure")
            return self._real.execute(sql, params)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="map_clear"))
        old_fact_id_str = str(old.fact_id)

        real_conn = repo._conn
        proxy = _FailOnPredecessorUpdate(real_conn)
        repo._conn = proxy  # type: ignore[assignment]

        with pytest.raises(_sqlite3.OperationalError, match="simulated predecessor link failure"):
            repo.record_revision(
                old_fact_id=old.fact_id,
                replacement=_lidar_assertion(),
                reason="LiDAR says blocked",
                policy_rule="lidar_overrides_map",
                revised_at=_REVISED_AT,
            )

        assert proxy.triggered, "proxy must have intercepted the UPDATE"

        repo._conn = real_conn  # type: ignore[assignment]

        # Predecessor must still be active
        row = real_conn.execute(
            "SELECT superseded_by FROM facts WHERE fact_id = ?", (old_fact_id_str,)
        ).fetchone()
        assert row is not None
        assert row["superseded_by"] is None, "predecessor must remain active after rollback"

        # No successor row should exist (was rolled back)
        all_facts = real_conn.execute("SELECT fact_id FROM facts").fetchall()
        assert len(all_facts) == 1, "only the original fact should remain after rollback"
        assert str(all_facts[0]["fact_id"]) == old_fact_id_str

        # No audit event should exist
        audit_count = real_conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        assert audit_count == 0, "no audit event must remain after rollback"
