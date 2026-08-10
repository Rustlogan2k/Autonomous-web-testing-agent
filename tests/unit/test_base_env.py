import io

import numpy as np
import pytest
from PIL import Image

from web_testing_agent.envs.base_env import WebTestingEnv
from web_testing_agent.envs.browser_session import BrowserSession
from web_testing_agent.envs.functional_env import WebFunctionalEnv
from web_testing_agent.envs.types import CANONICAL_VIEWPORT, SCREENSHOT_SHAPE, VIEWPORT_PRESETS, ActionType


def _env() -> WebFunctionalEnv:
    # Constructing WebFunctionalEnv doesn't launch a browser - that only happens in reset().
    return WebFunctionalEnv(base_url="http://localhost:4280/dvwa/", max_steps=5)


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


# --- domain / episode termination -----------------------------------------------


def test_same_domain_url_does_not_terminate():
    assert not _env()._left_target_domain("http://localhost:4280/dvwa/login.php")


def test_different_host_terminates():
    assert _env()._left_target_domain("http://evil.example.com/")


def test_different_port_terminates():
    assert _env()._left_target_domain("http://localhost:9999/dvwa/")


def test_loopback_aliases_are_treated_as_the_same_host():
    """A redirect between localhost and 127.0.0.1 is not "the agent left the app"."""
    assert BrowserSession.domain_of("http://127.0.0.1:3000/x") == BrowserSession.domain_of("http://localhost:3000/y")


def test_www_prefix_does_not_terminate():
    assert BrowserSession.domain_of("http://www.example.com/") == BrowserSession.domain_of("http://example.com/")


def test_action_index_out_of_range_resolves_to_no_op():
    env = _env()
    env._action_specs = []
    assert env._resolve_action(42).action_type == ActionType.NO_OP


# --- observation shape ----------------------------------------------------------


def test_viewport_presets_agree_with_the_canonical_viewport():
    """RESIZE_VIEWPORT('desktop') is the agent's only way back to the starting geometry."""
    assert VIEWPORT_PRESETS["desktop"] == (CANONICAL_VIEWPORT["width"], CANONICAL_VIEWPORT["height"])


def test_screenshot_at_the_canonical_viewport_matches_the_declared_shape():
    frame = WebTestingEnv._canonicalize_screenshot(_png(1280, 720))
    assert frame.shape == SCREENSHOT_SHAPE
    assert frame.dtype == np.uint8


@pytest.mark.parametrize("preset", sorted(VIEWPORT_PRESETS))
def test_screenshots_after_any_resize_keep_the_declared_shape(preset):
    """Regression: a mobile-viewport capture is (812, 375, 3) and violated the space."""
    width, height = VIEWPORT_PRESETS[preset]
    assert WebTestingEnv._canonicalize_screenshot(_png(width, height)).shape == SCREENSHOT_SHAPE


def test_full_page_capture_is_rescaled_not_cropped():
    frame = WebTestingEnv._canonicalize_screenshot(_png(1280, 4000))
    assert frame.shape == SCREENSHOT_SHAPE


def test_observation_space_contains_a_real_observation():
    """The old Dict(Text) space rejected its own observations and failed check_env."""
    env = _env()
    observation = {"screenshot": np.zeros(SCREENSHOT_SHAPE, dtype=np.uint8)}
    assert env.observation_space.contains(observation)


# --- navigation escape guard ----------------------------------------------------


def test_about_blank_is_not_treated_as_leaving_the_app():
    """Regression: BROWSER_BACK on step 1 ended every episode.

    A fresh Playwright context starts on about:blank, so the episode's opening goto()
    leaves that entry in history. Going back landed there, its empty netloc read as
    "left the target domain", and the episode terminated after one step. A DQN learned
    to fire it immediately to cap its accumulated negative reward — mean episode length
    fell to 1.05 steps.
    """
    env = _env()
    assert not env._left_target_domain("about:blank")


@pytest.mark.parametrize("url", ["blob:http://localhost:4280/abc", "data:text/html,x", "chrome://settings"])
def test_non_http_urls_never_terminate_the_episode(url):
    """Downloads and browser-internal pages are artifacts, not navigations away.

    Relevant well beyond the toy site: Gitea and Nextcloud are full of download links.
    """
    assert not _env()._left_target_domain(url)


def test_a_genuine_offsite_navigation_still_terminates():
    assert _env()._left_target_domain("http://evil.example.com/")


def test_is_in_app_rejects_non_http_and_cross_domain():
    reference = "http://localhost:4280/app"
    assert BrowserSession.is_in_app("http://localhost:4280/other", reference)
    assert not BrowserSession.is_in_app("about:blank", reference)
    assert not BrowserSession.is_in_app("http://elsewhere.test/", reference)


# --- foreign console/page events ------------------------------------------------


class _FakeConsoleMessage:
    def __init__(self, text: str, url: str, type_: str = "error") -> None:
        self.text = text
        self.type = type_
        self.location = {"url": url, "lineNumber": 1, "columnNumber": 1}


class _FakePage:
    def __init__(self, url: str) -> None:
        self.url = url


def _env_on(url: str) -> WebFunctionalEnv:
    env = _env()
    env._browser.page = _FakePage(url)
    return env


def test_console_error_from_the_application_is_kept():
    env = _env_on("http://localhost:4280/dvwa/")
    env._on_console_message(_FakeConsoleMessage("boom", "http://localhost:4280/dvwa/app.js"))
    assert env._console_buffer == ["boom"]


def test_console_error_from_a_foreign_origin_is_dropped_even_after_returning_to_the_app():
    """Regression: the buffer clear is a timing fix and loses a routine race.

    Refusing an off-site navigation clears the buffers, but a third-party beacon the
    foreign document requested resolves afterwards and lands on the *next* step, by
    which point the browser is back inside the application. Measured on the recapture
    that was meant to prove the clear sufficient: two static.cloudflareinsights.com
    errors from a code.gitea.io page were attributed to Gitea one step after the
    refusal. Filtering on the event's own origin closes the race outright.
    """
    env = _env_on("http://localhost:4280/dvwa/")  # already restored to the app
    env._on_console_message(
        _FakeConsoleMessage(
            "Loading the script 'https://static.cloudflareinsights.com/beacon.min.js' violates CSP",
            "https://static.cloudflareinsights.com/beacon.min.js",
        )
    )
    assert env._console_buffer == []


def test_console_error_with_no_location_is_attributed_to_the_current_page():
    off_site = _env_on("https://docs.gitea.com/install/")
    off_site._on_console_message(_FakeConsoleMessage("boom", ""))
    assert off_site._console_buffer == []

    in_app = _env_on("http://localhost:4280/dvwa/")
    in_app._on_console_message(_FakeConsoleMessage("boom", ""))
    assert in_app._console_buffer == ["boom"]


def test_inline_and_generated_sources_are_never_foreign():
    """data:/blob:/about: are produced by the page itself, so there is no origin to blame."""
    env = _env_on("http://localhost:4280/dvwa/")
    for source in ("", "data:text/html,x", "blob:http://localhost:4280/abc", "about:blank"):
        assert not env._is_foreign_source(source)


def test_page_error_raised_in_a_foreign_document_is_dropped():
    env = _env_on("https://github.com/go-gitea/gitea")
    env._on_page_error(RuntimeError("TypeError from someone else's site"))
    assert env._page_error_buffer == []


def test_page_error_raised_in_the_application_is_kept():
    env = _env_on("http://localhost:4280/dvwa/")
    env._on_page_error(RuntimeError("TypeError: x is not a function"))
    assert len(env._page_error_buffer) == 1


def test_a_non_error_console_message_is_never_recorded():
    env = _env_on("http://localhost:4280/dvwa/")
    env._on_console_message(_FakeConsoleMessage("just logging", "http://localhost:4280/x.js", type_="log"))
    assert env._console_buffer == []


# --- popup attribution ------------------------------------------------------------


class _FakeTab:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    def set_viewport_size(self, size) -> None:  # noqa: ANN001
        pass


class _FakeContext:
    def __init__(self, pages) -> None:
        self.pages = pages


def _session(active_url: str, other_urls: list[str]) -> tuple[BrowserSession, list[_FakeTab]]:
    session = BrowserSession()
    active = _FakeTab(active_url)
    others = [_FakeTab(u) for u in other_urls]
    session.page = active
    session.context = _FakeContext([active, *others])
    return session, others


def _in_app(url: str) -> bool:
    return url.startswith("http://localhost:4280")


def test_a_tab_left_over_from_an_earlier_step_is_closed_not_adopted():
    """Regression, measured on Gitea and the root cause of its remaining false positives.

    Popup creation is racy, so a tab opened at step 1 routinely first appears in
    context.pages several steps later. Adopting it there attributed it to whatever
    action was running: a click on the *internal* link /user/login?redirect_to=%2f and a
    click on the `remember` **checkbox** were both recorded as
    `left_application: https://github.com/go-gitea/gitea`. The harness then "restored" a
    page it had never left, and the reload wiped the typed form values — which the judge
    reported, at 90-95% confidence, as the application discarding the user's input.
    """
    session, others = _session("http://localhost:4280/app", ["https://github.com/x"])
    session.mark_pages()  # the stale tab already exists before this action runs
    adopted, offsite = session.take_new_page(accept=_in_app)
    assert adopted is None
    assert offsite == ""
    assert others[0].closed, "a stale tab must be closed so it cannot be adopted later"


def test_an_offsite_tab_opened_by_this_action_is_discarded_and_reported():
    session, _ = _session("http://localhost:4280/app", [])
    session.mark_pages()
    session.context.pages.append(_FakeTab("https://about.gitea.com/"))
    adopted, offsite = session.take_new_page(accept=_in_app)
    assert adopted is None
    assert offsite == "https://about.gitea.com/"


def test_an_in_app_tab_opened_by_this_action_is_adopted():
    session, _ = _session("http://localhost:4280/app", [])
    session.mark_pages()
    session.context.pages.append(_FakeTab("http://localhost:4280/popup"))
    adopted, offsite = session.take_new_page(accept=_in_app)
    assert adopted is not None and adopted.url == "http://localhost:4280/popup"
    assert offsite == ""
    assert session.page is adopted


def test_no_new_tab_means_nothing_happens():
    session, _ = _session("http://localhost:4280/app", [])
    session.mark_pages()
    assert session.take_new_page(accept=_in_app) == (None, "")
