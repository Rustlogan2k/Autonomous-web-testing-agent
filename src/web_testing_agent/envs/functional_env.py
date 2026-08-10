"""WebFunctionalEnv - Agent B's Gymnasium environment for functional bug detection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..reward.base import FunctionalRewardModel, NullRewardModel
from ..reward.functional_triggers import FunctionalRewardWeights, compose_reward, detect_bug_signals
from ..utils.logging import get_logger
from .action_registry import build_action_specs, scan_page_elements
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
    ) -> None:
        super().__init__(
            base_url=base_url,
            max_steps=max_steps,
            headless=headless,
            render_mode=render_mode,
            repetition_window=repetition_window,
        )
        self.reward_model = reward_model or NullRewardModel()
        self.reward_weights = reward_weights or FunctionalRewardWeights()

    def _start_reward_model_episode(self) -> None:
        self.reward_model.start_episode()

    def _build_action_specs(self) -> list[ActionSpec]:
        page = self._browser.page
        assert page is not None
        elements = scan_page_elements(page)
        return build_action_specs(elements)

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
            handler(page, spec)
            return {"success": True, "error": None}
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
        # hrefs and target="_blank" links, which never change the current URL either.
        spec = context.spec
        was_navigation_action = (
            spec.action_type == ActionType.CLICK and spec.params.get("navigational", False)
        )
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


def _do_click(page: "Page", spec: ActionSpec) -> None:
    assert spec.selector is not None
    page.locator(spec.selector).first.click(timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS)


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
