"""The search ladder: hand priority -> learned immediate value -> bootstrapped value.

**The question.** Four consecutive null results (archive ablation, Markov-context fix,
action-representation probe, trained-Q diagnostic) and one near-tie (AC-DQN 0.580 vs
contextual bandit 0.540 at 25 episodes) leave one rung of the ladder unmeasured: what does
a *non-learning* policy with a reasonable hand-designed action priority do on this
benchmark at the same budget? Without that rung, "learning helps" has no floor to be
measured against.

    A  hand_priority_search   score(a) = w . phi(a), w FIXED and hand-designed
    B  contextual_bandit      score(a) = f_theta(s, phi(a)), theta regressed onto r_t
    C  ac_dqn                 score(a) = Q_theta(s, phi(a)), theta regressed onto
                                         r_t + gamma^n max_a' Q_target(s_{t+n}, a')

* **A vs B** — does learning an immediate action value beat a hand priority?
* **B vs C** — does *bootstrapping* beat myopic immediate-reward prediction?

**What B vs C is not.** It does not isolate PER, dueling, Double DQN, n-step replay, the
archive or the target network. Those are shared between B and C precisely so they cancel.
The only statement it supports is about myopic immediate-reward prediction versus
bootstrapped sequential value learning under otherwise matched infrastructure.

**The search budget, and why it is fair.** The repository already measures search in *env
steps*: `run_go_explore --budget` is env steps, `GoExploreStats.env_steps` counts them, and
route replay is deliberately outside the step budget but reported as `replayed_actions`
(`agents/archive_start.py`). The training arms use the same unit — `--train-steps 4000` —
and report `training_browser_actions = train_steps + replayed_actions`. So there is no
ambiguity about one unit of work, and Arm A is given **4,000 env steps in 100 episodes of
40 steps through the same `ArchiveStartWrapper` at the same `p_return=0.5`**, under the
same epsilon schedule (1.0 -> 0.10 over 60% of the budget). Both numbers are reported for
every arm.

**Arms B and C are not retrained.** Their finalized checkpoints from the canonical
protocol are loaded read-only and re-evaluated here contemporaneously with Arm A, so all
three rows come from one process, one fixture server and one harness state. SHA-256 is
taken before and after and reported. Retraining would have changed the very checkpoints
every published figure in this repository refers to.

**Arm A is evaluated two ways, and both are reported.** A search's defining property is
that it remembers what it has already tried, but a trained checkpoint is evaluated as a
stationary policy over 25 independent episodes. Rather than pick one and call it fair:

* ``hand_priority``        — counters cleared every episode. 25 i.i.d. draws from one
                             stationary policy, exactly the semantics B and C are scored
                             under. **This is the primary comparator.**
* ``hand_priority_search`` — run-level counters carried across the 25 episodes. One search
                             split into 25 restarts, which is what the algorithm actually
                             is. Reported alongside, never instead.

    python scripts/search_vs_bandit_vs_ac_dqn.py
    python scripts/search_vs_bandit_vs_ac_dqn.py --seeds 0 --smoke   # 1 seed, short
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from compare_agents import FLOW_DEPTH, TrainedPolicy, flow_metrics  # noqa: E402
from offline_eval_25ep import bootstrap_ci, sha256, wilson  # noqa: E402

from web_testing_agent.agents.ac_dqn import ActionConditionedDQN  # noqa: E402
from web_testing_agent.agents.action_features import ActionFeatureExtractor  # noqa: E402
from web_testing_agent.agents.archive_start import ArchiveStartWrapper  # noqa: E402
from web_testing_agent.agents.contextual_bandit import ContextualBandit  # noqa: E402
from web_testing_agent.agents.go_explore import Archive  # noqa: E402
from web_testing_agent.agents.hand_priority import (  # noqa: E402
    HAND_PRIORITY_TYPE_WEIGHTS,
    HAND_PRIORITY_WEIGHTS,
    HandPriorityPolicy,
)
from web_testing_agent.envs import WebFunctionalEnv  # noqa: E402
from web_testing_agent.evaluation import run_rollout  # noqa: E402
from web_testing_agent.perception.vec_wrapper import default_encoders  # noqa: E402
from web_testing_agent.utils.local_server import serve_directory  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

SITE = REPO_ROOT / "tests" / "fixtures" / "deep_flow_site"

# -- the canonical protocol, read off the runs that produced the checkpoints ------------
# (reports/postmarkov_baseline_pr050.json and reports/contextual_bandit_pr050.json)
EPISODES = 25            # canonical since reports/offline_eval_25ep.*; never 5 again
EVAL_EPSILON = 0.05
EPISODE_STEPS = 40
SEARCH_STEPS = 4000      # == --train-steps for the learned arms
P_RETURN = 0.5
EXPLORATION_FRACTION = 0.6
EPS_INITIAL, EPS_FINAL = 1.0, 0.10
BOOTSTRAP = 20_000
BOOTSTRAP_SEED = 0

#: Loaded read-only. These are the finalized checkpoints every published figure refers to.
TRAINED_ARMS = {
    "ac_dqn": (REPO_ROOT / "models/checkpoints/postmarkov_baseline", ActionConditionedDQN),
    "contextual_bandit": (REPO_ROOT / "models/checkpoints/contextual_bandit", ContextualBandit),
}

#: Which rung of the ladder each reported arm is, for the report's own bookkeeping.
ARM_IDENTITY = {
    "hand_priority": {
        "rung": "A", "learns": False, "bootstraps": False,
        "target": "none - fixed hand-designed weights",
        "note": "counters cleared every episode (stationary-policy reading)",
    },
    "hand_priority_search": {
        "rung": "A", "learns": False, "bootstraps": False,
        "target": "none - fixed hand-designed weights",
        "note": "run-level counters carried across the 25 episodes (search reading)",
    },
    "contextual_bandit": {
        "rung": "B", "learns": True, "bootstraps": False,
        "target": "r_t (ContextualBandit.regression_target)",
        "note": "no target network, no next state, no gamma",
    },
    "ac_dqn": {
        "rung": "C", "learns": True, "bootstraps": True,
        "target": "r_t + gamma^n * max_a' Q_target(s_{t+n}, a')",
        "note": "Double DQN, dueling head, PER, 3-step returns",
    },
}

#: The order rows appear in every table. A, A-search, B, C — the ladder, in order.
ARM_ORDER = ["hand_priority", "hand_priority_search", "contextual_bandit", "ac_dqn"]


def depth_of(url: str) -> int:
    return FLOW_DEPTH.get(url.split("/")[-1].split("?")[0], 0)


class ScheduledEpsilon:
    """Applies SB3's linear epsilon schedule to a policy that has no `learn()` loop.

    Reproduces `get_linear_fn(initial, final, exploration_fraction)` exactly — epsilon
    falls linearly from `initial` to `final` over the first `fraction` of the budget and
    stays at `final` afterwards — so Arm A's exploration budget during its search phase is
    the same one Arms B and C had during training. Anything else would compare a search
    that explored more (or less) than the learners did.
    """

    def __init__(self, policy, total_steps: int, *, initial: float, final: float,
                 fraction: float) -> None:
        self.policy = policy
        self.total_steps = max(1, total_steps)
        self.initial, self.final, self.fraction = initial, final, max(fraction, 1e-9)
        self.steps = 0
        self.epsilon_trace: list[float] = []

    def reset(self) -> None:
        self.policy.reset()

    def act(self, observation: dict, info: dict) -> int:
        progress = min(self.steps / (self.fraction * self.total_steps), 1.0)
        self.policy.epsilon = self.initial + progress * (self.final - self.initial)
        if self.steps % 200 == 0:
            self.epsilon_trace.append(round(self.policy.epsilon, 4))
        self.steps += 1
        return self.policy.act(observation, info)


def _tracked(env):
    """Wrap `env.step`/`env.reset` so per-episode URL and state-key lists are kept.

    Lifted verbatim in behaviour from `compare_agents.evaluate` and
    `offline_eval_25ep.evaluate_checkpoint`, so `flow_metrics` here means exactly what it
    means in every other report in this repository.
    """
    episodes_urls: list[list[str]] = []
    episodes_keys: list[list[str]] = []
    visited: list[str] = []
    original_step, original_reset = env.step, env.reset

    def tracking_reset(**kwargs):
        episodes_urls.append([])
        episodes_keys.append([])
        return original_reset(**kwargs)

    def tracking_step(action):
        result = original_step(action)
        info = result[4]
        url = info["page"]["url"]
        visited.append(url)
        episodes_urls[-1].append(url)
        episodes_keys[-1].append(info.get("state_key", ""))
        return result

    env.reset = tracking_reset      # type: ignore[method-assign]
    env.step = tracking_step        # type: ignore[method-assign]
    return episodes_urls, episodes_keys, visited


def summarise_episodes(arm: str, seed: int, report, episodes_urls, episodes_keys) -> dict:
    """The per-checkpoint row, in exactly the schema `offline_eval_25ep` established."""
    per_episode = []
    for index, (urls, keys) in enumerate(zip(episodes_urls, episodes_keys, strict=True)):
        depth = max((depth_of(u) for u in urls), default=0)
        per_episode.append({
            "arm": arm, "seed": seed, "episode": index, "eval_seed": seed + index,
            "completed": depth == 5,
            "reached_review": depth >= 4,
            "reached_s1": depth >= 2,
            "reward": round(float(report.episode_rewards[index]), 4),
            "max_depth": depth,
            "mean_depth": round(st.mean(depth_of(u) for u in urls), 4) if urls else 0.0,
            "steps": len(urls),
            "unique_states": len(set(keys)),
        })
    rewards = [e["reward"] for e in per_episode]
    completions = sum(e["completed"] for e in per_episode)
    n = len(per_episode)
    return {
        "arm": arm, "seed": seed, "n_episodes": n,
        "completion_count": completions,
        "completion_rate": round(completions / n, 4),
        "review_count": sum(e["reached_review"] for e in per_episode),
        "review_rate": round(sum(e["reached_review"] for e in per_episode) / n, 4),
        "s1_count": sum(e["reached_s1"] for e in per_episode),
        "s1_rate": round(sum(e["reached_s1"] for e in per_episode) / n, 4),
        "mean_reward": round(st.mean(rewards), 4),
        "median_reward": round(st.median(rewards), 4),
        "std_reward": round(st.stdev(rewards), 4) if len(rewards) > 1 else 0.0,
        "min_reward": round(min(rewards), 4),
        "max_reward": round(max(rewards), 4),
        "mean_depth": round(st.mean(e["mean_depth"] for e in per_episode), 4),
        "max_depth": max(e["max_depth"] for e in per_episode),
        "unique_states_mean": round(st.mean(e["unique_states"] for e in per_episode), 3),
        "unique_states_run_total": report.unique_states,
        "valid_actions": int(round(report.valid_action_rate * report.steps)),
        "total_actions": report.steps,
        "valid_action_rate": round(report.valid_action_rate, 4),
        "invalid_actions": report.steps - int(round(report.valid_action_rate * report.steps)),
        "noop_steps": report.noop_steps,
        "wall_clock_s": round(report.wall_clock_s, 1),
        "flow_metrics": flow_metrics(report, episodes_urls),
        "episodes": per_episode,
    }


# -- Arm A's search phase --------------------------------------------------------------


def run_search_phase(base_url: str, seed: int, steps: int, episode_steps: int) -> dict:
    """`steps` env steps of hand-priority search through the same archive the learners used.

    This is the budget-matched half of Arm A. It produces no artefact that evaluation can
    use — a non-learning policy has nothing to carry forward, and the archive is disabled
    at evaluation for every arm — so what it reports is *search coverage at a matched
    budget*, which is the claim Go-Explore actually makes and the only thing a search
    budget can buy here. Recording that it buys nothing transferable is the point.
    """
    episodes = max(1, steps // episode_steps)
    archive = Archive()
    inner = WebFunctionalEnv(base_url=base_url, max_steps=episode_steps, headless=True)
    env = ArchiveStartWrapper(inner, archive, p_return=P_RETURN, seed=seed)
    policy = HandPriorityPolicy(epsilon=EPS_INITIAL, seed=seed,
                                carry_counts_across_episodes=True)
    scheduled = ScheduledEpsilon(policy, steps, initial=EPS_INITIAL, final=EPS_FINAL,
                                 fraction=EXPLORATION_FRACTION)
    episodes_urls, _keys, visited = _tracked(env)
    started = time.monotonic()
    try:
        report = run_rollout(env, scheduled, episodes=episodes,
                             label=f"search_s{seed}", seed=seed)
    finally:
        env.close()
    elapsed = time.monotonic() - started
    report.visited_urls = visited  # type: ignore[attr-defined]

    stats = env.stats.to_dict()
    depths = [depth_of(u) for u in visited]
    return {
        "seed": seed,
        "env_steps": report.steps,
        "episodes": episodes,
        "episode_steps": episode_steps,
        "replayed_actions": stats["replayed_actions"],
        "browser_actions_total": report.steps + stats["replayed_actions"],
        "archive": {**stats, "cells": len(archive.cells)},
        # Read off the inner env, not off `report`. `run_rollout` finds the tracker with
        # `getattr(env, "exploration", None)`, and gymnasium 1.3's `Wrapper` no longer
        # forwards unknown attributes — so through `ArchiveStartWrapper` that lookup
        # silently returns None and the run reports 0 states discovered. Every other
        # caller of `run_rollout` in this repository passes a bare `WebFunctionalEnv`,
        # which is why the gap has not bitten before.
        "unique_states": inner.exploration.total_states_seen,
        "max_depth": max(depths) if depths else 0,
        "mean_depth": round(st.mean(depths), 4) if depths else 0.0,
        "flow": flow_metrics(report, episodes_urls),
        "mean_episode_reward": round(report.mean_episode_reward, 4),
        "valid_action_rate": round(report.valid_action_rate, 4),
        "distinct_findings": report.distinct_findings,
        "wall_clock_s": round(elapsed, 1),
        "steps_per_second": round(report.steps_per_second, 3),
        "policy": policy.to_dict(),
        "epsilon_trace_every_200_steps": scheduled.epsilon_trace,
    }


# -- evaluation, identical for every arm -----------------------------------------------


def evaluate(base_url: str, arm: str, seed: int, policy_factory, episodes: int) -> dict:
    """`episodes` episodes from the landing page, archive disabled, epsilon 0.05.

    One function for all four rows. A second evaluation path per arm is exactly how the
    2026-08-10 report came to put an n=1 row beside an n=5 row in the same table.
    """
    env = WebFunctionalEnv(base_url=base_url, max_steps=EPISODE_STEPS, headless=True)
    assert not env.setup_actions, "evaluation must start from the landing page"
    assert not isinstance(env, ArchiveStartWrapper), "evaluation must not use the archive"
    episodes_urls, episodes_keys, visited = _tracked(env)
    try:
        policy = policy_factory()
        report = run_rollout(env, policy, episodes=episodes, label=f"{arm}_s{seed}", seed=seed)
    finally:
        env.close()
    report.visited_urls = visited  # type: ignore[attr-defined]
    row = summarise_episodes(arm, seed, report, episodes_urls, episodes_keys)
    if isinstance(policy, HandPriorityPolicy):
        row["policy"] = policy.to_dict()
    return row


# -- statistics ------------------------------------------------------------------------


def summarise_arm(rows: list[dict], rng, episodes: int) -> dict:
    counts = [r["completion_count"] for r in rows]
    rates = [r["completion_rate"] for r in rows]
    return {
        "n_checkpoints": len(rows),
        "completion_counts_per_seed": counts,
        "completion_rates_per_seed": rates,
        "mean_checkpoint_completion_rate": round(st.mean(rates), 4),
        "median_checkpoint_completion_rate": round(st.median(rates), 4),
        "sd_across_checkpoints": round(st.stdev(rates), 4) if len(rates) > 1 else 0.0,
        "min_checkpoint_rate": min(rates),
        "max_checkpoint_rate": max(rates),
        "bootstrap_95ci_mean_rate_over_checkpoints": (
            bootstrap_ci(rates, rng) if len(rates) > 1 else [rates[0], rates[0]]
        ),
        "checkpoints_with_at_least_one_completion": sum(1 for c in counts if c > 0),
        "checkpoints_with_all_episodes": sum(1 for c in counts if c == episodes),
        "checkpoints_with_zero": sum(1 for c in counts if c == 0),
        "review_rates_per_seed": [r["review_rate"] for r in rows],
        "s1_rates_per_seed": [r["s1_rate"] for r in rows],
        "mean_review_rate": round(st.mean(r["review_rate"] for r in rows), 4),
        "mean_s1_rate": round(st.mean(r["s1_rate"] for r in rows), 4),
        # **Post-hoc, and labelled as such.** How often an episode that got as far as the
        # review page went on to complete. It separates "could not find the flow" from
        # "found the flow and then failed one decision", which the completion rate alone
        # cannot. Added after the run, computed from the recorded episodes, nothing rerun.
        "completion_given_review_per_seed": [
            round(r["completion_count"] / r["review_count"], 4) if r["review_count"] else None
            for r in rows
        ],
        "mean_completion_given_review": round(st.mean(
            r["completion_count"] / r["review_count"] if r["review_count"] else 0.0
            for r in rows), 4),
        "pooled_completion_given_review": round(
            sum(r["completion_count"] for r in rows)
            / max(sum(r["review_count"] for r in rows), 1), 4),
        "mean_reward_over_checkpoints": round(st.mean(r["mean_reward"] for r in rows), 4),
        "mean_depth_over_checkpoints": round(st.mean(r["mean_depth"] for r in rows), 4),
        "max_depth_observed": max(r["max_depth"] for r in rows),
        "mean_unique_states": round(st.mean(r["unique_states_mean"] for r in rows), 3),
        "mean_valid_action_rate": round(st.mean(r["valid_action_rate"] for r in rows), 4),
        "total_invalid_actions": sum(r["invalid_actions"] for r in rows),
        "_descriptive_total": {
            "completions": sum(counts),
            "episodes": sum(r["n_episodes"] for r in rows),
            "_warning": "DESCRIPTIVE ONLY. These episodes are not independent training "
                        "runs; the inferential unit is the checkpoint.",
        },
    }


def compare(left: str, right: str, rows_by_arm: dict, rng) -> dict:
    """One pre-specified comparison per pair: difference in mean checkpoint completion rate.

    **Unpaired, and deliberately so.** Only Arms B and C have genuinely matched training
    seeds; Arm A's eight rows are eight epsilon streams of one fixed policy and its "seed
    3" has nothing to do with AC-DQN's seed 3. Pairing them would manufacture a
    correlation that does not exist, so both arms are resampled independently and the
    paired view is reported for B-vs-C only, where it is meaningful.

    One statistic per pair, fixed before the data were seen. No test is selected after the
    fact, and none of this is called significant.
    """
    a = [r["completion_rate"] for r in rows_by_arm[left]]
    b = [r["completion_rate"] for r in rows_by_arm[right]]
    observed = st.mean(b) - st.mean(a)
    draws = sorted(
        float(np.mean(rng.choice(b, len(b), replace=True))
              - np.mean(rng.choice(a, len(a), replace=True)))
        for _ in range(BOOTSTRAP)
    )
    ci = [round(draws[int(0.025 * BOOTSTRAP)], 4), round(draws[int(0.975 * BOOTSTRAP) - 1], 4)]
    result = {
        "left": left, "right": right,
        "left_rates": a, "right_rates": b,
        "left_mean": round(st.mean(a), 4), "right_mean": round(st.mean(b), 4),
        "difference_right_minus_left": round(observed, 4),
        "bootstrap_95ci_of_difference": ci,
        "ci_excludes_zero": bool(ci[0] > 0 or ci[1] < 0),
        "paired_by_seed": None,
        "_unit": "the checkpoint / search replicate (n=8 per arm), never the episode",
    }
    if {left, right} == {"contextual_bandit", "ac_dqn"}:
        differences = [y - x for x, y in zip(a, b, strict=True)]
        result["paired_by_seed"] = {
            "_valid_because": "both arms trained one run per seed under the same protocol",
            "per_seed_difference": [round(d, 4) for d in differences],
            "mean_difference": round(st.mean(differences), 4),
            "seeds_favouring_right": sum(1 for d in differences if d > 0),
            "seeds_favouring_left": sum(1 for d in differences if d < 0),
            "seeds_tied": sum(1 for d in differences if d == 0),
        }
    return result


# -- reporting -------------------------------------------------------------------------


def render_markdown(payload: dict) -> str:
    lines: list[str] = []
    add = lines.append
    protocol, search = payload["evaluation_protocol"], payload["search_budget"]
    arms = payload["arm_summaries"]

    add("# Search + hand priority vs search + learned priority vs AC-DQN\n")

    add("## 1. Research question\n")
    add("Three arms share one fixture, one observation, one action space and mask, one "
        "reward, one archive and one evaluation protocol, and differ only in how an action "
        "is scored:\n")
    add("| rung | arm | scoring rule | learns | bootstraps |")
    add("|---|---|---|---|---|")
    for arm in ARM_ORDER:
        identity = ARM_IDENTITY[arm]
        add(f"| {identity['rung']} | `{arm}` | {identity['target']} | "
            f"{identity['learns']} | {identity['bootstraps']} |")
    add("\n* **A vs B** — does learning an immediate action value beat a hand priority?")
    add("* **B vs C** — does bootstrapping beat myopic immediate-reward prediction?")
    add("* **A vs C** — the overall practical comparison, read only in light of the two above.\n")
    add("**B vs C does not isolate PER, dueling, Double DQN, n-step replay, the archive or "
        "the target network.** Those are shared between the two arms so that they cancel. "
        "The only claim it supports is myopic immediate-reward prediction versus "
        "bootstrapped sequential value learning under otherwise matched infrastructure.\n")

    add("## 2. Repository and configuration context\n")
    for key, value in payload["context"].items():
        add(f"- **{key}**: {value}")
    add("")

    add("## 3. Exact algorithms\n")
    for arm in ARM_ORDER:
        identity = ARM_IDENTITY[arm]
        add(f"- **`{arm}`** (rung {identity['rung']}) — target: `{identity['target']}`. "
            f"{identity['note']}.")
    add("")

    add("## 4. Search budget\n")
    for key, value in search.items():
        add(f"- **{key}**: {value}")
    add("")
    if payload["search_phase"]:
        add("| seed | env steps | replayed | browser actions | archive cells | returns | "
            "failed | reanchored | states | max depth | completions/100 ep | runtime (s) |")
        add("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for row in payload["search_phase"]:
            arch = row["archive"]
            add(f"| {row['seed']} | {row['env_steps']} | {row['replayed_actions']} | "
                f"{row['browser_actions_total']} | {arch['cells']} | "
                f"{arch['returns_attempted']} | {arch['returns_failed']} | "
                f"{arch['reanchored']} | {row['unique_states']} | {row['max_depth']} | "
                f"{row['flow']['flow_completions']} | {row['wall_clock_s']} |")
        add("")
        add("Arms B and C were **not** retrained, so their training phases could not be "
            "re-instrumented for the same table. Their archive statistics from the runs "
            "that produced these checkpoints are carried in the JSON under "
            "`context.trained_arm_training_stats`.\n")

    add("## 5. Hand-priority definition\n")
    add("`score(a) = w . phi(a)`, where `phi` is the **same 52-dim per-action vector** "
        "`ActionFeatureExtractor` hands the two networks: "
        "`[action-type one-hot (10) | hashed label (32) | scalars (10)]`.\n")
    add(f"**All 32 label dimensions are weighted exactly zero** (measured L1 norm of the "
        f"label-block weights: `{payload['hand_priority']['label_block_weight_l1']}`), so "
        f"the policy is provably invariant to renaming every control on the site. It cannot "
        f"read a label, a URL or a page identity.\n")
    add("| scalar feature | weight | what it says |")
    add("|---|---|---|")
    for name, meaning in payload["hand_priority"]["scalar_meanings"].items():
        add(f"| `{name}` | {HAND_PRIORITY_WEIGHTS[name]:+.2f} | {meaning} |")
    add("")
    add("| action type | weight |")
    add("|---|---|")
    for name, weight in payload["hand_priority"]["type_weights"].items():
        add(f"| `{name}` | {weight:+.2f} |")
    add("")
    add(f"Ties: **{payload['hand_priority']['tie_break']}**.\n")
    add(f"Verified by `tests/unit/test_hand_priority.py` "
        f"({payload['tests']['test_hand_priority']} assertions): the label block is zero, a "
        f"non-zero label weight is refused at construction, choices are unchanged when every "
        f"control is relabelled, the module's executable source contains no fixture "
        f"vocabulary, and the weights never move.\n")
    add("**Considered and deliberately not added**, because each was conceived after "
        "observing this fixture and adding it would have been benchmark-fitting:\n")
    for rejected in payload["hand_priority"]["considered_and_rejected"]:
        add(f"- {rejected}")
    add("")

    add("## 6. Training protocol\n")
    for key, value in payload["training_protocol"].items():
        add(f"- **{key}**: {value}")
    add("")

    add("## 7. Evaluation protocol\n")
    for key, value in protocol.items():
        add(f"- **{key}**: {value}")
    add(f"\nCheckpoints byte-identical after evaluation: "
        f"`{payload['all_checkpoints_unmodified']}`. Total episodes "
        f"{payload['total_eval_episodes']}, wall clock "
        f"{payload['wall_clock_s'] / 3600:.2f} h.\n")

    add("## 8. Per-seed results\n")
    add("| arm | seed | done /25 | rate | review | S1 | mean R | median R | sd R | "
        "mean depth | max depth | uniq states | valid% | invalid | errors |")
    add("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for arm in ARM_ORDER:
        for r in payload["per_checkpoint"].get(arm, []):
            add(f"| {arm} | {r['seed']} | **{r['completion_count']}** | "
                f"{r['completion_rate']:.2f} | {r['review_count']} | {r['s1_count']} | "
                f"{r['mean_reward']:+.2f} | {r['median_reward']:+.2f} | {r['std_reward']:.2f} | "
                f"{r['mean_depth']:.2f} | {r['max_depth']} | {r['unique_states_mean']:.1f} | "
                f"{r['valid_action_rate'] * 100:.0f} | {r['invalid_actions']} | "
                f"{r.get('errors') or 'none'} |")
    add("")

    add("## 9. Arm-level statistics\n")
    add("The experimental unit is the **checkpoint / search replicate** (n=8 per arm). The "
        "bootstrap resamples those, never the 25 episodes inside one of them.\n")
    add("| arm | counts /25 | mean | median | sd | min | max | bootstrap 95% CI | >=1 | "
        "25/25 | zero | pooled |")
    add("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for arm in ARM_ORDER:
        s = arms[arm]
        add(f"| `{arm}` | `{s['completion_counts_per_seed']}` | "
            f"{s['mean_checkpoint_completion_rate']:.3f} | "
            f"{s['median_checkpoint_completion_rate']:.3f} | {s['sd_across_checkpoints']:.3f} | "
            f"{s['min_checkpoint_rate']:.2f} | {s['max_checkpoint_rate']:.2f} | "
            f"{s['bootstrap_95ci_mean_rate_over_checkpoints']} | "
            f"{s['checkpoints_with_at_least_one_completion']}/{s['n_checkpoints']} | "
            f"{s['checkpoints_with_all_episodes']}/{s['n_checkpoints']} | "
            f"{s['checkpoints_with_zero']}/{s['n_checkpoints']} | "
            f"{s['_descriptive_total']['completions']}/{s['_descriptive_total']['episodes']} |")
    add("\nThe `pooled` column is **descriptive only** — those episodes are correlated "
        "samples of one policy, not independent replicates.\n")
    add("| arm | mean review rate | mean S1 rate | mean reward | mean depth | "
        "mean uniq states | valid-action rate |")
    add("|---|---|---|---|---|---|---|")
    for arm in ARM_ORDER:
        s = arms[arm]
        add(f"| `{arm}` | {s['mean_review_rate']:.3f} | {s['mean_s1_rate']:.3f} | "
            f"{s['mean_reward_over_checkpoints']:+.2f} | {s['mean_depth_over_checkpoints']:.2f} | "
            f"{s['mean_unique_states']:.1f} | {s['mean_valid_action_rate'] * 100:.1f}% |")
    add("")

    for section, key, question in (
        ("10", "A_vs_B", "Does learning immediate action value improve over a reasonable "
                         "hand-designed action priority?"),
        ("11", "B_vs_C", "Does bootstrapped sequential value learning improve over myopic "
                         "immediate-reward prediction?"),
        ("12", "A_vs_C", "Does the full AC-DQN improve over a hand-priority search? "
                         "(read only in light of sections 10 and 11)"),
    ):
        block = payload["comparisons"][key]
        add(f"## {section}. {key.replace('_', ' ')}\n")
        add(f"> {question}\n")
        add(f"- `{block['left']}` mean completion rate: **{block['left_mean']:.3f}** "
            f"`{block['left_rates']}`")
        add(f"- `{block['right']}` mean completion rate: **{block['right_mean']:.3f}** "
            f"`{block['right_rates']}`")
        add(f"- difference (right - left): **{block['difference_right_minus_left']:+.3f}**, "
            f"bootstrap 95% CI `{block['bootstrap_95ci_of_difference']}`")
        add(f"- CI excludes zero: **{block['ci_excludes_zero']}**")
        if block.get("paired_by_seed"):
            paired = block["paired_by_seed"]
            add(f"- paired by training seed ({paired['_valid_because']}): mean difference "
                f"{paired['mean_difference']:+.3f}, per seed "
                f"`{paired['per_seed_difference']}`, "
                f"{paired['seeds_favouring_right']} seeds favour `{block['right']}` / "
                f"{paired['seeds_favouring_left']} favour `{block['left']}`")
        add(f"\n**{block['verdict']}**\n")
        if block.get("secondary"):
            add(f"_Secondary reading (search mode): {block['secondary']}_\n")

    add("## 13. Failure and anomaly analysis\n")
    anomalies = payload["anomalies"]
    if not anomalies["entries"]:
        add("No run crashed, hung, selected an invalid action, or was excluded.\n")
    else:
        add("| arm | seed | what | action taken |")
        add("|---|---|---|---|")
        for entry in anomalies["entries"]:
            add(f"| {entry.get('arm', '-')} | {entry.get('seed', '-')} | {entry['what']} | "
                f"{entry['action_taken']} |")
        add("")
    add(f"- runs requested: {anomalies['runs_requested']}, completed: "
        f"{anomalies['runs_completed']}, excluded: **{anomalies['runs_excluded']}**")
    add(f"- invalid action selections across every arm: "
        f"**{anomalies['total_invalid_actions']}**")
    add(f"- archive returns failed during Arm A's search: "
        f"{anomalies['search_returns_failed']} of {anomalies['search_returns_attempted']}")
    add(f"- checkpoints modified by evaluation: "
        f"**{0 if payload['all_checkpoints_unmodified'] else 'SOME - SEE JSON'}**\n")
    add("No seed was rerun, dropped, or tuned against. No result was excluded.\n")

    add("## 13b. Where the measured gap lives (post hoc)\n")
    bottleneck = payload["post_hoc_where_the_gap_lives"]
    add(f"> {bottleneck['_status']}\n")
    add(f"{bottleneck['_why']}\n")
    add("| arm | reaches review | completes **given** it reached review | completion rate |")
    add("|---|---|---|---|")
    for arm, block in bottleneck["per_arm"].items():
        add(f"| `{arm}` | {block['reaches_review_rate']:.3f} | "
            f"{block['completion_given_review']:.3f} | {block['completion_rate']:.3f} |")
    add("")
    add(f"**Mechanism.** {bottleneck['_mechanism']}\n")
    add(f"**What it implies.** {bottleneck['_what_it_implies']}\n")

    add("## 14. Limitations\n")
    for limitation in payload["limitations"]:
        add(f"- {limitation}")
    add("")

    add("## 15. Interpretation\n")
    for paragraph in payload["interpretation"]:
        add(f"{paragraph}\n")

    add("## 16. Exact commands\n")
    add("```bash")
    for command in payload["commands"]:
        add(command)
    add("```\n")

    add("## 17. Runtime\n")
    add(f"- search phase: {payload['search_wall_clock_s'] / 3600:.2f} h "
        f"({payload['search_wall_clock_s']:.0f} s)")
    add(f"- evaluation phase: {payload['eval_wall_clock_s'] / 3600:.2f} h "
        f"({payload['eval_wall_clock_s']:.0f} s)")
    add(f"- **total: {payload['wall_clock_s'] / 3600:.2f} h**\n")
    return "\n".join(lines)


def print_console(payload: dict) -> None:
    print("=" * 88)
    print("SEARCH LADDER: hand priority -> learned immediate value -> bootstrapped value")
    print("=" * 88)
    for arm in ARM_ORDER:
        s = payload["arm_summaries"][arm]
        identity = ARM_IDENTITY[arm]
        print(f"  [{identity['rung']}] {arm}")
        print(f"      counts  {s['completion_counts_per_seed']}")
        print(f"      mean {s['mean_checkpoint_completion_rate']:.3f}  "
              f"median {s['median_checkpoint_completion_rate']:.3f}  "
              f"sd {s['sd_across_checkpoints']:.3f}  "
              f"CI {s['bootstrap_95ci_mean_rate_over_checkpoints']}")
    print("\n  comparisons (difference in mean checkpoint completion rate)")
    for key, block in payload["comparisons"].items():
        print(f"    {key:<8} {block['difference_right_minus_left']:+.3f}  "
              f"CI {block['bootstrap_95ci_of_difference']}  "
              f"excludes zero: {block['ci_excludes_zero']}")
    print(f"\n  wall clock {payload['wall_clock_s'] / 3600:.2f} h   "
          f"checkpoints unmodified: {payload['all_checkpoints_unmodified']}   "
          f"invalid actions: {payload['anomalies']['total_invalid_actions']}")


# -- main ------------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(range(8)))
    parser.add_argument("--episodes", type=int, default=EPISODES,
                        help="evaluation episodes per checkpoint. 25 is canonical; do not "
                             "revert to 5.")
    parser.add_argument("--search-steps", type=int, default=SEARCH_STEPS,
                        help="env steps for Arm A's search phase. Matched to --train-steps.")
    parser.add_argument(
        "--reanalyse", action="store_true",
        help="re-render the report from the episodes already recorded in the JSON. Touches "
             "no browser, loads no checkpoint and changes no measurement - it exists so an "
             "analysis added after the run does not require repeating a 6-hour experiment, "
             "and so the JSON, Markdown and CSV cannot drift apart when one does.",
    )
    parser.add_argument("--smoke", action="store_true",
                        help="short run for pre-flight checks. Writes to a *_smoke.* file "
                             "so it can never be mistaken for the real measurement.")
    parser.add_argument("--out-stem", type=Path,
                        default=REPO_ROOT / "reports" / "search_vs_bandit_vs_ac_dqn")
    return parser.parse_args()


def persist(stem: Path, payload: dict) -> None:
    """Write JSON / Markdown / CSV atomically, after every seed.

    Called incrementally for the same reason `compare_agents.persist` is: a 3h34m run was
    lost on 2026-08-28 to a single write at the end.
    """
    for suffix, text in (
        (".json", json.dumps(payload, indent=2, default=str)),
        (".md", render_markdown(payload)),
    ):
        path = stem.with_suffix(suffix)
        tmp = path.with_suffix(suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    fields = ["arm", "rung", "seed", "completion_count", "completion_rate", "review_count",
              "review_rate", "s1_count", "s1_rate", "mean_reward", "median_reward",
              "std_reward", "min_reward", "max_reward", "mean_depth", "max_depth",
              "unique_states_mean", "valid_action_rate", "invalid_actions", "errors"]
    csv_path = stem.with_suffix(".csv")
    tmp = csv_path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for arm in ARM_ORDER:
            for row in payload["per_checkpoint"].get(arm, []):
                writer.writerow({k: row.get(k) for k in fields}
                                | {"rung": ARM_IDENTITY[arm]["rung"]})
    tmp.replace(csv_path)


def main() -> None:  # noqa: PLR0915 - one linear experiment, kept in reading order
    args = parse_args()
    stem = args.out_stem.with_name(args.out_stem.name + "_smoke") if args.smoke else args.out_stem
    episodes = 3 if args.smoke else args.episodes
    search_steps = 80 if args.smoke else args.search_steps

    if args.reanalyse:
        reanalyse(args, stem, episodes, search_steps)
        return

    # Fail before spending hours if a checkpoint is missing, and hash them first.
    manifest = []
    for arm, (directory, _cls) in TRAINED_ARMS.items():
        for seed in args.seeds:
            path = directory / f"{arm}_seed{seed}_final.zip"
            if not path.exists():
                raise SystemExit(f"STOP: missing checkpoint {path}")
            manifest.append({"arm": arm, "seed": seed, "path": str(path),
                             "sha256_before": sha256(path), "bytes": path.stat().st_size})

    # A fresh ActionFeatureExtractor must still be bit-identical to a trained one, or
    # re-evaluating B and C here is not protocol-faithful to the runs that trained them.
    from web_testing_agent.envs.types import ActionSpec, ActionType
    probe = [ActionSpec(index=0, action_type=ActionType.CLICK, element_id="probe", description="d")]
    aged = ActionFeatureExtractor()
    for _ in range(50):
        aged.start_episode()
        aged.observe_action_set(probe, "http://x/p.html")
    assert np.array_equal(ActionFeatureExtractor().encode(probe, "k", set()),
                          aged.encode(probe, "k", set())), \
        "a fresh ActionFeatureExtractor no longer matches a trained one"

    encoders = default_encoders()
    rows_by_arm: dict[str, list[dict]] = {arm: [] for arm in ARM_ORDER}
    search_rows: list[dict] = []
    anomaly_entries: list[dict] = []
    search_seconds = 0.0
    eval_seconds = 0.0
    started = time.monotonic()

    with serve_directory(SITE) as origin:
        base_url = f"{origin}/index.html"
        logger.info("deep fixture at {}", base_url)

        for seed in args.seeds:
            logger.info("############ seed {} ############", seed)

            # -- Arm A, search phase (budget-matched to --train-steps) -----------------
            phase_started = time.monotonic()
            try:
                search_rows.append(run_search_phase(base_url, seed, search_steps, EPISODE_STEPS))
                logger.info("search seed {}: {}", seed, search_rows[-1]["flow"])
            except Exception as exc:  # noqa: BLE001 - recorded, never hidden
                anomaly_entries.append({"arm": "hand_priority_search_phase", "seed": seed,
                                        "what": f"search phase raised {exc!r}",
                                        "action_taken": "recorded; seed retained, not rerun"})
                logger.error("search seed {} FAILED: {}", seed, exc)
            search_seconds += time.monotonic() - phase_started

            # -- all four evaluation rows, identical protocol --------------------------
            factories = {
                "hand_priority": lambda s=seed: HandPriorityPolicy(
                    epsilon=EVAL_EPSILON, seed=s, carry_counts_across_episodes=False),
                "hand_priority_search": lambda s=seed: HandPriorityPolicy(
                    epsilon=EVAL_EPSILON, seed=s, carry_counts_across_episodes=True),
            }
            for arm, (directory, cls) in TRAINED_ARMS.items():
                path = directory / f"{arm}_seed{seed}_final.zip"

                def _trained(p=path, c=cls, s=seed):
                    model = c.load(str(p), device="cpu")
                    return TrainedPolicy(model, encoders, epsilon=EVAL_EPSILON, masked=True,
                                         seed=s, action_features=ActionFeatureExtractor())
                factories[arm] = _trained

            for arm in ARM_ORDER:
                phase_started = time.monotonic()
                try:
                    row = evaluate(base_url, arm, seed, factories[arm], episodes)
                    row["errors"] = None
                    if arm in TRAINED_ARMS:
                        row["checkpoint"] = str(
                            TRAINED_ARMS[arm][0] / f"{arm}_seed{seed}_final.zip")
                    rows_by_arm[arm].append(row)
                    logger.info("{} seed {}: {}/{} completions", arm, seed,
                                row["completion_count"], episodes)
                    if row["invalid_actions"]:
                        anomaly_entries.append({
                            "arm": arm, "seed": seed,
                            "what": f"{row['invalid_actions']} out-of-range action selections",
                            "action_taken": "recorded; run retained in every analysis"})
                except Exception as exc:  # noqa: BLE001
                    anomaly_entries.append({"arm": arm, "seed": seed,
                                            "what": f"evaluation raised {exc!r}",
                                            "action_taken": "recorded; seed retained, not rerun"})
                    logger.error("{} seed {} FAILED: {}", arm, seed, exc)
                eval_seconds += time.monotonic() - phase_started

            payload = assemble(args, stem, manifest, rows_by_arm, search_rows,
                               anomaly_entries, search_seconds, eval_seconds,
                               time.monotonic() - started, episodes, search_steps,
                               complete=False)
            if payload is not None:
                persist(stem, payload)

    for entry in manifest:
        entry["sha256_after"] = sha256(Path(entry["path"]))
        entry["unmodified"] = entry["sha256_after"] == entry["sha256_before"]

    payload = assemble(args, stem, manifest, rows_by_arm, search_rows, anomaly_entries,
                       search_seconds, eval_seconds, time.monotonic() - started,
                       episodes, search_steps, complete=True)
    if payload is None:
        raise SystemExit("STOP: no arm produced a single completed row")
    persist(stem, payload)
    print_console(payload)
    print(f"\nWrote {stem.with_suffix('.json')}\nWrote {stem.with_suffix('.md')}"
          f"\nWrote {stem.with_suffix('.csv')}")


def reanalyse(args, stem, episodes: int, search_steps: int) -> None:
    """Rebuild the report from the measurements already on disk. No env, no checkpoint.

    Every number here was produced by the original run; this only re-renders. The
    per-checkpoint rows are reunited with their per-episode records so `assemble` sees
    exactly the structure it saw live, and the wall-clock and anomaly fields are carried
    over verbatim rather than recomputed from a clock that is no longer measuring anything.
    """
    previous = json.loads(stem.with_suffix(".json").read_text(encoding="utf-8"))
    rows_by_arm: dict[str, list[dict]] = {arm: [] for arm in ARM_ORDER}
    for arm in ARM_ORDER:
        for row in previous["per_checkpoint"][arm]:
            rows_by_arm[arm].append({**row, "episodes": [
                e for e in previous["per_episode"]
                if e["arm"] == arm and e["seed"] == row["seed"]]})
    payload = assemble(args, stem, previous["checkpoints"], rows_by_arm,
                       previous["search_phase"], previous["anomalies"]["entries"],
                       previous["search_wall_clock_s"], previous["eval_wall_clock_s"],
                       previous["wall_clock_s"], episodes, search_steps,
                       complete=previous["_complete"])
    if payload is None:
        raise SystemExit("STOP: the saved report has no completed rows to re-render")
    payload["_reanalysed"] = ("re-rendered from the recorded episodes; no measurement was "
                              "repeated and no number changed")
    persist(stem, payload)
    print_console(payload)
    print(f"\nRe-rendered {stem.with_suffix('.json')} / .md / .csv from saved records.")


def assemble(args, stem, manifest, rows_by_arm, search_rows, anomaly_entries,  # noqa: PLR0913
             search_seconds, eval_seconds, total_seconds, episodes, search_steps,
             *, complete: bool) -> dict | None:
    """Build the report payload from whatever has finished. Returns None if nothing has."""
    if not all(rows_by_arm[arm] for arm in ARM_ORDER):
        return None
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    summaries = {arm: summarise_arm(rows_by_arm[arm], rng, episodes) for arm in ARM_ORDER}
    comparisons = {
        "A_vs_B": compare("hand_priority", "contextual_bandit", rows_by_arm, rng),
        "B_vs_C": compare("contextual_bandit", "ac_dqn", rows_by_arm, rng),
        "A_vs_C": compare("hand_priority", "ac_dqn", rows_by_arm, rng),
        "Asearch_vs_C": compare("hand_priority_search", "ac_dqn", rows_by_arm, rng),
        "Asearch_vs_B": compare("hand_priority_search", "contextual_bandit", rows_by_arm, rng),
    }
    for block in comparisons.values():
        block["verdict"] = verdict_for(block)
    comparisons["A_vs_B"]["secondary"] = comparisons["Asearch_vs_B"]["verdict"]
    comparisons["A_vs_C"]["secondary"] = comparisons["Asearch_vs_C"]["verdict"]

    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          cwd=REPO_ROOT).stdout.strip()
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                            text=True, cwd=REPO_ROOT).stdout.strip()
    total_invalid = sum(s["total_invalid_actions"] for s in summaries.values())

    return {
        "_purpose": "Locate AC-DQN on a ladder whose floor is a non-learning search with a "
                    "hand-designed action priority.",
        "_complete": complete,
        "_smoke": bool(args.smoke),
        "_inferential_unit": "the checkpoint / search replicate (n=8 per arm). The 25 "
                             "episodes inside one of them are correlated samples of one "
                             "policy, never independent training replicates.",
        "git": {"head": head, "branch": branch},
        "context": {
            "fixture": "tests/fixtures/deep_flow_site (DEEP-01: the receipt reports "
                       "quantity 1 whatever was ordered; llm_required, cross_step, "
                       "deep_flow; deterministic ceiling 0)",
            "flow_depth_map": FLOW_DEPTH,
            "observation": "6973-dim = 1664 perception + 9 episode context + "
                           "100x52 action features + 100 mask",
            "action_space": "Discrete(100), page-rebuilt each step, valid-only masking",
            "reward": "reward/functional_triggers.compose_reward with exploration shaping "
                      "(novelty 1.0/sqrt(1+run visits), reveal 1.5/unpaid identity, "
                      "repetition -0.05/repeat floored at -0.25, failed action -0.1, "
                      "step -0.05); judge disabled (NullRewardModel)",
            "encoders": "hash (default_encoders), as in every figure these checkpoints back",
            "trained_arm_checkpoints": "loaded read-only; NOT retrained for this experiment",
            "trained_arm_training_stats": "see reports/postmarkov_baseline_pr050.json and "
                                          "reports/contextual_bandit_pr050.json",
            "prior_canonical_result": "reports/offline_eval_25ep.md — ac_dqn "
                                      "[24,0,25,23,1,2,24,17] mean 0.580; contextual_bandit "
                                      "[17,8,0,0,25,14,24,20] mean 0.540",
        },
        "hand_priority": {
            "scoring_rule": "score(a) = w . phi(a), phi = ActionFeatureExtractor's 52-dim "
                            "per-action vector, w fixed",
            "label_block_weight_l1": 0.0,
            "scalar_weights": dict(HAND_PRIORITY_WEIGHTS),
            "type_weights": {t.value: w for t, w in HAND_PRIORITY_TYPE_WEIGHTS.items()},
            "tie_break": "lowest action index, which is action_registry.build_action_specs' "
                         "own generic priority (viewport-first, then CLICK, TYPE(valid), "
                         "SELECT, RAPID_CLICK, TYPE(edge cases), DOM order)",
            "scalar_meanings": {
                "is_newly_revealed": "a control that appeared in response to the previous "
                                     "action - the repository's own domain-general "
                                     "gated-flow progress signal",
                "is_form_control": "TYPE/SELECT: fill a form before trying to leave the page",
                "value_ordinal": "among generated values prefer a plausible one over a "
                                 "boundary or malformed one",
                "navigational": "expected to change the URL, i.e. forward progress",
                "in_viewport": "on screen; mirrors the registry's viewport-first priority",
                "has_href": "carries a link target at all (weak tie-breaker)",
                "opens_new_tab": "a popup leaves the flow",
                "is_fixed_action": "NO_OP/scroll/resize/back/forward/refresh cannot advance "
                                   "an application flow",
                "taken_this_episode": "already tried here this episode - what makes a "
                                      "deterministic argmax a search rather than a loop",
                "taken_here": "already tried here across the run",
            },
            "contains_no_fixture_knowledge_because": "the 32 hashed-label dimensions are "
                "weighted exactly zero, so the policy cannot respond to any control's name; "
                "asserted by test_hand_priority.py both directly and by relabelling",
            "frozen_before_any_result_was_seen": True,
            "considered_and_rejected": [
                "**A page-uniqueness ('control IDF') term** — demote an action identity that "
                "appears at many distinct states, since site chrome (nav bars, footers) "
                "appears everywhere while a page's own controls do not. It is generic, it is "
                "cheap, and it is a standard crawler idea. It was conceived *after* watching "
                "where Arm A stalls on this fixture, and tracing showed it would have "
                "promoted the stalling page's own controls. Adding it would therefore have "
                "been fitting the heuristic to the benchmark, which is exactly what this "
                "experiment exists to avoid. Recorded here instead of used.",
                "**A frontier term over unvisited href targets** — rejected for the same "
                "reason, and because reading a link's URL moves the policy toward responding "
                "to page identity, which the zero label weights exist to prevent.",
            ],
        },
        "search_budget": {
            "unit": "environment steps - the unit run_go_explore --budget and "
                    "compare_agents --train-steps both already use",
            "arm_A_env_steps": search_steps,
            "arm_B_C_train_steps": SEARCH_STEPS,
            "matched": search_steps == SEARCH_STEPS,
            "episodes_per_search": max(1, search_steps // EPISODE_STEPS),
            "archive": f"ArchiveStartWrapper, p_return={P_RETURN}, same as the trained arms",
            "route_replay": "outside the step budget (as in archive_start.py) and reported "
                            "separately as replayed_actions / browser_actions_total",
            "epsilon_schedule": f"{EPS_INITIAL} -> {EPS_FINAL} over "
                                f"{EXPLORATION_FRACTION:.0%} of the budget, identical to "
                                f"the trained arms",
            "why_fair": "both arms are given the same number of environment interactions, "
                        "the same archive at the same p_return, and the same exploration "
                        "schedule; browser actions spent on route replay are reported "
                        "rather than absorbed, so neither side gets free actions",
            "what_arm_A_cannot_carry_forward": "nothing. A non-learning policy has no "
                                               "parameters to update, and the archive is "
                                               "disabled at evaluation for every arm. The "
                                               "search phase therefore measures coverage at "
                                               "a matched budget, not a trainable asset.",
        },
        "training_protocol": {
            "_applies_to": "arms B and C, as run when their checkpoints were produced",
            "train_steps": SEARCH_STEPS, "episode_steps": EPISODE_STEPS,
            "seeds": list(range(8)), "p_return": P_RETURN,
            "exploration": f"epsilon {EPS_INITIAL} -> {EPS_FINAL} over "
                           f"{EXPLORATION_FRACTION:.0%} of training",
            "n_step": "3 for ac_dqn; forced to 1 for contextual_bandit (nothing to bootstrap)",
            "encoders": "hash", "judge": "disabled",
            "source": "reports/postmarkov_baseline_pr050.json, "
                      "reports/contextual_bandit_pr050.json",
            "retrained_here": False,
        },
        "evaluation_protocol": {
            "episodes_per_checkpoint": episodes,
            "eval_epsilon": EVAL_EPSILON,
            "episode_steps": EPISODE_STEPS,
            "start": "landing page (index.html), no setup_actions",
            "archive": "disabled at evaluation for every arm",
            "encoders": "hash (default_encoders)",
            "judge": "disabled (NullRewardModel)",
            "checkpoint_selection": "FINAL",
            "seed_scheme": "run_rollout's: env.reset(seed=seed + episode_index); each "
                           "policy's epsilon RNG seeded once with the seed. Identical for "
                           "all four rows.",
            "action_feature_extractor": "fresh per row; verified bit-identical to a trained "
                                        "one because record_taken() is never called on the "
                                        "RL path",
            "training_during_evaluation": "none - no optimiser is constructed, no buffer is "
                                          "written, and checkpoint SHA-256 is compared "
                                          "before and after",
        },
        "search_phase": search_rows,
        "per_checkpoint": {arm: [{k: v for k, v in row.items() if k != "episodes"}
                                 for row in rows_by_arm[arm]] for arm in ARM_ORDER},
        "per_episode": [ep for arm in ARM_ORDER for row in rows_by_arm[arm]
                        for ep in row["episodes"]],
        "within_checkpoint_wilson_intervals": {
            arm: {str(r["seed"]): wilson(r["completion_count"], r["n_episodes"])
                  for r in rows_by_arm[arm]} for arm in ARM_ORDER},
        "arm_summaries": summaries,
        "comparisons": comparisons,
        "checkpoints": manifest,
        "all_checkpoints_unmodified": all(e.get("unmodified", True) for e in manifest),
        "total_eval_episodes": sum(len(r["episodes"]) for arm in ARM_ORDER
                                   for r in rows_by_arm[arm]),
        "anomalies": {
            "entries": anomaly_entries,
            "runs_requested": len(args.seeds) * len(ARM_ORDER),
            "runs_completed": sum(len(rows_by_arm[arm]) for arm in ARM_ORDER),
            "runs_excluded": 0,
            "total_invalid_actions": total_invalid,
            "search_returns_attempted": sum(r["archive"]["returns_attempted"]
                                            for r in search_rows),
            "search_returns_failed": sum(r["archive"]["returns_failed"] for r in search_rows),
            "_policy": "Nothing was excluded, rerun, or tuned against. A run that failed "
                       "would appear above with its raw result retained.",
        },
        "tests": {"test_hand_priority": 22},
        "post_hoc_where_the_gap_lives": post_hoc_bottleneck(summaries),
        "limitations": LIMITATIONS,
        "interpretation": interpretation(summaries, comparisons),
        "commands": [
            "python -m pytest tests/unit/test_hand_priority.py "
            "tests/unit/test_contextual_bandit.py tests/unit/test_dueling.py "
            "tests/unit/test_action_features.py tests/unit/test_go_explore.py "
            "tests/unit/test_archive_start.py -q",
            f"python scripts/search_vs_bandit_vs_ac_dqn.py --seeds "
            f"{' '.join(str(s) for s in args.seeds)}",
        ],
        "search_wall_clock_s": round(search_seconds, 1),
        "eval_wall_clock_s": round(eval_seconds, 1),
        "wall_clock_s": round(total_seconds, 1),
    }


def post_hoc_bottleneck(summaries: dict) -> dict:
    """Decompose each arm's completion rate into *reaching* the last page and *passing* it.

    **This analysis was written after the run and is post-hoc.** It introduces no new
    measurement — both numbers are already in the per-seed table — and it is reported
    because the completion rate alone cannot distinguish an arm that never finds the flow
    from one that walks the whole flow and then fails a single decision. Those are very
    different failures and they point at different mechanisms.
    """
    return {
        "_status": "POST HOC. Written after seeing the results, computed from the recorded "
                   "episodes, nothing rerun and no arm changed. Read it as a description of "
                   "where the measured gap sits, not as a pre-registered test.",
        "_why": "completion = (reached the review page) x (passed the decision on it). An "
                "arm can lose on either factor, and the two say different things.",
        "per_arm": {
            arm: {
                "reaches_review_rate": summaries[arm]["mean_review_rate"],
                "completion_given_review": summaries[arm]["mean_completion_given_review"],
                "pooled_completion_given_review":
                    summaries[arm]["pooled_completion_given_review"],
                "completion_rate": summaries[arm]["mean_checkpoint_completion_rate"],
            } for arm in ARM_ORDER
        },
        "_mechanism": "On the review page every candidate is a navigational, in-viewport "
                      "link carrying an href, so every feature the hand priority is allowed "
                      "to read is identical across them. The one thing that separates the "
                      "control that completes the order from the one that returns to the "
                      "home page is the control's *label*, and the label block is weighted "
                      "exactly zero by construction. The hand priority is therefore "
                      "structurally indifferent at precisely this decision — which is the "
                      "same decision `reports/trained_action_q_diagnostic.md` independently "
                      "identified as the deadlock for the trained arms.",
        "_what_it_implies": "If the gap between Arm A and the learned arms is concentrated "
                            "in this one decision rather than spread over the flow, then "
                            "what the learned arms are buying on this benchmark is the "
                            "ability to attach value to a particular control's identity - "
                            "representation learning - and not sequential planning. B vs C "
                            "is the comparison that speaks to planning, and it is "
                            "inconclusive.",
    }


def verdict_for(block: dict) -> str:
    """One sentence, written from the numbers, never from a preference.

    Deliberately conservative: an interval that contains zero is reported as "no detectable
    difference at n=8", not as evidence of equality and certainly not as a win.
    """
    left, right = block["left"], block["right"]
    difference = block["difference_right_minus_left"]
    low, high = block["bootstrap_95ci_of_difference"]
    if low <= 0 <= high:
        return (f"INCONCLUSIVE. `{right}` is {difference:+.3f} on mean completion rate, but "
                f"the 95% interval [{low:+.3f}, {high:+.3f}] contains zero. At 8 replicates "
                f"per arm this experiment cannot separate them.")
    winner, loser = (right, left) if difference > 0 else (left, right)
    return (f"`{winner}` is ahead of `{loser}` by {abs(difference):.3f} on mean completion "
            f"rate, 95% interval [{low:+.3f}, {high:+.3f}], which excludes zero. Eight "
            f"replicates per arm on one fixture at one budget: a difference this experiment "
            f"can see, not a general property of either algorithm.")


LIMITATIONS = [
    "**Arm A's eight rows are not eight training runs.** A non-learning policy has nothing "
    "to vary across seeds except its epsilon stream, so Arm A's spread understates what a "
    "spread means for Arms B and C, whose rows carry genuine training variance. Its "
    "bootstrap interval is correspondingly narrow for a reason that is not a property of "
    "the algorithm.",
    "**Arm A reads two features that are structurally dead for Arms B and C.** `taken_here` "
    "and `taken_this_episode` are written only by `ActionFeatureExtractor.record_taken()`, "
    "which nothing on the RL path calls, so they are identically 0.0 in every observation "
    "the two networks have ever seen. Arm A calls it and reads them. This is an information "
    "advantage for Arm A; removing it would not level the comparison, it would delete the "
    "search, since a memoryless deterministic argmax repeats one action until truncation.",
    "**Arms B and C were not retrained.** Their finalized checkpoints were re-evaluated "
    "contemporaneously with Arm A. That keeps the checkpoints every published figure refers "
    "to intact and puts all rows in one harness state, but it means their training phase "
    "could not be re-instrumented for the search-metrics table.",
    "**The browser harness is intermittently non-deterministic on this configuration** "
    "(`reports/diag_harness_determinism.json`): two identical runs at one seed usually "
    "agree bit-for-bit and occasionally do not. Every row here is a single run.",
    "**n=8 per arm on one fixture at one budget.** Eight is a small sample. Nothing here "
    "generalises to another application, another step budget, or another reward.",
    "**Per-episode seeds overlap across rows** (row k uses env seeds k..k+24). This is the "
    "original experiment's scheme, reused unchanged and identical across all four rows.",
    "**One statistic per comparison, fixed in advance.** Difference in mean checkpoint "
    "completion rate with a bootstrap interval over replicates. No post-hoc test was run "
    "and none was selected after seeing the data.",
    "**Completion is a proxy for the research objective.** Reaching receipt.html is what "
    "makes DEEP-01 findable; it is not itself finding it. The deterministic ceiling on this "
    "fixture is 0, so no arm's `distinct_findings` is a bug-discovery figure.",
]


def interpretation(summaries: dict, comparisons: dict) -> list[str]:
    """Written from the measured numbers at report time, not from an expectation."""
    order = sorted(ARM_ORDER, key=lambda a: -summaries[a]["mean_checkpoint_completion_rate"])
    ranking = ", ".join(
        f"`{a}` {summaries[a]['mean_checkpoint_completion_rate']:.3f}" for a in order)
    inconclusive = [k for k in ("A_vs_B", "B_vs_C", "A_vs_C")
                    if not comparisons[k]["ci_excludes_zero"]]
    paragraphs = [
        f"**Ranking by mean checkpoint completion rate:** {ranking}.",
        f"**{len(inconclusive)} of the three primary comparisons "
        f"{'is' if len(inconclusive) == 1 else 'are'} inconclusive at this sample size** "
        f"({', '.join(inconclusive) if inconclusive else 'none'}). An "
        f"inconclusive result is not evidence that the two arms are equal; it is evidence "
        f"that eight replicates per arm cannot separate them.",
    ]
    if not comparisons["B_vs_C"]["ci_excludes_zero"]:
        paragraphs.append(
            "**On the rung that matters most — B vs C — this experiment does not detect a "
            "contribution from bootstrapping.** Under matched infrastructure (same "
            "observation, action space, mask, reward, archive, PER buffer, network shape, "
            "epsilon schedule and budget), replacing the bootstrapped target with the "
            "immediate reward does not produce a difference this design can see. That is "
            "consistent with the four preceding null results and with the 25-episode "
            "re-evaluation, and it is a statement about *this* AC-DQN on *this* benchmark "
            "at *this* budget, not about temporal-difference learning.")
    else:
        paragraphs.append(
            "**B vs C is the one comparison where an interval excludes zero**, so "
            "bootstrapping is doing something detectable here. Read section 11's per-seed "
            "column before generalising: a mean difference carried by one or two "
            "checkpoints is a different claim from one that holds across the eight.")
    hand = summaries["hand_priority"]["mean_checkpoint_completion_rate"]
    dqn = summaries["ac_dqn"]["mean_checkpoint_completion_rate"]
    if hand >= dqn:
        paragraphs.append(
            "**A hand-priority search is not behind AC-DQN here, and that does not make the "
            "RL approach useless.** It means the current AC-DQN, at 4,000 steps on this "
            "fixture, does not outperform a generic non-learning priority — which is a "
            "measurement of this configuration, not a verdict on learned exploration. The "
            "hand priority also cannot transfer a single thing it 'knows' to a new "
            "application, whereas the point of a learned action representation is exactly "
            "that it might.")
    else:
        paragraphs.append(
            "**AC-DQN is ahead of the hand-priority floor.** Check whether that lead is "
            "carried by the eight checkpoints or by two of them before calling it a "
            "property of the algorithm: section 9's per-seed counts, not the mean, are "
            "what settle that.")
    reaches = summaries["hand_priority"]["mean_review_rate"]
    best_learned = max(summaries["contextual_bandit"]["mean_review_rate"],
                       summaries["ac_dqn"]["mean_review_rate"])
    if reaches >= best_learned:
        paragraphs.append(
            f"**The hand priority is not losing because it cannot explore.** It reaches the "
            f"final review page in {reaches:.1%} of episodes — more often than either "
            f"learned arm ({best_learned:.1%} at best) — and then completes "
            f"{summaries['hand_priority']['mean_completion_given_review']:.1%} of the time "
            f"from there, against "
            f"{summaries['ac_dqn']['mean_completion_given_review']:.1%} for AC-DQN. The "
            f"whole measured gap sits in one decision, not in the four gates before it. See "
            f"section 13b, which is post hoc.")
    paragraphs.append(
        "**What this cannot establish.** It does not isolate PER, dueling, Double DQN, "
        "n-step replay, the archive or the target network — all shared, all cancelling. It "
        "does not measure bug discovery, since the fixture's deterministic ceiling is 0. And "
        "it does not measure transfer, which is the property the action-conditioned "
        "representation exists for and which a single-fixture benchmark cannot see.")
    return paragraphs


if __name__ == "__main__":
    main()
