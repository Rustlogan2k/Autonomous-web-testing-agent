"""Admission control for the benchmark: is every seeded fault real, reachable and checkable?

An application enters the suite only after this passes. The reason is what a failure here
would otherwise look like downstream: a fault whose trigger is unreachable through the
agent's action space, or whose assertion never fires, produces a fold in which every arm
is censored at the full budget. That reads as "the application is hard" and is actually
"the fixture is broken", and nothing in the transfer result distinguishes the two.

For every seeded fault, in a real browser:

1. **The reachability witness triggers it on the faulty build.** The fault's
   `trigger_trace` is replayed with no unresolved step, and the assertion fires.
2. **The same witness does not fire on the clean build.** This is what makes 1 mean
   anything: an assertion that fires on a correct application is measuring the trace.
3. **The oracle confirms it end to end** -- clean-build control, reproduction rule,
   delta-debugging minimization -- exactly as it will during an experiment.
4. **The minimized trace still triggers it**, and still does not fire on the clean build.

And for the application as a whole:

5. **The fault metadata is well formed** (`BenchmarkApp.validate`).
6. **The faulty build is exactly the clean build plus the declared patches**
   (`build.py --check`), so the only differences between the builds are the seeded ones.
7. **A correct walk through the workflow trips nothing**, on either build: every
   `benign_trace` replays cleanly and no assertion fires.

Optionally, a random walk measures three things that are properties of the fixture rather
than of any agent: how often the raw assertions fire on the *clean* build (the oracle's
unsoundness before the control is applied), whether the clean build emits any deterministic
trigger at all (8, below), and how far uniform-random exploration gets on the faulty build
(the difficulty floor).

8. **The clean build is silent to the deterministic triggers**: no console error, failed
   request, dead navigation control or runaway page during the random walk. The seeded
   faults are meant to be silent; a fixture that throws errors on its own gives every arm
   free findings unrelated to the faults, and makes the trigger channel measure the fixture.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field

from ..evaluation.rollout import RandomPolicy
from ..envs.types import MAX_ACTIONS
from ..oracle import PlaywrightTrajectoryRunner, VerificationPipeline, verify_all, verify_fault
from ..utils.logging import get_logger
from .hunt import run_hunt
from .monitor import FaultMonitor
from .suite import EnvSettings, SuiteApp, make_env, serve_app

logger = get_logger(__name__)

#: A replay may need more steps than an exploration episode: every scripted step can be
#: retried while its target renders.
REPLAY_MAX_STEPS = 120


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AppValidation:
    app_id: str
    checks: list[Check] = field(default_factory=list)
    faults: list[dict] = field(default_factory=list)
    random_walk: dict = field(default_factory=dict)
    wall_clock_s: float = 0.0

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def add(self, name: str, passed: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, bool(passed), detail))
        if not passed:
            logger.warning("[{}] FAILED {}: {}", self.app_id, name, detail)
        return bool(passed)

    def to_dict(self) -> dict:
        return {
            "app_id": self.app_id,
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
            "faults": self.faults,
            "random_walk": self.random_walk,
            "wall_clock_s": self.wall_clock_s,
        }


def _runner(url: str, settings: EnvSettings) -> PlaywrightTrajectoryRunner:
    return PlaywrightTrajectoryRunner(
        lambda: make_env(url, settings, max_steps=REPLAY_MAX_STEPS), max_steps=REPLAY_MAX_STEPS)


def build_in_sync(app: SuiteApp) -> tuple[bool, str]:
    """Whether `buggy/` is exactly `clean/` plus the patches `build.py` declares."""
    script = app.root / "build.py"
    if not script.is_file():
        return False, "no build.py: the faulty build cannot be shown to derive from the clean one"
    completed = subprocess.run([sys.executable, str(script), "--check"],  # noqa: S603
                               capture_output=True, text=True, timeout=120, check=False)
    output = (completed.stdout + completed.stderr).strip().splitlines()
    return completed.returncode == 0, (output[-1] if output else f"exit {completed.returncode}")


def validate_app(app: SuiteApp, settings: EnvSettings | None = None, *,
                 random_steps: int = 0, seed: int = 0,
                 max_minimization_trials: int = 40) -> AppValidation:
    """Run every admission check for one application. Never raises on a failing check."""
    settings = settings or EnvSettings()
    report = AppValidation(app.app_id)
    started = time.monotonic()
    manifest = app.manifest

    problems = manifest.validate()
    report.add("metadata_well_formed", not problems, "; ".join(problems))
    in_sync, detail = build_in_sync(app)
    report.add("faulty_build_is_clean_plus_patches", in_sync, detail)
    report.add("has_benign_trace", bool(manifest.benign_traces),
               "an application must declare at least one correct walk through its workflow")
    if problems:
        report.wall_clock_s = round(time.monotonic() - started, 2)
        return report

    with serve_app(app) as urls:
        faulty, clean = _runner(urls["buggy"], settings), _runner(urls["clean"], settings)
        pipeline = VerificationPipeline(manifest, faulty, control_runner=clean,
                                        max_minimization_trials=max_minimization_trials)

        for fault in manifest.faults:
            entry: dict = {"fault_id": fault.fault_id, "fault_class": fault.fault_class.value,
                           "witness_length": len(fault.trigger_trace)}
            prefix = fault.fault_id
            if not report.add(f"{prefix}:has_reachability_witness", bool(fault.trigger_trace),
                              "faults.json declares no trigger_trace for this fault"):
                report.faults.append(entry)
                continue
            witness = list(fault.trigger_trace)

            observed = faulty.run(witness)
            report.add(f"{prefix}:witness_replays_on_faulty_build", observed.replay_misses == 0,
                       f"{observed.replay_misses} scripted step(s) did not resolve; "
                       f"urls={[s.url.rsplit('/', 1)[-1] for s in observed.steps]}")
            verdict = verify_fault(fault, manifest, observed)
            report.add(f"{prefix}:witness_fires_on_faulty_build", verdict.violated,
                       "; ".join(f"{r.kind}: violated={r.violated} inconclusive={r.inconclusive}"
                                 f" {r.detail} observed={r.observed[:60]!r}"
                                 for r in verdict.results))

            on_clean = clean.run(witness)
            clean_verdict = verify_fault(fault, manifest, on_clean)
            report.add(f"{prefix}:witness_silent_on_clean_build", not clean_verdict.violated,
                       "the assertion fires on the build with no seeded fault")

            finding = pipeline.confirm(fault, observed, budget_at_discovery=len(witness))
            report.add(f"{prefix}:oracle_confirms", finding.confirmed, finding.rejected_because)
            entry.update({
                "confirmed": finding.confirmed,
                "minimized_length": finding.minimization.get("minimized_length"),
                "minimization_trials": finding.minimization.get("trials"),
                "minimization_exhausted": finding.minimization.get("exhausted_budget"),
                "minimized_trace": finding.minimized_trace,
                "declared_shortest_path": fault.shortest_path,
                "verification_steps": finding.verification_steps,
                "verification_seconds": finding.wall_clock_s,
            })
            if finding.confirmed:
                again = verify_fault(fault, manifest, faulty.run(finding.minimized_trace))
                control = verify_fault(fault, manifest, clean.run(finding.minimized_trace))
                report.add(f"{prefix}:minimized_trace_still_fires", again.violated)
                report.add(f"{prefix}:minimized_trace_silent_on_clean", not control.violated)
            report.faults.append(entry)

        for number, trace in enumerate(manifest.benign_traces):
            for build, runner in (("buggy", faulty), ("clean", clean)):
                walked = runner.run(list(trace))
                fired = [v.fault_id for v in verify_all(manifest, walked) if v.violated]
                report.add(f"benign_{number}:replays_on_{build}", walked.replay_misses == 0,
                           f"{walked.replay_misses} scripted step(s) did not resolve")
                report.add(f"benign_{number}:silent_on_{build}", not fired,
                           f"a correct walk tripped {fired}")

        if random_steps > 0:
            report.random_walk = _random_walks(app, urls, settings, pipeline, random_steps, seed)
            noisy = report.random_walk["clean"]["deterministic_triggers"]
            report.add("clean_build_silent_to_deterministic_triggers", not noisy,
                       "; ".join(f"{t['kind']} on {t['page']}: {t['detail']}" for t in noisy[:5]))

    report.wall_clock_s = round(time.monotonic() - started, 2)
    return report


def _random_walks(app: SuiteApp, urls: dict[str, str], settings: EnvSettings,
                  pipeline: VerificationPipeline, steps: int, seed: int) -> dict:
    """Uniform-random exploration of both builds: oracle unsoundness, and the difficulty floor."""
    out: dict = {"steps": steps, "seed": seed}

    # Clean build, candidates counted but never verified: how often do the raw assertions
    # fire on a correct application? Every one of these is a trace artefact by definition.
    # The deterministic triggers are watched on the same walk, also unverified.
    from ..tester.triggers import CompositeMonitor, TriggerMonitor

    env = make_env(urls["clean"], settings)
    try:
        silent = FaultMonitor(VerificationPipeline(app.manifest, pipeline.control_runner),
                              verify=False)
        triggers = TriggerMonitor(replayer=None, verify=False)  # type: ignore[arg-type]
        run_hunt(env, RandomPolicy(MAX_ACTIONS, seed=seed, valid_only=True),
                 CompositeMonitor(seeded=silent, triggers=triggers), budget=steps)
        clean_run_oracle = silent.summary()
    finally:
        env.close()
    timing = {"slow_response"}
    trigger_findings = [f for f in triggers.summary()["findings"] if f["kind"] not in timing]
    out["clean"] = {
        "raw_candidates": clean_run_oracle["candidates"],
        "raw_candidates_per_1000_steps": round(1000 * clean_run_oracle["candidates"] / steps, 2),
        "by_fault": {f["fault_id"]: f["candidates"] for f in clean_run_oracle["faults"]},
        "deterministic_triggers": [{"kind": f["kind"], "page": f["page"], "detail": f["detail"]}
                                   for f in trigger_findings],
        "timing_triggers": sum(1 for f in triggers.summary()["findings"] if f["kind"] in timing),
    }

    # Faulty build, full verification: what does no policy at all achieve?
    env = make_env(urls["buggy"], settings)
    try:
        monitor = FaultMonitor(pipeline)
        buggy_run = run_hunt(env, RandomPolicy(MAX_ACTIONS, seed=seed, valid_only=True),
                             monitor, budget=steps)
    finally:
        env.close()
    out["buggy"] = {
        "time_to_first_verified": buggy_run.time_to_first_verified,
        "censored": buggy_run.censored,
        "verified": buggy_run.oracle["verified"],
        "reached": buggy_run.oracle["reached"],
        "rejections": buggy_run.oracle["rejections"],
        "unique_states": buggy_run.unique_states,
        "steps_per_second": buggy_run.steps_per_second,
        "verify_seconds": buggy_run.verify_seconds,
    }
    return out
