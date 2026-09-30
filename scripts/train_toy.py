"""Train a DQN on the toy validation site and compare it against the random baselines.

This answers the load-bearing question the whole project rests on — *does RL contribute
anything over uniform-random exploration?* — at the smallest scale where the answer is
meaningful, and before any GPU encoder or LLM reward model exists to confound it.

The trained agent and both baselines are evaluated **in this same process, against the
same site, seed, and step budget, through the same `run_rollout` harness**. Evaluation
runs against the raw env for all three; the trained policy does its own encoding via
`encode_modalities`, the identical call the training wrapper uses. Anything less than
that and the comparison measures the harness, not the policy.

**One invocation is one training seed, and one seed does not settle this question.** A
DQN at a 4,000-step budget varies enough across seeds that a single run cannot separate a
real difference from initialization noise. Run the seeds separately and compare the
spread; `--eval-epsilon` (default 0.05) additionally keeps the trained policy's residual
exploration during evaluation, without which every episode against this static fixture
is the same trajectory replayed and `--eval-episodes` reports one sample N times.

Usage:
    python scripts/train_toy.py                       # 4000 train steps, 3x40-step eval
    python scripts/train_toy.py --train-steps 20000
    python scripts/train_toy.py --skip-train --model models/checkpoints/dqn_toy.zip

    # three seeds, kept apart so the spread is visible
    for s in 0 1 2; do
        python scripts/train_toy.py --seed $s \
            --model models/checkpoints/dqn_toy_s$s.zip \
            --out reports/toy_dqn_comparison_s$s.json
    done
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
from web_testing_agent.agents.masked_dqn import MaskedDQN  # noqa: E402
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

    **RETIRED 2026-09-02, and off unless `--keep-best-checkpoint` is passed.** The
    selector is confounded by the epsilon schedule and it selects noise. Measured on both
    the 2026-08-27 and 2026-08-28 runs, on all three seeds of each: the masked arm's
    "best" checkpoint is taken at step **481-521 of 4,000**, immediately after
    `learning_starts=500`, while epsilon is still ~0.87. Its high rolling training return
    (+4.7 to +10.4) is what *masked random* earns on this reward, credited to a policy
    that was barely acting — and evaluated greedily at epsilon=0.05 that checkpoint scores
    *worse* than the final policy (3 unique states against 9). The unmasked arm never
    shows it only because its training return is bad throughout, so its best checkpoint
    lands late.

    So the "(best)" rows were never a policy the algorithm achieved; they were a snapshot
    of the exploration schedule. PROJECT_CONTEXT has said "cite the final-policy rows"
    since 2026-08-28 while this script still produced the other ones by default.

    Kept rather than deleted, because the published `toy_dqn_comparison_v*` reports
    contain those rows and a reader has to be able to regenerate what they are looking
    at. Fixing it properly means comparing at matched epsilon or running a held-out
    greedy evaluation, and that is an experiment, not a cleanup.

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
    parser.add_argument(
        "--eval-epsilon", type=float, default=0.05,
        help="residual exploration kept during evaluation, matching the value training "
             "ends at. 0 restores greedy evaluation, under which every episode on this "
             "static fixture is an identical replay of one trajectory and --eval-episodes "
             "reports one sample N times, while the random baselines it is compared "
             "against remain genuinely stochastic.",
    )
    parser.add_argument(
        "--keep-best-checkpoint", action="store_true",
        help="also evaluate the best-by-rolling-training-return checkpoint, the '(best)' "
             "rows in `toy_dqn_comparison_v*.json`. OFF by default: the selector is "
             "confounded by the epsilon schedule and picks a barely-trained network at "
             "step ~500 of 4,000 (see KeepBestByTrainingReturn). Pass it only to "
             "regenerate a historical report, never to produce a new figure.",
    )
    parser.add_argument("--skip-train", action="store_true", help="load --model instead of training")
    parser.add_argument("--model", type=Path, default=REPO_ROOT / "models" / "checkpoints" / "dqn_toy.zip")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports" / "toy_dqn_comparison.json")
    return parser.parse_args()


def build_dqn(venv, seed: int, episode_steps: int, train_steps: int, masked: bool = False) -> DQN:
    """DQN with the spec's hyperparameters, minus two documented pilot-scale changes.

    `masked=True` swaps in `MaskedDQN` and changes **nothing else**. Every
    hyperparameter below is shared by both arms, so the only difference between the two
    DQN rows in the output table is whether invalid slots can be chosen.

    Why the masked arm exists: until 2026-08-27 this script trained an *unmasked* head
    only, so the toy table set a DQN that could waste ~85% of its budget on out-of-range
    no-ops beside a `random_masked` baseline that could not. That gap was a statement
    about masking, not about learning, and the table said so in a footnote rather than
    by measuring the fair comparison. Now it measures it.
    """
    cls = MaskedDQN if masked else DQN
    return cls(
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


def train_arm(base_url: str, args, masked: bool) -> tuple:
    """Train one DQN arm and return (model_to_report, extra_model_or_None, meta).

    Both arms are built by the same `build_dqn`, trained for the same number of steps,
    on the same seed, against the same fixture, and are scored below through the same
    `run_rollout`. The masking is the only difference between them.

    By default the model returned first is the **final** policy and the second is None.
    With `--keep-best-checkpoint` the historical arrangement is restored: first the
    best-by-training-return checkpoint, second the final policy. See
    `KeepBestByTrainingReturn` for why that ordering was retired.
    """
    name = "dqn_masked" if masked else "dqn_unmasked"
    suffix = "masked" if masked else "unmasked"
    model_path = args.model.with_name(f"{args.model.stem}_{suffix}.zip")
    cls = MaskedDQN if masked else DQN

    if args.skip_train:
        logger.info("Loading existing {} model from {}", name, model_path)
        return cls.load(model_path, device="auto"), None, {}

    # Monitor is what records episode returns; without it SB3 logs exploration_rate but
    # no ep_rew_mean, leaving no learning curve to diagnose an ambiguous policy from.
    venv = DummyVecEnv([
        lambda: Monitor(
            WebFunctionalEnv(base_url=base_url, max_steps=args.episode_steps, headless=True)
        )
    ])
    venv = SemanticPerceptionWrapper(venv, encoders=default_encoders())
    model = build_dqn(venv, args.seed, args.episode_steps, args.train_steps, masked=masked)
    logger.info("Training {} for {} steps...", name, args.train_steps)
    best_path = model_path.with_name(model_path.stem + "_best.zip")
    keeper = KeepBestByTrainingReturn(best_path) if args.keep_best_checkpoint else None
    started = time.monotonic()
    with contextlib.closing(venv):
        # `callback=None` is what SB3 receives by default now, which is also what it
        # received before this callback was written.
        model.learn(total_timesteps=args.train_steps, callback=keeper, progress_bar=False)
    train_seconds = time.monotonic() - started
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(model_path)
    logger.info("{} trained in {:.1f}s ({:.2f} steps/s); final -> {}",
                name, train_seconds, args.train_steps / train_seconds, model_path)

    meta = {"train_seconds": round(train_seconds, 1),
            "best_checkpoint_selection": bool(args.keep_best_checkpoint)}
    final_model = None
    if keeper is not None and keeper.best_step:
        # Report both the final policy and the best one seen. If they differ a lot,
        # training was unstable and that instability is itself the result.
        logger.info("{}: best training return {:+.2f} at step {} (epsilon at that step "
                    "is what makes this row uncitable — see KeepBestByTrainingReturn)",
                    name, keeper.best_mean, keeper.best_step)
        final_model, model = model, cls.load(best_path, device="auto")
        meta.update({"best_mean_training_return": round(keeper.best_mean, 2),
                     "best_step": keeper.best_step})
    return model, final_model, meta


def main() -> None:
    args = parse_args()
    if not TOY_SITE.is_dir():
        raise SystemExit(f"Toy site fixture not found at {TOY_SITE}")

    reports: dict[str, RolloutReport] = {}
    arm_meta: dict = {}

    with serve_directory(TOY_SITE) as origin:
        base_url = f"{origin}/index.html"
        logger.info("Toy validation site at {}", base_url)
        eval_encoders = default_encoders()

        # --- both DQN arms: unmasked (the historical row) and masked (the fair one) --
        # By default the primary row IS the final policy and there is no second row.
        # With --keep-best-checkpoint the historical arrangement returns: primary is the
        # best-by-training-return checkpoint, secondary is the final policy.
        primary_label = "best" if args.keep_best_checkpoint else "final"
        for masked in (False, True):
            name = "dqn_masked" if masked else "dqn_unmasked"
            primary_model, secondary_model, meta = train_arm(base_url, args, masked)
            arm_meta[name] = meta
            reports[name] = evaluate(
                base_url, f"{name}-{primary_label}",
                # Bound at definition time: `primary_model` is rebound each iteration,
                # and a late-binding closure would score both arms against the last one
                # trained.
                lambda env, m=primary_model: TrainedPolicy(
                    m, eval_encoders, deterministic=True,
                    epsilon=args.eval_epsilon, seed=args.seed,
                ), args,
            )
            if secondary_model is not None:
                reports[f"{name}_final"] = evaluate(
                    base_url, f"{name}-final",
                    lambda env, m=secondary_model: TrainedPolicy(
                        m, eval_encoders, deterministic=True,
                        epsilon=args.eval_epsilon, seed=args.seed,
                    ), args,
                )

        # --- baselines, same site / seed / budget / harness ----------------------
        reports["random_full"] = evaluate(
            base_url, "random-full-space",
            lambda env: RandomPolicy(MAX_ACTIONS, seed=args.seed, valid_only=False), args,
        )
        reports["random_masked"] = evaluate(
            base_url, "random-valid-only",
            lambda env: RandomPolicy(MAX_ACTIONS, seed=args.seed, valid_only=True), args,
        )

    # --- report -----------------------------------------------------------------
    rows = [
        ("mean episode reward", lambda r: f"{r.mean_episode_reward:+.2f}"),
        ("distinct findings", lambda r: str(r.distinct_findings)),
        ("seeded bugs found", lambda r: f"{len(_answer_key_hits(r).get('found', []))}/3"),
        ("unique states", lambda r: str(r.unique_states)),
        ("valid-action rate", lambda r: f"{r.valid_action_rate:.1%}"),
        ("NO_OP steps", lambda r: f"{r.noop_steps / max(r.steps, 1):.1%}"),
        ("exec-success rate", lambda r: f"{r.exec_success_rate:.1%}"),
        ("states / 100 steps", lambda r: f"{100 * r.unique_states / max(r.steps, 1):.1f}"),
        ("findings / 100 steps", lambda r: f"{100 * r.distinct_findings / max(r.steps, 1):.1f}"),
        ("steps/s", lambda r: f"{r.steps_per_second:.2f}"),
    ]
    order = [
        (f"DQN unmasked ({primary_label})", "dqn_unmasked"),
        ("DQN unmasked (final)", "dqn_unmasked_final"),
        (f"DQN masked ({primary_label})", "dqn_masked"),
        ("DQN masked (final)", "dqn_masked_final"),
        ("Random (full)", "random_full"),
        ("Random (masked)", "random_masked"),
    ]
    order = [(label, key) for label, key in order if key in reports]

    width = max(len(name) for name, _ in rows) + 2
    header = f"{'metric':<{width}}" + "".join(f"{name:>22}" for name, _ in order)
    print("\n" + "=" * len(header))
    print(f"Toy site - {args.eval_episodes} episodes x {args.episode_steps} steps, seed {args.seed}")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for metric, fmt in rows:
        print(f"{metric:<{width}}" + "".join(f"{fmt(reports[k]):>22}" for _, k in order))
    print("=" * len(header) + "\n")

    for label, key in order:
        hits = _answer_key_hits(reports[key])
        print(f"{label}: seeded bugs {sorted(hits.get('found', []))} (recall {hits.get('recall', 0):.0%})")
    print("\nDQN masked vs Random masked is the like-for-like comparison. The unmasked rows")
    print("are kept because every published toy figure before 2026-08-27 came from an")
    print("unmasked head set beside a masked baseline, which measured masking, not learning.\n")

    payload = {
        "config": {
            "train_steps": 0 if args.skip_train else args.train_steps,
            "eval_episodes": args.eval_episodes,
            "episode_steps": args.episode_steps,
            "seed": args.seed,
            "eval_epsilon": args.eval_epsilon,
            "reward_model": "NullRewardModel (deterministic triggers only)",
            "encoders": "HashEmbeddingEncoder stand-ins (no semantics)",
            "self_link_trigger_fix": True,
            # Which policy the primary DQN rows are. Recorded because every
            # `toy_dqn_comparison_v*.json` written before 2026-09-02 has them as the
            # best-by-training-return checkpoint, and that selector is retired.
            "primary_dqn_row": primary_label,
            "keep_best_checkpoint": bool(args.keep_best_checkpoint),
            "arms": arm_meta,
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
