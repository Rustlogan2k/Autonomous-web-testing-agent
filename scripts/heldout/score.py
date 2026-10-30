"""Score the held-out benchmark: line coverage as each application measures it.

    python scripts/heldout/score.py <downloaded artifacts dir> [--csv out.csv]

Reads every run under the directory: our agent's result files (`server_coverage`, written by
`hybrid.external` from the image's coverage server when the run ends) and WebRLED's run
folders (`coverage.json`, the same server's JSON, written by `run_webrled.sh` with
SERVER_COVERAGE). Prints per application and tool the mean line coverage, its standard
deviation and run count, beside WebRLED's published figure (Gu et al., 2025, Table 4).
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

APPS = ["timeoff", "parabank", "agilefant", "gadael"]
#: WebRLED's average line coverage on these applications as published (Table 4).
PUBLISHED_WEBRLED = {"timeoff": 44.08, "parabank": 32.80, "agilefant": 34.80, "gadael": 41.06}


def percent(value) -> float | None:  # noqa: ANN001
    """'35.01%' or '7' or 7.0 -> 35.01 / 7.0."""
    if value is None:
        return None
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def collect(root: Path) -> dict[tuple[str, str], list[float]]:
    runs: dict[tuple[str, str], list[float]] = defaultdict(list)
    for path in sorted(root.rglob("*.json")):
        if path.name == "meta.json":
            meta = json.loads(path.read_text(encoding="utf-8"))
            log = path.parent / "webrled.log"
            reached = log.exists() and "Reached 1 hours." in log.read_text(
                encoding="utf-8", errors="replace"
            )
            coverage = path.parent / "coverage.json"
            if reached and coverage.exists():
                line = percent(
                    json.loads(coverage.read_text(encoding="utf-8")).get("line_coverage")
                )
                if line is not None:
                    runs[(meta["app"], "WebRLED")].append(line)
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if (
            not isinstance(record, dict)
            or record.get("status") != "ok"
            or "server_coverage" not in record
        ):
            continue
        line = percent(record["server_coverage"].get("line_coverage"))
        if line is not None:
            runs[(record["job"]["app"], record["job"]["arm"])].append(line)
    return runs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args()
    runs = collect(args.root)
    tools = sorted({tool for _, tool in runs})
    rows = []
    print(f"{'app':10} {'tool':18} {'mean':>6} {'sd':>5} {'n':>3}  published WebRLED")
    for app in APPS:
        for tool in tools:
            values = runs.get((app, tool), [])
            if not values:
                continue
            mean = statistics.mean(values)
            sd = statistics.stdev(values) if len(values) > 1 else 0.0
            rows.append(
                {
                    "app": app,
                    "tool": tool,
                    "mean": round(mean, 2),
                    "sd": round(sd, 2),
                    "n": len(values),
                    "runs": " ".join(f"{v:.2f}" for v in values),
                    "published_webrled": PUBLISHED_WEBRLED[app],
                }
            )
            published = PUBLISHED_WEBRLED[app]
            print(f"{app:10} {tool:18} {mean:6.2f} {sd:5.2f} {len(values):3}  {published}")
    for tool in tools:
        means = [statistics.mean(runs[(a, tool)]) for a in APPS if runs.get((a, tool))]
        if len(means) == len(APPS):
            print(f"{'average':10} {tool:18} {statistics.mean(means):6.2f}")
    published = statistics.mean(PUBLISHED_WEBRLED.values())
    print(f"{'average':10} {'WebRLED published':18} {published:6.2f}")
    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as sink:
            writer = csv.DictWriter(sink, fieldnames=list(rows[0]) if rows else ["app"])
            writer.writeheader()
            writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
