"""Evaluation-time behaviour of `TrainedPolicy`.

Both properties here were absent in the version that produced the 2026-08-10 deep-flow
report, and both failed silently — the numbers looked precise rather than wrong.
"""

from __future__ import annotations

import numpy as np

from web_testing_agent.envs.types import MAX_ACTIONS, ActionSpec, ActionType
from web_testing_agent.evaluation.rollout import TrainedPolicy


class _StubModel:
    """Always chooses slot 7; records the observations it was asked about."""

    def __init__(self, masks: bool = False, n: int = MAX_ACTIONS) -> None:
        self.masks_actions = masks
        self.action_space = type("Space", (), {"n": n})()
        self.seen: list[np.ndarray] = []

    def predict(self, features, deterministic=True):  # noqa: ANN001, FBT002
        self.seen.append(np.asarray(features))
        return np.array([7]), None


def _observation() -> dict:
    return {"screenshot": np.zeros((720, 1280, 3), dtype=np.uint8)}


def _info(num_valid: int = 5) -> dict:
    specs = [
        ActionSpec(index=i, action_type=ActionType.NO_OP, description=f"slot {i}")
        for i in range(num_valid)
    ]
    return {"page": {"html": "<html></html>", "network": ""},
            "episode_context": None,
            "num_valid_actions": num_valid,
            "action_specs": specs}


def test_greedy_evaluation_returns_one_trajectory_and_epsilon_breaks_it():
    """The defect that made five evaluation episodes report a single sample.

    A greedy policy against a fixed page is a pure function of that page, so repeated
    calls cannot differ. That is fine as a property of the policy and fatal as a
    measurement, because the random baseline it is tabulated beside *does* vary.
    """
    greedy = TrainedPolicy(_StubModel(), _encoders(), epsilon=0.0)
    assert {greedy.act(_observation(), _info()) for _ in range(40)} == {7}

    explorer = TrainedPolicy(_StubModel(), _encoders(), epsilon=0.5, seed=0)
    assert len({explorer.act(_observation(), _info()) for _ in range(40)}) > 1


def test_residual_exploration_respects_the_agents_own_action_space():
    """A masked agent must not be evaluated with unmasked exploration, or vice versa.

    Sampling all 100 slots for a masked agent would credit it with invalid actions it
    would never take; sampling valid-only for an unmasked one would hand it the very
    ability the masked-vs-unmasked comparison exists to isolate.
    """
    masked = TrainedPolicy(_StubModel(masks=True), _encoders(), epsilon=1.0, seed=0)
    chosen = {masked.act(_observation(), _info(num_valid=5)) for _ in range(200)}
    assert chosen <= set(range(5)), f"masked agent sampled an invalid slot: {sorted(chosen)}"

    unmasked = TrainedPolicy(_StubModel(masks=False), _encoders(), epsilon=1.0, seed=0)
    reached = {unmasked.act(_observation(), _info(num_valid=5)) for _ in range(400)}
    assert max(reached) >= 5, "unmasked agent was silently restricted to valid slots"


def test_the_action_mask_reaches_the_policy_at_evaluation_time():
    """Omitting `num_valid_actions` turns masking off at evaluation only.

    The mask travels in the observation's trailing `MAX_ACTIONS` dims, and
    `action_mask(None)` yields all-valid. An evaluation path that did not pass the count
    would therefore hand a masked network a mask permitting everything — the agent would
    look unmasked in evaluation while every training log said otherwise.
    """
    model = _StubModel(masks=True)
    policy = TrainedPolicy(model, _encoders(), epsilon=0.0)
    policy.act(_observation(), _info(num_valid=5))

    mask = model.seen[-1][0, -MAX_ACTIONS:]
    assert mask[:5].all() and not mask[5:].any()


def _encoders() -> dict:
    from web_testing_agent.perception.vec_wrapper import default_encoders

    return default_encoders()
