"""Bagstreet — a small shop. Server-rendered pages and HTML forms, standard library only.

Post/redirect/get throughout: every form posts, the server validates and stores, then
redirects to a page that is rendered from the database. State lives in SQLite under
WTA_DATA_DIR, partitioned by the `wta_sid` session cookie.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import pages

APP_ID = "bagstreet"
ROOT = Path(__file__).resolve().parent
CATALOG = json.loads((ROOT.parent / "seed" / "catalog.json").read_text(encoding="utf-8"))
pages.VOCAB.update(json.loads((ROOT.parent / "vocab" / f"{os.environ.get('WTA_VOCAB', 'default')}.json")
                              .read_text(encoding="utf-8")))
t = pages.t
DATA_DIR = Path(os.environ.get("WTA_DATA_DIR") or tempfile.mkdtemp(prefix="bagstreet-"))
DB_PATH = DATA_DIR / "bagstreet.sqlite3"
TEST_HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
PRODUCTS = {p["slug"]: p for p in CATALOG["products"]}
METHOD_FEES = {"standard": 395, "express": 795, "collect": 0}
VOUCHERS = ("none", "welcome10", "freeship", "bundle5")
MAX_QTY = 99
TEXT_LIMIT = 10000
_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER);
CREATE TABLE IF NOT EXISTS bags (sid TEXT PRIMARY KEY, voucher TEXT, discount INTEGER, method TEXT,
    full_name TEXT, address TEXT, notice TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS lines (sid TEXT, line INTEGER, slug TEXT, variant TEXT, qty INTEGER,
    PRIMARY KEY (sid, line));
CREATE TABLE IF NOT EXISTS orders (sid TEXT, number TEXT, method TEXT, full_name TEXT, address TEXT,
    subtotal INTEGER, discount INTEGER, delivery INTEGER, total INTEGER, PRIMARY KEY (sid, number));
CREATE TABLE IF NOT EXISTS order_lines (sid TEXT, number TEXT, line INTEGER, slug TEXT, variant TEXT,
    qty INTEGER, price INTEGER);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def next_id(conn, key: str) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    value = (row["value"] if row else 0) + 1
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
    return value


def variant_of(slug: str, variant: str) -> dict | None:
    product = PRODUCTS.get(slug)
    if product is None:
        return None
    return next((v for v in product["variants"] if v["id"] == variant), None)


def method_label(method: str) -> str:
    return t(f"method.{method}")


def voucher_label(code: str) -> str:
    return t(f"voucher.{code}")


# -- the bag ---------------------------------------------------------------------------------


def bag_row(conn, sid) -> sqlite3.Row:
    return conn.execute("SELECT * FROM bags WHERE sid = ?", (sid,)).fetchone()


def bag_lines(conn, sid) -> list[dict]:
    out = []
    for row in conn.execute("SELECT * FROM lines WHERE sid = ? ORDER BY line", (sid,)):
        product, variant = PRODUCTS[row["slug"]], variant_of(row["slug"], row["variant"])
        out.append({"line": row["line"], "slug": row["slug"], "variant": row["variant"],
                    "name": product["name"], "variant_name": variant["name"],
                    "price": variant["price"], "qty": row["qty"]})
    return out


def voucher_discount(code: str, subtotal: int) -> int:
    if code == "welcome10":
        return (subtotal + 5) // 10
    if code == "bundle5":
        return 500 if subtotal >= 5000 else 0
    return 0


def refresh_voucher(conn, sid) -> None:
    """Recompute the stored voucher discount from the bag as it is now."""
    bag = bag_row(conn, sid)
    subtotal = sum(line["price"] * line["qty"] for line in bag_lines(conn, sid))
    conn.execute("UPDATE bags SET discount = ? WHERE sid = ?", (voucher_discount(bag["voucher"], subtotal), sid))


def totals(conn, sid, method: str | None = None) -> dict:
    bag = bag_row(conn, sid)
    subtotal = sum(line["price"] * line["qty"] for line in bag_lines(conn, sid))
    method = method or bag["method"]
    delivery = 0 if bag["voucher"] == "freeship" else METHOD_FEES[method]
    before = subtotal - bag["discount"]
    return {"subtotal": subtotal, "discount": bag["discount"], "before_delivery": before,
            "delivery": delivery, "total": before + delivery, "voucher_label": voucher_label(bag["voucher"])}


def committed_state(conn, sid) -> dict:
    bag = bag_row(conn, sid)
    return {
        "bag": {"voucher": bag["voucher"], "discount": bag["discount"], "method": bag["method"],
                "full_name": bag["full_name"], "address": bag["address"]},
        "lines": [dict(r) for r in conn.execute(
            "SELECT line, slug, variant, qty FROM lines WHERE sid = ? ORDER BY line", (sid,))],
        "orders": [dict(r) for r in conn.execute(
            "SELECT number, method, full_name, address, subtotal, discount, delivery, total FROM orders "
            "WHERE sid = ? ORDER BY number", (sid,))],
    }


def parse_qty(raw: str) -> int | None:
    text = (raw or "").strip()
    if not re.fullmatch(r"\d{1,3}", text):
        return None
    value = int(text)
    return value if value <= MAX_QTY else None


# -- HTTP ------------------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "Bagstreet/1.0"

    def log_message(self, fmt, *args):
        pass

    def _session(self, conn) -> str:
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        sid = jar["wta_sid"].value if "wta_sid" in jar else ""
        if sid and bag_row(conn, sid) is not None:
            return sid
        sid = f"b{next_id(conn, 'session'):06d}"
        conn.execute("INSERT INTO bags VALUES (?, 'none', 0, 'standard', '', '', '', '')", (sid,))
        self._cookie = sid
        return sid

    def _headers(self, status: int, content_type: str | None = None, length: int = 0, location: str = "") -> None:
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        if location:
            self.send_header("Location", location)
        if self._cookie:
            self.send_header("Set-Cookie", f"wta_sid={self._cookie}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()

    def _html(self, status: int, body: bytes) -> None:
        self._headers(status, "text/html; charset=utf-8", len(body))
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self._headers(303, location=location)

    def _form(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method: str) -> None:
        self._cookie = None
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/healthz":
            body = json.dumps({"ok": True, "app": APP_ID}).encode()
            self._headers(200, "application/json", len(body))
            self.wfile.write(body)
            return
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        with _LOCK:
            conn = connect()
            try:
                sid = self._session(conn)
                outcome = self._route(conn, sid, method, path, parse_qs(parts.query))
                conn.commit()
            finally:
                conn.close()
        kind, payload = outcome
        if kind == "redirect":
            self._redirect(payload)
        elif kind == "json":
            body = json.dumps(payload).encode()
            self._headers(200, "application/json", len(body))
            self.wfile.write(body)
        else:
            status, body = payload
            self._html(status, body)

    def _static(self, name: str) -> None:
        target = (ROOT / "static" / name).resolve()
        if (ROOT / "static") not in target.parents or not target.is_file():
            self._headers(404, "text/plain", 0)
            return
        kind = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}.get(target.suffix,
                                                                                            "application/octet-stream")
        body = target.read_bytes()
        self._headers(200, kind, len(body))
        self.wfile.write(body)

    def _page(self, conn, sid, title: str, body: str, status: int = 200):
        count = conn.execute("SELECT COALESCE(SUM(qty), 0) AS n FROM lines WHERE sid = ?", (sid,)).fetchone()["n"]
        bag = bag_row(conn, sid)
        notice = bag["notice"]
        if notice:
            conn.execute("UPDATE bags SET notice = '' WHERE sid = ?", (sid,))
        return "html", (status, pages.layout(title, body, count, notice))

    def _take_error(self, conn, sid) -> str:
        error = bag_row(conn, sid)["error"]
        if error:
            conn.execute("UPDATE bags SET error = '' WHERE sid = ?", (sid,))
        return error

    def _fail(self, conn, sid, message: str, location: str):
        conn.execute("UPDATE bags SET error = ? WHERE sid = ?", (message, sid))
        return "redirect", location

    def _route(self, conn, sid, method: str, path: str, query: dict):  # noqa: C901, PLR0911, PLR0912
        if method == "GET" and path == "/__wta/state":
            if not TEST_HOOKS:
                return "html", (404, b"not found")
            committed = committed_state(conn, sid)
            sha = hashlib.sha256(json.dumps(committed, sort_keys=True).encode()).hexdigest()
            return "json", {"session": sid, "committed": committed, "sha": sha}
        if method == "GET" and path == "/":
            return self._page(conn, sid, t("home.title"), pages.home(CATALOG))
        if method == "GET" and path == "/search":
            q = (query.get("q") or [""])[0].strip()
            category = (query.get("category") or ["all"])[0]
            sort = (query.get("sort") or ["relevance"])[0]
            items = [p for p in CATALOG["products"] if category in ("all", p["category"])]
            matched = [p for p in items if q and q.lower() in (p["name"] + " " + p["blurb"]).lower()]
            exact = bool(matched) or not q
            items = matched or items  # nothing matched: show everything rather than an empty page
            price = lambda p: min(v["price"] for v in p["variants"])  # noqa: E731
            if sort == "price-asc":
                items = sorted(items, key=price)
            elif sort == "price-desc":
                items = sorted(items, key=price, reverse=True)
            elif sort == "name":
                items = sorted(items, key=lambda p: p["name"])
            return self._page(conn, sid, t("results.title"), pages.results(CATALOG, items, q, category, sort, exact))
        match = re.fullmatch(r"/p/([\w-]+)", path)
        if method == "GET" and match and match.group(1) in PRODUCTS:
            return self._page(conn, sid, PRODUCTS[match.group(1)]["name"],
                              pages.product(PRODUCTS[match.group(1)], self._take_error(conn, sid)))
        if method == "POST" and path == "/bag/add":
            form = self._form()
            slug, variant = form.get("slug", ""), form.get("variant", "")
            if variant_of(slug, variant) is None:
                return "redirect", "/"
            qty = parse_qty(form.get("qty", ""))
            if qty is None or qty == 0:
                return self._fail(conn, sid, t("msg.qty_rule"), f"/p/{quote(slug)}")
            existing = conn.execute("SELECT * FROM lines WHERE sid = ? AND slug = ? AND variant = ?",
                                    (sid, slug, variant)).fetchone()
            if existing:
                if existing["qty"] + qty > MAX_QTY:
                    return self._fail(conn, sid, t("msg.qty_rule"), f"/p/{quote(slug)}")
                conn.execute("UPDATE lines SET qty = qty + ? WHERE sid = ? AND line = ?", (qty, sid, existing["line"]))
            else:
                line = (conn.execute("SELECT COALESCE(MAX(line), 0) AS n FROM lines WHERE sid = ?",
                                     (sid,)).fetchone()["n"]) + 1
                conn.execute("INSERT INTO lines VALUES (?, ?, ?, ?, ?)", (sid, line, slug, variant, qty))
            refresh_voucher(conn, sid)
            conn.execute("UPDATE bags SET notice = ? WHERE sid = ?", (t("msg.added"), sid))
            return "redirect", "/bag"
        if method == "GET" and path == "/bag":
            vouchers = [(code, voucher_label(code)) for code in VOUCHERS]
            body = pages.bag(bag_lines(conn, sid), totals(conn, sid), vouchers, bag_row(conn, sid)["voucher"],
                             self._take_error(conn, sid))
            return self._page(conn, sid, t("bag.title"), body)
        if method == "POST" and path == "/bag/update":
            form = self._form()
            changes = {}
            for line in bag_lines(conn, sid):
                raw = form.get(f"qty_{line['line']}")
                if raw is None:
                    continue
                qty = parse_qty(raw)
                if qty is None:
                    return self._fail(conn, sid, t("msg.qty_rule"), "/bag")
                changes[line["line"]] = qty
            for line, qty in changes.items():
                if qty == 0:
                    conn.execute("DELETE FROM lines WHERE sid = ? AND line = ?", (sid, line))
                else:
                    conn.execute("UPDATE lines SET qty = ? WHERE sid = ? AND line = ?", (qty, sid, line))
            refresh_voucher(conn, sid)  # the discount depends on what is in the bag
            return "redirect", "/bag"
        match = re.fullmatch(r"/bag/remove/(\d+)", path)
        if method == "POST" and match:
            conn.execute("DELETE FROM lines WHERE sid = ? AND line = ?", (sid, int(match.group(1))))
            refresh_voucher(conn, sid)
            return "redirect", "/bag"
        if method == "POST" and path == "/bag/voucher":
            code = self._form().get("voucher", "none")
            if code not in VOUCHERS:
                return self._fail(conn, sid, t("msg.voucher_unknown"), "/bag")
            conn.execute("UPDATE bags SET voucher = ? WHERE sid = ?", (code, sid))
            refresh_voucher(conn, sid)
            return "redirect", "/bag"
        if path == "/checkout/delivery":
            if not bag_lines(conn, sid):
                return "redirect", "/bag"
            if method == "GET":
                methods = [(m, method_label(m), METHOD_FEES[m]) for m in METHOD_FEES]
                bag = bag_row(conn, sid)
                saved = {"method": bag["method"], "full_name": bag["full_name"], "address": bag["address"]}
                return self._page(conn, sid, t("delivery.title"),
                                  pages.delivery(methods, saved, CATALOG["store"], self._take_error(conn, sid)))
            form = self._form()
            chosen = form.get("method", "standard")
            if chosen not in METHOD_FEES:
                return self._fail(conn, sid, t("msg.method_unknown"), "/checkout/delivery")
            conn.execute("UPDATE bags SET method = ? WHERE sid = ?", (chosen, sid))
            if "full_name" in form:
                full_name, address = form.get("full_name", "").strip(), form.get("address", "").strip()
                if len(full_name) > TEXT_LIMIT or len(address) > TEXT_LIMIT:
                    return self._fail(conn, sid, t("msg.too_long"), "/checkout/delivery")
                conn.execute("UPDATE bags SET full_name = ?, address = ? WHERE sid = ?", (full_name, address, sid))
            if form.get("action") != "review":
                return "redirect", "/checkout/delivery"  # the method changed: show its section
            bag = bag_row(conn, sid)
            if chosen in ("standard", "express") and not (bag["full_name"] and bag["address"]):
                return self._fail(conn, sid, t("msg.address_needed"), "/checkout/delivery")
            return "redirect", "/checkout/review"
        if method == "GET" and path == "/checkout/review":
            lines = bag_lines(conn, sid)
            if not lines:
                return "redirect", "/bag"
            bag = bag_row(conn, sid)
            saved = {"method": bag["method"], "full_name": bag["full_name"], "address": bag["address"]}
            return self._page(conn, sid, t("review.title"),
                              pages.review(lines, totals(conn, sid), saved, method_label(bag["method"]),
                                           CATALOG["store"]))
        if method == "POST" and path == "/checkout/place":
            lines = bag_lines(conn, sid)
            if not lines:
                return "redirect", "/bag"
            chosen = self._form().get("method", "")
            if chosen not in METHOD_FEES:
                return self._fail(conn, sid, t("msg.method_unknown"), "/checkout/delivery")
            bag = bag_row(conn, sid)
            sums = totals(conn, sid, chosen)
            number = f"BS-{1000 + next_id(conn, f'order:{sid}')}"
            conn.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (sid, number, chosen, bag["full_name"] if chosen != "collect" else "",
                          bag["address"] if chosen != "collect" else "", sums["subtotal"], sums["discount"],
                          sums["delivery"], sums["total"]))
            for line in lines:
                conn.execute("INSERT INTO order_lines VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (sid, number, line["line"], line["slug"], line["variant"], line["qty"], line["price"]))
            conn.execute("DELETE FROM lines WHERE sid = ?", (sid,))
            conn.execute("UPDATE bags SET voucher = 'none', discount = 0, notice = ? WHERE sid = ?",
                         (f"{t('msg.placed')} {number}", sid))
            return "redirect", f"/orders/{number}"
        if method == "GET" and path == "/orders":
            rows = [dict(r) for r in conn.execute("SELECT * FROM orders WHERE sid = ? ORDER BY number DESC", (sid,))]
            labels = {m: method_label(m) for m in METHOD_FEES}
            return self._page(conn, sid, t("nav.orders"), pages.orders(rows, labels))
        match = re.fullmatch(r"/orders/(BS-\d+)", path)
        if method == "GET" and match:
            order = conn.execute("SELECT * FROM orders WHERE sid = ? AND number = ?", (sid, match.group(1))).fetchone()
            if order is None:
                return self._page(conn, sid, t("nav.orders"), pages.info("orders.missing_title", "orders.missing"), 404)
            lines = []
            for row in conn.execute("SELECT * FROM order_lines WHERE sid = ? AND number = ? ORDER BY line",
                                    (sid, order["number"])):
                lines.append({"qty": row["qty"], "name": PRODUCTS[row["slug"]]["name"],
                              "variant_name": variant_of(row["slug"], row["variant"])["name"]})
            return self._page(conn, sid, t("confirm.title"),
                              pages.confirmation(dict(order), lines, method_label(order["method"]), CATALOG["store"]))
        if method == "GET" and path == "/express":
            return self._page(conn, sid, t("express.title"), pages.info("express.title", "express.body"))
        if method == "GET" and path == "/gifts":
            return self._page(conn, sid, t("gifts.title"), pages.info("gifts.title", "gifts.body"))
        return self._page(conn, sid, t("missing.title"), pages.info("missing.title", "missing.body"), 404)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    server = ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), int(os.environ["PORT"])), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
