"""The v2 action representation: semantics reach the policy, identity does not.

Two claims carry the whole design, and both are tested from several directions rather
than asserted in a docstring:

1. **No identity reaches the policy.** Not a URL, not a DOM id, not a CSS selector, not an
   element index, not a slot number. The strong form of this is an *invariance* test: the
   same control on two applications that spell their ids and selectors differently must
   encode identically.
2. **Semantics do reach it, and they generalise.** Cross-vocabulary synonyms must land
   close together on vocabulary the projection was never fitted on, or the representation
   buys nothing the trigram hash did not already buy.

The semantic tests load a real frozen encoder and are marked `requires_torch`, so a
machine without the deep-learning stack still runs the structural half.
"""

from __future__ import annotations

import numpy as np
import pytest

from web_testing_agent.agents import semantic_action_features as saf
from web_testing_agent.agents.semantic_action_features import (
    FLAG_SLICE,
    LABEL_SLICE,
    NUMERIC_SLICE,
    ROLE_SLICE,
    ROLES,
    SEMANTIC_ACTION_FEATURE_DIM,
    TYPE_SLICE,
    SemanticActionFeatureExtractor,
    action_identity,
    label_of,
    role_of,
)
from web_testing_agent.envs.types import MAX_ACTIONS, ActionSpec, ActionType


def spec(index=0, action_type=ActionType.CLICK, label="Continue", selector=None, **params):
    return ActionSpec(
        index=index, action_type=action_type,
        selector=selector if selector is not None else f'[id="e{index}"]',
        element_id=label, params=params, description=f"{action_type.value} {label}",
    )


class _StubLabels:
    """A deterministic stand-in for the frozen encoder, for the structural tests.

    Returns a unit vector determined by the label text, so identical labels match and
    different ones generally do not. It is *not* semantic, and no test that depends on
    meaning uses it.
    """

    dim = saf.LABEL_DIM

    def __init__(self) -> None:
        self._cache: dict[str, np.ndarray] = {}

    def encode(self, label: str) -> np.ndarray:
        return self.encode_batch([label])[0]

    def encode_batch(self, labels):  # noqa: ANN001
        out = []
        for label in labels:
            text = (label or "").strip()
            if text not in self._cache:
                if not text:
                    self._cache[text] = np.zeros(self.dim, dtype=np.float32)
                else:
                    rng = np.random.default_rng(abs(hash(text)) % (2**32))
                    vector = rng.standard_normal(self.dim).astype(np.float32)
                    self._cache[text] = vector / np.linalg.norm(vector)
            out.append(self._cache[text])
        return np.stack(out)

    def warm(self, labels) -> None:  # noqa: ANN001
        self.encode_batch(labels)


@pytest.fixture
def extractor() -> SemanticActionFeatureExtractor:
    return SemanticActionFeatureExtractor(labels=_StubLabels())


# -- shape and layout --------------------------------------------------------------------


def test_the_block_has_the_declared_shape(extractor):
    block = extractor.encode([spec(0), spec(1)], "state", set())
    assert block.shape == (MAX_ACTIONS, SEMANTIC_ACTION_FEATURE_DIM)
    assert block.dtype == np.float32


def test_the_blocks_tile_the_vector_without_gaps_or_overlap():
    slices = [TYPE_SLICE, LABEL_SLICE, ROLE_SLICE, FLAG_SLICE, NUMERIC_SLICE]
    assert slices[0].start == 0
    for left, right in zip(slices, slices[1:], strict=False):
        assert left.stop == right.start
    assert slices[-1].stop == SEMANTIC_ACTION_FEATURE_DIM


def test_padding_rows_past_the_valid_actions_are_all_zero(extractor):
    block = extractor.encode([spec(0), spec(1), spec(2)], "state", set())
    assert np.count_nonzero(block[3:]) == 0
    assert np.count_nonzero(block[:3]) > 0


def test_an_empty_action_set_encodes_to_all_zeros(extractor):
    assert np.count_nonzero(extractor.encode([], "state", set())) == 0


def test_more_actions_than_slots_are_truncated_not_wrapped(extractor):
    block = extractor.encode([spec(i) for i in range(MAX_ACTIONS + 25)], "s", set())
    assert block.shape[0] == MAX_ACTIONS


# -- the identity guarantee ---------------------------------------------------------------


def test_a_selector_is_never_embedded_as_a_label():
    """`label_of` must refuse identity strings; embedding one is the leak this prevents."""
    for selector_like in ('[id="nav-home"]', "[name=\"q\"]", "#submit", ".btn-primary",
                          "input, textarea, select, button, a[href] >> nth=12"):
        action = ActionSpec(index=0, action_type=ActionType.CLICK,
                            selector=selector_like, element_id=selector_like,
                            params={}, description="click something")
        assert label_of(action) == "click something"


def test_the_same_control_encodes_identically_across_two_applications(extractor):
    """The strong form: differing ids, selectors and slot positions must not change it.

    This is what "the same scorer works across applications" requires of the input.
    """
    app_a = ActionSpec(index=3, action_type=ActionType.CLICK, selector='[id="checkout-btn"]',
                       element_id="Place order", params={"navigational": True,
                                                         "in_viewport": True, "href": "r.html"},
                       description="click 'Place order'")
    app_b = ActionSpec(index=41, action_type=ActionType.CLICK,
                       selector='[id="wizard_step4_commit"]',
                       element_id="Place order", params={"navigational": True,
                                                         "in_viewport": True, "href": "done.php"},
                       description="click 'Place order'")
    first = extractor.encode([app_a], "state-a", set())[0]
    second = extractor.encode([app_b], "state-b-totally-different", set())[0]
    assert np.allclose(first, second)


def test_the_slot_position_does_not_change_an_actions_encoding(extractor):
    """Permutation check: the vector for an action must not depend on where it sits."""
    actions = [spec(0, label="Alpha"), spec(1, label="Beta"), spec(2, label="Gamma")]
    straight = extractor.encode(actions, "s", set())
    reversed_block = extractor.encode(list(reversed(actions)), "s", set())
    for index in range(3):
        assert np.allclose(straight[index], reversed_block[2 - index])


def test_the_encoded_vector_is_invariant_to_the_url_the_action_was_seen_on(extractor):
    """The URL is bookkeeping for the reveal rule; it must never reach the vector.

    Behavioural rather than a source scan, because `_previous_url` legitimately exists —
    `observe_action_set` compares URLs to decide whether a navigation happened, exactly as
    `ExplorationTracker` does. What matters is that the comparison changes *reveals*, and
    never a feature.
    """
    action = spec(0, label="Continue", href="next.html")
    first = SemanticActionFeatureExtractor(labels=_StubLabels())
    second = SemanticActionFeatureExtractor(labels=_StubLabels())
    first.observe_action_set([action], "http://shop.example/checkout?session=abc123")
    second.observe_action_set([action], "https://other.test/v2/step/4")
    assert np.allclose(first.encode([action], "state-x", set())[0],
                       second.encode([action], "state-y", set())[0])


def test_no_identity_hashing_in_the_executable_source():
    """A last line of defence against a helpful edit that reintroduces a hashed feature.

    Scans code with docstrings stripped. Targets *hashing primitives and identity
    features* specifically rather than any occurrence of the word "url": the module
    legitimately compares URLs to detect navigation, and a scan broad enough to forbid
    that would have to be switched off, which is worse than a narrow one that stays on.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(saf))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            node.body.pop(0)
            if not node.body:
                node.body.append(ast.Pass())
    code = ast.unparse(tree).lower()
    for forbidden in ("blake2b", "sha256", "md5", "hashlib", "trigram",
                      "app_id", "application_id", "slot_index", "spec.index"):
        assert forbidden not in code, f"{forbidden!r} appears in the v2 representation"

    # v1's `LabelEncoder` is the trigram hash. Checked as an import rather than as a
    # substring, because `SemanticLabelEncoder` contains the same characters and a
    # substring rule would either fire on it or have to be switched off.
    imported = {
        f"{node.module}.{alias.name}"
        for node in ast.walk(ast.parse(inspect.getsource(saf)))
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }
    assert not any(name.endswith("action_features.LabelEncoder") for name in imported), \
        f"v2 imports the v1 hashing label encoder: {sorted(imported)}"


def test_identity_is_still_available_for_counting_but_is_not_a_feature(extractor):
    """`action_identity` exists and is used for counts; the identity itself never ships."""
    action = spec(0, label="Place order", selector='[id="x"]')
    assert action_identity(action) == ("CLICK", "Place order")
    vector = extractor.encode([action], "s", set())[0]
    # Only the two count slots move when the action is taken; nothing encodes the tuple.
    before = vector.copy()
    extractor.record_taken("s", action)
    after = extractor.encode([action], "s", set())[0]
    changed = np.flatnonzero(~np.isclose(before, after))
    assert changed.size > 0
    assert all(NUMERIC_SLICE.start <= i < NUMERIC_SLICE.stop for i in changed)


# -- roles ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("action_type", "params", "expected"), [
    (ActionType.CLICK, {"href": "a.html"}, "link"),
    (ActionType.CLICK, {"navigational": True}, "submit_control"),
    (ActionType.CLICK, {}, "button"),
    (ActionType.SELECT, {"option": "x"}, "select"),
    (ActionType.TYPE, {"category": "valid_typical"}, "text_entry"),
    (ActionType.RAPID_CLICK, {}, "rapid_click"),
    (ActionType.BROWSER_BACK, {}, "navigation_move"),
    (ActionType.BROWSER_FORWARD, {}, "navigation_move"),
    (ActionType.REFRESH, {}, "page_reload"),
    (ActionType.SCROLL, {"direction": "down"}, "viewport"),
    (ActionType.RESIZE_VIEWPORT, {"preset": "mobile"}, "viewport"),
    (ActionType.NO_OP, {}, "no_op"),
])
def test_roles_are_derived_from_the_spec_alone(action_type, params, expected):
    assert role_of(spec(0, action_type=action_type, **params)) == expected


def test_every_role_is_reachable_and_one_hot(extractor):
    assert len(set(ROLES)) == len(ROLES)
    block = extractor.encode([spec(0, action_type=ActionType.CLICK, href="a.html")], "s", set())
    role_block = block[0][ROLE_SLICE]
    assert role_block.sum() == pytest.approx(1.0)


# -- flags and numerics ---------------------------------------------------------------------


def test_newly_revealed_is_set_only_for_the_revealed_action(extractor):
    ordinary = spec(0, label="Terms", href="t.html")
    revealed = spec(1, label="Continue", href="c.html")
    block = extractor.encode([ordinary, revealed], "s", {action_identity(revealed)})
    revealed_index = list(saf.FLAG_FIELDS).index("is_newly_revealed")
    assert block[0][FLAG_SLICE][revealed_index] == 0.0
    assert block[1][FLAG_SLICE][revealed_index] == 1.0


def test_every_numeric_feature_stays_bounded(extractor):
    action = spec(0, label="A very long control label " * 10, category="valid_typical")
    for _ in range(500):
        extractor.record_taken("s", action)
    numerics = extractor.encode([action], "s", set())[0][NUMERIC_SLICE]
    assert np.all(numerics >= 0.0) and np.all(numerics <= 1.0)


def test_counts_are_scoped_to_the_state_and_reset_per_episode(extractor):
    action = spec(0, label="Continue")
    extractor.record_taken("state-a", action)
    here = list(saf.NUMERIC_FIELDS).index("taken_here")
    episode = list(saf.NUMERIC_FIELDS).index("taken_this_episode")

    at_a = extractor.encode([action], "state-a", set())[0][NUMERIC_SLICE]
    at_b = extractor.encode([action], "state-b", set())[0][NUMERIC_SLICE]
    assert at_a[here] > 0 and at_b[here] == 0.0

    extractor.start_episode()
    after = extractor.encode([action], "state-a", set())[0][NUMERIC_SLICE]
    assert after[here] > 0.0, "run-level count must survive an episode boundary"
    assert after[episode] == 0.0, "episodic count must not"


def test_observe_action_set_matches_the_environments_reveal_rule(extractor):
    first = [spec(0, label="A"), spec(1, label="B")]
    grown = [*first, spec(2, label="C")]
    assert extractor.observe_action_set(first, "http://x/p") == set()          # first look
    assert extractor.observe_action_set(grown, "http://x/p") == {("CLICK", "C")}
    # A navigation re-anchors and pays nothing, even though the set changed.
    assert extractor.observe_action_set([spec(9, label="Z")], "http://x/q") == set()


# -- the semantic half (needs the real frozen encoder) ---------------------------------------


@pytest.mark.requires_torch
def test_cross_vocabulary_synonyms_land_closer_than_unrelated_controls():
    """The property the whole representation exists for, on held-out vocabulary.

    `Dispatch the parcel` / `Send the parcel` never appear in the projection's fit
    vocabulary. If the representation cannot put them closer than an unrelated pair, it
    buys nothing over the trigram hash it replaces.
    """
    from web_testing_agent.agents.semantic_labels import SemanticLabelEncoder

    encoder = SemanticLabelEncoder()
    vectors = encoder.encode_batch([
        "Dispatch the parcel", "Send the parcel", "Read the privacy policy",
    ])
    synonym = float(vectors[0] @ vectors[1])
    unrelated = float(vectors[0] @ vectors[2])
    assert synonym > unrelated + 0.2, (synonym, unrelated)


@pytest.mark.requires_torch
def test_the_projection_is_deterministic_across_encoder_instances():
    from web_testing_agent.agents.semantic_labels import SemanticLabelEncoder

    first = SemanticLabelEncoder().encode("Place order")
    second = SemanticLabelEncoder().encode("Place order")
    assert np.allclose(first, second)
    assert first.shape == (saf.LABEL_DIM,)
    assert float(np.linalg.norm(first)) == pytest.approx(1.0, abs=1e-5)


@pytest.mark.requires_torch
def test_an_empty_label_encodes_to_zero_not_to_the_models_bias_direction():
    from web_testing_agent.agents.semantic_labels import SemanticLabelEncoder

    assert np.count_nonzero(SemanticLabelEncoder().encode("")) == 0


@pytest.mark.requires_torch
def test_the_fit_vocabulary_excludes_the_held_out_probe():
    """If the probe leaked into the fit, the generalization check would be circular."""
    from web_testing_agent.agents.semantic_vocabulary import FIT_VOCABULARY, HELD_OUT_PROBE

    held_out = {label for left, right, _ in HELD_OUT_PROBE for label in (left, right)}
    assert not (held_out & set(FIT_VOCABULARY))


# -- layout description ----------------------------------------------------------------------


def test_describe_layout_matches_the_real_vector(extractor):
    layout = saf.describe_layout()
    assert layout["total_dim"] == SEMANTIC_ACTION_FEATURE_DIM
    assert sum(block["dim"] for block in layout["blocks"]) == SEMANTIC_ACTION_FEATURE_DIM
    assert layout["blocks"][-1]["stop"] == SEMANTIC_ACTION_FEATURE_DIM
    assert "hashed label" in layout["excluded_by_design"]


def test_v1_is_untouched_and_still_52_wide():
    """v2 is parallel, not a replacement: every published figure was measured through v1."""
    from web_testing_agent.agents.action_features import ACTION_FEATURE_DIM

    assert ACTION_FEATURE_DIM == 52
    assert SEMANTIC_ACTION_FEATURE_DIM == 80
