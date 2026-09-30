"""Integration tests for ActiveBeliefGraph cache lifecycle against real SQLite.

Uses the real in-memory MemoryRepository from #3 (:memory: db) to prove that
cache invalidation, rebuild, revision semantics, and failed-write isolation
work correctly against actual SQLite transactions — not just fakes.

Unit-level projection tests (fake reader, no SQLite) live in:
    tests/unit/declarative/test_active_graph_projection.py
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.contracts import FactAssertion, FactQuery, SpatialContext
from src.declarative.active_graph import ActiveBeliefGraph, GraphSyncedRepository
from src.declarative.consistency import check_graph_consistency
from src.declarative.memory_repository import MemoryRepository, RevisionOutcome

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_OBS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_REVISED = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)


def _assertion(
    subject: str = "route_A",
    predicate: str = "status_is",
    obj: str = "clear",
    source_agent: str = "static_map",
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


# ---------------------------------------------------------------------------
# Cache lifecycle tests
# ---------------------------------------------------------------------------


def test_first_access_performs_one_rebuild() -> None:
    """get_graph() on a fresh ActiveBeliefGraph triggers exactly one query_facts call."""
    with MemoryRepository() as repo:
        repo.record_fact(_assertion())
        abg = ActiveBeliefGraph(repo)

        call_count = 0
        original = repo.query_facts

        def counting_query(q: FactQuery) -> list:
            nonlocal call_count
            call_count += 1
            return original(q)

        repo.query_facts = counting_query  # type: ignore[method-assign]
        _ = abg.get_graph()
        assert call_count == 1


def test_repeated_access_without_write_reuses_cache() -> None:
    """Calling get_graph() multiple times without any write uses only one rebuild."""
    with MemoryRepository() as repo:
        repo.record_fact(_assertion())
        abg = ActiveBeliefGraph(repo)

        call_count = 0
        original = repo.query_facts

        def counting_query(q: FactQuery) -> list:
            nonlocal call_count
            call_count += 1
            return original(q)

        repo.query_facts = counting_query  # type: ignore[method-assign]

        _ = abg.get_graph()
        _ = abg.get_graph()
        _ = abg.get_graph()
        assert call_count == 1  # built once, reused twice


def test_successful_record_fact_invalidates_cache() -> None:
    """After a successful record_fact + invalidate(), the next get_graph() rebuilds."""
    with MemoryRepository() as repo:
        abg = ActiveBeliefGraph(repo)

        _ = abg.get_graph()  # prime the cache (0 facts)
        assert abg.get_graph().number_of_edges() == 0

        repo.record_fact(_assertion())
        abg.invalidate()  # caller signals the cache is stale after the commit

        graph = abg.get_graph()
        assert graph.number_of_edges() == 1


def test_write_through_synced_repository_invalidates_without_manual_call() -> None:
    """A repository write made through GraphSyncedRepository must invalidate
    the graph automatically — no manual abg.invalidate() call required.

    This is the regression test requested in review: it proves a normal
    caller writing a fact and then reading the graph sees fresh state,
    without ever calling invalidate() themselves.
    """
    with MemoryRepository() as repo:
        abg = ActiveBeliefGraph(repo)
        synced = GraphSyncedRepository(repo, abg)

        assert abg.get_graph().number_of_edges() == 0

        synced.record_fact(_assertion())
        # No manual abg.invalidate() call here — this is the point of the test.

        graph = abg.get_graph()
        assert graph.number_of_edges() == 1


def test_write_through_synced_repository_revision_invalidates_without_manual_call() -> None:
    """record_revision through GraphSyncedRepository also auto-invalidates."""
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="clear"))
        abg = ActiveBeliefGraph(repo)
        synced = GraphSyncedRepository(repo, abg)

        initial = abg.get_graph()
        assert initial.has_edge("route_A", "clear", key=str(old.fact_id))

        synced.record_revision(
            old_fact_id=old.fact_id,
            replacement=_assertion(obj="blocked", source_agent="lidar"),
            reason="lidar update",
            policy_rule="lidar_wins",
            revised_at=_REVISED,
        )
        # No manual abg.invalidate() call here either.

        updated = abg.get_graph()
        assert not updated.has_edge("route_A", "clear", key=str(old.fact_id))
        assert updated.number_of_edges() == 1


def test_write_through_synced_repository_failed_write_does_not_invalidate() -> None:
    """A failed write through GraphSyncedRepository must NOT invalidate the
    graph — the exception must propagate before invalidate() is reached.
    """
    with MemoryRepository() as repo:
        abg = ActiveBeliefGraph(repo)
        synced = GraphSyncedRepository(repo, abg)

        _ = abg.get_graph()  # prime cache at 0 edges

        with pytest.raises(ValueError):
            synced.record_revision(
                old_fact_id=uuid4(),
                replacement=_assertion(obj="blocked"),
                reason="bad revision",
                policy_rule="test_rule",
                revised_at=_REVISED,
            )

        # Cache must still be the primed empty state, not invalidated.
        assert abg.get_graph().number_of_edges() == 0


def test_successful_record_revision_invalidates_cache() -> None:
    """After a successful record_revision + invalidate(), the next get_graph() rebuilds."""
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="clear"))
        abg = ActiveBeliefGraph(repo)

        initial = abg.get_graph()
        assert initial.has_edge("route_A", "clear", key=str(old.fact_id))

        outcome = repo.record_revision(
            old_fact_id=old.fact_id,
            replacement=_assertion(obj="blocked", source_agent="lidar"),
            reason="lidar update",
            policy_rule="lidar_wins",
            revised_at=_REVISED,
        )
        abg.invalidate()

        updated = abg.get_graph()
        # Old edge is gone, new edge is present
        assert not updated.has_edge("route_A", "clear", key=str(old.fact_id))
        assert updated.has_edge("route_A", "blocked", key=str(outcome.successor.fact_id))


def test_after_revision_rebuild_excludes_predecessor_includes_successor() -> None:
    """Scenario A: route_A status_is clear (static_map) → revised to blocked (lidar).

    After lazy rebuild:
    - route_A --[status_is, fact_lidar]--> blocked   ← visible
    - route_A --[status_is, fact_map]-->  clear      ← NOT visible in graph

    The old fact must still be queryable via SQLite history (get_audit_chain),
    but must not appear as an active graph edge.
    """
    with MemoryRepository() as repo:
        # --- Before revision ---
        fact_map = repo.record_fact(_assertion(obj="clear", source_agent="static_map"))
        abg = ActiveBeliefGraph(repo)

        before = abg.get_graph()
        assert before.has_edge("route_A", "clear", key=str(fact_map.fact_id))

        # --- Tier 2 later commits a valid revision ---
        outcome = repo.record_revision(
            old_fact_id=fact_map.fact_id,
            replacement=_assertion(obj="blocked", source_agent="lidar"),
            reason="lidar detected obstacle",
            policy_rule="lidar_wins",
            revised_at=_REVISED,
        )
        fact_lidar = outcome.successor
        abg.invalidate()

        # --- After lazy rebuild ---
        after = abg.get_graph()
        assert after.has_edge("route_A", "blocked", key=str(fact_lidar.fact_id))
        assert not after.has_edge("route_A", "clear", key=str(fact_map.fact_id))

        # Old fact remains queryable in SQLite history (not from the graph)
        trail = repo.get_audit_chain(fact_map.fact_id)
        history_ids = {f.fact_id for f in trail.facts}
        assert fact_map.fact_id in history_ids
        assert fact_lidar.fact_id in history_ids


def test_failed_or_rolled_back_write_does_not_create_graph_edge() -> None:
    """A write that raises (rolling back the transaction) must not create a graph edge.

    Simulated by attempting to revise a nonexistent fact_id, which causes
    MemoryRepository.record_revision to raise ValueError and roll back.
    """
    with MemoryRepository() as repo:
        abg = ActiveBeliefGraph(repo)

        _ = abg.get_graph()  # prime cache

        nonexistent_id = uuid4()
        with pytest.raises(ValueError):
            repo.record_revision(
                old_fact_id=nonexistent_id,
                replacement=_assertion(obj="blocked"),
                reason="bad revision",
                policy_rule="test_rule",
                revised_at=_REVISED,
            )
        # Must NOT invalidate — the write failed.  Graph still has 0 edges.
        assert abg.get_graph().number_of_edges() == 0


def test_failed_write_does_not_expose_uncommitted_state() -> None:
    """A failed write that partially executes inside a transaction must not
    expose any of that intermediate state through the graph.

    This is distinct from the previous test: here we verify that even if the
    new fact was partially inserted (before the revision link step failed),
    the graph never sees it — because we only call invalidate() after a
    successful commit, and we never call it here.
    """
    with MemoryRepository() as repo:
        existing = repo.record_fact(_assertion(obj="clear"))
        abg = ActiveBeliefGraph(repo)

        initial = abg.get_graph()
        assert initial.number_of_edges() == 1

        # Force a failure inside the revision transaction by providing
        # an already-superseded fact. First, legitimately supersede it.
        outcome = repo.record_revision(
            old_fact_id=existing.fact_id,
            replacement=_assertion(obj="blocked", source_agent="lidar_01"),
            reason="first revision",
            policy_rule="lidar_wins",
            revised_at=_REVISED,
        )
        # Do NOT call invalidate() — simulate a caller that didn't get told
        # the write succeeded, or that the transaction was rolled back after
        # the fact insert but before the revision link.
        #
        # Try revising the already-superseded fact (will raise):
        with pytest.raises(ValueError, match="already superseded"):
            repo.record_revision(
                old_fact_id=existing.fact_id,
                replacement=_assertion(obj="unknown"),
                reason="bad double revision",
                policy_rule="test_rule",
                revised_at=_REVISED,
            )

        # Cache was never invalidated, so graph still reflects old state.
        graph = abg.get_graph()
        assert graph.has_edge("route_A", "clear", key=str(existing.fact_id))
        # The successfully revised edge (from the first revision) is NOT visible
        # because the cache was primed before that revision and never invalidated.
        assert not graph.has_edge("route_A", "blocked", key=str(outcome.successor.fact_id))


def test_restart_produces_same_logical_projection() -> None:
    """Recreating ActiveBeliefGraph against the same SQLite state yields the
    same logical projection (same subject/predicate/object/source triples).

    Per the issue's warning: we do NOT compare fact_id UUIDs across independent
    instances (they were generated by the same repo, so they are equal here —
    but the test asserts on logical triples, not UUID identity, to document
    the correct comparison strategy).
    """
    with MemoryRepository() as repo:
        repo.record_fact(_assertion(subject="route_A", obj="clear"))
        repo.record_fact(_assertion(subject="box_01", predicate="colour_is", obj="red"))

        def _logical_triples(abg: ActiveBeliefGraph) -> set[tuple[str, str, str, str]]:
            graph = abg.get_graph()
            return {
                (u, attrs["predicate"], v, attrs["source_agent"])
                for u, v, attrs in graph.edges(data=True)
            }

        first_instance = ActiveBeliefGraph(repo)
        triples_first = _logical_triples(first_instance)

        # "Restart" — discard first instance, create a new one on the same repo
        second_instance = ActiveBeliefGraph(repo)
        triples_second = _logical_triples(second_instance)

        assert triples_first == triples_second


def test_mutating_returned_graph_does_not_corrupt_later_reads() -> None:
    """Integration version: get_graph() returns a defensive copy.

    Mutating it must not affect the graph returned by a subsequent get_graph()
    call, even when backed by the real SQLite repository.
    """
    with MemoryRepository() as repo:
        fact = repo.record_fact(_assertion())
        abg = ActiveBeliefGraph(repo)

        first = abg.get_graph()
        # Add a phantom edge to the returned copy
        first.add_edge("PHANTOM_SRC", "PHANTOM_DST", key="phantom")
        assert first.has_edge("PHANTOM_SRC", "PHANTOM_DST", key="phantom")

        second = abg.get_graph()
        assert not second.has_edge("PHANTOM_SRC", "PHANTOM_DST", key="phantom")
        assert second.number_of_edges() == 1
        assert second.has_edge("route_A", "clear", key=str(fact.fact_id))


def test_write_through_synced_repository_failed_fact_write_does_not_invalidate() -> None:
    """A failed record_fact through GraphSyncedRepository must NOT invalidate the
    graph cache — the error must raise before invalidate() is reached.
    """
    with MemoryRepository() as repo:
        abg = ActiveBeliefGraph(repo)
        synced = GraphSyncedRepository(repo, abg)

        # Prime the cache with an existing fact
        synced.record_fact(_assertion(subject="route_A", obj="clear"))
        initial_graph = abg.get_graph()
        assert initial_graph.number_of_edges() == 1

        # Attempt to insert an invalid assertion directly into SQLite via a monkeypatched cursor
        # or simulated constraint error. We simulate a DB-level failure by temporarily
        # closing the connection or using an invalid confidence score (handled by SQLite CHECK).
        # We can bypass Pydantic by modifying the internal dict or using connection execution.
        class _FailOnInsert:
            def __init__(self, real_conn):
                self._real = real_conn

            def execute(self, sql, params=()):
                if "INSERT INTO facts" in sql:
                    import sqlite3

                    raise sqlite3.OperationalError("simulated fact insert failure")
                return self._real.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._real, name)

        real_conn = repo._conn
        repo._conn = _FailOnInsert(real_conn)  # type: ignore[assignment]

        import sqlite3

        with pytest.raises(sqlite3.OperationalError, match="simulated fact insert failure"):
            synced.record_fact(_assertion(subject="route_B", obj="blocked"))

        repo._conn = real_conn  # type: ignore[assignment]

        # The cache was NOT invalidated; reading it returns the cached 1-edge graph
        assert not abg._stale
        assert abg.get_graph().number_of_edges() == 1


def test_synced_repository_record_revision_returns_revision_outcome() -> None:
    """record_revision through GraphSyncedRepository must return a RevisionOutcome instance."""
    with MemoryRepository() as repo:
        old = repo.record_fact(_assertion(obj="clear"))
        synced = GraphSyncedRepository.create(repo)

        outcome = synced.record_revision(
            old_fact_id=old.fact_id,
            replacement=_assertion(obj="blocked", source_agent="lidar"),
            reason="lidar update",
            policy_rule="lidar_wins",
            revised_at=_REVISED,
        )

        assert isinstance(outcome, RevisionOutcome)
        assert outcome.successor.object == "blocked"
        assert outcome.audit_event.reason == "lidar update"
        assert synced.get_graph().has_edge("route_A", "blocked", key=str(outcome.successor.fact_id))


def test_direct_memory_repository_write_invalidates_synced_graph() -> None:
    """When a GraphSyncedRepository is active, even direct writes through MemoryRepository
    cannot leave a stale graph, because the write listener invalidates the cache on commit.
    """
    with MemoryRepository() as repo:
        abg = ActiveBeliefGraph(repo)
        _synced = GraphSyncedRepository(repo, abg)

        # Prime cache with 0 edges
        assert abg.get_graph().number_of_edges() == 0
        assert not abg._stale

        # Direct write via repo (bypassing _synced.record_fact)
        fact1 = repo.record_fact(_assertion(subject="route_A", obj="clear"))

        # The listener must have invalidated the cache
        assert abg._stale
        assert abg.get_graph().number_of_edges() == 1
        assert abg.get_graph().has_edge("route_A", "clear", key=str(fact1.fact_id))

        # Direct revision via repo (bypassing _synced.record_revision)
        outcome = repo.record_revision(
            old_fact_id=fact1.fact_id,
            replacement=_assertion(obj="blocked", source_agent="lidar"),
            reason="obstacle detected",
            policy_rule="lidar_wins",
            revised_at=_REVISED,
        )

        assert abg._stale
        graph = abg.get_graph()
        assert graph.has_edge("route_A", "blocked", key=str(outcome.successor.fact_id))
        assert not graph.has_edge("route_A", "clear", key=str(fact1.fact_id))


def test_synced_repository_facade_delegates_all_methods() -> None:
    """GraphSyncedRepository acts as the unified application facade, exposing query_facts,
    get_audit_chain, add_alias, resolve_entity, get_graph, and context manager support.
    """
    with MemoryRepository() as repo:
        synced = GraphSyncedRepository.create(repo)

        # Context manager
        with synced as s:
            f = s.record_fact(_assertion(subject="box_01", predicate="located_in", obj="room_A"))

            # query_facts
            facts = s.query_facts(FactQuery(subject="box_01"))
            assert len(facts) == 1
            assert facts[0].fact_id == f.fact_id

            # get_audit_chain
            trail = s.get_audit_chain(f.fact_id)
            assert trail.root_fact_id == f.fact_id

            # add_alias and resolve_entity
            s.add_alias("blue box", "box_01")
            resolution = s.resolve_entity("blue box")
            assert resolution.status == "resolved"
            assert resolution.canonical_entity_id == "box_01"

            # get_graph shortcut
            g = s.get_graph()
            assert g.has_edge("box_01", "room_A", key=str(f.fact_id))


def test_documented_consistency_check_identifies_superseded_cached_edge() -> None:
    """Verify the documented consistency-check call against real SQLite.

    1. Prime the graph with an active fact.
    2. Supersede the fact directly in SQLite without invalidating the graph.
    3. Query active_facts and superseded_facts from SQLite.
    4. Call check_graph_consistency(graph, active_facts, superseded_facts).
    5. Assert that the superseded edge is detected as stale_edge_keys.
    6. Rebuild the graph and assert report.is_clean is True.
    """
    with MemoryRepository() as repo:
        abg = ActiveBeliefGraph(repo)

        # 1. Store initial fact and prime cache
        old = repo.record_fact(_assertion(subject="route_A", obj="clear"))
        cached_graph = abg.get_graph()
        assert cached_graph.has_edge("route_A", "clear", key=str(old.fact_id))

        # 2. Record revision directly in SQLite WITHOUT invalidating abg
        # (temporarily detach listeners to simulate an unrefreshed/stale cache)
        repo._write_listeners.clear()
        outcome = repo.record_revision(
            old_fact_id=old.fact_id,
            replacement=_assertion(obj="blocked", source_agent="lidar"),
            reason="obstacle detected",
            policy_rule="lidar_wins",
            revised_at=_REVISED,
        )

        # Graph cache is still stale and un-invalidated
        stale_graph = abg._cache
        assert stale_graph is not None
        assert stale_graph.has_edge("route_A", "clear", key=str(old.fact_id))

        # 3. Query authoritative active and superseded facts from SQLite as documented
        active_facts = repo.query_facts(FactQuery(active_only=True))
        all_facts = repo.query_facts(FactQuery(active_only=False))
        superseded_facts = [f for f in all_facts if f.superseded_by is not None]

        # 4. Run consistency check
        report = check_graph_consistency(
            graph=stale_graph,
            active_facts=active_facts,
            superseded_facts=superseded_facts,
        )

        # 5. Verify detection
        assert not report.is_clean
        assert str(old.fact_id) in report.stale_edge_keys
        assert str(old.fact_id) not in report.extra_edge_keys
        assert outcome.successor.fact_id in report.missing_fact_ids

        # 6. Rebuild restores clean consistency
        rebuilt = abg.rebuild()
        fresh_report = check_graph_consistency(
            graph=rebuilt,
            active_facts=active_facts,
            superseded_facts=superseded_facts,
        )
        assert fresh_report.is_clean
