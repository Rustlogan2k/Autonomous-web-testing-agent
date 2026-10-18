"""Clinicflow's records, as SQL over the session's own schema (partitions.py).

Who occupies which appointment time is kept in one table, `occupancy`, keyed by
(practitioner, time). Every row has a holder: an open booking draft, or a booking. The slot
table's three figures are computed from different tables on purpose: *Taken* from
`occupancy`, *Taken by other patients* from the occupancy rows held for other patients, and
*Yours* from the draft's patient's `bookings` and the draft's own hold. They agree exactly
when every operation keeps the occupancy rows in step with what owns them.

Every function takes `db` (a partitions.Records: one request's transaction). Rows come back
as dicts; drafts and bookings carry their time as a slot key ("0611-1100"), as v1 did.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEED = json.loads((ROOT.parent / "seed" / "clinic.json").read_text(encoding="utf-8"))
STAFF = json.loads((ROOT.parent / "seed" / "staff.json").read_text(encoding="utf-8"))
NOW = dt.datetime.fromisoformat(os.environ.get("WTA_FIXED_NOW") or "2026-06-10T09:00:00")  # wta-clock
TODAY = NOW.date()

SERVICES = {s["id"]: s for s in SEED["services"]}
PRACTITIONERS = {p["id"]: p for p in SEED["practitioners"]}
AREAS = {a["id"]: a["name"] for a in SEED["injury_areas"]}
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH_NAMES = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DIRECTORY_PAGE = 10
DIRECTORY_SORTS = {
    "name": 'lower(full_name) COLLATE "C", id',
    "dob": "dob, id",
    "newest": "id DESC",
}


# -- the schedule ----------------------------------------------------------------------------


def clinic_days() -> list[dt.date]:
    """The working days shown on every slot table: the next weekdays after today."""
    days, day = [], TODAY
    while len(days) < SEED["days_shown"]:
        day += dt.timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return days


DAYS = clinic_days()
TIMES = tuple(SEED["times"])


def slot_key(day: dt.date, time: str) -> str:
    return f"{day.month:02d}{day.day:02d}-{time.replace(':', '')}"


SLOTS = {slot_key(day, time): (day, time) for day in DAYS for time in TIMES}


def slot_at(key: str) -> dt.datetime:
    day, time = SLOTS[key]
    return dt.datetime.combine(day, dt.time.fromisoformat(time))


def key_of(at: dt.datetime | None) -> str | None:
    return None if at is None else slot_key(at.date(), at.strftime("%H:%M"))


def day_label(day: dt.date) -> str:
    return f"{WEEKDAY_NAMES[day.weekday()]} {day.day} {MONTH_NAMES[day.month]}"


def slot_label(key: str | None) -> str:
    if key not in SLOTS:
        return ""
    day, time = SLOTS[key]
    return f"{day_label(day)}, {time}"


# -- the seeded template ---------------------------------------------------------------------------


def template() -> dict[str, list[dict]]:
    """The clinic on the first morning, table by table (the slot grid follows the clock)."""
    others = [(p, day, time) for p in SEED["practitioners"] for day, time in p["booked_by_others"]
              if day < len(DAYS)]
    bookings, occupancy, events = [], [], []
    for n, (practitioner, day, time) in enumerate(others, 1):
        ref = f"CF-{900 + n:04d}"
        at = slot_at(slot_key(DAYS[day], time)).isoformat(sep=" ")
        insurer = SEED["insurers_on_record"].get(ref)
        bookings.append({"ref": ref, "draft_id": None, "patient_id": 3 + (n - 1) % 7,
                         "service_id": practitioner["service"], "practitioner_id": practitioner["id"],
                         "slot_at": at, "pay_mode": "insurer" if insurer else "self",
                         "insurer": insurer[0] if insurer else None, "member": insurer[1] if insurer else None,
                         "state": "active", "booked_by": "tom"})
        occupancy.append({"practitioner_id": practitioner["id"], "slot_at": at, "holder_kind": "booking",
                          "draft_id": None, "booking_ref": ref})
        events.append({"ref": ref, "seq": 1, "kind": "booked", "slot_at": at, "by_user": "tom"})
    return {
        "counters": [{"name": "drafts", "value": 0}, {"name": "booking", "value": 0},
                     {"name": "patients", "value": len(SEED["patients"])},
                     {"name": "notes", "value": len(SEED["notes"])},
                     {"name": "waiting", "value": len(SEED["waiting"])}],
        "services": [{"id": s["id"], "name": s["name"], "minutes": s["minutes"],
                      "needs_questions": s["questions"], "position": n} for n, s in enumerate(SEED["services"], 1)],
        "practitioners": [{"id": p["id"], "name": p["name"], "service_id": p["service"], "room": p["room"],
                           "position": n} for n, p in enumerate(SEED["practitioners"], 1)],
        "users": [{k: s[k] for k in ("id", "username", "full_name", "role", "practitioner_id")}
                  for s in STAFF["staff"]],
        "slots": [{"practitioner_id": p["id"], "at": slot_at(k).isoformat(sep=" ")}
                  for p in SEED["practitioners"] for k in SLOTS],
        "patients": [{"id": n, "full_name": p["name"], "dob": p["dob"], "email": p["email"], "phone": p["phone"],
                      "origin": "record"} for n, p in enumerate(SEED["patients"], 1)],
        "patient_notes": [{"id": n, "patient_id": note["patient"], "body": note["body"],
                           "written_on": note["written_on"], "written_by": note["by"]}
                          for n, note in enumerate(SEED["notes"], 1)],
        "injury_areas": [{"id": a["id"], "name": a["name"], "position": n}
                         for n, a in enumerate(SEED["injury_areas"], 1)],
        "bookings": bookings, "occupancy": occupancy, "booking_events": events,
        "waiting_entries": [
            {"id": f"w{n}", "patient_id": w["patient"], "service_id": w["service"],
             "position": 1 + sum(1 for v in SEED["waiting"][:n - 1] if v["service"] == w["service"]),
             "note": w["note"], "state": "waiting", "added_on": w["added_on"], "added_by": "tom"}
            for n, w in enumerate(SEED["waiting"], 1)],
    }


def bump(db, name: str) -> int:
    return db.value("UPDATE counters SET value = value + 1 WHERE name = :name RETURNING value", name=name)


# -- one-shot messages (not part of the committed state) ---------------------------------------


def say(db, key: str, message: str) -> None:
    db.do("INSERT INTO flashes VALUES (:key, :message) ON CONFLICT (key) DO UPDATE SET message = EXCLUDED.message",
          key=key, message=message)


def hear(db, key: str) -> str:
    return db.value("DELETE FROM flashes WHERE key = :key RETURNING message", key=key) or ""


def staff_member(db, staff_id: str | None) -> dict | None:
    return db.row("SELECT * FROM users WHERE id = :id", id=staff_id) if staff_id else None


# -- drafts and bookings ------------------------------------------------------------------------------

#: A draft as the wizard reads it: its time as a slot key, and the pre-visit answers (a
#: reschedule draft answers with the questionnaire of the booking it moves).
_DRAFT = ("SELECT d.*, d.service_id AS service, d.practitioner_id AS practitioner, q.area_id AS area, "
          "q.notes FROM drafts d LEFT JOIN bookings b ON b.ref = d.booking_ref AND d.kind = 'reschedule' "
          "LEFT JOIN questionnaires q ON q.draft_id = coalesce(b.draft_id, d.id)")
_BOOKING = ("SELECT b.*, b.service_id AS service, b.practitioner_id AS practitioner, q.area_id AS area, "
            "q.notes FROM bookings b LEFT JOIN questionnaires q ON q.draft_id = b.draft_id")
BOOKING_SORTS = {"when": "b.slot_at, b.ref", "ref": 'b.ref COLLATE "C"'}


def _keyed(row: dict | None) -> dict | None:
    if row is not None:
        row["slot"] = key_of(row.get("slot_at"))
    return row


def draft(db, draft_id: int) -> dict | None:
    return _keyed(db.row(f"{_DRAFT} WHERE d.id = :id", id=draft_id))


def open_draft(db, staff_id: str) -> dict | None:
    return _keyed(db.row(f"{_DRAFT} WHERE d.state = 'open' AND d.created_by = :me ORDER BY d.id DESC LIMIT 1",
                         me=staff_id))


def patient(db, patient_id) -> dict | None:
    return db.row("SELECT * FROM patients WHERE id = :id", id=patient_id) if patient_id is not None else None


def patient_by_email(db, email: str) -> dict | None:
    return db.row("SELECT * FROM patients WHERE lower(email) = lower(:email) ORDER BY id LIMIT 1",
                  email=email.strip())


def booking(db, ref: str) -> dict | None:
    return _keyed(db.row(f"{_BOOKING} WHERE b.ref = :ref", ref=ref))


def bookings(db, state: str, staff_id: str, practitioner: str = "", sort: str = "when") -> list[dict]:
    """The appointments this member of staff booked, in `state`, optionally with one practitioner."""
    order = BOOKING_SORTS.get(sort, BOOKING_SORTS["when"])
    return [_keyed(r) for r in db.rows(
        f"{_BOOKING} WHERE b.state = :state AND b.booked_by = :me "
        f"AND (:who = '' OR b.practitioner_id = :who) ORDER BY {order}",
        state=state, me=staff_id, who=practitioner)]


def bookings_of_patient(db, patient_id: int) -> list[dict]:
    return [_keyed(r) for r in db.rows(f"{_BOOKING} WHERE b.patient_id = :p ORDER BY b.slot_at, b.ref",
                                       p=patient_id)]


def booking_place(db, ref: str) -> tuple[str, dt.datetime]:
    row = db.row("SELECT practitioner_id, slot_at FROM bookings WHERE ref = :ref", ref=ref)
    return row["practitioner_id"], row["slot_at"]


def booking_history(db, ref: str) -> list[dict]:
    return [_keyed(r) for r in db.rows(
        "SELECT e.seq, e.kind, e.slot_at, u.full_name AS by_name FROM booking_events e "
        "JOIN users u ON u.id = e.by_user WHERE e.ref = :ref ORDER BY e.seq", ref=ref)]


def _holder(row: dict) -> str:
    return f"draft:{row['draft_id']}" if row["holder_kind"] == "draft" else f"booking:{row['booking_ref']}"


def holder_at(db, practitioner: str, slot: str) -> str | None:
    row = db.row("SELECT * FROM occupancy WHERE practitioner_id = :p AND slot_at = :at",
                 p=practitioner, at=slot_at(slot))
    return _holder(row) if row else None


def occupancy_map(db, practitioner: str) -> dict[str, str]:
    return {key_of(r["slot_at"]): _holder(r)
            for r in db.rows("SELECT * FROM occupancy WHERE practitioner_id = :p", p=practitioner)}


def tally(db, practitioner: str, row: dict) -> dict[str, int]:
    """The slot table's figures for one practitioner, seen from draft `row` (module docstring)."""
    patient_id = row["patient_id"]
    taken = db.value("SELECT count(*) FROM occupancy WHERE practitioner_id = :p", p=practitioner)
    others = db.value(
        "SELECT count(*) FROM occupancy o LEFT JOIN bookings b ON b.ref = o.booking_ref "
        "LEFT JOIN drafts d ON d.id = o.draft_id WHERE o.practitioner_id = :p "
        "AND coalesce(b.patient_id, d.patient_id) IS DISTINCT FROM :patient", p=practitioner, patient=patient_id)
    booked = db.value("SELECT count(*) FROM bookings WHERE practitioner_id = :p AND state = 'active' "
                      "AND patient_id = :patient", p=practitioner, patient=patient_id)
    holding = db.value("SELECT count(*) FROM drafts WHERE id = :d AND state = 'open' AND practitioner_id = :p "
                       "AND slot_at IS NOT NULL", d=row["id"], p=practitioner)
    return {"taken": taken, "others": others, "yours": booked + holding}


# -- writes ------------------------------------------------------------------------------------

_DRAFT_COLUMNS = {"practitioner": "practitioner_id"}


def _release_draft_hold(db, draft_id: int) -> None:
    db.do("DELETE FROM occupancy WHERE draft_id = :d", d=draft_id)


def start_draft(db, staff_id: str, kind: str, service: str, **fields) -> int:
    """Open a new booking draft. One draft is open at a time per member of staff: older ones
    are closed and whatever time they held goes back on the schedule."""
    for old in db.rows("SELECT id FROM drafts WHERE state = 'open' AND created_by = :me", me=staff_id):
        _release_draft_hold(db, old["id"])
        db.do("UPDATE drafts SET state = 'replaced' WHERE id = :d", d=old["id"])
    draft_id = bump(db, "drafts")
    db.do("INSERT INTO drafts (id, kind, state, service_id, created_by) VALUES (:id, :kind, 'open', :service, :me)",
          id=draft_id, kind=kind, service=service, me=staff_id)
    if fields:
        update_draft(db, draft_id, **fields)
    return draft_id


def update_draft(db, draft_id: int, **fields) -> None:
    """Change draft fields; `area` and `notes` are the draft's questionnaire, `slot` its time."""
    answers = {k: fields.pop(k) for k in ("area", "notes") if k in fields}
    if answers.get("area"):
        db.do("INSERT INTO questionnaires VALUES (:d, :area, :notes) ON CONFLICT (draft_id) "
              "DO UPDATE SET area_id = EXCLUDED.area_id, notes = EXCLUDED.notes", d=draft_id, **answers)
    if "slot" in fields:
        fields["slot_at"] = slot_at(fields.pop("slot"))
    columns = {_DRAFT_COLUMNS.get(k, k): ((v or None) if k in ("insurer", "member") else v)
               for k, v in fields.items()}
    if columns:
        assignments = ", ".join(f"{column} = :{column}" for column in columns)
        db.do(f"UPDATE drafts SET {assignments} WHERE id = :draft_id", draft_id=draft_id, **columns)


def register_patient(db, name: str, dob: str, email: str, phone: str | None = None) -> int:
    patient_id = bump(db, "patients")
    db.do("INSERT INTO patients VALUES (:id, :name, CAST(:dob AS date), :email, :phone, 'registered')",
          id=patient_id, name=name, dob=dob, email=email, phone=phone)
    return patient_id


def hold_slot(db, row: dict, practitioner: str, slot: str) -> str:
    """Hold `slot` for the draft `row`. Returns '' on success or the vocab key of the refusal."""
    if slot not in SLOTS or practitioner not in PRACTITIONERS:
        return "msg.slot_unknown"
    holder = holder_at(db, practitioner, slot)
    if holder == f"draft:{row['id']}":
        return "msg.slot_yours"
    if holder is not None:
        return "msg.slot_taken"
    _release_draft_hold(db, row["id"])  # a draft holds one time at most
    db.do("INSERT INTO occupancy VALUES (:p, :at, 'draft', :d, NULL)", p=practitioner, at=slot_at(slot), d=row["id"])
    update_draft(db, row["id"], practitioner=practitioner, slot=slot)
    return ""


def _event(db, ref: str, kind: str, staff_id: str) -> None:
    db.do("INSERT INTO booking_events SELECT :ref, coalesce(max(seq), 0) + 1, :kind, "
          "(SELECT slot_at FROM bookings WHERE ref = :ref), :me FROM booking_events WHERE ref = :ref",
          ref=ref, kind=kind, me=staff_id)


def _hand_hold_to_booking(db, draft_id: int, ref: str) -> None:
    db.do("UPDATE occupancy SET holder_kind = 'booking', booking_ref = :ref, draft_id = NULL "
          "WHERE draft_id = :d", ref=ref, d=draft_id)


def create_booking(db, row: dict, staff_id: str) -> str:
    """Turn an open new-appointment draft into an appointment; its hold becomes the booking's."""
    ref = f"CF-{1000 + bump(db, 'booking')}"
    db.do("INSERT INTO bookings VALUES (:ref, :d, :patient, :service, :p, :at, :pay, :insurer, :member, "
          "'active', :me)", ref=ref, d=row["id"], patient=row["patient_id"], service=row["service"],
          p=row["practitioner"], at=row["slot_at"], pay=row["pay_mode"], insurer=row["insurer"],
          member=row["member"], me=staff_id)
    _hand_hold_to_booking(db, row["id"], ref)
    update_draft(db, row["id"], state="booked", booking_ref=ref)
    _event(db, ref, "booked", staff_id)
    return ref


def move_booking(db, row: dict, staff_id: str) -> str:
    """Complete a reschedule draft: the appointment takes the held time and gives back its old one."""
    ref = row["booking_ref"]
    leaving = booking_place(db, ref)  # where the visit is now: read it before the booking moves
    db.do("UPDATE bookings SET practitioner_id = :p, slot_at = :at WHERE ref = :ref",
          p=row["practitioner"], at=row["slot_at"], ref=ref)
    db.do("DELETE FROM occupancy WHERE practitioner_id = :p AND slot_at = :at AND booking_ref = :ref",
          p=leaving[0], at=leaving[1], ref=ref)
    _hand_hold_to_booking(db, row["id"], ref)
    update_draft(db, row["id"], state="done")
    _event(db, ref, "moved", staff_id)
    return ref


def cancel_booking(db, ref: str, staff_id: str) -> None:
    """Cancel an appointment, free its time, and close a reschedule still open for it."""
    db.do("UPDATE bookings SET state = 'cancelled' WHERE ref = :ref", ref=ref)
    db.do("DELETE FROM occupancy WHERE booking_ref = :ref", ref=ref)
    for pending in db.rows("SELECT id FROM drafts WHERE state = 'open' AND booking_ref = :ref", ref=ref):
        _release_draft_hold(db, pending["id"])
        db.do("UPDATE drafts SET state = 'replaced' WHERE id = :d", d=pending["id"])
    _event(db, ref, "cancelled", staff_id)


# -- the patient directory and records ------------------------------------------------------------


def directory(db, q: str, sort: str, page: int) -> dict:
    """One page of the patient directory: name or email containing `q`, in `sort` order."""
    needle = q.strip().lower()
    where = "WHERE (:q = '' OR strpos(lower(full_name), :q) > 0 OR strpos(lower(email), :q) > 0)"
    total = db.value(f"SELECT count(*) FROM patients {where}", q=needle)
    pages = max(1, -(-total // DIRECTORY_PAGE))
    page = min(max(page, 1), pages)
    first = (page - 1) * DIRECTORY_PAGE
    rows = db.rows(f"SELECT * FROM patients {where} ORDER BY {DIRECTORY_SORTS[sort]} LIMIT :n OFFSET :skip",
                   q=needle, n=DIRECTORY_PAGE, skip=first)
    last = min(first + DIRECTORY_PAGE, total)  # the range shown: rows first+1 .. last
    return {"rows": rows, "total": total, "page": page, "pages": pages, "first": first + 1, "last": last}


def update_patient(db, patient_id: int, name: str, dob: str, email: str, phone: str | None) -> None:
    db.do("UPDATE patients SET full_name = :name, dob = CAST(:dob AS date), email = :email, phone = :phone "
          "WHERE id = :id", id=patient_id, name=name, dob=dob, email=email, phone=phone)


def patient_in_use(db, patient_id: int) -> bool:
    """A patient with appointments or a booking in progress keeps their record."""
    return bool(db.value("SELECT (SELECT count(*) FROM bookings WHERE patient_id = :p) "
                         "+ (SELECT count(*) FROM drafts WHERE patient_id = :p)", p=patient_id))


def delete_patient(db, patient_id: int) -> None:
    db.do("DELETE FROM patient_notes WHERE patient_id = :p", p=patient_id)
    db.do("DELETE FROM waiting_entries WHERE patient_id = :p", p=patient_id)
    db.do("DELETE FROM patients WHERE id = :p", p=patient_id)


def notes_of(db, patient_id: int) -> list[dict]:
    return db.rows("SELECT n.*, u.full_name AS by_name FROM patient_notes n JOIN users u ON u.id = n.written_by "
                   "WHERE n.patient_id = :p ORDER BY n.id DESC", p=patient_id)


def add_note(db, patient_id: int, body: str, staff_id: str) -> int:
    note_id = bump(db, "notes")
    db.do("INSERT INTO patient_notes VALUES (:id, :p, :body, :on, :me)",
          id=note_id, p=patient_id, body=body, on=TODAY, me=staff_id)
    return note_id


def edit_note(db, patient_id: int, note_id: int, body: str) -> bool:
    return bool(db.value("UPDATE patient_notes SET body = :body WHERE id = :id AND patient_id = :p RETURNING id",
                         body=body, id=note_id, p=patient_id))


def delete_note(db, patient_id: int, note_id: int) -> bool:
    return bool(db.value("DELETE FROM patient_notes WHERE id = :id AND patient_id = :p RETURNING id",
                         id=note_id, p=patient_id))


# -- the waiting lists ------------------------------------------------------------------------------


def waiting_list(db, service: str) -> list[dict]:
    return db.rows("SELECT w.*, p.full_name FROM waiting_entries w JOIN patients p ON p.id = w.patient_id "
                   "WHERE w.service_id = :s AND w.state = 'waiting' ORDER BY w.position", s=service)


def waiting_counts(db) -> dict[str, int]:
    return {r["service_id"]: r["n"] for r in db.rows(
        "SELECT service_id, count(*) AS n FROM waiting_entries WHERE state = 'waiting' GROUP BY service_id")}


def waiting_entry(db, entry_id: str) -> dict | None:
    return db.row("SELECT w.*, p.full_name FROM waiting_entries w JOIN patients p ON p.id = w.patient_id "
                  "WHERE w.id = :id", id=entry_id)


def waiting_of_patient(db, patient_id: int) -> list[dict]:
    return db.rows("SELECT w.*, s.name AS service FROM waiting_entries w JOIN services s ON s.id = w.service_id "
                   "WHERE w.patient_id = :p AND w.state = 'waiting' ORDER BY s.position", p=patient_id)


def add_to_waiting_list(db, patient_id: int, service: str, note: str, staff_id: str) -> str:
    entry_id = f"w{bump(db, 'waiting')}"
    db.do("INSERT INTO waiting_entries SELECT :id, :p, :s, coalesce(max(position), 0) + 1, :note, 'waiting', "
          ":on, :me FROM waiting_entries WHERE service_id = :s",
          id=entry_id, p=patient_id, s=service, note=note, on=TODAY, me=staff_id)
    return entry_id


def move_waiting(db, entry: dict, direction: str) -> bool:
    """Swap an entry with its neighbour above or below among those still waiting."""
    compare, order = ("<", "DESC") if direction == "up" else (">", "ASC")
    neighbour = db.row(f"SELECT id, position FROM waiting_entries WHERE service_id = :s AND state = 'waiting' "
                       f"AND position {compare} :pos ORDER BY position {order} LIMIT 1",
                       s=entry["service_id"], pos=entry["position"])
    if neighbour is None:
        return False
    db.do("UPDATE waiting_entries SET position = :pos WHERE id = :id", pos=neighbour["position"], id=entry["id"])
    db.do("UPDATE waiting_entries SET position = :pos WHERE id = :id", pos=entry["position"], id=neighbour["id"])
    return True


def save_waiting_note(db, entry_id: str, note: str) -> None:
    db.do("UPDATE waiting_entries SET note = :note WHERE id = :id", note=note, id=entry_id)


def set_waiting_state(db, entry_id: str, state: str) -> None:
    """Remove an entry (it keeps its place, so Undo puts it back where it was) or restore it."""
    db.do("UPDATE waiting_entries SET state = :state WHERE id = :id", state=state, id=entry_id)


# -- the other roles' pages ---------------------------------------------------------------------------


def day_list(db, practitioner: str) -> list[dict]:
    return [_keyed(r) for r in db.rows(
        "SELECT b.ref, b.slot_at, p.full_name AS patient, s.name AS service FROM bookings b "
        "JOIN patients p ON p.id = b.patient_id JOIN services s ON s.id = b.service_id "
        "WHERE b.practitioner_id = :p AND b.state = 'active' ORDER BY b.slot_at", p=practitioner)]


def utilisation(db) -> list[dict]:
    return db.rows(
        "SELECT pr.id, pr.name, pr.room, (SELECT count(*) FROM slots s WHERE s.practitioner_id = pr.id) AS shown, "
        "(SELECT count(*) FROM bookings b WHERE b.practitioner_id = pr.id AND b.state = 'active') AS booked "
        "FROM practitioners pr ORDER BY pr.position")


# -- committed state ------------------------------------------------------------------------------------


def _plain(value):
    return value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else value


def committed(db) -> dict:
    """Business state: patients and their notes, drafts and answers, bookings, who holds which
    time, the booking history and the waiting lists. Never flashes, counters or sessions."""
    tables = {"patients": "id", "patient_notes": "id", "drafts": "id", "questionnaires": "draft_id",
              "bookings": 'ref COLLATE "C"', "occupancy": 'practitioner_id COLLATE "C", slot_at',
              "booking_events": 'ref COLLATE "C", seq', "waiting_entries": 'id COLLATE "C"'}
    return {table: [{k: _plain(v) for k, v in row.items()}
                    for row in db.rows(f"SELECT * FROM {table} ORDER BY {order}")]
            for table, order in tables.items()}


def digest(state: dict) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
