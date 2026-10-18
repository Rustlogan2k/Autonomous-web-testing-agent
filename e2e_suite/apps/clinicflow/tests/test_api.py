"""Clinicflow driven through its HTML forms, against the clean build at E2E_BASE_URL."""

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
PASSWORD = "Harbourside-clinic-26"  # every demo account (README)


class Desk:
    """One browser: a cookie jar, the last page and its URL; signed in as `who` unless None."""

    def __init__(self, who="rhiannon.price"):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.url, self.html = "", ""
        if who:
            self.post("/login", username=who, password=PASSWORD)
            assert "/login" not in self.url, self.url

    def _open(self, request):
        with self.opener.open(request, timeout=10) as response:
            parts = urllib.parse.urlsplit(response.geturl())
            self.url = parts.path + (f"?{parts.query}" if parts.query else "")
            self.status = response.status
            self.html = unescape(response.read().decode("utf-8"))
            return self.html

    def get(self, path):
        return self._open(BASE + path)

    def post(self, path, **fields):
        return self._open(urllib.request.Request(BASE + path, data=urllib.parse.urlencode(fields).encode()))

    def post_json(self, path, payload):
        request = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=10) as response:
            return json.loads(response.read())

    def state(self):
        return json.loads(self.get("/__wta/state"))

    def region(self, ident):
        """A region's text, read as the tester's oracle reads it: up to the element's own closing tag."""
        match = re.search(rf'<([a-zA-Z][\w-]*)[^>]*\bid="{ident}"[^>]*>(.*?)</\1>', self.html, re.S)
        return " ".join(re.sub(r"<[^>]+>", " ", match.group(2)).split()) if match else None

    def ids(self, prefix):
        return re.findall(rf'id="{prefix}([\w-]+)"', self.html)

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

    def own_occupancy(self):
        """Who holds which time, apart from the appointments booked before the first morning."""
        return [(o["practitioner_id"], o["slot_at"], o["holder_kind"], o["booking_ref"])
                for o in self.state()["committed"]["occupancy"] if not (o["booking_ref"] or "").startswith("CF-09")]


class SignIn(unittest.TestCase):
    def test_health(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "clinicflow"})

    def test_everything_is_behind_the_sign_in(self):
        stranger = Desk(who=None)
        for path in ("/", "/book", "/patients", "/waiting", "/bookings"):
            stranger.get(path)
            self.assertTrue(stranger.url.startswith("/login"), path)
        stranger.get("/help/sign-in")
        self.assertEqual(stranger.url, "/help/sign-in")
        self.assertIsNone(stranger.state()["session"])

    def test_a_refused_sign_in_is_one_message_and_no_session(self):
        for username, password in (("rhiannon.price", "wrong"), ("nobody", PASSWORD)):
            stranger = Desk(who=None)
            stranger.post("/login", username=username, password=password)
            self.assertEqual(stranger.url, "/login")
            self.assertIsNotNone(stranger.region("login-error"))
            self.assertIsNone(stranger.state()["session"])

    def test_sign_out_keeps_the_session_and_resumes_it(self):
        desk = Desk()
        desk.returning()
        before = desk.state()
        desk.post("/logout")
        self.assertEqual(desk.url, "/login")
        self.assertEqual((desk.state()["user"], desk.state()["sha"]), (None, before["sha"]))
        desk.post("/login", username="rhiannon.price", password=PASSWORD)
        self.assertEqual(desk.state()["sha"], before["sha"])

    def test_each_role_sees_its_own_pages_and_a_notice_elsewhere(self):
        doctor = Desk("amara.okafor")
        doctor.get("/my/day")
        self.assertEqual(len(re.findall(r"CF-09\d\d", doctor.html)), 6)  # Dr Okafor's six seeded visits
        doctor.get("/book")
        self.assertEqual((doctor.status, doctor.url), (200, "/book"))
        self.assertIsNotNone(doctor.region("access-notice"))
        manager = Desk("grace.holloway")
        manager.get("/admin/utilisation")
        self.assertIn("Dr Amara Okafor", manager.html)
        manager.get("/patients")
        self.assertIsNotNone(manager.region("access-notice"))
        desk = Desk()
        desk.get("/admin/utilisation")
        self.assertIsNotNone(desk.region("access-notice"))


class Booking(unittest.TestCase):
    def setUp(self):
        self.p = Desk()

    def test_sessions_are_isolated(self):
        other = Desk()
        seed = other.state()["sha"]
        self.p.returning()
        self.assertNotEqual(self.p.state()["sha"], seed)
        self.assertEqual(other.state()["sha"], seed)
        self.assertEqual(Desk().state()["sha"], seed)

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
            p = Desk()
            draft = p.register(**payment)
            p.hold(draft, "0611-1100")
            p.book(draft)
            self.assertEqual(p.region("paid-by"), shown)
        p = Desk()
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
        self.assertEqual(len([b for b in self.p.state()["committed"]["bookings"] if b["booked_by"] == "rhiannon"]), 1)

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
        self.assertEqual(self.p.own_occupancy(), [("okafor", "2026-06-12T14:00:00", "booking", "CF-1001")])
        before = self.p.state()["sha"]
        self.p.post("/bookings/CF-1001/cancel")  # the box was not ticked
        self.assertEqual(self.p.state()["sha"], before)
        self.p.post("/bookings/CF-1001/cancel", confirm="yes")
        self.assertIn("CF-1001", self.p.region("cancelled-list"))
        self.assertNotIn("CF-1001", self.p.region("upcoming-list"))
        self.assertEqual(self.p.own_occupancy(), [])
        self.p.get("/bookings/CF-1001")
        self.assertEqual(self.p.html.count("<li>"), 3)  # booked, moved, cancelled

    def test_one_open_draft_at_a_time(self):
        first = self.p.returning()
        self.p.hold(first, "0611-1100")
        self.p.returning()
        self.p.get(f"/book/{first}/slots")
        self.assertIn("started again", self.p.html)
        drafts = self.p.state()["committed"]["drafts"]
        self.assertEqual([d["state"] for d in drafts], ["replaced", "open"])
        self.assertEqual(self.p.own_occupancy(), [])

    def test_the_appointment_list_filters_by_practitioner(self):
        draft = self.p.returning()
        self.p.hold(draft, "0611-1100")
        self.p.book(draft)
        self.p.get("/bookings?with=okafor")
        self.assertIn("CF-1001", self.p.region("upcoming-list"))
        self.p.get("/bookings?with=lindqvist")
        self.assertNotIn("CF-1001", self.p.region("upcoming-list"))
        self.assertNotIn("CF-09", Desk().get("/bookings"))  # only what this receptionist booked


class Records(unittest.TestCase):
    def setUp(self):
        self.p = Desk()

    def test_the_directory_pages_sorts_and_searches(self):
        self.p.get("/patients")
        self.assertEqual((self.p.region("range-from"), self.p.region("range-to"), self.p.region("match-total")),
                         ("1", "10", "45"))
        self.p.get("/patients?page=5")
        self.assertEqual((self.p.region("range-from"), self.p.region("range-to")), ("41", "45"))
        self.assertEqual(self.p.region("page-number"), "5")
        self.p.get("/patients?page=99")  # past the end: the last page
        self.assertEqual(self.p.region("page-number"), "5")
        self.p.get("/patients?sort=newest")
        self.assertEqual(self.p.ids("open-patient-")[0], "45")
        self.p.get("/patients?sort=dob")
        self.assertEqual(self.p.ids("open-patient-")[0], "10")
        self.p.get("/patients?q=MORGAN")
        self.assertEqual(sorted(self.p.ids("open-patient-")), ["1", "17"])
        self.p.get("/patients?q=search+query")
        self.assertIsNotNone(self.p.region("no-matches"))
        self.p.get("/patients?q=%25")  # typed text, never a pattern
        self.assertIsNotNone(self.p.region("no-matches"))

    def test_patient_create_update_delete(self):
        self.p.post("/patients/new", full_name="Test User Input", dob="0001-01-01", email="tester@example.com", phone="")
        new_id = self.p.url.rsplit("/", 1)[1]
        self.assertEqual(self.p.region("record-name"), "Test User Input")
        self.p.post(f"/patients/{new_id}/edit", full_name="Test User Input", dob="1990-01-01",
                    email="tester@example.com", phone="+1-202-555-0143")
        self.assertEqual(self.p.region("record-phone"), "+1-202-555-0143")
        self.p.post(f"/patients/{new_id}/edit", full_name="X", dob="1990-01-01", email="x@y.zz", phone="call me")
        self.assertIsNotNone(self.p.region("page-error"))
        self.p.post(f"/patients/{new_id}/delete")
        self.assertEqual(self.p.url, "/patients")
        self.assertEqual(self.p.region("match-total"), "45")
        self.p.post("/patients/3/delete")  # has appointments: kept
        self.assertIsNotNone(self.p.region("page-error"))

    def test_notes_add_edit_delete(self):
        self.p.post("/patients/1/notes", note_body="Test User Input")
        self.assertIn("Test User Input", self.p.region("record-notes"))
        self.p.post("/patients/1/notes", note_body="  ")
        self.assertIsNotNone(self.p.region("page-error"))
        self.p.post("/patients/1/notes/1", note_body="Corrected")
        self.assertIn("Corrected", self.p.region("record-notes"))
        self.p.post("/patients/1/notes/2/delete")
        self.assertNotIn("latex", self.p.region("record-notes"))
        self.p.post("/patients/2/notes/1/delete")  # another patient's note: nothing happens
        self.assertIn("Corrected", self.p.get("/patients/1?tab=notes"))

    def test_waiting_lists_reorder_remove_undo_and_notes(self):
        def order():
            self.p.get("/waiting?service=gp")
            return self.p.ids("patient-of-")
        self.assertEqual(order(), ["w1", "w2", "w3", "w4"])
        self.p.post("/waiting/w2/up")
        self.assertEqual(order(), ["w2", "w1", "w3", "w4"])
        self.assertEqual(self.p.region("waiting-next"), "Ffion Pugh")
        self.p.post("/waiting/w2/up")  # already first: nothing moves
        self.p.post("/waiting/w4/down")
        self.assertEqual(order(), ["w2", "w1", "w3", "w4"])
        self.p.post("/waiting/w1/remove")
        self.assertIn("undo-remove", self.p.html)
        self.assertEqual(self.p.ids("patient-of-"), ["w2", "w3", "w4"])
        self.p.post("/waiting/w3/up")  # swaps with the next one still waiting
        self.assertEqual(order(), ["w3", "w2", "w4"])
        self.assertNotIn("undo-remove", self.p.html)  # offered once
        self.p.post("/waiting/w1/restore")
        self.assertEqual(order(), ["w3", "w1", "w2", "w4"])
        self.assertEqual(self.p.post_json("/waiting/w2/note", {"note": "Test User Input"}),
                         {"ok": True, "note": "Test User Input"})
        order()
        self.assertEqual(self.p.region("note-w2"), "Test User Input")
        self.assertFalse(self.p.post_json("/waiting/w2/note", {"note": "A" * 5001})["ok"])
        self.p.post("/patients/1/waiting", waiting_service="nurse", waiting_note="Any time")
        self.assertEqual(self.p.url, "/waiting?service=nurse")
        self.assertEqual(self.p.ids("patient-of-")[-1], "w10")

    def test_reading_changes_nothing(self):
        before = self.p.state()["sha"]
        for path in ("/patients", "/patients?page=3&sort=dob", "/patients/1?tab=notes", "/patients/1/edit",
                     "/waiting?service=physio", "/bookings?with=bevan&sort=ref", "/"):
            self.p.get(path)
        self.assertEqual(self.p.state()["sha"], before)


if __name__ == "__main__":
    unittest.main()
