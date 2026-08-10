from web_testing_agent.envs.types import ActionSpec, ActionType
from web_testing_agent.reward.exploration import ExplorationTracker


def _spec(action_type=ActionType.CLICK, element_id="btn", **params) -> ActionSpec:
    return ActionSpec(index=0, action_type=action_type, selector="#btn", element_id=element_id, params=params)


def _tracker(window: int = 20) -> ExplorationTracker:
    tracker = ExplorationTracker(repetition_window=window)
    tracker.start_episode()
    return tracker


def test_first_visit_to_a_state_is_novel():
    tracker = _tracker()
    tracker.observe_reset("home")
    assert tracker.observe_step(_spec(), "settings").is_novel_state


def test_revisiting_a_state_is_not_novel():
    tracker = _tracker()
    tracker.observe_reset("home")
    tracker.observe_step(_spec(), "settings")
    assert not tracker.observe_step(_spec(), "settings").is_novel_state


def test_the_reset_state_is_never_a_discovery():
    tracker = _tracker()
    tracker.observe_reset("home")
    assert not tracker.observe_step(_spec(), "home").is_novel_state


def test_repeat_count_grows_with_each_repetition():
    tracker = _tracker()
    tracker.observe_reset("home")
    counts = [tracker.observe_step(_spec(), "home").repeat_count for _ in range(4)]
    assert counts == [0, 1, 2, 3]


def test_different_elements_do_not_count_as_repeats():
    tracker = _tracker()
    tracker.observe_reset("home")
    tracker.observe_step(_spec(element_id="save"), "home")
    assert tracker.observe_step(_spec(element_id="cancel"), "home").repeat_count == 0


def test_typing_different_categories_into_one_field_is_not_a_repeat():
    """boundary_max after valid_typical is genuine exploration, not spam."""
    tracker = _tracker()
    tracker.observe_reset("home")
    tracker.observe_step(_spec(ActionType.TYPE, "email", category="valid_typical"), "home")
    signal = tracker.observe_step(_spec(ActionType.TYPE, "email", category="boundary_max"), "home")
    assert signal.repeat_count == 0


def test_repetition_window_forgets_old_actions():
    tracker = _tracker(window=3)
    tracker.observe_reset("home")
    tracker.observe_step(_spec(element_id="a"), "home")
    for element in ("b", "c", "d"):
        tracker.observe_step(_spec(element_id=element), "home")
    assert tracker.observe_step(_spec(element_id="a"), "home").repeat_count == 0


def test_repeated_no_op_is_penalized_like_any_other_repetition():
    """Regression: exempting NO_OP made passivity the single dominant strategy.

    An earlier version returned repeat_count=0 for NO_OP, reasoning that the step
    penalty already priced it. That made NO_OP the only action in the space immune to
    the repetition penalty, so once novelty was exhausted, doing nothing beat every
    alternative — a trained DQN converged to 100% NO_OP. Doing nothing repeatedly is
    precisely what this penalty should discourage.
    """
    tracker = _tracker()
    tracker.observe_reset("home")
    counts = [
        tracker.observe_step(ActionSpec(index=0, action_type=ActionType.NO_OP), "home").repeat_count
        for _ in range(5)
    ]
    assert counts == [0, 1, 2, 3, 4]


def test_exploring_somewhere_new_always_beats_standing_still():
    """The reward must never make passivity the rational choice.

    Compares the two policies the trained agent actually chose between: sit on NO_OP,
    or move to an unvisited state. If this inverts, the agent will stop testing the app.
    """
    from web_testing_agent.reward.base import RewardSignal
    from web_testing_agent.reward.functional_triggers import (
        FunctionalRewardWeights,
        compose_reward,
        detect_bug_signals,
    )
    import numpy as np
    from web_testing_agent.envs.types import RawObservation

    clean = detect_bug_signals(
        RawObservation(np.zeros((2, 2, 3), np.uint8), "", [], "u", [], []),
        0.1, "a", "a", False,
    )
    weights = FunctionalRewardWeights()

    idle_tracker, move_tracker = _tracker(), _tracker()
    idle_tracker.observe_reset("home")
    move_tracker.observe_reset("home")

    idle_total = move_total = 0.0
    for step in range(20):
        idle = idle_tracker.observe_step(ActionSpec(index=0, action_type=ActionType.NO_OP), "home")
        move = move_tracker.observe_step(_spec(element_id=f"link{step}"), f"page{step}")
        idle_total += compose_reward(RewardSignal(True, 0.0), clean, weights, exploration=idle)[0]
        move_total += compose_reward(RewardSignal(True, 0.0), clean, weights, exploration=move)[0]

    assert move_total > idle_total


def test_episode_coverage_resets_but_total_coverage_accumulates():
    tracker = _tracker()
    tracker.observe_reset("home")
    tracker.observe_step(_spec(), "settings")
    assert tracker.episode_states_seen == 2

    tracker.start_episode()
    tracker.observe_reset("home")
    assert tracker.episode_states_seen == 1
    assert tracker.total_states_seen == 2
    # A state seen in a previous episode is novel again this episode: episodic novelty
    # keeps rewarding "reach somewhere new from the start" for the whole run.
    assert tracker.observe_step(_spec(), "settings").is_novel_state
