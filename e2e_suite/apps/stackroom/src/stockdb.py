"""Stackroom's database plumbing: one PostgreSQL connection, one schema per signed-in session.

* **Home.** The database named by `DATABASE_URL` belongs to this server alone. At start it is
  reset: the `app_meta` schema (the session registry, the counters and the accounts) and the
  `tmpl` schema (src/schema.sql filled from seed/) are rebuilt, and every session schema left
  over from an earlier run is dropped.
* **Partitions.** The first successful sign-in in a browser context copies `tmpl` into a new
  schema `s_000017` in one round trip and maps the `wta_sid` cookie (`sr000017`) to it.
  Signing out keeps the schema, so signing in again in the same context resumes the same
  stockroom; anonymous requests create nothing. Above `WTA_SESSION_CAP` schemas the least
  recently used one is dropped.
* **Serialised.** One connection and one lock: a request runs as `BEGIN; SET LOCAL
  search_path TO <its schema>; ...; COMMIT` with no other request interleaved, which is what
  keeps a rapid double click deterministic.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "python"))
import pg8000.native  # noqa: E402 - the suite's pinned driver, copied into every build

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = (Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
SEED = json.loads((ROOT / "seed" / "stockroom.json").read_text(encoding="utf-8"))
ACCOUNTS = json.loads((ROOT / "seed" / "users.json").read_text(encoding="utf-8"))
#: Tables in the order schema.sql creates them, which is an order foreign keys allow.
TABLES = tuple(re.findall(r"^CREATE TABLE (\w+)", SCHEMA_SQL, re.MULTILINE))
SID = re.compile(r"sr\d{6}")
PARTITION = re.compile(r"s_\d{6}")
CONNECT_TIMEOUT_S = 30.0

#: The registry, shared by every session of this server.
META_SQL = """
CREATE SCHEMA app_meta;
CREATE TABLE app_meta.counters (name text PRIMARY KEY, value integer NOT NULL CHECK (value >= 0));
CREATE TABLE app_meta.accounts (
    username   text PRIMARY KEY,
    user_id    text NOT NULL UNIQUE,
    salt       text NOT NULL,
    hash       text NOT NULL,
    iterations integer NOT NULL CHECK (iterations >= 20000));
CREATE TABLE app_meta.sessions (
    sid       text PRIMARY KEY,
    partition text NOT NULL UNIQUE,
    user_id   text,
    touched   integer NOT NULL);
CREATE INDEX sessions_by_use ON app_meta.sessions (touched);
INSERT INTO app_meta.counters (name, value) VALUES ('partitions', 0), ('ticks', 0);
"""
#: Every request of a known session moves it to the front of the eviction order.
TOUCH = ("WITH tick AS (UPDATE app_meta.counters SET value = value + 1 WHERE name = 'ticks' "
         "RETURNING value) UPDATE app_meta.sessions SET touched = tick.value FROM tick "
         "WHERE sid = :sid RETURNING partition, user_id")


@dataclass(frozen=True)
class Visit:
    """One request's view of its session: the cookie, the schema and who is signed in."""

    db: "Db"
    sid: Optional[str]
    partition: Optional[str]
    user_id: Optional[str]


class Db:
    """Queries on the request's connection; rows come back as dicts."""

    def __init__(self, connection: pg8000.native.Connection) -> None:
        self.connection = connection

    def all(self, sql: str, **params) -> list[dict]:
        rows = self.connection.run(sql, **params)
        names = [column["name"] for column in (self.connection.columns or [])]
        return [dict(zip(names, row)) for row in rows]

    def one(self, sql: str, **params) -> Optional[dict]:
        rows = self.all(sql, **params)
        return rows[0] if rows else None

    def value(self, sql: str, **params):
        rows = self.connection.run(sql, **params)
        return rows[0][0] if rows else None

    def run(self, sql: str, **params) -> None:
        self.connection.run(sql, **params)


def _connect(url: str) -> pg8000.native.Connection:
    parts = urlsplit(url)
    if parts.scheme not in ("postgres", "postgresql") or not parts.path.strip("/"):
        raise ValueError("DATABASE_URL must be a PostgreSQL URL naming user, password, host and database")
    return pg8000.native.Connection(
        unquote(parts.username or ""), password=unquote(parts.password or ""),
        host=parts.hostname or "127.0.0.1", port=parts.port or 5432,
        database=unquote(parts.path.strip("/")), timeout=15, application_name="stackroom")


class Store:
    """The server's single connection, its lock, and the session partitions."""

    def __init__(self, url: str, cap: int) -> None:
        self.url, self.cap = url, max(1, cap)
        self.lock = threading.Lock()
        self.connection: Optional[pg8000.native.Connection] = None
        self.copy_seed = "".join(f"INSERT INTO {t} SELECT * FROM tmpl.{t};\n" for t in TABLES)

    # -- connection -------------------------------------------------------------------------

    def _live(self) -> pg8000.native.Connection:
        if self.connection is None:
            self.connection = _connect(self.url)
        return self.connection

    def _discard(self) -> None:
        if self.connection is not None:
            with contextlib.suppress(Exception):
                self.connection.close()
        self.connection = None

    def wait_for_server(self) -> None:
        """Connect, retrying while the database server is still starting."""
        deadline = time.monotonic() + CONNECT_TIMEOUT_S
        while True:
            try:
                self._live().run("SELECT 1")
                return
            except (pg8000.native.InterfaceError, OSError):
                self._discard()
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)

    def ping(self) -> bool:
        with self.lock:
            try:
                self._live().run("SELECT 1")
                return True
            except Exception:  # noqa: BLE001 - any failure means "not reachable"
                self._discard()
                return False

    # -- the home: registry and seed template ------------------------------------------------

    def install(self) -> None:
        """Reset this server's home: registry, accounts and the seeded template schema."""
        with self.lock:
            connection = self._live()
            stale = [r[0] for r in connection.run(
                "SELECT nspname FROM pg_namespace WHERE nspname ~ '^s_[0-9]{6}$' "
                "OR nspname IN ('app_meta', 'tmpl')")]
            for schema in stale:
                connection.run(f'DROP SCHEMA "{schema}" CASCADE')
            connection.run("BEGIN")
            try:
                connection.run(META_SQL)
                for user in ACCOUNTS["users"]:
                    connection.run(
                        "INSERT INTO app_meta.accounts (username, user_id, salt, hash, iterations) "
                        "VALUES (:username, :id, :salt, :hash, :iterations)",
                        username=user["username"], id=user["id"], salt=user["salt"],
                        hash=user["hash"], iterations=ACCOUNTS["iterations"])
                connection.run("CREATE SCHEMA tmpl")
                connection.run("SET LOCAL search_path TO tmpl")
                connection.run(SCHEMA_SQL)
                for table, rows in self._seed_rows().items():
                    connection.run(f"INSERT INTO {table} SELECT * FROM "
                                   f"json_populate_recordset(NULL::{table}, CAST(:rows AS json))",
                                   rows=json.dumps(rows))
                connection.run("COMMIT")
            except BaseException:
                connection.run("ROLLBACK")
                raise

    @staticmethod
    def _seed_rows() -> dict[str, list[dict]]:
        profiles = [{k: u[k] for k in ("id", "username", "name", "role")} for u in ACCOUNTS["users"]]
        rows = {"users": profiles, **{t: SEED[t] for t in TABLES if t in SEED}}
        rows["counters"] = [{"name": "journal", "value": max((e["seq"] for e in SEED["journal"]), default=0)}]
        return {t: rows[t] for t in TABLES if rows.get(t)}

    # -- sign-in, sign-out, eviction ---------------------------------------------------------

    def account(self, username: str, password: str) -> Optional[str]:
        """The user id `username` signs in as with `password`, or None. The PBKDF2 check
        (seeded salt, 20,000 iterations) runs outside the lock; an unknown name pays it too."""
        with self.lock:
            rows = self._live().run("SELECT user_id, salt, hash, iterations FROM app_meta.accounts "
                                    "WHERE username = :username", username=username)
        user_id, salt, expected, iterations = rows[0] if rows else (None, "00" * 16, "", 20000)
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt),
                                      iterations).hex()
        return user_id if user_id is not None and hmac.compare_digest(derived, expected) else None

    def _bump(self, connection, name: str) -> int:
        return connection.run("UPDATE app_meta.counters SET value = value + 1 WHERE name = :name "
                              "RETURNING value", name=name)[0][0]

    def sign_in(self, sid: Optional[str], user_id: str) -> str:
        """Sign `user_id` in on the browser context's partition, creating it (and evicting
        the least recently used one beyond the cap) when the context has none; return the
        session id the cookie must carry."""
        if not re.fullmatch(r"[a-z][a-z0-9_.-]*", user_id):
            raise ValueError(f"unexpected user id {user_id!r}")
        with self.lock:
            connection = self._live()
            if sid and SID.fullmatch(sid):
                if connection.run(TOUCH, sid=sid):
                    connection.run("UPDATE app_meta.sessions SET user_id = :user WHERE sid = :sid",
                                   user=user_id, sid=sid)
                    return sid
            number = self._bump(connection, "partitions")
            tick = self._bump(connection, "ticks")
            sid, partition = f"sr{number:06d}", f"s_{number:06d}"
            try:  # one simple-protocol round trip: schema, tables, seed copy, registry row
                connection.run(
                    f"BEGIN; CREATE SCHEMA {partition}; SET LOCAL search_path TO {partition};\n"
                    f"{SCHEMA_SQL}\n{self.copy_seed}"
                    f"INSERT INTO app_meta.sessions (sid, partition, user_id, touched) "
                    f"VALUES ('{sid}', '{partition}', '{user_id}', {tick}); COMMIT;")
            except BaseException:
                with contextlib.suppress(Exception):
                    connection.run("ROLLBACK")
                raise
            for (stale,) in connection.run("SELECT partition FROM app_meta.sessions "
                                           "ORDER BY touched DESC OFFSET :cap", cap=self.cap):
                if PARTITION.fullmatch(stale):
                    connection.run(f"BEGIN; DROP SCHEMA {stale} CASCADE; DELETE FROM "
                                   f"app_meta.sessions WHERE partition = '{stale}'; COMMIT;")
            return sid

    def sign_out(self, sid: Optional[str]) -> None:
        """Forget who is signed in; keep the partition for a later sign-in in this context."""
        if sid and SID.fullmatch(sid):
            with self.lock:
                self._live().run("UPDATE app_meta.sessions SET user_id = NULL WHERE sid = :sid", sid=sid)

    # -- requests ------------------------------------------------------------------------------

    @contextlib.contextmanager
    def visit(self, sid: Optional[str]):
        """One request: the lock, a transaction, and the session's schema on the search path.
        Commits when the block completes, rolls back when it raises."""
        with self.lock:
            connection = self._live()
            try:
                connection.run("BEGIN")
                found = connection.run(TOUCH, sid=sid) if sid and SID.fullmatch(sid) else []
                partition, user_id = found[0] if found else (None, None)
                if partition is not None and PARTITION.fullmatch(partition):
                    connection.run(f"SET LOCAL search_path TO {partition}")
                else:
                    partition = None
                yield Visit(Db(connection), sid if partition else None, partition, user_id)
                connection.run("COMMIT")
            except BaseException as exc:
                try:
                    connection.run("ROLLBACK")
                except Exception:  # noqa: BLE001 - the connection itself is gone
                    self._discard()
                if isinstance(exc, (pg8000.native.InterfaceError, OSError)):
                    self._discard()
                raise
