"""The product tester's verification of trigger findings, and the report it writes.

No browser: the replayer is a scripted fake, which is enough to pin the verification rules
and the report's honesty about what was and was not verified.
"""

from __future__ import annotations

from pathlib import Path

from web_testing_agent.envs.types import ActionSpec, ActionType, BugSignals, NetworkEvent
from web_testing_agent.tester.report import render_html, render_markdown
from web_testing_agent.tester.session import SessionResult
from web_testing_agent.tester.triggers import TriggerFinding, TriggerMonitor, signatures


def _click(label: str) -> ActionSpec:
    return ActionSpec(index=9, action_type=ActionType.CLICK, element_id=label,
                      selector=f'[id="{label.lower()}"]', params={"navigational": True})


def _info(url: str, spec: ActionSpec | None, signals: BugSignals | None = None) -> dict:
    return {"page": {"url": url, "html": ""}, "action_spec": spec, "bug_signals": signals,
            "exec_info": {"success": True}}


class FakeReplayer:
    """Fires the pricing 404 whenever the trace clicks 'Pricing', optionally only once."""

    def __init__(self, flaky: bool = False) -> None:
        self.flaky = flaky
        self.calls = 0

    def run(self, actions, *, capture=False):  # noqa: ANN001, ARG002
        self.calls += 1
        fired = set()
        if any(a.get("id") == "pricing" for a in actions) and not (self.flaky and self.calls > 1):
            fired.add(("broken_navigation", "/pricing.html", "Pricing"))
        return {"fired": fired, "misses": 0, "final": {}}


def test_a_trigger_that_reproduces_is_verified_and_minimized():
    monitor = TriggerMonitor(FakeReplayer())
    monitor.start_episode(_info("http://a/index.html", None))
    for label in ("About", "Help"):
        monitor.observe(_info("http://a/x.html", _click(label)), _click(label), [], 1)
    pricing = _click("Pricing")
    verified = monitor.observe(_info("http://a/pricing.html", pricing,
                                     BugSignals(broken_navigation=True)), pricing, [pricing], 3)
    assert [f.status for f in verified] == ["verified"]
    assert verified[0].minimized_trace == [{"type": "CLICK", "id": "pricing"}]
    assert verified[0].replays == [True, True]


def test_a_trigger_that_does_not_reproduce_stays_a_candidate():
    monitor = TriggerMonitor(FakeReplayer(flaky=True))
    monitor.start_episode(_info("http://a/index.html", None))
    pricing = _click("Pricing")
    assert monitor.observe(_info("http://a/pricing.html", pricing,
                                 BugSignals(broken_navigation=True)), pricing, [pricing], 1) == []
    finding = next(iter(monitor.findings.values()))
    assert finding.status == "not_reproduced" and not finding.verified


def test_a_spent_verification_time_budget_leaves_candidates_unverified():
    monitor = TriggerMonitor(FakeReplayer(), max_verification_seconds=0.0)
    monitor.start_episode(_info("http://a/index.html", None))
    pricing = _click("Pricing")
    assert monitor.observe(_info("http://a/pricing.html", pricing,
                                 BugSignals(broken_navigation=True)), pricing, [pricing], 1) == []
    finding = next(iter(monitor.findings.values()))
    assert not finding.verified and not finding.replays
    assert monitor.replayer.calls == 0


def test_identities_ignore_ports_and_request_ids():
    event = NetworkEvent(url="http://127.0.0.1:5123/api/item?id=77", method="GET",
                         request_headers={}, request_body=None, response_status=500,
                         response_headers={}, response_body_snippet="", timestamp=0.0,
                         duration_ms=1.0, resource_type="xhr")
    one = signatures(_info("http://127.0.0.1:5123/list.html", None,
                           BugSignals(unexpected_http_errors=[event], console_errors=["Error 77"])))
    assert ("http_error", "/list.html", "500 /api/item") in one
    assert ("console_error", "/list.html", "Error") in one


def _finding(kind: str, detail: str) -> dict:
    finding = TriggerFinding(kind=kind, page="/pricing.html", detail=detail, first_seen_at=4,
                             verified=True, status="verified",
                             minimized_trace=[{"type": "CLICK", "text": "Pricing"}],
                             minimization={"original_length": 9})
    return finding.to_dict()


def test_signals_with_one_root_cause_are_reported_once_under_the_most_specific_title():
    result = SessionResult(
        target="http://a/", app_name="Demo", settings={}, started_at="now",
        triggers={"verified": 3, "candidates": 3, "findings": [
            _finding("console_error", "Failed to load resource"),
            _finding("document_http_error", "404 /pricing.html"),
            _finding("broken_navigation", "Pricing")]},
        degradations=["LLM progress reward disabled: Ollama is not reachable"])
    markdown = render_markdown(result)
    assert "**1 verified finding(s)**" in markdown
    assert "### 1. Page failed to load" in markdown
    assert "This run was degraded" in markdown and "Ollama is not reachable" in markdown
    assert "<h1>Test report: Demo</h1>" in render_html(result, Path("."))


def test_page_text_ignores_markup_scripts_and_numbers_but_not_words():
    from web_testing_agent.envs.functional_env import _page_text

    before = "<main><h1>Ticket #109</h1><script>t=1</script><p>Status: open</p></main>"
    clock = "<main><h1>Ticket #110</h1><p>Status:  open</p></main>"
    resolved = "<main><h1>Ticket #109</h1><p>Status: resolved</p></main>"
    assert _page_text(before) == _page_text(clock)
    assert _page_text(before) != _page_text(resolved)


class _Stop(Exception):
    pass


def _navigation_flag(monkeypatch, exec_info: dict, post_html: str = "<p>same</p>") -> bool:
    """What `_compute_reward` tells the bug detector about a navigational link click."""
    from types import SimpleNamespace

    from web_testing_agent.envs import functional_env

    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        raise _Stop

    monkeypatch.setattr(functional_env, "detect_bug_signals", capture)
    env = functional_env.WebFunctionalEnv.__new__(functional_env.WebFunctionalEnv)
    env.navigation_needs_change = True
    page = SimpleNamespace(url="http://app.test/", html="<p>same</p>")
    context = SimpleNamespace(
        spec=_click("Docs"), pre_obs=page, post_obs=SimpleNamespace(url=page.url, html=post_html),
        exec_info=exec_info, load_duration_s=0.1, settled=True, previously_settled=True,
        error_baseline=None, opened_new_page=False,
    )
    try:
        env._compute_reward(context)
    except _Stop:
        pass
    return seen["was_navigation_action"]


def test_a_link_that_changes_nothing_is_still_a_navigation_check(monkeypatch):
    assert _navigation_flag(monkeypatch, {"success": True}) is True


def test_links_out_of_the_application_are_not_broken_navigation(monkeypatch):
    left = {"success": False, "left_application": "https://github.com/x"}
    tab = {"success": True, "opened_offsite_tab": "https://github.com/x"}
    assert _navigation_flag(monkeypatch, left) is False
    assert _navigation_flag(monkeypatch, tab) is False


def test_the_product_detects_listener_clickables_by_default():
    from web_testing_agent.tester.session import SessionSettings

    settings = SessionSettings()
    assert settings.custom_clickables and settings.listener_clickables
