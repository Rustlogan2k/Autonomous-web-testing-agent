"""The non-learning baseline, scored on exactly the features the learner receives.

`agents.hand_priority.HandPriorityPolicy` is the pre-registered search arm and is run
unchanged. It reads the older action representation, though, and the corrected comparison
the project owes itself is one in which search and learning are handed *the same vector*
and differ only in how they turn it into a choice. This policy is that arm:

    learner   score(a) = Q_theta(s, phi(a))      theta learned from reward
    this      score(a) = w . phi(a)              w written down by hand, fixed

`phi` is `rl.features`, identical in both rows, including the two label-meaning scalars
(the frozen A2 anchor axis and the frozen A1 word list) and the agent's own history with
each action. Nothing here is fitted to any application and nothing updates: `act()`
changes the visit memory and nothing else.

**The weights are the pre-registered hand priority, carried over term for term**
(`HAND_PRIORITY_WEIGHTS`), plus the label prior at its frozen weight of 1.2, plus two
terms for information the older representation did not carry:

* `value_already_set` -- retyping the value a field already holds is a wasted step;
* an observed-no-effect penalty -- an action that has been tried here and has never once
  changed the page is deprioritised in proportion to how reliably it did nothing.

Both are statements about web applications in general and both use only what the learner
is also shown. They were fixed before any transfer run and are not tuned.
"""

from __future__ import annotations

import numpy as np

from ..agents.hand_priority import HAND_PRIORITY_TYPE_WEIGHTS, HAND_PRIORITY_WEIGHTS
from ..agents.label_priors import W_LABEL_PRIOR
from ..envs.types import ActionSpec, ActionType
from .features import (
    ACTION_DIM,
    TYPE_SLICE,
    FeatureExtractor,
    flag_index,
    numeric_index,
)

ALGORITHM = "feature_search"

#: Penalty for entering the value a field already holds.
W_VALUE_ALREADY_SET = -3.0
#: Penalty, scaled by (1 - observed change rate), for an action tried here before.
W_OBSERVED_NO_EFFECT = -2.0


def build_weights(label_prior: str = "anchor") -> np.ndarray:
    """The fixed weight vector over `rl.features` action columns."""
    if label_prior not in ("anchor", "lexical", "none"):
        raise ValueError(f"label_prior must be anchor, lexical or none, got {label_prior!r}")
    weights = np.zeros(ACTION_DIM, dtype=np.float32)
    for position, action_type in enumerate(ActionType):
        weights[TYPE_SLICE.start + position] = HAND_PRIORITY_TYPE_WEIGHTS.get(action_type, 0.0)
    for name in ("navigational", "in_viewport", "opens_new_tab", "is_newly_revealed",
                 "has_href", "is_fixed_action", "is_form_control"):
        weights[flag_index(name)] = HAND_PRIORITY_WEIGHTS[name]
    for name in ("value_ordinal", "taken_here", "taken_this_episode"):
        weights[numeric_index(name)] = HAND_PRIORITY_WEIGHTS[name]
    weights[numeric_index("value_already_set")] = W_VALUE_ALREADY_SET
    if label_prior == "anchor":
        weights[numeric_index("anchor_commit_score")] = W_LABEL_PRIOR
    elif label_prior == "lexical":
        weights[numeric_index("lexical_commit_score")] = W_LABEL_PRIOR
    return weights


class FeatureSearchPolicy:
    """Argmax of a fixed linear score over the shared action features."""

    def __init__(self, *, label_prior: str = "anchor", epsilon: float = 0.05,
                 seed: int | None = None, features: FeatureExtractor | None = None) -> None:
        self.algorithm = ALGORITHM
        self.label_prior = label_prior
        self.epsilon = float(epsilon)
        self.weights = build_weights(label_prior)
        self.features = features or FeatureExtractor()
        self._rng = np.random.default_rng(seed)
        self._tried = numeric_index("tried_here")
        self._change_rate = numeric_index("changed_state_rate")
        self.steps = 0
        self.exploratory_steps = 0

    def reset(self) -> None:
        self.features.begin_episode()

    def scores(self, block: np.ndarray) -> np.ndarray:
        no_effect = block[:, self._tried] * (1.0 - block[:, self._change_rate])
        return block @ self.weights + W_OBSERVED_NO_EFFECT * no_effect

    def act(self, observation: dict, info: dict) -> int:  # noqa: ARG002 - reads no pixels
        encoded = self.features.encode(info)
        if encoded.count == 0:
            return 0
        self.steps += 1
        if self.epsilon and self._rng.random() < self.epsilon:
            self.exploratory_steps += 1
            return int(self._rng.integers(0, encoded.count))
        # `argmax` returns the lowest index among ties, which is the action registry's own
        # priority order (on-screen first, then clicks, typical values, selects, ...).
        return int(np.argmax(self.scores(encoded.actions)))

    def observe_transition(self, before: dict, action: int, reward: float, after: dict,  # noqa: ARG002
                           terminated: bool, done: bool) -> None:  # noqa: ARG002
        """Update the visit memory. Not learning: no parameter of the score changes."""
        executed: ActionSpec | None = after.get("action_spec")
        self.features.record(before, executed, after)

    def to_dict(self) -> dict:
        return {
            "algorithm": self.algorithm, "learns": False, "label_prior": self.label_prior,
            "label_prior_weight": W_LABEL_PRIOR if self.label_prior != "none" else 0.0,
            "epsilon": self.epsilon, "steps": self.steps,
            "exploratory_steps": self.exploratory_steps,
            "extra_terms": {"value_already_set": W_VALUE_ALREADY_SET,
                            "observed_no_effect": W_OBSERVED_NO_EFFECT},
        }
