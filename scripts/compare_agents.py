"""Masked DQN vs unmasked DQN vs random, on the deep-flow fixture.

This is the fair-fight rerun of the run-5 comparison (§5, 2026-08-02), which measured
DQN 1/3 seeded bugs against masked random's 3/3 and concluded — correctly — that the
gap was about **action masking** rather than about RL, because masked random could
sample only legal slots and the flat 100-way head structurally could not.

Two things changed since, and both are needed for the comparison to mean anything:

* `agents.masked_dqn` gives the agent the same ability the baseline always had.
* `tests/fixtures/deep_flow_site` is a regime where the answer is not a foregone
  conclusion. On the toy site nearly every bug sits one or two steps from the landing
  page, which is exactly where uniform-random is strongest and sequencing is worth
  nothing. Here the defect is behind four gates, so random's success probability decays
  exponentially in sequence length.

**The headline metric here is flow depth, not bugs found.** A judge cannot find a
defect in a state the explorer never reached, so on this fixture "how far down the flow
did the policy get" is the thing being measured; recall would just be a noisy proxy for
it. `--site toy` runs the same four policies on the shallow fixture for contrast.

**Why this reports a spread over seeds rather than one number.** The first version of
this script trained one seed and evaluated it greedily. Against a static local fixture a
greedy policy is a deterministic function of the page, so all five "episodes" replayed
one trajectory — the 2026-08-10 semantic run recorded `[-24.45] * 5`, five identical
numbers reported as a mean. That made the DQN rows n=1 while the random rows, being
genuinely stochastic, were n=5, and the two were printed in the same table as though
comparable. A 4,000-step DQN varies enormously across seeds, so a single seed cannot
support a conclusion in either direction. Two changes:

* `--seeds` trains and evaluates the whole comparison once per seed, and the table
  reports median [min-max]. Cost is linear: about 45 min per seed at 4,000 steps.
* `--eval-epsilon` keeps the trained policy's residual exploration during evaluation
  (0.05, the value it finished training at), so its episodes differ from each other for
  the same reason the random baseline's do. Pass 0 for the old greedy behaviour, in
  which case episodes beyond the first carry no additional information.

Usage:
    python scripts/compare_agents.py --train-steps 4000 --seeds 0 1 2
    python scripts/compare_agents.py --site toy --train-steps 4000 --seeds 0
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from stable_baselines3 import DQN  # noqa: E402
from stable_baselines3.common.monitor import Monitor  # noqa: E402
from stable_baselines3.common.vec_env import DummyVecEnv  # noqa: E402

from web_testing_agent.agents.features import FusionFeaturesExtractor  # noqa: E402
from web_testing_agent.agents.masked_dqn import MaskedDQN  # noqa: E402
from web_testing_agent.envs import WebFunctionalEnv  # noqa: E402
from web_testing_agent.envs.types import EPISODE_CONTEXT_DIM, MAX_ACTIONS  # noqa: E402
from web_testing_agent.evaluation import RandomPolicy, run_rollout  # noqa: E402
from web_testing_agent.perception.fusion import FUSED_DIM  # noqa: E402
from web_testing_agent.perception.encoders.models import semantic_encoders  # noqa: E402
from web_testing_agent.perception.vec_wrapper import (  # noqa: E402
    SemanticPerceptionWrapper,
    default_encoders,
    encode_modalities,
)
from web_testing_agent.utils.local_server import serve_directory  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

SITES = {
    "deep": REPO_ROOT / "tests" / "fixtures" / "deep_flow_site",
    "toy": REPO_ROOT / "tests" / "fixtures" / "toy_site",
}
REPORTS = REPO_ROOT / "reports"

# How far down the ordering flow a URL represents. Depth is what this fixture measures.
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
    parser.add_argument("--train-steps", type=int, default=4000)
    parser.add_argument("--episode-steps", type=int, default=40)
    parser.add_argument("--eval-episodes", type=int, default=5)
    parser.add_argument(
        "--seeds", type=int, nargs="+", default=[0, 1, 2],
        help="training seeds; the whole comparison is repeated once per seed and the "
             "table reports median [min-max]. One seed cannot separate a real effect "
             "from DQN seed variance at this step budget.",
    )
    parser.add_argument(
        "--eval-epsilon", type=float, default=0.05,
        help="residual exploration kept during evaluation, matching the value training "
             "ends at. 0 restores greedy evaluation, which on a static fixture makes "
             "every episode an identical replay of one trajectory.",
    )
    parser.add_argument(
        "--encoders", choices=["hash", "semantic"], default="semantic",
        help="'semantic' loads the real CLIP/CodeBERT/MiniLM encoders; 'hash' uses the "
             "deterministic stubs, which carry no semantics and are what the 2026-08-10 "
             "null result was measured on.",
    )
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args()


class TrainedPolicy:
    """Wraps an SB3 model in the rollout harness's `act(obs, info)` interface.

    Encodes through the *same* `encode_modalities` the training wrapper uses, including
    the action mask, so a trained policy is evaluated on exactly the observation it was
    trained on. Evaluating against a differently-shaped observation is the kind of
    mismatch that produces a confident, meaningless comparison.

    `epsilon` is the residual exploration retained from training. It is not a tweak to
    make the numbers look better: with `epsilon=0` on a static fixture the policy is a
    deterministic function of the page, so every evaluation episode is the same
    trajectory and `--eval-episodes N` reports one sample N times.

    The random branch samples from whatever action space the agent itself had —
    valid-only for the masked arm, all `MAX_ACTIONS` slots for the unmasked one. Giving
    the unmasked agent valid-only exploration at evaluation time would hand it the very
    ability the comparison exists to isolate.
    """

    def __init__(self, model, encoders, *, epsilon: float, masked: bool, seed: int) -> None:
        self.model = model
        self.encoders = encoders
        self.epsilon = epsilon
        self.masked = masked
        self._rng = np.random.default_rng(seed)

    def reset(self) -> None:
        pass

    def act(self, observation: dict, info: dict) -> int:
        features = encode_modalities(
            self.encoders,
            [observation["screenshot"]],
            [info.get("page", {})],
            [info.get("episode_context")],
            [info.get("num_valid_actions")],
        )
        if self.epsilon and self._rng.random() < self.epsilon:
            valid = info.get("num_valid_actions")
            upper = int(valid) if self.masked and valid else MAX_ACTIONS
            return int(self._rng.integers(0, max(1, upper)))
        action, _ = self.model.predict(features, deterministic=True)
        return int(action[0])


def build_agent(masked: bool, venv, seed: int, train_steps: int):
    """Identical hyperparameters either side; the only difference is the masking."""
    cls = MaskedDQN if masked else DQN
    return cls(
        "MlpPolicy",
        venv,
        learning_rate=1e-4,
        buffer_size=50_000,
        batch_size=32,
        gamma=0.99,
        exploration_fraction=0.2,
        exploration_initial_eps=1.0,
        exploration_final_eps=0.05,
        target_update_interval=max(100, train_steps // 20),
        learning_starts=min(500, train_steps // 8),
        train_freq=4,
        gradient_steps=1,
        policy_kwargs={
            "features_extractor_class": FusionFeaturesExtractor,
            "features_extractor_kwargs": {"features_dim": FUSED_DIM + EPISODE_CONTEXT_DIM},
            "net_arch": [],
        },
        seed=seed,
        verbose=0,
    )


def flow_metrics(report) -> dict:
    """Depth reached, and how often the flow was completed."""
    depths = [FLOW_DEPTH.get(url.split("/")[-1].split("?")[0], 0) for url in report.visited_urls]
    return {
        "max_depth": max(depths) if depths else 0,
        "mean_depth": round(sum(depths) / len(depths), 2) if depths else 0.0,
        "reached_receipt": sum(1 for d in depths if d == 5),
        "reached_review": sum(1 for d in depths if d >= 4),
    }


def evaluate(base_url: str, label: str, policy_factory, args, seed: int) -> tuple:
    env = WebFunctionalEnv(base_url=base_url, max_steps=args.episode_steps, headless=True)
    visited: list[str] = []
    try:
        policy = policy_factory(env)
        original_step = env.step

        def tracking_step(action):
            result = original_step(action)
            visited.append(result[4]["page"]["url"])
            return result

        env.step = tracking_step  # type: ignore[method-assign]
        report = run_rollout(env, policy, episodes=args.eval_episodes, label=label, seed=seed)
    finally:
        env.close()
    report.visited_urls = visited  # type: ignore[attr-defined]
    return report, flow_metrics(report)


# Metrics aggregated across seeds. Reported as median [min-max] rather than mean +- sd:
# at three seeds a standard deviation is not meaningful, and the range says exactly what
# the reader needs to know — whether the seeds agree.
_AGGREGATED = (
    ("reward", lambda r: r["mean_episode_reward"]),
    ("valid%", lambda r: r["valid_action_rate"] * 100),
    ("states", lambda r: r["unique_states"]),
    ("finds", lambda r: r["distinct_findings"]),
    ("maxD", lambda r: r["flow"]["max_depth"]),
    ("meanD", lambda r: r["flow"]["mean_depth"]),
)


def aggregate(per_seed: list[dict]) -> dict:
    """median / min / max for each headline metric across the seeds."""
    summary: dict[str, dict] = {}
    for name, extract in _AGGREGATED:
        values = [float(extract(run)) for run in per_seed]
        summary[name] = {
            "median": round(statistics.median(values), 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
        }
    summary["seeds"] = len(per_seed)
    return summary


def _fmt(stat: dict, width: int = 20, decimals: int = 2) -> str:
    """`median [min-max]`, or just the median when every seed agreed."""
    median, low, high = stat["median"], stat["min"], stat["max"]
    if low == high:
        return f"{median:.{decimals}f}".rjust(width)
    return f"{median:.{decimals}f} [{low:.{decimals}f}-{high:.{decimals}f}]".rjust(width)


def main() -> None:
    args = parse_args()
    site = SITES[args.site]
    if not site.is_dir():
        raise SystemExit(f"Fixture not found: {site}")

    # Loaded once and shared by training and evaluation. Two separate instances would
    # be two separate caches and, worse, two copies of the models in 6 GB of VRAM.
    build_encoders = semantic_encoders if args.encoders == "semantic" else default_encoders
    encoders = build_encoders()

    # label -> one entry per seed. Kept unaggregated in the report as well, so a reader
    # can see the individual seeds rather than having to trust the summary of them.
    per_seed: dict[str, list[dict]] = {}
    with serve_directory(site) as origin:
        base_url = f"{origin}/index.html"
        logger.info("{} fixture at {}", args.site, base_url)

        for seed in args.seeds:
            logger.info("################ seed {} ################", seed)
            for masked in (False, True):
                name = "dqn_masked" if masked else "dqn_unmasked"
                logger.info("=== training {} (seed {}) for {} steps ===", name, seed, args.train_steps)
                venv = DummyVecEnv([
                    lambda: Monitor(
                        WebFunctionalEnv(base_url=base_url, max_steps=args.episode_steps, headless=True)
                    )
                ])
                venv = SemanticPerceptionWrapper(venv, encoders=encoders)
                started = time.monotonic()
                model = build_agent(masked, venv, seed, args.train_steps)
                try:
                    model.learn(total_timesteps=args.train_steps, progress_bar=False)
                finally:
                    venv.close()
                train_s = time.monotonic() - started
                report, flow = evaluate(
                    base_url, name,
                    # Bound at definition time: `model`, `masked` and `seed` all change
                    # each iteration, and a late-binding closure would evaluate every
                    # arm against whichever agent happened to be trained last.
                    lambda env, m=model, mk=masked, s=seed: TrainedPolicy(
                        m, encoders, epsilon=args.eval_epsilon, masked=mk, seed=s
                    ),
                    args, seed,
                )
                per_seed.setdefault(name, []).append(
                    {**report.to_dict(), "flow": flow, "seed": seed, "train_seconds": round(train_s, 1)}
                )
                logger.info("{} seed {}: {}", name, seed, flow)

            for label, valid_only in (("random_unmasked", False), ("random_masked", True)):
                report, flow = evaluate(
                    base_url, label,
                    lambda env, v=valid_only, s=seed: RandomPolicy(MAX_ACTIONS, seed=s, valid_only=v),
                    args, seed,
                )
                per_seed.setdefault(label, []).append({**report.to_dict(), "flow": flow, "seed": seed})
                logger.info("{} seed {}: {}", label, seed, flow)

    summaries = {label: aggregate(runs) for label, runs in per_seed.items()}

    print("\n" + "=" * 96)
    print(f"{args.site} fixture — {len(args.seeds)} seeds {args.seeds} x {args.eval_episodes} episodes "
          f"x {args.episode_steps} steps, eval-epsilon={args.eval_epsilon}, encoders={args.encoders}")
    print("=" * 96)
    header = f"{'policy':<18}{'reward':>20}{'valid%':>14}{'states':>12}{'finds':>10}{'meanD':>14}"
    print(header)
    print("-" * len(header))
    for name, stats in summaries.items():
        print(
            f"{name:<18}{_fmt(stats['reward'])}{_fmt(stats['valid%'], 14, 0)}"
            f"{_fmt(stats['states'], 12, 0)}{_fmt(stats['finds'], 10, 0)}{_fmt(stats['meanD'], 14)}"
        )
    print("\nEach cell is median [min-max] across seeds; a bare number means every seed agreed.")
    print("meanD = mean stage of the 5-stage order flow, the headline metric on this fixture.")
    if len(args.seeds) < 3:
        print(f"\nWARNING: {len(args.seeds)} seed(s). A DQN at this step budget varies enough "
              f"across seeds that no difference here supports a conclusion. Use --seeds 0 1 2.")

    out = args.out or REPORTS / f"compare_agents_{args.site}_{args.encoders}.json"
    out.write_text(json.dumps({"site": args.site, "encoders": args.encoders,
                               "args": vars(args) | {"out": str(out)},
                               "summary": summaries,
                               "results_per_seed": per_seed}, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote {}", out)


if __name__ == "__main__":
    main()
