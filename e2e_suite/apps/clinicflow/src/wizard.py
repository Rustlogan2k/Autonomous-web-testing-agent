"""The booking wizard's shape: which steps each kind of draft walks, and which step it owes.

A draft lives at `/book/<id>/<step>`. Its *route* depends on the branch taken after the
service is chosen. The *owed* step is the first one on the route not yet satisfied; a draft
may revisit any step up to the owed one, never jump past it, and is booked only when the
owed step is `confirm`.
"""

from __future__ import annotations

import datetime as dt
import re

import store

#: Steps per kind of draft. `who` is where a new draft chooses its branch.
ROUTES = {
    "pending": ("who",),
    "new": ("details", "slots", "questions", "confirm"),
    "returning": ("lookup", "slots", "questions", "confirm"),
    "reschedule": ("slots", "confirm"),
}
TEXT_LIMIT = 5000
NOTES_LIMIT = 10000
EMAIL_SHAPE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
MEMBER_SHAPE = re.compile(r"\d{1,12}")


def needs_questions(row) -> bool:
    return bool(store.SERVICES[row["service"]]["questions"])


def answered(row) -> bool:
    return bool(row["area"]) and bool((row["notes"] or "").strip())


def satisfied(row, step: str) -> bool:
    if step == "who":
        return False
    if step in ("details", "lookup"):
        return row["patient_id"] is not None
    if step == "slots":
        return row["slot"] is not None
    if step == "questions":
        return not needs_questions(row) or answered(row)
    return False  # confirm is satisfied only by booking, which closes the draft


def owed(row) -> str:
    for step in ROUTES[row["kind"]]:
        if not satisfied(row, step):
            return step
    return ROUTES[row["kind"]][-1]


def may_view(row, step: str) -> bool:
    """Whether an open draft may show `step` now (otherwise it is sent to its owed step)."""
    if step == "who":
        return row["kind"] in ("pending", "new", "returning") and row["patient_id"] is None
    route = ROUTES[row["kind"]]
    if step not in route:
        return False
    if step in ("details", "lookup") and satisfied(row, step):
        return False  # registration is done once; the record is not edited mid-booking
    if step == "questions" and not needs_questions(row):
        return False
    return route.index(step) <= route.index(owed(row))


def after_slots(row) -> str:
    """Where the slot table's progress control leads once a time is held."""
    route = ROUTES[row["kind"]]
    for step in route[route.index("slots") + 1:]:
        if step == "questions" and not needs_questions(row):
            continue
        return step
    return "confirm"


def questions_status(row) -> str:
    if not needs_questions(row):
        return "status.not_needed"
    return "status.answered" if answered(row) else "status.not_answered"


# -- form rules ----------------------------------------------------------------------------------


def read_payment(form: dict) -> tuple[dict, str]:
    """The payment choice from a details or lookup form: (fields, error vocab key or '').

    `payment_mode` is written by the page's payment widget; the select itself is the
    fallback for a browser that runs no script.
    """
    mode = (form.get("payment_mode") or form.get("payment") or "").strip()
    if mode not in ("self", "insurer"):
        return {}, "msg.payment_needed"
    if mode == "self":
        return {"pay_mode": "self", "insurer": "", "member": ""}, ""
    insurer = form.get("insurer_name", "").strip()
    member = form.get("insurer_member", "").strip()
    if len(insurer) > TEXT_LIMIT:
        return {}, "msg.too_long"
    if not insurer or not MEMBER_SHAPE.fullmatch(member):
        return {}, "msg.insurer_needed"
    return {"pay_mode": "insurer", "insurer": insurer, "member": member}, ""


def read_registration(form: dict) -> tuple[dict, str]:
    name = form.get("full_name", "").strip()
    email = form.get("email", "").strip()
    raw_dob = form.get("dob", "").strip()
    if not name:
        return {}, "msg.name_needed"
    if len(name) > TEXT_LIMIT or len(email) > TEXT_LIMIT:
        return {}, "msg.too_long"
    try:
        dob = dt.date.fromisoformat(raw_dob)
    except ValueError:
        return {}, "msg.dob_bad"
    if dob > store.TODAY:
        return {}, "msg.dob_bad"
    if not EMAIL_SHAPE.fullmatch(email):
        return {}, "msg.email_bad"
    return {"name": name, "dob": dob.isoformat(), "email": email}, ""


def read_answers(form: dict) -> tuple[dict, str]:
    area = form.get("injury_area", "")
    notes = form.get("injury_notes", "").strip()
    if area not in store.AREAS or not notes:
        return {}, "msg.answers_needed"
    if len(notes) > NOTES_LIMIT:
        return {}, "msg.too_long"
    return {"area": area, "notes": notes}, ""
