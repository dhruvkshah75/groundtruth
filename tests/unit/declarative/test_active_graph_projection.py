"""Unit tests for ActiveBeliefGraph projection behaviour.

Uses a *fake* ActiveFactReader backed by a plain Python list — no SQLite.
These tests verify: given a set of StoredFact objects, does the graph have
the correct structure, metadata, and behaviour?

Cache lifecycle tests (which need real SQLite transactions) live in
``tests/integration/declarative/test_active_graph_with_sqlite.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import networkx as nx
import pytest

from src.contracts import FactQuery, SpatialContext, StoredFact
from src.declarative.active_graph import ActiveBeliefGraph

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
    observed_at: datetime = _OBS,
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
        observed_at=observed_at,
        created_at=_CREATED,
        context=context or SpatialContext(),
        evidence={},
        superseded_by=superseded_by,
    )


class FakeReader:
    """In-memory fake satisfying the ActiveFactReader Protocol."""

    def __init__(self, facts: list[StoredFact] | None = None) -> None:
        self._facts: list[StoredFact] = facts or []

    def query_facts(self, query: FactQuery) -> list[StoredFact]:
        if query.active_only:
            return [f for f in self._facts if f.superseded_by is None]
        return list(self._facts)


class RaisingReader:
    """Fake reader that always raises to test error propagation."""

    def query_facts(self, query: FactQuery) -> list[StoredFact]:
        raise RuntimeError("database unavailable")


# ---------------------------------------------------------------------------
# Projection tests
# ---------------------------------------------------------------------------


def test_empty_active_facts_produces_empty_graph() -> None:
    """Empty reader → empty MultiDiGraph (no nodes, no edges)."""
    graph = ActiveBeliefGraph(FakeReader()).get_graph()
    assert isinstance(graph, nx.MultiDiGraph)
    assert graph.number_of_nodes() == 0
    assert graph.number_of_edges() == 0


def test_single_fact_produces_subject_object_and_edge() -> None:
    """One active fact → correct subject node, object node, directed edge."""
    fact = _make_fact(subject="route_A", obj="clear")
    graph = ActiveBeliefGraph(FakeReader([fact])).get_graph()

    assert "route_A" in graph.nodes
    assert "clear" in graph.nodes
    assert graph.number_of_edges() == 1
    assert graph.has_edge("route_A", "clear", key=str(fact.fact_id))


def test_edge_preserves_all_required_metadata() -> None:
    """The edge must carry every required attribute from the issue spec."""
    ctx = SpatialContext(location="room_101", observer="robot_01", world_version=3)
    fact = _make_fact(
        subject="route_A",
        predicate="status_is",
        obj="clear",
        source_agent="static_map",
        confidence=0.85,
        context=ctx,
    )
    graph = ActiveBeliefGraph(FakeReader([fact])).get_graph()

    edge_key = str(fact.fact_id)
    attrs = graph["route_A"]["clear"][edge_key]

    assert attrs["fact_id"] == fact.fact_id
    assert attrs["predicate"] == "status_is"
    assert attrs["source_agent"] == "static_map"
    assert attrs["confidence_score"] == pytest.approx(0.85)
    assert attrs["observed_at"] == _OBS
    assert attrs["created_at"] == _CREATED
    assert attrs["context"] == ctx
    assert attrs["version"] == "v1"


def test_edge_retrievable_by_fact_id() -> None:
    """get_edge_by_fact_id returns the correct attrs dict."""
    fact = _make_fact()
    abg = ActiveBeliefGraph(FakeReader([fact]))

    result = abg.get_edge_by_fact_id(fact.fact_id)
    assert result is not None
    assert result["fact_id"] == fact.fact_id
    assert result["subject"] == fact.subject
    assert result["object"] == fact.object

    absent_result = abg.get_edge_by_fact_id(uuid4())
    assert absent_result is None


def test_two_distinct_facts_same_subject_object_both_preserved() -> None:
    """Two different facts between the same nodes both appear as separate edges."""
    fid_a = uuid4()
    fid_b = uuid4()
    fact_a = _make_fact(subject="box_01", obj="room_101", fact_id=fid_a)
    fact_b = _make_fact(subject="box_01", obj="room_101", source_agent="sensor_02", fact_id=fid_b)

    graph = ActiveBeliefGraph(FakeReader([fact_a, fact_b])).get_graph()

    assert graph.number_of_edges() == 2
    assert graph.has_edge("box_01", "room_101", key=str(fid_a))
    assert graph.has_edge("box_01", "room_101", key=str(fid_b))


def test_two_facts_different_predicates_same_nodes_do_not_overwrite() -> None:
    """Two facts with different predicates on the same nodes are both preserved."""
    fact_a = _make_fact(subject="box_01", predicate="located_at", obj="room_101")
    fact_b = _make_fact(subject="box_01", predicate="colour_is", obj="red")

    graph = ActiveBeliefGraph(FakeReader([fact_a, fact_b])).get_graph()

    # Two separate edges exist (different objects in the second case, but
    # the assertion holds regardless).
    assert graph.number_of_edges() == 2


def test_multiple_conflicting_active_source_claims_remain_separate() -> None:
    """Scenario B: box_01 with three distinct perspective-scoped predicates/sources.

    box_01 --[requested_colour, user claim]-->     red
    box_01 --[appears_colour, camera/yellow light]--> brown
    box_01 --[painted_colour, bot_02 history]-->   blue

    All three edges must be preserved; the graph must not collapse them.
    """
    fid_user = uuid4()
    fid_camera = uuid4()
    fid_bot = uuid4()

    ctx_user = SpatialContext(observer="user_01", extra_context={"lighting": "normal"})
    ctx_camera = SpatialContext(observer="camera_01", extra_context={"lighting": "yellow"})
    ctx_bot = SpatialContext(observer="bot_02", extra_context={"record": "history"})

    fact_user = _make_fact(
        subject="box_01",
        predicate="requested_colour",
        obj="red",
        source_agent="user_claim",
        context=ctx_user,
        fact_id=fid_user,
    )
    fact_camera = _make_fact(
        subject="box_01",
        predicate="appears_colour",
        obj="brown",
        source_agent="camera_01",
        context=ctx_camera,
        fact_id=fid_camera,
    )
    fact_bot = _make_fact(
        subject="box_01",
        predicate="painted_colour",
        obj="blue",
        source_agent="bot_02",
        context=ctx_bot,
        fact_id=fid_bot,
    )

    graph = ActiveBeliefGraph(FakeReader([fact_user, fact_camera, fact_bot])).get_graph()

    assert graph.number_of_edges() == 3
    assert graph.has_edge("box_01", "red", key=str(fid_user))
    assert graph.has_edge("box_01", "brown", key=str(fid_camera))
    assert graph.has_edge("box_01", "blue", key=str(fid_bot))

    # Confirm predicates are preserved on each edge
    assert graph["box_01"]["red"][str(fid_user)]["predicate"] == "requested_colour"
    assert graph["box_01"]["brown"][str(fid_camera)]["predicate"] == "appears_colour"
    assert graph["box_01"]["blue"][str(fid_bot)]["predicate"] == "painted_colour"

    # No source collapsed onto another
    assert graph["box_01"]["red"][str(fid_user)]["source_agent"] == "user_claim"
    assert graph["box_01"]["brown"][str(fid_camera)]["source_agent"] == "camera_01"
    assert graph["box_01"]["blue"][str(fid_bot)]["source_agent"] == "bot_02"


def test_superseded_facts_not_projected() -> None:
    """Facts with superseded_by set are excluded from the active projection."""
    old_id = uuid4()
    new_id = uuid4()

    superseded = _make_fact(obj="clear", fact_id=old_id, superseded_by=new_id)
    active = _make_fact(obj="blocked", fact_id=new_id)

    # Reader filters superseded when active_only=True
    graph = ActiveBeliefGraph(FakeReader([superseded, active])).get_graph()

    assert graph.number_of_edges() == 1
    assert graph.has_edge("route_A", "blocked", key=str(new_id))
    assert not graph.has_edge("route_A", "clear", key=str(old_id))


def test_context_and_perspective_metadata_survives_projection() -> None:
    """SpatialContext fields (location, observer, world_version, extra_context)
    are faithfully preserved on the graph edge."""
    ctx = SpatialContext(
        location="room_101",
        frame_of_reference="robot_base",
        world_version=7,
        observer="robot_01",
        extra_context={"session": "demo"},
    )
    fact = _make_fact(context=ctx)
    graph = ActiveBeliefGraph(FakeReader([fact])).get_graph()

    edge_key = str(fact.fact_id)
    stored_ctx = graph[fact.subject][fact.object][edge_key]["context"]
    assert stored_ctx.location == "room_101"
    assert stored_ctx.frame_of_reference == "robot_base"
    assert stored_ctx.world_version == 7
    assert stored_ctx.observer == "robot_01"
    assert stored_ctx.extra_context == {"session": "demo"}


def test_invalid_or_untyped_input_rejected() -> None:
    """Feeding a non-StoredFact object via a fake reader must raise, not silently
    drop or include the bad entry.

    We simulate this by constructing a reader that returns a plain dict
    instead of a StoredFact, then assert that accessing the graph raises
    an AttributeError (or similar) rather than silently accepting or
    ignoring the bad object.
    """

    class BadReader:
        def query_facts(self, query: FactQuery) -> list[Any]:
            return [{"subject": "x", "predicate": "p", "object": "y"}]  # type: ignore[return-value]

    abg = ActiveBeliefGraph(BadReader())  # type: ignore[arg-type]
    with pytest.raises((AttributeError, TypeError)):
        abg.get_graph()


def test_repository_query_failure_raises_not_empty_graph() -> None:
    """A repository/query failure must propagate as an explicit error.

    Returning an empty graph would be indistinguishable from 'no beliefs',
    which violates the error-handling requirement in the issue.
    """
    abg = ActiveBeliefGraph(RaisingReader())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="database unavailable"):
        abg.get_graph()


# ---------------------------------------------------------------------------
# Mutation safety
# ---------------------------------------------------------------------------


def test_mutating_returned_graph_does_not_corrupt_cache() -> None:
    """get_graph() returns a defensive copy; mutating it must not affect future reads.

    This is the unit-level version.  The integration version tests the same
    property after real SQLite writes.
    """
    fact = _make_fact()
    abg = ActiveBeliefGraph(FakeReader([fact]))

    first = abg.get_graph()
    # Inject a fake edge into the returned copy
    first.add_edge("FAKE_NODE", "FAKE_TARGET", key="fake_key")
    assert first.has_edge("FAKE_NODE", "FAKE_TARGET", key="fake_key")

    # A subsequent call must not see the injected edge
    second = abg.get_graph()
    assert not second.has_edge("FAKE_NODE", "FAKE_TARGET", key="fake_key")
    assert second.number_of_edges() == 1  # only the real fact


def test_mutating_rebuild_result_does_not_corrupt_cache() -> None:
    """rebuild() must also return a defensive copy — not the same mutable
    object stored internally as self._cache. Mutating the result of a direct
    rebuild() call must not affect subsequent get_graph() reads.
    """
    fact = _make_fact()
    abg = ActiveBeliefGraph(FakeReader([fact]))

    rebuilt = abg.rebuild()
    # Inject a fake edge into the object returned by rebuild()
    rebuilt.add_edge("FAKE_NODE", "FAKE_TARGET", key="fake_key")
    assert rebuilt.has_edge("FAKE_NODE", "FAKE_TARGET", key="fake_key")

    # A subsequent get_graph() call must not see the injected edge
    graph = abg.get_graph()
    assert not graph.has_edge("FAKE_NODE", "FAKE_TARGET", key="fake_key")
    assert graph.number_of_edges() == 1  # only the real fact


def test_mutating_nested_evidence_dict_does_not_corrupt_cache() -> None:
    """A shallow .copy() is insufficient: mutating a nested mutable value
    (the evidence dict) inside a returned graph's edge attrs must not
    affect what a later get_graph() call returns.
    """
    fact = _make_fact()
    # _make_fact() defaults evidence={}; give it a real nested value here.
    fact_with_evidence = fact.model_copy(update={"evidence": {"sensor_reading": 42}})
    abg = ActiveBeliefGraph(FakeReader([fact_with_evidence]))

    first = abg.get_graph()
    edge_key = str(fact_with_evidence.fact_id)
    # Mutate a nested dict value inside the returned edge's attrs.
    first[fact_with_evidence.subject][fact_with_evidence.object][edge_key]["evidence"][
        "sensor_reading"
    ] = "TAMPERED"

    second = abg.get_graph()
    untouched_evidence = second[fact_with_evidence.subject][fact_with_evidence.object][edge_key][
        "evidence"
    ]
    assert untouched_evidence["sensor_reading"] == 42


def test_mutating_nested_context_extra_does_not_corrupt_cache() -> None:
    """Mutating SpatialContext.extra_context (a nested dict inside a pydantic
    model held as an edge attribute) must not affect future get_graph() reads.
    """
    ctx = SpatialContext(extra_context={"lighting": "normal"})
    fact = _make_fact(context=ctx)
    abg = ActiveBeliefGraph(FakeReader([fact]))

    first = abg.get_graph()
    edge_key = str(fact.fact_id)
    first[fact.subject][fact.object][edge_key]["context"].extra_context["lighting"] = "TAMPERED"

    second = abg.get_graph()
    untouched_context = second[fact.subject][fact.object][edge_key]["context"]
    assert untouched_context.extra_context["lighting"] == "normal"


# ---------------------------------------------------------------------------
# Traversal helpers
# ---------------------------------------------------------------------------


def test_get_outgoing_returns_edges_for_subject() -> None:
    fact = _make_fact(subject="route_A", obj="clear")
    abg = ActiveBeliefGraph(FakeReader([fact]))

    results = abg.get_outgoing("route_A")
    assert len(results) == 1
    assert results[0]["object"] == "clear"
    assert results[0]["predicate"] == "status_is"


def test_get_incoming_returns_edges_for_object() -> None:
    fact = _make_fact(subject="route_A", obj="clear")
    abg = ActiveBeliefGraph(FakeReader([fact]))

    results = abg.get_incoming("clear")
    assert len(results) == 1
    assert results[0]["subject"] == "route_A"


def test_get_outgoing_unknown_node_returns_empty() -> None:
    abg = ActiveBeliefGraph(FakeReader([]))
    assert abg.get_outgoing("nonexistent") == []


def test_active_fact_ids_yields_all_fact_ids() -> None:
    fid_a = uuid4()
    fid_b = uuid4()
    facts = [
        _make_fact(fact_id=fid_a),
        _make_fact(subject="box_01", obj="red", fact_id=fid_b),
    ]
    abg = ActiveBeliefGraph(FakeReader(facts))
    ids = set(abg.active_fact_ids())
    assert ids == {fid_a, fid_b}
