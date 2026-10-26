"""When the inferred-specification oracle writes its rules, and when it writes more.

No browser and no LLM: pages are synthetic and `llm.propose_rules` is replaced.
"""

from __future__ import annotations

from web_testing_agent.specinfer import llm, monitor
from web_testing_agent.specinfer.monitor import SpecMonitor

TEXT = "A page with enough visible text to count as a real page of the application. " * 2


def info(path: str) -> dict:
    return {"page": {"url": f"https://shop.test{path}", "html": f"<main><h1>{path}</h1><p>{TEXT}</p></main>"}}


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, app, snapshots, journeys, *, existing=None, intent="", model=None):  # noqa: ANN001, ANN204
        self.calls.append({"pages": [s["page"] for s in snapshots], "existing": list(existing or [])})
        n = len(self.calls)
        return {"rules": [{"id": "R1", "kind": "arithmetic", "description": f"rule from call {n}",
                           "page": "/never", "operands": {"a": {"selector": "#a"}}, "expression": "a == a"}]}


def _monitor(monkeypatch, **kwargs) -> tuple[SpecMonitor, FakeLLM]:  # noqa: ANN003
    fake = FakeLLM()
    monkeypatch.setattr(llm, "propose_rules", fake)
    spec = SpecMonitor(replayer=None, app_name="shop", judge=False, **kwargs)  # type: ignore[arg-type]
    spec.start_episode(info("/"))
    return spec, fake


def _walk(spec: SpecMonitor, paths: list[str], start: int = 1) -> int:
    for step, path in enumerate(paths, start):
        spec.observe(info(path), None, None, step)
    return start + len(paths) - 1


def test_rules_wait_until_page_discovery_levels_off(monkeypatch):
    spec, fake = _monitor(monkeypatch, warmup=300, min_warmup=20, stall=10)
    # A new page every two steps for 24 steps: no rules yet.
    last = _walk(spec, [f"/page-{chr(97 + n // 2)}" for n in range(24)])
    assert fake.calls == [] and spec.rules is None
    # Then nothing new for `stall` steps: rules are written, once, covering all 13 pages.
    _walk(spec, ["/"] * 12, start=last + 1)
    assert len(fake.calls) == 1 and spec.rules is not None
    assert "levelled off" in spec.inferences[0]["reason"] and len(fake.calls[0]["pages"]) == 13


def test_pages_beyond_one_round_get_the_next_round(monkeypatch):
    spec, fake = _monitor(monkeypatch, warmup=300, min_warmup=5, stall=5)
    last = _walk(spec, [f"/page-{chr(97 + n)}" for n in range(20)])
    _walk(spec, ["/page-a"] * 6, start=last + 1)
    assert [len(c["pages"]) for c in fake.calls] == [14, 6]


def test_the_cap_forces_rules_on_an_app_that_keeps_growing(monkeypatch):
    spec, fake = _monitor(monkeypatch, warmup=40, min_warmup=10, stall=50)
    _walk(spec, [f"/item-{chr(97 + n % 26)}{n}" for n in range(45)])
    assert len(fake.calls) >= 1
    assert "cap" in spec.inferences[0]["reason"]


def test_new_pages_after_the_rules_get_rules_of_their_own(monkeypatch):
    spec, fake = _monitor(monkeypatch, warmup=300, min_warmup=5, stall=5, refresh_after=2, max_refreshes=2)
    last = _walk(spec, ["/inventory"] * 8)
    assert len(fake.calls) == 1
    last = _walk(spec, ["/cart"], start=last + 1)
    assert len(fake.calls) == 1                     # one new page is not yet enough
    last = _walk(spec, ["/checkout-one"], start=last + 1)
    assert len(fake.calls) == 2                     # two are: rules for just those pages
    assert set(fake.calls[1]["pages"]) == {"/cart", "/checkout-one"}
    assert fake.calls[1]["existing"] == ["rule from call 1"]
    assert [r.id for r in spec.rules] == ["R1", "N1-R1"]
    # At most `max_refreshes` refreshes, however many pages follow.
    _walk(spec, [f"/extra-{chr(97 + n)}" for n in range(10)], start=last + 1)
    assert len(fake.calls) == 3
    assert spec.summary()["inferences"][0]["pages"] == ["/inventory"]


def test_rules_written_late_still_check_the_earlier_episodes(monkeypatch):
    fake = FakeLLM()
    fake_rules = {"rules": [{"id": "C", "kind": "arithmetic", "description": "total is 5", "page": "/cart",
                             "operands": {"t": {"selector": "#total"}}, "expression": "t == 5"}]}
    monkeypatch.setattr(llm, "propose_rules", lambda *a, **k: fake_rules)
    seen = []
    monkeypatch.setattr(SpecMonitor, "_confirm", lambda self, rule, finding: seen.append(finding.page) or False)
    spec = SpecMonitor(replayer=None, app_name="shop", warmup=300, min_warmup=5, stall=3)  # type: ignore[arg-type]
    spec.start_episode(info("/"))
    bad = {"page": {"url": "https://shop.test/cart", "html": f"<p>{TEXT}</p><b id='total'>7</b>"}}
    spec.observe(bad, None, None, 1)
    spec.start_episode(info("/"))
    _walk(spec, ["/"] * 6, start=2)
    assert seen == ["/cart"]
    assert monitor  # module imported for monkeypatching clarity
