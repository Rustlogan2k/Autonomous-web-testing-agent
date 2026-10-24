"""Listener-based clickable detection (`RegistryOptions.listener_clickables`) on a synthetic
page: what the page's own code made clickable is found; a delegated page-sized container is
not; and with the option off nothing changes."""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from web_testing_agent.envs.action_registry import (  # noqa: E402
    CLICK_LISTENER_INIT_JS,
    scan_custom_clickables,
)

PAGE = """
<html><body>
  <div id="root" style="width:100vw;height:100vh">
    <div id="by-listener">Archive card</div>
    <div id="by-react">Add new board</div>
    <a id="no-href">Sign up</a>
    <div id="plain">Just text</div>
  </div>
  <script>
    document.getElementById('by-listener').addEventListener('click', () => {});
    document.getElementById('root').addEventListener('click', () => {});
    const el = document.getElementById('by-react');
    el['__reactProps$abc'] = {onClick: () => {}};
  </script>
</body></html>
"""


@pytest.fixture(scope="module")
def page():
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            context.add_init_script(CLICK_LISTENER_INIT_JS)
            pg = context.new_page()
            pg.set_content(PAGE)
            yield pg
            browser.close()
    except Exception as exc:  # noqa: BLE001 - no browser on this machine
        pytest.skip(f"chromium unavailable: {exc}")


def test_listener_detection_finds_what_the_page_made_clickable(page):
    found = {e["id"] for e in scan_custom_clickables(page, listeners=True)}
    assert {"by-listener", "by-react", "no-href"} <= found
    assert "root" not in found  # delegated, page-sized: not a control
    assert "plain" not in found


def test_without_the_option_the_scan_is_unchanged(page):
    found = {e["id"] for e in scan_custom_clickables(page, listeners=False)}
    assert not {"by-listener", "by-react", "no-href"} & found
