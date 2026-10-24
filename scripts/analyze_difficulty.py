"""Two post-hoc analyses of the held-out evaluation (`runs/v2-eval`), written to
`reports/heldout_difficulty.json`.

1. **Discovery by fault difficulty.** Difficulty comes from each fault's declared shortest
   trigger path (`shortest_path` in the validation app manifests, written when the faults
   were designed, before any agent ran), never from the agents' own results:
   shallow <= 4 interactions, moderate 5-6, deep >= 7. Faults that need a reload or a
   second visit to observe (`observation_horizon` > 0) are reported as a second split.
2. **Candidate verification.** For each arm: candidate observations submitted to the
   oracle, how many were verified, and why the rest were rejected.

    python scripts/analyze_difficulty.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARMS = ("random", "v1_dqn_pretrained", "hybrid_nolearn", "hybrid_bandit")
SEEDS = 5


def bucket(steps: int) -> str:
    return "shallow (<=4)" if steps <= 4 else "moderate (5-6)" if steps <= 6 else "deep (>=7)"


def main() -> None:
    faults = {}
    for manifest in sorted((REPO / "validation_suite" / "apps").glob("*/app.json")):
        for fault in json.loads(manifest.read_text(encoding="utf-8"))["faults"]:
            faults[fault["fault_id"]] = {
                "steps": int(fault["shortest_path"]),
                "difficulty": bucket(int(fault["shortest_path"])),
                "needs_revisit": int(fault.get("observation_horizon") or 0) > 0,
                "class": fault["fault_class"],
            }
    hits = defaultdict(Counter)            # arm -> fault -> runs that verified it
    candidates = Counter()
    verified = Counter()
    rejections = defaultdict(Counter)
    for path in sorted((REPO / "runs" / "v2-eval" / "results").glob("hunt__*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        job = record["job"]
        if record.get("status") != "ok" or job.get("build") != "buggy":
            continue
        oracle = record["hunt"]["oracle"]
        arm = job["arm"]
        candidates[arm] += int(oracle.get("candidates", 0))
        for fault in oracle.get("faults", []):
            if fault.get("verified_at") is not None:
                hits[arm][fault["fault_id"]] += 1
                verified[arm] += 1
            for reason, count in (fault.get("rejections") or {}).items():
                rejections[arm][reason] += count

    groups = defaultdict(list)
    for fid, info in faults.items():
        groups[("difficulty", info["difficulty"])].append(fid)
        groups[("revisit", "needs reload or revisit" if info["needs_revisit"] else "same visit")].append(fid)
    by_group = {}
    for (axis, name), fids in sorted(groups.items()):
        by_group[f"{axis}:{name}"] = {
            "faults": len(fids),
            "arms": {arm: {
                "distinct_found": sum(1 for f in fids if hits[arm][f] > 0),
                "runs_verifying": sum(hits[arm][f] for f in fids),
                "of_runs": len(fids) * SEEDS,
            } for arm in ARMS},
        }
    out = {
        "source": "runs/v2-eval (held-out, frozen v2-eval-freeze); difficulty from manifests",
        "faults": faults,
        "by_group": by_group,
        "verification": {arm: {
            "candidates": candidates[arm], "verified": verified[arm],
            "rejected": candidates[arm] - verified[arm],
            "rejection_reasons": dict(rejections[arm]),
        } for arm in ARMS},
    }
    target = REPO / "reports" / "heldout_difficulty.json"
    target.write_text(json.dumps(out, indent=2), encoding="utf-8")
    for key, group in by_group.items():
        row = "  ".join(f"{a}={g['distinct_found']}/{group['faults']} ({g['runs_verifying']}/{g['of_runs']})"
                        for a, g in group["arms"].items())
        print(f"{key:40s} {row}")
    for arm, v in out["verification"].items():
        print(arm, v)


if __name__ == "__main__":
    main()
