"""The real perception encoders: CLIP on the screenshot, CodeBERT on the structural
HTML, all-MiniLM-L6-v2 on the network trace.

These replace `HashEmbeddingEncoder`, which was a correctly-shaped stand-in with no
semantics at all. That distinction stopped being academic on 2026-08-10: the deep-flow
measurement showed action masking working exactly as designed (100% valid actions, 3x
the mean flow depth) while no policy still got past stage 1, because with a content
hash for an observation two near-identical pages have unrelated vectors and "this is a
Continue link" is not representable. The perception layer is the binding constraint on
the RL half, and this module is that constraint.

Three properties matter more than the model choices.

**Frozen.** All three run under `no_grad` in `eval` mode and their parameters are never
optimized. The fusion MLP inside the SB3 policy is the only trainable part of the
perception stack — that is the arrangement the spec calls for, and it is also what
keeps the encoders from drifting under an RL loss that would happily destroy them.

**Batched.** `encode_batch` runs one forward pass for the whole batch. Encoders live in
the parent process behind a `VecEnvWrapper` precisely so N parallel browsers share one
model copy rather than one each; per-item loops here would give back the throughput
that arrangement exists to buy.

**Cached by content.** A rollout revisits the same handful of pages constantly, and an
identical page must produce an identical vector anyway (state identity depends on it).
Hashing the input and caching the result turns most steps into a dictionary lookup.
Measured on the deep-flow fixture, this is the difference between the encoders being a
rounding error and being the dominant per-step cost.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict

import numpy as np

from ...utils.logging import get_logger
from ..fusion import NETWORK_DIM, STRUCTURAL_DIM, VISUAL_DIM
from .base import PerceptionEncoder

logger = get_logger(__name__)

CLIP_MODEL = "openai/clip-vit-base-patch32"
CODEBERT_MODEL = "microsoft/codebert-base"
MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Roughly the number of distinct pages a rollout touches, with headroom. Vectors are
# small (512-768 floats), so even a few thousand entries is a few megabytes.
_CACHE_SIZE = 2048


def _resolve_device(device: str | None) -> str:
    if device:
        return device
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:  # pragma: no cover - torch is a hard dependency in practice
        return "cpu"


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class _CachedEncoder(PerceptionEncoder):
    """Content-addressed caching and batching, shared by all three real encoders.

    Subclasses supply `_encode_uncached(items)` for a batch of genuinely-new inputs and
    `_key(item)` for the cache key. The split matters: the cache is checked per item but
    the forward pass runs once over only the misses, so a batch that is half cache hits
    costs half a forward pass rather than a full one plus bookkeeping.
    """

    def __init__(self, output_dim: int, cache_size: int = _CACHE_SIZE, normalize: bool = True) -> None:
        self._output_dim = output_dim
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._cache_size = cache_size
        # L2-normalized to unit length, because the rest of the system assumes it.
        # `HashEmbeddingEncoder` emitted unit vectors and `WebTestingEnv._episode_context`
        # says so out loud — its features are scaled into [0, 1] "so no single term
        # dominates the input scale of a network whose other 1664 dims are unit-norm
        # embeddings". Measured on the real encoders: CLIP image embeddings have norm
        # 10.6-11.2 and CodeBERT's centered mean-pool 2.4-2.9, so swapping the stub for
        # the real models silently broke that contract — the visual block outweighed the
        # other two modalities and the episode-context features by an order of magnitude,
        # and the masked agent's mean flow depth fell 0.33 -> 0.00.
        #
        # Normalizing keeps the direction, which is where all the semantic content is,
        # and discards only a magnitude the downstream network was never scaled for.
        self._normalize = normalize
        self.hits = 0
        self.misses = 0

    @property
    def output_dim(self) -> int:
        return self._output_dim

    def encode_batch(self, items: list) -> np.ndarray:
        keys = [self._key(item) for item in items]
        pending = [(index, item) for index, (key, item) in enumerate(zip(keys, items))
                   if key not in self._cache]

        # Resolved here rather than re-read from the cache at the end. A batch larger
        # than the cache evicts its own earlier entries while it is still being filled,
        # so assembling the result from `self._cache` raised KeyError on exactly the
        # inputs it had just encoded. Harmless at one env and a hard crash at eight.
        resolved = {key: self._cache[key] for key in keys if key in self._cache}
        if pending:
            self.misses += len(pending)
            encoded = self._encode_uncached([item for _, item in pending])
            for (index, _), vector in zip(pending, encoded):
                resolved[keys[index]] = self._unit(np.asarray(vector, dtype=np.float32))
                self._store(keys[index], resolved[keys[index]])
        self.hits += len(items) - len(pending)

        out = np.stack([resolved[key] for key in keys]).astype(np.float32)
        if out.shape != (len(items), self._output_dim):
            raise ValueError(
                f"{type(self).__name__} produced {out.shape}, expected "
                f"{(len(items), self._output_dim)} — a width mismatch here silently "
                f"corrupts the fused vector and the replay buffer."
            )
        return out

    def _unit(self, vector: np.ndarray) -> np.ndarray:
        if not self._normalize:
            return vector
        norm = float(np.linalg.norm(vector))
        # A genuinely zero vector (an empty network trace encodes to one) stays zero
        # rather than becoming NaN — "nothing happened" is a legitimate observation.
        return (vector / norm).astype(np.float32) if norm > 1e-9 else vector

    def _store(self, key: str, vector: np.ndarray) -> None:
        self._cache[key] = vector
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def cache_stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
            "entries": len(self._cache),
        }

    def _key(self, item) -> str:
        raise NotImplementedError

    def _encode_uncached(self, items: list) -> np.ndarray:
        raise NotImplementedError


class ClipScreenshotEncoder(_CachedEncoder):
    """CLIP ViT-B/32 image tower over the canonical 1280x720 screenshot."""

    def __init__(self, model_name: str = CLIP_MODEL, device: str | None = None,
                 output_dim: int = VISUAL_DIM) -> None:
        super().__init__(output_dim)
        import torch
        from transformers import CLIPImageProcessor, CLIPVisionModelWithProjection

        self.device = _resolve_device(device)
        self._torch = torch
        self.processor = CLIPImageProcessor.from_pretrained(model_name)
        # The vision tower with its projection head only. The text tower is ~40% of
        # CLIP's parameters and is never used here, and VRAM on the dev card (6 GB) is
        # shared with Chromium and, during training, the replay buffer.
        self.model = CLIPVisionModelWithProjection.from_pretrained(model_name).to(self.device).eval()
        for parameter in self.model.parameters():
            parameter.requires_grad_(False)
        logger.info("CLIP screenshot encoder on {} ({})", self.device, model_name)

    def _key(self, item) -> str:
        # Downsampled before hashing so two visually identical renders that differ by a
        # cursor position or an antialiased pixel still share a cache entry.
        array = np.asarray(item)
        return _digest(np.ascontiguousarray(array[::8, ::8]).tobytes())

    def _encode_uncached(self, items: list) -> np.ndarray:
        images = [np.asarray(item).astype(np.uint8) for item in items]
        inputs = self.processor(images=images, return_tensors="pt").to(self.device)
        with self._torch.no_grad():
            embeds = self.model(**inputs).image_embeds
        return embeds.float().cpu().numpy()


# Documents used once, at construction, to estimate the encoder's mean direction. They
# are deliberately generic rather than drawn from any fixture or target: the vector they
# produce must be a property of the *encoder*, not of the application under test, or the
# representation would shift depending on which site was loaded.
_CENTERING_PROBES = (
    "<title>Sign in</title><form><input name=username type=text><input name=password type=password></form>",
    "<title>Create account</title><form><input name=email type=email><input name=age type=number></form>",
    "<title>Search results</title><form><input name=q type=search></form><a href=/next>Next page</a>",
    "<title>Dashboard</title><a href=/settings>Settings</a><a href=/reports>Reports</a><a href=/help>Help</a>",
    "<title>Checkout</title><form><select name=country></select><input name=postcode></form>",
    "<title>Article</title><p>A page of ordinary prose with no controls on it at all.</p>",
    "<title>Error</title><p>Something went wrong. Please try again later.</p>",
    "<title>Pricing</title><table><tr><td>Free</td><td>Pro</td></tr></table>",
    "<title>Profile</title><form><input name=display_name><textarea name=bio></textarea></form>",
    "<title>Empty</title>",
    "<title>Confirmation</title><p>Your order has been placed.</p><a href=/home>Back to home</a>",
    "<title>Listing</title><a href=/a>Item A</a><a href=/b>Item B</a><a href=/c>Item C</a>",
)


class TransformerTextEncoder(_CachedEncoder):
    """A frozen HuggingFace encoder over text, pooled to one vector per input.

    Used for CodeBERT on the preprocessed structural HTML.

    **Two deliberate departures from the spec's "CodeBERT `[CLS]` token", both forced by
    measurement.** On 18 real fixture pages the specified encoder produced pairwise
    cosine similarities of min 0.973, mean 0.991 — every page looked like every other
    page, so as an RL observation it was barely better than the hash stub it replaced.

    1. **Mean pooling over tokens, not `[CLS]`.** A `[CLS]` embedding is only meaningful
       when a model has been fine-tuned with an objective that trains it; CodeBERT's is
       not, and untrained it carries little beyond the sequence's overall register.
       Mean pooling alone helped only slightly (min 0.934, mean 0.980).

    2. **Centering by a fixed reference vector.** Raw transformer embeddings are
       famously anisotropic — they occupy a narrow cone, so *everything* is cosine-
       similar to everything. Subtracting a fixed mean direction moved the same 18 pages
       to min −0.915, mean 0.027, and the nearest-neighbour structure became
       semantically correct: the four order stages became each other's neighbours, the
       static pages clustered separately, and the two form pages paired up.

    The reference is computed once at construction from `_CENTERING_PROBES` and then
    frozen. Centering per *batch* would be cheaper and is the obvious alternative, but
    it makes the representation depend on which other environments happened to be in the
    batch — a non-stationary observation, which is precisely the defect that produced
    the peak-then-collapse training curve documented in §5.
    """

    def __init__(self, model_name: str = CODEBERT_MODEL, device: str | None = None,
                 output_dim: int = STRUCTURAL_DIM, max_length: int = 512,
                 pooling: str = "mean", center: bool = True) -> None:
        super().__init__(output_dim)
        import torch
        from transformers import AutoModel, AutoTokenizer

        if pooling not in ("mean", "cls"):
            raise ValueError(f"pooling must be 'mean' or 'cls', got {pooling!r}")
        self.device = _resolve_device(device)
        self._torch = torch
        self.max_length = max_length
        self.pooling = pooling
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name).to(self.device).eval()
        for parameter in self.model.parameters():
            parameter.requires_grad_(False)

        # `cls` is kept selectable so the spec's literal configuration remains runnable
        # and the comparison above is reproducible, not just asserted.
        self._center = self._pool(list(_CENTERING_PROBES)).mean(axis=0) if center else None
        logger.info(
            "Structural encoder on {} ({}, pooling={}, centered={}, max_length={})",
            self.device, model_name, pooling, center, max_length,
        )

    def _key(self, item) -> str:
        return _digest(str(item).encode("utf-8", errors="replace"))

    def _pool(self, items: list) -> np.ndarray:
        # Truncation is unavoidable — a real page's preprocessed markup runs to tens of
        # thousands of tokens against a 512 limit. `preprocess_for_structural_encoder`
        # front-loads what matters (title, forms with field names and types, anchors)
        # precisely so the surviving prefix is the informative part rather than a
        # stylesheet link.
        batch = self.tokenizer(
            [str(item) for item in items],
            padding=True, truncation=True, max_length=self.max_length, return_tensors="pt",
        ).to(self.device)
        with self._torch.no_grad():
            hidden = self.model(**batch).last_hidden_state
        if self.pooling == "cls":
            pooled = hidden[:, 0, :]
        else:
            # Mask-weighted mean, so padding tokens do not drag every short page toward
            # a shared "mostly padding" vector.
            mask = batch["attention_mask"].unsqueeze(-1).float()
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        return pooled.float().cpu().numpy()

    def _encode_uncached(self, items: list) -> np.ndarray:
        pooled = self._pool(items)
        return pooled - self._center if self._center is not None else pooled


class SentenceTransformerEncoder(_CachedEncoder):
    """all-MiniLM-L6-v2 over the serialized network trace."""

    def __init__(self, model_name: str = MINILM_MODEL, device: str | None = None,
                 output_dim: int = NETWORK_DIM) -> None:
        super().__init__(output_dim)
        from sentence_transformers import SentenceTransformer

        self.device = _resolve_device(device)
        self.model = SentenceTransformer(model_name, device=self.device)
        self.model.eval()
        logger.info("Network encoder on {} ({})", self.device, model_name)

    def _key(self, item) -> str:
        return _digest(str(item).encode("utf-8", errors="replace"))

    def _encode_uncached(self, items: list) -> np.ndarray:
        return self.model.encode(
            [str(item) for item in items],
            batch_size=len(items), convert_to_numpy=True, show_progress_bar=False,
        )


def semantic_encoders(device: str | None = None) -> dict[str, PerceptionEncoder]:
    """The three real encoders, ready to hand to `SemanticPerceptionWrapper`.

    Loading is deliberately eager and here rather than lazily on first use: models take
    tens of seconds to download and load, and discovering that mid-training — after a
    browser is up and an episode is running — turns a missing-model error into a
    corrupted run.
    """
    return {
        "visual": ClipScreenshotEncoder(device=device),
        "structural": TransformerTextEncoder(device=device),
        "network": SentenceTransformerEncoder(device=device),
    }
