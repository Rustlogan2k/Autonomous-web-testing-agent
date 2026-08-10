"""Train a DQN on the toy validation site and compare it against the random baselines.

This answers the load-bearing question the whole project rests on — *does RL contribute
anything over uniform-random exploration?* — at the smallest scale where the answer is
meaningful, and before any GPU encoder or LLM reward model exists to confound it.

The trained agent and both baselines are evaluated **in this same process, against the
same site, seed, and step budget, through the same `run_rollout` harness**. Evaluation
runs against the raw env for all three; the trained policy does its own encoding via
`encode_modalities`, the identical call the training wrapper uses. Anything less than
that and the comparison measures the harness, not the policy.

Usage:
    python scripts/train_toy.py                       # 4000 train steps, 3x40-step eval
    python scripts/train_toy.py --train-steps 20000
    python scripts/train_toy.py --skip-train --model models/checkpoints/dqn_toy.zip
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import numpy as np  # noqa: E402
from stable_baselines3 import DQN  # noqa: E402
from stable_baselines3.common.callbacks import BaseCallback  # noqa: E402
from stable_baselines3.common.monitor import Monitor  # noqa: E402
from stable_baselines3.common.vec_env import DummyVecEnv  # noqa: E402

from web_testing_agent.agents.features import FusionFeaturesExtractor  # noqa: E402
from web_testing_agent.envs.functional_env import WebFunctionalEnv  # noqa: E402
from web_testing_agent.envs.types import EPISODE_CONTEXT_DIM, MAX_ACTIONS  # noqa: E402
from web_testing_agent.evaluation import (  # noqa: E402
    RandomPolicy,
    RolloutReport,
    TrainedPolicy,
    run_rollout,
)
from web_testing_agent.perception.fusion import FUSED_DIM  # noqa: E402
from web_testing_agent.perception.vec_wrapper import SemanticPerceptionWrapper, default_encoders  # noqa: E402
from web_testing_agent.utils.local_server import serve_directory  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

TOY_SITE = REPO_ROOT / "tests" / "fixtures" / "toy_site"
ANSWER_KEY = TOY_SITE / "answer_key.json"


class KeepBestByTrainingReturn(BaseCallback):
    """Snapshot the best policy seen during training, by rolling episode return.

    DQN on this task is not monotonic: the measured curve peaked at +10.10 around step
    2200 (3.4x the random baseline) and then declined for the next 5000 steps to -32.50.
    Evaluating whatever policy happens to exist at `total_timesteps` therefore measures
    the end of the collapse rather than what the algorithm achieved.

    Uses SB3's `ep_info_buffer` (populated by the `Monitor` wrapper) rather than a second
    eval env, because an eval env here means a second Chromium and a large share of the
    wall clock. It is a rolling *training* return, so it is optimistic — the reported
    result is still the independent evaluation in `run_rollout`, not this number.
    """

    def __init__(self, save_path: Path, min_episodes: int = 10, verbose: int = 0) -> None:
        super().__init__(verbose)
        self.save_path = save_path
        self.min_episodes = min_episodes
        self.best_mean = -float("inf")
        self.best_step = 0

    def _on_step(self) -> bool:
        buffer = self.model.ep_info_buffer
        if not buffer or len(buffer) < self.min_episodes:
            return True
        mean_return = float(np.mean([entry["r"] for entry in buffer]))
        if mean_return > self.best_mean:
            self.best_mean = mean_return
            self.best_step = self.num_timesteps
            self.save_path.parent.mkdir(parents=True, exist_ok=True)
            self.model.save(self.save_path)
        return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--train-steps", type=int, default=4000)
    parser.add_argument("--episode-steps", type=int, default=40, help="max steps per episode (train and eval)")
    parser.add_argument("--eval-episodes", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--skip-train", action="store_true", help="load --model instead of training")
    parser.add_argument("--model", type=Path, default=REPO_ROOT / "models" / "checkpoints" / "dqn_toy.zip")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports" / "toy_dqn_comparison.json")
    return parser.parse_args()


def build_dqn(venv, seed: int, episode_steps: int, train_steps: int) -> DQN:
    """DQN with the spec's hyperparameters, minus two documented pilot-scale changes."""
    return DQN(
        "MlpPolicy",
        venv,
        learning_rate=1e-4,            # spec
        buffer_size=50_000,            # spec
        batch_size=32,                 # spec
        gamma=0.99,                    # spec
        exploration_fraction=0.2,      # spec
        exploration_initial_eps=1.0,   # spec
        exploration_final_eps=0.05,    # spec
        # Spec says 1000. At a 4K-step pilot that is only 4 target refreshes, so the
        # bootstrap target is stale for most of training. Scaled to the run length.
        target_update_interval=max(100, train_steps // 20),
        learning_starts=min(500, train_steps // 8),
        train_freq=4,
        gradient_steps=1,
        # net_arch=[] so the Q-head is a single Linear(state -> 100): the spec's literal
        # "state(256) -> 100 Q-values", with FusionMLP supplying the 256 and the episode
        # -context features appended alongside it.
        policy_kwargs={
            "features_extractor_class": FusionFeaturesExtractor,
            "features_extractor_kwargs": {"features_dim": FUSED_DIM + EPISODE_CONTEXT_DIM},
            "net_arch": [],
        },
        seed=seed,
        # verbose=1 so SB3 prints ep_rew_mean/exploration_rate as training proceeds.
        # Without a training curve, an ambiguous final result cannot be diagnosed —
        # you cannot tell "learned the wrong thing" from "learned nothing".
        verbose=1,
    )


def evaluate(base_url: str, label: str, policy_factory, args, encoders=None) -> RolloutReport:
    """Score one policy against a fresh raw env — identical harness for every policy."""
    env = WebFunctionalEnv(base_url=base_url, max_steps=args.episode_steps, headless=True)
    try:
        policy = policy_factory(env)
        report = run_rollout(
            env, policy, episodes=args.eval_episodes, label=label, seed=args.seed
        )
    finally:
        env.close()
    logger.info("\n{}", report.summary())
    return report


def _answer_key_hits(report: RolloutReport) -> dict:
    """Map findings back onto the seeded-bug ground truth."""
    if not ANSWER_KEY.is_file():
        return {}
    key = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))
    detectable = [b for b in key["bugs"] if "deterministic" in b["detectability"]]
    blob = " ".join(f"{f.url} {f.element} {f.detail}" for f in report.findings).lower()
    hits = []
    for bug in detectable:
        needles = {
            "BUG-03": "pricing",
            "BUG-04": "generate report",
            "BUG-07": "sync now",
        }
        needle = needles.get(bug["id"])
        if needle and needle in blob:
            hits.append(bug["id"])
    return {
        "detectable_total": len(detectable),
        "found": hits,
        "recall": round(len(hits) / len(detectable), 3) if detectable else 0.0,
    }


def main() -> None:
    args = parse_args()
    if not TOY_SITE.is_dir():
        raise SystemExit(f"Toy site fixture not found at {TOY_SITE}")

    reports: dict[str, RolloutReport] = {}
    best_meta: dict = {}
    train_seconds = 0.0

    with serve_directory(TOY_SITE) as origin:
        base_url = f"{origin}/index.html"
        logger.info("Toy validation site at {}", base_url)

        # --- train -------------------------------------------------------------
        if args.skip_train:
            logger.info("Loading existing model from {}", args.model)
            model = DQN.load(args.model, device="auto")
        else:
            # Monitor is what records episode returns; without it SB3 logs
            # exploration_rate but no ep_rew_mean, leaving no learning curve to
            # diagnose an ambiguous final policy from.
            venv = DummyVecEnv([
                lambda: Monitor(
                    WebFunctionalEnv(base_url=base_url, max_steps=args.episode_steps, headless=True)
                )
            ])
            venv = SemanticPerceptionWrapper(venv, encoders=default_encoders())
            model = build_dqn(venv, args.seed, args.episode_steps, args.train_steps)
            logger.info("Training DQN for {} steps…", args.train_steps)
            best_path = args.model.with_name(args.model.stem + "_best.zip")
            keeper = KeepBestByTrainingReturn(best_path)
            started = time.monotonic()
            with contextlib.closing(venv):
                model.learn(total_timesteps=args.train_steps, callback=keeper, progress_bar=False)
            train_seconds = time.monotonic() - started
            args.model.parent.mkdir(parents=True, exist_ok=True)
            model.save(args.model)
            logger.info("Trained in {:.1f}s ({:.2f} steps/s); final -> {}",
                        train_seconds, args.train_steps / train_seconds, args.model)

            # Report both: the final policy and the best one seen. If they differ a lot,
            # training was unstable and that instability is itself the result.
            if keeper.best_step:
                logger.info("Best training return {:+.2f} at step {}; loading {} for evaluation",
                            keeper.best_mean, keeper.best_step, best_path)
                final_model, model = model, DQN.load(best_path, device="auto")
                best_meta.update({"best_mean_training_return": round(keeper.best_mean, 2),
                                  "best_step": keeper.best_step})
                reports["dqn_final"] = evaluate(
                    base_url, "dqn-final-policy",
                    lambda env: TrainedPolicy(final_model, default_encoders(), deterministic=True), args,
                )

        # --- evaluate all three, same site / seed / budget ----------------------
        eval_encoders = default_encoders()
        reports["dqn"] = evaluate(
            base_url, "dqn-trained",
            lambda env: TrainedPolicy(model, eval_encoders, deterministic=True), args,
        )
        reports["random_full"] = evaluate(
            base_url, "random-full-space",
            lambda env: RandomPolicy(MAX_ACTIONS, seed=args.seed, valid_only=False), args,
        )
        reports["random_masked"] = evaluate(
            base_url, "random-valid-only",
            lambda env: RandomPolicy(MAX_ACTIONS, seed=args.seed, valid_only=True), args,
        )

    # --- report ----------------------------------------------------------------
    rows = [
        ("mean episode reward", lambda r: f"{r.mean_episode_reward:+.2f}"),
        ("distinct findings", lambda r: str(r.distinct_findings)),
        ("seeded bugs found", lambda r: f"{len(_answer_key_hits(r).get('found', []))}/3"),
        ("unique states", lambda r: str(r.unique_states)),
        ("valid-action rate", lambda r: f"{r.valid_action_rate:.1%}"),
        ("NO_OP steps", lambda r: f"{r.noop_steps / max(r.steps, 1):.1%}"),
        ("exec-success rate", lambda r: f"{r.exec_success_rate:.1%}"),
        ("steps/s", lambda r: f"{r.steps_per_second:.2f}"),
    ]
    order = [("DQN (best)", "dqn")]
    if "dqn_final" in reports:
        order.append(("DQN (final)", "dqn_final"))
    order += [("Random (full)", "random_full"), ("Random (masked)", "random_masked")]

    width = max(len(name) for name, _ in rows) + 2
    header = f"{'metric':<{width}}" + "".join(f"{name:>22}" for name, _ in order)
    print("\n" + "=" * len(header))
    print(f"Toy site — {args.eval_episodes} episodes x {args.episode_steps} steps, seed {args.seed}")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for metric, fmt in rows:
        print(f"{metric:<{width}}" + "".join(f"{fmt(reports[k]):>22}" for _, k in order))
    print("=" * len(header) + "\n")

    for label, key in order:
        hits = _answer_key_hits(reports[key])
        print(f"{label}: seeded bugs {sorted(hits.get('found', []))} (recall {hits.get('recall', 0):.0%})")
    print()

    payload = {
        "config": {
            "train_steps": 0 if args.skip_train else args.train_steps,
            "train_seconds": round(train_seconds, 1),
            "eval_episodes": args.eval_episodes,
            "episode_steps": args.episode_steps,
            "seed": args.seed,
            "reward_model": "NullRewardModel (deterministic triggers only)",
            "encoders": "HashEmbeddingEncoder stand-ins (no semantics)",
            **best_meta,
        },
        "results": {
            key: {**reports[key].to_dict(), "answer_key": _answer_key_hits(reports[key])}
            for _, key in order
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote comparison to {}", args.out)


if __name__ == "__main__":
    main()
