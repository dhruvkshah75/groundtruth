"""
Tier 1 SQLite-backed evidence ledger.

This module provides MemoryRepository, the single authoritative persistence
layer for GroundTruth facts. SQLite is the only persistent source of truth.
NetworkX is deliberately excluded; #6 will build derived projections on top.

Connection strategy: one persistent connection per repository instance.
This avoids the :memory: multi-connection pitfall where separate connections
see separate databases.
"""

import json
import sqlite3
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.contracts import (
    AuditEvent,
    AuditTrail,
    EntityResolution,
    FactAssertion,
    FactQuery,
    SpatialContext,
    StoredFact,
)
from src.declarative.schema import initialize_schema


@dataclass
class RevisionOutcome:
    """Result of an atomic belief revision operation."""

    successor: StoredFact
    audit_event: AuditEvent


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_alias(text: str) -> str:
    """Strip, casefold, collapse whitespace, NFC normalize."""
    casefolded = unicodedata.normalize("NFC", text).casefold()
    return " ".join(casefolded.split())


def _dt_to_iso(dt: datetime) -> str:
    """Serialize timezone-aware datetime to canonical ISO-8601 UTC string."""
    return dt.astimezone(UTC).isoformat()


def _iso_to_dt(s: str) -> datetime:
    """Parse ISO-8601 string to timezone-aware datetime."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class MemoryRepository:
    """
    Tier 1 evidence ledger backed by SQLite.

    One connection is kept open for the lifetime of the repository.
    This makes :memory: databases safe for testing (one DB per instance).

    Args:
        db_path: File path for persistent storage, or ":memory:" for tests.
        clock: Injectable callable returning current UTC datetime (for tests).
        uuid_factory: Injectable callable returning a new UUID (for tests).
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._db_path = db_path
        self._clock = clock or _utc_now
        self._uuid_factory = uuid_factory or uuid4
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        initialize_schema(self._conn)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def __enter__(self) -> "MemoryRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # fact storage
    # ------------------------------------------------------------------

    def record_fact(self, assertion: FactAssertion) -> StoredFact:
        """Store a new sourced assertion and return the durable StoredFact.

        Assigns a UUID and created_at inside Tier 1. Does not supersede any
        existing fact; conflicting assertions coexist until an explicit revision.
        """
        fact_id = self._uuid_factory()
        created_at = self._clock()
        context_json = assertion.context.model_dump_json()
        evidence_json = json.dumps(assertion.evidence)

        self._conn.execute(
            """
            INSERT INTO facts (
                fact_id, version, subject, predicate, object,
                source_agent, confidence_score, observed_at, created_at,
                context_json, evidence_json, superseded_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                str(fact_id),
                assertion.version,
                assertion.subject,
                assertion.predicate,
                assertion.object,
                assertion.source_agent,
                assertion.confidence_score,
                _dt_to_iso(assertion.observed_at),
                _dt_to_iso(created_at),
                context_json,
                evidence_json,
            ),
        )
        self._conn.commit()

        return StoredFact(
            fact_id=fact_id,
            version=assertion.version,
            subject=assertion.subject,
            predicate=assertion.predicate,
            object=assertion.object,
            source_agent=assertion.source_agent,
            confidence_score=assertion.confidence_score,
            observed_at=assertion.observed_at,
            created_at=created_at,
            context=assertion.context,
            evidence=assertion.evidence,
            superseded_by=None,
        )

    # ------------------------------------------------------------------
    # query
    # ------------------------------------------------------------------

    def query_facts(self, query: FactQuery) -> list[StoredFact]:
        """Return StoredFact objects matching the structured query.

        Named context fields (location, observer, world_version, frame_of_reference)
        are matched exactly against stored JSON. extra_context keys are matched by
        exact equality on each key present in the query context: a stored fact matches
        only if every key in query.context.extra_context is present with the same value.
        Keys absent from the query filter are ignored (partial match).

        Never exposes raw SQL or sqlite3.Row objects to callers.
        """
        conditions: list[str] = []
        params: list[object] = []

        if query.subject is not None:
            conditions.append("subject = ?")
            params.append(query.subject)

        if query.predicate is not None:
            conditions.append("predicate = ?")
            params.append(query.predicate)

        if query.source_agent is not None:
            conditions.append("source_agent = ?")
            params.append(query.source_agent)

        if query.context is not None:
            ctx = query.context
            if ctx.location is not None:
                conditions.append("json_extract(context_json, '$.location') = ?")
                params.append(ctx.location)
            if ctx.observer is not None:
                conditions.append("json_extract(context_json, '$.observer') = ?")
                params.append(ctx.observer)
            if ctx.world_version is not None:
                conditions.append("json_extract(context_json, '$.world_version') = ?")
                params.append(ctx.world_version)
            if ctx.frame_of_reference is not None:
                conditions.append("json_extract(context_json, '$.frame_of_reference') = ?")
                params.append(ctx.frame_of_reference)
            # extra_context: match each key present in the query exactly.
            for key, value in ctx.extra_context.items():
                conditions.append(f"json_extract(context_json, '$.extra_context.{key}') = ?")
                params.append(value)

        if query.as_of is not None:
            as_of_str = _dt_to_iso(query.as_of)
            conditions.append("created_at <= ?")
            params.append(as_of_str)

            if query.active_only:
                # A fact is active as-of T if superseded_by is NULL at that time,
                # meaning the supersession was recorded AFTER T (or never).
                # We check the audit event created_at to find when supersession happened.
                conditions.append(
                    "(superseded_by IS NULL OR "
                    "(SELECT created_at FROM audit_events ae "
                    " WHERE ae.old_fact_id = fact_id "
                    " ORDER BY ae.created_at DESC LIMIT 1) > ?)"
                )
                params.append(as_of_str)
        elif query.active_only:
            conditions.append("superseded_by IS NULL")

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM facts {where_clause} ORDER BY created_at ASC"

        cursor = self._conn.execute(sql, params)
        return [self._row_to_stored_fact(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # revision
    # ------------------------------------------------------------------

    def record_revision(
        self,
        old_fact_id: UUID,
        replacement: FactAssertion,
        reason: str,
        policy_rule: str,
        revised_at: datetime,
    ) -> RevisionOutcome:
        """Atomically replace an active fact with a new assertion.

        All steps (insert successor, link predecessor, insert audit event) happen
        in one transaction. Any failure rolls back completely.

        Raises:
            ValueError: If old_fact_id is unknown, already superseded, or
                        parameters are invalid.
        """
        if not reason.strip():
            raise ValueError("reason must not be empty")
        if not policy_rule.strip():
            raise ValueError("policy_rule must not be empty")
        if revised_at.tzinfo is None or revised_at.utcoffset() is None:
            raise ValueError("revised_at must be timezone-aware")

        # Fast pre-flight check (existence only) outside the transaction so we
        # can raise a clear ValueError before acquiring a write lock.
        exists_row = self._conn.execute(
            "SELECT 1 FROM facts WHERE fact_id = ?", (str(old_fact_id),)
        ).fetchone()
        if exists_row is None:
            raise ValueError(f"fact {old_fact_id} does not exist")

        new_fact_id = self._uuid_factory()
        if new_fact_id == old_fact_id:
            raise ValueError("successor UUID must differ from predecessor")

        created_at = self._clock()
        event_id = self._uuid_factory()
        context_json = replacement.context.model_dump_json()
        evidence_json = json.dumps(replacement.evidence)
        revised_at_str = _dt_to_iso(revised_at)

        try:
            self._conn.execute("BEGIN")

            # Step 1: insert successor fact
            self._conn.execute(
                """
                INSERT INTO facts (
                    fact_id, version, subject, predicate, object,
                    source_agent, confidence_score, observed_at, created_at,
                    context_json, evidence_json, superseded_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    str(new_fact_id),
                    replacement.version,
                    replacement.subject,
                    replacement.predicate,
                    replacement.object,
                    replacement.source_agent,
                    replacement.confidence_score,
                    _dt_to_iso(replacement.observed_at),
                    _dt_to_iso(created_at),
                    context_json,
                    evidence_json,
                ),
            )

            # Step 2: link predecessor → successor only if it is still active.
            # The WHERE superseded_by IS NULL guard prevents a concurrent revision
            # from creating a second active successor on the same predecessor.
            cursor = self._conn.execute(
                "UPDATE facts SET superseded_by = ? WHERE fact_id = ? AND superseded_by IS NULL",
                (str(new_fact_id), str(old_fact_id)),
            )
            if cursor.rowcount == 0:
                # Predecessor was already superseded by a concurrent writer inside
                # this transaction window.
                raise ValueError(f"fact {old_fact_id} is already superseded")

            # Step 3: insert audit event
            self._conn.execute(
                """
                INSERT INTO audit_events (
                    event_id, event_type, old_fact_id, new_fact_id,
                    reason, policy_rule, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event_id),
                    "belief_revision",
                    str(old_fact_id),
                    str(new_fact_id),
                    reason,
                    policy_rule,
                    revised_at_str,
                ),
            )

            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

        successor = StoredFact(
            fact_id=new_fact_id,
            version=replacement.version,
            subject=replacement.subject,
            predicate=replacement.predicate,
            object=replacement.object,
            source_agent=replacement.source_agent,
            confidence_score=replacement.confidence_score,
            observed_at=replacement.observed_at,
            created_at=created_at,
            context=replacement.context,
            evidence=replacement.evidence,
            superseded_by=None,
        )
        audit_event = AuditEvent(
            event_id=event_id,
            event_type="belief_revision",
            input_fact_ids=[old_fact_id, new_fact_id],
            output_fact_ids=[new_fact_id],
            reason=reason,
            policy_rule=policy_rule,
            created_at=revised_at,
        )
        return RevisionOutcome(successor=successor, audit_event=audit_event)

    # ------------------------------------------------------------------
    # audit chain
    # ------------------------------------------------------------------

    def get_audit_chain(self, fact_id: UUID) -> AuditTrail:
        """Return the complete revision chain and audit events for a fact.

        Walks predecessor/successor links and raises ValueError if a cycle or
        corrupt link is detected rather than returning a partial trail.
        Raises ValueError for unknown fact_id or corrupt chain data.
        """
        root_row = self._conn.execute(
            "SELECT * FROM facts WHERE fact_id = ?", (str(fact_id),)
        ).fetchone()
        if root_row is None:
            raise ValueError(f"fact {fact_id} does not exist")

        # Collect all fact IDs in the chain
        chain_ids: list[str] = []
        visited: set[str] = set()

        # Walk backwards to find the oldest predecessor in the chain
        current_id = str(fact_id)
        while True:
            if current_id in visited:
                raise ValueError(
                    f"Corrupt audit chain detected: cycle at fact {current_id} "
                    f"while walking predecessors from {fact_id}"
                )
            visited.add(current_id)
            chain_ids.append(current_id)
            pred_row = self._conn.execute(
                "SELECT fact_id FROM facts WHERE superseded_by = ?", (current_id,)
            ).fetchone()
            if pred_row is None:
                break
            current_id = pred_row["fact_id"]

        # Walk forward from the oldest predecessor to collect all successors
        root_id = current_id
        chain_ids_set = set(chain_ids)
        forward_id = root_id
        forward_visited: set[str] = {root_id}
        while True:
            row = self._conn.execute(
                "SELECT superseded_by FROM facts WHERE fact_id = ?", (forward_id,)
            ).fetchone()
            if row is None or row["superseded_by"] is None:
                break
            next_id = row["superseded_by"]
            if next_id in forward_visited:
                raise ValueError(
                    f"Corrupt audit chain detected: cycle at fact {next_id} "
                    f"while walking successors from {fact_id}"
                )
            forward_visited.add(next_id)
            if next_id not in chain_ids_set:
                chain_ids.append(next_id)
                chain_ids_set.add(next_id)
            forward_id = next_id

        # Fetch all facts in the chain
        all_facts: list[StoredFact] = []
        for fid in chain_ids:
            row = self._conn.execute("SELECT * FROM facts WHERE fact_id = ?", (fid,)).fetchone()
            if row is not None:
                all_facts.append(self._row_to_stored_fact(row))

        # Collect all audit events for facts in this chain
        fact_id_strs = [f.fact_id for f in all_facts]
        all_events: list[AuditEvent] = []
        if fact_id_strs:
            placeholders = ",".join(["?" for _ in fact_id_strs])
            str_ids = [str(fid) for fid in fact_id_strs]
            event_rows = self._conn.execute(
                f"SELECT * FROM audit_events WHERE old_fact_id IN ({placeholders})"
                f" OR new_fact_id IN ({placeholders})"
                " ORDER BY created_at ASC",
                str_ids + str_ids,
            ).fetchall()
            for er in event_rows:
                all_events.append(self._row_to_audit_event(er))

        return AuditTrail(
            root_fact_id=fact_id,
            facts=all_facts,
            events=all_events,
        )

    # ------------------------------------------------------------------
    # entity resolution
    # ------------------------------------------------------------------

    def add_alias(self, mention: str, canonical_entity_id: str) -> None:
        """Register an alias mapping. Duplicate identical mappings are ignored."""
        normalized = _normalize_alias(mention)
        self._conn.execute(
            "INSERT OR IGNORE INTO aliases (alias, canonical_entity_id) VALUES (?, ?)",
            (normalized, canonical_entity_id),
        )
        self._conn.commit()

    def resolve_entity(self, mention: str) -> EntityResolution:
        """Resolve a user-friendly entity name to canonical ID(s).

        Returns resolved (one match), ambiguous (multiple matches), or missing.
        No fuzzy matching or LLM calls are used.
        """
        normalized = _normalize_alias(mention)
        rows = self._conn.execute(
            "SELECT DISTINCT canonical_entity_id FROM aliases WHERE alias = ?"
            " ORDER BY canonical_entity_id ASC",
            (normalized,),
        ).fetchall()
        candidates = [r["canonical_entity_id"] for r in rows]

        if len(candidates) == 0:
            return EntityResolution(mention=mention, status="missing")
        if len(candidates) == 1:
            return EntityResolution(
                mention=mention,
                status="resolved",
                canonical_entity_id=candidates[0],
            )
        return EntityResolution(
            mention=mention,
            status="ambiguous",
            candidates=candidates,
        )

    # ------------------------------------------------------------------
    # private helpers
    # ------------------------------------------------------------------

    def _row_to_stored_fact(self, row: sqlite3.Row) -> StoredFact:
        """Convert a sqlite3.Row from the facts table into a StoredFact contract."""
        context = SpatialContext.model_validate_json(row["context_json"])
        evidence = json.loads(row["evidence_json"])
        superseded_by = UUID(row["superseded_by"]) if row["superseded_by"] else None
        return StoredFact(
            fact_id=UUID(row["fact_id"]),
            version=row["version"],
            subject=row["subject"],
            predicate=row["predicate"],
            object=row["object"],
            source_agent=row["source_agent"],
            confidence_score=row["confidence_score"],
            observed_at=_iso_to_dt(row["observed_at"]),
            created_at=_iso_to_dt(row["created_at"]),
            context=context,
            evidence=evidence,
            superseded_by=superseded_by,
        )

    def _row_to_audit_event(self, row: sqlite3.Row) -> AuditEvent:
        """Convert a sqlite3.Row from audit_events into an AuditEvent contract."""
        return AuditEvent(
            event_id=UUID(row["event_id"]),
            event_type=row["event_type"],
            input_fact_ids=[UUID(row["old_fact_id"]), UUID(row["new_fact_id"])],
            output_fact_ids=[UUID(row["new_fact_id"])],
            reason=row["reason"],
            policy_rule=row["policy_rule"],
            created_at=_iso_to_dt(row["created_at"]),
        )
