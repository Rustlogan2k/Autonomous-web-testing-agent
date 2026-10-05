"""`Q(s, a)` as one shared scorer applied to every action that exists.

The architecture is the one `agents.ac_dqn.ActionConditionedQNetwork` established and the
repository already verified to be slot-free and permutation-equivariant
(`tests/unit/test_variable_length_scoring.py`):

    Q(s, a) = V(g(s)) + A(g(s), h(phi(a))) - mean_a' A(g(s), h(phi(a')))

with `g`, `h` and the scorer shared across actions. There is no output unit per action
slot, so nothing in the network is sized or indexed by a particular page, and the same
weights are meaningful on an application that was never trained on. That property is the
reason to keep the design, and it is kept.

What changes is what surrounds it. The original is welded to Stable-Baselines3's flat
`Box(6973,)` observation, to a 100-row padded action block of a fixed feature width, and
to a perception stack of hashed page encodings. This module is the same scorer taking the
two tensors it actually needs -- a state vector and a variable-length matrix of action
features -- so it can be trained on several applications in one process and adapted on a
new one.

**The residual head is the transfer mechanism.** After pretraining, `base` is frozen and a
second, smaller scorer `delta` is added on top, initialised so that it outputs exactly
zero:

    Q(s, a) = Q_base(s, a) + Q_delta(s, a)

At the first step on a new application the agent therefore behaves exactly as the
pretrained policy would -- zero-shot transfer is the starting point, not a separate code
path -- and everything it learns there is learned by `delta` alone. The prior cannot be
overwritten by a few hundred noisy target-application transitions, which is the failure
full fine-tuning on a small budget invites.
"""

from __future__ import annotations

import torch as th
from torch import nn

#: Finite, not -inf: -inf survives an argmax but turns into NaN the first time it reaches
#: a subtraction in the loss. Same sentinel and same reasoning as `agents.ac_dqn`.
MASKED_Q = -1e8


class ActionScorer(nn.Module):
    """One dueling, action-conditioned Q head."""

    def __init__(self, state_dim: int, action_dim: int, *, state_hidden: int = 64,
                 action_hidden: int = 96, scorer_hidden: int = 128, dueling: bool = True) -> None:
        super().__init__()
        self.dueling = dueling
        self.state_encoder = nn.Sequential(nn.Linear(state_dim, state_hidden), nn.ReLU())
        self.action_encoder = nn.Sequential(nn.Linear(action_dim, action_hidden), nn.ReLU())
        self.scorer = nn.Sequential(
            nn.Linear(state_hidden + action_hidden, scorer_hidden), nn.ReLU(),
            nn.Linear(scorer_hidden, 1),
        )
        # The state value must not see the action, or the decomposition is not one.
        self.value = nn.Sequential(
            nn.Linear(state_hidden, scorer_hidden), nn.ReLU(), nn.Linear(scorer_hidden, 1),
        ) if dueling else None

    def zero_output(self) -> None:
        """Make this head output exactly zero for every input, while staying trainable."""
        for head in (self.scorer, self.value):
            if head is None:
                continue
            final = head[-1]
            nn.init.zeros_(final.weight)
            nn.init.zeros_(final.bias)

    def forward(self, state: th.Tensor, actions: th.Tensor, mask: th.Tensor) -> th.Tensor:
        """`state` (B, S), `actions` (B, A, D), `mask` (B, A) bool -> unmasked Q (B, A)."""
        encoded_state = self.state_encoder(state)                          # (B, Hs)
        encoded_actions = self.action_encoder(actions)                     # (B, A, Ha)
        expanded = encoded_state.unsqueeze(1).expand(-1, actions.shape[1], -1)
        scored = self.scorer(th.cat([expanded, encoded_actions], dim=-1)).squeeze(-1)
        if not self.dueling:
            return scored
        # Mean over *valid* actions only. Padding rows are zeros, and averaging them in
        # would shift the baseline by an amount that depends on how many controls a page
        # happens to have -- a page-identity leak through the back door.
        count = mask.sum(dim=-1, keepdim=True).clamp(min=1)
        mean_advantage = th.where(mask, scored, th.zeros_like(scored)).sum(-1, keepdim=True) / count
        return self.value(encoded_state) + scored - mean_advantage


class TransferQNetwork(nn.Module):
    """A pretrained scorer plus an optional zero-initialised residual for adaptation."""

    def __init__(self, state_dim: int, action_dim: int, *, dueling: bool = True,
                 residual: bool = False, residual_scale: float = 0.5) -> None:
        super().__init__()
        self.state_dim, self.action_dim = state_dim, action_dim
        self.dueling = dueling
        self.base = ActionScorer(state_dim, action_dim, dueling=dueling)
        self.delta: ActionScorer | None = None
        self.register_buffer("state_mask", None, persistent=False)
        self.register_buffer("action_mask", None, persistent=False)
        if residual:
            self.add_residual(residual_scale)

    def set_input_mask(self, state_mask, action_mask) -> None:
        """Zero the given input columns at every forward pass (the contextual diagnostic's
        ablation arms). Non-persistent: not part of `state_dict`, so weights copy between
        masked and unmasked networks unchanged. `None` for both removes the mask."""
        if (state_mask is None) != (action_mask is None):
            raise ValueError("set both input masks or neither")
        if state_mask is None:
            self.state_mask = self.action_mask = None
            return
        state_mask = th.as_tensor(state_mask, dtype=th.float32)
        action_mask = th.as_tensor(action_mask, dtype=th.float32)
        if state_mask.shape != (self.state_dim,) or action_mask.shape != (self.action_dim,):
            raise ValueError(f"input masks must have shapes ({self.state_dim},), ({self.action_dim},)")
        self.register_buffer("state_mask", state_mask, persistent=False)
        self.register_buffer("action_mask", action_mask, persistent=False)

    def add_residual(self, scale: float = 0.5) -> None:
        """Freeze `base` and attach a zero-output residual head. Idempotent."""
        for parameter in self.base.parameters():
            parameter.requires_grad_(False)
        if self.delta is None:
            self.delta = ActionScorer(
                self.state_dim, self.action_dim, dueling=self.dueling,
                state_hidden=max(16, int(64 * scale)), action_hidden=max(24, int(96 * scale)),
                scorer_hidden=max(32, int(128 * scale)))
            self.delta.zero_output()

    @property
    def has_residual(self) -> bool:
        return self.delta is not None

    def trainable_parameters(self) -> list[nn.Parameter]:
        return [p for p in self.parameters() if p.requires_grad]

    def forward(self, state: th.Tensor, actions: th.Tensor, mask: th.Tensor) -> th.Tensor:
        """Masked Q-values, `(B, A)`. Invalid rows hold `MASKED_Q`."""
        if bool((mask.sum(dim=-1) == 0).any()):
            # Every page offers at least the nine fixed actions, so an empty mask means
            # the observation was not built from a live env. A run that costs hours must
            # not continue past an observation it cannot interpret.
            raise ValueError("action mask is empty for at least one observation in the batch")
        if self.state_mask is not None:
            state, actions = state * self.state_mask, actions * self.action_mask
        q_values = self.base(state, actions, mask)
        if self.delta is not None:
            q_values = q_values + self.delta(state, actions, mask)
        return th.where(mask, q_values, th.full_like(q_values, MASKED_Q))
