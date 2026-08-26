"""Go-Explore on the deep-flow fixture, against the same metric the DQN was scored on.

The DQN comparison (`compare_agents.py`) measured mean flow depth and distinct findings
over 3 seeds and found that **no policy of any kind reached `order-2.html`** — 4,800
evaluated steps, zero. `agents/go_explore.py` explains why (a ~1% gate repeated four
times) and argues the fix is not a better learner but an archive that removes the need
to re-derive a route by chance.

This script tests that claim on the identical fixture, at a **matched env-step budget**,
reporting the same numbers so the rows are comparable:

    python scripts/run_go_explore.py --seeds 0 1 2

`--budget` is env steps, not iterations, precisely so "it explored more because it ran
longer" is not available as an explanation. Route replay is deliberately *not* counted
against it: that is the same accounting `setup_actions` already uses for session
bootstrap, and it is the honest one — a replayed route is a cost in wall clock, not in
exploration. Wall clock is reported alongside so the trade is visible rather than
hidden.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from itertools import count
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from web_testing_agent.agents.go_explore import run_go_explore  # noqa: E402
from web_testing_agent.envs import WebFunctionalEnv  # noqa: E402
from web_testing_agent.evaluation.rollout import Finding, _record_findings  # noqa: E402
from web_testing_agent.utils.local_server import serve_directory  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

SITES = {
    "deep": REPO_ROOT / "tests" / "fixtures" / "deep_flow_site",
    "toy": REPO_ROOT / "tests" / "fixtures" / "toy_site",
}
REPORTS = REPO_ROOT / "reports"

# Same map as compare_agents.py, so meanD/maxD mean the same thing in both tables.
FLOW_DEPTH = {
    "index.html": 0,
    "order-1.html": 1,
    "order-2.html": 2,
    "order-3.html": 3,
    "order-4.html": 4,
    "receipt.html": 5,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", choices=sorted(SITES), default="deep")
    parser.add_argument(
        "--budget", type=int, default=200,
        help="env steps per seed. Default matches compare_agents.py's evaluation budget "
             "(5 episodes x 40 steps) so the rows are directly comparable.",
    )
    parser.add_argument(
        "--explore-steps", type=int, default=3,
        help="random actions taken after each return before re-selecting. Small on "
             "purpose: most actions on a flow page leave it, so long bursts spend the "
             "step budget wandering rather than at the frontier.",
    )
    parser.add_argument(
        "--depth-bias", type=float, default=1.0,
        help="exponent on route length in cell selection; 0 disables the depth "
             "preference and makes selection count-based only.",
    )
    parser.add_argument("--max-route-length", type=int, default=12)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args()


def depth_of(url: str) -> int:
    return FLOW_DEPTH.get(url.split("/")[-1].split("?")[0], 0)


def run_seed(base_url: str, seed: int, args) -> dict:
    """One seed: returns the same metrics compare_agents.py reports, plus archive stats.

    Findings are counted by `rollout._record_findings`, the *same* function the random
    and DQN rows are scored with, rather than by a second implementation here. The first
    version of this script reimplemented the dedup and read the signals from a key that
    does not exist (`info["reward_breakdown"]["bug_signals"]` — the breakdown is spread
    directly into `info`), so it reported 0 findings for every seed and the comparison
    looked like a catastrophic loss that was entirely a measurement bug.
    """
    visited: list[str] = []
    findings: dict[tuple[str, str, str], Finding] = {}
    triggers: Counter = Counter()
    step_counter = count(1)

    def on_step(info: dict, _reward: float) -> None:
        visited.append((info.get("page") or {}).get("url", ""))
        _record_findings(info, next(step_counter), findings, triggers)

    env = WebFunctionalEnv(
        base_url=base_url,
        # The step budget is enforced by `--budget` across the whole run, so an episode
        # must not truncate mid-exploration; this only has to exceed --explore-steps.
        max_steps=args.explore_steps + 2,
        headless=True,
    )
    started = time.monotonic()
    try:
        iterations = max(1, args.budget // args.explore_steps)
        archive, stats = run_go_explore(
            env,
            iterations=iterations,
            explore_steps=args.explore_steps,
            max_route_length=args.max_route_length,
            depth_bias=args.depth_bias,
            seed=seed,
            on_step=on_step,
        )
    finally:
        env.close()
    wall = time.monotonic() - started

    depths = [depth_of(url) for url in visited]
    archive_depths = [depth_of_cell(cell) for cell in archive.cells.values()]
    return {
        "seed": seed,
        "env_steps": stats.env_steps,
        "wall_clock_s": round(wall, 1),
        "unique_states": len(archive.cells),
        "distinct_findings": len(findings),
        "trigger_counts": dict(triggers),
        "max_depth": max(depths) if depths else 0,
        "mean_depth": round(sum(depths) / len(depths), 2) if depths else 0.0,
        "reached_receipt": sum(1 for d in depths if d == 5),
        "reached_review": sum(1 for d in depths if d >= 4),
        # Depth reached in the *archive* rather than in the step stream: a cell at depth
        # 4 means the flow was solved to stage 4 and can be returned to on demand, which
        # is the capability being claimed.
        "archive_max_depth": max(archive_depths) if archive_depths else 0,
        "archive": archive.to_dict(),
        **stats.to_dict(),
    }


def depth_of_cell(cell) -> int:  # noqa: ANN001
    """A cell's flow depth, read off the route rather than the fingerprint.

    The fingerprint is opaque, so depth is inferred from how many `Continue`-style
    stage transitions the stored route contains. Counting route steps that target a
    stage link is exact here because the fixture reveals exactly one per stage.
    """
    return sum(1 for step in cell.path if str(step.get("id", "")).startswith("to-step")) + (
        1 if any(str(s.get("id", "")) == "start-order" for s in cell.path) else 0
    )


def main() -> None:
    args = parse_args()
    site = SITES[args.site]
    if not site.is_dir():
        raise SystemExit(f"Fixture not found: {site}")

    per_seed: list[dict] = []
    with serve_directory(site) as origin:
        base_url = f"{origin}/index.html"
        logger.info("{} fixture at {}", args.site, base_url)
        for seed in args.seeds:
            logger.info("################ go-explore seed {} ################", seed)
            result = run_seed(base_url, seed, args)
            per_seed.append(result)
            logger.info("seed {}: {}", seed, {k: result[k] for k in
                                              ("unique_states", "max_depth", "mean_depth",
                                               "distinct_findings", "reached_receipt")})

    def spread(name: str) -> str:
        values = [float(r[name]) for r in per_seed]
        median, low, high = statistics.median(values), min(values), max(values)
        return f"{median:.2f}" if low == high else f"{median:.2f} [{low:.2f}-{high:.2f}]"

    print("\n" + "=" * 92)
    print(f"go-explore on the {args.site} fixture — {len(args.seeds)} seeds {args.seeds}, "
          f"{args.budget} env steps each, explore-steps={args.explore_steps}")
    print("=" * 92)
    for name in ("unique_states", "max_depth", "mean_depth", "reached_review",
                 "reached_receipt", "distinct_findings", "archive_max_depth",
                 "returns_failed", "wall_clock_s"):
        print(f"  {name:20} {spread(name)}")
    print("\nCompare against compare_agents.py on the same fixture and step budget:")
    print("  random_masked    unique_states 8 [8-9]   mean_depth 0.04   findings 10 [10-12]   receipt 0")
    print("  dqn_masked       unique_states 8 [7-9]   mean_depth 0.03   findings  1 [0-2]    receipt 0")

    out = args.out or REPORTS / f"go_explore_{args.site}.json"
    out.write_text(json.dumps({"site": args.site, "args": vars(args) | {"out": str(out)},
                               "results_per_seed": per_seed}, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote {}", out)


if __name__ == "__main__":
    main()
