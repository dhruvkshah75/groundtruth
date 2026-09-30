"""Unit tests for check_graph_consistency and GraphConsistencyReport.

Uses pure fake data (constructed graph + constructed fact list) — no SQLite.
The consistency function is a pure function so no real persistence is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import networkx as nx
import pytest

from src.contracts import FactAssertion, FactQuery, SpatialContext, StoredFact
from src.declarative.active_graph import ActiveBeliefGraph
from src.declarative.consistency import GraphConsistencyReport, check_graph_consistency
from src.declarative.memory_repository import MemoryRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OBS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_CREATED = datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC)


def _make_fact(
    *,
    subject: str = "route_A",
    predicate: str = "status_is",
    obj: str = "clear",
    source_agent: str = "static_map",
    confidence: float = 0.9,
    context: SpatialContext | None = None,
    fact_id: UUID | None = None,
    superseded_by: UUID | None = None,
) -> StoredFact:
    return StoredFact(
        fact_id=fact_id or uuid4(),
        version="v1",
        subject=subject,
        predicate=predicate,
        object=obj,
        source_agent=source_agent,
        confidence_score=confidence,
        observed_at=_OBS,
        created_at=_CREATED,
        context=context or SpatialContext(),
        evidence={},
        superseded_by=superseded_by,
    )


class _FakeReader:
    def __init__(self, facts: list[StoredFact]) -> None:
        self._facts = facts

    def query_facts(self, query: FactQuery) -> list[StoredFact]:
        if query.active_only:
            return [f for f in self._facts if f.superseded_by is None]
        return list(self._facts)


def _graph_from_facts(facts: list[StoredFact]) -> nx.MultiDiGraph:
    """Build a graph via ActiveBeliefGraph (the canonical way)."""
    return ActiveBeliefGraph(_FakeReader(facts)).get_graph()


# ---------------------------------------------------------------------------
# Helper: inject an edge into a copied graph for testing anomalies
# ---------------------------------------------------------------------------


def _with_extra_edge(
    base_graph: nx.MultiDiGraph,
    subject: str,
    obj: str,
    edge_key: str,
    **attrs: object,
) -> nx.MultiDiGraph:
    """Return a copy of *base_graph* with an additional edge injected."""
    g = base_graph.copy()
    g.add_edge(subject, obj, key=edge_key, **attrs)
    return g


# ---------------------------------------------------------------------------
# Consistency tests
# ---------------------------------------------------------------------------


def test_clean_state_produces_clean_report() -> None:
    """Correct graph + matching active facts → is_clean == True, all lists empty."""
    fact = _make_fact()
    graph = _graph_from_facts([fact])
    report = check_graph_consistency(graph, [fact])

    assert isinstance(report, GraphConsistencyReport)
    assert report.is_clean
    assert report.missing_fact_ids == []
    assert report.extra_edge_keys == []
    assert report.stale_edge_keys == []
    assert report.duplicate_edge_keys == []
    assert report.metadata_mismatches == []


def test_missing_edge_detected() -> None:
    """An active fact that has no corresponding graph edge is reported as missing."""
    fact_a = _make_fact(subject="route_A", obj="clear")
    fact_b = _make_fact(subject="route_B", obj="clear")

    # Build a graph that only contains fact_a
    graph = _graph_from_facts([fact_a])
    # But both facts are active in SQLite
    report = check_graph_consistency(graph, [fact_a, fact_b])

    assert not report.is_clean
    assert fact_b.fact_id in report.missing_fact_ids
    assert fact_a.fact_id not in report.missing_fact_ids


def test_extra_edge_detected() -> None:
    """A graph edge whose fact-ID is not in the active-fact list is reported as extra."""
    fact = _make_fact()
    graph = _graph_from_facts([fact])

    # Now pass an empty active-facts list (fact was removed somehow — phantom edge)
    report = check_graph_consistency(graph, [])

    assert not report.is_clean
    assert str(fact.fact_id) in report.extra_edge_keys


def test_superseded_fact_edge_detected() -> None:
    """Regression test: models real cache drift, not a hand-built stale edge.

    Builds the graph while a fact is active, then commits a revision in
    SQLite (superseding that fact) WITHOUT rebuilding the graph — exactly
    the scenario where a stale cache goes undetected without authoritative
    superseded-fact information.
    """
    with MemoryRepository() as repo:
        assertion = FactAssertion(
            subject="route_A",
            predicate="status_is",
            object="clear",
            source_agent="static_map",
            confidence_score=0.9,
            observed_at=_OBS,
            context=SpatialContext(),
            evidence={},
        )
        old = repo.record_fact(assertion)

        # Build the graph WHILE the fact is still active.
        abg = ActiveBeliefGraph(repo)
        stale_graph = abg.get_graph()
        assert stale_graph.has_edge("route_A", "clear", key=str(old.fact_id))

        # Now commit a revision in SQLite. The graph is NOT rebuilt/invalidated —
        # this models a caller who forgot to invalidate, or hasn't yet.
        repo.record_revision(
            old_fact_id=old.fact_id,
            replacement=FactAssertion(
                subject="route_A",
                predicate="status_is",
                object="blocked",
                source_agent="lidar",
                confidence_score=0.95,
                observed_at=_OBS,
                context=SpatialContext(),
                evidence={},
            ),
            reason="lidar update",
            policy_rule="lidar_wins",
            revised_at=_OBS,
        )

        # Query authoritative current state directly from SQLite.
        all_facts = repo.query_facts(FactQuery(active_only=False))
        active_facts = [f for f in all_facts if f.superseded_by is None]
        superseded_facts = [f for f in all_facts if f.superseded_by is not None]

        # Check the STALE, unrefreshed graph against authoritative SQLite state.
        report = check_graph_consistency(stale_graph, active_facts, superseded_facts)

        assert not report.is_clean
        assert str(old.fact_id) in report.stale_edge_keys
        assert str(old.fact_id) not in report.extra_edge_keys


def test_duplicate_fact_id_usage_detected() -> None:
    """The same fact-ID used as a key on two separate edges is reported as duplicate."""
    fact = _make_fact()
    key = str(fact.fact_id)

    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    # Add the same key on two different edges
    attrs = {
        "fact_id": fact.fact_id,
        "predicate": fact.predicate,
        "source_agent": fact.source_agent,
        "confidence_score": fact.confidence_score,
        "observed_at": fact.observed_at,
        "created_at": fact.created_at,
        "context": fact.context,
        "version": fact.version,
        "evidence": fact.evidence,
    }
    # NetworkX MultiDiGraph uses the key to distinguish parallel edges.
    # If we try to add the same key between different node pairs, it creates
    # separate edges. We simulate duplicate detection by adding two edges
    # with the same key between the SAME pair (NetworkX replaces data) or
    # by manipulating the internal structure.
    #
    # Since nx.MultiDiGraph allows the same key only once per (u,v) pair,
    # we add the duplicate between a different (u,v) to force two edges
    # sharing the same logical key.
    graph.add_edge(fact.subject, fact.object, key=key, **attrs)
    graph.add_edge("other_subject", "other_object", key=key, **attrs)

    report = check_graph_consistency(graph, [fact])

    assert not report.is_clean
    assert key in report.duplicate_edge_keys


@pytest.mark.parametrize(
    "field_name, bad_value",
    [
        ("predicate", "wrong_predicate"),
        ("source_agent", "wrong_agent"),
        ("confidence_score", 0.1),
        ("context", SpatialContext(location="wrong_location")),
    ],
)
def test_incorrect_metadata_detected(field_name: str, bad_value: object) -> None:
    """Parametrized: each required metadata field mismatch is individually detected."""
    fact = _make_fact()
    graph = _graph_from_facts([fact])

    # Tamper with the edge attr in the graph copy
    edge_key = str(fact.fact_id)
    tampered = graph.copy()
    tampered["route_A"]["clear"][edge_key][field_name] = bad_value

    report = check_graph_consistency(tampered, [fact])

    assert not report.is_clean
    mismatch_fields = [m[1] for m in report.metadata_mismatches]
    assert field_name in mismatch_fields

def test_edge_with_wrong_endpoints_detected() -> None:
    """An edge with the correct key but wrong (subject, object) nodes is a
    representation error and must be reported, not silently accepted."""
    fact = _make_fact(subject="route_A", obj="clear")
    key = str(fact.fact_id)

    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    attrs = {
        "fact_id": fact.fact_id,
        "predicate": fact.predicate,
        "source_agent": fact.source_agent,
        "confidence_score": fact.confidence_score,
        "observed_at": fact.observed_at,
        "created_at": fact.created_at,
        "context": fact.context,
        "version": fact.version,
        "superseded_by": fact.superseded_by,
        "evidence": fact.evidence,
    }
    # Wrong endpoints: edge is between the wrong nodes despite the right key.
    graph.add_edge("wrong_subject", "wrong_object", key=key, **attrs)

    report = check_graph_consistency(graph, [fact])

    assert not report.is_clean
    mismatch_fields = [m[1] for m in report.metadata_mismatches]
    assert "subject" in mismatch_fields
    assert "object" in mismatch_fields


def test_edge_with_missing_fact_id_attribute_detected() -> None:
    """An edge whose fact_id attribute is missing (None) must not silently
    skip validation — it must be reported as a mismatch."""
    fact = _make_fact()
    key = str(fact.fact_id)

    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    attrs = {
        "fact_id": None,  # missing
        "predicate": fact.predicate,
        "source_agent": fact.source_agent,
        "confidence_score": fact.confidence_score,
        "observed_at": fact.observed_at,
        "created_at": fact.created_at,
        "context": fact.context,
        "version": fact.version,
        "superseded_by": fact.superseded_by,
        "evidence": fact.evidence,
    }
    graph.add_edge(fact.subject, fact.object, key=key, **attrs)

    report = check_graph_consistency(graph, [fact])

    assert not report.is_clean
    mismatch_fields = [m[1] for m in report.metadata_mismatches]
    assert "fact_id" in mismatch_fields


def test_edge_with_wrong_fact_id_attribute_detected() -> None:
    """An edge whose fact_id attribute points to a different UUID than its
    own key must be reported."""
    fact = _make_fact()
    key = str(fact.fact_id)
    wrong_id = uuid4()

    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    attrs = {
        "fact_id": wrong_id,  # wrong
        "predicate": fact.predicate,
        "source_agent": fact.source_agent,
        "confidence_score": fact.confidence_score,
        "observed_at": fact.observed_at,
        "created_at": fact.created_at,
        "context": fact.context,
        "version": fact.version,
        "superseded_by": fact.superseded_by,
        "evidence": fact.evidence,
    }
    graph.add_edge(fact.subject, fact.object, key=key, **attrs)

    report = check_graph_consistency(graph, [fact])

    assert not report.is_clean
    mismatch_fields = [m[1] for m in report.metadata_mismatches]
    assert "fact_id" in mismatch_fields


@pytest.mark.parametrize(
    "field_name",
    ["observed_at", "created_at", "version", "superseded_by", "evidence"],
)
def test_previously_unchecked_metadata_fields_now_detected(field_name: str) -> None:
    """The five fields added in this fix (observed_at, created_at, version,
    superseded_by, evidence) must each be individually detectable."""
    fact = _make_fact()
    graph = _graph_from_facts([fact])

    edge_key = str(fact.fact_id)
    tampered = graph.copy()

    bad_values = {
        "observed_at": datetime(2020, 1, 1, tzinfo=UTC),
        "created_at": datetime(2020, 1, 1, tzinfo=UTC),
        "version": "v999",
        "superseded_by": uuid4(),
        "evidence": {"tampered": True},
    }
    tampered["route_A"]["clear"][edge_key][field_name] = bad_values[field_name]

    report = check_graph_consistency(tampered, [fact])

    assert not report.is_clean
    mismatch_fields = [m[1] for m in report.metadata_mismatches]
    assert field_name in mismatch_fields

def test_every_graph_edge_maps_to_one_active_fact() -> None:
    """In a correct state, each graph edge has exactly one matching active fact."""
    facts = [
        _make_fact(subject="route_A", obj="clear"),
        _make_fact(subject="box_01", predicate="colour_is", obj="red"),
    ]
    graph = _graph_from_facts(facts)
    report = check_graph_consistency(graph, facts)

    assert report.is_clean
    # Every edge key should correspond to exactly one active fact
    graph_keys = {k for _u, _v, k in graph.edges(keys=True)}
    active_keys = {str(f.fact_id) for f in facts}
    assert graph_keys == active_keys


def test_every_active_fact_maps_to_one_graph_edge() -> None:
    """In a correct state, each active fact has exactly one graph edge."""
    facts = [
        _make_fact(subject="route_A", obj="clear"),
        _make_fact(subject="route_B", obj="blocked"),
    ]
    graph = _graph_from_facts(facts)
    report = check_graph_consistency(graph, facts)

    assert report.is_clean
    active_keys = {str(f.fact_id) for f in facts}
    graph_keys = {k for _u, _v, k in graph.edges(keys=True)}
    assert active_keys == graph_keys
