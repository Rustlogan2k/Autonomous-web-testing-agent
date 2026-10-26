"""Bandit tuning on the development suite (PROJECT_CONTEXT 0.8, bandit follow-up): applies the
selection rule fixed before the runs.

Rule: among the tune_bandit_* arms, the most distinct verified faults (union over apps and
seeds); ties broken by lower restricted mean T, then by more runs that found a fault. The
reference hybrid_final_v5 is reported beside them and does not take part in the choice.

    python scripts/analyze_bandit_tuning.py runs/tune-gha
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REFERENCE = "hybrid_final_v5"


def main() -> None:
    root = Path(sys.argv[1])
    arms = defaultdict(lambda: {"runs": 0, "found": 0, "t": [], "faults": set(), "failed": 0})
    for path in sorted((root / "results").glob("hunt__*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        arm = arms[record["job"]["arm"]]
        if record.get("status") != "ok":
            arm["failed"] += 1
            continue
        hunt = record["hunt"]
        arm["runs"] += 1
        arm["found"] += 0 if hunt["censored"] else 1
        arm["t"].append(hunt["budget"] if hunt["censored"] else hunt["time_to_first_verified"])
        for fault in hunt["oracle"].get("faults", []):
            if fault.get("verified_at") is not None:
                arm["faults"].add(fault["fault_id"])
    table = {name: {"runs": a["runs"], "failed": a["failed"], "found": a["found"],
                    "rmst": round(statistics.mean(a["t"]), 1) if a["t"] else None,
                    "distinct_faults": len(a["faults"]), "faults": sorted(a["faults"])}
             for name, a in sorted(arms.items())}
    candidates = [n for n in table if n.startswith("tune_bandit_") and table[n]["runs"]]
    winner = min(candidates, key=lambda n: (-table[n]["distinct_faults"], table[n]["rmst"],
                                            -table[n]["found"])) if candidates else None
    print(f"{'arm':26s} runs fail found  RMST  distinct")
    for name, row in table.items():
        mark = "  <- chosen" if name == winner else ("  (reference)" if name == REFERENCE else "")
        print(f"{name:26s} {row['runs']:4d} {row['failed']:4d} {row['found']:5d} {row['rmst']!s:>6} "
              f"{row['distinct_faults']:6d}{mark}")
    out = Path("reports/bandit_tuning.json")
    out.write_text(json.dumps({"source": str(root), "rule": __doc__.split("\n\n")[1],
                               "table": table, "winner": winner}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
