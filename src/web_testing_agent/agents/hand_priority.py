"""Arm A of the search ladder: search with a hand-designed action priority, no learning.

**The ladder this completes.** Three arms now share one environment, one observation, one
action space and mask, one reward, one archive and one evaluation protocol, and differ
only in how an action is scored:

    A  this module          score(a) = w . phi(a)          w is hand-designed, FIXED
    B  ContextualBandit     score(a) = f_theta(s, phi(a))  theta regressed onto r_t
    C  ActionConditionedDQN score(a) = Q_theta(s, phi(a))  theta regressed onto
                                                           r_t + gamma^n max_a' Q_target

so A -> B isolates *learning an immediate action value at all*, and B -> C isolates
*bootstrapping a sequential one*. Nothing else moves between the rows.

**phi is not a second representation — it is the same one.** This policy owns an
`ActionFeatureExtractor` and scores the vector it emits, so the features a hand weight is
placed on are bit-identical to the features the two networks receive. A parallel
"search features" module would have made A-vs-B a comparison of two representations as
well as of two scoring rules, which is exactly the confound a controlled ladder exists to
remove.

**The label block is weighted zero, and that is the anti-cheating guarantee.** `phi` is
`[type one-hot (10) | hashed label (32) | scalars (10)]`. `HAND_PRIORITY_WEIGHTS` assigns
weights to the type block and the scalar block and leaves all 32 label dimensions at
exactly 0.0. The policy is therefore *provably invariant* to renaming every control on the
site: it cannot recognise "Place order", "Continue", "Start order" or any other string,
because the only dimensions that carry a string are multiplied by zero.
`test_hand_priority.py` asserts both the zeros and the invariance under a relabelling.

**What the heuristic actually says**, in words, in descending strength:

1. *Take a control that just appeared.* A gated flow reveals its next control once its
   input is valid, so the action set grows in response to progress. This is the repository's
   own domain-general progress signal — it is already a reward term (`reveal_bonus`, the
   largest one at 1.5) and already an observation feature both learned arms receive.
2. *Fill a form before trying to leave the page*, and among the generated values prefer a
   plausible one over a boundary or malformed one (`value_ordinal`).
3. *Prefer a control expected to change the URL*, weakly, over one that is not.
4. *Prefer what is on screen*, mirroring the action registry's own viewport-first priority.
5. *Avoid page-independent controls* (NO_OP, scroll, resize, back, forward, refresh): none
   of them can advance an application flow.
6. *Avoid what has already been tried here* — strongly within the episode, less strongly
   across the run. **This term is what makes it a search rather than a greedy loop**: with
   it, a deterministic argmax enumerates a state's alternatives instead of hammering one.

Ties are broken by lowest action index, which is not arbitrary: the index order *is*
`action_registry.build_action_specs`'s existing hand priority (on-screen before off-screen,
then CLICK, TYPE(valid_typical), SELECT, RAPID_CLICK, TYPE(edge cases), in DOM order). So
the tie-break reuses the repository's generic ordering rather than inventing a second one.

**A known and deliberate asymmetry, recorded here because it favours this arm.** Two of
the ten scalars — `taken_here` and `taken_this_episode` — are written only by
`ActionFeatureExtractor.record_taken()`, which nothing on the RL path calls (see
`scripts/probe_action_representation.py`). They are therefore identically 0.0 in every
observation Arms B and C have ever seen, while this policy calls `record_taken()` and reads
them live. Removing them would not level the comparison, it would delete the search: a
memoryless deterministic argmax repeats one action until the episode truncates. The right
response is to report the asymmetry, which the experiment's report does, not to hide it by
crippling the arm.

**Nothing here learns.** There is no optimiser, no buffer, no target, no gradient. `act()`
mutates exactly one thing — the visit counters — and `reset()` clears the episodic half.
"""

from __future__ import annotations

import numpy as np

from ..envs.types import MAX_ACTIONS, ActionSpec, ActionType
from ..utils.logging import get_logger
from .action_features import (
    ACTION_FEATURE_DIM,
    LABEL_DIM,
    SCALAR_FIELDS,
    TYPE_DIM,
    ActionFeatureExtractor,
)

logger = get_logger(__name__)

ALGORITHM = "hand_priority_search"

# Where each block of `phi(a)` starts. Mirrors `ActionFeatureExtractor._encode_one`'s
# concatenation order; `test_hand_priority.py` pins that they still agree.
_LABEL_START = TYPE_DIM
_SCALAR_START = TYPE_DIM + LABEL_DIM

# The order `_encode_one` one-hots over. Declared rather than imported from the private
# name in `action_features`, and verified against the live encoder by a test, so this
# module does not reach into another one's internals to stay correct.
_TYPE_ORDER: tuple[ActionType, ...] = tuple(ActionType)

# -- the hand priority ---------------------------------------------------------------
#
# Every entry below is a statement about web applications in general. None of them
# mentions a label, a URL, a page, a product, a quantity or a fixture, and none of them
# can: the only feature block that could carry such a thing is the hashed label, and it is
# weighted zero.

#: Weight on each named scalar of `ActionFeatureExtractor.SCALAR_FIELDS`.
HAND_PRIORITY_WEIGHTS: dict[str, float] = {
    # A control that appeared in response to the previous action. The single strongest
    # term, because on any gated flow it is the only feature that is genuinely correlated
    # with depth rather than with breadth.
    "is_newly_revealed": 4.0,
    # TYPE/SELECT. A form has to be filled before submitting it is worth anything.
    "is_form_control": 1.5,
    # Among the five generated values for a field, prefer the plausible one:
    # valid_typical 1.0 > boundary_max 0.7 > boundary_min 0.6 > empty 0.3 > mismatch 0.1.
    "value_ordinal": 1.0,
    # Expected to change the URL, i.e. to make forward progress through the application.
    "navigational": 0.6,
    # On screen. Mirrors the action registry's own viewport-first allocation.
    "in_viewport": 0.3,
    # Carries a link target at all. Deliberately weak: it is nearly implied by
    # `navigational` and is here only to separate a link from a bare button on a tie.
    "has_href": 0.1,
    # A popup leaves the flow the agent is in, and the harness has to adopt or discard it.
    "opens_new_tab": -1.0,
    # NO_OP / SCROLL / RESIZE_VIEWPORT / BROWSER_BACK / BROWSER_FORWARD / REFRESH. None of
    # them can advance an application flow; all of them cost a step.
    "is_fixed_action": -2.0,
    # Already taken at this state **this episode**. Both visit features are log1p-scaled
    # into [0, 1] by the extractor, so -6.0 costs a once-taken action about 1.39 — enough
    # to lose a tie against an untried sibling, not enough to outrank a freshly revealed
    # control at +4.0. That ordering is the intended one.
    "taken_this_episode": -6.0,
    # Already taken at this state **across the whole run**. Weaker, so a route that has to
    # be walked again is not forbidden, only discouraged once alternatives exist.
    "taken_here": -3.0,
}

#: Weight on the action-type one-hot. Small relative to the scalars: type is already
#: mostly expressed by `is_form_control` and `is_fixed_action`, and this only settles
#: what to reach for among controls those two cannot separate.
HAND_PRIORITY_TYPE_WEIGHTS: dict[ActionType, float] = {
    ActionType.CLICK: 0.3,
    ActionType.SELECT: 0.3,
    ActionType.TYPE: 0.3,
    # A burst of clicks is a robustness probe, not a way to advance a flow.
    ActionType.RAPID_CLICK: -0.5,
    ActionType.NO_OP: -1.0,
    ActionType.SCROLL: 0.0,
    ActionType.RESIZE_VIEWPORT: -0.5,
    ActionType.BROWSER_BACK: 0.0,
    ActionType.BROWSER_FORWARD: -0.5,
    ActionType.REFRESH: -0.5,
}


def build_weight_vector(
    scalars: dict[str, float] | None = None,
    types: dict[ActionType, float] | None = None,
) -> np.ndarray:
    """Assemble `w`, shaped exactly like one row of the action-feature block.

    The label block is never written, so it stays at the zeros `np.zeros` put there. That
    is the whole anti-cheating argument, and it is enforced by construction rather than by
    review: there is no code path in this function that could give a label dimension a
    non-zero weight.
    """
    scalar_weights = HAND_PRIORITY_WEIGHTS if scalars is None else scalars
    type_weights = HAND_PRIORITY_TYPE_WEIGHTS if types is None else types

    unknown = set(scalar_weights) - set(SCALAR_FIELDS)
    if unknown:
        raise ValueError(f"unknown scalar feature(s): {sorted(unknown)}")

    weights = np.zeros(ACTION_FEATURE_DIM, dtype=np.float32)
    for position, action_type in enumerate(_TYPE_ORDER):
        weights[position] = type_weights.get(action_type, 0.0)
    for offset, name in enumerate(SCALAR_FIELDS):
        weights[_SCALAR_START + offset] = scalar_weights.get(name, 0.0)
    return weights


class HandPriorityPolicy:
    """Non-learning search policy: argmax of a fixed linear score over `phi(a)`.

    Implements `evaluation.rollout.Policy`, so it is scored by the same `run_rollout` and
    the same `flow_metrics` as every trained arm, and it can drive the same
    `ArchiveStartWrapper` the trained arms explore through.

    `epsilon` is uniform over the **valid** slots only, matching
    `compare_agents.TrainedPolicy(masked=True)` exactly — the arms must explore over the
    same support or the comparison is between two different action spaces.

    `carry_counts_across_episodes` selects which of two honest readings of this arm is
    being measured, and both are reported:

    * ``False`` — the extractor is cleared on every `reset()`, so each episode is an
      independent draw from one stationary policy. This is the reading that matches how a
      trained checkpoint is evaluated, and it is the one the primary comparison uses.
    * ``True`` — run-level counts persist across episodes, so the 25 evaluation episodes
      are one search split into 25 restarts. This is the reading that matches what a search
      algorithm *is*, and it is reported alongside rather than instead.

    `label_prior` is how arms **A1 and A2** are built, and it is the only thing that
    separates them from A0. It is a callable `label -> float` whose value is added at
    `label_prior_weight` (1.2 for both, frozen) to the linear score. The default `None` is
    A0 exactly: no extra term is computed, no branch is taken, and the scores are the same
    floats they were before the parameter existed. See `agents/label_priors.py` for the two
    frozen priors and for why the label block staying weighted zero is still the right
    anti-cheating guarantee — a prior reads the label string off the `ActionSpec`, which is
    the same text the zeroed block hashes, so A0 -> A1 -> A2 varies *access to the label*
    and nothing else.
    """

    def __init__(
        self,
        *,
        epsilon: float = 0.0,
        seed: int | None = None,
        weights: np.ndarray | None = None,
        carry_counts_across_episodes: bool = False,
        label_prior=None,
        label_prior_weight: float = 0.0,
    ) -> None:
        self.algorithm = ALGORITHM
        self.epsilon = float(epsilon)
        self.weights = build_weight_vector() if weights is None else np.asarray(weights, np.float32)
        if self.weights.shape != (ACTION_FEATURE_DIM,):
            raise ValueError(
                f"weights must be ({ACTION_FEATURE_DIM},), got {self.weights.shape}"
            )
        if np.any(self.weights[_LABEL_START:_SCALAR_START] != 0.0):
            raise ValueError(
                "the hashed-label block must be weighted zero: a non-zero weight there "
                "would let this policy respond to what a control is called."
            )
        self.carry_counts_across_episodes = carry_counts_across_episodes
        # A1/A2's only addition to A0. `None` is A0 itself and is the default, so every
        # existing caller keeps bit-identical behaviour; `test_hand_priority.py` pins that
        # a default-constructed policy scores exactly as it did before this parameter
        # existed. The prior reads the label *string* off the `ActionSpec` rather than any
        # dimension of `phi`, so the hashed-label block stays weighted zero and the
        # constructor's anti-cheating assertion below remains exactly as strong.
        self.label_prior = label_prior
        self.label_prior_weight = float(label_prior_weight) if label_prior is not None else 0.0
        if label_prior is not None and not self.label_prior_weight:
            raise ValueError("a label_prior with weight 0.0 is A0 with a slower scorer; "
                             "pass label_prior_weight explicitly")
        self.features = ActionFeatureExtractor()
        self._rng = np.random.default_rng(seed)
        # Diagnostics, so a run can be audited without re-deriving it from the trace.
        self.steps = 0
        self.exploratory_steps = 0
        self.ties_broken = 0

    # -- Policy protocol ------------------------------------------------------------

    def reset(self) -> None:
        if self.carry_counts_across_episodes:
            # Clears only the episodic half; `_run_counts` is deliberately kept.
            self.features.start_episode()
        else:
            self.features = ActionFeatureExtractor()

    def act(self, observation: dict, info: dict) -> int:  # noqa: ARG002 - Policy signature
        """Choose a valid slot. The observation is unused: this policy reads no pixels.

        Deliberately scored from `info["action_specs"]` and `info["newly_revealed"]` rather
        than from the 6973-dim encoded vector. Those are the *inputs* the action-feature
        block is built from, and the env is the single place that computes them — so the
        features scored here and the features the networks are shown come from one
        definition, not two.
        """
        specs: list[ActionSpec] = list(info.get("action_specs") or [])
        if not specs:
            return 0

        self.steps += 1
        valid = min(len(specs), MAX_ACTIONS)

        if self.epsilon and self._rng.random() < self.epsilon:
            self.exploratory_steps += 1
            choice = int(self._rng.integers(0, valid))
        else:
            revealed = {tuple(entry) for entry in (info.get("newly_revealed") or [])}
            block = self.features.encode(specs, info.get("state_key", ""), revealed)
            scores = block[:valid] @ self.weights
            if self.label_prior is not None:
                # `element_id or description` is the same label expression
                # `ActionFeatureExtractor._encode_one` hashes, so A1/A2 read exactly the
                # string A0's zeroed label block refuses to read -- not a second, richer
                # source of text that would make this a comparison of two inputs.
                scores = scores + self.label_prior_weight * np.array(
                    [self.label_prior(s.element_id or s.description) for s in specs[:valid]],
                    dtype=np.float32)
            best = float(scores.max())
            # `argmax` already returns the lowest index among ties, which is the action
            # registry's own priority order. Counted only so the report can say how often
            # the heuristic was actually indifferent.
            if int(np.sum(scores >= best - 1e-9)) > 1:
                self.ties_broken += 1
            choice = int(np.argmax(scores))

        self.features.record_taken(info.get("state_key", ""), specs[choice])
        return choice

    # -- reporting ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """What this policy is, in the terms the comparison report needs."""
        prior = self.label_prior
        return {
            "algorithm": self.algorithm,
            "learns": False,
            "epsilon": self.epsilon,
            "carry_counts_across_episodes": self.carry_counts_across_episodes,
            "label_prior": None if prior is None else (
                prior.to_dict() if hasattr(prior, "to_dict") else repr(prior)),
            "label_prior_weight": self.label_prior_weight,
            "scalar_weights": dict(HAND_PRIORITY_WEIGHTS),
            "type_weights": {t.value: w for t, w in HAND_PRIORITY_TYPE_WEIGHTS.items()},
            "label_block_weight_l1": float(
                np.abs(self.weights[_LABEL_START:_SCALAR_START]).sum()
            ),
            "tie_break": "lowest action index (action_registry's own priority order)",
            "steps": self.steps,
            "exploratory_steps": self.exploratory_steps,
            "ties_broken": self.ties_broken,
        }
