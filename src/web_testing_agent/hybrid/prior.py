"""The cross-app prior: what kinds of probes paid off on the development applications.

The bandit's evidence is keyed by meaning — (page kind, operator, target type) — never by an
application's words or layout, so it can travel to an application the tester has never seen.
This module pools the final bandit states of development runs into one prior and caps each
key's weight, so the prior shapes the first decisions on a new application without
outweighing what the bandit learns there.

    python -m web_testing_agent.hybrid.prior --runs runs/v2-dev --out data/v2_prior.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

#: Pseudo-observations a key may carry into a new application (mean kept, weight capped).
MAX_PRIOR_WEIGHT = 5.0


def pool(states: list[dict], cap: float = MAX_PRIOR_WEIGHT) -> dict[str, list[float]]:
    """Sum per-key [successes, failures] across runs, then cap each key's total weight."""
    totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for state in states:
        for key, (successes, failures) in state.items():
            totals[key][0] += float(successes)
            totals[key][1] += float(failures)
    prior = {}
    for key, (successes, failures) in sorted(totals.items()):
        weight = successes + failures
        if weight <= 0:
            continue
        scale = min(1.0, cap / weight)
        prior[key] = [round(successes * scale, 4), round(failures * scale, 4)]
    return prior


def load_prior(path: Path) -> dict[tuple, list[float]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {tuple(key.split("|")): value for key, value in payload["prior"].items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--arm", default="hybrid_bandit")
    arguments = parser.parse_args(argv)
    states, sources = [], []
    for directory in arguments.runs:
        for path in sorted((directory / "results").glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            job = record.get("job", {})
            if (
                record.get("status") == "ok"
                and job.get("arm") == arguments.arm
                and job.get("build", "buggy") == "buggy"
                and record.get("bandit_state")
            ):
                states.append(record["bandit_state"])
                sources.append(job["job_id"])
    prior = pool(states)
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(
        json.dumps({"max_weight": MAX_PRIOR_WEIGHT, "sources": sources, "prior": prior}, indent=1)
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(sources)} runs pooled into {len(prior)} keys -> {arguments.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
