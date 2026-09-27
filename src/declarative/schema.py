"""SQLite schema initializer for the Tier 1 evidence ledger."""

import sqlite3


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Create all Tier 1 tables and indexes if they do not already exist.

    Safe to call multiple times (idempotent). All DDL uses IF NOT EXISTS so
    re-entrant calls on a live database are a no-op.

    Args:
        conn: An open SQLite connection with foreign keys already enabled.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS facts (
            fact_id TEXT PRIMARY KEY,
            version TEXT NOT NULL,
            subject TEXT NOT NULL CHECK(subject != ''),
            predicate TEXT NOT NULL CHECK(predicate != ''),
            object TEXT NOT NULL CHECK(object != ''),
            source_agent TEXT NOT NULL CHECK(source_agent != ''),
            confidence_score REAL NOT NULL
                CHECK(confidence_score >= 0.0 AND confidence_score <= 1.0),
            observed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            context_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            superseded_by TEXT NULL,
            FOREIGN KEY (superseded_by) REFERENCES facts(fact_id),
            CHECK (superseded_by != fact_id)
        );

        CREATE TABLE IF NOT EXISTS audit_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL CHECK(event_type != ''),
            old_fact_id TEXT NOT NULL,
            new_fact_id TEXT NOT NULL,
            reason TEXT NOT NULL CHECK(reason != ''),
            policy_rule TEXT NOT NULL CHECK(policy_rule != ''),
            created_at TEXT NOT NULL,
            FOREIGN KEY (old_fact_id) REFERENCES facts(fact_id),
            FOREIGN KEY (new_fact_id) REFERENCES facts(fact_id)
        );

        CREATE TABLE IF NOT EXISTS aliases (
            alias TEXT NOT NULL,
            canonical_entity_id TEXT NOT NULL,
            UNIQUE(alias, canonical_entity_id)
        );

        CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate_superseded
            ON facts(subject, predicate, superseded_by);

        CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate_observed
            ON facts(subject, predicate, observed_at);

        CREATE INDEX IF NOT EXISTS idx_facts_source_observed
            ON facts(source_agent, observed_at);

        CREATE INDEX IF NOT EXISTS idx_audit_old_fact_id
            ON audit_events(old_fact_id);

        CREATE INDEX IF NOT EXISTS idx_audit_new_fact_id
            ON audit_events(new_fact_id);

        CREATE INDEX IF NOT EXISTS idx_aliases_normalized
            ON aliases(alias);
        """
    )
    conn.commit()
