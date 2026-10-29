"""Which client-side branches WebRLED reaches and our agent does not, per application.

    python scripts/compare_coverage.py [--apps dimeshift pagekit] [--ours hybrid_final_v5]
                                       [--out runs/diag/coverage_gap]

WebRLED's runs (runs/gha-webrled/artifacts, `scripts/webrled/run_webrled.sh`) keep Istanbul's
raw coverage, branch map included. Our result files keep, per loaded file, the branch arms a
run never executed (`IstanbulCollector.summary()["uncovered"]`); a loaded file every arm of
which ran does not appear there, so it is recovered as the set of remaining files whose arms
add up to the run's total. The two are then compared arm by arm: in how many runs each tool
executed it. The arms one tool executes in most runs and the other in few, grouped by file
and source line, are where the coverage difference comes from.

DIAGNOSTIC, post-hoc on the benchmark applications: it says where to look, not what to tune.
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ["dimeshift", "pagekit", "splittypie", "phoenix", "retroboard", "petclinic"]
OUR_RESULTS = [
    "runs/gha-fix2/results",
    "runs/gha-agents/results",
    "runs/gha-v6/results",
    "runs/gha-v7/results",
    "runs/gha-v8/results",
]

Arm = tuple[str, str, int]  # file, branch id, arm


def webrled_runs(app: str) -> tuple[list[dict], dict]:
    """Each run's loaded files and executed arms, and the branch map of every file seen."""
    runs, branch_map = [], {}
    for path in sorted(
        glob.glob(str(ROOT / f"runs/gha-webrled/artifacts/webrled__{app}__run-*/coverage.json"))
    ):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        covered = set()
        for file, data in raw.items():
            branch_map.setdefault(
                file,
                {
                    bid: {**meta, "arms": len(data["b"][bid])}
                    for bid, meta in data["branchMap"].items()
                },
            )
            covered |= {
                (file, bid, i)
                for bid, counts in data["b"].items()
                for i, c in enumerate(counts)
                if c
            }
        runs.append({"files": set(raw), "covered": covered})
    return runs, branch_map


def _arms(branch_map: dict, file: str) -> int:
    return sum(meta["arms"] for meta in branch_map.get(file, {}).values())


def our_runs(app: str, agent: str, branch_map: dict) -> list[dict]:
    """Our runs in the same form. A file with an unexecuted arm was loaded; one with more arms
    than the run's fully covered remainder was not; the rest are known only when exactly one
    set of them adds up to that remainder, and are otherwise left `unknown`."""
    runs = []
    for directory in OUR_RESULTS:
        for path in sorted(glob.glob(str(ROOT / directory / f"*__{agent}__{app}__seed-*.json"))):
            record = json.loads(Path(path).read_text(encoding="utf-8"))
            if record.get("status") != "ok":
                continue
            istanbul = record["istanbul"]
            uncovered = istanbul["uncovered"]
            files = {f for f in uncovered if f in branch_map}
            residual = istanbul["branches"] - sum(_arms(branch_map, f) for f in uncovered)
            open_ = [
                f for f in branch_map if f not in files and 0 < _arms(branch_map, f) <= residual
            ]
            complete = _only_subset(open_, residual, branch_map)
            unknown = set() if complete is not None else set(open_)
            files |= set(complete or [])
            missing = {(f, *a.rsplit(".", 1)) for f, arms in uncovered.items() for a in arms}
            covered = {
                (f, bid, i)
                for f in files
                for bid, meta in branch_map.get(f, {}).items()
                for i in range(meta["arms"])
                if (f, bid, str(i)) not in missing
            }
            runs.append(
                {
                    "files": files,
                    "covered": covered,
                    "unknown": unknown,
                    "unattributed": 0 if complete is not None else residual,
                    "seed": record["job"]["seed"],
                }
            )
    return runs


def _only_subset(candidates: list[str], target: int, branch_map: dict) -> list[str] | None:
    """The set of files whose arms sum to `target`, when exactly one such set exists."""
    if target == 0:
        return []
    ways = [0] * (target + 1)  # number of subsets reaching each sum, capped at 2
    ways[0] = 1
    for f in candidates:
        n = _arms(branch_map, f)
        for total in range(target, n - 1, -1):
            ways[total] = min(2, ways[total] + ways[total - n])
    if ways[target] != 1:
        return None
    chosen, total = [], target
    for i in range(
        len(candidates) - 1, -1, -1
    ):  # walk back: keep f when the rest still sums uniquely
        f = candidates[i]
        n = _arms(branch_map, f)
        rest = [0] * (target + 1)
        rest[0] = 1
        for g in candidates[:i]:
            m = _arms(branch_map, g)
            for t in range(target, m - 1, -1):
                rest[t] = min(2, rest[t] + rest[t - m])
        if total >= n and rest[total - n]:
            chosen.append(f)
            total -= n
        if total == 0:
            return chosen
    return chosen if total == 0 else None


def _line(branch_map: dict, arm: Arm) -> int:
    file, bid, i = arm
    meta = branch_map[file][bid]
    locations = meta.get("locations") or [meta["loc"]]
    return locations[min(i, len(locations) - 1)]["start"]["line"]


def compare(app: str, agent: str) -> dict:
    theirs, branch_map = webrled_runs(app)
    ours = our_runs(app, agent, branch_map)
    if not theirs or not ours:
        return {"app": app, "error": f"runs missing (WebRLED {len(theirs)}, ours {len(ours)})"}
    count_t, count_o = defaultdict(int), defaultdict(int)
    for run in theirs:
        for arm in run["covered"]:
            count_t[arm] += 1
    for run in ours:
        for arm in run["covered"]:
            count_o[arm] += 1
    n_t, n_o = len(theirs), len(ours)
    unknown_files = set().union(*(r["unknown"] for r in ours))

    per_file = []
    for file in sorted(branch_map):
        loaded_t = sum(file in r["files"] for r in theirs) / n_t
        loaded_o = None if file in unknown_files else sum(file in r["files"] for r in ours) / n_o
        cov_t = statistics.mean(sum(1 for a in r["covered"] if a[0] == file) for r in theirs)
        cov_o = statistics.mean(sum(1 for a in r["covered"] if a[0] == file) for r in ours)
        if cov_t or cov_o:
            per_file.append(
                {
                    "file": file,
                    "arms": _arms(branch_map, file),
                    "loaded_webrled": loaded_t,
                    "loaded_ours": loaded_o,
                    "covered_webrled": round(cov_t, 1),
                    "covered_ours": round(cov_o, 1),
                    "difference": round(cov_o - cov_t, 1),
                }
            )
    per_file.sort(key=lambda row: row["difference"])

    def lines(mostly: dict, rarely: dict, n_mostly: int, n_rarely: int) -> list[dict]:
        grouped = defaultdict(list)
        for arm, hits in mostly.items():
            if arm[0] in unknown_files:
                continue
            if hits / n_mostly >= 0.6 and rarely.get(arm, 0) / n_rarely <= 0.2:
                grouped[(arm[0], _line(branch_map, arm))].append(arm)
        return sorted(
            (
                {
                    "file": f,
                    "line": line,
                    "arms": len(arms),
                    "types": sorted({branch_map[f][a[1]].get("type") for a in arms}),
                }
                for (f, line), arms in grouped.items()
            ),
            key=lambda r: (r["file"], r["line"]),
        )

    return {
        "app": app,
        "agent": agent,
        "runs_webrled": n_t,
        "runs_ours": n_o,
        "arms_covered_webrled": [len(r["covered"]) for r in theirs],
        "arms_covered_ours": [len(r["covered"]) for r in ours],
        "files_loaded_webrled": [
            sum(1 for f in r["files"] if _arms(branch_map, f)) for r in theirs
        ],
        "files_loaded_ours": [len(r["files"]) for r in ours],
        "unattributed_ours": [r["unattributed"] for r in ours],
        "unknown_files": sorted(unknown_files),
        "per_file": per_file,
        "webrled_only": lines(count_t, count_o, n_t, n_o),
        "ours_only": lines(count_o, count_t, n_o, n_t),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apps", nargs="+", default=APPS)
    parser.add_argument("--ours", default="hybrid_final_v5")
    parser.add_argument("--out", default="runs/diag/coverage_gap")
    args = parser.parse_args()
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    for app in args.apps:
        result = compare(app, args.ours)
        (out / f"{app}__{args.ours}.json").write_text(
            json.dumps(result, indent=1), encoding="utf-8"
        )
        if "error" in result:
            print(f"{app}: {result['error']}")
            continue
        mean = statistics.mean
        print(
            f"\n{app}: arms covered, WebRLED {mean(result['arms_covered_webrled']):.0f} "
            f"vs ours {mean(result['arms_covered_ours']):.0f}; files loaded "
            f"{mean(result['files_loaded_webrled']):.1f} vs "
            f"{mean(result['files_loaded_ours']):.1f} "
            f"(files with branches; ours + {len(result['unknown_files'])} undetermined, "
            f"{mean(result['unattributed_ours']):.0f} fully covered arms unattributed)"
        )
        print(f"  {'file':48} {'arms':>5} {'loaded W/O':>11} "
              f"{'covered W':>10} {'ours':>6} {'diff':>6}")
        for row in [r for r in result["per_file"] if r["difference"]][:12]:
            loaded = "?" if row["loaded_ours"] is None else f"{row['loaded_ours']:.0%}"
            print(
                f"  {row['file'][-48:]:48} {row['arms']:5} {row['loaded_webrled']:5.0%}/{loaded:<5}"
                f"{row['covered_webrled']:10} {row['covered_ours']:6} {row['difference']:6}"
            )
        print(
            f"  lines WebRLED reaches in most runs and we rarely: {len(result['webrled_only'])}; "
            f"the reverse: {len(result['ours_only'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
