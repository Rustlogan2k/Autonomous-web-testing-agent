"""Bagstreet — a small shop. Server-rendered pages and HTML forms on the Python standard
library, with the suite's vendored MongoDB driver.

Post/redirect/get throughout: every form posts, the server validates and stores, then
redirects to a page rendered from the database. Each signed-in browser has a MongoDB database
of its own (store.py); requests are served one at a time under one lock.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import threading
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import pages
import shop
from shop import Refused, refresh_voucher
from store import Visits, read_seed

APP_ID = "bagstreet"
ROOT = Path(__file__).resolve().parent
SEED = read_seed(ROOT.parent / "seed")
pages.VOCAB.update(json.loads((ROOT.parent / "vocab" / f"{os.environ.get('WTA_VOCAB', 'default')}.json")
                              .read_text(encoding="utf-8")))
t = pages.t
TEST_HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
NOW = os.environ.get("WTA_FIXED_NOW") or "2026-06-10T09:00:00"
ACCOUNTS = {u["username"]: u for u in SEED["users"]}
PEOPLE = {u["_id"]: u for u in SEED["users"]}
STAND_IN = {"salt": "0" * 32, "hash": "0" * 64, "iter": 20000}  # an unknown name costs one derivation too
PAGE_NUMBER = re.compile(r"[1-9]\d{0,3}")
ADDRESS_KEY = r"[a-z][a-z0-9-]{0,40}"
LOCK = threading.Lock()
VISITS: Visits | None = None


def authenticate(username: str, password: str) -> dict | None:
    account = ACCOUNTS.get(username.strip().lower())
    pw = account["pw"] if account else STAND_IN
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(pw["salt"]), pw["iter"]).hex()
    return account if account and hmac.compare_digest(derived, pw["hash"]) else None


def continue_to(raw: str) -> str:
    """Where to go after signing in: a same-origin path other than the sign-in page."""
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw or urlsplit(raw).path == "/login":
        return "/"
    return raw


def method_label(method: str) -> str:
    return t(f"method.{method}")


def page_number(raw: str) -> int:
    return int(raw) if PAGE_NUMBER.fullmatch(raw or "") else 1


class Handler(BaseHTTPRequestHandler):
    server_version = "Bagstreet/2.0"

    def log_message(self, fmt, *args):
        pass

    # -- the wire ----------------------------------------------------------------------------

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

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self._headers(status, content_type, len(body))
        self.wfile.write(body)

    def _read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        return parse_qs(raw, keep_blank_values=True)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method: str) -> None:
        self._cookie = None
        parts = urlsplit(self.path)
        if parts.path == "/healthz":
            ok = VISITS.healthy()
            return self._send(200 if ok else 503, "application/json", json.dumps({"ok": ok, "app": APP_ID}).encode())
        if parts.path.startswith("/static/"):
            return self._static(parts.path[len("/static/"):])
        self.form = self._read_form() if method == "POST" else {}
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        self.sid = jar["wta_sid"].value if "wta_sid" in jar else ""
        with LOCK:
            kind, payload = self._route(method, parts.path, parse_qs(parts.query), parts.query)
        if kind == "redirect":
            self._headers(303, location=payload)
        elif kind == "json":
            self._send(200, "application/json", json.dumps(payload).encode())
        else:
            status, body = payload
            self._send(status, "text/html; charset=utf-8", body)
        return None

    def _static(self, name: str) -> None:
        target = (ROOT / "static" / name).resolve()
        if (ROOT / "static") not in target.parents or not target.is_file():
            return self._send(404, "text/plain", b"not found")
        kind = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}.get(
            target.suffix, "application/octet-stream")
        return self._send(200, kind, target.read_bytes())

    def field(self, name: str, default: str = "") -> str:
        return (self.form.get(name) or [default])[-1]

    # -- page helpers ------------------------------------------------------------------------

    def page(self, title: str, body: str, status: int = 200, dialogs: str = ""):
        notice = VISITS.heard(self.visit, "notice")
        html = pages.layout(title, body, who=self.who, bag_count=shop.bag_count(self.db, self.who["id"]),
                            notice=notice, dialogs=dialogs)
        return "html", (status, html)

    def error(self) -> str:
        return VISITS.heard(self.visit, "error")

    def fail(self, key: str, location: str):
        VISITS.say(self.visit["_id"], error=t(key))
        return "redirect", location

    def done(self, location: str, key: str = "", extra: str = ""):
        if key:
            VISITS.say(self.visit["_id"], notice=f"{t(key)}{extra}")
        return "redirect", location

    def missing(self):
        return self.page(t("missing.title"), pages.info("missing.title", "missing.body"), 404)

    def refused(self):
        return self.page(t("access.title"), pages.access_notice())

    # -- routing -----------------------------------------------------------------------------

    def _route(self, method: str, path: str, query: dict, raw_query: str):  # noqa: PLR0911
        if method == "GET" and path == "/__wta/state":
            return self._state()
        if path == "/login":
            return self._sign_in(method, query)
        if method == "GET" and path == "/login/forgot":
            return "html", (200, pages.layout(t("forgot.title"), pages.forgot()))
        self.visit = VISITS.find(self.sid) if self.sid else None
        if self.visit is None or self.visit["user_id"] is None:
            if method == "GET":
                asked = path + (f"?{raw_query}" if raw_query else "")
                return "redirect", f"/login?next={quote(asked, safe='')}"
            return "redirect", "/login"
        person = PEOPLE[self.visit["user_id"]]
        self.who = {"id": person["_id"], "username": person["username"], "name": person["name"],
                    "roles": person["roles"]}
        self.db = VISITS.database(self.visit)
        if method == "POST" and path == "/logout":
            VISITS.sign_out(self.visit["_id"])
            return "redirect", "/login"
        sections = (("/account", self._account), ("/staff/", self._staff), ("/p/", self._product),
                    ("/bag", self._bag), ("/checkout/", self._checkout), ("/orders", self._orders))
        for prefix, handler in sections:
            if path.startswith(prefix):
                return handler(method, path, query)
        if method == "GET" and path == "/":
            return self.page(t("home.title"), pages.home(shop.categories(self.db)))
        if method == "GET" and path == "/search":
            return self._search(query)
        if method == "GET" and path == "/express":
            return self.page(t("express.title"), pages.info("express.title", "express.body"))
        if method == "GET" and path == "/gifts":
            return self.page(t("gifts.title"), pages.info("gifts.title", "gifts.body"))
        return self.missing()

    def _state(self):
        if not TEST_HOOKS:
            return "html", (404, b"not found")
        visit = VISITS.find(self.sid) if self.sid else None
        if visit is None:
            return "json", {"session": None, "user": None, "committed": None, "sha": None}
        committed = shop.committed(VISITS.database(visit))
        canonical = json.dumps(committed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        user = PEOPLE[visit["user_id"]]["username"] if visit["user_id"] else None
        return "json", {"session": visit["_id"], "user": user, "committed": committed,
                        "sha": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}

    def _sign_in(self, method: str, query: dict):
        if method == "GET":
            next_path = continue_to((query.get("next") or [""])[0])
            visit = VISITS.find(self.sid) if self.sid else None
            if visit is not None and visit["user_id"] is not None:
                return "redirect", next_path
            return "html", (200, pages.layout(t("signin.title"), pages.sign_in(next_path)))
        next_path = continue_to(self.field("next"))
        account = authenticate(self.field("username"), self.field("password"))
        if account is None:
            return "html", (200, pages.layout(t("signin.title"), pages.sign_in(next_path, t("msg.signin_refused"))))
        sid, _ = VISITS.sign_in(self.sid, account["_id"])
        self._cookie = sid
        return "redirect", next_path

    # -- browsing ----------------------------------------------------------------------------

    def _search(self, query: dict):
        q = (query.get("q") or [""])[0].strip()
        categories = shop.categories(self.db)
        category = (query.get("category") or ["all"])[0]
        if category not in {c["_id"] for c in categories}:
            category = "all"
        sort = (query.get("sort") or ["relevance"])[0]
        sort = sort if sort in shop.SORTS else "relevance"
        page = page_number((query.get("page") or ["1"])[0])
        items, exact = shop.search(self.db, q, category, sort)
        shown, page_count = shop.page_of(items, page)
        return self.page(t("results.title"), pages.results(categories, shown, q, category, sort, exact, page,
                                                           page_count, shop.from_price))

    def _product(self, method: str, path: str, query: dict):  # noqa: PLR0911
        match = re.fullmatch(r"/p/([a-z][a-z0-9-]*)(/reviews|/review/edit|/review/delete)?", path)
        item = shop.product(self.db, match.group(1)) if match else None
        if item is None:
            return self.missing()
        slug, action = item["_id"], match.group(2)
        reviews_tab = f"/p/{slug}?tab=reviews"
        if method == "GET" and action is None:
            tab = (query.get("tab") or ["details"])[0]
            message = self.error()
            authors = {k: v["name"] for k, v in PEOPLE.items()}
            body, dialogs = pages.product(item, tab=tab, authors=authors, me=self.who,
                                          error="" if tab == "reviews" else message,
                                          review_error=message if tab == "reviews" else "")
            return self.page(item["name"], body, dialogs=dialogs)
        if action is None:
            return self.missing()
        if "customer" not in self.who["roles"]:
            return self.refused()
        mine = shop.own_review(item, self.who["id"])
        if action == "/review/edit" and method == "GET":
            if mine is None:
                return "redirect", reviews_tab
            return self.page(t("review.edit_title"), pages.review_editor(item, mine, self.error()))
        if method != "POST":
            return self.missing()
        if action == "/review/delete":
            if shop.delete_review(self.db, slug, self.who["id"]):
                return self.done(reviews_tab, "msg.review_deleted")
            return "redirect", reviews_tab
        stars, text = self.field("stars").strip(), self.field("text").strip()
        problem = shop.review_problem(stars, text)
        if action == "/reviews":
            if problem:
                return self.fail(problem, reviews_tab)
            try:
                shop.post_review(self.db, slug, self.who["id"], int(stars), text)
            except Refused as refusal:
                return self.fail(refusal.key, reviews_tab)
            return self.done(reviews_tab, "msg.review_posted")
        if mine is None:
            return "redirect", reviews_tab
        if problem:
            return self.fail(problem, f"/p/{slug}/review/edit")
        shop.edit_review(self.db, slug, self.who["id"], int(stars), text)
        return self.done(reviews_tab, "msg.review_saved")

    # -- the bag -----------------------------------------------------------------------------

    def _bag(self, method: str, path: str, query: dict):  # noqa: PLR0911
        bag_id = self.who["id"]
        if method == "GET" and path == "/bag":
            vouchers = [(code, t(f"voucher.{code}")) for code in shop.VOUCHER_CODES]
            body = pages.bag(shop.bag_lines(self.db, bag_id), shop.totals(shop.bag(self.db, bag_id)), vouchers,
                             self.error())
            return self.page(t("bag.title"), body)
        if method != "POST":
            return self.missing()
        if path == "/bag/add":
            slug, code = self.field("slug"), self.field("variant")
            if shop.variant_of(shop.product(self.db, slug), code) is None:
                return "redirect", "/"
            qty = shop.parse_qty(self.field("qty"))
            if not qty:
                return self.fail("msg.qty_rule", f"/p/{quote(slug)}")
            try:
                shop.add_to_bag(self.db, bag_id, slug, code, qty)
            except Refused as refusal:
                return self.fail(refusal.key, f"/p/{quote(slug)}")
            return self.done("/bag", "msg.added")
        if path == "/bag/update":
            changes = {}
            for line in shop.bag(self.db, bag_id)["lines"]:
                raw = self.form.get(f"qty_{line['n']}")
                if raw is None:
                    continue
                qty = shop.parse_qty(raw[-1])
                if qty is None:
                    return self.fail("msg.qty_rule", "/bag")
                changes[line["n"]] = qty
            shop.set_quantities(self.db, bag_id, changes)
            refresh_voucher(self.db, bag_id)  # the discount depends on what is in the bag
            return "redirect", "/bag"
        match = re.fullmatch(r"/bag/remove/(\d{1,3})", path)
        if match:
            shop.remove_lines(self.db, bag_id, {int(match.group(1))})
            return "redirect", "/bag"
        if path == "/bag/remove-selected":
            picked = {int(n) for n in self.form.get("line", []) if re.fullmatch(r"\d{1,3}", n)}
            if not picked:
                return self.fail("msg.pick_items", "/bag")
            removed = shop.remove_lines(self.db, bag_id, picked)
            return self.done("/bag", "msg.removed", f" ({removed})")
        if path == "/bag/voucher":
            code = self.field("voucher", "none")
            if code not in shop.VOUCHER_CODES:
                return self.fail("msg.voucher_unknown", "/bag")
            shop.use_voucher(self.db, bag_id, code)
            return "redirect", "/bag"
        return self.missing()

    # -- checkout ----------------------------------------------------------------------------

    def _checkout(self, method: str, path: str, query: dict):  # noqa: PLR0911
        bag_id = self.who["id"]
        bag = shop.bag(self.db, bag_id)
        if not bag["lines"]:
            return "redirect", "/bag"
        saved = {"method": bag["method"], "full_name": bag["full_name"], "address": bag["address"]}
        store = shop.store(self.db, bag["store_id"])["address"]
        if path == "/checkout/delivery" and method == "GET":
            methods = [(m, method_label(m), fee) for m, fee in shop.METHOD_FEES.items()]
            book = shop.addresses(self.db, bag_id)
            return self.page(t("delivery.title"), pages.delivery(methods, saved, store, book, shop.one_line,
                                                                 self.error()))
        if path == "/checkout/delivery" and method == "POST":
            chosen = self.field("method", "standard")
            if chosen not in shop.METHOD_FEES:
                return self.fail("msg.method_unknown", "/checkout/delivery")
            details = None
            if "full_name" in self.form:
                full_name, address = self.field("full_name").strip(), self.field("address").strip()
                if len(full_name) > shop.TEXT_LIMIT or len(address) > shop.TEXT_LIMIT:
                    return self.fail("msg.too_long", "/checkout/delivery")
                details = (full_name, address)
            shop.save_delivery(self.db, bag_id, chosen, details)
            if self.field("action") != "review":
                return "redirect", "/checkout/delivery"  # the method changed: show its section
            bag = shop.bag(self.db, bag_id)
            if chosen in ("standard", "express") and not (bag["full_name"] and bag["address"]):
                return self.fail("msg.address_needed", "/checkout/delivery")
            return "redirect", "/checkout/review"
        if path == "/checkout/review" and method == "GET":
            return self.page(t("review.title"), pages.review(shop.bag_lines(self.db, bag_id), shop.totals(bag), saved,
                                                             method_label(bag["method"]), store))
        if path == "/checkout/place" and method == "POST":
            chosen = self.field("method")
            if chosen not in shop.METHOD_FEES:
                return self.fail("msg.method_unknown", "/checkout/delivery")
            number = shop.place_order(self.db, bag_id, chosen, NOW)
            return self.done(f"/orders/{number}", "msg.placed", f" {number}")
        return self.missing()

    # -- orders ------------------------------------------------------------------------------

    def _orders(self, method: str, path: str, query: dict):
        labels = {m: method_label(m) for m in shop.METHOD_FEES}
        if method == "GET" and path == "/orders":
            return self.page(t("nav.orders"), pages.orders(shop.orders_of(self.db, self.who["id"]), labels))
        match = re.fullmatch(r"/orders/(BS-\d{4})", path)
        placed = shop.order(self.db, match.group(1)) if match and method == "GET" else None
        if placed is None or (placed["customer_id"] != self.who["id"] and "staff" not in self.who["roles"]):
            return self.page(t("nav.orders"), pages.info("orders.missing_title", "orders.missing"), 404)
        store = shop.store(self.db, placed["store_id"])["address"]
        return self.page(t("confirm.title"), pages.confirmation(placed, method_label(placed["method"]), store))

    def _staff(self, method: str, path: str, query: dict):
        if "staff" not in self.who["roles"]:
            return self.refused()
        labels = {m: method_label(m) for m in shop.METHOD_FEES}
        customers = {k: v["name"] for k, v in PEOPLE.items()}
        if method == "GET" and path == "/staff/orders":
            status = (query.get("status") or ["placed"])[0]
            status = status if status in ("all", *shop.STATUSES) else "placed"
            return self.page(t("staff.title"), pages.staff_orders(shop.all_orders(self.db, status), status, customers,
                                                                  labels, self.error()))
        if method == "POST" and path == "/staff/orders/dispatch":
            numbers = [n for n in self.form.get("order", []) if re.fullmatch(r"BS-\d{4}", n)]
            if not numbers:
                return self.fail("msg.pick_orders", "/staff/orders")
            count = shop.dispatch(self.db, numbers, self.who["id"], NOW)
            return self.done("/staff/orders", "msg.dispatched", f" ({count})")
        return self.missing()

    # -- the account -------------------------------------------------------------------------

    def _account(self, method: str, path: str, query: dict):  # noqa: PLR0911
        user_id = self.who["id"]
        if method == "GET" and path == "/account":
            return self.page(t("account.title"), pages.account(self.who))
        if path == "/account/addresses" and method == "GET":
            body, dialogs = pages.address_book(shop.addresses(self.db, user_id), shop.one_line, self.error())
            return self.page(t("addresses.title"), body, dialogs=dialogs)
        if path == "/account/addresses/new":
            if method == "GET":
                return self.page(t("addresses.new_title"), pages.address_form(path, {}, self.error()))
            fields = self._address_fields()
            problem = shop.address_problem(fields)
            if problem:
                return self.fail(problem, path)
            try:
                shop.add_address(self.db, user_id, fields)
            except Refused as refusal:
                return self.fail(refusal.key, "/account/addresses")
            return self.done("/account/addresses", "msg.address_added")
        match = re.fullmatch(rf"/account/addresses/({ADDRESS_KEY})/(edit|delete)", path)
        if not match:
            return self.missing()
        key, action = match.groups()
        current = next((a for a in shop.addresses(self.db, user_id) if a["key"] == key), None)
        if current is None:
            return "redirect", "/account/addresses"  # already deleted, e.g. from another tab
        if action == "edit" and method == "GET":
            return self.page(t("addresses.edit_title"), pages.address_form(path, current, self.error()))
        if method != "POST":
            return self.missing()
        if action == "delete":
            shop.delete_address(self.db, user_id, key)
            return self.done("/account/addresses", "msg.address_deleted")
        fields = self._address_fields()
        problem = shop.address_problem(fields)
        if problem:
            return self.fail(problem, path)
        shop.update_address(self.db, user_id, key, fields)
        return self.done("/account/addresses", "msg.address_saved")

    def _address_fields(self) -> dict[str, str]:
        return {name: self.field(name).strip() for name in shop.ADDRESS_LIMITS}


def main() -> None:
    global VISITS  # noqa: PLW0603 - the one registry, made once the URL is known
    url = os.environ.get("MONGODB_URL")
    if not url:
        sys.exit("bagstreet: MONGODB_URL is required (the MongoDB home database for this server)")
    VISITS = Visits(url, SEED, int(os.environ.get("WTA_SESSION_CAP") or 32))
    server = ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), int(os.environ["PORT"])), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
