"""Re-evaluate the 16 completed checkpoints at 25 episodes instead of 5. No training.

**The question.** At 5 evaluation episodes AC-DQN's completion counts were perfectly
all-or-nothing (`[5,0,5,5,0,0,5,5]`, 8/8 seeds at an extreme) while the contextual bandit's
were not (`[5,2,0,0,5,1,4,3]`, 4/8 intermediate). Five episodes is a coarse instrument: a
policy that completes 80% of the time and one that completes 100% of the time both read as
5/5 most of the time. This asks whether that difference in *shape* survives a five-fold
longer evaluation of the very same trained policies.

**This is not a new training experiment.** Nothing is retrained, no checkpoint is written,
and the only protocol change from the original runs is `eval_episodes` 5 -> 25. The
experimental unit is still the checkpoint: 25 episodes from one policy are 25 correlated
samples of that policy, not 25 independent training runs, and every inferential statement
below resamples checkpoints rather than episodes.

**Seed scheme, reused rather than invented.** The original experiment derived per-episode
seeds inside `run_rollout` as `env.reset(seed=training_seed + episode_index)`, and seeded
`TrainedPolicy`'s epsilon RNG once with the training seed. Both are reused unchanged and
are identical across the two arms. Note that the episode-to-episode variation comes almost
entirely from the policy's epsilon stream rather than from the env seed: the fixture is
static, so `reset(seed=k)` returns the same landing page for every k.

**Why a fresh `ActionFeatureExtractor` is not a protocol difference.** Training shared one
extractor between learning and evaluation because its visit counts are part of the
observation. Those counts are only ever written by `record_taken()`, which nothing calls, so
a fresh extractor emits a bit-identical action block — verified, and asserted at startup
here rather than assumed.

    python scripts/offline_eval_25ep.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
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

from web_testing_agent.agents.ac_dqn import ActionConditionedDQN  # noqa: E402
from web_testing_agent.agents.action_features import ActionFeatureExtractor  # noqa: E402
from web_testing_agent.agents.contextual_bandit import ContextualBandit  # noqa: E402
from web_testing_agent.envs import WebFunctionalEnv  # noqa: E402
from web_testing_agent.evaluation import run_rollout  # noqa: E402
from web_testing_agent.perception.vec_wrapper import default_encoders  # noqa: E402
from web_testing_agent.utils.local_server import serve_directory  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

SITE = REPO_ROOT / "tests" / "fixtures" / "deep_flow_site"
SEEDS = list(range(8))
EPISODES = 25
EVAL_EPSILON = 0.05
EPISODE_STEPS = 40
BOOTSTRAP = 20_000
BOOTSTRAP_SEED = 0

ARMS = {
    "ac_dqn": (REPO_ROOT / "models/checkpoints/postmarkov_baseline", ActionConditionedDQN),
    "contextual_bandit": (REPO_ROOT / "models/checkpoints/contextual_bandit", ContextualBandit),
}
# The 5-episode results these are being compared against. Historical, never recomputed.
FIVE_EPISODE = {"ac_dqn": [5, 0, 5, 5, 0, 0, 5, 5],
                "contextual_bandit": [5, 2, 0, 0, 5, 1, 4, 3]}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def depth_of(url: str) -> int:
    return FLOW_DEPTH.get(url.split("/")[-1].split("?")[0], 0)


def evaluate_checkpoint(base_url: str, arm: str, seed: int, model, encoders) -> dict:
    """25 episodes from the landing page, archive disabled, epsilon 0.05.

    Deliberately mirrors `compare_agents.evaluate` — same `run_rollout`, same
    `TrainedPolicy`, same `flow_metrics` — with one addition: the per-episode URL and
    state-key lists are kept so each episode can be reported individually. Reusing the
    scoring functions rather than reimplementing them is what keeps this comparable to the
    numbers it is being read against.
    """
    env = WebFunctionalEnv(base_url=base_url, max_steps=EPISODE_STEPS, headless=True)
    assert not env.setup_actions, "evaluation must start from the landing page"
    episodes_urls: list[list[str]] = []
    episodes_keys: list[list[str]] = []
    visited: list[str] = []
    try:
        # A fresh extractor is bit-identical to a trained one (record_taken is dead);
        # asserted in main() before any checkpoint is scored.
        features = ActionFeatureExtractor()
        policy = TrainedPolicy(model, encoders, epsilon=EVAL_EPSILON, masked=True,
                               seed=seed, action_features=features)
        original_step, original_reset = env.step, env.reset

        def tracking_reset(**kwargs):
            episodes_urls.append([])
            episodes_keys.append([])
            return original_reset(**kwargs)

        def tracking_step(action):
            result = original_step(action)
            info = result[4]
            visited.append(info["page"]["url"])
            episodes_urls[-1].append(info["page"]["url"])
            episodes_keys[-1].append(info.get("state_key", ""))
            return result

        env.reset = tracking_reset          # type: ignore[method-assign]
        env.step = tracking_step            # type: ignore[method-assign]
        report = run_rollout(env, policy, episodes=EPISODES, label=f"{arm}_s{seed}",
                             seed=seed)
    finally:
        env.close()
    report.visited_urls = visited           # type: ignore[attr-defined]

    per_episode = []
    for index, (urls, keys) in enumerate(zip(episodes_urls, episodes_keys, strict=True)):
        depth = max((depth_of(u) for u in urls), default=0)
        per_episode.append({
            "arm": arm, "seed": seed, "episode": index,
            # The seed `run_rollout` passes to `env.reset` for this episode.
            "eval_seed": seed + index,
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
    return {
        "arm": arm, "seed": seed,
        "n_episodes": len(per_episode),
        "completion_count": completions,
        "completion_rate": round(completions / len(per_episode), 4),
        "review_count": sum(e["reached_review"] for e in per_episode),
        "review_rate": round(sum(e["reached_review"] for e in per_episode) / len(per_episode), 4),
        "s1_count": sum(e["reached_s1"] for e in per_episode),
        "s1_rate": round(sum(e["reached_s1"] for e in per_episode) / len(per_episode), 4),
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
        "flow_metrics": flow_metrics(report, episodes_urls),
        "episodes": per_episode,
    }


def bootstrap_ci(values: list[float], rng) -> list[float]:
    """95% CI for the mean, resampling **checkpoints** — never individual episodes."""
    draws = sorted(float(np.mean(rng.choice(values, len(values), replace=True)))
                   for _ in range(BOOTSTRAP))
    lo = draws[int(0.025 * BOOTSTRAP)]
    hi = draws[int(0.975 * BOOTSTRAP) - 1]
    return [round(lo, 4), round(hi, 4)]


def wilson(successes: int, total: int) -> list[float]:
    """Descriptive within-checkpoint interval. NOT an interval over training runs."""
    if total == 0:
        return [0.0, 0.0]
    z, p = 1.959964, successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denominator
    return [round(max(0.0, centre - spread), 4), round(min(1.0, centre + spread), 4)]


def summarise(rows: list[dict], rng) -> dict:
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
        "bootstrap_95ci_mean_rate_over_checkpoints": bootstrap_ci(rates, rng),
        "_descriptive_total": {
            "completions": sum(counts),
            "episodes": sum(r["n_episodes"] for r in rows),
            "_warning": "DESCRIPTIVE ONLY. These episodes are not independent training "
                        "runs; the inferential unit is the checkpoint.",
        },
        "checkpoints_with_at_least_one_completion": sum(1 for c in counts if c > 0),
        "checkpoints_with_all_25": sum(1 for c in counts if c == EPISODES),
        "checkpoints_with_zero": sum(1 for c in counts if c == 0),
        "mean_reward_over_checkpoints": round(st.mean(r["mean_reward"] for r in rows), 4),
        "mean_valid_action_rate": round(st.mean(r["valid_action_rate"] for r in rows), 4),
    }


def shape_diagnostic(rows_by_arm: dict) -> dict:
    """Where the eight checkpoints sit: near 0%, near 50%, near 100%, or in between."""
    def band(rate: float) -> str:
        if rate <= 0.10:
            return "near_0"
        if rate >= 0.90:
            return "near_100"
        if 0.35 <= rate <= 0.65:
            return "near_50"
        return "intermediate_other"

    out = {}
    for arm, rows in rows_by_arm.items():
        rates = [r["completion_rate"] for r in rows]
        bands = [band(r) for r in rates]
        out[arm] = {
            "rates": rates,
            "counts": [r["completion_count"] for r in rows],
            "near_0": bands.count("near_0"),
            "near_50": bands.count("near_50"),
            "near_100": bands.count("near_100"),
            "intermediate_other": bands.count("intermediate_other"),
            # The headline shape statistic: extreme (<=10% or >=90%) versus everything else.
            "extreme": sum(1 for b in bands if b in ("near_0", "near_100")),
            "intermediate": sum(1 for b in bands if b not in ("near_0", "near_100")),
        }
    return out


def seed1_archive_forensics() -> dict:
    """Read-only inspection of what the logs say about the bandit seed-1 failed returns.

    Nothing is rerun, repaired, retried or excluded. The question is narrow: do these
    failures look like an archive infrastructure defect that could contaminate the
    contextual-bandit result, or like ordinary failed return attempts?
    """
    log = REPO_ROOT / "logs" / "web_testing_agent.log"
    findings: dict = {
        "seed": 1, "arm": "contextual_bandit",
        "reported_failed_returns": 7,
        "log_file": str(log),
        "log_exists": log.exists(),
    }
    if not log.exists():
        findings["conclusion"] = "log file absent; cannot determine"
        return findings

    lines: list[str] = []
    with log.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "return failed" in line or "route" in line.lower() and "fail" in line.lower():
                lines.append(line.rstrip())
    findings["matching_log_lines"] = len(lines)
    findings["samples"] = lines[-12:]

    report = REPO_ROOT / "reports" / "contextual_bandit_pr050.json"
    if report.exists():
        data = json.loads(report.read_text(encoding="utf-8"))
        rows = {r["seed"]: r for r in data["results_per_seed"]["contextual_bandit"]}
        findings["archive_stats_per_seed"] = {
            str(s): rows[s]["archive"] for s in sorted(rows)
        }
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-json", type=Path,
                        default=REPO_ROOT / "reports" / "offline_eval_25ep.json")
    parser.add_argument("--out-md", type=Path,
                        default=REPO_ROOT / "reports" / "offline_eval_25ep.md")
    parser.add_argument("--out-csv", type=Path,
                        default=REPO_ROOT / "reports" / "offline_eval_25ep.csv")
    args = parser.parse_args()

    # A fresh extractor must match a trained one, or re-evaluation is not protocol-faithful.
    from web_testing_agent.envs.types import ActionSpec, ActionType
    probe_specs = [ActionSpec(index=0, action_type=ActionType.CLICK,
                              element_id="Place order", description="d")]
    aged = ActionFeatureExtractor()
    for _ in range(50):
        aged.start_episode()
        aged.observe_action_set(probe_specs, "http://x/order-4.html")
    assert np.array_equal(ActionFeatureExtractor().encode(probe_specs, "k", set()),
                          aged.encode(probe_specs, "k", set())), \
        "a fresh ActionFeatureExtractor no longer matches a trained one"

    manifest = []
    for arm, (directory, _cls) in ARMS.items():
        for seed in SEEDS:
            path = directory / f"{arm}_seed{seed}_final.zip"
            if not path.exists():
                raise SystemExit(f"STOP: missing checkpoint {path}")
            manifest.append({"arm": arm, "seed": seed, "path": str(path),
                             "sha256_before": sha256(path),
                             "bytes": path.stat().st_size})
    logger.info("evaluating {} checkpoints x {} episodes", len(manifest), EPISODES)

    started = time.monotonic()
    encoders = default_encoders()
    rows_by_arm: dict[str, list[dict]] = {arm: [] for arm in ARMS}
    errors: list[dict] = []
    with serve_directory(SITE) as origin:
        base_url = f"{origin}/index.html"
        for entry in manifest:
            arm, seed = entry["arm"], entry["seed"]
            cls = ARMS[arm][1]
            logger.info("=== {} seed {} ===", arm, seed)
            try:
                model = cls.load(entry["path"], device="cpu")
                row = evaluate_checkpoint(base_url, arm, seed, model, encoders)
                row["checkpoint"] = entry["path"]
                row["errors"] = None
                rows_by_arm[arm].append(row)
                logger.info("{} seed {}: {}/{} completions", arm, seed,
                            row["completion_count"], EPISODES)
            except Exception as exc:  # noqa: BLE001 - a failure must stop, not be hidden
                errors.append({"arm": arm, "seed": seed, "error": repr(exc)})
                logger.error("{} seed {} FAILED: {}", arm, seed, exc)
    elapsed = time.monotonic() - started

    if errors:
        raise SystemExit(f"STOP: {len(errors)} checkpoint(s) could not be evaluated: {errors}")

    for entry in manifest:
        entry["sha256_after"] = sha256(Path(entry["path"]))
        entry["unmodified"] = entry["sha256_after"] == entry["sha256_before"]

    rng = np.random.default_rng(BOOTSTRAP_SEED)
    summaries = {arm: summarise(rows, rng) for arm, rows in rows_by_arm.items()}
    for arm in summaries:
        summaries[arm]["five_episode_reference"] = FIVE_EPISODE[arm]
        summaries[arm]["five_episode_rates"] = [round(c / 5, 4) for c in FIVE_EPISODE[arm]]

    payload = {
        "_purpose": "Does the 5-episode completion-distribution difference between AC-DQN "
                    "and the contextual bandit survive 25 episodes of the same checkpoints?",
        "_not_a_training_experiment": "No training occurred. The only protocol change from "
                                      "the original runs is eval_episodes 5 -> 25.",
        "_inferential_unit": "the checkpoint. 25 episodes from one policy are correlated "
                             "samples of that policy, not independent training runs.",
        "protocol": {
            "episodes_per_checkpoint": EPISODES,
            "eval_epsilon": EVAL_EPSILON,
            "episode_steps": EPISODE_STEPS,
            "start": "landing page (index.html), no setup_actions",
            "archive": "disabled at evaluation for both arms",
            "encoders": "hash (default_encoders)",
            "judge": "disabled (NullRewardModel, as in the original runs)",
            "checkpoint_selection": "FINAL, matching the original experiments' primary result",
            "seed_scheme": "reused from run_rollout: env.reset(seed=training_seed + "
                           "episode_index); TrainedPolicy epsilon RNG seeded once with "
                           "training_seed. Identical for both arms.",
            "action_feature_extractor": "fresh per checkpoint; verified bit-identical to a "
                                        "trained one because record_taken() is never called",
        },
        "git": {
            "head": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                   text=True, cwd=REPO_ROOT).stdout.strip(),
            "branch": subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                     capture_output=True, text=True,
                                     cwd=REPO_ROOT).stdout.strip(),
        },
        "checkpoints": manifest,
        "all_checkpoints_unmodified": all(e["unmodified"] for e in manifest),
        "wall_clock_s": round(elapsed, 1),
        "total_episodes": sum(len(r["episodes"]) for rows in rows_by_arm.values() for r in rows),
        "per_checkpoint": {arm: [{k: v for k, v in row.items() if k != "episodes"}
                                 for row in rows] for arm, rows in rows_by_arm.items()},
        "per_episode": [ep for rows in rows_by_arm.values() for row in rows
                        for ep in row["episodes"]],
        "within_checkpoint_wilson_intervals": {
            arm: {str(r["seed"]): wilson(r["completion_count"], r["n_episodes"])
                  for r in rows} for arm, rows in rows_by_arm.items()},
        "arm_summaries": summaries,
        "distribution_shape": shape_diagnostic(rows_by_arm),
        "seed1_archive_forensics": seed1_archive_forensics(),
        "errors": errors,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with args.out_csv.open("w", newline="", encoding="utf-8") as handle:
        fields = ["arm", "seed", "checkpoint", "completion_count", "completion_rate",
                  "review_count", "review_rate", "s1_count", "s1_rate", "mean_reward",
                  "median_reward", "std_reward", "min_reward", "max_reward", "mean_depth",
                  "max_depth", "unique_states_mean", "valid_action_rate", "errors"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for rows in rows_by_arm.values():
            for row in rows:
                writer.writerow({k: row.get(k) for k in fields})

    args.out_md.write_text(render_markdown(payload), encoding="utf-8")
    print_console(payload)
    print(f"\nWrote {args.out_json}\nWrote {args.out_md}\nWrote {args.out_csv}")


def print_console(payload: dict) -> None:
    print("=" * 78)
    print(f"OFFLINE RE-EVALUATION AT {EPISODES} EPISODES  (no training)")
    print("=" * 78)
    print(f"checkpoints evaluated: {len(payload['checkpoints'])}   "
          f"total episodes: {payload['total_episodes']}   "
          f"all unmodified: {payload['all_checkpoints_unmodified']}   "
          f"wall clock: {payload['wall_clock_s'] / 60:.1f} min\n")
    for arm, summary in payload["arm_summaries"].items():
        print(f"  {arm}")
        print(f"    25-ep counts : {summary['completion_counts_per_seed']}")
        print(f"     5-ep counts : {summary['five_episode_reference']}  (historical)")
        print(f"    mean rate {summary['mean_checkpoint_completion_rate']:.3f}  "
              f"median {summary['median_checkpoint_completion_rate']:.3f}  "
              f"sd {summary['sd_across_checkpoints']:.3f}  "
              f"bootstrap 95% CI {summary['bootstrap_95ci_mean_rate_over_checkpoints']}")
        print(f"    >=1 completion {summary['checkpoints_with_at_least_one_completion']}/8   "
              f"all-25 {summary['checkpoints_with_all_25']}/8   "
              f"zero {summary['checkpoints_with_zero']}/8")
    shape = payload["distribution_shape"]
    print("\n  distribution shape (extreme = rate <=10% or >=90%)")
    for arm, block in shape.items():
        print(f"    {arm:<20} extreme {block['extreme']}/8  intermediate "
              f"{block['intermediate']}/8   near0 {block['near_0']} "
              f"near50 {block['near_50']} near100 {block['near_100']}")


def render_markdown(payload: dict) -> str:
    lines: list[str] = []
    add = lines.append
    protocol = payload["protocol"]
    add("# Offline re-evaluation at 25 episodes\n")
    add("## 1. Objective\n")
    add("Determine whether the completion-distribution difference seen at 5 evaluation "
        "episodes between AC-DQN and the contextual bandit survives a five-fold longer "
        "evaluation of the **same trained checkpoints**. No training was performed.\n")
    add("## 2. Evaluation protocol\n")
    for key, value in protocol.items():
        add(f"- **{key}**: {value}")
    add(f"\nAll {len(payload['checkpoints'])} checkpoints verified byte-identical after "
        f"evaluation: `{payload['all_checkpoints_unmodified']}`. "
        f"Wall clock {payload['wall_clock_s'] / 60:.1f} min, "
        f"{payload['total_episodes']} episodes total.\n")

    add("## 3. Checkpoint table\n")
    add("| arm | seed | completions /25 | rate | review | S1 | mean reward | mean depth | "
        "max depth | uniq states | valid% |")
    add("|---|---|---|---|---|---|---|---|---|---|---|")
    for arm, rows in payload["per_checkpoint"].items():
        for r in rows:
            add(f"| {arm} | {r['seed']} | **{r['completion_count']}** | "
                f"{r['completion_rate']:.2f} | {r['review_count']} | {r['s1_count']} | "
                f"{r['mean_reward']:+.2f} | {r['mean_depth']:.2f} | {r['max_depth']} | "
                f"{r['unique_states_mean']:.1f} | {r['valid_action_rate'] * 100:.0f} |")
    add("")

    add("## 4. Per-arm summary\n")
    add("| arm | counts /25 | mean rate | median | sd | bootstrap 95% CI | >=1 | all-25 | zero |")
    add("|---|---|---|---|---|---|---|---|---|")
    for arm, s in payload["arm_summaries"].items():
        add(f"| {arm} | `{s['completion_counts_per_seed']}` | "
            f"{s['mean_checkpoint_completion_rate']:.3f} | "
            f"{s['median_checkpoint_completion_rate']:.3f} | {s['sd_across_checkpoints']:.3f} | "
            f"{s['bootstrap_95ci_mean_rate_over_checkpoints']} | "
            f"{s['checkpoints_with_at_least_one_completion']}/8 | "
            f"{s['checkpoints_with_all_25']}/8 | {s['checkpoints_with_zero']}/8 |")
    add("\nThe bootstrap resamples **checkpoints**, not episodes. Pooled episode totals are "
        "reported in the JSON as descriptive only.\n")

    add("## 5. Completion-rate distribution\n")
    add("| arm | near 0% | near 50% | near 100% | other intermediate | **extreme** | "
        "**intermediate** |")
    add("|---|---|---|---|---|---|---|")
    for arm, b in payload["distribution_shape"].items():
        add(f"| {arm} | {b['near_0']} | {b['near_50']} | {b['near_100']} | "
            f"{b['intermediate_other']} | **{b['extreme']}/8** | **{b['intermediate']}/8** |")
    add("")

    add("## 6. 5-episode vs 25-episode (descriptive)\n")
    add("| arm | 5-episode counts | 5-ep rates | 25-episode counts | 25-ep rates |")
    add("|---|---|---|---|---|")
    for arm, s in payload["arm_summaries"].items():
        add(f"| {arm} | `{s['five_episode_reference']}` | `{s['five_episode_rates']}` | "
            f"`{s['completion_counts_per_seed']}` | `{s['completion_rates_per_seed']}` |")
    add("")

    add("## 7. Seed-1 archive forensic inspection\n")
    f = payload["seed1_archive_forensics"]
    add(f"- log file present: `{f['log_exists']}`")
    add(f"- matching log lines found: {f.get('matching_log_lines', 'n/a')}")
    add(f"- reported failed returns for contextual_bandit seed 1: {f['reported_failed_returns']}\n")

    add("## 8. Interpretation\n")
    add("_See the accompanying analysis; the numbers above are the evidence._\n")
    add("## 9. Limitations\n")
    add("- The experimental unit is the checkpoint (n=8 per arm); 25 episodes deepen each "
        "estimate but do not add training runs.")
    add("- Episode-to-episode variation comes almost entirely from the policy's epsilon "
        "stream; the fixture is static, so the env seed does little.")
    add("- Per-episode seeds overlap across training seeds (seed k uses k..k+24). This is "
        "the original experiment's scheme, reused unchanged and identical across arms.")
    add("- Single fixture, single budget. Nothing here generalises to another application.\n")
    add("## 10. Exact command\n")
    add("```bash\npython scripts/offline_eval_25ep.py\n```\n")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
