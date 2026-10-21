"""Stackroom's HTTP API, run against the clean build at E2E_BASE_URL."""

import http.cookiejar
import json
import os
import unittest
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8080/").rstrip("/")
PASSWORD = "Stackroom-aisle-26"  # the fixture password of every demo account (README)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Browser:
    """One cookie jar, i.e. one browser context; signed in as `username` unless None."""

    def __init__(self, username="dev.malik"):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                                  _NoRedirect())
        if username:
            status, location = self.sign_in(username, PASSWORD)
            assert status == 303 and location == "/stock", (status, location)

    def sign_in(self, username, password, next_path=None):
        fields = {"username": username, "password": password}
        if next_path is not None:
            fields["next"] = next_path
        status, headers, _ = self.raw("POST", "/login", urllib.parse.urlencode(fields).encode(),
                                      "application/x-www-form-urlencoded")
        return status, headers.get("Location")

    def raw(self, method, path, data=None, kind=None):
        request = urllib.request.Request(BASE + path, data=data, method=method,
                                         headers={"Content-Type": kind} if kind else {})
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:  # 3xx are not followed; 4xx land here too
            return error.code, error.headers, error.read()

    def get(self, path):
        status, _, body = self.raw("GET", path)
        return status, json.loads(body or b"null")

    def post(self, path, payload):
        status, _, body = self.raw("POST", path, json.dumps(payload).encode("utf-8"), "application/json")
        return status, json.loads(body or b"null")

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


class SignIn(unittest.TestCase):
    def test_health(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "stackroom"})

    def test_pages_are_behind_the_sign_in_and_return_there_afterwards(self):
        anonymous = Browser(username=None)
        for path in ("/", "/stock?sort=stock", "/items/SKU-101", "/items/SKU-101/adjust", "/log"):
            status, headers, _ = anonymous.raw("GET", path)
            self.assertEqual((status, headers["Location"]),
                             (303, "/login?next=" + urllib.parse.quote(path, safe="")), path)
        status, _, page = anonymous.raw("GET", "/login?next=%2Flog")
        self.assertEqual(status, 200)
        self.assertIn(b'id="login-username"', page)
        self.assertNotIn(b'id="login-error"', page)
        self.assertEqual(anonymous.raw("GET", "/help/sign-in")[0], 200)
        self.assertEqual(anonymous.get("/api/listing"), (200, {"ok": False, "reason": "signed-out"}))
        self.assertEqual(anonymous.sign_in("dev.malik", PASSWORD, "/log"), (303, "/log"))
        status, _, page = anonymous.raw("GET", "/stock")
        self.assertEqual(status, 200)
        self.assertIn("Dev Malik · Stock controller".encode(), page)

    def test_a_refused_sign_in_is_a_200_page_with_one_message_and_creates_nothing(self):
        for username, password in (("dev.malik", "wrong"), ("nobody", PASSWORD), ("dev.malik", "")):
            client = Browser(username=None)
            status, headers, page = client.raw(
                "POST", "/login", urllib.parse.urlencode({"username": username, "password": password}).encode(),
                "application/x-www-form-urlencoded")
            self.assertEqual(status, 200, (username, password))
            self.assertIn(b'id="login-error"', page)
            self.assertIsNone(headers.get("Set-Cookie"))
            self.assertEqual(client.get("/__wta/state")[1]["session"], None)

    def test_next_only_ever_names_a_path_on_this_site(self):
        for target in ("//evil.example/", "https://evil.example/", "/login", "\\\\evil", ""):
            self.assertEqual(Browser(username=None).sign_in("dev.malik", PASSWORD, target), (303, "/stock"))

    def test_signed_in_the_sign_in_page_redirects(self):
        client = Browser()
        status, headers, _ = client.raw("GET", "/login?next=%2Flog")
        self.assertEqual((status, headers["Location"]), (303, "/log"))

    def test_sign_out_keeps_the_data_and_signing_in_again_resumes_it(self):
        client = Browser()
        client.post("/api/items/SKU-101/stock", {"stock": "42"})
        before = client.get("/__wta/state")[1]
        status, headers, _ = client.raw("POST", "/logout", b"", "application/x-www-form-urlencoded")
        self.assertEqual((status, headers["Location"]), (303, "/login"))
        signed_out = client.get("/__wta/state")[1]
        self.assertEqual((signed_out["session"], signed_out["user"]), (before["session"], None))
        self.assertEqual(client.raw("GET", "/stock")[0], 303)
        self.assertEqual(client.sign_in("dev.malik", PASSWORD), (303, "/stock"))
        self.assertEqual(client.get("/__wta/state")[1]["sha"], before["sha"])
        self.assertEqual(client.stock("SKU-101"), 42)

    def test_a_viewer_reads_but_every_change_is_refused_with_200(self):
        viewer = Browser("lee.ward")
        self.assertEqual(viewer.listing()["total"], 40)
        before = viewer.get("/__wta/state")[1]["sha"]
        for path, payload in (("/api/items/SKU-101/stock", {"stock": "5"}),
                              ("/api/bulk", {"action": "archive", "skus": ["SKU-101"], "view": {}}),
                              ("/api/adjustments", {"sku": "SKU-101", "at": 0, "view": {}, "new_stock": "5"})):
            self.assertEqual(viewer.post(path, payload), (200, {"ok": False, "reason": "forbidden"}), path)
        self.assertEqual(viewer.get("/__wta/state")[1]["sha"], before)
        status, _, page = viewer.raw("GET", "/items/SKU-101/adjust")
        self.assertEqual(status, 200)
        self.assertIn(b'"role": "viewer"', page)


class StackroomApi(unittest.TestCase):
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
        self.assertEqual(client.listing(q="%")["total"], 0)  # typed text, never a pattern
        by_stock = client.listing(sort="stock", dir="asc")
        self.assertEqual([r["sku"] for r in by_stock["rows"][:3]], ["SKU-117", "SKU-131", "SKU-122"])
        descending = client.listing(sort="stock", dir="desc")
        self.assertEqual(descending["rows"][0]["sku"], "SKU-109")
        by_name = client.listing(sort="name", category="Tools")
        self.assertEqual(by_name["rows"][0]["name"], "Adjustable spanner")
        self.assertEqual(client.listing(sort="sku", dir="desc")["rows"][0]["sku"], "SKU-140")

    def test_ties_are_broken_by_sku_ascending_in_both_directions(self):
        client = Browser()
        client.post("/api/items/SKU-105/stock", {"stock": "3"})  # now ties SKU-117 at 3
        rising = [r["sku"] for r in client.listing(sort="stock", dir="asc")["rows"][:2]]
        falling = [r["sku"] for r in client.listing(sort="stock", dir="desc", page=4)["rows"][-2:]]
        self.assertEqual((rising, falling), (["SKU-105", "SKU-117"], ["SKU-105", "SKU-117"]))

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

    def test_restock_holds_an_item_at_the_stock_ceiling(self):
        client = Browser()
        self.assertEqual(client.post("/api/items/SKU-101/stock", {"stock": "999999999"})[0], 200)
        status, done = client.post("/api/bulk", {"action": "restock", "skus": ["SKU-101"], "view": {}})
        self.assertEqual((status, done["changed"]), (200, 1))
        self.assertEqual(client.stock("SKU-101"), 999999999)

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
        self.assertEqual(first["item"]["history"][0]["by"], "Dev Malik")

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

    def test_the_item_page_shows_supplier_and_what_is_on_order(self):
        item = Browser().get("/api/items/SKU-117")[1]["item"]
        self.assertEqual((item["supplier"], item["lead_days"], item["reorder_level"]), ("Ironside Tools", 5, 5))
        self.assertEqual((item["on_order"], item["on_order_due"]), (6, "2026-06-13"))
        self.assertEqual(Browser().get("/api/items/SKU-137")[1]["item"]["on_order"], 0)  # cancelled order

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
