"""Durable, local snapshots of independently owned browser chat sessions."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID

_SCHEMA = """CREATE TABLE IF NOT EXISTS sessions
    (id TEXT PRIMARY KEY, state TEXT NOT NULL, memory BLOB NOT NULL)"""
_UPSERT = """INSERT INTO sessions (id, state, memory) VALUES (?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET state=excluded.state, memory=excluded.memory"""


class SessionStore:
    """Commit a state and ledger together in a separate SQLite file per session."""

    def __init__(self, path: Path) -> None:
        self._directory = path.with_name(f"{path.name}.sessions")
        self._directory.mkdir(parents=True, exist_ok=True)
        self._closed = False
        if path.exists():
            self._migrate(path)

    def _path(self, session_id: str) -> Path:
        if self._closed:
            raise RuntimeError("Session store is closed.")
        return self._directory / f"{UUID(session_id)}.sqlite"

    def _migrate(self, path: Path) -> None:
        # Serialize migrations at startup. The source rows remain until every
        # destination transaction commits. A retry preserves already copied rows.
        with closing(sqlite3.connect(path)) as legacy, legacy:
            legacy.execute("BEGIN IMMEDIATE")
            legacy.execute(_SCHEMA)
            for session_id, state, memory in legacy.execute(
                "SELECT id, state, memory FROM sessions"
            ):
                with closing(sqlite3.connect(self._path(session_id))) as db, db:
                    db.execute(_SCHEMA)
                    db.execute(
                        "INSERT INTO sessions (id, state, memory) VALUES (?, ?, ?) "
                        "ON CONFLICT(id) DO NOTHING",
                        (session_id, state, memory),
                    )
            legacy.execute("DELETE FROM sessions")

    def save(self, session_id: str, state: dict, memory: bytes) -> None:
        payload = json.dumps(state)
        with closing(sqlite3.connect(self._path(session_id))) as db, db:
            db.execute(_SCHEMA)
            db.execute(_UPSERT, (session_id, payload, memory))

    def load(self, session_id: str) -> tuple[dict, bytes] | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        with closing(sqlite3.connect(path)) as db:
            row = db.execute(
                "SELECT state, memory FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        if path.exists():
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def close(self) -> None:
        self._closed = True
