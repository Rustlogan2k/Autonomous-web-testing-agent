"""Deep curiosity-driven Q-learning: an action-conditioned DQN with an NGU-style reward.

The tabular learner (`curiosity.py`) keeps one value per (abstract state, action) and
learns nothing about a state it has not visited. This learner generalises: states and
actions are described by the tokens they are made of (page kind, URL template, heading
words; action type, control name, value category), hashed into fixed-size vectors, and a
network estimates Q(s, a) from the pair, so what it learned about "submit on a form page"
carries to the next form page.

Its intrinsic reward follows Never Give Up [badia2020ngu], as WebRLED [gu2025webrled]
combines novelty within the episode with novelty over the whole run:

- episodic novelty: a state's similarity to the states already met in this episode, by a
  kernel over its k nearest neighbours in an episodic memory (states that share most of
  their tokens count as near-repeats, unlike the tabular count, which only knows equality);
- lifelong novelty: Random Network Distillation's prediction error for the state,
  normalised over the run, as a multiplier clipped to [1, L].

The reward is episodic × lifelong. It is used, like the tabular learner, to choose the
hybrid tester's free exploration steps (`HybridPolicy(explore="deep_curiosity")`), and it
learns from every transition the policy observes (off-policy), replays and operator steps
included.

Defaults: WebRLED's ε 0.4 and L 5, and γ 0.95 (WebRLED's released config uses γ 0.997;
the 0.95 here was taken from an earlier note and is what the 2026-10-28 runs used); the
network sizes and
optimiser settings are the usual small-DQN ones, fixed here and not tuned.
"""

from __future__ import annotations

import math
import re
import zlib
from collections import deque

import numpy as np
import torch
from torch import nn

from ..envs.types import ActionSpec, ActionType
from .labels import control_identity

#: Hashed feature sizes for states and actions.
STATE_DIM = 256
ACTION_DIM = 256
#: Exploration rate and the lifelong multiplier's cap (WebRLED's), and the discount (not
#: WebRLED's 0.997; see the module docstring).
EPSILON = 0.4
MAX_LIFELONG = 5.0
GAMMA = 0.95
#: NGU's episodic-memory settings: neighbours, kernel constant, pseudo-count constant and the
#: similarity above which a state earns nothing.
NEIGHBOURS = 10
KERNEL_EPSILON = 1e-3
PSEUDO_COUNT_C = 1e-3
MAX_SIMILARITY = 8.0
#: Training: replay capacity, batch, start, target-network period, learning rates.
REPLAY_CAPACITY = 20_000
BATCH = 32
LEARN_START = 64
TARGET_PERIOD = 250
LEARNING_RATE = 1e-3
RND_LEARNING_RATE = 1e-3

_WORD = re.compile(r"[a-z]+|\d+")


def _bucket(token: str, size: int) -> int:
    return zlib.crc32(token.encode("utf-8")) % size


def _hashed(tokens: list[str], size: int) -> np.ndarray:
    vector = np.zeros(size, dtype=np.float32)
    for token in tokens:
        vector[_bucket(token, size)] = 1.0
    return vector


def _words(text: str, limit: int = 8) -> list[str]:
    return _WORD.findall(text.lower())[:limit]


def state_tokens(cell: str) -> list[str]:
    """A hybrid cell key (`PageView.cell`: kind|path|heading|fill|alert|offers) as tokens."""
    parts = cell.split("|")
    if len(parts) < 6:
        return [f"cell:{cell}"]
    kind, path, fill, alert, offers = parts[0], parts[1], parts[-3], parts[-2], parts[-1]
    heading = "|".join(parts[2:-3])
    segments = [s for s in path.split("/") if s]
    tokens = [f"kind:{kind}", f"path:{path}", f"fill:{fill}", f"alert:{alert}", f"offers:{offers}"]
    tokens += [f"seg{i}:{s}" for i, s in enumerate(segments[:6])]
    tokens += [f"segment:{s}" for s in segments]
    tokens += [f"head:{w}" for w in _words(heading)]
    return tokens


def action_tokens(spec: ActionSpec) -> list[str]:
    """An action as tokens: its type, its control (whole and by word), its value category."""
    params = spec.params or {}
    identity = control_identity(spec)
    tokens = [f"type:{spec.action_type.value}", f"control:{identity}"]
    tokens += [f"word:{w}" for w in _words(identity)]
    tokens += [f"label:{w}" for w in _words(spec.element_id or "")]
    for key in ("category", "option", "direction", "preset"):
        if key in params:
            tokens.append(f"{key}:{params[key]}")
    if params.get("press_enter"):
        tokens.append("enter")
    return tokens


def _mlp(inputs: int, hidden: int, outputs: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(inputs, hidden), nn.ReLU(), nn.Linear(hidden, hidden), nn.ReLU(),
        nn.Linear(hidden, outputs),
    )


class _Running:
    """Running mean and standard deviation (Welford)."""

    def __init__(self) -> None:
        self.n, self.mean, self.m2 = 0, 0.0, 0.0

    def add(self, value: float) -> None:
        self.n += 1
        delta = value - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (value - self.mean)

    @property
    def std(self) -> float:
        return math.sqrt(self.m2 / (self.n - 1)) if self.n > 1 else 0.0


class DeepCuriosityQ:
    """Action-conditioned DQN with an NGU-style intrinsic reward; the `CuriosityQ` interface."""

    def __init__(self, seed: int = 0, *, epsilon: float = EPSILON, gamma: float = GAMMA,
                 max_lifelong: float = MAX_LIFELONG) -> None:
        self.epsilon, self.gamma, self.max_lifelong = epsilon, gamma, max_lifelong
        self.rng = np.random.default_rng(seed + 13)
        torch.manual_seed(seed + 13)
        self.q_net = _mlp(STATE_DIM + ACTION_DIM, 128, 1)
        self.target_net = _mlp(STATE_DIM + ACTION_DIM, 128, 1)
        self.target_net.load_state_dict(self.q_net.state_dict())
        self.optimizer = torch.optim.Adam(self.q_net.parameters(), lr=LEARNING_RATE)
        # Random Network Distillation: a fixed random target and a predictor trained to match
        # it; the predictor's error is high on states unlike those seen so far.
        self.rnd_target = _mlp(STATE_DIM, 64, 32)
        for parameter in self.rnd_target.parameters():
            parameter.requires_grad_(False)
        self.rnd_predictor = _mlp(STATE_DIM, 64, 32)
        self.rnd_optimizer = torch.optim.Adam(self.rnd_predictor.parameters(),
                                              lr=RND_LEARNING_RATE)
        self.rnd_error = _Running()
        self.replay: deque = deque(maxlen=REPLAY_CAPACITY)
        self.episode_memory: list[np.ndarray] = []
        self.neighbour_distance = _Running()  # NGU's running mean of squared k-NN distances
        self._state_cache: dict[str, np.ndarray] = {}
        self._action_cache: dict[tuple, np.ndarray] = {}
        self.updates = 0
        self.reward_total = 0.0
        self.states: set[str] = set()
        self.loss = 0.0

    # -- features -------------------------------------------------------------------------------

    def _state(self, cell: str) -> np.ndarray:
        vector = self._state_cache.get(cell)
        if vector is None:
            vector = _hashed(state_tokens(cell), STATE_DIM)
            self._state_cache[cell] = vector
        return vector

    def _action(self, spec: ActionSpec) -> np.ndarray:
        params = spec.params or {}
        key = (spec.action_type.value, control_identity(spec), spec.element_id,
               params.get("category"), params.get("option"), params.get("direction"),
               params.get("preset"), bool(params.get("press_enter")))
        vector = self._action_cache.get(key)
        if vector is None:
            vector = _hashed(action_tokens(spec), ACTION_DIM)
            self._action_cache[key] = vector
        return vector

    def _candidates(self, specs: list[ActionSpec]) -> list[ActionSpec]:
        return [s for s in specs if s.action_type is not ActionType.NO_OP]

    def _actions(self, specs: list[ActionSpec]) -> np.ndarray:
        if not specs:
            return np.zeros((0, ACTION_DIM), dtype=np.float32)
        return np.stack([self._action(s) for s in specs])

    # -- the CuriosityQ interface ---------------------------------------------------------------

    def begin_episode(self) -> None:
        self.episode_memory = []

    def choose(self, state: str, specs: list[ActionSpec]) -> ActionSpec | None:
        candidates = self._candidates(specs)
        if not candidates:
            return None
        if self.rng.random() < self.epsilon:
            return candidates[int(self.rng.integers(len(candidates)))]
        values = self._q(self.q_net, self._state(state), self._actions(candidates))
        best = np.flatnonzero(values >= values.max() - 1e-6)
        return candidates[int(best[self.rng.integers(len(best))])]

    def update(
        self, state: str, spec: ActionSpec, after: str, next_specs: list[ActionSpec], done: bool
    ) -> float:
        s, a, s_next = self._state(state), self._action(spec), self._state(after)
        if not self.episode_memory:
            self.episode_memory.append(s)  # the episode's first state is in memory too
        r = self.reward(s_next)
        self.episode_memory.append(s_next)
        self.states.update((state, after))
        nexts = self._actions(self._candidates(next_specs)) if not done else None
        self.replay.append((s, a, r, s_next, nexts))
        self._train_rnd(s_next)
        if len(self.replay) >= LEARN_START:
            self._train_q()
        self.updates += 1
        self.reward_total += r
        return r

    def summary(self) -> dict:
        return {
            "learner": "deep_curiosity",
            "epsilon": self.epsilon,
            "gamma": self.gamma,
            "max_lifelong": self.max_lifelong,
            "states": len(self.states),
            "replay": len(self.replay),
            "updates": self.updates,
            "mean_reward": round(self.reward_total / max(self.updates, 1), 4),
            "last_loss": round(self.loss, 5),
        }

    # -- the reward -----------------------------------------------------------------------------

    def episodic(self, s_next: np.ndarray) -> float:
        """NGU's episodic novelty, 1 / sqrt(Σ K(x, neighbours)), against this episode's
        memory. An exact repeat has kernel value 1, so n repeats give 1/√(1+n): the
        tabular count's reward, extended to near-repeats. The +1 (the state itself) keeps
        the reward bounded when the memory holds nothing near."""
        if not self.episode_memory:
            return 1.0
        memory = np.stack(self.episode_memory)
        distances = np.sort(((memory - s_next) ** 2).sum(axis=1))[:NEIGHBOURS]
        for d in distances:
            self.neighbour_distance.add(float(d))
        mean = self.neighbour_distance.mean
        normalised = distances / mean if mean > 0 else np.zeros_like(distances)
        kernel = KERNEL_EPSILON / (normalised + KERNEL_EPSILON)
        similarity = math.sqrt(float(kernel.sum()) + 1.0) + PSEUDO_COUNT_C
        return 0.0 if similarity > MAX_SIMILARITY else 1.0 / similarity

    def lifelong(self, s_next: np.ndarray) -> float:
        """RND's normalised error as a multiplier in [1, L]."""
        with torch.no_grad():
            x = torch.from_numpy(s_next).unsqueeze(0)
            error = float(((self.rnd_predictor(x) - self.rnd_target(x)) ** 2).mean())
        self.rnd_error.add(error)
        std = self.rnd_error.std
        alpha = 1.0 + (error - self.rnd_error.mean) / std if std > 0 else 1.0
        return float(min(max(alpha, 1.0), self.max_lifelong))

    def reward(self, s_next: np.ndarray) -> float:
        return self.episodic(s_next) * self.lifelong(s_next)

    # -- learning -------------------------------------------------------------------------------

    @staticmethod
    def _q(net: nn.Module, state: np.ndarray, actions: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            s = torch.from_numpy(np.repeat(state[None, :], len(actions), axis=0))
            return net(torch.cat([s, torch.from_numpy(actions)], dim=1)).squeeze(1).numpy()

    def _train_rnd(self, s_next: np.ndarray) -> None:
        x = torch.from_numpy(s_next).unsqueeze(0)
        loss = ((self.rnd_predictor(x) - self.rnd_target(x)) ** 2).mean()
        self.rnd_optimizer.zero_grad()
        loss.backward()
        self.rnd_optimizer.step()

    def _train_q(self) -> None:
        """One double-DQN step on a sampled batch; the next state's actions are padded per
        batch and masked, since each page offers a different number."""
        batch = [self.replay[int(i)] for i in self.rng.integers(len(self.replay), size=BATCH)]
        s = torch.from_numpy(np.stack([b[0] for b in batch]))
        a = torch.from_numpy(np.stack([b[1] for b in batch]))
        r = torch.tensor([b[2] for b in batch], dtype=torch.float32)
        widest = max((len(b[4]) for b in batch if b[4] is not None), default=0)
        target = r.clone()
        if widest:
            pairs = np.zeros((BATCH, widest, STATE_DIM + ACTION_DIM), dtype=np.float32)
            mask = np.zeros((BATCH, widest), dtype=bool)
            for i, (_, _, _, s_next, nexts) in enumerate(batch):
                if nexts is None or not len(nexts):
                    continue
                pairs[i, : len(nexts), :STATE_DIM] = s_next
                pairs[i, : len(nexts), STATE_DIM:] = nexts
                mask[i, : len(nexts)] = True
            pairs_t, mask_t = torch.from_numpy(pairs), torch.from_numpy(mask)
            with torch.no_grad():
                online = self.q_net(pairs_t).squeeze(-1).masked_fill(~mask_t, -1e9)
                choice = online.argmax(dim=1, keepdim=True)
                value = self.target_net(pairs_t).squeeze(-1).gather(1, choice).squeeze(1)
            value = torch.where(mask_t.any(dim=1), value, torch.zeros_like(value))
            target = r + self.gamma * value
        prediction = self.q_net(torch.cat([s, a], dim=1)).squeeze(1)
        loss = nn.functional.smooth_l1_loss(prediction, target)
        self.optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self.q_net.parameters(), 10.0)
        self.optimizer.step()
        self.loss = float(loss)
        if self.updates % TARGET_PERIOD == 0:
            self.target_net.load_state_dict(self.q_net.state_dict())
