"""Tidewater — personal banking. A single-page app over a JSON API, behind a sign-in.

The data lives in PostgreSQL, one schema per signed-in browser context (vault.py); the
sign-in and help pages are rendered here (pages.py) and everything after the sign-in is the
single-page client in static/. Standard library plus the suite's vendored pg8000.

Environment: PORT (required), DATABASE_URL (required), HOST, WTA_VOCAB, WTA_FIXED_NOW,
WTA_SESSION_CAP, WTA_TEST_HOOKS.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sys
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
from vault import Vault  # noqa: E402

APP_ID = "tidewater"
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
VOCAB = json.loads((ROOT.parent / "vocab" / f"{os.environ.get('WTA_VOCAB', 'default')}.json")
                   .read_text(encoding="utf-8"))
LOGINS = json.loads((ROOT.parent / "seed" / "logins.json").read_text(encoding="utf-8"))
NOW = dt.datetime.fromisoformat(os.environ.get("WTA_FIXED_NOW", "2026-06-10T09:00:00"))  # wta-clock
TEST_HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
SCHEDULE_OPTIONS = ("next-friday", "month-end")
WHEN_OPTIONS = ("now",) + SCHEDULE_OPTIONS
NAME_LIMIT = 5000
PUBLIC_FILES = {"/app.js": "text/javascript; charset=utf-8", "/styles.css": "text/css; charset=utf-8"}
VAULT: Vault | None = None  # opened by main()


def money(pence: int) -> str:
    sign = "-" if pence < 0 else ""
    return f"{sign}£{abs(pence) / 100:,.2f}"


def parse_amount(raw) -> int | None:
    text = str(raw if raw is not None else "").strip()
    if not re.fullmatch(r"\d{1,9}(\.\d{1,2})?", text):
        return None
    return int(round(float(text) * 100))


def run_date(when: str) -> dt.date:
    today = NOW.date()
    if when == "next-friday":
        days = (4 - today.weekday()) % 7 or 7
        return today + dt.timedelta(days=days)
    if when == "month-end":
        first_next = (today.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        last = first_next - dt.timedelta(days=1)
        while last.weekday() >= 5:  # payments run on business days
            last -= dt.timedelta(days=1)
        return last
    return today


def label_date(day: dt.date) -> str:
    return day.strftime("%a %d %b %Y")


def verify(username: str, password: str) -> str | None:
    """The customer a username and password sign in as (PBKDF2, seeded salts), or None."""
    login = next((entry for entry in LOGINS["logins"] if entry["username"] == username), None)
    salt = bytes.fromhex(login["salt"]) if login else b"\0" * 16  # unknown names cost the same
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, LOGINS["iterations"]).hex()
    if login and hmac.compare_digest(digest, login["pbkdf2_sha256"]):
        return login["customer_id"]
    return None


# -- the bank, as one customer sees it ---------------------------------------------------------

BALANCE = ("a.opening_balance_pence + coalesce((select sum(e.amount_pence) from ledger_entries e "
           "where e.account_id = a.id), 0)")


def accounts(db, me: str) -> list[dict]:
    """The accounts `me` may see, with their balances and what is scheduled to leave them."""
    rows = db.fetch(
        f"select a.id, a.name, a.number, x.level, {BALANCE} as balance, "
        "coalesce((select sum(t.amount_pence) from transfers t where t.source_id = a.id "
        "and t.status = 'scheduled'), 0) as scheduled "
        "from accounts a join account_access x on x.account_id = a.id and x.customer_id = %s "
        "order by a.position", me)
    return [{"id": r["id"], "name": r["name"], "number": "•••• " + r["number"][-4:],
             "level": r["level"], "balance": r["balance"], "balance_label": money(r["balance"]),
             "scheduled": r["scheduled"], "scheduled_label": money(r["scheduled"])} for r in rows]


def available(db, account_id: str) -> int | None:
    return db.scalar(f"select {BALANCE} from accounts a where a.id = %s", account_id)


def owns(db, me: str, account_id: str) -> bool:
    return db.scalar("select count(*) from account_access where customer_id = %s and account_id = %s "
                     "and level = 'owner'", me, account_id) == 1


def is_holder(db, me: str) -> bool:
    """A holder owns an account; anyone else has view access only."""
    return db.scalar("select count(*) from account_access where customer_id = %s and level = 'owner'", me) > 0


def split_target(target: str) -> tuple[str | None, str | None]:
    """`acct:<id>` or `payee:<id>` as (target account, target payee)."""
    kind, _, ident = target.partition(":")
    return (ident, None) if kind == "acct" else (None, ident) if kind == "payee" else (None, None)


def target_name(db, me: str, account_id: str | None, payee_id: str | None) -> str | None:
    if account_id is not None:
        return db.scalar("select a.name from accounts a join account_access x on x.account_id = a.id "
                         "where a.id = %s and x.customer_id = %s", account_id, me)
    if payee_id is not None:
        return db.scalar("select nickname from payees where id = %s and customer_id = %s", payee_id, me)
    return None


def payees(db, me: str) -> list[dict]:
    return db.fetch("select p.id, p.nickname, b.name as bank, p.account_number from payees p "
                    "join banks b on b.id = p.bank_id where p.customer_id = %s order by p.position", me)


def banks(db) -> list[str]:
    return [r["name"] for r in db.fetch("select name from banks order by position")]


def transfer_view(db, me: str, row: dict) -> dict:
    return {"id": row["id"], "from": target_name(db, me, row["source_id"], None),
            "to": target_name(db, me, row["target_account_id"], row["target_payee_id"]) or "(removed payee)",
            "amount": money(row["amount_pence"]), "reference": row["reference"],
            "when": row["run_when"], "date": label_date(row["run_on"]), "status": row["status"]}


def post_transfer(db, me: str, transfer: dict) -> None:
    """The ledger entries of a transfer that is carried out today."""
    source, account, payee = transfer["source_id"], transfer["target_account_id"], transfer["target_payee_id"]
    to_label = target_name(db, me, account, payee)
    reference = transfer["reference"]
    db.execute("insert into ledger_entries values (%s, %s, %s, %s, %s, %s, %s)",
               db.next_number("ledger"), source, transfer["run_on"],
               f"To {to_label}" + (f" — {reference}" if reference else ""), -transfer["amount_pence"],
               "transfer_out" if account else "payment", transfer["id"])
    if account is not None:
        db.execute("insert into ledger_entries values (%s, %s, %s, %s, %s, %s, %s)",
                   db.next_number("ledger"), account, transfer["run_on"],
                   f"From {target_name(db, me, source, None)}", transfer["amount_pence"],
                   "transfer_in", transfer["id"])


def iso(value):
    return value.isoformat() if isinstance(value, (dt.date, dt.datetime)) else value


def committed_state(db) -> dict:
    """Business state of this session's bank: never drafts, quotes or counters."""
    def table(sql: str) -> list[list]:
        return [[iso(v) for v in row.values()] for row in db.fetch(sql)]

    return {
        "accounts": table(f'select a.id, {BALANCE} as balance from accounts a order by a.id collate "C"'),
        "access": table('select * from account_access order by customer_id collate "C", account_id collate "C"'),
        "payees": table('select * from payees order by id collate "C"'),
        "direct_debits": table('select * from direct_debits order by id collate "C"'),
        "transfers": table("select * from transfers order by id"),
        "ledger": table("select * from ledger_entries order by id"),
    }


# -- HTTP ----------------------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, status: int, message: str, field: str = "") -> None:
        super().__init__(message)
        self.status, self.message, self.field = status, message, field


class Refusal(Exception):
    """Not an error: the caller is signed out, or their role may not do this (200, ok false)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def next_path(raw: str | None) -> str:
    value = (raw or "").strip()
    if re.fullmatch(r"/(?!/)[^\s\\]*", value) and not value.startswith(("/login", "/logout")):
        return value
    return "/"


class Handler(BaseHTTPRequestHandler):
    server_version = "Tidewater/2.0"

    def log_message(self, fmt, *args):  # quiet, deterministic logs
        pass

    def _cookie(self) -> str | None:
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return None
        return jar["wta_sid"].value if "wta_sid" in jar else None

    def _send(self, status: int, body: bytes, content_type: str, location: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if location:
            self.send_header("Location", location)
        if self._new_cookie:
            self.send_header("Set-Cookie", f"wta_sid={self._new_cookie}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _html(self, body: str) -> None:
        self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _see_other(self, location: str) -> None:
        self._send(303, b"", "text/plain; charset=utf-8", location)

    def _raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _body(self) -> dict:
        raw = self._raw_body()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise ApiError(400, "request body is not JSON") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "request body must be an object")
        return data

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def _dispatch(self, method: str) -> None:  # noqa: PLR0911 - one exit per route
        self._new_cookie = None
        parts = urlsplit(self.path)
        path = parts.path
        if method == "GET" and path == "/healthz":  # ok only while the database answers
            ok = VAULT.healthy()
            return self._json(200 if ok else 503, {"ok": ok, "app": APP_ID})
        if method == "GET" and path in PUBLIC_FILES:
            return self._send(200, (STATIC / path[1:]).read_bytes(), PUBLIC_FILES[path])
        if method == "GET" and path == "/api/vocab":
            return self._json(200, VOCAB)
        if method == "GET" and path == "/help/password":
            return self._html(pages.password_help(VOCAB))
        if path == "/login":
            return self._sign_in(parts.query) if method == "POST" else self._sign_in_page(parts.query)
        if method == "POST" and path == "/logout":
            VAULT.release(self._cookie())
            return self._see_other("/login")
        is_page = method == "GET" and path in ("/", "/index.html")
        if not is_page and not path.startswith("/api/") and not (TEST_HOOKS and path == "/__wta/state"):
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        try:  # the reply is worked out inside the transaction and sent once it has committed
            with VAULT.session(self._cookie()) as db:
                if path == "/__wta/state":
                    status, payload = 200, self._state(db)
                elif db.customer_id is None:
                    raise Refusal("signed-out")
                elif is_page:
                    status, payload = 200, None
                else:
                    status, payload = self._api(db, db.customer_id, method, path, parse_qs(parts.query))
        except Refusal as refusal:
            if is_page:  # signed out: to the sign-in page, and back here afterwards
                return self._see_other("/login?next=" + quote(self.path, safe=""))
            return self._json(200, {"ok": False, "reason": refusal.reason})
        except ApiError as exc:
            return self._json(exc.status, {"error": exc.message, "field": exc.field})
        if is_page:
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        return self._json(status, payload)

    # signing in ----------------------------------------------------------------------------
    def _sign_in_page(self, query: str) -> None:
        wanted = (parse_qs(query).get("next") or [""])[0]
        with VAULT.session(self._cookie()) as db:
            signed_in = db.customer_id is not None
        if signed_in:  # Back to the sign-in page after signing in lands here
            return self._see_other(next_path(wanted))
        return self._html(pages.sign_in(VOCAB, wanted))

    def _sign_in(self, query: str) -> None:
        form = {k: v[-1] for k, v in parse_qs(self._raw_body().decode("utf-8", "replace"),
                                              keep_blank_values=True).items()}
        username, password = form.get("username", "").strip(), form.get("password", "")
        wanted = form.get("next") or (parse_qs(query).get("next") or [""])[0]
        customer = verify(username, password) if username and password else None
        if customer is None:  # one message whatever was wrong; no session is created
            return self._html(pages.sign_in(VOCAB, wanted, username, refused=True))
        self._new_cookie = VAULT.admit(self._cookie(), customer)
        return self._see_other(next_path(wanted))

    def _state(self, db) -> dict:
        if db.schema is None:
            return {"session": None, "user": None, "committed": None, "sha": None}
        committed = committed_state(db)
        sha = hashlib.sha256(json.dumps(committed, sort_keys=True).encode()).hexdigest()
        user = next((e["username"] for e in LOGINS["logins"] if e["customer_id"] == db.customer_id), None)
        return {"session": db.sid, "user": user, "committed": committed, "sha": sha}

    # API ----------------------------------------------------------------------------------
    def _api(self, db, me: str, method: str, path: str, query: dict):  # noqa: C901, PLR0911, PLR0912
        if method == "GET" and path == "/api/me":
            holder = is_holder(db, me)
            customer = db.first("select full_name from customers where id = %s", me)
            return 200, {"ok": True, "name": customer["full_name"], "holder": holder}
        if method == "GET" and path == "/api/overview":
            visible = accounts(db, me)
            ids = [a["id"] for a in visible]
            scheduled = [transfer_view(db, me, r) for r in db.fetch(
                "select * from transfers where status = 'scheduled' and source_id = any(%s) order by id", ids)]
            mandates = db.fetch(
                "select d.originator, d.reference, d.collection_day, a.name as account from direct_debits d "
                "join accounts a on a.id = d.account_id where d.status = 'active' and d.account_id = any(%s) "
                'order by d.collection_day, d.id collate "C"', ids)
            return 200, {"accounts": visible, "scheduled": scheduled, "direct_debits": mandates,
                         "today": label_date(NOW.date())}
        if method == "GET" and path == "/api/products":
            return 200, {"products": db.fetch(
                'select title, kind, interest_bps from account_products order by title collate "C"')}
        if method == "GET" and path == "/api/statements":
            visible = {a["id"]: a["name"] for a in accounts(db, me)}
            account = (query.get("account") or ["all"])[0]
            direction = (query.get("direction") or ["all"])[0]
            chosen = [account] if account in visible else list(visible)
            sign = {"in": "and amount_pence > 0", "out": "and amount_pence < 0"}.get(direction, "")
            rows = db.fetch("select account_id, posted_on, description, amount_pence from ledger_entries "
                            f"where account_id = any(%s) {sign} order by posted_on desc, id desc", chosen)
            return 200, {"rows": [{"date": r["posted_on"].isoformat(), "account": visible[r["account_id"]],
                                   "description": r["description"], "amount": money(r["amount_pence"])}
                                  for r in rows],
                         "count": len(rows)}
        if not is_holder(db, me):  # everything below moves money or manages payees
            raise Refusal("forbidden")
        if method == "GET" and path == "/api/payees":
            return 200, {"payees": payees(db, me), "banks": banks(db)}
        if method == "POST" and path == "/api/payees":
            body = self._body()
            nickname = str(body.get("nickname", "")).strip()
            bank = str(body.get("bank", ""))
            number = str(body.get("account_number", "")).strip()
            if not nickname:
                raise ApiError(422, "Enter a name for the payee.", "nickname")
            if len(nickname) > NAME_LIMIT:
                raise ApiError(422, "That name is too long.", "nickname")
            bank_id = db.scalar("select id from banks where name = %s", bank)
            if bank_id is None:
                raise ApiError(422, "Choose the payee's bank.", "bank")
            if not re.fullmatch(r"\d{2,8}", number):
                raise ApiError(422, "Account numbers are 2 to 8 digits.", "account_number")
            ident = f"p{db.next_number('payees')}"
            position = db.scalar("select count(*) from payees where customer_id = %s", me)
            db.execute("insert into payees values (%s, %s, %s, %s, %s, %s)",
                       ident, me, nickname, bank_id, number, position)
            return 201, {"id": ident}
        match = re.fullmatch(r"/api/payees/([\w-]+)", path)
        if method == "PUT" and match:
            body = self._body()
            nickname = str(body.get("nickname", "")).strip()
            if not nickname:
                raise ApiError(422, "A payee needs a name.", "nickname")
            if len(nickname) > NAME_LIMIT:
                raise ApiError(422, "That name is too long.", "nickname")
            if not db.execute("update payees set nickname = %s where id = %s and customer_id = %s",
                              nickname, match.group(1), me):
                raise ApiError(404, "No such payee.")
            return 200, {"id": match.group(1), "nickname": nickname}
        if method == "POST" and path == "/api/drafts":
            body = self._body()
            source, target = str(body.get("from", "")), str(body.get("to", ""))
            account, payee = self._check_route(db, me, source, target)
            ident = db.next_number("drafts")
            db.execute("insert into transfer_drafts values (%s, %s, %s, %s, %s, %s)",
                       ident, me, source, account, payee, available(db, source))
            return 201, {"id": ident}
        match = re.fullmatch(r"/api/drafts/(\d+)", path)
        if method == "PUT" and match:
            body = self._body()
            draft = db.first("select * from transfer_drafts where id = %s and customer_id = %s",
                             int(match.group(1)), me)
            if draft is None:
                raise ApiError(404, "That transfer has expired. Start again.")
            source, target = str(body.get("from", "")), str(body.get("to", ""))
            account, payee = self._check_route(db, me, source, target)
            snapshot = draft["available_snapshot_pence"]
            if source != draft["source_id"]:
                snapshot = available(db, source)
            db.execute("update transfer_drafts set source_id = %s, target_account_id = %s, "
                       "target_payee_id = %s, available_snapshot_pence = %s where id = %s",
                       source, account, payee, snapshot, draft["id"])
            return 200, {"id": draft["id"]}
        match = re.fullmatch(r"/api/drafts/(\d+)/quote", path)
        if method == "POST" and match:
            return self._quote(db, me, int(match.group(1)), self._body())
        match = re.fullmatch(r"/api/quotes/(\d+)/authorise", path)
        if method == "POST" and match:
            return self._authorise(db, me, int(match.group(1)))
        match = re.fullmatch(r"/api/transfers/(\d+)/cancel", path)
        if method == "POST" and match:
            row = db.first("select * from transfers where id = %s", int(match.group(1)))
            if row is None or row["status"] != "scheduled" or not owns(db, me, row["source_id"]):
                raise ApiError(409, "Only scheduled transfers can be cancelled.")
            db.execute("update transfers set status = 'cancelled' where id = %s", row["id"])
            return 200, {"id": row["id"], "status": "cancelled"}
        raise ApiError(404, "not found")

    def _check_route(self, db, me: str, source: str, target: str) -> tuple[str | None, str | None]:
        if not owns(db, me, source):
            raise ApiError(422, "Choose the account to pay from.", "from")
        account, payee = split_target(target)
        if target_name(db, me, account, payee) is None:
            raise ApiError(422, "Choose who to pay.", "to")
        if account == source:
            raise ApiError(422, "You can't move money to the same account.", "to")
        return account, payee

    def _quote(self, db, me: str, draft_id: int, body: dict):
        draft = db.first("select * from transfer_drafts where id = %s and customer_id = %s", draft_id, me)
        if draft is None:
            raise ApiError(404, "That transfer has expired. Start again.")
        amount = parse_amount(body.get("amount"))
        if amount is None or amount <= 0:
            raise ApiError(422, "Enter an amount in pounds, like 25 or 25.50.", "amount")
        when = str(body.get("when", "now"))
        if when not in WHEN_OPTIONS:
            raise ApiError(422, "Choose when to send it.", "when")
        reference = str(body.get("reference", "")).strip()
        if len(reference) > 140:
            raise ApiError(422, "References are at most 140 characters.", "reference")
        if amount > draft["available_snapshot_pence"]:
            raise ApiError(422, "That's more than the account has available.", "amount")
        ident = db.next_number("quotes")
        db.execute("insert into quotes values (%s, %s, %s, %s, %s, %s, %s, %s, false)",
                   ident, draft_id, draft["source_id"], draft["target_account_id"],
                   draft["target_payee_id"], amount, reference, when)
        after = available(db, draft["source_id"]) - amount
        return 201, {"id": ident, "from": target_name(db, me, draft["source_id"], None),
                     "to": target_name(db, me, draft["target_account_id"], draft["target_payee_id"]),
                     "amount": money(amount), "reference": reference, "when": when,
                     "date": label_date(run_date(when)), "available_after": money(after)}

    def _authorise(self, db, me: str, quote_id: int):
        quote = db.first("select q.* from quotes q join transfer_drafts d on d.id = q.draft_id "
                         "where q.id = %s and d.customer_id = %s", quote_id, me)
        if quote is None:
            raise ApiError(404, "That transfer has expired. Start again.")
        if quote["used"]:
            raise ApiError(409, "This transfer has already been authorised.")
        db.execute("update quotes set used = true where id = %s", quote_id)
        day = run_date(quote["run_when"])
        if quote["run_when"] in SCHEDULE_OPTIONS:
            status = "scheduled"
        else:
            status = "completed"
        transfer = {"id": db.next_number("transfers"), "quote_id": quote_id, "source_id": quote["source_id"],
                    "target_account_id": quote["target_account_id"], "target_payee_id": quote["target_payee_id"],
                    "amount_pence": quote["amount_pence"], "reference": quote["reference"],
                    "run_when": quote["run_when"], "run_on": day, "status": status, "authorised_by": me}
        db.execute("insert into transfers values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                   *transfer.values())
        if status == "completed":
            post_transfer(db, me, transfer)
        return 201, transfer_view(db, me, transfer)


def main() -> None:
    global VAULT  # noqa: PLW0603 - opened once, at start
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("tidewater: set DATABASE_URL to the PostgreSQL database this server may own")
    VAULT = Vault(url, int(os.environ.get("WTA_SESSION_CAP") or 32))
    VAULT.open()
    VAULT.rebuild()
    host = os.environ.get("HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, int(os.environ["PORT"])), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
