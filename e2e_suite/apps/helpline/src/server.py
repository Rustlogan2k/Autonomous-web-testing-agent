"""Helpline — a broadband provider's care desk. Server-rendered pages plus HTML fragments.

Full pages are rendered here. Interactions inside a page (replies, status changes,
hand-offs, the merge dialog, tabs, queue filters) are fetched by `static/desk.js`, and the
server answers with HTML fragments: one element per part of the page that changed, each
carrying the id of the element it replaces. A response header `X-Desk-Url` tells the
script which address the page now represents (it is applied with `history.replaceState`).

Without the script every form still works: a request without `X-Desk-Fragment: 1` gets a
redirect (or a full page) instead of fragments.

Standard library only. Each browser session (`wta_sid` cookie) has its own SQLite file,
created from the seed on its first request.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import desk
import views
from store import SessionStore

APP_ID = "helpline"
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
SEED = json.loads((ROOT.parent / "seed" / "desk.json").read_text(encoding="utf-8"))
views.VOCAB.update(json.loads((ROOT.parent / "vocab" / f"{os.environ.get('WTA_VOCAB') or 'default'}.json")
                              .read_text(encoding="utf-8")))
t = views.t
CLOCK = datetime.fromisoformat(os.environ.get("WTA_FIXED_NOW") or "2026-06-10T09:00:00")  # wta-clock
NOW = CLOCK.strftime("%Y-%m-%d %H:%M")
TEST_HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}


# -- requests and responses ------------------------------------------------------------------


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    form: dict[str, str]
    fragment: bool
    conn: object = None
    params: tuple = ()

    def arg(self, name: str, default: str = "") -> str:
        return self.query.get(name, default)


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


def html(body: bytes, status: int = 200) -> Response:
    return Response(status, body)


def fragments(*parts: str, url: str = "") -> Response:
    response = Response(200, "\n".join(parts).encode("utf-8"))
    if url:
        response.headers["X-Desk-Url"] = url
    return response


def redirect(location: str) -> Response:
    return Response(303, b"", headers={"Location": location})


# -- routing ---------------------------------------------------------------------------------

ROUTES: list[tuple[str, re.Pattern, object]] = []


def route(method: str, pattern: str):
    def register(handler):
        ROUTES.append((method, re.compile(pattern + r"\Z"), handler))
        return handler
    return register


@route("GET", r"/")
def home(req: Request) -> Response:
    return redirect("/queue")


@route("GET", r"/__wta/state")
def state(req: Request) -> Response:
    if not TEST_HOOKS:
        return html(b"not found", 404)
    committed = desk.committed(req.conn)
    sha = hashlib.sha256(json.dumps(committed, sort_keys=True).encode()).hexdigest()
    body = json.dumps({"session": req.params[0], "committed": committed, "sha": sha}).encode()
    return Response(200, body, "application/json")


# The queue.

def _filters(req: Request) -> tuple[str, str]:
    return desk.normalise_filters(req.arg("status", "open"), req.arg("assignee", "any"))


def _queue_parts(conn, status: str, assignee: str, flash: str = "") -> list[str]:
    rows = desk.queue(conn, status, assignee)
    return [views.queue_rows(rows, status, assignee), views.queue_count(len(rows)), views.queue_flash(flash)]


@route("GET", r"/queue")
def queue_page(req: Request) -> Response:
    status, assignee = _filters(req)
    rows = desk.queue(req.conn, status, assignee)
    return html(views.queue_page(rows, status, assignee, desk.counts(req.conn)))


@route("GET", r"/queue/rows")
def queue_rows(req: Request) -> Response:
    status, assignee = _filters(req)
    if not req.fragment:
        return redirect(views.queue_url(status, assignee))
    return fragments(*_queue_parts(req.conn, status, assignee), url=views.queue_url(status, assignee))


@route("POST", r"/queue/handoff/(\d+)")
def queue_hand_off(req: Request) -> Response:
    status, assignee = _filters(req)
    ticket_id = int(req.params[1])
    try:
        desk.hand_off_many(req.conn, [ticket_id], NOW)
        flash = t("msg.queue_handed_off").format(id=ticket_id)
    except desk.Refusal as refusal:
        flash = t(refusal.key)
    if not req.fragment:
        return redirect(views.queue_url(status, assignee))
    return fragments(*_queue_parts(req.conn, status, assignee, flash), views.desk_counts(desk.counts(req.conn)),
                     url=views.queue_url(status, assignee))


# A ticket.

def _tab(value: str) -> str:
    return value if value in views.TABS else "conversation"


def _ticket_or_404(req: Request):
    row = desk.ticket(req.conn, int(req.params[1]))
    if row is None:
        return None, html(views.not_found(desk.counts(req.conn)), 404)
    return row, None


@route("GET", r"/tickets/(\d+)")
def ticket_page(req: Request) -> Response:
    row, missing = _ticket_or_404(req)
    if missing:
        return missing
    return html(views.ticket_page(req.conn, row, _tab(req.arg("tab")), desk.counts(req.conn)))


@route("GET", r"/tickets/(\d+)/tab/(\w+)")
def ticket_tab(req: Request) -> Response:
    row, missing = _ticket_or_404(req)
    if missing:
        return missing
    tab = _tab(req.params[2])
    if not req.fragment:
        return redirect(views.ticket_url(row["id"], tab))
    return fragments(views.ticket_tabs(row["id"], tab), views.tab_panel(req.conn, row, tab),
                     views.ticket_flash(""), url=views.ticket_url(row["id"], tab))


def _ticket_action(operation, *, refresh_panel: bool = False):
    """Wrap a desk operation as a ticket action answered with the parts of the page it changed.

    The conversation panel is re-rendered only when the action changed the conversation (or
    closed the ticket, which removes the reply forms), so a draft typed into one of its
    forms survives an unrelated status change.
    """
    def handler(req: Request) -> Response:
        row, missing = _ticket_or_404(req)
        if missing:
            return missing
        tab = _tab(req.form.get("_tab", ""))
        try:
            message = operation(req, row["id"])
            changed = True
        except desk.Refusal as refusal:
            message, changed = t(refusal.key), False
        if not req.fragment:
            return redirect(views.ticket_url(row["id"], tab))
        fresh = desk.ticket(req.conn, row["id"])
        parts = [views.ticket_summary(req.conn, fresh), views.ticket_flash(message), views.ticket_actions(fresh),
                 views.merge_dialog(req.conn, fresh), views.desk_counts(desk.counts(req.conn))]
        if changed and (refresh_panel or tab != "conversation" or fresh["status"] == "closed"):
            parts.append(views.tab_panel(req.conn, fresh, tab))
        return fragments(*parts)
    return handler


def _reply(req, ticket_id):
    desk.send_reply(req.conn, ticket_id, req.form.get("reply_body", ""), NOW)
    return t("msg.reply_sent")


def _customer_reply(req, ticket_id):
    desk.log_customer_reply(req.conn, ticket_id, req.form.get("customer_body", ""), NOW)
    return t("msg.customer_logged")


def _move(req, ticket_id):
    desk.move(req.conn, ticket_id, req.form.get("to", ""), NOW)
    return t("msg.status_changed")


def _resolve(req, ticket_id):
    desk.resolve(req.conn, ticket_id, NOW)
    return t("msg.status_changed")


def _close(req, ticket_id):
    desk.close(req.conn, ticket_id, NOW)
    return t("msg.status_changed")


def _hand_off(req, ticket_id):
    desk.hand_off(req.conn, ticket_id, NOW)
    return t("msg.handed_off")


route("POST", r"/tickets/(\d+)/reply")(_ticket_action(_reply, refresh_panel=True))
route("POST", r"/tickets/(\d+)/customer-reply")(_ticket_action(_customer_reply, refresh_panel=True))
route("POST", r"/tickets/(\d+)/move")(_ticket_action(_move))
route("POST", r"/tickets/(\d+)/resolve")(_ticket_action(_resolve))
route("POST", r"/tickets/(\d+)/close")(_ticket_action(_close))
route("POST", r"/tickets/(\d+)/handoff")(_ticket_action(_hand_off))


@route("POST", r"/tickets/(\d+)/merge")
def ticket_merge(req: Request) -> Response:
    row, missing = _ticket_or_404(req)
    if missing:
        return missing
    tab = _tab(req.form.get("_tab", ""))
    try:
        merged = desk.merge(req.conn, row["id"], req.form.get("merge_source", ""), NOW)
    except desk.Refusal as refusal:
        if not req.fragment:
            return redirect(views.ticket_url(row["id"], tab))
        return fragments(views.merge_dialog(req.conn, row, error=t(refusal.key), open_=True))
    if not req.fragment:
        return redirect(views.ticket_url(row["id"], tab))
    fresh = desk.ticket(req.conn, row["id"])
    parts = [views.ticket_summary(req.conn, fresh), views.ticket_flash(t("msg.merged").format(other=merged)),
             views.ticket_actions(fresh), views.merge_dialog(req.conn, fresh),
             views.desk_counts(desk.counts(req.conn))]
    if tab != "conversation":
        parts.append(views.tab_panel(req.conn, fresh, tab))
    return fragments(*parts)


# -- HTTP plumbing ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "Helpline/1.0"
    store: SessionStore  # set in main()

    def log_message(self, fmt, *args):  # quiet: the harness reads its own logs
        pass

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")

    def _send(self, response: Response, cookie: str = "") -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in response.headers.items():
            self.send_header(name, value)
        if cookie:
            self.send_header("Set-Cookie", f"wta_sid={cookie}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    def _serve(self, method: str) -> None:
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/healthz":
            return self._send(Response(200, json.dumps({"ok": True, "app": APP_ID}).encode(), "application/json"))
        if path.startswith("/static/"):
            return self._send(self._static(path[len("/static/"):]))
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        sid = jar["wta_sid"].value if "wta_sid" in jar else ""
        fresh_cookie = ""
        if not self.store.known(sid):
            sid = fresh_cookie = self.store.create()
        query = {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        form: dict[str, str] = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
            form = {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        req = Request(method, path, query, form, self.headers.get("X-Desk-Fragment") == "1")
        for verb, pattern, handler in ROUTES:
            match = pattern.match(path)
            if verb == method and match:
                with self.store.open(sid) as conn:
                    req.conn, req.params = conn, (sid, *match.groups())
                    response = handler(req)
                return self._send(response, fresh_cookie)
        with self.store.open(sid) as conn:
            response = html(views.not_found(desk.counts(conn)), 404)
        self._send(response, fresh_cookie)

    def _static(self, name: str) -> Response:
        target = (STATIC / name).resolve()
        if STATIC not in target.parents or not target.is_file() or target.suffix not in STATIC_TYPES:
            return Response(404, b"not found", "text/plain; charset=utf-8")
        return Response(200, target.read_bytes(), STATIC_TYPES[target.suffix])


def main() -> None:
    data_dir = Path(os.environ.get("WTA_DATA_DIR") or tempfile.mkdtemp(prefix="helpline-"))
    Handler.store = SessionStore(data_dir, SEED)
    server = ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), int(os.environ["PORT"])), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
