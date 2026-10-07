"""Markbook against the clean build (E2E_BASE_URL), plus the arithmetic it is built on."""

import http.cookiejar
import json
import os
import re
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from fractions import Fraction
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import grades  # noqa: E402

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8080/").rstrip("/")
CLASS = "/teacher/classes/10b"
SEED_BASIS = "hw:20,quiz:30,proj:40,exam:120"
ASSESSMENTS = [{"id": "hw", "max_marks": 20, "weight": 20, "published": True},
               {"id": "quiz", "max_marks": 30, "weight": 20, "published": True},
               {"id": "proj", "max_marks": 40, "weight": 30, "published": True},
               {"id": "exam", "max_marks": 120, "weight": 30, "published": False}]


class Browser:
    """One cookie jar: one markbook session."""

    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.url = ""

    def _open(self, request):
        try:
            with self.opener.open(request, timeout=10) as response:
                self.url = response.geturl()
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8")

    def get(self, path):
        return self._open(urllib.request.Request(BASE + path))

    def post(self, path, fields):
        data = urllib.parse.urlencode(fields).encode("utf-8")
        return self._open(urllib.request.Request(BASE + path, data=data))  # follows the 303

    def patch_mark(self, student, assessment, mark, basis=SEED_BASIS):
        body = json.dumps({"mark": mark, "basis": basis}).encode("utf-8")
        status, text = self._open(urllib.request.Request(
            f"{BASE}{CLASS}/marks/{student}/{assessment}", data=body, method="PATCH",
            headers={"Content-Type": "application/json"}))
        return status, json.loads(text)

    def state(self):
        return json.loads(self.get("/__wta/state")[1])

    def committed(self):
        return self.state()["committed"]

    @staticmethod
    def region(html, ident):
        """The oracle's reading of a region: text up to the element's first closing tag."""
        match = re.search(rf'<([a-zA-Z][\w-]*)[^>]*\bid="{re.escape(ident)}"[^>]*>(.*?)</\1>', html, re.S)
        if match is None:
            return None
        return " ".join(unescape(re.sub(r"<[^>]+>", " ", match.group(2))).split())


class Arithmetic(unittest.TestCase):
    """The verified numbers of the plan, pinned on the pure functions."""

    def test_chen_with_42_in_the_exam_is_67_1(self):
        exact = grades.weighted({"hw": 17, "quiz": 20, "proj": 35, "exam": 42}, ASSESSMENTS)
        self.assertEqual(exact, Fraction(805, 12))  # 67.0833...
        self.assertEqual(grades.percent(exact), Decimal("67.1"))
        self.assertEqual(grades.level(grades.percent(exact)), 3)

    def test_amara_against_an_exam_out_of_42_would_be_123_0(self):
        lowered = [dict(a, max_marks=42) if a["id"] == "exam" else a for a in ASSESSMENTS]
        exact = grades.weighted({"hw": 18, "quiz": 26, "proj": 36, "exam": 85}, lowered)
        self.assertEqual(exact, Fraction(2584, 21))  # 123.0476...
        self.assertEqual(grades.percent(exact), Decimal("123.0"))

    def test_halves_round_up_and_rounding_is_exact(self):
        self.assertEqual(grades.percent(Fraction(6705, 100)), Decimal("67.1"))
        self.assertEqual(grades.percent(Fraction(67049, 1000)), Decimal("67.0"))
        self.assertEqual(grades.percent(Fraction(1, 3) * 100), Decimal("33.3"))
        self.assertEqual(grades.percent(Fraction(2, 3) * 100), Decimal("66.7"))
        self.assertEqual(grades.percent(Fraction(100)), Decimal("100.0"))

    def test_rounding_never_moves_more_than_half_a_tenth(self):
        for numerator in range(0, 2001):
            exact = Fraction(numerator, 17)
            self.assertLessEqual(abs(Fraction(grades.percent(exact)) - exact), Fraction(1, 20))

    def test_levels_and_blanks(self):
        self.assertEqual([grades.level(Decimal(v)) for v in ("80.0", "79.9", "70.0", "60.0", "50.0", "49.9")],
                         [5, 4, 4, 3, 2, 1])
        self.assertIsNone(grades.weighted({"hw": 1, "quiz": 1, "proj": 1}, ASSESSMENTS))
        self.assertEqual(grades.shown(None), "-")


class Markbook(unittest.TestCase):
    def setUp(self):
        self.b = Browser()

    def matrix(self):
        status, html = self.b.get(CLASS)
        self.assertEqual(status, 200)
        return html

    def test_health_and_entry(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "markbook"})
        status, html = self.b.get("/")
        self.assertEqual(status, 200)
        self.assertTrue(self.b.url.endswith("/teacher"))
        self.assertIn('id="class-10b"', html)

    def test_seed_matrix(self):
        html = self.matrix()
        region = self.b.region
        self.assertEqual(region(html, "pct-s1"), "83.6%")
        self.assertEqual(region(html, "level-s1"), "5")
        self.assertEqual(region(html, "pct-s2"), "70.7%")
        self.assertEqual(region(html, "pct-s3"), "-")
        self.assertEqual(region(html, "level-s3"), "-")
        self.assertEqual(region(html, "mark-s3-exam"), "-")
        self.assertEqual(region(html, "mark-s3-proj"), "35")
        self.assertEqual([region(html, f"max-{a}") for a in ("hw", "quiz", "proj", "exam")], ["20", "30", "40", "120"])
        self.assertEqual(html.count('class="cell"'), 24)
        self.assertIn(f'data-basis="{SEED_BASIS}"', html)

    def test_a_cell_edit_answers_with_the_recomputed_row_and_column(self):
        status, reply = self.b.patch_mark("s3", "exam", "42")
        self.assertEqual(status, 200)
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["row"], {"student": "s3", "scores": {"hw": 17, "quiz": 20, "proj": 35, "exam": 42},
                                        "pct": "67.1%", "level": "3"})
        self.assertEqual(reply["column"], {"assessment": "exam", "average": "57.2%"})
        self.assertEqual(reply["class_average"], "70.0%")
        html = self.matrix()
        self.assertEqual(self.b.region(html, "pct-s3"), "67.1%")
        self.assertEqual(self.b.region(html, "mark-s3-exam"), "42")

    def test_marks_are_whole_numbers_up_to_the_maximum(self):
        before = self.b.committed()
        for bad in ("999999999", "121", "-1", "4.5", "abc", "1e2", 3.5):
            status, reply = self.b.patch_mark("s1", "exam", bad)
            self.assertEqual(status, 200, bad)
            self.assertFalse(reply["ok"], bad)
            self.assertIn("120", reply["error"])
        self.assertEqual(self.b.committed(), before)
        self.assertTrue(self.b.patch_mark("s2", "quiz", "0")[1]["ok"])
        self.assertTrue(self.b.patch_mark("s1", "exam", "120")[1]["ok"])
        _, cleared = self.b.patch_mark("s1", "hw", "")
        self.assertEqual(cleared["row"]["scores"]["hw"], None)
        self.assertEqual(cleared["row"]["pct"], "-")

    def test_a_page_drawn_with_other_maxima_is_told_to_reload(self):
        self.b.post(f"{CLASS}/assessments/quiz/maximum", {"max_marks": "42"})
        before = self.b.committed()
        status, reply = self.b.patch_mark("s3", "exam", "42")  # still sends the seed's maxima
        self.assertEqual(status, 200)
        self.assertFalse(reply["ok"])
        self.assertEqual(self.b.committed(), before)
        self.assertTrue(self.b.patch_mark("s3", "exam", "42", basis="hw:20,quiz:42,proj:40,exam:120")[1]["ok"])

    def test_a_maximum_cannot_go_below_a_mark_already_given(self):
        before = self.b.committed()
        _, html = self.b.post(f"{CLASS}/assessments/exam/maximum", {"max_marks": "42"})
        self.assertIn("102", self.b.region(html, "assess-flash"))
        self.assertEqual(self.b.committed(), before)
        for bad in ("0", "1001", "999999999", "", "abc", "4.5"):
            self.b.post(f"{CLASS}/assessments/hw/maximum", {"max_marks": bad})
            self.assertEqual(self.b.committed(), before, bad)
        self.b.post(f"{CLASS}/assessments/exam/maximum", {"max_marks": "102"})  # equal to the highest mark
        self.b.post(f"{CLASS}/assessments/quiz/maximum", {"max_marks": "42"})
        maxima = {a["id"]: a["max_marks"] for a in self.b.committed()["assessments"]}
        self.assertEqual(maxima, {"hw": 20, "quiz": 42, "proj": 40, "exam": 102})
        html = self.matrix()
        self.assertEqual(self.b.region(html, "max-quiz"), "42")
        self.assertEqual(self.b.region(html, "pct-s1"), "82.4%")  # 18 + 26*20/42 + 27 + 85*30/102

    def test_publish_withdraw_and_the_student_view(self):
        _, html = self.b.get("/student/s1")
        self.assertNotIn("85 / 120", self.b.region(html, "student-view"))
        self.b.post(f"{CLASS}/assessments/exam/publish", {})
        self.assertTrue(self.b.committed()["assessments"][3]["published"])
        published = self.b.committed()
        _, html = self.b.post(f"{CLASS}/assessments/exam/publish", {})  # already published
        self.assertEqual(self.b.committed(), published)
        self.assertIn("already", self.b.region(html, "assess-flash"))
        _, html = self.b.get("/student/s1")
        view = self.b.region(html, "student-view")
        self.assertIn("85 / 120", view)
        self.assertIn("83.6%", view)
        self.b.post(f"{CLASS}/assessments/exam/withdraw", {})
        _, html = self.b.get("/student/s1")
        self.assertNotIn("85 / 120", self.b.region(html, "student-view"))
        self.assertIn("89.0%", self.b.region(html, "student-view"))  # 62.33 of 70

    def test_feedback_from_the_bank_reaches_the_student(self):
        _, html = self.b.post(f"{CLASS}/students/s2/feedback", {"feedback": ""})
        self.assertIn("bank", self.b.region(html, "record-flash"))
        self.assertEqual(self.b.committed()["notes"], Browser().committed()["notes"])
        self.b.post(f"{CLASS}/students/s2/feedback", {"feedback": "cb2"})
        _, html = self.b.get("/student/s2")
        comments = self.b.region(html, "student-comments")
        self.assertIn("keep practising algebra", comments)
        self.assertIn("From your teacher", comments)
        self.b.post(f"{CLASS}/students/s2/feedback", {"feedback": "cb5"})  # replaces it
        _, html = self.b.get("/student/s2")
        comments = self.b.region(html, "student-comments")
        self.assertNotIn("algebra", comments)
        self.assertIn("Well organised", comments)

    def test_a_private_note_never_reaches_the_student_even_after_an_edit(self):
        text = "This is a typical multi-line\ncomment used for functional testing."
        _, html = self.b.post(f"{CLASS}/students/s2/note", {"private_note": text})
        self.assertIn("multi-line", self.b.region(html, "private-note-text"))
        self.assertIn('id="edit-note"', html)
        _, html = self.b.get(f"{CLASS}/students/s2?note=edit")
        self.assertIn("multi-line\ncomment used", unescape(html))
        self.b.post(f"{CLASS}/students/s2/note", {"private_note": text})
        self.b.post(f"{CLASS}/students/s2/note", {"private_note": "Second edit"})
        notes = [n for n in self.b.committed()["notes"] if n["student"] == "s2"]
        self.assertEqual(notes, [{"student": "s2", "canned": None, "body": "Second edit", "visibility": "teacher"}])
        for student in ("s2", "s4"):
            _, html = self.b.get(f"/student/{student}")
            comments = self.b.region(html, "student-comments")
            self.assertNotIn("Private", comments)
            self.assertNotIn("Second edit", html)
            self.assertNotIn("exam nerves", html)
        self.b.post(f"{CLASS}/students/s4/note", {"private_note": "Edited by the tester"})
        _, html = self.b.get("/student/s4")
        self.assertNotIn("Edited by the tester", html)

    def test_note_rules(self):
        before = self.b.committed()
        for bad in ("", "   ", "A" * 10_001):
            self.b.post(f"{CLASS}/students/s3/note", {"private_note": bad})
        self.assertEqual(self.b.committed(), before)
        self.b.post(f"{CLASS}/students/s3/note", {"private_note": "A" * 8192})
        self.assertEqual(len(self.b.committed()["notes"]), len(before["notes"]) + 1)

    def test_report_and_help_change_nothing(self):
        before = self.b.state()["sha"]
        status, html = self.b.get(f"{CLASS}/report")
        self.assertEqual(status, 200)
        self.assertEqual(self.b.region(html, "level-count-5"), "2")
        self.assertEqual(self.b.region(html, "level-count-none"), "1")
        self.assertIn("2026-06-10", html)
        self.assertEqual(self.b.get("/help")[0], 200)
        self.assertEqual(self.b.state()["sha"], before)

    def test_sessions_are_isolated_and_start_from_the_seed(self):
        other = Browser()
        seed = other.state()["sha"]
        self.b.patch_mark("s3", "exam", "42")
        self.assertEqual(other.state()["sha"], seed)
        self.assertNotEqual(self.b.state()["sha"], seed)
        self.assertEqual(Browser().state()["sha"], seed)

    def test_unknown_addresses(self):
        for path in ("/student/s9", f"{CLASS}/assessments/essay", "/teacher/classes/11a", "/nowhere"):
            self.assertEqual(self.b.get(path)[0], 404, path)


if __name__ == "__main__":
    unittest.main()
