"""The dueling decomposition, and the two ways it silently goes wrong.

**Why it exists.** Measured over 1,500 training steps, the within-state Q spread fell
from 24% of |Q| to 5% while mean |Q| grew 23x (0.08 -> 1.88). The action-level signal
was not destroyed, it was outgrown: the network converged on `Q ~= V(s)` with a
vestigial action term, and mean Q varied from -1.36 to -4.70 *between* states while
varying ~0.1 *within* one. On the trained checkpoints that showed up as the
flow-advancing TYPE action ranking 19th-24th of 24 on two seeds out of three.

`Q = V(s) + A(s,a) - mean_a' A(s,a')` forces the advantage stream to carry only
within-state differences, so a state's overall value can inflate without swamping the
comparison between the actions available in it.

The two silent failure modes pinned here: taking the mean over *all* slots rather than
the valid ones (which leaks how many controls a page has into the baseline), and letting
the value stream see the action (which makes the decomposition vacuous).
"""

from __future__ import annotations

import numpy as np
import pytest
import torch as th
from gymnasium import spaces

from web_testing_agent.agents.ac_dqn import (
    AC_OBS_DIM,
    ACStateExtractor,
    ActionConditionedQNetwork,
    split_observation,
)
from web_testing_agent.envs.types import EPISODE_CONTEXT_DIM, MAX_ACTIONS
from web_testing_agent.perception.fusion import FUSED_DIM

SPACE = spaces.Box(-np.inf, np.inf, (AC_OBS_DIM,), np.float32)
ACTION_SPACE = spaces.Discrete(MAX_ACTIONS)
_MASKED_Q = -1e8


def _net(dueling: bool = True) -> ActionConditionedQNetwork:
    th.manual_seed(0)
    extractor = ACStateExtractor(SPACE, features_dim=FUSED_DIM + EPISODE_CONTEXT_DIM)
    return ActionConditionedQNetwork(
        observation_space=SPACE, action_space=ACTION_SPACE,
        features_extractor=extractor, features_dim=extractor.features_dim,
        net_arch=[], dueling=dueling,
    )


def _obs(batch: int = 4, n_valid: int = 7) -> th.Tensor:
    th.manual_seed(1)
    obs = th.randn(batch, AC_OBS_DIM)
    obs[:, -MAX_ACTIONS:] = 0.0
    obs[:, -MAX_ACTIONS:][:, :n_valid] = 1.0
    return obs


def _valid_q(q: th.Tensor, n_valid: int) -> th.Tensor:
    return q[:, :n_valid]


# -- shape and masking are unchanged -------------------------------------------------

@pytest.mark.parametrize("dueling", [True, False])
def test_output_shape_and_masking_are_unchanged(dueling: bool):
    q = _net(dueling)(_obs())
    assert q.shape == (4, MAX_ACTIONS)
    assert (q[:, 7:] == _MASKED_Q).all()
    assert (q[:, :7] > _MASKED_Q).all()


@pytest.mark.parametrize("dueling", [True, False])
def test_an_all_zero_mask_does_not_make_every_action_the_sentinel(dueling: bool):
    obs = _obs()
    obs[:, -MAX_ACTIONS:] = 0.0
    q = _net(dueling)(obs)
    assert (q > _MASKED_Q).all()


# -- the decomposition itself --------------------------------------------------------

def test_the_advantage_is_centred_over_valid_actions_only():
    """The defining property: with the mean subtracted, the mean Q over the valid
    actions equals V(s). If the mean were taken over all 100 slots instead, the
    baseline would scale with how many controls a page happens to have — leaking page
    identity back in through the value stream."""
    net = _net(dueling=True)
    obs = _obs(batch=4, n_valid=7)
    q = net(obs)
    state = net.extract_features(obs, net.features_extractor)
    value = net.value(state).squeeze(-1)
    assert th.allclose(_valid_q(q, 7).mean(dim=1), value, atol=1e-5)


def test_the_number_of_valid_actions_does_not_shift_the_baseline():
    net = _net(dueling=True)
    state_part = th.randn(1, AC_OBS_DIM)
    means = []
    for n_valid in (3, 9, 20):
        obs = state_part.clone()
        obs[:, -MAX_ACTIONS:] = 0.0
        obs[:, -MAX_ACTIONS:][:, :n_valid] = 1.0
        q = net(obs)
        means.append(float(q[0, :n_valid].mean()))
    # Every one of them equals V(s) for the same state, so they agree with each other.
    assert max(means) - min(means) < 1e-4, means


def test_the_value_stream_cannot_see_the_action():
    """A value stream fed the action features would make the decomposition vacuous."""
    net = _net(dueling=True)
    obs = _obs(batch=1)
    state = net.extract_features(obs, net.features_extractor)
    baseline = float(net.value(state))

    changed = obs.clone()
    _p, _c, actions, _m = split_observation(changed)
    start = AC_OBS_DIM - MAX_ACTIONS - actions.shape[-1] * MAX_ACTIONS
    changed[:, start:start + 200] += 5.0     # perturb only the action block
    state2 = net.extract_features(changed, net.features_extractor)
    assert float(net.value(state2)) == pytest.approx(baseline, abs=1e-6)


def test_dueling_still_lets_actions_differ():
    """Centring removes the mean, not the differences."""
    q = _net(dueling=True)(_obs(batch=4, n_valid=7))
    spread = (_valid_q(q, 7).max(dim=1).values - _valid_q(q, 7).min(dim=1).values)
    assert (spread > 1e-6).all()


def test_a_state_value_shift_moves_every_action_together():
    """The property that matters for the measured failure: the state's overall value can
    inflate without changing which action is preferred."""
    net = _net(dueling=True)
    obs = _obs(batch=1, n_valid=7)
    q = _valid_q(net(obs), 7)
    order = th.argsort(q, dim=1)

    with th.no_grad():                      # raise V(s) by a constant
        net.value[-1].bias += 10.0
    q2 = _valid_q(net(obs), 7)
    assert th.equal(th.argsort(q2, dim=1), order)
    assert float(q2.mean() - q.mean()) == pytest.approx(10.0, abs=1e-4)
    assert float((q2 - q).std()) == pytest.approx(0.0, abs=1e-5)


def test_the_undueled_head_is_still_available_for_reproducing_earlier_runs():
    plain, duel = _net(False), _net(True)
    assert not hasattr(plain, "value")
    assert hasattr(duel, "value")
    assert plain(_obs()).shape == duel(_obs()).shape


def test_dueling_adds_a_value_stream_to_the_optimizer():
    duel = _net(True)
    names = {n for n, _ in duel.named_parameters()}
    assert any(n.startswith("value.") for n in names)
    assert any(n.startswith("scorer.") for n in names)
    assert not any(n.startswith("q_net.") for n in names)  # Identity has no parameters
