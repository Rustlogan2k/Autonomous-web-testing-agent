"""Describe the benchmark suite from its manifests and the validator's measurements.

    python scripts/benchmark_report.py --validation runs/validation --out reports/benchmark_suite

Reads every application's `faults.json` and the per-application validator outputs
(`validate_benchmark.py --out <dir>/<app>.json`), merges the latter into
`reports/benchmark_validation.json` (the file the transfer runner's admission gate reads),
and writes a Markdown table of the suite: structure, seeded faults and their classes, the
minimized trigger length of each fault, the observation horizon, the uniform-random
difficulty floor, and how often the raw assertions fired on the clean build.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from web_testing_agent.benchmark import discover  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--validation", type=Path, required=True,
                        help="directory of per-application validator JSON files")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports" / "benchmark_suite")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    apps = discover()
    validations, admitted, rejected = [], [], []
    for app in apps:
        path = args.validation / f"{app.app_id}.json"
        if not path.is_file():
            rejected.append(app.app_id)
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        for entry in report["applications"]:
            validations.append({**entry, "git": report["git"], "env_settings": report["env_settings"]})
            (admitted if entry["passed"] else rejected).append(entry["app_id"])

    merged = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "admitted": sorted(set(admitted)), "rejected": sorted(set(rejected)),
              "applications": validations}
    target = REPO_ROOT / "reports" / "benchmark_validation.json"
    target.write_text(json.dumps(merged, indent=2, default=str), encoding="utf-8")

    by_id = {v["app_id"]: v for v in validations}
    lines = ["# Benchmark suite", "",
             f"{len(merged['admitted'])} of {len(apps)} applications admitted by "
             "`scripts/validate_benchmark.py` (every check in a real browser).", "",
             "| application | name | structure | state | fault | class | witness → minimized | H | "
             "random walk (600 steps) | clean-build assertion firings |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for app in apps:
        validation = by_id.get(app.app_id, {})
        walk = validation.get("random_walk") or {}
        minimized = {f["fault_id"]: f for f in validation.get("faults", [])}
        character = app.manifest.characterization or {}
        structure = str(character.get("structure") or character.get("_contrast_with_deep_fixture", ""))[:60]
        state = str(character.get("workflow_state_representation", ""))[:30]
        for number, fault in enumerate(app.manifest.faults):
            entry = minimized.get(fault.fault_id, {})
            buggy = walk.get("buggy") or {}
            walk_text = ("—" if not buggy else
                         f"verified at {buggy['time_to_first_verified']}" if not buggy["censored"]
                         else "nothing verified")
            lines.append(
                f"| {app.app_id if number == 0 else ''} | {app.manifest.name if number == 0 else ''} | "
                f"{structure if number == 0 else ''} | {state if number == 0 else ''} | "
                f"{fault.fault_id} | {fault.fault_class.value} | "
                f"{entry.get('witness_length', '—')} → {entry.get('minimized_length', '—')} | "
                f"{fault.observation_horizon} | {walk_text if number == 0 else ''} | "
                f"{(walk.get('clean') or {}).get('raw_candidates', '—') if number == 0 else ''} |")
    lines += ["", "Witness → minimized: the reachability witness's length and the length delta "
              "debugging reduced it to while it still fired on the faulty build and stayed silent "
              "on the faulty build without that fault's own patch (its per-fault control). H: steps between the triggering action and the page where the "
              "violation is visible."]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nadmitted: {merged['admitted']}; not admitted: {merged['rejected']}")
    return 0 if not merged["rejected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
