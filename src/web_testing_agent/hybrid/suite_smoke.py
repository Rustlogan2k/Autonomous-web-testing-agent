"""The pre-declared infrastructure test of the v2 suite path (plan addendum §10).

For every suite application: private builds, servers, and the admission's own benign walks
and fault witnesses replayed through the runner's monitor (`SuiteMonitor`) by a scripted
policy. Passes when every witness is confirmed and no benign walk yields a confirmed
finding. No agent touches a suite application here.

    python -m web_testing_agent.hybrid.suite_smoke [--apps ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

from ..experiments.transfer import REPO_ROOT, git_state

OUT = REPO_ROOT / "reports" / "v2_suite_smoke.json"


def smoke_app(app_id: str) -> dict:
    from e2e_suite.harness.variants import control_key

    from ..benchmark import FaultMonitor, make_env, run_hunt
    from ..evaluation.scripted import ScriptedPolicy
    from ..oracle import VerificationPipeline
    from .run import HybridConfig, _runner_factory, _settings
    from .suite import ExtensionMonitor, SuiteMonitor, load_suite_app, serve_suite_app

    config = HybridConfig()
    settings = _settings(config)
    app = load_suite_app(app_id)
    manifest = app.oracle_app()
    out: dict = {"app_id": app_id, "witnesses": {}, "benign": []}

    def monitor(runner) -> SuiteMonitor:
        controls = {f.fault_id: runner(control_key(f.fault_id)) for f in manifest.faults}
        primary = (
            FaultMonitor(
                VerificationPipeline(
                    manifest,
                    runner("buggy"),
                    control_runner=runner("clean"),
                    control_runners=controls,
                    max_minimization_trials=config.max_minimization_trials,
                )
            )
            if manifest.faults
            else None
        )
        ext = ExtensionMonitor(
            app,
            faulty=runner("buggy"),
            clean=runner("clean"),
            controls={
                f["fault_id"]: runner(control_key(f["fault_id"]))
                for f in app.faults_by_oracle("extension")
            },
        )
        return SuiteMonitor(primary, ext)

    def replay(urls, runner, steps: list[dict]) -> dict:
        watcher = monitor(runner)
        env = make_env(urls["buggy"], settings, max_steps=max(len(steps) + 2, 40))
        try:
            result = run_hunt(
                env, ScriptedPolicy(list(steps), name="smoke"), watcher, budget=len(steps)
            )
        finally:
            env.close()
        summary = watcher.summary()
        return {
            "verified": [f["fault_id"] for f in summary["faults"] if f["verified_at"]],
            "candidates": summary["candidates"],
            "rejections": summary["rejections"],
            "steps": result.steps,
        }

    with serve_suite_app(app, controls=True) as urls:
        runner = _runner_factory(urls, settings)
        for fault in app.faults:
            outcome = replay(urls, runner, fault["trigger_trace"])
            outcome["confirmed"] = fault["fault_id"] in outcome["verified"]
            out["witnesses"][fault["fault_id"]] = outcome
        for trace in app.data.get("benign_traces", []):
            out["benign"].append(replay(urls, runner, trace))
    out["passed"] = all(w["confirmed"] for w in out["witnesses"].values()) and not any(
        b["verified"] for b in out["benign"]
    )
    return out


def main(argv: list[str] | None = None) -> int:
    from .suite import suite_app_ids

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apps", nargs="*")
    parser.add_argument("--workers", type=int, default=4)
    arguments = parser.parse_args(argv)
    apps = arguments.apps or suite_app_ids()
    results = []
    with ProcessPoolExecutor(max_workers=arguments.workers) as pool:
        futures = {pool.submit(smoke_app, a): a for a in apps}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - a crash is a failure of the test
                result = {
                    "app_id": futures[future],
                    "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            results.append(result)
            witnesses = {k: v.get("confirmed") for k, v in result.get("witnesses", {}).items()}
            benign = [b["verified"] for b in result.get("benign", [])]
            verdict = "PASS" if result["passed"] else "FAIL"
            print(
                f"== {result['app_id']}: {verdict} {witnesses} benign={benign} "
                f"{result.get('error', '')}",
                flush=True,
            )
    payload = {
        "git": git_state(),
        "passed": all(r["passed"] for r in results),
        "results": sorted(results, key=lambda r: r["app_id"]),
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
