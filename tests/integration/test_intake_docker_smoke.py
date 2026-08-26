"""Live Docker checks for the Repo Intake runner.

**Two suites in one file, separated on purpose.**

*Smoke* (default, runs whenever a daemon is reachable). Non-destructive: builds a tiny
static-content image from a pinned base, runs it, checks it answers, tears it down. It
asks "does the pipeline work against a real daemon" — the question the mocked suite
structurally cannot answer, because a fake client that records `cap_drop: ["ALL"]` proves
only that the argument was passed. These are safe against a daily-driver daemon: no host
mounts, no privileged flags, no network egress at build time, everything removed
afterwards, and nothing that tries to escape anything.

*Adversarial* (skipped unless `WTA_ADVERSARIAL_SANDBOX=1`). These deliberately run
hostile build definitions to find out what the daemon actually enforces, and **must not
be pointed at a machine you care about**. On a rootful daemon — Docker Desktop's WSL2
backend included — a build runs as root in the VM, and host directories shared into that
VM are in scope. The opt-in is a second pair of eyes, not a formality.

**What neither suite establishes.** That this is safe for untrusted input. The runner's
documented scope is trusted/controlled repositories, and the decisive reason is that
`docker build` executes arbitrary code *before* any `docker run` restriction exists. A
passing adversarial suite would show that specific attempts were contained, never that
the boundary holds.
"""

from __future__ import annotations

import os
import shutil
import textwrap
from pathlib import Path

import pytest

from web_testing_agent.intake.runner import (
    RepoIntakeError,
    build_image,
    deploy_repository,
    _Created,
    teardown,
)

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker CLI not on PATH"
)

ADVERSARIAL_ENABLED = os.environ.get("WTA_ADVERSARIAL_SANDBOX") == "1"
adversarial = pytest.mark.skipif(
    not ADVERSARIAL_ENABLED,
    reason="destructive: set WTA_ADVERSARIAL_SANDBOX=1, and only on a disposable machine",
)

# Pinned by digest-free tag but deliberately tiny and ubiquitous, so the smoke suite does
# not become a network-bandwidth test. `busybox httpd` serves static files in one process
# and exits cleanly, which is what a health check and a teardown both want.
SMOKE_DOCKERFILE = textwrap.dedent(
    """
    FROM busybox:1.36
    RUN mkdir -p /www
    COPY index.html /www/index.html
    EXPOSE 8080
    CMD ["httpd", "-f", "-p", "8080", "-h", "/www"]
    """
).strip()

SMOKE_PAGE = "<!doctype html><title>intake smoke</title><h1>deployed</h1>"


@pytest.fixture
def docker_client():
    docker = pytest.importorskip("docker")
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no reachable Docker daemon: {exc}")
    return client


@pytest.fixture
def smoke_repo(tmp_path: Path) -> Path:
    (tmp_path / "Dockerfile").write_text(SMOKE_DOCKERFILE, encoding="utf-8")
    (tmp_path / "index.html").write_text(SMOKE_PAGE, encoding="utf-8")
    return tmp_path


# --- smoke: safe against the current daemon ----------------------------------------


def test_a_repository_builds_runs_and_answers_on_its_base_url(docker_client, smoke_repo: Path):
    """The whole path end to end against a real daemon.

    `build_network` is relaxed to bridge only because the base image must be pulled;
    nothing in this Dockerfile installs anything. The default remains "none".
    """
    import urllib.request

    with deploy_repository(
        smoke_repo, client=docker_client, build_network="bridge", boot_timeout_s=120
    ) as deployment:
        assert deployment.base_url.startswith("http://127.0.0.1:")
        assert deployment.container_port == 8080
        with urllib.request.urlopen(deployment.base_url, timeout=10) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
        assert "deployed" in body


def test_everything_created_is_gone_afterwards(docker_client, smoke_repo: Path):
    """Teardown is the claim most likely to be quietly false, so verify it live."""
    with deploy_repository(
        smoke_repo, client=docker_client, build_network="bridge", boot_timeout_s=120
    ) as deployment:
        container_id, network_id, tag = deployment.container_id, deployment.network_id, deployment.image_tag

    import docker.errors  # noqa: PLC0415

    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(container_id)
    with pytest.raises(docker.errors.NotFound):
        docker_client.networks.get(network_id)
    with pytest.raises(docker.errors.ImageNotFound):
        docker_client.images.get(tag)


def test_teardown_still_happens_when_the_caller_raises(docker_client, smoke_repo: Path):
    captured: dict = {}
    with pytest.raises(RuntimeError, match="agent exploded"):
        with deploy_repository(
            smoke_repo, client=docker_client, build_network="bridge", boot_timeout_s=120
        ) as deployment:
            captured["container"] = deployment.container_id
            raise RuntimeError("agent exploded")

    import docker.errors  # noqa: PLC0415

    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(captured["container"])


def test_a_build_with_no_network_cannot_reach_the_internet(docker_client, tmp_path: Path):
    """The one build-time control that does something, verified against the daemon.

    Non-destructive: the build *fails*, which is the assertion. It demonstrates that
    `network_mode="none"` is honoured — which the mocked suite cannot show, since there it
    is only a recorded keyword argument.
    """
    (tmp_path / "Dockerfile").write_text(
        "FROM busybox:1.36\nRUN wget -T 5 -O /dev/null http://example.com\n", encoding="utf-8"
    )
    created = _Created()
    try:
        with pytest.raises(RepoIntakeError) as excinfo:
            build_image(docker_client, tmp_path, tag="wta-smoke-nonet:latest",
                        created=created, build_network="none", build_timeout_s=180)
        assert "Build failed" in str(excinfo.value)
    finally:
        teardown(docker_client, created)


def test_a_repo_without_a_build_definition_is_refused_before_the_daemon_is_touched(tmp_path: Path):
    with pytest.raises(RepoIntakeError, match="No Dockerfile or compose file"):
        with deploy_repository(tmp_path):
            pass


# --- adversarial: opt-in, disposable environments only -----------------------------


@adversarial
def test_a_privileged_compose_file_is_refused(tmp_path: Path):
    """Gated with the rest even though it never reaches the daemon.

    It belongs to the adversarial *story*, and splitting the story across two files by
    implementation detail makes the suite harder to read than the marker costs.
    """
    from web_testing_agent.intake.compose_policy import UnsafeRepositoryError

    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  evil:\n    image: alpine\n    privileged: true\n"
        "    volumes: ['/:/host']\n    network_mode: host\n",
        encoding="utf-8",
    )
    with pytest.raises(UnsafeRepositoryError):
        with deploy_repository(tmp_path):
            pass


@adversarial
def test_a_container_that_tries_to_write_its_rootfs_is_stopped(docker_client, tmp_path: Path):
    """Whether `read_only: True` is actually enforced, as opposed to merely requested."""
    (tmp_path / "Dockerfile").write_text(
        'FROM busybox:1.36\nEXPOSE 8080\nCMD ["sh", "-c", "touch /escape && sleep 60"]\n',
        encoding="utf-8",
    )
    from web_testing_agent.intake.runner import HealthCheckTimeout

    with pytest.raises((HealthCheckTimeout, RepoIntakeError)):
        with deploy_repository(tmp_path, client=docker_client,
                               build_network="bridge", boot_timeout_s=30):
            pass


@adversarial
def test_a_fork_bomb_is_bounded_by_the_pids_limit(docker_client, tmp_path: Path):
    """`pids_limit` under real load.

    **Destructive if the limit is not enforced.** This is the clearest example of why the
    suite is opt-in: the failure mode of the assertion is the host becoming unusable.
    """
    (tmp_path / "Dockerfile").write_text(
        'FROM busybox:1.36\nEXPOSE 8080\nCMD ["sh", "-c", ":(){ :|:& };:"]\n',
        encoding="utf-8",
    )
    from web_testing_agent.intake.runner import HealthCheckTimeout

    with pytest.raises((HealthCheckTimeout, RepoIntakeError)):
        with deploy_repository(tmp_path, client=docker_client,
                               build_network="bridge", boot_timeout_s=30):
            pass


@adversarial
def test_a_build_that_never_finishes_is_abandoned_at_the_deadline(docker_client, tmp_path: Path):
    """The build timeout against a daemon that really is still working.

    Bounds our wait, not the daemon's CPU: the `RUN` keeps going after we stop reading,
    which is why this needs a machine whose CPU you are willing to donate for a minute.
    """
    (tmp_path / "Dockerfile").write_text(
        "FROM busybox:1.36\nRUN sleep 600\n", encoding="utf-8"
    )
    from web_testing_agent.intake.runner import BuildTimeout

    created = _Created()
    try:
        with pytest.raises(BuildTimeout):
            build_image(docker_client, tmp_path, tag="wta-adv-slow:latest",
                        created=created, build_network="bridge", build_timeout_s=15)
    finally:
        teardown(docker_client, created)
