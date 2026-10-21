"""Helpline — a broadband provider's care desk. Server-rendered pages plus HTML fragments.

Full pages are rendered here. Interactions inside a page (replies, status changes,
hand-offs, the merge dialog, tabs, queue filters) are fetched by `static/desk.js`, and the
server answers with HTML fragments: one element per part of the page that changed, each
carrying the id of the element it replaces. A response header `X-Desk-Url` tells the
script which address the page now represents (it is applied with `history.replaceState`).
Without the script every form still works: a request without `X-Desk-Fragment: 1` gets a
redirect (or a full page) instead of fragments.

Agents sign in; each browser's data lives in a MongoDB database of its own (`store.py`).
Standard library plus the suite's vendored pymongo.

Environment: PORT (required), MONGODB_URL (required), HOST, WTA_VOCAB, WTA_FIXED_NOW,
WTA_SESSION_CAP, WTA_TEST_HOOKS.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "python"))
from bson import json_util  # noqa: E402 - the vendored driver's BSON helpers

import desk  # noqa: E402
import views  # noqa: E402
from store import SessionStore  # noqa: E402

APP_ID = "helpline"
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
SEED = json.loads((ROOT.parent / "seed" / "desk.json").read_text(encoding="utf-8"))
views.VOCAB.update(json.loads((ROOT.parent / "vocab" / f"{os.environ.get('WTA_VOCAB') or 'default'}.json")
                              .read_text(encoding="utf-8")))
views.AGENT_NAMES.update({a["_id"]: a["name"] for a in SEED["agents"]})
LOGINS = {a["username"]: a for a in SEED["agents"]}
t = views.t
CLOCK = datetime.fromisoformat(os.environ.get("WTA_FIXED_NOW") or "2026-06-10T09:00:00")  # wta-clock
NOW = CLOCK.strftime("%Y-%m-%d %H:%M")
TODAY = CLOCK.strftime("%Y-%m-%d")
TEST_HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
STORE: SessionStore | None = None  # opened by main()


# -- requests and responses ------------------------------------------------------------------


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    form: dict[str, str]
    fragment: bool
    raw: bytes = b""
    db: object = None
    me: dict = field(default_factory=dict)
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


def reply_json(payload: dict) -> Response:
    return Response(200, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")


def message(req: Request, name: str = "error") -> str:
    """A vocabulary message named in the query string (after a redirect), or ''."""
    key = req.arg(name)
    return t(key) if key.startswith("msg.") and key in views.VOCAB else ""


# -- routing ---------------------------------------------------------------------------------

ROUTES: list[tuple[str, re.Pattern, object, bool]] = []


def route(method: str, pattern: str, *, lead: bool = False):
    def register(handler):
        ROUTES.append((method, re.compile(pattern + r"\Z"), handler, lead))
        return handler
    return register


def counts(req: Request) -> dict[str, int]:
    return desk.counts(req.db)


@route("GET", r"/")
def home(req: Request) -> Response:
    return redirect("/queue")


# The queue.

def _filters(req: Request) -> tuple[str, str]:
    return desk.normalise_filters(req.arg("status", "open"), req.arg("assignee", "any"))


def _queue_parts(db, status: str, assignee: str, flash: str = "") -> list[str]:
    rows = desk.queue(db, status, assignee)
    return [views.queue_rows(db, rows, status, assignee), views.queue_count(len(rows)), views.queue_flash(flash)]


@route("GET", r"/queue")
def queue_page(req: Request) -> Response:
    status, assignee = _filters(req)
    flash = t("msg.ticket_deleted").format(id=req.arg("deleted")) if req.arg("deleted").isdigit() else ""
    rows = desk.queue(req.db, status, assignee)
    return html(views.queue_page(req.db, rows, status, assignee, counts(req), req.me, flash))


@route("GET", r"/queue/rows")
def queue_rows(req: Request) -> Response:
    status, assignee = _filters(req)
    if not req.fragment:
        return redirect(views.queue_url(status, assignee))
    return fragments(*_queue_parts(req.db, status, assignee), url=views.queue_url(status, assignee))


@route("POST", r"/queue/handoff/(\d+)")
def queue_hand_off(req: Request) -> Response:
    status, assignee = _filters(req)
    ticket_id = int(req.params[0])
    try:
        desk.hand_off_many(req.db, [ticket_id], NOW, req.me["_id"])
        flash = t("msg.queue_handed_off").format(id=ticket_id)
    except desk.Refusal as refusal:
        flash = t(refusal.key)
    if not req.fragment:
        return redirect(views.queue_url(status, assignee))
    return fragments(*_queue_parts(req.db, status, assignee, flash), views.desk_counts(counts(req)),
                     url=views.queue_url(status, assignee))


# A ticket.

def _tab(value: str) -> str:
    return value if value in views.TABS else "conversation"


def _ticket_or_404(req: Request):
    row = desk.ticket(req.db, int(req.params[0]))
    if row is None:
        return None, html(views.not_found(counts(req), req.me), 404)
    return row, None


@route("GET", r"/tickets/(\d+)")
def ticket_page(req: Request) -> Response:
    row, missing = _ticket_or_404(req)
    if missing:
        return missing
    return html(views.ticket_page(req.db, row, _tab(req.arg("tab")), counts(req), req.me, CLOCK))


@route("GET", r"/tickets/(\d+)/tab/(\w+)")
def ticket_tab(req: Request) -> Response:
    row, missing = _ticket_or_404(req)
    if missing:
        return missing
    tab = _tab(req.params[1])
    if not req.fragment:
        return redirect(views.ticket_url(row["_id"], tab))
    return fragments(views.ticket_tabs(row["_id"], tab), views.tab_panel(req.db, row, tab, CLOCK),
                     views.ticket_flash(""), url=views.ticket_url(row["_id"], tab))


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
            text = operation(req, row["_id"])
            changed = True
        except desk.Refusal as refusal:
            text, changed = t(refusal.key), False
        if not req.fragment:
            return redirect(views.ticket_url(row["_id"], tab))
        fresh = desk.ticket(req.db, row["_id"])
        parts = [views.ticket_summary(req.db, fresh), views.ticket_flash(text), views.ticket_actions(req.db, fresh),
                 views.merge_dialog(req.db, fresh), views.desk_counts(counts(req))]
        if changed and (refresh_panel or tab != "conversation" or fresh["status"] == "closed"):
            parts.append(views.tab_panel(req.db, fresh, tab, CLOCK))
        return fragments(*parts)
    return handler


def _reply(req, ticket_id):
    desk.send_reply(req.db, ticket_id, req.form.get("reply_body", ""), NOW, req.me["_id"])
    return t("msg.reply_sent")


def _customer_reply(req, ticket_id):
    desk.log_customer_reply(req.db, ticket_id, req.form.get("customer_body", ""), NOW, req.me["_id"])
    return t("msg.customer_logged")


def _move(req, ticket_id):
    desk.move(req.db, ticket_id, req.form.get("to", ""), NOW, req.me["_id"])
    return t("msg.status_changed")


def _resolve(req, ticket_id):
    desk.resolve(req.db, ticket_id, NOW, req.me["_id"])
    return t("msg.status_changed")


def _close(req, ticket_id):
    desk.close(req.db, ticket_id, NOW, req.me["_id"])
    return t("msg.status_changed")


def _hand_off(req, ticket_id):
    desk.hand_off(req.db, ticket_id, NOW, req.me["_id"])
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
        merged = desk.merge(req.db, row["_id"], req.form.get("merge_source", ""), NOW, req.me["_id"])
    except desk.Refusal as refusal:
        if not req.fragment:
            return redirect(views.ticket_url(row["_id"], tab))
        return fragments(views.merge_dialog(req.db, row, error=t(refusal.key), open_=True))
    if not req.fragment:
        return redirect(views.ticket_url(row["_id"], tab))
    fresh = desk.ticket(req.db, row["_id"])
    parts = [views.ticket_summary(req.db, fresh), views.ticket_flash(t("msg.merged").format(other=merged)),
             views.ticket_actions(req.db, fresh), views.merge_dialog(req.db, fresh), views.desk_counts(counts(req))]
    if tab != "conversation":
        parts.append(views.tab_panel(req.db, fresh, tab, CLOCK))
    return fragments(*parts)


@route("POST", r"/tickets/(\d+)/delete")
def ticket_delete(req: Request) -> Response:
    try:
        row = desk.delete_ticket(req.db, int(req.params[0]))
    except desk.Refusal:
        return redirect("/queue")
    return redirect(f"/queue?deleted={row['_id']}")


# A new ticket: choose the customer, then the details, then review.

TICKET_FIELDS = ("subject", "channel", "priority", "message")


@route("GET", r"/tickets/new")
def new_ticket(req: Request) -> Response:
    listing = desk.directory(req.db, req.arg("q")[:200], "name", 1)
    return html(views.new_ticket_customer(listing["rows"], req.arg("q"), counts(req), req.me))


def _ticket_values(source: dict) -> dict:
    return {k: str(source.get(k, "")) for k in TICKET_FIELDS}


@route("GET", r"/tickets/new/details")
def new_ticket_details(req: Request) -> Response:
    who = desk.customer(req.db, req.arg("customer"))
    if who is None:
        return redirect("/tickets/new")
    return html(views.new_ticket_details(who, _ticket_values(req.query), message(req), counts(req), req.me))


@route("POST", r"/tickets/new/review")
def new_ticket_check(req: Request) -> Response:
    values = _ticket_values(req.form)
    customer = req.form.get("customer", "")
    try:
        desk.check_new_ticket(req.db, customer, values["subject"], values["channel"], values["priority"],
                              values["message"])
    except desk.Refusal as refusal:
        if refusal.key == "msg.choose_customer":
            return redirect("/tickets/new")
        return redirect("/tickets/new/details?" + urlencode({"customer": customer, "error": refusal.key, **values}))
    return redirect("/tickets/new/review?" + urlencode({"customer": customer, **values}))


@route("GET", r"/tickets/new/review")
def new_ticket_review(req: Request) -> Response:
    who = desk.customer(req.db, req.arg("customer"))
    if who is None:
        return redirect("/tickets/new")
    values = _ticket_values(req.query)
    try:
        desk.check_new_ticket(req.db, who["_id"], values["subject"], values["channel"], values["priority"],
                              values["message"])
    except desk.Refusal as refusal:
        return redirect("/tickets/new/details?" + urlencode({"customer": who["_id"], "error": refusal.key, **values}))
    return html(views.new_ticket_review(who, values, counts(req), req.me))


@route("POST", r"/tickets/new")
def new_ticket_create(req: Request) -> Response:
    values = _ticket_values(req.form)
    customer = req.form.get("customer", "")
    try:
        ticket_id = desk.create_ticket(req.db, customer, values["subject"], values["channel"], values["priority"],
                                       values["message"], NOW, req.me["_id"])
    except desk.Refusal as refusal:
        return redirect("/tickets/new/details?" + urlencode({"customer": customer, "error": refusal.key, **values}))
    return redirect(f"/tickets/{ticket_id}")


# Customers.

CUSTOMER_FIELDS = ("name", "email", "phone", "plan", "line1", "town", "postcode")


@route("GET", r"/customers")
def customers(req: Request) -> Response:
    sort = req.arg("sort") if req.arg("sort") in desk.CUSTOMER_SORTS else "name"
    page = int(req.arg("page")) if re.fullmatch(r"\d{1,4}", req.arg("page")) else 1
    q = req.arg("q")[:200]
    flash = t("msg.customer_deleted") if req.arg("done") == "deleted" else ""
    return html(views.customers_page(desk.directory(req.db, q, sort, page), q, sort, counts(req), req.me, flash))


@route("GET", r"/customers/new")
def customer_new_form(req: Request) -> Response:
    values = {k: req.arg(k) for k in CUSTOMER_FIELDS if k in req.query}
    return html(views.customer_form(None, values, message(req), counts(req), req.me))


@route("POST", r"/customers/new")
def customer_create(req: Request) -> Response:
    try:
        customer_id = desk.add_customer(req.db, req.form, TODAY)
    except desk.Refusal as refusal:
        values = {k: req.form.get(k, "") for k in CUSTOMER_FIELDS}
        return redirect("/customers/new?" + urlencode({"error": refusal.key, **values}))
    return redirect(f"/customers/{customer_id}?done=added")


def _customer_or_404(req: Request):
    who = desk.customer(req.db, req.params[0])
    if who is None:
        return None, html(views.not_found(counts(req), req.me), 404)
    return who, None


@route("GET", r"/customers/(c\d+)")
def customer_page(req: Request) -> Response:
    who, missing = _customer_or_404(req)
    if missing:
        return missing
    flash = message(req) or {"added": t("msg.customer_added"), "saved": t("msg.customer_saved")}.get(req.arg("done"), "")
    return html(views.customer_page(req.db, who, counts(req), req.me, flash))


@route("GET", r"/customers/(c\d+)/edit")
def customer_edit_form(req: Request) -> Response:
    who, missing = _customer_or_404(req)
    if missing:
        return missing
    values = {k: req.arg(k) for k in CUSTOMER_FIELDS if k in req.query}
    return html(views.customer_form(who, values, message(req), counts(req), req.me))


@route("POST", r"/customers/(c\d+)/edit")
def customer_update(req: Request) -> Response:
    who, missing = _customer_or_404(req)
    if missing:
        return missing
    try:
        desk.update_customer(req.db, who["_id"], req.form)
    except desk.Refusal as refusal:
        values = {k: req.form.get(k, "") for k in CUSTOMER_FIELDS}
        return redirect(f"/customers/{who['_id']}/edit?" + urlencode({"error": refusal.key, **values}))
    return redirect(f"/customers/{who['_id']}?done=saved")


@route("POST", r"/customers/(c\d+)/delete")
def customer_delete(req: Request) -> Response:
    try:
        desk.delete_customer(req.db, req.params[0])
    except desk.Refusal as refusal:
        return redirect(f"/customers/{req.params[0]}?error={refusal.key}")
    return redirect("/customers?done=deleted")


# Saved replies.

@route("GET", r"/replies")
def replies(req: Request) -> Response:
    flash = message(req) or (t("msg.reply_deleted") if req.arg("done") == "deleted" else "")
    return html(views.replies_page(desk.replies(req.db), counts(req), req.me, flash))


@route("POST", r"/replies")
def reply_add(req: Request) -> Response:
    try:
        desk.add_reply(req.db, req.form.get("title", ""), req.form.get("body", ""), TODAY, req.me["_id"])
    except desk.Refusal as refusal:
        return redirect(f"/replies?error={refusal.key}")
    return redirect("/replies")


@route("POST", r"/replies/(r\d+)/(up|down)")
def reply_move(req: Request) -> Response:
    try:
        desk.move_reply(req.db, req.params[0], req.params[1])
    except desk.Refusal:
        pass
    return redirect("/replies")


@route("POST", r"/replies/(r\d+)/delete")
def reply_delete(req: Request) -> Response:
    try:
        desk.delete_reply(req.db, req.params[0])
    except desk.Refusal:
        return redirect("/replies")
    return redirect("/replies?done=deleted")


@route("POST", r"/replies/(r\d+)/title")
def reply_rename(req: Request) -> Response:
    """The in-place title editor's save: JSON in, JSON out (never an error status)."""
    try:
        body = json.loads(req.raw.decode("utf-8") or "{}")
    except ValueError:
        body = {}
    try:
        title = desk.rename_reply(req.db, req.params[0], str((body or {}).get("title", "")), TODAY)
    except desk.Refusal as refusal:
        return reply_json({"ok": False, "error": t(refusal.key)})
    return reply_json({"ok": True, "title": title})


# The team, and the lead's report.

@route("GET", r"/team")
def team(req: Request) -> Response:
    return html(views.team_page(desk.agents(req.db), counts(req), req.me))


@route("GET", r"/reports", lead=True)
def reports(req: Request) -> Response:
    return html(views.reports_page(req.db, desk.breaches(req.db, CLOCK), counts(req), req.me))


# -- signing in ------------------------------------------------------------------------------


def verify(username: str, password: str) -> str | None:
    """The agent these credentials belong to (PBKDF2, seeded salts), or None."""
    agent = LOGINS.get(username)
    salt = bytes.fromhex(agent["pw"]["salt"]) if agent else b"\x5a" * 16  # unknown names pay the same
    rounds = agent["pw"]["iter"] if agent else 20000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds).hex()
    return agent["_id"] if agent and hmac.compare_digest(digest, agent["pw"]["hash"]) else None


def next_path(raw: str) -> str:
    value = (raw or "").strip()
    if re.fullmatch(r"/(?!/)[^\s\\]*", value) and not value.startswith(("/login", "/logout")):
        return value
    return "/queue"


def state(db, sid, agent_id) -> dict:
    if db is None:
        return {"session": None, "user": None, "committed": None, "sha": None}
    committed = json.loads(json_util.dumps(desk.committed(db)))
    sha = hashlib.sha256(json.dumps(committed, sort_keys=True).encode()).hexdigest()
    user = next((a["username"] for a in SEED["agents"] if a["_id"] == agent_id), None)
    return {"session": sid, "user": user, "committed": committed, "sha": sha}


# -- HTTP plumbing ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "Helpline/2.0"

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

    def _cookie(self) -> str | None:
        try:
            jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return None
        return jar["wta_sid"].value if "wta_sid" in jar else None

    def _serve(self, method: str) -> None:  # noqa: PLR0911 - one exit per kind of request
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/healthz":  # ok only while the database answers
            ok = STORE.alive()
            return self._send(Response(200 if ok else 503, json.dumps({"ok": ok, "app": APP_ID}).encode(),
                                       "application/json"))
        if path.startswith("/static/"):
            return self._send(self._static(path[len("/static/"):]))
        query = {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        raw = b""
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
        form = {k: v[-1] for k, v in parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True).items()}
        sid = self._cookie()
        if path == "/help/sign-in":
            return self._send(html(views.sign_in_help()))
        if path == "/login":
            return self._sign_in(method, sid, query, form)
        if path == "/logout" and method == "POST":
            STORE.sign_out(sid)
            return self._send(redirect("/login"))
        if path == "/__wta/state" and not TEST_HOOKS:
            return self._send(html(b"not found", 404))
        req = Request(method, path, query, form, self.headers.get("X-Desk-Fragment") == "1", raw)
        try:
            with STORE.open(sid) as (db, known_sid, agent_id):
                if path == "/__wta/state":
                    response = reply_json(state(db, known_sid, agent_id))
                elif db is None or agent_id is None:
                    response = self._signed_out(req)
                else:
                    req.db = db
                    req.me = db.agents.find_one({"_id": agent_id}, {"pw": 0})
                    response = self._route(req)
        except Exception:  # noqa: BLE001 - a defect: logged, and answered like any web server would
            traceback.print_exc(file=sys.stderr)
            response = html(views.server_error(), 500)
        self._send(response)

    def _route(self, req: Request) -> Response:
        for verb, pattern, handler, lead in ROUTES:
            match = pattern.match(req.path)
            if verb == req.method and match:
                req.params = match.groups()
                if lead and "lead" not in req.me["roles"]:
                    return html(views.access_notice(counts(req), req.me))
                return handler(req)
        return html(views.not_found(counts(req), req.me), 404)

    def _signed_out(self, req: Request) -> Response:
        if req.fragment or req.path.endswith("/title"):
            response = reply_json({"ok": False, "reason": "signed-out"})
            response.headers["X-Desk-Signed-Out"] = "1"
            return response
        wanted = self.path if req.method == "GET" else "/queue"
        return redirect("/login?next=" + quote(wanted, safe=""))

    def _sign_in(self, method: str, sid: str | None, query: dict, form: dict) -> None:
        if method != "POST":
            with STORE.open(sid) as (_db, _sid, agent_id):
                signed_in = agent_id is not None
            if signed_in:  # where Back to the sign-in page lands once signed in
                return self._send(redirect(next_path(query.get("next", ""))))
            return self._send(html(views.sign_in(query.get("next", ""))))
        username, password = form.get("username", "").strip(), form.get("password", "")
        wanted = form.get("next") or query.get("next", "")
        agent_id = verify(username, password) if username and password else None
        if agent_id is None:  # one message whatever was wrong; no session is created
            return self._send(html(views.sign_in(wanted, username, refused=True)))
        fresh = STORE.sign_in(sid, agent_id)
        return self._send(redirect(next_path(wanted)), fresh)

    def _static(self, name: str) -> Response:
        target = (STATIC / name).resolve()
        if STATIC not in target.parents or not target.is_file() or target.suffix not in STATIC_TYPES:
            return Response(404, b"not found", "text/plain; charset=utf-8")
        return Response(200, target.read_bytes(), STATIC_TYPES[target.suffix])


def main() -> None:
    global STORE  # noqa: PLW0603 - opened once, at start
    url = os.environ.get("MONGODB_URL")
    if not url:
        sys.exit("helpline: MONGODB_URL is required: the connection string of the database this server owns")
    STORE = SessionStore(url, int(os.environ.get("WTA_SESSION_CAP") or 32), desk.template(SEED))
    STORE.wait()
    STORE.reset()
    server = ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), int(os.environ["PORT"])), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
