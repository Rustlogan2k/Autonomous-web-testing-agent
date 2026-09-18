"""`python -m web_testing_agent.app` — start the platform.

Kept to argument parsing and a `uvicorn.run` call. The application itself is built by
`main.create_app`, so a test, a notebook or an ASGI server can construct one without
going through a command line.
"""

from __future__ import annotations

import argparse

from ..utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m web_testing_agent.app",
        description="Autonomous Web Testing Platform — the product layer over the "
                    "research system.",
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST,
        help="interface to bind. Defaults to 127.0.0.1: this application builds and runs "
             "user-supplied repositories, so it should not be reachable off-host without "
             "a deliberate decision.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reload", action="store_true", help="auto-reload on code edits")
    args = parser.parse_args()

    import uvicorn

    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(f"\n  Autonomous Web Testing Platform\n  Open {url}\n")
    if args.host == "0.0.0.0":  # noqa: S104 - warned about, not silently accepted
        print("  WARNING: bound to 0.0.0.0. This application executes uploaded "
              "repositories; do not expose it on an untrusted network.\n")

    uvicorn.run(
        "web_testing_agent.app.main:app" if args.reload else create_app_for_serving(),
        host=args.host, port=args.port, reload=args.reload, log_level="info",
    )


def create_app_for_serving():  # noqa: ANN201
    from .main import create_app

    return create_app()


if __name__ == "__main__":
    main()
