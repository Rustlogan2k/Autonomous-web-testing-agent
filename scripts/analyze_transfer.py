"""Turn a transfer-experiment directory into the pre-registered analysis and a readable report.

    python scripts/analyze_transfer.py runs/transfer_final --out reports/transfer_final

Reads every `results/*.json` the runner wrote and produces `<out>.json` and `<out>.md`:

* **Primary** (pre-registration section 7): per fold, the paired difference in mean
  time-to-first-verified-fault between two arms, seeds averaged within the fold; across
  folds, its median with a bootstrap interval that resamples folds.
* **Censoring-aware secondary**: the fold-stratified log-rank test, and pooled Kaplan-Meier
  discovery curves (descriptive only).
* **Supporting metrics** per arm: verified-fault discovery rate, faults reached /
  discovered / verified, candidates rejected by the clean-build control, unique states,
  action efficiency, minimized trace length, verification cost.
* **False positives**: the clean-build control runs.
* **The top-up rule** (section 8): which folds have `|delta| < 200` for the pre-registered
  comparison and would need seeds 3-5.

Runs that failed for infrastructure reasons are listed and excluded, as section 11 allows;
nothing is excluded for its outcome.
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

from web_testing_agent.evaluation.survival import (  # noqa: E402
    Run,
    arm_summary,
    paired_fold_difference,
    stratified_logrank,
)

#: Comparisons reported, as (treatment, control, why). The first is the owner-directed
#: primary tester against its from-scratch twin; the pre-registered bandit comparison is
#: reported unchanged alongside it.
COMPARISONS: list[tuple[str, str, str]] = [
    ("dqn_pretrained", "dqn_scratch", "transfer: pretrained vs scratch, same learner (primary tester)"),
    ("bandit_pretrained", "bandit_scratch", "transfer: the pre-registered C vs B comparison"),
    ("dqn_pretrained", "search_S", "learned vs search with identical features"),
    ("dqn_pretrained", "search_A0", "learned vs the pre-registered search arm A"),
    ("dqn_pretrained", "random", "learned vs uniform random"),
    ("dqn_pretrained", "dqn_pretrained_nollm", "ablation: dynamic LLM reward on vs off"),
    ("dqn_pretrained", "dqn_zeroshot", "ablation: online adaptation on vs off"),
    ("dqn_scratch", "bandit_scratch", "ablation: bootstrapped vs myopic, from scratch"),
]

TOP_UP_THRESHOLD = 200.0  # pre-registration section 8: 10% of the 2,000-step budget


def load(directory: Path) -> tuple[list[dict], list[dict], dict]:
    ok, failed = [], []
    for path in sorted((directory / "results").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        (ok if record.get("status") == "ok" else failed).append(record)
    experiment = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
    return ok, failed, experiment


def hunt_rows(records: list[dict]) -> list[dict]:
    rows = []
    for record in records:
        job = record["job"]
        if job["kind"] != "hunt":
            continue
        hunt = record["hunt"]
        oracle = hunt["oracle"]
        minimized = [f["finding"]["minimization"]["minimized_length"]
                     for f in oracle["faults"] if f.get("finding")]
        original = [f["finding"]["minimization"]["original_length"]
                    for f in oracle["faults"] if f.get("finding")]
        rows.append({
            "job_id": job["job_id"], "fold": job["fold"], "arm": job["arm"], "seed": job["seed"],
            "build": job.get("build", "buggy"),
            "time": hunt["time_to_first_verified"], "censored": hunt["censored"],
            "seeded": oracle["seeded"], "reached": oracle["reached"],
            "discovered": oracle["discovered"], "verified": oracle["verified"],
            "candidates": oracle["candidates"], "rejections": oracle["rejections"],
            "cap_hit": oracle["cap_hit"],
            "unique_states": hunt["unique_states"], "steps": hunt["steps"],
            "state_changing_fraction": hunt["state_changing_actions"] / max(hunt["steps"], 1),
            "failed_action_fraction": hunt["failed_actions"] / max(hunt["steps"], 1),
            "minimized_lengths": minimized, "original_lengths": original,
            "verify_seconds": hunt["verify_seconds"], "explore_seconds": hunt["explore_seconds"],
            "commit": record["provenance"]["git"]["commit"],
            "dirty": record["provenance"]["git"]["dirty"],
        })
    return rows


def per_arm_metrics(rows: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["arm"]].append(row)
    out = {}
    for arm, items in sorted(grouped.items()):
        seeded = sum(r["seeded"] for r in items)
        rejections: dict[str, int] = defaultdict(int)
        for r in items:
            for kind, count in r["rejections"].items():
                rejections[kind] += count
        minimized = [m for r in items for m in r["minimized_lengths"]]
        original = [m for r in items for m in r["original_lengths"]]
        out[arm] = {
            "runs": len(items),
            "faults_reached_fraction": round(sum(r["reached"] for r in items) / seeded, 4),
            "faults_discovered_fraction": round(sum(r["discovered"] for r in items) / seeded, 4),
            "faults_verified_fraction": round(sum(r["verified"] for r in items) / seeded, 4),
            "candidates": sum(r["candidates"] for r in items),
            "candidates_rejected": dict(rejections),
            "mean_unique_states": round(float(np.mean([r["unique_states"] for r in items])), 1),
            "mean_state_changing_fraction": round(float(np.mean(
                [r["state_changing_fraction"] for r in items])), 3),
            "mean_failed_action_fraction": round(float(np.mean(
                [r["failed_action_fraction"] for r in items])), 3),
            "mean_minimized_trace_length": round(float(np.mean(minimized)), 2) if minimized else None,
            "mean_original_trace_length": round(float(np.mean(original)), 2) if original else None,
            "mean_verify_seconds": round(float(np.mean([r["verify_seconds"] for r in items])), 1),
            "runs_with_verification_cap_hit": sum(r["cap_hit"] for r in items),
        }
    return out


def analyse(directory: Path, dev_apps: list[str]) -> dict:
    records, failed, experiment = load(directory)
    config = experiment["config"]
    budget = float(config["budget"])
    rows = hunt_rows(records)
    buggy = [r for r in rows if r["build"] == "buggy"]
    clean = [r for r in rows if r["build"] == "clean"]
    runs = [Run(r["fold"], r["arm"], r["seed"], float(r["time"]), bool(r["censored"])) for r in buggy]
    folds = sorted({r.fold for r in runs})
    pristine = [f for f in folds if f not in dev_apps]
    arms = sorted({r.arm for r in runs})

    def comparisons_on(subset_folds: list[str]) -> list[dict]:
        subset = [r for r in runs if r.fold in subset_folds]
        out = []
        for treatment, control, why in COMPARISONS:
            if treatment not in arms or control not in arms:
                continue
            paired = paired_fold_difference(subset, treatment, control, budget).to_dict()
            paired["why"] = why
            paired["stratified_logrank"] = stratified_logrank(subset, treatment, control)
            out.append(paired)
        return out

    all_comparisons = comparisons_on(folds)
    top_up = {}
    for comparison in all_comparisons:
        if (comparison["treatment"], comparison["control"]) in (
                ("bandit_pretrained", "bandit_scratch"), ("dqn_pretrained", "dqn_scratch")):
            top_up[f"{comparison['treatment']} - {comparison['control']}"] = sorted(
                f for f, d in comparison["per_fold_difference"].items()
                if abs(d) < TOP_UP_THRESHOLD)

    per_fold_table = defaultdict(dict)
    for row in buggy:
        entry = per_fold_table[row["fold"]].setdefault(row["arm"], [])
        entry.append({"seed": row["seed"], "time": row["time"], "censored": row["censored"],
                      "verified": row["verified"]})

    commits = sorted({r["commit"] for r in rows})
    return {
        "experiment_dir": str(directory),
        "config": config,
        "commits": commits,
        "any_dirty_tree": any(r["dirty"] for r in rows),
        "budget": budget,
        "folds": folds,
        "development_folds": [f for f in folds if f in dev_apps],
        "pristine_held_out_folds": pristine,
        "arms": arms,
        "runs": len(buggy),
        "excluded_infrastructure_failures": [
            {"job": f["job"]["job_id"], "error": f.get("error")} for f in failed],
        "primary_and_secondary_all_folds": all_comparisons,
        "comparisons_pristine_folds_only": comparisons_on(pristine) if pristine else [],
        "top_up_rule_folds_needing_seeds_3_to_5": top_up,
        "arm_survival": arm_summary(runs, budget),
        "arm_metrics": per_arm_metrics(buggy),
        "false_positive_controls_clean_build": [
            {"fold": r["fold"], "arm": r["arm"], "seed": r["seed"],
             "raw_candidates_on_correct_build": r["candidates"],
             "verified_findings_on_correct_build": r["verified"]} for r in clean],
        "per_fold_runs": per_fold_table,
    }


def markdown(result: dict) -> str:
    lines = [
        "# Transfer experiment: leave-one-application-out",
        "",
        f"- Directory: `{result['experiment_dir']}`",
        f"- Commit(s): {', '.join(c[:10] for c in result['commits'])}"
        + (" — **a run was made from a dirty tree**" if result["any_dirty_tree"] else ""),
        f"- Budget: {int(result['budget'])} interactions per run; folds: {len(result['folds'])} "
        f"({', '.join(result['folds'])})",
        f"- Development folds (used to design the system, not transfer evidence): "
        f"{', '.join(result['development_folds']) or 'none'}",
        f"- Pristine held-out folds: {', '.join(result['pristine_held_out_folds']) or 'none'}",
        f"- Runs analysed: {result['runs']}; infrastructure exclusions: "
        f"{len(result['excluded_infrastructure_failures'])}",
        "",
        "## Time to first verified fault, per arm (pooled; descriptive)",
        "",
        "| arm | runs | verified runs | discovery rate | restricted mean time | median time (verified runs) |",
        "|---|---|---|---|---|---|",
    ]
    for arm, s in result["arm_survival"].items():
        lines.append(f"| {arm} | {s['runs']} | {s['verified_runs']} | {s['discovery_rate']:.2f} | "
                     f"{s['restricted_mean_time']:.0f} | {s['median_time_uncensored'] or '—'} |")

    def comparison_table(title: str, rows: list[dict]) -> None:
        lines.extend(["", f"## {title}", "",
                      "Negative = the first arm confirms a fault in fewer interactions. The "
                      "interval resamples folds (the unit of inference).", "",
                      "| comparison | why | median Δ | 95% CI | folds favouring | log-rank p |",
                      "|---|---|---|---|---|---|"])
        for c in rows:
            ci = c["bootstrap_95ci_median"]
            p = c["stratified_logrank"].get("p_value")
            lines.append(f"| {c['treatment']} − {c['control']} | {c['why']} | "
                         f"{c['median_difference']:+.0f} | [{ci[0]:+.0f}, {ci[1]:+.0f}] | "
                         f"{c['folds_favouring_treatment']}/{c['n_folds']} | "
                         f"{'—' if p is None else f'{p:.3g}'} |")

    comparison_table("Paired fold-level comparisons, all folds",
                     result["primary_and_secondary_all_folds"])
    if result["comparisons_pristine_folds_only"]:
        comparison_table("Paired fold-level comparisons, pristine held-out folds only",
                         result["comparisons_pristine_folds_only"])

    lines.extend(["", "## Supporting metrics per arm", "",
                  "| arm | reached | discovered | verified | candidates rejected | unique states | "
                  "state-changing actions | minimized trace |", "|---|---|---|---|---|---|---|---|"])
    for arm, m in result["arm_metrics"].items():
        rejected = sum(m["candidates_rejected"].values())
        lines.append(f"| {arm} | {m['faults_reached_fraction']:.2f} | "
                     f"{m['faults_discovered_fraction']:.2f} | {m['faults_verified_fraction']:.2f} | "
                     f"{rejected} | {m['mean_unique_states']} | {m['mean_state_changing_fraction']:.2f} | "
                     f"{m['mean_minimized_trace_length'] or '—'} |")

    lines.extend(["", "## False-positive controls (clean builds)", ""])
    if result["false_positive_controls_clean_build"]:
        lines.extend(["| fold | arm | raw assertion firings | verified findings |", "|---|---|---|---|"])
        for row in result["false_positive_controls_clean_build"]:
            lines.append(f"| {row['fold']} | {row['arm']} | {row['raw_candidates_on_correct_build']} | "
                         f"{row['verified_findings_on_correct_build']} |")
    else:
        lines.append("No clean-build control runs in this experiment.")

    lines.extend(["", "## Top-up rule (pre-registration §8)", ""])
    for comparison, folds in result["top_up_rule_folds_needing_seeds_3_to_5"].items():
        lines.append(f"- {comparison}: {', '.join(folds) if folds else 'no fold below 200 steps'}")
    if result["excluded_infrastructure_failures"]:
        lines.extend(["", "## Excluded runs (infrastructure failures only)", ""])
        for item in result["excluded_infrastructure_failures"]:
            lines.append(f"- `{item['job']}`: {item['error']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="output stem (.json and .md)")
    parser.add_argument("--dev-apps", nargs="*", default=["shop_catalog", "booking_desk"],
                        help="applications used to design the system")
    args = parser.parse_args()
    result = analyse(args.directory, args.dev_apps)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(result, indent=2, default=str),
                                             encoding="utf-8")
    args.out.with_suffix(".md").write_text(markdown(result), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
