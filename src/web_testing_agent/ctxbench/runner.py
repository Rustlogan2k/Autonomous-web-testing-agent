"""The contextual diagnostic's runs: pretraining and hunts (pre-registration §3, §4(b)).

Separate from the primary experiment by construction: its own arms, jobs and output
directory (`runs/ctx-*`). What a job *does* is the primary's code, called the same way:
`rl.train.pretrain` for pretraining and `benchmark.run_hunt` with the full verification
pipeline (per-fault controls, replay, minimization) for hunts, under the primary's ε
schedules, LLM reward settings, episode length and settle window. The differences are
the ones the pre-registration names:

- the learned arms differ only in `AgentConfig.input_mask`;
- one pretraining per arm and seed, on the four training variants (1,500 interactions
  each), shared by every held-out variant -- there are no folds;
- hunts of 1,000 interactions on held-out variants and 2,000 on the pristine primary
  applications, residual head adapting; no top-up rule;
- every hunt also counts selections of a decoy control (diagnostic 5).

    python -m web_testing_agent.ctxbench.runner --out-dir runs/ctx-main --workers 4
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..experiments.transfer import (
    REPO_ROOT,
    ExperimentConfig,
    Schedule,
    _epsilon_config,
    _scorer,
    _write_json,
    provenance,
    run_schedule,
)
from ..utils.logging import get_logger
from .generate import OUT_ROOT as CTX_ROOT

logger = get_logger(__name__)

PRIMARY_ROOT = REPO_ROOT / "tests" / "fixtures" / "benchmark"
TRAINING = ("ctx_v0_s1_d0", "ctx_v0_s2_d0", "ctx_v1_s1_d0", "ctx_v1_s2_d0")
HELD_OUT = ("ctx_vs_s1_d0", "ctx_vu1_s1_d0", "ctx_vu2_s1_d0", "ctx_v0_s3_d0", "ctx_v0_s4_d0",
            "ctx_v0_s5_d0", "ctx_v0_s1_d1", "ctx_vu1_s5_d1")
PRISTINE = ("admin_records", "support_portal", "media_library", "account_hub")
SEEDS = (0, 1, 2)
#: Controls the generator renders as decoys in D1 variants (`window.__ctxDecoys`).
DECOY_IDS = ("decoy-advance",)

ARMS: dict[str, dict] = {
    "ctx_full":     {"kind": "agent", "input_mask": "none"},
    "action_only":  {"kind": "agent", "input_mask": "action_only"},
    "lexical_only": {"kind": "agent", "input_mask": "lexical_only"},
    "label_blind":  {"kind": "agent", "input_mask": "label_blind"},
    "search_S":     {"kind": "feature_search"},
    "random":       {"kind": "random"},
}
LEARNED = tuple(a for a, spec in ARMS.items() if spec["kind"] == "agent")

_PRIMARY = ExperimentConfig()


@dataclass(frozen=True)
class CtxConfig:
    """Held fixed across every diagnostic job. Defaults are the primary's settings."""

    pretrain_steps_per_app: int = _PRIMARY.pretrain_steps_per_app
    heldout_budget: int = 1000
    pristine_budget: int = _PRIMARY.budget
    episode_steps: int = _PRIMARY.episode_steps
    settle_quiet_ms: int = _PRIMARY.settle_quiet_ms
    llm_weight: float = _PRIMARY.llm_weight
    llm_model: str = _PRIMARY.llm_model
    llm_offline: bool = _PRIMARY.llm_offline
    max_minimization_trials: int = _PRIMARY.max_minimization_trials
    target_epsilon: tuple[float, float, float] = _PRIMARY.target_epsilon
    pretrain_epsilon: tuple[float, float, float] = _PRIMARY.pretrain_epsilon

    def to_dict(self) -> dict:
        return asdict(self)

    def budget(self, app: str) -> int:
        return self.pristine_budget if app in PRISTINE else self.heldout_budget


@dataclass(frozen=True)
class CtxJob:
    kind: str                 # "pretrain" | "hunt"
    arm: str
    seed: int
    app: str = ""             # hunt jobs: the application hunted
    train_apps: tuple[str, ...] = field(default_factory=tuple)

    @property
    def job_id(self) -> str:
        if self.kind == "pretrain":
            return f"pretrain__{self.arm}__seed-{self.seed}"
        return f"hunt__{self.arm}__{self.app}__seed-{self.seed}"

    def depends_on(self) -> str | None:
        if self.kind == "hunt" and self.arm in LEARNED:
            return CtxJob("pretrain", self.arm, self.seed).job_id
        return None

    def to_dict(self) -> dict:
        return {**asdict(self), "job_id": self.job_id}


def build_plan(arms: tuple[str, ...] = tuple(ARMS), seeds: tuple[int, ...] = SEEDS,
               apps: tuple[str, ...] = HELD_OUT + PRISTINE) -> list[CtxJob]:
    """All pretraining first, then every hunt, held-out variants before pristine apps."""
    jobs = [CtxJob("pretrain", arm, seed, train_apps=TRAINING)
            for arm in arms if arm in LEARNED for seed in seeds]
    jobs += [CtxJob("hunt", arm, seed, app=app) for app in apps for arm in arms for seed in seeds]
    return jobs


def app_root(app: str) -> Path:
    return PRIMARY_ROOT if app in PRISTINE else CTX_ROOT


def checkpoint_path(out_dir: Path, arm: str, seed: int) -> Path:
    return Path(out_dir) / "checkpoints" / f"{arm}__seed-{seed}.pt"


def result_path(out_dir: Path, job: CtxJob) -> Path:
    return Path(out_dir) / "results" / f"{job.job_id}.json"


def _settings(config: CtxConfig):
    from ..benchmark import EnvSettings

    return EnvSettings(max_steps=config.episode_steps, settle_quiet_ms=config.settle_quiet_ms,
                       capture_screenshots=False)


def _rewarder(config: CtxConfig, agent_config, log):
    from ..rl import RewardComposer, RewardConfig

    # Every learned arm carries the live LLM reward at the primary's settings (§3).
    return RewardComposer(RewardConfig(llm_weight=config.llm_weight, shaping_gamma=agent_config.gamma),
                          _scorer(config), log)


def run_pretrain(job: CtxJob, config: CtxConfig, out_dir: Path) -> dict:
    import contextlib

    from ..benchmark import load_app
    from ..benchmark.suite import serve_app
    from ..rl import AgentConfig, QAgent, RewardLog
    from ..rl.train import pretrain

    apps = [load_app(a, CTX_ROOT) for a in job.train_apps]
    total = config.pretrain_steps_per_app * len(apps)
    agent_config = AgentConfig(algorithm="dqn", seed=job.seed, total_steps=total, gamma=0.95,
                               input_mask=ARMS[job.arm]["input_mask"],
                               **_epsilon_config(config.pretrain_epsilon, total))
    log = RewardLog(Path(out_dir) / "reward_logs" / f"{job.job_id}.jsonl", run=job.to_dict())
    agent = QAgent(agent_config, rewarder=_rewarder(config, agent_config, log))
    curve: list[dict] = []
    with contextlib.ExitStack() as stack:
        urls = {app.app_id: stack.enter_context(serve_app(app, builds=("buggy",)))["buggy"]
                for app in apps}
        report = pretrain(agent, apps, urls, steps_per_app=config.pretrain_steps_per_app,
                          settings=_settings(config),
                          on_episode=lambda app, n, row: curve.append({"app": app, "episode": n, **row}))
    log.close()
    checkpoint = checkpoint_path(out_dir, job.arm, job.seed)
    agent.save(checkpoint, meta={"job": job.to_dict(), "report": report.to_dict()})
    return {"pretrain": report.to_dict(), "agent": agent.summary(),
            "checkpoint": str(checkpoint), "learning_curve": curve}


def _build_policy(job: CtxJob, config: CtxConfig, out_dir: Path, app_name: str):
    from ..envs.types import MAX_ACTIONS
    from ..evaluation.rollout import RandomPolicy
    from ..rl import AgentConfig, FeatureSearchPolicy, QAgent, RewardLog

    spec = ARMS[job.arm]
    if spec["kind"] == "random":
        return RandomPolicy(MAX_ACTIONS, seed=job.seed, valid_only=True), {"arm": job.arm}
    if spec["kind"] == "feature_search":
        policy = FeatureSearchPolicy(seed=job.seed, epsilon=0.05)
        return policy, policy.to_dict()

    budget = config.budget(job.app)
    log = RewardLog(Path(out_dir) / "reward_logs" / f"{job.job_id}.jsonl", run=job.to_dict())
    agent_config = AgentConfig(algorithm="dqn", seed=job.seed, total_steps=budget, gamma=0.95,
                               learn=True, input_mask=spec["input_mask"],
                               **_epsilon_config(config.target_epsilon, budget))
    agent = QAgent.load(checkpoint_path(out_dir, job.arm, job.seed), agent_config,
                        rewarder=_rewarder(config, agent_config, log))
    agent.enable_adaptation()
    agent.set_application(job.app, app_name)
    agent._reward_log = log  # closed by the caller
    return agent, {"arm": job.arm, **spec}


def run_hunt_job(job: CtxJob, config: CtxConfig, out_dir: Path) -> dict:
    from ..benchmark import FaultMonitor, control_key, load_app, make_env, run_hunt, serve_app
    from ..oracle import PlaywrightTrajectoryRunner, VerificationPipeline

    app = load_app(job.app, app_root(job.app))
    settings = _settings(config)
    policy, description = _build_policy(job, config, out_dir, app.manifest.name)
    decoy_selectors = {f'[id="{d}"]' for d in DECOY_IDS} if job.app.endswith("_d1") else set()
    decoys = {"selections": 0, "by_episode": {}}

    def on_step(row: dict) -> None:
        executed = row.get("action")
        if executed is not None and executed.selector in decoy_selectors:
            decoys["selections"] += 1
            episode = str(row["episode"])
            decoys["by_episode"][episode] = decoys["by_episode"].get(episode, 0) + 1

    with serve_app(app, controls=True) as urls:
        def runner(build: str) -> PlaywrightTrajectoryRunner:
            return PlaywrightTrajectoryRunner(
                lambda: make_env(urls[build], settings, max_steps=120), max_steps=120)

        controls = {f.fault_id: runner(control_key(f.fault_id)) for f in app.manifest.faults}
        pipeline = VerificationPipeline(
            app.manifest, runner("buggy"), control_runner=runner("clean"),
            control_runners=controls, max_minimization_trials=config.max_minimization_trials)
        monitor = FaultMonitor(pipeline)
        env = make_env(urls["buggy"], settings)
        try:
            result = run_hunt(env, policy, monitor, budget=config.budget(job.app), on_step=on_step)
        finally:
            env.close()
    log = getattr(policy, "_reward_log", None)
    if log is not None:
        log.close()
    decoys["has_decoy"] = bool(decoy_selectors)
    decoys["per_episode"] = round(decoys["selections"] / max(result.episodes, 1), 4)
    payload = {"hunt": result.to_dict(), "policy": description, "decoys": decoys,
               "app_root": str(app_root(job.app))}
    if hasattr(policy, "summary"):
        payload["agent"] = policy.summary()
    elif hasattr(policy, "to_dict"):
        payload["policy_state"] = policy.to_dict()
    return payload


def run_job(job: CtxJob, config: CtxConfig, out_dir: Path) -> dict:
    """Run one job to completion and write its result file. Never raises."""
    import torch

    torch.set_num_threads(1)
    started = time.monotonic()
    record = {"job": job.to_dict(), "provenance": provenance(config)}
    try:
        body = (run_pretrain if job.kind == "pretrain" else run_hunt_job)(job, config, out_dir)
        record.update(status="ok", **body)
    except Exception as exc:  # noqa: BLE001 - a failed job is recorded, and retried on restart
        logger.exception("job {} failed", job.job_id)
        record.update(status="error", error=f"{type(exc).__name__}: {exc}",
                      traceback=traceback.format_exc())
    record["wall_clock_s"] = round(time.monotonic() - started, 1)
    _write_json(result_path(out_dir, job), record)
    return record


def _job_main(arguments: argparse.Namespace) -> int:
    stored = json.loads(arguments.config.read_text(encoding="utf-8"))["config"]
    config = CtxConfig(**{k: tuple(v) if isinstance(v, list) else v for k, v in stored.items()})
    raw = json.loads(arguments.job)
    raw.pop("job_id", None)
    raw["train_apps"] = tuple(raw.get("train_apps") or ())
    record = run_job(CtxJob(**raw), config, arguments.out_dir)
    return 0 if record["status"] == "ok" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the contextual diagnostic's jobs.")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--arms", nargs="*", default=list(ARMS))
    parser.add_argument("--seeds", nargs="*", type=int, default=list(SEEDS))
    parser.add_argument("--apps", nargs="*", default=list(HELD_OUT + PRISTINE))
    parser.add_argument("--config", type=Path, help="(internal) run one job under this config")
    parser.add_argument("--job", help="(internal) the job, as JSON")
    arguments = parser.parse_args(argv)
    if arguments.job:
        return _job_main(arguments)
    unknown = set(arguments.arms) - set(ARMS)
    if unknown:
        parser.error(f"unknown arm(s): {sorted(unknown)}")
    jobs = build_plan(tuple(arguments.arms), tuple(arguments.seeds), tuple(arguments.apps))
    summary = run_schedule(Schedule(jobs, arguments.out_dir, CtxConfig(), workers=arguments.workers,
                                    module="web_testing_agent.ctxbench.runner"))
    logger.info("schedule finished: {}", summary)
    return 0 if not summary["failed"] and summary["done"] == summary["jobs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
