"""The benchmark suite: which applications exist, how they are served, how an env is built.

One place answers those three questions, so that the validator, the training loop, the
transfer evaluation and the tests cannot each hold a slightly different idea of what
"application 3, faulty build, as the agent sees it" means.

An application lives in a directory of its own:

    <suite root>/<app_id>/
        clean/          the application with no seeded fault
        buggy/          generated from clean/ by build.py, never edited by hand
        build.py        the patches that turn clean into buggy, one per fault
        faults.json     the fault library: assertions, reachability witnesses, regions

`clean/` and `buggy/` are served on **separate origins**. Serving both under one origin
would let browser storage written on one build be read on the other, and the clean-build
control in the oracle is only a control if the two builds share nothing.
"""

from __future__ import annotations

import contextlib
import os
import runpy
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..envs.action_registry import EXTENDED_OPTIONS, RegistryOptions
from ..oracle import BenchmarkApp
from ..utils.local_server import serve_directory

#: Overrides the suite location, for a container that mounts the fixtures elsewhere.
ROOT_ENV_VAR = "WTA_BENCHMARK_ROOT"
_DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "benchmark"

BUILDS: tuple[str, str] = ("buggy", "clean")


def suite_root() -> Path:
    override = os.environ.get(ROOT_ENV_VAR)
    return Path(override) if override else _DEFAULT_ROOT


@dataclass(frozen=True)
class SuiteApp:
    """One benchmark application on disk."""

    app_id: str
    root: Path
    manifest: BenchmarkApp

    def build_dir(self, build: str) -> Path:
        if build not in BUILDS:
            raise ValueError(f"build must be one of {BUILDS}, got {build!r}")
        return self.root / build

    def entry_url(self, origin: str) -> str:
        return f"{origin.rstrip('/')}/{self.manifest.entry}"


def discover(root: Path | None = None) -> list[SuiteApp]:
    """Every application in the suite, in `app_id` order.

    The order is the identity of the suite: leave-one-application-out folds are assigned
    mechanically from it, so no fold is ever chosen.
    """
    base = Path(root) if root else suite_root()
    apps: list[SuiteApp] = []
    if not base.is_dir():
        return apps
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        manifest_path = directory / "faults.json"
        if not manifest_path.is_file():
            continue
        manifest = BenchmarkApp.load(manifest_path)
        apps.append(SuiteApp(app_id=manifest.app_id, root=directory, manifest=manifest))
    return sorted(apps, key=lambda app: app.app_id)


def load_app(app_id: str, root: Path | None = None) -> SuiteApp:
    for app in discover(root):
        if app.app_id == app_id:
            return app
    known = ", ".join(a.app_id for a in discover(root)) or "none"
    raise KeyError(f"no benchmark application {app_id!r} (known: {known})")


def seeded_patches(app: SuiteApp) -> list:
    """The patches `build.py` declares, each with `fault_id`, `filename`, `find`, `replace`."""
    namespace = runpy.run_path(str(app.root / "build.py"), run_name="wta_benchmark_build")
    return list(namespace["PATCHES"])


def materialize_without(app: SuiteApp, fault_id: str, destination: Path) -> Path:
    """Write the faulty build minus one fault's patches into `destination`.

    The oracle's per-fault control: a violation that still happens here does not depend on
    `fault_id`'s patch, so it is not evidence of that fault. Built from `clean/` and the
    declared patches exactly as `build.py` builds `buggy/`, so it differs from `buggy/` in
    precisely one fault.
    """
    shutil.copytree(app.build_dir("clean"), destination, dirs_exist_ok=True)
    for patch in seeded_patches(app):
        if patch.fault_id == fault_id:
            continue
        target = destination / patch.filename
        with target.open("r", encoding="utf-8", newline="") as handle:
            text = handle.read()
        if text.count(patch.find) != 1:
            raise ValueError(f"{app.app_id}: patch {patch.fault_id} anchor is not unique in "
                             f"{patch.filename}; the control build would not match buggy/")
        with target.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text.replace(patch.find, patch.replace, 1))
    return destination


def control_key(fault_id: str) -> str:
    return f"without:{fault_id}"


@contextlib.contextmanager
def serve_app(app: SuiteApp, builds: tuple[str, ...] = BUILDS, *,
              controls: bool = False) -> Iterator[dict[str, str]]:
    """Serve the requested builds, each on its own origin. Yields `{build: entry_url}`.

    With `controls=True` it also serves, for every seeded fault, the faulty build without
    that fault's patch, under the key `control_key(fault_id)`.
    """
    with contextlib.ExitStack() as stack:
        urls = {
            build: app.entry_url(stack.enter_context(serve_directory(app.build_dir(build))))
            for build in builds
        }
        if controls:
            workspace = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="wta-control-")))
            for fault in app.manifest.faults:
                built = materialize_without(app, fault.fault_id, workspace / fault.fault_id)
                urls[control_key(fault.fault_id)] = app.entry_url(
                    stack.enter_context(serve_directory(built)))
        yield urls


@dataclass(frozen=True)
class EnvSettings:
    """Everything about the environment a result depends on, in one recordable object.

    `settle_quiet_ms` is the DOM-quiescence window. The benchmark applications are static
    pages whose scripts run synchronously inside the event that triggers them, so they are
    quiescent long before the 250 ms a real application is given; the value used is part
    of every run's metadata precisely because it is a choice and not a constant.
    """

    max_steps: int = 40
    settle_quiet_ms: int | None = None
    headless: bool = True
    registry: RegistryOptions = field(default_factory=lambda: EXTENDED_OPTIONS)
    #: Off for experiments (the learners read the DOM, never pixels); on for the product.
    capture_screenshots: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def make_env(base_url: str, settings: EnvSettings | None = None, **overrides):
    """A `WebFunctionalEnv` on `base_url`, configured by `settings`.

    `overrides` reach the env constructor unchanged (`reward_model`, `reward_weights`,
    `max_steps` for a replay that needs a longer leash than an exploration episode).
    """
    from ..envs import WebFunctionalEnv  # deferred: importing the env pulls in Playwright

    settings = settings or EnvSettings()
    kwargs = {
        "base_url": base_url,
        "max_steps": settings.max_steps,
        "headless": settings.headless,
        "settle_quiet_ms": settings.settle_quiet_ms,
        "registry_options": settings.registry,
        "capture_screenshots": settings.capture_screenshots,
    }
    kwargs.update(overrides)
    return WebFunctionalEnv(**kwargs)
