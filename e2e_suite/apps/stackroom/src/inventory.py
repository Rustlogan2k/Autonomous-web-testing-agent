"""Stackroom's inventory domain: items, the grid's server-side query, and the audit journal.

Everything here takes a `Db` whose transaction is already open on the session's own schema
(stockdb.Store.visit); the HTTP layer owns transactions and sessions. Every ORDER BY ends in
a unique key and compares text byte-wise (COLLATE "C"), so a page of the grid is the same
on every run and every machine.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, replace
from typing import Optional

from stockdb import SEED, Db

PAGE_SIZE = int(SEED["page_size"])
CATEGORIES = tuple(c["name"] for c in sorted(SEED["categories"], key=lambda c: c["position"]))
SORT_KEYS = ("sku", "name", "stock")
MAX_LEVEL = 999_999_999
_LEVEL = re.compile(r"\d{1,9}")
#: The grid's orders. Ties are broken by SKU ascending in either direction, as the stock
#: controllers have always read the list.
ORDERS = {
    ("sku", "asc"): 'i.sku COLLATE "C" ASC',
    ("sku", "desc"): 'i.sku COLLATE "C" DESC',
    ("name", "asc"): 'lower(i.name) COLLATE "C" ASC, i.sku COLLATE "C" ASC',
    ("name", "desc"): 'lower(i.name) COLLATE "C" DESC, i.sku COLLATE "C" ASC',
    ("stock", "asc"): 'i.stock ASC, i.sku COLLATE "C" ASC',
    ("stock", "desc"): 'i.stock DESC, i.sku COLLATE "C" ASC',
}
_ROWS = ("SELECT i.sku, i.name, c.name AS category, l.label AS location, i.stock, i.status "
         "FROM items i JOIN categories c ON c.id = i.category_id "
         "JOIN locations l ON l.code = i.location_code")
_FILTER = ("WHERE (:category = 'all' OR c.name = :category) AND (:q = '' "
           "OR strpos(lower(i.sku), :q) > 0 OR strpos(lower(i.name), :q) > 0)")


class Rejected(Exception):
    """A request the domain refuses; carries the HTTP status and the offending field."""

    def __init__(self, status: int, message: str, field: str = "") -> None:
        super().__init__(message)
        self.status, self.message, self.field = status, message, field


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

    def params(self) -> dict:
        return {"category": self.category, "q": self.q.strip().lower()}


def count_items(db: Db, view: View) -> int:
    return int(db.value("SELECT count(*) FROM items i JOIN categories c ON c.id = i.category_id "
                        f"{_FILTER}", **view.params()))


def page_of_items(db: Db, view: View, offset: int, limit: int) -> list[dict]:
    """`limit` items of `view` from position `offset` (0-based), filtered and ordered."""
    return db.all(f"{_ROWS} {_FILTER} ORDER BY {ORDERS[(view.sort, view.direction)]} "
                  "LIMIT :limit OFFSET :offset", limit=limit, offset=offset, **view.params())


def listing(db: Db, view: View, page: int) -> dict:
    """One page of `view`. The page asked for is reported as asked: a page past the end is
    simply empty, and keeping the page inside 1..pages is the client's job."""
    total = count_items(db, view)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    first = (page - 1) * PAGE_SIZE
    return {"view": view.as_query(), "page": page, "pages": pages, "page_size": PAGE_SIZE,
            "total": total, "first": first, "rows": page_of_items(db, view, first, PAGE_SIZE),
            "categories": list(CATEGORIES)}


def find_item(db: Db, sku: str) -> Optional[dict]:
    return db.one(f"{_ROWS} WHERE i.sku = :sku", sku=sku)


def item_details(db: Db, sku: str) -> Optional[dict]:
    """An item with what its page adds: the supplier, the reorder level, what is on order
    and the latest changes."""
    item = db.one(
        "SELECT i.sku, i.name, c.name AS category, l.label AS location, i.stock, i.status, "
        "i.reorder_level, s.name AS supplier, s.lead_days FROM items i "
        "JOIN categories c ON c.id = i.category_id JOIN locations l ON l.code = i.location_code "
        "JOIN suppliers s ON s.id = i.supplier_id WHERE i.sku = :sku", sku=sku)
    if item is None:
        return None
    on_order = db.one(
        "SELECT coalesce(sum(ol.quantity), 0) AS units, min(po.due_on) AS due "
        "FROM order_lines ol JOIN purchase_orders po ON po.number = ol.order_number "
        "WHERE ol.sku = :sku AND po.status = 'open'", sku=sku)
    item["on_order"] = int(on_order["units"])
    item["on_order_due"] = on_order["due"].isoformat() if on_order["due"] else None
    item["history"] = _journal_rows(db, "WHERE j.sku = :sku ORDER BY j.seq DESC LIMIT 3", sku=sku)
    return item


# -- changes, and the journal that records them ---------------------------------------------


def _journal(db: Db, stamp: dt.datetime, user: str, sku: str, kind: str, before, after,
             origin: str) -> None:
    db.run("WITH next AS (UPDATE counters SET value = value + 1 WHERE name = 'journal' "
           "RETURNING value) INSERT INTO journal (seq, stamp, sku, kind, before, after, origin, "
           "user_id) SELECT next.value, CAST(:stamp AS timestamp), :sku, :kind, :before, :after, "
           ":origin, :user FROM next",
           stamp=stamp.isoformat(sep=" "), sku=sku, kind=kind, before=str(before),
           after=str(after), origin=origin, user=user)


def _write_stock(db: Db, sku: str, level: int) -> int:
    """Set an item's stock; return the level it had (the self-join reads the old row)."""
    return int(db.value("UPDATE items i SET stock = :level FROM items old "
                        "WHERE i.sku = :sku AND old.sku = i.sku RETURNING old.stock",
                        level=level, sku=sku))


def save_inline(db: Db, stamp: dt.datetime, user: str, sku: str, level: int) -> dict:
    """The grid's inline stock cell. Every save is journalled, even an unchanged level,
    so the log shows each value the stockroom was set to."""
    if find_item(db, sku) is None:
        raise Rejected(404, "No item with that SKU.")
    before = _write_stock(db, sku, level)
    _journal(db, stamp, user, sku, "stock", before, level, "inline")  # audited like every other change
    return find_item(db, sku)


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


def adjustment_target(db: Db, ref: RowRef) -> Optional[dict]:
    """The item an adjustment is committed to: the one the adjust page was opened for."""
    return find_item(db, ref.sku)  # the SKU names the item; `at` is only the way back


def commit_adjustment(db: Db, stamp: dt.datetime, user: str, ref: RowRef, level: int) -> dict:
    item = adjustment_target(db, ref)
    if item is None:
        raise Rejected(404, "No item with that SKU.")
    before = _write_stock(db, item["sku"], level)
    _journal(db, stamp, user, item["sku"], "stock", before, level, "adjust")
    db.run("INSERT INTO notices (slot, sku) VALUES (1, :sku) "
           "ON CONFLICT (slot) DO UPDATE SET sku = EXCLUDED.sku", sku=item["sku"])
    return find_item(db, item["sku"])


def take_notice(db: Db) -> Optional[dict]:
    """The one-time 'saved' notice left by the last committed adjustment, consumed on read."""
    sku = db.value("DELETE FROM notices WHERE slot = 1 RETURNING sku")
    return None if sku is None else {"sku": sku}


BULK_ACTIONS = ("archive", "restock")
RESTOCK_STEP = 10


def bulk(db: Db, stamp: dt.datetime, user: str, action: str, skus: list) -> int:
    """Apply `action` to the selected SKUs; returns how many items actually changed.
    Nothing selected changes nothing."""
    if action not in BULK_ACTIONS:
        raise Rejected(422, "Choose what to do with the selected rows.", "action")
    if not isinstance(skus, list) or not all(isinstance(s, str) for s in skus):
        raise Rejected(422, "The selection is not a list of SKUs.", "skus")
    changed = 0
    for sku in sorted(set(skus)):
        item = find_item(db, sku)
        if item is None:
            raise Rejected(404, f"No item {sku}.")
        if action == "archive":
            if item["status"] == "archived":
                continue
            db.run("UPDATE items SET status = 'archived' WHERE sku = :sku", sku=sku)
            _journal(db, stamp, user, sku, "archive", "active", "archived", "bulk")
        else:
            level = min(MAX_LEVEL, item["stock"] + RESTOCK_STEP)
            _write_stock(db, sku, level)
            _journal(db, stamp, user, sku, "stock", item["stock"], level, "bulk")
        changed += 1
    return changed


def _journal_rows(db: Db, where: str, **params) -> list[dict]:
    return [{"stamp": r["stamp"].strftime("%Y-%m-%d %H:%M"), "sku": r["sku"], "kind": r["kind"],
             "before": r["before"], "after": r["after"], "origin": r["origin"], "by": r["by"]}
            for r in db.all("SELECT j.stamp, j.sku, j.kind, j.before, j.after, j.origin, "
                            f"u.name AS by FROM journal j JOIN users u ON u.id = j.user_id {where}",
                            **params)]


def journal(db: Db) -> list[dict]:
    """The audit log, newest first."""
    return _journal_rows(db, "ORDER BY j.seq DESC")


#: Business state, table by table in key order (CONVENTIONS.md §3): everything a session can
#: change or read as data; never the counters, the one-time notice or the session registry.
COMMITTED = (("users", 'id COLLATE "C"'), ("categories", 'id COLLATE "C"'),
             ("locations", 'code COLLATE "C"'), ("suppliers", 'id COLLATE "C"'),
             ("items", 'sku COLLATE "C"'), ("journal", "seq"),
             ("purchase_orders", 'number COLLATE "C"'),
             ("order_lines", 'order_number COLLATE "C", line_no'))


def _plain(value):
    return value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else value


def committed(db: Db) -> dict:
    return {table: [[_plain(v) for v in row.values()]
                    for row in db.all(f"SELECT * FROM {table} ORDER BY {order}")]
            for table, order in COMMITTED}
