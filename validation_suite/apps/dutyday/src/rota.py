"""The rota's rules: the season's calendar, shift statuses and the weekly hours limit.

Shifts run Monday to Saturday in three slots with two places each; Sundays are closed and
days before today are read-only. A shift's status (Open, Needs n, Full) is derived from its
assignments and stored on the shift row, so the month grid reads open places without
counting assignments; every change to a shift's assignments refreshes it in the same
transaction. Nobody may be rostered for more than the weekly limit in a Monday-Sunday week,
or for more than one shift a day.
"""

from __future__ import annotations

import calendar
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = json.loads((Path(__file__).resolve().parent.parent / "seed" / "rota.json").read_text(encoding="utf-8"))
TODAY = datetime.fromisoformat(os.environ.get("WTA_FIXED_NOW") or "2026-06-10T09:00:00").date()  # wta-clock
FIRST_DAY = date.fromisoformat(SEED["season"]["first"])
LAST_DAY = date.fromisoformat(SEED["season"]["last"])
WEEKLY_LIMIT = float(SEED["weekly_limit_hours"])
PLACES = int(SEED["places_per_shift"])
SLOTS = {slot["id"]: slot for slot in SEED["slots"]}
SLOT_ORDER = list(SLOTS)
VOLUNTEERS = {volunteer["id"]: volunteer for volunteer in SEED["volunteers"]}
SUNDAY = 6
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_ISO_MONTH = re.compile(r"(\d{4})-(\d{2})")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY, next_assignment INTEGER NOT NULL, notice TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shifts (sid TEXT NOT NULL, id TEXT NOT NULL, day TEXT NOT NULL, slot TEXT NOT NULL,
    status TEXT NOT NULL, needed INTEGER NOT NULL, PRIMARY KEY (sid, id));
CREATE TABLE IF NOT EXISTS assignments (sid TEXT NOT NULL, id TEXT NOT NULL, shift_id TEXT NOT NULL,
    volunteer TEXT NOT NULL, PRIMARY KEY (sid, id));
CREATE INDEX IF NOT EXISTS assignments_by_shift ON assignments (sid, shift_id);
"""


class Refusal(Exception):
    """A change the rota's rules do not allow: `key` names the message, `params` fill it in."""

    def __init__(self, key: str, **params) -> None:
        super().__init__(key)
        self.key, self.params = key, params


# -- the calendar ----------------------------------------------------------------------------


def parse_day(text: str) -> date | None:
    if not _ISO_DAY.fullmatch(text or ""):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_month(text: str) -> date | None:
    match = _ISO_MONTH.fullmatch(text or "")
    if not match or int(match.group(1)) < 1 or not 1 <= int(match.group(2)) <= 12:
        return None
    return date(int(match.group(1)), int(match.group(2)), 1)


def month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def season_month(month: date | None) -> date:
    """The first of `month` (today's month when none is given), kept inside the season."""
    month = month or TODAY.replace(day=1)
    return min(max(month, FIRST_DAY.replace(day=1)), LAST_DAY.replace(day=1))


def season_week(day: date | None) -> date:
    """The Monday of the week holding `day` (this week when none is given), kept inside the season."""
    monday = monday_of(day or TODAY)
    return min(max(monday, monday_of(FIRST_DAY)), monday_of(LAST_DAY))


def adjacent_month(month: date, step: int) -> date | None:
    """The month before (`step` -1) or after (+1) `month`, or None outside the season."""
    other = (month - timedelta(days=1)).replace(day=1) if step < 0 else month_end(month) + timedelta(days=1)
    return other if season_month(other) == other else None


def adjacent_week(monday: date, step: int) -> date | None:
    other = monday + timedelta(days=7 * step)
    return other if season_week(other) == other else None


def in_season(day: date) -> bool:
    return FIRST_DAY <= day <= LAST_DAY


def has_shifts(day: date) -> bool:
    return in_season(day) and day.weekday() != SUNDAY


def is_past(day: date) -> bool:
    return day < TODAY


def week_window(day: date) -> tuple[date, date]:
    """The first and last day whose hours count towards the weekly limit for a shift on `day`."""
    monday = monday_of(day)
    return monday, monday + timedelta(days=6)  # the ISO week, whatever the month


def days_until(shift_day: date) -> int:
    """Whole days from today until `shift_day`."""
    return (shift_day - TODAY).days


def shift_key(day: date, slot: str) -> str:
    return f"{day.isoformat()}-{slot}"


def hours_text(hours: float) -> str:
    return f"{hours:g}"


def slot_hours(slot: str) -> float:
    return float(SLOTS[slot]["hours"])


# -- sessions ----------------------------------------------------------------------------------


def install(conn) -> None:
    conn.executescript(SCHEMA)


def next_counter(conn, key: str) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    value = (row["value"] if row else 0) + 1
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
    return value


def session_exists(conn, sid: str) -> bool:
    return conn.execute("SELECT 1 FROM sessions WHERE sid = ?", (sid,)).fetchone() is not None


def start_session(conn, sid: str) -> None:
    """Give a new session the season's shifts and the seeded assignments."""
    shifts, day = [], FIRST_DAY
    while day <= LAST_DAY:
        if has_shifts(day):
            shifts += [(sid, shift_key(day, slot), day.isoformat(), slot, "open", PLACES) for slot in SLOT_ORDER]
        day += timedelta(days=1)
    conn.executemany("INSERT INTO shifts VALUES (?, ?, ?, ?, ?, ?)", shifts)
    seeded = SEED["assignments"]
    conn.executemany("INSERT INTO assignments VALUES (?, ?, ?, ?)",
                     [(sid, a["id"], shift_key(date.fromisoformat(a["day"]), a["slot"]), a["volunteer"])
                      for a in seeded])
    for key in sorted({shift_key(date.fromisoformat(a["day"]), a["slot"]) for a in seeded}):
        refresh_status(conn, sid, key)
    following = max(int(a["id"][1:]) for a in seeded) + 1
    conn.execute("INSERT INTO sessions VALUES (?, ?, '')", (sid, following))


def notify(conn, sid: str, text: str) -> None:
    """Leave a one-time notice for the next page this session sees."""
    conn.execute("UPDATE sessions SET notice = ? WHERE sid = ?", (text, sid))


def take_notice(conn, sid: str) -> str:
    row = conn.execute("SELECT notice FROM sessions WHERE sid = ?", (sid,)).fetchone()
    if row and row["notice"]:
        conn.execute("UPDATE sessions SET notice = '' WHERE sid = ?", (sid,))
        return row["notice"]
    return ""


# -- shifts and assignments -----------------------------------------------------------------------


def refresh_status(conn, sid: str, shift_id: str) -> None:
    """Recompute a shift's stored status and open places from its assignments."""
    taken = conn.execute("SELECT COUNT(*) AS n FROM assignments WHERE sid = ? AND shift_id = ?",
                         (sid, shift_id)).fetchone()["n"]
    needed = max(PLACES - taken, 0)
    status = "full" if needed == 0 else "open" if taken == 0 else "needs"
    conn.execute("UPDATE shifts SET status = ?, needed = ? WHERE sid = ? AND id = ?", (status, needed, sid, shift_id))


def shifts_between(conn, sid: str, first: date, last: date) -> dict[date, list[dict]]:
    """Every shift from `first` to `last`, by day in slot order, each with its roster."""
    days: dict[date, dict[str, dict]] = {}
    for row in conn.execute("SELECT id, day, slot, status, needed FROM shifts WHERE sid = ? AND day BETWEEN ? AND ?",
                            (sid, first.isoformat(), last.isoformat())):
        days.setdefault(date.fromisoformat(row["day"]), {})[row["slot"]] = {
            "id": row["id"], "slot": row["slot"], "status": row["status"], "needed": row["needed"], "roster": []}
    for row in conn.execute(
            "SELECT a.id, a.volunteer, s.day, s.slot FROM assignments a "
            "JOIN shifts s ON s.sid = a.sid AND s.id = a.shift_id "
            "WHERE a.sid = ? AND s.day BETWEEN ? AND ? ORDER BY CAST(SUBSTR(a.id, 2) AS INTEGER)",
            (sid, first.isoformat(), last.isoformat())):
        days[date.fromisoformat(row["day"])][row["slot"]]["roster"].append(
            {"id": row["id"], "volunteer": row["volunteer"]})
    return {day: [slots[slot] for slot in SLOT_ORDER] for day, slots in sorted(days.items())}


def open_places(conn, sid: str, month: date) -> dict[date, int]:
    """Open places per day of `month`, as the shifts' stored statuses record them."""
    return {day: sum(shift["needed"] for shift in shifts)
            for day, shifts in shifts_between(conn, sid, month, month_end(month)).items()}


def _assignment_rows(conn, sid: str, where: str, params: tuple) -> list[dict]:
    rows = conn.execute(
        "SELECT a.id, a.shift_id, a.volunteer, s.day, s.slot FROM assignments a "
        f"JOIN shifts s ON s.sid = a.sid AND s.id = a.shift_id WHERE a.sid = ? AND {where}", (sid, *params))
    found = [{**dict(row), "day": date.fromisoformat(row["day"])} for row in rows]
    return sorted(found, key=lambda a: (a["day"], SLOT_ORDER.index(a["slot"])))


def assignment(conn, sid: str, assignment_id: str) -> dict | None:
    found = _assignment_rows(conn, sid, "a.id = ?", (assignment_id,))
    return found[0] if found else None


def volunteer_shifts(conn, sid: str, volunteer: str, first: date, last: date) -> list[dict]:
    return _assignment_rows(conn, sid, "a.volunteer = ? AND s.day BETWEEN ? AND ?",
                            (volunteer, first.isoformat(), last.isoformat()))


def next_shift(conn, sid: str, volunteer: str) -> dict | None:
    """The volunteer's first shift today or later, or None."""
    upcoming = volunteer_shifts(conn, sid, volunteer, TODAY, LAST_DAY)
    return upcoming[0] if upcoming else None


def hours_of(shifts: list[dict]) -> float:
    return sum(slot_hours(shift["slot"]) for shift in shifts)


def check_assignment(conn, sid: str, day: date, slot: str, volunteer: str) -> None:
    """Raise `Refusal` unless `volunteer` may take the `slot` shift on `day`."""
    if is_past(day):
        raise Refusal("msg.day_read_only")
    if volunteer not in VOLUNTEERS:
        raise Refusal("msg.choose_volunteer")
    shift = conn.execute("SELECT needed FROM shifts WHERE sid = ? AND id = ?", (sid, shift_key(day, slot))).fetchone()
    if shift["needed"] == 0:
        raise Refusal("msg.shift_full")
    name = VOLUNTEERS[volunteer]["name"]
    same_day = volunteer_shifts(conn, sid, volunteer, day, day)
    if same_day:
        raise Refusal("msg.already_on_shift" if same_day[0]["slot"] == slot else "msg.one_a_day", name=name)
    first, last = week_window(day)
    total = hours_of(volunteer_shifts(conn, sid, volunteer, first, last)) + slot_hours(slot)
    if total > WEEKLY_LIMIT:
        raise Refusal("msg.over_limit", name=name, hours=hours_text(total), limit=hours_text(WEEKLY_LIMIT),
                      week=monday_of(day))


def add_assignment(conn, sid: str, shift_id: str, volunteer: str) -> str:
    number = conn.execute("SELECT next_assignment FROM sessions WHERE sid = ?", (sid,)).fetchone()["next_assignment"]
    conn.execute("UPDATE sessions SET next_assignment = ? WHERE sid = ?", (number + 1, sid))
    assignment_id = f"a{number}"
    conn.execute("INSERT INTO assignments VALUES (?, ?, ?, ?)", (sid, assignment_id, shift_id, volunteer))
    return assignment_id


def delete_assignment(conn, sid: str, assignment_id: str) -> None:
    conn.execute("DELETE FROM assignments WHERE sid = ? AND id = ?", (sid, assignment_id))


def committed(conn, sid: str) -> dict:
    """The session's business state: who is on which shift, and every shift's stored status."""
    return {
        "assignments": [dict(row) for row in conn.execute(
            "SELECT id, shift_id, volunteer FROM assignments WHERE sid = ? ORDER BY CAST(SUBSTR(id, 2) AS INTEGER)",
            (sid,))],
        "shifts": {row["id"]: f"{row['status']}/{row['needed']}" for row in conn.execute(
            "SELECT id, status, needed FROM shifts WHERE sid = ? ORDER BY id", (sid,))},
    }
