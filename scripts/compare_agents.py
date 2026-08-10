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

Usage:
    python scripts/compare_agents.py --train-steps 4000 --eval-episodes 5
    python scripts/compare_agents.py --site toy --train-steps 4000
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

from stable_baselines3 import DQN  # noqa: E402
from stable_baselines3.common.monitor import Monitor  # noqa: E402
from stable_baselines3.common.vec_env import DummyVecEnv  # noqa: E402

from web_testing_agent.agents.features import FusionFeaturesExtractor  # noqa: E402
from web_testing_agent.agents.masked_dqn import MaskedDQN  # noqa: E402
from web_testing_agent.envs import WebFunctionalEnv  # noqa: E402
from web_testing_agent.envs.types import EPISODE_CONTEXT_DIM, MAX_ACTIONS  # noqa: E402
from web_testing_agent.evaluation import RandomPolicy, run_rollout  # noqa: E402
from web_testing_agent.perception.fusion import FUSED_DIM  # noqa: E402
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
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args()


class TrainedPolicy:
    """Wraps an SB3 model in the rollout harness's `act(obs, info)` interface.

    Encodes through the *same* `encode_modalities` the training wrapper uses, including
    the action mask, so a trained policy is evaluated on exactly the observation it was
    trained on. Evaluating against a differently-shaped observation is the kind of
    mismatch that produces a confident, meaningless comparison.
    """

    def __init__(self, model, encoders) -> None:
        self.model = model
        self.encoders = encoders

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


def evaluate(base_url: str, label: str, policy_factory, args) -> tuple:
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
        report = run_rollout(env, policy, episodes=args.eval_episodes, label=label, seed=args.seed)
    finally:
        env.close()
    report.visited_urls = visited  # type: ignore[attr-defined]
    return report, flow_metrics(report)


def main() -> None:
    args = parse_args()
    site = SITES[args.site]
    if not site.is_dir():
        raise SystemExit(f"Fixture not found: {site}")

    results: dict[str, dict] = {}
    with serve_directory(site) as origin:
        base_url = f"{origin}/index.html"
        logger.info("{} fixture at {}", args.site, base_url)

        for masked in (False, True):
            name = "dqn_masked" if masked else "dqn_unmasked"
            logger.info("=== training {} for {} steps ===", name, args.train_steps)
            venv = DummyVecEnv([
                lambda: Monitor(
                    WebFunctionalEnv(base_url=base_url, max_steps=args.episode_steps, headless=True)
                )
            ])
            venv = SemanticPerceptionWrapper(venv, encoders=default_encoders())
            started = time.monotonic()
            model = build_agent(masked, venv, args.seed, args.train_steps)
            try:
                model.learn(total_timesteps=args.train_steps, progress_bar=False)
            finally:
                venv.close()
            train_s = time.monotonic() - started
            encoders = default_encoders()
            report, flow = evaluate(base_url, name, lambda env: TrainedPolicy(model, encoders), args)
            results[name] = {**report.to_dict(), "flow": flow, "train_seconds": round(train_s, 1)}
            logger.info("{}: {}", name, flow)

        for label, valid_only in (("random_unmasked", False), ("random_masked", True)):
            report, flow = evaluate(
                base_url, label,
                lambda env: RandomPolicy(MAX_ACTIONS, seed=args.seed, valid_only=valid_only), args,
            )
            results[label] = {**report.to_dict(), "flow": flow}
            logger.info("{}: {}", label, flow)

    print("\n" + "=" * 88)
    print(f"{args.site} fixture — {args.eval_episodes} episodes x {args.episode_steps} steps, seed {args.seed}")
    print("=" * 88)
    header = f"{'policy':<18}{'reward':>9}{'valid%':>9}{'states':>8}{'finds':>7}{'maxD':>6}{'meanD':>7}{'done':>6}"
    print(header)
    print("-" * len(header))
    for name, data in results.items():
        flow = data["flow"]
        print(
            f"{name:<18}{data['mean_episode_reward']:>9.2f}{data['valid_action_rate'] * 100:>8.0f}%"
            f"{data['unique_states']:>8}{data['distinct_findings']:>7}"
            f"{flow['max_depth']:>6}{flow['mean_depth']:>7.2f}{flow['reached_receipt']:>6}"
        )
    print("\nmaxD/meanD = deepest / mean stage of the 5-stage order flow; done = steps on the receipt page.")

    out = args.out or REPORTS / f"compare_agents_{args.site}.json"
    out.write_text(json.dumps({"site": args.site, "args": vars(args) | {"out": str(out)},
                               "results": results}, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote {}", out)


if __name__ == "__main__":
    main()
