"""Helpline's server, driven over HTTP against the clean build (E2E_BASE_URL).

Requests marked as fragment requests (`X-Desk-Fragment: 1`) get what the page script gets:
the parts of the page that changed. Unmarked requests get redirects and whole pages, the
no-script path. Every desk signs in first, through the form, like a browser.
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
PASSWORD = "Helpline-desk-26"  # every demo agent (README)


class Desk:
    """One browser session, signed in as `who` unless that is None."""

    def __init__(self, who="sam.lewis"):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.url = ""
        self.headers = {}
        if who:
            self.post("/login", {"username": who, "password": PASSWORD}, fragment=False)
            assert self.url.endswith("/queue"), self.url

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

    def post_json(self, path, payload):
        request = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        return json.loads(self._open(request)[1])

    def state(self):
        return json.loads(self.get("/__wta/state")[1])

    def ticket(self, number):
        return next(t for t in self.state()["committed"]["tickets"] if t["_id"] == number)

    @staticmethod
    def region(markup, ident):
        match = re.search(rf'<(\w+)[^>]*\bid="{ident}"[^>]*>(.*?)</\1>', markup, re.S)
        return " ".join(re.sub(r"<[^>]+>", " ", match.group(2)).split()) if match else None


class SignIn(unittest.TestCase):
    def test_health_and_the_sign_in_wall(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "helpline"})
        stranger = Desk(who=None)
        for path in ("/", "/queue", "/tickets/101", "/customers", "/replies"):
            stranger.get(path)
            self.assertIn("/login", stranger.url, path)
        status, _ = stranger.get("/help/sign-in")
        self.assertEqual(status, 200)
        status, body = stranger.post("/tickets/101/resolve")  # a fragment request, signed out
        self.assertEqual((status, json.loads(body)["reason"]), (200, "signed-out"))
        self.assertIsNone(stranger.state()["session"])

    def test_a_refused_sign_in_is_one_message_and_no_session(self):
        for username, password in (("sam.lewis", "nope"), ("nobody", PASSWORD), ("sam.lewis", "")):
            stranger = Desk(who=None)
            _, page = stranger.post("/login", {"username": username, "password": password}, fragment=False)
            self.assertIn("/login", stranger.url)
            self.assertIsNotNone(stranger.region(page, "login-error"))
            self.assertIsNone(stranger.state()["session"])

    def test_sign_out_keeps_the_session_for_the_next_sign_in(self):
        desk = Desk()
        desk.post("/tickets/101/handoff")
        before = desk.state()
        desk.post("/logout", fragment=False)
        self.assertIn("/login", desk.url)
        after = desk.state()
        self.assertEqual((after["session"], after["user"], after["sha"]), (before["session"], None, before["sha"]))
        desk.post("/login", {"username": "sam.lewis", "password": PASSWORD}, fragment=False)
        self.assertEqual(desk.state()["sha"], before["sha"])

    def test_only_the_lead_sees_the_sla_report(self):
        lead = Desk("morgan.blake")
        _, page = lead.get("/reports")
        self.assertIn("breach-102", page)  # urgent, opened yesterday morning, unanswered
        _, page = Desk("kai.moreno").get("/reports")
        self.assertIsNotNone(Desk.region(page, "access-notice"))
        self.assertNotIn('id="nav-reports"', page)


class HelplineDesk(unittest.TestCase):
    def setUp(self):
        self.d = Desk()

    def test_entry(self):
        status, page = self.d.get("/")
        self.assertEqual(status, 200)
        self.assertTrue(self.d.url.endswith("/queue"))
        self.assertIn('id="queue-rows"', page)
        self.assertEqual(self.d.region(page, "count-new"), "3")
        self.assertEqual(self.d.region(page, "signed-in-as"), "Sam Lewis · Agent")

    def test_queue_filters(self):
        _, page = self.d.get("/queue?status=open&assignee=none")
        rows = self.d.region(page, "queue-rows")
        for subject in ("Router keeps dropping Wi-Fi", "No internet since the storm", "Refund for the double charge",
                        "Can't log in to my account"):
            self.assertIn(subject, rows)
        self.assertNotIn("Charged twice in May", rows)
        _, page = self.d.get("/queue?status=resolved&assignee=any")
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
        index = next(r for r in self.d.state()["committed"]["queue_index"] if r["_id"] == 103)
        self.assertEqual(index["status"], "in_progress")

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
        bodies = [e["body"] for e in self.d.ticket(101)["entries"] if e["role"] != "system"]
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
        self.assertEqual((self.d.ticket(106)["status"], self.d.ticket(106)["merged_into"]), ("closed", 105))
        _, page = self.d.get("/tickets/106")
        self.assertIn("105", self.d.region(page, "linked-tickets"))
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
        self.assertEqual(self.d.region(fragment, "ticket-assignee"), "Kai Moreno")
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
        self.assertIn("2026-06-04 09:05", self.d.region(fragment, "history-list"))
        _, page = self.d.get("/tickets/108?tab=details")
        details = self.d.region(page, "customer-details")
        self.assertIn("jonas.berg@example.net", details)
        self.assertIn("Copper Basic · 36 Mbps", details)
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


class Enlarged(unittest.TestCase):
    def setUp(self):
        self.d = Desk()

    def test_new_ticket_wizard(self):
        _, page = self.d.get("/tickets/new?q=bello")
        self.assertEqual(re.findall(r'id="choose-customer-(c\d+)"', page), ["c10"])
        fields = {"customer": "c10", "subject": "Test User Input", "channel": "phone", "priority": "urgent",
                  "message": TYPED}
        self.d.post("/tickets/new/review", {**fields, "subject": " "}, fragment=False)
        self.assertIn("/tickets/new/details", self.d.url)
        self.d.post("/tickets/new/review", fields, fragment=False)
        self.assertIn("/tickets/new/review", self.d.url)
        _, page = self.d.post("/tickets/new", fields, fragment=False)
        self.assertTrue(self.d.url.endswith("/tickets/111"))
        self.assertIn("functional testing", self.d.region(page, "conversation"))
        self.assertEqual(self.d.region(page, "count-new"), "4")
        self.assertEqual(self.d.ticket(111)["customer_id"], "c10")

    def test_ticket_delete(self):
        self.d.post("/tickets/102/delete", fragment=False)
        self.assertTrue(self.d.url.endswith("/queue?deleted=102"))
        tickets = {t["_id"]: t for t in self.d.state()["committed"]["tickets"]}
        self.assertNotIn(102, tickets)
        self.assertEqual(tickets[107]["linked"], [])  # its link went with it

    def test_deleting_a_merge_target_clears_the_merged_ticket_pointer(self):
        self.d.post("/tickets/105/merge", {"merge_source": "106"})
        self.assertEqual(self.d.ticket(106)["merged_into"], 105)
        self.d.post("/tickets/105/delete", fragment=False)
        self.assertIsNone(self.d.ticket(106)["merged_into"])
        _, page = self.d.get("/tickets/106")
        self.assertNotIn('id="merged-into"', page)  # no link to a ticket that is gone

    def test_customer_directory_pages_sorts_and_searches(self):
        _, page = self.d.get("/customers")
        self.assertEqual((self.d.region(page, "page-count"), self.d.region(page, "speed-max")), ("4", "1000"))
        _, page = self.d.get("/customers?page=4")
        self.assertEqual(re.findall(r'id="open-customer-(c\d+)"', page)[-1], "c34")
        _, page = self.d.get("/customers?q=&sort=speed")
        self.assertEqual(self.d.region(page, "speed-1"), "1000")
        _, page = self.d.get("/customers?q=WEI&sort=name")
        self.assertEqual(re.findall(r'id="open-customer-(c\d+)"', page), ["c5"])
        _, page = self.d.get("/customers?q=.%2A")  # typed text, never a pattern
        self.assertIsNotNone(self.d.region(page, "no-customers"))

    def test_customer_create_update_delete(self):
        form = {"name": "Test User Input", "email": "tester@example.com", "phone": "+1-202-555-0143",
                "plan": "Gigafast", "line1": "1 High St", "town": "Leeds", "postcode": "LS1 1AA"}
        self.d.post("/customers/new", {**form, "email": "priya.shah@example.net"}, fragment=False)
        self.assertIn("error=msg.email_taken", self.d.url)
        _, page = self.d.post("/customers/new", form, fragment=False)
        self.assertTrue(self.d.url.endswith("/customers/c36?done=added"))
        self.assertEqual(self.d.region(page, "customer-name"), "Test User Input")
        _, page = self.d.post("/customers/c36/edit", {**form, "phone": "0113 496 0000", "plan": "Fibre Plus"}, fragment=False)
        self.assertEqual(self.d.region(page, "customer-phone"), "0113 496 0000")
        self.assertIn("500 Mbps", self.d.region(page, "customer-plan"))
        self.d.post("/customers/c36/delete", fragment=False)
        self.assertTrue(self.d.url.endswith("/customers?done=deleted"))
        _, page = self.d.post("/customers/c1/delete", fragment=False)  # Priya has tickets: kept
        self.assertIn("error=msg.customer_has_tickets", self.d.url)

    def test_saved_replies_add_move_rename_delete(self):
        self.d.post("/replies", {"title": "Test User Input", "body": TYPED}, fragment=False)
        _, page = self.d.get("/replies")
        self.assertEqual(re.findall(r'id="title-(r\d+)"', page)[-1], "r6")
        self.d.post("/replies/r2/up", fragment=False)
        _, page = self.d.get("/replies")
        self.assertEqual(self.d.region(page, "first-reply"), "Router restart steps")
        self.assertEqual(self.d.post_json("/replies/r3/title", {"title": "Refund issued"}),
                         {"ok": True, "title": "Refund issued"})
        self.assertFalse(self.d.post_json("/replies/r3/title", {"title": " "})["ok"])
        too_long = self.d.post_json("/replies/r3/title", {"title": "A" * 2048})  # refused, not a 500
        self.assertFalse(too_long["ok"])
        self.assertTrue(too_long["error"])
        self.d.post("/replies/r5/delete", fragment=False)
        _, page = self.d.get("/replies")
        self.assertNotIn("Closing note", self.d.region(page, "saved-replies"))
        self.assertIn("Refund issued", self.d.region(page, "saved-replies"))
        _, page = self.d.get("/tickets/101")
        self.assertIn('value="r2"', page)  # offered in the reply form, in their order

    def test_reading_changes_nothing(self):
        before = self.d.state()["sha"]
        for path in ("/customers?sort=speed&page=2", "/customers/c1", "/tickets/new?q=a", "/replies", "/team",
                     "/tickets/104?tab=details", "/tickets/new/details?customer=c3"):
            self.d.get(path)
        self.assertEqual(self.d.state()["sha"], before)


if __name__ == "__main__":
    unittest.main()
