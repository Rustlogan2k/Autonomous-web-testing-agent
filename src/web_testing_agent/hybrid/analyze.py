"""Analysis of a hybrid-tester evaluation (and of development runs, with the same code).

Uses the primary experiment's statistics unchanged (`evaluation.survival`): time to the first
verified seeded fault, right-censored at the budget; per-application restricted mean time with
seeds averaged; the median over applications of paired differences with an application
bootstrap; the stratified log-rank test. The application is the unit of inference.

Reported separately, never pooled with the seeded-fault metrics: the spec-less oracle — how
many seeded faults the generic invariants alone flagged (a reproduced candidate attributed to
a fault by its per-fault control) and how many reproduced candidates appeared on clean builds.

    python -m web_testing_agent.hybrid.analyze --runs runs/v2-eval --out reports/v2_evaluation
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from ..evaluation.survival import (
    Run,
    discovery_rate,
    paired_fold_difference,
    restricted_mean,
    stratified_logrank,
)

PRIMARY = ("hybrid_bandit", "v1_dqn")
SECONDARY = (
    ("hybrid_bandit", "search_S"),
    ("hybrid_bandit", "random"),
    ("hybrid_bandit", "hybrid_nolearn"),
    ("hybrid_bandit_prior", "hybrid_bandit"),
)
EXPLORATORY = (("hybrid_bandit_llm", "hybrid_bandit"),)

#: The comparisons of each pre-registered protocol: (primary, secondary, exploratory).
#: "draft" is the 2026-10-06 draft; "freeze-2026-10-08" is the evaluation the owner chose to
#: run on the current validation set (docs/methodology/hybrid-v2-evaluation-preregistration.md).
PROTOCOLS: dict[str, tuple] = {
    "draft": (PRIMARY, SECONDARY, EXPLORATORY),
    "freeze-2026-10-08": (
        ("hybrid_nolearn", "random"),
        (
            ("hybrid_bandit", "hybrid_nolearn"),
            ("hybrid_bandit", "random"),
            ("hybrid_nolearn", "v1_dqn_pretrained"),
        ),
        (),
    ),
}


def load(run_dirs: list[Path]) -> list[dict]:
    records = []
    for directory in run_dirs:
        for path in sorted((Path(directory) / "results").glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("status") == "ok":
                records.append(record)
    return records


def verdict(median: float, low: float, high: float) -> str:
    """Pre-registered rule: lower time is better; the interval must exclude 0."""
    if median < 0 and high < 0:
        return "supported"
    if median > 0 and low > 0:
        return "refuted"
    return "inconclusive"


def _comparison(runs: list[Run], treatment: str, control: str, budget: int) -> dict:
    arms = {r.arm for r in runs}
    if treatment not in arms or control not in arms:
        return {"treatment": treatment, "control": control, "available": False}
    paired = paired_fold_difference(runs, treatment, control, budget)
    return {
        "verdict": verdict(paired.median, paired.ci_low, paired.ci_high),
        "treatment": treatment,
        "control": control,
        "available": True,
        "median_difference": paired.median,
        "ci": [paired.ci_low, paired.ci_high],
        "apps_favouring_treatment": paired.folds_favouring_treatment,
        "apps": paired.n_folds,
        "per_app": paired.per_fold,
        "logrank": stratified_logrank(runs, treatment, control),
    }


def analyze(records: list[dict], protocol: str = "draft") -> dict:
    primary, secondary, exploratory = PROTOCOLS[protocol]
    buggy = [r for r in records if r["job"].get("build", "buggy") == "buggy"]
    clean = [r for r in records if r["job"].get("build", "buggy") == "clean"]
    budget = max((r["hunt"]["budget"] for r in buggy), default=2000)
    runs = [
        Run(
            fold=r["job"]["app"],
            arm=r["job"]["arm"],
            seed=r["job"]["seed"],
            time=float(r["hunt"]["time_to_first_verified"]),
            censored=bool(r["hunt"]["censored"]),
        )
        for r in buggy
    ]

    per_arm: dict[str, dict] = {}
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for record in buggy:
        by_arm[record["job"]["arm"]].append(record)
    for arm, rows in sorted(by_arm.items()):
        arm_runs = [run for run in runs if run.arm == arm]
        distinct: dict[str, set[str]] = defaultdict(set)
        per_run_faults = []
        flagged: dict[str, set[str]] = defaultdict(set)
        candidates = reproduced = on_clean = 0
        returns = failed_returns = 0
        for record in rows:
            faults = {
                f["fault_id"] for f in record["hunt"]["oracle"]["faults"] if f.get("verified_at")
            }
            distinct[record["job"]["app"]] |= faults
            per_run_faults.append(len(faults))
            for candidate in record.get("invariants", {}).get("candidates", []):
                candidates += 1
                if candidate.get("reproduced") == 2:
                    reproduced += 1
                attribution = candidate.get("attribution") or {}
                on_clean += bool(attribution.get("on_clean"))
                flagged[record["job"]["app"]] |= set(attribution.get("faults", []))
            policy = record.get("policy") or {}
            returns += policy.get("returns", 0)
            failed_returns += policy.get("failed_returns", 0)
        per_arm[arm] = {
            "runs": len(rows),
            "discovery_rate": discovery_rate(arm_runs),
            "restricted_mean_time": restricted_mean([r.time for r in arm_runs], budget),
            "mean_faults_verified_per_run": sum(per_run_faults) / max(len(per_run_faults), 1),
            "distinct_faults_verified": sum(len(v) for v in distinct.values()),
            "distinct_faults_by_app": {k: sorted(v) for k, v in sorted(distinct.items())},
            "specless": {
                "candidates": candidates,
                "reproduced": reproduced,
                "reproduced_on_clean_build_too": on_clean,
                "seeded_faults_flagged": sum(len(v) for v in flagged.values()),
                "flagged_by_app": {k: sorted(v) for k, v in sorted(flagged.items())},
            },
            "returns": returns,
            "failed_returns": failed_returns,
        }

    clean_controls: dict[str, dict] = {}
    for record in clean:
        arm = record["job"]["arm"]
        row = clean_controls.setdefault(
            arm, {"jobs": 0, "verified_seeded": 0, "reproduced_invariant_candidates": 0}
        )
        row["jobs"] += 1
        row["verified_seeded"] += int(record["hunt"]["oracle"]["verified"])
        row["reproduced_invariant_candidates"] += sum(
            c.get("reproduced") == 2 for c in record.get("invariants", {}).get("candidates", [])
        )

    return {
        "budget": budget,
        "apps": sorted({r.fold for r in runs}),
        "per_arm": per_arm,
        "protocol": protocol,
        "primary": _comparison(runs, *primary, budget),
        "secondary": [_comparison(runs, t, c, budget) for t, c in secondary],
        "exploratory": [_comparison(runs, t, c, budget) for t, c in exploratory],
        "clean_build_controls": clean_controls,
    }


def render(result: dict) -> str:
    lines = [
        "# Hybrid tester evaluation — numbers",
        "",
        f"Budget {result['budget']} interactions; applications: {', '.join(result['apps'])}.",
        "",
        "| arm | runs | discovery | RMST | faults/run | distinct faults | spec-less flagged |"
        " returns failed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for arm, row in result["per_arm"].items():
        cells = [
            arm,
            row["runs"],
            f"{row['discovery_rate']:.2f}",
            f"{row['restricted_mean_time']:.0f}",
            f"{row['mean_faults_verified_per_run']:.2f}",
            row["distinct_faults_verified"],
            row["specless"]["seeded_faults_flagged"],
            f"{row['failed_returns']}/{row['returns']}",
        ]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines += [
        "",
        "| comparison | median Δ RMST | 95% CI | apps favouring | log-rank p | verdict |",
        "|---|---|---|---|---|---|",
    ]
    for label, rows in (
        ("primary", [result["primary"]]),
        ("secondary", result["secondary"]),
        ("exploratory", result["exploratory"]),
    ):
        for c in rows:
            if not c.get("available"):
                lines.append(f"| {label}: {c['treatment']} − {c['control']} | n/a | | | | |")
                continue
            p = c["logrank"].get("p_value")
            cells = [
                f"{label}: {c['treatment']} − {c['control']}",
                f"{c['median_difference']:+.0f}",
                f"[{c['ci'][0]:+.0f}, {c['ci'][1]:+.0f}]",
                f"{c['apps_favouring_treatment']}/{c['apps']}",
                p if p is not None else "n/a",
                c["verdict"],
            ]
            lines.append("| " + " | ".join(str(x) for x in cells) + " |")
    lines += ["", "Clean-build controls: " + json.dumps(result["clean_build_controls"])]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--protocol", choices=sorted(PROTOCOLS), default="draft")
    arguments = parser.parse_args(argv)
    result = analyze(load(arguments.runs), arguments.protocol)
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.with_suffix(".json").write_text(
        json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
    )
    arguments.out.with_suffix(".md").write_text(render(result), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
