"""The suite's PostgreSQL server: one local container, one database per running variant.

Every app of suite v2 stores its data in PostgreSQL. The harness runs one server for all of
them (`postgres:16-alpine`, published on 127.0.0.1 only) and gives every running variant its
own database, created when the variant starts and dropped when it stops, so the clean build,
the buggy build and each per-fault control never share a row. Inside a database each browser
session gets its own schema (the app's job, see CONVENTIONS.md §3), which is what resets the
application between episodes.

The server is a disposable test fixture: its data directory is a tmpfs, and durability is
switched off (`fsync`, `synchronous_commit`, `full_page_writes`) because nothing in it has to
survive the run. The credentials are fixed local test credentials, not secrets: the port is
bound to the loopback interface.

The harness talks to the server with the same pinned, vendored driver the Python apps use
(`e2e_suite/vendor/python`), so nothing new is installed into the tester's environment.
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

IMAGE = "postgres:16-alpine"
CONTAINER = os.environ.get("WTA_PG_CONTAINER", "wta-postgres")
PORT = int(os.environ.get("WTA_PG_PORT", "55432"))
USER = "wta"
PASSWORD = "wta-local-test"  # a fixed fixture credential on a loopback-only port
#: How containers reach the server (Docker Desktop's name for the host).
CONTAINER_HOST = "host.docker.internal"
READY_TIMEOUT_S = 60.0
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
SERVER_ARGS = ("-c", "max_connections=600", "-c", "fsync=off", "-c", "synchronous_commit=off",
               "-c", "full_page_writes=off", "-c", "shared_buffers=256MB")


def url_for(database: str, *, host: str = "127.0.0.1") -> str:
    return f"postgresql://{USER}:{PASSWORD}@{host}:{PORT}/{database}"


def _docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                          check=False)


def _connect(database: str = "postgres") -> pg8000.native.Connection:
    return pg8000.native.Connection(USER, password=PASSWORD, host="127.0.0.1", port=PORT,
                                    database=database, timeout=10)


def _ready() -> bool:
    try:
        _connect().close()
        return True
    except Exception:  # noqa: BLE001 - not up yet, for any reason
        return False


def ensure_server() -> None:
    """Start the container if it is not running; return once it accepts connections.

    Safe to call from several worker processes at once: a lost race to create the container
    is followed by the same readiness wait as a win.
    """
    if _ready():
        return
    state = _docker("inspect", "-f", "{{.State.Running}}", CONTAINER)
    if state.returncode == 0 and state.stdout.strip() != "true":
        _docker("start", CONTAINER)
    elif state.returncode != 0:
        _docker("run", "-d", "--name", CONTAINER, "-p", f"127.0.0.1:{PORT}:5432",
                "-e", f"POSTGRES_USER={USER}", "-e", f"POSTGRES_PASSWORD={PASSWORD}",
                "--tmpfs", "/var/lib/postgresql/data:rw", "--shm-size", "512m",
                IMAGE, *SERVER_ARGS, timeout=300)
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if _ready():
            return
        time.sleep(0.5)
    logs = _docker("logs", "--tail", "20", CONTAINER).stdout
    raise RuntimeError(f"PostgreSQL container {CONTAINER} not ready on port {PORT}:\n{logs}")


def create_database(name: str) -> str:
    """Create an empty database `name` (dropping a leftover of the same name); return its URL."""
    if not _NAME.match(name):
        raise ValueError(f"not a safe database name: {name!r}")
    ensure_server()
    connection = _connect()
    try:
        connection.run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        connection.run(f'CREATE DATABASE "{name}"')
    finally:
        connection.close()
    return url_for(name)


def drop_database(name: str) -> None:
    if not _NAME.match(name):
        raise ValueError(f"not a safe database name: {name!r}")
    with contextlib.suppress(Exception):  # the server going away first is not an error here
        connection = _connect()
        try:
            connection.run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            connection.close()


def database_name(app_id: str, port: int) -> str:
    """One database per running variant: the app and the variant's (unique) port."""
    return f"e2e_{re.sub(r'[^a-z0-9]', '_', app_id.lower())}_{port}"


def describe(database: str) -> dict:
    """What an app's schema template looks like, for the admission report: the tables of one
    session schema, their foreign keys and check constraints."""
    connection = _connect(database)
    try:
        rows = connection.run(
            "SELECT n.nspname FROM pg_namespace n WHERE n.nspname LIKE 's\\_%' "
            "ORDER BY n.oid LIMIT 1")
        if not rows:
            return {"session_schemas": 0}
        schema = rows[0][0]
        tables = [r[0] for r in connection.run(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = :s "
            "AND table_type = 'BASE TABLE' ORDER BY table_name", s=schema)]
        constraints = connection.run(
            "SELECT c.contype, count(*) FROM pg_constraint c JOIN pg_namespace n "
            "ON n.oid = c.connamespace WHERE n.nspname = :s GROUP BY c.contype", s=schema)
        kinds = {"f": "foreign_keys", "c": "checks", "u": "uniques", "p": "primary_keys"}
        counts = {kinds.get(kind, kind): int(n) for kind, n in constraints}
        rows_total = 0
        for table in tables:
            rows_total += int(connection.run(f'SELECT count(*) FROM "{schema}"."{table}"')[0][0])
        sessions = int(connection.run(
            "SELECT count(*) FROM pg_namespace WHERE nspname LIKE 's\\_%'")[0][0])
        return {"session_schemas": sessions, "example_schema": schema, "tables": tables,
                "table_count": len(tables), "seed_rows": rows_total, **counts}
    finally:
        connection.close()
