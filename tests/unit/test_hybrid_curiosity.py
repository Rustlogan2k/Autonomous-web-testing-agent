"""Curiosity-driven tabular Q-learning (`hybrid/curiosity.py`), without a browser."""

from __future__ import annotations

import math

from web_testing_agent.envs.types import ActionSpec, ActionType
from web_testing_agent.hybrid.curiosity import EPISODIC_BONUS, OPTIMISTIC_Q, CuriosityQ, action_key


def _click(i, dom):
    return ActionSpec(index=i, action_type=ActionType.CLICK, selector=f'[id="{dom}"]',
                      element_id=dom, params={}, description=f"click {dom}")


A, B = _click(1, "a"), _click(2, "b")


def test_reward_is_transition_curiosity_plus_episodic_novelty():
    learner = CuriosityQ()
    learner.begin_episode()
    first = learner.update("s", A, "t", [A, B], done=False)
    assert first == 1.0 + EPISODIC_BONUS  # an unseen transition into an unseen state
    second = learner.update("s", A, "t", [A, B], done=False)
    assert math.isclose(second, 1 / math.sqrt(2))  # seen twice; "t" already this episode
    learner.begin_episode()
    third = learner.update("s", A, "t", [A, B], done=False)
    assert math.isclose(third, 1 / math.sqrt(3) + EPISODIC_BONUS)  # novel again per episode


def test_untried_actions_come_first_and_repeated_ones_lose_value():
    learner = CuriosityQ(seed=0)
    for _ in range(6):
        learner.update("s", A, "s", [A, B], done=False)  # a self-loop: nothing new
    assert learner.q[("s", action_key(A))] < OPTIMISTIC_Q
    picks = {learner.choose("s", [A, B]).index for _ in range(30)}
    assert 2 in picks  # the untried action keeps its optimistic value and is preferred
    assert learner.curiosity("s") > 0  # b is offered and untried
