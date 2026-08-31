"""Per-action feature vectors — the representation that replaces the action *index*.

**Why this module exists.** The flat Q-head scored a fixed 100-slot output, so slot 47
meant "click login" on one page and "type a boundary value" on the next. Two consequences
were measured rather than argued: the head could only memorise per page, and nothing it
learned could transfer to an application it had not seen. A web-testing agent is pointed
at a *new* application every time, so a representation that cannot transfer is not a
representation of the task.

The information needed to fix that was already being computed and thrown away. Dumped
live from `order-1.html` of the deep-flow fixture:

    [13] CLICK  id='Cancel order'  {'navigational':True,'in_viewport':True,'href':'index.html'}
    [18] SELECT id='product'       {'option':'folders-25','in_viewport':True}

Every action already carries its type, a human-readable label, and structural flags.
`FusionFeaturesExtractor` received none of it — only a count of how many actions existed,
as a 0/1 mask, which it then split off and discarded. This module turns each `ActionSpec`
into a vector describing *what the action is*, so a Q-function can learn statements like
"a newly-revealed CLICK labelled 'Continue' is valuable", which is true of every web
application rather than of one page of one fixture.

**The newly-revealed flag is the load-bearing feature.** Gated flows work by revealing a
control once their input is valid, so the set of available actions *grows* in response to
progress. Measured on the same fixture, diffing the action set across the gate-opening
SELECT:

    NEWLY REVEALED: [('CLICK', 'Continue')]
    disappeared   : []

That is an exact, free, domain-general progress signal: it is computed by comparing two
consecutive action lists, needs no answer key, and means the same thing on any site that
gates a flow. It is a feature here and a reward term in `reward.exploration`.

**Labels are hashed, not embedded, in this phase.** A signed character-trigram hash is
deterministic, costs nothing, needs no model loaded, and gives two things that matter
now: stable identity for the same control across visits, and partial lexical overlap
between related labels ("Continue" / "Continue »"). A sentence embedding would add
*semantic* similarity across vocabularies ("Continue" ~ "Next" ~ "Proceed"), which is
what cross-application transfer needs — that is a deliberate later step, and
`LabelEncoder` is the seam it will be swapped in at.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

from ..envs.types import MAX_ACTIONS, ActionSpec, ActionType

# Width of the hashed-label block.
LABEL_DIM = 32

# One-hot over ActionType.
_ACTION_TYPES: tuple[ActionType, ...] = tuple(ActionType)
TYPE_DIM = len(_ACTION_TYPES)

# Hand-specified scalars. Each is a property of the action or of its history at the
# current state, and none of them references an action *index*.
SCALAR_FIELDS: tuple[str, ...] = (
    "navigational",        # clicking it is expected to change the URL
    "in_viewport",         # currently on screen
    "opens_new_tab",       # target="_blank"
    "is_newly_revealed",   # appeared in the action set since the previous step
    "has_href",            # carries a link target at all
    "is_fixed_action",     # page-independent (no-op/scroll/resize/back/forward/refresh)
    "taken_here",          # log-scaled count of this action at this state, whole run
    "taken_this_episode",  # log-scaled count of this action in this episode
    "value_ordinal",       # TYPE value category, 0..1; 0 for other action types
    "is_form_control",     # TYPE/SELECT — advances a form rather than navigating
)
SCALAR_DIM = len(SCALAR_FIELDS)

ACTION_FEATURE_DIM = TYPE_DIM + LABEL_DIM + SCALAR_DIM

# Ordinal for TYPE actions, so "a valid value" and "a malformed value" are distinguishable
# without one-hotting a category that only applies to one action type.
_VALUE_ORDINAL = {
    "valid_typical": 1.0,
    "boundary_min": 0.6,
    "boundary_max": 0.7,
    "empty_string": 0.3,
    "type_mismatch": 0.1,
}

_FIXED_TYPES = frozenset({
    ActionType.NO_OP, ActionType.SCROLL, ActionType.RESIZE_VIEWPORT,
    ActionType.BROWSER_BACK, ActionType.BROWSER_FORWARD, ActionType.REFRESH,
})

_FORM_TYPES = frozenset({ActionType.TYPE, ActionType.SELECT})


def action_identity(spec: ActionSpec) -> tuple[str, str]:
    """What counts as "the same action" across steps and across visits to a state.

    Deliberately identical in shape to `ExplorationTracker._action_identity`: the counts
    used as features here and the repetition penalty used in the reward must agree on
    what a repeat is, or the agent is being charged for one thing and shown another.
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


class LabelEncoder:
    """Signed character-trigram hashing of a control's visible label.

    Deterministic, allocation-light, and model-free. Signed hashing (a second hash bit
    decides the sign) keeps colliding trigrams from systematically inflating a bucket,
    which is the standard reason the hashing trick uses it.

    The vector is L2-normalized because every other block reaching this network is —
    `HashEmbeddingEncoder` emits unit vectors and the real encoders were explicitly
    normalized after an unnormalized CLIP block was measured to outweigh every other
    modality by an order of magnitude.
    """

    def __init__(self, dim: int = LABEL_DIM) -> None:
        self.dim = dim
        self._cache: dict[str, np.ndarray] = {}

    def encode(self, label: str) -> np.ndarray:
        text = (label or "").strip().lower()
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        vector = np.zeros(self.dim, dtype=np.float32)
        if text:
            padded = f"  {text}  "
            for i in range(len(padded) - 2):
                digest = hashlib.blake2b(padded[i : i + 3].encode("utf-8"), digest_size=8).digest()
                value = int.from_bytes(digest, "little")
                vector[value % self.dim] += 1.0 if (value >> 63) & 1 else -1.0
            norm = float(np.linalg.norm(vector))
            if norm:
                vector /= norm
        # Bounded so a long-running process cannot accumulate one entry per distinct
        # label on a large site.
        if len(self._cache) < 4096:
            self._cache[text] = vector
        return vector


@dataclass
class ActionFeatureExtractor:
    """Builds the `(MAX_ACTIONS, ACTION_FEATURE_DIM)` block for one observation.

    Owns the visit counts that two of the scalar features report. They live here rather
    than in the env because they describe *the agent's* history with an action, are
    needed by the policy at inference time, and must be identical on the training and
    evaluation paths — the same reason `encode_modalities` is a free function shared by
    both.
    """

    labels: LabelEncoder = field(default_factory=LabelEncoder)
    # (state_key, action_identity) -> times taken, across the whole run.
    _run_counts: dict[tuple[str, tuple[str, str]], int] = field(default_factory=dict, init=False)
    _episode_counts: dict[tuple[str, tuple[str, str]], int] = field(default_factory=dict, init=False)
    # Action identities present at the previous step, for the newly-revealed flag.
    _previous_identities: set[tuple[str, str]] = field(default_factory=set, init=False)
    _previous_url: str = field(default="", init=False)

    def start_episode(self) -> None:
        self._episode_counts = {}
        # Nothing is "newly revealed" on the first observation of an episode: the whole
        # action set is new, and paying attention to that would mark every action on
        # every landing page as a gate opening.
        self._previous_identities = set()
        self._previous_url = ""

    def observe_action_set(self, specs: list[ActionSpec], url: str = "") -> set[tuple[str, str]]:
        """Record the current action set and return the identities a gate revealed.

        Mirrors `ExplorationTracker.observe_action_set` exactly — same three conditions,
        same semantics. In the live pipeline the env computes this once and the features
        read it from `info`, so the two can never disagree; this copy exists for callers
        that hold an extractor directly, and it must not drift from the env's definition.
        """
        identities = {action_identity(spec) for spec in specs}
        previous, previous_url = self._previous_identities, self._previous_url
        self._previous_identities = identities
        self._previous_url = url
        if not previous or url != previous_url:
            return set()
        if len(identities) <= len(previous):
            return set()
        return identities - previous

    def record_taken(self, state_key: str, spec: ActionSpec) -> None:
        key = (state_key, action_identity(spec))
        self._run_counts[key] = self._run_counts.get(key, 0) + 1
        self._episode_counts[key] = self._episode_counts.get(key, 0) + 1

    def visit_count(self, state_key: str, spec: ActionSpec) -> int:
        return self._run_counts.get((state_key, action_identity(spec)), 0)

    def encode(
        self,
        specs: list[ActionSpec],
        state_key: str = "",
        newly_revealed: set[tuple[str, str]] | None = None,
    ) -> np.ndarray:
        """`(MAX_ACTIONS, ACTION_FEATURE_DIM)`, zero-padded past the valid actions.

        Padding rows are all-zero and are masked out of the Q-values anyway; keeping
        them zero rather than arbitrary means a stale padding row can never look like a
        plausible action if a masking bug is ever introduced.
        """
        revealed = newly_revealed if newly_revealed is not None else set()
        block = np.zeros((MAX_ACTIONS, ACTION_FEATURE_DIM), dtype=np.float32)
        for row, spec in enumerate(specs[:MAX_ACTIONS]):
            block[row] = self._encode_one(spec, state_key, revealed)
        return block

    def _encode_one(
        self, spec: ActionSpec, state_key: str, revealed: set[tuple[str, str]]
    ) -> np.ndarray:
        identity = action_identity(spec)
        params = spec.params or {}

        type_block = np.zeros(TYPE_DIM, dtype=np.float32)
        type_block[_ACTION_TYPES.index(spec.action_type)] = 1.0

        label_block = self.labels.encode(spec.element_id or spec.description)

        run_count = self._run_counts.get((state_key, identity), 0)
        episode_count = self._episode_counts.get((state_key, identity), 0)
        scalars = np.array(
            [
                1.0 if params.get("navigational") else 0.0,
                1.0 if params.get("in_viewport") else 0.0,
                1.0 if params.get("opens_new_tab") else 0.0,
                1.0 if identity in revealed else 0.0,
                1.0 if params.get("href") else 0.0,
                1.0 if spec.action_type in _FIXED_TYPES else 0.0,
                # log1p-scaled and squashed: the difference between 0 and 1 visits
                # matters far more than between 20 and 21, and the network needs a
                # bounded input either way.
                min(np.log1p(run_count) / 4.0, 1.0),
                min(np.log1p(episode_count) / 3.0, 1.0),
                _VALUE_ORDINAL.get(str(params.get("category", "")), 0.0),
                1.0 if spec.action_type in _FORM_TYPES else 0.0,
            ],
            dtype=np.float32,
        )
        return np.concatenate([type_block, label_block, scalars])
