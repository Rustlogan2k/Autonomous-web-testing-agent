"""The whole pipeline, from a repository on disk to a bug report.

    repo -> profile -> detection -> policy gate -> build -> run -> health check
         -> base_url -> WebFunctionalEnv -> exploration -> judge -> bug report
         -> teardown

This is the demonstrable path §2 describes, with the parts that exist wired together and
the parts that do not left out rather than faked.

**This file is the command line, not the pipeline.** The orchestration lives in
`web_testing_agent.pipeline`, where it is callable without a terminal and its stages can
be substituted for testing. What stays here is what genuinely belongs to a CLI: parsing
arguments, printing progress, and writing the report to a path the user chose.

**Scope, stated here because this is the entry point people will run.** The repository is
assumed to be *trusted or controlled* — your own fixture, a known application, something a
collaborator handed you. Execution is resource-limited, and that is not the same as
contained. `docker build` runs the repository's own build commands **before** any
`docker run` restriction exists, so a hostile repo does not need to escape the container.
Point this at a disposable machine if the input is not genuinely trusted.

Usage:

    # deployment only, to see the base_url the agent would be handed
    python scripts/run_repo.py --repo tests/fixtures/demo_repo --deploy-only

    # the full path, deterministic triggers only (no model required)
    python scripts/run_repo.py --repo tests/fixtures/demo_repo --judge stub

    # the full path with a live judge
    python scripts/run_repo.py --repo tests/fixtures/demo_repo \
        --judge ollama --model qwen2.5:7b-instruct --episodes 2 --steps 25
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from web_testing_agent.pipeline import (  # noqa: E402
    STAGE_DEPLOYED,
    STAGE_DETECTED,
    STAGE_PROFILED,
    STAGE_ROLLOUT,
    RunSettings,
    run_pipeline,
)
from web_testing_agent.reporting import render_markdown  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)
REPORTS = REPO_ROOT / "reports"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, required=True, help="repository to deploy and test")
    parser.add_argument("--deploy-only", action="store_true",
                        help="build, run, print the base_url, wait for Enter, then tear down")
    parser.add_argument("--episodes", type=int, default=2)
    parser.add_argument("--steps", type=int, default=25)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--judge", choices=["stub", "ollama"], default="stub")
    parser.add_argument("--model", default="qwen2.5:7b-instruct")
    parser.add_argument("--prompt", choices=["detailed", "compact"], default="compact")
    parser.add_argument("--min-confidence", type=float, default=0.0)
    parser.add_argument("--port", type=int, default=None,
                        help="container port to publish, when the image declares no EXPOSE")
    parser.add_argument(
        "--build-network", default="none", choices=["none", "bridge"],
        help="'none' (default) denies the repository's build commands network egress. "
             "'bridge' is required by repos that install packages at build time, and is a "
             "real loosening of an already-narrow control.",
    )
    parser.add_argument("--build-timeout", type=int, default=600)
    parser.add_argument("--boot-timeout", type=int, default=180)
    parser.add_argument("--allow-writable-rootfs", action="store_true",
                        help="relax read_only, which many real images need in order to start")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--no-evidence", action="store_true",
                        help="skip artifact capture. Findings then carry only the step "
                             "number they were found at, as they did before evidence "
                             "existed.")
    parser.add_argument("--no-screenshots", action="store_true",
                        help="capture evidence but not frames, when disk or time is tight")
    return parser.parse_args()


def settings_from_args(args: argparse.Namespace) -> RunSettings:
    """The CLI's arguments as the pipeline's settings. The only translation layer."""
    return RunSettings(
        episodes=args.episodes,
        steps=args.steps,
        seed=args.seed,
        min_confidence=args.min_confidence,
        port=args.port,
        build_network=args.build_network,
        build_timeout_s=args.build_timeout,
        boot_timeout_s=args.boot_timeout,
        allow_writable_rootfs=args.allow_writable_rootfs,
        judge=args.judge,
        model=args.model,
        prompt=args.prompt,
        capture_screenshots=not args.no_screenshots,
    )


def print_stage(stage: str, data: dict) -> None:
    """Render one pipeline stage. The pipeline itself never prints."""
    if stage == STAGE_PROFILED:
        profile = data["profile"]
        if profile.get("available"):
            systems = ", ".join(d["value"] for d in profile.get("build_systems") or []) or "none"
            print(f"profile       : {profile.get('primary_language') or 'unknown'} · "
                  f"build systems: {systems} · {profile['stats']['files_scanned']} files")
            for note in profile.get("notes") or []:
                print(f"  note        : {note}")
        else:
            print(f"profile       : unavailable ({profile.get('error', 'unknown error')})")
    elif stage == STAGE_DETECTED:
        plan = data["plan"]
        print(f"detected      : {plan.kind} at {plan.path}")
        if plan.policy is not None:
            for violation in plan.policy.violations:
                print(f"  policy      : {violation}")
    elif stage == STAGE_DEPLOYED:
        deployment = data["deployment"]
        print(f"base_url      : {deployment.base_url}")
        print(f"container     : {deployment.container_id[:12]}  image {deployment.image_tag}")
    elif stage == STAGE_ROLLOUT:
        print("\n" + data["rollout"].summary())


def main() -> None:
    args = parse_args()

    def confirm(_deployment) -> bool:  # noqa: ANN001 - Deployment, only used for the prompt
        """`--deploy-only` holds the deployment open until the operator is done with it."""
        if not args.deploy_only:
            return True
        input("\nDeployment is live. Press Enter to tear it down... ")
        return False

    # Resolved before the run because the evidence directory is derived from it: the
    # trace sits beside the report it belongs to, so moving one moves the other.
    out = args.out or REPORTS / f"repo_intake_{args.repo.resolve().name}_report.md"
    evidence_dir = None if args.no_evidence else out.parent / f"{out.stem}_evidence"

    result = run_pipeline(
        args.repo,
        settings_from_args(args),
        observer=print_stage,
        on_deployment=confirm,
        evidence_dir=evidence_dir,
    )
    if result.stopped_after_deploy:
        return

    report = result.report

    # Written after teardown, so the artifact exists even if cleanup is noisy.
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(report), encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")

    counts = report.counts
    print("\n" + "=" * 74)
    print("bug report")
    print("=" * 74)
    print(f"findings      : {counts['total']} ({counts['deterministic']} deterministic, "
          f"{counts['judge']} judge)")
    print(f"severity      : {counts['high']} high, {counts['medium']} medium, {counts['low']} low")
    if counts["ungrounded_excluded"]:
        print(f"excluded      : {counts['ungrounded_excluded']} ungrounded verdict(s)")
    if result.evidence.get("available"):
        print(f"evidence      : {result.evidence.get('indexed_steps', 0)} step(s) referenced "
              f"in {result.evidence.get('trace_dir', '')}")
    print(f"written to    : {out}")


if __name__ == "__main__":
    main()
