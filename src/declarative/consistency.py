"""
Graph/SQLite consistency checker for GroundTruth Tier 1.

This module provides ``check_graph_consistency``, a pure function that diffs
the active ``networkx.MultiDiGraph`` projection against authoritative SQLite
state and returns a structured ``GraphConsistencyReport``.

The checker never repairs anything.  A non-clean report means the caller should
call ``ActiveBeliefGraph.rebuild()`` — discarding the stale projection and
reconstructing from SQLite.  The checker must not write to SQLite.

Why a dataclass, not a Pydantic model
--------------------------------------
``GraphConsistencyReport`` is an internal Tier 1 diagnostic type.  It does not
cross a tier boundary, so adding it to the shared ``src.contracts`` package is
not warranted.  A stdlib ``dataclass`` keeps it lightweight, dependency-free,
and easy to construct in tests.

Why staleness requires superseded facts, not just active facts
----------------------------------------------------------------
A cached graph edge is built at rebuild time from a ``StoredFact`` that was
active *then*.  If that fact is later superseded in SQLite, nothing
retroactively updates the cached edge; the edge is simply absent from a
fresh ``active_facts`` query.  To correctly classify a missing-from-active
edge as "stale" (fact still exists, just superseded) versus "extra" (fact_id
never existed at all), the checker needs authoritative knowledge of
superseded fact IDs, supplied via ``superseded_facts``.

What "representation correctness" means here
-----------------------------------------------
An edge is only a faithful representation of a ``StoredFact`` if ALL of the
following hold:

* the edge is keyed by ``str(fact.fact_id)`` (the key itself matches);
* the edge's endpoints ``(u, v)`` equal ``(fact.subject, fact.object)``;
* the edge carries a ``fact_id`` attribute equal to ``fact.fact_id`` (not
  missing, not some other UUID);
* every other required attribute (``predicate``, ``source_agent``,
  ``confidence_score``, ``observed_at``, ``created_at``, ``context``,
  ``version``, ``superseded_by``, ``evidence``) matches the stored value.

A violation of any of these is a representation error and is reported via
``metadata_mismatches``, keyed by field name (``"subject"``, ``"object"``,
``"fact_id"``, or one of the other attribute names).

How to interpret the report
----------------------------
* ``missing_fact_ids``: active facts that have no corresponding graph edge
  keyed by their fact ID.
* ``extra_edge_keys``: graph edges whose key does not correspond to any
  known SQLite fact, active or superseded.
* ``stale_edge_keys``: graph edges whose key is known to SQLite (via
  ``superseded_facts``) but is now superseded.
* ``duplicate_edge_keys``: fact IDs that appear on more than one graph edge.
* ``metadata_mismatches``: ``(edge_key, field_name, graph_value, stored_value)``
  tuples for every divergence found, including endpoint and fact_id
  mismatches, not only the four original attribute fields.

Why conflict resolution is out of scope
----------------------------------------
The consistency check compares *representation* (does the graph faithfully
mirror SQLite?), not *truth* (which conflicting claim is operationally
correct?).  That is Tier 2's responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import networkx as nx

from src.contracts import StoredFact

# ---------------------------------------------------------------------------
# Report dataclass
# ---------------------------------------------------------------------------


@dataclass
class GraphConsistencyReport:
    """Structured diff between the active-belief graph and authoritative SQLite state.

    A report with all empty collections means the graph is a faithful,
    up-to-date, correctly represented projection of the current active
    SQLite state.

    Attributes:
        missing_fact_ids: UUIDs of active SQLite facts that have no edge in
            the graph keyed by their fact ID.
        extra_edge_keys: Edge keys (``str(fact_id)``) present in the graph
            that do not correspond to any known SQLite fact, active or
            superseded.
        stale_edge_keys: Edge keys present in the graph that map to SQLite
            facts which are now superseded (per ``superseded_facts``).
        duplicate_edge_keys: Edge keys that appear on more than one graph edge.
        metadata_mismatches: List of 4-tuples
            ``(edge_key, field_name, graph_value, stored_value)``. ``field_name``
            may be ``"subject"``, ``"object"``, ``"fact_id"``, or any other
            required edge attribute name.
    """

    missing_fact_ids: list[UUID] = field(default_factory=list)
    extra_edge_keys: list[str] = field(default_factory=list)
    stale_edge_keys: list[str] = field(default_factory=list)
    duplicate_edge_keys: list[str] = field(default_factory=list)
    metadata_mismatches: list[tuple[str, str, object, object]] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """``True`` when the report contains no anomalies."""
        return not (
            self.missing_fact_ids
            or self.extra_edge_keys
            or self.stale_edge_keys
            or self.duplicate_edge_keys
            or self.metadata_mismatches
        )


# ---------------------------------------------------------------------------
# Metadata fields to compare (beyond subject/object/fact_id, checked separately)
# ---------------------------------------------------------------------------

_METADATA_FIELDS: tuple[tuple[str, str], ...] = (
    ("predicate", "predicate"),
    ("source_agent", "source_agent"),
    ("confidence_score", "confidence_score"),
    ("observed_at", "observed_at"),
    ("created_at", "created_at"),
    ("context", "context"),
    ("version", "version"),
    ("superseded_by", "superseded_by"),
    ("evidence", "evidence"),
)


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def check_graph_consistency(
    graph: nx.MultiDiGraph,
    active_facts: list[StoredFact],
    superseded_facts: list[StoredFact] | None = None,
) -> GraphConsistencyReport:
    """Diff *graph* against authoritative SQLite state and return a report.

    This is a **pure function**: it reads from *graph*, *active_facts*, and
    *superseded_facts* only, and never writes to SQLite or modifies the graph.

    Args:
        graph: The current active-belief ``MultiDiGraph``.
        active_facts: The list of currently active ``StoredFact`` objects.
        superseded_facts: The list of ``StoredFact`` objects known to SQLite
            but no longer active. Required to distinguish a stale edge from
            an extra/phantom edge. If omitted, every unmatched edge is
            reported as "extra".

    Returns:
        A ``GraphConsistencyReport`` describing every detected anomaly.
    """
    report = GraphConsistencyReport()

    active_by_id: dict[UUID, StoredFact] = {f.fact_id: f for f in active_facts}
    active_keys: set[str] = {str(fid) for fid in active_by_id}

    superseded_ids: set[str] = (
        {str(f.fact_id) for f in superseded_facts} if superseded_facts else set()
    )

    # Build a full index of graph edges: key -> list of (u, v, attrs).
    # Kept as a list per key so duplicate-key usage across different node
    # pairs is fully captured, not overwritten.
    key_to_edges: dict[str, list[tuple[str, str, dict[str, object]]]] = {}
    for u, v, key, attrs in graph.edges(keys=True, data=True):
        key_to_edges.setdefault(key, []).append((u, v, attrs))

    graph_keys: set[str] = set(key_to_edges.keys())

    # ------------------------------------------------------------------
    # 1. Missing: active facts not represented by any edge keyed with their ID
    # ------------------------------------------------------------------
    for fact_id in active_by_id:
        if str(fact_id) not in graph_keys:
            report.missing_fact_ids.append(fact_id)

    # ------------------------------------------------------------------
    # 2. Extra / stale: graph edge keys not in the active-fact list
    # ------------------------------------------------------------------
    for key in graph_keys:
        if key not in active_keys:
            if key in superseded_ids:
                report.stale_edge_keys.append(key)
            else:
                report.extra_edge_keys.append(key)

    # ------------------------------------------------------------------
    # 3. Duplicates: same fact-ID key used on more than one edge
    # ------------------------------------------------------------------
    for key, edges in key_to_edges.items():
        if len(edges) > 1:
            report.duplicate_edge_keys.append(key)

    # ------------------------------------------------------------------
    # 4. Representation correctness: key, endpoints, fact_id, and all
    #    other required attributes must match the stored active fact.
    # ------------------------------------------------------------------
    for key in graph_keys:
        if key not in active_keys:
            continue  # already flagged as extra/stale above
        stored = active_by_id[UUID(key)]

        for u, v, attrs in key_to_edges[key]:
            # Endpoint validation: edge with the right key but wrong nodes.
            if u != stored.subject:
                report.metadata_mismatches.append((key, "subject", u, stored.subject))
            if v != stored.object:
                report.metadata_mismatches.append((key, "object", v, stored.object))

            # fact_id attribute validation: missing or wrong fact_id on the edge.
            fact_id_attr = attrs.get("fact_id")
            if fact_id_attr != stored.fact_id:
                report.metadata_mismatches.append((key, "fact_id", fact_id_attr, stored.fact_id))

            # All other required attributes.
            for edge_attr, fact_attr in _METADATA_FIELDS:
                graph_val = attrs.get(edge_attr)
                stored_val = getattr(stored, fact_attr, None)
                if graph_val != stored_val:
                    report.metadata_mismatches.append((key, edge_attr, graph_val, stored_val))

    return report
