"""Helpline's collections: the $jsonSchema validator and the indexes of each.

store.py creates every collection of a session's database with its validator (strict,
errors refused) and its indexes before the seed is inserted, so no document of any session
ever fails these rules. Dates and times are ISO strings from the app's clock, ids are counters
or seeded names: no ObjectId and no server-side clock anywhere.
"""

from __future__ import annotations

STATUSES = ["new", "triage", "in_progress", "waiting", "resolved", "closed"]
PRIORITIES = ["urgent", "high", "normal", "low"]
CHANNELS = ["email", "phone", "chat"]
WHEN = {"bsonType": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}( [0-9]{2}:[0-9]{2})?$"}
TEXT = {"bsonType": "string"}
INT = {"bsonType": "int"}


def _object(required: list[str], properties: dict, *, closed: bool = True) -> dict:
    schema = {"bsonType": "object", "required": required, "properties": properties}
    if closed:
        schema["additionalProperties"] = False
    return schema


_ENTRY = {"oneOf": [
    _object(["role", "at", "body", "via"], {
        "role": {"enum": ["customer"]}, "at": WHEN, "body": TEXT, "via": {"enum": CHANNELS}}),
    _object(["role", "at", "body", "author_id"], {
        "role": {"enum": ["agent"]}, "at": WHEN, "body": TEXT, "author_id": TEXT}),
    _object(["role", "at", "event"], {
        "role": {"enum": ["system"]}, "at": WHEN, "event": {"enum": ["acknowledged", "merged"]}}),
]}

VALIDATORS = {
    "agents": _object(["_id", "username", "name", "roles", "on_call", "pw"], {
        "_id": TEXT, "username": TEXT, "name": TEXT,
        "roles": {"bsonType": "array", "minItems": 1, "items": {"enum": ["agent", "lead"]}},
        "on_call": {"bsonType": "bool"},
        "pw": _object(["salt", "hash", "iter"], {"salt": TEXT, "hash": TEXT, "iter": INT})}),
    "customers": _object(["_id", "name", "email", "phone", "plan", "address", "since", "contacts"], {
        "_id": TEXT, "name": {"bsonType": "string", "minLength": 1}, "email": TEXT, "phone": TEXT,
        "since": WHEN,
        "plan": _object(["name", "speed_mbps", "router"], {
            "name": {"enum": ["Gigafast", "Fibre Max", "Fibre Plus", "Fibre Essential", "Rural Wireless",
                              "Copper Basic"]},
            "speed_mbps": {"bsonType": "int", "minimum": 1},
            "router": _object(["model", "serial"], {"model": TEXT, "serial": TEXT})}),
        "address": _object(["line1", "town", "postcode"], {"line1": TEXT, "town": TEXT, "postcode": TEXT}),
        # People the customer has authorised to discuss the account.
        "contacts": {"bsonType": "array", "items": _object(["name", "relation", "phone"], {
            "name": TEXT, "relation": {"enum": ["partner", "family", "landlord", "carer"]}, "phone": TEXT})}}),
    "tickets": _object(["_id", "subject", "customer_id", "channel", "priority", "status", "assignee_id",
                        "merged_into", "linked", "tags", "opened", "entries", "history"], {
        "_id": INT, "subject": {"bsonType": "string", "minLength": 1}, "customer_id": TEXT,
        "channel": {"enum": CHANNELS}, "priority": {"enum": PRIORITIES}, "status": {"enum": STATUSES},
        "assignee_id": {"bsonType": ["string", "null"]}, "merged_into": {"bsonType": ["int", "null"]},
        "linked": {"bsonType": "array", "items": INT}, "tags": {"bsonType": "array", "items": TEXT},
        "opened": WHEN, "entries": {"bsonType": "array", "minItems": 1, "items": _ENTRY},
        "history": {"bsonType": "array", "items": _object(["n", "at", "actor", "event", "detail"], {
            "n": INT, "at": WHEN, "actor": TEXT, "detail": TEXT,
            "event": {"enum": ["created", "status", "assigned", "reply", "customer_reply", "merged_into",
                               "merged_from", "subject"]}})}}),
    # Derived for the queue's assignee filters; whoever changes an assignee or a status re-indexes.
    "queue_index": _object(["_id", "assignee", "status"], {
        "_id": INT, "assignee": TEXT, "status": {"enum": STATUSES}}),
    "sla_policies": _object(["_id", "first_response_min", "resolve_hours"], {
        "_id": {"enum": PRIORITIES}, "first_response_min": INT, "resolve_hours": INT}),
    "replies": _object(["_id", "title", "body", "position", "author_id", "updated"], {
        "_id": TEXT, "title": {"bsonType": "string", "minLength": 1, "maxLength": 200},
        "body": {"bsonType": "string", "minLength": 1}, "position": {"bsonType": "int", "minimum": 1},
        "author_id": TEXT, "updated": WHEN}),
    "counters": _object(["_id", "n"], {"_id": TEXT, "n": INT}),
}

#: (keys, options) per collection.
INDEXES = {
    "agents": [([("username", 1)], {"unique": True, "name": "username"})],
    "customers": [([("email", 1)], {"unique": True, "name": "email"}),
                  ([("name", 1), ("_id", 1)], {"name": "by_name"})],
    "tickets": [([("status", 1), ("opened", 1)], {"name": "by_status"}),
                ([("customer_id", 1)], {"name": "by_customer"}),
                ([("linked", 1)], {"name": "by_link"})],
    "queue_index": [([("assignee", 1), ("status", 1)], {"name": "by_assignee"})],
    "replies": [([("position", 1), ("_id", 1)], {"name": "by_position"})],
}
