"""SB3 features extractor that puts `FusionMLP` inside the policy.

This is where the fusion MLP finally receives gradients. The three encoders upstream
are frozen (and, until the real ones land, are deterministic hash embeddings), so this
projection is the only part of the perception stack the RL loss can shape — which is
exactly the arrangement the spec calls for.

Splitting the concatenated 1664-dim vector back into its three modalities here, rather
than feeding it to one flat `Linear`, keeps `FusionMLP` reusable outside SB3 (for the
auxiliary inverse-dynamics objective in `perception.fusion`) and keeps the modality
boundaries explicit rather than implied by slice arithmetic scattered across files.
"""

from __future__ import annotations

import torch
from gymnasium import spaces
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor

from ..envs.types import EPISODE_CONTEXT_DIM
from ..perception.fusion import FUSED_DIM, NETWORK_DIM, STRUCTURAL_DIM, VISUAL_DIM, FusionMLP


class FusionFeaturesExtractor(BaseFeaturesExtractor):
    """`Box(1670,)` from SemanticPerceptionWrapper -> the 256-dim state + episode context.

    The three perception modalities go through `FusionMLP`; the episode-context features
    are **concatenated after fusion, not fused**. They are a handful of already-normalized
    scalars, and pushing them through a 1664->512->256 bottleneck alongside embeddings
    would let the projection learn to discard them — which is precisely the information
    the agent needs to predict a history-dependent reward.
    """

    def __init__(
        self,
        observation_space: spaces.Box,
        features_dim: int = FUSED_DIM + EPISODE_CONTEXT_DIM,
        visual_dim: int = VISUAL_DIM,
        structural_dim: int = STRUCTURAL_DIM,
        network_dim: int = NETWORK_DIM,
        context_dim: int = EPISODE_CONTEXT_DIM,
    ) -> None:
        expected = visual_dim + structural_dim + network_dim + context_dim
        if observation_space.shape != (expected,):
            raise ValueError(
                f"FusionFeaturesExtractor expects Box({expected},) from "
                f"SemanticPerceptionWrapper, got {observation_space.shape}"
            )
        fused_dim = features_dim - context_dim
        if fused_dim <= 0:
            raise ValueError(f"features_dim must exceed context_dim ({context_dim}), got {features_dim}")

        super().__init__(observation_space, features_dim)
        self._splits = (visual_dim, structural_dim, network_dim, context_dim)
        self.fusion = FusionMLP(
            visual_dim=visual_dim,
            structural_dim=structural_dim,
            network_dim=network_dim,
            output_dim=fused_dim,
        )

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        visual, structural, network, context = torch.split(observations, self._splits, dim=-1)
        return torch.cat([self.fusion(visual, structural, network), context], dim=-1)
