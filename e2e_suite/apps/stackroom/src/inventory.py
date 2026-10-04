"""Stackroom's inventory domain: items, the grid's server-side query, and the audit journal.

Everything here takes an open SQLite connection and a session id; the HTTP layer owns
transactions. Rows of every table carry the session id, so each browser session works on
its own copy of the stockroom, seeded from seed/stockroom.json on first contact.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

SEED = json.loads((Path(__file__).resolve().parent.parent / "seed" / "stockroom.json")
                  .read_text(encoding="utf-8"))
PAGE_SIZE = int(SEED["page_size"])
CATEGORIES = tuple(sorted({entry["category"] for entry in SEED["items"]}))
SORT_KEYS = ("sku", "name", "stock")
MAX_LEVEL = 999_999_999
_LEVEL = re.compile(r"\d{1,9}")

TABLES = """
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS items (
    sid TEXT NOT NULL, sku TEXT NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL,
    location TEXT NOT NULL, stock INTEGER NOT NULL, status TEXT NOT NULL,
    PRIMARY KEY (sid, sku));
CREATE TABLE IF NOT EXISTS journal (
    sid TEXT NOT NULL, seq INTEGER NOT NULL, stamp TEXT NOT NULL, sku TEXT NOT NULL,
    kind TEXT NOT NULL, before TEXT NOT NULL, after TEXT NOT NULL, origin TEXT NOT NULL,
    PRIMARY KEY (sid, seq));
CREATE TABLE IF NOT EXISTS notices (sid TEXT PRIMARY KEY, sku TEXT NOT NULL);
"""


class Rejected(Exception):
    """A request the domain refuses; carries the HTTP status and the offending field."""

    def __init__(self, status: int, message: str, field: str = "") -> None:
        super().__init__(message)
        self.status, self.message, self.field = status, message, field


def bump(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT INTO counters (name, value) VALUES (?, 1) "
                 "ON CONFLICT(name) DO UPDATE SET value = value + 1", (name,))
    return conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()[0]


def open_session(conn: sqlite3.Connection, sid: str) -> None:
    """Create session `sid` and copy the seed stockroom into it."""
    conn.execute("INSERT INTO sessions (sid) VALUES (?)", (sid,))
    conn.executemany(
        "INSERT INTO items (sid, sku, name, category, location, stock, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(sid, e["sku"], e["name"], e["category"], e["location"], e["stock"], e["status"])
         for e in SEED["items"]])
    for entry in SEED["journal"]:
        _journal(conn, sid, entry["stamp"], entry["sku"], entry["kind"], entry["before"],
                 entry["after"], entry["origin"])


def session_exists(conn: sqlite3.Connection, sid: str) -> bool:
    return conn.execute("SELECT 1 FROM sessions WHERE sid = ?", (sid,)).fetchone() is not None


def parse_level(raw: object, field: str) -> int:
    """A stock level as typed: a whole number of units, 0 to 999,999,999."""
    text = str(raw if raw is not None else "").strip()
    if not _LEVEL.fullmatch(text):
        raise Rejected(422, "Use a whole number from 0 to 999999999.", field)
    return int(text)


# -- the grid's view of the items -----------------------------------------------------------


@dataclass(frozen=True)
class View:
    """What the stock grid is showing: a filter and an order (paging is applied on top)."""

    category: str = "all"
    q: str = ""
    sort: str = "sku"
    direction: str = "asc"

    @classmethod
    def read(cls, source: dict) -> "View":
        """Build a view from query parameters or a JSON object, normalising what is unknown."""
        view = cls(category=str(source.get("category") or "all"), q=str(source.get("q") or ""),
                   sort=str(source.get("sort") or "sku"), direction=str(source.get("dir") or "asc"))
        if view.category not in CATEGORIES:
            view = replace(view, category="all")
        if view.sort not in SORT_KEYS:
            view = replace(view, sort="sku")
        if view.direction not in ("asc", "desc"):
            view = replace(view, direction="asc")
        return view

    def as_query(self) -> dict:
        return {"category": self.category, "q": self.q, "sort": self.sort, "dir": self.direction}


def _item(row: sqlite3.Row) -> dict:
    return {"sku": row["sku"], "name": row["name"], "category": row["category"],
            "location": row["location"], "stock": row["stock"], "status": row["status"]}


def matching_items(conn: sqlite3.Connection, sid: str, view: View) -> list[dict]:
    """Every item in `view`, filtered and ordered (ties always broken by SKU)."""
    items = [_item(r) for r in conn.execute("SELECT * FROM items WHERE sid = ? ORDER BY sku", (sid,))]
    if view.category != "all":
        items = [i for i in items if i["category"] == view.category]
    needle = view.q.strip().lower()
    if needle:
        items = [i for i in items if needle in i["sku"].lower() or needle in i["name"].lower()]
    if view.sort == "name":
        items.sort(key=lambda i: i["name"].lower(), reverse=view.direction == "desc")
    elif view.sort == "stock":
        items.sort(key=lambda i: i["stock"], reverse=view.direction == "desc")
    elif view.direction == "desc":
        items.reverse()
    return items


def listing(conn: sqlite3.Connection, sid: str, view: View, page: int) -> dict:
    """One page of `view`. The page asked for is reported as asked: a page past the end is
    simply empty, and keeping the page inside 1..pages is the client's job."""
    items = matching_items(conn, sid, view)
    pages = max(1, math.ceil(len(items) / PAGE_SIZE))
    first = (page - 1) * PAGE_SIZE
    return {"view": view.as_query(), "page": page, "pages": pages, "page_size": PAGE_SIZE,
            "total": len(items), "first": first, "rows": items[first:first + PAGE_SIZE],
            "categories": list(CATEGORIES)}


def find_item(conn: sqlite3.Connection, sid: str, sku: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM items WHERE sid = ? AND sku = ?", (sid, sku)).fetchone()
    return None if row is None else _item(row)


# -- changes, and the journal that records them ---------------------------------------------


def _journal(conn, sid: str, stamp: str, sku: str, kind: str, before, after, origin: str) -> None:
    conn.execute("INSERT INTO journal (sid, seq, stamp, sku, kind, before, after, origin) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (sid, bump(conn, f"journal:{sid}"), stamp, sku, kind, str(before), str(after), origin))


def _write_stock(conn, sid: str, sku: str, level: int) -> int:
    before = conn.execute("SELECT stock FROM items WHERE sid = ? AND sku = ?", (sid, sku)).fetchone()[0]
    conn.execute("UPDATE items SET stock = ? WHERE sid = ? AND sku = ?", (level, sid, sku))
    return before


def save_inline(conn, sid: str, stamp: str, sku: str, level: int) -> dict:
    """The grid's inline stock cell. Every save is journalled, even an unchanged level,
    so the log shows each value the stockroom was set to."""
    if find_item(conn, sid, sku) is None:
        raise Rejected(404, "No item with that SKU.")
    before = _write_stock(conn, sid, sku, level)
    _journal(conn, sid, stamp, sku, "stock", before, level, "inline")  # audited like every other change
    return find_item(conn, sid, sku)


@dataclass(frozen=True)
class RowRef:
    """How the grid's Adjust link points at a row: the item's SKU, plus where the row sat
    (`at`, 0-based) in the view the user was looking at, so Discard can return there."""

    sku: str
    at: int
    view: View

    @classmethod
    def read(cls, body: dict) -> "RowRef":
        at = body.get("at")
        if not isinstance(at, int) or isinstance(at, bool) or at < 0:
            raise Rejected(422, "The row position is missing.", "at")
        view = body.get("view")
        return cls(sku=str(body.get("sku") or ""), at=at, view=View.read(view if isinstance(view, dict) else {}))


def adjustment_target(conn, sid: str, ref: RowRef) -> Optional[dict]:
    """The item an adjustment is committed to: the one the adjust page was opened for."""
    return find_item(conn, sid, ref.sku)  # the SKU names the item; `at` is only the way back


def commit_adjustment(conn, sid: str, stamp: str, ref: RowRef, level: int) -> dict:
    item = adjustment_target(conn, sid, ref)
    if item is None:
        raise Rejected(404, "No item with that SKU.")
    before = _write_stock(conn, sid, item["sku"], level)
    _journal(conn, sid, stamp, item["sku"], "stock", before, level, "adjust")
    conn.execute("INSERT INTO notices (sid, sku) VALUES (?, ?) "
                 "ON CONFLICT(sid) DO UPDATE SET sku = excluded.sku", (sid, item["sku"]))
    return find_item(conn, sid, item["sku"])


def take_notice(conn, sid: str) -> Optional[dict]:
    """The one-time 'saved' notice left by the last committed adjustment, consumed on read."""
    row = conn.execute("SELECT sku FROM notices WHERE sid = ?", (sid,)).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM notices WHERE sid = ?", (sid,))
    return {"sku": row["sku"]}


BULK_ACTIONS = ("archive", "restock")
RESTOCK_STEP = 10


def bulk(conn, sid: str, stamp: str, action: str, skus: list) -> int:
    """Apply `action` to the selected SKUs; returns how many items actually changed.
    Nothing selected changes nothing."""
    if action not in BULK_ACTIONS:
        raise Rejected(422, "Choose what to do with the selected rows.", "action")
    if not isinstance(skus, list) or not all(isinstance(s, str) for s in skus):
        raise Rejected(422, "The selection is not a list of SKUs.", "skus")
    changed = 0
    for sku in sorted(set(skus)):
        item = find_item(conn, sid, sku)
        if item is None:
            raise Rejected(404, f"No item {sku}.")
        if action == "archive":
            if item["status"] == "archived":
                continue
            conn.execute("UPDATE items SET status = 'archived' WHERE sid = ? AND sku = ?", (sid, sku))
            _journal(conn, sid, stamp, sku, "archive", "active", "archived", "bulk")
        else:
            level = min(MAX_LEVEL, item["stock"] + RESTOCK_STEP)
            _write_stock(conn, sid, sku, level)
            _journal(conn, sid, stamp, sku, "stock", item["stock"], level, "bulk")
        changed += 1
    return changed


def journal(conn, sid: str) -> list[dict]:
    """The audit log, newest first."""
    return [{"stamp": r["stamp"], "sku": r["sku"], "kind": r["kind"], "before": r["before"],
             "after": r["after"], "origin": r["origin"]}
            for r in conn.execute("SELECT * FROM journal WHERE sid = ? ORDER BY seq DESC", (sid,))]


def committed(conn, sid: str) -> dict:
    """Business state only: stock levels, statuses and the journal (not notices or UI)."""
    return {
        "items": [[r["sku"], r["stock"], r["status"]] for r in conn.execute(
            "SELECT sku, stock, status FROM items WHERE sid = ? ORDER BY sku", (sid,))],
        "journal": [[r["sku"], r["kind"], r["before"], r["after"], r["origin"]] for r in conn.execute(
            "SELECT * FROM journal WHERE sid = ? ORDER BY seq", (sid,))],
    }
