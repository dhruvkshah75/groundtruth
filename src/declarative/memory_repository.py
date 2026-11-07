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
import threading
import unicodedata
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.contracts import (
    AuditEvent,
    AuditTrail,
    CameraScan,
    EntityResolution,
    FactAssertion,
    FactQuery,
    ObservationSample,
    Pose,
    SensorObservation,
    SpatialContext,
    StoredFact,
    StoredObservationSample,
    TemporalObservationQuery,
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
        write_listeners: list[Callable[[], None]] | None = None,
    ) -> None:
        self._ingestion_lock = threading.RLock()
        self._db_path = db_path
        self._clock = clock or _utc_now
        self._uuid_factory = uuid_factory or uuid4
        self._write_listeners: list[Callable[[], None]] = list(write_listeners or [])
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        initialize_schema(self._conn)

    def observation_ingestion_lock(self) -> AbstractContextManager:
        """Share one reentrant ingestion boundary across services using this repository."""
        return self._ingestion_lock

    def export_snapshot(self) -> bytes:
        """Return a complete SQLite image, including facts, revisions, and aliases."""
        return self._conn.serialize()

    def restore_snapshot(self, image: bytes) -> None:
        """Restore a previously exported SQLite image into this repository."""
        self._conn.deserialize(image)
        self._conn.execute("PRAGMA foreign_keys = ON")
        initialize_schema(self._conn)
        self._notify_write_listeners()

    def add_write_listener(self, listener: Callable[[], None]) -> None:
        """Register a callback invoked after any successful fact or revision commit."""
        if listener not in self._write_listeners:
            self._write_listeners.append(listener)

    def remove_write_listener(self, listener: Callable[[], None]) -> None:
        """Unregister a previously registered write listener."""
        if listener in self._write_listeners:
            self._write_listeners.remove(listener)

    def _notify_write_listeners(self) -> None:
        """Invoke all registered write listeners post-commit.

        Guarantees that every listener is executed even if an earlier listener
        raises an exception. Any exceptions raised by listeners are collected
        and re-raised after all listeners have been called.
        """
        errors: list[Exception] = []
        for listener in list(self._write_listeners):
            try:
                listener()
            except Exception as exc:
                errors.append(exc)
        if errors:
            if len(errors) == 1:
                raise errors[0]
            raise ExceptionGroup("One or more write listeners failed post-commit", errors)

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
        self._notify_write_listeners()

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

    def get_facts_by_sample_id(self, sample_id: UUID) -> list[StoredFact]:
        """Return all stored facts referencing sample_id in their evidence payload."""
        cursor = self._conn.execute(
            """
            SELECT * FROM facts
            WHERE json_extract(evidence_json, '$.sample_id') = ?
            ORDER BY created_at ASC
            """,
            (str(sample_id),),
        )
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

        self._notify_write_listeners()

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

    # ------------------------------------------------------------------
    # Observation ledger operations (P2-04)
    # ------------------------------------------------------------------

    def record_observation_sample(self, sample: ObservationSample) -> StoredObservationSample:
        """Durable, idempotent persistence for a raw observation sample.

        Does not create facts, revisions, audit events, or graph edges.

        Args:
            sample: Validated ObservationSample acquisition envelope.

        Returns:
            StoredObservationSample containing immutable acquisition data and
            storage UTC timestamp.

        Raises:
            ValidationError: If acquisition envelope or nested models are invalid.
            ValueError: If sample_id already exists with different canonical content.
        """
        # Revalidate detached acquisition payload before any DB interaction
        detached_data = sample.model_dump(mode="json")
        validated_sample = ObservationSample.model_validate(detached_data)

        stored_at = self._clock()
        if stored_at.tzinfo is None:
            stored_at = stored_at.replace(tzinfo=UTC)

        # Pre-validate full stored record before SQLite commit
        stored_data = validated_sample.model_dump(mode="json")
        stored_data["stored_at"] = stored_at
        stored_sample = StoredObservationSample.model_validate(stored_data)

        existing = self._conn.execute(
            "SELECT raw_sample_json, stored_at FROM observation_samples WHERE sample_id = ?",
            (str(stored_sample.sample_id),),
        ).fetchone()

        if existing is not None:
            existing_data = json.loads(existing["raw_sample_json"])
            new_data = validated_sample.model_dump(mode="json")
            if existing_data == new_data:
                return self._row_to_stored_sample(existing)
            raise ValueError(
                f"Observation sample with ID {stored_sample.sample_id} "
                "already exists with different content."
            )

        if isinstance(stored_sample.result, SensorObservation):
            status = "success"
            observation_id = str(stored_sample.result.observation_id)
            failure_reason = None
        else:
            status = "unavailable"
            observation_id = None
            failure_reason = stored_sample.result.reason

        detections: list[tuple[str, str]] = []
        if status == "success" and stored_sample.request.capability == "camera_detect":
            scan = CameraScan.model_validate(stored_sample.result.measurements)
            for obj in scan.objects:
                detections.append(
                    (obj.object_id, json.dumps(obj.model_dump(mode="json"), sort_keys=True))
                )

        sample_json = json.dumps(validated_sample.model_dump(mode="json"), sort_keys=True)
        pose_json = json.dumps(stored_sample.observer_pose.model_dump(mode="json"), sort_keys=True)

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO observation_samples (
                    sample_id, run_id, sample_sequence, epoch_utc, sim_time_ms,
                    captured_at, stored_at, sensor_id, capability, target,
                    observer_pose_json, world_version, status, observation_id,
                    failure_reason, raw_sample_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(stored_sample.sample_id),
                    str(stored_sample.run_id),
                    stored_sample.sample_sequence,
                    _dt_to_iso(stored_sample.epoch_utc),
                    stored_sample.sim_time_ms,
                    _dt_to_iso(stored_sample.captured_at),
                    _dt_to_iso(stored_at),
                    stored_sample.sensor_id,
                    stored_sample.request.capability,
                    stored_sample.request.target,
                    pose_json,
                    stored_sample.world_version,
                    status,
                    observation_id,
                    failure_reason,
                    sample_json,
                ),
            )
            for entity_id, det_json in detections:
                self._conn.execute(
                    """
                    INSERT INTO observation_detections (
                        sample_id, run_id, entity_id, sim_time_ms, sample_sequence, detection_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(stored_sample.sample_id),
                        str(stored_sample.run_id),
                        entity_id,
                        stored_sample.sim_time_ms,
                        stored_sample.sample_sequence,
                        det_json,
                    ),
                )

        return stored_sample

    def get_observation_sample(self, sample_id: UUID) -> StoredObservationSample | None:
        """Retrieve a stored observation sample by its unique sample_id."""
        row = self._conn.execute(
            "SELECT raw_sample_json, stored_at FROM observation_samples WHERE sample_id = ?",
            (str(sample_id),),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_stored_sample(row)

    def query_samples(
        self,
        run_id: UUID,
        capability: str | None = None,
        sensor_id: str | None = None,
        entity_id: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 100,
    ) -> list[StoredObservationSample]:
        """Query observation samples with inclusive simulation-time bounds."""
        if start_ms is not None and start_ms < 0:
            raise ValueError("start_ms must be non-negative")
        if end_ms is not None and end_ms < 0:
            raise ValueError("end_ms must be non-negative")
        if start_ms is not None and end_ms is not None and start_ms > end_ms:
            raise ValueError("start_ms must not exceed end_ms")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

        if entity_id is not None:
            clauses = ["s.run_id = ?", "d.entity_id = ?"]
            params: list[object] = [str(run_id), entity_id]
            if capability is not None:
                clauses.append("s.capability = ?")
                params.append(capability)
            if sensor_id is not None:
                clauses.append("s.sensor_id = ?")
                params.append(sensor_id)
            if start_ms is not None:
                clauses.append("s.sim_time_ms >= ?")
                params.append(start_ms)
            if end_ms is not None:
                clauses.append("s.sim_time_ms <= ?")
                params.append(end_ms)

            sql = f"""
                SELECT s.raw_sample_json, s.stored_at
                FROM observation_samples s
                JOIN observation_detections d ON d.sample_id = s.sample_id
                WHERE {" AND ".join(clauses)}
                ORDER BY s.sim_time_ms ASC, s.sample_sequence ASC, s.sample_id ASC
                LIMIT ?
            """
        else:
            clauses = ["run_id = ?"]
            params = [str(run_id)]
            if capability is not None:
                clauses.append("capability = ?")
                params.append(capability)
            if sensor_id is not None:
                clauses.append("sensor_id = ?")
                params.append(sensor_id)
            if start_ms is not None:
                clauses.append("sim_time_ms >= ?")
                params.append(start_ms)
            if end_ms is not None:
                clauses.append("sim_time_ms <= ?")
                params.append(end_ms)

            sql = f"""
                SELECT raw_sample_json, stored_at FROM observation_samples
                WHERE {" AND ".join(clauses)}
                ORDER BY sim_time_ms ASC, sample_sequence ASC, sample_id ASC
                LIMIT ?
            """
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_stored_sample(r) for r in rows]

    def get_latest_sample(
        self,
        run_id: UUID,
        capability: str,
        sensor_id: str | None = None,
        entity_id: str | None = None,
        start_ms: int | None = None,
        at_sim_time_ms: int | None = None,
    ) -> StoredObservationSample | None:
        """Return the latest sample for capability/source at or before simulation time."""
        if start_ms is not None and start_ms < 0:
            raise ValueError("start_ms must be non-negative")
        if at_sim_time_ms is not None and at_sim_time_ms < 0:
            raise ValueError("at_sim_time_ms must be non-negative")
        if start_ms is not None and at_sim_time_ms is not None and start_ms > at_sim_time_ms:
            raise ValueError("start_ms must not exceed at_sim_time_ms")

        if entity_id is not None:
            clauses = ["s.run_id = ?", "s.capability = ?", "d.entity_id = ?"]
            params: list[object] = [str(run_id), capability, entity_id]
            if sensor_id is not None:
                clauses.append("s.sensor_id = ?")
                params.append(sensor_id)
            if start_ms is not None:
                clauses.append("s.sim_time_ms >= ?")
                params.append(start_ms)
            if at_sim_time_ms is not None:
                clauses.append("s.sim_time_ms <= ?")
                params.append(at_sim_time_ms)

            sql = f"""
                SELECT s.raw_sample_json, s.stored_at
                FROM observation_samples s
                JOIN observation_detections d ON d.sample_id = s.sample_id
                WHERE {" AND ".join(clauses)}
                ORDER BY s.sim_time_ms DESC, s.sample_sequence DESC, s.sample_id DESC
                LIMIT 1
            """
        else:
            clauses = ["run_id = ?", "capability = ?"]
            params = [str(run_id), capability]
            if sensor_id is not None:
                clauses.append("sensor_id = ?")
                params.append(sensor_id)
            if start_ms is not None:
                clauses.append("sim_time_ms >= ?")
                params.append(start_ms)
            if at_sim_time_ms is not None:
                clauses.append("sim_time_ms <= ?")
                params.append(at_sim_time_ms)

            sql = f"""
                SELECT raw_sample_json, stored_at FROM observation_samples
                WHERE {" AND ".join(clauses)}
                ORDER BY sim_time_ms DESC, sample_sequence DESC, sample_id DESC
                LIMIT 1
            """
        row = self._conn.execute(sql, params).fetchone()
        if row is None:
            return None
        return self._row_to_stored_sample(row)

    def get_latest_camera_attempt(
        self, run_id: UUID, entity_id: str, observer_pose: Pose, at_sim_time_ms: int
    ) -> StoredObservationSample | None:
        """Latest broad or target-specific attempt in the sighting's observer context."""
        TemporalObservationQuery(
            run_id=run_id, mode="latest", capability="camera_detect", end_ms=at_sim_time_ms
        )
        row = self._conn.execute(
            """
            SELECT raw_sample_json, stored_at FROM observation_samples
            WHERE run_id = ? AND capability = 'camera_detect'
              AND (target IS NULL OR target = ?) AND sim_time_ms <= ?
              AND json_extract(observer_pose_json, '$.robot_id') = ?
              AND json_extract(observer_pose_json, '$.location') = ?
              AND json_extract(observer_pose_json, '$.frame_of_reference') = ?
            ORDER BY sim_time_ms DESC, sample_sequence DESC, sample_id DESC
            LIMIT 1
            """,
            (
                str(run_id),
                entity_id,
                at_sim_time_ms,
                observer_pose.robot_id,
                observer_pose.location,
                observer_pose.frame_of_reference,
            ),
        ).fetchone()
        return self._row_to_stored_sample(row) if row is not None else None

    def get_last_sighting(
        self,
        run_id: UUID,
        entity_id: str,
        capability: str | None = None,
        sensor_id: str | None = None,
        start_ms: int | None = None,
        at_sim_time_ms: int | None = None,
    ) -> StoredObservationSample | None:
        """Return the latest positive sighting sample for an entity at or before simulation time."""
        if not entity_id:
            raise ValueError("entity_id must be non-empty")
        if start_ms is not None and start_ms < 0:
            raise ValueError("start_ms must be non-negative")
        if at_sim_time_ms is not None and at_sim_time_ms < 0:
            raise ValueError("at_sim_time_ms must be non-negative")
        if start_ms is not None and at_sim_time_ms is not None and start_ms > at_sim_time_ms:
            raise ValueError("start_ms must not exceed at_sim_time_ms")

        clauses = ["d.run_id = ?", "d.entity_id = ?"]
        params: list[object] = [str(run_id), entity_id]
        if capability is not None:
            clauses.append("s.capability = ?")
            params.append(capability)
        if sensor_id is not None:
            clauses.append("s.sensor_id = ?")
            params.append(sensor_id)
        if start_ms is not None:
            clauses.append("d.sim_time_ms >= ?")
            params.append(start_ms)
        if at_sim_time_ms is not None:
            clauses.append("d.sim_time_ms <= ?")
            params.append(at_sim_time_ms)

        sql = f"""
            SELECT s.raw_sample_json, s.stored_at
            FROM observation_detections d
            JOIN observation_samples s ON d.sample_id = s.sample_id
            WHERE {" AND ".join(clauses)}
            ORDER BY d.sim_time_ms DESC, d.sample_sequence DESC, s.sample_id DESC
            LIMIT 1
        """
        row = self._conn.execute(sql, params).fetchone()
        if row is None:
            return None
        return self._row_to_stored_sample(row)

    def get_entity_sighting_history(
        self,
        run_id: UUID,
        entity_id: str,
        capability: str | None = None,
        sensor_id: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 100,
    ) -> list[StoredObservationSample]:
        """Return chronological positive sightings for an entity within time bounds."""
        if not entity_id:
            raise ValueError("entity_id must be non-empty")
        if start_ms is not None and start_ms < 0:
            raise ValueError("start_ms must be non-negative")
        if end_ms is not None and end_ms < 0:
            raise ValueError("end_ms must be non-negative")
        if start_ms is not None and end_ms is not None and start_ms > end_ms:
            raise ValueError("start_ms must not exceed end_ms")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

        clauses = ["d.run_id = ?", "d.entity_id = ?"]
        params: list[object] = [str(run_id), entity_id]
        if capability is not None:
            clauses.append("s.capability = ?")
            params.append(capability)
        if sensor_id is not None:
            clauses.append("s.sensor_id = ?")
            params.append(sensor_id)
        if start_ms is not None:
            clauses.append("d.sim_time_ms >= ?")
            params.append(start_ms)
        if end_ms is not None:
            clauses.append("d.sim_time_ms <= ?")
            params.append(end_ms)

        sql = f"""
            SELECT s.raw_sample_json, s.stored_at
            FROM observation_detections d
            JOIN observation_samples s ON d.sample_id = s.sample_id
            WHERE {" AND ".join(clauses)}
            ORDER BY d.sim_time_ms ASC, d.sample_sequence ASC, s.sample_id ASC
            LIMIT ?
        """
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_stored_sample(r) for r in rows]

    def query_observations(self, query: TemporalObservationQuery) -> list[StoredObservationSample]:
        """Execute a typed TemporalObservationQuery from #21."""
        if query.mode == "samples":
            return self.query_samples(
                run_id=query.run_id,
                capability=query.capability,
                sensor_id=query.sensor_id,
                entity_id=query.entity_id,
                start_ms=query.start_ms,
                end_ms=query.end_ms,
                limit=query.limit,
            )
        if query.mode == "latest":
            if query.capability is None:
                raise ValueError("latest query mode requires capability")
            latest = self.get_latest_sample(
                run_id=query.run_id,
                capability=query.capability,
                sensor_id=query.sensor_id,
                entity_id=query.entity_id,
                start_ms=query.start_ms,
                at_sim_time_ms=query.end_ms,
            )
            return [latest] if latest is not None else []
        if query.mode == "last_seen":
            if query.entity_id is None:
                raise ValueError("last_seen query mode requires entity_id")
            sighting = self.get_last_sighting(
                run_id=query.run_id,
                entity_id=query.entity_id,
                capability=query.capability,
                sensor_id=query.sensor_id,
                start_ms=query.start_ms,
                at_sim_time_ms=query.end_ms,
            )
            return [sighting] if sighting is not None else []
        if query.mode == "history":
            if query.entity_id is None:
                raise ValueError("history query mode requires entity_id")
            return self.get_entity_sighting_history(
                run_id=query.run_id,
                entity_id=query.entity_id,
                capability=query.capability,
                sensor_id=query.sensor_id,
                start_ms=query.start_ms,
                end_ms=query.end_ms,
                limit=query.limit,
            )
        raise ValueError(f"Unknown query mode: {query.mode}")

    def _row_to_stored_sample(self, row: sqlite3.Row) -> StoredObservationSample:
        """Convert an observation_samples sqlite3.Row into a StoredObservationSample contract."""
        data = json.loads(row["raw_sample_json"])
        data["stored_at"] = _iso_to_dt(row["stored_at"])
        return StoredObservationSample.model_validate(data)
