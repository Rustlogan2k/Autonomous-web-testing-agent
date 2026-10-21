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
from urllib.parse import urlsplit

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
    # v1's best AC-DQN arm in the frozen transfer experiment (lowest restricted mean time):
    # dqn_pretrained_nollm - pretrained on benchmark apps without the LLM term, residual head
    # adapting on the target. Checkpoints are the transfer run's own (`V1_CHECKPOINTS`).
    "v1_dqn_pretrained": {"kind": "v1_dqn", "pretrained": True},
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
    # Development variants (suite round 4, 2026-10-08): pairwise operators, technique-first
    # choice and form-submitting buttons as submit controls, with and without the learner.
    "dev_pairwise_nolearn": {
        **HYBRID,
        "planner": "count",
        "pairwise": True,
        "family_choice": True,
        "form_submit": True,
    },
    # Development arms (suite round 5, 2026-10-08), from the RL web-testing literature: the
    # hybrid with curiosity-driven Q-learning choosing its free exploration steps, and a
    # standalone curiosity Q-learning tester (WebExplor / WebRLED components only).
    "dev_hybrid_curiosity": {**HYBRID, "planner": "count", "explore": "curiosity"},
    "dev_curiosity_rl": {"kind": "curiosity_rl"},
    # Tuning round v3 (2026-10-19, rebuilt development apps): one change per arm, then
    # combined. Each is off in every other arm.
    "dev_v3_browse": {**HYBRID, "planner": "count", "browse": True},
    "dev_v3_clickables": {**HYBRID, "planner": "count", "custom_clickables": True},
    "dev_v3_curiosity": {**HYBRID, "planner": "count", "explore": "curiosity"},
    "dev_v3_failfeedback": {**HYBRID, "planner": "count", "failure_bonus": 2.0},
    "dev_pairwise_bandit": {
        **HYBRID,
        "planner": "bandit",
        "pairwise": True,
        "family_choice": True,
        "form_submit": True,
    },
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


def _settings(config: HybridConfig, spec: dict | None = None):
    from dataclasses import replace

    from ..benchmark import EnvSettings
    from ..envs.action_registry import EXTENDED_OPTIONS

    registry = EXTENDED_OPTIONS
    if (spec or {}).get("custom_clickables"):
        registry = replace(EXTENDED_OPTIONS, custom_clickables=True)
    return EnvSettings(
        max_steps=config.episode_steps,
        settle_quiet_ms=config.settle_quiet_ms,
        capture_screenshots=False,
        registry=registry,
    )


#: The frozen transfer experiment's `dqn_nollm` pretraining checkpoints: one per (held-out
#: fold, seed), each pretrained on the other five benchmark applications.
V1_CHECKPOINT_DIRS = tuple(
    REPO_ROOT / "runs" / f"final-cpu-{i}" / "checkpoints" for i in range(1, 5)
)
V1_FOLDS = ("account_hub", "admin_records", "booking_desk", "media_library", "shop_catalog",
            "support_portal")


def v1_checkpoint(seed: int) -> Path:
    """The pretrained network for hunt seed `seed` on an application outside the benchmark:
    fold `V1_FOLDS[seed % 6]`, pretraining seed `seed % 3`, so seeds 0-4 get five distinct
    networks, each pretrained on five benchmark applications."""
    name = f"dqn_nollm__fold-{V1_FOLDS[seed % len(V1_FOLDS)]}__seed-{seed % 3}.pt"
    for directory in V1_CHECKPOINT_DIRS:
        if (directory / name).is_file():
            return directory / name
    raise FileNotFoundError(f"v1 checkpoint {name} not found under runs/final-cpu-*/checkpoints")


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
        if spec.get("pretrained"):
            # As the transfer experiment's dqn_pretrained_nollm hunts: load, then adapt.
            agent = QAgent.load(v1_checkpoint(job.seed), agent_config, rewarder=rewarder)
            agent.enable_adaptation()
        else:
            agent = QAgent(agent_config, rewarder=rewarder)
        agent.set_application(job.app, app_name)
        return agent, None
    if spec["kind"] == "curiosity_rl":
        from .curiosity import CuriosityPolicy

        return CuriosityPolicy(seed=job.seed, episode_steps=config.episode_steps), None
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
        pairwise=bool(spec.get("pairwise", False)),
        family_choice=bool(spec.get("family_choice", False)),
        form_submit=bool(spec.get("form_submit", False)),
        browse=bool(spec.get("browse", False)),
        failure_bonus=float(spec.get("failure_bonus", 0.0)),
    )
    return policy, labeller


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _runner_factory(urls: dict[str, str], env_for, *, events: bool = False):
    """Replay runners per build. With `events` (suite apps), the harness's event-recording
    runner: the primary runner unchanged, returning each step's browser events too, which
    crash-type faults need; every other fault verifies exactly as before."""
    from ..oracle import PlaywrightTrajectoryRunner

    def runner(build: str) -> PlaywrightTrajectoryRunner:
        if events:
            from e2e_suite.harness.ext_oracle import EventTrajectoryRunner

            return EventTrajectoryRunner(
                lambda: env_for(urls[build], max_steps=120), max_steps=120,
                origin=_origin(urls[build]),
            )
        return PlaywrightTrajectoryRunner(
            lambda: env_for(urls[build], max_steps=120), max_steps=120
        )

    return runner


def run_hunt_job(job: HybridJob, config: HybridConfig, out_dir: Path) -> dict:
    """One hunt on a development (benchmark) application or a suite application."""
    from ..benchmark import FaultMonitor, control_key, load_app, make_env, run_hunt, serve_app
    from ..oracle import VerificationPipeline
    from .invariants import attribute, verify
    from .metrics import CoverageCollector, FailureCounter

    settings = _settings(config, ARMS[job.arm])
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
    # Suite v2 apps sit behind a sign-in: every environment - the hunt, its replays, the
    # control builds and the invariant checks - runs the app's sign-in macro first, as the
    # tester's session bootstrap (outside the interaction budget, as for Gitea). Apps without
    # one get exactly the environment they always had.
    login = list(getattr(suite_app, "login_steps", None) or []) if suite else []

    def env_for(url: str, **overrides):  # noqa: ANN202
        return make_env(url, settings, setup_actions=login or None, **overrides)

    with serving as urls:
        runner = _runner_factory(urls, env_for, events=suite)
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
                    origin=_origin(urls[job.build]),
                ),
            )
        else:
            monitor = primary
        env = env_for(urls[job.build])
        # The literature's run metrics, beside seeded faults: unique failures (console and
        # request errors) and client-side code coverage (`metrics.py`).
        failures = FailureCounter()
        coverage = CoverageCollector(_origin(urls[job.build]))
        coverage.install(env)

        def on_step(event: dict) -> None:
            failures.observe(event["info"], event["step"])
            coverage.sample(env)

        try:
            result = run_hunt(env, policy, monitor, budget=config.budget, on_step=on_step)
        finally:
            env.close()
        candidates = getattr(policy, "candidates", [])
        verify_started = time.monotonic()
        verify(
            lambda: env_for(urls[job.build], max_steps=120),
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
                        lambda: env_for(urls["clean"], max_steps=120),
                        {
                            fid: (
                                lambda fid=fid: env_for(urls[key_for(fid)], max_steps=120)
                            )
                            for fid in fault_ids
                        },
                        labeller=labeller,
                        context=name,
                    )
        verify_seconds = time.monotonic() - verify_started
    payload = {
        "hunt": result.to_dict(),
        "failures": failures.summary(),
        "coverage": coverage.summary(),
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
