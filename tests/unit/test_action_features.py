"""Per-action features: the representation that has to transfer across applications.

The flat head scored a fixed 100-slot output, so slot 47 meant "click login" on one page
and "type a boundary value" on the next — it could only memorise per page, and nothing it
learned could carry to an unseen application. These tests pin the properties that make
the replacement different: features describe the *action*, never its index; the same
control produces the same vector wherever it appears; and the newly-revealed flag fires
exactly when a gate opens.
"""

from __future__ import annotations

import numpy as np
import pytest

from web_testing_agent.agents.action_features import (
    ACTION_FEATURE_DIM,
    LABEL_DIM,
    SCALAR_FIELDS,
    TYPE_DIM,
    ActionFeatureExtractor,
    LabelEncoder,
    action_identity,
)
from web_testing_agent.envs.types import MAX_ACTIONS, ActionSpec, ActionType


def _click(index: int, label: str, **params) -> ActionSpec:
    base = {"navigational": True, "in_viewport": True, "opens_new_tab": False, "href": "x.html"}
    return ActionSpec(index=index, action_type=ActionType.CLICK, selector=f"[id={label}]",
                      element_id=label, params={**base, **params})


def _scalar(vector: np.ndarray, name: str) -> float:
    return float(vector[TYPE_DIM + LABEL_DIM + SCALAR_FIELDS.index(name)])


# -- label encoding ----------------------------------------------------------------

def test_the_same_label_always_encodes_identically():
    enc = LabelEncoder()
    assert np.array_equal(enc.encode("Continue"), enc.encode("Continue"))
    assert np.array_equal(enc.encode("Continue"), enc.encode("  continue  "))


def test_different_labels_encode_differently():
    enc = LabelEncoder()
    assert not np.array_equal(enc.encode("Continue"), enc.encode("Cancel order"))


def test_labels_sharing_text_are_more_similar_than_unrelated_ones():
    """Character-trigram hashing gives partial lexical overlap, which is what lets
    'Continue' and 'Continue >>' be recognised as related controls."""
    enc = LabelEncoder()
    base = enc.encode("Continue")
    related = float(base @ enc.encode("Continue >>"))
    unrelated = float(base @ enc.encode("Privacy"))
    assert related > unrelated


def test_an_empty_label_is_all_zeros_rather_than_arbitrary():
    assert not LabelEncoder().encode("").any()


def test_label_vectors_are_unit_norm():
    """Every other block reaching this network is unit-norm; an unnormalized one was
    measured to outweigh the others by an order of magnitude."""
    norm = float(np.linalg.norm(LabelEncoder().encode("Place order")))
    assert norm == pytest.approx(1.0, abs=1e-5)


# -- feature block shape and content ------------------------------------------------

def test_the_block_is_zero_padded_past_the_valid_actions():
    extractor = ActionFeatureExtractor()
    block = extractor.encode([_click(0, "Home"), _click(1, "Catalog")], "state")
    assert block.shape == (MAX_ACTIONS, ACTION_FEATURE_DIM)
    assert block[2:].sum() == 0.0
    assert block[:2].any()


def test_action_type_is_one_hot():
    extractor = ActionFeatureExtractor()
    block = extractor.encode([_click(0, "Home")], "state")
    assert block[0][:TYPE_DIM].sum() == pytest.approx(1.0)


def test_structural_flags_are_carried_through():
    extractor = ActionFeatureExtractor()
    spec = _click(0, "Docs", navigational=False, in_viewport=False, opens_new_tab=True)
    row = extractor.encode([spec], "state")[0]
    assert _scalar(row, "navigational") == 0.0
    assert _scalar(row, "in_viewport") == 0.0
    assert _scalar(row, "opens_new_tab") == 1.0
    assert _scalar(row, "has_href") == 1.0


def test_a_fixed_action_is_flagged_as_page_independent():
    extractor = ActionFeatureExtractor()
    refresh = ActionSpec(index=8, action_type=ActionType.REFRESH, description="REFRESH")
    assert _scalar(extractor.encode([refresh], "s")[0], "is_fixed_action") == 1.0
    assert _scalar(extractor.encode([_click(0, "Home")], "s")[0], "is_fixed_action") == 0.0


def test_form_controls_are_distinguished_from_navigation():
    extractor = ActionFeatureExtractor()
    typing = ActionSpec(index=1, action_type=ActionType.TYPE, element_id="quantity",
                        params={"category": "valid_typical", "value": "42"})
    assert _scalar(extractor.encode([typing], "s")[0], "is_form_control") == 1.0
    assert _scalar(extractor.encode([_click(0, "Home")], "s")[0], "is_form_control") == 0.0


def test_type_value_category_is_ordered_by_plausibility():
    extractor = ActionFeatureExtractor()

    def ordinal(category: str) -> float:
        spec = ActionSpec(index=0, action_type=ActionType.TYPE, element_id="q",
                          params={"category": category})
        return _scalar(extractor.encode([spec], "s")[0], "value_ordinal")

    assert ordinal("valid_typical") > ordinal("boundary_max") > ordinal("empty_string")
    assert ordinal("empty_string") > ordinal("type_mismatch")


# -- the identity that matters ------------------------------------------------------

def test_the_same_control_encodes_identically_at_a_different_index():
    """The whole point. Under the flat head these two were unrelated outputs."""
    extractor = ActionFeatureExtractor()
    early = extractor.encode([_click(9, "Continue")], "state")[0]
    late = extractor.encode([_click(47, "Continue")], "state")[0]
    assert np.array_equal(early, late)


def test_typing_different_categories_into_one_field_are_different_actions():
    a = ActionSpec(index=0, action_type=ActionType.TYPE, element_id="qty",
                   params={"category": "valid_typical"})
    b = ActionSpec(index=1, action_type=ActionType.TYPE, element_id="qty",
                   params={"category": "type_mismatch"})
    assert action_identity(a) != action_identity(b)


# -- visit counts -------------------------------------------------------------------

def test_taking_an_action_raises_its_recorded_count():
    extractor = ActionFeatureExtractor()
    spec = _click(0, "Continue")
    before = _scalar(extractor.encode([spec], "state")[0], "taken_here")
    for _ in range(5):
        extractor.record_taken("state", spec)
    after = _scalar(extractor.encode([spec], "state")[0], "taken_here")
    assert before == 0.0
    assert after > before


def test_counts_are_kept_per_state_not_globally():
    extractor = ActionFeatureExtractor()
    spec = _click(0, "Continue")
    extractor.record_taken("state-a", spec)
    assert extractor.visit_count("state-a", spec) == 1
    assert extractor.visit_count("state-b", spec) == 0


def test_the_episode_count_resets_but_the_run_count_does_not():
    extractor = ActionFeatureExtractor()
    spec = _click(0, "Continue")
    extractor.record_taken("s", spec)
    extractor.start_episode()
    row = extractor.encode([spec], "s")[0]
    assert _scalar(row, "taken_this_episode") == 0.0
    assert _scalar(row, "taken_here") > 0.0


def test_visit_counts_are_bounded():
    """An unbounded input would dominate a network whose other blocks are unit-norm."""
    extractor = ActionFeatureExtractor()
    spec = _click(0, "Continue")
    for _ in range(10_000):
        extractor.record_taken("s", spec)
    row = extractor.encode([spec], "s")[0]
    assert 0.0 <= _scalar(row, "taken_here") <= 1.0


# -- the gate signal ----------------------------------------------------------------

def test_newly_revealed_marks_exactly_the_action_a_gate_opened():
    """Measured on the deep-flow fixture: the gate-opening SELECT on order-1.html
    reveals precisely {('CLICK', 'Continue')} and nothing disappears."""
    extractor = ActionFeatureExtractor()
    before = [_click(0, "Home"), _click(1, "Cancel order")]
    after = before + [_click(2, "Continue")]

    extractor.start_episode()
    assert extractor.observe_action_set(before) == set()
    revealed = extractor.observe_action_set(after)
    assert revealed == {("CLICK", "Continue")}

    rows = extractor.encode(after, "state", revealed)
    assert _scalar(rows[2], "is_newly_revealed") == 1.0
    assert _scalar(rows[0], "is_newly_revealed") == 0.0


def test_nothing_is_newly_revealed_on_the_first_step_of_an_episode():
    """Otherwise every landing page reads as a gate opening."""
    extractor = ActionFeatureExtractor()
    extractor.start_episode()
    assert extractor.observe_action_set([_click(0, "Home")]) == set()


def test_an_action_that_stays_available_is_not_re_revealed():
    extractor = ActionFeatureExtractor()
    extractor.start_episode()
    specs = [_click(0, "Home"), _click(1, "Continue")]
    extractor.observe_action_set(specs)
    assert extractor.observe_action_set(specs) == set()
