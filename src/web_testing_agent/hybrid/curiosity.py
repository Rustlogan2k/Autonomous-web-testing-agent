"""Curiosity-driven tabular Q-learning, as the RL web testers that report gains use it.

The literature's RL web testers give the learner one job, exploration, and reward it on
every step for novelty:

- WebExplor [zheng2021webexplor]: tabular Q-learning over abstract states, a curiosity
  reward from a visit-count table over transitions, and high-level guidance (an automaton
  built online) that sends the agent back to under-explored states;
- WebRLED [gu2025webrled]: a curiosity reward that considers a state's novelty both within
  the episode and over the global history;
- the 2026 empirical study [liu2026webgui]: compact state abstractions suit RL.

Two uses here, each a development arm:

- `CuriosityQ` inside the hybrid tester (`HybridPolicy(explore="curiosity")`): it chooses
  the free exploration steps between test operators, which were weighted-random;
- `CuriosityPolicy`, a standalone tester built only from those components: no test
  operators, no bandit, no invariants.

Both learn from every transition they observe (Q-learning is off-policy), about 2,000
updates per run, where the hybrid's operator bandit gets about 135 outcomes.
"""

from __future__ import annotations

import math

import numpy as np

from ..envs.types import ActionSpec, ActionType
from ..evaluation.scripted import ScriptedPolicy
from .abstraction import PageView, observe
from .archive import Archive, step_for
from .labels import control_identity

#: Learning rate, discount and exploration rate of the tabular learner. γ > 0 so that it
#: learns paths toward unexplored states, not only the next step's novelty.
ALPHA = 0.5
GAMMA = 0.9
EPSILON = 0.1
#: Episodic novelty (WebRLED): reaching a state not yet seen in this episode earns this much
#: on top of the global transition-count term.
EPISODIC_BONUS = 0.5
#: Value of an action never taken in a state: the largest value any action can reach,
#: R_max / (1 - γ). Anything lower lets an action that changes nothing (a self-loop)
#: bootstrap off its own value and outrank untried actions for many steps.
OPTIMISTIC_Q = (1.0 + EPISODIC_BONUS) / (1.0 - GAMMA)
#: Standalone tester: chance an episode starts by returning to the most curious known state
#: (WebExplor's high-level guidance), otherwise it starts from the entry page.
GUIDE_PROBABILITY = 0.5


def action_key(spec: ActionSpec) -> str:
    """An action as the same choice across visits: its control and what it does there."""
    params = spec.params or {}
    detail = (params.get("category") or params.get("option") or "") + ("+enter" if params.get("press_enter") else "")
    return f"{spec.action_type.value}|{control_identity(spec)}|{detail}"


class CuriosityQ:
    """Tabular Q-learning over (abstract state, action) with a count-based curiosity reward."""

    def __init__(self, seed: int = 0) -> None:
        self.rng = np.random.default_rng(seed + 7)
        self.q: dict[tuple[str, str], float] = {}
        self.transitions: dict[tuple[str, str, str], int] = {}
        self.state_visits: dict[str, int] = {}
        self.tried: dict[str, set[str]] = {}
        self.offered: dict[str, set[str]] = {}
        self.episode_states: set[str] = set()
        self.updates = 0
        self.reward_total = 0.0

    def begin_episode(self) -> None:
        self.episode_states = set()

    def _value(self, state: str, key: str) -> float:
        return self.q.get((state, key), OPTIMISTIC_Q)

    def _candidates(self, specs: list[ActionSpec]) -> list[ActionSpec]:
        return [s for s in specs if s.action_type is not ActionType.NO_OP]

    def choose(self, state: str, specs: list[ActionSpec]) -> ActionSpec | None:
        candidates = self._candidates(specs)
        if not candidates:
            return None
        self.offered.setdefault(state, set()).update(action_key(s) for s in candidates)
        if self.rng.random() < EPSILON:
            return candidates[int(self.rng.integers(len(candidates)))]
        values = np.array([self._value(state, action_key(s)) for s in candidates])
        best = np.flatnonzero(values == values.max())
        return candidates[int(best[self.rng.integers(len(best))])]

    def reward(self, state: str, key: str, after: str) -> float:
        """Transition curiosity (WebExplor) plus episodic novelty (WebRLED)."""
        count = self.transitions.get((state, key, after), 0) + 1
        novel = after not in self.episode_states
        return 1.0 / math.sqrt(count) + (EPISODIC_BONUS if novel else 0.0)

    def update(
        self, state: str, spec: ActionSpec, after: str, next_specs: list[ActionSpec], done: bool
    ) -> float:
        key = action_key(spec)
        r = self.reward(state, key, after)
        self.transitions[(state, key, after)] = self.transitions.get((state, key, after), 0) + 1
        self.state_visits[after] = self.state_visits.get(after, 0) + 1
        self.tried.setdefault(state, set()).add(key)
        self.episode_states.add(state)
        self.episode_states.add(after)
        future = 0.0
        if not done:
            nexts = self._candidates(next_specs)
            self.offered.setdefault(after, set()).update(action_key(s) for s in nexts)
            future = max((self._value(after, action_key(s)) for s in nexts), default=0.0)
        old = self._value(state, key)
        self.q[(state, key)] = old + ALPHA * (r + GAMMA * future - old)
        self.updates += 1
        self.reward_total += r
        return r

    def curiosity(self, state: str) -> float:
        """How much a state still promises: its untried actions, discounted by visits."""
        untried = len(self.offered.get(state, set()) - self.tried.get(state, set()))
        return untried / math.sqrt(1 + self.state_visits.get(state, 0))

    def summary(self) -> dict:
        return {
            "states": len(self.state_visits),
            "q_entries": len(self.q),
            "transitions": len(self.transitions),
            "updates": self.updates,
            "mean_reward": round(self.reward_total / max(self.updates, 1), 4),
        }


class CuriosityPolicy:
    """A standalone curiosity-driven RL tester (WebExplor / WebRLED components only).

    States are the hybrid's compact cells (deterministic labels), so it shares the
    abstraction but none of the testing knowledge: no operators, no follow-through, no
    learner over techniques, no invariants. Each episode either starts at the entry page or
    (with probability `GUIDE_PROBABILITY`) first replays the shortest known walk to the most
    curious state, as WebExplor's automaton guidance does. Replayed steps count toward the
    budget, as for every arm.
    """

    def __init__(self, *, seed: int = 0, episode_steps: int = 40) -> None:
        self.learner = CuriosityQ(seed)
        self.archive = Archive(seed, episode_steps=episode_steps)
        self.rng = np.random.default_rng(seed + 11)
        self.candidates: list = []  # no invariant oracle in this arm
        self.stats = {"episodes": 0, "guided": 0, "failed_returns": 0}
        self._views: dict[int, PageView] = {}
        self.reset()

    def reset(self) -> None:
        self.trace: list[dict] = []
        self.replay: list[dict] = []
        self.learner.begin_episode()

    def begin_episode(self, info: dict) -> None:  # noqa: ARG002 - the entry page is implied
        self.stats["episodes"] += 1
        away = [c for c in self.archive.cells.values() if c.trace]  # the entry is free
        if away and self.rng.random() < GUIDE_PROBABILITY:
            target = max(away, key=lambda c: (self.learner.curiosity(c.key), -c.depth, c.key))
            self.replay = list(target.trace)
            self.stats["guided"] += 1

    def _view(self, info: dict) -> PageView:
        """The view of an env `info`; the last two are kept (a step's before and after)."""
        view = self._views.get(id(info))
        if view is None:
            view = observe(info)
            self._views = {**dict(list(self._views.items())[-1:]), id(info): view}
        return view

    def act(self, observation: dict, info: dict) -> int:  # noqa: ARG002 - reads no pixels
        view = self._view(info)
        self.archive.visit(view.cell, self.trace, view.labels.kind, 0)
        spec = None
        if self.replay:
            index = ScriptedPolicy._match(self.replay[0], view.specs)  # noqa: SLF001
            if index is None:
                self.stats["failed_returns"] += 1
                self.replay = []
            else:
                self.replay.pop(0)
                spec = next(s for s in view.specs if s.index == index)
        if spec is None:
            spec = self.learner.choose(view.cell, view.specs) or view.specs[0]
        self.trace.append(step_for(spec))
        return spec.index

    def observe_transition(
        self, before: dict, action: int, reward: float, after: dict, terminated: bool, done: bool
    ) -> None:  # noqa: ARG002 - the env reward is not this learner's
        before_view, after_view = self._view(before), self._view(after)
        spec = next((s for s in before_view.specs if s.index == action), None)
        if spec is not None:
            self.learner.update(
                before_view.cell, spec, after_view.cell, after_view.specs, terminated or done
            )

    def summary(self) -> dict:
        return {
            "policy": "curiosity_rl",
            "archive": self.archive.summary(),
            **self.stats,
            "learner": self.learner.summary(),
        }
