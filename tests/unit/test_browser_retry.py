"""An episode's opening page load is retried before the session gives up. No browser."""

from __future__ import annotations

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from web_testing_agent.envs import browser_session
from web_testing_agent.envs.browser_session import START_ATTEMPTS, BrowserSession


class _Page:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.loads = 0

    def goto(self, url, wait_until):  # noqa: ANN001, ARG002
        self.loads += 1
        if self.loads <= self.failures:
            raise PlaywrightTimeoutError("Page.goto: Timeout 15000ms exceeded.")


class _Context:
    def __init__(self, page: _Page, storage_state=None) -> None:  # noqa: ANN001
        self.page = page
        self.received = storage_state
        self.cookies = list((storage_state or {}).get("cookies", []))

    def storage_state(self) -> dict:
        return {"cookies": list(self.cookies), "origins": []}

    def set_default_navigation_timeout(self, ms):  # noqa: ANN001, ARG002
        pass

    def add_init_script(self, script):  # noqa: ANN001, ARG002
        pass

    def new_page(self) -> _Page:
        return self.page

    def close(self) -> None:
        pass


class _Browser:
    def __init__(self, page: _Page) -> None:
        self.page = page

    def new_context(self, viewport, storage_state=None):  # noqa: ANN001, ARG002
        return _Context(self.page, storage_state)


def _session(page: _Page, monkeypatch) -> BrowserSession:
    session = BrowserSession.__new__(BrowserSession)
    session._browser = _Browser(page)
    session.context = None
    session.page = None
    session.init_scripts = []
    session.on_new_page = None
    session.persist_session = False
    monkeypatch.setattr(BrowserSession, "start", lambda self: None)
    monkeypatch.setattr(BrowserSession, "wait_settled", lambda self, *a, **k: True)
    return session


def test_a_slow_first_load_is_retried(monkeypatch):
    page = _Page(failures=START_ATTEMPTS - 1)
    assert _session(page, monkeypatch).new_episode("https://example.test/") is page
    assert page.loads == START_ATTEMPTS


def test_a_site_that_never_loads_still_fails(monkeypatch):
    page = _Page(failures=START_ATTEMPTS)
    with pytest.raises(PlaywrightTimeoutError):
        _session(page, monkeypatch).new_episode("https://example.test/")
    assert page.loads == START_ATTEMPTS
    assert browser_session.START_ATTEMPTS == START_ATTEMPTS


@pytest.mark.parametrize("persist", [False, True])
def test_the_session_is_carried_over_only_when_asked(monkeypatch, persist):
    session = _session(_Page(failures=0), monkeypatch)
    session.persist_session = persist
    session.new_episode("http://app/")
    session.context.cookies.append({"name": "sid", "value": "signed-in"})
    session.new_episode("http://app/")
    carried = [c["name"] for c in session.context.cookies]
    assert carried == (["sid"] if persist else [])
