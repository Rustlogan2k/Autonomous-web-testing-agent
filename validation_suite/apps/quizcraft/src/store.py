"""Quizcraft's storage: an append-only table of saved survey revisions per browser session.

One SQLite database under WTA_DATA_DIR. Every request runs inside one transaction under a
process-wide lock, so a save either appends exactly one revision or nothing. A new session is
numbered from a counter (never from time or chance) and starts with revision 1, the seeded
survey. Revisions are never updated or deleted: saving appends revision n+1.

The working copy being edited and its undo/redo history live only in the page's memory;
nothing about them reaches the server until a save.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from survey import canonical

SCHEMA = """
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS revisions (
    sid TEXT NOT NULL,
    number INTEGER NOT NULL,
    saved_at TEXT NOT NULL,
    document TEXT NOT NULL,
    PRIMARY KEY (sid, number)
);
"""

_SID_SHAPE = re.compile(r"qc[0-9]{6}")


class Store:
    def __init__(self, path: Path, seed_document: dict, seed_saved_at: str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._seed = (seed_saved_at, canonical(seed_document))

    @contextmanager
    def transaction(self):
        """The database inside one transaction: committed on success, rolled back on error."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def session(self, conn: sqlite3.Connection, sid: str) -> str:
        """`sid` if it names a live session, otherwise a new session holding revision 1."""
        if _SID_SHAPE.fullmatch(sid or "") and conn.execute(
                "SELECT 1 FROM sessions WHERE sid = ?", (sid,)).fetchone():
            return sid
        row = conn.execute("SELECT value FROM counters WHERE name = 'session'").fetchone()
        number = (row["value"] if row else 0) + 1
        conn.execute("INSERT OR REPLACE INTO counters (name, value) VALUES ('session', ?)", (number,))
        fresh = f"qc{number:06d}"
        conn.execute("INSERT INTO sessions (sid) VALUES (?)", (fresh,))
        conn.execute("INSERT INTO revisions (sid, number, saved_at, document) VALUES (?, 1, ?, ?)",
                     (fresh, *self._seed))
        return fresh


def latest(conn: sqlite3.Connection, sid: str) -> tuple[int, dict, str]:
    """(number, document, canonical JSON) of the session's newest revision."""
    row = conn.execute("SELECT number, document FROM revisions WHERE sid = ? ORDER BY number DESC LIMIT 1",
                       (sid,)).fetchone()
    return row["number"], json.loads(row["document"]), row["document"]


def append(conn: sqlite3.Connection, sid: str, document: dict, saved_at: str) -> int:
    """Store `document` as the next revision and return its number."""
    number = latest(conn, sid)[0] + 1
    conn.execute("INSERT INTO revisions (sid, number, saved_at, document) VALUES (?, ?, ?, ?)",
                 (sid, number, saved_at, canonical(document)))
    return number


def history(conn: sqlite3.Connection, sid: str) -> list[dict]:
    """Every revision of the session, newest first."""
    rows = conn.execute("SELECT number, saved_at, document FROM revisions WHERE sid = ? ORDER BY number DESC",
                        (sid,)).fetchall()
    return [{"number": row["number"], "saved_at": row["saved_at"], "document": json.loads(row["document"])}
            for row in rows]


def committed(conn: sqlite3.Connection, sid: str) -> dict:
    """The session's business state: its saved revisions, oldest first."""
    return {"revisions": list(reversed(history(conn, sid)))}
