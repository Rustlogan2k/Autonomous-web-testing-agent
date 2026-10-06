"""Tidewater — personal banking. A single-page app over a JSON API, standard library only.

State lives in SQLite under WTA_DATA_DIR, partitioned by the `wta_sid` session cookie and
seeded from seed/seed.json on a session's first request.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

APP_ID = "tidewater"
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
SEED = json.loads((ROOT.parent / "seed" / "seed.json").read_text(encoding="utf-8"))
VOCAB = json.loads((ROOT.parent / "vocab" / f"{os.environ.get('WTA_VOCAB', 'default')}.json")
                   .read_text(encoding="utf-8"))
NOW = dt.datetime.fromisoformat(os.environ.get("WTA_FIXED_NOW", "2026-06-10T09:00:00"))  # wta-clock
DATA_DIR = Path(os.environ.get("WTA_DATA_DIR") or tempfile.mkdtemp(prefix="tidewater-"))
DB_PATH = DATA_DIR / "tidewater.sqlite3"
TEST_HOOKS = os.environ.get("WTA_TEST_HOOKS") == "1"
SCHEDULE_OPTIONS = ("next-friday", "month-end")
WHEN_OPTIONS = ("now",) + SCHEDULE_OPTIONS
BANKS = ("Harbour Bank", "Northgate", "Coastal Mutual", "Fernway")
NAME_LIMIT = 5000
_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER);
CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS accounts (sid TEXT, id TEXT, name TEXT, number TEXT, balance INTEGER,
    PRIMARY KEY (sid, id));
CREATE TABLE IF NOT EXISTS payees (sid TEXT, id TEXT, nickname TEXT, bank TEXT, account_number TEXT,
    position INTEGER, PRIMARY KEY (sid, id));
CREATE TABLE IF NOT EXISTS transactions (sid TEXT, seq INTEGER, account TEXT, date TEXT,
    description TEXT, amount INTEGER);
CREATE TABLE IF NOT EXISTS drafts (sid TEXT, id INTEGER, source TEXT, target TEXT,
    available_snapshot INTEGER, PRIMARY KEY (sid, id));
CREATE TABLE IF NOT EXISTS quotes (sid TEXT, id INTEGER, draft INTEGER, source TEXT, target TEXT,
    amount INTEGER, reference TEXT, run_when TEXT, used INTEGER DEFAULT 0, PRIMARY KEY (sid, id));
CREATE TABLE IF NOT EXISTS transfers (sid TEXT, id INTEGER, source TEXT, target TEXT, amount INTEGER,
    reference TEXT, run_when TEXT, run_date TEXT, status TEXT, PRIMARY KEY (sid, id));
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def next_id(conn: sqlite3.Connection, key: str) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    value = (row["value"] if row else 0) + 1
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
    return value


def seed_session(conn: sqlite3.Connection, sid: str) -> None:
    conn.execute("INSERT INTO sessions (sid) VALUES (?)", (sid,))
    for a in SEED["accounts"]:
        conn.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                     (sid, a["id"], a["name"], a["number"], a["balance"]))
    for position, p in enumerate(SEED["payees"]):
        conn.execute("INSERT INTO payees VALUES (?, ?, ?, ?, ?, ?)",
                     (sid, p["id"], p["nickname"], p["bank"], p["account_number"], position))
    for seq, t in enumerate(SEED["transactions"]):
        conn.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?)",
                     (sid, seq, t["account"], t["date"], t["description"], t["amount"]))


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}£{abs(cents) / 100:,.2f}"


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


# -- domain --------------------------------------------------------------------------------


def accounts(conn, sid) -> list[dict]:
    rows = conn.execute("SELECT * FROM accounts WHERE sid = ? ORDER BY rowid", (sid,)).fetchall()
    scheduled = {r["source"]: r["total"] for r in conn.execute(
        "SELECT source, SUM(amount) AS total FROM transfers WHERE sid = ? AND status = 'scheduled' "
        "GROUP BY source", (sid,))}
    return [{"id": r["id"], "name": r["name"], "number": r["number"], "balance": r["balance"],
             "balance_label": money(r["balance"]), "scheduled": scheduled.get(r["id"], 0),
             "scheduled_label": money(scheduled.get(r["id"], 0))} for r in rows]


def available(conn, sid, account_id) -> int | None:
    row = conn.execute("SELECT balance FROM accounts WHERE sid = ? AND id = ?", (sid, account_id)).fetchone()
    return None if row is None else row["balance"]


def payees(conn, sid) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, nickname, bank, account_number FROM payees WHERE sid = ? ORDER BY position", (sid,))]


def target_name(conn, sid, target: str) -> str | None:
    kind, _, ident = target.partition(":")
    if kind == "acct":
        row = conn.execute("SELECT name FROM accounts WHERE sid = ? AND id = ?", (sid, ident)).fetchone()
        return row["name"] if row else None
    if kind == "payee":
        row = conn.execute("SELECT nickname FROM payees WHERE sid = ? AND id = ?", (sid, ident)).fetchone()
        return row["nickname"] if row else None
    return None


def transfer_view(conn, sid, row) -> dict:
    return {"id": row["id"], "from": target_name(conn, sid, "acct:" + row["source"]),
            "to": target_name(conn, sid, row["target"]) or "(removed payee)",
            "amount": money(row["amount"]), "reference": row["reference"],
            "when": row["run_when"], "date": label_date(dt.date.fromisoformat(row["run_date"])),
            "status": row["status"]}


def post_transfer(conn, sid, source: str, target: str, amount: int, reference: str, day: dt.date) -> None:
    seq = next_id(conn, f"tx:{sid}")
    to_label = target_name(conn, sid, target)
    from_label = target_name(conn, sid, "acct:" + source)
    conn.execute("UPDATE accounts SET balance = balance - ? WHERE sid = ? AND id = ?", (amount, sid, source))
    conn.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?)",
                 (sid, 1000 + seq, source, day.isoformat(), f"To {to_label}" + (f" — {reference}" if reference else ""), -amount))
    if target.startswith("acct:"):
        conn.execute("UPDATE accounts SET balance = balance + ? WHERE sid = ? AND id = ?",
                     (amount, sid, target[5:]))
        conn.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?)",
                     (sid, 1000 + seq, target[5:], day.isoformat(), f"From {from_label}", amount))


def committed_state(conn, sid) -> dict:
    return {
        "accounts": [dict(r) for r in conn.execute(
            "SELECT id, balance FROM accounts WHERE sid = ? ORDER BY id", (sid,))],
        "payees": [dict(r) for r in conn.execute(
            "SELECT id, nickname, bank, account_number FROM payees WHERE sid = ? ORDER BY id", (sid,))],
        "transfers": [dict(r) for r in conn.execute(
            "SELECT id, source, target, amount, reference, run_when, run_date, status FROM transfers "
            "WHERE sid = ? ORDER BY id", (sid,))],
        "transactions": conn.execute("SELECT COUNT(*) AS n FROM transactions WHERE sid = ?",
                                     (sid,)).fetchone()["n"],
    }


# -- HTTP ----------------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, status: int, message: str, field: str = "") -> None:
        super().__init__(message)
        self.status, self.message, self.field = status, message, field


class Handler(BaseHTTPRequestHandler):
    server_version = "Tidewater/1.0"

    def log_message(self, fmt, *args):  # quiet, deterministic logs
        pass

    # session ------------------------------------------------------------------------------
    def _session(self, conn) -> str:
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        sid = jar["wta_sid"].value if "wta_sid" in jar else ""
        if sid and conn.execute("SELECT 1 FROM sessions WHERE sid = ?", (sid,)).fetchone():
            return sid
        sid = f"s{next_id(conn, 'session'):06d}"
        seed_session(conn, sid)
        self._new_cookie = sid
        return sid

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "_new_cookie", None):
            self.send_header("Set-Cookie", f"wta_sid={self._new_cookie}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
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

    def _dispatch(self, method: str) -> None:
        self._new_cookie = None
        path = urlsplit(self.path).path
        if method == "GET" and path == "/healthz":
            return self._json(200, {"ok": True, "app": APP_ID})
        if method == "GET" and not path.startswith(("/api/", "/__wta/")):
            return self._static(path)
        with _LOCK:
            conn = connect()
            try:
                sid = self._session(conn)
                status, payload = self._api(conn, sid, method, path)
                conn.commit()
            except ApiError as exc:
                conn.commit()  # a new session's seed is kept even when the request fails
                status, payload = exc.status, {"error": exc.message, "field": exc.field}
            finally:
                conn.close()
        self._json(status, payload)

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        target = (STATIC / name).resolve()
        if STATIC not in target.parents and target != STATIC / "index.html" or not target.is_file():
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        kinds = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
        with _LOCK:  # create the session on the first page load, so its cookie is set early
            conn = connect()
            try:
                self._session(conn)
                conn.commit()
            finally:
                conn.close()
        self._send(200, target.read_bytes(), kinds.get(target.suffix, "application/octet-stream"))

    # API ----------------------------------------------------------------------------------
    def _api(self, conn, sid: str, method: str, path: str):  # noqa: C901, PLR0911, PLR0912
        query = parse_qs(urlsplit(self.path).query)
        if method == "GET" and path == "/__wta/state":
            if not TEST_HOOKS:
                raise ApiError(404, "not found")
            committed = committed_state(conn, sid)
            sha = hashlib.sha256(json.dumps(committed, sort_keys=True).encode()).hexdigest()
            return 200, {"session": sid, "committed": committed, "sha": sha}
        if method == "GET" and path == "/api/vocab":
            return 200, VOCAB
        if method == "GET" and path == "/api/overview":
            transfers = [transfer_view(conn, sid, r) for r in conn.execute(
                "SELECT * FROM transfers WHERE sid = ? AND status = 'scheduled' ORDER BY id", (sid,))]
            return 200, {"accounts": accounts(conn, sid), "scheduled": transfers,
                         "today": label_date(NOW.date())}
        if method == "GET" and path == "/api/payees":
            return 200, {"payees": payees(conn, sid), "banks": list(BANKS)}
        if method == "POST" and path == "/api/payees":
            body = self._body()
            nickname = str(body.get("nickname", "")).strip()
            bank = str(body.get("bank", ""))
            number = str(body.get("account_number", "")).strip()
            if not nickname:
                raise ApiError(422, "Enter a name for the payee.", "nickname")
            if len(nickname) > NAME_LIMIT:
                raise ApiError(422, "That name is too long.", "nickname")
            if bank not in BANKS:
                raise ApiError(422, "Choose the payee's bank.", "bank")
            if not re.fullmatch(r"\d{2,8}", number):
                raise ApiError(422, "Account numbers are 2 to 8 digits.", "account_number")
            ident = f"p{next_id(conn, f'payee:{sid}')}"
            position = conn.execute("SELECT COUNT(*) AS n FROM payees WHERE sid = ?", (sid,)).fetchone()["n"]
            conn.execute("INSERT INTO payees VALUES (?, ?, ?, ?, ?, ?)",
                         (sid, ident, nickname, bank, number, position))
            return 201, {"id": ident}
        match = re.fullmatch(r"/api/payees/([\w-]+)", path)
        if method == "PUT" and match:
            body = self._body()
            nickname = str(body.get("nickname", "")).strip()
            if not nickname:
                raise ApiError(422, "A payee needs a name.", "nickname")
            if len(nickname) > NAME_LIMIT:
                raise ApiError(422, "That name is too long.", "nickname")
            updated = conn.execute("UPDATE payees SET nickname = ? WHERE sid = ? AND id = ?",
                                   (nickname, sid, match.group(1))).rowcount
            if not updated:
                raise ApiError(404, "No such payee.")
            return 200, {"id": match.group(1), "nickname": nickname}
        if method == "POST" and path == "/api/drafts":
            body = self._body()
            source, target = str(body.get("from", "")), str(body.get("to", ""))
            self._check_route(conn, sid, source, target)
            ident = next_id(conn, f"draft:{sid}")
            conn.execute("INSERT INTO drafts VALUES (?, ?, ?, ?, ?)",
                         (sid, ident, source, target, available(conn, sid, source)))
            return 201, {"id": ident}
        match = re.fullmatch(r"/api/drafts/(\d+)", path)
        if method == "PUT" and match:
            body = self._body()
            draft = conn.execute("SELECT * FROM drafts WHERE sid = ? AND id = ?",
                                 (sid, int(match.group(1)))).fetchone()
            if draft is None:
                raise ApiError(404, "That transfer has expired. Start again.")
            source, target = str(body.get("from", "")), str(body.get("to", ""))
            self._check_route(conn, sid, source, target)
            snapshot = draft["available_snapshot"]
            if source != draft["source"]:
                snapshot = available(conn, sid, source)
            conn.execute("UPDATE drafts SET source = ?, target = ?, available_snapshot = ? "
                         "WHERE sid = ? AND id = ?", (source, target, snapshot, sid, draft["id"]))
            return 200, {"id": draft["id"]}
        match = re.fullmatch(r"/api/drafts/(\d+)/quote", path)
        if method == "POST" and match:
            return self._quote(conn, sid, int(match.group(1)), self._body())
        match = re.fullmatch(r"/api/quotes/(\d+)/authorise", path)
        if method == "POST" and match:
            return self._authorise(conn, sid, int(match.group(1)))
        match = re.fullmatch(r"/api/transfers/(\d+)/cancel", path)
        if method == "POST" and match:
            row = conn.execute("SELECT * FROM transfers WHERE sid = ? AND id = ?",
                               (sid, int(match.group(1)))).fetchone()
            if row is None or row["status"] != "scheduled":
                raise ApiError(409, "Only scheduled transfers can be cancelled.")
            conn.execute("UPDATE transfers SET status = 'cancelled' WHERE sid = ? AND id = ?", (sid, row["id"]))
            return 200, {"id": row["id"], "status": "cancelled"}
        if method == "GET" and path == "/api/statements":
            account = (query.get("account") or ["all"])[0]
            direction = (query.get("direction") or ["all"])[0]
            sql = "SELECT * FROM transactions WHERE sid = ?"
            args: list = [sid]
            if account != "all":
                sql += " AND account = ?"
                args.append(account)
            if direction == "in":
                sql += " AND amount > 0"
            elif direction == "out":
                sql += " AND amount < 0"
            rows = conn.execute(sql + " ORDER BY date DESC, seq DESC", args).fetchall()
            names = {a["id"]: a["name"] for a in accounts(conn, sid)}
            return 200, {"rows": [{"date": r["date"], "account": names.get(r["account"], r["account"]),
                                   "description": r["description"], "amount": money(r["amount"])}
                                  for r in rows],
                         "count": len(rows)}
        raise ApiError(404, "not found")

    def _check_route(self, conn, sid, source: str, target: str) -> None:
        if available(conn, sid, source) is None:
            raise ApiError(422, "Choose the account to pay from.", "from")
        if target_name(conn, sid, target) is None:
            raise ApiError(422, "Choose who to pay.", "to")
        if target == "acct:" + source:
            raise ApiError(422, "You can't move money to the same account.", "to")

    def _quote(self, conn, sid: str, draft_id: int, body: dict):
        draft = conn.execute("SELECT * FROM drafts WHERE sid = ? AND id = ?", (sid, draft_id)).fetchone()
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
        if amount > draft["available_snapshot"]:
            raise ApiError(422, "That's more than the account has available.", "amount")
        ident = next_id(conn, f"quote:{sid}")
        conn.execute("INSERT INTO quotes (sid, id, draft, source, target, amount, reference, run_when) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (sid, ident, draft_id, draft["source"], draft["target"], amount, reference, when))
        after = available(conn, sid, draft["source"]) - amount
        return 201, {"id": ident, "from": target_name(conn, sid, "acct:" + draft["source"]),
                     "to": target_name(conn, sid, draft["target"]), "amount": money(amount),
                     "reference": reference, "when": when, "date": label_date(run_date(when)),
                     "available_after": money(after)}

    def _authorise(self, conn, sid: str, quote_id: int):
        quote = conn.execute("SELECT * FROM quotes WHERE sid = ? AND id = ?", (sid, quote_id)).fetchone()
        if quote is None:
            raise ApiError(404, "That transfer has expired. Start again.")
        if quote["used"]:
            raise ApiError(409, "This transfer has already been authorised.")
        conn.execute("UPDATE quotes SET used = 1 WHERE sid = ? AND id = ?", (sid, quote_id))
        day = run_date(quote["run_when"])
        ident = next_id(conn, f"transfer:{sid}")
        if quote["run_when"] in SCHEDULE_OPTIONS:
            status = "scheduled"
        else:
            status = "completed"
            post_transfer(conn, sid, quote["source"], quote["target"], quote["amount"], quote["reference"], day)
        conn.execute("INSERT INTO transfers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (sid, ident, quote["source"], quote["target"], quote["amount"], quote["reference"],
                      quote["run_when"], day.isoformat(), status))
        row = conn.execute("SELECT * FROM transfers WHERE sid = ? AND id = ?", (sid, ident)).fetchone()
        return 201, transfer_view(conn, sid, row)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    host = os.environ.get("HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, int(os.environ["PORT"])), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
