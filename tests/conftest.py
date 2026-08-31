"""What each part of the suite needs, stated in one place.

Three groups of tests need something the machine may not have: a GPU-stack install
(`torch`, `stable-baselines3`, `transformers`), a Docker daemon, or a real Chromium.
Before M6 the first two were handled inconsistently and the third not at all — the two
browser integration tests simply failed with a Playwright error on a machine without the
browser binary, which looks like a broken test suite rather than a missing dependency.

**Gating lives here rather than in the test files themselves.** The heavy modules are
mostly RL tests, which are frozen; marking them centrally means the freeze is not
disturbed to make the suite runnable elsewhere. It also keeps the whole picture of "what
does this suite require" in one readable list instead of scattered across 40 files.

**Nothing is skipped silently.** `pytest_report_header` prints what is available and what
was therefore ignored, so a short run in CI is explained by its own output rather than
looking like tests went missing.

Selection:

    pytest -m "not requires_torch and not requires_docker and not requires_browser"

is the portable subset, and is what CI runs.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path

import pytest

# Test modules whose *imports* need the deep-learning stack. Listed by filename because
# the alternative — a `pytest.importorskip` at the top of each — means editing frozen RL
# test files. Kept explicit rather than pattern-matched so adding a module to the heavy
# set is a deliberate act.
HEAVY_MODULES = frozenset({
    "test_archive_start.py",
    "test_dueling.py",
    "test_replay_buffer.py",
    "test_vec_wrapper.py",
    "test_encoders.py",
    "test_perception_wrapper.py",
    "test_action_features.py",
    "test_go_explore.py",
    "test_gating.py",
    "test_network_canonicalization.py",
    "test_trained_policy_eval.py",
})

DOCKER_MODULES = frozenset({"test_intake_docker_smoke.py"})
BROWSER_MODULES = frozenset({"test_form_state_capture.py", "test_offsite_navigation.py"})


@functools.lru_cache(maxsize=1)
def has_torch_stack() -> bool:
    """Whether the deep-learning stack is importable.

    Checked by import rather than by version pin: the question is only whether these
    modules will fail to load, and a partial install fails exactly the same way.
    """
    try:
        import gymnasium  # noqa: F401
        import stable_baselines3  # noqa: F401
        import torch  # noqa: F401
    except Exception:  # noqa: BLE001 - a broken install is as unusable as a missing one
        return False
    return True


@functools.lru_cache(maxsize=1)
def has_docker() -> bool:
    """The CLI on PATH **and** a daemon that actually answers.

    Checking only the CLI was not enough, and the gap showed up as a test failure rather
    than a skip. Docker Desktop leaves `docker` on PATH when the engine is stopped, so
    `test_intake_docker_smoke.py` was neither skipped nor able to run and failed with
    `DockerException: Error while fetching server API version`. A skip condition that does
    not match what the test needs reports a stopped daemon as broken code — which is the
    one thing a test suite must never do to whoever picks this up next.

    `docker info` is the cheapest call that proves the daemon is reachable, and the result
    is cached for the session, so the cost is one subprocess per run.
    """
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


@functools.lru_cache(maxsize=1)
def has_browser() -> bool:
    """Playwright *and* its Chromium download, which is a separate step from `pip install`.

    Checked lazily and cached: it starts the Playwright driver, which costs about a
    second, and a run that selects no browser tests should not pay for it.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        return False
    try:
        with sync_playwright() as play:
            return Path(play.chromium.executable_path).exists()
    except Exception:  # noqa: BLE001 - driver missing, browser not downloaded, etc.
        return False


def pytest_configure(config: pytest.Config) -> None:
    for name, need in (
        ("requires_torch", "needs torch / stable-baselines3 / gymnasium"),
        ("requires_docker", "needs a Docker daemon"),
        ("requires_browser", "needs Playwright with Chromium installed"),
    ):
        config.addinivalue_line("markers", f"{name}: {need}")


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Skip collecting modules whose imports would fail outright.

    A module that imports `torch` cannot be collected without it — the failure is a
    collection error, not a skip — so the only way to keep the rest of the suite runnable
    is to not collect it. Reported in the header, never silently.
    """
    name = collection_path.name
    if name in HEAVY_MODULES and not has_torch_stack():
        return True
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Attach the requirement markers, so `-m` selection works without editing test files."""
    for item in items:
        name = Path(str(item.fspath)).name
        if name in HEAVY_MODULES:
            item.add_marker(pytest.mark.requires_torch)
        if name in DOCKER_MODULES:
            item.add_marker(pytest.mark.requires_docker)
            if not has_docker():
                item.add_marker(pytest.mark.skip(
                    reason="no reachable Docker daemon (the CLI may be installed but "
                           "the engine stopped — start Docker Desktop to run these)"))
        if name in BROWSER_MODULES:
            item.add_marker(pytest.mark.requires_browser)
            if not has_browser():
                item.add_marker(pytest.mark.skip(
                    reason="Playwright Chromium not installed "
                           "(run `python -m playwright install chromium`)"))


def pytest_report_header(config: pytest.Config) -> list[str]:
    """Say what is available, so a short run explains itself."""
    ignored = len(HEAVY_MODULES) if not has_torch_stack() else 0
    line = (f"capabilities: torch-stack={has_torch_stack()} docker={has_docker()} "
            f"browser={has_browser()}")
    if ignored:
        line += f" ({ignored} deep-learning test modules not collected)"
    return [line]
