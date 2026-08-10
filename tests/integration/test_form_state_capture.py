"""The captured observation must reflect live form state, not just authored markup.

Needs a real browser: the defect these cover is precisely the gap between the DOM
*properties* a user's input writes and the *attributes* `page.content()` serializes,
which no mock can reproduce.
"""

import re
from pathlib import Path

import pytest

from web_testing_agent.envs.functional_env import WebFunctionalEnv
from web_testing_agent.envs.types import ActionType
from web_testing_agent.perception.normalization import state_fingerprint
from web_testing_agent.utils.local_server import serve_directory

TOY_SITE = Path(__file__).resolve().parents[1] / "fixtures" / "toy_site"


@pytest.fixture(scope="module")
def origin():
    with serve_directory(TOY_SITE) as base:
        yield base


@pytest.fixture
def signup_env(origin):
    env = WebFunctionalEnv(base_url=f"{origin}/signup.html", max_steps=8, headless=True)
    try:
        yield env
    finally:
        env.close()


def _find(env, action_type: ActionType, selector: str, **params) -> int:
    for spec in env._action_specs:
        if spec.action_type is not action_type or spec.selector != selector:
            continue
        if all(spec.params.get(k) == v for k, v in params.items()):
            return spec.index
    raise AssertionError(f"no {action_type} action for {selector} {params}")


def _attr(html: str, element_id: str) -> str:
    """The opening tag of #element_id, for asserting on its serialized attributes."""
    match = re.search(rf'<(?:a|button|input|textarea|select)[^>]*id="{element_id}"[^>]*>', html)
    assert match is not None, f"#{element_id} not found in captured html"
    return match.group(0)


def test_typed_text_appears_in_the_captured_html(signup_env):
    """Regression: TYPE left the markup byte-identical, so the env could not perceive it."""
    signup_env.reset()
    before = signup_env._last_raw_obs.html
    index = _find(signup_env, ActionType.TYPE, '[id="username"]', category="valid_typical")
    _, _, _, _, info = signup_env.step(index)

    after = info["page"]["html"]
    assert before != after
    assert 'value="Test User Input"' in _attr(after, "username")


def test_filling_a_form_is_a_state_change(signup_env):
    """Otherwise progressing through a flow earns no novelty reward — it looks like a no-op."""
    signup_env.reset()
    before = state_fingerprint(signup_env._last_raw_obs.url, signup_env._last_raw_obs.html)
    index = _find(signup_env, ActionType.TYPE, '[id="username"]', category="valid_typical")
    signup_env.step(index)
    after = state_fingerprint(signup_env._last_raw_obs.url, signup_env._last_raw_obs.html)
    assert before != after


def test_selected_option_is_reflected(signup_env):
    signup_env.reset()
    index = _find(signup_env, ActionType.SELECT, '[id="country"]')
    _, _, _, _, info = signup_env.step(index)
    html = info["page"]["html"]
    selected = re.findall(r'<option[^>]*selected[^>]*value="(\w+)"|<option[^>]*value="(\w+)"[^>]*selected', html)
    assert selected, "no option was serialized as selected"


def test_checkbox_state_is_reflected(origin):
    """BUG-09's entire evidence is checkbox state; without this it is invisible."""
    env = WebFunctionalEnv(base_url=f"{origin}/settings.html", max_steps=8, headless=True)
    try:
        env.reset()
        assert "checked" not in _attr(env._last_raw_obs.html, "opt-darkmode")
        index = _find(env, ActionType.CLICK, '[id="opt-darkmode"]')
        _, _, _, _, info = env.step(index)
        assert "checked" in _attr(info["page"]["html"], "opt-darkmode")
    finally:
        env.close()


def test_dark_mode_preference_visibly_fails_to_persist(origin):
    """The full BUG-09 repro must be observable end to end: check -> save -> refresh."""
    env = WebFunctionalEnv(base_url=f"{origin}/settings.html", max_steps=8, headless=True)
    try:
        env.reset()
        env.step(_find(env, ActionType.CLICK, '[id="opt-darkmode"]'))
        env.step(_find(env, ActionType.CLICK, '[id="btn-save"]'))
        saved = env._last_raw_obs.html
        assert "checked" in _attr(saved, "opt-darkmode"), "checkbox was not on before the refresh"

        refresh = next(s.index for s in env._action_specs if s.action_type is ActionType.REFRESH)
        _, _, _, _, info = env.step(refresh)
        assert "checked" not in _attr(info["page"]["html"], "opt-darkmode"), (
            "the preference persisted; BUG-09 no longer reproduces"
        )
    finally:
        env.close()


def test_password_values_are_never_serialized(origin):
    """Observations are written to disk and sent to an LLM judge."""
    env = WebFunctionalEnv(base_url=f"{origin}/signup.html", max_steps=4, headless=True)
    try:
        env.reset()
        page = env._browser.page
        page.evaluate(
            "() => { const i = document.createElement('input');"
            " i.type = 'password'; i.id = 'pw'; i.value = 'hunter2';"
            " document.body.appendChild(i); }"
        )
        html = env._capture_html(page)
        assert 'id="pw"' in html
        assert "hunter2" not in html
    finally:
        env.close()


def test_a_filled_password_field_is_still_visibly_filled(origin):
    """Withholding the value must not make the field look untouched.

    Regression, measured on Gitea's login form. With the value simply omitted, the
    serialized markup stayed byte-identical across every TYPE into a password field, so
    the window reported "NO OBSERVABLE CHANGE" — the strongest bug signal in the prompt
    — and drew two confident verdicts (`ui_regression`, `other`) on an application that
    had behaved correctly. Occupancy is what the judge and state identity both need;
    the content is what must never leave.
    """
    env = WebFunctionalEnv(base_url=f"{origin}/signup.html", max_steps=4, headless=True)
    try:
        env.reset()
        page = env._browser.page
        page.evaluate(
            "() => { const i = document.createElement('input');"
            " i.type = 'password'; i.id = 'pw'; document.body.appendChild(i); }"
        )
        empty = env._capture_html(page)
        page.evaluate("() => { document.getElementById('pw').value = 'hunter2'; }")
        filled = env._capture_html(page)

        assert empty != filled, "typing a password must change the observation"
        assert "hunter2" not in filled
        assert "[password withheld]" in filled
        # The marker is fixed-width, so it cannot leak the password's length either.
        page.evaluate("() => { document.getElementById('pw').value = 'a-much-longer-secret'; }")
        assert env._capture_html(page) == filled
    finally:
        env.close()


def test_projection_does_not_mutate_the_live_document(origin):
    """Writing attributes into the real DOM would fire the app's own MutationObservers."""
    env = WebFunctionalEnv(base_url=f"{origin}/settings.html", max_steps=4, headless=True)
    try:
        env.reset()
        page = env._browser.page
        page.evaluate("() => document.getElementById('opt-darkmode').click()")
        env._capture_html(page)
        live_attr = page.evaluate(
            "() => document.getElementById('opt-darkmode').hasAttribute('checked')"
        )
        assert live_attr is False
    finally:
        env.close()


def test_hidden_elements_are_marked_in_the_captured_html(origin):
    """BUG-08: the CTA is present in the markup and hidden by a media query.

    No text view of the DOM can see a responsive-layout regression without this, so
    without the annotation the bug is unreachable for a text judge by construction.
    """
    env = WebFunctionalEnv(base_url=f"{origin}/index.html", max_steps=4, headless=True)
    try:
        env.reset()
        desktop = env._last_raw_obs.html
        assert 'id="cta-signup"' in desktop
        assert "data-hidden" not in _attr(desktop, "cta-signup")

        preset = next(
            s.index for s in env._action_specs
            if s.action_type is ActionType.RESIZE_VIEWPORT and s.params.get("preset") == "mobile"
        )
        _, _, _, _, info = env.step(preset)
        mobile = info["page"]["html"]
        assert 'id="cta-signup"' in mobile, "the element should still be in the DOM"
        assert 'data-hidden="true"' in _attr(mobile, "cta-signup")
    finally:
        env.close()


def test_visible_controls_are_not_annotated(origin):
    """The annotation must stay sparse, or it is noise rather than signal."""
    env = WebFunctionalEnv(base_url=f"{origin}/widgets.html", max_steps=4, headless=True)
    try:
        env.reset()
        html = env._last_raw_obs.html
        assert "data-hidden" not in _attr(html, "btn-export")
        assert "data-hidden" not in _attr(html, "btn-report")
    finally:
        env.close()
