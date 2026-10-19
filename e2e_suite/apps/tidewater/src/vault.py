"""Where Tidewater keeps its data: PostgreSQL, one schema per signed-in browser context.

The database named by DATABASE_URL belongs to this server. On start the server wipes what
an earlier run left there and lays out:

* `app_meta.logins`: one row per browser context that has signed in (the `wta_sid` cookie),
  naming its schema, who is signed in now (NULL once they sign out) and when it was last
  seen, as a tick of `app_meta.ticks` rather than a time;
* `tmpl`: src/schema.sql filled from seed/seed.json, the bank as every session first sees it.

A context's first successful sign-in clones `tmpl` into a schema of its own (`s_000042`).
Signing out keeps the clone, so signing back in within the same browser picks up where it
left off. Above WTA_SESSION_CAP clones the one seen least recently is dropped.

Everything goes through one connection under one lock, so two requests never interleave.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "python"))
import pg8000.dbapi  # noqa: E402 - the suite's pinned driver, shipped with every build

HERE = Path(__file__).resolve().parent
TABLE_DDL = (HERE / "schema.sql").read_text(encoding="utf-8")
SEED_ROWS = json.loads((HERE.parent / "seed" / "seed.json").read_text(encoding="utf-8"))
#: Table names in creation order; schema.sql creates each after the tables it refers to.
TABLE_ORDER = re.findall(r"^create table (\w+)", TABLE_DDL, flags=re.MULTILINE | re.IGNORECASE)
COOKIE_SHAPE = re.compile(r"tw\d{6}")
CLONE_SHAPE = re.compile(r"s_\d{6}")

REGISTRY_DDL = """
create schema app_meta;
create table app_meta.ticks (kind text primary key, n bigint not null);
create table app_meta.logins (
    sid         text primary key,
    schema_name text not null unique,
    customer_id text,
    last_seen   bigint not null
);
insert into app_meta.ticks values ('clone', 0), ('seen', 0);
"""
#: Seen again: bump the clock and stamp the login with it, in one statement.
SEEN = """
with tick as (update app_meta.ticks set n = n + 1 where kind = 'seen' returning n)
update app_meta.logins l set last_seen = tick.n from tick
 where l.sid = %s returning l.schema_name, l.customer_id
"""


def rows_of(cursor) -> list[dict]:
    """A cursor's result as dicts keyed by column name."""
    if cursor.description is None:
        return []
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, values)) for values in cursor.fetchall()]


class Session:
    """What one request knows about its caller."""

    def __init__(self, cursor, sid, schema, customer_id) -> None:
        self.cursor, self.sid, self.schema, self.customer_id = cursor, sid, schema, customer_id

    def fetch(self, sql: str, *args) -> list[dict]:
        self.cursor.execute(sql, args)
        return rows_of(self.cursor)

    def first(self, sql: str, *args):
        found = self.fetch(sql, *args)
        return found[0] if found else None

    def scalar(self, sql: str, *args):
        self.cursor.execute(sql, args)
        row = self.cursor.fetchone()
        return None if row is None else row[0]

    def execute(self, sql: str, *args) -> int:
        self.cursor.execute(sql, args)
        return self.cursor.rowcount

    def next_number(self, counter: str) -> int:
        return self.scalar("update counters set value = value + 1 where name = %s returning value", counter)


class Vault:
    def __init__(self, url: str, cap: int) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("postgresql", "postgres") or not parts.hostname or len(parts.path) < 2:
            raise ValueError("DATABASE_URL must name a PostgreSQL user, host and database")
        self.params = {"user": unquote(parts.username or ""), "password": unquote(parts.password or ""),
                       "host": parts.hostname, "port": parts.port or 5432,
                       "database": unquote(parts.path[1:]), "application_name": "tidewater"}
        self.cap = max(cap, 1)
        self.mutex = threading.Lock()
        self.link = None
        self.clone_sql = "".join(f"insert into {name} select * from tmpl.{name};\n" for name in TABLE_ORDER)

    # -- the connection ----------------------------------------------------------------------

    def _link(self):
        if self.link is None:
            self.link = pg8000.dbapi.connect(timeout=15, **self.params)
        return self.link

    def _drop_link(self) -> None:
        with contextlib.suppress(Exception):
            if self.link is not None:
                self.link.close()
        self.link = None

    def open(self, patience_s: float = 30.0) -> None:
        """Connect, waiting while the database server is still coming up."""
        give_up = time.monotonic() + patience_s
        while True:
            try:
                self._link()
                return
            except (pg8000.dbapi.InterfaceError, OSError):
                self._drop_link()
                if time.monotonic() >= give_up:
                    raise
                time.sleep(0.5)

    def healthy(self) -> bool:
        with self.mutex:
            try:
                cursor = self._link().cursor()
                cursor.execute("select 1")
                cursor.fetchall()
                self.link.rollback()
                return True
            except Exception:  # noqa: BLE001 - whatever failed, the database is not answering
                self._drop_link()
                return False

    # -- start-up ------------------------------------------------------------------------------

    def rebuild(self) -> None:
        """Wipe this server's database and lay out the registry and the seeded template."""
        with self.mutex:
            link = self._link()
            cursor = link.cursor()
            cursor.execute("select nspname from pg_namespace where nspname ~ '^s_[0-9]{6}$' "
                           "or nspname in ('app_meta', 'tmpl')")
            for (leftover,) in cursor.fetchall():
                cursor.execute(f'drop schema "{leftover}" cascade')
            cursor.execute(REGISTRY_DDL)
            cursor.execute("create schema tmpl")
            cursor.execute("set local search_path to tmpl")
            cursor.execute(TABLE_DDL)
            for table in TABLE_ORDER:
                records = SEED_ROWS.get(table, [])
                if records:
                    columns = list(records[0])
                    cursor.executemany(
                        f"insert into {table} ({', '.join(columns)}) "
                        f"values ({', '.join(['%s'] * len(columns))})",
                        [[record[c] for c in columns] for record in records])
            cursor.executemany("insert into counters values (%s, %s)", [
                ("drafts", 0), ("quotes", 0), ("transfers", 0), ("payees", 0),
                ("ledger", max(e["id"] for e in SEED_ROWS["ledger_entries"]))])
            link.commit()

    # -- signing in and out --------------------------------------------------------------------

    def admit(self, sid: str | None, customer_id: str) -> str:
        """Sign `customer_id` in for this browser context and return the cookie it keeps:
        the context's existing clone when it has one, otherwise a fresh clone of the seed."""
        if not re.fullmatch(r"[a-z]+", customer_id):
            raise ValueError("unexpected customer id")
        with self.mutex:
            link = self._link()
            cursor = link.cursor()
            try:
                if sid and COOKIE_SHAPE.fullmatch(sid):
                    cursor.execute(SEEN, (sid,))
                    if cursor.fetchall():
                        cursor.execute("update app_meta.logins set customer_id = %s where sid = %s",
                                       (customer_id, sid))
                        link.commit()
                        return sid
                cursor.execute("update app_meta.ticks set n = n + 1 where kind = 'clone' returning n")
                number = cursor.fetchone()[0]
                cursor.execute("update app_meta.ticks set n = n + 1 where kind = 'seen' returning n")
                seen = cursor.fetchone()[0]
                sid, schema = f"tw{number:06d}", f"s_{number:06d}"
                # One round trip for the whole clone (no parameters: the simple protocol).
                cursor.execute(f"create schema {schema};\nset local search_path to {schema};\n"
                               f"{TABLE_DDL}\n{self.clone_sql}"
                               f"insert into app_meta.logins values ('{sid}', '{schema}', "
                               f"'{customer_id}', {seen});")
                cursor.execute("select schema_name from app_meta.logins order by last_seen desc "
                               "offset %s", (self.cap,))
                for (stale,) in cursor.fetchall():
                    if CLONE_SHAPE.fullmatch(stale):
                        cursor.execute(f"drop schema {stale} cascade")
                        cursor.execute("delete from app_meta.logins where schema_name = %s", (stale,))
                link.commit()
                return sid
            except BaseException:
                with contextlib.suppress(Exception):
                    link.rollback()
                raise

    def release(self, sid: str | None) -> None:
        """Sign out: nobody is signed in any more, but the clone stays for this context."""
        if not sid or not COOKIE_SHAPE.fullmatch(sid):
            return
        with self.mutex:
            link = self._link()
            link.cursor().execute("update app_meta.logins set customer_id = null where sid = %s", (sid,))
            link.commit()

    # -- requests ------------------------------------------------------------------------------

    @contextlib.contextmanager
    def session(self, sid: str | None):
        """A request's transaction on its own clone (search_path), under the lock."""
        with self.mutex:
            link = self._link()
            cursor = link.cursor()
            try:
                schema = customer_id = None
                if sid and COOKIE_SHAPE.fullmatch(sid):
                    cursor.execute(SEEN, (sid,))
                    found = cursor.fetchall()
                    if found and CLONE_SHAPE.fullmatch(found[0][0]):
                        schema, customer_id = found[0]
                        cursor.execute(f"set local search_path to {schema}")
                yield Session(cursor, sid if schema else None, schema, customer_id)
                link.commit()
            except BaseException as failure:
                try:
                    link.rollback()
                except Exception:  # noqa: BLE001 - the connection itself is broken
                    self._drop_link()
                if isinstance(failure, (pg8000.dbapi.InterfaceError, OSError)):
                    self._drop_link()
                raise
