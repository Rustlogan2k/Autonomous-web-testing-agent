"""Replay one fault's witness on a variant and show what the oracle sees, step by step.

    python -m e2e_suite.harness.replay_debug <app> <FAULT-ID> <variant> [runs]

`variant` is clean, buggy or without-<FAULT-ID>. Works for faults in the primary grammar and
for the suite's extension kinds. One browser, sequential: cheap enough to use while
developing a fault.
"""

import sys

from web_testing_agent.oracle import verify_fault

from e2e_suite.harness import ext_oracle, variants
from e2e_suite.harness.browser_checks import _runner
from e2e_suite.harness.launcher import Server
from e2e_suite.harness.manifest import load


def main() -> None:
    app = load(sys.argv[1])
    fid, variant = sys.argv[2], sys.argv[3]
    runs = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    fault = next(f for f in app.faults if f["fault_id"] == fid)
    oracle_app = app.oracle_app()
    directory = variants.build_variant(app, variant)
    with Server(app, directory) as server:
        runner = _runner(server.url)
        for i in range(runs):
            observed = runner.run(list(fault["trigger_trace"]))
            if fault.get("oracle", "existing") == "existing":
                spec = next(f for f in oracle_app.faults if f.fault_id == fid)
                verdict = verify_fault(spec, oracle_app, observed)
            else:
                verdict = ext_oracle.verify(fault, app.data["regions"], oracle_app, observed)
            print(f"run {i} on {variant}: violated={verdict.violated} inconclusive={verdict.inconclusive} "
                  f"replay_misses={observed.replay_misses}")
            for result in verdict.results:
                print(f"   {result.kind}: violated={result.violated} inconclusive={result.inconclusive} "
                      f"{result.detail} | observed={str(getattr(result, 'observed', ''))[:120]!r}")
            for step in observed.steps:
                act = step.action or {}
                print(f"   {act.get('type', '-'):7} {str(act.get('id', '')):22} -> {step.url.split('/', 3)[-1][:70]}")


if __name__ == "__main__":
    main()
