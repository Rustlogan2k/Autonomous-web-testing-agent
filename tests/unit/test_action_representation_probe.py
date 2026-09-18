"""Tests for the action-representation probe's own arithmetic.

The probe exists to answer a question about the production encoder, so the thing that
must be trustworthy is the measurement, not the encoder. These pin the parts that could
silently produce a wrong number: the cosine, the zero-vector case that
`LabelEncoder.encode("")` really does return, determinism under the fixed seed, and the
guarantee that probing does not disturb the encoder or the global RNG.

Nothing here tests the *result* — that is a measurement, not an invariant, and pinning it
would turn a finding into a fixture.
"""

from __future__ import annotations

import importlib.util
import itertools
import random
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "probe_action_representation", REPO_ROOT / "scripts" / "probe_action_representation.py"
)
probe = importlib.util.module_from_spec(_spec)
sys.modules["probe_action_representation"] = probe
_spec.loader.exec_module(probe)

from web_testing_agent.agents.action_features import LABEL_DIM, LabelEncoder  # noqa: E402

# -- cosine ---------------------------------------------------------------------------

def test_identical_vectors_are_similarity_one():
    v = np.array([0.3, -0.7, 0.2], dtype=np.float32)
    assert probe.cosine(v, v) == pytest.approx(1.0)


def test_opposite_and_orthogonal_vectors():
    v = np.array([1.0, 0.0], dtype=np.float32)
    assert probe.cosine(v, -v) == pytest.approx(-1.0)
    assert probe.cosine(v, np.array([0.0, 1.0], dtype=np.float32)) == pytest.approx(0.0)


def test_cosine_is_scale_invariant():
    a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert probe.cosine(a, 7.5 * a) == pytest.approx(1.0)


def test_cosine_matches_the_textbook_definition_on_a_hand_case():
    a = np.array([1.0, 1.0], dtype=np.float32)
    b = np.array([1.0, 0.0], dtype=np.float32)
    assert probe.cosine(a, b) == pytest.approx(1.0 / np.sqrt(2.0))


def test_zero_vector_is_defined_as_zero_not_nan():
    """`LabelEncoder.encode("")` really returns all zeros, so this input is reachable."""
    zero = np.zeros(3, dtype=np.float32)
    other = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    for left, right in ((zero, other), (other, zero), (zero, zero)):
        value = probe.cosine(left, right)
        assert value == 0.0
        assert not np.isnan(value)


def test_the_empty_label_really_does_encode_to_a_zero_vector():
    """Pins the precondition the previous test exists for."""
    encoded = LabelEncoder().encode("")
    assert encoded.shape == (LABEL_DIM,)
    assert float(np.linalg.norm(encoded)) == 0.0
    assert probe.cosine(encoded, LabelEncoder().encode("Place order")) == 0.0


# -- the production encoder's shape ---------------------------------------------------

def test_production_encoder_output_dimensionality_is_what_the_probe_assumes():
    encoder = LabelEncoder()
    for label in ("Place order", "Back", "a", "Ballpoint pens, box of 50"):
        vector = encoder.encode(label)
        assert vector.shape == (LABEL_DIM,) == (32,)
        assert vector.dtype == np.float32
        assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-6)


# -- helpers the report's conclusion rests on -----------------------------------------

def test_trigrams_reproduce_the_encoders_own_padding():
    """The stratification is only meaningful if `trigrams` matches what is hashed."""
    assert probe.trigrams("ab") == {"  a", " ab", "ab ", "b  "}
    assert probe.trigrams("") == set()
    assert probe.trigrams("  Back  ") == probe.trigrams("back")


def test_jaccard_and_tokens():
    assert probe.jaccard(set(), set()) == 0.0
    assert probe.jaccard({"a"}, {"a"}) == 1.0
    assert probe.jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert probe.tokens("Place  order") == {"place", "order"}
    assert probe.tokens("") == set()


def test_describe_reports_an_empty_group_without_raising():
    assert probe.describe([]) == {"n": 0}
    summary = probe.describe([0.0, 1.0])
    assert summary["n"] == 2 and summary["mean"] == pytest.approx(0.5)


# -- determinism and non-interference -------------------------------------------------

def test_random_pairs_are_reproducible_under_the_fixed_seed():
    encoder = LabelEncoder()
    first = probe.random_pairs(encoder, np.random.default_rng(probe.SEED), 40)
    second = probe.random_pairs(encoder, np.random.default_rng(probe.SEED), 40)
    assert [(p["a"], p["b"], p["cosine"]) for p in first] == \
           [(p["a"], p["b"], p["cosine"]) for p in second]


def test_random_pairs_never_include_a_declared_semantic_pair():
    """Otherwise the control is contaminated by the group it is meant to contrast with."""
    banned = {frozenset((a, b)) for group in probe.SEMANTIC_GROUPS
              for a, b in itertools.combinations(group, 2)}
    banned |= {frozenset(pair) for pair in probe.LEXICAL_TRAPS}
    pairs = probe.random_pairs(LabelEncoder(), np.random.default_rng(probe.SEED), 120)
    assert pairs, "precondition: the sampler produced pairs"
    for pair in pairs:
        assert frozenset((pair["a"], pair["b"])) not in banned


def test_probing_does_not_disturb_the_global_rng_or_the_encoder_cache():
    """The probe must be safe to call from anywhere, and must not reorder its own draws."""
    encoder = LabelEncoder()
    encoder.encode("Place order")
    cache_before = dict(encoder._cache)

    random.seed(1234)
    np.random.seed(1234)
    py_state, np_state = random.getstate(), np.random.get_state()

    probe.experiment_1(encoder)
    probe.random_pairs(encoder, np.random.default_rng(probe.SEED), 20)
    probe.cosine(encoder.encode("Back"), encoder.encode("Next"))

    assert random.getstate() == py_state, "the probe consumed the stdlib RNG"
    after = np.random.get_state()
    assert after[0] == np_state[0] and np.array_equal(after[1], np_state[1]) \
        and after[2:] == np_state[2:], "the probe consumed the legacy numpy global RNG"

    # The encoder memoises, so the cache may grow; what must not change is any answer
    # it had already committed to.
    for label, vector in cache_before.items():
        assert np.array_equal(encoder._cache[label], vector)


def test_experiment_1_labels_every_pair_and_computes_a_real_similarity():
    records = probe.experiment_1(LabelEncoder())
    assert {r["kind"] for r in records} == {"semantic", "lexical_trap"}
    assert len(records) == sum(
        len(g) * (len(g) - 1) // 2 for g in probe.SEMANTIC_GROUPS) + len(probe.LEXICAL_TRAPS)
    for record in records:
        assert -1.0 <= record["cosine"] <= 1.0
        assert 0.0 <= record["trigram_jaccard"] <= 1.0
        assert record["shares_token"] == bool(
            probe.tokens(record["a"]) & probe.tokens(record["b"]))
