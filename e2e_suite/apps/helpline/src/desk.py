"""Helpline's care desk: tickets, their conversations, and the rules that move them.

Pure domain code over one SQLite connection (one database file per session; see
`store.py`). Nothing here knows about HTTP or HTML. Every operation either commits a
complete change or raises `Refusal` having written nothing, so a refused action leaves the
committed state byte-for-byte as it was.

The status machine::

    new -> triage -> in_progress -> waiting -> resolved -> closed
           (start work also resumes a waiting ticket)

* Sending a reply to the customer moves a new, triaged or in-progress ticket to
  "waiting on customer".
* Logging a reply the customer gave by phone reopens a waiting or resolved ticket.
* Resolving needs the agent to have the last word in the conversation.
* Only a resolved ticket can be closed. Closed is final.
"""

from __future__ import annotations

import sqlite3

STATUSES = ("new", "triage", "in_progress", "waiting", "resolved", "closed")
OPEN_STATUSES = ("new", "triage", "in_progress", "waiting")
PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
#: Explicit status moves an agent can make with a button, and where each may start from.
MOVES = {"triage": ("new",), "in_progress": ("triage", "waiting")}
ME = "sam"
ONCALL = "kai"
PEOPLE = {"sam": "Sam", "noor": "Noor", "kai": "Kai"}
BODY_LIMIT = 10_000
#: Queue filters as they appear in URLs, and what they select.
STATUS_FILTERS = ("open", "all") + STATUSES
ASSIGNEE_FILTERS = ("any", "none", "noor", "kai")

SCHEMA = """
CREATE TABLE tickets (
    id INTEGER PRIMARY KEY, subject TEXT NOT NULL, customer TEXT NOT NULL, email TEXT, phone TEXT,
    plan TEXT, channel TEXT, priority TEXT NOT NULL, status TEXT NOT NULL,
    assignee TEXT NOT NULL DEFAULT '', merged_into INTEGER, opened TEXT NOT NULL);
CREATE TABLE entries (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL, role TEXT NOT NULL,
    author TEXT NOT NULL, body TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE links (ticket_id INTEGER NOT NULL, other_id INTEGER NOT NULL,
    PRIMARY KEY (ticket_id, other_id));
CREATE TABLE history (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL, at TEXT NOT NULL,
    actor TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
-- Denormalised for the queue's assignee filters (which would otherwise scan every ticket
-- with its conversation joined). Derived data: whoever changes an assignee re-indexes.
CREATE TABLE assignee_index (ticket_id INTEGER PRIMARY KEY, assignee TEXT NOT NULL);
"""


class Refusal(Exception):
    """An action the desk declines. `key` names the vocabulary message shown to the agent."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


# -- building a session's database -----------------------------------------------------------


def install(conn: sqlite3.Connection, seed: dict) -> None:
    """Create the schema and load the seed: the state every new session starts from."""
    conn.executescript(SCHEMA)
    for ticket in seed["tickets"]:
        conn.execute(
            "INSERT INTO tickets (id, subject, customer, email, phone, plan, channel, priority, status,"
            " assignee, merged_into, opened) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
            (ticket["id"], ticket["subject"], ticket["customer"], ticket["email"], ticket["phone"],
             ticket["plan"], ticket["channel"], ticket["priority"], ticket["status"], ticket["assignee"],
             ticket["opened"]))
        _log(conn, ticket["id"], ticket["opened"], ticket["customer"], "created", ticket["channel"])
        for entry in ticket["conversation"]:
            author = ticket["customer"] if entry["role"] == "customer" else entry["author"]
            conn.execute("INSERT INTO entries (ticket_id, role, author, body, at) VALUES (?, ?, ?, ?, ?)",
                         (ticket["id"], entry["role"], author, entry["body"], entry["at"]))
        last = ticket["conversation"][-1]["at"]
        if ticket["assignee"]:
            _log(conn, ticket["id"], last, "noor", "assigned", ticket["assignee"])
        if ticket["status"] != "new":
            _log(conn, ticket["id"], last, "noor", "status", f"new>{ticket['status']}")
    for a, b in seed.get("links", []):
        conn.executemany("INSERT INTO links VALUES (?, ?)", ((a, b), (b, a)))
    refresh_assignee_index(conn, [ticket["id"] for ticket in seed["tickets"]])


def refresh_assignee_index(conn: sqlite3.Connection, ids: list[int]) -> None:
    """Bring the queue's assignee index in line with the tickets themselves."""
    for ticket_id in ids:
        conn.execute("INSERT OR REPLACE INTO assignee_index (ticket_id, assignee)"
                     " SELECT id, assignee FROM tickets WHERE id = ?", (ticket_id,))


# -- reading ---------------------------------------------------------------------------------


def ticket(conn: sqlite3.Connection, ticket_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()


def entries(conn: sqlite3.Connection, ticket_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM entries WHERE ticket_id = ? ORDER BY seq", (ticket_id,)).fetchall()


def latest_role(conn: sqlite3.Connection, ticket_id: int) -> str:
    row = conn.execute("SELECT role FROM entries WHERE ticket_id = ? ORDER BY seq DESC LIMIT 1",
                       (ticket_id,)).fetchone()
    return row["role"] if row else ""


def history(conn: sqlite3.Connection, ticket_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM history WHERE ticket_id = ? ORDER BY seq", (ticket_id,)).fetchall()


def linked(conn: sqlite3.Connection, ticket_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT t.id, t.subject FROM links l JOIN tickets t ON t.id = l.other_id"
                        " WHERE l.ticket_id = ? ORDER BY t.id", (ticket_id,)).fetchall()


def merge_candidates(conn: sqlite3.Connection, ticket_id: int) -> list[sqlite3.Row]:
    """Open tickets that could be merged into this one: the same customer's first."""
    target = ticket(conn, ticket_id)
    marks = ",".join("?" * len(OPEN_STATUSES))
    rows = conn.execute(f"SELECT * FROM tickets WHERE id != ? AND status IN ({marks})",
                        (ticket_id, *OPEN_STATUSES)).fetchall()
    return sorted(rows, key=lambda row: (row["customer"] != target["customer"], row["id"]))


def can_merge_into(row: sqlite3.Row) -> bool:
    return row["status"] in OPEN_STATUSES


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    found = dict(conn.execute("SELECT status, COUNT(*) FROM tickets GROUP BY status").fetchall())
    return {status: found.get(status, 0) for status in STATUSES}


def normalise_filters(status: str, assignee: str) -> tuple[str, str]:
    return (status if status in STATUS_FILTERS else "open",
            assignee if assignee in ASSIGNEE_FILTERS else "any")


def queue(conn: sqlite3.Connection, status: str, assignee: str) -> list[sqlite3.Row]:
    """The queue under a filter, most urgent first. Assignee filters read the index."""
    status, assignee = normalise_filters(status, assignee)
    rows = conn.execute("SELECT * FROM tickets").fetchall()
    if status == "open":
        rows = [row for row in rows if row["status"] in OPEN_STATUSES]
    elif status != "all":
        rows = [row for row in rows if row["status"] == status]
    if assignee != "any":
        code = "" if assignee == "none" else assignee
        wanted = {r[0] for r in conn.execute("SELECT ticket_id FROM assignee_index WHERE assignee = ?",
                                             (code,))}
        rows = [row for row in rows if row["id"] in wanted]
    return sorted(rows, key=lambda row: (PRIORITY_RANK.get(row["priority"], 9), row["id"]))


def committed(conn: sqlite3.Connection) -> dict:
    """The business state a test hook reports: tickets, conversations, links and the audit log."""
    def rows(sql: str) -> list[dict]:
        return [dict(row) for row in conn.execute(sql)]
    return {
        "tickets": rows("SELECT id, status, assignee, priority, merged_into FROM tickets ORDER BY id"),
        "entries": rows("SELECT seq, ticket_id, role, author, body FROM entries ORDER BY seq"),
        "links": rows("SELECT ticket_id, other_id FROM links ORDER BY ticket_id, other_id"),
        "history": rows("SELECT seq, ticket_id, actor, event, detail FROM history ORDER BY seq"),
    }


# -- changing --------------------------------------------------------------------------------


def _log(conn, ticket_id: int, at: str, actor: str, event: str, detail: str = "") -> None:
    conn.execute("INSERT INTO history (ticket_id, at, actor, event, detail) VALUES (?, ?, ?, ?, ?)",
                 (ticket_id, at, actor, event, detail))


def _set_status(conn, row: sqlite3.Row, to: str, now: str) -> None:
    if row["status"] == to:
        return
    conn.execute("UPDATE tickets SET status = ? WHERE id = ?", (to, row["id"]))
    _log(conn, row["id"], now, ME, "status", f"{row['status']}>{to}")


def _open_ticket(conn, ticket_id: int) -> sqlite3.Row:
    row = ticket(conn, ticket_id)
    if row is None:
        raise Refusal("msg.no_such_ticket")
    if row["status"] == "closed":
        raise Refusal("msg.ticket_closed")
    return row


def _clean_body(body: str) -> str:
    if not body.strip():
        raise Refusal("msg.reply_empty")
    if len(body) > BODY_LIMIT:
        raise Refusal("msg.reply_too_long")
    return body  # stored exactly as typed


def send_reply(conn, ticket_id: int, body: str, now: str) -> None:
    """The agent answers the customer; the ball is then in the customer's court."""
    row = _open_ticket(conn, ticket_id)
    text = _clean_body(body)
    conn.execute("INSERT INTO entries (ticket_id, role, author, body, at) VALUES (?, 'agent', ?, ?, ?)",
                 (ticket_id, ME, text, now))
    _log(conn, ticket_id, now, ME, "reply")
    if row["status"] in ("new", "triage", "in_progress"):
        _set_status(conn, row, "waiting", now)


def log_customer_reply(conn, ticket_id: int, body: str, now: str) -> None:
    """The agent records what the customer said on the phone."""
    row = _open_ticket(conn, ticket_id)
    text = _clean_body(body)
    conn.execute("INSERT INTO entries (ticket_id, role, author, body, at) VALUES (?, 'customer', ?, ?, ?)",
                 (ticket_id, row["customer"], text, now))
    _log(conn, ticket_id, now, ME, "customer_reply")
    if row["status"] in ("waiting", "resolved"):  # the customer answered: back to work
        _set_status(conn, row, "in_progress", now)


def move(conn, ticket_id: int, to: str, now: str) -> None:
    """An explicit step along the machine: start triage, start (or resume) work."""
    row = _open_ticket(conn, ticket_id)
    if row["status"] not in MOVES.get(to, ()):
        raise Refusal("msg.bad_transition")
    _set_status(conn, row, to, now)


def resolve(conn, ticket_id: int, now: str) -> None:
    row = _open_ticket(conn, ticket_id)
    if row["status"] not in OPEN_STATUSES:
        raise Refusal("msg.already_resolved")
    if latest_role(conn, ticket_id) != "agent":
        raise Refusal("msg.resolve_needs_reply")
    _set_status(conn, row, "resolved", now)


def close(conn, ticket_id: int, now: str) -> None:
    row = _open_ticket(conn, ticket_id)
    if row["status"] != "resolved":
        raise Refusal("msg.close_needs_resolved")
    _set_status(conn, row, "closed", now)


def _assign(conn, row: sqlite3.Row, who: str, now: str) -> None:
    conn.execute("UPDATE tickets SET assignee = ? WHERE id = ?", (who, row["id"]))
    _log(conn, row["id"], now, ME, "assigned", who)


def hand_off(conn, ticket_id: int, now: str) -> None:
    """The ticket page's hand-off: one ticket to whoever is on call."""
    row = _open_ticket(conn, ticket_id)
    if row["assignee"] == ONCALL:
        raise Refusal("msg.already_oncall")
    _assign(conn, row, ONCALL, now)
    refresh_assignee_index(conn, [ticket_id])


def hand_off_many(conn, ticket_ids: list[int], now: str) -> None:
    """The queue's hand-off, written for several rows at once (one row today)."""
    tickets = [_open_ticket(conn, ticket_id) for ticket_id in ticket_ids]
    if any(row["assignee"] == ONCALL for row in tickets):
        raise Refusal("msg.already_oncall")
    for row in tickets:
        _assign(conn, row, ONCALL, now)
    refresh_assignee_index(conn, [row["id"] for row in tickets])


def merge(conn, target_id: int, source_raw: str, now: str) -> int:
    """Merge another open ticket into this one. Returns the merged ticket's number."""
    target = _open_ticket(conn, target_id)
    if not can_merge_into(target):
        raise Refusal("msg.merge_target_done")
    if not source_raw.strip().isdigit():
        raise Refusal("msg.merge_invalid")
    source = ticket(conn, int(source_raw))
    if source is None or source["id"] == target_id or source["status"] not in OPEN_STATUSES:
        raise Refusal("msg.merge_invalid")
    conn.execute("UPDATE tickets SET status = 'closed', merged_into = ? WHERE id = ?", (target_id, source["id"]))
    _log(conn, source["id"], now, ME, "merged_into", str(target_id))
    _log(conn, target_id, now, ME, "merged_from", str(source["id"]))
    for owner, other in ((target_id, source["id"]), (source["id"], target_id)):  # each lists the other
        conn.execute("INSERT OR IGNORE INTO links VALUES (?, ?)", (owner, other))
    return source["id"]
