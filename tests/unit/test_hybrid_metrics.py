"""Run metrics beside seeded faults: unique failures and client-side code coverage."""

from __future__ import annotations

from web_testing_agent.envs.types import BugSignals
from web_testing_agent.hybrid.metrics import CoverageCollector, FailureCounter

ORIGIN = "http://127.0.0.1:5000"


class _FakeCdp:
    def __init__(self, takes):
        self.takes = list(takes)

    def send(self, method, params=None):  # noqa: ANN001, ANN201, ARG002
        return {"result": self.takes.pop(0)} if method == "Profiler.takePreciseCoverage" else {}


class _FakeEnv:
    def __init__(self, page):
        self._browser = type("B", (), {"page": page, "on_new_page": None})()


def _fn(*ranges):
    return {"ranges": [{"startOffset": s, "endOffset": e, "count": c} for s, e, c in ranges]}


def _collector(takes):
    page = object()
    collector = CoverageCollector(ORIGIN)
    collector._page, collector._cdp = page, _FakeCdp(takes)
    return collector, _FakeEnv(page)


def test_nested_ranges_innermost_wins_and_samples_are_unioned():
    url = ORIGIN + "/app.js"
    first = [{"url": url, "functions": [_fn((0, 100, 1), (40, 60, 0))]}]  # 80 bytes ran
    # After a take, V8 lists only functions that ran since: no top-level entry here, and a
    # later sample must neither shrink the script nor forget what earlier samples covered.
    second = [{"url": url, "functions": [_fn((40, 50, 1))]}]
    third = [{"url": "http://elsewhere/x.js", "functions": [_fn((0, 10, 1))]}]  # other origin
    collector, env = _collector([first, second, third])
    for _ in range(3):
        collector.sample(env)
    summary = collector.summary()
    assert summary["js_bytes"] == 100 and summary["scripts"] == 1
    assert summary["js_bytes_covered"] == 90 and summary["js_coverage"] == 0.9


def test_a_page_the_collector_did_not_attach_to_is_not_sampled():
    collector, _ = _collector([[]])
    collector.sample(_FakeEnv(object()))
    assert collector.summary()["samples"] == 0


def test_failures_are_unique_by_identity_and_kind():
    counter = FailureCounter()

    def info(url, console=(), http=()):
        signals = BugSignals(console_errors=list(console))
        signals.unexpected_http_errors = list(http)
        return {"bug_signals": signals, "page": {"url": url}}

    counter.observe(info(ORIGIN + "/a", ["TypeError: x is undefined at 12"]), 3)
    counter.observe(info(ORIGIN + "/a", ["TypeError: x is undefined at 99"]), 7)  # same defect
    counter.observe(info(ORIGIN + "/b", ["TypeError: x is undefined at 12"]), 9)  # other page
    summary = counter.summary()
    assert summary["unique_failures"] == 2 and summary["first_failure_at"] == 3
    assert summary["by_kind"] == {"console_error": 2}
