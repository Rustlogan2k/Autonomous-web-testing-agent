"""Dutyday's server, driven through its links and HTML forms against the clean build (E2E_BASE_URL).

Every change is a form post answered with a redirect (post/redirect/get), which urllib follows,
so each `post` returns the page the browser would land on.
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


class Browser:
    """One browser session (its own `wta_sid` cookie)."""

    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.url = ""

    def _open(self, request):
        try:
            with self.opener.open(request, timeout=10) as response:
                self.url = response.geturl()
                return response.status, unescape(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, unescape(error.read().decode("utf-8"))

    def get(self, path):
        return self._open(urllib.request.Request(BASE + path))

    def post(self, path, fields=None):
        data = urllib.parse.urlencode(fields or {}).encode()
        return self._open(urllib.request.Request(BASE + path, data=data))

    def assign(self, day, slot, volunteer):
        return self.post(f"/rota/day/{day}/assign", {"slot": slot, "volunteer": volunteer})

    def state(self):
        return json.loads(self.get("/__wta/state")[1])

    @staticmethod
    def region(markup, ident):
        match = re.search(rf'<(\w+)[^>]*\bid="{ident}"[^>]*>(.*?)</\1>', markup, re.S)
        return " ".join(re.sub(r"<[^>]+>", " ", match.group(2)).split()) if match else None


class DutydayRota(unittest.TestCase):
    def setUp(self):
        self.b = Browser()

    def test_health_and_entry(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "dutyday"})
        status, html = self.b.get("/")
        self.assertEqual(status, 200)
        self.assertTrue(self.b.url.endswith("/rota"))
        self.assertEqual(self.b.region(html, "month-title"), "June 2026")
        self.assertNotIn("<script", html)

    def test_month_grid_reads_open_places_and_pages_within_the_season(self):
        _, june = self.b.get("/rota")
        self.assertIn('id="day-2026-06-30"', june)
        self.assertNotIn('id="month-prev"', june)  # the season starts in June
        cell = re.search(r'id="day-2026-06-12"[^>]*>12</a><span class="note">([^<]*)<', june).group(1)
        self.assertEqual(cell, "4 open")  # afternoon full, morning and evening open
        _, july = self.b.get("/rota?month=2026-07")
        self.assertEqual(self.b.region(july, "month-title"), "July 2026")
        _, august = self.b.get("/rota?month=2026-08")
        self.assertNotIn('id="month-next"', august)
        for junk in ("2030-01", "abc", "0000-13", ""):
            status, html = self.b.get(f"/rota?month={junk}")
            self.assertEqual(status, 200, junk)
            self.assertIn('id="month-title"', html)

    def test_seeded_day_shows_counts_and_stored_status(self):
        _, html = self.b.get("/rota/day/2026-06-12")
        self.assertEqual(self.b.region(html, "count-afternoon"), "2")
        self.assertEqual(self.b.region(html, "status-afternoon"), "Full")
        self.assertEqual(self.b.region(html, "status-morning"), "Open")
        self.assertIn("Ana", self.b.region(html, "roster-afternoon"))
        _, sunday = self.b.get("/rota/day/2026-06-14")
        self.assertNotIn('id="status-afternoon"', sunday)
        _, past = self.b.get("/rota/day/2026-06-08")
        self.assertIn('id="status-morning"', past)
        self.assertNotIn('id="assign-morning"', past)  # read-only

    def test_assigning_fills_a_shift_and_full_shifts_refuse(self):
        _, html = self.b.assign("2026-06-15", "morning", "chidi")
        self.assertTrue(self.b.url.endswith("/rota/day/2026-06-15"))
        self.assertIn("Chidi", self.b.region(html, "roster-morning"))
        self.assertEqual(self.b.region(html, "count-morning"), "2")
        self.assertEqual(self.b.region(html, "status-morning"), "Full")
        before = self.b.state()["sha"]
        _, html = self.b.assign("2026-06-15", "morning", "ben")
        self.assertIn("full", self.b.region(html, "flash"))
        self.assertEqual(self.b.state()["sha"], before)

    def test_refusals_keep_the_rota_unchanged(self):
        before = self.b.state()["sha"]
        cases = [("2026-06-08", "evening", "ana", "passed"),         # read-only day
                 ("2026-06-16", "morning", "", "Choose"),            # nobody chosen
                 ("2026-06-16", "morning", "nobody", "Choose"),
                 ("2026-06-12", "morning", "ana", "already has"),    # one shift a day
                 ("2026-06-13", "morning", "priya", "already on")]
        for day, slot, volunteer, words in cases:
            status, html = self.b.assign(day, slot, volunteer)
            self.assertEqual(status, 200)
            self.assertIn(words, self.b.region(html, "flash") or "", (day, slot, volunteer))
        for day, slot in (("2026-06-14", "morning"), ("9999-12-31", "morning"), ("2026-06-16", "night")):
            status, _ = self.b.assign(day, slot, "ana")
            self.assertEqual(status, 200)
        self.assertEqual(self.b.state()["sha"], before)

    def test_weekly_limit_counts_the_whole_week_across_a_month_end(self):
        _, html = self.b.assign("2026-07-04", "morning", "priya")
        self.assertEqual(self.b.region(html, "flash"),
                         "Priya would have 13.5 of 12 hours in the week of Mon 29 Jun.")
        _, html = self.b.assign("2026-06-30", "afternoon", "priya")  # 10.5 h in July + 4 h in June
        self.assertIn("14.5 of 12", self.b.region(html, "flash"))
        self.assertNotIn("Priya", self.b.region(html, "roster-afternoon"))
        _, html = self.b.get("/volunteers/priya?week=2026-06-29")
        self.assertEqual(self.b.region(html, "week-hours"), "10.5 of 12")
        _, html = self.b.assign("2026-06-30", "evening", "priya")  # 10.5 + 2.5 = 13: still over
        self.assertIn("13 of 12", self.b.region(html, "flash"))

    def test_days_until_the_next_shift_crosses_month_ends(self):
        _, html = self.b.get("/volunteers/ben")
        self.assertIsNone(self.b.region(html, "next-in"))  # only past shifts
        _, html = self.b.assign("2026-07-01", "morning", "ben")
        self.assertIn("Ben", self.b.region(html, "roster-morning"))
        _, html = self.b.get("/volunteers/ben?week=2026-06-29")
        self.assertEqual(self.b.region(html, "next-in"), "21")
        self.assertIn("Wed 1 Jul", self.b.region(html, "next-when"))
        self.assertEqual(self.b.region(html, "week-hours"), "3 of 12")
        _, html = self.b.get("/volunteers/priya")
        self.assertEqual(self.b.region(html, "next-in"), "3")  # Sat 13 Jun

    def test_withdrawing_from_the_volunteer_page_reopens_the_shift(self):
        _, html = self.b.get("/volunteers/ana")
        self.assertIn('href="/assignments/a14/withdraw"', html)
        _, html = self.b.get("/assignments/a14/withdraw")
        self.assertIn('id="confirm-withdraw"', html)
        self.assertIn('href="/volunteers/ana?week=2026-06-08"', html)
        _, html = self.b.post("/assignments/a14/withdraw")
        self.assertTrue(self.b.url.endswith("/volunteers/ana?week=2026-06-08"))
        self.assertIn("withdrawn", self.b.region(html, "flash"))
        self.assertEqual(self.b.region(html, "week-hours"), "0 of 12")
        _, html = self.b.get("/rota/day/2026-06-12")
        self.assertEqual(self.b.region(html, "count-afternoon"), "1")
        self.assertEqual(self.b.region(html, "status-afternoon"), "Needs 1")
        self.assertEqual(self.b.state()["committed"]["shifts"]["2026-06-12-afternoon"], "needs/1")
        _, june = self.b.get("/rota")
        self.assertIn('id="day-2026-06-12" href="/rota/day/2026-06-12">12</a><span class="note">5 open', june)
        status, html = self.b.post("/assignments/a14/withdraw")  # again: already gone
        self.assertEqual(status, 200)
        self.assertIn("already been given up", self.b.region(html, "flash"))
        status, _ = self.b.get("/assignments/a14/withdraw")
        self.assertEqual(status, 200)

    def test_past_shifts_cannot_be_withdrawn(self):
        before = self.b.state()["sha"]
        _, html = self.b.post("/assignments/a9/withdraw")
        self.assertTrue(self.b.url.endswith("/volunteers/ben?week=2026-06-08"))
        self.assertIn("already happened", self.b.region(html, "flash"))
        self.assertEqual(self.b.state()["sha"], before)

    def test_removing_from_the_day_page_refreshes_the_status(self):
        _, html = self.b.post("/rota/day/2026-06-12/remove", {"assignment": "a15"})
        self.assertEqual(self.b.region(html, "count-afternoon"), "1")
        self.assertEqual(self.b.region(html, "status-afternoon"), "Needs 1")
        self.b.post("/rota/day/2026-06-12/remove", {"assignment": "a14"})
        _, html = self.b.get("/rota/day/2026-06-12")
        self.assertEqual(self.b.region(html, "status-afternoon"), "Open")
        before = self.b.state()["sha"]
        _, html = self.b.post("/rota/day/2026-06-08/remove", {"assignment": "a9"})
        self.assertIn("passed", self.b.region(html, "flash"))
        status, _ = self.b.post("/rota/day/2026-06-12/remove", {"assignment": "a99"})
        self.assertEqual(status, 200)
        self.assertEqual(self.b.state()["sha"], before)

    def test_new_assignments_get_counter_ids(self):
        self.b.assign("2026-06-16", "afternoon", "chidi")
        ids = [a["id"] for a in self.b.state()["committed"]["assignments"]]
        self.assertEqual(ids[-1], "a28")

    def test_jump_and_boundary_dates(self):
        self.b.get("/rota/jump?date=2026-06-15&month=2026-06")
        self.assertTrue(self.b.url.endswith("/rota/day/2026-06-15"))
        for value in ("", "0001-01-01", "9999-12-31", "not-a-date", "2026-02-30", "A" * 2048):
            status, html = self.b.get("/rota/jump?" + urllib.parse.urlencode({"date": value, "month": "2026-07"}))
            self.assertEqual(status, 200, value)
            self.assertIn("Choose a date", self.b.region(html, "flash"), value)
            self.assertEqual(self.b.region(html, "month-title"), "July 2026")

    def test_volunteer_weeks_page_within_the_season(self):
        _, html = self.b.get("/volunteers")
        self.assertIn('id="vol-priya"', html)
        _, html = self.b.get("/volunteers/dana?week=2026-06-01")
        self.assertNotIn('id="week-prev"', html)
        _, html = self.b.get("/volunteers/dana?week=9999-12-31")
        self.assertNotIn('id="week-next"', html)
        status, _ = self.b.get("/volunteers/nobody")
        self.assertEqual(status, 200)

    def test_decoys_change_nothing(self):
        before = self.b.state()["sha"]
        _, html = self.b.get("/rota/print?month=2026-06")
        self.assertIn("Ana, Dana", html)
        _, html = self.b.get("/rota?month=2026-07&feed=show")
        self.assertIn('id="feed-address"', html)
        self.assertEqual(self.b.state()["sha"], before)

    def test_sessions_are_isolated_and_start_from_the_seed(self):
        other = Browser()
        seed = other.state()["sha"]
        self.b.assign("2026-06-16", "afternoon", "chidi")
        self.assertEqual(other.state()["sha"], seed)
        self.assertNotEqual(self.b.state()["sha"], seed)
        self.assertEqual(Browser().state()["sha"], seed)


if __name__ == "__main__":
    unittest.main()
