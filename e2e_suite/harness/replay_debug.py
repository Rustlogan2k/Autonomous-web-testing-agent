"""Replay one fault's witness N times on a variant and print each step's URL and action."""
import sys

from e2e_suite.harness import variants
from e2e_suite.harness.browser_checks import _runner
from e2e_suite.harness.launcher import Server
from e2e_suite.harness.manifest import load
from web_testing_agent.oracle import verify_fault

app = load(sys.argv[1])
fid, variant, n = sys.argv[2], sys.argv[3], int(sys.argv[4])
fault = next(f for f in app.faults if f["fault_id"] == fid)
spec = next(f for f in app.oracle_app().faults if f.fault_id == fid)
directory = variants.build_variant(app, variant)
with Server(app, directory) as server:
    runner = _runner(server.url)
    for i in range(n):
        observed = runner.run(list(fault["trigger_trace"]))
        verdict = verify_fault(spec, app.oracle_app(), observed)
        print(f"run {i}: violated={verdict.violated} misses={observed.replay_misses}")
        for step in observed.steps:
            act = step.action or {}
            print(f"   {act.get('type', '-'):7} {act.get('id', ''):18} -> {step.url.split('/', 3)[-1]}")
