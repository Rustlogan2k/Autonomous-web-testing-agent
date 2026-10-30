"""One agent against WebRLED on the external benchmark, per application, with Welch intervals.

    python scripts/compare_agents.py hybrid_final_v9 [--ours runs/gha-v9/results ...]

Reads our result files (`istanbul.branch_coverage`, status ok) for the agent from every
directory given, and WebRLED's runs as scored on the same runners
(`runs/gha-webrled/results`, `scripts/webrled/score_webrled.py`). Prints, per application:
both means, standard deviations and run counts, the difference, its 95% Welch interval and
two-sided p-value; then both six-application averages.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import statistics
from pathlib import Path

from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
APPS = ["dimeshift", "pagekit", "splittypie", "phoenix", "retroboard", "petclinic"]
OUR_RESULTS = [
    "runs/gha-fix2/results",
    "runs/gha-agents/results",
    "runs/gha-v6/results",
    "runs/gha-v7/results",
    "runs/gha-v8/results",
    "runs/gha-v9/results",
    "runs/gha-v9t/results",
]


def branch_coverage(directories: list[str], arm: str | None) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {app: [] for app in APPS}
    for directory in directories:
        for path in sorted(glob.glob(str(ROOT / directory / "*.json"))):
            record = json.loads(Path(path).read_text(encoding="utf-8"))
            if record.get("status") != "ok" or "istanbul" not in record:
                continue
            job = record["job"]
            if job["app"] in out and (arm is None or job.get("arm") == arm):
                out[job["app"]].append(100 * record["istanbul"]["branch_coverage"])
    return out


def welch(a: list[float], b: list[float]) -> tuple[float, float, float]:
    """Difference of means, half-width of its 95% interval, two-sided p (Welch)."""
    diff = statistics.mean(a) - statistics.mean(b)
    va = statistics.variance(a) / len(a) if len(a) > 1 else 0.0
    vb = statistics.variance(b) / len(b) if len(b) > 1 else 0.0
    se = math.sqrt(va + vb)
    if se == 0:
        return diff, 0.0, 1.0 if diff == 0 else 0.0
    dof = (va + vb) ** 2 / ((va**2 / (len(a) - 1)) + (vb**2 / (len(b) - 1)))
    return diff, float(stats.t.ppf(0.975, dof) * se), float(2 * stats.t.sf(abs(diff) / se, dof))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("arm")
    parser.add_argument("--ours", nargs="*", default=OUR_RESULTS)
    parser.add_argument("--webrled", default="runs/gha-webrled/results")
    args = parser.parse_args()
    ours = branch_coverage(args.ours, args.arm)
    webrled = branch_coverage([args.webrled], None)
    print(
        f"{'app':11} {'ours':>6} {'sd':>5} {'n':>3}  {'WebRLED':>7} {'sd':>5} {'n':>3}"
        f"  {'diff':>6} {'95% CI':>17} {'p':>6}"
    )
    for app in APPS:
        a, b = ours[app], webrled[app]
        if not a or not b:
            print(f"{app:11} missing runs (ours {len(a)}, WebRLED {len(b)})")
            continue
        diff, half, p = welch(a, b)
        sd = [statistics.stdev(v) if len(v) > 1 else 0.0 for v in (a, b)]
        print(
            f"{app:11} {statistics.mean(a):6.2f} {sd[0]:5.2f} {len(a):3}  "
            f"{statistics.mean(b):7.2f} {sd[1]:5.2f} {len(b):3}  {diff:+6.2f} "
            f"[{diff - half:+6.2f}, {diff + half:+6.2f}] {p:6.3f}"
        )
    if all(ours[app] and webrled[app] for app in APPS):
        averages = [
            statistics.mean(statistics.mean(d[app]) for app in APPS) for d in (ours, webrled)
        ]
        print(
            f"{'average':11} {averages[0]:6.2f} {'':9}  {averages[1]:7.2f} {'':9}  "
            f"{averages[0] - averages[1]:+6.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
