"""Batched semantic perception, applied across all parallel envs in the parent process.

**Why this is a VecEnvWrapper and not code inside `WebTestingEnv`.**

The obvious place to encode observations is the env itself. It is the wrong place. SB3
parallelizes with `SubprocVecEnv`, which forks one process per environment — so an
encoder living in the env means N copies of CLIP + CodeBERT + MiniLM resident at once,
each running batch-size-1 forward passes, and N x the VRAM. With the 8-ish parallel
browsers the 300K-step curriculum needs, that is the difference between fitting on one
GPU and not.

Sitting here instead, the wrapper sees all N observations *after* they cross back from
the subprocesses, encodes them in a single batched forward pass per modality, and hands
SB3 a plain `Box(256,)`. One model copy, full GPU utilization, and the raw dict/`info`
plumbing stays confined to the env where it belongs.
"""

from __future__ import annotations

import numpy as np
from gymnasium import spaces

from ..envs.types import EPISODE_CONTEXT_DIM
from ..utils.logging import get_logger
from .encoders.base import HashEmbeddingEncoder, PerceptionEncoder
from .fusion import FUSED_DIM, NETWORK_DIM, STRUCTURAL_DIM, VISUAL_DIM
from .normalization import canonicalize_text, preprocess_for_structural_encoder

logger = get_logger(__name__)

try:  # pragma: no cover - exercised only when SB3 is installed
    from stable_baselines3.common.vec_env import VecEnv, VecEnvWrapper
except ImportError:  # pragma: no cover
    VecEnv = object  # type: ignore[assignment,misc]
    VecEnvWrapper = object  # type: ignore[assignment,misc]


MODALITIES = ("visual", "structural", "network")


def default_encoders() -> dict[str, PerceptionEncoder]:
    """Correctly-shaped stand-ins so the pipeline runs before the GPU encoders land."""
    return {
        "visual": HashEmbeddingEncoder(VISUAL_DIM),
        "structural": HashEmbeddingEncoder(STRUCTURAL_DIM),
        "network": HashEmbeddingEncoder(NETWORK_DIM),
    }


def encode_modalities(
    encoders: dict[str, PerceptionEncoder],
    screenshots: list,
    pages: list[dict],
    contexts: list | None = None,
) -> np.ndarray:
    """Encode a batch of observations into the concatenated pre-fusion feature vector.

    Deliberately a free function rather than a wrapper method: training runs through
    `SemanticPerceptionWrapper`, but evaluation runs a trained policy against the *raw*
    env (so it reuses the same rollout harness as the random baseline). Both paths must
    encode identically or the comparison is meaningless, so they share this one call.
    """
    structural_inputs = [preprocess_for_structural_encoder(page.get("html", "")) for page in pages]
    network_inputs = [canonicalize_text(page.get("network", "")) for page in pages]
    parts = [
        encoders["visual"].encode_batch(screenshots),
        encoders["structural"].encode_batch(structural_inputs),
        encoders["network"].encode_batch(network_inputs),
    ]
    # Episode-history features ride alongside the perception vector rather than through
    # an encoder — they are already low-dimensional and normalized, and they must reach
    # the policy unmodified for the reward to be predictable from the observation.
    # Normalize per element, not just the whole list: an env that predates these
    # features, or a `reset()` info without them, yields None entries. `np.asarray(None)`
    # silently produces a 1-wide NaN column instead of failing, which would corrupt the
    # feature width and poison the replay buffer rather than raising.
    contexts = list(contexts) if contexts is not None else [None] * len(screenshots)
    normalized = []
    for context in contexts:
        if context is None:
            normalized.append(np.zeros(EPISODE_CONTEXT_DIM, dtype=np.float32))
            continue
        vector = np.asarray(context, dtype=np.float32).reshape(-1)
        if vector.shape != (EPISODE_CONTEXT_DIM,):
            raise ValueError(f"episode_context must have width {EPISODE_CONTEXT_DIM}, got {vector.shape}")
        normalized.append(vector)
    parts.append(np.stack(normalized))
    return np.concatenate(parts, axis=1).astype(np.float32)


class SemanticPerceptionWrapper(VecEnvWrapper):  # type: ignore[misc]
    """Turns the raw browser observation into the shared fused state vector.

    Reads the screenshot from `obs["screenshot"]` and the textual modalities from
    `info["page"]` (see `WebTestingEnv.observation_space` for why the text lives there),
    canonicalizes the volatile parts, encodes each modality across the whole batch, and
    concatenates. Fusion into 256 dims is done by `FusionMLP` *inside* the SB3 policy's
    features extractor, because it is the one trainable piece and must receive gradients
    from the RL loss — so this wrapper emits the 1664-dim concatenation.
    """

    def __init__(
        self,
        venv: "VecEnv",
        encoders: dict[str, PerceptionEncoder] | None = None,
    ) -> None:
        self.encoders = encoders or default_encoders()
        missing = {"visual", "structural", "network"} - set(self.encoders)
        if missing:
            raise ValueError(f"SemanticPerceptionWrapper is missing encoder(s): {sorted(missing)}")

        self.feature_dim = sum(self.encoders[name].output_dim for name in MODALITIES) + EPISODE_CONTEXT_DIM
        observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(self.feature_dim,), dtype=np.float32
        )
        super().__init__(venv, observation_space=observation_space)
        logger.info(
            "SemanticPerceptionWrapper: {} -> {} dims (fused to {} by the policy's FusionMLP)",
            {name: enc.output_dim for name, enc in self.encoders.items()},
            self.feature_dim,
            FUSED_DIM,
        )

    def reset(self) -> np.ndarray:
        obs = self.venv.reset()
        # `VecEnv.reset()` returns no infos, but SB3's DummyVecEnv/SubprocVecEnv stash
        # them on `reset_infos`. Reading them keeps the first observation of every
        # episode encoded from the same three modalities as every later one — otherwise
        # step 0 alone would carry an empty structural/network vector, a train/eval
        # mismatch that is easy to introduce and very hard to notice.
        infos = getattr(self.venv, "reset_infos", None) or [{} for _ in range(self.num_envs)]
        pages = [info.get("page", {}) if isinstance(info, dict) else {} for info in infos]
        contexts = [info.get("episode_context") if isinstance(info, dict) else None for info in infos]
        return self._encode(obs, pages, contexts)

    def step_wait(self):  # noqa: ANN201 - SB3 returns a 4-tuple whose type it does not export
        obs, rewards, dones, infos = self.venv.step_wait()
        reset_infos = getattr(self.venv, "reset_infos", None) or []

        # On a done step SB3's VecEnvs auto-reset: the observation handed back already
        # belongs to the *next* episode, while `info["page"]` still describes the
        # terminal one. Pairing them naively encodes the new screenshot against the old
        # page text — a silent modality mismatch on every episode boundary.
        pages: list[dict] = []
        contexts: list = []
        for index, info in enumerate(infos):
            info = info if isinstance(info, dict) else {}
            if dones[index] and index < len(reset_infos) and isinstance(reset_infos[index], dict):
                source = reset_infos[index]
            else:
                source = info
            pages.append(source.get("page", {}))
            contexts.append(source.get("episode_context"))

        encoded = self._encode(obs, pages, contexts)

        # Any wrapper that changes the observation space must also convert the raw
        # `terminal_observation` SB3 stashes in `info`, or the replay buffer receives an
        # unencoded dict where it expects a feature vector.
        for index, info in enumerate(infos):
            if isinstance(info, dict) and "terminal_observation" in info:
                terminal = info["terminal_observation"]
                screenshot = terminal["screenshot"] if isinstance(terminal, dict) else terminal
                info["terminal_observation"] = encode_modalities(
                    self.encoders,
                    [screenshot],
                    [info.get("page", {})],
                    [info.get("episode_context")],
                )[0]

        return encoded, rewards, dones, infos

    def _encode(self, obs, pages: list[dict], contexts: list) -> np.ndarray:
        screenshots = list(obs["screenshot"]) if isinstance(obs, dict) else list(obs)
        safe = [
            c if c is not None else np.zeros(EPISODE_CONTEXT_DIM, dtype=np.float32) for c in contexts
        ]
        return encode_modalities(self.encoders, screenshots, pages, safe)
