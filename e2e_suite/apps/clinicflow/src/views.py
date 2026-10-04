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

import store
import wizard

HERE = Path(__file__).resolve().parent
LABELS: dict[str, str] = json.loads(
    (HERE.parent / "vocab" / f"{os.environ.get('WTA_VOCAB') or 'default'}.json").read_text(encoding="utf-8"))
TEMPLATES = {path.stem: path.read_text(encoding="utf-8") for path in (HERE / "templates").glob("*.html")}
_PLACEHOLDER = re.compile(r"""\{\{\s*(?:t\(\s*["']([\w.-]+)["']\s*\)|(\w+)(\|raw)?)\s*\}\}""")


def t(key: str) -> str:
    return LABELS[key]


def h(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def fill(name: str, **values) -> str:
    def substitute(match: re.Match) -> str:
        if match.group(1):
            return h(t(match.group(1)))
        value = values[match.group(2)]
        return str(value) if match.group(3) else h(value)
    return _PLACEHOLDER.sub(substitute, TEMPLATES[name])


def page(title: str, body: str, *, notice: str = "", error: str = "", resume: str = "") -> bytes:
    notice_html = f'<p class="notice" id="page-notice" role="status">{h(notice)}</p>' if notice else ""
    error_html = f'<p class="problem" id="page-error" role="alert">{h(error)}</p>' if error else ""
    resume_html = (f'<a id="nav-resume" href="{h(resume)}">{h(t("act.resume"))}</a>' if resume else "")
    return fill("layout", title=title, notice=notice_html, error=error_html, resume=resume_html,
                body=body).encode("utf-8")


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


# -- the wizard ------------------------------------------------------------------------------------


def home(resume: str) -> str:
    link = (f'<p><a id="home-resume" class="button" href="{h(resume)}">{h(t("act.resume"))}</a></p>'
            if resume else "")
    return fill("home", resume=link)


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


def slots(conn, row, practitioner: str) -> str:
    taken = store.occupancy_map(conn, practitioner)
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
    figures = store.tally(conn, practitioner)
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
                  f'<span id="moving-from">{h(store.slot_label(current_slot_of(conn, row)))}</span></p>')
    return fill("slots", draft=row["id"], tabs=tabs, practitioner=practitioner, head=head,
                rows="".join(body), taken=figures["taken"], others=figures["others"],
                yours=figures["yours"], held=held, onward=onward, moving=moving)


def current_slot_of(conn, row) -> str | None:
    if not row["booking_ref"]:
        return None
    return store.booking(conn, row["booking_ref"])["slot"]


def questions(row) -> str:
    options = "".join(f'<option value="{h(key)}"{" selected" if key == row["area"] else ""}>{h(name)}</option>'
                      for key, name in store.AREAS.items())
    return fill("questions", draft=row["id"], options=options, notes=row["notes"] or "")


def summary_rows(conn, row) -> dict:
    person = store.patient(conn, row["patient_id"])
    return {"service": store.SERVICES[row["service"]]["name"], "with_name": practitioner_name(row["practitioner"]),
            "when": store.slot_label(row["slot"]), "patient": person["name"] if person else "",
            "paid": paid_by(row)}


def confirm(conn, row) -> str:
    moving = ""
    if row["kind"] == "reschedule":
        moving = (f'<p class="lede">{h(t("confirm.moving"))} — {h(t("label.was"))} '
                  f'<span id="confirm-was">{h(store.slot_label(current_slot_of(conn, row)))}</span></p>')
    return fill("confirm", draft=row["id"], moving=moving, status=t(wizard.questions_status(row)),
                **summary_rows(conn, row))


def booked(conn, row, record, banner: str) -> str:
    banner_html = f'<p class="banner" id="booked-banner" role="status">{h(banner)}</p>' if banner else ""
    person = store.patient(conn, record["patient_id"])
    state = t("booking.active") if record["state"] == "active" else t("booking.cancelled")
    return fill("booked", banner=banner_html, ref=record["ref"], state=state,
                service=store.SERVICES[record["service"]]["name"],
                with_name=practitioner_name(record["practitioner"]), when=store.slot_label(record["slot"]),
                patient=person["name"], paid=paid_by(record), answers=answers_text(record))


def manage(conn, record) -> str:
    person = store.patient(conn, record["patient_id"])
    if record["state"] == "active":
        actions = fill("manage_actions", ref=record["ref"])
        state = t("booking.active")
    else:
        actions = ""
        state = t("booking.cancelled")
    return fill("manage", ref=record["ref"], state=state, service=store.SERVICES[record["service"]]["name"],
                with_name=practitioner_name(record["practitioner"]), when=store.slot_label(record["slot"]),
                patient=person["name"], paid=paid_by(record), actions=actions)


def booking_list(conn) -> str:
    upcoming = "".join(
        f'<tr><td><a id="open-{h(b["ref"])}" href="/bookings/{h(b["ref"])}">{h(b["ref"])}</a></td>'
        f'<td>{h(store.slot_label(b["slot"]))}</td><td>{h(practitioner_name(b["practitioner"]))}</td>'
        f'<td>{h(store.SERVICES[b["service"]]["name"])}</td></tr>'
        for b in store.bookings(conn, "active"))
    if not upcoming:
        upcoming = f'<tr><td colspan="4">{h(t("bookings.none"))}</td></tr>'
    cancelled = "".join(f'<li>{h(b["ref"])} · {h(store.slot_label(b["slot"]))} · '
                        f'{h(practitioner_name(b["practitioner"]))}</li>' for b in store.bookings(conn, "cancelled"))
    return fill("bookings", upcoming=upcoming, cancelled=cancelled)


def message(title_key: str, body_key: str) -> str:
    return fill("message", title=t(title_key), body=t(body_key))
