"""The leave-one-application-out transfer experiment, as a set of independent, resumable jobs.

**A job is the unit of failure.** Every run -- one pretraining of one agent for one fold and
seed, or one budgeted hunt of one arm on one held-out application -- is a separate process
that writes exactly one result file when it finishes, atomically. A run that crashes, or a
machine that is switched off, loses that run and nothing else: restarting the experiment
skips every job whose result file already exists and continues with the rest. A run that
failed writes an error record instead, which a restart retries.

**The plan is data.** `build_plan()` returns the complete list of jobs for a suite, a set of
seeds and a configuration, mechanically: fold *i* holds out application *i*, every arm is
run on every fold for every seed, and every pretrained arm depends on the pretraining job
for its own fold and seed. Nothing is selected by hand, which is what the pre-registration
means by "no fold is chosen".

**Every result records what produced it**: the commit, whether the tree was dirty, the job's
full configuration, the environment settings, the LLM configuration and the timing. A
number that cannot be traced back to those is not reported.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..utils.logging import get_logger

logger = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]


# -- configuration ------------------------------------------------------------------------


@dataclass(frozen=True)
class ExperimentConfig:
    """Everything that is held fixed across all jobs of one experiment."""

    budget: int = 2000
    episode_steps: int = 40
    settle_quiet_ms: int = 80
    pretrain_steps_per_app: int = 1500
    llm_weight: float = 1.0
    llm_model: str = "qwen2.5:7b-instruct"
    #: Replay the cached LLM judgments only; never call the model. For machines without
    #: a GPU, running from a cache shipped with the experiment.
    llm_offline: bool = False
    max_minimization_trials: int = 30
    #: ONE epsilon schedule for every learning arm on the held-out application, scratch
    #: and pretrained alike: (start, end, fraction of the budget to decay over). Exploratory
    #: actions are mostly the search guide's, so unequal schedules would hand one arm
    #: several times as many search-driven decisions as the other and confound the
    #: transfer comparison with "how much search was used" (code review, 2026-10-03).
    target_epsilon: tuple[float, float, float] = (0.50, 0.05, 0.50)
    pretrain_epsilon: tuple[float, float, float] = (1.0, 0.10, 0.50)

    def to_dict(self) -> dict:
        return asdict(self)


#: Every arm of the experiment. `pretrain` names the pretraining configuration an arm loads
#: (None for arms with no prior). Keep this table the single definition of each arm.
ARMS: dict[str, dict] = {
    "random":              {"kind": "random"},
    "search_A0":           {"kind": "hand_priority"},
    "search_S":            {"kind": "feature_search"},
    "dqn_scratch":         {"kind": "agent", "algorithm": "dqn", "llm": True, "pretrain": None},
    "dqn_scratch_nollm":   {"kind": "agent", "algorithm": "dqn", "llm": False, "pretrain": None},
    "dqn_pretrained":      {"kind": "agent", "algorithm": "dqn", "llm": True, "pretrain": "dqn_llm",
                            "adapt": True},
    "dqn_zeroshot":        {"kind": "agent", "algorithm": "dqn", "llm": False, "pretrain": "dqn_llm",
                            "adapt": False},
    "dqn_pretrained_nollm": {"kind": "agent", "algorithm": "dqn", "llm": False,
                             "pretrain": "dqn_nollm", "adapt": True},
    "bandit_scratch":      {"kind": "agent", "algorithm": "bandit", "llm": False, "pretrain": None},
    "bandit_pretrained":   {"kind": "agent", "algorithm": "bandit", "llm": False,
                            "pretrain": "bandit_nollm", "adapt": True},
}

#: Pretraining configurations, referenced by `ARMS[...]["pretrain"]`.
PRETRAIN_CONFIGS: dict[str, dict] = {
    "dqn_llm": {"algorithm": "dqn", "llm": True},
    "dqn_nollm": {"algorithm": "dqn", "llm": False},
    "bandit_nollm": {"algorithm": "bandit", "llm": False},
}


@dataclass(frozen=True)
class Job:
    kind: str                 # "pretrain" | "hunt"
    fold: str                 # the held-out application
    seed: int
    arm: str = ""             # hunt jobs
    pretrain: str = ""        # pretrain jobs: which configuration
    train_apps: tuple[str, ...] = ()
    build: str = "buggy"

    @property
    def job_id(self) -> str:
        if self.kind == "pretrain":
            return f"pretrain__{self.pretrain}__fold-{self.fold}__seed-{self.seed}"
        suffix = "" if self.build == "buggy" else f"__{self.build}"
        return f"hunt__{self.arm}__fold-{self.fold}__seed-{self.seed}{suffix}"

    def depends_on(self) -> str | None:
        spec = ARMS.get(self.arm, {})
        if self.kind != "hunt" or not spec.get("pretrain"):
            return None
        return Job("pretrain", self.fold, self.seed, pretrain=spec["pretrain"]).job_id

    def to_dict(self) -> dict:
        return {**asdict(self), "job_id": self.job_id}


def build_plan(apps: list[str], seeds: list[int], arms: list[str],
               *, folds: list[str] | None = None, clean_control_arms: list[str] | None = None,
               ) -> list[Job]:
    """Every job, in dependency order: all pretraining first, then all hunts.

    `folds` restricts which applications are held out (default: every one). The training
    set of a fold is always *every other* application in `apps`.
    """
    unknown = set(arms) - set(ARMS)
    if unknown:
        raise ValueError(f"unknown arm(s): {sorted(unknown)}")
    folds = folds or list(apps)
    needed = {ARMS[a]["pretrain"] for a in arms if ARMS[a].get("pretrain")}
    jobs: list[Job] = []
    for fold in folds:
        training = tuple(a for a in apps if a != fold)
        for config in sorted(needed):
            for seed in seeds:
                jobs.append(Job("pretrain", fold, seed, pretrain=config, train_apps=training))
    for fold in folds:
        for arm in arms:
            for seed in seeds:
                jobs.append(Job("hunt", fold, seed, arm=arm))
        # The clean build, explored by the same arms: every confirmation there would be a
        # false positive, so this measures the verified-finding false-positive rate.
        for arm in clean_control_arms or []:
            jobs.append(Job("hunt", fold, seeds[0], arm=arm, build="clean"))
    return jobs


# -- provenance ----------------------------------------------------------------------------


def git_state() -> dict:
    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,  # noqa: S603,S607
                                  text=True, timeout=20, check=False).stdout.strip()
        except OSError:
            return ""
    return {"commit": run("rev-parse", "HEAD"),
            "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}


def provenance(config: ExperimentConfig) -> dict:
    return {"git": git_state(), "host": platform.node(), "platform": platform.platform(),
            "python": platform.python_version(), "pid": os.getpid(),
            "experiment_config": config.to_dict(),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


# -- running one job ----------------------------------------------------------------------


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def result_path(out_dir: Path, job: Job) -> Path:
    return Path(out_dir) / "results" / f"{job.job_id}.json"


def checkpoint_path(out_dir: Path, fold: str, seed: int, pretrain: str) -> Path:
    return Path(out_dir) / "checkpoints" / f"{pretrain}__fold-{fold}__seed-{seed}.pt"


def is_done(out_dir: Path, job: Job) -> bool:
    path = result_path(out_dir, job)
    if not path.is_file():
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("status") == "ok"
    except (OSError, ValueError):
        return False


def _epsilon_config(schedule: tuple[float, float, float], steps: int) -> dict:
    start, end, fraction = schedule
    return {"epsilon_start": start, "epsilon_end": end,
            "epsilon_decay_steps": max(1, int(fraction * steps))}


def _scorer(config: ExperimentConfig):
    from ..reward.progress import ProgressScorer

    scorer = ProgressScorer(model=config.llm_model, offline=config.llm_offline)
    scorer.preflight()
    return scorer


def run_pretrain(job: Job, config: ExperimentConfig, out_dir: Path) -> dict:
    from ..benchmark import EnvSettings, load_app
    from ..benchmark.suite import serve_app
    from ..rl import AgentConfig, QAgent, RewardComposer, RewardConfig, RewardLog
    from ..rl.train import pretrain

    spec = PRETRAIN_CONFIGS[job.pretrain]
    apps = [load_app(a) for a in job.train_apps]
    total = config.pretrain_steps_per_app * len(apps)
    agent_config = AgentConfig(algorithm=spec["algorithm"], seed=job.seed, total_steps=total,
                               gamma=0.95, **_epsilon_config(config.pretrain_epsilon, total))
    log = RewardLog(Path(out_dir) / "reward_logs" / f"{job.job_id}.jsonl", run=job.to_dict())
    rewarder = RewardComposer(
        RewardConfig(llm_weight=config.llm_weight if spec["llm"] else 0.0,
                     shaping_gamma=1.0 if spec["algorithm"] == "bandit" else agent_config.gamma),
        _scorer(config) if spec["llm"] else None, log)
    agent = QAgent(agent_config, rewarder=rewarder)
    settings = EnvSettings(max_steps=config.episode_steps, settle_quiet_ms=config.settle_quiet_ms,
                           capture_screenshots=False)
    curve: list[dict] = []

    import contextlib
    with contextlib.ExitStack() as stack:
        urls = {app.app_id: stack.enter_context(serve_app(app, builds=("buggy",)))["buggy"]
                for app in apps}
        report = pretrain(agent, apps, urls, steps_per_app=config.pretrain_steps_per_app,
                          settings=settings,
                          on_episode=lambda app, n, row: curve.append({"app": app, "episode": n, **row}))
    log.close()
    checkpoint = checkpoint_path(out_dir, job.fold, job.seed, job.pretrain)
    agent.save(checkpoint, meta={"job": job.to_dict(), "report": report.to_dict()})
    return {"pretrain": report.to_dict(), "agent": agent.summary(),
            "checkpoint": str(checkpoint), "learning_curve": curve}


def _build_policy(job: Job, config: ExperimentConfig, out_dir: Path, app_name: str):
    """The policy for a hunt job, plus a dict describing it."""
    from ..agents.hand_priority import HandPriorityPolicy
    from ..envs.types import MAX_ACTIONS
    from ..evaluation.rollout import RandomPolicy
    from ..rl import AgentConfig, FeatureSearchPolicy, QAgent, RewardComposer, RewardConfig, RewardLog

    spec = ARMS[job.arm]
    if spec["kind"] == "random":
        return RandomPolicy(MAX_ACTIONS, seed=job.seed, valid_only=True), {"arm": job.arm}
    if spec["kind"] == "hand_priority":
        policy = HandPriorityPolicy(epsilon=0.05, seed=job.seed, carry_counts_across_episodes=True)
        return policy, policy.to_dict()
    if spec["kind"] == "feature_search":
        policy = FeatureSearchPolicy(seed=job.seed, epsilon=0.05)
        return policy, policy.to_dict()

    log = RewardLog(Path(out_dir) / "reward_logs" / f"{job.job_id}.jsonl", run=job.to_dict())
    pretrained = spec.get("pretrain")
    schedule = config.target_epsilon
    agent_config = AgentConfig(
        algorithm=spec["algorithm"], seed=job.seed, total_steps=config.budget, gamma=0.95,
        learn=bool(spec.get("adapt", True)) if pretrained else True,
        **_epsilon_config(schedule, config.budget))
    rewarder = RewardComposer(
        RewardConfig(llm_weight=config.llm_weight if spec["llm"] else 0.0,
                     shaping_gamma=1.0 if spec["algorithm"] == "bandit" else agent_config.gamma),
        _scorer(config) if spec["llm"] else None, log)
    if pretrained:
        checkpoint = checkpoint_path(out_dir, job.fold, job.seed, pretrained)
        agent = QAgent.load(checkpoint, agent_config, rewarder=rewarder)
        if spec.get("adapt"):
            agent.enable_adaptation()
    else:
        agent = QAgent(agent_config, rewarder=rewarder)
    agent.set_application(job.fold, app_name)
    agent._reward_log = log  # closed by the caller
    return agent, {"arm": job.arm, **spec}


def run_hunt_job(job: Job, config: ExperimentConfig, out_dir: Path) -> dict:
    from ..benchmark import EnvSettings, FaultMonitor, load_app, make_env, run_hunt, serve_app
    from ..oracle import PlaywrightTrajectoryRunner, VerificationPipeline

    app = load_app(job.fold)
    settings = EnvSettings(max_steps=config.episode_steps, settle_quiet_ms=config.settle_quiet_ms,
                           capture_screenshots=False)
    policy, description = _build_policy(job, config, out_dir, app.manifest.name)
    with serve_app(app) as urls:
        def runner(build: str) -> PlaywrightTrajectoryRunner:
            return PlaywrightTrajectoryRunner(
                lambda: make_env(urls[build], settings, max_steps=120), max_steps=120)

        # The oracle replays on the build under test and controls against the clean one.
        # On a clean-build control job the two coincide, and every candidate is rejected by
        # construction; what is measured there is how many candidates arise at all.
        pipeline = VerificationPipeline(
            app.manifest, runner(job.build), control_runner=runner("clean"),
            max_minimization_trials=config.max_minimization_trials)
        monitor = FaultMonitor(pipeline)
        env = make_env(urls[job.build], settings)
        try:
            result = run_hunt(env, policy, monitor, budget=config.budget)
        finally:
            env.close()
    log = getattr(policy, "_reward_log", None)
    if log is not None:
        log.close()
    payload = {"hunt": result.to_dict(), "policy": description}
    if hasattr(policy, "summary"):
        payload["agent"] = policy.summary()
    elif hasattr(policy, "to_dict"):
        payload["policy_state"] = policy.to_dict()
    return payload


def run_job(job: Job, config: ExperimentConfig, out_dir: Path) -> dict:
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


# -- the scheduler ----------------------------------------------------------------------------


@dataclass
class Schedule:
    jobs: list[Job]
    out_dir: Path
    config: ExperimentConfig
    workers: int = 4
    log: list[str] = field(default_factory=list)


def run_schedule(schedule: Schedule) -> dict:
    """Run every job not already done, `workers` at a time, respecting dependencies.

    Each job runs in a fresh interpreter (`python -m ... --job`), so a browser that hangs or
    a process that leaks memory takes down one job, never the scheduler.
    """
    import sys

    out_dir = Path(schedule.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = out_dir / "experiment.json"
    if existing.is_file():
        # Resuming is only meaningful under the configuration the finished jobs ran with;
        # otherwise old checkpoints and results would be mixed silently with new ones.
        previous = json.loads(existing.read_text(encoding="utf-8")).get("config")
        if previous is not None and previous != json.loads(json.dumps(schedule.config.to_dict())):
            raise RuntimeError(f"{out_dir} was started with a different ExperimentConfig; "
                               f"use a new --out-dir rather than mixing configurations")
    _write_json(out_dir / "experiment.json", {
        "config": schedule.config.to_dict(), "jobs": [j.to_dict() for j in schedule.jobs],
        "provenance": provenance(schedule.config)})
    pending = [j for j in schedule.jobs if not is_done(out_dir, j)]
    by_id = {j.job_id: j for j in schedule.jobs}
    running: dict[str, subprocess.Popen] = {}
    failed: set[str] = set()
    config_path = out_dir / "experiment.json"
    logs = out_dir / "logs"
    logs.mkdir(exist_ok=True)
    logger.info("{} of {} jobs to run with {} workers", len(pending), len(schedule.jobs),
                schedule.workers)

    while pending or running:
        for job_id, process in list(running.items()):
            if process.poll() is None:
                continue
            del running[job_id]
            job = by_id[job_id]
            status = "ok" if is_done(out_dir, job) else "error"
            if status != "ok":
                failed.add(job_id)
            logger.info("[{} done, {} running, {} pending] {} -> {}",
                        len(schedule.jobs) - len(pending) - len(running), len(running),
                        len(pending), job_id, status)

        launchable = []
        for job in pending:
            dependency = job.depends_on()
            if dependency and dependency in failed:
                failed.add(job.job_id)
                continue
            if dependency and dependency in by_id and not is_done(out_dir, by_id[dependency]):
                continue
            launchable.append(job)
        pending = [j for j in pending if j.job_id not in failed]
        for job in launchable:
            if len(running) >= schedule.workers:
                break
            pending.remove(job)
            handle = (logs / f"{job.job_id}.log").open("w", encoding="utf-8")
            running[job.job_id] = subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "web_testing_agent.experiments.transfer",
                 "--config", str(config_path), "--out-dir", str(out_dir),
                 "--job", json.dumps(job.to_dict())],
                stdout=handle, stderr=subprocess.STDOUT, cwd=REPO_ROOT)
        if not launchable and not running and pending:
            logger.error("{} jobs are blocked on failed dependencies", len(pending))
            break
        time.sleep(2.0)

    done = sum(is_done(out_dir, j) for j in schedule.jobs)
    return {"jobs": len(schedule.jobs), "done": done, "failed": sorted(failed)}


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run one transfer-experiment job.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--job", required=True, help="the job, as JSON")
    arguments = parser.parse_args()
    stored = json.loads(arguments.config.read_text(encoding="utf-8"))["config"]
    stored = {k: tuple(v) if isinstance(v, list) else v for k, v in stored.items()}
    config = ExperimentConfig(**stored)
    raw = json.loads(arguments.job)
    raw.pop("job_id", None)
    raw["train_apps"] = tuple(raw.get("train_apps") or ())
    record = run_job(Job(**raw), config, arguments.out_dir)
    return 0 if record["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
