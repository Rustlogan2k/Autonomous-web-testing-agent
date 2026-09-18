"""Action representation v2: semantics for learning, identity kept out of the policy.

**The architectural rule this file implements.** Identity is for counting; semantics are
for learning. Everything the policy is shown here describes *what an action is* in terms
that mean the same thing on an application it has never seen. Nothing here identifies
*which* application, page or element it is — not a URL, not a DOM id, not an element
index, not a slot number, and not a hashed label.

**What changed from `action_features.ActionFeatureExtractor`, and why.**

The v1 block is `[type one-hot (10) | hashed label (32) | scalars (10)] = 52`. Its label
block is a signed character-trigram hash, and `reports/probe_semantic_vs_hash.md` measured
what that buys: cross-vocabulary synonyms sit **0.24 random SDs** above chance. A policy
cannot learn "a control that commits a workflow is worth taking" from that, because it
cannot tell that `Place order` and `Submit purchase` are the same kind of control. Worse,
the hash scores `Place order` against `Cancel order` at **+0.456** — it actively asserts
that committing and abandoning a flow are similar, because they share a word.

v2 replaces that block with a frozen sentence embedding projected to 48 dimensions, which
puts the same synonyms **3.85 random SDs** above chance on vocabulary the projection was
never fitted on. The rest of the vector is widened from ten hand-named scalars into
explicit type, role, structural and numeric blocks.

**v1 is not modified and not removed.** Every published figure in this repository was
measured through it, and `AC_OBS_DIM` is baked into sixteen checkpoints. v2 is a parallel
representation selected per run; the two never have to agree.

    layout                                    width
    ------------------------------------------------
    action type one-hot                          10
    semantic label (frozen, projected)           48
    element role one-hot                         10
    structural / interaction flags                8
    normalized numeric features                   4
    ------------------------------------------------
    SEMANTIC_ACTION_FEATURE_DIM                  80

**On `taken_here` / `taken_this_episode`.** These are carried, and they are the one place
where "identity for counting" touches the policy input — but what crosses is a *count*,
not an identity: how often this agent has taken this action here, not which action it was.
A count generalises (every application has repeated actions); an identity does not. They
are written only by `record_taken()`, exactly as in v1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..envs.types import MAX_ACTIONS, ActionSpec, ActionType
from .semantic_labels import LABEL_DIM, SemanticLabelEncoder

# -- block widths ---------------------------------------------------------------------

_ACTION_TYPES: tuple[ActionType, ...] = tuple(ActionType)
TYPE_DIM = len(_ACTION_TYPES)

#: What kind of control this is, derived from the action and its parameters.
#:
#: **Coarser than a DOM tag on purpose.** `ActionSpec` does not persist the element's tag
#: or input type -- `build_action_specs` discards them after building the spec -- and
#: adding them would change `action_registry`, which every existing arm shares. The roles
#: below are exactly what is recoverable from an `ActionSpec` today. Finer roles
#: (email vs number vs date input, checkbox vs radio) need a registry change, and that is
#: deliberately deferred rather than smuggled in beside a representation change.
ROLES: tuple[str, ...] = (
    "link",             # CLICK carrying an href
    "submit_control",   # CLICK expected to change the URL without an href (submit button)
    "button",           # CLICK with neither
    "select",           # SELECT
    "text_entry",       # TYPE
    "rapid_click",      # RAPID_CLICK
    "navigation_move",  # BROWSER_BACK / BROWSER_FORWARD
    "page_reload",      # REFRESH
    "viewport",         # SCROLL / RESIZE_VIEWPORT
    "no_op",            # NO_OP
)
ROLE_DIM = len(ROLES)
_ROLE_INDEX = {role: index for index, role in enumerate(ROLES)}

#: Structural and interaction properties. Every one is a statement about the action that
#: is true or false on any web application.
FLAG_FIELDS: tuple[str, ...] = (
    "navigational",        # expected to change the URL
    "in_viewport",         # currently on screen
    "opens_new_tab",       # target="_blank"
    "is_newly_revealed",   # appeared in the action set as a result of the last step
    "has_href",            # carries a link target at all
    "is_fixed_action",     # page-independent (no-op/scroll/resize/history/refresh)
    "is_form_control",     # TYPE/SELECT -- advances a form rather than navigating
    "is_repeatable_probe", # RAPID_CLICK -- a robustness probe, not a flow step
)
FLAG_DIM = len(FLAG_FIELDS)

#: Bounded numeric features, all in [0, 1].
NUMERIC_FIELDS: tuple[str, ...] = (
    "value_ordinal",       # TYPE value plausibility: valid 1.0 ... malformed 0.1
    "label_length",        # log-scaled; a bare icon and a sentence are different controls
    "taken_here",          # log-scaled count of this action at this state, whole run
    "taken_this_episode",  # log-scaled count of this action at this state, this episode
)
NUMERIC_DIM = len(NUMERIC_FIELDS)

SEMANTIC_ACTION_FEATURE_DIM = TYPE_DIM + LABEL_DIM + ROLE_DIM + FLAG_DIM + NUMERIC_DIM

#: Slice boundaries, exported so tests and diagnostics can address blocks by name rather
#: than by a magic number that drifts.
TYPE_SLICE = slice(0, TYPE_DIM)
LABEL_SLICE = slice(TYPE_DIM, TYPE_DIM + LABEL_DIM)
ROLE_SLICE = slice(LABEL_SLICE.stop, LABEL_SLICE.stop + ROLE_DIM)
FLAG_SLICE = slice(ROLE_SLICE.stop, ROLE_SLICE.stop + FLAG_DIM)
NUMERIC_SLICE = slice(FLAG_SLICE.stop, FLAG_SLICE.stop + NUMERIC_DIM)

_VALUE_ORDINAL = {
    "valid_typical": 1.0,
    "boundary_max": 0.7,
    "boundary_min": 0.6,
    "empty_string": 0.3,
    "type_mismatch": 0.1,
}

_FIXED_TYPES = frozenset({
    ActionType.NO_OP, ActionType.SCROLL, ActionType.RESIZE_VIEWPORT,
    ActionType.BROWSER_BACK, ActionType.BROWSER_FORWARD, ActionType.REFRESH,
})
_FORM_TYPES = frozenset({ActionType.TYPE, ActionType.SELECT})


def action_identity(spec: ActionSpec) -> tuple[str, str]:
    """What counts as "the same action" for counting purposes.

    Identical to `action_features.action_identity` and to
    `ExplorationTracker._action_identity`, deliberately: the counts fed to the policy, the
    repetition penalty in the reward and the archive's notion of a repeat must agree, or
    the agent is charged for one thing and shown another. **This is identity, and it never
    reaches the policy** -- only counts derived from it do.
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


def role_of(spec: ActionSpec) -> str:
    """Which of `ROLES` this action is. Derived only from the spec and its params."""
    action_type = spec.action_type
    params = spec.params or {}
    if action_type is ActionType.CLICK:
        if params.get("href"):
            return "link"
        # A submit button carries no href but is expected to change the URL; the registry
        # marks exactly that case navigational.
        return "submit_control" if params.get("navigational") else "button"
    if action_type is ActionType.SELECT:
        return "select"
    if action_type is ActionType.TYPE:
        return "text_entry"
    if action_type is ActionType.RAPID_CLICK:
        return "rapid_click"
    if action_type in (ActionType.BROWSER_BACK, ActionType.BROWSER_FORWARD):
        return "navigation_move"
    if action_type is ActionType.REFRESH:
        return "page_reload"
    if action_type in (ActionType.SCROLL, ActionType.RESIZE_VIEWPORT):
        return "viewport"
    return "no_op"


def label_of(spec: ActionSpec) -> str:
    """The human-readable text the semantic encoder reads.

    `element_id` is the visible label the action registry captured (`"Place order"`), and
    `description` is the generated fallback (`"scroll down"`). A CSS selector is neither
    -- it is identity -- so it is refused rather than embedded: encoding
    `[id="nav-home"]` would put a DOM id into the policy through the semantic channel,
    which is the one thing this representation exists to prevent.
    """
    label = (spec.element_id or "").strip()
    if label and not label.startswith(("[id=", "[name=", "#", ".")) and ">>" not in label:
        return label
    return (spec.description or "").strip()


@dataclass
class SemanticActionFeatureExtractor:
    """Builds the `(MAX_ACTIONS, SEMANTIC_ACTION_FEATURE_DIM)` block for one observation.

    Same interface as `action_features.ActionFeatureExtractor` -- `start_episode`,
    `observe_action_set`, `record_taken`, `visit_count`, `encode` -- so the two are
    interchangeable at every call site that already exists.
    """

    labels: SemanticLabelEncoder = field(default_factory=SemanticLabelEncoder)
    _run_counts: dict[tuple[str, tuple[str, str]], int] = field(default_factory=dict, init=False)
    _episode_counts: dict[tuple[str, tuple[str, str]], int] = field(default_factory=dict, init=False)
    _previous_identities: set[tuple[str, str]] = field(default_factory=set, init=False)
    _previous_url: str = field(default="", init=False)

    # -- episode bookkeeping --------------------------------------------------------

    def start_episode(self) -> None:
        self._episode_counts = {}
        # Nothing is newly revealed on an episode's first observation: the whole action
        # set is new then, and treating that as a gate opening would pay every landing
        # page the full progress signal.
        self._previous_identities = set()
        self._previous_url = ""

    def observe_action_set(self, specs: list[ActionSpec], url: str = "") -> set[tuple[str, str]]:
        """Record the action set and return the identities a gate revealed.

        Mirrors `ExplorationTracker.observe_action_set` exactly -- same three conditions,
        same semantics. In the live pipeline the env computes this once and the features
        read it from `info`, so the two cannot disagree; this copy serves callers holding
        an extractor directly and must not drift from the env's definition.
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

    # -- encoding -------------------------------------------------------------------

    def warm(self, specs: list[ActionSpec]) -> None:
        """Pre-encode a page's labels in one batch, so stepping does not pay per label."""
        self.labels.warm([label_of(spec) for spec in specs])

    def encode(
        self,
        specs: list[ActionSpec],
        state_key: str = "",
        newly_revealed: set[tuple[str, str]] | None = None,
    ) -> np.ndarray:
        """`(MAX_ACTIONS, SEMANTIC_ACTION_FEATURE_DIM)`, zero-padded past the valid actions.

        Padding rows are all-zero and are masked out of the Q-values anyway; keeping them
        zero means a stale padding row can never look like a plausible action if a masking
        bug is introduced.
        """
        revealed = newly_revealed if newly_revealed is not None else set()
        block = np.zeros((MAX_ACTIONS, SEMANTIC_ACTION_FEATURE_DIM), dtype=np.float32)
        visible = specs[:MAX_ACTIONS]
        if not visible:
            return block

        # One batched encode for the whole page rather than one call per action: the
        # encoder is an order of magnitude faster batched, and a page has tens of controls.
        label_vectors = self.labels.encode_batch([label_of(spec) for spec in visible])

        for row, (spec, label_vector) in enumerate(zip(visible, label_vectors, strict=True)):
            block[row] = self._encode_one(spec, state_key, revealed, label_vector)
        return block

    def _encode_one(self, spec: ActionSpec, state_key: str,
                    revealed: set[tuple[str, str]], label_vector: np.ndarray) -> np.ndarray:
        identity = action_identity(spec)
        params = spec.params or {}
        vector = np.zeros(SEMANTIC_ACTION_FEATURE_DIM, dtype=np.float32)

        vector[TYPE_SLICE][_ACTION_TYPES.index(spec.action_type)] = 1.0
        vector[LABEL_SLICE] = label_vector
        vector[ROLE_SLICE][_ROLE_INDEX[role_of(spec)]] = 1.0

        vector[FLAG_SLICE] = np.array([
            1.0 if params.get("navigational") else 0.0,
            1.0 if params.get("in_viewport") else 0.0,
            1.0 if params.get("opens_new_tab") else 0.0,
            1.0 if identity in revealed else 0.0,
            1.0 if params.get("href") else 0.0,
            1.0 if spec.action_type in _FIXED_TYPES else 0.0,
            1.0 if spec.action_type in _FORM_TYPES else 0.0,
            1.0 if spec.action_type is ActionType.RAPID_CLICK else 0.0,
        ], dtype=np.float32)

        run_count = self._run_counts.get((state_key, identity), 0)
        episode_count = self._episode_counts.get((state_key, identity), 0)
        vector[NUMERIC_SLICE] = np.array([
            _VALUE_ORDINAL.get(str(params.get("category", "")), 0.0),
            # log-scaled and bounded: a 4-character label and a 40-character one differ,
            # a 40 and a 44 do not.
            min(np.log1p(len(label_of(spec))) / 4.0, 1.0),
            # Same log1p/4 and log1p/3 convention v1 uses, so a count means the same thing
            # in both representations and in `_episode_context`.
            min(np.log1p(run_count) / 4.0, 1.0),
            min(np.log1p(episode_count) / 3.0, 1.0),
        ], dtype=np.float32)
        return vector


def describe_layout() -> dict:
    """The block layout, for reports and for anything that needs to slice the vector."""
    return {
        "total_dim": SEMANTIC_ACTION_FEATURE_DIM,
        "blocks": [
            {"name": "action_type", "dim": TYPE_DIM,
             "start": TYPE_SLICE.start, "stop": TYPE_SLICE.stop,
             "fields": [t.value for t in _ACTION_TYPES]},
            {"name": "semantic_label", "dim": LABEL_DIM,
             "start": LABEL_SLICE.start, "stop": LABEL_SLICE.stop,
             "fields": ["frozen all-MiniLM-L6-v2, PCA-projected, unit norm"]},
            {"name": "element_role", "dim": ROLE_DIM,
             "start": ROLE_SLICE.start, "stop": ROLE_SLICE.stop, "fields": list(ROLES)},
            {"name": "flags", "dim": FLAG_DIM,
             "start": FLAG_SLICE.start, "stop": FLAG_SLICE.stop, "fields": list(FLAG_FIELDS)},
            {"name": "numeric", "dim": NUMERIC_DIM,
             "start": NUMERIC_SLICE.start, "stop": NUMERIC_SLICE.stop,
             "fields": list(NUMERIC_FIELDS)},
        ],
        "excluded_by_design": [
            "hashed label", "URL or URL hash", "DOM id or id hash", "CSS selector",
            "element index", "action slot index", "application identifier",
        ],
    }
