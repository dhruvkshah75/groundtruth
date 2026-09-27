"""
Active-belief graph projection for GroundTruth Tier 1.

SQLite (via MemoryRepository) is the **only authoritative and durable source
of truth**.  This module builds a disposable, in-memory
``networkx.MultiDiGraph`` that mirrors the currently active beliefs so that
Tier 2 and the future evaluator dashboard can traverse relationships without
issuing SQL queries for every hop.

Why ``MultiDiGraph``
--------------------
Several active assertions may legitimately connect the same pair of nodes:

* multiple predicates may relate the same entities;
* multiple sources may hold unsuperseded claims about the same relationship;
* conflicting assertions coexist until Tier 2 records a justified revision;
* perspective- or context-specific claims must not overwrite each other.

One independently addressable edge per stored fact is required.  The edge key
is ``str(fact_id)`` which is deterministic, unique, and directly traceable to
the SQLite row.

Cache lifecycle
---------------
::

    application starts
        → graph cache is absent/stale

    first graph request
        → query active facts from SQLite via ActiveFactReader
        → build complete MultiDiGraph
        → cache the result

    subsequent graph request with no successful writes
        → reuse cached projection

    successful record_fact or record_revision transaction
        → caller marks graph cache stale via invalidate()

    next graph request
        → rebuild from the new active SQLite state

Mutation safety
---------------
``get_graph()`` returns a **defensive copy** of the internal cached graph.
A Tier 2 caller or dashboard may add/remove edges on that copy without
ever corrupting the projection that subsequent reads will return.

What this module will not do
-----------------------------
* Resolve conflicts or rank sources.
* Calculate confidence decay.
* Write facts or audit events back to SQLite.
* Persist the graph to disk.
* Import Tier 2 or Tier 3 modules.
* Query SQLite directly (only goes through ``ActiveFactReader``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol
from uuid import UUID

import networkx as nx

from src.contracts import FactQuery, StoredFact


class ActiveFactReader(Protocol):
    """Structural interface that the graph projector requires from Tier 1.

    Any object that implements ``query_facts`` with this signature satisfies
    the protocol.  In production this will be a ``MemoryRepository`` instance;
    in unit tests it will be a lightweight fake.
    """

    def query_facts(self, query: FactQuery) -> list[StoredFact]:
        """Return all ``StoredFact`` objects matching *query*."""
        ...


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_REQUIRED_EDGE_ATTRS: tuple[str, ...] = (
    "fact_id",
    "predicate",
    "source_agent",
    "confidence_score",
    "observed_at",
    "created_at",
    "context",
    "version",
)


def _fact_to_edge_attrs(fact: StoredFact) -> dict[str, object]:
    """Convert a ``StoredFact`` into the required graph-edge attribute dict.

    All values are derived directly from the validated contract; no raw
    database rows are read here.  Confidence decay, source ranking, or
    "winner" status are deliberately absent.
    """
    return {
        "fact_id": fact.fact_id,
        "predicate": fact.predicate,
        "source_agent": fact.source_agent,
        "confidence_score": fact.confidence_score,
        "observed_at": fact.observed_at,
        "created_at": fact.created_at,
        "context": fact.context,
        "version": fact.version,
        "superseded_by": fact.superseded_by,
        # Optional: carry the full evidence payload for explanation/debugging.
        "evidence": fact.evidence,
    }


def _build_graph(
    facts: list[StoredFact],
) -> tuple[nx.MultiDiGraph, dict[str, tuple[str, str]]]:
    """Construct a fresh ``MultiDiGraph`` from *facts*.

    Returns the graph plus a ``fact_id -> (subject, object)`` index so that
    edge lookups by fact ID are O(1) instead of a full graph scan.

    Each fact becomes exactly one directed edge keyed by ``str(fact_id)``.
    Subject and object nodes are added automatically by NetworkX when the
    edge is added.
    """
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    index: dict[str, tuple[str, str]] = {}
    for fact in facts:
        edge_key = str(fact.fact_id)
        graph.add_edge(
            fact.subject,
            fact.object,
            key=edge_key,
            **_fact_to_edge_attrs(fact),
        )
        index[edge_key] = (fact.subject, fact.object)
    return graph, index


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class ActiveBeliefGraph:
    """Lazy, rebuildable projection of active SQLite facts into a graph.

    The graph is a *derived cache*.  It is always safe to discard and
    reconstruct from SQLite.  Callers must never treat it as a second
    authoritative store.

    Args:
        fact_reader: Any object satisfying ``ActiveFactReader``.  In
            production this is a ``MemoryRepository``; in tests it is a
            fake reader.
    """

    def __init__(self, fact_reader: ActiveFactReader) -> None:
        self._reader = fact_reader
        self._cache: nx.MultiDiGraph | None = None  # None means stale
        self._edge_index: dict[str, tuple[str, str]] = {}
        self._stale: bool = True

    # ------------------------------------------------------------------
    # Core lifecycle API
    # ------------------------------------------------------------------

    def get_graph(self) -> nx.MultiDiGraph:
        """Return the current active projection, rebuilding lazily if stale.

        Returns a **defensive copy** of the internal graph so that external
        mutations (adding or removing edges/nodes) cannot corrupt future reads.

        Raises:
            Exception: Propagates any exception raised by the underlying
                ``ActiveFactReader``.  A query failure is never silently
                converted into an empty graph, because an empty graph is
                indistinguishable from "no beliefs exist."
        """
        if self._stale or self._cache is None:
            self._cache = self.rebuild()
            self._stale = False
        return self._cache.copy()

    def invalidate(self) -> None:
        """Mark the derived cache stale without touching SQLite.

        Call this **after** a successful ``record_fact`` or
        ``record_revision`` transaction commits.  Never call it before the
        commit: a failed or rolled-back write must not alter the graph.
        """
        self._stale = True
        self._cache = None

    def rebuild(self) -> nx.MultiDiGraph:
        """Reconstruct the entire graph from the current active SQLite facts.

        Queries the reader for all active facts (``FactQuery(active_only=True)``)
        and builds a fresh ``MultiDiGraph`` from scratch. Also rebuilds the
        internal fact_id → (subject, object) index used for O(1) edge lookups.
        The result replaces the internal cache and is also returned so callers
        can inspect it.

        This method never writes facts or audit events back to SQLite.

        Raises:
            Exception: Propagates any exception raised by ``query_facts``.
        """
        active_facts = self._reader.query_facts(FactQuery(active_only=True))
        graph, index = _build_graph(active_facts)
        self._cache = graph
        self._edge_index = index
        self._stale = False
        return graph

    # ------------------------------------------------------------------
    # Traversal helpers
    # ------------------------------------------------------------------

    def get_outgoing(self, subject: str) -> list[dict[str, object]]:
        """Return all active outgoing relationships for *subject*.

        Each item in the returned list is a dict with ``object`` (the target
        node) plus all required edge attributes.  The list may be empty if
        *subject* has no active outgoing edges.

        Callers must not use this to infer which conflicting claim "wins."
        Multiple entries with the same predicate may legitimately coexist.
        """
        graph = self.get_graph()
        results: list[dict[str, object]] = []
        if subject not in graph:
            return results
        for _u, target, attrs in graph.out_edges(subject, data=True):
            results.append({"object": target, **attrs})
        return results

    def get_incoming(self, obj: str) -> list[dict[str, object]]:
        """Return all active incoming relationships for *obj* (the object node).

        Each item in the returned list is a dict with ``subject`` (the source
        node) plus all required edge attributes.
        """
        graph = self.get_graph()
        results: list[dict[str, object]] = []
        if obj not in graph:
            return results
        for source, _v, attrs in graph.in_edges(obj, data=True):
            results.append({"subject": source, **attrs})
        return results

    def get_edge_by_fact_id(self, fact_id: UUID) -> dict[str, object] | None:
        """Return the edge attributes for *fact_id*, or ``None`` if absent.

        Performs an O(1) lookup using an internal fact_id → (subject, object)
        index built during the last rebuild, rather than scanning every edge.
        """
        graph = self.get_graph()
        key = str(fact_id)
        location = self._edge_index.get(key)
        if location is None:
            return None
        u, v = location
        if not graph.has_edge(u, v, key=key):
            return None
        attrs = graph.get_edge_data(u, v, key=key)
        return {"subject": u, "object": v, **attrs}

    def active_fact_ids(self) -> Iterator[UUID]:
        """Yield the ``fact_id`` UUID for every edge currently in the graph.

        The graph is rebuilt lazily if stale before iterating.
        """
        graph = self.get_graph()
        for _u, _v, attrs in graph.edges(data=True):
            yield attrs["fact_id"]
