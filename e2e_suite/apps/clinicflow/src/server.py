"""Clinicflow — the front desk of Harbourside Health. A WSGI application, standard library
plus the suite's vendored pg8000.

Receptionists book appointments through a server-side draft addressed in the URL
(`/book/<id>/<step>`): every form posts to the draft, the server validates and stores, then
redirects to the step the draft now owes (`wizard.py`). They also keep the patient directory
(records, notes) and one waiting list per service. Practitioners see their day, the manager
the clinic's utilisation. Small scripts show and hide parts of pages, open the confirmation
dialogs and save waiting-list notes in place; every decision that matters is taken here.

Run: DATABASE_URL=... PORT=8080 python src/server.py
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
from http.cookies import CookieError, SimpleCookie
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, quote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import store
import views
import wizard
from partitions import Partitions
from views import t

APP_ID = "clinicflow"
ASSETS = store.ROOT / "assets"
TEST_HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
DESK, DIARY, OFFICE = ("receptionist",), ("practitioner",), ("manager",)
EVERYONE = DESK + DIARY + OFFICE
NAME_LIMIT = 5000
PHONE_SHAPE = re.compile(r"[0-9 +()-]{6,20}")
_ROUTES: list[tuple[str, re.Pattern, tuple, object]] = []
RECORDS: Partitions | None = None  # opened by main()


def on(method: str, pattern: str, roles: tuple = DESK):
    """Register a handler for `method` requests whose path matches `pattern` in full, for
    staff in `roles` (anyone else is shown the access notice)."""
    def register(handler):
        _ROUTES.append((method, re.compile(pattern + r"\Z"), roles, handler))
        return handler
    return register


class Exchange:
    """One request: its transaction, the signed-in member of staff, and what goes back."""

    def __init__(self, environ: dict, db, user: dict, args: tuple):
        self.environ, self.db, self.user, self.args = environ, db, user, args
        self.query = {k: v[-1] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
        self._form: dict | None = None
        self._raw: bytes | None = None

    @property
    def me(self) -> str:
        return self.user["id"]

    def raw(self) -> bytes:
        if self._raw is None:
            try:
                length = int(self.environ.get("CONTENT_LENGTH") or 0)
            except ValueError:
                length = 0
            self._raw = self.environ["wsgi.input"].read(length) if length else b""
        return self._raw

    @property
    def form(self) -> dict:
        if self._form is None:
            fields = parse_qs(self.raw().decode("utf-8", "replace"), keep_blank_values=True)
            self._form = {k: v[-1] for k, v in fields.items()}
        return self._form

    def show(self, title: str, body: str, status: str = "200 OK"):
        resume = ""
        if self.user["role"] == "receptionist":
            current = store.open_draft(self.db, self.me)
            if current is not None and current["kind"] != "pending":
                resume = f"/book/{current['id']}/{wizard.owed(current)}"
        return status, "text/html; charset=utf-8", views.page(
            title, body, self.user, notice=store.hear(self.db, "notice"), error=store.hear(self.db, "error"),
            resume=resume)

    def refuse(self, key: str, location: str):
        store.say(self.db, "error", t(key))
        return go(location)


def go(location: str):
    return "303 See Other", None, location


def reply_json(payload: dict):
    return "200 OK", "application/json; charset=utf-8", json.dumps(payload).encode("utf-8")


# -- landing ---------------------------------------------------------------------------------------


@on("GET", r"/", EVERYONE)
def home(x: Exchange):
    if x.user["role"] == "practitioner":
        return x.show(t("home.title"), views.home_practitioner())
    if x.user["role"] == "manager":
        return x.show(t("home.title"), views.home_manager())
    current = store.open_draft(x.db, x.me)
    resume = f"/book/{current['id']}/{wizard.owed(current)}" if current else ""
    return x.show(t("home.title"), views.home(resume, store.waiting_counts(x.db)))


@on("GET", r"/book")
def choose_service(x: Exchange):
    return x.show(t("service.title"), views.services())


@on("POST", r"/book")
def start_booking(x: Exchange):
    service = x.form.get("service", "")
    if service not in store.SERVICES:
        return go("/book")
    draft_id = store.start_draft(x.db, x.me, "pending", service)
    return go(f"/book/{draft_id}/who")


# -- the draft's steps -------------------------------------------------------------------------------


def _draft_or_exit(x: Exchange, draft_id: str):
    """(draft row, None) for an open draft of this receptionist, else (None, where to go)."""
    row = store.draft(x.db, int(draft_id))
    if row is None or row["created_by"] != x.me:
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
        store.update_draft(x.db, row["id"], kind=branch)
        row = store.draft(x.db, row["id"])
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
    patient_id = store.register_patient(x.db, person["name"], person["dob"], person["email"])
    store.update_draft(x.db, row["id"], patient_id=patient_id, **payment)
    return go(f"/book/{row['id']}/{wizard.owed(store.draft(x.db, row['id']))}")


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
    person = store.patient_by_email(x.db, email)
    if person is None:
        return x.refuse("msg.not_found", here)
    payment, problem = wizard.read_payment(x.form)
    if problem:
        return x.refuse(problem, here)
    store.update_draft(x.db, row["id"], patient_id=person["id"], **payment)
    return go(f"/book/{row['id']}/{wizard.owed(store.draft(x.db, row['id']))}")


def _shown_practitioner(db, row, wanted: str) -> str:
    """Whose week the slot table shows: fixed for a reschedule, else the choice or the held one."""
    if row["kind"] == "reschedule":
        return row["practitioner"] or store.booking(db, row["booking_ref"])["practitioner"]
    choices = [p["id"] for p in store.SEED["practitioners"] if p["service"] == row["service"]]
    if wanted in choices:
        return wanted
    if row["practitioner"] in choices:
        return row["practitioner"]
    return choices[0]


@on("GET", r"/book/(\d+)/slots")
def slots_page(x: Exchange):
    def render(row):
        practitioner = _shown_practitioner(x.db, row, x.query.get("with", ""))
        return x.show(t("slots.title"), views.slots(x.db, row, practitioner))
    return _step_page(x, x.args[0], "slots", render)


@on("POST", r"/book/(\d+)/hold")
def hold(x: Exchange):
    row, exit_ = _draft_or_exit(x, x.args[0])
    if exit_:
        return exit_
    if not wizard.may_view(row, "slots"):
        return go(f"/book/{row['id']}/{wizard.owed(row)}")
    practitioner = _shown_practitioner(x.db, row, x.form.get("with", ""))
    here = f"/book/{row['id']}/slots?with={practitioner}"
    problem = store.hold_slot(x.db, row, practitioner, x.form.get("slot", ""))
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
    store.update_draft(x.db, row["id"], **answers)
    return go(f"/book/{row['id']}/{wizard.owed(store.draft(x.db, row['id']))}")


@on("GET", r"/book/(\d+)/confirm")
def confirm_page(x: Exchange):
    return _step_page(x, x.args[0], "confirm", lambda row: x.show(t("confirm.title"), views.confirm(x.db, row)))


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
        store.update_draft(x.db, row["id"], **answers)
        row = store.draft(x.db, row["id"])
    owed = wizard.owed(row)
    if owed != "confirm":
        return x.refuse("msg.finish_first", f"/book/{row['id']}/{owed}")
    if row["kind"] == "reschedule":
        record = store.booking(x.db, row["booking_ref"])
        if record is None or record["state"] != "active":
            return x.refuse("msg.no_longer_active", f"/bookings/{row['booking_ref']}")
        ref = store.move_booking(x.db, row, x.me)
        store.say(x.db, "notice", f"{t('msg.moved')} {store.slot_label(row['slot'])} ({ref})")
        return go(f"/bookings/{ref}")
    ref = store.create_booking(x.db, row, x.me)
    store.say(x.db, f"booked:{row['id']}", f"{t('msg.booked')} {ref}")
    return go(f"/book/{row['id']}/booked")


@on("GET", r"/book/(\d+)/booked")
def booked_page(x: Exchange):
    row = store.draft(x.db, int(x.args[0]))
    if row is None or row["created_by"] != x.me or row["state"] != "booked":
        exit_ = _draft_or_exit(x, x.args[0])[1]
        return exit_ or go(f"/book/{x.args[0]}/{wizard.owed(row)}")
    record = store.booking(x.db, row["booking_ref"])
    banner = store.hear(x.db, f"booked:{row['id']}")  # shown once, on the first view after booking
    return x.show(t("booked.title"), views.booked(x.db, row, record, banner))


# -- appointments --------------------------------------------------------------------------------------


@on("GET", r"/bookings")
def booking_list(x: Exchange):
    practitioner = x.query.get("with", "")
    practitioner = practitioner if practitioner in store.PRACTITIONERS else ""
    sort = x.query.get("sort") if x.query.get("sort") in store.BOOKING_SORTS else "when"
    return x.show(t("bookings.title"), views.booking_list(x.db, x.me, practitioner, sort))


def _record(x: Exchange, ref: str):
    record = store.booking(x.db, ref)
    if record is None:
        return None, x.show(t("missing.title"), views.message("missing.title", "missing.body"), "404 Not Found")
    return record, None


@on("GET", r"/bookings/(CF-\d+)")
def manage_page(x: Exchange):
    record, exit_ = _record(x, x.args[0])
    return exit_ or x.show(t("manage.title"), views.manage(x.db, record))


@on("POST", r"/bookings/(CF-\d+)/reschedule")
def reschedule(x: Exchange):
    record, exit_ = _record(x, x.args[0])
    if exit_:
        return exit_
    if record["state"] != "active":
        return x.refuse("msg.no_longer_active", f"/bookings/{record['ref']}")
    current = store.open_draft(x.db, x.me)
    if current is not None and current["kind"] == "reschedule" and current["booking_ref"] == record["ref"]:
        return go(f"/book/{current['id']}/slots")  # already moving this one: carry on
    draft_id = store.start_draft(
        x.db, x.me, "reschedule", record["service"], patient_id=record["patient_id"],
        booking_ref=record["ref"], pay_mode=record["pay_mode"], insurer=record["insurer"], member=record["member"])
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
    store.cancel_booking(x.db, record["ref"], x.me)
    store.say(x.db, "notice", f"{t('msg.cancelled')} {record['ref']}")
    return go("/bookings")


# -- the patient directory and records ------------------------------------------------------------------


@on("GET", r"/patients")
def patient_directory(x: Exchange):
    sort = x.query.get("sort") if x.query.get("sort") in store.DIRECTORY_SORTS else "name"
    page = int(x.query["page"]) if re.fullmatch(r"\d{1,4}", x.query.get("page", "")) else 1
    q = x.query.get("q", "")[:200]
    return x.show(t("patients.title"), views.directory(store.directory(x.db, q, sort, page), q, sort))


def read_person(form: dict) -> tuple[dict, str]:
    """A patient's details from the record form (registration rules, plus an optional phone)."""
    person, problem = wizard.read_registration(form)
    if problem:
        return {}, problem
    phone = form.get("phone", "").strip()
    if phone and not PHONE_SHAPE.fullmatch(phone):
        return {}, "msg.phone_bad"
    return {**person, "phone": phone or None}, ""


@on("GET", r"/patients/new")
def new_patient_page(x: Exchange):
    return x.show(t("patient.new_title"), views.patient_form(None))


@on("POST", r"/patients/new")
def new_patient(x: Exchange):
    person, problem = read_person(x.form)
    if problem:
        return x.refuse(problem, "/patients/new")
    patient_id = store.register_patient(x.db, person["name"], person["dob"], person["email"], person["phone"])
    store.say(x.db, "notice", f"{t('msg.patient_added')} {person['name']}")
    return go(f"/patients/{patient_id}")


def _patient(x: Exchange, patient_id: str):
    person = store.patient(x.db, int(patient_id))
    if person is None:
        return None, x.show(t("missing.title"), views.message("missing.title", "missing.body"), "404 Not Found")
    return person, None


@on("GET", r"/patients/(\d+)")
def patient_record(x: Exchange):
    person, exit_ = _patient(x, x.args[0])
    if exit_:
        return exit_
    tab = x.query.get("tab") if x.query.get("tab") in views.RECORD_TABS else "details"
    editing = int(x.query["edit"]) if tab == "notes" and x.query.get("edit", "").isdigit() else None
    return x.show(person["full_name"], views.patient_record(x.db, person, tab, editing))


@on("GET", r"/patients/(\d+)/edit")
def edit_patient_page(x: Exchange):
    person, exit_ = _patient(x, x.args[0])
    return exit_ or x.show(t("patient.edit_title"), views.patient_form(person))


@on("POST", r"/patients/(\d+)/edit")
def edit_patient(x: Exchange):
    person, exit_ = _patient(x, x.args[0])
    if exit_:
        return exit_
    details, problem = read_person(x.form)
    if problem:
        return x.refuse(problem, f"/patients/{person['id']}/edit")
    store.update_patient(x.db, person["id"], details["name"], details["dob"], details["email"], details["phone"])
    store.say(x.db, "notice", t("msg.patient_saved"))
    return go(f"/patients/{person['id']}")


@on("POST", r"/patients/(\d+)/delete")
def delete_patient(x: Exchange):
    person, exit_ = _patient(x, x.args[0])
    if exit_:
        return exit_
    if store.patient_in_use(x.db, person["id"]):
        return x.refuse("msg.patient_in_use", f"/patients/{person['id']}")
    store.delete_patient(x.db, person["id"])
    store.say(x.db, "notice", f"{t('msg.patient_deleted')} {person['full_name']}")
    return go("/patients")


def _note_text(x: Exchange, back: str):
    body = x.form.get("note_body", "").strip()
    if not body:
        return None, x.refuse("msg.note_needed", back)
    if len(body) > NAME_LIMIT:
        return None, x.refuse("msg.too_long", back)
    return body, None


@on("POST", r"/patients/(\d+)/notes")
def add_note(x: Exchange):
    person, exit_ = _patient(x, x.args[0])
    if exit_:
        return exit_
    back = f"/patients/{person['id']}?tab=notes"
    body, refused = _note_text(x, back)
    if refused:
        return refused
    store.add_note(x.db, person["id"], body, x.me)
    return go(back)


@on("POST", r"/patients/(\d+)/notes/(\d+)")
def edit_note(x: Exchange):
    person, exit_ = _patient(x, x.args[0])
    if exit_:
        return exit_
    back = f"/patients/{person['id']}?tab=notes"
    body, refused = _note_text(x, f"{back}&edit={x.args[1]}")
    if refused:
        return refused
    store.edit_note(x.db, person["id"], int(x.args[1]), body)
    return go(back)


@on("POST", r"/patients/(\d+)/notes/(\d+)/delete")
def delete_note(x: Exchange):
    person, exit_ = _patient(x, x.args[0])
    if exit_:
        return exit_
    store.delete_note(x.db, person["id"], int(x.args[1]))
    return go(f"/patients/{person['id']}?tab=notes")


@on("POST", r"/patients/(\d+)/waiting")
def join_waiting_list(x: Exchange):
    person, exit_ = _patient(x, x.args[0])
    if exit_:
        return exit_
    service = x.form.get("waiting_service", "")
    note = x.form.get("waiting_note", "").strip()
    if service not in store.SERVICES:
        return x.refuse("msg.service_needed", f"/patients/{person['id']}")
    if len(note) > NAME_LIMIT:
        return x.refuse("msg.too_long", f"/patients/{person['id']}")
    store.add_to_waiting_list(x.db, person["id"], service, note, x.me)
    store.say(x.db, "notice", f"{t('msg.waiting_added')} {store.SERVICES[service]['name']}")
    return go(f"/waiting?service={service}")


# -- the waiting lists ---------------------------------------------------------------------------------------


@on("GET", r"/waiting")
def waiting_page(x: Exchange):
    service = x.query.get("service") if x.query.get("service") in store.SERVICES else "gp"
    undo = store.hear(x.db, "undo")
    removed = store.waiting_entry(x.db, undo) if undo else None
    return x.show(t("waiting.title"), views.waiting(x.db, service, removed))


def _entry(x: Exchange, entry_id: str):
    entry = store.waiting_entry(x.db, entry_id)
    if entry is None:
        return None, x.show(t("missing.title"), views.message("missing.title", "missing.body"), "404 Not Found")
    return entry, None


@on("POST", r"/waiting/(w\d+)/(up|down)")
def move_waiting(x: Exchange):
    entry, exit_ = _entry(x, x.args[0])
    if exit_:
        return exit_
    if entry["state"] == "waiting":
        store.move_waiting(x.db, entry, x.args[1])
    return go(f"/waiting?service={entry['service_id']}")


@on("POST", r"/waiting/(w\d+)/remove")
def remove_waiting(x: Exchange):
    entry, exit_ = _entry(x, x.args[0])
    if exit_:
        return exit_
    if entry["state"] == "waiting":
        store.set_waiting_state(x.db, entry["id"], "removed")
        store.say(x.db, "undo", entry["id"])  # the next view of the list offers Undo, once
    return go(f"/waiting?service={entry['service_id']}")


@on("POST", r"/waiting/(w\d+)/restore")
def restore_waiting(x: Exchange):
    entry, exit_ = _entry(x, x.args[0])
    if exit_:
        return exit_
    store.set_waiting_state(x.db, entry["id"], "waiting")
    return go(f"/waiting?service={entry['service_id']}")


@on("POST", r"/waiting/(w\d+)/note")
def save_waiting_note(x: Exchange):
    """The in-place note editor's save: JSON in, JSON out (never an error status)."""
    entry = store.waiting_entry(x.db, x.args[0])
    try:
        body = json.loads(x.raw().decode("utf-8") or "{}")
    except ValueError:
        body = {}
    note = str(body.get("note", "")).strip() if isinstance(body, dict) else ""
    if entry is None or entry["state"] != "waiting":
        return reply_json({"ok": False, "error": t("msg.waiting_gone")})
    if len(note) > NAME_LIMIT:
        return reply_json({"ok": False, "error": t("msg.too_long")})
    store.save_waiting_note(x.db, entry["id"], note)
    return reply_json({"ok": True, "note": note})


# -- the other roles -------------------------------------------------------------------------------------------


@on("GET", r"/my/day", DIARY)
def my_day(x: Exchange):
    practitioner = x.user["practitioner_id"]
    return x.show(t("day.title"), views.my_day(store.PRACTITIONERS[practitioner], store.day_list(x.db, practitioner)))


@on("GET", r"/admin/utilisation", OFFICE)
def utilisation_page(x: Exchange):
    return x.show(t("use.title"), views.utilisation(store.utilisation(x.db)))


# -- signing in and out ------------------------------------------------------------------------------------------


def _respond(start_response, status: str, content_type: str | None, payload, extra: list | None = None):
    headers = [("Cache-Control", "no-store"), *(extra or [])]
    if content_type is None:
        headers += [("Location", payload), ("Content-Length", "0")]
        start_response(status, headers)
        return [b""]
    body = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    headers += [("Content-Type", content_type), ("Content-Length", str(len(body)))]
    start_response(status, headers)
    return [body]


def _next(raw: str | None) -> str:
    value = (raw or "").strip()
    if re.fullmatch(r"/(?!/)[^\s\\]*", value) and not value.startswith(("/login", "/logout")):
        return value
    return "/"


def _sign_in(environ, sid, start_response):
    query = {k: v[-1] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
    if environ.get("REQUEST_METHOD") != "POST":
        with RECORDS.visit(sid) as db:
            signed_in = db.staff_id is not None
        if signed_in:  # where Back to the sign-in page lands once signed in
            return _respond(start_response, "303 See Other", None, _next(query.get("next")))
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", views.sign_in(query.get("next", "")))
    length = int(environ.get("CONTENT_LENGTH") or 0)
    raw = environ["wsgi.input"].read(length).decode("utf-8", "replace") if length else ""
    form = {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}
    username, password = form.get("username", "").strip(), form.get("password", "")
    wanted = form.get("next") or query.get("next", "")
    staff_id = RECORDS.check_password(username, password) if username and password else None
    if staff_id is None:  # one message whatever was wrong, and no session
        return _respond(start_response, "200 OK", "text/html; charset=utf-8",
                        views.sign_in(wanted, username, refused=True))
    sid = RECORDS.sign_in(sid, staff_id)
    return _respond(start_response, "303 See Other", None, _next(wanted),
                    [("Set-Cookie", f"wta_sid={sid}; Path=/; HttpOnly; SameSite=Lax")])


def _state(db) -> dict:
    if db.schema is None:
        return {"session": None, "user": None, "committed": None, "sha": None}
    committed = store.committed(db)
    member = store.staff_member(db, db.staff_id)
    return {"session": db.sid, "user": member["username"] if member else None, "committed": committed,
            "sha": store.digest(committed)}


# -- WSGI ------------------------------------------------------------------------------------------------

#: Where a form's address leads when it is opened with the other method (history, a typed URL).
_PAGE_OF_ACTION = ((re.compile(r"(/book/\d+)/hold\Z"), r"\1/slots"),
                   (re.compile(r"(/book/\d+)/book\Z"), r"\1/confirm"),
                   (re.compile(r"(/bookings/CF-\d+)/(?:reschedule|cancel)\Z"), r"\1"),
                   (re.compile(r"(/patients/\d+)/(?:delete|waiting|notes(?:/\d+(?:/delete)?)?)\Z"), r"\1"),
                   (re.compile(r"/waiting/w\d+/(?:up|down|remove|restore|note)\Z"), "/waiting"))


def _wrong_method(x: Exchange, path: str):
    for pattern, page in _PAGE_OF_ACTION:
        if pattern.match(path):
            return go(pattern.sub(page, path))
    if any(pattern.match(path) for _, pattern, _, _ in _ROUTES):
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


def _cookie(environ) -> str | None:
    try:
        jar = SimpleCookie(environ.get("HTTP_COOKIE", ""))
    except CookieError:
        return None
    return jar["wta_sid"].value if "wta_sid" in jar else None


def application(environ, start_response):  # noqa: PLR0911 - one exit per kind of request
    path = environ.get("PATH_INFO") or "/"
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if path == "/healthz":  # ok only while the database answers
        ok = RECORDS.alive()
        return _respond(start_response, "200 OK" if ok else "503 Service Unavailable",
                        "application/json", json.dumps({"ok": ok, "app": APP_ID}))
    if path.startswith("/assets/"):
        return _asset(path[len("/assets/"):], start_response)
    sid = _cookie(environ)
    if path == "/login":
        return _sign_in(environ, sid, start_response)
    if path == "/logout" and method == "POST":
        RECORDS.sign_out(sid)
        return _respond(start_response, "303 See Other", None, "/login")
    if path == "/help/sign-in":
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", views.sign_in_help())
    if path == "/__wta/state" and not TEST_HOOKS:
        return _respond(start_response, "404 Not Found", "text/plain", "not found")
    handler, args, roles = None, (), EVERYONE
    for verb, pattern, allowed, candidate in _ROUTES:
        match = pattern.match(path)
        if match and verb == method:
            handler, args, roles = candidate, match.groups(), allowed
            break
    wants_json = method == "POST" and path.endswith("/note")
    with RECORDS.visit(sid) as db:  # the reply is worked out here and sent once committed
        if path == "/__wta/state":
            outcome = reply_json(_state(db))
        else:
            user = store.staff_member(db, db.staff_id)
            if user is None:
                if wants_json:
                    outcome = reply_json({"ok": False, "reason": "signed-out"})
                else:  # signed out: to the sign-in page, and back here afterwards
                    target = path + (f"?{environ['QUERY_STRING']}" if environ.get("QUERY_STRING") else "")
                    outcome = go("/login?next=" + quote(target if method == "GET" else "/", safe=""))
            else:
                exchange = Exchange(environ, db, user, args)
                if handler is not None and user["role"] not in roles:
                    outcome = reply_json({"ok": False, "reason": "forbidden"}) if wants_json \
                        else exchange.show(t("access.title"), views.access_notice())
                elif handler is None:
                    outcome = _wrong_method(exchange, path)
                else:
                    outcome = handler(exchange)
    return _respond(start_response, *outcome)


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - the stdlib's signature
        pass


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def main() -> None:
    global RECORDS  # noqa: PLW0603 - opened once, at start
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("clinicflow: DATABASE_URL must name the PostgreSQL database this server keeps its records in")
    RECORDS = Partitions(url, int(os.environ.get("WTA_SESSION_CAP") or 32))
    RECORDS.wait()
    RECORDS.reset(store.template(), store.STAFF["staff"], store.STAFF["rounds"])
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ["PORT"])
    with make_server(host, port, application, server_class=ThreadedServer, handler_class=QuietHandler) as server:
        print(f"{APP_ID} on http://{host}:{port}/", file=sys.stderr, flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
