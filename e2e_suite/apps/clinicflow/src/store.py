"""Clinicflow's records: one SQLite file per browser session, seeded from seed/clinic.json.

Who occupies which appointment time is kept in one table, `occupancy`, keyed by
(practitioner, slot). Every row has a holder:

* ``other``          -- a seeded appointment belonging to another patient;
* ``draft:<id>``     -- a time held by an open booking draft of this session;
* ``booking:<ref>``  -- the time of one of this session's active appointments.

The slot table's three figures are computed from different tables on purpose: *Taken*
from `occupancy`, *Yours* from `bookings` and `drafts`. They agree exactly when every
operation keeps the occupancy rows in step with the appointments and drafts that own them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEED = json.loads((ROOT.parent / "seed" / "clinic.json").read_text(encoding="utf-8"))
NOW = dt.datetime.fromisoformat(os.environ.get("WTA_FIXED_NOW") or "2026-06-10T09:00:00")  # wta-clock
TODAY = NOW.date()
DATA_DIR = Path(os.environ.get("WTA_DATA_DIR") or tempfile.mkdtemp(prefix="clinicflow-"))
SESSION_DIR = DATA_DIR / "sessions"
REGISTRY = DATA_DIR / "registry.sqlite3"
WRITE_LOCK = threading.Lock()
SID_SHAPE = re.compile(r"cf\d{6}")

SERVICES = {s["id"]: s for s in SEED["services"]}
PRACTITIONERS = {p["id"]: p for p in SEED["practitioners"]}
AREAS = {a["id"]: a["name"] for a in SEED["injury_areas"]}
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH_NAMES = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

DDL = """
CREATE TABLE patients (id INTEGER PRIMARY KEY, name TEXT NOT NULL, dob TEXT NOT NULL,
                       email TEXT NOT NULL, origin TEXT NOT NULL);
CREATE TABLE drafts (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, service TEXT NOT NULL,
                     patient_id INTEGER, practitioner TEXT, slot TEXT,
                     pay_mode TEXT, insurer TEXT, member TEXT, area TEXT, notes TEXT,
                     booking_ref TEXT, state TEXT NOT NULL);
CREATE TABLE bookings (ref TEXT PRIMARY KEY, draft_id INTEGER NOT NULL, patient_id INTEGER NOT NULL,
                       service TEXT NOT NULL, practitioner TEXT NOT NULL, slot TEXT NOT NULL,
                       pay_mode TEXT NOT NULL, insurer TEXT, member TEXT, area TEXT, notes TEXT,
                       state TEXT NOT NULL);
CREATE TABLE occupancy (practitioner TEXT NOT NULL, slot TEXT NOT NULL, holder TEXT NOT NULL,
                        PRIMARY KEY (practitioner, slot));
CREATE TABLE counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE flashes (key TEXT PRIMARY KEY, message TEXT NOT NULL);
"""


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


def day_label(day: dt.date) -> str:
    return f"{WEEKDAY_NAMES[day.weekday()]} {day.day} {MONTH_NAMES[day.month]}"


def slot_label(key: str | None) -> str:
    if key not in SLOTS:
        return ""
    day, time = SLOTS[key]
    return f"{day_label(day)}, {time}"


# -- sessions ----------------------------------------------------------------------------------


def _registry() -> sqlite3.Connection:
    conn = sqlite3.connect(REGISTRY, timeout=10)
    conn.execute("CREATE TABLE IF NOT EXISTS issued (n INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT)")
    return conn


def session_path(sid: str) -> Path:
    return SESSION_DIR / f"{sid}.sqlite3"


def known_session(sid: str) -> bool:
    return bool(SID_SHAPE.fullmatch(sid or "")) and session_path(sid).is_file()


def new_session() -> str:
    """Issue a session id and create its database from the seed."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    registry = _registry()
    try:
        number = registry.execute("INSERT INTO issued (sid) VALUES ('')").lastrowid
        sid = f"cf{number:06d}"
        registry.execute("UPDATE issued SET sid = ? WHERE n = ?", (sid, number))
        registry.commit()
    finally:
        registry.close()
    conn = sqlite3.connect(session_path(sid))
    try:
        conn.executescript(DDL)
        for person in SEED["patients"]:
            conn.execute("INSERT INTO patients (name, dob, email, origin) VALUES (?, ?, ?, 'record')",
                         (person["name"], person["dob"], person["email"]))
        for practitioner in SEED["practitioners"]:
            for day_index, time in practitioner["booked_by_others"]:
                if day_index < len(DAYS):
                    conn.execute("INSERT INTO occupancy VALUES (?, ?, 'other')",
                                 (practitioner["id"], slot_key(DAYS[day_index], time)))
        conn.commit()
    finally:
        conn.close()
    return sid


def open_session(sid: str) -> sqlite3.Connection:
    conn = sqlite3.connect(session_path(sid), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def bump(conn, name: str) -> int:
    row = conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
    value = (row["value"] if row else 0) + 1
    conn.execute("INSERT OR REPLACE INTO counters VALUES (?, ?)", (name, value))
    return value


# -- one-shot messages (not part of the committed state) ---------------------------------------


def say(conn, key: str, message: str) -> None:
    conn.execute("INSERT OR REPLACE INTO flashes VALUES (?, ?)", (key, message))


def hear(conn, key: str) -> str:
    row = conn.execute("SELECT message FROM flashes WHERE key = ?", (key,)).fetchone()
    if row is None:
        return ""
    conn.execute("DELETE FROM flashes WHERE key = ?", (key,))
    return row["message"]


# -- reads -------------------------------------------------------------------------------------


def draft(conn, draft_id: int):
    return conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()


def open_draft(conn):
    return conn.execute("SELECT * FROM drafts WHERE state = 'open' ORDER BY id DESC LIMIT 1").fetchone()


def patient(conn, patient_id):
    return conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()


def patient_by_email(conn, email: str):
    wanted = email.strip().casefold()
    for row in conn.execute("SELECT * FROM patients ORDER BY id"):
        if row["email"].strip().casefold() == wanted:
            return row
    return None


def booking(conn, ref: str):
    return conn.execute("SELECT * FROM bookings WHERE ref = ?", (ref,)).fetchone()


def bookings(conn, state: str) -> list:
    return conn.execute("SELECT * FROM bookings WHERE state = ? ORDER BY slot, ref", (state,)).fetchall()


def booking_place(conn, ref: str) -> tuple[str, str]:
    row = booking(conn, ref)
    return row["practitioner"], row["slot"]


def holder_at(conn, practitioner: str, slot: str) -> str | None:
    row = conn.execute("SELECT holder FROM occupancy WHERE practitioner = ? AND slot = ?",
                       (practitioner, slot)).fetchone()
    return row["holder"] if row else None


def occupancy_map(conn, practitioner: str) -> dict[str, str]:
    return {row["slot"]: row["holder"] for row in
            conn.execute("SELECT slot, holder FROM occupancy WHERE practitioner = ?", (practitioner,))}


def tally(conn, practitioner: str) -> dict[str, int]:
    """The slot table's figures for one practitioner (see the module docstring)."""
    taken = conn.execute("SELECT COUNT(*) FROM occupancy WHERE practitioner = ?",
                         (practitioner,)).fetchone()[0]
    others = conn.execute("SELECT COUNT(*) FROM occupancy WHERE practitioner = ? AND holder = 'other'",
                          (practitioner,)).fetchone()[0]
    booked = conn.execute("SELECT COUNT(*) FROM bookings WHERE practitioner = ? AND state = 'active'",
                          (practitioner,)).fetchone()[0]
    holding = conn.execute("SELECT COUNT(*) FROM drafts WHERE practitioner = ? AND state = 'open' "
                           "AND slot IS NOT NULL", (practitioner,)).fetchone()[0]
    return {"taken": taken, "others": others, "yours": booked + holding}


def committed(conn) -> dict:
    def rows(sql):
        return [dict(r) for r in conn.execute(sql)]
    return {
        "patients": rows("SELECT id, name, dob, email, origin FROM patients ORDER BY id"),
        "drafts": rows("SELECT id, kind, service, patient_id, practitioner, slot, pay_mode, insurer, member, "
                       "area, notes, booking_ref, state FROM drafts ORDER BY id"),
        "bookings": rows("SELECT ref, draft_id, patient_id, service, practitioner, slot, pay_mode, insurer, "
                         "member, area, notes, state FROM bookings ORDER BY ref"),
        "occupancy": rows("SELECT practitioner, slot, holder FROM occupancy ORDER BY practitioner, slot"),
    }


def digest(state: dict) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# -- writes ------------------------------------------------------------------------------------


def _release_draft_hold(conn, draft_id: int) -> None:
    conn.execute("DELETE FROM occupancy WHERE holder = ?", (f"draft:{draft_id}",))


def start_draft(conn, kind: str, service: str, **fields) -> int:
    """Open a new booking draft. Only one draft is open at a time: older ones are closed
    and whatever time they held goes back on the schedule."""
    for row in conn.execute("SELECT id FROM drafts WHERE state = 'open'").fetchall():
        _release_draft_hold(conn, row["id"])
        conn.execute("UPDATE drafts SET state = 'replaced' WHERE id = ?", (row["id"],))
    columns = ["kind", "service", "state"] + list(fields)
    values = [kind, service, "open"] + list(fields.values())
    marks = ", ".join("?" for _ in columns)
    return conn.execute(f"INSERT INTO drafts ({', '.join(columns)}) VALUES ({marks})", values).lastrowid


def update_draft(conn, draft_id: int, **fields) -> None:
    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(f"UPDATE drafts SET {assignments} WHERE id = ?", (*fields.values(), draft_id))


def register_patient(conn, name: str, dob: str, email: str) -> int:
    return conn.execute("INSERT INTO patients (name, dob, email, origin) VALUES (?, ?, ?, 'registered')",
                        (name, dob, email)).lastrowid


def hold_slot(conn, row, practitioner: str, slot: str) -> str:
    """Hold `slot` for the draft `row`. Returns '' on success or the vocab key of the refusal."""
    if slot not in SLOTS or practitioner not in PRACTITIONERS:
        return "msg.slot_unknown"
    holder = holder_at(conn, practitioner, slot)
    if holder == f"draft:{row['id']}":
        return "msg.slot_yours"
    if holder is not None:
        return "msg.slot_taken"
    _release_draft_hold(conn, row["id"])  # a draft holds one time at most
    conn.execute("INSERT INTO occupancy VALUES (?, ?, ?)", (practitioner, slot, f"draft:{row['id']}"))
    update_draft(conn, row["id"], practitioner=practitioner, slot=slot)
    return ""


def create_booking(conn, row) -> str:
    """Turn an open new-appointment draft into an appointment; its hold becomes the booking's."""
    ref = f"CF-{1000 + bump(conn, 'booking')}"
    conn.execute("INSERT INTO bookings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')",
                 (ref, row["id"], row["patient_id"], row["service"], row["practitioner"], row["slot"],
                  row["pay_mode"], row["insurer"], row["member"], row["area"], row["notes"]))
    conn.execute("UPDATE occupancy SET holder = ? WHERE holder = ?", (f"booking:{ref}", f"draft:{row['id']}"))
    update_draft(conn, row["id"], state="booked", booking_ref=ref)
    return ref


def move_booking(conn, row) -> str:
    """Complete a reschedule draft: the appointment takes the held time and gives back its old one."""
    ref = row["booking_ref"]
    leaving = booking_place(conn, ref)  # where the visit is now: read it before the booking moves
    conn.execute("UPDATE bookings SET practitioner = ?, slot = ? WHERE ref = ?",
                 (row["practitioner"], row["slot"], ref))
    conn.execute("DELETE FROM occupancy WHERE practitioner = ? AND slot = ? AND holder = ?",
                 (leaving[0], leaving[1], f"booking:{ref}"))
    conn.execute("UPDATE occupancy SET holder = ? WHERE holder = ?", (f"booking:{ref}", f"draft:{row['id']}"))
    update_draft(conn, row["id"], state="done")
    return ref


def cancel_booking(conn, ref: str) -> None:
    """Cancel an appointment, free its time, and close a reschedule still open for it."""
    conn.execute("UPDATE bookings SET state = 'cancelled' WHERE ref = ?", (ref,))
    conn.execute("DELETE FROM occupancy WHERE holder = ?", (f"booking:{ref}",))
    for pending in conn.execute("SELECT id FROM drafts WHERE state = 'open' AND booking_ref = ?",
                                (ref,)).fetchall():
        _release_draft_hold(conn, pending["id"])
        conn.execute("UPDATE drafts SET state = 'replaced' WHERE id = ?", (pending["id"],))
