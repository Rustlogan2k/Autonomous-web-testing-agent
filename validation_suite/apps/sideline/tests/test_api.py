"""Sideline over HTTP, against the clean build (E2E_BASE_URL, test hooks on).

Each test opens its own browser-like session (cookie jar), so it starts from the seed.
The numbers pinned here are the plan's verified ones: the seed table, the tie-break that
puts Ashby Rovers above Cobham Town after F7 2-0, and the footnote/Played identity.
"""

import http.cookiejar
import json
import os
import re
import unittest
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8080/").rstrip("/")
SEED_TABLE = [("Cobham Town", 4, 8, 6, 8), ("Ashby Rovers", 3, 1, 0, 5), ("Eastleigh Albion", 2, 5, 5, 3),
              ("Dunmore", 4, 5, 6, 3), ("Brookfield", 2, 3, 3, 2), ("Fenwick", 3, 2, 4, 2)]


class Browser:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.url = ""

    def _open(self, request):
        try:
            with self.opener.open(request, timeout=10) as response:
                self.url = response.geturl()
                return response.status, unescape(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            self.url = error.geturl()
            return error.code, unescape(error.read().decode("utf-8"))

    def get(self, path):
        return self._open(urllib.request.Request(BASE + path))

    def post(self, path, fields=None):
        data = urllib.parse.urlencode(fields or {}).encode("utf-8")
        return self._open(urllib.request.Request(BASE + path, data=data))

    def state(self):
        return json.loads(self.get("/__wta/state")[1])

    def result(self, fixture):
        return next(r for r in self.state()["committed"]["results"] if r["id"] == fixture)

    @staticmethod
    def region(markup, ident):
        match = re.search(rf'<(\w+)[^>]*\bid="{re.escape(ident)}"[^>]*>(.*?)</\1>', markup, re.S)
        return " ".join(re.sub(r"<[^>]+>", " ", match.group(2)).split()) if match else None

    def table(self):
        _, page = self.get("/table")
        rows = []
        for position in range(1, 7):
            rows.append(tuple(int(self.region(page, f"{col}-{position}")) for col in ("pl", "gf", "ga", "pts")))
        names = re.findall(r'<a id="team-[a-z]+" href="/teams/[a-z]+">([^<]+)</a>', page)
        return names, rows, int(self.region(page, "results-counted"))


class Sideline(unittest.TestCase):
    def setUp(self):
        self.b = Browser()

    def enter(self, fixture, home, away):
        return self.b.post(f"/sec/results/{fixture}", {"home_goals": str(home), "away_goals": str(away)})

    def test_health_landing_and_doors(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "sideline"})
        status, page = self.b.get("/")
        self.assertEqual(status, 200)
        for ident in ("door-secretary", "door-public", "door-club-ash", "door-club-fen"):
            self.assertIn(f'id="{ident}"', page)
        status, _ = self.b.get("/teams/nowhere")
        self.assertEqual(status, 404)

    def test_seed_table_is_the_verified_one(self):
        names, rows, counted = self.b.table()
        self.assertEqual(counted, 9)
        self.assertEqual(names, [name for name, *_ in SEED_TABLE])
        self.assertEqual(rows, [tuple(numbers) for _, *numbers in SEED_TABLE])
        self.assertEqual(sum(row[0] for row in rows), 2 * counted)

    def test_level_on_points_is_split_by_goal_difference(self):
        self.enter("F7", 2, 0)
        self.b.post("/club/ash/confirm/F7")
        _, page = self.b.post("/club/bro/confirm/F7")
        self.assertIn("F7", self.b.region(page, "flash"))
        names, rows, counted = self.b.table()
        self.assertEqual(counted, 10)
        self.assertEqual(names[:2], ["Ashby Rovers", "Cobham Town"])  # 8 pts +3 above 8 pts +2
        self.assertEqual(rows[:2], [(4, 3, 0, 8), (4, 8, 6, 8)])

    def test_enter_then_both_clubs_confirm(self):
        _, page = self.enter("F12", 0, 0)
        self.assertTrue(self.b.url.endswith("/sec/results/F12"))
        self.assertEqual(self.b.region(page, "result-status"), "Provisional")
        self.assertEqual(self.b.region(page, "confirm-count"), "0")
        _, page = self.b.post("/club/fen/confirm/F12")
        self.assertTrue(self.b.url.endswith("/club/fen"))
        self.assertEqual(self.b.table()[2], 9)  # one club is not enough
        self.b.post("/club/cob/confirm/F12")
        names, rows, counted = self.b.table()
        self.assertEqual(counted, 10)
        self.assertEqual((names[0], rows[0][3]), ("Cobham Town", 9))
        _, page = self.b.get("/sec/results/F12")
        self.assertEqual(self.b.region(page, "result-status"), "Confirmed")
        self.assertEqual(self.b.region(page, "confirm-count"), "2")

    def test_one_sided_confirmation_stays_out_of_public(self):
        self.enter("F8", 0, 1)
        self.b.post("/club/eas/confirm/F8")
        names, rows, counted = self.b.table()
        self.assertEqual(counted, 9)
        self.assertEqual(sum(row[0] for row in rows), 18)
        _, page = self.b.get("/results")
        self.assertNotIn("0–1", page)

    def test_correction_returns_to_provisional_and_leaves_the_table(self):
        _, page = self.enter("F3", 1, 2)
        self.assertEqual(self.b.region(page, "result-status"), "Provisional")
        self.assertEqual(self.b.region(page, "confirm-count"), "0")
        self.assertEqual(self.b.region(page, "result-score"), "1–2")
        names, rows, counted = self.b.table()
        self.assertEqual(counted, 8)
        self.assertEqual(sum(row[0] for row in rows), 16)
        row = self.b.result("F3")
        self.assertEqual((row["status"], row["home_ok"], row["away_ok"]), ("provisional", 0, 0))

    def test_unchanged_save_changes_nothing(self):
        before = self.b.state()["sha"]
        _, page = self.enter("F3", 2, 2)
        self.assertIn("F3", self.b.region(page, "flash"))
        self.assertEqual(self.b.region(page, "result-status"), "Confirmed")
        self.assertEqual(self.b.state()["sha"], before)

    def test_confirm_refused_where_it_does_not_apply(self):
        before = self.b.state()["sha"]
        _, page = self.b.post("/club/cob/confirm/F10")  # already confirmed by both
        self.assertIn("F10", self.b.region(page, "flash"))
        self.b.post("/club/ash/confirm/F10")  # not Ashby's fixture
        self.b.post("/club/ash/confirm/F7")  # no score yet
        self.assertEqual(self.b.state()["sha"], before)
        self.enter("F7", 1, 1)
        self.b.post("/club/ash/confirm/F7")
        middle = self.b.state()["sha"]
        self.b.post("/club/ash/confirm/F7")  # twice
        self.assertEqual(self.b.state()["sha"], middle)
        self.assertEqual(self.b.result("F7")["status"], "provisional")

    def test_dispute_needs_a_reason_and_blocks_confirmation(self):
        self.enter("F8", 0, 1)
        _, page = self.b.get("/club/bro")
        self.assertEqual(self.b.region(page, "club-pending"), "1")
        before = self.b.state()["sha"]
        _, page = self.b.post("/club/bro/dispute/F8", {"dispute_reason": "   "})
        self.assertTrue(self.b.url.endswith("/club/bro/dispute/F8"))
        self.assertTrue(self.b.region(page, "flash"))
        self.b.post("/club/bro/dispute/F8", {"dispute_reason": "A" * 2048})
        self.assertEqual(self.b.state()["sha"], before)
        self.b.post("/club/bro/dispute/F8", {"dispute_reason": "Test User Input"})
        self.assertEqual(self.b.result("F8")["status"], "disputed")
        _, page = self.b.get("/sec/results/F8")
        self.assertEqual(self.b.region(page, "dispute-text"), "Test User Input")
        self.assertEqual(self.b.region(page, "confirm-count"), "0")
        disputed = self.b.state()["sha"]
        self.b.post("/club/eas/confirm/F8")
        self.assertEqual(self.b.state()["sha"], disputed)
        self.enter("F8", 1, 1)  # the secretary corrects it: back to both clubs
        row = self.b.result("F8")
        self.assertEqual((row["status"], row["disputed_by"]), ("provisional", None))

    def test_bad_input_is_refused_with_a_page_not_an_error(self):
        before = self.b.state()["sha"]
        for fields in ({"home_goals": "10", "away_goals": "0"}, {"home_goals": "", "away_goals": "1"},
                       {"home_goals": "-1", "away_goals": "0"}):
            status, page = self.b.post("/sec/results/F7", fields)
            self.assertEqual(status, 200)
            self.assertTrue(self.b.region(page, "flash"))
        status, _ = self.b.post("/sec/results/F99", {"home_goals": "1", "away_goals": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(self.b.state()["sha"], before)

    def test_sessions_are_isolated(self):
        other = Browser()
        self.enter("F7", 3, 3)
        self.assertEqual(other.result("F7")["status"], "scheduled")
        self.assertEqual(self.b.result("F7")["status"], "provisional")
        self.assertNotEqual(self.b.state()["session"], other.state()["session"])


if __name__ == "__main__":
    unittest.main()
