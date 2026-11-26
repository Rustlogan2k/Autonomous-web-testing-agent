"""GitHub Actions client for this repository's CI runs (stdlib only).

The token comes from `git credential fill` (protocol=https, host=github.com) and is never
printed. Network errors are retried, since the machine this runs on drops offline.

  python scripts/ci/gha.py runs [branch]           recent workflow runs
  python scripts/ci/gha.py status <run>            one run's status
  python scripts/ci/gha.py jobs <run>              job counts, and every job not succeeded
  python scripts/ci/gha.py fetch <run> <dir>       each artifact extracted to <dir>/<name>;
                                                   <dir>/<name>/.done only after a full extract
  python scripts/ci/gha.py logs <run> <dir> [pat]  job logs (skipped jobs left out; name filter)
  python scripts/ci/gha.py cancel <run>            cancel a queued or running run
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

API = "https://api.github.com/repos/Rustlogan2k/Autonomous-web-testing-agent/"


def _token() -> str:
    out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("no GitHub credential")


_TOKEN: str | None = None


def request(url: str, *, raw: bool = False, tries: int = 8, method: str = "GET"):
    global _TOKEN  # noqa: PLW0603 - fetched once, on first use
    if _TOKEN is None:
        _TOKEN = _token()
    if not url.startswith("http"):
        url = API + url
    err: Exception | None = None
    for attempt in range(tries):
        req = urllib.request.Request(url, method=method)
        # Not forwarded on redirects: artifact downloads redirect to storage that rejects it.
        req.add_unredirected_header("Authorization", f"Bearer {_TOKEN}")
        req.add_header("Accept", "application/vnd.github+json")
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                data = response.read()
                return data if raw else (json.loads(data) if data else {})
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 409, 410, 422):
                raise
            err = exc
        except Exception as exc:  # noqa: BLE001 - network drops: retry
            err = exc
        time.sleep(min(60, 5 * (attempt + 1)))
    raise RuntimeError(f"{url}: {err}")


def runs(branch: str | None = None) -> None:
    q = "actions/runs?per_page=30" + (f"&branch={branch}" if branch else "")
    for r in request(q)["workflow_runs"]:
        print(r["id"], r["head_branch"], r["name"], r["status"], r["conclusion"],
              r["created_at"], r["head_sha"][:7], r["display_title"][:60])


def all_jobs(run: str) -> list[dict]:
    jobs, page = [], 1
    while True:
        batch = request(f"actions/runs/{run}/jobs?per_page=100&page={page}&filter=latest")["jobs"]
        jobs += batch
        if len(batch) < 100:
            return jobs
        page += 1


def status(run: str) -> None:
    r = request(f"actions/runs/{run}")
    print(r["id"], r["name"], r["head_branch"], r["status"], r["conclusion"], r["created_at"],
          r["updated_at"])


def jobs(run: str) -> None:
    js = all_jobs(run)
    print(Counter((j["status"], j["conclusion"]) for j in js))
    for j in js:
        if j["conclusion"] not in ("success", "skipped", None):
            print(j["id"], j["name"][:90], j["status"], j["conclusion"])


def fetch(run: str, out: str) -> None:
    out_dir = Path(out)
    page, arts = 1, []
    while True:
        batch = request(f"actions/runs/{run}/artifacts?per_page=100&page={page}")["artifacts"]
        arts += batch
        if len(batch) < 100:
            break
        page += 1
    got = 0
    for a in arts:
        dest = out_dir / a["name"]
        if (dest / ".done").exists():
            continue
        data = request(a["archive_download_url"], raw=True)
        dest.mkdir(parents=True, exist_ok=True)
        zipfile.ZipFile(io.BytesIO(data)).extractall(dest)
        (dest / ".done").write_text("")
        got += 1
    done = sum((out_dir / a["name"] / ".done").exists() for a in arts)
    print(f"{len(arts)} artifacts, {got} downloaded now, {done} complete in {out_dir}")


def logs(run: str, out: str, pattern: str = "") -> None:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for j in all_jobs(run):
        if j["conclusion"] == "skipped" or pattern not in j["name"]:
            continue
        name = "".join(c if c.isalnum() or c in "-_." else "_" for c in j["name"])[:80]
        path = out_dir / f"{name}__{j['id']}.txt"
        if path.exists():
            continue
        try:
            path.write_bytes(request(f"actions/jobs/{j['id']}/logs", raw=True))
        except urllib.error.HTTPError as exc:
            print("no log", j["id"], exc.code)
    print("logs in", out_dir)


def cancel(run: str) -> None:
    request(f"actions/runs/{run}/cancel", method="POST")
    print("cancel requested for", run)


if __name__ == "__main__":
    command, *args = sys.argv[1:]
    {"runs": runs, "status": status, "jobs": jobs, "fetch": fetch, "logs": logs,
     "cancel": cancel}[command](*args)
