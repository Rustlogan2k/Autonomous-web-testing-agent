"""Stackroom's JSON API, run against the clean build at E2E_BASE_URL."""

import http.cookiejar
import json
import os
import unittest
import urllib.error
import urllib.request

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8080/").rstrip("/")


class Browser:
    """One cookie jar, i.e. one stockroom session."""

    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def get(self, path):
        return self._send(urllib.request.Request(BASE + path))

    def post(self, path, payload):
        return self._send(urllib.request.Request(
            BASE + path, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"}))

    def _send(self, request):
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"null")

    def listing(self, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        status, body = self.get(f"/api/listing?{query}")
        assert status == 200, body
        return body

    def stock(self, sku):
        return self.get(f"/api/items/{sku}")[1]["item"]["stock"]

    def log_lines(self):
        return [f"{e['sku']} {e['kind']} {e['before']}>{e['after']} {e['origin']}"
                for e in self.get("/api/log")[1]["entries"]]


class StackroomApi(unittest.TestCase):
    def test_health_and_page_routes(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "stackroom"})
        for path in ("/stock", "/items/SKU-101", "/items/SKU-101/adjust", "/log", "/"):
            with urllib.request.urlopen(BASE + path, timeout=5) as response:
                self.assertEqual(response.status, 200, path)
                self.assertIn(b'id="stackroom-words"', response.read())

    def test_paging_is_ten_rows_and_a_page_past_the_end_is_empty(self):
        client = Browser()
        first = client.listing(page=1)
        self.assertEqual((first["total"], first["pages"], len(first["rows"])), (40, 4, 10))
        self.assertEqual(first["rows"][0]["sku"], "SKU-101")
        last = client.listing(page=4)
        self.assertEqual([r["sku"] for r in last["rows"]][-1], "SKU-140")
        beyond = client.listing(category="Lighting", page=3)
        self.assertEqual((beyond["total"], beyond["page"], beyond["pages"], beyond["rows"]), (7, 3, 1, []))
        self.assertEqual(client.get("/api/listing?page=0")[0], 422)

    def test_filtering_search_and_sorting(self):
        client = Browser()
        self.assertEqual(client.listing(category="Fasteners")["total"], 14)
        self.assertEqual(client.listing(category="Paint")["total"], 7)
        self.assertEqual(client.listing(category="Nonsense")["total"], 40)  # unknown means all
        found = client.listing(q="bulb")
        self.assertEqual([r["sku"] for r in found["rows"]], ["SKU-127", "SKU-128"])
        by_stock = client.listing(sort="stock", dir="asc")
        self.assertEqual([r["sku"] for r in by_stock["rows"][:3]], ["SKU-117", "SKU-131", "SKU-122"])
        descending = client.listing(sort="stock", dir="desc")
        self.assertEqual(descending["rows"][0]["sku"], "SKU-109")
        by_name = client.listing(sort="name", category="Tools")
        self.assertEqual(by_name["rows"][0]["name"], "Adjustable spanner")

    def test_bulk_actions_need_a_selection(self):
        client = Browser()
        status, nothing = client.post("/api/bulk", {"action": "archive", "skus": [], "view": {"page": 1}})
        self.assertEqual((status, nothing["changed"]), (200, 0))
        status, done = client.post("/api/bulk", {"action": "archive", "skus": ["SKU-103", "SKU-111"],
                                                 "view": {"category": "Fasteners", "page": 1}})
        self.assertEqual((status, done["changed"]), (200, 1))  # SKU-111 was already archived
        self.assertEqual(done["listing"]["view"]["category"], "Fasteners")
        restock = client.post("/api/bulk", {"action": "restock", "skus": ["SKU-101", "SKU-102"], "view": {}})[1]
        self.assertEqual(restock["changed"], 2)
        self.assertEqual((client.stock("SKU-101"), client.stock("SKU-102")), (22, 95))
        self.assertIn("SKU-103 archive active>archived bulk", client.log_lines())
        self.assertEqual(client.post("/api/bulk", {"action": "delete", "skus": ["SKU-101"]})[0], 422)

    def test_adjustment_saves_to_the_named_item_and_leaves_a_one_time_notice(self):
        client = Browser()
        status, saved = client.post("/api/adjustments", {
            "sku": "SKU-131", "at": 1, "view": {"sort": "stock", "dir": "asc"}, "new_stock": "42"})
        self.assertEqual((status, saved["sku"], saved["stock"]), (200, "SKU-131", 42))
        self.assertEqual(client.stock("SKU-102"), 85)  # position 1 of the unsorted list is untouched
        first = client.get("/api/items/SKU-131?notice=take")[1]
        again = client.get("/api/items/SKU-131?notice=take")[1]
        self.assertEqual((first["notice"], again["notice"]), ({"sku": "SKU-131"}, None))
        self.assertEqual(client.log_lines()[0], "SKU-131 stock 4>42 adjust")

    def test_every_inline_save_is_audited_even_when_unchanged(self):
        client = Browser()
        for value in ("42", "42", "0", "999999999"):
            status, item = client.post("/api/items/SKU-104/stock", {"stock": value})
            self.assertEqual((status, item["stock"]), (200, int(value)))
        self.assertEqual(client.log_lines()[:4], [
            "SKU-104 stock 0>999999999 inline", "SKU-104 stock 42>0 inline",
            "SKU-104 stock 42>42 inline", "SKU-104 stock 7>42 inline"])

    def test_stock_levels_are_validated(self):
        client = Browser()
        for bad in ("", "not-a-number", "-1", "1.5", "1e3", "1000000000", None):
            self.assertEqual(client.post("/api/items/SKU-101/stock", {"stock": bad})[0], 422, bad)
            status, body = client.post("/api/adjustments", {"sku": "SKU-101", "at": 0, "view": {}, "new_stock": bad})
            self.assertEqual((status, body["field"]), (422, "new_stock"), bad)
        self.assertEqual(client.post("/api/adjustments", {"sku": "SKU-101", "view": {}, "new_stock": "5"})[0], 422)
        self.assertEqual(client.post("/api/items/SKU-999/stock", {"stock": "5"})[0], 404)
        self.assertEqual(client.stock("SKU-101"), 12)
        self.assertEqual(len(client.log_lines()), 2)  # only the seeded history

    def test_sessions_are_isolated_and_the_state_hook_tracks_committed_changes(self):
        first, second = Browser(), Browser()
        _, before = first.get("/__wta/state")
        first.post("/api/items/SKU-101/stock", {"stock": "42"})
        _, after = first.get("/__wta/state")
        _, other = second.get("/__wta/state")
        self.assertNotEqual(before["sha"], after["sha"])
        self.assertEqual(other["sha"], before["sha"])
        self.assertNotEqual(other["session"], after["session"])
        self.assertEqual(second.stock("SKU-101"), 12)

    def test_reading_never_changes_committed_state(self):
        client = Browser()
        _, before = client.get("/__wta/state")
        client.listing(sort="stock", page=2)
        client.get("/api/items/SKU-105?notice=take")
        client.get("/api/log")
        client.post("/api/bulk", {"action": "restock", "skus": [], "view": {}})
        _, after = client.get("/__wta/state")
        self.assertEqual(before["sha"], after["sha"])


if __name__ == "__main__":
    unittest.main()
