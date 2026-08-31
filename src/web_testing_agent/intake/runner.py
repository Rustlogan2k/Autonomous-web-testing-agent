"""Build and run a repository, hand back a `base_url`, and always tear it down.

This is §3.0a's runner. It completes the intake path:

    detection -> policy gate -> controlled build -> run -> health check
              -> base_url -> WebFunctionalEnv -> teardown

**Read this before trusting it with anything.**

*Scope is trusted / controlled repositories.* Resource-limited execution of code you
already have reason to trust — your own fixtures, a known application, a repo handed to
you by a collaborator. It is **not a security boundary against a determined attacker**,
and no combination of the options here makes it one.

*The decisive limitation is that `docker build` executes arbitrary code before any of
this applies.* Every `RUN` line in a Dockerfile is code execution at build time. The
constraints in `SANDBOX_RUN_ARGS` — dropped capabilities, read-only rootfs, pids and
memory caps, a non-root user — are arguments to `docker run`. They constrain a container
that only exists *after* the build has already finished running whatever it wanted. A
repository that wants to do something hostile does not need to escape the container; it
can do it during the build. `build_network="none"` (the default here) removes the most
useful capability — egress — but does not change the fact that the code ran.

*The daemon is as trusted as the code it builds.* On a rootful daemon (Docker Desktop's
WSL2 backend included) the build runs as root inside the VM, and anything that VM can
reach — including host directories shared into it — is in scope. Run this against a
disposable environment if the input is not genuinely trusted.

**Compose files are detected, policy-gated, and refused.** `compose_policy` validates
them, but the runner does not execute them. Applying `SANDBOX_RUN_ARGS` to a multi-service
compose file means generating an override and hoping the merge does what you expect, and
"the constraints were approximately applied" is not a claim worth making about a security
control. Dockerfile builds are executed because there the runtime arguments are applied
directly and verifiably. This is a deliberate scope cut in the spirit of §6.2, not a gap.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import socket
import tarfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ..utils.logging import get_logger
from .compose_policy import (
    BUILD_TIMEOUT_S,
    SANDBOX_RUN_ARGS,
    PolicyResult,
    UnsafeRepositoryError,
    validate_compose_file,
    validate_dockerfile,
)

logger = get_logger(__name__)

# Filenames that mean "this repo says how to build itself". Order matters: a repo with
# both is treated as a compose project, because that is the one that describes the whole
# system rather than one image.
COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
DOCKERFILE_NAMES = ("Dockerfile", "dockerfile")

# How long to wait for the application to answer on its port before giving up. Distinct
# from BUILD_TIMEOUT_S: a build that hangs and a container that never listens are
# different failures and deserve different diagnostics.
BOOT_TIMEOUT_S = 180
HEALTH_POLL_INTERVAL_S = 1.0

# Build output is untrusted text of unbounded length. Kept for diagnostics, capped so a
# repository cannot exhaust memory by printing, and so a failure message stays readable.
MAX_BUILD_LOG_LINES = 400
MAX_LOG_LINE_CHARS = 500
MAX_CONTAINER_LOG_BYTES = 32_000

_EXPOSED_PORT = re.compile(r"^(\d+)")


class RepoIntakeError(RuntimeError):
    """Any failure along the intake path, carrying diagnostics with it."""

    def __init__(self, message: str, *, diagnostics: dict | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}

    def __str__(self) -> str:
        base = super().__str__()
        if not self.diagnostics:
            return base
        rendered = json.dumps(self.diagnostics, indent=2, default=str)
        return f"{base}\n--- diagnostics ---\n{rendered}"


class BuildTimeout(RepoIntakeError):
    """The build exceeded its deadline and was abandoned."""


class HealthCheckTimeout(RepoIntakeError):
    """The container started but never answered on its port."""


@dataclass(frozen=True, slots=True)
class BuildPlan:
    """What was found in the repository and what will be done about it."""

    kind: str  # "dockerfile" | "compose"
    path: Path
    policy: PolicyResult | None = None

    @property
    def runnable(self) -> bool:
        return self.kind == "dockerfile"


def detect_build_definition(repo: Path) -> BuildPlan:
    """Find the repo's build definition, preferring compose when both exist.

    Shallow by design: root, then one level down. A build definition buried four levels
    deep is not this repo's entry point, and searching the whole tree invites picking up
    a vendored example from a dependency.
    """
    repo = Path(repo)
    if not repo.is_dir():
        raise RepoIntakeError(f"Not a directory: {repo}")

    candidates: list[Path] = []
    for name in COMPOSE_NAMES + DOCKERFILE_NAMES:
        candidates.append(repo / name)
        candidates.extend(sorted(repo.glob(f"*/{name}")))

    for path in candidates:
        if not path.is_file():
            continue
        if path.name in COMPOSE_NAMES:
            return BuildPlan("compose", path, validate_compose_file(path))
        return BuildPlan("dockerfile", path, validate_dockerfile(path.read_text(
            encoding="utf-8", errors="replace"), location=str(path)))

    raise RepoIntakeError(
        f"No Dockerfile or compose file found in {repo} (searched the root and one level "
        f"down). v1 requires the repository to describe its own build; see §6.2.",
        diagnostics={"searched": list(COMPOSE_NAMES + DOCKERFILE_NAMES)},
    )


@dataclass
class Deployment:
    """A running application and everything created to get it there."""

    base_url: str = ""
    container_id: str = ""
    image_id: str = ""
    image_tag: str = ""
    network_id: str = ""
    host_port: int = 0
    container_port: int = 0
    build_log: list[str] = field(default_factory=list)
    plan: BuildPlan | None = None


@dataclass
class _Created:
    """Everything this run brought into existence, so teardown can be exhaustive.

    Recorded as it happens rather than inferred at the end. A container that failed its
    health check still exists, and a build that timed out still leaves an image layer and
    possibly a running intermediate — teardown driven by "what did we create" cleans those
    up, whereas teardown driven by "what succeeded" leaks exactly the failure cases.
    """

    containers: list[Any] = field(default_factory=list)
    networks: list[Any] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    volumes: list[Any] = field(default_factory=list)


def _free_port() -> int:
    """An ephemeral host port, bound to loopback only."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _tar_context(repo: Path) -> io.BytesIO:
    """The build context as an in-memory tar.

    Used instead of handing the daemon a path so the context is assembled by us and its
    size is knowable before anything is sent.
    """
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        archive.add(str(repo), arcname=".")
    stream.seek(0)
    return stream


def _exposed_port(image_attrs: dict) -> int | None:
    """The port the image says it listens on, if it says."""
    exposed = ((image_attrs or {}).get("Config") or {}).get("ExposedPorts") or {}
    for key in exposed:
        match = _EXPOSED_PORT.match(str(key))
        if match:
            return int(match.group(1))
    return None


def build_image(
    client: Any,
    repo: Path,
    *,
    tag: str,
    created: _Created,
    build_network: str | None = "none",
    build_timeout_s: int = BUILD_TIMEOUT_S,
    nocache: bool = False,
) -> tuple[str, list[str]]:
    """Build the repo's Dockerfile under a deadline, returning (image_id, log lines).

    `build_network` defaults to `"none"`. This is the single most useful build-time
    control available: it does not stop the repository's build commands from running --
    nothing does, short of not building -- but it denies them egress, which is what most
    build-time exfiltration or dial-home needs. Repositories that install packages at
    build time will fail under it, and switching to `"bridge"` to fix that is a real
    loosening that this module logs loudly rather than doing quietly.

    The deadline is enforced by consuming the build stream and abandoning it when the
    clock runs out. The daemon may keep working on an already-issued step for a while
    after that -- there is no reliable cross-platform way to interrupt a running `RUN` --
    so this bounds *our* wait and the layers we keep, not the daemon's CPU.
    """
    logger.info("building {} from {} (network={}, timeout={}s)", tag, repo, build_network, build_timeout_s)
    if build_network != "none":
        logger.warning(
            "build network is {!r}: the repository's build commands have network access, "
            "which is a real loosening of an already-narrow control", build_network,
        )

    log: list[str] = []
    image_id = ""
    started = time.monotonic()
    stream = client.api.build(
        fileobj=_tar_context(repo),
        custom_context=True,
        tag=tag,
        rm=True,
        forcerm=True,
        nocache=nocache,
        network_mode=build_network,
        decode=True,
    )
    created.images.append(tag)

    for chunk in stream:
        elapsed = time.monotonic() - started
        if elapsed > build_timeout_s:
            with contextlib.suppress(Exception):
                stream.close()
            raise BuildTimeout(
                f"Build exceeded {build_timeout_s}s and was abandoned.",
                diagnostics={"tag": tag, "elapsed_s": round(elapsed, 1), "log_tail": log[-40:]},
            )
        if not isinstance(chunk, dict):
            continue
        if "error" in chunk:
            raise RepoIntakeError(
                f"Build failed: {str(chunk['error']).strip()[:400]}",
                diagnostics={"tag": tag, "log_tail": log[-40:]},
            )
        text = str(chunk.get("stream") or chunk.get("status") or "").rstrip()
        if text and len(log) < MAX_BUILD_LOG_LINES:
            log.append(text[:MAX_LOG_LINE_CHARS])
        aux = chunk.get("aux") or {}
        if isinstance(aux, dict) and aux.get("ID"):
            image_id = str(aux["ID"])

    if not image_id:
        # Older daemons do not emit `aux`; resolve by tag instead of guessing.
        image_id = str(getattr(client.images.get(tag), "id", "") or "")
    if not image_id:
        raise RepoIntakeError("Build produced no image id.", diagnostics={"tag": tag, "log_tail": log[-40:]})

    logger.info("built {} ({}) in {:.1f}s", tag, image_id[:19], time.monotonic() - started)
    return image_id, log


def wait_for_http(base_url: str, *, timeout_s: int = BOOT_TIMEOUT_S,
                  container: Any = None) -> None:
    """Poll until the application answers, or fail with why it did not.

    Any HTTP status counts as alive. A 404 or a 500 means something is listening and
    speaking HTTP, which is all this check is for -- deciding whether the application is
    healthy is the agent's job, and treating a 500 as "not started" would hide exactly the
    kind of broken deployment worth reporting.
    """
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        if container is not None:
            # Only the reload is suppressed. An earlier version wrapped the whole block,
            # which swallowed the HealthCheckTimeout raised two lines below and turned a
            # container that had already died into a silent wait for the full boot
            # timeout -- the opposite of the fast failure this check exists to give.
            with contextlib.suppress(Exception):
                container.reload()
            if getattr(container, "status", "") == "exited":
                raise HealthCheckTimeout(
                    "The container exited before serving anything.",
                    diagnostics={"status": container.status, "logs": _container_logs(container)},
                )
        try:
            with urllib.request.urlopen(base_url, timeout=5) as response:  # noqa: S310
                logger.info("health check: {} answered {}", base_url, response.status)
                return
        except urllib.error.HTTPError as exc:
            logger.info("health check: {} answered {}", base_url, exc.code)
            return
        except Exception as exc:  # noqa: BLE001 - connection refused while booting is normal
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(HEALTH_POLL_INTERVAL_S)

    raise HealthCheckTimeout(
        f"No HTTP response from {base_url} within {timeout_s}s.",
        diagnostics={
            "last_error": last_error,
            "logs": _container_logs(container) if container is not None else "",
        },
    )


def _container_logs(container: Any) -> str:
    try:
        raw = container.logs(tail=200)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not raise
        return f"<could not read logs: {exc}>"
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    return text[-MAX_CONTAINER_LOG_BYTES:]


def teardown(client: Any, created: _Created) -> list[str]:
    """Remove everything the run created. Never raises.

    Ordering matters: containers hold references to networks and images, so they go
    first. Every step is individually guarded because a teardown that stops at its first
    error leaks whatever came after it, and the common case for that error is an object
    already gone.
    """
    problems: list[str] = []

    for container in created.containers:
        try:
            container.remove(force=True, v=True)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"container {getattr(container, 'id', '?')[:12]}: {exc}")
    for network in created.networks:
        try:
            network.remove()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"network {getattr(network, 'id', '?')[:12]}: {exc}")
    for volume in created.volumes:
        try:
            volume.remove(force=True)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"volume {getattr(volume, 'id', '?')[:12]}: {exc}")
    for image in created.images:
        try:
            client.images.remove(image, force=True)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"image {image}: {exc}")

    created.containers.clear()
    created.networks.clear()
    created.volumes.clear()
    created.images.clear()

    if problems:
        logger.warning("teardown finished with {} problem(s): {}", len(problems), "; ".join(problems[:5]))
    else:
        logger.info("teardown: everything created by this run was removed")
    return problems


@contextlib.contextmanager
def deploy_repository(
    repo: Path,
    *,
    client: Any = None,
    port: int | None = None,
    build_network: str | None = "none",
    build_timeout_s: int = BUILD_TIMEOUT_S,
    boot_timeout_s: int = BOOT_TIMEOUT_S,
    run_args: dict | None = None,
    keep_image: bool = False,
) -> Iterator[Deployment]:
    """Build, run and health-check a repository; yield its `Deployment`; always tear down.

    The whole body is wrapped so teardown runs on success, on failure, and on an
    exception raised by the *caller* inside the `with` block. A leaked container from a
    failed run is the case most likely to happen and least likely to be noticed.

    `run_args` overrides individual `SANDBOX_RUN_ARGS` entries. The defaults are strict
    enough to break many real images (read-only rootfs, non-root user, all capabilities
    dropped); relaxing one is sometimes the only way to run a given application, and each
    relaxation is logged so the record of what was actually enforced is accurate.
    """
    if client is None:
        import docker  # imported lazily: everything above is testable without a daemon

        client = docker.from_env()

    repo = Path(repo)
    plan = detect_build_definition(repo)
    if plan.policy is not None and plan.policy.blocked:
        plan.policy.raise_if_blocked()
    if not plan.runnable:
        raise RepoIntakeError(
            f"Found {plan.path.name}, and compose projects are not executed by this runner. "
            f"The policy gate validated it, but applying the sandbox arguments to a "
            f"multi-service compose file means generating an override and trusting the "
            f"merge — 'approximately applied' is not a claim worth making about a security "
            f"control. Provide a Dockerfile, or run the compose file yourself and pass "
            f"--base-url to the agent.",
            diagnostics={"detected": plan.kind, "path": str(plan.path)},
        )

    created = _Created()
    deployment = Deployment(plan=plan)
    run_id = uuid.uuid4().hex[:12]
    tag = f"wta-target-{run_id}:latest"

    try:
        deployment.image_tag = tag
        deployment.image_id, deployment.build_log = build_image(
            client, repo, tag=tag, created=created,
            build_network=build_network, build_timeout_s=build_timeout_s,
        )
        if keep_image:
            created.images.remove(tag)

        container_port = port or _exposed_port(getattr(client.images.get(tag), "attrs", {}) or {})
        if not container_port:
            raise RepoIntakeError(
                "Could not determine which port the application listens on: the image "
                "declares no EXPOSE and no --port was given.",
                diagnostics={"tag": tag},
            )
        host_port = _free_port()

        # A dedicated bridge per run, so the target cannot see anything else on the
        # default bridge. Not `internal=True`: the browser driving this application runs
        # on the host, so the port has to be publishable, and an internal network cannot
        # publish. The consequence -- the container has outbound network access -- is a
        # real limitation of running the browser outside the sandbox, and is documented
        # rather than papered over.
        network = client.networks.create(f"wta-net-{run_id}", driver="bridge")
        created.networks.append(network)

        args = {**SANDBOX_RUN_ARGS, **(run_args or {})}
        relaxed = [k for k, v in (run_args or {}).items() if SANDBOX_RUN_ARGS.get(k) != v]
        if relaxed:
            logger.warning("sandbox arguments relaxed by caller: {}", ", ".join(sorted(relaxed)))
        args["network"] = network.name
        args.pop("network_mode", None)  # `network` and `network_mode` are mutually exclusive

        logger.info("running {} with container port {} -> 127.0.0.1:{}", tag, container_port, host_port)
        # **Created and started separately, so the container is in the teardown ledger
        # before anything can go wrong with starting it.**
        #
        # `docker-py`'s `containers.run()` is `create()` followed by an unguarded
        # `start()`. When `start()` raises, `run()` never returns the object, so the
        # caller cannot register it — and the container it already created is left
        # behind. That is not a rare path here: `SANDBOX_RUN_ARGS` is deliberately strict
        # enough to break many real images (read-only rootfs, `user=1000:1000`, all
        # capabilities dropped), and a host port can also be taken between `_free_port`
        # and this call. Every one of those failures used to leak a container.
        container = client.containers.create(
            tag,
            detach=True,
            name=f"wta-target-{run_id}",
            ports={f"{container_port}/tcp": ("127.0.0.1", host_port)},
            **args,
        )
        created.containers.append(container)
        container.start()

        deployment.container_id = str(getattr(container, "id", "") or "")
        deployment.network_id = str(getattr(network, "id", "") or "")
        deployment.container_port = container_port
        deployment.host_port = host_port
        deployment.base_url = f"http://127.0.0.1:{host_port}/"

        wait_for_http(deployment.base_url, timeout_s=boot_timeout_s, container=container)
        logger.info("deployment ready at {}", deployment.base_url)
        yield deployment
    finally:
        teardown(client, created)
