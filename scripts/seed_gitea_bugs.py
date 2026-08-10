"""Installs (and removes) seeded defects in a running Gitea container.

Why this exists: every judge-accuracy figure in this project came from a 5-page fixture
whose bugs were written alongside the judge. On a real application there was no ground
truth at all, so "precision on Gitea" meant a human reading nine verdicts and forming an
opinion. This gives a real recall/precision pair on a real target.

**Gitea is not forked or rebuilt.** The defects are injected through Gitea's own
supported customization path — `$GITEA_CUSTOM/templates`, which overrides the embedded
templates — so the application under test is stock apart from the specific overrides
listed in `answer_key.json`, and `--remove` restores it exactly by deleting a directory.

That matters for the result's credibility: a forked binary invites "you tested your own
fork", whereas an override directory is auditable in a few files and provably absent
once removed.

Usage:
    python scripts/seed_gitea_bugs.py --install
    python scripts/seed_gitea_bugs.py --status
    python scripts/seed_gitea_bugs.py --remove
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
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

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "gitea_bugs"
ANSWER_KEY = FIXTURE / "answer_key.json"
CONTAINER = "wta-gitea"
CUSTOM_TEMPLATES = "/data/gitea/templates"
BASE_URL = "http://localhost:3000"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true")
    action.add_argument("--remove", action="store_true")
    action.add_argument("--status", action="store_true")
    parser.add_argument("--container", default=CONTAINER)
    parser.add_argument("--base-url", default=BASE_URL)
    return parser.parse_args()


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"docker {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result


def container_running(name: str) -> bool:
    out = docker("ps", "--filter", f"name={name}", "--format", "{{.Names}}", check=False).stdout
    return name in out.split()


def installed_files(name: str) -> list[str]:
    result = docker(
        "exec", name, "sh", "-c", f"find {CUSTOM_TEMPLATES} -type f 2>/dev/null | sort", check=False
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def fetch(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(f"Could not reach {url}: {exc}") from exc


def install(args: argparse.Namespace) -> None:
    sources = sorted(p for p in (FIXTURE / "templates").rglob("*.tmpl"))
    if not sources:
        raise SystemExit(f"No templates found under {FIXTURE / 'templates'}")

    for source in sources:
        relative = source.relative_to(FIXTURE / "templates").as_posix()
        target_dir = f"{CUSTOM_TEMPLATES}/{Path(relative).parent.as_posix()}".rstrip("/.")
        docker("exec", args.container, "mkdir", "-p", target_dir)
        docker("cp", str(source), f"{args.container}:{CUSTOM_TEMPLATES}/{relative}")
        logger.info("installed {}", relative)

    # Gitea caches compiled templates, so an override is not live until it reloads.
    # Restarting is the reliable way to be sure the running instance is the one the
    # answer key describes -- scoring a half-applied fixture would be worse than not
    # seeding at all, because the misses would look like judge failures.
    logger.info("restarting {} so the overrides take effect", args.container)
    docker("restart", args.container)
    _wait_until_up(args.base_url)
    verify(args)


def remove(args: argparse.Namespace) -> None:
    docker("exec", args.container, "sh", "-c", f"rm -rf {CUSTOM_TEMPLATES}")
    logger.info("removed {}", CUSTOM_TEMPLATES)
    docker("restart", args.container)
    _wait_until_up(args.base_url)
    remaining = installed_files(args.container)
    print("\nstock Gitea restored" if not remaining else f"\n!! files remain: {remaining}")


def _wait_until_up(base_url: str, attempts: int = 60) -> None:
    import time

    for _ in range(attempts):
        try:
            with urllib.request.urlopen(base_url, timeout=3) as response:
                if response.status == 200:
                    return
        except Exception:  # noqa: BLE001 - the container is expected to be down at first
            pass
        time.sleep(1)
    raise SystemExit(f"{base_url} did not come back up")


def verify(args: argparse.Namespace) -> None:
    """Check each seeded defect is actually live, and say which are not.

    A seeded bug that failed to install is indistinguishable, downstream, from a judge
    that missed it. This turns that ambiguity into an error message.
    """
    explore = fetch(f"{args.base_url}/explore/repos")
    users_page = fetch(f"{args.base_url}/explore/users")

    checks = {
        "GITEA-01 users tab points at organizations":
            'href="/explore/organizations"' in explore and explore.count('href="/explore/organizations"') >= 2,
        "GITEA-02 export button present": 'id="export-results"' in explore,
        "GITEA-03 narrow-viewport rule injected": "max-width: 600px" in explore,
        "GITEA-04 search box cleared on load": 'input[name="q"]' in explore,
        "GITEA-05 TypeError scheduled on /explore/users": "missing.render()" in users_page,
        "GITEA-06 runaway timer scoped to repo pages": "sync-status" in explore,
    }
    print()
    for label, ok in checks.items():
        print(f"  [{'ok' if ok else 'MISSING'}] {label}")
    failed = [label for label, ok in checks.items() if not ok]
    if failed:
        raise SystemExit(
            f"\n{len(failed)} seeded defect(s) are not live. Scoring now would report them "
            f"as judge misses. Check the template overrides before capturing a corpus."
        )
    key = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))
    deterministic = [b["id"] for b in key["bugs"] if "deterministic" in b["detectability"]]
    print(
        f"\nAll {len(key['bugs'])} seeded defects live. Deterministic ceiling: "
        f"{len(deterministic)}/{len(key['bugs'])} ({', '.join(deterministic)}) — "
        f"that is the number the judge has to beat."
    )


def status(args: argparse.Namespace) -> None:
    files = installed_files(args.container)
    if not files:
        print("stock Gitea — no seeded defects installed")
        return
    print(f"{len(files)} override(s) installed:")
    for path in files:
        print(f"  {path}")
    verify(args)


def main() -> None:
    args = parse_args()
    if not container_running(args.container):
        raise SystemExit(
            f"Container {args.container!r} is not running.\n"
            f"    cd docker && docker compose up -d gitea && cd ..\n"
            f"    python scripts/setup_gitea.py"
        )
    if args.install:
        install(args)
    elif args.remove:
        remove(args)
    else:
        status(args)


if __name__ == "__main__":
    main()
