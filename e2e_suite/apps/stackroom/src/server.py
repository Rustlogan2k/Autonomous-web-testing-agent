"""Stackroom — inventory administration for a hardware wholesaler's stockroom.

A data grid over a JSON API with server-side filtering, sorting and paging. Every page route
(/stock, /items/<sku>, /items/<sku>/adjust, /log) is the same HTML shell; the client script
renders the route from the path, and the grid keeps its state in its own query string.
Python standard library and SQLite only.

Environment: PORT (required), HOST, WTA_VOCAB, WTA_FIXED_NOW, WTA_DATA_DIR, WTA_TEST_HOOKS.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inventory  # noqa: E402
from inventory import Rejected, RowRef, View  # noqa: E402

APP_ID = "stackroom"
HERE = Path(__file__).resolve().parent
ASSETS = HERE / "static"
PROFILE = os.environ.get("WTA_VOCAB", "default")
WORDS = json.loads((HERE.parent / "vocab" / f"{PROFILE}.json").read_text(encoding="utf-8"))
CLOCK = dt.datetime.fromisoformat(os.environ.get("WTA_FIXED_NOW", "2026-06-10T09:00:00"))  # wta-clock
STAMP = CLOCK.strftime("%Y-%m-%d %H:%M")
STORE = Path(os.environ.get("WTA_DATA_DIR") or tempfile.mkdtemp(prefix="stackroom-")) / "stackroom.db"
HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
GUARD = threading.Lock()

PAGE_ROUTES = (re.compile(r"/stock"), re.compile(r"/items/SKU-\d+"), re.compile(r"/items/SKU-\d+/adjust"),
               re.compile(r"/log"))
ASSET_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
ITEM_PATH = re.compile(r"/api/items/(SKU-\d+)")
INLINE_PATH = re.compile(r"/api/items/(SKU-\d+)/stock")


def shell() -> bytes:
    """The one HTML document every page route serves, with this profile's labels inlined."""
    words = json.dumps(WORDS, ensure_ascii=False).replace("</", "<\\/")
    page = (ASSETS / "shell.html").read_text(encoding="utf-8")
    return (page.replace("{{TITLE}}", WORDS["app.title"]).replace("{{WORDS}}", words)
            .encode("utf-8"))


def database() -> sqlite3.Connection:
    conn = sqlite3.connect(STORE, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


class StockroomHandler(BaseHTTPRequestHandler):
    server_version = "Stackroom/1.0"

    def log_message(self, format, *args):  # noqa: A002 - keep test output quiet
        return

    # -- plumbing --------------------------------------------------------------------------

    def _reply(self, status: int, body: bytes, kind: str, extra: dict | None = None) -> None:
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

    def _session(self, conn: sqlite3.Connection) -> str:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie") or "")
        except Exception:  # noqa: BLE001 - a malformed cookie header is just no cookie
            jar = SimpleCookie()
        morsel = jar.get("wta_sid")
        if morsel is not None and inventory.session_exists(conn, morsel.value):
            return morsel.value
        sid = "sr{:05d}".format(inventory.bump(conn, "sessions"))
        inventory.open_session(conn, sid)
        self._issued_sid = sid
        return sid

    def _payload(self) -> dict:
        size = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(size) if size else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError) as exc:
            raise Rejected(400, "The request body is not JSON.") from exc
        if not isinstance(data, dict):
            raise Rejected(400, "The request body must be a JSON object.")
        return data

    # -- entry points ------------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def _handle(self) -> None:
        self._issued_sid = ""
        parts = urlsplit(self.path)
        path, query = parts.path, {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        if self.command == "GET" and path == "/healthz":
            return self._reply_json(200, {"ok": True, "app": APP_ID})
        if self.command == "GET" and path == "/":
            return self._reply(303, b"", "text/plain; charset=utf-8", {"Location": "/stock"})
        if self.command == "GET" and path.startswith("/assets/"):
            return self._asset(path[len("/assets/"):])
        is_page = self.command == "GET" and any(r.fullmatch(path) for r in PAGE_ROUTES)
        if not is_page and not path.startswith(("/api/", "/__wta/")):
            return self._reply(404, b"Not found", "text/plain; charset=utf-8")
        with GUARD:
            conn = database()
            try:
                sid = self._session(conn)
                conn.commit()  # a new session is kept even when its first call is refused
                status, payload = (200, None) if is_page else self._api(conn, sid, path, query)
                conn.commit()
            except Rejected as exc:
                conn.rollback()
                status, payload = exc.status, {"error": exc.message, "field": exc.field}
            finally:
                conn.close()
        if is_page:
            return self._reply(200, shell(), "text/html; charset=utf-8")
        return self._reply_json(status, payload)

    def _asset(self, name: str) -> None:
        target = (ASSETS / name).resolve()
        if target.parent != ASSETS or target.suffix not in ASSET_TYPES or not target.is_file():
            return self._reply(404, b"Not found", "text/plain; charset=utf-8")
        return self._reply(200, target.read_bytes(), ASSET_TYPES[target.suffix])

    # -- the JSON API --------------------------------------------------------------------------

    def _api(self, conn, sid: str, path: str, query: dict):  # noqa: PLR0911
        verb = self.command
        if verb == "GET" and path == "/__wta/state":
            if not HOOKS:
                raise Rejected(404, "Not found.")
            state = inventory.committed(conn, sid)
            digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            return 200, {"session": sid, "committed": state, "sha": digest}

        if verb == "GET" and path == "/api/listing":
            return 200, inventory.listing(conn, sid, View.read(query), self._page(query.get("page")))

        if verb == "POST" and path == "/api/bulk":
            body = self._payload()
            shown = body.get("view") if isinstance(body.get("view"), dict) else {}
            view, page = View.read(shown), self._page(shown.get("page"))
            changed = inventory.bulk(conn, sid, STAMP, str(body.get("action") or ""), body.get("skus", []))
            return 200, {"changed": changed, "listing": inventory.listing(conn, sid, view, page)}

        match = INLINE_PATH.fullmatch(path)
        if verb == "POST" and match:
            level = inventory.parse_level(self._payload().get("stock"), "stock")
            return 200, inventory.save_inline(conn, sid, STAMP, match.group(1), level)

        match = ITEM_PATH.fullmatch(path)
        if verb == "GET" and match:
            item = inventory.find_item(conn, sid, match.group(1))
            if item is None:
                raise Rejected(404, "No item with that SKU.")
            notice = inventory.take_notice(conn, sid) if query.get("notice") == "take" else None
            return 200, {"item": item, "notice": notice}

        if verb == "POST" and path == "/api/adjustments":
            body = self._payload()
            ref = RowRef.read(body)
            level = inventory.parse_level(body.get("new_stock"), "new_stock")
            return 200, inventory.commit_adjustment(conn, sid, STAMP, ref, level)

        if verb == "GET" and path == "/api/log":
            return 200, {"entries": inventory.journal(conn, sid)}

        raise Rejected(404, "Not found.")

    @staticmethod
    def _page(raw) -> int:
        text = str(raw if raw is not None else "1").strip() or "1"
        if not re.fullmatch(r"\d{1,6}", text) or int(text) < 1:
            raise Rejected(422, "Pages are numbered from 1.", "page")
        return int(text)


def main() -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    conn = database()
    conn.executescript(inventory.TABLES)
    conn.commit()
    conn.close()
    server = ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), int(os.environ["PORT"])),
                                 StockroomHandler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
