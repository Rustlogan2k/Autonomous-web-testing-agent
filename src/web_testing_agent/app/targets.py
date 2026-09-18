"""How a repository becomes a running application the browser can reach.

Two adapters, both shaped as `pipeline.run_pipeline`'s `deployer` contract —
`Callable[[Path, RunSettings], AbstractContextManager]` yielding something with
`base_url` — so the pipeline is unchanged and unaware of which one it got.

**`docker_deployer` is the real path and adds nothing of its own.** It delegates straight
to `intake.deploy_repository`, which owns the policy gate, the build, `SANDBOX_RUN_ARGS`,
the health check and exhaustive teardown. Writing a second Docker path here would mean a
second, weaker set of security defaults; there is deliberately no such code in this file.

**`static_deployer` exists for Demo Mode and does not run user code at all.** It serves a
directory of files this repository ships, over `utils.local_server.serve_directory`, which
the smoke test and every fixture experiment already use. It is offered only for bundled
fixtures — never for an upload — because serving an untrusted directory from the host
process is exactly the thing the Docker path exists to avoid. `demo_targets()` is the
allow-list, and `run_manager` never passes it anything else.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..utils.logging import get_logger

logger = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "tests" / "fixtures"


@dataclass(frozen=True, slots=True)
class DemoTarget:
    """A bundled application the product can test without an upload or a daemon."""

    key: str
    name: str
    path: Path
    #: The document the run starts at, relative to what is *served*. Meaningful only for
    #: static targets: a Docker target's entry point is whatever its image serves at `/`,
    #: which this layer does not get to choose.
    entry: str
    summary: str
    expectation: str
    needs_docker: bool = False

    @property
    def available(self) -> bool:
        if not self.path.is_dir():
            return False
        if self.needs_docker:
            return any((self.path / name).is_file()
                       for name in ("Dockerfile", "dockerfile"))
        return (self.path / self.entry).is_file()

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "summary": self.summary,
                "expectation": self.expectation, "needs_docker": self.needs_docker,
                "available": self.available}


def demo_targets() -> list[DemoTarget]:
    """The allow-list of bundled demo applications.

    These are the project's existing research fixtures, used **as they are**. Nothing in
    this module edits a fixture, its answer key or its behaviour: the whole point of a
    demo built on them is that the report it produces is the report the research system
    produces.
    """
    return [
        DemoTarget(
            key="toy_site",
            name="Acme Tools (validation site)",
            path=FIXTURES / "toy_site",
            entry="index.html",
            summary="The project's 10-bug validation application. Shallow flows, a signup "
                    "form, a settings page and an archive view.",
            expectation="Several deterministic findings are expected — broken navigation "
                        "and console errors are seeded here on purpose.",
        ),
        DemoTarget(
            key="deep_flow_site",
            name="Northwind Supply (deep-flow fixture)",
            path=FIXTURES / "deep_flow_site",
            entry="index.html",
            summary="A four-stage gated ordering flow whose single seeded defect is only "
                    "reachable after completing every stage in order.",
            expectation="The deterministic ceiling here is zero by construction: the seeded "
                        "defect needs a language model to see. Expect depth and coverage, "
                        "not deterministic findings.",
        ),
        DemoTarget(
            key="demo_repo",
            name="Northwind static site (Docker)",
            path=FIXTURES / "demo_repo",
            # Served by the image's own httpd from /www, so the URL entry is the root.
            entry="",
            summary="A tiny Dockerised repository (busybox httpd). Exercises the full "
                    "upload path — policy gate, image build, sandboxed container, health "
                    "check — without an upload.",
            expectation="Demonstrates the container pipeline end to end. Requires a running "
                        "Docker daemon.",
            needs_docker=True,
        ),
    ]


def demo_target(key: str) -> DemoTarget | None:
    return next((t for t in demo_targets() if t.key == key), None)


@dataclass
class StaticDeployment:
    """The `Deployment`-shaped result of serving a directory.

    Carries the same attribute names `pipeline.run_pipeline` reads off a real
    `Deployment` (`base_url`, `image_tag`, `container_id`, `host_port`) so the pipeline
    needs no branch. The container fields are empty because there is no container, which
    is exactly what the run page then shows.
    """

    base_url: str = ""
    image_tag: str = ""
    container_id: str = ""
    host_port: int = 0
    container_port: int = 0
    build_log: list[str] = field(default_factory=list)
    plan: object = None


def static_deployer(directory: Path, entry: str = "index.html"):  # noqa: ANN201
    """A deployer that serves `directory` over HTTP for the life of the run.

    Restricted to bundled fixtures by the caller. Binds to 127.0.0.1 on an ephemeral port
    via the existing `serve_directory`, so nothing is exposed off-host and the server dies
    with the context.
    """

    @contextlib.contextmanager
    def deploy(repo: Path, settings) -> Iterator[StaticDeployment]:  # noqa: ANN001, ARG001
        from ..utils.local_server import serve_directory

        with serve_directory(Path(directory)) as origin:
            port = int(origin.rsplit(":", 1)[-1])
            logger.info("demo target served at {} from {}", origin, directory)
            yield StaticDeployment(
                base_url=f"{origin}/{entry}",
                image_tag="(static fixture — no image built)",
                host_port=port,
                container_port=port,
            )

    return deploy


def static_detector(directory: Path):  # noqa: ANN201
    """A `detector` for a static fixture, which has no build definition.

    `pipeline.run_pipeline` calls the detector before deploying and the real one raises
    when a repository describes no build — correct for an upload, wrong for a directory of
    HTML this repository ships. Returns a `BuildPlan`-shaped record saying exactly that,
    so `run_meta["build_definition"]` stays truthful rather than blank.
    """

    def detect(repo: Path):  # noqa: ANN001, ANN202, ARG001
        from ..intake.runner import BuildPlan

        return BuildPlan(kind="static", path=Path(directory))

    return detect


def docker_deployer():  # noqa: ANN201
    """The real sandboxed path: `intake.deploy_repository`, unmodified.

    Returned as a factory for symmetry with `static_deployer` so `run_manager` chooses
    between two values of the same shape. `pipeline.default_deployer` already applies
    `RunSettings`' port, build network and timeouts, and `deploy_repository` applies
    `SANDBOX_RUN_ARGS`.
    """
    from ..pipeline import default_deployer

    return default_deployer
