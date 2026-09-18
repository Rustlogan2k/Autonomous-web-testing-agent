"""The product layer: uploads, run lifecycle, reports, and the routes that expose them.

**No browser, no Docker, no training.** Every test here either exercises pure logic
(archive validation, the registry, status transitions) or drives the pipeline with fake
stages. The one thing that must never happen in this file is a real run: those take
minutes, need Chromium and a daemon, and belong to the end-to-end smoke test, not to the
unit suite.

The fakes are deliberately shaped like the real components rather than like mocks with
`assert_called_with`: a fake deployer is a context manager yielding something with a
`base_url`, because that is the contract `pipeline.run_pipeline` actually depends on, and
a test built on the contract keeps working when the implementation changes.
"""

from __future__ import annotations

import contextlib
import io
import time
import zipfile
from pathlib import Path

import pytest

from web_testing_agent.app import agents as agent_registry
from web_testing_agent.app import targets as target_registry
from web_testing_agent.app import uploads
from web_testing_agent.app.models import (
    ComponentState,
    Run,
    RunEvent,
    RunStatus,
    SourceInfo,
    TestSettings,
)
from web_testing_agent.app.registry import REPORT_JSON, RunRegistry

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from web_testing_agent.app.main import create_app  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


# -- fixtures ---------------------------------------------------------------------------


@pytest.fixture
def registry(tmp_path: Path) -> RunRegistry:
    return RunRegistry(tmp_path / "var")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(var_dir=tmp_path / "var"))


def make_zip(entries: dict[str, str | bytes], compression=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


DOCKERFILE = "FROM busybox:1.36\nCOPY . /www\nEXPOSE 8080\nCMD [\"httpd\",\"-f\"]\n"


# -- upload validation --------------------------------------------------------------------


def test_a_valid_archive_extracts_into_a_temp_workspace_outside_the_project():
    data = make_zip({"Dockerfile": DOCKERFILE, "index.html": "<h1>hi</h1>"})
    extracted = uploads.extract_zip(data, "app.zip")
    try:
        assert extracted.root.is_dir()
        assert (extracted.root / "Dockerfile").is_file()
        assert extracted.file_count == 2
        # The decisive property: nothing was written inside the repository.
        assert REPO_ROOT not in extracted.workspace.parents
        assert extracted.workspace.name.startswith(uploads.WORKSPACE_PREFIX)
    finally:
        extracted.cleanup()
    assert not extracted.workspace.exists()


@pytest.mark.parametrize("name", ["../../evil.txt", "../escape", "a/../../b"])
def test_path_traversal_entries_are_refused(name: str):
    with pytest.raises(uploads.UploadError, match="traversal"):
        uploads.extract_zip(make_zip({name: "x"}), "evil.zip")


@pytest.mark.parametrize("name", ["/etc/passwd", "C:/Windows/x", "//server/share/x"])
def test_absolute_and_drive_qualified_entries_are_refused(name: str):
    with pytest.raises(uploads.UploadError):
        uploads.extract_zip(make_zip({name: "x"}), "evil.zip")


def test_symlink_entries_are_refused():
    """A link entry is how an archive escapes a directory it was correctly extracted into."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(uploads.UploadError, match="symbolic link"):
        uploads.extract_zip(buffer.getvalue(), "link.zip")


def test_a_high_compression_ratio_is_refused():
    data = make_zip({"big.bin": b"\0" * (40 * 1024 * 1024)})
    with pytest.raises(uploads.UploadError, match="zip bomb"):
        uploads.extract_zip(data, "bomb.zip")


def test_a_non_zip_upload_is_refused():
    with pytest.raises(uploads.UploadError, match="Only .zip"):
        uploads.extract_zip(b"not a zip at all", "payload.tar")


def test_an_empty_upload_is_refused():
    with pytest.raises(uploads.UploadError, match="empty"):
        uploads.extract_zip(b"", "empty.zip")


def test_a_corrupt_archive_is_refused_with_a_readable_message():
    with pytest.raises(uploads.UploadError) as caught:
        uploads.extract_zip(b"PK\x03\x04 garbage garbage", "broken.zip")
    assert "corrupt" in str(caught.value).lower() or "could not be read" in str(caught.value)


def test_a_single_wrapper_directory_is_collapsed():
    """GitHub archives wrap the repository in `<name>-<ref>/`, which hides the Dockerfile."""
    data = make_zip({"app-main/Dockerfile": DOCKERFILE, "app-main/index.html": "x"})
    extracted = uploads.extract_zip(data, "app-main.zip")
    try:
        assert (extracted.root / "Dockerfile").is_file()
    finally:
        extracted.cleanup()


def test_extraction_never_escapes_even_when_names_look_relative(tmp_path: Path):
    """The check is on the resolved path, so a nested traversal cannot slip through."""
    data = make_zip({"a/b/../../../../outside.txt": "x"})
    with pytest.raises(uploads.UploadError):
        uploads.extract_zip(data, "nested.zip")


# -- registry and run lifecycle ------------------------------------------------------------


def test_a_created_run_is_persisted_and_reloads_identically(registry: RunRegistry):
    run = registry.create(SourceInfo(kind="demo", name="toy"), TestSettings(), "Search")
    reloaded = registry.get(run.run_id)
    assert reloaded is not None
    assert reloaded.run_id == run.run_id
    assert reloaded.status is RunStatus.CREATED
    assert reloaded.source.name == "toy"
    assert reloaded.agent_label == "Search"


def test_run_ids_increment_and_are_unique(registry: RunRegistry):
    first = registry.create(SourceInfo(name="a"), TestSettings())
    second = registry.create(SourceInfo(name="b"), TestSettings())
    assert second.sequence == first.sequence + 1
    assert first.run_id != second.run_id
    assert first.display_id == "#001"


def test_status_transitions_are_persisted(registry: RunRegistry):
    run = registry.create(SourceInfo(name="x"), TestSettings())
    for status in (RunStatus.VALIDATING, RunStatus.BUILDING, RunStatus.STARTING,
                   RunStatus.TESTING, RunStatus.COMPLETED):
        run.status = status
        registry.save(run)
        assert registry.get(run.run_id).status is status


def test_terminal_and_active_states_are_classified():
    for status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.STOPPED):
        assert status.is_terminal and not status.is_active
    for status in (RunStatus.CREATED, RunStatus.BUILDING, RunStatus.TESTING):
        assert status.is_active and not status.is_terminal


def test_an_invalid_run_id_cannot_reach_the_filesystem(registry: RunRegistry):
    for bad in ("../escape", "a/b", "..", "with space"):
        with pytest.raises(ValueError):
            registry.directory(bad)
        assert registry.get(bad) is None


def test_events_append_and_replay_from_an_offset(registry: RunRegistry):
    run = registry.create(SourceInfo(name="x"), TestSettings())
    for index in range(5):
        registry.append_event(run.run_id, RunEvent(time.time(), "action", f"step {index}"))
    assert len(registry.events(run.run_id)) == 5
    assert len(registry.events(run.run_id, offset=3)) == 2
    assert registry.events(run.run_id)[0]["message"] == "step 0"


def test_stats_and_findings_are_read_from_the_report_file(registry: RunRegistry):
    run = registry.create(SourceInfo(name="shop"), TestSettings())
    run.status = RunStatus.COMPLETED
    registry.save(run)
    registry.write_artifact(run.run_id, REPORT_JSON, """
    {"counts": {"total": 2}, "bugs": [
      {"title": "Broken link", "severity": 8.0, "source": "deterministic"},
      {"title": "Console error", "severity": 4.0, "source": "deterministic"}]}
    """)
    findings = list(registry.findings())
    assert [f["title"] for f in findings] == ["Broken link", "Console error"]
    assert findings[0]["application"] == "shop"
    stats = registry.stats()
    assert stats["total_runs"] == 1
    assert stats["completed_runs"] == 1
    assert stats["findings"] == 2
    assert stats["by_severity"] == {"high": 1, "medium": 1, "low": 0}


def test_a_corrupt_run_record_is_skipped_rather_than_fatal(registry: RunRegistry):
    good = registry.create(SourceInfo(name="ok"), TestSettings())
    broken = registry.runs_dir / "999-broken"
    broken.mkdir(parents=True)
    (broken / "run.json").write_text("{not json", encoding="utf-8")
    assert [r.run_id for r in registry.list()] == [good.run_id]


# -- the agent seam --------------------------------------------------------------------------


def test_the_default_agent_is_always_available():
    default = agent_registry.agent_by_key(agent_registry.DEFAULT_AGENT)
    assert default is not None and default.available


def test_an_unavailable_agent_falls_back_rather_than_raising():
    assert agent_registry.resolve("no-such-agent").key == agent_registry.DEFAULT_AGENT


def test_research_agents_are_labelled_as_such():
    by_key = {a.key: a for a in agent_registry.available_agents()}
    assert by_key["ac_dqn"].research_stage
    assert by_key["contextual_bandit"].research_stage
    assert not by_key["search"].research_stage
    assert not by_key["random"].research_stage


def test_an_unavailable_agent_explains_why():
    for agent in agent_registry.available_agents():
        if not agent.available:
            assert agent.unavailable_reason, f"{agent.key} is unavailable with no reason"


def test_the_non_learning_policies_build_and_satisfy_the_policy_protocol():
    from web_testing_agent.pipeline import RunSettings

    for key in (agent_registry.AGENT_RANDOM, agent_registry.AGENT_SEARCH):
        policy = agent_registry.build_policy(key, TestSettings())(RunSettings())
        assert callable(policy.act) and callable(policy.reset)


# -- settings ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("given", "expected"), [
    ((0, 0), (1, 5)),
    ((999, 9999), (20, 200)),
    ((3, 40), (3, 40)),
])
def test_settings_are_clamped_to_what_the_product_supports(given, expected):
    clamped = TestSettings(episodes=given[0], steps=given[1]).clamped()
    assert (clamped.episodes, clamped.steps) == expected


# -- demo targets --------------------------------------------------------------------------------


def test_demo_targets_point_at_fixtures_that_exist():
    for target in target_registry.demo_targets():
        assert target.path.is_dir(), f"{target.key} fixture missing"
        assert target.available, f"{target.key} is not runnable"
        if not target.needs_docker:
            assert (target.path / target.entry).is_file()


def test_the_static_deployer_serves_a_fixture_and_shuts_down():
    import urllib.request

    from web_testing_agent.pipeline import RunSettings

    target = target_registry.demo_target("toy_site")
    deploy = target_registry.static_deployer(target.path, target.entry)
    with deploy(target.path, RunSettings()) as deployment:
        assert deployment.base_url.endswith("/index.html")
        with urllib.request.urlopen(deployment.base_url, timeout=5) as response:
            assert response.status == 200
        url = deployment.base_url
    with pytest.raises(Exception):
        urllib.request.urlopen(url, timeout=2)


# -- routes ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/new", "/runs", "/findings", "/reports"])
def test_every_page_route_renders(client: TestClient, path: str):
    response = client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.parametrize("path", ["/api/stats", "/api/runs", "/api/agents"])
def test_every_read_api_responds(client: TestClient, path: str):
    assert client.get(path).status_code == 200


def test_an_unknown_run_is_a_404_not_a_crash(client: TestClient):
    assert client.get("/runs/nope").status_code == 404
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/nope/report").status_code == 404


def test_uploading_a_malicious_archive_returns_a_readable_error(client: TestClient):
    data = make_zip({"../../evil": "x"})
    response = client.post("/api/uploads",
                           files={"file": ("evil.zip", data, "application/zip")})
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert "refused" in body["error"]
    assert "Traceback" not in body["error"]


def test_a_repository_without_a_dockerfile_is_rejected_without_leaking_a_host_path(
    client: TestClient,
):
    data = make_zip({"index.html": "<h1>no docker</h1>"})
    response = client.post("/api/uploads",
                           files={"file": ("static.zip", data, "application/zip")})
    assert response.status_code == 400
    error = response.json()["error"]
    assert "Dockerfile" in error
    for leak in ("C:\\", "/tmp/", "AppData", uploads.WORKSPACE_PREFIX):
        assert leak not in error


def test_a_valid_upload_is_described_for_the_user(client: TestClient):
    data = make_zip({"Dockerfile": DOCKERFILE, "index.html": "<h1>hi</h1>"})
    response = client.post("/api/uploads",
                           files={"file": ("shop.zip", data, "application/zip")})
    assert response.status_code == 200
    body = response.json()
    try:
        assert body["ok"] and body["validation_ok"]
        assert body["name"] == "shop"
        assert body["dockerfile_found"]
        assert body["exposed_ports"] == [8080]
        assert body["file_count"] == 2
    finally:
        import shutil

        shutil.rmtree(body["workspace"], ignore_errors=True)


def test_a_run_cannot_be_started_from_a_path_this_app_did_not_create(client: TestClient):
    """`workspace` comes from a form field, so it must be validated before use."""
    response = client.post("/api/runs", data={
        "workspace": str(REPO_ROOT), "repo": str(REPO_ROOT), "name": "evil"})
    assert response.status_code == 400
    assert REPO_ROOT.is_dir(), "the project tree must be untouched"


def test_an_unknown_demo_target_is_refused(client: TestClient):
    assert client.post("/api/runs/demo", data={"target": "../../etc"}).status_code == 400


def test_stopping_an_unknown_run_is_a_404(client: TestClient):
    assert client.post("/api/runs/nope/stop").status_code == 404


def test_evidence_paths_cannot_escape_the_run_directory(client: TestClient, tmp_path: Path):
    response = client.get("/runs/001-abc/evidence/../../../../etc/passwd")
    assert response.status_code == 404


def test_downloads_are_404_when_the_artifact_does_not_exist(client: TestClient):
    assert client.get("/api/runs/nope/download/json").status_code == 404
    assert client.get("/api/runs/nope/download/exe").status_code == 404


# -- the orchestration, with every stage faked -------------------------------------------------


class _FakeRollout:
    unique_states = 7

    def to_dict(self) -> dict:
        return {"findings": [{"trigger": "console_error", "url": "http://x/p",
                              "first_seen_step": 3, "detail": "boom"}],
                "steps": 10, "episodes": 1, "unique_states": 7}


def _fake_deps(tmp_path: Path):
    """Pipeline dependencies that exercise the wiring without a browser or a daemon."""
    from web_testing_agent.pipeline import PipelineDependencies

    @contextlib.contextmanager
    def deployer(repo, settings):  # noqa: ANN001, ARG001
        yield target_registry.StaticDeployment(base_url="http://127.0.0.1:1/index.html",
                                               image_tag="fake:latest")

    def detector(repo):  # noqa: ANN001, ARG001
        from web_testing_agent.intake.runner import BuildPlan

        return BuildPlan(kind="static", path=tmp_path)

    class _Env:
        def close(self) -> None:
            return None

    def rollout_runner(env, policy, settings, recorder=None):  # noqa: ANN001, ARG001
        if recorder is not None:
            recorder.on_reset({}, {"page": {"url": "http://x/"}, "state_key": "a"})
            recorder.on_step(0, {}, 1.0, False, False,
                             {"page": {"url": "http://x/p"}, "state_key": "b"})
        return _FakeRollout()

    return PipelineDependencies(
        profiler=lambda repo: {"name": "fake"},
        detector=detector,
        deployer=deployer,
        env_factory=lambda base_url, settings, reward: _Env(),
        policy_factory=lambda settings: object(),
        judge_factory=lambda settings: None,
        recorder_factory=lambda evidence_dir, settings: None,
        rollout_runner=rollout_runner,
    )


def test_a_faked_run_completes_and_writes_the_research_artifacts(registry, tmp_path):
    """The full RunManager path with every expensive stage faked.

    Exercises the real observer wiring, the real status transitions, the real report
    writer and the real event log — but no browser, container or model. The stages are
    replaced through `RunManager.start(overrides=...)`, the same injection seam
    `run_pipeline` exposes.
    """
    from web_testing_agent.app.run_manager import RunManager

    manager = RunManager(registry)
    run = registry.create(SourceInfo(kind="demo", name="fake"),
                          TestSettings(episodes=1, steps=5), "Search (hand priority)")
    fake = _fake_deps(tmp_path)

    manager.start(run, tmp_path, fake.deployer, fake.detector, overrides={
        "profiler": fake.profiler,
        "env_factory": fake.env_factory,
        "judge_factory": fake.judge_factory,
        "policy_factory": fake.policy_factory,
        "rollout_runner": fake.rollout_runner,
    })
    _wait(manager, run.run_id)

    final = registry.get(run.run_id)
    assert final.status is RunStatus.COMPLETED, final.error
    assert final.counts.get("total") == 1
    assert final.finished_at > 0

    report = registry.read_json(run.run_id, REPORT_JSON)
    assert report["bugs"][0]["title"]
    assert report["bugs"][0]["source"] == "deterministic"
    assert registry.artifact(run.run_id, "report.md") is not None
    assert registry.artifact(run.run_id, "rollout.json") is not None

    messages = [e["message"] for e in registry.events(run.run_id)]
    assert any("started" in m for m in messages)
    assert any("completed" in m.lower() for m in messages)
    assert any("Report generated" in m for m in messages)

    # The step stream produced real activity lines from the rollout's own callbacks.
    kinds = {e["kind"] for e in registry.events(run.run_id)}
    assert "action" in kinds and "episode" in kinds


def test_a_stopped_run_ends_in_the_stopped_state(registry, tmp_path):
    """Cancellation unwinds through the rollout so teardown follows the normal path."""
    from web_testing_agent.app.run_manager import RunManager

    manager = RunManager(registry)
    run = registry.create(SourceInfo(name="x"), TestSettings(episodes=1, steps=50))
    fake = _fake_deps(tmp_path)

    def slow_rollout(env, policy, settings, recorder=None):  # noqa: ANN001, ARG001
        recorder.on_reset({}, {"page": {"url": "http://x/"}, "state_key": "a"})
        for index in range(50):
            manager.stop(run.run_id)
            recorder.on_step(0, {}, 0.0, False, False,
                             {"page": {"url": f"http://x/{index}"}, "state_key": str(index)})
        raise AssertionError("the stop flag was never honoured")

    manager.start(run, tmp_path, fake.deployer, fake.detector, overrides={
        "profiler": fake.profiler, "env_factory": fake.env_factory,
        "judge_factory": fake.judge_factory, "policy_factory": fake.policy_factory,
        "rollout_runner": slow_rollout,
    })
    _wait(manager, run.run_id)

    final = registry.get(run.run_id)
    assert final.status is RunStatus.STOPPED
    assert not final.error


def test_a_failing_stage_produces_a_readable_error_not_a_traceback(registry, tmp_path):
    from web_testing_agent.app.run_manager import RunManager

    manager = RunManager(registry)
    run = registry.create(SourceInfo(name="bad"), TestSettings(episodes=1, steps=5))

    @contextlib.contextmanager
    def exploding_deployer(repo, settings):  # noqa: ANN001, ARG001
        raise RuntimeError("docker: error during connect: open //./pipe/docker_engine")
        yield  # pragma: no cover

    def detector(repo):  # noqa: ANN001, ARG001
        from web_testing_agent.intake.runner import BuildPlan

        return BuildPlan(kind="dockerfile", path=tmp_path / "Dockerfile")

    manager.start(run, tmp_path, exploding_deployer, detector)
    _wait(manager, run.run_id)

    final = registry.get(run.run_id)
    assert final.status is RunStatus.FAILED
    assert "Docker" in final.error
    assert "Traceback" not in final.error
    assert "RuntimeError" not in final.error


def test_a_health_check_timeout_is_explained_in_the_users_terms(registry, tmp_path):
    from web_testing_agent.app.run_manager import RunManager
    from web_testing_agent.intake import HealthCheckTimeout

    manager = RunManager(registry)
    run = registry.create(SourceInfo(name="slow"), TestSettings(episodes=1, steps=5))

    @contextlib.contextmanager
    def timing_out(repo, settings):  # noqa: ANN001, ARG001
        raise HealthCheckTimeout("never answered")
        yield  # pragma: no cover

    def detector(repo):  # noqa: ANN001, ARG001
        from web_testing_agent.intake.runner import BuildPlan

        return BuildPlan(kind="dockerfile", path=tmp_path / "Dockerfile")

    manager.start(run, tmp_path, timing_out, detector)
    _wait(manager, run.run_id)

    final = registry.get(run.run_id)
    assert final.status is RunStatus.FAILED
    assert "never answered on its port" in final.error


def test_the_workspace_is_deleted_when_a_run_finishes(registry, tmp_path):
    """Cleanup runs on the failure path too — that is where a leak would actually happen."""
    from web_testing_agent.app.run_manager import RunManager

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cleaned: list[bool] = []

    manager = RunManager(registry)
    run = registry.create(SourceInfo(name="x"), TestSettings(episodes=1, steps=5))

    @contextlib.contextmanager
    def failing(repo, settings):  # noqa: ANN001, ARG001
        raise RuntimeError("boom")
        yield  # pragma: no cover

    def detector(repo):  # noqa: ANN001, ARG001
        from web_testing_agent.intake.runner import BuildPlan

        return BuildPlan(kind="dockerfile", path=tmp_path)

    def cleanup() -> None:
        import shutil

        shutil.rmtree(workspace, ignore_errors=True)
        cleaned.append(True)

    manager.start(run, tmp_path, failing, detector, on_cleanup=cleanup)
    _wait(manager, run.run_id)

    assert cleaned == [True]
    assert not workspace.exists()


def test_interrupted_runs_are_marked_failed_at_startup(tmp_path: Path):
    """A run cannot still be live after the process that owned it exited."""
    from web_testing_agent.app.main import _mark_interrupted_runs

    registry = RunRegistry(tmp_path / "var")
    run = registry.create(SourceInfo(name="x"), TestSettings())
    run.status = RunStatus.TESTING
    registry.save(run)

    _mark_interrupted_runs(registry)

    final = registry.get(run.run_id)
    assert final.status is RunStatus.FAILED
    assert "server stopped" in final.error


def _wait(manager, run_id: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while manager.is_active(run_id) and time.time() < deadline:
        time.sleep(0.05)
    assert not manager.is_active(run_id), "run did not finish in time"


# -- resilience of the file-backed registry ------------------------------------------------


def test_a_transient_lock_on_the_run_record_is_retried_not_fatal(registry, monkeypatch):
    """`os.replace` fails with WinError 5 when a sync agent holds the destination open.

    Measured, not hypothetical: it killed a live run during the first end-to-end test,
    because this repository sits inside a OneDrive-synced folder. The write must survive
    a few collisions rather than propagating out of a worker thread.
    """
    run = registry.create(SourceInfo(name="x"), TestSettings())
    calls = {"n": 0}
    real_replace = Path.replace

    def flaky(self, target):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] <= 3:
            raise PermissionError(5, "Access is denied")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    run.status = RunStatus.TESTING
    registry.save(run)

    assert calls["n"] == 4, "the write should have retried rather than given up"
    assert registry.get(run.run_id).status is RunStatus.TESTING


def test_a_permanent_lock_falls_back_to_an_in_place_write(registry, monkeypatch):
    """Atomicity is given up only after the atomic path has genuinely failed."""
    run = registry.create(SourceInfo(name="x"), TestSettings())

    def always_denied(self, target):  # noqa: ANN001, ARG001
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(Path, "replace", always_denied)
    run.status = RunStatus.COMPLETED
    registry.save(run)

    assert registry.get(run.run_id).status is RunStatus.COMPLETED
    assert not (registry.directory(run.run_id) / "run.json.tmp").exists()


def test_a_failed_progress_snapshot_does_not_abort_a_working_run(registry, tmp_path, monkeypatch):
    """The event log is the durable record; a lost snapshot must not fail the run."""
    from web_testing_agent.app.run_manager import RunManager

    manager = RunManager(registry)
    run = registry.create(SourceInfo(name="x"), TestSettings(episodes=1, steps=5))
    fake = _fake_deps(tmp_path)

    original_save = RunRegistry.save
    state = {"fail": False}

    def sometimes_failing(self, saved_run):  # noqa: ANN001
        if state["fail"]:
            raise OSError(5, "Access is denied")
        return original_save(self, saved_run)

    def rollout(env, policy, settings, recorder=None):  # noqa: ANN001, ARG001
        recorder.on_reset({}, {"page": {"url": "http://x/"}, "state_key": "a"})
        state["fail"] = True
        for index in range(3):
            time.sleep(1.1)  # cross the snapshot throttle so a save is attempted
            recorder.on_step(0, {}, 0.0, False, False,
                             {"page": {"url": f"http://x/{index}"}, "state_key": str(index)})
        state["fail"] = False
        return _FakeRollout()

    monkeypatch.setattr(RunRegistry, "save", sometimes_failing)
    manager.start(run, tmp_path, fake.deployer, fake.detector, overrides={
        "profiler": fake.profiler, "env_factory": fake.env_factory,
        "judge_factory": fake.judge_factory, "policy_factory": fake.policy_factory,
        "rollout_runner": rollout,
    })
    _wait(manager, run.run_id, timeout=60)

    assert registry.get(run.run_id).status is RunStatus.COMPLETED
