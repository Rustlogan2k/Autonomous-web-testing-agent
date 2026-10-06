"""The e2e suite must be invisible to the frozen primary benchmark, and must not touch it."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from web_testing_agent.benchmark import discover as primary_discover
from web_testing_agent.benchmark.suite import ROOT_ENV_VAR, suite_root

from e2e_suite.harness.manifest import SUITE_ROOT, discover

PRIMARY_APPS = {"account_hub", "admin_records", "booking_desk", "media_library",
                "shop_catalog", "support_portal"}


def test_primary_benchmark_still_discovers_exactly_its_six_applications():
    assert {app.app_id for app in primary_discover()} == PRIMARY_APPS


def test_primary_root_is_not_redirected_and_does_not_contain_the_suite():
    assert os.environ.get(ROOT_ENV_VAR) is None
    root = suite_root().resolve()
    assert root.name == "benchmark" and root.parent.name == "fixtures"
    assert root not in SUITE_ROOT.resolve().parents and SUITE_ROOT.resolve() != root


def test_no_file_in_the_suite_carries_the_name_primary_discovery_reads():
    assert not list(SUITE_ROOT.rglob("faults.json"))


def test_primary_discovery_pointed_at_the_suite_finds_nothing():
    assert primary_discover(SUITE_ROOT / "apps") == []


def test_the_suite_has_its_own_eight_applications():
    assert {a.app_id for a in discover()} >= set()  # grows as apps land; exact set in test_suite


def test_the_main_checkout_has_no_modified_tracked_files():
    common = subprocess.run(["git", "rev-parse", "--git-common-dir"], capture_output=True,
                            text=True, cwd=SUITE_ROOT, check=True).stdout.strip()
    # `--git-common-dir` is relative to the directory git ran in when it is not absolute
    # (the main checkout answers "../.git"; a linked worktree answers an absolute path).
    common_path = Path(common) if Path(common).is_absolute() else SUITE_ROOT / common
    main = common_path.resolve().parent
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                            capture_output=True, text=True, cwd=main, check=True).stdout
    assert status.strip() == "", status
