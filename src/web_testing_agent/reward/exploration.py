"""Coverage and repetition tracking for Agent B's exploration incentive.

Agent A's spec has an explicit -3 repetition penalty; Agent B's did not, which leaves
a value-based agent free to camp on one already-discovered buggy element and farm the
deterministic trigger bonus forever instead of covering the app. This module supplies
the two counter-signals the reward needs:

* **novelty** — did this step reach a page state the agent has not seen this episode?
* **repetition** — how many times has it already taken this exact action on this exact
  element in the recent past?

Novelty is *episodic* by default (reset each episode, as in NGU/RND-style episodic
curiosity): a persistent bonus decays to zero once the site is covered and stops
shaping behaviour, whereas an episodic one keeps rewarding "get somewhere new from
the start state" for the whole run. The persistent counters are still kept, but for
reporting coverage metrics rather than for reward.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..envs.types import ActionSpec, ActionType


@dataclass(frozen=True, slots=True)
class ExplorationSignal:
    """Per-step exploration bookkeeping handed to the reward composer."""

    is_novel_state: bool
    repeat_count: int
    episode_states_seen: int
    total_states_seen: int

    @property
    def is_repetitive(self) -> bool:
        return self.repeat_count > 0


@dataclass
class ExplorationTracker:
    """Tracks state coverage and recent action repetition across an episode.

    `repetition_window` bounds how far back a repeat counts, so an agent that
    legitimately revisits an element much later in a long flow is not punished for it.
    """

    repetition_window: int = 20
    _episode_states: set[str] = field(default_factory=set, init=False)
    _all_states: set[str] = field(default_factory=set, init=False)
    _recent_actions: deque[tuple[str, str]] = field(default_factory=deque, init=False)

    @property
    def total_states_seen(self) -> int:
        """Distinct page states reached across every episode — the coverage metric."""
        return len(self._all_states)

    @property
    def episode_states_seen(self) -> int:
        return len(self._episode_states)

    def start_episode(self) -> None:
        self._episode_states = set()
        self._recent_actions = deque(maxlen=self.repetition_window)

    def observe_reset(self, state_key: str) -> None:
        """Register the episode's starting state so it never counts as a discovery."""
        self._episode_states.add(state_key)
        self._all_states.add(state_key)

    def observe_step(self, spec: ActionSpec, state_key: str) -> ExplorationSignal:
        identity = self._action_identity(spec)
        repeat_count = sum(1 for entry in self._recent_actions if entry == identity)
        # NO_OP is counted like any other action. An earlier version exempted it, on the
        # reasoning that "the absence of an action isn't a repetition and the step
        # penalty already prices it". That made NO_OP the single action in the whole
        # space immune to a penalty reaching -3.0, so as soon as novelty ran out, doing
        # nothing strictly dominated every alternative. A trained DQN converged to 100%
        # NO_OP for exactly this reason. Repeatedly doing nothing is the behaviour this
        # penalty most needs to discourage, not the one it should exempt.
        self._recent_actions.append(identity)

        is_novel = state_key not in self._episode_states
        self._episode_states.add(state_key)
        self._all_states.add(state_key)

        return ExplorationSignal(
            is_novel_state=is_novel,
            repeat_count=repeat_count,
            episode_states_seen=len(self._episode_states),
            total_states_seen=len(self._all_states),
        )

    @staticmethod
    def _action_identity(spec: ActionSpec) -> tuple[str, str]:
        """What counts as "the same action" for repetition purposes.

        Keyed on the *semantic* target (action type + element identity + value category),
        not the action index — indices are rebuilt every step and mean nothing across
        states, so an index-keyed counter would never detect a genuine repeat.
        """
        target = spec.element_id or spec.selector or ""
        if spec.action_type is ActionType.TYPE:
            target = f"{target}#{spec.params.get('category', '')}"
        elif spec.action_type is ActionType.SELECT:
            target = f"{target}#{spec.params.get('option', '')}"
        elif spec.action_type is ActionType.RESIZE_VIEWPORT:
            target = str(spec.params.get("preset", ""))
        elif spec.action_type is ActionType.SCROLL:
            target = str(spec.params.get("direction", ""))
        return (spec.action_type.value, target)
