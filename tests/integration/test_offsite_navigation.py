"""Leaving the application must be refused and undone, not end the episode.

Measured against real Gitea on first contact: its footer links point at github.com and
docs.gitea.com, an unguided explorer reached one within a few steps, and three
40-step episodes produced 22 steps in total. Terminating also hands an agent the same
escape hatch as reward-hacking exploit #4 — an early exit is valuable to a policy
accumulating negative reward, and on any real application an off-site link is always a
click away.
"""

import re
from pathlib import Path

import pytest

from web_testing_agent.envs.functional_env import WebFunctionalEnv
from web_testing_agent.envs.types import ActionType
from web_testing_agent.utils.local_server import serve_directory

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "offsite_site"


@pytest.fixture(scope="module")
def offsite_fixture(tmp_path_factory):
    """A two-page app whose second page is served from a *different* origin."""
    root = tmp_path_factory.mktemp("offsite")
    (root / "index.html").write_text(
        "<html><head><title>App</title></head><body>"
        '<h1>App</h1><a href="local.html" id="stay">Stay</a>'
        "<p id=marker>home</p></body></html>",
        encoding="utf-8",
    )
    (root / "local.html").write_text(
        "<html><head><title>Local</title></head><body><p id=marker>local page</p></body></html>",
        encoding="utf-8",
    )
    yield root


@pytest.fixture(scope="module")
def two_origins(offsite_fixture):
    """Serve the app and a stand-in 'external site' on two different ports."""
    other = offsite_fixture.parent / "external"
    other.mkdir(exist_ok=True)
    (other / "away.html").write_text(
        "<html><head><title>Elsewhere</title></head><body><p>off site</p></body></html>",
        encoding="utf-8",
    )
    with serve_directory(offsite_fixture) as app_origin, serve_directory(other) as away_origin:
        # Point the app's outbound link at the other origin now that its port is known.
        index = offsite_fixture / "index.html"
        index.write_text(
            index.read_text(encoding="utf-8").replace(
                "</body>", f'<a href="{away_origin}/away.html" id="leave">Leave</a></body>'
            ),
            encoding="utf-8",
        )
        yield app_origin, away_origin


def _click_index(env, element_id: str) -> int:
    for spec in env._action_specs:
        if spec.action_type is ActionType.CLICK and spec.selector == f'[id="{element_id}"]':
            return spec.index
    raise AssertionError(f"no CLICK action for #{element_id}")


def test_an_offsite_click_does_not_end_the_episode(two_origins):
    app_origin, _ = two_origins
    env = WebFunctionalEnv(base_url=f"{app_origin}/index.html", max_steps=10, headless=True)
    try:
        env.reset()
        _, _, terminated, truncated, info = env.step(_click_index(env, "leave"))
        assert not terminated, "leaving the app must be refused, not treated as the end"
        assert not truncated
    finally:
        env.close()


def test_the_browser_is_returned_to_the_application(two_origins):
    app_origin, away_origin = two_origins
    env = WebFunctionalEnv(base_url=f"{app_origin}/index.html", max_steps=10, headless=True)
    try:
        env.reset()
        _, _, _, _, info = env.step(_click_index(env, "leave"))
        assert info["page"]["url"].startswith(app_origin)
        assert "off site" not in info["page"]["html"]
        assert 'id="marker"' in info["page"]["html"]
    finally:
        env.close()


def test_the_blocked_navigation_is_reported_as_a_failed_action(two_origins):
    """The agent must be able to tell a refusal from a successful click."""
    app_origin, away_origin = two_origins
    env = WebFunctionalEnv(base_url=f"{app_origin}/index.html", max_steps=10, headless=True)
    try:
        env.reset()
        _, _, _, _, info = env.step(_click_index(env, "leave"))
        assert info["exec_info"]["success"] is False
        assert "left the application" in info["exec_info"]["error"]
        assert away_origin in info["exec_info"]["left_application"]
    finally:
        env.close()


def test_the_episode_continues_usefully_afterwards(two_origins):
    """The budget must survive: the whole point is not wasting the remaining steps."""
    app_origin, _ = two_origins
    env = WebFunctionalEnv(base_url=f"{app_origin}/index.html", max_steps=10, headless=True)
    try:
        env.reset()
        env.step(_click_index(env, "leave"))
        _, _, terminated, _, info = env.step(_click_index(env, "stay"))
        assert not terminated
        assert info["page"]["url"].endswith("local.html")
    finally:
        env.close()


def test_going_back_after_a_refusal_does_not_reach_the_offsite_page(two_origins):
    """A `goto` restore would leave the off-site page one BROWSER_BACK away."""
    app_origin, away_origin = two_origins
    env = WebFunctionalEnv(base_url=f"{app_origin}/index.html", max_steps=10, headless=True)
    try:
        env.reset()
        env.step(_click_index(env, "leave"))
        back = next(s.index for s in env._action_specs if s.action_type is ActionType.BROWSER_BACK)
        _, _, terminated, _, info = env.step(back)
        assert not terminated
        assert away_origin not in info["page"]["url"]
    finally:
        env.close()


def test_in_app_navigation_is_untouched(two_origins):
    app_origin, _ = two_origins
    env = WebFunctionalEnv(base_url=f"{app_origin}/index.html", max_steps=10, headless=True)
    try:
        env.reset()
        _, _, terminated, _, info = env.step(_click_index(env, "stay"))
        assert not terminated
        assert info["exec_info"]["success"] is True
        assert "local page" in info["page"]["html"]
    finally:
        env.close()
