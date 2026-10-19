"""The suite's PostgreSQL server: one local container, one database per running variant.

Every PostgreSQL app of suite v2 stores its data here. The harness runs one server for all
of them (`postgres:16-alpine`, pinned by digest, published on 127.0.0.1 only) and gives every
running variant its own database (its *home*), created when the variant starts and dropped
when it stops, so the clean build, the buggy build and each per-fault control never share a
row. Inside a home each signed-in browser session gets its own schema, its *partition*
(`s_` + six digits, CONVENTIONS.md §3), which is what resets the application between episodes.

The server is a disposable test fixture: its data directory is a size-capped tmpfs, and
durability is switched off (`fsync`, `synchronous_commit`, `full_page_writes`) because
nothing in it has to survive the run. It runs in UTC so that nothing an app reads back can
depend on the host's zone. The credentials are fixed local test credentials, not secrets:
the port is bound to the loopback interface.

The harness talks to the server with the same pinned, vendored driver the Python apps use
(`e2e_suite/vendor/python`), so nothing new is installed into the tester's environment.

The functions shared with `mongo.py` (`ensure_server`, `home_name`, `create_home`,
`drop_home`, `home_exists`, `list_homes`, `drop_prefixed`, `partitions`, `describe`,
`run_probes`, `url_for`, `container_url`) are the engine interface the launcher and the
checks dispatch on (`launcher.engine`).
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "vendor"
if str(VENDOR / "python") not in sys.path:
    sys.path.insert(0, str(VENDOR / "python"))

import pg8000.native  # noqa: E402 - the vendored driver, made importable just above

ENGINE = "postgres"
IMAGE = "postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
CONTAINER = os.environ.get("WTA_PG_CONTAINER", "wta-postgres")
PORT = int(os.environ.get("WTA_PG_PORT", "55432"))
#: The port inside the container: what a container on the `wta-net` fallback network uses.
INTERNAL_PORT = 5432
USER = "wta"
PASSWORD = "wta-local-test"  # a fixed fixture credential on a loopback-only port
READY_TIMEOUT_S = 60.0
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
#: A session partition: one schema per signed-in browser context (CONVENTIONS.md §3).
PARTITION_PATTERN = "^s_[0-9]{6}$"
SERVER_ARGS = ("-c", "max_connections=600", "-c", "fsync=off", "-c", "synchronous_commit=off",
               "-c", "full_page_writes=off", "-c", "shared_buffers=256MB", "-c", "TimeZone=UTC")
#: The SQLSTATE each kind of data-model probe must be refused with.
PROBE_STATES = {"foreign_key": "23503", "check": "23514", "unique": "23505"}
#: A column default that reads the database's clock or a random source makes the seed state
#: depend on when it was created (CONVENTIONS.md §8: never use the database's clock).
_VOLATILE_DEFAULT = re.compile(
    r"\b(now|clock_timestamp|statement_timestamp|transaction_timestamp|timeofday|random|"
    r"random_normal|gen_random_uuid|uuid_generate_v\w*)\s*\(|\bcurrent_(date|time|timestamp)\b|"
    r"\blocaltime(stamp)?\b", re.IGNORECASE)


def url_for(database: str, *, host: str = "127.0.0.1", port: int = PORT) -> str:
    return f"postgresql://{USER}:{PASSWORD}@{host}:{port}/{database}"


def container_url(database: str, route: str) -> str:
    """The URL an app container uses: `host` reaches the published port through
    host.docker.internal; `network` reaches the server by name on the `wta-net` network."""
    if route == "host":
        return url_for(database, host="host.docker.internal")
    return url_for(database, host=CONTAINER, port=INTERNAL_PORT)


def _docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                          check=False)


def _connect(database: str = "postgres") -> pg8000.native.Connection:
    return pg8000.native.Connection(USER, password=PASSWORD, host="127.0.0.1", port=PORT,
                                    database=database, timeout=10)


@contextlib.contextmanager
def connection(database: str = "postgres"):
    conn = _connect(database)
    try:
        yield conn
    finally:
        conn.close()


def ready() -> bool:
    try:
        _connect().close()
        return True
    except Exception:  # noqa: BLE001 - not up yet, for any reason
        return False


def run_arguments() -> list[str]:
    """The `docker run` arguments that create the server container."""
    return ["run", "-d", "--name", CONTAINER, "-p", f"127.0.0.1:{PORT}:{INTERNAL_PORT}",
            "-e", f"POSTGRES_USER={USER}", "-e", f"POSTGRES_PASSWORD={PASSWORD}",
            "--tmpfs", "/var/lib/postgresql/data:rw,size=2g", "--memory", "2g",
            "--shm-size", "512m", IMAGE, *SERVER_ARGS]


def ensure_server() -> None:
    """Start the container if it is not running; return once it accepts connections.

    Safe to call from several worker processes at once: a lost race to create the container
    is followed by the same readiness wait as a win.
    """
    if ready():
        return
    state = _docker("inspect", "-f", "{{.State.Running}}", CONTAINER)
    if state.returncode == 0 and state.stdout.strip() != "true":
        _docker("start", CONTAINER)
    elif state.returncode != 0:
        _docker(*run_arguments(), timeout=300)
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.5)
    logs = _docker("logs", "--tail", "20", CONTAINER).stdout
    raise RuntimeError(f"PostgreSQL container {CONTAINER} not ready on port {PORT}:\n{logs}")


def _safe(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"not a safe database name: {name!r}")
    return name


def home_name(app_id: str, port: int | str) -> str:
    """One database per running variant: the app and the variant's (unique) port."""
    return f"e2e_{re.sub(r'[^a-z0-9]', '_', app_id.lower())}_{port}"


def create_home(name: str) -> str:
    """Create an empty database `name` (dropping a leftover of the same name); return its URL."""
    _safe(name)
    ensure_server()
    with connection() as conn:
        conn.run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.run(f'CREATE DATABASE "{name}"')
    return url_for(name)


def drop_home(name: str) -> None:
    _safe(name)
    with contextlib.suppress(Exception):  # the server going away first is not an error here
        with connection() as conn:
            conn.run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def list_homes(prefix: str) -> list[str]:
    """Every database whose name starts with `prefix`."""
    with connection() as conn:
        rows = conn.run("SELECT datname FROM pg_database WHERE NOT datistemplate "
                        "AND left(datname, length(:p)) = :p ORDER BY datname", p=prefix)
    return [r[0] for r in rows]


def home_exists(name: str) -> bool:
    return name in list_homes(name)


def drop_prefixed(prefix: str) -> list[str]:
    """Drop every database whose name starts with `prefix` (`dbctl reset`); return them."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", prefix):
        raise ValueError(f"not a safe prefix: {prefix!r}")
    dropped = list_homes(prefix)
    for name in dropped:
        drop_home(name)
    return dropped


def partitions(name: str) -> list[str]:
    """The session partitions (schemas) that exist in home `name`, oldest first."""
    with connection(_safe(name)) as conn:
        rows = conn.run("SELECT nspname FROM pg_namespace WHERE nspname ~ :p ORDER BY nspname",
                        p=PARTITION_PATTERN)
    return [r[0] for r in rows]


# -- the data model of one partition (CONVENTIONS.md §9) ----------------------------------------


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def describe(name: str, partition: str | None = None, entities: list[str] | None = None) -> dict:
    """What one session partition of home `name` holds, for the data-model contract.

    Structure (constraints, indexes, nullability, rows) is counted over `entities`, the
    business tables the manifest declares; every table of the partition is listed so the
    contract can insist that the rest are declared auxiliary. Triggers, rules and clock or
    random defaults are looked for in the whole database, not only in the partition.
    """
    with connection(_safe(name)) as conn:
        if partition is None:
            found = partitions(name)
            if not found:
                return {"partition": None}
            partition = found[0]
        tables = [r[0] for r in conn.run(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :s AND c.relkind IN ('r', 'p') ORDER BY c.relname", s=partition)]
        counted = [t for t in tables if entities is None or t in entities]
        columns = {t: {"columns": 0, "not_null": 0, "enum_typed": []} for t in counted}
        for table, column, not_null, typtype in conn.run(
                "SELECT c.relname, a.attname, a.attnotnull, t.typtype FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_type t ON t.oid = a.atttypid WHERE n.nspname = :s "
                "AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped", s=partition):
            if table in columns:
                columns[table]["columns"] += 1
                columns[table]["not_null"] += int(bool(not_null))
                if typtype == "e":
                    columns[table]["enum_typed"].append(column)
        foreign_keys, checks, enumerations = [], [], []
        for table, conname, contype, conkey, definition in conn.run(
                "SELECT c.relname, k.conname, k.contype, k.conkey::int2[], pg_get_constraintdef(k.oid) "
                "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :s "
                "ORDER BY c.relname, k.conname", s=partition):
            if table not in columns:
                continue
            if contype == "f":
                foreign_keys.append({"table": table, "name": conname, "columns": list(conkey),
                                     "definition": definition})
            elif contype == "c":
                checks.append({"table": table, "name": conname, "definition": definition})
                # PostgreSQL prints `col IN ('a', 'b')` back as `col = ANY (ARRAY[...])`.
                if "= ANY (ARRAY[" in definition:
                    enumerations.append(f"{table}.{conname}")
        enumerations += [f"{t}.{c} (enum type)" for t, info in columns.items()
                         for c in info["enum_typed"]]
        unique, secondary, index_keys = [], [], {}
        for table, is_unique, is_primary, keys, index in conn.run(
                # indkey is an int2vector ("2 1"; 0 for an expression column), read as text:
                # cast to an array it keeps a zero lower bound the driver cannot parse.
                "SELECT c.relname, i.indisunique, i.indisprimary, i.indkey::text, ic.relname "
                "FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid "
                "JOIN pg_class ic ON ic.oid = i.indexrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :s "
                "ORDER BY ic.relname", s=partition):
            if table not in columns:
                continue
            index_keys.setdefault(table, []).append([int(k) for k in keys.split()])
            if not is_primary:
                (unique if is_unique else secondary).append(f"{table}.{index}")
        for fk in foreign_keys:
            # Backed when some index leads with exactly the key's columns (in any order).
            width = len(fk["columns"])
            fk["indexed"] = any(set(keys[:width]) == set(fk["columns"])
                                for keys in index_keys.get(fk["table"], []))
        rows = {t: int(conn.run(f"SELECT count(*) FROM {_quoted(partition)}.{_quoted(t)}")[0][0])
                for t in tables}
        triggers = [".".join(r) for r in conn.run(
            "SELECT n.nspname, c.relname, t.tgname FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE NOT t.tgisinternal ORDER BY 1, 2, 3")]
        rules = [".".join(r) for r in conn.run(
            "SELECT schemaname, tablename, rulename FROM pg_rules "
            "WHERE schemaname NOT IN ('pg_catalog', 'information_schema') ORDER BY 1, 2, 3")]
        event_triggers = [r[0] for r in conn.run("SELECT evtname FROM pg_event_trigger ORDER BY 1")]
        volatile_defaults = [
            f"{schema}.{table}.{column} DEFAULT {expression}"
            for schema, table, column, expression in conn.run(
                "SELECT n.nspname, c.relname, a.attname, pg_get_expr(d.adbin, d.adrelid) "
                "FROM pg_attrdef d JOIN pg_class c ON c.oid = d.adrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum "
                "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
                "ORDER BY 1, 2, 3")
            if _VOLATILE_DEFAULT.search(expression or "")]
        identity_columns = [f"{t}.{c}" for t, c in conn.run(
            "SELECT c.relname, a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE a.attidentity <> '' AND n.nspname = :s ORDER BY 1, 2", s=partition)]
    total_columns = sum(info["columns"] for info in columns.values())
    not_null = sum(info["not_null"] for info in columns.values())
    return {
        "partition": partition, "tables": tables, "entities_found": counted,
        "table_count": len(counted),
        "foreign_keys": foreign_keys, "foreign_key_count": len(foreign_keys),
        "foreign_keys_indexed": sum(1 for fk in foreign_keys if fk["indexed"]),
        "checks": checks, "check_count": len(checks), "enumerations": enumerations,
        "unique_indexes": unique, "secondary_indexes": secondary,
        "columns": total_columns, "not_null_columns": not_null,
        "not_null_ratio": round(not_null / total_columns, 3) if total_columns else 0.0,
        "rows": rows, "seed_rows": sum(rows[t] for t in counted),
        "non_empty_tables": sum(1 for t in counted if rows[t] > 0),
        "user_triggers": triggers, "rules": rules, "event_triggers": event_triggers,
        "volatile_defaults": volatile_defaults, "identity_columns": identity_columns,
    }


def run_probes(name: str, partition: str, probes: list[dict]) -> list[dict]:
    """Run each probe statement in its own rolled-back transaction inside `partition` and
    report the SQLSTATE it was refused with. A statement the database accepts fails its probe."""
    out = []
    with connection(_safe(name)) as conn:
        for probe in probes:
            expected = str(probe.get("sqlstate") or PROBE_STATES.get(probe.get("kind"), ""))
            conn.run("BEGIN")
            try:
                conn.run(f"SET LOCAL search_path TO {_quoted(partition)}")
                conn.run(probe["sql"])
                got = "accepted"
            except pg8000.native.DatabaseError as exc:
                detail = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
                got = str(detail.get("C") or exc)[:160]
            finally:
                conn.run("ROLLBACK")
            out.append({"name": probe.get("name", probe["sql"][:60]), "kind": probe.get("kind"),
                        "expected": expected, "got": got, "passed": got == expected})
    return out
