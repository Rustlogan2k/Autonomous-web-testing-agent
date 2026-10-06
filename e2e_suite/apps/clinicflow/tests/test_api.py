"""Clinicflow driven through its HTML forms, against the clean build at E2E_BASE_URL."""

import http.cookiejar
import json
import os
import re
import unittest
import urllib.parse
import urllib.request
from html import unescape

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8080/").rstrip("/")


class Patient:
    """One browser: a cookie jar, the last page and its URL."""

    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.url, self.html = "", ""

    def _open(self, request):
        with self.opener.open(request, timeout=10) as response:
            self.url = urllib.parse.urlsplit(response.geturl()).path
            query = urllib.parse.urlsplit(response.geturl()).query
            self.url += f"?{query}" if query else ""
            self.html = unescape(response.read().decode("utf-8"))
            return self.html

    def get(self, path):
        return self._open(BASE + path)

    def post(self, path, **fields):
        return self._open(urllib.request.Request(BASE + path, data=urllib.parse.urlencode(fields).encode()))

    def state(self):
        return json.loads(self.get("/__wta/state"))

    def region(self, ident):
        match = re.search(rf'id="{ident}"[^>]*>(.*?)</', self.html, re.S)
        return " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).split()) if match else None

    @property
    def draft(self):
        return int(re.search(r"/book/(\d+)/", self.url).group(1))

    # -- the usual journeys --------------------------------------------------------------------

    def begin(self, service, branch):
        self.post("/book", service=service)
        self.post(f"/book/{self.draft}/who", branch=branch)
        return self.draft

    def register(self, service="gp", **payment):
        draft = self.begin(service, "new")
        fields = {"full_name": "Test User Input", "dob": "1990-01-01", "email": "tester@example.com",
                  "payment": "self", "payment_mode": "self"}
        fields.update(payment)
        self.post(f"/book/{draft}/details", **fields)
        return draft

    def returning(self, service="gp", **payment):
        draft = self.begin(service, "returning")
        fields = {"lookup_email": "tester@example.com", "payment": "self", "payment_mode": "self"}
        fields.update(payment)
        self.post(f"/book/{draft}/lookup", **fields)
        return draft

    def hold(self, draft, slot, practitioner=""):
        return self.post(f"/book/{draft}/hold", slot=slot, **({"with": practitioner} if practitioner else {}))

    def tally(self):
        return tuple(int(self.region(name)) for name in ("taken-count", "others-count", "yours-count"))

    def book(self, draft, **answers):
        return self.post(f"/book/{draft}/book", **answers)


class Clinicflow(unittest.TestCase):
    def setUp(self):
        self.p = Patient()

    def test_health_and_session_isolation(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "clinicflow"})
        other = Patient()
        seed = other.state()["sha"]
        self.p.returning()
        self.assertNotEqual(self.p.state()["sha"], seed)
        self.assertEqual(other.state()["sha"], seed)
        self.assertEqual(Patient().state()["sha"], seed)

    def test_branching_routes(self):
        draft = self.p.begin("gp", "new")
        self.assertTrue(self.p.url.endswith(f"/book/{draft}/details"))
        self.p.get(f"/book/{draft}/lookup")  # not on the new-patient route
        self.assertTrue(self.p.url.endswith("/details"))
        self.p.get(f"/book/{draft}/slots")  # cannot skip registration
        self.assertTrue(self.p.url.endswith("/details"))
        self.p.post(f"/book/{draft}/who", branch="returning")  # may still switch branch
        self.assertTrue(self.p.url.endswith(f"/book/{draft}/lookup"))
        self.p.post(f"/book/{draft}/lookup", lookup_email="tester@example.com", payment="self")
        self.assertIn(f"/book/{draft}/slots", self.p.url)
        self.p.get(f"/book/{draft}/who")  # the branch is fixed once a patient is attached
        self.assertIn("/slots", self.p.url)

    def test_registration_rules(self):
        draft = self.p.begin("nurse", "new")
        good = {"full_name": "Test User Input", "dob": "1990-01-01", "email": "tester@example.com", "payment": "self"}
        for change in ({"full_name": ""}, {"dob": "2026-06-15"}, {"dob": ""}, {"email": "not-an-email"},
                       {"payment": ""}):
            self.p.post(f"/book/{draft}/details", **{**good, **change})
            self.assertTrue(self.p.url.endswith("/details"), change)
            self.assertIsNotNone(self.p.region("page-error"), change)
        self.p.post(f"/book/{draft}/details", **{**good, "dob": "0001-01-01", "full_name": "A" * 2048})
        self.assertIn("/slots", self.p.url)

    def test_returning_lookup(self):
        draft = self.p.begin("nurse", "returning")
        self.p.post(f"/book/{draft}/lookup", lookup_email="nobody@example.com", payment="self")
        self.assertIn("/lookup", self.p.url)
        self.p.post(f"/book/{draft}/lookup", lookup_email="  TESTER@example.com ", payment="self")
        self.p.hold(draft, "0611-0900")
        self.p.get(f"/book/{draft}/confirm")
        self.assertEqual(self.p.region("confirm-patient"), "Alex Morgan")
        self.assertEqual(self.p.region("questions-status"), "Not needed")

    def test_slot_rules_and_figures(self):
        draft = self.p.returning("gp")
        self.p.get(f"/book/{draft}/slots")
        self.assertEqual(self.p.tally(), (6, 6, 0))
        before = self.p.state()["sha"]
        self.p.hold(draft, "0611-0900")  # booked by another patient
        self.assertIn("already booked", self.p.region("page-error"))
        self.assertEqual(self.p.state()["sha"], before)
        self.p.hold(draft, "0611-1100")
        self.assertEqual(self.p.tally(), (7, 6, 1))
        self.assertIn("Thu 11 Jun, 11:00", self.p.region("held-slot"))
        held = self.p.state()["sha"]
        self.p.hold(draft, "0611-1100")  # holding it again changes nothing
        self.assertEqual(self.p.state()["sha"], held)
        self.p.hold(draft, "0612-1400")  # a draft holds one time: the first goes back
        self.assertEqual(self.p.tally(), (7, 6, 1))
        self.p.get(f"/book/{draft}/slots?with=lindqvist")
        self.assertEqual(self.p.tally(), (4, 4, 0))
        self.p.hold(draft, "0611-0900", "lindqvist")
        self.p.get(f"/book/{draft}/slots?with=okafor")
        self.assertEqual(self.p.tally(), (6, 6, 0))

    def test_physiotherapy_needs_the_questions(self):
        draft = self.p.returning("physio")
        self.p.hold(draft, "0611-1100")
        self.assertIn(f"/book/{draft}/questions", self.p.html)  # the onward link
        self.p.get(f"/book/{draft}/confirm")
        self.assertTrue(self.p.url.endswith("/questions"))
        before = self.p.state()["sha"]
        self.p.book(draft, injury_area="", injury_notes="")  # "Book visit" on the questions page
        self.assertTrue(self.p.url.endswith("/questions"))
        self.p.book(draft)  # and from anywhere else
        self.assertEqual(self.p.state()["sha"], before)
        self.p.book(draft, injury_area="knee", injury_notes="Test User Input")
        self.assertTrue(self.p.url.endswith(f"/book/{draft}/booked"))
        self.assertIn("Knee: Test User Input", self.p.region("booked-answers"))

    def test_saved_answers_lead_to_the_review(self):
        draft = self.p.register("physio")
        self.p.hold(draft, "0611-1100")
        self.p.post(f"/book/{draft}/questions", injury_area="back", injury_notes="Lifting")
        self.assertTrue(self.p.url.endswith("/confirm"))
        self.assertEqual(self.p.region("questions-status"), "Answered")

    def test_payment_modes(self):
        cases = [({"payment": "self", "payment_mode": "self"}, "Self-pay"),
                 ({"payment": "insurer", "payment_mode": "insurer", "insurer_name": "Test User Input",
                   "insurer_member": "42"}, "Insurer: Test User Input (member 42)"),
                 ({"payment": "insurer", "payment_mode": "", "insurer_name": "Mutual",  # no script
                   "insurer_member": "7"}, "Insurer: Mutual (member 7)")]
        for payment, shown in cases:
            p = Patient()
            draft = p.register(**payment)
            p.hold(draft, "0611-1100")
            p.book(draft)
            self.assertEqual(p.region("paid-by"), shown)
        p = Patient()
        draft = p.begin("gp", "new")
        p.post(f"/book/{draft}/details", full_name="X", dob="1990-01-01", email="x@y.zz", payment="insurer",
               payment_mode="insurer", insurer_name="Mutual", insurer_member="abc")
        self.assertIn("/details", p.url)

    def test_one_time_banner(self):
        draft = self.p.returning()
        self.p.hold(draft, "0611-1100")
        self.p.book(draft)
        self.assertEqual(self.p.region("booked-banner"), "Appointment booked: CF-1001")
        self.p.get(f"/book/{draft}/booked")
        self.assertIsNone(self.p.region("booked-banner"))
        self.p.book(draft)  # pressed again: no second appointment
        self.assertEqual(len(self.p.state()["committed"]["bookings"]), 1)

    def test_reschedule_and_cancel_give_times_back(self):
        draft = self.p.returning()
        self.p.hold(draft, "0611-1100")
        self.p.book(draft)
        self.p.post("/bookings/CF-1001/reschedule")
        moving = self.p.draft
        self.assertEqual(self.p.tally(), (7, 6, 1))
        self.p.hold(moving, "0612-1400")
        self.assertEqual(self.p.tally(), (8, 6, 2))
        self.p.book(moving)
        self.assertTrue(self.p.url.endswith("/bookings/CF-1001"))
        self.assertIn("Fri 12 Jun, 14:00", self.p.region("manage-when"))
        self.p.post("/bookings/CF-1001/reschedule")
        self.assertEqual(self.p.tally(), (7, 6, 1))
        holders = [o for o in self.p.state()["committed"]["occupancy"] if o["holder"] != "other"]
        self.assertEqual(holders, [{"practitioner": "okafor", "slot": "0612-1400", "holder": "booking:CF-1001"}])
        before = self.p.state()["sha"]
        self.p.post("/bookings/CF-1001/cancel")  # the box was not ticked
        self.assertEqual(self.p.state()["sha"], before)
        self.p.post("/bookings/CF-1001/cancel", confirm="yes")
        self.assertIn("CF-1001", self.p.region("cancelled-list"))
        self.assertNotIn("CF-1001", self.p.region("upcoming-list"))
        self.assertEqual([o for o in self.p.state()["committed"]["occupancy"] if o["holder"] != "other"], [])

    def test_one_open_draft_at_a_time(self):
        first = self.p.returning()
        self.p.hold(first, "0611-1100")
        self.p.returning()
        self.p.get(f"/book/{first}/slots")
        self.assertIn("started again", self.p.html)
        drafts = self.p.state()["committed"]["drafts"]
        self.assertEqual([d["state"] for d in drafts], ["replaced", "open"])
        self.assertEqual([o for o in self.p.state()["committed"]["occupancy"] if o["holder"] != "other"], [])


if __name__ == "__main__":
    unittest.main()
