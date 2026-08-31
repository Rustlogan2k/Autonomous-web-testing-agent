"""M3: the repo-to-report pipeline, exercised entirely through injected stages.

Every test here runs the *real* `run_pipeline` with fake stages. That is the point of the
extraction: the composition is the part most likely to be quietly wrong and was previously
the part hardest to test, because exercising it meant a Docker daemon, a browser and
possibly a model. None of those are loaded by this file.

Two properties get the most attention. **Teardown must happen on every exit path** — the
deployer is a context manager and a leaked container is the failure most likely to occur
and least likely to be noticed. And **`--deploy-only` must not produce a report**, because
a run that stopped early and a run that found nothing must not look alike.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from web_testing_agent.pipeline import (
    STAGE_DEPLOYED,
    STAGE_DETECTED,
    STAGE_PROFILED,
    STAGE_REPORT,
    STAGE_ROLLOUT,
    PipelineDependencies,
    RunSettings,
    build_run_meta,
    default_judge_factory,
    run_pipeline,
)

REPO = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "demo_repo"


class FakeDeployment:
    base_url = "http://127.0.0.1:9999/"
    image_tag = "wta-target-test:latest"
    container_id = "abcdef123456789"


class FakePlan:
    kind = "dockerfile"
    path = Path("Dockerfile")
    policy = None


class FakeRollout:
    def __init__(self, findings=None):
        self._findings = findings or []

    def to_dict(self):
        return {"findings": self._findings}

    def summary(self):
        return "fake rollout summary"


class FakeEnv:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class Harness:
    """Fake stages plus a record of what the pipeline did with them."""

    def __init__(self, *, findings=None, verdicts=None, deploy_raises=None, rollout_raises=None):
        self.calls: list[str] = []
        self.events: list[str] = []
        self.env = FakeEnv()
        self.torn_down = False
        self.deploy_raises = deploy_raises
        self.rollout_raises = rollout_raises
        self.reward_model = type("RM", (), {"verdicts": verdicts or []})()
        self.report_kwargs: dict = {}
        self.recorder = None
        self.recorder_seen = "unset"
        self._findings = findings or []

    @contextlib.contextmanager
    def deployer(self, repo, settings):
        self.calls.append("deploy")
        try:
            if self.deploy_raises:
                raise self.deploy_raises
            yield FakeDeployment()
        finally:
            # Stands in for `deploy_repository`'s teardown, which is the behaviour that
            # must survive every exit path.
            self.torn_down = True

    def deps(self, **overrides) -> PipelineDependencies:
        def profiler(repo):
            self.calls.append("profile")
            return {"available": True, "primary_language": "HTML"}

        def detector(repo):
            self.calls.append("detect")
            return FakePlan()

        def env_factory(base_url, settings, reward_model):
            self.calls.append("env")
            return self.env

        def policy_factory(settings):
            self.calls.append("policy")
            return object()

        def judge_factory(settings):
            self.calls.append("judge")
            return self.reward_model

        def rollout_runner(env, policy, settings, recorder=None):
            self.calls.append("rollout")
            self.recorder_seen = recorder
            if self.rollout_raises:
                raise self.rollout_raises
            return FakeRollout(self._findings)

        def reporter(**kwargs):
            self.calls.append("report")
            self.report_kwargs = kwargs
            return type("Rep", (), {"counts": {}, "to_dict": lambda self: {}})()

        def recorder_factory(evidence_dir, settings):
            self.calls.append("recorder")
            return self.recorder

        base = dict(profiler=profiler, detector=detector, deployer=self.deployer,
                    env_factory=env_factory, policy_factory=policy_factory,
                    judge_factory=judge_factory, recorder_factory=recorder_factory,
                    rollout_runner=rollout_runner, reporter=reporter)
        base.update(overrides)
        return PipelineDependencies(**base)

    def observe(self, stage, data):
        self.events.append(stage)


# -- the happy path -------------------------------------------------------------------


def test_the_pipeline_runs_its_stages_in_order():
    harness = Harness()
    run_pipeline(REPO, RunSettings(), harness.deps(), observer=harness.observe)
    assert harness.calls == ["profile", "detect", "deploy", "judge", "env", "recorder",
                            "policy", "rollout", "report"]


def test_profiling_happens_before_deployment():
    """If the build fails, the record of what the repository was is already captured."""
    harness = Harness()
    run_pipeline(REPO, RunSettings(), harness.deps())
    assert harness.calls.index("profile") < harness.calls.index("deploy")


def test_every_stage_emits_an_event():
    harness = Harness()
    run_pipeline(REPO, RunSettings(), harness.deps(), observer=harness.observe)
    assert harness.events == [STAGE_PROFILED, STAGE_DETECTED, STAGE_DEPLOYED,
                              STAGE_ROLLOUT, STAGE_REPORT]


def test_the_pipeline_never_prints(capsys: pytest.CaptureFixture):
    """Rendering belongs to the CLI; a pipeline that prints is unusable from a service."""
    run_pipeline(REPO, RunSettings(), Harness().deps())
    assert capsys.readouterr().out == ""


def test_the_result_carries_the_deployment_details():
    result = run_pipeline(REPO, RunSettings(), Harness().deps())
    assert result.base_url == FakeDeployment.base_url
    assert result.image_tag == FakeDeployment.image_tag
    assert result.repository == REPO.resolve()
    assert result.stopped_after_deploy is False
    assert result.report is not None


def test_findings_reach_the_reporter():
    findings = [{"trigger": "console_error", "url": "u", "element": "e"}]
    harness = Harness(findings=findings)
    run_pipeline(REPO, RunSettings(), harness.deps())
    assert harness.report_kwargs["findings"] == findings
    assert harness.report_kwargs["target"] == FakeDeployment.base_url


def test_judge_verdicts_reach_the_reporter():
    harness = Harness(verdicts=[{"is_bug": True}])
    run_pipeline(REPO, RunSettings(), harness.deps())
    assert harness.report_kwargs["verdicts"] == [{"is_bug": True}]


# -- dependency injection -------------------------------------------------------------


def test_the_policy_is_replaceable_without_touching_the_pipeline():
    """The seam that keeps today's agent from becoming an assumption."""
    sentinel = object()
    harness = Harness()
    seen: list = []

    def rollout_runner(env, policy, settings, recorder=None):
        seen.append(policy)
        return FakeRollout()

    run_pipeline(REPO, RunSettings(),
                 harness.deps(policy_factory=lambda s: sentinel, rollout_runner=rollout_runner))
    assert seen == [sentinel]


def test_the_judge_is_replaceable_and_its_verdicts_are_read_from_it():
    harness = Harness()
    model = type("RM", (), {"verdicts": [{"is_bug": True, "bug_type": "x"}]})()
    run_pipeline(REPO, RunSettings(), harness.deps(judge_factory=lambda s: model))
    assert harness.report_kwargs["verdicts"] == [{"is_bug": True, "bug_type": "x"}]


def test_a_judge_without_verdicts_yields_an_empty_list():
    """`--judge stub` produces no reward model at all; the pipeline must cope."""
    harness = Harness()
    run_pipeline(REPO, RunSettings(), harness.deps(judge_factory=lambda s: None))
    assert harness.report_kwargs["verdicts"] == []


def test_the_stub_judge_factory_loads_no_model():
    assert default_judge_factory(RunSettings(judge="stub")) is None


def test_the_deployer_is_replaceable():
    harness = Harness()
    run_pipeline(REPO, RunSettings(), harness.deps())
    assert harness.torn_down is True


# -- deploy-only ----------------------------------------------------------------------


def test_deploy_only_stops_before_the_rollout_and_produces_no_report():
    harness = Harness()
    result = run_pipeline(REPO, RunSettings(), harness.deps(), on_deployment=lambda d: False)
    assert result.stopped_after_deploy is True
    assert result.report is None
    assert result.rollout is None
    assert "rollout" not in harness.calls


def test_deploy_only_still_tears_down():
    harness = Harness()
    run_pipeline(REPO, RunSettings(), harness.deps(), on_deployment=lambda d: False)
    assert harness.torn_down is True


def test_a_confirming_hook_lets_the_run_continue():
    harness = Harness()
    result = run_pipeline(REPO, RunSettings(), harness.deps(), on_deployment=lambda d: True)
    assert result.stopped_after_deploy is False
    assert "rollout" in harness.calls


# -- failure paths --------------------------------------------------------------------


def test_a_failing_rollout_still_closes_the_environment_and_tears_down():
    """The browser and the container both outlive an exception unless something closes
    them, and the container the browser was pointed at is removed after it."""
    harness = Harness(rollout_raises=RuntimeError("browser died"))
    with pytest.raises(RuntimeError, match="browser died"):
        run_pipeline(REPO, RunSettings(), harness.deps())
    assert harness.env.closed is True
    assert harness.torn_down is True


def test_a_detector_that_refuses_stops_before_anything_is_built():
    """`detect_build_definition` raises for a repository with no build definition, and
    that must fail the run rather than reach the deployer."""
    harness = Harness()

    def detector(repo):
        raise RuntimeError("no Dockerfile")

    with pytest.raises(RuntimeError, match="no Dockerfile"):
        run_pipeline(REPO, RunSettings(), harness.deps(detector=detector))
    assert "deploy" not in harness.calls


def test_a_deployment_failure_propagates_after_teardown():
    harness = Harness(deploy_raises=RuntimeError("build failed"))
    with pytest.raises(RuntimeError, match="build failed"):
        run_pipeline(REPO, RunSettings(), harness.deps())
    assert harness.torn_down is True


# -- settings and run metadata --------------------------------------------------------


def test_the_judge_label_matches_what_the_script_recorded():
    assert RunSettings(judge="stub").judge_label == "stub"
    assert RunSettings(judge="ollama", model="qwen2.5:7b-instruct").judge_label == (
        "ollama/qwen2.5:7b-instruct")


def test_writable_rootfs_is_the_only_sandbox_relaxation_expressed_here():
    assert RunSettings().run_args is None
    assert RunSettings(allow_writable_rootfs=True).run_args == {"read_only": False}


def test_run_meta_keeps_the_key_set_reports_were_generated_with():
    """A report produced through the pipeline must stay comparable with one produced
    before the pipeline existed."""
    meta = build_run_meta(Path("/repo"), {"available": True}, FakePlan(), FakeDeployment(),
                          RunSettings(), 12.34)
    assert set(meta) == {
        "repository", "repository_profile", "build_definition", "build_network", "image",
        "episodes", "steps_per_episode", "seed", "judge", "wall_clock_s", "scope_note",
    }
    assert meta["wall_clock_s"] == 12.3
    assert meta["repository_profile"] == {"available": True}
    assert "NOT a security boundary" in meta["scope_note"]


def test_the_profile_reaches_run_meta_through_the_pipeline():
    harness = Harness()
    run_pipeline(REPO, RunSettings(), harness.deps())
    assert harness.report_kwargs["run_meta"]["repository_profile"]["available"] is True


def test_settings_defaults_match_the_cli_defaults():
    """Drift here would silently change what a plain `run_repo.py` invocation does."""
    settings = RunSettings()
    assert (settings.episodes, settings.steps, settings.seed) == (2, 25, 0)
    assert settings.build_network == "none"
    assert (settings.build_timeout_s, settings.boot_timeout_s) == (600, 180)
    assert settings.judge == "stub" and settings.min_confidence == 0.0
