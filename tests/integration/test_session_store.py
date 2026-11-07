"""Compatibility checks for migrating the old shared session database."""

import json
import sqlite3
from contextlib import closing
from uuid import uuid4

from src.web.session_store import SessionStore


def test_legacy_rows_migrate_and_deleted_rows_stay_deleted(tmp_path):
    path = tmp_path / "legacy.sqlite"
    first, second = str(uuid4()), str(uuid4())
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, state TEXT, memory BLOB)")
        db.executemany(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            [
                (first, json.dumps({"history": ["first"]}), b"first image"),
                (second, json.dumps({"history": ["second"]}), b"second image"),
            ],
        )

    store = SessionStore(path)
    assert store.load(first) == ({"history": ["first"]}, b"first image")
    assert store.load(second) == ({"history": ["second"]}, b"second image")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone() == (0,)
    store.delete(first)
    store.close()

    reopened = SessionStore(path)
    assert reopened.load(first) is None
    assert reopened.load(second) == ({"history": ["second"]}, b"second image")
    reopened.close()
