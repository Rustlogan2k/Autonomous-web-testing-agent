"""The whole pipeline, from a repository on disk to a bug report.

    repo -> detection -> policy gate -> build -> run -> health check
         -> base_url -> WebFunctionalEnv -> exploration -> judge -> bug report
         -> teardown

This is the demonstrable path §2 describes, with the parts that exist wired together and
the parts that do not left out rather than faked.

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
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from web_testing_agent.envs import WebFunctionalEnv  # noqa: E402
from web_testing_agent.envs.types import MAX_ACTIONS  # noqa: E402
from web_testing_agent.evaluation import RandomPolicy, run_rollout  # noqa: E402
from web_testing_agent.intake import deploy_repository, detect_build_definition  # noqa: E402
from web_testing_agent.reporting import build_report, render_markdown  # noqa: E402
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
    return parser.parse_args()


def build_judge(args: argparse.Namespace):
    """The reward model, or None when only deterministic triggers are wanted."""
    if args.judge == "stub":
        return None
    from web_testing_agent.judge.ollama import OllamaJudge
    from web_testing_agent.reward.llm_judge import JudgeRewardModel

    return JudgeRewardModel(
        judge=OllamaJudge(model=args.model, prompt_style=args.prompt),
        min_confidence=args.min_confidence,
    )


def main() -> None:
    args = parse_args()
    repo = args.repo.resolve()

    plan = detect_build_definition(repo)
    print(f"detected      : {plan.kind} at {plan.path}")
    if plan.policy is not None:
        for violation in plan.policy.violations:
            print(f"  policy      : {violation}")

    run_args = {"read_only": False} if args.allow_writable_rootfs else None
    started = time.monotonic()

    with deploy_repository(
        repo,
        port=args.port,
        build_network=args.build_network,
        build_timeout_s=args.build_timeout,
        boot_timeout_s=args.boot_timeout,
        run_args=run_args,
    ) as deployment:
        print(f"base_url      : {deployment.base_url}")
        print(f"container     : {deployment.container_id[:12]}  image {deployment.image_tag}")

        if args.deploy_only:
            input("\nDeployment is live. Press Enter to tear it down... ")
            return

        reward_model = build_judge(args)
        env = WebFunctionalEnv(
            base_url=deployment.base_url,
            max_steps=args.steps,
            headless=True,
            reward_model=reward_model,
        )
        try:
            rollout = run_rollout(
                env,
                RandomPolicy(MAX_ACTIONS, seed=args.seed, valid_only=True),
                episodes=args.episodes,
                label="repo-intake",
                seed=args.seed,
            )
        finally:
            env.close()

        elapsed = time.monotonic() - started
        print("\n" + rollout.summary())

        verdicts = list(getattr(reward_model, "verdicts", []) or [])
        report = build_report(
            target=deployment.base_url,
            findings=rollout.to_dict().get("findings") or [],
            verdicts=verdicts,
            min_confidence=args.min_confidence,
            run_meta={
                "repository": str(repo),
                "build_definition": str(plan.path),
                "build_network": args.build_network,
                "image": deployment.image_tag,
                "episodes": args.episodes,
                "steps_per_episode": args.steps,
                "seed": args.seed,
                "judge": args.judge if args.judge == "stub" else f"{args.judge}/{args.model}",
                "wall_clock_s": round(elapsed, 1),
                # Recorded in the artifact itself, because a report that travels without
                # this reads as a security assessment and it is not one.
                "scope_note": (
                    "Trusted/controlled repository input, resource-limited execution. "
                    "NOT a security boundary against a determined attacker: Dockerfile "
                    "build commands execute before any docker run restriction applies."
                ),
            },
        )

    # Written after teardown, so the artifact exists even if cleanup is noisy.
    out = args.out or REPORTS / f"repo_intake_{repo.name}_report.md"
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
    print(f"written to    : {out}")


if __name__ == "__main__":
    main()
