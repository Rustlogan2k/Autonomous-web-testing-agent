"""Bagstreet's server, driven through its HTML forms against the clean build (E2E_BASE_URL)."""

import http.cookiejar
import json
import os
import re
import unittest
import urllib.parse
import urllib.request
from html import unescape

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8080/").rstrip("/")


class Browser:
    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.url = ""

    def get(self, path):
        with self.opener.open(BASE + path, timeout=10) as response:
            self.url = response.geturl()
            return response.status, unescape(response.read().decode("utf-8"))

    def post(self, path, fields):
        data = urllib.parse.urlencode(fields).encode()
        with self.opener.open(urllib.request.Request(BASE + path, data=data), timeout=10) as response:
            self.url = response.geturl()  # redirects are followed (post/redirect/get)
            return response.status, unescape(response.read().decode("utf-8"))

    def state(self):
        return json.loads(self.get("/__wta/state")[1])

    @staticmethod
    def region(html, ident):
        match = re.search(rf'id="{ident}"[^>]*>(.*?)</', html, re.S)
        return re.sub(r"<[^>]+>", " ", match.group(1)).strip() if match else None


class BagstreetForms(unittest.TestCase):
    def setUp(self):
        self.b = Browser()

    def add(self, slug="lantern", variant="small", qty="1"):
        return self.b.post("/bag/add", {"slug": slug, "variant": variant, "qty": qty})

    def test_health_and_catalogue(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "bagstreet"})
        _, html = self.b.get("/search?category=kitchen&sort=price-desc")
        self.assertLess(html.index("Stovetop kettle"), html.index("Linen tea towel"))
        self.assertNotIn("Storm lantern", html)

    def test_search_falls_back_to_everything(self):
        _, html = self.b.get("/search?q=search+query")
        self.assertIn('id="no-exact"', html)
        self.assertEqual(html.count('class="result"'), 6)
        _, html = self.b.get("/search?q=kettle")
        self.assertEqual(html.count('class="result"'), 1)

    def test_adding_merges_lines_and_totals_follow_quantities(self):
        self.add(qty="2")
        self.add(qty="1")
        self.add(variant="large")
        _, html = self.b.get("/bag")
        self.assertEqual(self.b.region(html, "bag-subtotal"), "£104.00")  # 3 x 24 + 32
        self.b.post("/bag/update", {"qty_1": "1", "qty_2": "0"})
        _, html = self.b.get("/bag")
        self.assertEqual(self.b.region(html, "bag-subtotal"), "£24.00")

    def test_the_percentage_voucher_follows_the_bag(self):
        self.add()
        self.b.post("/bag/voucher", {"voucher": "welcome10"})
        _, html = self.b.get("/bag")
        self.assertEqual(self.b.region(html, "bag-discount"), "−£2.40")
        self.b.post("/bag/update", {"qty_1": "42"})
        _, html = self.b.get("/bag")
        self.assertEqual(self.b.region(html, "bag-subtotal"), "£1,008.00")
        self.assertEqual(self.b.region(html, "bag-discount"), "−£100.80")

    def test_the_bundle_voucher_needs_fifty_pounds(self):
        self.add()
        self.b.post("/bag/voucher", {"voucher": "bundle5"})
        _, html = self.b.get("/bag")
        self.assertEqual(self.b.region(html, "bag-discount"), "−£0.00")
        self.b.post("/bag/update", {"qty_1": "3"})
        _, html = self.b.get("/bag")
        self.assertEqual(self.b.region(html, "bag-discount"), "−£5.00")

    def test_quantity_rules(self):
        for bad in ("", "0", "100", "999999999", "not-a-number"):
            self.add(qty=bad)
            self.assertIn("/p/lantern", self.b.url, bad)
        self.add()
        self.b.post("/bag/update", {"qty_1": "abc"})
        self.assertEqual(self.b.state()["committed"]["lines"][0]["qty"], 1)

    def test_delivery_needs_an_address_for_standard_and_express(self):
        self.add()
        for method in ("standard", "express"):
            self.b.post("/checkout/delivery", {"method": method, "full_name": "", "address": "",
                                               "action": "review"})
            self.assertTrue(self.b.url.endswith("/checkout/delivery"), method)
        self.b.post("/checkout/delivery", {"method": "express", "full_name": "Ada", "address": "1 Quay St",
                                           "action": "review"})
        self.assertTrue(self.b.url.endswith("/checkout/review"))
        _, html = self.b.post("/checkout/place", {"method": "express"})
        self.assertIn("next day", self.b.region(html, "confirm-method"))
        self.assertEqual(self.b.region(html, "confirm-total"), "£31.95")
        self.assertIn("Order placed: BS-1001", self.b.region(html, "notice"))
        _, again = self.b.get(urllib.parse.urlsplit(self.b.url).path)
        self.assertNotIn('id="notice"', again)  # the banner is shown once

    def test_collection_needs_no_address_and_names_the_shop(self):
        self.add(slug="hammock", variant="std")
        self.b.post("/checkout/delivery", {"method": "collect"})
        _, html = self.b.get("/checkout/delivery")
        self.assertNotIn('id="address-section"', html)
        self.b.post("/checkout/delivery", {"method": "collect", "action": "review"})
        _, html = self.b.post("/checkout/place", {"method": "collect"})
        self.assertIn("14 Mill Lane", self.b.region(html, "confirm-address"))
        self.assertEqual(self.b.state()["committed"]["lines"], [])

    def test_the_review_posts_the_chosen_method(self):
        self.add()
        self.b.post("/checkout/delivery", {"method": "express", "full_name": "Ada", "address": "1 Quay St",
                                           "action": "review"})
        _, html = self.b.get("/checkout/review")
        self.assertIn('name="method" value="express"', html)

    def test_sessions_are_isolated(self):
        other = Browser()
        before = other.state()["sha"]
        self.add()
        self.assertEqual(other.state()["sha"], before)
        self.assertNotEqual(self.b.state()["sha"], before)


if __name__ == "__main__":
    unittest.main()
