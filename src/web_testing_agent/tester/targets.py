"""How an application under test is provided, launched and torn down.

    url        an application already running somewhere (a live website). Nothing is
               launched; the tester only ever visits the given origin.
    site       a static site: a directory, or a zip of one, whose `index.html` is the entry.
               Served locally on a free port; no Docker needed.
    repo       a repository with a Dockerfile. Built and run in the existing sandbox
               (`intake.deploy_repository`: no build network, read-only root, dropped
               capabilities, resource limits) and removed afterwards.
    benchmark  an application from the benchmark suite, served in both builds so the
               seeded-fault oracle and its clean-build control are available.

A site or repository that ships a `faults.json` in the benchmark format also gets the
seeded-fault oracle, so a user can declare invariants for their own application.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ..utils.local_server import serve_directory


@dataclass
class Target:
    kind: str
    url: str
    name: str
    clean_url: str | None = None
    seeded_app: object | None = None
    #: `{fault_id: url of the faulty build without that fault}` -- the per-fault controls.
    control_urls: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)
    #: A sign-in macro run at the start of every episode, for applications behind a login.
    setup_actions: list = field(default_factory=list)
    #: A development-suite application, whose extension-oracle faults (including crash-type
    #: faults) are verified beside the primary ones.
    suite_app: object | None = None


def _site_root(path: Path) -> Path:
    """The directory holding `index.html`: the path itself, one level down, or `www/`."""
    if (path / "index.html").is_file():
        return path
    for candidate in [path / "www", path / "public", path / "site", *sorted(path.iterdir())]:
        if candidate.is_dir() and (candidate / "index.html").is_file():
            return candidate
    raise FileNotFoundError(f"no index.html in {path} or one level below it")


def _unzip(path: Path, workspace: Path) -> Path:
    """Extract through the upload layer's hardened extractor (sizes, paths, symlinks)."""
    from ..app.uploads import extract_zip

    extracted = extract_zip(path.read_bytes(), path.name)
    destination = workspace / "site"
    shutil.copytree(extracted.root, destination)
    shutil.rmtree(extracted.workspace, ignore_errors=True)
    return destination


@contextlib.contextmanager
def provide(kind: str, value: str) -> Iterator[Target]:
    """Launch the target described by `(kind, value)` for the duration of the context."""
    if kind == "url":
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(f"not an http(s) URL: {value!r}")
        yield Target("url", value, parts.netloc)
        return

    if kind == "benchmark":
        from ..benchmark.suite import control_key, load_app, serve_app

        app = load_app(value)
        with serve_app(app, controls=True) as urls:
            yield Target("benchmark", urls["buggy"], app.manifest.name, clean_url=urls["clean"],
                         seeded_app=app.manifest, details={"app_id": app.app_id},
                         control_urls={f.fault_id: urls[control_key(f.fault_id)]
                                       for f in app.manifest.faults})
        return

    if kind == "suite":
        # A full-stack application of the development suite: its database server, sign-in,
        # every build (faulty, clean, one control per fault) and its seeded-fault manifest,
        # served by the suite's own harness exactly as the evaluations served it.
        from e2e_suite.harness.variants import control_key as suite_control_key

        from ..hybrid.suite import load_suite_app, serve_suite_app

        app = load_suite_app(value)
        manifest = app.oracle_app()
        with serve_suite_app(app, controls=True) as urls:
            yield Target("suite", urls["buggy"], app.data.get("name", app.app_id),
                         clean_url=urls["clean"], seeded_app=manifest,
                         control_urls={p.fault_id: urls[suite_control_key(p.fault_id)]
                                       for p in app.patches},
                         details={"app_id": app.app_id},
                         setup_actions=list(app.login_steps), suite_app=app)
        return

    if kind == "site":
        with tempfile.TemporaryDirectory(prefix="wta-site-") as workspace:
            path = Path(value)
            root = _site_root(_unzip(path, Path(workspace)) if path.suffix == ".zip" else path)
            seeded = None
            manifest = root / "faults.json"
            if not manifest.is_file():
                manifest = root.parent / "faults.json"
            if manifest.is_file():
                from ..oracle import BenchmarkApp

                seeded = BenchmarkApp.load(manifest)
            with serve_directory(root) as origin:
                yield Target("site", f"{origin}/index.html", root.name, seeded_app=seeded,
                             details={"root": str(root)})
        return

    if kind == "repo":
        from ..intake import deploy_repository

        with deploy_repository(Path(value)) as deployment:
            yield Target("repo", deployment.base_url, Path(value).name,
                         details={"image": getattr(deployment, "image_tag", "")})
        return

    raise ValueError(f"unknown target kind {kind!r}; expected url, site, repo, benchmark or suite")
