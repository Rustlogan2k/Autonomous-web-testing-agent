"""Dutyday — a community food bank's volunteer rota. Server-rendered pages, no JavaScript at all.

What a page shows is chosen by its address alone: the month (`/rota?month=2026-07`), the day
(`/rota/day/2026-07-01`) and a volunteer's week (`/volunteers/priya?week=2026-06-29`). Every
change is an HTML form posted to the server and answered with a 303 redirect to the page that
shows its result (post/redirect/get); the only thing carried from a post to that page is a
one-time notice kept with the session. Withdrawing from a shift goes through a confirmation
page rather than a dialog.

Standard library only. State lives in one SQLite file under WTA_DATA_DIR, its rows partitioned
by the `wta_sid` session cookie; a new session starts from `seed/rota.json`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from datetime import timedelta
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pages
import rota

APP_ID = "dutyday"
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
pages.VOCAB.update(json.loads((ROOT.parent / "vocab" / f"{os.environ.get('WTA_VOCAB') or 'default'}.json")
                              .read_text(encoding="utf-8")))
t = pages.t
DATA_DIR = Path(os.environ.get("WTA_DATA_DIR") or tempfile.mkdtemp(prefix="dutyday-"))
DB_PATH = DATA_DIR / "dutyday.sqlite3"
TEST_HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
STATIC_TYPES = {".css": "text/css; charset=utf-8"}
_SID = re.compile(r"d\d{6}")
_LOCK = threading.Lock()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def page(title: str, body: str, status: int = 200, flash: str = "") -> tuple:
    return "page", status, title, body, flash


def redirect(location: str) -> tuple:
    return ("redirect", location)


def refusal_text(refusal: rota.Refusal) -> str:
    params = {k: pages.short_date(v) if hasattr(v, "isoformat") else v for k, v in refusal.params.items()}
    return t(refusal.key).format(**params)


def change_text(key: str, volunteer: str, slot: str, day) -> str:
    """The notice confirming a change to a shift's roster."""
    return t(key).format(name=pages.volunteer_name(volunteer), slot=pages.slot_name(slot).lower(),
                         day=pages.short_date(day))


def first(query: dict, name: str) -> str:
    return (query.get(name) or [""])[0].strip()


# -- the rota -----------------------------------------------------------------------------------------


def month_view(conn, sid: str, query: dict, flash: str = "") -> tuple:
    month = rota.season_month(rota.parse_month(first(query, "month")))
    body = pages.month_page(month, rota.open_places(conn, sid, month), feed=first(query, "feed") == "show")
    return page(pages.month_name(month), body, flash=flash)


def jump(conn, sid: str, query: dict) -> tuple:
    day = rota.parse_day(first(query, "date"))
    if day is None or not rota.in_season(day):
        flash = t("msg.bad_date").format(first=pages.long_date(rota.FIRST_DAY), last=pages.long_date(rota.LAST_DAY))
        return month_view(conn, sid, query, flash)
    return redirect(f"/rota/day/{day.isoformat()}")


def day_view(conn, sid: str, raw_day: str) -> tuple:
    day = rota.parse_day(raw_day)
    if day is None:
        return page(t("day.no_rota"), pages.message("day.missing_title", "day.no_rota"))
    shifts = rota.shifts_between(conn, sid, day, day).get(day, [])
    return page(pages.long_date(day), pages.day_page(day, shifts))


def assign(conn, sid: str, raw_day: str, form: dict) -> tuple:
    day, slot, volunteer = rota.parse_day(raw_day), form.get("slot", ""), form.get("volunteer", "")
    if day is None or not rota.has_shifts(day) or slot not in rota.SLOTS:
        rota.notify(conn, sid, t("msg.no_shift"))
        return redirect("/rota")
    try:
        rota.check_assignment(conn, sid, day, slot, volunteer)
    except rota.Refusal as refusal:
        rota.notify(conn, sid, refusal_text(refusal))
        return redirect(f"/rota/day/{day.isoformat()}")
    key = rota.shift_key(day, slot)
    rota.add_assignment(conn, sid, key, volunteer)
    rota.refresh_status(conn, sid, key)
    rota.notify(conn, sid, change_text("msg.assigned", volunteer, slot, day))
    return redirect(f"/rota/day/{day.isoformat()}")


def remove(conn, sid: str, raw_day: str, form: dict) -> tuple:
    day = rota.parse_day(raw_day)
    if day is None or not rota.in_season(day):
        return redirect("/rota")
    found = rota.assignment(conn, sid, form.get("assignment", ""))
    if found is None or found["day"] != day:
        rota.notify(conn, sid, t("msg.assignment_gone"))
    elif rota.is_past(day):
        rota.notify(conn, sid, t("msg.day_read_only"))
    else:
        rota.delete_assignment(conn, sid, found["id"])
        rota.refresh_status(conn, sid, found["shift_id"])
        rota.notify(conn, sid, change_text("msg.removed", found["volunteer"], found["slot"], day))
    return redirect(f"/rota/day/{day.isoformat()}")


# -- volunteers --------------------------------------------------------------------------------------


def volunteers_view(conn, sid: str) -> tuple:
    monday = rota.monday_of(rota.TODAY)
    hours = {vid: rota.hours_of(rota.volunteer_shifts(conn, sid, vid, monday, monday + timedelta(days=6)))
             for vid in rota.VOLUNTEERS}
    return page(t("vol.title"), pages.volunteers_page(hours))


def volunteer_view(conn, sid: str, volunteer: str, query: dict) -> tuple:
    if volunteer not in rota.VOLUNTEERS:
        return page(t("vol.title"), pages.message("vol.missing_title", "vol.unknown"))
    monday = rota.season_week(rota.parse_day(first(query, "week")))
    shifts = rota.volunteer_shifts(conn, sid, volunteer, monday, monday + timedelta(days=6))
    body = pages.volunteer_page(volunteer, monday, shifts, rota.next_shift(conn, sid, volunteer))
    return page(pages.volunteer_name(volunteer), body)


def withdraw_view(conn, sid: str, assignment_id: str) -> tuple:
    found = rota.assignment(conn, sid, assignment_id)
    if found is None:
        return page(t("withdraw.title"), pages.message("withdraw.missing_title", "msg.assignment_gone"))
    return page(t("withdraw.title"), pages.withdraw_page(found))


def withdraw(conn, sid: str, assignment_id: str) -> tuple:
    found = rota.assignment(conn, sid, assignment_id)
    if found is None:
        rota.notify(conn, sid, t("msg.assignment_gone"))
        return redirect("/volunteers")
    back = pages.volunteer_href(found["volunteer"], found["day"])
    if rota.is_past(found["day"]):
        rota.notify(conn, sid, t("msg.past_withdraw"))
        return redirect(back)
    shift_id = found["shift_id"]
    rota.delete_assignment(conn, sid, found["id"])
    rota.refresh_status(conn, sid, shift_id)  # a withdrawal can reopen the shift
    rota.notify(conn, sid, change_text("msg.withdrawn", found["volunteer"], found["slot"], found["day"]))
    return redirect(back)


# -- routing -----------------------------------------------------------------------------------------

ROUTES: list[tuple[str, re.Pattern, object]] = [
    ("GET", re.compile(r"/"), lambda c, s, q, f: redirect("/rota")),
    ("GET", re.compile(r"/rota"), lambda c, s, q, f: month_view(c, s, q)),
    ("GET", re.compile(r"/rota/jump"), lambda c, s, q, f: jump(c, s, q)),
    ("GET", re.compile(r"/rota/print"), lambda c, s, q, f: print_view(c, s, q)),
    ("GET", re.compile(r"/rota/day/([^/]+)"), lambda c, s, q, f, d: day_view(c, s, d)),
    ("POST", re.compile(r"/rota/day/([^/]+)/assign"), lambda c, s, q, f, d: assign(c, s, d, f)),
    ("POST", re.compile(r"/rota/day/([^/]+)/remove"), lambda c, s, q, f, d: remove(c, s, d, f)),
    ("GET", re.compile(r"/volunteers"), lambda c, s, q, f: volunteers_view(c, s)),
    ("GET", re.compile(r"/volunteers/([^/]+)"), lambda c, s, q, f, v: volunteer_view(c, s, v, q)),
    ("GET", re.compile(r"/assignments/([^/]+)/withdraw"), lambda c, s, q, f, a: withdraw_view(c, s, a)),
    ("POST", re.compile(r"/assignments/([^/]+)/withdraw"), lambda c, s, q, f, a: withdraw(c, s, a)),
    ("GET", re.compile(r"/help"), lambda c, s, q, f: page(t("help.title"), pages.help_page())),
]


def print_view(conn, sid: str, query: dict) -> tuple:
    month = rota.season_month(rota.parse_month(first(query, "month")))
    days = rota.shifts_between(conn, sid, month, rota.month_end(month))
    return page(t("print.title").format(month=pages.month_name(month)), pages.print_page(month, days))


def state(conn, sid: str) -> dict:
    committed = rota.committed(conn, sid)
    sha = hashlib.sha256(json.dumps(committed, sort_keys=True).encode()).hexdigest()
    return {"session": sid, "committed": committed, "sha": sha}


# -- HTTP plumbing -----------------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "Dutyday/1.0"

    def log_message(self, fmt, *args):  # quiet: the harness keeps its own logs
        pass

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _send(self, status: int, body: bytes = b"", content_type: str = "", location: str = "") -> None:
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if location:
            self.send_header("Location", location)
        if self._cookie:
            self.send_header("Set-Cookie", f"wta_sid={self._cookie}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()
        self.wfile.write(body)

    def _session(self, conn) -> str:
        try:
            jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            sid = jar["wta_sid"].value if "wta_sid" in jar else ""
        except cookies.CookieError:
            sid = ""
        if _SID.fullmatch(sid) and rota.session_exists(conn, sid):
            return sid
        sid = f"d{rota.next_counter(conn, 'session'):06d}"
        rota.start_session(conn, sid)
        self._cookie = sid
        return sid

    def _form(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        return {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def _handle(self, method: str) -> None:
        self._cookie = None
        parts = urlsplit(self.path)
        path = parts.path.rstrip("/") or "/"
        if path == "/healthz":
            return self._send(200, json.dumps({"ok": True, "app": APP_ID}).encode(), "application/json")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        query = parse_qs(parts.query, keep_blank_values=True)
        form = self._form() if method == "POST" else {}
        with _LOCK:
            conn = connect()
            try:
                sid = self._session(conn)
                outcome = self._route(conn, sid, method, path, query, form)
                if outcome[0] == "page":
                    _, status, title, body, flash = outcome
                    outcome = ("html", status, pages.layout(title, body, flash or rota.take_notice(conn, sid)))
                conn.commit()
            finally:
                conn.close()
        if outcome[0] == "redirect":
            self._send(303, location=outcome[1])
        elif outcome[0] == "json":
            self._send(200, json.dumps(outcome[1]).encode(), "application/json")
        else:
            self._send(outcome[1], outcome[2], "text/html; charset=utf-8")

    def _route(self, conn, sid: str, method: str, path: str, query: dict, form: dict) -> tuple:
        if method == "GET" and path == "/__wta/state":
            return ("json", state(conn, sid)) if TEST_HOOKS else page(t("missing.title"), pages.message(
                "missing.title", "missing.body"), 404)
        for verb, pattern, handler in ROUTES:
            match = pattern.fullmatch(path)
            if verb == method and match:
                return handler(conn, sid, query, form, *match.groups())
        return page(t("missing.title"), pages.message("missing.title", "missing.body"), 404)

    def _static(self, name: str) -> None:
        target = (STATIC / name).resolve()
        if STATIC not in target.parents or not target.is_file() or target.suffix not in STATIC_TYPES:
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        return self._send(200, target.read_bytes(), STATIC_TYPES[target.suffix])


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    rota.install(conn)
    conn.commit()
    conn.close()
    server = ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), int(os.environ["PORT"])), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
