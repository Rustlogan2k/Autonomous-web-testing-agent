"""WebFunctionalEnv - Agent B's Gymnasium environment for functional bug detection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..reward.base import FunctionalRewardModel, NullRewardModel
from ..reward.functional_triggers import FunctionalRewardWeights, compose_reward, detect_bug_signals
from ..utils.logging import get_logger
from .action_registry import (
    LEGACY_OPTIONS,
    RegistryOptions,
    build_action_specs,
    CLICK_LISTENER_INIT_JS,
    scan_custom_clickables,
    scan_page_elements,
)
from .base_env import StepContext, WebTestingEnv
from .types import VIEWPORT_PRESETS, ActionSpec, ActionType

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = get_logger(__name__)

_PLAYWRIGHT_ACTION_TIMEOUT_MS = 5_000


class WebFunctionalEnv(WebTestingEnv):
    """Agent B: DQN over CLICK/TYPE/SELECT/SCROLL/RESIZE_VIEWPORT/RAPID_CLICK/BACK/FORWARD/REFRESH."""

    def __init__(
        self,
        base_url: str,
        max_steps: int = 200,
        headless: bool = True,
        render_mode: str | None = None,
        reward_model: FunctionalRewardModel | None = None,
        reward_weights: FunctionalRewardWeights | None = None,
        repetition_window: int = 20,
        setup_actions: list[dict] | None = None,
        settle_quiet_ms: int | None = None,
        registry_options: RegistryOptions | None = None,
        capture_screenshots: bool = True,
        reveal_min_retained: float = 0.0,
        reveal_run_decay: bool = False,
        navigation_needs_change: bool = False,
    ) -> None:
        super().__init__(
            base_url=base_url,
            max_steps=max_steps,
            headless=headless,
            render_mode=render_mode,
            repetition_window=repetition_window,
            setup_actions=setup_actions,
            settle_quiet_ms=settle_quiet_ms,
            capture_screenshots=capture_screenshots,
            reveal_min_retained=reveal_min_retained,
            reveal_run_decay=reveal_run_decay,
        )
        # Default is the original registry, so every pre-benchmark experiment re-runs
        # against the action space it was measured on. See `RegistryOptions`.
        self.registry_options = registry_options or LEGACY_OPTIONS
        self.reward_model = reward_model or NullRewardModel()
        self.reward_weights = reward_weights or FunctionalRewardWeights()
        #: Report a navigation control as broken only when the page's text did not change
        #: either. Off by default (the frozen research environment compares URLs only). A
        #: form that posts and redirects back to its own URL (post-redirect-get), or a
        #: control that updates part of the page in place, changes the page without
        #: changing the URL; the URL rule calls every one of them broken.
        self.navigation_needs_change = navigation_needs_change
        if self.registry_options.custom_clickables and self.registry_options.listener_clickables:
            self._browser.init_scripts.append(CLICK_LISTENER_INIT_JS)

    def _start_reward_model_episode(self) -> None:
        self.reward_model.start_episode()

    def _build_action_specs(self) -> list[ActionSpec]:
        page = self._browser.page
        assert page is not None
        elements = scan_page_elements(page)
        if self.registry_options.custom_clickables:
            elements = elements + scan_custom_clickables(
                page, listeners=self.registry_options.listener_clickables)
        # The page's own URL decides whether a link is a self-link, and a self-link
        # cannot be a broken navigation. Read here rather than cached, because the scan
        # describes whatever document is loaded *now*.
        return build_action_specs(elements, page_url=page.url, options=self.registry_options)

    def _execute_action(self, spec: ActionSpec) -> dict:
        page = self._browser.page
        assert page is not None
        # BACK/FORWARD go through the session rather than a bare page handler: they need
        # the guard that stops history from walking out of the application entirely.
        if spec.action_type in (ActionType.BROWSER_BACK, ActionType.BROWSER_FORWARD):
            direction = "back" if spec.action_type is ActionType.BROWSER_BACK else "forward"
            moved = self._browser.history_move(direction)
            return {"success": moved, "error": None if moved else f"no in-app history to go {direction}"}

        handler = _ACTION_HANDLERS.get(spec.action_type)
        if handler is None:
            return {"success": False, "error": f"no handler for {spec.action_type}"}
        try:
            return {"success": True, "error": None, **(handler(page, spec) or {})}
        except PlaywrightTimeoutError as exc:
            logger.debug("Action {} timed out: {}", spec.description, exc)
            return {"success": False, "error": f"timeout: {exc}"}
        except Exception as exc:  # noqa: BLE001 - a stale selector/detached element is expected data, not a crash
            logger.debug("Action {} failed: {}", spec.description, exc)
            return {"success": False, "error": str(exc)}

    def _compute_reward(self, context: StepContext) -> tuple[float, dict]:
        # "Broken navigation" (spec: link click -> same URL or 404) only makes sense for
        # clicks expected to change the URL - a link or a submit button. REFRESH is
        # *supposed* to keep the same URL, BACK/FORWARD may legitimately no-op with no
        # history, and a checkbox/plain-button click isn't expected to navigate at all.
        # `_is_navigational` in the action registry also excludes fragment/javascript:
        # hrefs, target="_blank" links, and links resolving to the document they are
        # already in — none of which can change the current URL either.
        spec = context.spec
        was_navigation_action = (
            spec.action_type == ActionType.CLICK and spec.params.get("navigational", False)
        )
        if (was_navigation_action and self.navigation_needs_change
                and _page_text(context.pre_obs.html) != _page_text(context.post_obs.html)):
            was_navigation_action = False
        # A link out of the application did navigate: the harness refused and undid it
        # (or closed the off-site tab), so the unchanged URL is ours, not the link's.
        # Measured on demo.playwright.dev's TodoMVC: its three footer links to other
        # sites were each reported, reproduced and minimised as "navigation does nothing".
        if context.exec_info.get("left_application") or context.exec_info.get("opened_offsite_tab"):
            was_navigation_action = False
        # The click did load a document: a form that posts and redirects back to its own URL
        # (a cart's "+") changes only numbers, which the text comparison above ignores.
        if context.exec_info.get("navigated"):
            was_navigation_action = False
        exec_success = bool(context.exec_info.get("success", False))
        bug_signals = detect_bug_signals(
            after=context.post_obs,
            load_duration_s=context.load_duration_s,
            pre_url=context.pre_obs.url,
            post_url=context.post_obs.url,
            was_navigation_action=was_navigation_action,
            settled=context.settled,
            previously_settled=context.previously_settled,
            baseline=context.error_baseline,
            opened_new_page=context.opened_new_page,
        )
        # A finding is paid for once per episode. Without this, holding the app on one
        # broken page out-earns finding anything else — see FindingLedger.
        new_triggers = context.finding_ledger.classify(
            state_key=context.state_key,
            element=spec.element_id or spec.selector or spec.action_type.value,
            bug_signals=bug_signals,
        )
        # `context` carries what the two observations cannot: whether the action ran,
        # whether the harness refused it, whether the page settled. A judge needs all
        # three to render the window it was measured against (see reward.base).
        llm_signal = self.reward_model.score(
            context.pre_obs, spec, context.post_obs, context=context
        )
        reward, breakdown = compose_reward(
            llm_signal,
            bug_signals,
            self.reward_weights,
            exploration=context.exploration,
            exec_success=exec_success,
            new_triggers=new_triggers,
        )
        breakdown["exec_success"] = exec_success
        if llm_signal.detail:
            breakdown.update(llm_signal.detail)
        return reward, breakdown


#: How long a click on a link or submit button waits for the navigation it starts to commit.
#: Playwright's click no longer waits for navigations, so without this the settle probe can
#: run on the page being left and see "nothing changed" after a perfectly good form post.
_NAVIGATION_COMMIT_MS = 600


def _do_click(page: "Page", spec: ActionSpec) -> dict | None:
    assert spec.selector is not None
    locator = page.locator(spec.selector).first
    if not spec.params.get("navigational", False):
        locator.click(timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS)
        return None
    try:
        with page.expect_navigation(wait_until="commit", timeout=_NAVIGATION_COMMIT_MS):
            locator.click(timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        return None  # no navigation (or a slow one): the settle probe and the trigger rules decide
    return {"navigated": True}


def _do_type(page: "Page", spec: ActionSpec) -> None:
    assert spec.selector is not None
    page.locator(spec.selector).first.fill(spec.params["value"], timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS)


def _do_select(page: "Page", spec: ActionSpec) -> None:
    assert spec.selector is not None
    page.locator(spec.selector).first.select_option(
        value=spec.params["option"], timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS
    )


def _do_scroll(page: "Page", spec: ActionSpec) -> None:
    amount = spec.params.get("amount", 400)
    delta_y = amount if spec.params.get("direction") == "down" else -amount
    page.mouse.wheel(0, delta_y)


def _do_resize_viewport(page: "Page", spec: ActionSpec) -> None:
    width, height = VIEWPORT_PRESETS[spec.params["preset"]]
    page.set_viewport_size({"width": width, "height": height})


def _do_rapid_click(page: "Page", spec: ActionSpec) -> None:
    assert spec.selector is not None
    locator = page.locator(spec.selector).first
    for _ in range(spec.params.get("n", 5)):
        locator.click(timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS, force=True, no_wait_after=True)


def _do_back(page: "Page", spec: ActionSpec) -> None:
    page.go_back(timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS)


def _do_forward(page: "Page", spec: ActionSpec) -> None:
    page.go_forward(timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS)


def _do_refresh(page: "Page", spec: ActionSpec) -> None:
    page.reload(timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS)


def _do_no_op(page: "Page", spec: ActionSpec) -> None:
    return None


_ACTION_HANDLERS = {
    ActionType.NO_OP: _do_no_op,
    ActionType.CLICK: _do_click,
    ActionType.TYPE: _do_type,
    ActionType.SELECT: _do_select,
    ActionType.SCROLL: _do_scroll,
    ActionType.RESIZE_VIEWPORT: _do_resize_viewport,
    ActionType.RAPID_CLICK: _do_rapid_click,
    ActionType.BROWSER_BACK: _do_back,
    ActionType.BROWSER_FORWARD: _do_forward,
    ActionType.REFRESH: _do_refresh,
}


def _page_text(html: str) -> str:
    """A page's visible text with markup, scripts, digits and spacing removed, so clocks,
    counters and ids do not count as the page changing."""
    import re

    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\d+", "", text)
    return " ".join(text.split())
