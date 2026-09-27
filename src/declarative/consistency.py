"""
Graph/SQLite consistency checker for GroundTruth Tier 1.

This module provides ``check_graph_consistency``, a pure function that diffs
the active ``networkx.MultiDiGraph`` projection against the current list of
active ``StoredFact`` objects and returns a structured ``GraphConsistencyReport``.

The checker never repairs anything.  A non-clean report means the caller should
call ``ActiveBeliefGraph.rebuild()`` — discarding the stale projection and
reconstructing from SQLite.  The checker must not write to SQLite.

Why a dataclass, not a Pydantic model
--------------------------------------
``GraphConsistencyReport`` is an internal Tier 1 diagnostic type.  It does not
cross a tier boundary, so adding it to the shared ``src.contracts`` package is
not warranted.  A stdlib ``dataclass`` keeps it lightweight, dependency-free,
and easy to construct in tests.

How to interpret the report
----------------------------
* ``missing_fact_ids``: active facts that have no corresponding graph edge.
  The graph is behind SQLite; a rebuild will fix this.
* ``extra_edge_keys``: graph edges whose fact-ID key does not appear in the
  active-fact list at all.  These are phantom edges — they may represent
  facts that were never stored or were fully deleted (which should not happen
  under append-only semantics, but is detectable).
* ``stale_edge_keys``: graph edges whose fact-ID is in SQLite but is now
  superseded (``superseded_by is not None``).  The cache was not invalidated
  after a revision commit.
* ``duplicate_edge_keys``: fact IDs that appear on more than one graph edge.
  Each fact must map to exactly one edge.
* ``metadata_mismatches``: ``(edge_key, field_name, graph_value, stored_value)``
  tuples for edges whose attribute values diverge from the corresponding
  ``StoredFact`` field.

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
    """Structured diff between the active-belief graph and SQLite active facts.

    A report with all empty collections means the graph is a faithful,
    up-to-date projection of the current active SQLite state.

    Attributes:
        missing_fact_ids: UUIDs of active SQLite facts that have no edge in
            the graph.
        extra_edge_keys: Edge keys (``str(fact_id)``) present in the graph
            that do not correspond to any known active SQLite fact.
        stale_edge_keys: Edge keys present in the graph that map to SQLite
            facts which are now superseded (``superseded_by is not None``).
        duplicate_edge_keys: Edge keys that appear on more than one graph edge.
        metadata_mismatches: List of 4-tuples
            ``(edge_key, field_name, graph_value, stored_value)``
            for every attribute that diverges between the graph edge and the
            corresponding ``StoredFact``.
    """

    missing_fact_ids: list[UUID] = field(default_factory=list)
    extra_edge_keys: list[str] = field(default_factory=list)
    stale_edge_keys: list[str] = field(default_factory=list)
    duplicate_edge_keys: list[str] = field(default_factory=list)
    metadata_mismatches: list[tuple[str, str, object, object]] = field(
        default_factory=list
    )

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
# Metadata fields to compare
# ---------------------------------------------------------------------------

# Maps the edge attribute name to the StoredFact attribute name.
# These are the four fields the issue explicitly requires checking.
_METADATA_FIELDS: tuple[tuple[str, str], ...] = (
    ("predicate", "predicate"),
    ("source_agent", "source_agent"),
    ("confidence_score", "confidence_score"),
    ("context", "context"),
)


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def check_graph_consistency(
    graph: nx.MultiDiGraph,
    active_facts: list[StoredFact],
) -> GraphConsistencyReport:
    """Diff *graph* against *active_facts* and return a ``GraphConsistencyReport``.

    This is a **pure function**: it reads from *graph* and *active_facts* only
    and never writes to SQLite or modifies the graph.

    Args:
        graph: The current active-belief ``MultiDiGraph``.  Typically obtained
            from ``ActiveBeliefGraph.get_graph()`` (which returns a copy).
        active_facts: The list of currently active ``StoredFact`` objects.
            Typically obtained by calling
            ``repo.query_facts(FactQuery(active_only=True))``.
        superseded_facts: Optional list of ``StoredFact`` objects that are no
            longer active (``superseded_by is not None``). Used to distinguish
            a genuinely stale edge (fact exists in SQLite but has been
            revised) from an extra/phantom edge (fact-ID does not correspond
            to any known SQLite row at all). If omitted, all unmatched edges
            are reported as "extra" rather than "stale".

    Returns:
        A ``GraphConsistencyReport`` describing every detected anomaly.
        ``report.is_clean`` is ``True`` when the graph is a faithful mirror
        of *active_facts*.

    Notes:
        * The function never attempts to repair the graph or SQLite.
        * It never calls any LLM, sensor, or Tier 2/3 module.
        * "Stale" edges are a subset of extra edges where the fact-ID exists
          in SQLite but is superseded; "extra" edges are those whose fact-ID
          is absent from *active_facts* entirely.  The report keeps them in
          separate lists so callers know whether the fact still exists in
          history or was never stored.
    """
    report = GraphConsistencyReport()

    # Build lookup structures from the active-facts list.
    active_by_id: dict[UUID, StoredFact] = {f.fact_id: f for f in active_facts}
    active_keys: set[str] = {str(fid) for fid in active_by_id}

    # Build lookup structures from the graph.
    graph_key_counts: dict[str, int] = {}
    graph_key_to_attrs: dict[str, dict[str, object]] = {}

    for _u, _v, key, attrs in graph.edges(keys=True, data=True):
        graph_key_counts[key] = graph_key_counts.get(key, 0) + 1
        # Keep the last attrs seen for a key (duplicates are flagged separately).
        graph_key_to_attrs[key] = attrs

    graph_keys: set[str] = set(graph_key_counts.keys())

    # ------------------------------------------------------------------
    # 1. Missing: active facts not in graph
    # ------------------------------------------------------------------
    for fact_id, _fact in active_by_id.items():
        if str(fact_id) not in graph_keys:
            report.missing_fact_ids.append(fact_id)

    # ------------------------------------------------------------------
    # 2. Extra / stale: graph edges not in the active-fact list
    # ------------------------------------------------------------------
    # A key absent from active_keys is either "stale" (the edge's own
    # superseded_by attribute is set — the fact was active when the edge was
    # built but has since been revised) or "extra" (the fact_id never
    # existed / superseded_by is unset). We read the signal directly off the
    # edge because active_facts alone (already filtered to active-only)
    # cannot distinguish the two cases.
    for key in graph_keys:
        if key not in active_keys:
            attrs = graph_key_to_attrs[key]
            if attrs.get("superseded_by") is not None:
                report.stale_edge_keys.append(key)
            else:
                report.extra_edge_keys.append(key)

    # ------------------------------------------------------------------
    # 3. Duplicates: same fact-ID used on more than one edge
    # ------------------------------------------------------------------
    for key, count in graph_key_counts.items():
        if count > 1:
            report.duplicate_edge_keys.append(key)

    # ------------------------------------------------------------------
    # 4. Metadata mismatches: edge attrs diverge from StoredFact fields
    # ------------------------------------------------------------------
    for key, attrs in graph_key_to_attrs.items():
        fact_id_attr = attrs.get("fact_id")
        if not isinstance(fact_id_attr, UUID):
            continue
        stored = active_by_id.get(fact_id_attr)
        if stored is None:
            continue  # already flagged as extra/stale above
        for edge_attr, fact_attr in _METADATA_FIELDS:
            graph_val = attrs.get(edge_attr)
            stored_val = getattr(stored, fact_attr, None)
            if graph_val != stored_val:
                report.metadata_mismatches.append(
                    (key, edge_attr, graph_val, stored_val)
                )

    return report
