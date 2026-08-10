"""The fusion MLP: concat(visual, structural, network) -> the shared 256-dim state.

This is the only trainable component in the entire perception stack — CLIP, CodeBERT
and MiniLM stay frozen throughout RL training. It is a plain `nn.Module` here rather
than an SB3 policy so that it can be used two ways:

* as SB3's `features_extractor`, trained end-to-end by the DQN/PPO loss, and
* with an auxiliary self-supervised head (see `InverseDynamicsHead`), because TD error
  alone is a weak signal for learning a 1664->256 projection. Agent A already builds an
  ICM inverse model; reusing just that head as an auxiliary loss on Agent B costs
  almost nothing and gives the representation a dense, well-posed training signal.
"""

from __future__ import annotations

import torch
from torch import nn

VISUAL_DIM = 512  # CLIP ViT-B/32
STRUCTURAL_DIM = 768  # CodeBERT-base [CLS]
NETWORK_DIM = 384  # all-MiniLM-L6-v2
FUSED_DIM = 256


class FusionMLP(nn.Module):
    """concat(1664) -> Linear(512) -> ReLU -> Linear(256) -> ReLU."""

    def __init__(
        self,
        visual_dim: int = VISUAL_DIM,
        structural_dim: int = STRUCTURAL_DIM,
        network_dim: int = NETWORK_DIM,
        hidden_dim: int = 512,
        output_dim: int = FUSED_DIM,
    ) -> None:
        super().__init__()
        self.input_dim = visual_dim + structural_dim + network_dim
        self.output_dim = output_dim
        self.net = nn.Sequential(
            nn.Linear(self.input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, output_dim),
            nn.ReLU(),
        )

    def forward(self, visual: torch.Tensor, structural: torch.Tensor, network: torch.Tensor) -> torch.Tensor:
        return self.net(torch.cat([visual, structural, network], dim=-1))


class InverseDynamicsHead(nn.Module):
    """Auxiliary head: predict which action was taken from (state, next_state).

    Trained jointly with `FusionMLP` on transitions the agent is already collecting.
    Its purpose is not to be accurate — it is to force the fused representation to
    retain exactly the information that distinguishes one action's effect from
    another's, which is precisely what a bug-detection state vector needs.
    """

    def __init__(self, state_dim: int = FUSED_DIM, num_actions: int = 100, hidden_dim: int = 256) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, num_actions),
        )

    def forward(self, state: torch.Tensor, next_state: torch.Tensor) -> torch.Tensor:
        return self.net(torch.cat([state, next_state], dim=-1))
