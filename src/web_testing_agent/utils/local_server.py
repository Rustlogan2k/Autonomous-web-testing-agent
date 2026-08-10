"""Throwaway static HTTP server for the local fixture sites.

Used by the smoke test, the baseline harness, and the toy-site A/B experiments so
none of them need Docker or a real training target running.
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import socket
import threading
from collections.abc import Iterator
from pathlib import Path


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: ANN002 - stdlib signature
        """Suppress per-request stderr noise; the env's own logging is the signal."""


@contextlib.contextmanager
def serve_directory(directory: Path, port: int | None = None) -> Iterator[str]:
    """Serve `directory` over HTTP for the duration of the context. Yields the base URL."""
    port = port or free_port()
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
