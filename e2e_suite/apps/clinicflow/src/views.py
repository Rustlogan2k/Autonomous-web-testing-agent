"""Pages, from the HTML files in templates/ and a twelve-line placeholder renderer.

A template holds `{{ t(<quoted vocab key>) }}` for a label from the active vocabulary, `{{ name }}` for
an escaped value and `{{ name|raw }}` for a fragment that is already HTML. A placeholder
the caller did not supply is an error, never an empty string.
"""

from __future__ import annotations

import json
import os
import re
from html import escape
from pathlib import Path
from urllib.parse import urlencode

import store
import wizard

HERE = Path(__file__).resolve().parent
LABELS: dict[str, str] = json.loads(
    (HERE.parent / "vocab" / f"{os.environ.get('WTA_VOCAB') or 'default'}.json").read_text(encoding="utf-8"))
TEMPLATES = {path.stem: path.read_text(encoding="utf-8") for path in (HERE / "templates").glob("*.html")}
_PLACEHOLDER = re.compile(r"""\{\{\s*(?:t\(\s*["']([\w.-]+)["']\s*\)|(\w+)(\|raw)?)\s*\}\}""")
RECORD_TABS = ("details", "appointments", "notes")


def t(key: str) -> str:
    return LABELS[key]


def h(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def fill(template: str, /, **values) -> str:
    def substitute(match: re.Match) -> str:
        if match.group(1):
            return h(t(match.group(1)))
        value = values[match.group(2)]
        return str(value) if match.group(3) else h(value)
    return _PLACEHOLDER.sub(substitute, TEMPLATES[template])


def _nav(user: dict, resume: str) -> str:
    """The sections a role may use: navigation never links a role to a page it may not open."""
    if user["role"] == "practitioner":
        links = [("nav-day", "/my/day", "nav.day")]
    elif user["role"] == "manager":
        links = [("nav-utilisation", "/admin/utilisation", "nav.utilisation")]
    else:
        links = [("nav-book", "/book", "nav.book"), ("nav-bookings", "/bookings", "nav.bookings"),
                 ("nav-patients", "/patients", "nav.patients"), ("nav-waiting", "/waiting", "nav.waiting")]
    resume_html = f'<a id="nav-resume" href="{h(resume)}">{h(t("act.resume"))}</a>' if resume else ""
    return resume_html + "".join(f'<a id="{i}" href="{href}">{h(t(key))}</a>' for i, href, key in links)


def page(title: str, body: str, user: dict, *, notice: str = "", error: str = "", resume: str = "") -> bytes:
    notice_html = f'<p class="notice" id="page-notice" role="status">{h(notice)}</p>' if notice else ""
    error_html = f'<p class="problem" id="page-error" role="alert">{h(error)}</p>' if error else ""
    return fill("layout", title=title, notice=notice_html, error=error_html, nav=_nav(user, resume),
                who=f'{user["full_name"]} · {t("role." + user["role"])}', body=body).encode("utf-8")


def sign_in(wanted: str, username: str = "", refused: bool = False) -> bytes:
    problem = f'<p class="problem" id="login-error" role="alert">{h(t("login.refused"))}</p>' if refused else ""
    return fill("public", title=t("login.title"),
                body=fill("login", next=wanted, username=username, problem=problem)).encode("utf-8")


def sign_in_help() -> bytes:
    return fill("public", title=t("help.title"), body=fill("help")).encode("utf-8")


# -- shared pieces ---------------------------------------------------------------------------------


def practitioner_name(pid: str | None) -> str:
    return store.PRACTITIONERS[pid]["name"] if pid in store.PRACTITIONERS else ""


def paid_by(row) -> str:
    """How an appointment is paid for, as the patient reads it."""
    if row["pay_mode"] == "insurer":
        return f'{t("pay.insurer")}: {row["insurer"]} ({t("pay.member")} {row["member"]})'
    if row["pay_mode"] == "self":
        return t("pay.self")
    return ""


def answers_text(row) -> str:
    if not row["area"]:
        return ""
    return f'{store.AREAS.get(row["area"], row["area"])}: {row["notes"]}'


def payment_fields() -> str:
    return fill("payment")


def _options(pairs, chosen: str) -> str:
    return "".join(f'<option value="{h(v)}"{" selected" if v == chosen else ""}>{h(label)}</option>'
                   for v, label in pairs)


# -- landing pages -----------------------------------------------------------------------------------


def home(resume: str, waiting: dict[str, int]) -> str:
    link = (f'<p><a id="home-resume" class="button" href="{h(resume)}">{h(t("act.resume"))}</a></p>'
            if resume else "")
    queues = "".join(f'<li>{h(s["name"])}: <span id="waiting-count-{h(s["id"])}">{waiting.get(s["id"], 0)}</span></li>'
                     for s in store.SEED["services"])
    return fill("home", resume=link, queues=queues)


def home_practitioner() -> str:
    return fill("home_role", body=t("home.practitioner"), link_id="home-day", href="/my/day", label=t("nav.day"))


def home_manager() -> str:
    return fill("home_role", body=t("home.manager"), link_id="home-utilisation", href="/admin/utilisation",
                label=t("nav.utilisation"))


def access_notice() -> str:
    return fill("access")


# -- the wizard ------------------------------------------------------------------------------------


def services() -> str:
    cards = "".join(
        f'<li><button id="svc-{h(s["id"])}" name="service" value="{h(s["id"])}" type="submit">{h(s["name"])}'
        f'</button> <span class="muted">{s["minutes"]} {h(t("service.minutes"))}</span></li>'
        for s in store.SEED["services"])
    return fill("service", cards=cards)


def who(row) -> str:
    return fill("who", draft=row["id"], service=store.SERVICES[row["service"]]["name"])


def details(row) -> str:
    return fill("details", draft=row["id"], service=store.SERVICES[row["service"]]["name"],
                payment=payment_fields())


def lookup(row) -> str:
    return fill("lookup", draft=row["id"], service=store.SERVICES[row["service"]]["name"],
                payment=payment_fields())


def slots(db, row, practitioner: str) -> str:
    taken = store.occupancy_map(db, practitioner)
    own = f"draft:{row['id']}"
    head = "".join(f"<th scope=\"col\">{h(store.day_label(day))}</th>" for day in store.DAYS)
    body = []
    for time in store.TIMES:
        cells = []
        for day in store.DAYS:
            key = store.slot_key(day, time)
            holder = taken.get(key)
            state, css = (("cell.free", "free") if holder is None else
                          ("cell.held", "held") if holder == own else ("cell.booked", "booked"))
            button_css = "hold slot-free" if holder is None else "hold"
            cells.append(f'<td class="cell {css}"><span class="cell-state">{h(t(state))}</span>'
                         f'<button id="hold-{key}" class="{button_css}" name="slot" value="{key}" '
                         f'type="submit">{h(t("act.hold"))}</button></td>')
        body.append(f'<tr><th scope="row">{h(time)}</th>{"".join(cells)}</tr>')
    if row["kind"] == "reschedule":
        tabs = f'<p class="fixed-with">{h(practitioner_name(practitioner))}</p>'
    else:
        tabs = "".join(
            f'<a id="with-{h(p["id"])}" class="tab{" current" if p["id"] == practitioner else ""}" '
            f'href="/book/{row["id"]}/slots?with={h(p["id"])}">{h(p["name"])}</a>'
            for p in store.SEED["practitioners"] if p["service"] == row["service"])
        tabs = f'<nav class="tabs" aria-label="{h(t("slots.with"))}">{tabs}</nav>'
    figures = store.tally(db, practitioner, row)
    if row["slot"]:
        held = (f'<p class="held" id="held-slot">{h(t("slots.held"))} {h(store.slot_label(row["slot"]))} · '
                f'{h(practitioner_name(row["practitioner"]))}</p>')
        target = wizard.after_slots(row)
        label = t("act.to_questions") if target == "questions" else t("act.to_review")
        onward = f'<a id="slots-done" class="button primary" href="/book/{row["id"]}/{target}">{h(label)}</a>'
    else:
        held = f'<p class="held muted" id="held-slot">{h(t("slots.none_held"))}</p>'
        label = t("act.to_questions") if wizard.after_slots(row) == "questions" else t("act.to_review")
        onward = f'<button id="slots-done" class="primary" type="button" disabled>{h(label)}</button>'
    moving = ""
    if row["kind"] == "reschedule":
        moving = (f'<p class="muted">{h(t("label.was"))}: '
                  f'<span id="moving-from">{h(store.slot_label(current_slot_of(db, row)))}</span></p>')
    return fill("slots", draft=row["id"], tabs=tabs, practitioner=practitioner, head=head,
                rows="".join(body), taken=figures["taken"], others=figures["others"],
                yours=figures["yours"], held=held, onward=onward, moving=moving)


def current_slot_of(db, row) -> str | None:
    if not row["booking_ref"]:
        return None
    return store.booking(db, row["booking_ref"])["slot"]


def questions(row) -> str:
    options = "".join(f'<option value="{h(key)}"{" selected" if key == row["area"] else ""}>{h(name)}</option>'
                      for key, name in store.AREAS.items())
    return fill("questions", draft=row["id"], options=options, notes=row["notes"] or "")


def summary_rows(db, row) -> dict:
    person = store.patient(db, row["patient_id"])
    return {"service": store.SERVICES[row["service"]]["name"], "with_name": practitioner_name(row["practitioner"]),
            "when": store.slot_label(row["slot"]), "patient": person["full_name"] if person else "",
            "paid": paid_by(row)}


def confirm(db, row) -> str:
    moving = ""
    if row["kind"] == "reschedule":
        moving = (f'<p class="lede">{h(t("confirm.moving"))} — {h(t("label.was"))} '
                  f'<span id="confirm-was">{h(store.slot_label(current_slot_of(db, row)))}</span></p>')
    return fill("confirm", draft=row["id"], moving=moving, status=t(wizard.questions_status(row)),
                **summary_rows(db, row))


def booked(db, row, record, banner: str) -> str:
    banner_html = f'<p class="banner" id="booked-banner" role="status">{h(banner)}</p>' if banner else ""
    person = store.patient(db, record["patient_id"])
    state = t("booking.active") if record["state"] == "active" else t("booking.cancelled")
    return fill("booked", banner=banner_html, ref=record["ref"], state=state,
                service=store.SERVICES[record["service"]]["name"],
                with_name=practitioner_name(record["practitioner"]), when=store.slot_label(record["slot"]),
                patient=person["full_name"], paid=paid_by(record), answers=answers_text(record))


# -- appointments ----------------------------------------------------------------------------------------


def manage(db, record) -> str:
    person = store.patient(db, record["patient_id"])
    if record["state"] == "active":
        actions = fill("manage_actions", ref=record["ref"])
        state = t("booking.active")
    else:
        actions = ""
        state = t("booking.cancelled")
    history = "".join(f'<li>{h(t("event." + e["kind"]))} · {h(store.slot_label(e["slot"]))} · {h(e["by_name"])}</li>'
                      for e in store.booking_history(db, record["ref"]))
    return fill("manage", ref=record["ref"], state=state, service=store.SERVICES[record["service"]]["name"],
                with_name=practitioner_name(record["practitioner"]), when=store.slot_label(record["slot"]),
                patient=person["full_name"], patient_id=person["id"], paid=paid_by(record), actions=actions,
                history=history)


def booking_list(db, staff_id: str, practitioner: str, sort: str) -> str:
    def rows(state: str):
        return store.bookings(db, state, staff_id, practitioner, sort)
    upcoming = "".join(
        f'<tr><td><a id="open-{h(b["ref"])}" href="/bookings/{h(b["ref"])}">{h(b["ref"])}</a></td>'
        f'<td>{h(store.slot_label(b["slot"]))}</td><td>{h(practitioner_name(b["practitioner"]))}</td>'
        f'<td>{h(store.SERVICES[b["service"]]["name"])}</td></tr>'
        for b in rows("active"))
    if not upcoming:
        upcoming = f'<tr><td colspan="4">{h(t("bookings.none"))}</td></tr>'
    cancelled = "".join(f'<li>{h(b["ref"])} · {h(store.slot_label(b["slot"]))} · '
                        f'{h(practitioner_name(b["practitioner"]))}</li>' for b in rows("cancelled"))
    with_options = _options([("", t("filter.everyone"))] + [(p["id"], p["name"]) for p in store.SEED["practitioners"]],
                            practitioner)
    sort_options = _options([("when", t("sort.when")), ("ref", t("sort.reference"))], sort)
    return fill("bookings", upcoming=upcoming, cancelled=cancelled, with_options=with_options,
                sort_options=sort_options)


# -- patients ------------------------------------------------------------------------------------------------


def directory(listing: dict, q: str, sort: str) -> str:
    def link(page: int) -> str:
        return "/patients?" + urlencode({"q": q, "sort": sort, "page": page})
    rows = "".join(
        f'<tr><td><a id="open-patient-{p["id"]}" href="/patients/{p["id"]}">{h(p["full_name"])}</a></td>'
        f'<td>{h(p["dob"].isoformat())}</td><td>{h(p["email"])}</td></tr>' for p in listing["rows"])
    if listing["total"]:
        summary = (f'<p class="range">{h(t("patients.showing"))} <output id="range-from">{listing["first"]}</output>–'
                   f'<output id="range-to">{listing["last"]}</output> {h(t("label.of"))} '
                   f'<output id="match-total">{listing["total"]}</output></p>')
    else:
        summary = f'<p class="range" id="no-matches">{h(t("patients.none"))}</p>'
    page, pages = listing["page"], listing["pages"]

    def pager_button(ident: str, target: int, key: str, enabled: bool) -> str:
        if not enabled:
            return f'<span class="pager-off">{h(t(key))}</span>'
        return f'<a id="{ident}" class="button" href="{h(link(target))}">{h(t(key))}</a>'
    pager = (pager_button("page-first", 1, "act.first", page > 1) + pager_button("page-prev", page - 1, "act.prev", page > 1)
             + f'<span>{h(t("label.page"))} <output id="page-number">{page}</output> {h(t("label.of"))} '
               f'<output id="page-count">{pages}</output></span>'
             + pager_button("page-next", page + 1, "act.next", page < pages)
             + pager_button("page-last", pages, "act.last", page < pages))
    sort_options = _options([("name", t("sort.name")), ("dob", t("sort.dob")), ("newest", t("sort.newest"))], sort)
    return fill("patients", q=q, sort_options=sort_options, summary=summary, rows=rows, pager=pager)


def patient_form(person: dict | None) -> str:
    if person is None:
        return fill("patient_form", title=t("patient.new_title"), action="/patients/new", name="", dob="",
                    email="", phone="", back="/patients")
    return fill("patient_form", title=t("patient.edit_title"), action=f"/patients/{person['id']}/edit",
                name=person["full_name"], dob=person["dob"].isoformat(), email=person["email"],
                phone=person["phone"] or "", back=f"/patients/{person['id']}")


def patient_record(db, person: dict, tab: str, editing: int | None) -> str:
    pid = person["id"]
    def tab_link(name: str) -> str:
        current = ' current" aria-current="page' if name == tab else ""
        return (f'<a id="tab-{name}" class="tab{current}" href="/patients/{pid}?tab={name}">'
                f'{h(t("tab." + name))}</a>')
    tabs = "".join(tab_link(name) for name in RECORD_TABS)
    if tab == "appointments":
        visits = "".join(
            f'<tr><td><a id="visit-{h(b["ref"])}" href="/bookings/{h(b["ref"])}">{h(b["ref"])}</a></td>'
            f'<td>{h(store.slot_label(b["slot"]))}</td><td>{h(practitioner_name(b["practitioner"]))}</td>'
            f'<td>{h(t("booking.active") if b["state"] == "active" else t("booking.cancelled"))}</td></tr>'
            for b in store.bookings_of_patient(db, pid))
        panel = fill("record_visits", rows=visits or f'<tr><td colspan="4">{h(t("record.no_visits"))}</td></tr>')
    elif tab == "notes":
        items = []
        for note in store.notes_of(db, pid):
            nid = note["id"]
            if nid == editing:
                items.append(fill("note_edit", pid=pid, nid=nid, body=note["body"]))
            else:
                items.append(fill("note_item", pid=pid, nid=nid, body=note["body"],
                                  meta=f'{note["written_on"].isoformat()} · {note["by_name"]}'))
        panel = fill("record_notes", pid=pid, notes="".join(items) or f'<li>{h(t("record.no_notes"))}</li>')
    else:
        queues = "".join(f'<li>{h(w["service"])}: {h(w["note"])}</li>' for w in store.waiting_of_patient(db, pid))
        services = _options([(s["id"], s["name"]) for s in store.SEED["services"]], "gp")
        panel = fill("record_details", pid=pid, dob=person["dob"].isoformat(), email=person["email"],
                     phone=person["phone"] or "—", queues=queues or f'<li>{h(t("record.no_queues"))}</li>',
                     services=services)
    return fill("patient", name=person["full_name"], pid=pid, tabs=tabs, panel=panel)


# -- the waiting lists ----------------------------------------------------------------------------------------


def waiting(db, service: str, removed: dict | None) -> str:
    counts = store.waiting_counts(db)
    tabs = "".join(
        f'<a id="queue-{h(s["id"])}" class="tab{" current" if s["id"] == service else ""}" '
        f'href="/waiting?service={h(s["id"])}">{h(s["name"])} ({counts.get(s["id"], 0)})</a>'
        for s in store.SEED["services"])
    entries = store.waiting_list(db, service)
    rows = "".join(fill("waiting_row", wid=e["id"], number=n, pid=e["patient_id"], name=e["full_name"],
                        note=e["note"], since=e["added_on"].isoformat())
                   for n, e in enumerate(entries, 1))
    undo = ""
    if removed is not None:
        undo = fill("waiting_undo", wid=removed["id"], name=removed["full_name"])
    return fill("waiting", tabs=tabs, service=store.SERVICES[service]["name"], undo=undo,
                rows=rows or f'<tr><td colspan="5">{h(t("waiting.empty"))}</td></tr>',
                count=len(entries), first=entries[0]["full_name"] if entries else "—")


# -- other roles ------------------------------------------------------------------------------------------------


def my_day(practitioner: dict, visits: list[dict]) -> str:
    rows = "".join(f'<tr><td>{h(store.slot_label(v["slot"]))}</td><td>{h(v["patient"])}</td>'
                   f'<td>{h(v["service"])}</td><td>{h(v["ref"])}</td></tr>' for v in visits)
    return fill("my_day", name=practitioner["name"], room=practitioner["room"],
                rows=rows or f'<tr><td colspan="4">{h(t("day.none"))}</td></tr>')


def utilisation(rows: list[dict]) -> str:
    body = "".join(f'<tr><td>{h(r["name"])}</td><td>{h(r["room"])}</td><td>{r["booked"]}</td><td>{r["shown"]}</td>'
                   f'<td>{round(100 * r["booked"] / r["shown"]) if r["shown"] else 0}%</td></tr>' for r in rows)
    return fill("utilisation", rows=body)


def message(title_key: str, body_key: str) -> str:
    return fill("message", title=t(title_key), body=t(body_key))
