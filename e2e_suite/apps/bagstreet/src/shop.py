"""What the shop does, on one session's database: the catalogue, the bag, orders, the address
book, reviews and the staff's dispatch list. Every function takes that database (`db`)."""

from __future__ import annotations

import re

ASCENDING, DESCENDING = 1, -1  # MongoDB sort directions

METHOD_FEES = {"standard": 395, "express": 795, "collect": 0}
VOUCHER_CODES = ("none", "welcome10", "freeship", "bundle5")
PAGE_SIZE = 8
MAX_QTY = 99
MAX_LINES = 50
MAX_ADDRESSES = 10
REVIEW_LIMIT = 2000
TEXT_LIMIT = 10000  # what a delivery label can carry
ADDRESS_LIMITS = {"label": 30, "name": 80, "line1": 120, "town": 60, "postcode": 20}
STARS = (5, 4, 3, 2, 1)
SORTS = ("relevance", "price-asc", "price-desc", "name")
STATUSES = ("placed", "dispatched")
_LISTING = {"reviews": 0, "attributes": 0}


class Refused(Exception):
    """A request the shop turns down; `key` names the message in the vocabulary."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


# -- the catalogue ---------------------------------------------------------------------------


def categories(db) -> list[dict]:
    return list(db.categories.find({}, sort=[("position", ASCENDING)]))


def from_price(product: dict) -> int:
    """The price a listing shows: the cheapest variant's."""
    return min(v["price_pence"] for v in product["variants"])


def catalogue(db, category: str = "all") -> list[dict]:
    """The products of one department (or all), in the shop's own order."""
    rank = {c["_id"]: c["position"] for c in categories(db)}
    query = {} if category == "all" else {"category_id": category}
    found = db.products.find(query, _LISTING)
    return sorted(found, key=lambda p: (rank.get(p["category_id"], 0), p["position"], p["_id"]))


def search(db, q: str, category: str, sort: str) -> tuple[list[dict], bool]:
    """Products for a search; the flag says whether the words matched anything.

    When nothing matches, the department's products are shown instead of an empty page."""
    items = catalogue(db, category)
    exact = True
    if q:
        words = re.compile(re.escape(q), re.IGNORECASE)  # typed text is data, not a pattern
        matched = [p for p in items if words.search(p["name"]) or words.search(p["blurb"])]
        exact = bool(matched)
        items = matched or items
    if sort in ("price-asc", "price-desc"):
        items.sort(key=from_price, reverse=sort == "price-desc")
    elif sort == "name":
        items.sort(key=lambda p: (p["name"], p["_id"]))
    return items, exact


def page_of(items: list, page: int) -> tuple[list, int]:
    """One page of a list and how many pages there are (an empty list is one empty page)."""
    pages = max(1, -(-len(items) // PAGE_SIZE))
    start = (page - 1) * PAGE_SIZE
    return items[start:start + PAGE_SIZE], pages


def product(db, slug: str) -> dict | None:
    return db.products.find_one({"_id": slug})


def variant_of(item: dict | None, code: str) -> dict | None:
    if item is None:
        return None
    return next((v for v in item["variants"] if v["code"] == code), None)


# -- the bag ---------------------------------------------------------------------------------


def bag(db, user_id: str) -> dict:
    return db.bags.find_one({"_id": user_id})


def bag_lines(db, user_id: str) -> list[dict]:
    """The bag's lines with what the pages show about each."""
    lines = bag(db, user_id)["lines"]
    names = {p["_id"]: p for p in db.products.find({"_id": {"$in": [line["product_id"] for line in lines]}},
                                                    {"name": 1, "variants.code": 1, "variants.label": 1})}
    out = []
    for line in lines:
        item = names[line["product_id"]]
        variant = variant_of(item, line["variant"])
        out.append({**line, "name": item["name"], "variant_label": variant["label"]})
    return out


def bag_count(db, user_id: str) -> int:
    found = db.bags.find_one({"_id": user_id}, {"lines.qty": 1})
    return sum(line["qty"] for line in found["lines"]) if found else 0


def parse_qty(raw: str | None) -> int | None:
    text = (raw or "").strip()
    if not re.fullmatch(r"\d{1,3}", text):
        return None
    value = int(text)
    return value if value <= MAX_QTY else None


def voucher_discount(code: str | None, subtotal: int) -> int:
    if code == "welcome10":
        return (subtotal + 5) // 10
    if code == "bundle5":
        return 500 if subtotal >= 5000 else 0
    return 0


def subtotal_of(lines: list[dict]) -> int:
    return sum(line["unit_price_pence"] * line["qty"] for line in lines)


def refresh_voucher(db, bag_id: str) -> None:
    """Recompute the stored voucher discount from the bag as it is now."""
    current = bag(db, bag_id)
    if current["voucher"] is not None:
        discount = voucher_discount(current["voucher"]["code"], subtotal_of(current["lines"]))
        db.bags.update_one({"_id": bag_id}, {"$set": {"voucher.discount_pence": discount}})


def totals(current: dict, method: str | None = None) -> dict:
    subtotal = subtotal_of(current["lines"])
    voucher = current["voucher"]
    discount = voucher["discount_pence"] if voucher else 0
    delivery = 0 if voucher and voucher["code"] == "freeship" else METHOD_FEES[method or current["method"]]
    before = subtotal - discount
    return {"subtotal": subtotal, "discount": discount, "before_delivery": before, "delivery": delivery,
            "total": before + delivery, "voucher": voucher["code"] if voucher else "none"}


def add_to_bag(db, user_id: str, slug: str, code: str, qty: int) -> None:
    item = product(db, slug)
    variant = variant_of(item, code)
    lines = bag(db, user_id)["lines"]
    existing = next((line for line in lines if line["product_id"] == slug and line["variant"] == code), None)
    if existing is not None:
        if existing["qty"] + qty > MAX_QTY:
            raise Refused("msg.qty_rule")
        db.bags.update_one({"_id": user_id, "lines.n": existing["n"]}, {"$inc": {"lines.$.qty": qty}})
    else:
        if len(lines) >= MAX_LINES:
            raise Refused("msg.bag_full")
        n = max((line["n"] for line in lines), default=0) + 1
        db.bags.update_one({"_id": user_id}, {"$push": {"lines": {
            "n": n, "product_id": slug, "variant": code, "qty": qty, "unit_price_pence": variant["price_pence"]}}})
    refresh_voucher(db, user_id)


def set_quantities(db, user_id: str, changes: dict[int, int]) -> None:
    """Apply edited quantities; a quantity of 0 takes the line out."""
    lines = []
    for line in bag(db, user_id)["lines"]:
        qty = changes.get(line["n"], line["qty"])
        if qty:
            lines.append({**line, "qty": qty})
    db.bags.update_one({"_id": user_id}, {"$set": {"lines": lines}})


def remove_lines(db, user_id: str, numbers: set[int]) -> int:
    """Take lines out of the bag; returns how many went."""
    before = bag(db, user_id)["lines"]
    kept = [line for line in before if line["n"] not in numbers]
    if len(kept) != len(before):
        db.bags.update_one({"_id": user_id}, {"$set": {"lines": kept}})
        refresh_voucher(db, user_id)
    return len(before) - len(kept)


def use_voucher(db, user_id: str, code: str) -> None:
    voucher = None if code == "none" else {"code": code, "discount_pence": 0}
    db.bags.update_one({"_id": user_id}, {"$set": {"voucher": voucher}})
    refresh_voucher(db, user_id)


def save_delivery(db, user_id: str, method: str, details: tuple[str, str] | None) -> None:
    change = {"method": method}
    if details is not None:
        change["full_name"], change["address"] = details
    db.bags.update_one({"_id": user_id}, {"$set": change})


# -- orders ----------------------------------------------------------------------------------


def place_order(db, user_id: str, method: str, now: str) -> str:
    """Turn the bag into an order with the delivery `method`; returns the order number."""
    current = bag(db, user_id)
    lines = bag_lines(db, user_id)
    sums = totals(current, method)
    n = 1000 + db.counters.find_one_and_update({"_id": "seq"}, {"$inc": {"order": 1}})["order"]
    delivered = method != "collect"
    db.orders.insert_one({
        "_id": f"BS-{n}", "customer_id": user_id, "n": n, "method": method,
        "full_name": current["full_name"] if delivered else "", "address": current["address"] if delivered else "",
        "store_id": current["store_id"],
        "lines": [{"n": line["n"], "product_id": line["product_id"], "name": line["name"],
                   "variant": line["variant"], "variant_label": line["variant_label"], "qty": line["qty"],
                   "unit_price_pence": line["unit_price_pence"]} for line in lines],
        "totals": {k: sums[k] for k in ("subtotal", "discount", "delivery", "total")},
        "status": "placed", "events": [{"kind": "placed", "by": user_id, "at": now}]})
    db.bags.update_one({"_id": user_id}, {"$set": {"lines": [], "voucher": None}})
    return f"BS-{n}"


def orders_of(db, user_id: str) -> list[dict]:
    return list(db.orders.find({"customer_id": user_id}, sort=[("n", DESCENDING)]))


def order(db, number: str) -> dict | None:
    return db.orders.find_one({"_id": number})


def all_orders(db, status: str) -> list[dict]:
    query = {} if status == "all" else {"status": status}
    return list(db.orders.find(query, sort=[("n", DESCENDING)]))


def dispatch(db, numbers: list[str], by: str, now: str) -> int:
    """Mark placed orders dispatched; returns how many changed."""
    result = db.orders.update_many({"_id": {"$in": numbers}, "status": "placed"},
                                   {"$set": {"status": "dispatched"},
                                    "$push": {"events": {"kind": "dispatched", "by": by, "at": now}}})
    return result.modified_count


def store(db, store_id: str) -> dict:
    return db.stores.find_one({"_id": store_id})


# -- the address book ------------------------------------------------------------------------


def addresses(db, user_id: str) -> list[dict]:
    return db.users.find_one({"_id": user_id}, {"addresses": 1})["addresses"]


def address_problem(fields: dict[str, str]) -> str | None:
    """The vocabulary key of what is wrong with an address, or None."""
    if any(not fields[name] for name in ADDRESS_LIMITS):
        return "msg.address_fields"
    if any(len(fields[name]) > limit for name, limit in ADDRESS_LIMITS.items()):
        return "msg.too_long"
    return None


def add_address(db, user_id: str, fields: dict[str, str]) -> str:
    if len(addresses(db, user_id)) >= MAX_ADDRESSES:
        raise Refused("msg.address_book_full")
    number = db.counters.find_one_and_update({"_id": "seq"}, {"$inc": {"address": 1}})["address"]
    key = f"addr-{number}"
    db.users.update_one({"_id": user_id}, {"$push": {"addresses": {"key": key, **fields}}})
    return key


def update_address(db, user_id: str, key: str, fields: dict[str, str]) -> bool:
    change = {f"addresses.$.{name}": value for name, value in fields.items()}
    return db.users.update_one({"_id": user_id, "addresses.key": key}, {"$set": change}).matched_count == 1


def delete_address(db, user_id: str, key: str) -> bool:
    return db.users.update_one({"_id": user_id}, {"$pull": {"addresses": {"key": key}}}).modified_count == 1


def one_line(address: dict) -> str:
    return f"{address['line1']}, {address['town']} {address['postcode']}"


# -- reviews ---------------------------------------------------------------------------------


def review_problem(stars: str, text: str) -> str | None:
    if stars not in {str(s) for s in STARS}:
        return "msg.stars_rule"
    if not text:
        return "msg.review_empty"
    if len(text) > REVIEW_LIMIT:
        return "msg.review_long"
    return None


def own_review(item: dict, user_id: str) -> dict | None:
    return next((r for r in item["reviews"] if r["author_id"] == user_id), None)


def post_review(db, slug: str, user_id: str, stars: int, text: str) -> None:
    item = product(db, slug)
    if own_review(item, user_id) is not None:
        raise Refused("msg.review_exists")
    n = max((r["n"] for r in item["reviews"]), default=0) + 1
    db.products.update_one({"_id": slug}, {"$push": {"reviews": {
        "n": n, "author_id": user_id, "stars": stars, "text": text}}})


def edit_review(db, slug: str, user_id: str, stars: int, text: str) -> bool:
    return db.products.update_one({"_id": slug, "reviews.author_id": user_id},
                                  {"$set": {"reviews.$.stars": stars, "reviews.$.text": text}}).matched_count == 1


def delete_review(db, slug: str, user_id: str) -> bool:
    return db.products.update_one({"_id": slug}, {"$pull": {"reviews": {"author_id": user_id}}}).modified_count == 1


# -- what the tests compare ------------------------------------------------------------------


def committed(db) -> dict:
    """Business data only: bags, orders, address books and reviews (no credentials, counters)."""
    return {
        "bags": list(db.bags.find({}, sort=[("_id", ASCENDING)])),
        "orders": list(db.orders.find({}, sort=[("_id", ASCENDING)])),
        "addresses": {u["_id"]: u["addresses"] for u in db.users.find({}, {"addresses": 1})},
        "reviews": {p["_id"]: p["reviews"] for p in db.products.find({}, {"reviews": 1})},
    }
