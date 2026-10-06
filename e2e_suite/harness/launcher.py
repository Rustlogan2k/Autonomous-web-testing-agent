"""Start, health-check and stop application servers, one process per variant.

Each variant runs in its own process on its own port with its own data directory, so the
buggy build, the clean build and every per-fault control are independent origins with no
shared state — the same isolation the primary oracle gets from serving each static build on
its own origin.
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

from .manifest import AppManifest

DEFAULT_NOW = "2026-06-10T09:00:00"
HEALTH_TIMEOUT_S = 30.0


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
                 test_hooks: bool = True, now: str = DEFAULT_NOW) -> None:
        self.app, self.directory = app, Path(directory)
        self.vocab, self.test_hooks, self.now = vocab, test_hooks, now
        self.port = 0
        self.process: subprocess.Popen | None = None
        self.data_dir = ""
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
        if self.data_dir:
            shutil.rmtree(self.data_dir, ignore_errors=True)
            self.data_dir = ""

    def __enter__(self) -> "Server":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


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
