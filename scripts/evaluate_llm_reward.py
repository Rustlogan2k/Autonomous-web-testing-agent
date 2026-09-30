"""Validate the dynamic LLM reward against deterministic ground truth.

    python scripts/evaluate_llm_reward.py --out reports/llm_reward_validation
    python scripts/evaluate_llm_reward.py --logs runs/transfer_final --out ...

The progress potential `Phi` is supposed to be high where functional faults become
observable and low on pages that have nothing to do with a task. The benchmark knows where
that is -- every seeded fault has a reachability witness -- so the claim can be checked
without trusting the model:

**Part A, on the applications (always run).** For each application:

1. replay every fault's witness on the faulty build and score `Phi` at every step;
2. score every state a uniform-random walk reaches (the "ordinary state" population);
3. report
   * Spearman correlation between position along the witness and `Phi` (does it rise as
     the task progresses?),
   * `Phi` at the fault-observable step, and its percentile among random-walk states,
   * AUROC of `Phi` for "a witness's fault-observable state" versus random-walk states.

**Part B, from an experiment's reward logs (when `--logs` is given).** Per step: mean
shaping on steps that reached a new state versus not, on repeated actions versus not, on
steps that changed the page versus not. Per run: correlation of the run's total shaping
with whether (and when) it verified a fault.

No part of this feeds back into the agent or the oracle. It measures the reward.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from web_testing_agent.benchmark import EnvSettings, discover, make_env, serve_app  # noqa: E402
from web_testing_agent.envs.types import MAX_ACTIONS  # noqa: E402
from web_testing_agent.evaluation import ScriptedPolicy  # noqa: E402
from web_testing_agent.evaluation.rollout import RandomPolicy  # noqa: E402
from web_testing_agent.oracle import build_trajectory, observed_record, verify_fault  # noqa: E402
from web_testing_agent.reward.progress import ProgressScorer  # noqa: E402


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    from scipy.stats import spearmanr

    return float(spearmanr(x, y).statistic)


def auroc(positives: list[float], negatives: list[float]) -> float | None:
    if not positives or not negatives:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return float(wins / (len(positives) * len(negatives)))


def score_page(scorer: ProgressScorer, info: dict, context: str) -> float | None:
    page = info.get("page") or {}
    judgment = scorer.potential(page.get("url", ""), page.get("html", ""), context=context,
                                visible_text=page.get("visible_text"))
    return judgment.value if judgment is not None else None


def part_a(scorer: ProgressScorer, random_steps: int, settings: EnvSettings) -> dict:
    results = {}
    for app in discover():
        context = app.manifest.name
        witness_rows, observable, ordinary = [], [], []
        with serve_app(app, builds=("buggy",)) as urls:
            for fault in app.manifest.faults:
                env = make_env(urls["buggy"], settings, max_steps=120)
                policy = ScriptedPolicy(list(fault.trigger_trace))
                observation, info = env.reset()
                records, phis = [observed_record(info)], [score_page(scorer, info, context)]
                while not policy.finished:
                    available = list(info.get("action_specs") or [])
                    observation, _r, _te, _tr, info = env.step(policy.act(observation, info))
                    records.append(observed_record(info, info.get("action_spec"), available))
                    phis.append(score_page(scorer, info, context))
                env.close()
                verdict = verify_fault(fault, app.manifest, build_trajectory(records))
                step = verdict.violation_step if verdict.violated else len(phis) - 1
                valid = [(i, p) for i, p in enumerate(phis) if p is not None]
                witness_rows.append({
                    "fault_id": fault.fault_id, "phi_along_witness": phis,
                    "spearman_position_vs_phi": spearman([i for i, _ in valid], [p for _, p in valid]),
                    "phi_at_fault_observable_step": phis[step] if 0 <= step < len(phis) else None,
                    "phi_at_start": phis[0],
                })
                if 0 <= step < len(phis) and phis[step] is not None:
                    observable.append(phis[step])

            env = make_env(urls["buggy"], settings)
            policy = RandomPolicy(MAX_ACTIONS, seed=0, valid_only=True)
            seen: set[str] = set()
            observation, info = env.reset()
            for _ in range(random_steps):
                key = info.get("state_key", "")
                if key not in seen:
                    seen.add(key)
                    value = score_page(scorer, info, context)
                    if value is not None:
                        ordinary.append(value)
                observation, _r, terminated, truncated, info = env.step(policy.act(observation, info))
                if terminated or truncated:
                    observation, info = env.reset()
            env.close()

        percentiles = [float(np.mean([o <= v for o in ordinary])) for v in observable] if ordinary else []
        results[app.app_id] = {
            "witnesses": witness_rows,
            "random_walk_states_scored": len(ordinary),
            "random_walk_phi_mean": round(float(np.mean(ordinary)), 3) if ordinary else None,
            "fault_observable_phi_mean": round(float(np.mean(observable)), 3) if observable else None,
            "fault_observable_percentile_among_random_states": [round(p, 3) for p in percentiles],
            "auroc_fault_observable_vs_random_states": auroc(observable, ordinary),
        }
        print(f"{app.app_id}: observable phi {results[app.app_id]['fault_observable_phi_mean']}, "
              f"random mean {results[app.app_id]['random_walk_phi_mean']}, AUROC "
              f"{results[app.app_id]['auroc_fault_observable_vs_random_states']}", flush=True)
    return results


def part_b(directory: Path) -> dict:
    by_flag: dict[str, dict[bool, list[float]]] = defaultdict(lambda: {True: [], False: []})
    per_run = []
    for log in sorted((directory / "reward_logs").glob("hunt__*.jsonl")):
        result_path = directory / "results" / (log.stem + ".json")
        if not result_path.is_file():
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("status") != "ok":
            continue
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
        rows = [r for r in rows if "_run" not in r]
        shaped = [r for r in rows if r.get("phi_after") is not None and r.get("phi_before") is not None]
        if not shaped:
            continue
        for row in shaped:
            by_flag["novel_state"][bool(row.get("novel_state"))].append(row["llm_shaping"])
            by_flag["repeated_action"][bool(row.get("repeat_count"))].append(row["llm_shaping"])
            by_flag["page_changed"][bool(row.get("state_changed"))].append(row["llm_shaping"])
            by_flag["action_failed"][not row.get("exec_success", True)].append(row["llm_shaping"])
        hunt = result["hunt"]
        per_run.append({
            "job": result["job"]["job_id"], "arm": result["job"]["arm"],
            "total_shaping": float(sum(r["llm_shaping"] for r in shaped)),
            "max_phi": float(max(r["phi_after"] for r in shaped)),
            "verified": not hunt["censored"], "time": hunt["time_to_first_verified"],
        })
    flags = {name: {"mean_when_true": round(float(np.mean(v[True])), 4) if v[True] else None,
                    "mean_when_false": round(float(np.mean(v[False])), 4) if v[False] else None,
                    "n_true": len(v[True]), "n_false": len(v[False])}
             for name, v in by_flag.items()}
    verified = [r for r in per_run if r["verified"]]
    censored = [r for r in per_run if not r["verified"]]
    return {
        "per_step_shaping_by_condition": flags,
        "runs": len(per_run),
        "max_phi_auroc_verified_vs_censored": auroc([r["max_phi"] for r in verified],
                                                     [r["max_phi"] for r in censored]),
        "spearman_total_shaping_vs_time_to_fault": spearman(
            [r["total_shaping"] for r in per_run], [r["time"] for r in per_run]),
        "per_run": per_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--random-steps", type=int, default=300)
    parser.add_argument("--logs", type=Path, default=None, help="an experiment directory")
    parser.add_argument("--skip-apps", action="store_true", help="only run part B")
    args = parser.parse_args()
    scorer = ProgressScorer()
    scorer.preflight()
    out: dict = {"scorer": scorer.config()}
    if not args.skip_apps:
        out["part_a_applications"] = part_a(scorer, args.random_steps,
                                            EnvSettings(max_steps=40, settle_quiet_ms=80))
    if args.logs is not None:
        out["part_b_experiment_logs"] = part_b(args.logs)
    out["scorer_stats"] = scorer.stats.to_dict()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"-> {args.out.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
