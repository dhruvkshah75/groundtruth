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

        CREATE TABLE IF NOT EXISTS simulation_entities (
            run_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            PRIMARY KEY (run_id, entity_id)
        );

        CREATE TABLE IF NOT EXISTS observation_samples (
            sample_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL CHECK(run_id != ''),
            sample_sequence INTEGER NOT NULL CHECK(sample_sequence >= 0),
            epoch_utc TEXT NOT NULL,
            sim_time_ms INTEGER NOT NULL CHECK(sim_time_ms >= 0),
            captured_at TEXT NOT NULL,
            stored_at TEXT NOT NULL,
            sensor_id TEXT NOT NULL CHECK(sensor_id != ''),
            capability TEXT NOT NULL CHECK(capability != ''),
            target TEXT NULL,
            observer_pose_json TEXT NOT NULL,
            world_version INTEGER NOT NULL CHECK(world_version >= 0),
            status TEXT NOT NULL CHECK(status IN ('success', 'unavailable')),
            observation_id TEXT NULL,
            failure_reason TEXT NULL,
            raw_sample_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS observation_detections (
            sample_id TEXT NOT NULL,
            run_id TEXT NOT NULL CHECK(run_id != ''),
            entity_id TEXT NOT NULL CHECK(entity_id != ''),
            sim_time_ms INTEGER NOT NULL CHECK(sim_time_ms >= 0),
            sample_sequence INTEGER NOT NULL CHECK(sample_sequence >= 0),
            detection_json TEXT NOT NULL,
            PRIMARY KEY (sample_id, entity_id),
            FOREIGN KEY (sample_id) REFERENCES observation_samples(sample_id) ON DELETE CASCADE
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

        CREATE INDEX IF NOT EXISTS idx_facts_sample_id
            ON facts(json_extract(evidence_json, '$.sample_id'));

        CREATE INDEX IF NOT EXISTS idx_obs_run_capability_sim_time
            ON observation_samples(run_id, capability, sim_time_ms, sample_sequence);

        CREATE INDEX IF NOT EXISTS idx_obs_run_sim_time
            ON observation_samples(run_id, sim_time_ms, sample_sequence);

        CREATE INDEX IF NOT EXISTS idx_obs_detections_run_entity_time
            ON observation_detections(run_id, entity_id, sim_time_ms, sample_sequence);
        """
    )
    conn.commit()
