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
        → OR, if writing through GraphSyncedRepository, this happens
          automatically on every successful write

    next graph request
        → rebuild from the new active SQLite state

    Mutation safety
    ---------------
    ``get_graph()`` and ``rebuild()`` both return a **deep copy** of the
    internal cached graph, including nested attribute values (``evidence``
    dicts, ``SpatialContext.extra_context``). A Tier 2 caller or dashboard
    may add/remove edges or mutate nested metadata on the returned object
    without ever corrupting the projection that subsequent reads will return.

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

import copy
from collections.abc import Iterator
from datetime import datetime
from typing import Protocol
from uuid import UUID

import networkx as nx

from src.contracts import (
    AuditTrail,
    EntityResolution,
    FactAssertion,
    FactQuery,
    StoredFact,
)
from src.declarative.memory_repository import RevisionOutcome


class ActiveFactReader(Protocol):
    """Structural interface that the graph projector requires from Tier 1.

    Any object that implements ``query_facts`` with this signature satisfies
    the protocol.  In production this will be a ``MemoryRepository`` instance;
    in unit tests it will be a lightweight fake.
    """

    def query_facts(self, query: FactQuery) -> list[StoredFact]:
        """Return all ``StoredFact`` objects matching *query*."""
        ...


class WritableFactRepository(Protocol):
    """Structural interface for the subset of MemoryRepository that mutates state.

    Any object implementing these two methods with this signature satisfies
    the protocol. In production this is a ``MemoryRepository`` instance.
    """

    def record_fact(self, assertion: FactAssertion) -> StoredFact:
        """Store a new sourced assertion and return the durable StoredFact."""
        ...

    def record_revision(
        self,
        old_fact_id: UUID,
        replacement: FactAssertion,
        reason: str,
        policy_rule: str,
        revised_at: datetime,
    ) -> RevisionOutcome:
        """Atomically replace an active fact with a new assertion."""
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
        return copy.deepcopy(self._cache)

    def invalidate(self) -> None:
        """Mark the derived cache stale without touching SQLite.

        Call this **after** a successful ``record_fact`` or
        ``record_revision`` transaction commits.  Never call it before the
        commit: a failed or rolled-back write must not alter the graph.

        Callers who want this called automatically on every successful write
        should write through ``GraphSyncedRepository`` instead of calling
        the repository directly.
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
        return copy.deepcopy(graph)

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


class GraphSyncedRepository:
    """Composition wrapper that owns both writes and cache invalidation.

    Wraps a ``WritableFactRepository`` (in production, ``MemoryRepository``)
    and an ``ActiveBeliefGraph``. Every write goes through this wrapper's
    ``record_fact``/``record_revision`` methods, which delegate to the
    underlying repository and call ``graph.invalidate()`` *only after* the
    underlying call returns successfully.

    In addition, if the underlying repository supports write listeners
    (``add_write_listener``), the graph's ``invalidate()`` callback is
    registered directly on the repository so that even direct repository
    writes cannot leave a stale graph cache.

    If the underlying repository call raises (a failed or rolled-back
    write), the exception propagates immediately and ``invalidate()`` is
    never reached — the graph cache is correctly left untouched.

    This class serves as the primary application-level write boundary
    and facade connecting Tier 1 SQLite persistence with the NetworkX
    active-belief graph projection.
    """

    def __init__(self, repository: WritableFactRepository, graph: ActiveBeliefGraph) -> None:
        self._repository = repository
        self._graph = graph
        self._invalidator = graph.invalidate
        self._uses_listeners = hasattr(repository, "add_write_listener")
        if self._uses_listeners:
            repository.add_write_listener(self._invalidator)

    @classmethod
    def create(cls, repository: WritableFactRepository) -> GraphSyncedRepository:
        """Create a GraphSyncedRepository managing its own ActiveBeliefGraph."""
        graph = ActiveBeliefGraph(repository)  # type: ignore[arg-type]
        return cls(repository, graph)

    @property
    def repository(self) -> WritableFactRepository:
        """The underlying storage repository."""
        return self._repository

    @property
    def graph(self) -> ActiveBeliefGraph:
        """The managed active belief graph."""
        return self._graph

    def record_fact(self, assertion: FactAssertion) -> StoredFact:
        """Delegate to the repository, then invalidate the graph on success."""
        result = self._repository.record_fact(assertion)
        if not self._uses_listeners:
            self._graph.invalidate()
        return result

    def record_revision(
        self,
        old_fact_id: UUID,
        replacement: FactAssertion,
        reason: str,
        policy_rule: str,
        revised_at: datetime,
    ) -> RevisionOutcome:
        """Delegate to the repository, then invalidate the graph on success."""
        result = self._repository.record_revision(
            old_fact_id, replacement, reason, policy_rule, revised_at
        )
        if not self._uses_listeners:
            self._graph.invalidate()
        return result

    def query_facts(self, query: FactQuery) -> list[StoredFact]:
        """Delegate query_facts to the underlying repository."""
        if hasattr(self._repository, "query_facts"):
            return self._repository.query_facts(query)
        raise NotImplementedError("Underlying repository does not support query_facts")

    def get_audit_chain(self, fact_id: UUID) -> AuditTrail:
        """Delegate get_audit_chain to the underlying repository."""
        if hasattr(self._repository, "get_audit_chain"):
            return self._repository.get_audit_chain(fact_id)
        raise NotImplementedError("Underlying repository does not support get_audit_chain")

    def add_alias(self, mention: str, canonical_entity_id: str) -> None:
        """Delegate add_alias to the underlying repository."""
        if hasattr(self._repository, "add_alias"):
            self._repository.add_alias(mention, canonical_entity_id)
            return
        raise NotImplementedError("Underlying repository does not support add_alias")

    def resolve_entity(self, mention: str) -> EntityResolution:
        """Delegate resolve_entity to the underlying repository."""
        if hasattr(self._repository, "resolve_entity"):
            return self._repository.resolve_entity(mention)
        raise NotImplementedError("Underlying repository does not support resolve_entity")

    def get_graph(self) -> nx.MultiDiGraph:
        """Return the active belief graph projection."""
        return self._graph.get_graph()

    def close(self) -> None:
        """Close the wrapper and unregister its listener from the underlying repository."""
        if self._uses_listeners and hasattr(self._repository, "remove_write_listener"):
            self._repository.remove_write_listener(self._invalidator)

    def __enter__(self) -> GraphSyncedRepository:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
