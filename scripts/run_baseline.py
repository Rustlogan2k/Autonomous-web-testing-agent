"""Random-policy baseline against the toy validation site (or any target URL).

This is the number every later result has to beat. Uniform-random GUI exploration is a
strong baseline in the testing literature, so measuring it *before* training tells you
whether the RL agent is contributing anything at all.

Usage:
    python scripts/run_baseline.py                          # toy site, 5 episodes
    python scripts/run_baseline.py --episodes 20 --steps 100
    python scripts/run_baseline.py --url http://localhost:3000   # e.g. Gitea
    python scripts/run_baseline.py --valid-only             # masked-action variant
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from web_testing_agent.envs.functional_env import WebFunctionalEnv  # noqa: E402
from web_testing_agent.envs.types import MAX_ACTIONS  # noqa: E402
from web_testing_agent.evaluation import RandomPolicy, run_rollout  # noqa: E402
from web_testing_agent.utils.local_server import serve_directory  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

TOY_SITE = REPO_ROOT / "tests" / "fixtures" / "toy_site"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=None, help="Target base URL (default: serve the local toy site)")
    parser.add_argument("--episodes", type=int, default=5)
    parser.add_argument("--steps", type=int, default=60, help="Max steps per episode")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument(
        "--valid-only",
        action="store_true",
        help="Sample only currently-valid action slots (the action-masked variant)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "reports" / "baseline_random.json",
        help="Where to write the JSON report",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with contextlib.ExitStack() as stack:
        if args.url:
            base_url = args.url
        else:
            if not TOY_SITE.is_dir():
                raise SystemExit(f"Toy site fixture not found at {TOY_SITE}")
            base_url = stack.enter_context(serve_directory(TOY_SITE)) + "/index.html"
            logger.info("Serving toy validation site at {}", base_url)

        env = WebFunctionalEnv(base_url=base_url, max_steps=args.steps, headless=not args.headed)
        stack.callback(env.close)

        label = "random-valid-only" if args.valid_only else "random-full-space"
        policy = RandomPolicy(num_actions=MAX_ACTIONS, seed=args.seed, valid_only=args.valid_only)
        report = run_rollout(env, policy, episodes=args.episodes, label=label, seed=args.seed)

    print("\n" + report.summary() + "\n")
    if report.findings:
        print("Distinct findings:")
        for finding in report.findings:
            print(f"  - [{finding.trigger}] {finding.action} {finding.element!r} @ {finding.url}")
            if finding.detail:
                print(f"      {finding.detail}")
    report.save(args.out)


if __name__ == "__main__":
    main()
