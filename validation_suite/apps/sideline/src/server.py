"""Sideline: a Sunday football league's results, confirmations and table.

Three front doors over the same results, each a server-rendered portal with its own
actions: the league secretary (`/sec/...`) enters and corrects scores, each club's captain
(`/club/<club>/...`) confirms or disputes their club's results, and the public (`/table`,
`/results`, `/teams/<club>`) sees only what both clubs have confirmed. Every change is a
form post answered with a redirect to a page (post/redirect/get); the page after the
redirect shows a one-time message. The only script re-sorts the public table in place.

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
from urllib.parse import parse_qs, urlsplit

import pages
import results
import standings
from store import Store, committed, put_flash, take_flash

APP_ID = "sideline"
SRC = Path(__file__).resolve().parent
STATIC = SRC / "static"
APP_ROOT = SRC.parent
SEED = json.loads((APP_ROOT / "seed" / "league.json").read_text(encoding="utf-8"))
PROFILE = os.environ.get("WTA_VOCAB") or "default"
pages.VOCAB.update(json.loads((APP_ROOT / "vocab" / f"{PROFILE}.json").read_text(encoding="utf-8")))
pages.LEAGUE.update({k: SEED[k] for k in ("league", "division", "season")})
pages.TEAMS.update({club["code"]: club["name"] for club in SEED["teams"]})
TEAMS = dict(pages.TEAMS)
CLOCK = datetime.fromisoformat(os.environ.get("WTA_FIXED_NOW") or "2026-06-10T09:00:00")  # wta-clock
TODAY = CLOCK.date().isoformat()
TEST_HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
t = pages.t


class Reply:
    def __init__(self, status: int = 200, body: bytes = b"", content_type: str = "text/html; charset=utf-8",
                 location: str = "") -> None:
        self.status, self.body, self.content_type, self.location = status, body, content_type, location


def see_other(location: str) -> Reply:
    return Reply(303, location=location)


def missing() -> Reply:
    return Reply(404, pages.not_found())


class _Values(dict):
    """Placeholder values for a vocabulary sentence; an unknown placeholder stays as written."""

    def __missing__(self, name: str) -> str:
        return "{" + name + "}"


def message(key: str, **values: object) -> str:
    return t(key).format_map(_Values(values))


class Visit:
    """One request as the handlers see it: who is asking, and what they sent."""

    def __init__(self, conn, sid: str, form: dict[str, str]) -> None:
        self.conn, self.sid, self.form = conn, sid, form

    def flash(self) -> str:
        return take_flash(self.conn, self.sid)

    def tell(self, text: str) -> None:
        put_flash(self.conn, self.sid, text)


ROUTES: list[tuple[str, re.Pattern, object]] = []


def route(method: str, pattern: str):
    def register(handler):
        ROUTES.append((method, re.compile(pattern + r"\Z"), handler))
        return handler
    return register


# -- landing and test hooks ----------------------------------------------------------------------


@route("GET", r"/")
def landing(visit: Visit) -> Reply:
    visit.flash()
    return Reply(body=pages.landing(TODAY))


@route("GET", r"/__wta/state")
def wta_state(visit: Visit) -> Reply:
    if not TEST_HOOKS:
        return missing()
    state = committed(visit.conn, visit.sid)
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"))
    payload = {"session": visit.sid, "committed": state,
               "sha": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
    return Reply(body=json.dumps(payload).encode("utf-8"), content_type="application/json")


# -- the league secretary ------------------------------------------------------------------------


@route("GET", r"/sec")
def secretary(visit: Visit) -> Reply:
    return Reply(body=pages.secretary(results.fixtures(visit.conn, visit.sid), TODAY, visit.flash()))


@route("GET", r"/sec/results/(F\d+)")
def result_detail(visit: Visit, fixture_id: str) -> Reply:
    result = results.fixture(visit.conn, visit.sid, fixture_id)
    if result is None:
        return missing()
    return Reply(body=pages.result_detail(result, visit.flash()))


@route("GET", r"/sec/results/(F\d+)/edit")
def result_edit(visit: Visit, fixture_id: str) -> Reply:
    result = results.fixture(visit.conn, visit.sid, fixture_id)
    if result is None:
        return missing()
    visit.flash()
    return Reply(body=pages.result_edit(result))


@route("POST", r"/sec/results/(F\d+)")
def save_result(visit: Visit, fixture_id: str) -> Reply:
    if results.fixture(visit.conn, visit.sid, fixture_id) is None:
        visit.tell(message("refuse.no_fixture"))
        return see_other("/sec")
    try:
        key = results.save_score(visit.conn, visit.sid, fixture_id, visit.form.get("home_goals", ""),
                                 visit.form.get("away_goals", ""), TODAY)
        visit.tell(message(key, fixture=fixture_id))
    except results.Refused as refusal:
        visit.tell(message(refusal.key, **{"fixture": fixture_id, **refusal.values}))
    return see_other(f"/sec/results/{fixture_id}")


# -- club captains -------------------------------------------------------------------------------


def _club(code: str) -> str | None:
    return code if code in TEAMS else None


@route("GET", r"/club/([a-z]+)")
def club_portal(visit: Visit, code: str) -> Reply:
    if _club(code) is None:
        return missing()
    return Reply(body=pages.club(code, results.club_fixtures(visit.conn, visit.sid, code), visit.flash()))


@route("POST", r"/club/([a-z]+)/confirm/(F\d+)")
def club_confirm(visit: Visit, code: str, fixture_id: str) -> Reply:
    if _club(code) is None:
        return see_other("/")
    try:
        key = results.confirm(visit.conn, visit.sid, fixture_id, code)
        visit.tell(message(key, fixture=fixture_id))
    except results.Refused as refusal:
        visit.tell(message(refusal.key, **{"fixture": fixture_id, **refusal.values}))
    return see_other(f"/club/{code}")


@route("GET", r"/club/([a-z]+)/dispute/(F\d+)")
def dispute_page(visit: Visit, code: str, fixture_id: str) -> Reply:
    result = results.fixture(visit.conn, visit.sid, fixture_id)
    if _club(code) is None or result is None or not result.involves(code):
        return missing()
    refusal = results.dispute_refusal(result, code)
    text = message(refusal, fixture=fixture_id) if refusal else ""
    return Reply(body=pages.dispute_form(code, result, text, visit.flash()))


@route("POST", r"/club/([a-z]+)/dispute/(F\d+)")
def dispute_submit(visit: Visit, code: str, fixture_id: str) -> Reply:
    if _club(code) is None:
        return see_other("/")
    try:
        key = results.dispute(visit.conn, visit.sid, fixture_id, code, visit.form.get("dispute_reason", ""))
    except results.Refused as refusal:
        visit.tell(message(refusal.key, **{"fixture": fixture_id, **refusal.values}))
        if refusal.key in ("refuse.reason_missing", "refuse.reason_long"):
            return see_other(f"/club/{code}/dispute/{fixture_id}")
        return see_other(f"/club/{code}")
    visit.tell(message(key, fixture=fixture_id))
    return see_other(f"/club/{code}")


# -- the public ----------------------------------------------------------------------------------


@route("GET", r"/table")
def league_table(visit: Visit) -> Reply:
    visit.flash()
    ranked = standings.table(visit.conn, visit.sid, TEAMS)
    return Reply(body=pages.league_table(ranked, standings.results_counted(visit.conn, visit.sid), TODAY))


@route("GET", r"/results")
def public_results(visit: Visit) -> Reply:
    visit.flash()
    return Reply(body=pages.public_results(results.fixtures(visit.conn, visit.sid)))


@route("GET", r"/teams/([a-z]+)")
def team_page(visit: Visit, code: str) -> Reply:
    if _club(code) is None:
        return missing()
    visit.flash()
    ranked = standings.table(visit.conn, visit.sid, TEAMS)
    position, row = standings.position_of(ranked, code)
    return Reply(body=pages.team_page(code, position, row, results.club_fixtures(visit.conn, visit.sid, code),
                                      len(ranked)))


# -- HTTP ----------------------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "Sideline/1.0"
    store: Store

    def log_message(self, fmt: str, *args) -> None:  # the harness keeps its own logs
        pass

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def _handle(self, method: str) -> None:
        url = urlsplit(self.path)
        if url.path == "/healthz":
            return self._send(Reply(body=json.dumps({"ok": True, "app": APP_ID}).encode("utf-8"),
                                    content_type="application/json"))
        if url.path.startswith("/static/"):
            return self._send(self._static(url.path[len("/static/"):]))
        form: dict[str, str] = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else ""
            form = {name: values[-1] for name, values in parse_qs(raw, keep_blank_values=True).items()}
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie") or "")
        except cookies.CookieError:
            jar = cookies.SimpleCookie()
        asked = jar["wta_sid"].value if "wta_sid" in jar else ""
        try:
            with self.store.transaction() as conn:
                sid = self.store.session(conn, asked)
                reply = self._dispatch(method, url.path, Visit(conn, sid, form))
        except Exception:  # noqa: BLE001 - reported, never hidden behind a 200
            traceback.print_exc(file=sys.stderr)
            return self._send(Reply(500, b"internal error", "text/plain; charset=utf-8"))
        self._send(reply, sid if sid != asked else "")

    @staticmethod
    def _dispatch(method: str, path: str, visit: Visit) -> Reply:
        for verb, pattern, handler in ROUTES:
            match = pattern.match(path)
            if match and verb == method:
                return handler(visit, *match.groups())
        if method == "POST":
            return see_other("/")
        return missing()

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
    data_dir = Path(os.environ.get("WTA_DATA_DIR") or tempfile.mkdtemp(prefix="sideline-"))
    data_dir.mkdir(parents=True, exist_ok=True)
    Handler.store = Store(data_dir / "sideline.sqlite3", SEED)
    server = ThreadingHTTPServer((os.environ.get("HOST") or "127.0.0.1", int(os.environ["PORT"])), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
