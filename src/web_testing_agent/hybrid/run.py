"""Runs of the hybrid tester: development runs now, the held-out evaluation later.

Every job is one hunt under v1's protocol (`benchmark.run_hunt`, the seeded-fault oracle with
per-fault controls, replay and minimization; 40-step episodes; every interaction counted).
After the hunt, the generic-invariant candidates the policy raised are replayed twice each in
fresh sessions (`invariants.verify`) — reported separately from the seeded-fault oracle.

    python -m web_testing_agent.hybrid.run --out-dir runs/v2-dev --apps shop_catalog \\
        --arms hybrid_nolearn hybrid_bandit --seeds 0 1 --workers 4 [--clean]
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..experiments.transfer import REPO_ROOT, Schedule, _write_json, provenance, run_schedule
from ..utils.logging import get_logger

logger = get_logger(__name__)

BENCHMARK_ROOT = REPO_ROOT / "tests" / "fixtures" / "benchmark"
DEVELOPMENT_APPS = (
    "shop_catalog",
    "booking_desk",
    "admin_records",
    "support_portal",
    "media_library",
    "account_hub",
)

#: Final hybrid configuration (development round 3, 2026-10-07): weighted free exploration and
#: a preference for deeper archived states, the same for every hybrid arm so that the arms
#: differ only in what each comparison names.
HYBRID = {"kind": "hybrid", "labels": "deterministic", "explore": "weighted", "depth_bias": 2.0}

ARMS: dict[str, dict] = {
    "random": {"kind": "random"},
    # v1's strongest non-learning baseline, unchanged (`rl.search.FeatureSearchPolicy`).
    "search_S": {"kind": "feature_search"},
    # v1's DQN from scratch, as in the primary's dqn_scratch_nollm arm: the primary found no
    # benefit from the LLM reward, so the v1 learner is compared without it.
    "v1_dqn": {"kind": "v1_dqn"},
    "hybrid_nolearn": {**HYBRID, "planner": "count"},
    "hybrid_bandit": {**HYBRID, "planner": "bandit"},
    # Exploratory: labeller v3's labels, which failed their accuracy gates (owner's option A).
    "hybrid_bandit_llm": {**HYBRID, "planner": "bandit", "labels": "llm"},
    # The bandit starting from the development applications' pooled evidence (hybrid/prior.py).
    "hybrid_bandit_prior": {**HYBRID, "planner": "bandit", "prior": "data/v2_prior.json"},
    # Development variants compared in round 3 (kept so those results stay reproducible).
    "dev_bandit_search": {**HYBRID, "planner": "bandit", "explore": "search", "depth_bias": 0.0},
    "dev_bandit_deep": {**HYBRID, "planner": "bandit"},
    "dev_bandit_search_deep": {**HYBRID, "planner": "bandit", "explore": "search"},
    # Development variants of the bandit's outcome signal (suite round 3, 2026-10-08).
    "dev_bandit_novelty": {**HYBRID, "planner": "bandit", "outcome": "novelty"},
    "dev_bandit_surprise": {**HYBRID, "planner": "bandit", "outcome": "surprise"},
}


@dataclass(frozen=True)
class HybridConfig:
    budget: int = 2000
    episode_steps: int = 40
    settle_quiet_ms: int = 80
    max_minimization_trials: int = 30
    invariant_replays: int = 2
    invariant_verify_limit: int = 20

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class HybridJob:
    arm: str
    app: str
    seed: int
    build: str = "buggy"  # "buggy" | "clean" (a false-positive control)
    kind: str = "hunt"
    train_apps: tuple[str, ...] = field(default_factory=tuple)

    @property
    def job_id(self) -> str:
        suffix = "" if self.build == "buggy" else f"__{self.build}"
        return f"hunt__{self.arm}__{self.app}__seed-{self.seed}{suffix}"

    def depends_on(self) -> str | None:
        return None

    def to_dict(self) -> dict:
        return {**asdict(self), "job_id": self.job_id}


def _settings(config: HybridConfig):
    from ..benchmark import EnvSettings

    return EnvSettings(
        max_steps=config.episode_steps,
        settle_quiet_ms=config.settle_quiet_ms,
        capture_screenshots=False,
    )


def _policy(job: HybridJob, app_name: str, config: HybridConfig):
    spec = ARMS[job.arm]
    if spec["kind"] == "random":
        from ..envs.types import MAX_ACTIONS
        from ..evaluation.rollout import RandomPolicy

        return RandomPolicy(MAX_ACTIONS, seed=job.seed, valid_only=True), None
    if spec["kind"] == "feature_search":
        from ..rl import FeatureSearchPolicy

        return FeatureSearchPolicy(seed=job.seed, epsilon=0.05), None
    if spec["kind"] == "v1_dqn":
        from ..experiments.transfer import ExperimentConfig, _epsilon_config
        from ..rl import AgentConfig, QAgent, RewardComposer, RewardConfig

        primary = ExperimentConfig()
        agent_config = AgentConfig(
            algorithm="dqn",
            seed=job.seed,
            total_steps=config.budget,
            gamma=0.95,
            learn=True,
            **_epsilon_config(primary.target_epsilon, config.budget),
        )
        rewarder = RewardComposer(RewardConfig(llm_weight=0.0, shaping_gamma=agent_config.gamma))
        agent = QAgent(agent_config, rewarder=rewarder)
        agent.set_application(job.app, app_name)
        return agent, None
    from .learner import BanditPlanner
    from .policy import HybridPolicy

    labeller = None
    if spec["labels"] == "llm":
        from .labels import SemanticLabeller

        labeller = SemanticLabeller()
    planner = BanditPlanner if spec["planner"] == "bandit" else None
    if spec.get("prior"):
        import functools

        from .prior import load_prior

        planner = functools.partial(BanditPlanner, prior=load_prior(REPO_ROOT / spec["prior"]))
    policy = HybridPolicy(
        labeller=labeller,
        context=app_name,
        seed=job.seed,
        planner_factory=planner,
        episode_steps=config.episode_steps,
        explore=spec.get("explore", "weighted"),
        depth_bias=float(spec.get("depth_bias", 0.0)),
        outcome=spec.get("outcome", "alert"),
    )
    return policy, labeller


def _runner_factory(urls: dict[str, str], settings):
    from ..benchmark import make_env
    from ..oracle import PlaywrightTrajectoryRunner

    def runner(build: str) -> PlaywrightTrajectoryRunner:
        return PlaywrightTrajectoryRunner(
            lambda: make_env(urls[build], settings, max_steps=120), max_steps=120
        )

    return runner


def run_hunt_job(job: HybridJob, config: HybridConfig, out_dir: Path) -> dict:
    """One hunt on a development (benchmark) application or a suite application."""
    from ..benchmark import FaultMonitor, control_key, load_app, make_env, run_hunt, serve_app
    from ..oracle import VerificationPipeline
    from .invariants import attribute, verify

    settings = _settings(config)
    suite = job.app not in DEVELOPMENT_APPS
    if suite:
        from e2e_suite.harness.variants import control_key as suite_control_key

        from .suite import ExtensionMonitor, SuiteMonitor, load_suite_app, serve_suite_app

        key_for = suite_control_key  # the suite names its control builds "without-<id>"
        suite_app = load_suite_app(job.app)
        manifest = suite_app.oracle_app()
        name = suite_app.data.get("name", job.app)
        serving = serve_suite_app(suite_app, controls=job.build == "buggy")
    else:
        key_for = control_key  # the benchmark names them "without:<id>"
        app = load_app(job.app, BENCHMARK_ROOT)
        manifest, name = app.manifest, app.manifest.name
        serving = serve_app(app, controls=True)
    policy, labeller = _policy(job, name, config)

    with serving as urls:
        runner = _runner_factory(urls, settings)
        if job.build == "buggy":
            controls = {f.fault_id: runner(key_for(f.fault_id)) for f in manifest.faults}
        else:
            controls = {}
        primary = None
        if manifest.faults:
            primary = FaultMonitor(
                VerificationPipeline(
                    manifest,
                    runner(job.build),
                    control_runner=runner("clean"),
                    control_runners=controls,
                    max_minimization_trials=config.max_minimization_trials,
                )
            )
        if suite:
            # On a clean-build control job there is no per-fault build; every candidate is
            # checked against the clean build, as for the primary oracle.
            ext_controls = {
                f["fault_id"]: runner(key_for(f["fault_id"]) if job.build == "buggy" else "clean")
                for f in suite_app.faults_by_oracle("extension")
            }
            monitor = SuiteMonitor(
                primary,
                ExtensionMonitor(
                    suite_app,
                    faulty=runner(job.build),
                    clean=runner("clean"),
                    controls=ext_controls,
                    max_trials=config.max_minimization_trials,
                ),
            )
        else:
            monitor = primary
        env = make_env(urls[job.build], settings)
        try:
            result = run_hunt(env, policy, monitor, budget=config.budget)
        finally:
            env.close()
        candidates = getattr(policy, "candidates", [])
        verify_started = time.monotonic()
        verify(
            lambda: make_env(urls[job.build], settings, max_steps=120),
            candidates,
            replays=config.invariant_replays,
            limit=config.invariant_verify_limit,
            labeller=labeller,
            context=name,
        )
        if job.build == "buggy":
            # Spec-less detection: attribute each fully reproduced candidate to the seeded
            # fault it depends on, by the oracle's own per-fault control rule.
            fault_ids = (
                [f["fault_id"] for f in suite_app.faults]
                if suite
                else [f.fault_id for f in manifest.faults]
            )
            for candidate in candidates[: config.invariant_verify_limit]:
                if candidate.reproduced == config.invariant_replays:
                    attribute(
                        candidate,
                        lambda: make_env(urls["clean"], settings, max_steps=120),
                        {
                            fid: (
                                lambda fid=fid: make_env(
                                    urls[key_for(fid)], settings, max_steps=120
                                )
                            )
                            for fid in fault_ids
                        },
                        labeller=labeller,
                        context=name,
                    )
        verify_seconds = time.monotonic() - verify_started
    payload = {
        "hunt": result.to_dict(),
        "arm": {"name": job.arm, **ARMS[job.arm]},
        "suite": suite,
        "invariants": {
            "candidates": [c.to_dict() for c in candidates],
            "verify_seconds": round(verify_seconds, 1),
        },
    }
    if hasattr(policy, "summary"):
        payload["policy"] = policy.summary()
        payload["policy"].pop("invariant_candidates", None)
    if hasattr(policy, "planner") and hasattr(policy.planner, "state"):
        payload["bandit_state"] = policy.planner.state()
    return payload


def result_path(out_dir: Path, job: HybridJob) -> Path:
    return Path(out_dir) / "results" / f"{job.job_id}.json"


def run_job(job: HybridJob, config: HybridConfig, out_dir: Path) -> dict:
    import torch

    torch.set_num_threads(1)
    started = time.monotonic()
    record = {"job": job.to_dict(), "provenance": provenance(config)}
    try:
        record.update(status="ok", **run_hunt_job(job, config, out_dir))
    except Exception as exc:  # noqa: BLE001 - a failed job is recorded, and retried on restart
        logger.exception("job {} failed", job.job_id)
        record.update(
            status="error", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()
        )
    record["wall_clock_s"] = round(time.monotonic() - started, 1)
    _write_json(result_path(out_dir, job), record)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run hybrid-tester hunts.")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--apps", nargs="*", default=list(DEVELOPMENT_APPS))
    parser.add_argument("--arms", nargs="*", default=["hybrid_nolearn", "hybrid_bandit"])
    parser.add_argument("--seeds", nargs="*", type=int, default=[0, 1])
    parser.add_argument(
        "--clean", action="store_true", help="also run each arm once on clean builds"
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--budget", type=int, default=HybridConfig.budget)
    parser.add_argument("--config", type=Path, help="(internal)")
    parser.add_argument("--job", help="(internal)")
    arguments = parser.parse_args(argv)
    if arguments.job:
        stored = json.loads(arguments.config.read_text(encoding="utf-8"))["config"]
        raw = json.loads(arguments.job)
        raw.pop("job_id", None)
        raw["train_apps"] = tuple(raw.get("train_apps") or ())
        record = run_job(HybridJob(**raw), HybridConfig(**stored), arguments.out_dir)
        return 0 if record["status"] == "ok" else 1
    unknown = set(arguments.arms) - set(ARMS)
    if unknown:
        parser.error(f"unknown arm(s): {sorted(unknown)}")
    jobs = [
        HybridJob(arm, app, seed)
        for app in arguments.apps
        for arm in arguments.arms
        for seed in arguments.seeds
    ]
    if arguments.clean:
        jobs += [
            HybridJob(arm, app, arguments.seeds[0], build="clean")
            for app in arguments.apps
            for arm in arguments.arms
        ]
    summary = run_schedule(
        Schedule(
            jobs,
            arguments.out_dir,
            HybridConfig(budget=arguments.budget),
            workers=arguments.workers,
            module="web_testing_agent.hybrid.run",
        )
    )
    logger.info("schedule finished: {}", summary)
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
