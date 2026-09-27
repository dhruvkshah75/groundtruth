"""Unit tests for check_graph_consistency and GraphConsistencyReport.

Uses pure fake data (constructed graph + constructed fact list) — no SQLite.
The consistency function is a pure function so no real persistence is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import networkx as nx
import pytest

from src.contracts import FactQuery, SpatialContext, StoredFact
from src.declarative.active_graph import ActiveBeliefGraph
from src.declarative.consistency import GraphConsistencyReport, check_graph_consistency

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
    """A graph edge representing a superseded fact is reported as stale.

    We inject an edge that carries a non-None superseded_by attribute to
    simulate a cache that was not invalidated after a revision commit.
    """
    new_id = uuid4()
    old_id = uuid4()

    # The stale edge carries superseded_by set on its attrs
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    graph.add_edge(
        "route_A",
        "clear",
        key=str(old_id),
        fact_id=old_id,
        predicate="status_is",
        source_agent="static_map",
        confidence_score=0.9,
        observed_at=_OBS,
        created_at=_CREATED,
        context=SpatialContext(),
        version="v1",
        evidence={},
        superseded_by=new_id,  # marks this edge as stale
    )

    # active_facts only contains the new fact
    new_fact = _make_fact(obj="blocked", fact_id=new_id)
    graph.add_edge(
        "route_A",
        "blocked",
        key=str(new_id),
        fact_id=new_id,
        predicate="status_is",
        source_agent="lidar",
        confidence_score=0.95,
        observed_at=_OBS,
        created_at=_CREATED,
        context=SpatialContext(),
        version="v1",
        evidence={},
        superseded_by=None,
    )

    report = check_graph_consistency(graph, [new_fact])

    assert not report.is_clean
    assert str(old_id) in report.stale_edge_keys
    assert str(new_id) not in report.stale_edge_keys
    assert str(new_id) not in report.extra_edge_keys


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
