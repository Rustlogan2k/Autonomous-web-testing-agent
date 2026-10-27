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
    #: How the test-session path provides this target: `(kind, value)` for
    #: `tester.targets.provide`. Every demo has one.
    session: tuple[str, str] = ("", "")

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

    The two development-suite applications on which the default agent verified the most
    findings in this app's own runs (Helpline, then Orbit). They are used **as they are**:
    nothing here edits an application, its answer key or its behaviour, so the report a demo
    produces is the report the research system produces. Each is served with its clean build
    and per-fault control builds, so a demo run also exercises the seeded-fault oracle.
    """
    return _suite_demos()


#: Full-stack applications of the development suite offered as demos, each behind a sign-in
#: and with seeded functional and crash-type faults.
SUITE_DEMOS = ("helpline", "orbit")


def _suite_demos() -> list[DemoTarget]:
    import json

    demos = []
    for app_id in SUITE_DEMOS:
        root = REPO_ROOT / "e2e_suite" / "apps" / app_id
        try:
            data = json.loads((root / "app.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        database = "PostgreSQL" if "postgres" in data.get("stack", "") else "MongoDB"
        demos.append(DemoTarget(
            key=f"suite:{app_id}", name=f"{data.get('name', app_id)} (full-stack)",
            path=root, entry="app.json",
            summary=f"{data.get('domain', '').capitalize()}: a {database} application behind a "
                    f"sign-in, with {len(data.get('faults', []))} seeded faults.",
            expectation="Signs in at every episode; seeded faults are verified against "
                        "the per-fault control builds. Needs Docker for the database.",
            needs_docker=True, session=("suite", app_id)))
    return demos


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
