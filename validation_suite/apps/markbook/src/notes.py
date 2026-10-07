"""Comments on a student's record.

Two kinds live in the `notes` table. Canned feedback is chosen from the department's comment
bank and is meant for the student (visibility 'shared'). The private note is the teacher's
own free text about the student and is for the teacher only (visibility 'teacher'). Which
page may list a row is decided by `visibility`; what a row *is* (and so its badge) is decided
by whether it came from the comment bank.
"""

from __future__ import annotations

MAX_NOTE = 10_000


def badge_key(note) -> str:
    """The vocabulary key of the badge shown with a comment, wherever it is rendered."""
    return "badge.feedback" if note["canned"] else "badge.private"


def student_comments(conn, student_id: str) -> list[dict]:
    """What the student's own page lists: only rows shared with the student."""
    return [dict(r) for r in conn.execute(
        "SELECT id, body, canned FROM notes WHERE student = ? AND visibility = 'shared' ORDER BY id",
        (student_id,))]


def feedback(conn, student_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, body, canned FROM notes WHERE student = ? AND canned IS NOT NULL ORDER BY id",
        (student_id,))]


def give_feedback(conn, student_id: str, canned_id: str, body: str) -> None:
    """A student has at most one comment from the bank; choosing another replaces it."""
    conn.execute("DELETE FROM notes WHERE student = ? AND canned IS NOT NULL", (student_id,))
    conn.execute("INSERT INTO notes (student, body, canned, visibility) VALUES (?, ?, ?, 'shared')",
                 (student_id, body, canned_id))


def private_note(conn, student_id: str) -> dict | None:
    row = conn.execute("SELECT id, body, canned FROM notes WHERE student = ? AND canned IS NULL",
                       (student_id,)).fetchone()
    return dict(row) if row else None


def save_private_note(conn, student_id: str, body: str) -> None:
    """Write the teacher's private note: the first save creates it, later saves edit it."""
    note = private_note(conn, student_id)
    if note is None:
        conn.execute("INSERT INTO notes (student, body, visibility) VALUES (?, ?, 'teacher')",
                     (student_id, body))
    else:  # an edit keeps the note's id, so it keeps its place on the record
        conn.execute("INSERT OR REPLACE INTO notes (id, student, body, visibility) VALUES (?, ?, ?, 'teacher')",
                     (note["id"], student_id, body))
