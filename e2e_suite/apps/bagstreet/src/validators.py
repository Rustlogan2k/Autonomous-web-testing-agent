"""Bagstreet's collections, each with the `$jsonSchema` MongoDB checks every write against.

users       shoppers and staff, with their saved addresses (an embedded address book)
categories  the shop's departments
products    category_id -> categories; attributes that depend on the department (oneOf);
            variants (sizes, colours) with a shop-wide unique SKU; reviews by customers
vouchers    what a voucher code does (oneOf by kind)
stores      where collection orders are picked up
bags        one per shopper (_id -> users): lines, the voucher and its discount, delivery choice
orders      placed orders: customer_id -> users, an immutable snapshot of the lines and
            totals, status and its history
counters    the next order number and address key (auxiliary)
"""

from __future__ import annotations

METHODS = ["standard", "express", "collect"]
VOUCHER_KINDS = ["percent", "free_delivery", "amount_off"]
ORDER_STATUSES = ["placed", "dispatched"]
ROLES = ["customer", "staff"]
TEXT_LIMIT = 10000  # what a delivery label can carry (the checkout's own limit)


def _text(low: int, high: int) -> dict:
    return {"bsonType": "string", "minLength": low, "maxLength": high}


def _int(low: int, high: int | None = None) -> dict:
    rule = {"bsonType": "int", "minimum": low}
    if high is not None:
        rule["maximum"] = high
    return rule


def _closed(required: list[str], **properties) -> dict:
    """An object with exactly these properties; `required` lists the ones it must have."""
    return {"bsonType": "object", "required": required, "additionalProperties": False,
            "properties": properties}


def _all(**properties) -> dict:
    return _closed(list(properties), **properties)


_KEY = {"bsonType": "string", "pattern": "^[a-z][a-z0-9-]*$"}
_ADDRESS = _all(key=_KEY, label=_text(1, 30), name=_text(1, 80), line1=_text(1, 120), town=_text(1, 60),
                postcode=_text(1, 20))
_ATTRIBUTES = {"oneOf": [
    _all(kind={"enum": ["lighting"]}, bulb=_text(1, 40), lumens=_int(1)),
    _all(kind={"enum": ["kitchen"]}, material=_text(1, 40), capacity_l={"bsonType": ["double", "int"], "minimum": 0}),
    _all(kind={"enum": ["outdoor"]}, weatherproof={"bsonType": "bool"}, max_load_kg=_int(1)),
]}
_LINE = _all(n=_int(1), product_id=_KEY, variant=_KEY, qty=_int(1, 99), unit_price_pence=_int(1))
_SNAPSHOT = _all(n=_int(1), product_id=_KEY, name=_text(1, 120), variant=_KEY, variant_label=_text(1, 80),
                 qty=_int(1, 99), unit_price_pence=_int(1))
_MONEY = {"bsonType": "int", "minimum": 0}

COLLECTIONS = [
    {"name": "users", "indexes": [{"keys": [("username", 1)], "name": "one_account_per_username", "unique": True}],
     "validator": _closed(["_id", "username", "name", "roles", "pw", "addresses"],
                          _id=_KEY, username={"bsonType": "string", "pattern": r"^[a-z]+\.[a-z]+$"},
                          name=_text(1, 80), roles={"bsonType": "array", "minItems": 1, "items": {"enum": ROLES}},
                          pw=_all(salt={"bsonType": "string", "pattern": "^[0-9a-f]{32}$"},
                                  hash={"bsonType": "string", "pattern": "^[0-9a-f]{64}$"}, iter=_int(1000)),
                          addresses={"bsonType": "array", "maxItems": 10, "items": _ADDRESS})},
    {"name": "categories", "indexes": [{"keys": [("position", 1)], "name": "department_order", "unique": True}],
     "validator": _all(_id=_KEY, name=_text(1, 60), position=_int(1))},
    {"name": "products",
     "indexes": [{"keys": [("category_id", 1), ("position", 1)], "name": "products_by_department"},
                 {"keys": [("variants.sku", 1)], "name": "one_product_per_sku", "unique": True},
                 {"keys": [("reviews.author_id", 1)], "name": "reviews_by_author"}],
     "validator": _all(_id=_KEY, category_id=_KEY, position=_int(1), name=_text(1, 120), blurb=_text(1, 400),
                       attributes=_ATTRIBUTES,
                       variants={"bsonType": "array", "minItems": 1, "items": _all(
                           code=_KEY, label=_text(1, 80), price_pence=_int(1), stock=_int(0),
                           sku={"bsonType": "string", "pattern": "^BS-[A-Z0-9-]+$"},
                           size=_all(height_cm=_int(1), weight_g=_int(1)))},
                       reviews={"bsonType": "array", "items": _all(
                           n=_int(1), author_id=_KEY, stars=_int(1, 5), text=_text(1, 2000))})},
    {"name": "vouchers", "indexes": [],
     "validator": {"oneOf": [
         _all(_id=_KEY, kind={"enum": ["percent"]}, percent=_int(1, 100)),
         _all(_id=_KEY, kind={"enum": ["free_delivery"]}),
         _all(_id=_KEY, kind={"enum": ["amount_off"]}, amount_pence=_int(1), min_subtotal_pence=_MONEY),
     ], "bsonType": "object", "required": ["_id", "kind"]}},
    {"name": "stores", "indexes": [],
     "validator": _all(_id=_KEY, name=_text(1, 80), address=_text(1, 200),
                       hours={"bsonType": "array", "items": _text(1, 60)})},
    {"name": "bags", "indexes": [],
     "validator": _all(_id=_KEY, lines={"bsonType": "array", "maxItems": 50, "items": _LINE},
                       voucher={"oneOf": [{"bsonType": "null"}, _all(code=_KEY, discount_pence=_MONEY)]},
                       method={"enum": METHODS}, full_name=_text(0, TEXT_LIMIT), address=_text(0, TEXT_LIMIT),
                       store_id=_KEY)},
    {"name": "orders", "indexes": [{"keys": [("customer_id", 1), ("n", -1)], "name": "orders_by_customer"},
                                   {"keys": [("status", 1), ("n", -1)], "name": "orders_by_status"}],
     # The address is not required to be non-empty for delivery: that is the checkout's
     # rule (BAG-3), and a database rule here would pre-empt it.
     "validator": _all(_id={"bsonType": "string", "pattern": "^BS-[0-9]{4}$"}, customer_id=_KEY, n=_int(1),
                       method={"enum": METHODS}, full_name=_text(0, TEXT_LIMIT), address=_text(0, TEXT_LIMIT),
                       store_id=_KEY, lines={"bsonType": "array", "minItems": 1, "items": _SNAPSHOT},
                       totals=_all(subtotal=_MONEY, discount=_MONEY, delivery=_MONEY, total=_MONEY),
                       status={"enum": ORDER_STATUSES},
                       events={"bsonType": "array", "items": _all(kind={"enum": ORDER_STATUSES}, by=_KEY,
                                                                  at=_text(19, 19))})},
    {"name": "counters", "indexes": [],
     "validator": _all(_id={"enum": ["seq"]}, order=_int(1), address=_int(1))},
]
