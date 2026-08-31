"""The repo-to-bug-report pipeline, as a reusable function rather than a script body.

    repository -> profile -> detect -> policy gate -> build -> run -> health check
               -> base_url -> environment -> policy rollout -> judge -> bug report
               -> teardown

**What this is and is not.** This is a *composition root*: it wires components that
already exist and adds no behaviour of its own. Every stage it performs was previously
inline in `scripts/run_repo.py`; the extraction changes where the code lives, not what it
does. The script remains the CLI — argument parsing, printing and file writing stay
there, because those are the parts that genuinely belong to a command-line program.

**Why the dependencies are injectable.** The pipeline's stages are a browser, a Docker
daemon and possibly a language model, which makes the *composition* — the part most
likely to be quietly wrong — the part hardest to test. Every stage is therefore a
parameter with a default that reproduces the previous behaviour exactly, so a test can
substitute a fake deployer, a scripted policy and a stub judge and assert on the wiring
in milliseconds. The defaults are resolved lazily inside the factory functions rather
than at import time, so importing this module does not drag in Playwright or Docker.

**The agent is replaceable here, and that is the point.** `policy_factory` returns
anything satisfying `evaluation.rollout.Policy` — `RandomPolicy` today, a trained agent,
a scripted walk. Nothing in this module imports an agent or knows one exists, which is
what keeps today's AC-DQN from becoming an assumption baked into the pipeline.

**Scope note carried through, not restated.** Deployment remains Dockerfile-only and
`deploy_repository` remains the sole authority on whether a repository can be deployed
(§6.2). Execution is resource-limited, not contained: a repository's Dockerfile `RUN`
lines execute before any `docker run` restriction exists. That limitation lives in
`intake/runner.py` and is recorded in every report's `run_meta`.
"""

from __future__ import annotations

import contextlib
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .utils.logging import get_logger

logger = get_logger(__name__)

# A stage announcing itself. The CLI renders these; nothing in this module prints, so the
# pipeline is usable from a test, a notebook or a service without stdout side effects.
Observer = Callable[[str, dict], None]

STAGE_PROFILED = "profiled"
STAGE_DETECTED = "detected"
STAGE_DEPLOYED = "deployed"
STAGE_ROLLOUT = "rollout"
STAGE_REPORT = "report"


@dataclass(frozen=True, slots=True)
class RunSettings:
    """Everything a run needs to know that a caller chooses.

    Deliberately not an `argparse.Namespace`: the pipeline should be callable without a
    command line, and a frozen dataclass documents the contract that a namespace hides.
    Defaults match `scripts/run_repo.py`'s so a settings object built from its arguments
    reproduces the previous run exactly.
    """

    episodes: int = 2
    steps: int = 25
    seed: int = 0
    min_confidence: float = 0.0
    port: int | None = None
    build_network: str = "none"
    build_timeout_s: int = 600
    boot_timeout_s: int = 180
    allow_writable_rootfs: bool = False
    # Recorded in `run_meta` and used by the default judge factory. `"stub"` means
    # deterministic triggers only and no model is loaded.
    judge: str = "stub"
    model: str = "qwen2.5:7b-instruct"
    prompt: str = "compact"
    label: str = "repo-intake"
    # Frames are the most useful evidence a finding can cite and the most expensive to
    # keep. On by default because a trace without them cannot show what a page looked
    # like; `TraceRecorder` content-addresses them, so a static site costs a handful of
    # PNGs however many steps it takes.
    capture_screenshots: bool = True

    @property
    def judge_label(self) -> str:
        """How the judge is named in the report, matching the previous run_meta value."""
        return self.judge if self.judge == "stub" else f"{self.judge}/{self.model}"

    @property
    def run_args(self) -> dict | None:
        """Sandbox relaxations, or None when the strict defaults are kept."""
        return {"read_only": False} if self.allow_writable_rootfs else None


@dataclass
class PipelineResult:
    """What one run produced, whether or not it got all the way to a report."""

    repository: Path
    profile: dict = field(default_factory=dict)
    plan: Any = None
    base_url: str = ""
    image_tag: str = ""
    container_id: str = ""
    rollout: Any = None
    report: Any = None
    wall_clock_s: float = 0.0
    # Content-addressed references to the artifacts the report cites. Empty when
    # evidence capture is off, which is the backwards-compatible path.
    evidence: dict = field(default_factory=dict)
    # True when `on_deployment` asked to stop — the `--deploy-only` path. The report is
    # None in that case, and a caller has to be able to tell that apart from a run that
    # produced no findings.
    stopped_after_deploy: bool = False


# -- default stage implementations ----------------------------------------------------
#
# Each import is inside its function. Importing this module therefore costs nothing, and
# a test that injects a fake never loads Playwright, Docker or a model.


def default_profiler(repo: Path) -> dict:
    from .intake import profile_for_run

    return profile_for_run(repo)


def default_detector(repo: Path):  # noqa: ANN201 - BuildPlan, not imported at module scope
    from .intake import detect_build_definition

    return detect_build_definition(repo)


def default_deployer(repo: Path, settings: RunSettings) -> AbstractContextManager:
    from .intake import deploy_repository

    return deploy_repository(
        repo,
        port=settings.port,
        build_network=settings.build_network,
        build_timeout_s=settings.build_timeout_s,
        boot_timeout_s=settings.boot_timeout_s,
        run_args=settings.run_args,
    )


def default_env_factory(base_url: str, settings: RunSettings, reward_model: Any):  # noqa: ANN201
    from .envs import WebFunctionalEnv

    return WebFunctionalEnv(
        base_url=base_url,
        max_steps=settings.steps,
        headless=True,
        reward_model=reward_model,
    )


def default_policy_factory(settings: RunSettings):  # noqa: ANN201
    """Uniform-random over valid actions — the baseline `run_repo.py` has always used.

    Replaceable by anything satisfying `evaluation.rollout.Policy`. This module never
    imports an agent, so swapping one in requires no change here.
    """
    from .envs.types import MAX_ACTIONS
    from .evaluation import RandomPolicy

    return RandomPolicy(MAX_ACTIONS, seed=settings.seed, valid_only=True)


def default_judge_factory(settings: RunSettings) -> Any | None:
    """The reward model, or None when only deterministic triggers are wanted."""
    if settings.judge == "stub":
        return None
    from .judge.ollama import OllamaJudge
    from .reward.llm_judge import JudgeRewardModel

    return JudgeRewardModel(
        judge=OllamaJudge(model=settings.model, prompt_style=settings.prompt),
        min_confidence=settings.min_confidence,
    )


def default_recorder_factory(evidence_dir, settings: RunSettings):  # noqa: ANN001, ANN201
    """A `TraceRecorder` writing under `evidence_dir`, or None when evidence is off.

    Deliberately the *existing* recorder rather than anything new: it already
    content-addresses page bodies and frames, and a second artifact store would
    duplicate that and risk disagreeing with it.
    """
    if evidence_dir is None:
        return None
    from .annotation import TraceRecorder

    return TraceRecorder(
        Path(evidence_dir), settings.label,
        save_screenshots=settings.capture_screenshots,
        meta={"purpose": "evidence for the bug report", "label": settings.label},
    )


def default_rollout_runner(env, policy, settings: RunSettings, recorder=None):  # noqa: ANN001, ANN201
    from .evaluation import run_rollout

    return run_rollout(
        env, policy, episodes=settings.episodes, label=settings.label, seed=settings.seed,
        recorder=recorder,
    )


def default_reporter(**kwargs):  # noqa: ANN003, ANN201
    from .reporting import build_report

    return build_report(**kwargs)


@dataclass(frozen=True, slots=True)
class PipelineDependencies:
    """Every stage, swappable. Defaults reproduce `scripts/run_repo.py` exactly."""

    profiler: Callable[[Path], dict] = default_profiler
    detector: Callable[[Path], Any] = default_detector
    deployer: Callable[[Path, RunSettings], AbstractContextManager] = default_deployer
    env_factory: Callable[[str, RunSettings, Any], Any] = default_env_factory
    policy_factory: Callable[[RunSettings], Any] = default_policy_factory
    judge_factory: Callable[[RunSettings], Any] = default_judge_factory
    recorder_factory: Callable[[Any, RunSettings], Any] = default_recorder_factory
    rollout_runner: Callable[..., Any] = default_rollout_runner
    reporter: Callable[..., Any] = default_reporter


# -- the pipeline ---------------------------------------------------------------------


@contextlib.contextmanager
def _suppressed(what: str):
    """Swallow and log a failure in a best-effort cleanup step.

    Closing a recorder must not be able to mask the exception that a failing run is
    trying to report, nor abort a successful one over a file handle.
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - cleanup must not replace the real error
        logger.warning("ignored failure while {}: {}: {}", what, type(exc).__name__, exc)


def _cited_steps(findings: list[dict], verdicts: list[dict]) -> set[int]:
    """The global steps a report will actually mention.

    Both producers already carry one: `Finding.first_seen_step` (copied into
    `ReportedBug.step` by the report builder) and the live judge's own `step`, which is
    the same global counter. Indexing only these keeps a 200-step trace out of
    `run_meta` while leaving every reported bug traceable.
    """
    steps = {int(f.get("first_seen_step", 0) or 0) for f in findings}
    steps |= {int(v.get("step", 0) or 0) for v in verdicts}
    return {s for s in steps if s > 0}


def _evidence_index(recorder, findings: list[dict], verdicts: list[dict]) -> dict:  # noqa: ANN001
    """Reference the artifacts for the steps this report cites, or {} when not recording."""
    if recorder is None:
        return {}
    root = getattr(recorder, "root", None)
    if root is None:
        return {}
    from .annotation import build_evidence_index

    return build_evidence_index(root, _cited_steps(findings, verdicts), base=Path.cwd())


SCOPE_NOTE = (
    "Trusted/controlled repository input, resource-limited execution. "
    "NOT a security boundary against a determined attacker: Dockerfile "
    "build commands execute before any docker run restriction applies."
)


def build_run_meta(
    repo: Path, profile: dict, plan: Any, deployment: Any, settings: RunSettings,
    elapsed: float,
) -> dict:
    """The `run_meta` block, kept in one place so the report's provenance is auditable.

    The key set and ordering match what `scripts/run_repo.py` produced before this
    module existed; a report generated through the pipeline is comparable with one
    generated before it.
    """
    return {
        "repository": str(repo),
        # The full serialized profile, not a summary: provenance, per-detection
        # confidence and repository-relative evidence paths are what let a reader check
        # a claim about the repository rather than take it on trust.
        "repository_profile": profile,
        "build_definition": str(getattr(plan, "path", "")),
        "build_network": settings.build_network,
        "image": getattr(deployment, "image_tag", ""),
        "episodes": settings.episodes,
        "steps_per_episode": settings.steps,
        "seed": settings.seed,
        "judge": settings.judge_label,
        "wall_clock_s": round(elapsed, 1),
        # Recorded in the artifact itself, because a report that travels without this
        # reads as a security assessment and it is not one.
        "scope_note": SCOPE_NOTE,
    }


def run_pipeline(
    repo: Path,
    settings: RunSettings | None = None,
    deps: PipelineDependencies | None = None,
    *,
    observer: Observer | None = None,
    on_deployment: Callable[[Any], bool] | None = None,
    evidence_dir: Path | None = None,
) -> PipelineResult:
    """Profile, deploy, explore and report on one repository. Always tears down.

    `on_deployment` runs once the application is answering and decides whether to carry
    on. Returning False stops after deployment with `stopped_after_deploy=True` — the
    `--deploy-only` path, kept here as a hook so the CLI can block on input without this
    module knowing what a terminal is.

    `evidence_dir` turns on artifact capture: a `TraceRecorder` writes the run's page
    bodies and frames there, content-addressed, and the steps the report cites are
    referenced from `run_meta["evidence"]`. `None` records nothing and produces a report
    identical to one from before this existed.

    Teardown is the deployer's responsibility and happens on every exit path, including
    an exception raised by `on_deployment` or by the rollout, because `deploy_repository`
    is a context manager whose `finally` covers the whole body.
    """
    settings = settings or RunSettings()
    deps = deps or PipelineDependencies()
    repo = Path(repo).resolve()
    result = PipelineResult(repository=repo)

    def emit(stage: str, **data: Any) -> None:
        if observer is not None:
            observer(stage, data)

    # Profiled before anything is built. It is a pure filesystem read, so it costs
    # nothing and cannot be affected by the build; and if the build later fails, the
    # record of what the repository *was* has already been captured, which is exactly
    # when that description is most useful.
    result.profile = deps.profiler(repo)
    emit(STAGE_PROFILED, profile=result.profile)

    # `detect_build_definition` raises when the repository describes no build, and that
    # is deliberate: it is the authority on deployability and a run that cannot deploy
    # should fail here rather than later with a vaguer error.
    result.plan = deps.detector(repo)
    emit(STAGE_DETECTED, plan=result.plan)

    started = time.monotonic()
    with deps.deployer(repo, settings) as deployment:
        result.base_url = getattr(deployment, "base_url", "")
        result.image_tag = getattr(deployment, "image_tag", "")
        result.container_id = getattr(deployment, "container_id", "")
        emit(STAGE_DEPLOYED, deployment=deployment)

        if on_deployment is not None and not on_deployment(deployment):
            result.stopped_after_deploy = True
            return result

        reward_model = deps.judge_factory(settings)
        env = deps.env_factory(result.base_url, settings, reward_model)
        recorder = deps.recorder_factory(evidence_dir, settings)
        try:
            result.rollout = deps.rollout_runner(
                env, deps.policy_factory(settings), settings, recorder=recorder,
            )
        finally:
            # The environment owns a browser; it is closed whether the rollout finished
            # or raised, and before teardown removes the container it was pointed at.
            env.close()
            # The recorder owns an open file handle. Closed here, on both paths, so a
            # failed run still leaves a readable trace of what happened before it failed
            # — which is when a trace is most worth having.
            if recorder is not None:
                with _suppressed("closing the trace recorder"):
                    recorder.close()
        emit(STAGE_ROLLOUT, rollout=result.rollout)

        result.wall_clock_s = time.monotonic() - started
        verdicts = list(getattr(reward_model, "verdicts", []) or [])
        findings = result.rollout.to_dict().get("findings") or []
        result.evidence = _evidence_index(recorder, findings, verdicts)
        run_meta = build_run_meta(
            repo, result.profile, result.plan, deployment, settings, result.wall_clock_s,
        )
        if result.evidence:
            run_meta["evidence"] = result.evidence
        result.report = deps.reporter(
            target=result.base_url,
            findings=findings,
            verdicts=verdicts,
            min_confidence=settings.min_confidence,
            run_meta=run_meta,
        )
        emit(STAGE_REPORT, report=result.report)

    return result
