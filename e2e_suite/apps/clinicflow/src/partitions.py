"""Clinicflow's storage: a PostgreSQL database of its own, one schema per signed-in browser.

The database comes from DATABASE_URL and belongs to this server. When the server starts it
empties it and builds two schemas:

* ``app_meta`` -- ``session_map`` (which ``wta_sid`` cookie owns which schema, which member
  of staff is signed in there, and a touch counter for eviction), ``tallies`` (counters),
  and ``staff_logins`` (usernames with their PBKDF2 hashes);
* ``tmpl``     -- ``schema.sql`` filled with the clinic as it is on the first morning.

Signing in for the first time in a browser copies ``tmpl`` into ``s_000123`` with a single
round trip. Signing out forgets who is signed in but keeps the schema, so signing in again
in that browser finds everything as it was. When more than WTA_SESSION_CAP schemas exist
the least recently touched one is dropped. Requests never interleave: one connection, one
lock, and every request is ``BEGIN; SET LOCAL search_path ...; ...; COMMIT``.
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
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "python"))
import pg8000.native  # noqa: E402 - pinned by the suite and shipped in every build

DDL = (Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
TABLES = re.findall(r"^CREATE TABLE (\w+)", DDL, re.MULTILINE)
SID = re.compile(r"cf\d{6}")
SCHEMA = re.compile(r"s_\d{6}")
TOUCH = ("WITH clock AS (UPDATE app_meta.tallies SET n = n + 1 WHERE what = 'touch' RETURNING n) "
         "UPDATE app_meta.session_map m SET touched = clock.n FROM clock WHERE m.sid = :sid "
         "RETURNING m.schema_name, m.staff_id")
META = """
CREATE SCHEMA app_meta;
CREATE TABLE app_meta.tallies (what TEXT PRIMARY KEY, n INTEGER NOT NULL);
CREATE TABLE app_meta.staff_logins (
    username TEXT PRIMARY KEY, staff_id TEXT NOT NULL UNIQUE,
    salt TEXT NOT NULL, pbkdf2 TEXT NOT NULL, rounds INTEGER NOT NULL);
CREATE TABLE app_meta.session_map (
    sid TEXT PRIMARY KEY, schema_name TEXT NOT NULL UNIQUE, staff_id TEXT, touched INTEGER NOT NULL);
INSERT INTO app_meta.tallies VALUES ('schema', 0), ('touch', 0);
"""


class Records:
    """The open transaction of one request, with its schema on the search path."""

    def __init__(self, link, sid, schema, staff_id) -> None:
        self.link, self.sid, self.schema, self.staff_id = link, sid, schema, staff_id

    def rows(self, sql: str, **params) -> list[dict]:
        found = self.link.run(sql, **params)
        keys = [c["name"] for c in self.link.columns or ()]
        return [dict(zip(keys, values)) for values in found]

    def row(self, sql: str, **params) -> dict | None:
        found = self.rows(sql, **params)
        return found[0] if found else None

    def value(self, sql: str, **params):
        found = self.link.run(sql, **params)
        return found[0][0] if found else None

    def do(self, sql: str, **params) -> None:
        self.link.run(sql, **params)


class Partitions:
    def __init__(self, url: str, cap: int) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("postgresql", "postgres") or not parts.hostname:
            raise SystemExit("clinicflow: DATABASE_URL is not a PostgreSQL connection URL")
        self.login = dict(user=unquote(parts.username or ""), password=unquote(parts.password or ""),
                          host=parts.hostname, port=parts.port or 5432,
                          database=unquote(parts.path.lstrip("/")), application_name="clinicflow")
        self.cap = max(1, cap)
        self.guard = threading.Lock()
        self.link = None

    def _connection(self):
        if self.link is None:
            self.link = pg8000.native.Connection(timeout=15, **self.login)
        return self.link

    def _forget_connection(self) -> None:
        if self.link is not None:
            with contextlib.suppress(Exception):
                self.link.close()
        self.link = None

    def wait(self, seconds: float = 30.0) -> None:
        """Connect, retrying while the database server starts."""
        stop = time.monotonic() + seconds
        while True:
            try:
                self._connection().run("SELECT 1")
                return
            except (pg8000.native.InterfaceError, OSError):
                self._forget_connection()
                if time.monotonic() > stop:
                    raise
                time.sleep(0.5)

    def alive(self) -> bool:
        with self.guard:
            try:
                self._connection().run("SELECT 1")
                return True
            except Exception:  # noqa: BLE001 - any failure: the database is not answering
                self._forget_connection()
                return False

    def reset(self, template: dict[str, list[dict]], staff: list[dict], rounds: int) -> None:
        """Empty this server's database; build the registry, the logins and the template."""
        with self.guard:
            link = self._connection()
            for (name,) in link.run("SELECT nspname FROM pg_namespace WHERE nspname ~ '^s_[0-9]{6}$' "
                                    "OR nspname IN ('app_meta', 'tmpl') ORDER BY 1"):
                link.run(f'DROP SCHEMA "{name}" CASCADE')
            link.run("BEGIN")
            try:
                link.run(META)
                for person in staff:
                    link.run("INSERT INTO app_meta.staff_logins VALUES (:u, :s, :salt, :pbkdf2, :rounds)",
                             u=person["username"], s=person["id"], salt=person["salt"],
                             pbkdf2=person["pbkdf2"], rounds=rounds)
                link.run("CREATE SCHEMA tmpl")
                link.run("SET LOCAL search_path TO tmpl")
                link.run(DDL)
                for table in TABLES:
                    if template.get(table):
                        link.run(f"INSERT INTO {table} SELECT * FROM "
                                 f"json_populate_recordset(null::{table}, CAST(:payload AS json))",
                                 payload=json.dumps(template[table]))
                link.run("COMMIT")
            except BaseException:
                link.run("ROLLBACK")
                raise

    def check_password(self, username: str, password: str) -> str | None:
        """The staff id these credentials belong to, or None; the PBKDF2 work is done
        outside the lock, and an unknown username does the same work."""
        with self.guard:
            found = self._connection().run(
                "SELECT staff_id, salt, pbkdf2, rounds FROM app_meta.staff_logins WHERE username = :u",
                u=username)
        staff_id, salt, expected, rounds = found[0] if found else (None, "ff" * 16, "", 20000)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), rounds).hex()
        return staff_id if staff_id and hmac.compare_digest(actual, expected) else None

    def sign_in(self, sid: str | None, staff_id: str) -> str:
        """Attach `staff_id` to this browser's schema, creating the schema on first sign-in."""
        if not re.fullmatch(r"[a-z]+", staff_id):
            raise ValueError("unexpected staff id")
        with self.guard:
            link = self._connection()
            if sid and SID.fullmatch(sid) and link.run(TOUCH, sid=sid):
                link.run("UPDATE app_meta.session_map SET staff_id = :who WHERE sid = :sid",
                         who=staff_id, sid=sid)
                return sid
            number = link.run("UPDATE app_meta.tallies SET n = n + 1 WHERE what = 'schema' RETURNING n")[0][0]
            touched = link.run("UPDATE app_meta.tallies SET n = n + 1 WHERE what = 'touch' RETURNING n")[0][0]
            sid, schema = f"cf{number:06d}", f"s_{number:06d}"
            copy = "".join(f"INSERT INTO {table} SELECT * FROM tmpl.{table};\n" for table in TABLES)
            try:
                link.run(f"BEGIN;\nCREATE SCHEMA {schema};\nSET LOCAL search_path TO {schema};\n{DDL}\n{copy}"
                         f"INSERT INTO app_meta.session_map VALUES ('{sid}', '{schema}', '{staff_id}', {touched});\n"
                         "COMMIT;")
            except BaseException:
                with contextlib.suppress(Exception):
                    link.run("ROLLBACK")
                raise
            surplus = link.run("SELECT schema_name FROM app_meta.session_map ORDER BY touched DESC OFFSET :keep",
                               keep=self.cap)
            for (old,) in surplus:
                if SCHEMA.fullmatch(old):
                    link.run(f"BEGIN; DROP SCHEMA {old} CASCADE; "
                             f"DELETE FROM app_meta.session_map WHERE schema_name = '{old}'; COMMIT;")
            return sid

    def sign_out(self, sid: str | None) -> None:
        if sid and SID.fullmatch(sid):
            with self.guard:
                self._connection().run("UPDATE app_meta.session_map SET staff_id = NULL WHERE sid = :sid",
                                       sid=sid)

    @contextlib.contextmanager
    def visit(self, sid: str | None):
        """One request's transaction on its browser's schema (none for an unknown cookie)."""
        with self.guard:
            link = self._connection()
            try:
                link.run("BEGIN")
                mapped = link.run(TOUCH, sid=sid) if sid and SID.fullmatch(sid) else []
                schema, staff_id = mapped[0] if mapped and SCHEMA.fullmatch(mapped[0][0]) else (None, None)
                if schema:
                    link.run(f"SET LOCAL search_path TO {schema}")
                yield Records(link, sid if schema else None, schema, staff_id)
                link.run("COMMIT")
            except BaseException as problem:
                try:
                    link.run("ROLLBACK")
                except Exception:  # noqa: BLE001 - the connection is gone
                    self._forget_connection()
                if isinstance(problem, (pg8000.native.InterfaceError, OSError)):
                    self._forget_connection()
                raise
