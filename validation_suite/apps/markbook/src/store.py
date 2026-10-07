"""Storage: one SQLite file per browser session, copied from a template built from the seed.

At start-up the seed (`seed/markbook.json`) is written once into `template.sqlite3`. A
browser without a known `wta_sid` cookie gets the next session number and its own copy of
that file, so every session starts from the same markbook and no session can see another.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

SEED = json.loads((Path(__file__).resolve().parent.parent / "seed" / "markbook.json")
                  .read_text(encoding="utf-8"))
SESSION_ID = re.compile(r"mb\d{6}")

SCHEMA = """
CREATE TABLE classes (id TEXT PRIMARY KEY, name TEXT NOT NULL, subject TEXT NOT NULL,
    teacher TEXT NOT NULL, term TEXT NOT NULL);
CREATE TABLE students (id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL);
CREATE TABLE assessments (id TEXT PRIMARY KEY, title TEXT NOT NULL, max_marks INTEGER NOT NULL,
    weight INTEGER NOT NULL, published INTEGER NOT NULL, position INTEGER NOT NULL);
CREATE TABLE marks (student TEXT NOT NULL REFERENCES students (id),
    assessment TEXT NOT NULL REFERENCES assessments (id), score INTEGER NOT NULL,
    PRIMARY KEY (student, assessment));
CREATE TABLE comment_bank (id TEXT PRIMARY KEY, body TEXT NOT NULL, position INTEGER NOT NULL);
CREATE TABLE notes (id INTEGER PRIMARY KEY, student TEXT NOT NULL REFERENCES students (id),
    body TEXT NOT NULL, canned TEXT REFERENCES comment_bank (id),
    visibility TEXT NOT NULL DEFAULT 'shared');
CREATE TABLE flash (slot TEXT PRIMARY KEY, message TEXT NOT NULL, tone TEXT NOT NULL);
"""


def _build_template(path: Path) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        c = SEED["class"]
        conn.execute("INSERT INTO classes VALUES (?, ?, ?, ?, ?)",
                     (c["id"], c["name"], c["subject"], c["teacher"], c["term"]))
        for position, a in enumerate(SEED["assessments"]):
            conn.execute("INSERT INTO assessments VALUES (?, ?, ?, ?, ?, ?)",
                         (a["id"], a["title"], a["max_marks"], a["weight"], int(a["published"]), position))
        for position, s in enumerate(SEED["students"]):
            conn.execute("INSERT INTO students VALUES (?, ?, ?)", (s["id"], s["name"], position))
            for assessment, score in s["marks"].items():
                conn.execute("INSERT INTO marks VALUES (?, ?, ?)", (s["id"], assessment, score))
        bank = {entry["id"]: entry["text"] for entry in SEED["comment_bank"]}
        for position, (ident, body) in enumerate(bank.items()):
            conn.execute("INSERT INTO comment_bank VALUES (?, ?, ?)", (ident, body, position))
        for note in SEED["notes"]:
            if note.get("canned"):
                conn.execute("INSERT INTO notes (student, body, canned, visibility) VALUES (?, ?, ?, 'shared')",
                             (note["student"], bank[note["canned"]], note["canned"]))
            else:
                conn.execute("INSERT INTO notes (student, body, visibility) VALUES (?, ?, 'teacher')",
                             (note["student"], note["body"]))
        conn.commit()
    finally:
        conn.close()


class Sessions:
    """The session files under `root`. Not thread-safe: the server serialises requests."""

    def __init__(self, root: Path) -> None:
        self.folder = root / "sessions"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.template = root / "template.sqlite3"
        _build_template(self.template)
        taken = [int(p.stem[2:]) for p in self.folder.glob("mb*.sqlite3") if SESSION_ID.fullmatch(p.stem)]
        self._next = max(taken, default=0) + 1

    def _path(self, sid: str) -> Path:
        return self.folder / f"{sid}.sqlite3"

    def open(self, sid: str | None) -> tuple[str, sqlite3.Connection, bool]:
        """(session id, connection, whether the session was created by this call)."""
        created = False
        if not (sid and SESSION_ID.fullmatch(sid) and self._path(sid).is_file()):
            sid = f"mb{self._next:06d}"
            self._next += 1
            shutil.copyfile(self.template, self._path(sid))
            created = True
        conn = sqlite3.connect(self._path(sid), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return sid, conn, created


# -- reading ----------------------------------------------------------------------------------


def the_class(conn, class_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
    return dict(row) if row else None


def classes(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM classes ORDER BY id")]


def students(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, name FROM students ORDER BY position")]


def student(conn, student_id: str) -> dict | None:
    row = conn.execute("SELECT id, name FROM students WHERE id = ?", (student_id,)).fetchone()
    return dict(row) if row else None


def assessments(conn) -> list[dict]:
    rows = conn.execute("SELECT id, title, max_marks, weight, published FROM assessments ORDER BY position")
    return [{**dict(r), "published": bool(r["published"])} for r in rows]


def assessment(conn, assessment_id: str) -> dict | None:
    return next((a for a in assessments(conn) if a["id"] == assessment_id), None)


def scores(conn) -> dict[str, dict[str, int]]:
    """Every mark, as {student: {assessment: score}}; a missing key is a blank cell."""
    table: dict[str, dict[str, int]] = {s["id"]: {} for s in students(conn)}
    for row in conn.execute("SELECT student, assessment, score FROM marks"):
        table[row["student"]][row["assessment"]] = row["score"]
    return table


def highest_mark(conn, assessment_id: str, published_only: bool = False) -> int | None:
    """The highest mark anyone has in an assessment, or None while nobody has one.

    `published_only` limits it to marks students can see (the student view's "highest in
    class" figure); a draft assessment then has no visible marks at all.
    """
    sql = ("SELECT MAX(m.score) FROM marks m JOIN assessments a ON a.id = m.assessment "
           "WHERE m.assessment = ?")
    if published_only:
        sql += " AND a.published = 1"
    return conn.execute(sql, (assessment_id,)).fetchone()[0]


def comment_bank(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, body FROM comment_bank ORDER BY position")]


# -- writing ----------------------------------------------------------------------------------


def set_mark(conn, student_id: str, assessment_id: str, score: int | None) -> None:
    if score is None:
        conn.execute("DELETE FROM marks WHERE student = ? AND assessment = ?", (student_id, assessment_id))
    else:
        conn.execute("INSERT INTO marks (student, assessment, score) VALUES (?, ?, ?) "
                     "ON CONFLICT (student, assessment) DO UPDATE SET score = excluded.score",
                     (student_id, assessment_id, score))


def set_maximum(conn, assessment_id: str, maximum: int) -> None:
    conn.execute("UPDATE assessments SET max_marks = ? WHERE id = ?", (maximum, assessment_id))


def set_published(conn, assessment_id: str, published: bool) -> None:
    conn.execute("UPDATE assessments SET published = ? WHERE id = ?", (int(published), assessment_id))


def push_flash(conn, slot: str, message: str, tone: str = "ok") -> None:
    """A one-time message for the next page that shows `slot`."""
    conn.execute("INSERT OR REPLACE INTO flash (slot, message, tone) VALUES (?, ?, ?)", (slot, message, tone))


def take_flash(conn, slot: str) -> dict | None:
    row = conn.execute("SELECT message, tone FROM flash WHERE slot = ?", (slot,)).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM flash WHERE slot = ?", (slot,))
    return dict(row)


def committed(conn) -> dict:
    """Business state for the test hook: marks, assessment settings and comments.
    One-time messages are page state and deliberately left out."""
    return {
        "assessments": [{"id": a["id"], "max_marks": a["max_marks"], "published": a["published"]}
                        for a in assessments(conn)],
        "marks": [dict(r) for r in conn.execute(
            "SELECT student, assessment, score FROM marks ORDER BY student, assessment")],
        "notes": [dict(r) for r in conn.execute(
            "SELECT student, canned, body, visibility FROM notes ORDER BY student, id")],
    }
