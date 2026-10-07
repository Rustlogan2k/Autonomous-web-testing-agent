"""The public league table, derived from confirmed results on every request.

Nothing about the table is stored: it is recomputed from the session's results each time
it is shown, so it can never drift from them. Only results both clubs have confirmed
count. Teams are ranked by points (3 for a win, 1 for a draw), then goal difference, then
goals scored, then name.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

WIN_POINTS, DRAW_POINTS = 3, 1


@dataclass
class Row:
    code: str
    name: str
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against

    @property
    def points(self) -> int:
        return self.won * WIN_POINTS + self.drawn * DRAW_POINTS

    def record(self, scored: int, conceded: int) -> None:
        self.played += 1
        self.goals_for += scored
        self.goals_against += conceded
        if scored > conceded:
            self.won += 1
        elif scored == conceded:
            self.drawn += 1
        else:
            self.lost += 1


def table(conn: sqlite3.Connection, sid: str, teams: dict[str, str]) -> list[Row]:
    """Every team, in league order. `teams` maps club code to name."""
    rows = {code: Row(code, name) for code, name in teams.items()}
    games = conn.execute(
        "SELECT r.home, r.away, r.home_goals, r.away_goals"
        " FROM results r WHERE r.status = 'confirmed' AND r.sid = ?", (sid,))
    for game in games:
        if game["home_goals"] is None or game["away_goals"] is None:
            continue
        rows[game["home"]].record(game["home_goals"], game["away_goals"])
        rows[game["away"]].record(game["away_goals"], game["home_goals"])
    ranked = list(rows.values())
    ranked.sort(key=lambda row: (-row.points, -row.goal_difference, -row.goals_for, row.name))
    return ranked


def results_counted(conn: sqlite3.Connection, sid: str) -> int:
    """How many results the public table is based on: those both clubs have confirmed."""
    return conn.execute("SELECT COUNT(*) FROM results WHERE sid = ? AND status = ?",
                        (sid, "confirmed")).fetchone()[0]


def position_of(ranked: list[Row], code: str) -> tuple[int, Row | None]:
    for position, row in enumerate(ranked, start=1):
        if row.code == code:
            return position, row
    return 0, None
