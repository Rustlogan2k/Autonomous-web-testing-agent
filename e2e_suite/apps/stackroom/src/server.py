"""Stackroom — inventory administration for a hardware wholesaler's stockroom.

A data grid over a JSON API with server-side filtering, sorting and paging, behind a sign-in.
Every page route (/stock, /items/<sku>, /items/<sku>/adjust, /log) is the same HTML shell;
the client script renders the route from the path, and the grid keeps its state in its own
query string. The sign-in and help pages are rendered here. Data lives in PostgreSQL, one
schema per signed-in browser session (stockdb.py). Python standard library plus the suite's
vendored pg8000.

Environment: PORT (required), DATABASE_URL (required), HOST, WTA_VOCAB, WTA_FIXED_NOW,
WTA_SESSION_CAP, WTA_TEST_HOOKS.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inventory  # noqa: E402
from inventory import Rejected, RowRef, View  # noqa: E402
from stockdb import ACCOUNTS, Store  # noqa: E402

APP_ID = "stackroom"
HERE = Path(__file__).resolve().parent
ASSETS = HERE / "static"
PROFILE = os.environ.get("WTA_VOCAB", "default")
WORDS = json.loads((HERE.parent / "vocab" / f"{PROFILE}.json").read_text(encoding="utf-8"))
CLOCK = dt.datetime.fromisoformat(os.environ.get("WTA_FIXED_NOW", "2026-06-10T09:00:00"))  # wta-clock
STAMP = CLOCK.replace(second=0, microsecond=0)
HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
SESSION_CAP = int(os.environ.get("WTA_SESSION_CAP") or 32)
USERS = {u["id"]: {k: u[k] for k in ("id", "username", "name", "role")} for u in ACCOUNTS["users"]}
STORE: Optional[Store] = None  # opened by main()

PAGE_ROUTES = (re.compile(r"/stock"), re.compile(r"/items/SKU-\d+"), re.compile(r"/items/SKU-\d+/adjust"),
               re.compile(r"/log"))
ASSET_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
HTML = "text/html; charset=utf-8"
ITEM_PATH = re.compile(r"/api/items/(SKU-\d+)")
INLINE_PATH = re.compile(r"/api/items/(SKU-\d+)/stock")
#: Where a sign-in may send the browser next: a path on this site, never another origin.
SAFE_NEXT = re.compile(r"/(?!/)[^\s\\]*")
HOME = "/stock"
#: What only a stock controller may do; a viewer is answered {ok: false, reason: forbidden}.
CONTROLLER_ONLY = (re.compile(r"/api/bulk"), INLINE_PATH, re.compile(r"/api/adjustments"))


def t(key: str) -> str:
    return WORDS[key]


def fill(template: str, values: dict[str, str]) -> bytes:
    """A template from static/ with its {{NAME}} slots filled (the values are already HTML)."""
    page = (ASSETS / template).read_text(encoding="utf-8")
    for name, value in values.items():
        page = page.replace("{{" + name + "}}", value)
    return page.encode("utf-8")


def role_label(user: dict) -> str:
    return t("role.controller") if user["role"] == "controller" else t("role.viewer")


def shell(user: dict) -> bytes:
    """The one HTML document every page route serves, with this profile's labels and the
    signed-in user inlined."""
    words = json.dumps(WORDS, ensure_ascii=False).replace("</", "<\\/")
    session = json.dumps({"name": user["name"], "role": user["role"]}).replace("</", "<\\/")
    return fill("shell.html", {
        "TITLE": html.escape(t("app.title")), "WORDS": words, "SESSION": session,
        "SIGNED_IN_AS": html.escape(f"{user['name']} · {role_label(user)}"),
        "ACCOUNT": html.escape(t("act.account")), "SIGN_OUT": html.escape(t("act.sign_out"))})


def sign_in_page(next_path: str, username: str = "", refused: bool = False) -> bytes:
    error = (f'<p id="login-error" class="error" role="alert">{html.escape(t("login.refused"))}</p>'
             if refused else "")
    return fill("login.html", {
        "TITLE": html.escape(t("app.title")), "HEADING": html.escape(t("login.title")),
        "NEXT": html.escape(next_path), "USERNAME_LABEL": html.escape(t("login.username")),
        "USERNAME": html.escape(username), "PASSWORD_LABEL": html.escape(t("login.password")),
        "ERROR": error, "SIGN_IN": html.escape(t("act.sign_in")),
        "FORGOT": html.escape(t("act.forgot"))})


def help_page() -> bytes:
    return fill("help.html", {
        "TITLE": html.escape(t("app.title")), "HEADING": html.escape(t("help.title")),
        "BODY": html.escape(t("help.body")), "BACK": html.escape(t("act.back_sign_in"))})


def safe_next(raw: Optional[str]) -> str:
    """The page a sign-in returns to: a same-origin path that is not the sign-in page."""
    value = (raw or "").strip()
    if not SAFE_NEXT.fullmatch(value) or value.startswith(("/login", "/logout")) or value == "/":
        return HOME
    return value


class StockroomHandler(BaseHTTPRequestHandler):
    server_version = "Stackroom/2.0"

    def log_message(self, format, *args):  # noqa: A002 - keep test output quiet
        return

    # -- plumbing --------------------------------------------------------------------------

    def _reply(self, status: int, body: bytes, kind: str, extra: Optional[dict] = None) -> None:
        self.send_response(status)
        headers = {"Content-Type": kind, "Content-Length": str(len(body)), "Cache-Control": "no-store",
                   "X-Content-Type-Options": "nosniff", **(extra or {})}
        if self._issued_sid:
            headers["Set-Cookie"] = f"wta_sid={self._issued_sid}; Path=/; HttpOnly; SameSite=Lax"
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _reply_json(self, status: int, payload) -> None:
        self._reply(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8")

    def _redirect(self, location: str) -> None:
        self._reply(303, b"", "text/plain; charset=utf-8", {"Location": location})

    def _sid(self) -> Optional[str]:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie") or "")
        except Exception:  # noqa: BLE001 - a malformed cookie header is just no cookie
            return None
        morsel = jar.get("wta_sid")
        return morsel.value if morsel is not None else None

    def _body(self) -> bytes:
        size = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(size) if size else b""

    def _payload(self) -> dict:
        raw = self._body() or b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError) as exc:
            raise Rejected(400, "The request body is not JSON.") from exc
        if not isinstance(data, dict):
            raise Rejected(400, "The request body must be a JSON object.")
        return data

    def _form(self) -> dict:
        fields = parse_qs(self._body().decode("utf-8", errors="replace"), keep_blank_values=True)
        return {k: v[-1] for k, v in fields.items()}

    # -- entry points ------------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def _handle(self) -> None:  # noqa: PLR0911 - one exit per route
        self._issued_sid = ""
        parts = urlsplit(self.path)
        path, query = parts.path, {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        verb = self.command
        if verb == "GET" and path == "/healthz":  # ok only while the database answers
            ok = STORE.ping()
            return self._reply_json(200 if ok else 503, {"ok": ok, "app": APP_ID})
        if verb == "GET" and path.startswith("/assets/"):
            return self._asset(path[len("/assets/"):])
        if path == "/login":
            return self._sign_in(query) if verb == "POST" else self._sign_in_form(query)
        if verb == "POST" and path == "/logout":
            STORE.sign_out(self._sid())
            return self._redirect("/login")
        if verb == "GET" and path == "/help/sign-in":
            return self._reply(200, help_page(), HTML)
        is_page = verb == "GET" and (path == "/" or any(r.fullmatch(path) for r in PAGE_ROUTES))
        if not is_page and not path.startswith("/api/") and not (HOOKS and path == "/__wta/state"):
            return self._reply(404, b"Not found", "text/plain; charset=utf-8")
        try:
            with STORE.visit(self._sid()) as visit:
                kind, status, result = self._route(visit, verb, path, query, is_page)
        except Rejected as exc:
            kind, status, result = "json", exc.status, {"error": exc.message, "field": exc.field}
        except Exception:  # noqa: BLE001 - a defect: rolled back by visit(), logged, answered 500
            traceback.print_exc(file=sys.stderr)
            kind, status, result = "json", 500, {"error": t("msg.failed")}
        if kind == "redirect":
            return self._redirect(result)
        if kind == "html":
            return self._reply(status, result, HTML)
        return self._reply_json(status, result)

    def _route(self, visit, verb: str, path: str, query: dict, is_page: bool):
        if path == "/__wta/state":
            return "json", 200, self._state(visit)
        user = USERS.get(visit.user_id or "")
        if user is None:
            if is_page:  # signed out: to the sign-in page, and back here afterwards
                return "redirect", 303, "/login?next=" + quote(self.path, safe="")
            return "json", 200, {"ok": False, "reason": "signed-out"}
        if is_page:
            return ("redirect", 303, HOME) if path == "/" else ("html", 200, shell(user))
        if verb == "POST" and user["role"] != "controller" \
                and any(r.fullmatch(path) for r in CONTROLLER_ONLY):
            return "json", 200, {"ok": False, "reason": "forbidden"}
        status, payload = self._api(visit.db, user, verb, path, query)
        return "json", status, payload

    def _asset(self, name: str) -> None:
        target = (ASSETS / name).resolve()
        if target.parent != ASSETS or target.suffix not in ASSET_TYPES or not target.is_file():
            return self._reply(404, b"Not found", "text/plain; charset=utf-8")
        return self._reply(200, target.read_bytes(), ASSET_TYPES[target.suffix])

    # -- signing in ----------------------------------------------------------------------------

    def _sign_in_form(self, query: dict) -> None:
        with STORE.visit(self._sid()) as visit:
            signed_in = (visit.user_id or "") in USERS
        if signed_in:  # also where Back to the sign-in page lands once signed in
            return self._redirect(safe_next(query.get("next")))
        return self._reply(200, sign_in_page(query.get("next") or ""), HTML)

    def _sign_in(self, query: dict) -> None:
        form = self._form()
        username, password = form.get("username", "").strip(), form.get("password", "")
        next_path = form.get("next") or query.get("next") or ""
        user_id = STORE.account(username, password) if username and password else None
        if user_id is None:  # refused: the form again, with one message whatever the reason
            return self._reply(200, sign_in_page(next_path, username, refused=True), HTML)
        self._issued_sid = STORE.sign_in(self._sid(), user_id)
        return self._redirect(safe_next(next_path))

    # -- the JSON API --------------------------------------------------------------------------

    def _state(self, visit) -> dict:
        if visit.partition is None:
            return {"session": None, "user": None, "committed": None, "sha": None}
        state = inventory.committed(visit.db)
        digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        user = USERS.get(visit.user_id or "")
        return {"session": visit.sid, "user": user["username"] if user else None,
                "committed": state, "sha": digest}

    def _api(self, db, user: dict, verb: str, path: str, query: dict):  # noqa: PLR0911
        if verb == "GET" and path == "/api/listing":
            return 200, inventory.listing(db, View.read(query), self._page(query.get("page")))

        if verb == "POST" and path == "/api/bulk":
            body = self._payload()
            shown = body.get("view") if isinstance(body.get("view"), dict) else {}
            view, page = View.read(shown), self._page(shown.get("page"))
            changed = inventory.bulk(db, STAMP, user["id"], str(body.get("action") or ""),
                                     body.get("skus", []))
            return 200, {"changed": changed, "listing": inventory.listing(db, view, page)}

        match = INLINE_PATH.fullmatch(path)
        if verb == "POST" and match:
            level = inventory.parse_level(self._payload().get("stock"), "stock")
            return 200, inventory.save_inline(db, STAMP, user["id"], match.group(1), level)

        match = ITEM_PATH.fullmatch(path)
        if verb == "GET" and match:
            item = inventory.item_details(db, match.group(1))
            if item is None:
                raise Rejected(404, "No item with that SKU.")
            notice = inventory.take_notice(db) if query.get("notice") == "take" else None
            return 200, {"item": item, "notice": notice}

        if verb == "POST" and path == "/api/adjustments":
            body = self._payload()
            ref = RowRef.read(body)
            level = inventory.parse_level(body.get("new_stock"), "new_stock")
            return 200, inventory.commit_adjustment(db, STAMP, user["id"], ref, level)

        if verb == "GET" and path == "/api/log":
            return 200, {"entries": inventory.journal(db)}

        raise Rejected(404, "Not found.")

    @staticmethod
    def _page(raw) -> int:
        text = str(raw if raw is not None else "1").strip() or "1"
        if not re.fullmatch(r"\d{1,6}", text) or int(text) < 1:
            raise Rejected(422, "Pages are numbered from 1.", "page")
        return int(text)


def main() -> None:
    global STORE  # noqa: PLW0603 - the one store, opened once at start
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("stackroom: DATABASE_URL is required: the PostgreSQL connection URL of the "
                 "database this server owns")
    STORE = Store(url, SESSION_CAP)
    STORE.wait_for_server()
    STORE.install()
    server = ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), int(os.environ["PORT"])),
                                 StockroomHandler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
