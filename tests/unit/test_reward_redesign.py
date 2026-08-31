"""The redesigned exploration reward, and every way it could be farmed.

Two changes, each fixing a measured failure:

* **Count-decayed novelty.** A flat +1.0 per episodic first-visit made reaching a static
  footer page pay exactly what advancing a gated flow stage paid, while being roughly
  nine times easier per step. On every seed of every deep-flow run, `random_masked`
  earned the best reward in the table while reaching the lowest flow depth of any policy.
* **The newly-revealed-action bonus.** A gated flow reveals its next control once its
  input is valid, so the action set grows in response to real progress. This is the only
  exploration term that is genuinely depth-correlated.

This project has had four reward exploits found by the optimizer rather than by review,
each novel after the previous fix, and one of the tests written to prevent the first
exploit *passed while the exploit was live*. So the anti-farming tests here are written
against the cheapest pump available to an optimizer, not the most obvious one to a reader.
"""

from __future__ import annotations

import pytest

from web_testing_agent.envs.types import ActionSpec, ActionType, BugSignals
from web_testing_agent.reward.base import RewardSignal
from web_testing_agent.reward.exploration import ExplorationTracker
from web_testing_agent.reward.functional_triggers import FunctionalRewardWeights, compose_reward

EXPECTED = RewardSignal(is_expected=True, severity=0.0)
WEIGHTS = FunctionalRewardWeights()


def _click(label: str) -> ActionSpec:
    return ActionSpec(index=0, action_type=ActionType.CLICK, selector=f"[id={label}]",
                      element_id=label, params={"navigational": True})


def _select(label: str) -> ActionSpec:
    return ActionSpec(index=1, action_type=ActionType.SELECT, selector=f"[id={label}]",
                      element_id=label, params={"option": "a"})


def _resize(preset: str) -> ActionSpec:
    return ActionSpec(index=2, action_type=ActionType.RESIZE_VIEWPORT,
                      params={"preset": preset})


def _reward(signal) -> float:
    total, _ = compose_reward(EXPECTED, BugSignals(), WEIGHTS, exploration=signal)
    return total


# -- count-decayed novelty ----------------------------------------------------------

def test_the_first_visit_to_a_state_pays_the_full_novelty_bonus():
    tracker = ExplorationTracker()
    tracker.start_episode()
    tracker.observe_reset("home")
    signal = tracker.observe_step(_click("Terms"), "terms")
    assert signal.state_run_visits == 0
    total, breakdown = compose_reward(EXPECTED, BugSignals(), WEIGHTS, exploration=signal)
    assert breakdown["novelty_bonus"] == pytest.approx(WEIGHTS.novelty_bonus)


def test_re_collecting_the_same_page_every_episode_pays_less_each_time():
    """The breadth-versus-depth fix, stated as an executable assertion."""
    tracker = ExplorationTracker()
    payouts = []
    for _ in range(4):
        tracker.start_episode()
        tracker.observe_reset("home")
        signal = tracker.observe_step(_click("Terms"), "terms")
        _total, breakdown = compose_reward(EXPECTED, BugSignals(), WEIGHTS, exploration=signal)
        payouts.append(breakdown["novelty_bonus"])
    assert payouts == sorted(payouts, reverse=True), payouts
    assert payouts[-1] < payouts[0] / 1.5


def test_a_much_visited_state_still_pays_something():
    """Never zero: a persistent bonus that decays to nothing stops shaping behaviour
    entirely once a site is covered, which is why the original was episodic."""
    tracker = ExplorationTracker()
    for _ in range(50):
        tracker.start_episode()
        tracker.observe_reset("home")
        tracker.observe_step(_click("Terms"), "terms")
    tracker.start_episode()
    tracker.observe_reset("home")
    signal = tracker.observe_step(_click("Terms"), "terms")
    _total, breakdown = compose_reward(EXPECTED, BugSignals(), WEIGHTS, exploration=signal)
    assert breakdown["novelty_bonus"] > 0.0


def test_revisiting_within_one_episode_pays_no_novelty_at_all():
    tracker = ExplorationTracker()
    tracker.start_episode()
    tracker.observe_reset("home")
    tracker.observe_step(_click("Terms"), "terms")
    signal = tracker.observe_step(_click("Terms"), "terms")
    _total, breakdown = compose_reward(EXPECTED, BugSignals(), WEIGHTS, exploration=signal)
    assert breakdown["novelty_bonus"] == 0.0


def test_the_flat_bonus_is_still_available_for_reproducing_old_runs():
    flat = FunctionalRewardWeights(count_based_novelty=False)
    tracker = ExplorationTracker()
    for _ in range(5):
        tracker.start_episode()
        tracker.observe_reset("home")
        signal = tracker.observe_step(_click("Terms"), "terms")
    _total, breakdown = compose_reward(EXPECTED, BugSignals(), flat, exploration=signal)
    assert breakdown["novelty_bonus"] == pytest.approx(flat.novelty_bonus)


# -- the newly-revealed-action bonus ------------------------------------------------

def test_opening_a_gate_pays_the_reveal_bonus():
    tracker = ExplorationTracker()
    tracker.start_episode()
    tracker.observe_reset("order-1")
    before = [_click("Home"), _select("product")]
    tracker.observe_action_set(before)
    revealed = tracker.observe_action_set(before + [_click("Continue")])
    signal = tracker.observe_step(_select("product"), "order-1-open", revealed)
    _total, breakdown = compose_reward(EXPECTED, BugSignals(), WEIGHTS, exploration=signal)
    assert breakdown["reveal_bonus"] == pytest.approx(WEIGHTS.reveal_bonus)


def test_advancing_a_flow_stage_out_earns_reaching_a_static_page():
    """The rank inversion this term exists to correct: under the old reward these paid
    exactly the same, and the static page was far easier to reach."""
    gate = ExplorationTracker()
    gate.start_episode()
    gate.observe_reset("order-1")
    specs = [_click("Home"), _select("product")]
    gate.observe_action_set(specs)
    revealed = gate.observe_action_set(specs + [_click("Continue")])
    deep = _reward(gate.observe_step(_select("product"), "order-1-open", revealed))

    flat = ExplorationTracker()
    flat.start_episode()
    flat.observe_reset("index")
    flat.observe_action_set([_click("Terms")])
    shallow = _reward(flat.observe_step(_click("Terms"), "terms", frozenset()))

    assert deep > shallow


# -- anti-farming -------------------------------------------------------------------

def test_a_control_revealed_and_re_hidden_pays_only_once_per_episode():
    """A checkbox that toggles a panel is the cheapest available pump."""
    tracker = ExplorationTracker()
    tracker.start_episode()
    tracker.observe_reset("s")
    closed = [_click("Toggle")]
    opened = closed + [_click("Panel link")]

    payouts = []
    for _ in range(4):
        tracker.observe_action_set(closed)
        revealed = tracker.observe_action_set(opened)
        signal = tracker.observe_step(_click("Toggle"), "s", revealed)
        _total, breakdown = compose_reward(EXPECTED, BugSignals(), WEIGHTS, exploration=signal)
        payouts.append(breakdown["reveal_bonus"])
    assert payouts[0] > 0.0
    assert sum(payouts[1:]) == 0.0, payouts


def test_resizing_the_viewport_never_earns_the_reveal_bonus():
    """RESIZE_VIEWPORT genuinely changes which controls are visible — that is the point
    of the action and why BUG-08 is findable — so it would otherwise be a pure pump."""
    tracker = ExplorationTracker()
    tracker.start_episode()
    tracker.observe_reset("s")
    tracker.observe_action_set([_click("Menu")])
    revealed = tracker.observe_action_set([_click("Menu"), _resize("mobile")])
    signal = tracker.observe_step(_resize("mobile"), "s", revealed)
    _total, breakdown = compose_reward(EXPECTED, BugSignals(), WEIGHTS, exploration=signal)
    assert breakdown["reveal_bonus"] == 0.0


def test_doing_nothing_repeatedly_is_still_never_profitable():
    """The exploit a trained DQN actually found: 100% NO_OP."""
    tracker = ExplorationTracker()
    tracker.start_episode()
    tracker.observe_reset("home")
    noop = ActionSpec(index=0, action_type=ActionType.NO_OP)
    for _ in range(10):
        signal = tracker.observe_step(noop, "home")
        assert _reward(signal) < 0.0


def test_camping_on_one_page_is_never_profitable():
    tracker = ExplorationTracker()
    tracker.start_episode()
    tracker.observe_reset("home")
    tracker.observe_step(_click("Terms"), "terms")
    for _ in range(10):
        assert _reward(tracker.observe_step(_click("Terms"), "terms")) < 0.0


def test_the_reveal_bonus_cannot_be_multiplied_by_re_entering_a_state():
    """Paid per (state, revealed action), so leaving and returning does not re-open it."""
    tracker = ExplorationTracker()
    tracker.start_episode()
    tracker.observe_reset("order-1")
    specs = [_click("Home"), _select("product")]

    def open_gate() -> float:
        tracker.observe_action_set(specs)
        revealed = tracker.observe_action_set(specs + [_click("Continue")])
        signal = tracker.observe_step(_select("product"), "order-1-open", revealed)
        _total, breakdown = compose_reward(EXPECTED, BugSignals(), WEIGHTS, exploration=signal)
        return breakdown["reveal_bonus"]

    assert open_gate() > 0.0
    assert open_gate() == 0.0


# -- the reveal bonus must mean "a gate opened", not "I navigated" ------------------
#
# Measured defect, 2026-08-28. The first version diffed consecutive action sets with no
# notion of *where* they came from. A navigation replaces the whole action set, so every
# link on the destination page counted as newly revealed. Decomposed on masked random
# over 200 steps of the deep-flow fixture: the reveal term paid +87.00 of a +70.62 total
# return, and 81.00 of that 87.00 -- 93% -- came from steps that changed page. The
# identities paid for were ordinary nav links (`Filing`, `Delivery times`, `Returns`).
# It was a breadth bonus wearing a depth bonus's name.

_A = "http://app/order-1.html"
_B = "http://app/catalog.html"


def _tracker_on(url: str) -> ExplorationTracker:
    tracker = ExplorationTracker()
    tracker.start_episode()
    tracker.observe_reset("s")
    return tracker


def test_a_same_page_gate_pays_the_reveal_bonus():
    """The intended case: a SELECT on order-1.html reveals Continue on order-1.html."""
    tracker = _tracker_on(_A)
    before = [_click("Home"), _select("product")]
    tracker.observe_action_set(before, _A)
    revealed = tracker.observe_action_set(before + [_click("Continue")], _A)
    assert revealed == {("CLICK", "Continue")}


def test_navigating_to_another_page_reveals_nothing():
    """The defect. Every control on the destination page is new relative to the origin's
    action set, and none of them is a gate opening."""
    tracker = _tracker_on(_A)
    tracker.observe_action_set([_click("Home"), _select("product")], _A)
    revealed = tracker.observe_action_set(
        [_click("Filing"), _click("Paper"), _click("Pens and markers")], _B
    )
    assert revealed == frozenset()


def test_a_same_page_replacement_without_growth_is_not_a_reveal():
    """One control swapped for another is not progress, however new the identity is."""
    tracker = _tracker_on(_A)
    tracker.observe_action_set([_click("Home"), _click("Edit")], _A)
    revealed = tracker.observe_action_set([_click("Home"), _click("Save")], _A)
    assert revealed == frozenset()


def test_an_already_known_action_is_never_re_revealed():
    tracker = _tracker_on(_A)
    specs = [_click("Home"), _click("Continue")]
    tracker.observe_action_set(specs, _A)
    assert tracker.observe_action_set(specs, _A) == frozenset()


def test_navigating_away_and_back_does_not_pay_for_the_original_page():
    """Re-anchoring on navigation must not turn a return visit into a reveal."""
    tracker = _tracker_on(_A)
    origin = [_click("Home"), _select("product")]
    tracker.observe_action_set(origin, _A)
    tracker.observe_action_set([_click("Filing")], _B)
    assert tracker.observe_action_set(origin, _A) == frozenset()


def test_a_gate_is_still_detected_on_the_page_arrived_at():
    """Re-anchoring must not disable the next genuine gate."""
    tracker = _tracker_on(_A)
    tracker.observe_action_set([_click("Home")], _A)
    arrived = [_click("Back"), _select("quantity")]
    tracker.observe_action_set(arrived, _B)          # navigation: pays nothing
    revealed = tracker.observe_action_set(arrived + [_click("Continue")], _B)
    assert revealed == {("CLICK", "Continue")}


def test_the_reveal_reward_now_requires_a_same_page_gate():
    """End to end through `compose_reward`, both directions."""
    gate = _tracker_on(_A)
    specs = [_click("Home"), _select("product")]
    gate.observe_action_set(specs, _A)
    revealed = gate.observe_action_set(specs + [_click("Continue")], _A)
    _t, gate_terms = compose_reward(
        EXPECTED, BugSignals(), WEIGHTS,
        exploration=gate.observe_step(_select("product"), "order-1-open", revealed))

    nav = _tracker_on(_A)
    nav.observe_action_set(specs, _A)
    nav_revealed = nav.observe_action_set(
        [_click("Filing"), _click("Paper"), _click("Pens")], _B)
    _t, nav_terms = compose_reward(
        EXPECTED, BugSignals(), WEIGHTS,
        exploration=nav.observe_step(_click("Catalog"), "catalog", nav_revealed))

    assert gate_terms["reveal_bonus"] == pytest.approx(WEIGHTS.reveal_bonus)
    assert nav_terms["reveal_bonus"] == 0.0
