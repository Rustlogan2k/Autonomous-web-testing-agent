"""Admit applications to the benchmark suite, or say exactly why one is not admissible.

    python scripts/validate_benchmark.py                       # every application
    python scripts/validate_benchmark.py --apps shop_catalog   # one
    python scripts/validate_benchmark.py --random-steps 600    # plus the random-walk floor

Runs every check in `web_testing_agent.benchmark.validate` in a real browser and writes
`reports/benchmark_validation.json`. Exits non-zero if any application fails, so it can
gate an experiment: the transfer runner refuses to start on a suite this has not passed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from web_testing_agent.benchmark import EnvSettings, discover, validate_app  # noqa: E402


def _git_head() -> dict:
    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,  # noqa: S603,S607
                                  text=True, timeout=20, check=False).stdout.strip()
        except OSError:
            return ""
    return {"commit": run("rev-parse", "HEAD"),
            "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apps", nargs="*", default=None,
                        help="application ids to validate (default: the whole suite)")
    parser.add_argument("--random-steps", type=int, default=0,
                        help="also run a uniform-random walk of this many steps on each build")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--settle-quiet-ms", type=int, default=None,
                        help="DOM-quiescence window; default is the env's own (250 ms)")
    parser.add_argument("--episode-steps", type=int, default=40)
    parser.add_argument("--max-minimization-trials", type=int, default=40)
    parser.add_argument("--out", type=Path,
                        default=REPO_ROOT / "reports" / "benchmark_validation.json")
    args = parser.parse_args()

    apps = discover()
    if args.apps:
        unknown = set(args.apps) - {a.app_id for a in apps}
        if unknown:
            parser.error(f"unknown application(s): {sorted(unknown)}")
        apps = [a for a in apps if a.app_id in args.apps]
    if not apps:
        print("no benchmark applications found", file=sys.stderr)
        return 2

    settings = EnvSettings(max_steps=args.episode_steps, settle_quiet_ms=args.settle_quiet_ms)
    started = time.time()
    results = []
    for app in apps:
        print(f"== {app.app_id} ({len(app.manifest.faults)} seeded faults)", flush=True)
        report = validate_app(app, settings, random_steps=args.random_steps, seed=args.seed,
                              max_minimization_trials=args.max_minimization_trials)
        results.append(report)
        for check in report.checks:
            mark = "ok  " if check.passed else "FAIL"
            suffix = "" if check.passed or not check.detail else f"  -- {check.detail[:240]}"
            print(f"   {mark} {check.name}{suffix}")
        for fault in report.faults:
            print(f"   {fault['fault_id']}: witness {fault['witness_length']} actions -> "
                  f"minimized {fault.get('minimized_length')} "
                  f"({fault.get('minimization_trials')} trials)")
        if report.random_walk:
            walk = report.random_walk
            print(f"   random walk ({walk['steps']} steps): clean raw candidates "
                  f"{walk['clean']['raw_candidates']}; faulty first verified at "
                  f"{walk['buggy']['time_to_first_verified']} "
                  f"(censored={walk['buggy']['censored']}), "
                  f"{walk['buggy']['steps_per_second']} steps/s")
        print(f"   -> {'ADMITTED' if report.passed else 'NOT ADMITTED'} "
              f"in {report.wall_clock_s:.0f}s", flush=True)

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git": _git_head(),
        "env_settings": settings.to_dict(),
        "random_steps": args.random_steps,
        "seed": args.seed,
        "wall_clock_s": round(time.time() - started, 1),
        "admitted": [r.app_id for r in results if r.passed],
        "rejected": [r.app_id for r in results if not r.passed],
        "applications": [r.to_dict() for r in results],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    temporary.replace(args.out)
    print(f"\n{len(payload['admitted'])}/{len(results)} admitted -> {args.out}")
    return 0 if not payload["rejected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
