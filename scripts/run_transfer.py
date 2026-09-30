"""Run the leave-one-application-out transfer experiment, resumably, in parallel.

    # the frozen final experiment over the whole suite
    python scripts/run_transfer.py --out-dir runs/transfer_final --workers 8

    # a development run on the development applications only
    python scripts/run_transfer.py --out-dir runs/dev --folds shop_catalog booking_desk \\
        --arms search_S dqn_scratch --seeds 0 --budget 1000

Re-running the same command resumes: every job whose result file already reports success
is skipped. Results land in `<out-dir>/results/*.json`, one per job, with checkpoints in
`<out-dir>/checkpoints/`, per-step reward logs in `<out-dir>/reward_logs/` and one log per
job in `<out-dir>/logs/`. `scripts/analyze_transfer.py` turns the directory into the report.

Refuses to start unless `reports/benchmark_validation.json` admits every application the
plan uses, at the commit being run: a fold whose fault is unreachable measures the fixture.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from web_testing_agent.benchmark import discover  # noqa: E402
from web_testing_agent.experiments.transfer import (  # noqa: E402
    ARMS,
    ExperimentConfig,
    Schedule,
    build_plan,
    run_schedule,
)

VALIDATION = REPO_ROOT / "reports" / "benchmark_validation.json"


def admitted_apps() -> set[str]:
    if not VALIDATION.is_file():
        return set()
    return set(json.loads(VALIDATION.read_text(encoding="utf-8")).get("admitted", []))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--apps", nargs="*", default=None,
                        help="the suite (default: every application found)")
    parser.add_argument("--folds", nargs="*", default=None,
                        help="which applications to hold out (default: all of --apps)")
    parser.add_argument("--arms", nargs="*", default=list(ARMS), choices=list(ARMS))
    parser.add_argument("--seeds", nargs="*", type=int, default=[0, 1, 2])
    parser.add_argument("--clean-control-arms", nargs="*", default=["search_S", "dqn_pretrained"],
                        help="arms also run once per fold on the clean build (false positives)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--budget", type=int, default=2000)
    parser.add_argument("--pretrain-steps-per-app", type=int, default=1500)
    parser.add_argument("--llm-weight", type=float, default=1.0)
    parser.add_argument("--llm-offline", action="store_true",
                        help="replay cached LLM judgments only; never call the model")
    parser.add_argument("--skip-validation-gate", action="store_true",
                        help="development only: run on applications not yet admitted")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = parser.parse_args()

    apps = args.apps or [app.app_id for app in discover()]
    if not args.skip_validation_gate:
        missing = sorted(set(apps) - admitted_apps())
        if missing:
            print(f"refusing to run: {missing} not admitted by {VALIDATION.relative_to(REPO_ROOT)}. "
                  f"Run scripts/validate_benchmark.py first.", file=sys.stderr)
            return 2

    config = ExperimentConfig(budget=args.budget,
                              pretrain_steps_per_app=args.pretrain_steps_per_app,
                              llm_weight=args.llm_weight, llm_offline=args.llm_offline)
    jobs = build_plan(apps, args.seeds, args.arms, folds=args.folds,
                      clean_control_arms=[a for a in args.clean_control_arms if a in args.arms])
    print(f"{len(jobs)} jobs: {sum(j.kind == 'pretrain' for j in jobs)} pretraining, "
          f"{sum(j.kind == 'hunt' for j in jobs)} hunts; folds={args.folds or apps}")
    if args.dry_run:
        for job in jobs:
            print("  ", job.job_id, "<-", job.depends_on() or "")
        return 0
    summary = run_schedule(Schedule(jobs=jobs, out_dir=args.out_dir, config=config,
                                    workers=args.workers))
    print(json.dumps(summary, indent=2))
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
