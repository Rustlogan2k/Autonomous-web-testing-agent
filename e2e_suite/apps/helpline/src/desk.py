"""Helpline's care desk: tickets, their conversations, customers, saved replies, and the
rules that move them.

Domain code over one session's MongoDB database (`store.py`); nothing here knows about HTTP
or HTML. A ticket is one document: its conversation (`entries`: customer, agent and system
entries), its history and its links travel with it. Every operation checks everything it
needs before it writes, and raises `Refusal` having written nothing, so a refused action
leaves the committed state exactly as it was.

The status machine::

    new -> triage -> in_progress -> waiting -> resolved -> closed
           (start work also resumes a waiting ticket)

* Sending a reply to the customer moves a new, triaged or in-progress ticket to
  "waiting on customer".
* Logging a reply the customer gave by phone reopens a waiting or resolved ticket.
* Resolving needs the agent to have the last word in the conversation.
* Only a resolved ticket can be closed. Closed is final.
"""

from __future__ import annotations

import datetime as dt
import re

import pymongo

STATUSES = ("new", "triage", "in_progress", "waiting", "resolved", "closed")
OPEN_STATUSES = ("new", "triage", "in_progress", "waiting")
PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
CHANNELS = ("email", "phone", "chat")
#: Explicit status moves an agent can make with a button, and where each may start from.
MOVES = {"triage": ("new",), "in_progress": ("triage", "waiting")}
BODY_LIMIT = 10_000
TEXT_LIMIT = 200
#: Queue filters as they appear in URLs, and what they select.
STATUS_FILTERS = ("open", "all") + STATUSES
ASSIGNEE_FILTERS = ("any", "none", "noor", "kai")
#: Customer directory: page size and orders (each ends in a unique key).
DIRECTORY_PAGE = 10
CUSTOMER_SORTS = {
    "name": [("name", pymongo.ASCENDING), ("_id", pymongo.ASCENDING)],
    "newest": [("since", pymongo.DESCENDING), ("_id", pymongo.ASCENDING)],
    "speed": [("plan.speed_mbps", pymongo.DESCENDING), ("name", pymongo.ASCENDING), ("_id", pymongo.ASCENDING)],
}
PLANS = {"Gigafast": 1000, "Fibre Max": 900, "Fibre Plus": 500, "Fibre Essential": 150, "Rural Wireless": 80,
         "Copper Basic": 36}
ROUTERS = {"Gigafast": "Hub 6", "Fibre Max": "Hub 5", "Fibre Plus": "Hub 5", "Fibre Essential": "Hub 4",
           "Rural Wireless": "Outdoor unit W2", "Copper Basic": "Hub 3"}
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PHONE = re.compile(r"[0-9 +()-]{6,20}")


class Refusal(Exception):
    """An action the desk declines. `key` names the vocabulary message shown to the agent."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


# -- the seed --------------------------------------------------------------------------------


def template(seed: dict) -> dict[str, list[dict]]:
    """Every collection as a new session first sees it: the seed, the derived queue index
    and the counters (the next ticket is #111, the next customer c36)."""
    return {**seed, "queue_index": [{"_id": t["_id"], "assignee": t["assignee_id"] or "", "status": t["status"]}
                                     for t in seed["tickets"]],
            "counters": [{"_id": "ticket", "n": max(t["_id"] for t in seed["tickets"])},
                         {"_id": "customer", "n": len(seed["customers"])},
                         {"_id": "reply", "n": len(seed["replies"])}]}


def _next(db, counter: str) -> int:
    return db.counters.find_one_and_update({"_id": counter}, {"$inc": {"n": 1}},
                                           return_document=pymongo.ReturnDocument.AFTER)["n"]


# -- people ----------------------------------------------------------------------------------


def agents(db) -> dict[str, dict]:
    return {a["_id"]: a for a in db.agents.find({}, {"pw": 0}).sort("_id", 1)}


def on_call(db) -> dict:
    return db.agents.find_one({"on_call": True}, {"pw": 0}, sort=[("_id", 1)])


# -- reading tickets ---------------------------------------------------------------------------


def ticket(db, ticket_id: int) -> dict | None:
    return db.tickets.find_one({"_id": ticket_id})


def customer(db, customer_id: str) -> dict | None:
    return db.customers.find_one({"_id": customer_id})


def conversation(row: dict) -> list[dict]:
    return list(row["entries"])


def latest_role(row: dict) -> str:
    """Who had the last word: the latest customer or agent entry (system notes do not speak)."""
    spoken = [e for e in row["entries"] if e["role"] in ("customer", "agent")]
    return spoken[-1]["role"] if spoken else ""


def linked(db, row: dict) -> list[dict]:
    return list(db.tickets.find({"_id": {"$in": row["linked"]}}, {"subject": 1}).sort("_id", 1))


def merge_candidates(db, row: dict) -> list[dict]:
    """Open tickets that could be merged into this one: the same customer's first."""
    rows = list(db.tickets.find({"_id": {"$ne": row["_id"]}, "status": {"$in": list(OPEN_STATUSES)}}))
    return sorted(rows, key=lambda other: (other["customer_id"] != row["customer_id"], other["_id"]))


def can_merge_into(row: dict) -> bool:
    return row["status"] in OPEN_STATUSES


def counts(db) -> dict[str, int]:
    found = {r["_id"]: r["n"] for r in db.tickets.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}])}
    return {status: found.get(status, 0) for status in STATUSES}


def normalise_filters(status: str, assignee: str) -> tuple[str, str]:
    return (status if status in STATUS_FILTERS else "open",
            assignee if assignee in ASSIGNEE_FILTERS else "any")


def queue(db, status: str, assignee: str) -> list[dict]:
    """The queue under a filter, most urgent first. Assignee filters read the index."""
    status, assignee = normalise_filters(status, assignee)
    rows = list(db.tickets.find({}, {"entries": 0, "history": 0}))
    if status == "open":
        rows = [row for row in rows if row["status"] in OPEN_STATUSES]
    elif status != "all":
        rows = [row for row in rows if row["status"] == status]
    if assignee != "any":
        code = "" if assignee == "none" else assignee
        wanted = {r["_id"] for r in db.queue_index.find({"assignee": code}, {"_id": 1})}
        rows = [row for row in rows if row["_id"] in wanted]
    return sorted(rows, key=lambda row: (PRIORITY_RANK.get(row["priority"], 9), row["_id"]))


def sla(db, row: dict, now: dt.datetime) -> dict:
    """When a ticket must first be answered and resolved, and whether either is already late."""
    policy = db.sla_policies.find_one({"_id": row["priority"]})
    opened = dt.datetime.fromisoformat(row["opened"])
    respond_by = opened + dt.timedelta(minutes=policy["first_response_min"])
    resolve_by = opened + dt.timedelta(hours=policy["resolve_hours"])
    answered = any(e["role"] == "agent" for e in row["entries"])
    open_ = row["status"] in OPEN_STATUSES
    return {"respond_by": respond_by.strftime("%Y-%m-%d %H:%M"), "resolve_by": resolve_by.strftime("%Y-%m-%d %H:%M"),
            "response_late": not answered and open_ and now > respond_by, "resolve_late": open_ and now > resolve_by}


def breaches(db, now: dt.datetime) -> list[dict]:
    """Open tickets past either of their SLA times, most urgent first (the lead's report)."""
    out = []
    for row in queue(db, "open", "any"):
        full = ticket(db, row["_id"])
        due = sla(db, full, now)
        if due["response_late"] or due["resolve_late"]:
            out.append({"ticket": full, **due})
    return out


def committed(db) -> dict:
    """Business state: tickets (with conversations, history and links), the queue index,
    customers, saved replies and agents (never their passwords)."""
    return {"tickets": list(db.tickets.find({}).sort("_id", 1)),
            "queue_index": list(db.queue_index.find({}).sort("_id", 1)),
            "customers": list(db.customers.find({}).sort("_id", 1)),
            "replies": list(db.replies.find({}).sort("_id", 1)),
            "agents": list(db.agents.find({}, {"pw": 0}).sort("_id", 1))}


# -- changing tickets ----------------------------------------------------------------------------


def refresh_assignee_index(db, ids: list[int]) -> None:
    """Bring the queue's index in line with the tickets themselves."""
    for ticket_id in ids:
        row = ticket(db, ticket_id)
        if row is not None:
            db.queue_index.replace_one({"_id": ticket_id}, {"_id": ticket_id, "assignee": row["assignee_id"] or "",
                                                            "status": row["status"]}, upsert=True)


def _log(db, ticket_id: int, at: str, actor: str, event: str, detail: str = "") -> None:
    row = db.tickets.find_one({"_id": ticket_id}, {"history": 1})
    entry = {"n": len(row["history"]) + 1, "at": at, "actor": actor, "event": event, "detail": detail}
    db.tickets.update_one({"_id": ticket_id}, {"$push": {"history": entry}})


def _set_status(db, row: dict, to: str, now: str, me: str) -> None:
    if row["status"] == to:
        return
    db.tickets.update_one({"_id": row["_id"]}, {"$set": {"status": to}})
    _log(db, row["_id"], now, me, "status", f"{row['status']}>{to}")
    db.queue_index.update_one({"_id": row["_id"]}, {"$set": {"status": to}})


def _open_ticket(db, ticket_id: int) -> dict:
    row = ticket(db, ticket_id)
    if row is None:
        raise Refusal("msg.no_such_ticket")
    if row["status"] == "closed":
        raise Refusal("msg.ticket_closed")
    return row


def _clean_body(body: str) -> str:
    if not body.strip():
        raise Refusal("msg.reply_empty")
    if len(body) > BODY_LIMIT:
        raise Refusal("msg.reply_too_long")
    return body  # stored exactly as typed


def send_reply(db, ticket_id: int, body: str, now: str, me: str) -> None:
    """The agent answers the customer; the ball is then in the customer's court."""
    row = _open_ticket(db, ticket_id)
    text = _clean_body(body)
    db.tickets.update_one({"_id": ticket_id}, {"$push": {"entries": {"role": "agent", "at": now, "body": text,
                                                                     "author_id": me}}})
    _log(db, ticket_id, now, me, "reply")
    if row["status"] in ("new", "triage", "in_progress"):
        _set_status(db, row, "waiting", now, me)


def log_customer_reply(db, ticket_id: int, body: str, now: str, me: str) -> None:
    """The agent records what the customer said on the phone."""
    row = _open_ticket(db, ticket_id)
    text = _clean_body(body)
    db.tickets.update_one({"_id": ticket_id}, {"$push": {"entries": {"role": "customer", "at": now, "body": text,
                                                                     "via": "phone"}}})
    _log(db, ticket_id, now, me, "customer_reply")
    if row["status"] in ("waiting", "resolved"):  # the customer answered: back to work
        _set_status(db, row, "in_progress", now, me)


def move(db, ticket_id: int, to: str, now: str, me: str) -> None:
    """An explicit step along the machine: start triage, start (or resume) work."""
    row = _open_ticket(db, ticket_id)
    if row["status"] not in MOVES.get(to, ()):
        raise Refusal("msg.bad_transition")
    _set_status(db, row, to, now, me)


def resolve(db, ticket_id: int, now: str, me: str) -> None:
    row = _open_ticket(db, ticket_id)
    if row["status"] not in OPEN_STATUSES:
        raise Refusal("msg.already_resolved")
    if latest_role(row) != "agent":
        raise Refusal("msg.resolve_needs_reply")
    _set_status(db, row, "resolved", now, me)


def close(db, ticket_id: int, now: str, me: str) -> None:
    row = _open_ticket(db, ticket_id)
    if row["status"] != "resolved":
        raise Refusal("msg.close_needs_resolved")
    _set_status(db, row, "closed", now, me)


def _assign(db, row: dict, who: str, now: str, me: str) -> None:
    db.tickets.update_one({"_id": row["_id"]}, {"$set": {"assignee_id": who}})
    _log(db, row["_id"], now, me, "assigned", who)


def hand_off(db, ticket_id: int, now: str, me: str) -> None:
    """The ticket page's hand-off: one ticket to whoever is on call."""
    row = _open_ticket(db, ticket_id)
    target = on_call(db)["_id"]
    if row["assignee_id"] == target:
        raise Refusal("msg.already_oncall")
    _assign(db, row, target, now, me)
    refresh_assignee_index(db, [ticket_id])


def hand_off_many(db, ticket_ids: list[int], now: str, me: str) -> None:
    """The queue's hand-off, written for several rows at once (one row today)."""
    target = on_call(db)["_id"]
    tickets = [_open_ticket(db, ticket_id) for ticket_id in ticket_ids]
    if any(row["assignee_id"] == target for row in tickets):
        raise Refusal("msg.already_oncall")
    for row in tickets:
        _assign(db, row, target, now, me)
    refresh_assignee_index(db, [row["_id"] for row in tickets])


def merge(db, target_id: int, source_raw: str, now: str, me: str) -> int:
    """Merge another open ticket into this one. Returns the merged ticket's number."""
    target = _open_ticket(db, target_id)
    if not can_merge_into(target):
        raise Refusal("msg.merge_target_done")
    if not source_raw.strip().isdigit():
        raise Refusal("msg.merge_invalid")
    source = ticket(db, int(source_raw))
    if source is None or source["_id"] == target_id or source["status"] not in OPEN_STATUSES:
        raise Refusal("msg.merge_invalid")
    db.tickets.update_one({"_id": source["_id"]}, {"$set": {"status": "closed", "merged_into": target_id}})
    db.queue_index.update_one({"_id": source["_id"]}, {"$set": {"status": "closed"}})
    _log(db, source["_id"], now, me, "merged_into", str(target_id))
    _log(db, target_id, now, me, "merged_from", str(source["_id"]))
    for owner, other in ((target_id, source["_id"]), (source["_id"], target_id)):  # each lists the other
        db.tickets.update_one({"_id": owner}, {"$addToSet": {"linked": other}})
    return source["_id"]


def create_ticket(db, customer_id: str, subject: str, channel: str, priority: str, message: str,
                  now: str, me: str) -> int:
    """A ticket opened at the desk for a customer, with the customer's first message."""
    found = check_new_ticket(db, customer_id, subject, channel, priority, message)
    ticket_id = _next(db, "ticket")
    db.tickets.insert_one({
        "_id": ticket_id, "subject": subject.strip(), "customer_id": customer_id, "channel": channel,
        "priority": priority, "status": "new", "assignee_id": None, "merged_into": None, "linked": [], "tags": [],
        "opened": now,
        "entries": [{"role": "customer", "at": now, "body": message, "via": channel},
                    {"role": "system", "at": now, "event": "acknowledged"}],
        "history": [{"n": 1, "at": now, "actor": found["name"], "event": "created", "detail": channel}]})
    refresh_assignee_index(db, [ticket_id])
    return ticket_id


def check_new_ticket(db, customer_id: str, subject: str, channel: str, priority: str, message: str) -> dict:
    """The new-ticket wizard's rules; returns the customer, or raises Refusal."""
    found = customer(db, customer_id)
    if found is None:
        raise Refusal("msg.choose_customer")
    if not subject.strip() or len(subject) > TEXT_LIMIT:
        raise Refusal("msg.subject_needed")
    if channel not in CHANNELS or priority not in PRIORITY_RANK:
        raise Refusal("msg.choose_options")
    _clean_body(message)
    return found


def delete_ticket(db, ticket_id: int) -> dict:
    """Delete a ticket opened by mistake (or spam): it disappears from every list and link."""
    row = ticket(db, ticket_id)
    if row is None:
        raise Refusal("msg.no_such_ticket")
    db.tickets.update_many({"linked": ticket_id}, {"$pull": {"linked": ticket_id}})
    db.tickets.update_many({"merged_into": ticket_id}, {"$set": {"merged_into": None}})
    db.tickets.delete_one({"_id": ticket_id})
    db.queue_index.delete_one({"_id": ticket_id})
    return row


# -- customers ---------------------------------------------------------------------------------------


def directory(db, q: str, sort: str, page: int) -> dict:
    """One page of customers whose name or email contains `q`, in `sort` order."""
    needle = q.strip()
    query = {"$or": [{"name": {"$regex": re.escape(needle), "$options": "i"}},
                     {"email": {"$regex": re.escape(needle), "$options": "i"}}]} if needle else {}
    total = db.customers.count_documents(query)
    pages = max(1, -(-total // DIRECTORY_PAGE))
    page = min(max(page, 1), pages)
    rows = list(db.customers.find(query).sort(CUSTOMER_SORTS[sort]).skip((page - 1) * DIRECTORY_PAGE)
                .limit(DIRECTORY_PAGE))
    speeds = [c["plan"]["speed_mbps"] for c in db.customers.find(query, {"plan.speed_mbps": 1})]
    return {"rows": rows, "total": total, "page": page, "pages": pages,
            "slowest": min(speeds, default=0), "fastest": max(speeds, default=0)}


def tickets_of(db, customer_id: str) -> list[dict]:
    return list(db.tickets.find({"customer_id": customer_id}, {"entries": 0, "history": 0}).sort("_id", 1))


def read_customer(form: dict) -> dict:
    """A customer's details from the form, or Refusal."""
    name, email, phone = form.get("name", "").strip(), form.get("email", "").strip(), form.get("phone", "").strip()
    plan = form.get("plan", "")
    address = {"line1": form.get("line1", "").strip(), "town": form.get("town", "").strip(),
               "postcode": form.get("postcode", "").strip()}
    if not name or len(name) > TEXT_LIMIT or any(len(v) > TEXT_LIMIT for v in address.values()):
        raise Refusal("msg.customer_name_needed")
    if not EMAIL.fullmatch(email) or len(email) > TEXT_LIMIT:
        raise Refusal("msg.email_bad")
    if not PHONE.fullmatch(phone):
        raise Refusal("msg.phone_bad")
    if plan not in PLANS:
        raise Refusal("msg.choose_plan")
    if not all(address.values()):
        raise Refusal("msg.address_needed")
    return {"name": name, "email": email, "phone": phone, "plan": plan, "address": address}


def _email_taken(db, email: str, other_than: str = "") -> bool:
    return db.customers.count_documents({"email": email, "_id": {"$ne": other_than}}) > 0


def add_customer(db, form: dict, today: str) -> str:
    details = read_customer(form)
    if _email_taken(db, details["email"]):
        raise Refusal("msg.email_taken")
    customer_id = f"c{_next(db, 'customer')}"
    db.customers.insert_one({
        "_id": customer_id, "name": details["name"], "email": details["email"], "phone": details["phone"],
        "since": today, "address": details["address"], "contacts": [],
        "plan": {"name": details["plan"], "speed_mbps": PLANS[details["plan"]],
                 "router": {"model": ROUTERS[details["plan"]], "serial": f"TW{90000 + int(customer_id[1:]):06d}"}}})
    return customer_id


def update_customer(db, customer_id: str, form: dict) -> None:
    current = customer(db, customer_id)
    if current is None:
        raise Refusal("msg.no_such_customer")
    details = read_customer(form)
    if _email_taken(db, details["email"], customer_id):
        raise Refusal("msg.email_taken")
    plan = current["plan"]
    if details["plan"] != plan["name"]:  # a new plan comes with its own router
        plan = {"name": details["plan"], "speed_mbps": PLANS[details["plan"]],
                "router": {"model": ROUTERS[details["plan"]], "serial": plan["router"]["serial"]}}
    db.customers.update_one({"_id": customer_id}, {"$set": {
        "name": details["name"], "email": details["email"], "phone": details["phone"],
        "address": details["address"], "plan": plan}})


def delete_customer(db, customer_id: str) -> dict:
    current = customer(db, customer_id)
    if current is None:
        raise Refusal("msg.no_such_customer")
    if db.tickets.count_documents({"customer_id": customer_id}):
        raise Refusal("msg.customer_has_tickets")
    db.customers.delete_one({"_id": customer_id})
    return current


# -- saved replies ----------------------------------------------------------------------------------


def replies(db) -> list[dict]:
    return list(db.replies.find({}).sort([("position", 1), ("_id", 1)]))


def _reply_text(title: str, body: str) -> tuple[str, str]:
    title, body = title.strip(), body.strip()
    if not title or len(title) > TEXT_LIMIT:
        raise Refusal("msg.reply_title_needed")
    return title, _clean_body(body)


def add_reply(db, title: str, body: str, today: str, me: str) -> str:
    title, body = _reply_text(title, body)
    reply_id = f"r{_next(db, 'reply')}"
    last = db.replies.find_one({}, sort=[("position", -1)])
    db.replies.insert_one({"_id": reply_id, "title": title, "body": body, "position": (last["position"] if last else 0) + 1,
                           "author_id": me, "updated": today})
    return reply_id


def rename_reply(db, reply_id: str, title: str, today: str) -> str:
    title = title.strip()
    if not title or len(title) > TEXT_LIMIT:
        raise Refusal("msg.reply_title_needed")
    if not db.replies.update_one({"_id": reply_id}, {"$set": {"title": title, "updated": today}}).matched_count:
        raise Refusal("msg.no_such_reply")
    return title


def move_reply(db, reply_id: str, direction: str) -> None:
    """Swap a saved reply with its neighbour above or below."""
    row = db.replies.find_one({"_id": reply_id})
    if row is None:
        raise Refusal("msg.no_such_reply")
    compare, order = ("$lt", -1) if direction == "up" else ("$gt", 1)
    neighbour = db.replies.find_one({"position": {compare: row["position"]}}, sort=[("position", order)])
    if neighbour is None:
        return
    db.replies.update_one({"_id": row["_id"]}, {"$set": {"position": neighbour["position"]}})
    db.replies.update_one({"_id": neighbour["_id"]}, {"$set": {"position": row["position"]}})


def delete_reply(db, reply_id: str) -> dict:
    row = db.replies.find_one_and_delete({"_id": reply_id})
    if row is None:
        raise Refusal("msg.no_such_reply")
    return row
