"""Bagstreet's server, driven through its HTML forms against the clean build (E2E_BASE_URL),
signed in through the sign-in form like a browser. The server runs with WTA_TEST_HOOKS=1."""

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
PASSWORD = "Bagstreet-shop-26"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Browser:
    """One browser context: its own cookie jar, following redirects like a browser."""

    def __init__(self, follow=True):
        self.jar = http.cookiejar.CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(self.jar)] + ([] if follow else [NoRedirect()])
        self.opener = urllib.request.build_opener(*handlers)
        self.url = ""

    def open(self, request):
        try:
            with self.opener.open(request, timeout=10) as response:
                self.url = response.geturl()
                return response.status, unescape(response.read().decode("utf-8")), response.headers
        except urllib.error.HTTPError as error:  # a 3xx when not following, or a 404 page
            self.url = error.geturl()
            return error.code, unescape(error.read().decode("utf-8")), error.headers

    def get(self, path):
        status, html, _ = self.open(BASE + path)
        return status, html

    def post(self, path, fields):
        data = urllib.parse.urlencode(fields, doseq=True).encode()
        status, html, _ = self.open(urllib.request.Request(BASE + path, data=data))
        return status, html

    def sign_in(self, username="jo.marsh", password=PASSWORD):
        return self.post("/login", {"username": username, "password": password, "next": "/"})

    def state(self):
        return json.loads(self.get("/__wta/state")[1])

    @staticmethod
    def region(html, ident):
        """The text of an element, read up to its closing tag as the oracle reads a region."""
        match = re.search(rf'<([a-zA-Z][\w-]*)[^>]*\bid="{re.escape(ident)}"[^>]*>(.*?)</\1>', html, re.S)
        return " ".join(re.sub(r"<[^>]+>", " ", match.group(2)).split()) if match else None


def signed_in(username="jo.marsh"):
    browser = Browser()
    browser.sign_in(username)
    return browser


class SignIn(unittest.TestCase):
    def test_health(self):
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True, "app": "bagstreet"})

    def test_signed_out_pages_go_to_sign_in_and_create_nothing(self):
        raw = Browser(follow=False)
        status, _, headers = raw.open(BASE + "/bag?x=1")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/login?next=%2Fbag%3Fx%3D1")
        self.assertIsNone(headers.get("Set-Cookie"))
        self.assertEqual(raw.state(), {"session": None, "user": None, "committed": None, "sha": None})
        self.assertEqual(Browser().get("/login/forgot")[0], 200)

    def test_refusal_is_generic_and_sets_no_cookie(self):
        for username, password in (("jo.marsh", "wrong"), ("nobody.here", PASSWORD), ("jo.marsh", "")):
            browser = Browser()
            status, html = browser.sign_in(username, password)
            self.assertEqual(status, 200)
            self.assertIn("don't match", browser.region(html, "login-error"))
            self.assertEqual(len(browser.jar), 0)

    def test_sign_in_honours_next_and_sign_out_keeps_the_data(self):
        browser = Browser()
        browser.post("/login", {"username": "jo.marsh", "password": PASSWORD, "next": "/orders"})
        self.assertTrue(browser.url.endswith("/orders"))
        self.assertEqual(browser.region(browser.get("/")[1], "signed-in-as"), "Jo Marsh · Customer")
        browser.post("/bag/add", {"slug": "kettle", "variant": "std", "qty": "1"})
        before = browser.state()
        self.assertEqual(before["user"], "jo.marsh")
        browser.post("/logout", {})
        self.assertTrue(browser.url.endswith("/login"))
        self.assertIsNone(browser.state()["user"])
        browser.sign_in()
        after = browser.state()
        self.assertEqual((after["session"], after["sha"]), (before["session"], before["sha"]))
        elsewhere = Browser()
        elsewhere.post("/login", {"username": "jo.marsh", "password": PASSWORD, "next": "//elsewhere.example/"})
        self.assertEqual(urllib.parse.urlsplit(elsewhere.url).path, "/")

    def test_a_new_browser_starts_from_the_seed(self):
        first, second = signed_in(), signed_in()
        first.post("/bag/add", {"slug": "kettle", "variant": "std", "qty": "1"})
        self.assertNotEqual(first.state()["sha"], second.state()["sha"])
        self.assertEqual(second.state()["sha"], signed_in().state()["sha"])
        committed = second.state()["committed"]
        self.assertEqual(sorted(o["_id"] for o in committed["orders"]),
                         ["BS-0901", "BS-0902", "BS-0903", "BS-0904", "BS-0905", "BS-0906"])
        self.assertNotIn('"pw"', json.dumps(committed))

    def test_staff_page_is_refused_to_customers(self):
        status, html = signed_in("sam.adeyemi").get("/staff/orders")
        self.assertEqual(status, 200)
        self.assertIn("staff", Browser.region(html, "access-notice"))
        self.assertNotIn('id="nav-staff"', html)


class Catalogue(unittest.TestCase):
    def setUp(self):
        self.b = signed_in()

    def test_sort_and_pages(self):
        _, html = self.b.get("/search?category=kitchen&sort=price-desc")
        self.assertLess(html.index("Bread crock"), html.index("Stovetop kettle"))
        self.assertLess(html.index("Stovetop kettle"), html.index("Linen tea towel"))
        self.assertNotIn("Storm lantern", html)
        _, html = self.b.get("/search?category=outdoor&sort=price-desc")
        self.assertEqual([self.b.region(html, f"result-{i}-price") for i in (1, 2, 3, 4)],
                         ["£44.00", "£28.00", "£26.00", "£19.00"])
        _, html = self.b.get("/search?category=all")
        self.assertEqual((self.b.region(html, "results-page"), self.b.region(html, "results-pages")), ("1", "2"))
        self.assertEqual(html.count('class="result"'), 8)
        self.assertIn('id="results-next"', html)
        _, html = self.b.get("/search?category=all&page=2")
        self.assertEqual(html.count('class="result"'), 6)
        self.assertIn("Folding stool", html)
        self.assertNotIn('id="results-next"', html)
        self.assertIn('name="page" value="1"', html, "a new filter starts at page 1")

    def test_search_is_literal_and_falls_back_to_everything(self):
        _, html = self.b.get("/search?q=search+query")
        self.assertIn('id="no-exact"', html)
        self.assertEqual(self.b.region(html, "results-pages"), "2")
        _, html = self.b.get("/search?q=kettle")
        self.assertEqual(html.count('class="result"'), 1)
        _, html = self.b.get("/search?q=.%2A")
        self.assertIn('id="no-exact"', html, "typed text is not a pattern")

    def test_product_page_has_details_and_reviews(self):
        _, html = self.b.get("/p/hammock")
        self.assertIn("150 kg", html)
        self.assertIn("Took two of us at once", self.b.region(html, "review-list"))
        self.assertIn('id="panel-reviews" class="panel" role="tabpanel" aria-labelledby="tab-reviews" hidden', html)
        _, html = self.b.get("/p/hammock?tab=reviews")
        self.assertIn('id="panel-details" class="panel" role="tabpanel" aria-labelledby="tab-details" hidden', html)


class BagAndCheckout(unittest.TestCase):
    def setUp(self):
        self.b = signed_in()

    def add(self, slug="lantern", variant="small", qty="1"):
        return self.b.post("/bag/add", {"slug": slug, "variant": variant, "qty": qty})

    def test_voucher_follows_the_bag(self):
        self.add("kettle", "std")
        _, html = self.b.post("/bag/voucher", {"voucher": "welcome10"})
        self.assertEqual(self.b.region(html, "bag-discount"), "−£3.60")
        _, html = self.b.post("/bag/update", {"qty_1": "2"})
        self.assertEqual(self.b.region(html, "bag-subtotal"), "£72.00")
        self.assertEqual(self.b.region(html, "bag-discount"), "−£7.20")
        _, html = self.b.post("/bag/update", {"qty_1": "100"})
        self.assertIn("1 to 99", self.b.region(html, "bag-error"))

    def test_bulk_remove(self):
        self.add()
        self.add("kettle", "std")
        self.add("tea-towel", "std")
        _, html = self.b.post("/bag/remove-selected", {"line": ["1", "3"]})
        self.assertIn("(2)", self.b.region(html, "notice"))
        bags = {b["_id"]: b for b in self.b.state()["committed"]["bags"]}
        self.assertEqual([line["product_id"] for line in bags["jo"]["lines"]], ["kettle"])
        _, html = self.b.post("/bag/remove-selected", {})
        self.assertIn("Tick", self.b.region(html, "bag-error"))

    def test_express_needs_an_address_and_the_order_keeps_its_method(self):
        self.add()
        _, html = self.b.post("/checkout/delivery", {"method": "express", "action": "review"})
        self.assertIn("name and address", self.b.region(html, "delivery-error"))
        self.b.post("/checkout/delivery", {"method": "express", "full_name": "Jo Marsh", "address": "1 Road",
                                           "action": "review"})
        _, html = self.b.post("/checkout/place", {"method": "express"})
        self.assertEqual(self.b.region(html, "order-number"), "BS-1001")
        self.assertIn("Express", self.b.region(html, "confirm-method"))
        self.assertEqual(self.b.region(html, "confirm-total"), "£31.95")
        self.assertIn("BS-1001", self.b.region(html, "notice"))
        _, html = self.b.get("/orders/BS-1001")
        self.assertIsNone(self.b.region(html, "notice"), "the placed banner shows once")
        self.assertEqual(self.b.get("/orders/BS-0902")[0], 404, "another customer's order is not shown")

    def test_saved_addresses_are_offered_but_never_prefilled(self):
        self.add()
        _, html = self.b.get("/checkout/delivery")
        self.assertIn('data-address="Unit 4, Calder Works, Todmorden OL14 5RT"', html)
        self.assertIn('id="full-name" name="full_name" type="text" value=""', html)


class AccountAndReviews(unittest.TestCase):
    def setUp(self):
        self.b = signed_in()

    def test_address_book_crud(self):
        fields = {"label": "Mum's", "name": "Ann Marsh", "line1": "9 Hill Top", "town": "Heptonstall",
                  "postcode": "HX7 7LT"}
        _, html = self.b.post("/account/addresses/new", fields)
        self.assertIn("9 Hill Top, Heptonstall HX7 7LT", self.b.region(html, "address-list"))
        self.assertIn('id="edit-address-addr-1"', html)
        _, html = self.b.post("/account/addresses/addr-1/edit", {**fields, "town": "Blackshaw Head"})
        self.assertIn("Blackshaw Head", self.b.region(html, "address-list"))
        _, html = self.b.post("/account/addresses/addr-1/edit", {**fields, "postcode": ""})
        self.assertIn("every part", self.b.region(html, "address-form-error"))
        _, html = self.b.post("/account/addresses/new", {**fields, "postcode": "A" * 2048})
        self.assertIn("longer", self.b.region(html, "address-form-error"))
        _, html = self.b.post("/account/addresses/addr-1/delete", {})
        self.assertNotIn("Ann Marsh", self.b.region(html, "address-list"))
        self.b.post("/account/addresses/addr-1/delete", {})
        self.assertTrue(self.b.url.endswith("/account/addresses"), "deleting twice is harmless")

    def test_review_crud(self):
        _, html = self.b.post("/p/lantern/reviews", {"stars": "4", "text": "Bright enough to read by."})
        self.assertIn("4/5 Jo Marsh: Bright enough to read by.", self.b.region(html, "review-list"))
        _, html = self.b.post("/p/lantern/reviews", {"stars": "5", "text": "Again"})
        self.assertIn("already", self.b.region(html, "review-error"))
        _, html = self.b.post("/p/lantern/review/edit", {"stars": "2", "text": "Dimmer than I hoped."})
        self.assertIn("2/5 Jo Marsh: Dimmer than I hoped.", self.b.region(html, "review-list"))
        _, html = self.b.post("/p/lantern/review/edit", {"stars": "9", "text": "x"})
        self.assertIn("1 to 5", self.b.region(html, "review-error"))
        _, html = self.b.post("/p/lantern/review/delete", {})
        self.assertNotIn("Jo Marsh", self.b.region(html, "review-list"))
        self.assertIn("Stayed lit", self.b.region(html, "review-list"))

    def test_staff_dispatch(self):
        staff = signed_in("priya.shah")
        _, html = staff.get("/staff/orders")
        self.assertIn('id="pick-order-BS-0904"', html)
        self.assertNotIn("BS-0901", html, "the default list is what is left to send")
        _, html = staff.post("/staff/orders/dispatch", {"order": ["BS-0904", "BS-0906"]})
        self.assertIn("(2)", staff.region(html, "notice"))
        self.assertNotIn("BS-0904", html)
        orders = {o["_id"]: o for o in staff.state()["committed"]["orders"]}
        self.assertEqual(orders["BS-0904"]["status"], "dispatched")
        self.assertEqual(orders["BS-0904"]["events"][-1],
                         {"kind": "dispatched", "by": "priya", "at": "2026-06-10T09:00:00"})
        self.assertEqual(self.b.post("/staff/orders/dispatch", {"order": ["BS-0905"]})[0], 200)
        self.assertEqual({o["_id"]: o["status"] for o in self.b.state()["committed"]["orders"]}["BS-0905"], "placed")


if __name__ == "__main__":
    unittest.main()
