"""Helpline's server, driven over HTTP against the clean build (E2E_BASE_URL).

Requests marked as fragment requests (`X-Desk-Fragment: 1`) get what the page script gets:
the parts of the page that changed. Unmarked requests get redirects and whole pages, the
no-script path.
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
TYPED = "This is a typical multi-line\ncomment used for functional testing."


class Desk:
    """One browser session."""

    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.url = ""
        self.headers = {}

    def _open(self, request):
        try:
            with self.opener.open(request, timeout=10) as response:
                self.url, self.headers = response.geturl(), dict(response.headers)
                return response.status, unescape(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, unescape(error.read().decode("utf-8"))

    def get(self, path, fragment=False):
        headers = {"X-Desk-Fragment": "1"} if fragment else {}
        return self._open(urllib.request.Request(BASE + path, headers=headers))

    def post(self, path, fields=None, fragment=True):
        headers = {"X-Desk-Fragment": "1"} if fragment else {}
        data = urllib.parse.urlencode(fields or {}).encode()
        return self._open(urllib.request.Request(BASE + path, data=data, headers=headers))

    def state(self):
        return json.loads(self.get("/__wta/state")[1])

    def ticket(self, number):
        return next(t for t in self.state()["committed"]["tickets"] if t["id"] == number)

    @staticmethod
    def region(markup, ident):
        match = re.search(rf'<(\w+)[^>]*\bid="{ident}"[^>]*>(.*?)</\1>', markup, re.S)
        return " ".join(re.sub(r"<[^>]+>", " ", match.group(2)).split()) if match else None


class HelplineDesk(unittest.TestCase):
    def setUp(self):
        self.d = Desk()

    def test_health_and_entry(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "helpline"})
        status, page = self.d.get("/")
        self.assertEqual(status, 200)
        self.assertTrue(self.d.url.endswith("/queue"))
        self.assertIn('id="queue-rows"', page)
        self.assertEqual(self.d.region(page, "count-new"), "3")

    def test_queue_filters(self):
        _, page = self.d.get("/queue?status=open&assignee=none")
        rows = self.d.region(page, "queue-rows")
        for subject in ("Router keeps dropping Wi-Fi", "No internet since the storm", "Refund for the double charge",
                        "Moving house next month", "Can't log in to my account"):
            self.assertIn(subject, rows)
        self.assertNotIn("Charged twice in May", rows)
        _, page = self.d.get("/queue?status=resolved&assignee=any")
        self.assertIn("Upgrade to the fibre plan", self.d.region(page, "queue-rows"))
        self.assertEqual(page.count('id="open-'), 1)
        _, fragment = self.d.get("/queue/rows?status=new&assignee=noor", fragment=True)
        self.assertEqual(self.d.headers.get("X-Desk-Url"), "/queue?status=new&assignee=noor")
        self.assertIn("No tickets match", self.d.region(fragment, "queue-rows"))
        _, page = self.d.get("/queue?status=bogus&assignee=bogus")
        self.assertEqual(page.count('id="open-'), 8)  # falls back to all open tickets, anyone's

    def test_resolve_needs_the_agent_to_have_the_last_word(self):
        before = self.d.state()["sha"]
        _, fragment = self.d.post("/tickets/101/resolve")
        self.assertIn("last word", self.d.region(fragment, "ticket-flash"))
        self.assertEqual(self.d.state()["sha"], before)
        _, fragment = self.d.post("/tickets/101/reply", {"reply_body": TYPED})
        self.assertEqual(self.d.region(fragment, "ticket-status"), "Waiting on customer")
        self.assertEqual(self.d.region(fragment, "latest-role"), "Agent")
        self.assertIn("used for functional testing", self.d.region(fragment, "conversation"))
        _, fragment = self.d.post("/tickets/101/resolve")
        self.assertEqual(self.d.region(fragment, "ticket-status"), "Resolved")
        self.assertEqual(self.d.region(fragment, "count-resolved"), "2")

    def test_close_only_from_resolved(self):
        before = self.d.state()["sha"]
        _, fragment = self.d.post("/tickets/107/close")
        self.assertIn("Only a resolved ticket", self.d.region(fragment, "ticket-flash"))
        self.assertEqual(self.d.state()["sha"], before)
        self.d.post("/tickets/107/resolve")
        _, fragment = self.d.post("/tickets/107/close")
        self.assertEqual(self.d.region(fragment, "ticket-status"), "Closed")
        self.assertNotIn('id="reply-body"', fragment)
        _, fragment = self.d.post("/tickets/107/reply", {"reply_body": TYPED})
        self.assertIn("closed", self.d.region(fragment, "ticket-flash"))
        self.assertEqual(self.d.ticket(107)["status"], "closed")

    def test_explicit_moves_follow_the_machine(self):
        _, fragment = self.d.post("/tickets/103/move", {"to": "in_progress"})
        self.assertIn("isn't available", self.d.region(fragment, "ticket-flash"))
        self.d.post("/tickets/103/move", {"to": "triage"})
        self.assertEqual(self.d.ticket(103)["status"], "triage")
        self.d.post("/tickets/103/move", {"to": "in_progress"})
        self.assertEqual(self.d.ticket(103)["status"], "in_progress")
        self.d.post("/tickets/103/move", {"to": "closed"})
        self.assertEqual(self.d.ticket(103)["status"], "in_progress")

    def test_a_customer_reply_reopens_waiting_and_resolved_tickets(self):
        _, fragment = self.d.post("/tickets/107/customer-reply", {"customer_body": TYPED})
        self.assertEqual(self.d.region(fragment, "ticket-status"), "In progress")
        self.assertEqual(self.d.region(fragment, "latest-role"), "Customer")
        _, fragment = self.d.post("/tickets/108/customer-reply", {"customer_body": "a"})
        self.assertEqual(self.d.region(fragment, "ticket-status"), "In progress")
        self.d.post("/tickets/104/customer-reply", {"customer_body": "12345"})
        self.assertEqual(self.d.ticket(104)["status"], "in_progress")  # was already being worked

    def test_reply_bodies_are_checked_and_stored_as_typed(self):
        before = self.d.state()["sha"]
        for body in ("", "   ", "A" * 10001):
            self.d.post("/tickets/101/reply", {"reply_body": body})
        self.assertEqual(self.d.state()["sha"], before)
        self.d.post("/tickets/101/reply", {"reply_body": "A" * 8192})
        self.d.post("/tickets/101/customer-reply", {"customer_body": TYPED})
        bodies = [e["body"] for e in self.d.state()["committed"]["entries"] if e["ticket_id"] == 101]
        self.assertEqual(bodies[-2:], ["A" * 8192, TYPED])

    def test_merge_closes_the_source_and_links_both_tickets(self):
        _, page = self.d.get("/tickets/105")
        options = re.findall(r'<option value="(\d+)"', page)
        self.assertEqual(options[0], "106")  # the same customer's ticket comes first
        self.assertNotIn("108", options)
        self.assertNotIn("105", options)
        _, fragment = self.d.post("/tickets/105/merge", {"merge_source": "106"})
        self.assertIn("#106", self.d.region(fragment, "ticket-flash"))
        self.assertIn("106", self.d.region(fragment, "linked-tickets"))
        self.assertEqual(self.d.ticket(106)["status"], "closed")
        self.assertEqual(self.d.ticket(106)["merged_into"], 105)
        _, page = self.d.get("/tickets/106")
        self.assertIn("105", self.d.region(page, "linked-tickets"))
        self.assertIsNone(self.d.region(page, "ticket-flash") or None)  # the banner is one-time
        before = self.d.state()["sha"]
        _, fragment = self.d.post("/tickets/105/merge", {"merge_source": "106"})
        self.assertIn("Choose an open ticket", self.d.region(fragment, "merge-error"))
        self.d.post("/tickets/105/merge", {"merge_source": ""})
        self.d.post("/tickets/105/merge", {"merge_source": "105"})
        self.assertEqual(self.d.state()["sha"], before)

    def test_hand_offs_keep_the_unassigned_filter_true(self):
        _, fragment = self.d.post("/queue/handoff/102?status=open&assignee=none")
        rows = self.d.region(fragment, "queue-rows")
        self.assertNotIn("No internet since the storm", rows)
        self.assertNotIn("Kai", rows)
        self.assertEqual(self.d.headers.get("X-Desk-Url"), "/queue?status=open&assignee=none")
        _, fragment = self.d.post("/tickets/101/handoff")
        self.assertEqual(self.d.region(fragment, "ticket-assignee"), "Kai")
        _, page = self.d.get("/queue?status=open&assignee=none")
        self.assertNotIn("Kai", self.d.region(page, "queue-rows"))
        _, page = self.d.get("/queue?status=open&assignee=kai")
        self.assertEqual(page.count('id="open-'), 2)
        self.assertIn('id="handoff-101" type="submit" disabled', page)
        _, fragment = self.d.post("/queue/handoff/101?status=open&assignee=any")
        self.assertIn("already", self.d.region(fragment, "queue-flash"))

    def test_tabs_are_fragments_with_an_address(self):
        _, fragment = self.d.get("/tickets/108/tab/history", fragment=True)
        self.assertEqual(self.d.headers.get("X-Desk-Url"), "/tickets/108?tab=history")
        self.assertIn("2026-06-03 14:10", self.d.region(fragment, "history-list"))
        _, page = self.d.get("/tickets/108?tab=details")
        self.assertIn("jonas.berg@example.net", self.d.region(page, "customer-details"))
        self.d.get("/tickets/108/tab/details")  # without the script: a redirect to the page
        self.assertTrue(self.d.url.endswith("/tickets/108?tab=details"))

    def test_without_the_script_forms_redirect_back(self):
        self.d.post("/tickets/101/reply", {"reply_body": TYPED, "_tab": "conversation"}, fragment=False)
        self.assertTrue(self.d.url.endswith("/tickets/101?tab=conversation"))
        self.assertEqual(self.d.ticket(101)["status"], "waiting")
        status, _ = self.d.get("/tickets/999")
        self.assertEqual(status, 404)

    def test_sessions_are_isolated(self):
        other = Desk()
        before = other.state()["sha"]
        self.d.post("/tickets/101/handoff")
        self.assertEqual(other.state()["sha"], before)
        self.assertNotEqual(self.d.state()["sha"], before)


if __name__ == "__main__":
    unittest.main()
