"""Results, and the rules that move them between states. No HTTP and no HTML here.

A fixture's life::

    scheduled --secretary enters a score--> provisional
    provisional --one club confirms--> provisional (that club's flag set)
    provisional --the second club confirms--> confirmed
    provisional --a club that has not confirmed disputes it--> disputed
    provisional | confirmed | disputed --secretary saves a different score--> provisional
                                          (both confirmations and any dispute cleared)

A result is Confirmed exactly while both clubs' flags are set, and only Confirmed results
are public. Every operation either writes a complete change or raises `Refused` having
written nothing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

SCHEDULED, PROVISIONAL, CONFIRMED, DISPUTED = "scheduled", "provisional", "confirmed", "disputed"
STATUSES = (SCHEDULED, PROVISIONAL, CONFIRMED, DISPUTED)
#: What the score selects offer, and therefore every score the league accepts.
GOAL_CHOICES = tuple(range(10))
REASON_LIMIT = 300


class Refused(Exception):
    """An action the league declines; `key` names the message shown to the user."""

    def __init__(self, key: str, **values: object) -> None:
        super().__init__(key)
        self.key = key
        self.values = values


@dataclass(frozen=True)
class Result:
    id: str
    number: int
    played_on: str
    home: str
    away: str
    home_goals: int | None
    away_goals: int | None
    status: str
    home_ok: bool
    away_ok: bool
    disputed_by: str | None
    dispute_reason: str | None

    @property
    def has_score(self) -> bool:
        return self.home_goals is not None and self.away_goals is not None

    @property
    def confirmations(self) -> int:
        return int(self.home_ok) + int(self.away_ok)

    def involves(self, club: str) -> bool:
        return club in (self.home, self.away)

    def opponent_of(self, club: str) -> str:
        return self.away if club == self.home else self.home

    def confirmed_by(self, club: str) -> bool:
        return self.home_ok if club == self.home else self.away_ok if club == self.away else False

    def awaits(self, club: str) -> bool:
        """Whether this result is waiting for `club` to confirm it."""
        return self.status == PROVISIONAL and self.involves(club) and not self.confirmed_by(club)


_COLUMNS = ("id, number, played_on, home, away, home_goals, away_goals, status, home_ok, away_ok,"
            " disputed_by, dispute_reason")


def _result(row: sqlite3.Row) -> Result:
    return Result(row["id"], row["number"], row["played_on"], row["home"], row["away"],
                  row["home_goals"], row["away_goals"], row["status"], bool(row["home_ok"]),
                  bool(row["away_ok"]), row["disputed_by"], row["dispute_reason"])


def fixtures(conn: sqlite3.Connection, sid: str) -> list[Result]:
    rows = conn.execute(f"SELECT {_COLUMNS} FROM results WHERE sid = ? ORDER BY number", (sid,))
    return [_result(row) for row in rows]


def fixture(conn: sqlite3.Connection, sid: str, fixture_id: str) -> Result | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM results WHERE sid = ? AND id = ?",
                       (sid, fixture_id)).fetchone()
    return _result(row) if row else None


def club_fixtures(conn: sqlite3.Connection, sid: str, club: str) -> list[Result]:
    return [r for r in fixtures(conn, sid) if r.involves(club)]


def _goals(raw: str) -> int:
    text = (raw or "").strip()
    if not text.isdigit() or int(text) not in GOAL_CHOICES:
        raise Refused("refuse.goals")
    return int(text)


def save_score(conn: sqlite3.Connection, sid: str, fixture_id: str, home_raw: str, away_raw: str,
               today: str) -> str:
    """The secretary's Save: enter a first score, or correct one. Returns the message key."""
    result = fixture(conn, sid, fixture_id)
    if result is None:
        raise Refused("refuse.no_fixture")
    home_goals, away_goals = _goals(home_raw), _goals(away_raw)
    if result.played_on > today:
        raise Refused("refuse.not_played", date=result.played_on)
    if not result.has_score:
        conn.execute("UPDATE results SET status = 'provisional', home_goals = ?, away_goals = ?"
                     " WHERE sid = ? AND id = ?", (home_goals, away_goals, sid, fixture_id))
        return "msg.entered"
    if (home_goals, away_goals) == (result.home_goals, result.away_goals):
        raise Refused("refuse.unchanged")
    # A different score is a new claim: both clubs must confirm it again.
    conn.execute(
        "UPDATE results SET home_goals = ?, away_goals = ?, status = 'provisional', home_ok = 0, away_ok = 0,"
        " disputed_by = NULL, dispute_reason = NULL WHERE sid = ? AND id = ?",
        (home_goals, away_goals, sid, fixture_id))
    return "msg.corrected"


def _club_result(conn: sqlite3.Connection, sid: str, fixture_id: str, club: str) -> Result:
    result = fixture(conn, sid, fixture_id)
    if result is None or not result.involves(club):
        raise Refused("refuse.not_yours")
    if not result.has_score:
        raise Refused("refuse.no_score")
    return result


def confirm_refusal(result: Result, club: str) -> str:
    """Why `club` cannot confirm `result` now, as a message key; empty when it can."""
    if result.status == CONFIRMED:
        return "refuse.already_confirmed"
    if result.status == DISPUTED:
        return "refuse.under_dispute"
    if result.confirmed_by(club):
        return "refuse.you_confirmed"
    return ""


def confirm(conn: sqlite3.Connection, sid: str, fixture_id: str, club: str) -> str:
    result = _club_result(conn, sid, fixture_id, club)
    refusal = confirm_refusal(result, club)
    if refusal:
        raise Refused(refusal, fixture=result.id)
    flag = "home_ok" if club == result.home else "away_ok"
    conn.execute(f"UPDATE results SET {flag} = 1 WHERE sid = ? AND id = ?", (sid, fixture_id))
    both = conn.execute("UPDATE results SET status = 'confirmed' WHERE sid = ? AND id = ?"
                        " AND home_ok = 1 AND away_ok = 1", (sid, fixture_id)).rowcount
    return "msg.confirmed_both" if both else "msg.confirmed_one"


def dispute_refusal(result: Result, club: str) -> str:
    """Why `club` cannot dispute `result` now, as a message key; empty when it can."""
    if not result.has_score:
        return "refuse.no_score"
    if result.status == CONFIRMED:
        return "refuse.already_confirmed"
    if result.status == DISPUTED:
        return "refuse.already_disputed"
    if result.confirmed_by(club):
        return "refuse.you_confirmed"
    return ""


def dispute(conn: sqlite3.Connection, sid: str, fixture_id: str, club: str, reason: str) -> str:
    result = _club_result(conn, sid, fixture_id, club)
    refusal = dispute_refusal(result, club)
    if refusal:
        raise Refused(refusal, fixture=result.id)
    reason = (reason or "").strip()
    if not reason:
        raise Refused("refuse.reason_missing")
    if len(reason) > REASON_LIMIT:
        raise Refused("refuse.reason_long", limit=REASON_LIMIT)
    conn.execute("UPDATE results SET status = 'disputed', disputed_by = ?, dispute_reason = ?"
                 " WHERE sid = ? AND id = ?", (club, reason, sid, fixture_id))
    return "msg.disputed"
