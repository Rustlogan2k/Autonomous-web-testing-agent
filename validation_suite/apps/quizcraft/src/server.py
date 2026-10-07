"""Quizcraft: a cycling club secretary edits the members' annual survey.

The editor is a single page (/quiz). Every edit is a command executed in the browser against
an in-memory working copy, with undo and redo (static/editor.js); a preview pane walks the
survey as a member would, skip logic included (static/preview.js). Nothing is sent until
*Save revision*, which posts the whole document to /api/revisions; the server checks it and
appends it as the session's next revision. Reloading the page starts again from the latest
saved revision. /revisions lists every revision saved in this session.

Standard library only. All state lives in one SQLite database under WTA_DATA_DIR, its rows
partitioned by the `wta_sid` session cookie.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import traceback
from datetime import datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pages
import store
from survey import Invalid, check_document

APP_ID = "quizcraft"
SRC = Path(__file__).resolve().parent
STATIC = SRC / "static"
APP_ROOT = SRC.parent
SEED = json.loads((APP_ROOT / "seed" / "survey.json").read_text(encoding="utf-8"))
PROFILE = "alt" if os.environ.get("WTA_VOCAB") == "alt" else "default"
pages.VOCAB.update(json.loads((APP_ROOT / "vocab" / f"{PROFILE}.json").read_text(encoding="utf-8")))
pages.CLUB["club"] = SEED["club"]
CLOCK = datetime.fromisoformat(os.environ.get("WTA_FIXED_NOW") or "2026-06-10T09:00:00")  # wta-clock
NOW = CLOCK.isoformat(timespec="seconds")
TEST_HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
MAX_BODY = 256 * 1024
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
t = pages.t


class Reply:
    def __init__(self, status: int = 200, body: bytes = b"", content_type: str = "text/html; charset=utf-8",
                 location: str = "") -> None:
        self.status, self.body, self.content_type, self.location = status, body, content_type, location


def as_json(value: object) -> Reply:
    return Reply(body=json.dumps(value, ensure_ascii=False).encode("utf-8"),
                 content_type="application/json; charset=utf-8")


ROUTES: list[tuple[str, re.Pattern, object]] = []


def route(method: str, pattern: str):
    def register(handler):
        ROUTES.append((method, re.compile(pattern + r"\Z"), handler))
        return handler
    return register


@route("GET", r"/")
def landing(conn, sid: str, body: bytes) -> Reply:
    return Reply(303, location="/quiz")


@route("GET", r"/quiz")
def editor(conn, sid: str, body: bytes) -> Reply:
    number, document, _ = store.latest(conn, sid)
    return Reply(body=pages.quiz_page(number, document))


@route("POST", r"/api/revisions")
def save_revision(conn, sid: str, body: bytes) -> Reply:
    """Append the posted document as the next revision. Refusals are answered 200 {ok: false}."""
    try:
        posted = json.loads(body.decode("utf-8") or "null")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return as_json({"ok": False, "message": t("error.document")})
    if not isinstance(posted, dict):
        return as_json({"ok": False, "message": t("error.document")})
    try:
        document = check_document(posted.get("document"))
    except Invalid as problem:
        return as_json({"ok": False, "message": t(problem.key, n=problem.position)})
    number, _, _ = store.latest(conn, sid)
    if posted.get("base") != number:
        # Edited from an older revision (another tab saved meanwhile): never overwrite it.
        return as_json({"ok": False, "message": t("save.stale", n=number)})
    return as_json({"ok": True, "revision": store.append(conn, sid, document, NOW)})


@route("GET", r"/revisions")
def revisions(conn, sid: str, body: bytes) -> Reply:
    return Reply(body=pages.revisions_page(store.history(conn, sid)))


@route("GET", r"/help")
def help_view(conn, sid: str, body: bytes) -> Reply:
    return Reply(body=pages.help_page())


@route("GET", r"/__wta/state")
def wta_state(conn, sid: str, body: bytes) -> Reply:
    if not TEST_HOOKS:
        return Reply(404, pages.not_found())
    state = store.committed(conn, sid)
    sha = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return as_json({"session": sid, "committed": state, "sha": sha})


class Handler(BaseHTTPRequestHandler):
    server_version = "Quizcraft/1.0"
    store: store.Store

    def log_message(self, fmt: str, *args) -> None:  # the harness keeps its own logs
        pass

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def _handle(self, method: str) -> None:
        url = urlsplit(self.path)
        if url.path == "/healthz":
            return self._send(as_json({"ok": True, "app": APP_ID}))
        if url.path.startswith("/static/"):
            return self._send(self._static(url.path[len("/static/"):]))
        body = b""
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(as_json({"ok": False, "message": t("error.too_large")}))
            body = self.rfile.read(length) if length > 0 else b""
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie") or "")
        except cookies.CookieError:
            jar = cookies.SimpleCookie()
        asked = jar["wta_sid"].value if "wta_sid" in jar else ""
        try:
            with self.store.transaction() as conn:
                sid = self.store.session(conn, asked)
                reply = self._dispatch(method, url.path, conn, sid, body)
        except Exception:  # noqa: BLE001 - reported, never hidden behind a 200
            traceback.print_exc(file=sys.stderr)
            return self._send(Reply(500, b"internal error", "text/plain; charset=utf-8"))
        self._send(reply, sid if sid != asked else "")

    @staticmethod
    def _dispatch(method: str, path: str, conn, sid: str, body: bytes) -> Reply:
        for verb, pattern, handler in ROUTES:
            if verb == method and pattern.match(path):
                return handler(conn, sid, body)
        return Reply(404, pages.not_found())

    def _static(self, name: str) -> Reply:
        target = (STATIC / name).resolve()
        if STATIC not in target.parents or not target.is_file() or target.suffix not in STATIC_TYPES:
            return Reply(404, b"not found", "text/plain; charset=utf-8")
        return Reply(body=target.read_bytes(), content_type=STATIC_TYPES[target.suffix])

    def _send(self, reply: Reply, new_sid: str = "") -> None:
        self.send_response(reply.status)
        self.send_header("Content-Type", reply.content_type)
        self.send_header("Content-Length", str(len(reply.body)))
        self.send_header("Cache-Control", "no-store")
        if reply.location:
            self.send_header("Location", reply.location)
        if new_sid:
            self.send_header("Set-Cookie", f"wta_sid={new_sid}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()
        self.wfile.write(reply.body)


def main() -> None:
    data_dir = Path(os.environ.get("WTA_DATA_DIR") or tempfile.mkdtemp(prefix="quizcraft-"))
    data_dir.mkdir(parents=True, exist_ok=True)
    Handler.store = store.Store(data_dir / "quizcraft.sqlite3", check_document(SEED["document"]), SEED["saved_at"])
    port = int(os.environ["PORT"])
    server = ThreadingHTTPServer((os.environ.get("HOST") or "127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"{APP_ID} listening on {port} (vocabulary {PROFILE}, now {NOW}, data {data_dir})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
