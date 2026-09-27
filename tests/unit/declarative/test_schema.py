"""Tests for the Tier 1 schema initializer."""

import sqlite3

import pytest

from src.declarative.schema import initialize_schema


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def test_initialize_schema_is_idempotent() -> None:
    """Calling initialize_schema twice must not raise any exception."""
    conn = _make_conn()
    initialize_schema(conn)
    initialize_schema(conn)  # second call – must be a no-op
    conn.close()


def test_foreign_keys_enabled() -> None:
    """PRAGMA foreign_keys should return 1 after connection setup."""
    conn = _make_conn()
    initialize_schema(conn)
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1
    conn.close()


def test_required_tables_exist() -> None:
    """All three tables must be present after initialization."""
    conn = _make_conn()
    initialize_schema(conn)
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "facts" in tables
    assert "audit_events" in tables
    assert "aliases" in tables
    conn.close()


def test_required_indexes_exist() -> None:
    """All six named indexes must appear in sqlite_master."""
    expected_indexes = {
        "idx_facts_subject_predicate_superseded",
        "idx_facts_subject_predicate_observed",
        "idx_facts_source_observed",
        "idx_audit_old_fact_id",
        "idx_audit_new_fact_id",
        "idx_aliases_normalized",
    }
    conn = _make_conn()
    initialize_schema(conn)
    actual_indexes = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert expected_indexes <= actual_indexes
    conn.close()


def test_confidence_constraint() -> None:
    """Inserting a confidence_score > 1.0 must raise IntegrityError."""
    conn = _make_conn()
    initialize_schema(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO facts (
                fact_id, version, subject, predicate, object,
                source_agent, confidence_score, observed_at, created_at,
                context_json, evidence_json, superseded_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                "f1",
                "v1",
                "route_A",
                "status_is",
                "blocked",
                "sensor_01",
                1.5,  # violates CHECK constraint
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "{}",
                "{}",
            ),
        )
    conn.close()


def test_self_supersession_constraint() -> None:
    """A fact whose superseded_by equals its own fact_id must be rejected."""
    conn = _make_conn()
    initialize_schema(conn)

    # First insert a valid fact
    conn.execute(
        """
        INSERT INTO facts (
            fact_id, version, subject, predicate, object,
            source_agent, confidence_score, observed_at, created_at,
            context_json, evidence_json, superseded_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            "f1",
            "v1",
            "route_A",
            "status_is",
            "clear",
            "sensor_01",
            0.9,
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            "{}",
            "{}",
        ),
    )
    conn.commit()

    # Now try to set superseded_by = fact_id (self-loop)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE facts SET superseded_by = fact_id WHERE fact_id = 'f1'")
        conn.commit()

    conn.close()
