"""Score WebRLED runs with the coverage counter our own agents are scored with.

    python scripts/webrled/score_webrled.py <downloaded artifacts dir> <results dir>

Each artifact folder holds one run of `run_webrled.sh`: express-istanbul's merged coverage
(`coverage.json`, Istanbul's raw format), its HTML report (`coverage.html`, the figure WebRLED
logs) and `meta.json`. The raw coverage goes through `IstanbulCollector.merge` exactly as a
snapshot taken in our own runs does, so both tools are counted by one piece of code: branch
arms ever executed over all arms of the files the application loaded. WebRLED's own report
is kept beside it as a cross-check.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from web_testing_agent.hybrid.metrics import IstanbulCollector

_REPORT = re.compile(r"<span class=\"strong\">([\d.]+)% </span>\s*<span class=\"quiet\">(\w+)</span>"
                     r"\s*<span class='fraction'>(\d+)/(\d+)</span>")


def snapshot(raw: dict) -> dict:
    """Istanbul's raw coverage in the shape our page snapshot produces (`_ISTANBUL_SNAPSHOT`)."""
    out = {}
    for file, data in raw.items():
        branches = {bid: "".join("1" if c > 0 else "0" for c in arms)
                    for bid, arms in (data.get("b") or {}).items()}
        counts = list((data.get("s") or {}).values())
        out[file] = {"b": branches, "s": len(counts), "sh": sum(1 for c in counts if c > 0)}
    return out


def own_report(html: str) -> dict:
    """WebRLED's figure: the totals of express-istanbul's HTML report."""
    return {label.lower(): {"percent": float(pct), "covered": int(hit), "total": int(total)}
            for pct, label, hit, total in _REPORT.findall(html)}


def score(folder: Path) -> dict:
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    collector = IstanbulCollector()
    collector.merge(snapshot(json.loads((folder / "coverage.json").read_text(encoding="utf-8"))))
    html = folder / "coverage.html"
    run = int(re.search(r"run-(\d+)", folder.name).group(1)) if "run-" in folder.name else 1
    # Complete when WebRLED logged the end of its time budget. It sometimes exits 1 after that,
    # while shutting down ("Event loop is closed"); the coverage then already spans the run.
    log = folder / "webrled.log"
    reached = log.exists() and "Reached 1 hours." in log.read_text(encoding="utf-8", errors="replace")
    return {
        "job": {"arm": "webrled", "app": meta["app"], "seed": run - 1, "kind": "external"},
        "status": "ok" if reached else "error",
        "exit_status": meta.get("exit_status"),
        "istanbul": collector.summary(),
        "webrled_report": own_report(html.read_text(encoding="utf-8")) if html.exists() else {},
        "meta": meta,
    }


def main() -> int:
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    target.mkdir(parents=True, exist_ok=True)
    for folder in sorted(p for p in source.iterdir() if (p / "meta.json").exists()):
        record = score(folder)
        (target / f"{folder.name}.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
        ours = record["istanbul"]["branch_coverage"]
        theirs = record["webrled_report"].get("branches", {}).get("percent")
        print(f"{folder.name}: ours {ours if ours is None else round(100 * ours, 1)}%  "
              f"WebRLED's report {theirs}%  status {record['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
