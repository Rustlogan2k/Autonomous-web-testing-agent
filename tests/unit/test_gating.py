import pytest

from web_testing_agent.envs.types import ActionType
from web_testing_agent.reward.gating import GateStats, should_judge


def gate(action_type, **overrides):
    kwargs = {
        "exec_success": True,
        "blocked": False,
        "state_changed": False,
        "triggered": False,
        "settled": True,
    }
    kwargs.update(overrides)
    return should_judge(action_type, **kwargs)


# --- the case the gate exists to protect -----------------------------------------


@pytest.mark.parametrize("action_type", [ActionType.CLICK, ActionType.RAPID_CLICK, ActionType.SELECT])
def test_a_control_that_did_nothing_is_always_judged(action_type):
    """The dead-control case. Filtering on "the state changed" would discard exactly
    the bug class the judge is most needed for, and the one most commonly missed."""
    assert gate(action_type, state_changed=False).judge


def test_the_filter_is_not_state_changed():
    """Restated as an executable claim, because it is the design's load-bearing rule."""
    unchanged_click = gate(ActionType.CLICK, state_changed=False)
    unchanged_scroll = gate(ActionType.SCROLL, state_changed=False)
    assert unchanged_click.judge and not unchanged_scroll.judge


# --- failure vs. no effect --------------------------------------------------------


def test_a_failed_action_that_changed_the_page_is_still_judged():
    """Regression, found by replaying the gate over a scored corpus.

    A RAPID_CLICK navigates on its first click, so clicks 2-5 time out against a
    detached element and Playwright records the burst as `success: False`. On the toy
    corpus that exact step had already reached /confirm.html and carried BUG-01's
    evidence in the URL (`?username=…&age=…`, no `email`). An earlier version skipped it
    as "did not execute" and would have discarded a 95%-confidence semantic finding.
    """
    decision = gate(ActionType.RAPID_CLICK, exec_success=False, state_changed=True)
    assert decision.judge
    assert decision.reason == "the page changed"


def test_a_failed_action_that_changed_nothing_is_skipped():
    """A click that never landed is an explorer problem, and 'nothing changed' is not
    dead-control evidence when nothing was clicked."""
    assert not gate(ActionType.CLICK, exec_success=False, state_changed=False).judge


def test_a_blocked_navigation_that_changed_nothing_is_skipped():
    assert not gate(ActionType.CLICK, blocked=True, state_changed=False).judge


def test_a_blocked_navigation_that_somehow_changed_the_page_is_judged():
    """The harness restores the previous page, so a change here means the restore did
    not fully undo the navigation — worth a look rather than a silent skip."""
    assert gate(ActionType.CLICK, blocked=True, state_changed=True).judge


# --- deterministic triggers -------------------------------------------------------


def test_a_deterministic_trigger_is_never_gated_away():
    for action_type in ActionType:
        assert gate(action_type, triggered=True).judge


def test_an_unsettled_page_is_always_judged():
    """A page that never stops mutating is the hang signal; it must reach the judge
    even after an action that promises nothing."""
    assert gate(ActionType.SCROLL, settled=False).judge


def test_a_trigger_outranks_a_blocked_action():
    assert gate(ActionType.CLICK, triggered=True, blocked=True).judge


# --- actions that promise nothing -------------------------------------------------


@pytest.mark.parametrize(
    "action_type",
    [ActionType.TYPE, ActionType.SCROLL, ActionType.RESIZE_VIEWPORT,
     ActionType.REFRESH, ActionType.BROWSER_BACK, ActionType.BROWSER_FORWARD],
)
def test_actions_that_promise_no_effect_are_skipped_when_nothing_changed(action_type):
    assert not gate(action_type, state_changed=False).judge


@pytest.mark.parametrize(
    "action_type",
    [ActionType.TYPE, ActionType.SCROLL, ActionType.RESIZE_VIEWPORT,
     ActionType.REFRESH, ActionType.BROWSER_BACK, ActionType.BROWSER_FORWARD],
)
def test_the_same_actions_are_judged_when_they_did_change_something(action_type):
    """A refresh that loses a saved setting is state_persistence; a resize that hides a
    control is ui_regression. Both are only visible as changes."""
    assert gate(action_type, state_changed=True).judge


def test_an_idle_no_op_is_skipped():
    assert not gate(ActionType.NO_OP, state_changed=False).judge


def test_a_no_op_that_changed_the_page_is_judged():
    assert gate(ActionType.NO_OP, state_changed=True).judge


# --- bookkeeping ------------------------------------------------------------------


def test_stats_track_the_saving_and_the_reasons():
    stats = GateStats()
    stats.record(gate(ActionType.CLICK))
    stats.record(gate(ActionType.SCROLL))
    stats.record(gate(ActionType.SCROLL))
    assert stats.total == 3
    assert stats.judged == 1
    assert stats.skipped == 2
    assert stats.judged_fraction == pytest.approx(1 / 3)
    assert sum(stats.to_dict()["reasons"].values()) == 3


def test_every_decision_carries_a_reason():
    for action_type in ActionType:
        for changed in (True, False):
            assert gate(action_type, state_changed=changed).reason
