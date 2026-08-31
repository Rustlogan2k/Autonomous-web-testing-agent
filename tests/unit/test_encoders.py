"""Encoder contract tests.

The real encoders need a GPU and ~1 GB of model downloads, so the tests that load them
are marked and skipped unless the weights are already cached. The contract tests below
run everywhere: a width or dtype mismatch here silently corrupts the fused vector and
poisons the replay buffer rather than raising, which is precisely why they exist.
"""

from __future__ import annotations

import zlib

import numpy as np
import pytest

from web_testing_agent.perception.encoders.base import HashEmbeddingEncoder
from web_testing_agent.perception.encoders.models import _CachedEncoder


class _Counting(_CachedEncoder):
    """A cached encoder whose 'model' just counts how often it really ran."""

    def __init__(self, output_dim: int = 4, cache_size: int = 8) -> None:
        super().__init__(output_dim, cache_size)
        self.forward_passes = 0
        self.items_encoded = 0

    def _key(self, item) -> str:
        return str(item)

    def _encode_uncached(self, items: list) -> np.ndarray:
        self.forward_passes += 1
        self.items_encoded += len(items)
        # `crc32`, not `hash()`, and never zero. Python randomizes string hashing per
        # process, so `hash(i) % 97` was 0 for some inputs on some runs — an all-zero row
        # that normalizes to an all-zero row, failing the unit-norm assertion about once
        # in forty runs with no way to reproduce it. Demonstrated at PYTHONHASHSEED=15,
        # where `hash("a") % 97 == 0`. crc32 is stable across processes, and the `+ 1`
        # keeps a legitimately-encoded item from ever looking like the zero vector that
        # `test_a_zero_vector_stays_zero_rather_than_becoming_nan` covers deliberately.
        return np.stack([
            np.full(self._output_dim, float(zlib.crc32(str(i).encode("utf-8")) % 97 + 1))
            for i in items
        ])


# --- caching ----------------------------------------------------------------------


def test_a_repeated_input_is_not_re_encoded():
    """A rollout revisits the same handful of pages constantly; without this the
    encoders dominate the per-step cost instead of being a rounding error."""
    enc = _Counting()
    enc.encode_batch(["a", "b"])
    enc.encode_batch(["a", "b"])
    assert enc.items_encoded == 2
    assert enc.cache_stats()["hits"] == 2


def test_only_the_misses_reach_the_model():
    """A half-cached batch should cost half a forward pass, not a full one."""
    enc = _Counting()
    enc.encode_batch(["a", "b"])
    enc.items_encoded = 0
    enc.encode_batch(["a", "b", "c", "d"])
    assert enc.items_encoded == 2


def test_a_fully_cached_batch_runs_no_forward_pass_at_all():
    enc = _Counting()
    enc.encode_batch(["a"])
    before = enc.forward_passes
    enc.encode_batch(["a", "a", "a"])
    assert enc.forward_passes == before


def test_the_cache_is_bounded():
    enc = _Counting(cache_size=3)
    enc.encode_batch([str(i) for i in range(10)])
    assert enc.cache_stats()["entries"] == 3


def test_cached_and_fresh_results_are_identical():
    enc = _Counting()
    first = enc.encode_batch(["x", "y"])
    assert np.array_equal(first, enc.encode_batch(["x", "y"]))


# --- contract ---------------------------------------------------------------------


def test_output_is_float32_and_correctly_shaped():
    enc = _Counting(output_dim=7)
    out = enc.encode_batch(["a", "b", "c"])
    assert out.shape == (3, 7)
    assert out.dtype == np.float32


def test_a_width_mismatch_raises_instead_of_corrupting_the_buffer():
    class Wrong(_Counting):
        def _encode_uncached(self, items):
            return np.zeros((len(items), self._output_dim + 1))

    with pytest.raises(ValueError, match="width mismatch|expected"):
        Wrong().encode_batch(["a"])


def test_batch_order_is_preserved():
    """Encoding only the misses reorders work internally; results must not follow."""
    enc = _Counting()
    enc.encode_batch(["b"])  # prime the cache out of order
    out = enc.encode_batch(["a", "b", "c"])
    assert np.array_equal(out[1], enc.encode_batch(["b"])[0])


def test_the_stub_still_satisfies_the_same_contract():
    """`HashEmbeddingEncoder` remains the offline/no-GPU path, so it must not drift."""
    out = HashEmbeddingEncoder(11).encode_batch(["a", "b"])
    assert out.shape == (2, 11) and out.dtype == np.float32


# --- the real encoders ------------------------------------------------------------


def _weights_cached(repo: str) -> bool:
    from pathlib import Path

    hub = Path.home() / ".cache" / "huggingface" / "hub"
    return (hub / f"models--{repo.replace('/', '--')}").is_dir()


needs_codebert = pytest.mark.skipif(
    not _weights_cached("microsoft/codebert-base"),
    reason="CodeBERT weights not cached; run scripts/compare_agents.py once to fetch them",
)


@needs_codebert
def test_centering_is_what_makes_codebert_discriminate():
    """Regression for a measured design correction to the spec.

    The spec calls for CodeBERT's `[CLS]` token. Measured on 18 real fixture pages it
    produced pairwise cosine similarity min 0.973 / mean 0.991 — every page looked like
    every other page, making it barely better than the hash stub it replaced. Raw
    transformer embeddings are anisotropic: they occupy a narrow cone. Mean pooling plus
    a fixed centering vector restores real spread.
    """
    from web_testing_agent.perception.encoders.models import TransformerTextEncoder

    pages = [
        "<title>Sign in</title><form><input name=user><input name=pass></form>",
        "<title>Order step 2</title><form><input name=quantity type=number></form>",
        "<title>Terms of service</title><p>Standard commercial terms apply.</p>",
        "<title>Order confirmed</title><p>Thank you, your order has been placed.</p>",
    ]

    def mean_offdiagonal(vectors):
        unit = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        sim = unit @ unit.T
        return sim[~np.eye(len(unit), dtype=bool)].mean()

    specced = TransformerTextEncoder(pooling="cls", center=False).encode_batch(pages)
    shipped = TransformerTextEncoder().encode_batch(pages)
    assert mean_offdiagonal(specced) > 0.9, "the spec's config should be near-degenerate"
    assert mean_offdiagonal(shipped) < mean_offdiagonal(specced) - 0.1


@needs_codebert
def test_the_centering_vector_does_not_depend_on_the_batch():
    """Per-batch centering would make the observation depend on which other envs were
    in the batch — a non-stationary observation, which is the defect behind the
    peak-then-collapse training curve in PROJECT_CONTEXT 5."""
    from web_testing_agent.perception.encoders.models import TransformerTextEncoder

    enc = TransformerTextEncoder()
    alone = enc.encode_batch(["<title>A</title>"])
    enc._cache.clear()
    with_company = enc.encode_batch(["<title>A</title>", "<title>Something else entirely</title>"])
    assert np.allclose(alone[0], with_company[0], atol=1e-5)


def test_a_batch_larger_than_the_cache_still_returns_every_row():
    """Regression. A batch bigger than the cache evicts its own earlier entries while
    it is still being filled, so assembling the result from the cache raised KeyError
    on exactly the inputs it had just encoded — harmless at one env, a hard crash at
    eight."""
    enc = _Counting(cache_size=3)
    out = enc.encode_batch([str(i) for i in range(10)])
    assert out.shape == (10, enc.output_dim)


def test_outputs_are_unit_norm_by_default():
    """The rest of the system assumes it. `HashEmbeddingEncoder` emitted unit vectors and
    `WebTestingEnv._episode_context` scales its features into [0,1] explicitly "so no
    single term dominates the input scale of a network whose other 1664 dims are
    unit-norm embeddings". Real CLIP embeddings have norm ~11 and CodeBERT's centered
    mean-pool ~2.7, so without this the visual block outweighs everything else by an
    order of magnitude — measured as the masked agent's mean flow depth falling
    0.33 -> 0.00 when the stubs were swapped for real models."""
    enc = _Counting(output_dim=5)
    out = enc.encode_batch(["a", "b", "c"])
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)


def test_a_zero_vector_stays_zero_rather_than_becoming_nan():
    """An empty network trace legitimately encodes to zero; 'nothing happened' is an
    observation, not a division by zero."""

    class Zero(_Counting):
        def _encode_uncached(self, items):
            return np.zeros((len(items), self._output_dim))

    out = Zero().encode_batch(["a"])
    assert np.isfinite(out).all() and not out.any()


def test_normalization_can_be_switched_off_for_inspection():
    enc = _Counting(output_dim=5)
    enc._normalize = False
    assert not np.allclose(np.linalg.norm(enc.encode_batch(["a"]), axis=1), 1.0)
