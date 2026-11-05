"""Tier 1 declarative memory package for GroundTruth.

Public surface
--------------
Repository (from #3):
    MemoryRepository  — SQLite-backed evidence ledger.
    RevisionOutcome   — Return type for record_revision().

Graph projection (GT-05):
    ActiveFactReader      — Protocol satisfied by MemoryRepository and test fakes.
    ActiveBeliefGraph     — Lazy, rebuildable NetworkX MultiDiGraph cache.
    GraphConsistencyReport — Structured diff report for graph/SQLite consistency.
    check_graph_consistency — Pure function that produces a GraphConsistencyReport.
"""

from src.contracts import (
    ObservationLedger,
    ObservationLedgerReader,
    ObservationLedgerWriter,
    StoredObservationSample,
)

from .active_graph import ActiveBeliefGraph, ActiveFactReader, GraphSyncedRepository
from .consistency import GraphConsistencyReport, check_graph_consistency
from .memory_repository import MemoryRepository, RevisionOutcome

__all__ = [
    # Repository — GT-03 / P2-04
    "MemoryRepository",
    "RevisionOutcome",
    "StoredObservationSample",
    "ObservationLedger",
    "ObservationLedgerReader",
    "ObservationLedgerWriter",
    # Graph projection — GT-05
    "ActiveFactReader",
    "ActiveBeliefGraph",
    "GraphConsistencyReport",
    "check_graph_consistency",
    "GraphSyncedRepository",
]
