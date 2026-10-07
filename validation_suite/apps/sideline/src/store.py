"""Sideline's storage: one SQLite database whose rows are partitioned by browser session.

Every request runs inside one transaction under a single process-wide lock, so a page is
always rendered from a consistent set of rows and a refused action writes nothing. A new
browser session is numbered from a counter (never from time or chance) and receives its
own copy of the seeded fixtures; sessions never see each other's rows.

Only `results` is business state. The one-time flash message a page shows after a
post/redirect/get is kept in its own table and is not part of the committed snapshot.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS results (
    sid TEXT NOT NULL,
    id TEXT NOT NULL,
    number INTEGER NOT NULL,
    played_on TEXT NOT NULL,
    home TEXT NOT NULL,
    away TEXT NOT NULL,
    home_goals INTEGER,
    away_goals INTEGER,
    status TEXT NOT NULL,
    home_ok INTEGER NOT NULL DEFAULT 0,
    away_ok INTEGER NOT NULL DEFAULT 0,
    disputed_by TEXT,
    dispute_reason TEXT,
    PRIMARY KEY (sid, id)
);
CREATE TABLE IF NOT EXISTS flashes (sid TEXT PRIMARY KEY, message TEXT NOT NULL);
"""

_SID_SHAPE = re.compile(r"sl[0-9]{6}")


class Store:
    def __init__(self, path: Path, seed: dict) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._fixtures = [self._seed_row(fixture) for fixture in seed["fixtures"]]

    @staticmethod
    def _seed_row(fixture: dict) -> tuple:
        score = fixture.get("score")
        confirmed = 1 if fixture["status"] == "confirmed" else 0
        return (fixture["id"], int(fixture["id"][1:]), fixture["played_on"], fixture["home"],
                fixture["away"], score[0] if score else None, score[1] if score else None,
                fixture["status"], confirmed, confirmed)

    @contextmanager
    def transaction(self):
        """The database, inside one transaction: committed on success, rolled back on error."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def session(self, conn: sqlite3.Connection, sid: str) -> str:
        """`sid` if it names a live session, otherwise a new session seeded from scratch."""
        if _SID_SHAPE.fullmatch(sid or "") and conn.execute(
                "SELECT 1 FROM sessions WHERE sid = ?", (sid,)).fetchone():
            return sid
        row = conn.execute("SELECT value FROM counters WHERE name = 'session'").fetchone()
        number = (row["value"] if row else 0) + 1
        conn.execute("INSERT OR REPLACE INTO counters (name, value) VALUES ('session', ?)", (number,))
        fresh = f"sl{number:06d}"
        conn.execute("INSERT INTO sessions (sid) VALUES (?)", (fresh,))
        conn.executemany(
            "INSERT INTO results (sid, id, number, played_on, home, away, home_goals, away_goals,"
            " status, home_ok, away_ok) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(fresh, *fixture) for fixture in self._fixtures])
        return fresh


def put_flash(conn: sqlite3.Connection, sid: str, message: str) -> None:
    conn.execute("INSERT OR REPLACE INTO flashes (sid, message) VALUES (?, ?)", (sid, message))


def take_flash(conn: sqlite3.Connection, sid: str) -> str:
    row = conn.execute("SELECT message FROM flashes WHERE sid = ?", (sid,)).fetchone()
    if row is None:
        return ""
    conn.execute("DELETE FROM flashes WHERE sid = ?", (sid,))
    return row["message"]


def committed(conn: sqlite3.Connection, sid: str) -> dict:
    """The session's business state: every fixture's score, status and confirmations."""
    rows = conn.execute(
        "SELECT id, home, away, home_goals, away_goals, status, home_ok, away_ok, disputed_by,"
        " dispute_reason FROM results WHERE sid = ? ORDER BY number", (sid,)).fetchall()
    return {"results": [dict(row) for row in rows]}
