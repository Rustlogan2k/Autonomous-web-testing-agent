"""Summary of tuning round v3 and the pre-freeze check, as a committed result file.

Reads `runs/v3-tune/results` and `runs/v3-check/results` (development data, local only) and
writes `reports/tuning_v3.json`: per arm, runs that verified a fault, restricted mean time,
distinct faults (and crash-type faults) verified, unique failures and client-side coverage;
and each arm against `hybrid_nolearn` on matched (application, seed) pairs, the median over
applications of the per-application mean difference with a bootstrap interval.

    python scripts/summarize_tuning.py
"""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUDGET = 2000
BASELINE = "hybrid_nolearn"


def _crash_ids() -> set[str]:
    ids = set()
    for manifest in (REPO / "e2e_suite" / "apps").glob("*/app.json"):
        for fault in json.loads(manifest.read_text(encoding="utf-8"))["faults"]:
            if "console_or_request_error" in json.dumps(fault):
                ids.add(fault["fault_id"])
    return ids


def _runs() -> list[dict]:
    out = []
    for round_dir in ("v3-tune", "v3-check"):
        for path in sorted((REPO / "runs" / round_dir / "results").glob("hunt__*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("status") != "ok" or record["job"]["build"] != "buggy":
                continue
            oracle = record["hunt"]["oracle"]
            out.append({
                "arm": record["job"]["arm"], "app": record["job"]["app"],
                "seed": record["job"]["seed"],
                "T": BUDGET if record["hunt"]["censored"] else record["hunt"]["time_to_first_verified"],
                "found": not record["hunt"]["censored"],
                "faults": [f["fault_id"] for f in oracle.get("faults", []) if f.get("verified_at")],
                "seeded": [f["fault_id"] for f in oracle.get("faults", [])],
                "failures": record.get("failures", {}).get("unique_failures", 0),
                "coverage": (record.get("coverage") or {}).get("js_coverage"),
            })
    return out


def main() -> None:
    runs = _runs()
    crash = _crash_ids()
    seeded = {fid for r in runs for fid in r["seeded"]}
    by_arm = defaultdict(list)
    for r in runs:
        by_arm[r["arm"]].append(r)
    arms = []
    for arm, rs in sorted(by_arm.items()):
        distinct = set().union(*[set(r["faults"]) for r in rs])
        covs = [r["coverage"] for r in rs if r["coverage"] is not None]
        arms.append({
            "arm": arm, "runs": len(rs), "found": sum(r["found"] for r in rs),
            "rmst": round(statistics.mean(r["T"] for r in rs), 1),
            "distinct_faults": len(distinct), "distinct_crash_faults": len(distinct & crash),
            "unique_failures_per_run": round(statistics.mean(r["failures"] for r in rs), 2),
            "js_coverage": round(statistics.mean(covs), 3) if covs else None,
        })
    base = {(r["app"], r["seed"]): r for r in by_arm[BASELINE]}
    rng = random.Random(0)
    for entry in arms:
        if entry["arm"] == BASELINE:
            continue
        per_app = defaultdict(list)
        for r in by_arm[entry["arm"]]:
            if (r["app"], r["seed"]) in base:
                per_app[r["app"]].append(r["T"] - base[(r["app"], r["seed"])]["T"])
        diffs = [statistics.mean(v) for v in per_app.values()]
        boots = sorted(statistics.median(rng.choice(diffs) for _ in diffs) for _ in range(5000))
        entry["vs_baseline"] = {"median": statistics.median(diffs),
                                "interval": [boots[124], boots[4874]],
                                "apps_faster": sum(d < 0 for d in diffs),
                                "apps_slower": sum(d > 0 for d in diffs)}
    arms.sort(key=lambda a: a["rmst"])
    out = {"budget": BUDGET, "baseline": BASELINE, "apps": sorted({r["app"] for r in runs}),
           "seeded_faults": len(seeded), "crash_faults": len(crash & seeded), "arms": arms,
           "frozen": "hybrid_final = dev_v3_combined (tag v3-freeze)"}
    path = REPO / "reports" / "tuning_v3.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {path} ({len(runs)} runs, {len(arms)} arms)")


if __name__ == "__main__":
    main()
