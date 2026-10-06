"""Session storage: one SQLite database file per browser session.

A new session gets a copy of a template database built once from the seed at start-up
(SQLite's online backup API, so the copy is consistent and fast). Session ids come from a
counter in a small registry database, never from time or chance, and each session has its
own lock so concurrent requests from one browser are applied one at a time while other
sessions proceed in parallel.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import threading
from pathlib import Path

import desk

_SID = re.compile(r"h\d{6}")


class SessionStore:
    def __init__(self, data_dir: Path, seed: dict) -> None:
        self.root = Path(data_dir)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir(parents=True, exist_ok=True)
        self._registry = self.root / "registry.sqlite3"
        self._template = self.root / "template.sqlite3"
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        with contextlib.closing(sqlite3.connect(self._registry)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS counter (name TEXT PRIMARY KEY, value INTEGER NOT NULL)")
            conn.commit()
        self._template.unlink(missing_ok=True)
        with contextlib.closing(sqlite3.connect(self._template)) as conn:
            desk.install(conn, seed)
            conn.commit()

    def _path(self, sid: str) -> Path:
        return self.sessions / f"{sid}.sqlite3"

    def known(self, sid: str) -> bool:
        return bool(_SID.fullmatch(sid or "")) and self._path(sid).is_file()

    def create(self) -> str:
        with self._guard, contextlib.closing(sqlite3.connect(self._registry)) as registry:
            row = registry.execute("SELECT value FROM counter WHERE name = 'session'").fetchone()
            number = (row[0] if row else 0) + 1
            registry.execute("INSERT OR REPLACE INTO counter VALUES ('session', ?)", (number,))
            registry.commit()
            sid = f"h{number:06d}"
            with contextlib.closing(sqlite3.connect(self._template)) as source, \
                    contextlib.closing(sqlite3.connect(self._path(sid))) as target:
                source.backup(target)
        return sid

    def _lock(self, sid: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(sid, threading.Lock())

    @contextlib.contextmanager
    def open(self, sid: str):
        """A connection to this session's database, committed on success, rolled back on error."""
        with self._lock(sid):
            conn = sqlite3.connect(self._path(sid), timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()
