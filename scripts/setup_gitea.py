"""Bring a fresh Gitea container to a state worth exploring, then report its surface.

An empty Gitea is not a target. Straight out of `compose up` it has no users, no
repositories and nothing to navigate: an explorer would find nothing, and "found
nothing" would be a property of the fixture rather than a result. This seeds enough
content that a run measures the agent and the judge instead of an empty database.

It also answers the question that decides how much of Gitea is reachable at all right
now: **session bootstrap is unbuilt**, so the agent browses anonymously. This script
finishes by probing a set of routes with no credentials and printing which ones are
actually available, so the first real run is planned against measured surface rather
than an assumption.

Idempotent — safe to re-run against an already-seeded instance.

Usage:
    docker compose -f docker/docker-compose.yml up -d gitea
    python scripts/setup_gitea.py
    python scripts/setup_gitea.py --probe-only     # just report reachable routes
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

BASE_URL = "http://localhost:3000"
CONTAINER = "wta-gitea"
ADMIN_USER = "tester"
ADMIN_PASSWORD = "TestPassw0rd!"
ADMIN_EMAIL = "tester@example.com"

# Routes worth knowing the anonymous status of before planning a run. Each is a page
# the explorer could plausibly reach from the landing page.
PROBE_ROUTES = [
    "/",
    "/explore/repos",
    "/explore/users",
    "/explore/organizations",
    "/user/login",
    "/user/sign_up",
    f"/{ADMIN_USER}",
    f"/{ADMIN_USER}/demo-project",
    f"/{ADMIN_USER}/demo-project/issues",
    f"/{ADMIN_USER}/demo-project/issues/new",
    f"/{ADMIN_USER}/demo-project/src/branch/main/README.md",
    f"/{ADMIN_USER}/demo-project/releases",
    "/api/healthz",
]

SEED_REPOS = [
    ("demo-project", "A small project used as an exploration target.", False),
    ("notes", "Scratch notes repository.", False),
]

SEED_ISSUES = [
    ("Login button does nothing on mobile", "Tapping sign in has no effect below 600px."),
    ("Typo in README", "Second paragraph says 'teh'."),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--container", default=CONTAINER)
    parser.add_argument("--timeout", type=int, default=180, help="seconds to wait for Gitea to boot")
    parser.add_argument("--probe-only", action="store_true", help="skip seeding, just report reachable routes")
    return parser.parse_args()


def _request(url: str, method: str = "GET", body: dict | None = None,
             auth: tuple[str, str] | None = None, timeout: float = 15.0) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - connection refused while booting
        return 0, str(exc)


def wait_for_gitea(base_url: str, timeout: int) -> None:
    """Poll until Gitea answers. A fresh container takes tens of seconds to migrate."""
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        status, _ = _request(f"{base_url}/api/healthz", timeout=5)
        if status == 200:
            logger.info("Gitea is up after {} attempt(s)", attempt)
            return
        time.sleep(3)
    raise SystemExit(
        f"Gitea did not become healthy within {timeout}s.\n"
        f"    docker compose -f docker/docker-compose.yml up -d gitea\n"
        f"    docker logs {CONTAINER} --tail 50"
    )


def ensure_not_installer(base_url: str) -> None:
    """A fresh instance without INSTALL_LOCK serves its setup wizard at every route.

    Worth failing loudly on: the agent would happily explore the installation form and
    produce an entire run's worth of findings about a page that is not the application.
    """
    status, body = _request(base_url)
    if status == 200 and ("Initial Configuration" in body or "/install" in body[:2000]):
        raise SystemExit(
            "Gitea is serving its installation wizard, not the application.\n"
            "The compose service sets GITEA__security__INSTALL_LOCK=true; an older "
            "container predating that change is probably still running with its volume.\n"
            "    docker compose -f docker/docker-compose.yml down\n"
            "    docker volume rm docker_gitea-data\n"
            "    docker compose -f docker/docker-compose.yml up -d gitea"
        )


def create_admin(container: str) -> bool:
    """Create the seed user via Gitea's CLI. Returns False if it already existed."""
    result = subprocess.run(
        ["docker", "exec", "-u", "git", container, "gitea", "admin", "user", "create",
         "--admin", "--username", ADMIN_USER, "--password", ADMIN_PASSWORD,
         "--email", ADMIN_EMAIL, "--must-change-password=false"],
        capture_output=True, text=True,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode == 0:
        logger.info("Created admin user {}", ADMIN_USER)
        return True
    if "already exists" in output or "user already exists" in output.lower():
        logger.info("Admin user {} already exists", ADMIN_USER)
        return False
    raise SystemExit(f"Could not create the admin user:\n{output}")


def seed_content(base_url: str) -> None:
    auth = (ADMIN_USER, ADMIN_PASSWORD)
    for name, description, private in SEED_REPOS:
        status, body = _request(
            f"{base_url}/api/v1/user/repos", method="POST", auth=auth,
            body={"name": name, "description": description, "private": private,
                  "auto_init": True, "default_branch": "main",
                  "readme": "Default", "gitignores": "", "license": "MIT"},
        )
        if status in (201, 409):
            logger.info("Repo {}: {}", name, "created" if status == 201 else "already exists")
        else:
            logger.warning("Repo {} -> HTTP {}: {}", name, status, body[:200])

    target = SEED_REPOS[0][0]
    status, body = _request(f"{base_url}/api/v1/repos/{ADMIN_USER}/{target}/issues", auth=auth)
    existing = {item.get("title") for item in json.loads(body)} if status == 200 else set()
    for title, description in SEED_ISSUES:
        if title in existing:
            logger.info("Issue {!r} already exists", title)
            continue
        status, body = _request(
            f"{base_url}/api/v1/repos/{ADMIN_USER}/{target}/issues", method="POST",
            auth=auth, body={"title": title, "body": description},
        )
        logger.info("Issue {!r} -> HTTP {}", title, status)


def probe_anonymous(base_url: str) -> list[tuple[str, int, int]]:
    """What an unauthenticated explorer can actually reach, measured not assumed."""
    results = []
    for route in PROBE_ROUTES:
        status, body = _request(f"{base_url}{route}")
        results.append((route, status, len(body)))
    return results


def main() -> None:
    args = parse_args()
    wait_for_gitea(args.base_url, args.timeout)
    ensure_not_installer(args.base_url)

    if not args.probe_only:
        create_admin(args.container)
        seed_content(args.base_url)

    rows = probe_anonymous(args.base_url)
    reachable = [row for row in rows if row[1] == 200]

    print("\n" + "=" * 72)
    print(f"Gitea at {args.base_url} — anonymous surface (session bootstrap is unbuilt)")
    print("=" * 72)
    for route, status, size in rows:
        mark = "ok  " if status == 200 else ("AUTH" if status in (302, 401, 403) else "----")
        print(f"  [{mark}] {status:>3}  {size:>7,}b  {route}")
    print("-" * 72)
    print(f"  {len(reachable)}/{len(rows)} routes reachable without credentials")
    print("=" * 72)

    if len(reachable) < 4:
        print("\n!! Very little is reachable anonymously. Either registration/anonymous")
        print("   browsing is disabled, or seeding failed — check before running the agent,")
        print("   or the run will measure an empty app rather than the agent.\n")
    else:
        print(f"\nReady. Point the env at {args.base_url}/ and capture a trace:")
        print(f"    python scripts/capture_traces.py --run-id gitea --only random \\")
        print(f"        --base-url {args.base_url}/ --random-episodes 4 --random-steps 60\n")


if __name__ == "__main__":
    main()
