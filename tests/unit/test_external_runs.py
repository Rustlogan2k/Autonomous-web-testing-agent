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
