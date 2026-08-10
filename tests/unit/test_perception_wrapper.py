import numpy as np

from web_testing_agent.perception.encoders.base import HashEmbeddingEncoder
from web_testing_agent.perception.fusion import (
    FUSED_DIM,
    NETWORK_DIM,
    STRUCTURAL_DIM,
    VISUAL_DIM,
    FusionMLP,
    InverseDynamicsHead,
)


def test_hash_encoder_returns_the_declared_shape():
    encoder = HashEmbeddingEncoder(64)
    out = encoder.encode_batch(["a", "b", "c"])
    assert out.shape == (3, 64)
    assert out.dtype == np.float32


def test_hash_encoder_is_deterministic():
    encoder = HashEmbeddingEncoder(32)
    first = encoder.encode_batch(["same page"])
    second = encoder.encode_batch(["same page"])
    assert np.array_equal(first, second)


def test_hash_encoder_separates_different_content():
    encoder = HashEmbeddingEncoder(32)
    out = encoder.encode_batch(["page one", "page two"])
    assert not np.allclose(out[0], out[1])


def test_hash_encoder_handles_screenshots():
    encoder = HashEmbeddingEncoder(16)
    frames = [np.zeros((720, 1280, 3), dtype=np.uint8), np.full((720, 1280, 3), 255, dtype=np.uint8)]
    out = encoder.encode_batch(frames)
    assert out.shape == (2, 16)
    assert not np.allclose(out[0], out[1])


def test_fusion_mlp_maps_concatenated_modalities_to_the_shared_state():
    import torch

    mlp = FusionMLP()
    assert mlp.input_dim == VISUAL_DIM + STRUCTURAL_DIM + NETWORK_DIM == 1664

    batch = 4
    state = mlp(
        torch.zeros(batch, VISUAL_DIM),
        torch.zeros(batch, STRUCTURAL_DIM),
        torch.zeros(batch, NETWORK_DIM),
    )
    assert state.shape == (batch, FUSED_DIM)


def test_fusion_mlp_is_trainable():
    import torch

    mlp = FusionMLP()
    out = mlp(torch.randn(2, VISUAL_DIM), torch.randn(2, STRUCTURAL_DIM), torch.randn(2, NETWORK_DIM))
    out.sum().backward()
    assert any(p.grad is not None and p.grad.abs().sum() > 0 for p in mlp.parameters())


def test_inverse_dynamics_head_predicts_over_the_action_space():
    import torch

    head = InverseDynamicsHead(num_actions=100)
    logits = head(torch.randn(3, FUSED_DIM), torch.randn(3, FUSED_DIM))
    assert logits.shape == (3, 100)
