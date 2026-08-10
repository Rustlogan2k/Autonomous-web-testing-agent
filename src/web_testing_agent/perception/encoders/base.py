"""The interface every perception encoder implements, and a deterministic stub.

The three real encoders (CLIP ViT-B/32 on the screenshot, CodeBERT on the structural
HTML, all-MiniLM-L6-v2 on the network trace) are Phase 2 work. What matters *now* is
the shape of the seam, because it determines where the models get instantiated — and
instantiating them per-env inside `SubprocVecEnv` would mean one full copy of every
model per parallel browser, each doing batch-size-1 GPU forward passes.

Hence `encode_batch`: encoders receive every parallel env's observation at once and
return a stacked array, so the wrapper in the parent process can run one forward pass
per encoder per timestep regardless of how many browsers are running.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod

import numpy as np


class PerceptionEncoder(ABC):
    """Encodes one modality for a whole batch of parallel environments."""

    @property
    @abstractmethod
    def output_dim(self) -> int:
        """Width of this encoder's contribution to the fused state vector."""

    @abstractmethod
    def encode_batch(self, items: list) -> np.ndarray:
        """Encode `len(items)` observations. Returns float32 `(len(items), output_dim)`."""


class HashEmbeddingEncoder(PerceptionEncoder):
    """Deterministic content-hash embedding — a real, usable stand-in, not a mock.

    Maps input content to a fixed pseudo-random unit vector. It carries no semantics,
    so it will not find semantic bugs, but it *is* stable (same page -> same vector)
    and correctly shaped, which makes it enough to run the whole DQN pipeline, the toy-
    site A/B harness, and the random-policy baseline before the GPU encoders land.
    Swap it out via `SemanticPerceptionWrapper(encoders=...)` with no other changes.
    """

    def __init__(self, output_dim: int) -> None:
        self._output_dim = output_dim

    @property
    def output_dim(self) -> int:
        return self._output_dim

    def encode_batch(self, items: list) -> np.ndarray:
        return np.stack([self._encode_one(item) for item in items]).astype(np.float32)

    def _encode_one(self, item) -> np.ndarray:
        if isinstance(item, np.ndarray):
            # Downsample before hashing: pixel-identical screenshots are rare, but
            # visually identical pages should still land on the same vector.
            payload = np.ascontiguousarray(item[::16, ::16]).tobytes()
        else:
            payload = str(item).encode("utf-8", errors="replace")
        seed = int.from_bytes(hashlib.sha256(payload).digest()[:8], "little")
        vector = np.random.default_rng(seed).standard_normal(self._output_dim)
        norm = np.linalg.norm(vector)
        return (vector / norm if norm else vector).astype(np.float32)
