"""The external benchmark's run mechanics: a wall-clock budget, Istanbul branch coverage, and
a monitor for applications without seeded faults. No browser: the hunt loop runs on the fake
application of the hybrid policy's episode test."""

from __future__ import annotations

import itertools

import web_testing_agent.benchmark.hunt as hunt_module
from web_testing_agent.benchmark.hunt import run_hunt
from web_testing_agent.evaluation.rollout import RandomPolicy
from web_testing_agent.envs.types import MAX_ACTIONS
from web_testing_agent.hybrid.external import NullMonitor, run_all
from web_testing_agent.hybrid.metrics import IstanbulCollector
from web_testing_agent.hybrid.run import HybridJob

from .test_hybrid_policy_episode import FakeApp


def test_without_a_deadline_the_budget_alone_ends_the_run():
    result = run_hunt(FakeApp(), RandomPolicy(MAX_ACTIONS, seed=0, valid_only=True),
                      NullMonitor(), budget=120)
    assert result.steps == 120 and result.stopped_by == "budget"


def test_a_deadline_ends_the_run_before_the_budget(monkeypatch):
    clock = itertools.count(start=0.0, step=1.0)  # one second per call of the clock
    monkeypatch.setattr(hunt_module.time, "monotonic", lambda: next(clock))
    result = run_hunt(FakeApp(), RandomPolicy(MAX_ACTIONS, seed=0, valid_only=True),
                      NullMonitor(), budget=10**9, deadline_s=50)
    assert result.stopped_by == "deadline"
    assert 0 < result.steps < 50


def test_the_null_monitor_never_confirms_a_fault():
    result = run_hunt(FakeApp(), RandomPolicy(MAX_ACTIONS, seed=1, valid_only=True),
                      NullMonitor(), budget=60)
    assert result.censored and result.oracle["seeded"] == 0


def test_istanbul_branches_are_unioned_across_samples_and_pages():
    collector = IstanbulCollector()
    # first page: branch 0 took its first arm, branch 1 nothing
    collector.merge({"app.js": {"b": {"0": "10", "1": "000"}, "s": 10, "sh": 4}})
    # after a navigation the counters restart: branch 0's second arm and branch 1's last
    collector.merge({"app.js": {"b": {"0": "01", "1": "001"}, "s": 10, "sh": 3}})
    collector.merge({"vendor.js": {"b": {"0": "11"}, "s": 2, "sh": 2}})
    summary = collector.summary()
    assert summary["branches"] == 2 + 3 + 2
    assert summary["branches_covered"] == 2 + 1 + 2
    assert summary["branch_coverage"] == round(5 / 7, 4)
    assert summary["files"] == 2


def test_an_empty_collector_reports_no_coverage_rather_than_zero():
    assert IstanbulCollector().summary()["branch_coverage"] is None


def test_finished_runs_are_skipped_on_restart(tmp_path, monkeypatch):
    import json

    from web_testing_agent.hybrid import external

    job = HybridJob("random", "demo", 0, build="external")
    path = external.result_path(tmp_path, job)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(external.subprocess, "run", lambda *a, **k: calls.append(a))
    status = run_all([job], {"demo": {"url": "http://127.0.0.1:1/"}},
                     external.ExternalConfig(), tmp_path, workers=1)
    assert status["done"] == 1 and not calls


def test_the_container_comes_from_the_spec_or_its_docker_run_name():
    from web_testing_agent.hybrid.external import container_name

    assert container_name({"up": ["docker", "run", "-d", "--name", "wta-x", "img"]}) == "wta-x"
    assert container_name({"up": ["docker", "run", "--name=wta-y", "img"]}) == "wta-y"
    assert container_name({"container": "app", "up": ["docker", "compose", "up"]}) == "app"
    assert container_name({"up": ["docker", "compose", "up", "-d"]}) is None
    assert container_name({}) is None


def test_the_server_log_keeps_the_end_of_a_long_log(tmp_path, monkeypatch):
    from web_testing_agent.hybrid import external

    monkeypatch.setattr(external, "SERVER_LOG_BYTES", 10)
    monkeypatch.setattr(external.subprocess, "run", lambda *a, **k: external.subprocess.
                        CompletedProcess(a[0], 0, stdout=b"0123456789ABCDEF"))
    path = tmp_path / "job.server.log"
    assert external.save_server_log({"container": "app"}, path)
    assert path.read_bytes().endswith(b"6789ABCDEF")
    assert path.read_bytes().startswith(b"[... truncated")


def test_no_container_means_no_server_log(tmp_path):
    from web_testing_agent.hybrid.external import save_server_log

    assert not save_server_log({"up": ["docker", "compose", "up"]}, tmp_path / "x.log")
    assert not (tmp_path / "x.log").exists()
