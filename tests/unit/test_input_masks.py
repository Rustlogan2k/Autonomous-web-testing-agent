"""The contextual diagnostic's input masks (`rl/masks.py`).

The default must leave the primary agent bit-for-bit unchanged; a masked column must have
no effect on Q in the online network, the target and the adaptation residual alike.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch as th

from web_testing_agent.rl import ACTION_DIM, STATE_DIM, AgentConfig, QAgent, TransferQNetwork
from web_testing_agent.rl.features import (
    ACTION_DIM as FEATURE_ACTION_DIM,
)
from web_testing_agent.rl.features import (
    CATEGORY_SLICE,
    FLAG_FIELDS,
    LABEL_SLICE,
    NUMERIC_FIELDS,
    ROLE_SLICE,
    TYPE_SLICE,
    flag_index,
    numeric_index,
)
from web_testing_agent.rl.masks import MODES, input_masks

from .test_transfer_rl import FakeLabels

LABEL_SCALARS = ("label_length", "anchor_commit_score", "lexical_commit_score")
STATE_RELATIVE = ("in_viewport", "is_newly_revealed", "taken_here", "taken_this_episode",
                  "taken_anywhere", "tried_here", "changed_state_rate", "reached_new_rate",
                  "field_is_filled", "value_already_set", "toggle_is_on")


def _column(name: str) -> int:
    return flag_index(name) if name in FLAG_FIELDS else numeric_index(name)


def _batch(seed: int = 0, batch: int = 3, actions: int = 7):
    g = th.Generator().manual_seed(seed)
    state = th.randn(batch, STATE_DIM, generator=g)
    acts = th.randn(batch, actions, ACTION_DIM, generator=g)
    mask = th.ones(batch, actions, dtype=th.bool)
    mask[:, -2:] = th.tensor([True, False])
    return state, acts, mask


def _trained_like(net: TransferQNetwork, seed: int = 1) -> TransferQNetwork:
    """Non-zero weights everywhere (a fresh residual outputs exactly zero)."""
    g = th.Generator().manual_seed(seed)
    with th.no_grad():
        for p in net.parameters():
            p.copy_(th.randn(p.shape, generator=g) * 0.3)
    return net


def test_none_is_the_primary_agent():
    assert input_masks("none") is None
    assert AgentConfig().input_mask == "none"
    agent = QAgent(AgentConfig(seed=3), labels=FakeLabels())
    assert agent.network.state_mask is None and agent.target.state_mask is None
    # The masks are not weights: a primary checkpoint's state_dict is unchanged.
    assert not any("mask" in k for k in agent.network.state_dict())


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        AgentConfig(input_mask="state_only")
    with pytest.raises(ValueError):
        input_masks("ctx_full")


def test_column_lists_match_the_preregistration():
    assert FEATURE_ACTION_DIM == ACTION_DIM
    state, action = input_masks("action_only")
    assert state.sum() == 0
    kept = set(np.flatnonzero(action))
    for block in (TYPE_SLICE, LABEL_SLICE, ROLE_SLICE, CATEGORY_SLICE):
        assert set(range(block.start, block.stop)) <= kept
    for name in STATE_RELATIVE:
        assert _column(name) not in kept
    # Every action column is either an intrinsic descriptor or state-relative.
    assert len(kept) + len(STATE_RELATIVE) == ACTION_DIM

    label = set(range(LABEL_SLICE.start, LABEL_SLICE.stop)) | {numeric_index(n) for n in LABEL_SCALARS}
    state, action = input_masks("lexical_only")
    assert state.sum() == 0 and set(np.flatnonzero(action)) == label
    state, action = input_masks("label_blind")
    assert state.all() and set(np.flatnonzero(action == 0)) == label
    assert set(NUMERIC_FIELDS) >= set(LABEL_SCALARS)


@pytest.mark.parametrize("mode", [m for m in MODES if m != "none"])
@pytest.mark.parametrize("residual", [False, True])
def test_masked_columns_have_no_effect(mode, residual):
    net = _trained_like(TransferQNetwork(STATE_DIM, ACTION_DIM, residual=residual))
    state_mask, action_mask = input_masks(mode)
    net.set_input_mask(state_mask, action_mask)
    state, acts, mask = _batch()
    noise_state = th.randn_like(state) * th.as_tensor(1 - state_mask)
    noise_acts = th.randn_like(acts) * th.as_tensor(1 - action_mask)
    with th.no_grad():
        q = net(state, acts, mask)
        q_perturbed = net(state + noise_state * 5, acts + noise_acts * 5, mask)
        # A kept column still matters (the mask is not zeroing everything).
        kept = int(np.flatnonzero(action_mask)[0])
        bumped = acts.clone()
        bumped[..., kept] += 5
        q_kept = net(state, bumped, mask)
    assert th.equal(q, q_perturbed)
    assert not th.allclose(q[mask], q_kept[mask])


def test_unmasked_network_output_is_unchanged_by_the_hook():
    net = _trained_like(TransferQNetwork(STATE_DIM, ACTION_DIM, residual=True))
    state, acts, mask = _batch(seed=5)
    with th.no_grad():
        before = net(state, acts, mask)
        net.set_input_mask(*input_masks("label_blind"))
        net.set_input_mask(None, None)
        after = net(state, acts, mask)
    assert th.equal(before, after)


def test_agent_masks_target_and_adapted_networks(tmp_path):
    agent = QAgent(AgentConfig(input_mask="lexical_only", seed=2), labels=FakeLabels())
    for net in (agent.network, agent.target):
        assert net.state_mask is not None and float(net.state_mask.sum()) == 0
    agent.enable_adaptation()
    assert agent.network.has_residual and agent.target.has_residual
    assert agent.target.state_mask is not None and float(agent.target.state_mask.sum()) == 0

    path = agent.save(tmp_path / "masked.pt")
    loaded = QAgent.load(path, labels=FakeLabels())
    assert loaded.config.input_mask == "lexical_only"
    assert loaded.network.state_mask is not None
    with pytest.raises(ValueError, match="input_mask"):
        QAgent.load(path, AgentConfig(input_mask="none"), labels=FakeLabels())
