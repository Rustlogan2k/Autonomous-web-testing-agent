"""One external-benchmark run with every step written down: where the agent went and what it did.

    python scripts/trace_external.py dimeshift hybrid_final_v5 --seed 0 --minutes 15 \
        --out runs/diag/trace-dimeshift.jsonl

Runs `hybrid.external.run_external_job` exactly as the benchmark does (same application
container, arm, settings and collectors) and appends one JSON line per step: episode, step,
URL after the step, the action taken and whether it succeeded, the state key, and the
branch arms covered so far. The final result record is written beside it (`.result.json`).
Answers questions the run logs cannot - which pages an hour of exploration actually reaches,
how an episode is spent, when an account stops working.

DIAGNOSTIC: for reading the agent's behaviour, not for scoring it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from web_testing_agent.hybrid.external import ExternalConfig, run_external_job
from web_testing_agent.hybrid.run import HybridJob

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("app")
    parser.add_argument("arm")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--minutes", type=float, default=15.0)
    parser.add_argument("--apps-file", type=Path, default=ROOT / "configs" / "external_apps.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    spec = json.loads(args.apps_file.read_text(encoding="utf-8"))[args.app]
    job = HybridJob(arm=args.arm, app=args.app, seed=args.seed, build="external")
    config = ExternalConfig(time_budget_s=60 * args.minutes)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    with args.out.open("w", encoding="utf-8") as sink:
        def trace(event: dict, env) -> None:  # noqa: ANN001
            spec_ = event.get("action")
            page = getattr(env._browser, "page", None)  # noqa: SLF001 - read-only
            info = event["info"]
            sink.write(json.dumps({
                "episode": event["episode"], "step": event["step"],
                "url": page.url if page is not None else None,
                "action": spec_.description if spec_ is not None else None,
                "type": spec_.action_type.value if spec_ is not None else None,
                "value": (spec_.params or {}).get("value") if spec_ is not None else None,
                "ok": bool((info.get("exec_info") or {}).get("success", False)),
                "state": event["state_key"],
            }) + "\n")

        result = run_external_job(job, spec, config, trace=trace)
    args.out.with_suffix(".result.json").write_text(json.dumps(result, indent=1, default=str),
                                                    encoding="utf-8")
    istanbul = result["istanbul"]
    print(f"{args.app} {args.arm} seed {args.seed}: {istanbul['branches_covered']}/{istanbul['branches']} "
          f"arms ({istanbul['branch_coverage']}), {result['hunt']['episodes']} episodes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
