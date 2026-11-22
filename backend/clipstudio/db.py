"""SQLite persistence. Small repository layer over stdlib sqlite3 (no ORM needed at this size)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,          -- url | upload | path
    source_url TEXT,
    source_path TEXT,                   -- absolute path to the video used for processing
    source_owned INTEGER NOT NULL DEFAULT 0,  -- 1 if the app created the file (download/upload)
    fingerprint TEXT,
    duration REAL, width INTEGER, height INTEGER, fps REAL,
    status TEXT NOT NULL DEFAULT 'new', -- new | queued | processing | ready | failed | cancelled
    options TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                 -- process | render
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,               -- queued | running | done | failed | cancelled
    stage TEXT, progress REAL NOT NULL DEFAULT 0, message TEXT,
    stages TEXT NOT NULL DEFAULT '{}',  -- per-stage status/progress JSON
    error TEXT,
    created_at REAL NOT NULL, started_at REAL, finished_at REAL
);
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    start REAL NOT NULL, "end" REAL NOT NULL,
    score REAL NOT NULL,
    analysis TEXT NOT NULL DEFAULT '{}', -- scores, reasons, weaknesses, transcript, words
    title TEXT, hook TEXT, description TEXT, category TEXT,
    meta TEXT NOT NULL DEFAULT '{}',     -- hooks[], hashtags[]
    edit TEXT NOT NULL DEFAULT '{}',     -- aspect, layout, caption preset/overrides, word edits
    status TEXT NOT NULL DEFAULT 'candidate', -- candidate | approved | rejected
    render_status TEXT NOT NULL DEFAULT 'pending', -- pending | rendering | done | failed | stale
    render_path TEXT, thumb_path TEXT, render_error TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_clips_project ON clips(project_id, rank);
"""

JSON_COLS = {"options", "payload", "stages", "analysis", "meta", "edit"}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        d[k] = json.loads(d[k]) if d[k] else {}
    return d


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [_row(r) for r in self._conn.execute(sql, args).fetchall()]  # type: ignore[misc]

    def one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            return _row(self._conn.execute(sql, args).fetchone())

    def execute(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, args).rowcount

    @staticmethod
    def _encode(values: dict[str, Any]) -> dict[str, Any]:
        return {k: (json.dumps(v) if k in JSON_COLS and not isinstance(v, str) else v) for k, v in values.items()}

    def insert(self, table: str, values: dict[str, Any]) -> None:
        v = self._encode(values)
        cols = ", ".join(f'"{c}"' for c in v)
        qs = ", ".join("?" for _ in v)
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", tuple(v.values()))

    def update(self, table: str, row_id: str, values: dict[str, Any]) -> None:
        if table in ("projects", "clips"):
            values = values | {"updated_at": time.time()}
        v = self._encode(values)
        sets = ", ".join(f'"{c}" = ?' for c in v)
        self.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*v.values(), row_id))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
