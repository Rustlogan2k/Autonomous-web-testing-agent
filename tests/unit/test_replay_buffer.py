"""Prioritized n-step replay: the arithmetic, and the two ways it silently goes wrong.

The spec has asked for PER since the start (`priority = |TD error| + 1e-6`) and the code
never had it — SB3 ships only a uniform buffer, so every DQN number this project has
published trained on uniform samples. These tests pin the replacement, with particular
attention to the failure modes that produce a *plausible* buffer rather than an error:
duplicated transitions at episode boundaries, and bootstrapping through a time limit as
though it were a terminal state.
"""

from __future__ import annotations

import numpy as np
import pytest
from gymnasium import spaces

from web_testing_agent.agents.replay import PrioritizedNStepReplayBuffer, SumTree

OBS = spaces.Box(-np.inf, np.inf, (4,), np.float32)
ACT = spaces.Discrete(3)
GAMMA = 0.9


def _buffer(n_step: int = 3, size: int = 128, **kwargs) -> PrioritizedNStepReplayBuffer:
    return PrioritizedNStepReplayBuffer(
        size, OBS, ACT, device="cpu", n_step=n_step, gamma=GAMMA, **kwargs
    )


def _add(buf, step: int, reward: float, done: bool = False, truncated: bool = False):
    obs = np.full((1, 4), step, dtype=np.float32)
    nxt = np.full((1, 4), step + 1, dtype=np.float32)
    infos = [{"TimeLimit.truncated": truncated}]
    buf.add(obs, nxt, np.array([[step % 3]]), np.array([reward]), np.array([done]), infos)


# -- sum tree ----------------------------------------------------------------------

def test_sum_tree_totals_and_samples_proportionally():
    tree = SumTree(4)
    for i, p in enumerate([1.0, 3.0, 0.0, 4.0]):
        tree.set(i, p)
    assert tree.total == pytest.approx(8.0)
    # Interval boundaries: [0,1)->0, [1,4)->1, index 2 has zero width, [4,8)->3
    assert tree.sample(0.5) == 0
    assert tree.sample(2.0) == 1
    assert tree.sample(6.0) == 3
    assert tree.max_leaf() == pytest.approx(4.0)


def test_sum_tree_updates_propagate():
    tree = SumTree(4)
    tree.set(0, 5.0)
    assert tree.total == pytest.approx(5.0)
    tree.set(0, 1.0)
    assert tree.total == pytest.approx(1.0)


# -- n-step aggregation ------------------------------------------------------------

def test_one_stored_transition_per_env_step():
    """The bug this catches: committing the full window *and* re-committing it in the
    terminal flush stored 420 transitions for 400 steps, silently duplicating the end of
    every episode — the region where a completed flow would appear."""
    buf = _buffer(n_step=3)
    for step in range(10):
        _add(buf, step, reward=1.0, done=(step == 9))
    assert buf.size() == 10


def test_n_step_return_is_discounted_correctly():
    buf = _buffer(n_step=3)
    for step, reward in enumerate([1.0, 2.0, 4.0, 0.0]):
        _add(buf, step, reward=reward)
    # First stored transition aggregates rewards 1, 2, 4 at gamma^0, gamma^1, gamma^2.
    expected = 1.0 + GAMMA * 2.0 + GAMMA**2 * 4.0
    assert buf.rewards[0][0] == pytest.approx(expected)


def test_the_aggregated_transition_spans_n_steps():
    buf = _buffer(n_step=3)
    for step in range(4):
        _add(buf, step, reward=0.0)
    # obs of step 0, next_obs of step 2 (whose next is 3).
    assert buf.observations[0][0][0] == pytest.approx(0.0)
    assert buf.next_observations[0][0][0] == pytest.approx(3.0)


def test_a_terminal_inside_the_window_truncates_the_return():
    """Nothing after an episode ends belongs in that episode's return."""
    buf = _buffer(n_step=3)
    _add(buf, 0, reward=1.0)
    _add(buf, 1, reward=2.0, done=True)
    _add(buf, 2, reward=99.0)
    assert buf.rewards[0][0] == pytest.approx(1.0 + GAMMA * 2.0)


def test_the_episode_tail_is_kept_as_shorter_returns():
    """Dropping the last n-1 transitions would discard ~7% of experience, all of it
    from the end of episodes."""
    buf = _buffer(n_step=3)
    for step in range(5):
        _add(buf, step, reward=1.0, done=(step == 4))
    assert buf.size() == 5
    # The final stored transition is a 1-step return ending in the terminal.
    assert buf.rewards[4][0] == pytest.approx(1.0)
    assert buf.dones[4][0] == pytest.approx(1.0)


def test_n_step_one_behaves_like_an_ordinary_buffer():
    buf = _buffer(n_step=1)
    for step in range(5):
        _add(buf, step, reward=float(step))
    assert buf.size() == 5
    assert buf.rewards[3][0] == pytest.approx(3.0)


# -- timeout handling --------------------------------------------------------------

def test_a_time_limit_does_not_read_as_a_terminal_state():
    """`max_steps` truncation is not the end of the world, and bootstrapping must
    continue through it. Getting this wrong teaches the agent that every 40th step
    ends existence."""
    buf = _buffer(n_step=1)
    _add(buf, 0, reward=1.0, done=True, truncated=True)
    samples = buf.sample(1)
    assert float(samples.dones[0]) == pytest.approx(0.0)


def test_a_real_terminal_is_still_terminal():
    buf = _buffer(n_step=1)
    _add(buf, 0, reward=1.0, done=True, truncated=False)
    samples = buf.sample(1)
    assert float(samples.dones[0]) == pytest.approx(1.0)


# -- prioritization ----------------------------------------------------------------

def test_new_transitions_enter_at_maximum_priority():
    """Every transition must be sampled at least once before prioritization can decide
    it is uninteresting."""
    buf = _buffer(n_step=1)
    _add(buf, 0, reward=0.0)
    buf.update_priorities(np.array([0]), np.array([5.0]))
    _add(buf, 1, reward=0.0)
    assert buf._tree.get(1) == pytest.approx(buf._tree.get(0), rel=0.2)


def test_high_td_error_transitions_are_sampled_far_more_often():
    buf = _buffer(n_step=1, size=64)
    for step in range(16):
        _add(buf, step, reward=0.0)
    errors = np.full(16, 0.01)
    errors[7] = 100.0
    buf.update_priorities(np.arange(16), errors)
    drawn = np.concatenate([buf.sample(16).indices for _ in range(20)])
    share = float((drawn == 7).mean())
    assert share > 0.5, f"high-priority transition drawn {share:.1%} of the time"


def test_importance_weights_are_normalized_and_correct_the_bias():
    buf = _buffer(n_step=1, size=64)
    for step in range(16):
        _add(buf, step, reward=0.0)
    errors = np.full(16, 0.01)
    errors[3] = 50.0
    buf.update_priorities(np.arange(16), errors)
    samples = buf.sample(16)
    weights = samples.weights.numpy().reshape(-1)
    assert weights.max() == pytest.approx(1.0)
    assert (weights > 0).all()
    # The over-sampled transition must be down-weighted relative to a rare one.
    if (samples.indices == 3).any() and (samples.indices != 3).any():
        assert weights[samples.indices == 3].mean() < weights[samples.indices != 3].mean()


def test_beta_anneals_from_beta0_to_one():
    buf = _buffer(n_step=1, beta0=0.4)
    buf.set_beta(1.0)
    assert buf.beta == pytest.approx(0.4)
    buf.set_beta(0.0)
    assert buf.beta == pytest.approx(1.0)


def test_priority_is_absolute_td_error_plus_epsilon():
    """The spec's formula, so a zero-error transition is never unreachable."""
    buf = _buffer(n_step=1)
    _add(buf, 0, reward=0.0)
    buf.update_priorities(np.array([0]), np.array([0.0]))
    assert buf._tree.get(0) > 0.0


def test_sampling_an_empty_buffer_fails_loudly():
    with pytest.raises(RuntimeError):
        _buffer().sample(4)


def test_multiple_environments_are_refused_rather_than_silently_interleaved():
    """Two envs' transitions folded into one n-step return would be a plausible-looking
    buffer full of returns that never happened."""
    with pytest.raises(ValueError, match="one environment"):
        PrioritizedNStepReplayBuffer(64, OBS, ACT, device="cpu", n_envs=2)
