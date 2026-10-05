"""Input masks for the contextual diagnostic's arms.

`docs/methodology/2026-10-04-contextual-diagnostic-preregistration.md` §3 defines four learned
arms that differ only in which inputs the Q-network sees. A mask is a 0/1 vector per input;
masked columns are zeroed at every forward pass (training and evaluation), inside the
network, so the replay buffer, the exploration guide and everything else see the full
features. `"none"` is the primary agent, unchanged.
"""

from __future__ import annotations

import numpy as np

from .features import (
    ACTION_DIM,
    CATEGORY_SLICE,
    LABEL_SLICE,
    ROLE_SLICE,
    STATE_DIM,
    TYPE_SLICE,
    flag_index,
    numeric_index,
)

MODES = ("none", "action_only", "lexical_only", "label_blind")

#: Intrinsic action descriptors `action_only` keeps beyond the type, label, role and value
#: category blocks (pre-registration §3, verbatim).
_ACTION_ONLY_FLAGS = ("navigational", "opens_new_tab", "has_href", "is_fixed_action",
                      "is_form_control", "is_repeatable_probe")
_ACTION_ONLY_NUMERIC = ("value_ordinal", "label_length", "anchor_commit_score", "lexical_commit_score")
#: The label channel: the embedding plus the three scalars derived from the label text.
_LABEL_NUMERIC = ("label_length", "anchor_commit_score", "lexical_commit_score")


def _ones(dim: int) -> np.ndarray:
    return np.ones(dim, dtype=np.float32)


def _label_columns() -> list[int]:
    return list(range(LABEL_SLICE.start, LABEL_SLICE.stop)) + [numeric_index(n) for n in _LABEL_NUMERIC]


def input_masks(mode: str) -> tuple[np.ndarray, np.ndarray] | None:
    """`(state_mask, action_mask)` for `mode`, or None for the unmasked primary agent."""
    if mode not in MODES:
        raise ValueError(f"input_mask must be one of {MODES}, got {mode!r}")
    if mode == "none":
        return None
    state, action = _ones(STATE_DIM), _ones(ACTION_DIM)
    if mode == "label_blind":
        action[_label_columns()] = 0.0
        return state, action
    state[:] = 0.0
    if mode == "lexical_only":
        action[:] = 0.0
        action[_label_columns()] = 1.0
        return state, action
    # action_only: keep the intrinsic descriptors, drop every state-relative column.
    action[:] = 0.0
    for block in (TYPE_SLICE, LABEL_SLICE, ROLE_SLICE, CATEGORY_SLICE):
        action[block] = 1.0
    for name in _ACTION_ONLY_FLAGS:
        action[flag_index(name)] = 1.0
    for name in _ACTION_ONLY_NUMERIC:
        action[numeric_index(name)] = 1.0
    return state, action
