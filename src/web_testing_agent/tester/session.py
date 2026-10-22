"""One complete automated test session against a running web application.

    target URL
      -> the learning tester (pretrained on the benchmark suite, adapting online here)
         exploring through Playwright, rewarded by novelty and the LLM progress potential
      -> deterministic triggers  -> replay x2 -> minimize            -> verified findings
      -> seeded-fault oracle (when the application declares faults)  -> verified findings
      -> outcome pages -> LLM review, grounded quotes only           -> qualitative notes
      -> evidence: a screenshot and the page of every verified finding, re-captured by
         replaying its minimized trace from a fresh session
      -> report (JSON, Markdown, standalone HTML)

This is the product path, and it is built from the same parts as the experiment: the same
agent, features, reward, environment, harness and oracle. A result on the benchmark is
therefore a result about the thing a user runs, not about a research twin of it.

**Graceful degradation, stated in the report.** No language model reachable: the agent
explores without the LLM term and no outcome review runs; the report says so. No
pretrained checkpoint: the agent starts from scratch and learns online; the report says so.
Neither situation makes a run fail, and neither is hidden.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..benchmark.hunt import run_hunt
from ..benchmark.suite import EnvSettings, make_env
from ..utils.logging import get_logger
from .triggers import CompositeMonitor, TriggerMonitor, TriggerReplayer

logger = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
#: The tester's production weights: pretrained on every benchmark application.
DEFAULT_CHECKPOINT = REPO_ROOT / "models" / "tester" / "tester_all_apps.pt"


class SessionStopped(Exception):
    """Raised inside the loop when the caller asks the session to stop."""


@dataclass(frozen=True)
class SessionSettings:
    budget: int = 600
    episode_steps: int = 40
    seed: int = 0
    #: "rl" (the learning tester), "search" (feature search), or "random".
    agent: str = "rl"
    checkpoint: str = ""
    llm_reward: bool = True
    llm_review: bool = True
    llm_model: str = "qwen2.5:7b-instruct"
    llm_weight: float = 1.0
    max_reviews: int = 6
    settle_quiet_ms: int | None = None
    max_minimization_trials: int = 20
    setup_actions: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SessionResult:
    target: str
    app_name: str
    settings: dict
    started_at: str
    hunt: dict = field(default_factory=dict)
    agent: dict = field(default_factory=dict)
    triggers: dict = field(default_factory=dict)
    seeded: dict | None = None
    reviews: list[dict] = field(default_factory=list)
    degradations: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    wall_clock_s: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _llm_available(model: str) -> tuple[bool, str]:
    from ..reward.progress import ProgressScorer

    try:
        ProgressScorer(model=model).preflight()
        return True, ""
    except RuntimeError as exc:
        return False, str(exc)


#: The hybrid arms of the held-out evaluation, built here exactly as the evaluation built them:
#: their configuration is read from `hybrid.run.ARMS` at the freeze, never restated.
FROZEN_HYBRID_AGENTS = ("hybrid_nolearn", "hybrid_bandit")

#: Hybrid agents a session can run, by session key -> `hybrid.run.ARMS` key. The frozen arms
#: keep their names; `hybrid_curiosity` is tuning round v3's curiosity arm (count-based
#: Q-learning over page transitions chooses where to explore).
HYBRID_AGENTS = {**{key: key for key in FROZEN_HYBRID_AGENTS},
                 "hybrid_curiosity": "dev_v3_curiosity"}


def _hybrid_agent(settings: SessionSettings, app_name: str):
    from ..hybrid.learner import BanditPlanner
    from ..hybrid.policy import HybridPolicy
    from ..hybrid.run import ARMS

    arm = HYBRID_AGENTS[settings.agent]
    spec = ARMS[arm]
    policy = HybridPolicy(
        context=app_name,
        seed=settings.seed,
        planner_factory=BanditPlanner if spec["planner"] == "bandit" else None,
        episode_steps=settings.episode_steps,
        explore=spec.get("explore", "weighted"),
        depth_bias=float(spec.get("depth_bias", 0.0)),
        browse=bool(spec.get("browse", False)),
        failure_bonus=float(spec.get("failure_bonus", 0.0)),
    )
    mode = ("held-out evaluation configuration" if arm in FROZEN_HYBRID_AGENTS
            else f"tuning-round configuration ({arm})")
    return policy, {"agent": settings.agent, "mode": mode}


def _v1_pretrained_agent(settings: SessionSettings, app_name: str, degradations: list[str]):
    """v1's best AC-DQN as the held-out evaluation ran it: the transfer experiment's
    pretraining checkpoint, residual head adapting, the frozen v1 exploration schedule, no
    LLM reward."""
    from ..experiments.transfer import ExperimentConfig, _epsilon_config
    from ..hybrid.run import v1_checkpoint
    from ..rl import AgentConfig, QAgent, RewardComposer, RewardConfig

    config = AgentConfig(algorithm="dqn", seed=settings.seed, total_steps=settings.budget,
                         gamma=0.95, learn=True,
                         **_epsilon_config(ExperimentConfig().target_epsilon, settings.budget))
    rewarder = RewardComposer(RewardConfig(llm_weight=0.0, shaping_gamma=config.gamma))
    try:
        checkpoint = v1_checkpoint(settings.seed)
    except FileNotFoundError as exc:
        degradations.append(f"{exc}; the AC-DQN learns from scratch on this application")
        agent = QAgent(config, rewarder=rewarder)
        description = {"agent": "v1_dqn_pretrained", "checkpoint": None, "mode": "from scratch"}
    else:
        agent = QAgent.load(checkpoint, config, rewarder=rewarder)
        agent.enable_adaptation()
        description = {"agent": "v1_dqn_pretrained", "checkpoint": str(checkpoint),
                       "mode": "pretrained + adapting"}
    agent.set_application("target", app_name)
    return agent, description


def build_agent(settings: SessionSettings, app_name: str, degradations: list[str],
                log_path: Path | None = None):
    """The exploring policy for a session, and a description of it."""
    from ..envs.types import MAX_ACTIONS
    from ..evaluation.rollout import RandomPolicy
    from ..rl import AgentConfig, FeatureSearchPolicy, QAgent, RewardComposer, RewardConfig, RewardLog

    if settings.agent == "random":
        return RandomPolicy(MAX_ACTIONS, seed=settings.seed, valid_only=True), {"agent": "random"}
    if settings.agent in HYBRID_AGENTS:
        return _hybrid_agent(settings, app_name)
    if settings.agent == "v1_dqn_pretrained":
        return _v1_pretrained_agent(settings, app_name, degradations)
    if settings.agent == "search":
        policy = FeatureSearchPolicy(seed=settings.seed)
        return policy, policy.to_dict()

    scorer = None
    if settings.llm_reward:
        available, why = _llm_available(settings.llm_model)
        if available:
            from ..reward.progress import ProgressScorer

            scorer = ProgressScorer(model=settings.llm_model)
        else:
            degradations.append(f"LLM progress reward disabled: {why}")
    log = RewardLog(log_path) if log_path is not None else None
    rewarder = RewardComposer(
        RewardConfig(llm_weight=settings.llm_weight if scorer else 0.0), scorer, log)
    config = AgentConfig(seed=settings.seed, total_steps=settings.budget,
                         epsilon_start=0.20, epsilon_end=0.05,
                         epsilon_decay_steps=max(1, int(0.3 * settings.budget)))
    checkpoint = Path(settings.checkpoint) if settings.checkpoint else DEFAULT_CHECKPOINT
    if checkpoint.is_file():
        agent = QAgent.load(checkpoint, config, rewarder=rewarder)
        agent.enable_adaptation()
        description = {"agent": "rl", "checkpoint": str(checkpoint), "mode": "pretrained + adapting"}
    else:
        degradations.append(f"no pretrained checkpoint at {checkpoint}; the agent learns from "
                            f"scratch on this application")
        agent = QAgent(AgentConfig(seed=settings.seed, total_steps=settings.budget,
                                   epsilon_decay_steps=max(1, int(0.6 * settings.budget))),
                       rewarder=rewarder)
        description = {"agent": "rl", "checkpoint": None, "mode": "from scratch"}
    agent.set_application("target", app_name)
    agent._reward_log = log
    return agent, description


def run_session(base_url: str, settings: SessionSettings | None = None, *,
                app_name: str = "", out_dir: Path | None = None,
                seeded_app=None, clean_url: str | None = None,  # noqa: ANN001
                control_urls: dict | None = None,
                on_event: Callable[[str, dict], None] | None = None,
                should_stop: Callable[[], bool] | None = None) -> SessionResult:
    """Test the application at `base_url`. Writes evidence under `out_dir` when given.

    `seeded_app` (a `BenchmarkApp`) and `clean_url` switch on the seeded-fault oracle with
    its clean-build control; they are supplied for benchmark applications and for any
    upload that ships a `faults.json`.
    """
    from ..benchmark.monitor import FaultMonitor
    from ..oracle import PlaywrightTrajectoryRunner, VerificationPipeline

    settings = settings or SessionSettings()
    emit = on_event or (lambda kind, data: None)
    started = time.monotonic()
    result = SessionResult(target=base_url, app_name=app_name or base_url,
                           settings=settings.to_dict(),
                           started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    env_settings = EnvSettings(max_steps=settings.episode_steps,
                               settle_quiet_ms=settings.settle_quiet_ms)
    setup = [dict(step) for step in settings.setup_actions]

    def env_factory(url: str, steps: int | None = None):  # noqa: ANN202
        return make_env(url, env_settings, setup_actions=setup or None,
                        **({"max_steps": steps} if steps else {}))

    out_dir = Path(out_dir) if out_dir is not None else None
    policy, description = build_agent(
        settings, result.app_name, result.degradations,
        (out_dir / "reward_log.jsonl") if out_dir is not None else None)
    result.agent = description
    for note in result.degradations:
        emit("warning", {"message": note})

    replayer = TriggerReplayer(lambda: env_factory(base_url, 120))
    triggers = TriggerMonitor(replayer, max_minimization_trials=settings.max_minimization_trials)
    seeded = None
    if seeded_app is not None:
        def runner(url: str) -> PlaywrightTrajectoryRunner:
            return PlaywrightTrajectoryRunner(lambda: env_factory(url, 120), max_steps=120)

        seeded = FaultMonitor(VerificationPipeline(
            seeded_app, runner(base_url),
            control_runner=runner(clean_url) if clean_url else None,
            control_runners={fid: runner(url) for fid, url in (control_urls or {}).items()},
            max_minimization_trials=settings.max_minimization_trials))
    monitor = CompositeMonitor(triggers=triggers, seeded=seeded)

    outcomes: dict[str, dict] = {}
    episode_trace: list[dict] = []
    entered: dict[str, str] = {}

    def on_step(row: dict) -> None:
        if should_stop is not None and should_stop():
            raise SessionStopped()
        info = row["info"]
        from ..oracle import entered_value, replay_step_of

        spec = row["action"]
        step = replay_step_of(spec, None)
        if step:
            episode_trace.append(step)
        entered.update(entered_value(spec, info.get("exec_info")))
        emit("step", {"step": row["step"], "episode": row["episode"],
                      "url": (info.get("page") or {}).get("url", ""),
                      "action": spec.description if spec else "", "reward": row["reward"]})
        rewarder = getattr(policy, "rewarder", None)
        phi = getattr(rewarder, "_last_potential", ("", None))[1] if rewarder else None
        if phi is not None and phi >= 0.8:
            page = info.get("page") or {}
            key = page.get("url", "").split("?", 1)[0] + "|" + str(hash(page.get("visible_text", "")))
            if key not in outcomes:
                outcomes[key] = {"url": page.get("url", ""), "html": page.get("html", ""),
                                 "visible_text": page.get("visible_text"), "potential": phi,
                                 "trace": list(episode_trace), "entered": dict(entered)}

    env = env_factory(base_url)
    emit("stage", {"message": f"Exploring with the {description.get('mode', settings.agent)} agent"})
    try:
        hunt = run_hunt(env, policy, _EpisodeResetting(monitor, episode_trace, entered),
                        budget=settings.budget, on_step=on_step)
        result.hunt = {k: v for k, v in hunt.to_dict().items() if k != "oracle"}
    except SessionStopped:
        result.degradations.append("stopped by the user before the budget was spent")
    finally:
        env.close()
        log = getattr(policy, "_reward_log", None)
        if log is not None:
            log.close()
    if hasattr(policy, "summary"):
        result.agent["summary"] = policy.summary()

    result.triggers = triggers.summary()
    result.seeded = seeded.summary() if seeded is not None else None

    if settings.llm_review and settings.agent == "rl" and outcomes:
        available, why = _llm_available(settings.llm_model)
        if available:
            from .review import OutcomeReviewer

            reviewer = OutcomeReviewer(model=settings.llm_model)
            ranked = sorted(outcomes.values(), key=lambda o: -(o["potential"] or 0))
            emit("stage", {"message": f"LLM reviewing {min(len(ranked), settings.max_reviews)} outcome page(s)"})
            for outcome in ranked[: settings.max_reviews]:
                observation = reviewer.review(outcome["url"], outcome["html"],
                                              outcome["visible_text"], outcome["trace"],
                                              outcome["entered"], outcome["potential"])
                result.reviews.append(observation.to_dict())
        else:
            result.degradations.append(f"outcome review skipped: {why}")

    if out_dir is not None:
        result.evidence = capture_evidence(result, base_url, env_factory, out_dir)
    result.wall_clock_s = round(time.monotonic() - started, 1)
    return result


class _EpisodeResetting:
    """Passes monitor calls through, and clears the session's per-episode trace on reset."""

    def __init__(self, inner: CompositeMonitor, trace: list[dict], entered: dict) -> None:
        self.inner, self._trace, self._entered = inner, trace, entered

    def start_episode(self, info: dict) -> None:
        self._trace.clear()
        self._entered.clear()
        self.inner.start_episode(info)

    def observe(self, *args):  # noqa: ANN002, ANN201
        return self.inner.observe(*args)

    @property
    def verification_seconds(self) -> float:
        return self.inner.verification_seconds

    def summary(self) -> dict:
        return self.inner.summary()


def capture_evidence(result: SessionResult, base_url: str, env_factory, out_dir: Path) -> dict:  # noqa: ANN001
    """Replay each verified finding's minimized trace once more and keep what it shows."""
    evidence_dir = Path(out_dir) / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    replayer = TriggerReplayer(lambda: env_factory(base_url, 120))
    index: dict = {}

    def keep(name: str, actions: list[dict]) -> None:
        try:
            outcome = replayer.run(actions, capture=True)
        except Exception as exc:  # noqa: BLE001 - evidence is best effort, the finding stands
            index[name] = {"error": f"{type(exc).__name__}: {exc}"}
            return
        final = outcome["final"]
        entry = {"url": final.get("url", ""), "replay_misses": outcome["misses"]}
        if final.get("screenshot_png"):
            path = evidence_dir / f"{name}.png"
            path.write_bytes(final["screenshot_png"])
            entry["screenshot"] = str(path.relative_to(out_dir))
        page = evidence_dir / f"{name}.html"
        page.write_text(final.get("html", ""), encoding="utf-8")
        entry["page"] = str(page.relative_to(out_dir))
        index[name] = entry

    for number, finding in enumerate(result.triggers.get("findings", [])):
        if finding.get("verified"):
            keep(f"trigger_{number}", finding["minimized_trace"])
    for fault in (result.seeded or {}).get("faults", []):
        found = fault.get("finding")
        if found and found.get("confirmed"):
            keep(f"seeded_{fault['fault_id']}", found["minimized_trace"])
    return index


def embed_png(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def load_result(path: Path) -> SessionResult:
    """A saved `session.json`, so a report can be re-rendered without re-running the test."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return SessionResult(**{k: v for k, v in data.items() if k in SessionResult.__dataclass_fields__})


def save_result(result: SessionResult, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "session.json"
    path.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")
    return path
