"""The contextual bandit's defining property: its target is the immediate reward, full stop.

Everything this baseline is for rests on one claim — that it does not bootstrap. That claim
is cheap to make in a docstring and easy to break by accident, so it is tested here from
four independent directions:

* the target function returns the reward tensor itself, not a value derived from it;
* a next state with an enormous value changes nothing;
* `train()` completes with `q_net_target` rigged to raise if it is ever called;
* `train()` completes with `gamma` rigged to raise if it is ever read.

The rest pins the properties that make the comparison against AC-DQN controlled: the same
6973-dim observation, the same mask, the same epsilon schedule, and a checkpoint that
refuses to be confused with a DQN one.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch as th
from gymnasium import spaces

from web_testing_agent.agents.ac_dqn import AC_OBS_DIM, ACStateExtractor
from web_testing_agent.agents.contextual_bandit import ALGORITHM, ContextualBandit
from web_testing_agent.agents.replay import PrioritizedNStepReplayBuffer
from web_testing_agent.envs.types import EPISODE_CONTEXT_DIM, MAX_ACTIONS
from web_testing_agent.perception.fusion import FUSED_DIM

pytest.importorskip("stable_baselines3")

import gymnasium as gym  # noqa: E402
from stable_baselines3.common.vec_env import DummyVecEnv  # noqa: E402


class _StubEnv(gym.Env):
    """Minimal env with the real observation and action spaces, no browser.

    The bandit under test only needs shapes and a reward stream; using the real
    `WebFunctionalEnv` here would make these unit tests need Playwright.
    """

    def __init__(self, n_valid: int = 6, reward: float = 1.0) -> None:
        self.observation_space = spaces.Box(-np.inf, np.inf, (AC_OBS_DIM,), np.float32)
        self.action_space = spaces.Discrete(MAX_ACTIONS)
        self.n_valid = n_valid
        self.reward = reward
        self._steps = 0

    def _obs(self) -> np.ndarray:
        obs = np.zeros(AC_OBS_DIM, dtype=np.float32)
        obs[-MAX_ACTIONS:][: self.n_valid] = 1.0
        return obs

    def reset(self, *, seed=None, options=None):  # noqa: ANN001
        super().reset(seed=seed)
        self._steps = 0
        return self._obs(), {}

    def step(self, action):  # noqa: ANN001
        self._steps += 1
        return self._obs(), self.reward, False, self._steps >= 10, {}


def _bandit(env=None, **kwargs) -> ContextualBandit:
    return ContextualBandit(
        "ACDQNPolicy",
        DummyVecEnv([lambda: env or _StubEnv()]),
        learning_rate=1e-4, buffer_size=200, batch_size=8, gamma=0.99,
        learning_starts=4, train_freq=4, gradient_steps=1, target_update_interval=100,
        exploration_fraction=0.6, exploration_initial_eps=1.0, exploration_final_eps=0.10,
        policy_kwargs={
            "features_extractor_class": ACStateExtractor,
            "features_extractor_kwargs": {"features_dim": FUSED_DIM + EPISODE_CONTEXT_DIM},
            "net_arch": [], "dueling": False,
        },
        seed=0, verbose=0, device="cpu", **kwargs,
    )


def _bandit_with_logger(**kwargs) -> ContextualBandit:
    """`train()` records metrics, and SB3 only builds `_logger` inside `learn()`."""
    from stable_baselines3.common.utils import configure_logger

    model = _bandit(**kwargs)
    model.set_logger(configure_logger(0, None, "", True))
    return model


class _Batch:
    """The fields `train()` reads, plus a next_observations nothing may consult."""

    def __init__(self, rewards, next_observations=None, batch=8):
        self.rewards = rewards
        self.observations = th.zeros(batch, AC_OBS_DIM)
        self.observations[:, -MAX_ACTIONS:][:, :6] = 1.0
        self.actions = th.zeros(batch, 1, dtype=th.long)
        self.dones = th.zeros(batch, 1)
        self.next_observations = (
            next_observations if next_observations is not None
            else th.zeros(batch, AC_OBS_DIM))
        self.weights = th.ones(batch, 1)
        self.indices = np.arange(batch)


# -- the target ------------------------------------------------------------------------

def test_the_regression_target_is_exactly_the_reward_tensor():
    rewards = th.tensor([[0.0], [1.5], [-0.05], [7.25]])
    target = ContextualBandit.regression_target(_Batch(rewards, batch=4))
    assert th.equal(target, rewards)
    assert target is rewards, "the target must be the reward itself, not a transform of it"


@pytest.mark.parametrize("reward", [0.0, 1.0, -3.0, 2.45, 1e4])
def test_target_equals_reward_for_any_magnitude(reward: float):
    rewards = th.full((5, 1), reward)
    assert th.equal(ContextualBandit.regression_target(_Batch(rewards, batch=5)), rewards)


def test_an_enormously_valuable_next_state_does_not_move_the_target():
    """The direct test of 'no bootstrapping': make the future huge and check nothing moves."""
    rewards = th.tensor([[1.0], [2.0], [3.0]])
    ordinary = ContextualBandit.regression_target(_Batch(rewards, batch=3))
    huge = th.full((3, AC_OBS_DIM), 1e9)
    bootstrapped = ContextualBandit.regression_target(
        _Batch(rewards, next_observations=huge, batch=3))
    assert th.equal(ordinary, bootstrapped)
    assert th.equal(bootstrapped, rewards)


def test_training_never_touches_the_target_network():
    """`q_net_target` is rigged to raise; a Bellman target would have to call it."""
    model = _bandit_with_logger()
    model.replay_buffer = _StubBuffer(reward=1.0)

    def explode(*_a, **_k):
        raise AssertionError("q_net_target was called - this arm must not bootstrap")

    model.policy.q_net_target.forward = explode
    model.train(gradient_steps=2, batch_size=8)      # must not raise


def test_training_never_reads_gamma():
    """`gamma` is rigged to raise on access; the target must not discount anything."""
    model = _bandit_with_logger()
    model.replay_buffer = _StubBuffer(reward=1.0)

    class _Boom(type(model)):
        @property
        def gamma(self):
            raise AssertionError("gamma was read - the target must be undiscounted")

    model.__class__ = _Boom
    model.train(gradient_steps=2, batch_size=8)      # must not raise


class _StubBuffer(PrioritizedNStepReplayBuffer):
    """A buffer that yields a fixed batch, so `train()` can be driven deterministically."""

    def __init__(self, reward: float) -> None:
        super().__init__(
            200, spaces.Box(-np.inf, np.inf, (AC_OBS_DIM,), np.float32),
            spaces.Discrete(MAX_ACTIONS), n_step=1, gamma=0.99)
        self._reward = reward

    def sample(self, batch_size, env=None):  # noqa: ANN001
        from web_testing_agent.agents.replay import PrioritizedSamples

        batch = _Batch(th.full((batch_size, 1), self._reward), batch=batch_size)
        return PrioritizedSamples(
            observations=batch.observations, actions=batch.actions,
            next_observations=batch.next_observations, dones=batch.dones,
            rewards=batch.rewards, indices=batch.indices, weights=batch.weights)


def test_training_drives_the_prediction_towards_the_reward():
    """A learning-rule smoke test: regressing on a constant reward must reduce the error."""
    model = _bandit_with_logger()
    model.replay_buffer = _StubBuffer(reward=5.0)
    obs = np.zeros(AC_OBS_DIM, dtype=np.float32)
    obs[-MAX_ACTIONS:][:6] = 1.0

    before = model.predicted_immediate_reward(obs)[0, 0]
    for _ in range(60):
        model.train(gradient_steps=1, batch_size=8)
    after = model.predicted_immediate_reward(obs)[0, 0]
    assert abs(after - 5.0) < abs(before - 5.0), "prediction moved away from the reward"


# -- n-step is neutralised -------------------------------------------------------------

def test_n_step_is_forced_to_one_whatever_the_cli_passes():
    for requested in (1, 3, 5):
        model = _bandit(n_step=requested)
        assert model.n_step == 1
        assert model.replay_buffer.n_step == 1


def test_the_buffer_stores_the_raw_one_step_reward():
    """With n_step=1 the stored reward must be the env's reward, undiscounted."""
    buffer = PrioritizedNStepReplayBuffer(
        50, spaces.Box(-np.inf, np.inf, (AC_OBS_DIM,), np.float32),
        spaces.Discrete(MAX_ACTIONS), n_step=1, gamma=0.5)
    obs = np.zeros((1, AC_OBS_DIM), np.float32)
    for reward in (1.0, 2.0, 4.0):
        buffer.add(obs, obs, np.array([[0]]), np.array([reward]),
                   np.array([False]), [{}])
    stored = buffer.rewards[:3, 0]
    assert list(stored) == [1.0, 2.0, 4.0], "gamma leaked into a stored one-step reward"


# -- masking and exploration -----------------------------------------------------------

def test_an_invalid_action_is_never_selected_however_high_it_scores():
    model = _bandit()
    obs = np.zeros(AC_OBS_DIM, dtype=np.float32)
    obs[-MAX_ACTIONS:][:4] = 1.0                       # only slots 0-3 are valid

    scores = model.predicted_immediate_reward(obs)[0]
    assert np.allclose(scores[4:], -1e8), "invalid slots must be sentinelled"

    model.exploration_rate = 0.0
    for _ in range(25):
        action, _ = model.predict(obs, deterministic=False)
        assert int(action) < 4


def test_epsilon_zero_is_deterministic_greedy():
    model = _bandit()
    obs = np.zeros(AC_OBS_DIM, dtype=np.float32)
    obs[-MAX_ACTIONS:][:6] = 1.0
    model.exploration_rate = 0.0
    greedy = int(model.predicted_immediate_reward(obs)[0][:6].argmax())
    for _ in range(15):
        action, _ = model.predict(obs, deterministic=False)
        assert int(action) == greedy


def test_epsilon_one_explores_across_valid_slots_only():
    model = _bandit()
    obs = np.zeros(AC_OBS_DIM, dtype=np.float32)
    obs[-MAX_ACTIONS:][:5] = 1.0
    model.exploration_rate = 1.0
    np.random.seed(0)
    chosen = {int(model.predict(obs, deterministic=False)[0]) for _ in range(80)}
    assert chosen <= {0, 1, 2, 3, 4}
    assert len(chosen) > 1, "epsilon=1 must not collapse to one action"


def test_the_epsilon_schedule_matches_the_ac_dqn_arm():
    model = _bandit()
    assert model.exploration_initial_eps == 1.0
    assert model.exploration_final_eps == 0.10
    assert model.exploration_fraction == 0.6


# -- shape and checkpointing -----------------------------------------------------------

def test_the_model_consumes_the_current_observation_width():
    model = _bandit()
    assert model.observation_space.shape == (AC_OBS_DIM,) == (6973,)
    assert int(model.action_space.n) == MAX_ACTIONS
    obs = np.zeros(AC_OBS_DIM, dtype=np.float32)
    obs[-MAX_ACTIONS:][:6] = 1.0          # a real observation always has valid slots
    scores = model.predicted_immediate_reward(obs)
    assert scores.shape == (1, MAX_ACTIONS)


def test_the_head_has_no_value_stream():
    """`dueling=False` is what makes the output a plain per-action scalar."""
    model = _bandit()
    assert model.policy.dueling is False
    assert not hasattr(model.q_net, "value")


def test_checkpoint_round_trips_to_identical_predictions(tmp_path):
    model = _bandit()
    obs = np.zeros(AC_OBS_DIM, dtype=np.float32)
    obs[-MAX_ACTIONS:][:7] = 1.0
    before = model.predicted_immediate_reward(obs)

    path = tmp_path / "bandit.zip"
    model.save(path)
    reloaded = ContextualBandit.load(path, device="cpu")
    assert reloaded.algorithm == ALGORITHM
    assert np.array_equal(reloaded.predicted_immediate_reward(obs), before)


def test_loading_refuses_a_checkpoint_that_is_not_a_bandit(tmp_path):
    """A DQN and a bandit checkpoint have identical tensor shapes; only the tag separates them."""
    from web_testing_agent.agents.ac_dqn import ActionConditionedDQN

    dqn = ActionConditionedDQN(
        "ACDQNPolicy", DummyVecEnv([_StubEnv]),
        learning_rate=1e-4, buffer_size=50, batch_size=8, learning_starts=4,
        policy_kwargs={
            "features_extractor_class": ACStateExtractor,
            "features_extractor_kwargs": {"features_dim": FUSED_DIM + EPISODE_CONTEXT_DIM},
            "net_arch": [], "dueling": True,
        }, seed=0, verbose=0, device="cpu")
    path = tmp_path / "dqn.zip"
    dqn.save(path)
    with pytest.raises(ValueError, match="not saved by a ContextualBandit"):
        ContextualBandit.load(path, device="cpu")


# -- end to end ------------------------------------------------------------------------

def test_learn_runs_end_to_end_on_the_stub_env():
    model = _bandit()
    model.learn(total_timesteps=32, progress_bar=False)
    assert model.num_timesteps >= 32
    assert model.replay_buffer.n_step == 1
    assert model.algorithm == ALGORITHM
