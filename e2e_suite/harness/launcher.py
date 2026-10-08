"""Start, health-check and stop application servers, one process per variant.

Each variant runs in its own process on its own port with its own data directory and, for
the suite v2 apps, its own database home (a PostgreSQL database or a MongoDB home database,
dropped before the variant starts and after it stops), so the buggy build, the clean build
and every per-fault control are independent origins with no shared state — the same
isolation the primary oracle gets from serving each static build on its own origin.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from .dbctl import engine
from .manifest import AppManifest

DEFAULT_NOW = "2026-06-10T09:00:00"
HEALTH_TIMEOUT_S = 30.0
#: The environment variable through which each engine's app receives its home's URL.
URL_VARIABLES = {"postgres": "DATABASE_URL", "mongodb": "MONGODB_URL"}
#: Every database home a Server created in this process, as (app_id, engine, home), so an
#: admission can prove that each was released again (`leftover_homes`).
CREATED_HOMES: list[tuple[str, str, str]] = []


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _command(app: AppManifest) -> list[str]:
    command = list(app.data["start"])
    if command[0] == "python":
        command[0] = sys.executable
    elif command[0] == "node":
        command[0] = shutil.which("node") or "node"
    return command


def http_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - local only
        return json.loads(response.read().decode("utf-8"))


class Server:
    """One running variant. Use as a context manager."""

    def __init__(self, app: AppManifest, directory: Path, *, vocab: str = "default",
                 test_hooks: bool = True, now: str = DEFAULT_NOW,
                 session_cap: int | None = None) -> None:
        self.app, self.directory = app, Path(directory)
        self.vocab, self.test_hooks, self.now = vocab, test_hooks, now
        #: `WTA_SESSION_CAP` for this variant; None passes the harness's own value through.
        self.session_cap = session_cap
        self.port = 0
        self.process: subprocess.Popen | None = None
        self.data_dir = ""
        self.home = ""  # this variant's own database home (suite v2 apps)
        self.log_path = self.directory / f"server-{vocab}.log"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> "Server":
        self.port = free_port()
        self.data_dir = tempfile.mkdtemp(prefix=f"e2e-{self.app.app_id}-")
        env = {**os.environ, "PORT": str(self.port), "WTA_VOCAB": self.vocab,
               "WTA_FIXED_NOW": self.now, "WTA_DATA_DIR": self.data_dir,
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
        env.pop("WTA_TEST_HOOKS", None)
        for variable in URL_VARIABLES.values():
            env.pop(variable, None)
        if self.app.database:
            store = engine(self.app.database)
            self.home = store.home_name(self.app.app_id, self.port)
            CREATED_HOMES.append((self.app.app_id, self.app.database, self.home))
            env[URL_VARIABLES[self.app.database]] = store.create_home(self.home)
        if self.session_cap is not None:
            env["WTA_SESSION_CAP"] = str(self.session_cap)
        if self.test_hooks:
            env["WTA_TEST_HOOKS"] = "1"
        log = self.log_path.open("w", encoding="utf-8")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.process = subprocess.Popen(  # noqa: S603 - the app's own declared command
            _command(self.app), cwd=self.directory, env=env, stdout=log,
            stderr=subprocess.STDOUT, creationflags=flags)
        deadline = time.monotonic() + HEALTH_TIMEOUT_S
        last_error = ""
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"{self.app.app_id} exited with {self.process.returncode}; "
                                   f"see {self.log_path}")
            try:
                health = http_json(self.url + "healthz", timeout=2)
                if health.get("ok") and health.get("app") == self.app.app_id:
                    return self
                last_error = f"unexpected health payload {health!r}"
            except OSError as exc:
                last_error = str(exc)
            time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"{self.app.app_id} not healthy after {HEALTH_TIMEOUT_S}s: {last_error}")

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self.process = None
        if self.home:
            engine(self.app.database).drop_home(self.home)
            self.home = ""
        if self.data_dir:
            shutil.rmtree(self.data_dir, ignore_errors=True)
            self.data_dir = ""

    def __enter__(self) -> "Server":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def leftover_homes(app_id: str) -> list[str]:
    """Database homes this process created for `app_id` that still exist (for Mongo, a
    home counts while any of its partition databases is left)."""
    return [home for app, name, home in CREATED_HOMES
            if app == app_id and engine(name).home_exists(home)]


@contextlib.contextmanager
def serve(app: AppManifest, directories: dict[str, Path], **kwargs):
    """Start every variant in `directories`; yield `{variant: url}`; stop them all."""
    servers: dict[str, Server] = {}
    try:
        for name, directory in directories.items():
            servers[name] = Server(app, directory, **kwargs).start()
        yield {name: server.url for name, server in servers.items()}
    finally:
        for server in servers.values():
            server.stop()
