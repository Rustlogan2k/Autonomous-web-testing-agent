"""Cost of a run, per tool: CPU time and memory of the tester's own process tree.

    python scripts/run_cost.py [DIR ...]

Reads every `usage*.json` that `scripts/ci/measure.py` wrote under the given directories
(default: the 2026-11-26 batch). Each one covers the tester's process tree only (the agent or
WebRLED, its browser and its helpers); the application under test runs in its own container
and is not counted for either tool. Prints per tool and benchmark: runs, wall-clock minutes,
CPU minutes (mean, sd), mean and peak resident memory (MiB), on one runner type (GitHub's
ubuntu-latest, 4 vCPUs). Only runs that used their whole hour are counted (wall clock of
at least 59 minutes): a run that ended early, e.g. because the application crashed, would
look cheap. The exit status is not used, since WebRLED sometimes exits 1 while shutting
down after its hour.
"""

from __future__ import annotations

import glob
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FULL_RUN_S = 59 * 60
DEFAULT = ["runs/gha-final", "runs/gha-webrled-final", "runs/heldout-final"]


def tool(command: list[str]) -> str:
    """The tool a measured command ran: our arm's name, or WebRLED."""
    if "src/main.py" in command:
        return "WebRLED"
    if "--arms" in command:
        return command[command.index("--arms") + 1]
    if "--arm" in command:
        return command[command.index("--arm") + 1]
    return "unknown"


def main() -> int:
    dirs = sys.argv[1:] or DEFAULT
    rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for directory in dirs:
        for path in sorted(glob.glob(str(ROOT / directory / "**" / "usage*.json"),
                                     recursive=True)):
            usage = json.loads(Path(path).read_text(encoding="utf-8"))
            if not usage.get("samples") or usage["wall_s"] < FULL_RUN_S:
                continue
            rows[(Path(directory).name, tool(usage["command"]))].append(usage)
    print(f"{'runs dir':22} {'tool':26} {'n':>3} {'wall min':>8} {'CPU min':>8} {'sd':>5} "
          f"{'mean MiB':>8} {'peak MiB':>8}")
    for (directory, name), runs in sorted(rows.items()):
        cpu = [u["cpu_s"] / 60 for u in runs]
        print(f"{directory:22} {name:26} {len(runs):3} "
              f"{statistics.mean(u['wall_s'] for u in runs) / 60:8.1f} "
              f"{statistics.mean(cpu):8.1f} {statistics.stdev(cpu) if len(cpu) > 1 else 0:5.1f} "
              f"{statistics.mean(u['mean_rss_mib'] for u in runs):8.0f} "
              f"{max(u['peak_rss_mib'] for u in runs):8.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
