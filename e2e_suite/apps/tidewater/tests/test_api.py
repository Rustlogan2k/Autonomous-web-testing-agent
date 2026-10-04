"""Tidewater's API, against the clean build (E2E_BASE_URL)."""

import http.cookiejar
import json
import os
import unittest
import urllib.error
import urllib.request

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8080/").rstrip("/")


class Client:
    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(BASE + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"} if data else {})
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

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


class TidewaterApi(unittest.TestCase):
    def test_health(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "tidewater"})

    def test_a_new_session_starts_from_the_seed(self):
        self.assertEqual(Client().balances(), {"everyday": 54000, "saver": 2500, "bills": 18000})

    def test_an_immediate_transfer_moves_money_and_is_posted(self):
        client = Client()
        status, result = client.transfer()
        self.assertEqual((status, result["status"]), (201, "completed"))
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
                                      "account_number": "42"})[0], 201)
        self.assertEqual(client.call("POST", "/api/payees",
                                     {"nickname": "X", "bank": "Northgate", "account_number": "1"})[0], 422)
        self.assertEqual(client.call("PUT", "/api/payees/mum", {"nickname": "Mum & Dad"})[0], 200)
        self.assertEqual(client.call("PUT", "/api/payees/mum", {"nickname": ""})[0], 422)
        self.assertEqual(client.call("PUT", "/api/payees/mum", {"nickname": "A" * 2048})[0], 200)
        _, data = client.call("GET", "/api/payees")
        names = [p["nickname"] for p in data["payees"]]
        self.assertIn("A" * 2048, names)
        self.assertIn("Test User Input", names)

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
