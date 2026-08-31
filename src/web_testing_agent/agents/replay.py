"""Prioritized, n-step replay — the two pieces the spec asked for and the code never had.

`PROJECT_CONTEXT` §3.3 specifies "DQN + Prioritized Experience Replay" and
"priority = |TD error| + 1e-6". Neither existed: Stable-Baselines3 ships only a uniform
`ReplayBuffer`, so every run reported in this project trained on uniform samples. This
module supplies both, and they are wanted here for reasons specific to this task rather
than as a general upgrade.

**Why prioritization.** Functional bugs are rare and their transitions are the ones worth
learning from. On the toy site a 4,000-step run collects perhaps a dozen transitions in
which a deterministic trigger fired and a handful the judge flagged; uniform sampling
shows each of them to the network roughly 875 x 32 / 4000 ≈ 7 times over the whole run.
Prioritized sampling shows the surprising ones far more often, which is exactly the
regime PER was designed for.

**Why n-step.** The measured failure is that value never propagates back along a flow.
One-step TD moves credit one transition per update, and at 875 gradient updates a reward
five steps into a gated flow has no realistic path back to the action that started it.
An n-step return moves it n transitions at once. This does not manufacture the missing
trajectories — that is the archive's job — but once a deep trajectory exists, n-step is
what lets a 4,000-step budget learn anything from it.

**The two interact and the order matters.** Priorities are assigned to the *aggregated*
n-step transition, not to its constituents, so a single surprising step raises the
priority of the whole window that leads to it.

Implementation notes worth keeping:

* The sum tree is the standard proportional-prioritization structure: `O(log n)` update
  and sample, against `O(n)` for renormalizing a flat array every step.
* Importance-sampling weights correct the bias prioritization introduces, annealed from
  `beta0` to 1.0 across training. Without them the value estimate is biased toward
  whatever the buffer over-samples, which is by construction the noisiest transitions.
* Timeout handling is preserved from SB3: an episode that ends because it hit
  `max_steps` is **not** a terminal state, and bootstrapping must continue through it.
  Getting this wrong makes every 40th transition teach the agent that the world ends.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, NamedTuple

import numpy as np
import torch as th
from stable_baselines3.common.buffers import ReplayBuffer
from stable_baselines3.common.vec_env import VecNormalize

from ..utils.logging import get_logger

logger = get_logger(__name__)

# Added to every priority so a transition whose TD error is exactly zero can still be
# sampled. This is the spec's `|TD error| + 1e-6`.
_PRIORITY_EPS = 1e-6


class PrioritizedSamples(NamedTuple):
    """`ReplayBufferSamples` plus what the loss needs to correct and update priorities."""

    observations: th.Tensor
    actions: th.Tensor
    next_observations: th.Tensor
    dones: th.Tensor
    rewards: th.Tensor
    indices: np.ndarray
    weights: th.Tensor


class SumTree:
    """Fixed-capacity sum tree over priorities, for O(log n) proportional sampling."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._tree = np.zeros(2 * capacity, dtype=np.float64)

    @property
    def total(self) -> float:
        return float(self._tree[1])

    def set(self, index: int, priority: float) -> None:
        node = index + self.capacity
        delta = priority - self._tree[node]
        self._tree[node] = priority
        node //= 2
        while node >= 1:
            self._tree[node] += delta
            node //= 2

    def get(self, index: int) -> float:
        return float(self._tree[index + self.capacity])

    def sample(self, value: float) -> int:
        """Index whose cumulative priority interval contains `value`."""
        node = 1
        while node < self.capacity:
            left = 2 * node
            if value <= self._tree[left]:
                node = left
            else:
                value -= self._tree[left]
                node = left + 1
        return node - self.capacity

    def max_leaf(self) -> float:
        return float(self._tree[self.capacity :].max())


@dataclass
class _Pending:
    """One raw transition waiting to be folded into an n-step return."""

    obs: np.ndarray
    next_obs: np.ndarray
    action: np.ndarray
    reward: np.ndarray
    done: np.ndarray
    infos: list[dict[str, Any]]


class PrioritizedNStepReplayBuffer(ReplayBuffer):
    """Uniform SB3 buffer, extended with n-step aggregation and proportional priorities.

    Single-environment only, which is what this project runs (`DummyVecEnv` with one
    browser). Raising it to `n_envs > 1` needs one pending deque per env; that is
    deliberately not written until something needs it, because an untested branch that
    silently mixes two environments' transitions into one n-step return is a worse
    outcome than an explicit refusal.
    """

    def __init__(
        self,
        buffer_size: int,
        observation_space,
        action_space,
        device="auto",
        n_envs: int = 1,
        optimize_memory_usage: bool = False,
        handle_timeout_termination: bool = True,
        *,
        n_step: int = 3,
        gamma: float = 0.99,
        alpha: float = 0.6,
        beta0: float = 0.4,
    ) -> None:
        if n_envs != 1:
            raise ValueError(
                f"PrioritizedNStepReplayBuffer supports one environment, got n_envs={n_envs}. "
                "n-step returns must not be accumulated across interleaved environments."
            )
        super().__init__(
            buffer_size, observation_space, action_space, device,
            n_envs=n_envs, optimize_memory_usage=optimize_memory_usage,
            handle_timeout_termination=handle_timeout_termination,
        )
        self.n_step = max(1, int(n_step))
        self.gamma = gamma
        self.alpha = alpha
        self.beta0 = beta0
        self.beta = beta0
        self._tree = SumTree(buffer_size)
        self._max_priority = 1.0
        self._pending: deque[_Pending] = deque(maxlen=self.n_step)

    # -- writing ------------------------------------------------------------------

    def add(self, obs, next_obs, action, reward, done, infos) -> None:  # noqa: ANN001
        self._pending.append(
            _Pending(np.array(obs).copy(), np.array(next_obs).copy(), np.array(action).copy(),
                     np.array(reward).copy(), np.array(done).copy(), list(infos))
        )
        terminal = bool(np.any(done))
        # The deque has `maxlen=n_step`, so appending when full evicts the oldest and the
        # window slides on its own. Committing here therefore stores exactly one
        # aggregated transition per env step, starting from the n'th.
        committed_full = len(self._pending) == self.n_step
        if committed_full:
            self._commit(len(self._pending))
        if terminal:
            # Flush the tail as progressively shorter returns rather than discarding it.
            # At 40-step episodes, dropping the last n-1 transitions of every episode
            # would throw away ~7% of all experience, and disproportionately the *end*
            # of episodes — which is exactly where a completed flow would appear.
            if committed_full:
                # Its window was just stored; re-committing it here would duplicate the
                # transition and over-weight the end of every episode.
                self._pending.popleft()
            while self._pending:
                self._commit(len(self._pending))
                self._pending.popleft()
            self._pending.clear()

    def _commit(self, length: int) -> None:
        """Fold the first `length` pending transitions into one aggregated sample."""
        window = list(self._pending)[:length]
        first = window[0]

        cumulative = np.zeros_like(np.asarray(first.reward, dtype=np.float64))
        discount = 1.0
        last = window[0]
        for entry in window:
            cumulative = cumulative + discount * np.asarray(entry.reward, dtype=np.float64)
            discount *= self.gamma
            last = entry
            # A terminal inside the window truncates it: nothing after the episode ended
            # belongs in this return, and bootstrapping must use the terminal's own flag.
            if bool(np.any(entry.done)):
                break

        index = self.pos
        super().add(first.obs, last.next_obs, first.action, cumulative.astype(np.float32),
                    last.done, last.infos)
        # New transitions enter at the highest priority seen so far, so every one is
        # sampled at least once before prioritization can decide it is uninteresting.
        self._tree.set(index, self._max_priority ** self.alpha)

    # -- reading ------------------------------------------------------------------

    def sample(self, batch_size: int, env: VecNormalize | None = None) -> PrioritizedSamples:  # type: ignore[override]
        upper = self.buffer_size if self.full else self.pos
        if upper == 0:
            raise RuntimeError("sample() called on an empty buffer")
        total = self._tree.total
        if total <= 0.0:
            indices = np.random.randint(0, upper, size=batch_size)
        else:
            # Stratified: one draw from each of `batch_size` equal slices of the
            # cumulative distribution. Plain i.i.d. draws leave the high-priority tail
            # over-represented in a way stratification avoids at no cost.
            edges = np.linspace(0.0, total, batch_size + 1)
            draws = np.random.uniform(edges[:-1], edges[1:])
            indices = np.array([self._tree.sample(float(d)) for d in draws])
            indices = np.clip(indices, 0, upper - 1)

        probabilities = np.array([self._tree.get(int(i)) for i in indices]) / max(total, 1e-12)
        probabilities = np.maximum(probabilities, 1e-12)
        weights = (upper * probabilities) ** (-self.beta)
        weights = weights / max(weights.max(), 1e-12)

        samples = self._get_samples(indices, env=env)
        return PrioritizedSamples(
            observations=samples.observations,
            actions=samples.actions,
            next_observations=samples.next_observations,
            dones=samples.dones,
            rewards=samples.rewards,
            indices=indices,
            weights=th.as_tensor(weights, dtype=th.float32, device=self.device).reshape(-1, 1),
        )

    def update_priorities(self, indices: np.ndarray, td_errors: np.ndarray) -> None:
        """`priority = |TD error| + eps`, as the spec states it."""
        priorities = np.abs(np.asarray(td_errors, dtype=np.float64).reshape(-1)) + _PRIORITY_EPS
        for index, priority in zip(indices, priorities):
            self._tree.set(int(index), float(priority) ** self.alpha)
            self._max_priority = max(self._max_priority, float(priority))

    def set_beta(self, progress_remaining: float) -> None:
        """Anneal the importance-sampling exponent from `beta0` to 1.0 over training."""
        self.beta = self.beta0 + (1.0 - self.beta0) * (1.0 - max(0.0, min(1.0, progress_remaining)))

    def reset(self) -> None:
        super().reset()
        self._pending.clear()
        self._tree = SumTree(self.buffer_size)
        self._max_priority = 1.0
