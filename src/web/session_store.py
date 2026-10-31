"""Durable, local snapshots of independently owned browser chat sessions."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class SessionStore:
    """Store complete chat snapshots atomically in a local SQLite database."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS sessions "
            "(id TEXT PRIMARY KEY, state TEXT NOT NULL, memory BLOB NOT NULL)"
        )
        self._db.commit()

    def save(self, session_id: str, state: dict, memory: bytes) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sessions (id, state, memory) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET state=excluded.state, memory=excluded.memory",
                (session_id, json.dumps(state), memory),
            )

    def load(self, session_id: str) -> tuple[dict, bytes] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT state, memory FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def delete(self, session_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def close(self) -> None:
        with self._lock:
            self._db.close()
