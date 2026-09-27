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
from src.declarative.memory_repository import MemoryRepository

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
        assert not graph.has_edge(
            "route_A", "blocked", key=str(outcome.successor.fact_id)
        )


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
        repo.record_fact(
            _assertion(subject="box_01", predicate="colour_is", obj="red")
        )

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
