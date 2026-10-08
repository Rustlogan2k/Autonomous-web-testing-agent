"""Tidewater's API, against the clean build (E2E_BASE_URL)."""

import http.cookiejar
import json
import os
import unittest
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8080/").rstrip("/")
PASSWORD = "Tidewater-quay-26"  # both demo customers (README)


class _Manual(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # redirects are asserted, not followed
        return None


class Client:
    """One browser context. Signed in as `who` unless that is None."""

    def __init__(self, who="alex.carter"):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _Manual())
        if who:
            status, location, _ = self.form("/login", username=who, password=PASSWORD)
            assert (status, location) == (303, "/"), (status, location)

    def raw(self, method, path, data=None, content_type=None):
        request = urllib.request.Request(BASE + path, data=data, method=method,
                                         headers={"Content-Type": content_type} if content_type else {})
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, response.headers.get("Location"), response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Location"), error.read()

    def form(self, path, **fields):
        return self.raw("POST", path, urllib.parse.urlencode(fields).encode(),
                        "application/x-www-form-urlencoded")

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        status, _, payload = self.raw(method, path, data, "application/json" if data else None)
        return status, json.loads(payload)

    def balances(self):
        _, data = self.call("GET", "/api/overview")
        return {a["id"]: a["balance"] for a in data["accounts"]}

    def transfer(self, source="everyday", target="payee:landlord", amount="42", when="now"):
        _, draft = self.call("POST", "/api/drafts", {"from": source, "to": target})
        status, quote = self.call("POST", f"/api/drafts/{draft['id']}/quote",
                                  {"amount": amount, "reference": "", "when": when})
        if status != 201:
            return status, quote
        return self.call("POST", f"/api/quotes/{quote['id']}/authorise")


class SignIn(unittest.TestCase):
    def test_health(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "tidewater"})

    def test_the_app_is_behind_the_sign_in_and_its_help_page_is_public(self):
        stranger = Client(who=None)
        self.assertEqual(stranger.raw("GET", "/")[:2], (303, "/login?next=%2F"))
        self.assertEqual(stranger.call("GET", "/api/overview"), (200, {"ok": False, "reason": "signed-out"}))
        status, _, page = stranger.raw("GET", "/login")
        self.assertEqual(status, 200)
        self.assertIn(b'id="login-password"', page)
        self.assertEqual(stranger.raw("GET", "/help/password")[0], 200)
        self.assertEqual(stranger.raw("GET", "/app.js")[0], 200)  # assets are not secrets
        self.assertEqual(stranger.call("GET", "/__wta/state")[1]["session"], None)

    def test_a_refused_sign_in_answers_200_once_for_every_reason_and_creates_no_session(self):
        for username, password in (("alex.carter", "nope"), ("nobody", PASSWORD), ("alex.carter", "")):
            stranger = Client(who=None)
            status, location, page = stranger.form("/login", username=username, password=password)
            self.assertEqual((status, location), (200, None))
            self.assertIn(b'id="login-error"', page)
            self.assertEqual(stranger.call("GET", "/__wta/state")[1]["session"], None)

    def test_next_returns_only_to_a_path_on_this_site(self):
        for wanted, landing in (("/index.html", "/index.html"), ("//elsewhere.test/", "/"),
                                ("https://elsewhere.test/", "/"), ("/login", "/")):
            status, location, _ = Client(who=None).form("/login", username="alex.carter",
                                                        password=PASSWORD, next=wanted)
            self.assertEqual((status, location), (303, landing), wanted)

    def test_signing_out_keeps_the_session_and_signing_in_again_resumes_it(self):
        client = Client()
        client.transfer()
        before = client.call("GET", "/__wta/state")[1]
        self.assertEqual(client.form("/logout")[:2], (303, "/login"))
        after = client.call("GET", "/__wta/state")[1]
        self.assertEqual((after["session"], after["user"], after["sha"]), (before["session"], None, before["sha"]))
        self.assertEqual(client.raw("GET", "/")[0], 303)
        self.assertEqual(client.form("/login", username="alex.carter", password=PASSWORD)[:2], (303, "/"))
        self.assertEqual(client.call("GET", "/__wta/state")[1]["sha"], before["sha"])
        self.assertEqual(client.raw("GET", "/login?next=%2F")[:2], (303, "/"))  # signed in: no form

    def test_view_only_access_sees_two_accounts_and_cannot_move_money(self):
        jamie = Client("jamie.carter")
        self.assertEqual(jamie.call("GET", "/api/me")[1], {"ok": True, "name": "Jamie Carter", "holder": False})
        self.assertEqual(jamie.balances(), {"everyday": 54000, "bills": 18000})
        _, statements = jamie.call("GET", "/api/statements?account=saver&direction=all")
        self.assertEqual({r["account"] for r in statements["rows"]}, {"Everyday", "Bills"})
        before = jamie.call("GET", "/__wta/state")[1]["sha"]
        forbidden = {"ok": False, "reason": "forbidden"}
        self.assertEqual(jamie.call("POST", "/api/drafts", {"from": "everyday", "to": "payee:mum"}), (200, forbidden))
        self.assertEqual(jamie.call("GET", "/api/payees"), (200, forbidden))
        self.assertEqual(jamie.call("PUT", "/api/payees/mum", {"nickname": "X"}), (200, forbidden))
        self.assertEqual(jamie.call("GET", "/__wta/state")[1]["sha"], before)


class TidewaterApi(unittest.TestCase):
    def test_a_new_session_starts_from_the_seed(self):
        client = Client()
        self.assertEqual(client.balances(), {"everyday": 54000, "saver": 2500, "bills": 18000})
        _, overview = client.call("GET", "/api/overview")
        self.assertEqual((overview["scheduled"], len(overview["direct_debits"])), ([], 5))
        _, statements = client.call("GET", "/api/statements?account=all&direction=all")
        self.assertEqual(statements["count"], 8)

    def test_an_immediate_transfer_moves_money_and_is_posted(self):
        client = Client()
        status, result = client.transfer()
        self.assertEqual((status, result["status"], result["id"]), (201, "completed", 1))
        self.assertEqual(client.balances()["everyday"], 54000 - 4200)
        _, statements = client.call("GET", "/api/statements?account=everyday&direction=out")
        self.assertTrue(any("Landlord" in r["description"] and r["amount"] == "-£42.00"
                            for r in statements["rows"]))

    def test_a_transfer_between_own_accounts_credits_the_other_account(self):
        client = Client()
        client.transfer(target="acct:bills", amount="10")
        self.assertEqual(client.balances(), {"everyday": 53000, "saver": 2500, "bills": 19000})

    def test_funds_are_checked_against_the_source_account(self):
        status, body = Client().transfer(source="saver", amount="42")
        self.assertEqual((status, body["field"]), (422, "amount"))

    def test_switching_the_source_account_rechecks_funds(self):
        client = Client()
        _, draft = client.call("POST", "/api/drafts", {"from": "everyday", "to": "payee:landlord"})
        client.call("PUT", f"/api/drafts/{draft['id']}", {"from": "saver", "to": "payee:landlord"})
        status, body = client.call("POST", f"/api/drafts/{draft['id']}/quote",
                                   {"amount": "42", "reference": "", "when": "now"})
        self.assertEqual((status, body["field"]), (422, "amount"))

    def test_future_transfers_are_scheduled_on_business_days_and_move_no_money(self):
        client = Client()
        status, friday = client.transfer(when="next-friday")
        self.assertEqual((status, friday["status"], friday["date"]), (201, "scheduled", "Fri 12 Jun 2026"))
        _, month_end = client.transfer(when="month-end")
        self.assertEqual(month_end["date"], "Tue 30 Jun 2026")
        self.assertEqual(client.balances()["everyday"], 54000)
        _, overview = client.call("GET", "/api/overview")
        self.assertEqual(len(overview["scheduled"]), 2)
        self.assertEqual(client.call("POST", "/api/transfers/1/cancel")[0], 200)
        self.assertEqual(client.call("POST", "/api/transfers/1/cancel")[0], 409)

    def test_a_quote_can_be_authorised_only_once(self):
        client = Client()
        _, draft = client.call("POST", "/api/drafts", {"from": "everyday", "to": "payee:mum"})
        _, quote = client.call("POST", f"/api/drafts/{draft['id']}/quote",
                               {"amount": "5", "reference": "", "when": "now"})
        self.assertEqual(client.call("POST", f"/api/quotes/{quote['id']}/authorise")[0], 201)
        self.assertEqual(client.call("POST", f"/api/quotes/{quote['id']}/authorise")[0], 409)
        self.assertEqual(client.balances()["everyday"], 53500)

    def test_amount_validation(self):
        client = Client()
        _, draft = client.call("POST", "/api/drafts", {"from": "everyday", "to": "payee:mum"})
        for bad in ("", "0", "not-a-number", "999999999", "1.234"):
            status, _ = client.call("POST", f"/api/drafts/{draft['id']}/quote",
                                    {"amount": bad, "reference": "", "when": "now"})
            self.assertEqual(status, 422, bad)

    def test_payees_can_be_added_and_renamed_with_validation(self):
        client = Client()
        self.assertEqual(client.call("POST", "/api/payees",
                                     {"nickname": "Test User Input", "bank": "Northgate",
                                      "account_number": "42"}), (201, {"id": "p1"}))
        self.assertEqual(client.call("POST", "/api/payees",
                                     {"nickname": "X", "bank": "Northgate", "account_number": "1"})[0], 422)
        self.assertEqual(client.call("POST", "/api/payees",
                                     {"nickname": "X", "bank": "Atlantis", "account_number": "12"})[0], 422)
        self.assertEqual(client.call("PUT", "/api/payees/mum", {"nickname": "Mum & Dad"})[0], 200)
        self.assertEqual(client.call("PUT", "/api/payees/mum", {"nickname": ""})[0], 422)
        self.assertEqual(client.call("PUT", "/api/payees/mum", {"nickname": "A" * 2048})[0], 200)
        _, data = client.call("GET", "/api/payees")
        names = [p["nickname"] for p in data["payees"]]
        self.assertIn("A" * 2048, names)
        self.assertEqual(names[-1], "Test User Input")

    def test_statements_filter_by_account_and_direction(self):
        _, saver = Client().call("GET", "/api/statements?account=saver&direction=all")
        self.assertEqual((saver["count"], saver["rows"][0]["description"]), (1, "From Everyday"))
        _, money_in = Client().call("GET", "/api/statements?account=all&direction=in")
        self.assertEqual(money_in["count"], 3)

    def test_sessions_are_isolated_and_the_state_hook_tracks_committed_changes(self):
        first, second = Client(), Client()
        _, before = first.call("GET", "/__wta/state")
        first.transfer()
        _, after = first.call("GET", "/__wta/state")
        _, other = second.call("GET", "/__wta/state")
        self.assertNotEqual(before["sha"], after["sha"])
        self.assertEqual(other["sha"], before["sha"])
        self.assertNotEqual(other["session"], after["session"])


if __name__ == "__main__":
    unittest.main()
