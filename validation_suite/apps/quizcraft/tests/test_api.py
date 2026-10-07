"""Quizcraft over HTTP, against the clean build (E2E_BASE_URL, test hooks on).

Each test opens its own browser-like session (cookie jar), so it starts from revision 1.
"""

import copy
import http.cookiejar
import json
import os
import re
import unittest
import urllib.error
import urllib.request
from html import unescape

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8080/").rstrip("/")


class Browser:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _open(self, request):
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, response.geturl(), response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.geturl(), error.read().decode("utf-8")

    def get(self, path):
        return self._open(urllib.request.Request(BASE + path))

    def save(self, document, base):
        body = json.dumps({"base": base, "document": document}).encode("utf-8")
        request = urllib.request.Request(BASE + "/api/revisions", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        status, _, text = self._open(request)
        assert status == 200, status
        return json.loads(text)

    def state(self):
        return json.loads(self.get("/__wta/state")[2])

    def editor(self):
        """(revision, document) embedded in the editor page."""
        status, _, html = self.get("/quiz")
        assert status == 200
        data = json.loads(re.search(r'id="quiz-data">(.*?)</script>', html, re.S).group(1))
        return data["revision"], data["document"], html


def region(html, ident):
    found = re.search(rf'<(\w+)[^>]*\sid="{ident}"[^>]*>(.*?)</\1>', html, re.S)
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", found.group(2))).split()) if found else None


class RuntimeTest(unittest.TestCase):
    def test_health_and_entry(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=10) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "quizcraft"})
        status, url, _ = Browser().get("/")
        self.assertEqual(status, 200)
        self.assertTrue(url.endswith("/quiz"))

    def test_seed_revision(self):
        browser = Browser()
        revision, document, html = browser.editor()
        self.assertEqual(revision, 1)
        self.assertEqual(len(document["questions"]), 6)
        self.assertEqual(region(html, "saved-summary"), "Revision 1: 6 questions, 2 required")
        self.assertEqual(region(html, "question-count"), "6")
        self.assertEqual(region(html, "history-ahead"), "0")
        state = browser.state()
        self.assertEqual([r["number"] for r in state["committed"]["revisions"]], [1])
        self.assertRegex(state["sha"], r"^[0-9a-f]{64}$")

    def test_sessions_are_isolated(self):
        one, two = Browser(), Browser()
        _, document, _ = one.editor()
        two.editor()
        document["questions"][4]["required"] = True
        self.assertEqual(one.save(document, 1), {"ok": True, "revision": 2})
        self.assertEqual(len(two.state()["committed"]["revisions"]), 1)


class SaveTest(unittest.TestCase):
    def test_a_save_appends_a_revision_and_a_reload_returns_it(self):
        browser = Browser()
        _, document, _ = browser.editor()
        document["questions"].append({"id": "q7", "kind": "text", "title": "New question", "required": False,
                                      "options": [], "rule": None})
        self.assertEqual(browser.save(document, 1), {"ok": True, "revision": 2})
        revision, stored, html = browser.editor()
        self.assertEqual(revision, 2)
        self.assertEqual(len(stored["questions"]), 7)
        self.assertEqual(region(html, "saved-summary"), "Revision 2: 7 questions, 2 required")
        revisions = browser.state()["committed"]["revisions"]
        self.assertEqual([r["number"] for r in revisions], [1, 2])
        self.assertEqual(len(revisions[0]["document"]["questions"]), 6, "revision 1 is never changed")

    def test_making_q5_required_gives_three_required(self):
        browser = Browser()
        _, document, _ = browser.editor()
        document["questions"][4]["required"] = True
        browser.save(document, 1)
        _, _, html = browser.editor()
        self.assertEqual(region(html, "saved-summary"), "Revision 2: 6 questions, 3 required")

    def test_refusals_are_200_and_store_nothing(self):
        browser = Browser()
        _, document, _ = browser.editor()
        before = browser.state()["sha"]
        untitled = copy.deepcopy(document)
        untitled["questions"][1]["title"] = ""
        answer = browser.save(untitled, 1)
        self.assertFalse(answer["ok"])
        self.assertIn("2", answer["message"])
        long = copy.deepcopy(document)
        long["questions"][0]["title"] = "A" * 2048
        self.assertFalse(browser.save(long, 1)["ok"])
        self.assertFalse(browser.save(document, 7)["ok"], "a save from a stale base is refused")
        self.assertEqual(browser.state()["sha"], before)

    def test_revisions_page_lists_newest_first(self):
        browser = Browser()
        _, document, _ = browser.editor()
        document["questions"].pop()
        browser.save(document, 1)
        status, _, html = browser.get("/revisions")
        self.assertEqual(status, 200)
        self.assertLess(html.index('id="rev-2"'), html.index('id="rev-1"'))


if __name__ == "__main__":
    unittest.main()
