"""Repo Intake runner, against a **mocked** Docker client.

**What these tests do and do not establish.** They verify the runner's own logic: that
it detects the right build definition, refuses what the policy gate blocks, passes the
arguments it claims to pass, enforces its deadlines, and removes what it created on every
path including the failing ones.

They do **not** demonstrate Docker isolation. A fake client records `cap_drop: ["ALL"]`
and a real daemon enforces it, and nothing here can tell you the second happened. A test
named "malicious compose file is refused" proves the gate returned a blocking result, not
that an attacker was stopped. Real-daemon behaviour is out of scope for this file by
design -- see `tests/integration/test_intake_docker_smoke.py` for the non-destructive
live checks, and the adversarial suite gated behind `WTA_ADVERSARIAL_SANDBOX=1` for the
ones that need a disposable machine.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from web_testing_agent.intake.compose_policy import SANDBOX_RUN_ARGS, UnsafeRepositoryError
from web_testing_agent.intake.runner import (
    BuildTimeout,
    HealthCheckTimeout,
    RepoIntakeError,
    _Created,
    build_image,
    deploy_repository,
    detect_build_definition,
    teardown,
    wait_for_http,
)

DOCKERFILE = "FROM nginx:alpine\nEXPOSE 8080\n"


# --- fakes -------------------------------------------------------------------------


class FakeImages:
    def __init__(self, exposed: int | None = 8080) -> None:
        self.exposed = exposed
        self.removed: list[str] = []
        self.remove_fails = False

    def get(self, tag: str):  # noqa: ANN001, ARG002
        ports = {f"{self.exposed}/tcp": {}} if self.exposed else {}
        return type("Image", (), {"id": "sha256:deadbeef", "attrs": {"Config": {"ExposedPorts": ports}}})()

    def remove(self, image: str, force: bool = False) -> None:  # noqa: ARG002, FBT002
        if self.remove_fails:
            raise RuntimeError("image is in use")
        self.removed.append(image)


class FakeContainer:
    def __init__(self, status: str = "running", start_error: Exception | None = None) -> None:
        self.id = "c" * 64
        self.status = status
        self.removed = False
        self.started = False
        self.start_error = start_error
        self.remove_kwargs: dict = {}

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def reload(self) -> None:
        return None

    def logs(self, tail: int = 100):  # noqa: ARG002
        return b"application log line\n"

    def remove(self, **kwargs) -> None:
        self.removed = True
        self.remove_kwargs = kwargs


class FakeNetwork:
    def __init__(self, name: str) -> None:
        self.id, self.name = "n" * 64, name
        self.removed = False

    def remove(self) -> None:
        self.removed = True


class FakeNetworks:
    def __init__(self) -> None:
        self.created: list[FakeNetwork] = []

    def create(self, name: str, **kwargs):  # noqa: ANN001, ARG002
        network = FakeNetwork(name)
        self.created.append(network)
        return network


class FakeContainers:
    """Mirrors the split the runner now relies on: `create` then `start`.

    `docker-py`'s `run()` is exactly this pair with nothing between them, which is why a
    container that fails to start is never handed back to the caller. Modelling the two
    separately is what lets a test make `start` fail and assert the container is still
    torn down.
    """

    def __init__(self, status: str = "running", start_error: Exception | None = None) -> None:
        self.status = status
        self.start_error = start_error
        self.create_calls: list[tuple[tuple, dict]] = []
        self.created: list["FakeContainer"] = []

    def create(self, *args, **kwargs):
        self.create_calls.append((args, kwargs))
        container = FakeContainer(self.status, start_error=self.start_error)
        self.created.append(container)
        return container

    # Kept so a test that stubs the old entry point fails loudly rather than silently
    # exercising a path the runner no longer takes.
    def run(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("the runner must create and start separately, not call run()")


class FakeAPI:
    def __init__(self, chunks=None, delay: float = 0.0) -> None:
        self.chunks = chunks if chunks is not None else [
            {"stream": "Step 1/2 : FROM nginx:alpine\n"},
            {"aux": {"ID": "sha256:deadbeef"}},
        ]
        self.delay = delay
        self.build_kwargs: dict = {}

    def build(self, **kwargs):
        self.build_kwargs = kwargs
        return self._stream()

    def _stream(self):
        for chunk in self.chunks:
            if self.delay:
                time.sleep(self.delay)
            yield chunk


class FakeClient:
    def __init__(self, *, chunks=None, delay: float = 0.0, exposed: int | None = 8080,
                 status: str = "running", start_error: Exception | None = None) -> None:
        self.api = FakeAPI(chunks, delay)
        self.images = FakeImages(exposed)
        self.containers = FakeContainers(status, start_error=start_error)
        self.networks = FakeNetworks()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    return tmp_path


@pytest.fixture
def healthy_http(monkeypatch):
    """Make the health check succeed without a real server.

    Deploy tests are about build arguments, runtime arguments and teardown; they should
    not also depend on binding a socket. An earlier version passed `boot_timeout_s=0` to
    skip the wait, which does not skip it -- it makes it fail instantly -- so those tests
    were asserting against a deployment that had already raised.
    """
    import web_testing_agent.intake.runner as runner_mod

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(runner_mod.urllib.request, "urlopen",
                        lambda url, timeout=0: _Response())


# --- detection ---------------------------------------------------------------------


def test_a_dockerfile_at_the_root_is_detected(repo: Path):
    plan = detect_build_definition(repo)
    assert plan.kind == "dockerfile" and plan.runnable


def test_compose_wins_when_a_repo_has_both():
    """Compose describes the whole system; a lone Dockerfile describes one image of it."""
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
        (root / "docker-compose.yml").write_text(
            "services:\n  web:\n    build: .\n    ports: ['8080:80']\n", encoding="utf-8")
        plan = detect_build_definition(root)
        assert plan.kind == "compose" and not plan.runnable


def test_a_build_definition_one_level_down_is_found(tmp_path: Path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    assert detect_build_definition(tmp_path).kind == "dockerfile"


def test_a_repo_with_no_build_definition_is_refused_with_a_usable_message(tmp_path: Path):
    with pytest.raises(RepoIntakeError) as excinfo:
        detect_build_definition(tmp_path)
    assert "No Dockerfile or compose file" in str(excinfo.value)


def test_detection_runs_the_policy_gate_on_what_it_finds(tmp_path: Path):
    (tmp_path / "Dockerfile").write_text("FROM alpine\nRUN ls /var/run/docker.sock\n", encoding="utf-8")
    plan = detect_build_definition(tmp_path)
    assert plan.policy is not None and plan.policy.blocked


# --- refusals ----------------------------------------------------------------------


def test_a_policy_blocked_compose_file_is_refused_before_anything_runs(tmp_path: Path):
    """The gate's verdict has to stop the pipeline, not merely be recorded.

    Mocked: this shows the runner honours a blocking PolicyResult. It says nothing about
    whether Docker would have contained the container had it been started.
    """
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  evil:\n    image: alpine\n    privileged: true\n    volumes: ['/:/host']\n",
        encoding="utf-8")
    client = FakeClient()
    with pytest.raises(UnsafeRepositoryError):
        with deploy_repository(tmp_path, client=client):
            pass
    assert client.api.build_kwargs == {}, "a blocked repo must never reach the build"


def test_a_clean_compose_file_is_still_refused_by_the_runner(tmp_path: Path):
    """Deliberate scope cut: 'approximately applied' is not a security claim."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: nginx\n    ports: ['8080:80']\n", encoding="utf-8")
    with pytest.raises(RepoIntakeError, match="compose projects are not executed"):
        with deploy_repository(tmp_path, client=FakeClient()):
            pass


def test_a_dockerfile_that_touches_the_docker_socket_is_refused(tmp_path: Path):
    (tmp_path / "Dockerfile").write_text("FROM alpine\nRUN cat /var/run/docker.sock\n", encoding="utf-8")
    client = FakeClient()
    with pytest.raises(UnsafeRepositoryError):
        with deploy_repository(tmp_path, client=client):
            pass
    assert client.api.build_kwargs == {}


# --- build invocation and its controls ---------------------------------------------


def test_the_build_runs_with_no_network_by_default(repo: Path):
    """The one genuinely useful build-time control, and it must be the default.

    It does not stop build commands executing -- nothing does -- but it denies them
    egress. Mocked: this asserts the argument is passed, not that the daemon honours it.
    """
    client = FakeClient()
    build_image(client, repo, tag="t:1", created=_Created())
    assert client.api.build_kwargs["network_mode"] == "none"


def test_the_caller_can_loosen_the_build_network_and_it_is_recorded(repo: Path, caplog):
    client = FakeClient()
    build_image(client, repo, tag="t:1", created=_Created(), build_network="bridge")
    assert client.api.build_kwargs["network_mode"] == "bridge"


def test_intermediate_containers_are_removed_even_when_the_build_fails(repo: Path):
    client = FakeClient()
    build_image(client, repo, tag="t:1", created=_Created())
    assert client.api.build_kwargs["rm"] is True
    assert client.api.build_kwargs["forcerm"] is True


def test_a_build_error_is_raised_with_the_log_tail(repo: Path):
    client = FakeClient(chunks=[{"stream": "Step 1/2\n"}, {"error": "no such file: requirements.txt"}])
    with pytest.raises(RepoIntakeError) as excinfo:
        build_image(client, repo, tag="t:1", created=_Created())
    assert "requirements.txt" in str(excinfo.value)
    assert "log_tail" in str(excinfo.value)


def test_a_build_that_overruns_its_deadline_is_abandoned(repo: Path):
    """Bounds our wait and the layers we keep -- not the daemon's CPU."""
    client = FakeClient(chunks=[{"stream": f"line {i}\n"} for i in range(50)], delay=0.02)
    created = _Created()
    with pytest.raises(BuildTimeout) as excinfo:
        build_image(client, repo, tag="t:1", created=created, build_timeout_s=0)
    assert "abandoned" in str(excinfo.value)
    # The tag was registered before the failure, so teardown can still reclaim it.
    assert "t:1" in created.images


def test_build_output_is_capped_so_a_repo_cannot_exhaust_memory_by_printing(repo: Path):
    from web_testing_agent.intake.runner import MAX_BUILD_LOG_LINES

    noisy = [{"stream": f"line {i}\n"} for i in range(MAX_BUILD_LOG_LINES * 3)]
    noisy.append({"aux": {"ID": "sha256:deadbeef"}})
    _image_id, log = build_image(FakeClient(chunks=noisy), repo, tag="t:1", created=_Created())
    assert len(log) == MAX_BUILD_LOG_LINES


def test_an_overlong_build_log_line_is_truncated(repo: Path):
    from web_testing_agent.intake.runner import MAX_LOG_LINE_CHARS

    client = FakeClient(chunks=[{"stream": "x" * 10_000}, {"aux": {"ID": "sha256:d"}}])
    _image_id, log = build_image(client, repo, tag="t:1", created=_Created())
    assert len(log[0]) == MAX_LOG_LINE_CHARS


# --- runtime arguments -------------------------------------------------------------


def test_the_sandbox_arguments_are_all_passed_to_run(repo: Path, healthy_http):
    """Mocked: records what was requested. Enforcement is the daemon's, and untested here."""
    client = FakeClient()
    with deploy_repository(repo, client=client):
        pass
    _args, kwargs = client.containers.create_calls[0]
    for key, value in SANDBOX_RUN_ARGS.items():
        if key == "network_mode":
            continue  # replaced by the per-run network, asserted separately
        assert kwargs[key] == value, f"{key} was not passed through"


def test_each_run_gets_its_own_network_rather_than_the_shared_default(repo: Path, healthy_http):
    client = FakeClient()
    with deploy_repository(repo, client=client):
        pass
    _args, kwargs = client.containers.create_calls[0]
    assert kwargs["network"].startswith("wta-net-")
    assert "network_mode" not in kwargs, "network and network_mode are mutually exclusive"


def test_the_port_is_published_to_loopback_only(repo: Path, healthy_http):
    """Binding 0.0.0.0 would expose an untrusted application to the local network."""
    client = FakeClient()
    with deploy_repository(repo, client=client) as deployment:
        _args, kwargs = client.containers.create_calls[0]
        host_binding = kwargs["ports"]["8080/tcp"]
        assert host_binding[0] == "127.0.0.1"
        assert deployment.base_url == f"http://127.0.0.1:{host_binding[1]}/"


def test_a_caller_override_relaxes_exactly_one_argument_and_leaves_the_rest(repo: Path, healthy_http):
    client = FakeClient()
    with deploy_repository(repo, client=client, run_args={"read_only": False}):
        pass
    _args, kwargs = client.containers.create_calls[0]
    assert kwargs["read_only"] is False
    assert kwargs["cap_drop"] == ["ALL"], "unrelated constraints must not be disturbed"


# --- base_url discovery ------------------------------------------------------------


def test_the_container_port_comes_from_the_images_expose(repo: Path, healthy_http):
    client = FakeClient(exposed=8080)
    with deploy_repository(repo, client=client) as deployment:
        assert deployment.container_port == 8080


def test_an_explicit_port_overrides_what_the_image_declares(repo: Path, healthy_http):
    client = FakeClient(exposed=8080)
    with deploy_repository(repo, client=client, port=3000) as deployment:
        assert deployment.container_port == 3000


def test_an_image_with_no_expose_and_no_port_is_a_clear_failure(repo: Path):
    client = FakeClient(exposed=None)
    with pytest.raises(RepoIntakeError, match="which port"):
        with deploy_repository(repo, client=client):
            pass


# --- health check ------------------------------------------------------------------


def test_an_http_error_status_counts_as_alive():
    """A 500 means something is listening and speaking HTTP.

    Treating it as "not started" would hide exactly the broken deployment worth reporting,
    and deciding whether the response is *correct* is the agent's job, not the runner's.
    """
    import urllib.error

    calls: list[str] = []

    def fake_urlopen(url, timeout=0):  # noqa: ANN001, ARG001
        calls.append(url)
        raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)

    import web_testing_agent.intake.runner as runner_mod

    original = runner_mod.urllib.request.urlopen
    runner_mod.urllib.request.urlopen = fake_urlopen
    try:
        wait_for_http("http://127.0.0.1:1/", timeout_s=5)
    finally:
        runner_mod.urllib.request.urlopen = original
    assert calls == ["http://127.0.0.1:1/"]


def test_a_container_that_exits_fails_fast_with_its_logs():
    """Waiting the full boot timeout for a container that is already dead wastes minutes."""
    container = FakeContainer(status="exited")
    with pytest.raises(HealthCheckTimeout) as excinfo:
        wait_for_http("http://127.0.0.1:1/", timeout_s=30, container=container)
    assert "exited before serving" in str(excinfo.value)
    assert "application log line" in str(excinfo.value)


def test_a_health_check_timeout_reports_the_last_connection_error():
    with pytest.raises(HealthCheckTimeout) as excinfo:
        wait_for_http("http://127.0.0.1:1/", timeout_s=0)
    assert "No HTTP response" in str(excinfo.value)


# --- teardown ----------------------------------------------------------------------


def test_teardown_removes_containers_networks_and_images(repo: Path, healthy_http):
    client = FakeClient()
    with deploy_repository(repo, client=client):
        pass
    assert client.networks.created[0].removed is True
    # The tag embeds a random run id, so assert its shape rather than its value.
    assert len(client.images.removed) == 1
    assert client.images.removed[0].startswith("wta-target-")


def test_teardown_runs_when_the_health_check_fails(repo: Path):
    """The failure paths are exactly the ones that leak if teardown is tied to success."""
    client = FakeClient(status="exited")
    with pytest.raises(HealthCheckTimeout):
        with deploy_repository(repo, client=client, boot_timeout_s=5):
            pass
    assert client.networks.created[0].removed is True
    assert len(client.images.removed) == 1


def test_teardown_runs_when_the_caller_raises_inside_the_with_block(repo: Path, healthy_http):
    client = FakeClient()
    with pytest.raises(ZeroDivisionError):
        with deploy_repository(repo, client=client):
            raise ZeroDivisionError("the agent crashed mid-run")
    assert client.networks.created[0].removed is True
    assert len(client.images.removed) == 1


def test_teardown_removes_anonymous_volumes_with_the_container(repo: Path, healthy_http):
    client = FakeClient()
    with deploy_repository(repo, client=client):
        pass
    assert client.containers.created[0].remove_kwargs == {"force": True, "v": True}


def test_teardown_continues_past_a_failure_and_reports_it():
    """Stopping at the first error leaks everything after it, and 'already gone' is common."""
    client = FakeClient()
    client.images.remove_fails = True
    created = _Created()
    container, network = FakeContainer(), FakeNetwork("n")
    created.containers.append(container)
    created.networks.append(network)
    created.images.append("t:1")

    problems = teardown(client, created)
    assert container.removed and network.removed, "later objects must still be removed"
    assert len(problems) == 1 and "t:1" in problems[0]


def test_teardown_empties_its_ledger_so_a_second_call_is_a_no_op():
    client = FakeClient()
    created = _Created()
    created.containers.append(FakeContainer())
    teardown(client, created)
    assert teardown(client, created) == []


def test_keep_image_leaves_the_image_but_still_removes_the_container(repo: Path, healthy_http):
    client = FakeClient()
    with deploy_repository(repo, client=client, keep_image=True):
        pass
    assert client.images.removed == []
    assert client.networks.created[0].removed is True


# --- M5: lifecycle and cleanup guarantees --------------------------------------------


def test_a_container_that_fails_to_start_is_still_torn_down(repo: Path):
    """The leak this split exists to close.

    `docker-py`'s `containers.run()` is `create()` then an unguarded `start()`. When the
    start fails, `run()` raises without returning the object, so the caller can never
    register it and the container it already created is orphaned. `SANDBOX_RUN_ARGS` is
    strict enough to break many real images, so this is a routine failure rather than an
    exotic one.
    """
    client = FakeClient(start_error=RuntimeError("read-only rootfs: cannot start"))
    with pytest.raises(RuntimeError, match="cannot start"):
        with deploy_repository(repo, client=client):
            pass

    assert client.containers.created, "a container was created"
    assert client.containers.created[0].removed is True, "and it must not be left behind"
    assert client.networks.created[0].removed is True
    assert client.images.removed, "the image built for this run goes too"


def test_a_container_that_never_started_is_removed_anyway(repo: Path):
    """The precise invariant: registered on creation, so cleanup does not depend on
    the start succeeding. A container that never ran still has to be removed."""
    client = FakeClient(start_error=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        with deploy_repository(repo, client=client):
            pass

    container = client.containers.created[0]
    assert container.started is False, "it never started"
    assert container.removed is True, "and was cleaned up regardless"


def test_the_full_lifecycle_yields_a_deployment_and_then_cleans_up(repo: Path, healthy_http):
    """build -> create -> start -> health check -> yield -> teardown, in one pass."""
    client = FakeClient()
    seen: dict = {}
    with deploy_repository(repo, client=client) as deployment:
        seen["base_url"] = deployment.base_url
        seen["container_id"] = deployment.container_id
        seen["image_tag"] = deployment.image_tag
        seen["port"] = deployment.container_port
        assert client.containers.created[0].started is True
        assert client.containers.created[0].removed is False, "still alive inside the block"

    assert seen["base_url"].startswith("http://127.0.0.1:")
    assert seen["container_id"] and seen["image_tag"].startswith("wta-target-")
    assert seen["port"] == 8080
    assert client.containers.created[0].removed is True
    assert client.networks.created[0].removed is True
    assert client.images.removed == [seen["image_tag"]]


def test_an_interruption_inside_the_block_still_tears_down(repo: Path, healthy_http):
    """Ctrl-C is a `BaseException`, so a bare `except` would miss it. `finally` does not.

    An interrupted run is exactly when a leaked container is least likely to be noticed.
    """
    client = FakeClient()
    with pytest.raises(KeyboardInterrupt):
        with deploy_repository(repo, client=client):
            raise KeyboardInterrupt

    assert client.containers.created[0].removed is True
    assert client.networks.created[0].removed is True
    assert client.images.removed


def test_a_network_that_cannot_be_created_still_removes_the_image(repo: Path):
    """Failing between the build and the container must not strand the image."""
    client = FakeClient()

    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("no address space available")

    client.networks.create = refuse
    with pytest.raises(RuntimeError, match="address space"):
        with deploy_repository(repo, client=client):
            pass

    assert client.images.removed, "the image built moments earlier must be removed"
    assert client.containers.created == [], "nothing got as far as a container"


def test_teardown_is_reported_but_not_raised_when_removal_fails(repo: Path, healthy_http):
    """A noisy cleanup must not replace the result of a run that otherwise succeeded."""
    client = FakeClient()
    client.images.remove_fails = True
    with deploy_repository(repo, client=client) as deployment:
        assert deployment.base_url
    # No exception escaped; the container and network still went.
    assert client.containers.created[0].removed is True
    assert client.networks.created[0].removed is True


# --- M5: the pipeline's use of the runner ---------------------------------------------
#
# These drive the *real* `run_pipeline` and the *real* `deploy_repository` against the
# fake daemon above. They live here rather than beside the other pipeline tests because
# the fakes do, and a second copy of `FakeClient` would be free to drift from this one.


class _PipeEnv:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _PipeRollout:
    def to_dict(self) -> dict:
        return {"findings": []}

    def summary(self) -> str:
        return ""


class _PipeRecorder:
    root = None

    def __init__(self) -> None:
        self.closed = False

    def close(self, extra=None) -> dict:  # noqa: ANN001, ARG002
        self.closed = True
        return {}


def _pipeline_deps(client, env, recorder, *, rollout_raises: Exception | None = None):
    from web_testing_agent.pipeline import PipelineDependencies

    def deployer(repo, settings):
        # The real context manager, the real teardown, a fake daemon.
        return deploy_repository(repo, client=client, boot_timeout_s=5)

    def rollout_runner(env_, policy, settings, recorder=None):  # noqa: ARG001
        if rollout_raises is not None:
            raise rollout_raises
        return _PipeRollout()

    return PipelineDependencies(
        profiler=lambda repo: {"available": True},
        detector=detect_build_definition,
        deployer=deployer,
        env_factory=lambda base_url, settings, model: env,
        policy_factory=lambda settings: object(),
        judge_factory=lambda settings: None,
        recorder_factory=lambda evidence_dir, settings: recorder,
        rollout_runner=rollout_runner,
        reporter=lambda **kwargs: type("R", (), {"counts": {}, "to_dict": lambda self: {}})(),
    )


def test_the_whole_pipeline_lifecycle_cleans_up_after_a_successful_run(repo: Path, healthy_http):
    """repository -> build -> create -> start -> health check -> rollout -> teardown."""
    from web_testing_agent.pipeline import RunSettings, run_pipeline

    client, env, recorder = FakeClient(), _PipeEnv(), _PipeRecorder()
    result = run_pipeline(repo, RunSettings(), _pipeline_deps(client, env, recorder))

    assert result.report is not None
    assert env.closed is True and recorder.closed is True
    assert client.containers.created[0].started is True
    assert client.containers.created[0].removed is True
    assert client.networks.created[0].removed is True
    assert client.images.removed, "the image built for this run was removed"


def test_a_rollout_failure_leaks_no_sandbox_resources(repo: Path, healthy_http):
    """The browser, the trace handle and the container all outlive an exception unless
    something closes them — and the container goes last, after the browser stopped
    pointing at it."""
    from web_testing_agent.pipeline import RunSettings, run_pipeline

    client, env, recorder = FakeClient(), _PipeEnv(), _PipeRecorder()
    deps = _pipeline_deps(client, env, recorder, rollout_raises=RuntimeError("browser died"))

    with pytest.raises(RuntimeError, match="browser died"):
        run_pipeline(repo, RunSettings(), deps)

    assert env.closed is True, "the browser must be closed"
    assert recorder.closed is True, "the trace handle must be closed"
    assert client.containers.created[0].removed is True
    assert client.networks.created[0].removed is True
    assert client.images.removed


def test_a_health_check_failure_leaks_nothing_and_never_reaches_the_rollout(repo: Path):
    """No `healthy_http` here: the container never answers, so the deploy fails."""
    from web_testing_agent.pipeline import RunSettings, run_pipeline

    client, env, recorder = FakeClient(status="exited"), _PipeEnv(), _PipeRecorder()
    with pytest.raises(HealthCheckTimeout):
        run_pipeline(repo, RunSettings(), _pipeline_deps(client, env, recorder))

    assert client.containers.created[0].removed is True
    assert client.networks.created[0].removed is True
    assert client.images.removed
    assert env.closed is False, "the environment was never built, so nothing to close"


def test_a_compose_repository_is_refused_by_the_pipeline_without_building(tmp_path: Path):
    """M5 keeps the existing policy: compose is detected, gated, and refused."""
    from web_testing_agent.pipeline import RunSettings, run_pipeline

    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: nginx\n", encoding="utf-8")
    client, env, recorder = FakeClient(), _PipeEnv(), _PipeRecorder()

    with pytest.raises(RepoIntakeError, match="not executed by this runner"):
        run_pipeline(tmp_path, RunSettings(), _pipeline_deps(client, env, recorder))

    assert client.containers.created == [], "nothing was built or started"
    assert client.networks.created == []
