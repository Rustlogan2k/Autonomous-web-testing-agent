"""Run the primary pipeline's confirmation N times for one fault and print each replay."""
import json
import sys

from e2e_suite.harness import variants
from e2e_suite.harness.browser_checks import _runner
from e2e_suite.harness.launcher import serve
from e2e_suite.harness.manifest import load
from web_testing_agent.oracle import VerificationPipeline

app = load(sys.argv[1])
fid, n = sys.argv[2], int(sys.argv[3])
fault = next(f for f in app.faults if f["fault_id"] == fid)
spec = next(f for f in app.oracle_app().faults if f.fault_id == fid)
builds = {"buggy": variants.build_variant(app, "buggy"), "clean": variants.build_variant(app, "clean"),
          f"without-{fid}": variants.build_variant(app, f"without-{fid}")}
with serve(app, builds) as urls:
    faulty, clean, control = _runner(urls["buggy"]), _runner(urls["clean"]), _runner(urls[f"without-{fid}"])
    pipeline = VerificationPipeline(app.oracle_app(), faulty, control_runner=clean,
                                    control_runners={fid: control}, max_minimization_trials=5)
    for i in range(n):
        observed = faulty.run(list(fault["trigger_trace"]))
        finding = pipeline.confirm(spec, observed, budget_at_discovery=1)
        print(f"attempt {i}: confirmed={finding.confirmed} {finding.rejected_because}")
        if not finding.confirmed:
            print(json.dumps(observed.actions, indent=0)[:1500])
            for r in finding.replays:
                print("  replay:", json.dumps(r, default=str)[:1500])
