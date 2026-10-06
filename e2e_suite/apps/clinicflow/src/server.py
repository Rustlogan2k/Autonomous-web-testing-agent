"""Clinicflow — appointment booking for Harbourside Health. A WSGI application, stdlib only.

The booking is a server-side draft addressed in the URL (`/book/<id>/<step>`). Every form
posts to the draft, the server validates and stores, then redirects to the step the draft
now owes (see `wizard.py`). A small script (`assets/booking.js`) shows and hides sections
of a page without a reload; every decision that matters is taken on the server.

Run: PORT=8080 python src/server.py
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
from http.cookies import SimpleCookie
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import store
import views
import wizard
from views import t

APP_ID = "clinicflow"
ASSETS = store.ROOT / "assets"
TEST_HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
_ROUTES: list[tuple[str, re.Pattern, object]] = []


def on(method: str, pattern: str):
    """Register a handler for `method` requests whose path matches `pattern` in full."""
    def register(handler):
        _ROUTES.append((method, re.compile(pattern + r"\Z"), handler))
        return handler
    return register


class Exchange:
    """One request against one session's database, and what will be sent back."""

    def __init__(self, environ: dict, conn, sid: str, args: tuple):
        self.environ, self.conn, self.sid, self.args = environ, conn, sid, args
        self.query = {k: v[-1] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
        self._form: dict | None = None

    @property
    def form(self) -> dict:
        if self._form is None:
            try:
                length = int(self.environ.get("CONTENT_LENGTH") or 0)
            except ValueError:
                length = 0
            raw = self.environ["wsgi.input"].read(length).decode("utf-8", "replace") if length else ""
            self._form = {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        return self._form

    def show(self, title: str, body: str, status: str = "200 OK"):
        resume = ""
        current = store.open_draft(self.conn)
        if current is not None and current["kind"] != "pending":
            resume = f"/book/{current['id']}/{wizard.owed(current)}"
        return status, "text/html; charset=utf-8", views.page(
            title, body, notice=store.hear(self.conn, "notice"), error=store.hear(self.conn, "error"),
            resume=resume)

    def refuse(self, key: str, location: str):
        store.say(self.conn, "error", t(key))
        return go(location)


def go(location: str):
    return "303 See Other", None, location


# -- landing ---------------------------------------------------------------------------------------


@on("GET", r"/")
def home(x: Exchange):
    current = store.open_draft(x.conn)
    resume = f"/book/{current['id']}/{wizard.owed(current)}" if current else ""
    return x.show(t("home.title"), views.home(resume))


@on("GET", r"/book")
def choose_service(x: Exchange):
    return x.show(t("service.title"), views.services())


@on("POST", r"/book")
def start_booking(x: Exchange):
    service = x.form.get("service", "")
    if service not in store.SERVICES:
        return go("/book")
    draft_id = store.start_draft(x.conn, "pending", service)
    return go(f"/book/{draft_id}/who")


# -- the draft's steps -------------------------------------------------------------------------------


def _draft_or_exit(x: Exchange, draft_id: str):
    """(draft row, None) for an open draft, else (None, response) for where to go instead."""
    row = store.draft(x.conn, int(draft_id))
    if row is None:
        return None, x.show(t("missing.title"), views.message("missing.title", "missing.body"), "404 Not Found")
    if row["state"] == "booked":
        return None, go(f"/book/{row['id']}/booked")
    if row["state"] == "done":
        return None, go(f"/bookings/{row['booking_ref']}")
    if row["state"] == "replaced":
        return None, x.show(t("replaced.title"), views.message("replaced.title", "replaced.body"))
    return row, None


def _step_page(x: Exchange, draft_id: str, step: str, render):
    row, exit_ = _draft_or_exit(x, draft_id)
    if exit_:
        return exit_
    if not wizard.may_view(row, step):
        return go(f"/book/{row['id']}/{wizard.owed(row)}")
    return render(row)


@on("GET", r"/book/(\d+)/who")
def who_page(x: Exchange):
    return _step_page(x, x.args[0], "who", lambda row: x.show(t("who.title"), views.who(row)))


@on("POST", r"/book/(\d+)/who")
def who_choose(x: Exchange):
    row, exit_ = _draft_or_exit(x, x.args[0])
    if exit_:
        return exit_
    branch = x.form.get("branch", "")
    if wizard.may_view(row, "who") and branch in ("new", "returning"):
        store.update_draft(x.conn, row["id"], kind=branch)
        row = store.draft(x.conn, row["id"])
    return go(f"/book/{row['id']}/{wizard.owed(row)}")


@on("GET", r"/book/(\d+)/details")
def details_page(x: Exchange):
    return _step_page(x, x.args[0], "details", lambda row: x.show(t("details.title"), views.details(row)))


@on("POST", r"/book/(\d+)/details")
def details_save(x: Exchange):
    row, exit_ = _draft_or_exit(x, x.args[0])
    if exit_:
        return exit_
    here = f"/book/{row['id']}/details"
    if not wizard.may_view(row, "details"):
        return go(f"/book/{row['id']}/{wizard.owed(row)}")
    person, problem = wizard.read_registration(x.form)
    if problem:
        return x.refuse(problem, here)
    payment, problem = wizard.read_payment(x.form)
    if problem:
        return x.refuse(problem, here)
    patient_id = store.register_patient(x.conn, person["name"], person["dob"], person["email"])
    store.update_draft(x.conn, row["id"], patient_id=patient_id, **payment)
    return go(f"/book/{row['id']}/{wizard.owed(store.draft(x.conn, row['id']))}")


@on("GET", r"/book/(\d+)/lookup")
def lookup_page(x: Exchange):
    return _step_page(x, x.args[0], "lookup", lambda row: x.show(t("lookup.title"), views.lookup(row)))


@on("POST", r"/book/(\d+)/lookup")
def lookup_find(x: Exchange):
    row, exit_ = _draft_or_exit(x, x.args[0])
    if exit_:
        return exit_
    here = f"/book/{row['id']}/lookup"
    if not wizard.may_view(row, "lookup"):
        return go(f"/book/{row['id']}/{wizard.owed(row)}")
    email = x.form.get("lookup_email", "").strip()
    if not wizard.EMAIL_SHAPE.fullmatch(email):
        return x.refuse("msg.email_bad", here)
    person = store.patient_by_email(x.conn, email)
    if person is None:
        return x.refuse("msg.not_found", here)
    payment, problem = wizard.read_payment(x.form)
    if problem:
        return x.refuse(problem, here)
    store.update_draft(x.conn, row["id"], patient_id=person["id"], **payment)
    return go(f"/book/{row['id']}/{wizard.owed(store.draft(x.conn, row['id']))}")


def _shown_practitioner(conn, row, wanted: str) -> str:
    """Whose week the slot table shows: fixed for a reschedule, else the choice or the held one."""
    if row["kind"] == "reschedule":
        return store.booking(conn, row["booking_ref"])["practitioner"] if not row["practitioner"] \
            else row["practitioner"]
    choices = [p["id"] for p in store.SEED["practitioners"] if p["service"] == row["service"]]
    if wanted in choices:
        return wanted
    if row["practitioner"] in choices:
        return row["practitioner"]
    return choices[0]


@on("GET", r"/book/(\d+)/slots")
def slots_page(x: Exchange):
    def render(row):
        practitioner = _shown_practitioner(x.conn, row, x.query.get("with", ""))
        return x.show(t("slots.title"), views.slots(x.conn, row, practitioner))
    return _step_page(x, x.args[0], "slots", render)


@on("POST", r"/book/(\d+)/hold")
def hold(x: Exchange):
    row, exit_ = _draft_or_exit(x, x.args[0])
    if exit_:
        return exit_
    if not wizard.may_view(row, "slots"):
        return go(f"/book/{row['id']}/{wizard.owed(row)}")
    practitioner = _shown_practitioner(x.conn, row, x.form.get("with", ""))
    here = f"/book/{row['id']}/slots?with={practitioner}"
    problem = store.hold_slot(x.conn, row, practitioner, x.form.get("slot", ""))
    if problem:
        return x.refuse(problem, here)
    return go(here)


@on("GET", r"/book/(\d+)/questions")
def questions_page(x: Exchange):
    return _step_page(x, x.args[0], "questions", lambda row: x.show(t("questions.title"), views.questions(row)))


@on("POST", r"/book/(\d+)/questions")
def questions_save(x: Exchange):
    row, exit_ = _draft_or_exit(x, x.args[0])
    if exit_:
        return exit_
    if not wizard.may_view(row, "questions"):
        return go(f"/book/{row['id']}/{wizard.owed(row)}")
    answers, problem = wizard.read_answers(x.form)
    if problem:
        return x.refuse(problem, f"/book/{row['id']}/questions")
    store.update_draft(x.conn, row["id"], **answers)
    return go(f"/book/{row['id']}/{wizard.owed(store.draft(x.conn, row['id']))}")


@on("GET", r"/book/(\d+)/confirm")
def confirm_page(x: Exchange):
    return _step_page(x, x.args[0], "confirm", lambda row: x.show(t("confirm.title"), views.confirm(x.conn, row)))


@on("POST", r"/book/(\d+)/book")
def book(x: Exchange):
    row, exit_ = _draft_or_exit(x, x.args[0])
    if exit_:
        return exit_
    if "injury_area" in x.form and wizard.may_view(row, "questions"):
        # "Book visit" pressed on the questions page: the answers come with it.
        answers, problem = wizard.read_answers(x.form)
        if problem:
            return x.refuse(problem, f"/book/{row['id']}/questions")
        store.update_draft(x.conn, row["id"], **answers)
        row = store.draft(x.conn, row["id"])
    owed = wizard.owed(row)
    if owed != "confirm":
        return x.refuse("msg.finish_first", f"/book/{row['id']}/{owed}")
    if row["kind"] == "reschedule":
        record = store.booking(x.conn, row["booking_ref"])
        if record is None or record["state"] != "active":
            return x.refuse("msg.no_longer_active", f"/bookings/{row['booking_ref']}")
        ref = store.move_booking(x.conn, row)
        store.say(x.conn, "notice", f"{t('msg.moved')} {store.slot_label(row['slot'])} ({ref})")
        return go(f"/bookings/{ref}")
    ref = store.create_booking(x.conn, row)
    store.say(x.conn, f"booked:{row['id']}", f"{t('msg.booked')} {ref}")
    return go(f"/book/{row['id']}/booked")


@on("GET", r"/book/(\d+)/booked")
def booked_page(x: Exchange):
    row = store.draft(x.conn, int(x.args[0]))
    if row is None or row["state"] != "booked":
        return _draft_or_exit(x, x.args[0])[1] or go(f"/book/{x.args[0]}/{wizard.owed(row)}")
    record = store.booking(x.conn, row["booking_ref"])
    banner = store.hear(x.conn, f"booked:{row['id']}")  # shown once, on the first view after booking
    return x.show(t("booked.title"), views.booked(x.conn, row, record, banner))


# -- appointments --------------------------------------------------------------------------------------


@on("GET", r"/bookings")
def booking_list(x: Exchange):
    return x.show(t("bookings.title"), views.booking_list(x.conn))


def _record(x: Exchange, ref: str):
    record = store.booking(x.conn, ref)
    if record is None:
        return None, x.show(t("missing.title"), views.message("missing.title", "missing.body"), "404 Not Found")
    return record, None


@on("GET", r"/bookings/(CF-\d+)")
def manage_page(x: Exchange):
    record, exit_ = _record(x, x.args[0])
    return exit_ or x.show(t("manage.title"), views.manage(x.conn, record))


@on("POST", r"/bookings/(CF-\d+)/reschedule")
def reschedule(x: Exchange):
    record, exit_ = _record(x, x.args[0])
    if exit_:
        return exit_
    if record["state"] != "active":
        return x.refuse("msg.no_longer_active", f"/bookings/{record['ref']}")
    current = store.open_draft(x.conn)
    if current is not None and current["kind"] == "reschedule" and current["booking_ref"] == record["ref"]:
        return go(f"/book/{current['id']}/slots")  # already moving this one: carry on
    draft_id = store.start_draft(
        x.conn, "reschedule", record["service"], patient_id=record["patient_id"], booking_ref=record["ref"],
        pay_mode=record["pay_mode"], insurer=record["insurer"], member=record["member"],
        area=record["area"], notes=record["notes"])
    return go(f"/book/{draft_id}/slots")


@on("POST", r"/bookings/(CF-\d+)/cancel")
def cancel(x: Exchange):
    record, exit_ = _record(x, x.args[0])
    if exit_:
        return exit_
    if record["state"] != "active":
        return go(f"/bookings/{record['ref']}")
    if x.form.get("confirm") != "yes":
        return x.refuse("msg.cancel_tick", f"/bookings/{record['ref']}")
    store.cancel_booking(x.conn, record["ref"])
    store.say(x.conn, "notice", f"{t('msg.cancelled')} {record['ref']}")
    return go("/bookings")


# -- test hook -------------------------------------------------------------------------------------------


@on("GET", r"/__wta/state")
def state(x: Exchange):
    if not TEST_HOOKS:
        return x.show(t("missing.title"), views.message("missing.title", "missing.body"), "404 Not Found")
    committed = store.committed(x.conn)
    return "200 OK", "application/json", json.dumps(
        {"session": x.sid, "committed": committed, "sha": store.digest(committed)}).encode()


# -- WSGI ------------------------------------------------------------------------------------------------

#: Where a form's address leads when it is opened with the other method (history, a typed URL).
_PAGE_OF_ACTION = ((re.compile(r"(/book/\d+)/hold\Z"), r"\1/slots"),
                   (re.compile(r"(/book/\d+)/book\Z"), r"\1/confirm"),
                   (re.compile(r"(/bookings/CF-\d+)/(?:reschedule|cancel)\Z"), r"\1"))


def _wrong_method(x: Exchange, path: str):
    for pattern, page in _PAGE_OF_ACTION:
        if pattern.match(path):
            return go(pattern.sub(page, path))
    if any(pattern.match(path) for _, pattern, _ in _ROUTES):
        return go(path)  # a page address posted to: show the page
    return x.show(t("missing.title"), views.message("missing.title", "missing.body"), "404 Not Found")



def _asset(path: str, start_response):
    target = (ASSETS / path).resolve()
    if ASSETS not in target.parents or not target.is_file():
        start_response("404 Not Found", [("Content-Type", "text/plain"), ("Content-Length", "0")])
        return [b""]
    body = target.read_bytes()
    kind = {".js": "text/javascript", ".css": "text/css"}.get(target.suffix) \
        or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    start_response("200 OK", [("Content-Type", f"{kind}; charset=utf-8"), ("Content-Length", str(len(body))),
                              ("Cache-Control", "no-cache")])
    return [body]


def application(environ, start_response):
    path = environ.get("PATH_INFO") or "/"
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if path == "/healthz":
        body = json.dumps({"ok": True, "app": APP_ID}).encode()
        start_response("200 OK", [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return [body]
    if path.startswith("/assets/"):
        return _asset(path[len("/assets/"):], start_response)
    jar = SimpleCookie(environ.get("HTTP_COOKIE", ""))
    sid = jar["wta_sid"].value if "wta_sid" in jar else ""
    headers = [("Cache-Control", "no-store")]
    with store.WRITE_LOCK:
        if not store.known_session(sid):
            sid = store.new_session()
            headers.append(("Set-Cookie", f"wta_sid={sid}; Path=/; HttpOnly; SameSite=Lax"))
        conn = store.open_session(sid)
        try:
            handler, args = None, ()
            for verb, pattern, candidate in _ROUTES:
                match = pattern.match(path)
                if match and verb == method:
                    handler, args = candidate, match.groups()
                    break
            exchange = Exchange(environ, conn, sid, args)
            if handler is None:
                outcome = _wrong_method(exchange, path)
            else:
                outcome = handler(exchange)
            conn.commit()
        finally:
            conn.close()
    status, content_type, payload = outcome
    if content_type is None:
        headers.append(("Location", payload))
        headers.append(("Content-Length", "0"))
        start_response(status, headers)
        return [b""]
    headers += [("Content-Type", content_type), ("Content-Length", str(len(payload)))]
    start_response(status, headers)
    return [payload]


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - the stdlib's signature
        pass


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def main() -> None:
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ["PORT"])
    with make_server(host, port, application, server_class=ThreadedServer, handler_class=QuietHandler) as server:
        print(f"{APP_ID} on http://{host}:{port}/", file=sys.stderr, flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
