"""Admit (or reject) every application of the e2e suite, and write the admission reports.

    python -m e2e_suite.harness.validate_suite                      # static + API only
    python -m e2e_suite.harness.validate_suite --browser            # + browser and faults
    python -m e2e_suite.harness.validate_suite --browser --docker   # everything
    python -m e2e_suite.harness.validate_suite --apps tidewater --browser

Writes `e2e_suite/admission/<app>.json`, `suite.json` and `suite.md`. An app is admitted
only when every check that was run passed; checks not run are reported as not run, never
as passed. No RL code is imported or run here.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import diversity, static_checks, variants
from .launcher import Server, serve
from .manifest import ADMISSION_ROOT, AppManifest, discover, load, validate_manifest


def _check(name: str, passed: bool, detail=None) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


def api_tests(app: AppManifest, clean_dir: Path) -> dict:
    """The app's own API tests, run against its clean build."""
    tests = app.root / "tests"
    if not tests.is_dir():
        return {"passed": False, "detail": "no tests/ directory"}
    with Server(app, clean_dir) as server:
        env = {**os.environ, "E2E_BASE_URL": server.url}
        if app.data["start"][0] == "node":
            command = [shutil.which("node") or "node", "--test", "--test-reporter=tap", str(tests)]
        else:
            command = [sys.executable, "-m", "unittest", "discover", "-s", str(tests), "-v"]
        completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=600,
                                   check=False, cwd=app.root)
    output = (completed.stdout + "\n" + completed.stderr)
    if app.data["start"][0] == "node":
        passed_n = sum(1 for line in output.splitlines() if line.startswith("# pass"))
        counts = [line for line in output.splitlines() if line.startswith(("# pass", "# fail", "# tests"))]
    else:
        counts = [line for line in output.splitlines() if line.startswith(("Ran ", "OK", "FAILED"))]
        passed_n = None
    return {"passed": completed.returncode == 0, "summary": counts, "pass_lines": passed_n,
            "tail": output.strip().splitlines()[-8:] if completed.returncode else []}


def validate(app: AppManifest, *, browser: bool, docker: bool, walk_steps: int) -> dict:
    started = time.monotonic()
    report: dict = {"app_id": app.app_id, "checks": [], "sections": {}}
    add = report["checks"].append

    problems = validate_manifest(app)
    add(_check("manifest_well_formed", not problems, problems))
    static = static_checks.run_all(app)
    for name, result in static.items():
        add(_check(name, result["passed"], result["detail"]))
    report["sections"]["vocabulary"] = static["vocabulary_complete"]["summary"]

    try:
        builds = variants.build_all(app)
        diff = variants.audit_diff(app, builds["clean"], builds["buggy"])
        add(_check("patches_apply_exactly_once", True))
        add(_check("buggy_equals_clean_plus_patches_only", not diff, diff))
        report["sections"]["patch_sizes"] = variants.patch_sizes(app)
    except variants.PatchError as exc:
        add(_check("patches_apply_exactly_once", False, str(exc)))
        builds = None

    if builds is not None:
        try:
            api = api_tests(app, builds["clean"])
            add(_check("api_tests_pass", api["passed"], api))
        except Exception as exc:  # noqa: BLE001
            add(_check("api_tests_pass", False, f"{type(exc).__name__}: {exc}"))

    if builds is not None and browser:
        from . import browser_checks as bc

        with serve(app, {"clean": builds["clean"]}, vocab="alt") as alt_urls:
            alt_workflows = bc.check_workflows(app, alt_urls["clean"], "alt vocabulary")
        with serve(app, builds) as urls:
            workflows = bc.check_workflows(app, urls["clean"], "default vocabulary") + alt_workflows
            add(_check("workflows_reachable_both_vocabularies", all(w["passed"] for w in workflows),
                       [w for w in workflows if not w["passed"]]))
            report["sections"]["workflows"] = workflows
            dependent = bc.check_state_dependent(app, urls["clean"])
            add(_check("state_dependent_actions_real", all(s["passed"] for s in dependent),
                       [s for s in dependent if not s["passed"]]))
            report["sections"]["state_dependent"] = dependent
            decoys = bc.check_decoys(app, urls["clean"])
            add(_check("decoys_change_no_committed_state", all(d["passed"] for d in decoys),
                       [d for d in decoys if not d["passed"]]))
            report["sections"]["decoys"] = decoys
            rid = bc.check_reset_isolation_determinism(app, urls["clean"])
            for name, result in rid.items():
                add(_check(name, result["passed"], result))
            faults = bc.check_faults(app, urls)
            for entry in faults["faults"]:
                add(_check(f"fault {entry['fault_id']}", entry["passed"],
                           {k: v for k, v in entry.items() if k != "minimized_trace"}))
            report["sections"]["faults"] = faults["faults"]
            benign = bc.check_benign(app, urls)
            add(_check("benign_traces_silent_on_both_builds", all(b["passed"] for b in benign),
                       [b for b in benign if not b["passed"]]))
            report["sections"]["benign"] = benign
            walk = bc.random_walk_clean(app, urls["clean"], steps=walk_steps)
            add(_check("clean_random_walk_no_candidates_errors_or_network", walk["passed"], walk))
            report["sections"]["random_walk_clean"] = walk
            network = [w for w in workflows if w.get("foreign_requests")]
            add(_check("no_external_network_at_runtime", not network and not walk["foreign_requests"],
                       network[:3]))

    if builds is not None and docker:
        from . import docker_checks as dc

        if app.has_dockerfile:
            if dc.docker_available():
                result = dc.build_and_run(app, builds["clean"])
                add(_check("docker_offline_build_and_health", result["passed"], result))
            else:
                add(_check("docker_offline_build_and_health", False, "docker daemon unavailable"))
        intake = dc.product_intake(app, builds["clean"])
        add(_check("product_intake_matches_expectation", intake["matches_expectation"], intake))
        report["sections"]["product_intake"] = intake

    report["admitted"] = all(c["passed"] for c in report["checks"])
    report["checks_run"] = len(report["checks"])
    report["checks_failed"] = [c["name"] for c in report["checks"] if not c["passed"]]
    report["browser_checks_run"] = browser
    report["docker_checks_run"] = docker
    report["wall_clock_s"] = round(time.monotonic() - started, 1)
    return report


def _markdown(reports: list[dict], div: dict | None) -> str:
    lines = ["# e2e suite admission", "",
             "| app | admitted | checks | failed | browser | docker | seconds |",
             "|---|---|---|---|---|---|---|"]
    for r in reports:
        lines.append(f"| {r['app_id']} | {'yes' if r['admitted'] else '**no**'} | {r['checks_run']} | "
                     f"{', '.join(r['checks_failed']) or '—'} | {r['browser_checks_run']} | "
                     f"{r['docker_checks_run']} | {r['wall_clock_s']} |")
    lines += ["", "## Seeded faults", "",
              "| fault | subtype | oracle | witness | fires buggy | silent clean | needs own patch |"
              " confirmed | minimized | min. fires | min. silent clean |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in reports:
        for f in r["sections"].get("faults", []):
            lines.append(
                f"| {f['fault_id']} | {f['subtype']} | {f['oracle']} | {f['witness_length']} | "
                f"{f['witness_fires_on_buggy']} | {f['witness_silent_on_clean']} | "
                f"{f['witness_needs_its_own_patch']} | {f.get('confirmed')} | "
                f"{f.get('minimized_length')} | {f.get('minimized_still_fires')} | "
                f"{f.get('minimized_silent_on_clean')} |")
    if div:
        lines += ["", "## Diversity", "", f"passed: {div['passed']}",
                  f"max shared source lines between two apps: {div['max_source_line_jaccard']:.1%}"]
        lines += [f"- {p}" for p in div["problems"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apps", nargs="*")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--docker", action="store_true")
    parser.add_argument("--walk-steps", type=int, default=150)
    args = parser.parse_args()
    apps = [load(a) for a in args.apps] if args.apps else discover()
    ADMISSION_ROOT.mkdir(parents=True, exist_ok=True)
    reports = []
    for app in apps:
        print(f"== {app.app_id}", flush=True)
        report = validate(app, browser=args.browser, docker=args.docker, walk_steps=args.walk_steps)
        (ADMISSION_ROOT / f"{app.app_id}.json").write_text(json.dumps(report, indent=2, default=str),
                                                           encoding="utf-8")
        print(f"   admitted={report['admitted']} failed={report['checks_failed']} "
              f"({report['wall_clock_s']}s)", flush=True)
        reports.append(report)
    div = diversity.analyse(discover()) if not args.apps else None
    summary = {"apps": {r["app_id"]: {"admitted": r["admitted"], "failed": r["checks_failed"]}
                        for r in reports}, "diversity": div}
    if not args.apps:
        (ADMISSION_ROOT / "suite.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        (ADMISSION_ROOT / "suite.md").write_text(_markdown(reports, div), encoding="utf-8")
    return 0 if all(r["admitted"] for r in reports) and (div is None or div["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
