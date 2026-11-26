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

from web_testing_agent.agents.ac_dqn import ACStateExtractor, ActionConditionedDQN  # noqa: E402
from web_testing_agent.agents.action_features import ActionFeatureExtractor  # noqa: E402
from web_testing_agent.agents.contextual_bandit import ContextualBandit  # noqa: E402
from web_testing_agent.agents.archive_start import ArchiveStartWrapper  # noqa: E402
from web_testing_agent.agents.diagnostics import (  # noqa: E402
    QGapProbe,
    TaggedPrioritizedNStepReplayBuffer,
)
from web_testing_agent.agents.features import FusionFeaturesExtractor  # noqa: E402
from web_testing_agent.agents.go_explore import Archive  # noqa: E402
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
    parser.add_argument(
        "--arms", nargs="+",
        default=["dqn_masked", "ac_dqn", "random_masked"],
        choices=["dqn_unmasked", "dqn_masked", "ac_dqn", "contextual_bandit",
                 "random_unmasked", "random_masked"],
        help="which policies to run. The default is the Phase 1-4 comparison: the new "
             "action-conditioned agent against the existing masked DQN and masked random.",
    )
    parser.add_argument("--p-return", type=float, default=0.5,
                        help="probability an ac_dqn training episode starts at an archived "
                             "cell rather than the landing page. Evaluation always starts "
                             "at the landing page, for every arm.")
    parser.add_argument("--n-step", type=int, default=3)
    parser.add_argument("--exploration-fraction", type=float, default=0.6)
    parser.add_argument("--final-eps", type=float, default=0.10)
    parser.add_argument("--out", type=Path, default=None)

    # -- diagnostics. Every one of these is off by default; with all of them off the code
    #    path is the pre-instrumentation one exactly (`callback=None`, the unmodified
    #    replay buffer, no saves). Switched on, they consume no randomness and mutate no
    #    training state — measured, not asserted, by test_diagnostics.py's
    #    `test_instrumented_training_is_bit_identical_to_uninstrumented_training`,
    #    which replays a fixed observation stream because the browser harness itself is
    #    intermittently non-deterministic and cannot serve as the control (see
    #    `scripts/check_probe_rng_neutrality.py --replicates`).
    diag = parser.add_argument_group(
        "diagnostics (ac_dqn arm only; all off by default and RNG-neutral when on)"
    )
    diag.add_argument(
        "--dueling", dest="dueling", action="store_true", default=True,
        help="action-conditioned head uses Q = V(s) + A(s,a) - mean_a' A(s,a'). The "
             "default, and what every ac_dqn figure in PROJECT_CONTEXT was measured on.",
    )
    diag.add_argument(
        "--no-dueling", dest="dueling", action="store_false",
        help="plain Q(s,a) head. Until now `ActionConditionedQNetwork(dueling=...)` was "
             "unreachable from this script, so diag_ac_3seed_deep_nodueling.json could "
             "not be regenerated from the repository.",
    )
    diag.add_argument(
        "--save-checkpoints", type=Path, default=None, metavar="DIR",
        help="write <arm>_seed<k>_{init,final}.zip. The initial network is saved too: "
             "the Q-gap at initialization is not recoverable once training has started.",
    )
    diag.add_argument(
        "--probe-q", type=str, default=None, metavar="URL_SUBSTRING",
        help="record Q(--probe-actions) at a FIXED observation captured the first time "
             "the agent reaches a page whose URL contains this, e.g. 'order-4.html'.",
    )
    diag.add_argument(
        "--probe-actions", nargs=2, default=["Place order", "Back"], metavar=("A", "B"),
        help="the two controls whose Q-values the probe compares, by visible label.",
    )
    diag.add_argument("--probe-every", type=int, default=250, metavar="STEPS")
    diag.add_argument(
        "--tag-transitions", type=str, default=None, metavar="URL_SUBSTRING",
        help="count how many stored n-step windows touch this page and how often PER "
             "draws them, e.g. 'receipt.html'. Bookkeeping only — the draws themselves "
             "come from the unmodified buffer.",
    )
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

    def __init__(self, model, encoders, *, epsilon: float, masked: bool, seed: int,
                 action_features=None) -> None:
        self.model = model
        self.encoders = encoders
        self.epsilon = epsilon
        self.masked = masked
        self._rng = np.random.default_rng(seed)
        # Present for the action-conditioned arm and None for the flat ones. The policy
        # must be evaluated on exactly the observation layout it was trained on; an arm
        # scored against a differently-shaped observation produces a confident,
        # meaningless number.
        self.action_features = action_features

    def reset(self) -> None:
        if self.action_features is not None:
            self.action_features.start_episode()

    def act(self, observation: dict, info: dict) -> int:
        blocks = None
        if self.action_features is not None:
            revealed = {tuple(entry) for entry in (info.get("newly_revealed") or [])}
            blocks = [self.action_features.encode(
                info.get("action_specs") or [], info.get("state_key", ""), revealed
            )]
        features = encode_modalities(
            self.encoders,
            [observation["screenshot"]],
            [info.get("page", {})],
            [info.get("episode_context")],
            [info.get("num_valid_actions")],
            blocks,
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


# Deterministically-detectable seeded bugs, per fixture, and a substring that identifies
# the finding that corresponds to each. Kept beside the FLOW_DEPTH map rather than
# derived, because `answer_key.json` records *which* bugs are deterministic but not what
# a `Finding` produced by one looks like.
#
# `deep` is deliberately empty and that is the whole point of splitting this column: its
# answer key declares exactly one bug, DEEP-01, as `llm_required` + `deep_flow`, so the
# deterministic ceiling on that fixture is **0**. Every `distinct_findings` count ever
# reported for it is therefore an unattributed firing, and until 2026-08-27 all of them
# were self-link `broken_navigation` false positives.
ANSWER_KEY_MARKERS: dict[str, dict[str, str]] = {
    "deep": {},
    "toy": {"BUG-03": "pricing", "BUG-04": "generate report", "BUG-07": "sync now"},
}


def build_bandit_agent(venv, seed: int, train_steps: int, args):
    """The myopic baseline, on byte-identical hyperparameters to `build_ac_agent`.

    Every argument below is copied from the AC-DQN arm deliberately, including the ones a
    bandit does not conceptually need (`gamma`, `target_update_interval`): leaving them at
    the same values keeps the two constructors diffable, and `ContextualBandit` ignores
    both — `gamma` never enters its target and it never updates a target network. The
    differences that matter are `dueling=False` (a plain scalar head instead of a value
    plus centred advantage) and `n_step=1`.
    """
    return ContextualBandit(
        "ACDQNPolicy",
        venv,
        learning_rate=1e-4,
        buffer_size=10_000,
        batch_size=32,
        gamma=0.99,
        exploration_fraction=args.exploration_fraction,
        exploration_initial_eps=1.0,
        exploration_final_eps=args.final_eps,
        target_update_interval=max(100, train_steps // 20),
        learning_starts=min(500, train_steps // 8),
        train_freq=4,
        gradient_steps=2,
        n_step=1,
        policy_kwargs={
            "features_extractor_class": ACStateExtractor,
            "features_extractor_kwargs": {"features_dim": FUSED_DIM + EPISODE_CONTEXT_DIM},
            "net_arch": [],
            # No value stream and no advantage centring: the head emits one scalar per
            # action, which is the predicted immediate reward.
            "dueling": False,
        },
        seed=seed,
        verbose=0,
    )


def build_ac_agent(venv, seed: int, train_steps: int, args):
    """Action-conditioned Double DQN with PER and n-step returns.

    Hyperparameters that differ from the flat baselines are the ones the diagnosis named,
    and each is a consequence of the 4,000-step budget rather than a tuning choice:

    * `exploration_fraction` 0.2 -> 0.6. At 0.2 epsilon reaches its floor at step 800,
      by which point only 75 gradient updates have happened, so 80% of the data is
      collected near-greedily from an essentially untrained network.
    * `gradient_steps` 1 -> 2 and `train_freq` 4. 875 updates on ~1M parameters is not
      enough to learn anything; this doubles it at no wall-clock cost worth measuring.
    * `buffer_size` 50,000 -> 10,000. Nothing is ever evicted at a 4,000-step budget, and
      the action-feature block makes each observation ~4x wider.

    `--no-dueling` and `--tag-transitions` reach the agent from here. With neither set,
    every argument below is what it was before the diagnostics existed.
    """
    extra: dict = {}
    if args.tag_transitions:
        # A subclass of the production buffer that records which stored windows touch a
        # page and how often they are drawn. It delegates every decision to the base
        # class; see `agents/diagnostics.py`.
        extra["replay_buffer_class"] = TaggedPrioritizedNStepReplayBuffer
        extra["replay_buffer_kwargs"] = {"tag_match": args.tag_transitions}
    return ActionConditionedDQN(
        "ACDQNPolicy",
        venv,
        learning_rate=1e-4,
        buffer_size=10_000,
        batch_size=32,
        gamma=0.99,
        exploration_fraction=args.exploration_fraction,
        exploration_initial_eps=1.0,
        exploration_final_eps=args.final_eps,
        target_update_interval=max(100, train_steps // 20),
        learning_starts=min(500, train_steps // 8),
        train_freq=4,
        gradient_steps=2,
        n_step=args.n_step,
        policy_kwargs={
            "features_extractor_class": ACStateExtractor,
            "features_extractor_kwargs": {"features_dim": FUSED_DIM + EPISODE_CONTEXT_DIM},
            "net_arch": [],
            "dueling": args.dueling,
        },
        seed=seed,
        verbose=0,
        **extra,
    )


def flow_metrics(report, episodes: list[list[str]]) -> dict:
    """Depth reached, how often the flow was completed, and by how many episodes."""
    depths = [FLOW_DEPTH.get(url.split("/")[-1].split("?")[0], 0) for url in report.visited_urls]
    per_episode_max = [
        max((FLOW_DEPTH.get(u.split("/")[-1].split("?")[0], 0) for u in urls), default=0)
        for urls in episodes
    ]
    return {
        "max_depth": max(depths) if depths else 0,
        "mean_depth": round(sum(depths) / len(depths), 2) if depths else 0.0,
        # Steps *spent* at each stage: a policy that reaches the receipt once and a
        # policy that lives there look very different here, and both are worth knowing.
        "reached_receipt": sum(1 for d in depths if d == 5),
        "reached_review": sum(1 for d in depths if d >= 4),
        # Episodes that completed the flow at least once. This is the headline number
        # the fixture's own answer key asks for ("report flow completions per N steps"),
        # and it cannot be inflated by dwelling on a page the way a step count can.
        "flow_completions": sum(1 for d in per_episode_max if d == 5),
        "episodes_reaching_review": sum(1 for d in per_episode_max if d >= 4),
        "episodes_leaving_stage_1": sum(1 for d in per_episode_max if d >= 2),
    }


def attribute_findings(report, site: str) -> dict:
    """Split distinct findings into answer-key-attributed and unattributed.

    A bare `distinct_findings` count says how many `(trigger, url, element)` triples a
    policy touched, not how many seeded bugs it found, and on a fixture whose
    deterministic ceiling is zero those are entirely different numbers. Reporting the
    total alone is what let 10-12 self-link false positives per run be published as a
    10x bug-discovery win for masked random.
    """
    markers = ANSWER_KEY_MARKERS.get(site, {})
    attributed: set[str] = set()
    matched_findings = 0
    for finding in report.findings:
        blob = f"{finding.url} {finding.element} {finding.detail}".lower()
        hit = {bug for bug, needle in markers.items() if needle in blob}
        if hit:
            attributed |= hit
            matched_findings += 1
    return {
        "seeded_bugs_found": sorted(attributed),
        "seeded_bugs_total": len(markers),
        "findings_attributed": matched_findings,
        "findings_unattributed": report.distinct_findings - matched_findings,
    }


def _save_checkpoint(model, directory: Path, label: str, seed: int, phase: str) -> Path:
    """`<label>_seed<k>_<phase>.zip`, and say where it went.

    Checkpointing lives here rather than in a callback because the two moments that
    matter are before the first gradient step and after the last one. README told a
    reader to regenerate `models/checkpoints/` with this script; until now the script
    saved nothing at all, so that instruction could not have worked.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{label}_seed{seed}_{phase}.zip"
    model.save(path)
    logger.info("saved {} checkpoint -> {}", phase, path)
    return path


def evaluate(base_url: str, label: str, policy_factory, args, seed: int) -> tuple:
    """Score one policy. Every arm goes through this same function and the same
    `run_rollout`, and every arm starts at `base_url` with no `setup_actions` — a
    policy evaluated from anywhere but the landing page is not comparable with one that
    is, however it was trained."""
    env = WebFunctionalEnv(base_url=base_url, max_steps=args.episode_steps, headless=True)
    assert not env.setup_actions, "evaluation must start from the landing page"
    visited: list[str] = []
    # URLs bucketed per episode, so "did this episode complete the flow" is answerable.
    # The flat list cannot answer it: 116 steps on the receipt in one episode and one
    # step in each of five are indistinguishable once concatenated.
    episodes: list[list[str]] = []
    try:
        policy = policy_factory(env)
        original_step, original_reset = env.step, env.reset

        def tracking_reset(**kwargs):
            episodes.append([])
            return original_reset(**kwargs)

        def tracking_step(action):
            result = original_step(action)
            url = result[4]["page"]["url"]
            visited.append(url)
            episodes[-1].append(url)
            return result

        env.reset = tracking_reset  # type: ignore[method-assign]
        env.step = tracking_step  # type: ignore[method-assign]
        report = run_rollout(env, policy, episodes=args.eval_episodes, label=label, seed=seed)
    finally:
        env.close()
    report.visited_urls = visited  # type: ignore[attr-defined]
    return report, flow_metrics(report, episodes)


# Metrics aggregated across seeds. Reported as median [min-max] rather than mean +- sd:
# at three seeds a standard deviation is not meaningful, and the range says exactly what
# the reader needs to know — whether the seeds agree.
_AGGREGATED = (
    ("reward", lambda r: r["mean_episode_reward"]),
    ("valid%", lambda r: r["valid_action_rate"] * 100),
    ("states", lambda r: r["unique_states"]),
    ("finds", lambda r: r["distinct_findings"]),
    # `finds` alone is not a bug-discovery figure. These say how much of it is.
    ("finds_ak", lambda r: r["attribution"]["findings_attributed"]),
    ("finds_un", lambda r: r["attribution"]["findings_unattributed"]),
    ("bugs", lambda r: len(r["attribution"]["seeded_bugs_found"])),
    ("maxD", lambda r: r["flow"]["max_depth"]),
    ("meanD", lambda r: r["flow"]["mean_depth"]),
    ("receipt", lambda r: r["flow"]["reached_receipt"]),
    ("done", lambda r: r["flow"]["flow_completions"]),
    ("pastS1", lambda r: r["flow"]["episodes_leaving_stage_1"]),
    # Discovery efficiency: the objective per unit of budget, not per run length.
    ("states/100", lambda r: 100.0 * r["unique_states"] / max(r["steps"], 1)),
    ("finds/100", lambda r: 100.0 * r["distinct_findings"] / max(r["steps"], 1)),
    ("steps/s", lambda r: r["steps_per_second"]),
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


def persist(out: Path, args, per_seed: dict, complete: bool) -> None:
    """Write the results file, whether or not every seed has finished.

    Called after **every arm**, not only at the end. A 3h34m run was lost on
    2026-08-28 because the script wrote its JSON once, after the last seed, and the
    process was killed part-way through seed 1: two completed seeds of a
    two-hour-per-seed arm went with it, and the only surviving evidence was a log.

    This changes nothing about the experiment. The same arms run in the same order with
    the same seeds and the same RNG draws; only the number of times the file is written
    differs. `complete` records whether the run finished, so a partial file can never be
    mistaken for a whole one — and `seeds_completed` names exactly which seeds are in it,
    because a partial file whose provenance is unclear is worse than no file.
    """
    summaries = {label: aggregate(runs) for label, runs in per_seed.items() if runs}
    completed = sorted({run["seed"] for runs in per_seed.values() for run in runs})
    # (seed, replicate) per arm. `seeds_completed` alone cannot describe a run-to-run
    # variance study, where the same seed is deliberately run more than once.
    runs_completed = {
        label: [[run["seed"], run.get("replicate")] for run in runs]
        for label, runs in per_seed.items()
    }
    payload = {
        "site": args.site,
        "encoders": args.encoders,
        "complete": complete,
        "seeds_requested": list(args.seeds),
        "seeds_completed": completed,
        "seed_sequence": list(args.seeds),
        "runs_completed": runs_completed,
        "repeated_seeds": len(args.seeds) != len(set(args.seeds)),
        "arms_completed": {label: len(runs) for label, runs in per_seed.items()},
        "args": vars(args) | {"out": str(out)},
        "summary": summaries,
        "results_per_seed": per_seed,
    }
    if not complete:
        payload["_status"] = (
            "PARTIAL — this run had not finished when the file was written. Rows are "
            "real measurements, but arms and seeds are missing, so the medians are over "
            "fewer seeds than `seeds_requested` and must not be quoted as a 3-seed result."
        )
    # Written via a temporary file and replaced atomically: a process killed mid-write
    # would otherwise leave truncated JSON where the previous good results had been.
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(out)


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
    # Resolved before the loop so every arm can checkpoint into it.
    out = args.out or REPORTS / f"compare_agents_{args.site}_{args.encoders}.json"
    with serve_directory(site) as origin:
        base_url = f"{origin}/index.html"
        logger.info("{} fixture at {}", args.site, base_url)

        # `--seeds` may legitimately repeat a value: the sampling unit of a
        # run-to-run variance study is the **run**, not the distinct seed, and this
        # harness is now known to diverge between two runs at one seed. `replicate` is
        # what keeps those runs distinguishable in the report — without it three
        # records all say `"seed": 0` and `seeds_completed` dedupes them to `[0]`.
        for replicate, seed in enumerate(args.seeds):
            logger.info("############ seed {} (replicate {}) ############", seed, replicate)

            # -- flat-head DQN arms (unchanged; the baselines the new agent is measured
            #    against, built by the same `build_agent` that produced every published
            #    figure) ------------------------------------------------------------
            for masked in (False, True):
                name = "dqn_masked" if masked else "dqn_unmasked"
                if name not in args.arms:
                    continue
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
                    {**report.to_dict(), "flow": flow,
                     "attribution": attribute_findings(report, args.site),
                     "seed": seed, "replicate": replicate,
                     "train_seconds": round(train_s, 1)}
                )
                logger.info("{} seed {}: {}", name, seed, flow)
                persist(out, args, per_seed, complete=False)

            # -- the action-conditioned agent ---------------------------------------
            if "ac_dqn" in args.arms:
                logger.info("=== training ac_dqn (seed {}) for {} steps ===", seed, args.train_steps)
                archive = Archive()
                # One extractor, shared by training and evaluation. Its visit counts are
                # part of the observation, so a fresh extractor at evaluation would show
                # the policy a different feature than the one it was trained on.
                action_features = ActionFeatureExtractor()
                wrappers: dict = {}

                def _make_env(a=archive, s=seed, holder=wrappers):
                    inner = WebFunctionalEnv(
                        base_url=base_url, max_steps=args.episode_steps, headless=True
                    )
                    # Archive wrapper innermost: it must choose the start state *before*
                    # the env replays it, and Monitor must see the episode the agent
                    # actually experienced.
                    wrapped = ArchiveStartWrapper(inner, a, p_return=args.p_return, seed=s)
                    holder["archive_wrapper"] = wrapped
                    return Monitor(wrapped)

                venv = DummyVecEnv([_make_env])
                venv = SemanticPerceptionWrapper(
                    venv, encoders=encoders, action_features=action_features
                )
                started = time.monotonic()
                model = build_ac_agent(venv, seed, args.train_steps, args)
                # Saved before `learn`, because the initial weights are the one thing
                # that cannot be recovered afterwards — and the Q-gap they produce at
                # `order-4` is what the deadlock diagnosis turns on.
                if args.save_checkpoints:
                    _save_checkpoint(model, args.save_checkpoints, "ac_dqn", seed, "init")
                probe = (
                    QGapProbe(args.probe_q, *args.probe_actions, probe_every=args.probe_every)
                    if args.probe_q else None
                )
                try:
                    # `callback=None` is what SB3 receives when no probe is configured,
                    # which is exactly the pre-instrumentation call.
                    model.learn(total_timesteps=args.train_steps, progress_bar=False,
                                callback=probe)
                finally:
                    venv.close()
                train_s = time.monotonic() - started
                if args.save_checkpoints:
                    _save_checkpoint(model, args.save_checkpoints, "ac_dqn", seed, "final")
                diagnostics = {}
                if probe is not None:
                    diagnostics["q_probe"] = probe.to_dict()
                if isinstance(model.replay_buffer, TaggedPrioritizedNStepReplayBuffer):
                    diagnostics["tagged_transitions"] = {
                        "tag_match": args.tag_transitions,
                        **model.replay_buffer.stats(),
                    }

                # Evaluation starts at the landing page with no archive and no route
                # replay, exactly like every other arm. Handing this agent its archive at
                # evaluation would measure the archive, not the policy it trained.
                report, flow = evaluate(
                    base_url, "ac_dqn",
                    lambda env, m=model, s=seed, af=action_features: TrainedPolicy(
                        m, encoders, epsilon=args.eval_epsilon, masked=True, seed=s,
                        action_features=af,
                    ),
                    args, seed,
                )
                stats = wrappers["archive_wrapper"].stats.to_dict()
                per_seed.setdefault("ac_dqn", []).append(
                    {**report.to_dict(), "flow": flow,
                     "attribution": attribute_findings(report, args.site),
                     "seed": seed, "replicate": replicate,
                     "train_seconds": round(train_s, 1),
                     # Route replay is outside the step budget and inside the cost. A
                     # comparison matched on env steps alone hands this arm free browser
                     # actions, so the count is reported rather than absorbed.
                     "archive": {**stats, "cells": len(archive.cells)},
                     "training_browser_actions": args.train_steps + stats["replayed_actions"],
                     **({"diagnostics": diagnostics} if diagnostics else {})}
                )
                logger.info("ac_dqn seed {}: {} archive={}", seed, flow, stats)
                persist(out, args, per_seed, complete=False)

            # -- the myopic baseline -------------------------------------------------
            # Deliberately a copy of the ac_dqn block rather than a refactor of it. The
            # two arms must differ only in the learning rule, and the safest way to
            # guarantee that is to leave the arm this experiment is measured against
            # completely untouched.
            if "contextual_bandit" in args.arms:
                logger.info("=== training contextual_bandit (seed {}) for {} steps ===",
                            seed, args.train_steps)
                archive = Archive()
                action_features = ActionFeatureExtractor()
                wrappers = {}

                def _make_bandit_env(a=archive, s=seed, holder=wrappers):
                    inner = WebFunctionalEnv(
                        base_url=base_url, max_steps=args.episode_steps, headless=True
                    )
                    wrapped = ArchiveStartWrapper(inner, a, p_return=args.p_return, seed=s)
                    holder["archive_wrapper"] = wrapped
                    return Monitor(wrapped)

                venv = DummyVecEnv([_make_bandit_env])
                venv = SemanticPerceptionWrapper(
                    venv, encoders=encoders, action_features=action_features
                )
                started = time.monotonic()
                model = build_bandit_agent(venv, seed, args.train_steps, args)
                if args.save_checkpoints:
                    _save_checkpoint(model, args.save_checkpoints,
                                     "contextual_bandit", seed, "init")
                try:
                    model.learn(total_timesteps=args.train_steps, progress_bar=False)
                finally:
                    venv.close()
                train_s = time.monotonic() - started
                if args.save_checkpoints:
                    _save_checkpoint(model, args.save_checkpoints,
                                     "contextual_bandit", seed, "final")

                # Same evaluation call as every other arm: landing page, no archive, no
                # route replay, `--eval-epsilon` residual exploration.
                report, flow = evaluate(
                    base_url, "contextual_bandit",
                    lambda env, m=model, s=seed, af=action_features: TrainedPolicy(
                        m, encoders, epsilon=args.eval_epsilon, masked=True, seed=s,
                        action_features=af,
                    ),
                    args, seed,
                )
                stats = wrappers["archive_wrapper"].stats.to_dict()
                per_seed.setdefault("contextual_bandit", []).append(
                    {**report.to_dict(), "flow": flow,
                     "attribution": attribute_findings(report, args.site),
                     "seed": seed, "replicate": replicate,
                     "train_seconds": round(train_s, 1),
                     "archive": {**stats, "cells": len(archive.cells)},
                     "training_browser_actions": args.train_steps + stats["replayed_actions"],
                     "algorithm": model.algorithm,
                     "bootstrapping": False,
                     "n_step_effective": model.n_step}
                )
                logger.info("contextual_bandit seed {}: {} archive={}", seed, flow, stats)
                persist(out, args, per_seed, complete=False)

            # -- random baselines ----------------------------------------------------
            for label, valid_only in (("random_unmasked", False), ("random_masked", True)):
                if label not in args.arms:
                    continue
                report, flow = evaluate(
                    base_url, label,
                    lambda env, v=valid_only, s=seed: RandomPolicy(MAX_ACTIONS, seed=s, valid_only=v),
                    args, seed,
                )
                per_seed.setdefault(label, []).append(
                    {**report.to_dict(), "flow": flow,
                     "attribution": attribute_findings(report, args.site),
                     "seed": seed, "replicate": replicate}
                )
                logger.info("{} seed {}: {}", label, seed, flow)
                persist(out, args, per_seed, complete=False)

    summaries = {label: aggregate(runs) for label, runs in per_seed.items()}

    print("\n" + "=" * 96)
    print(f"{args.site} fixture — {len(args.seeds)} seeds {args.seeds} x {args.eval_episodes} episodes "
          f"x {args.episode_steps} steps, eval-epsilon={args.eval_epsilon}, encoders={args.encoders}")
    print("=" * 96)
    header = (f"{'policy':<18}{'reward':>19}{'valid%':>12}{'states':>12}"
              f"{'finds (ak/un)':>24}{'meanD':>13}{'maxD':>8}{'done':>7}")
    print(header)
    print("-" * len(header))
    for name, stats in summaries.items():
        finds = (f"{_fmt(stats['finds'], 1, 0).strip()} "
                 f"({_fmt(stats['finds_ak'], 1, 0).strip()}/"
                 f"{_fmt(stats['finds_un'], 1, 0).strip()})")
        print(
            f"{name:<18}{_fmt(stats['reward'], 19)}{_fmt(stats['valid%'], 12, 0)}"
            f"{_fmt(stats['states'], 12, 0)}{finds:>24}{_fmt(stats['meanD'], 13)}"
            f"{_fmt(stats['maxD'], 8, 0)}{_fmt(stats['done'], 7, 0)}"
        )
    print("\nEach cell is median [min-max] across seeds; a bare number means every seed agreed.")
    print("meanD/maxD  = stage of the 5-stage order flow (0 = landing page, 5 = receipt).")
    print("done        = evaluation episodes that reached receipt.html at least once —")
    print("              the completion metric the fixture's own answer key asks for.")
    print("finds(ak/un)= distinct findings, split answer-key-attributed / unattributed.")
    if not ANSWER_KEY_MARKERS.get(args.site):
        print(f"\nNOTE: the '{args.site}' fixture declares NO deterministically-detectable seeded")
        print("      bugs, so its deterministic ceiling is 0 and every finding above is")
        print("      unattributed. Read `finds` as exploration diversity, never as bug")
        print("      discovery. Before 2026-08-27 all of them were self-link false positives.")
    if len(args.seeds) < 3:
        print(f"\nWARNING: {len(args.seeds)} seed(s). A DQN at this step budget varies enough "
              f"across seeds that no difference here supports a conclusion. Use --seeds 0 1 2.")

    persist(out, args, per_seed, complete=True)
    logger.info("Wrote {}", out)


if __name__ == "__main__":
    main()
