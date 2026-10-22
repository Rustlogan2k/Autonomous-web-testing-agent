"""Runs on the external benchmark: the subject applications of WebExplor and WebRLED.

The published comparison (WebRLED, arXiv 2504.19237, Table 3) gives each tool a one-hour
budget per application, repeats every run five times, and reports client-side branch
coverage (Istanbul) and failures (console errors, deduplicated). Runs here follow the same
protocol: a wall-clock budget instead of an interaction budget, a fresh application instance
per run, and the same two measures, recorded with `metrics.IstanbulCollector` (branch
coverage of the instrumented client code) and `metrics.FailureCounter` (unique failures).
V8 byte coverage is recorded beside them, as in the development rounds.

There are no seeded faults on these applications, so there is no oracle verification; the
hunt runs with a monitor that never confirms anything.

Applications are described in a JSON file (`--apps-file`), one entry per application:

    {"dimeshift": {"url": "http://127.0.0.1:3000/",
                   "up": ["docker", "compose", "-f", "...", "up", "-d"],
                   "down": ["docker", "compose", "-f", "...", "down", "-v"],
                   "ready": "http://127.0.0.1:3000/",
                   "login": [ ...optional setup actions run at every episode start... ],
                   "stub": [ ...optional extra hosts to answer with an empty 200... ]}}

Runs of the same application are serialised (one instance per application at a time);
different applications run in parallel, up to `--workers`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from ..experiments.transfer import _write_json, provenance
from ..utils.logging import get_logger
from .run import ARMS, HybridConfig, HybridJob, _origin, _policy, _settings

logger = get_logger(__name__)

#: The published protocol's budget per run.
ONE_HOUR = 3600.0
#: Effectively unlimited interactions: the wall clock ends the run.
UNLIMITED = 10**9


@dataclass(frozen=True)
class ExternalConfig:
    time_budget_s: float = ONE_HOUR
    episode_steps: int = 40
    settle_quiet_ms: int = 80
    ready_timeout_s: float = 600.0

    def hybrid(self) -> HybridConfig:
        # The policies read `budget` only for v1's epsilon schedule (not an arm here) and
        # `episode_steps`; the interaction budget itself is unlimited.
        return HybridConfig(budget=UNLIMITED, episode_steps=self.episode_steps,
                            settle_quiet_ms=self.settle_quiet_ms)

    def to_dict(self) -> dict:
        return asdict(self)


class NullMonitor:
    """The hunt's monitor when there are no seeded faults: observes, never confirms."""

    verification_seconds = 0.0

    def start_episode(self, info: dict) -> None:  # noqa: ARG002
        return None

    def observe(self, info, executed, available, step) -> bool:  # noqa: ANN001, ARG002
        return False

    def summary(self) -> dict:
        return {"seeded": 0, "note": "external application: no seeded faults"}


def _run(command: list[str], *, timeout: float = 900.0) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                               check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed ({completed.returncode}): "
                           f"{(completed.stderr or completed.stdout)[-800:]}")


def _wait_ready(url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - local apps
                if response.status < 500:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return
            last = str(exc)
        except Exception as exc:  # noqa: BLE001 - not up yet
            last = str(exc)
        time.sleep(3)
    raise TimeoutError(f"{url} not ready after {timeout:.0f}s ({last})")


#: Hosts that are not part of any subject application but that its pages call: the
#: development live-reload server some images still reference, and web analytics. Answered
#: with an empty success so they neither fail (a console error in every run of every arm,
#: whatever the tester does) nor leave the machine. Extended per application with "stub".
STUB_HOSTS = ("localhost:35729", "127.0.0.1:35729", "www.google-analytics.com",
              "analytics.google.com", "www.googletagmanager.com", "stats.g.doubleclick.net")


def stub_third_party(env, hosts) -> None:  # noqa: ANN001
    """Answer requests to `hosts` with an empty 200, on every episode's page."""
    from urllib.parse import urlsplit

    session = env._browser  # noqa: SLF001 - the session's documented hook
    previous = session.on_new_page
    hosts = frozenset(hosts)

    def hook(page) -> None:  # noqa: ANN001
        if previous is not None:
            previous(page)
        page.route(lambda url: urlsplit(url).netloc in hosts,
                   lambda route: route.fulfill(status=200, body=""))

    session.on_new_page = hook


def run_external_job(job: HybridJob, spec: dict, config: ExternalConfig) -> dict:
    """One time-budgeted run of `job.arm` on a fresh instance of the application."""
    from ..benchmark import make_env, run_hunt
    from .metrics import CoverageCollector, FailureCounter, IstanbulCollector

    hybrid_config = config.hybrid()
    settings = _settings(hybrid_config, ARMS[job.arm])
    url = spec["url"]
    login = list(spec.get("login") or [])
    if spec.get("down"):
        _run(spec["down"])
    if spec.get("up"):
        _run(spec["up"])
    try:
        _wait_ready(spec.get("ready", url), config.ready_timeout_s)
        policy, _labeller = _policy(job, job.app, hybrid_config)
        env = make_env(url, settings, setup_actions=login or None)
        failures = FailureCounter()
        coverage = CoverageCollector(_origin(url))
        istanbul = IstanbulCollector()
        coverage.install(env)
        istanbul.install(env)  # chains onto the V8 collector's hook
        stub_third_party(env, (*STUB_HOSTS, *(spec.get("stub") or ())))

        def on_step(event: dict) -> None:
            failures.observe(event["info"], event["step"])
            coverage.sample(env)
            istanbul.sample(env)

        try:
            result = run_hunt(env, policy, NullMonitor(), budget=UNLIMITED, on_step=on_step,
                              deadline_s=config.time_budget_s)
        finally:
            env.close()
    finally:
        if spec.get("down"):
            try:
                _run(spec["down"])
            except Exception as exc:  # noqa: BLE001 - the result stands; the next run resets
                logger.warning("{}: stopping the application failed: {}", job.app, exc)
    payload = {
        "hunt": result.to_dict(),
        "failures": failures.summary(),
        "coverage": coverage.summary(),
        "istanbul": istanbul.summary(),
        "arm": {"name": job.arm, **ARMS[job.arm]},
        "external": True,
    }
    if hasattr(policy, "summary"):
        payload["policy"] = policy.summary()
        payload["policy"].pop("invariant_candidates", None)
    return payload


def result_path(out_dir: Path, job: HybridJob) -> Path:
    return Path(out_dir) / "results" / f"{job.job_id}.json"


def run_job(job: HybridJob, spec: dict, config: ExternalConfig, out_dir: Path) -> dict:
    import torch

    torch.set_num_threads(1)
    started = time.monotonic()
    record = {"job": job.to_dict(), "provenance": provenance(config),
              "config": config.to_dict()}
    try:
        record.update(status="ok", **run_external_job(job, spec, config))
    except Exception as exc:  # noqa: BLE001 - a failed job is recorded, and retried on restart
        logger.exception("job {} failed", job.job_id)
        record.update(status="error", error=f"{type(exc).__name__}: {exc}",
                      traceback=traceback.format_exc())
    record["wall_clock_s"] = round(time.monotonic() - started, 1)
    _write_json(result_path(out_dir, job), record)
    return record


def _done(out_dir: Path, job: HybridJob) -> bool:
    path = result_path(out_dir, job)
    if not path.is_file():
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("status") == "ok"
    except (OSError, ValueError):
        return False


def run_all(jobs: list[HybridJob], apps: dict, config: ExternalConfig, out_dir: Path,
            workers: int) -> dict:
    """Every job not already done; one application per worker at a time, each job in its
    own interpreter so a hung browser takes down one run, never the schedule."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "experiment.json", {
        "config": config.to_dict(), "apps": apps, "jobs": [j.to_dict() for j in jobs],
    })
    by_app: dict[str, list[HybridJob]] = defaultdict(list)
    for job in jobs:
        if not _done(out_dir, job):
            by_app[job.app].append(job)
    lock = threading.Lock()
    status = {"total": len(jobs), "done": len(jobs) - sum(map(len, by_app.values())),
              "failed": []}

    def worker(app: str) -> None:
        for job in by_app[app]:
            logger.info("start {}", job.job_id)
            command = [sys.executable, "-m", "web_testing_agent.hybrid.external",
                       "--out-dir", str(out_dir), "--job", json.dumps(job.to_dict()),
                       "--app-spec", json.dumps(apps[app]),
                       "--config", json.dumps(config.to_dict())]
            completed = subprocess.run(command, check=False)
            with lock:
                if completed.returncode == 0 and _done(out_dir, job):
                    status["done"] += 1
                else:
                    status["failed"].append(job.job_id)
                logger.info("[{} done of {}] {} -> {}", status["done"], status["total"],
                            job.job_id, "ok" if completed.returncode == 0 else "failed")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(worker, list(by_app)))
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Time-budgeted runs on external applications.")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--apps-file", type=Path, help="JSON: application -> how to run it")
    parser.add_argument("--apps", nargs="*", help="subset of the applications in --apps-file")
    parser.add_argument("--arms", nargs="*", default=["random"])
    parser.add_argument("--seeds", nargs="*", type=int, default=[0, 1, 2])
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--time-budget", type=float, default=ONE_HOUR, help="seconds per run")
    parser.add_argument("--job", help="(internal)")
    parser.add_argument("--app-spec", help="(internal)")
    parser.add_argument("--config", help="(internal)")
    arguments = parser.parse_args(argv)
    if arguments.job:
        raw = json.loads(arguments.job)
        raw.pop("job_id", None)
        raw["train_apps"] = tuple(raw.get("train_apps") or ())
        record = run_job(HybridJob(**raw), json.loads(arguments.app_spec),
                         ExternalConfig(**json.loads(arguments.config)), arguments.out_dir)
        return 0 if record["status"] == "ok" else 1
    unknown = set(arguments.arms) - set(ARMS)
    if unknown:
        parser.error(f"unknown arm(s): {sorted(unknown)}")
    apps = json.loads(arguments.apps_file.read_text(encoding="utf-8"))
    names = arguments.apps or list(apps)
    missing = set(names) - set(apps)
    if missing:
        parser.error(f"not in {arguments.apps_file}: {sorted(missing)}")
    jobs = [HybridJob(arm, app, seed, build="external")
            for app in names for arm in arguments.arms for seed in arguments.seeds]
    config = ExternalConfig(time_budget_s=arguments.time_budget)
    status = run_all(jobs, {name: apps[name] for name in names}, config, arguments.out_dir,
                     arguments.workers)
    logger.info("schedule finished: {}", status)
    return 0 if not status["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
