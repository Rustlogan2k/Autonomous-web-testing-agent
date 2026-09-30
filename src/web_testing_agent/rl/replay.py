"""Prioritized replay over transitions whose action sets differ in size.

`agents.replay.PrioritizedNStepReplayBuffer` stores Stable-Baselines3's fixed-width
observation and is single-environment by construction. The transitions here carry a state
vector and a variable-length action matrix, and they arrive from several applications
interleaved, so the buffer stores them as they are and pads only when a batch is drawn.

**Prioritization is proportional** (Schaul et al., 2016): a transition is sampled with
probability proportional to `(|error| + eps)^alpha` and its gradient is corrected by the
importance weight `(N * P(i))^-beta`, normalised by the batch maximum. Sampling is a
cumulative-sum search over the priority array rather than a sum tree: at the buffer sizes
a browser-bound agent reaches (tens of thousands) that is a fraction of a millisecond, and
it is one array instead of a data structure that has to be kept consistent.

**n-step returns are assembled before insertion**, by the agent, so each stored transition
already carries its accumulated reward, the observation `k` steps later and the exact
discount `gamma^k` that applies to the bootstrap. Storing the discount per transition is
what makes a truncated tail correct: the last transitions of an episode look fewer than
`n` steps ahead, and bootstrapping them with `gamma^n` would over-discount every one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class Transition:
    state: np.ndarray            # (S,) float32
    actions: np.ndarray          # (n, D) float16 -- shared by reference with neighbours
    action: int
    reward: float                # accumulated over `steps` env steps, already discounted
    next_state: np.ndarray
    next_actions: np.ndarray
    terminal: bool               # no bootstrap when True
    discount: float              # gamma ** steps
    app: str = ""                # which application it came from; bookkeeping only


class PrioritizedReplay:
    """Ring buffer with proportional prioritization."""

    def __init__(self, capacity: int, *, alpha: float = 0.6, beta0: float = 0.4,
                 epsilon: float = 1e-3, seed: int = 0) -> None:
        self.capacity = int(capacity)
        self.alpha, self.beta0, self.epsilon = float(alpha), float(beta0), float(epsilon)
        self._items: list[Transition | None] = [None] * self.capacity
        self._priorities = np.zeros(self.capacity, dtype=np.float64)
        self._cursor = 0
        self._size = 0
        self._max_priority = 1.0
        self._rng = np.random.default_rng(seed)

    def __len__(self) -> int:
        return self._size

    def add(self, transition: Transition) -> None:
        # New experience enters at the current maximum priority, so it is replayed at
        # least once before its own error decides how often it comes back.
        self._items[self._cursor] = transition
        self._priorities[self._cursor] = self._max_priority ** self.alpha
        self._cursor = (self._cursor + 1) % self.capacity
        self._size = min(self._size + 1, self.capacity)

    def sample(self, batch_size: int, progress: float = 0.0):
        """`(transitions, indices, weights)`. `progress` in [0, 1] anneals beta to 1."""
        if self._size == 0:
            raise ValueError("cannot sample from an empty replay buffer")
        priorities = self._priorities[: self._size]
        total = priorities.sum()
        if self.alpha == 0.0 or total <= 0.0:
            indices = self._rng.integers(0, self._size, size=batch_size)
            weights = np.ones(batch_size, dtype=np.float32)
        else:
            cumulative = np.cumsum(priorities)
            indices = np.searchsorted(cumulative, self._rng.random(batch_size) * total)
            indices = np.minimum(indices, self._size - 1)
            beta = self.beta0 + (1.0 - self.beta0) * float(np.clip(progress, 0.0, 1.0))
            probabilities = priorities[indices] / total
            weights = (self._size * probabilities) ** (-beta)
            weights = (weights / weights.max()).astype(np.float32)
        return [self._items[i] for i in indices], indices, weights

    def update_priorities(self, indices: np.ndarray, errors: np.ndarray) -> None:
        magnitudes = np.abs(np.asarray(errors, dtype=np.float64)).reshape(-1) + self.epsilon
        self._priorities[indices] = magnitudes ** self.alpha
        self._max_priority = max(self._max_priority, float(magnitudes.max()))


def collate(transitions: list[Transition]):
    """Pad a batch to its widest action set. Returns numpy arrays ready for `torch`.

    Padding rows are zeros and are excluded by the returned masks, so a transition's
    Q-values do not depend on which other transitions it happened to be batched with.
    """
    batch = len(transitions)
    width = max(t.actions.shape[0] for t in transitions)
    next_width = max(t.next_actions.shape[0] for t in transitions)
    state_dim = transitions[0].state.shape[0]
    action_dim = transitions[0].actions.shape[1]

    states = np.zeros((batch, state_dim), dtype=np.float32)
    next_states = np.zeros((batch, state_dim), dtype=np.float32)
    actions = np.zeros((batch, width, action_dim), dtype=np.float32)
    next_actions = np.zeros((batch, next_width, action_dim), dtype=np.float32)
    mask = np.zeros((batch, width), dtype=bool)
    next_mask = np.zeros((batch, next_width), dtype=bool)
    chosen = np.zeros((batch, 1), dtype=np.int64)
    rewards = np.zeros((batch, 1), dtype=np.float32)
    terminals = np.zeros((batch, 1), dtype=np.float32)
    discounts = np.zeros((batch, 1), dtype=np.float32)

    for row, t in enumerate(transitions):
        n, m = t.actions.shape[0], t.next_actions.shape[0]
        states[row], next_states[row] = t.state, t.next_state
        actions[row, :n], mask[row, :n] = t.actions, True
        next_actions[row, :m], next_mask[row, :m] = t.next_actions, True
        chosen[row, 0], rewards[row, 0] = t.action, t.reward
        terminals[row, 0], discounts[row, 0] = float(t.terminal), t.discount
    return (states, actions, mask, chosen, rewards,
            next_states, next_actions, next_mask, terminals, discounts)
