"""The frozen-definition guarantees A1, A2 and the out-of-list probe all depend on.

These tests are not about behaviour under load or numerical accuracy. Each one pins a
*claim a report makes*, so that the claim breaks loudly here rather than quietly in a
write-up:

* A1's word list is the one the methodology document froze, and still has the shape that
  document states (21 positive, 14 negative tokens).
* A1 is an indicator, as frozen -- not the count variant `probe_anchor_audit.py` shipped.
  Both forms exist, and the discrepancy is pinned rather than papered over.
* The out-of-list probe's head words are absent from A1, which is the entire premise of
  the probe. If anyone ever "improves" A1 by adding `finalise`, this file fails.
* Tier 1 of the probe contains no A1 token at all, and tier 2's lexical signal is matched
  across its two categories. Both are load-bearing: the probe's AUCs are meaningless if
  either rule is violated.
* Adding `label_prior` to `HandPriorityPolicy` did not change A0. A0's published numbers
  were produced before that parameter existed.
"""

from __future__ import annotations

import numpy as np
import pytest

from web_testing_agent.agents.hand_priority import HandPriorityPolicy
from web_testing_agent.agents.label_priors import (
    A1_NEGATIVE_TOKENS,
    A1_POSITIVE_TOKENS,
    A2_NEGATIVE_ANCHORS,
    A2_POSITIVE_ANCHORS,
    W_LABEL_PRIOR,
    LexicalCommitPrior,
    a1_lexical_count_score,
    a1_lexical_score,
    anchor_axis_scores,
    tokenize,
)
from web_testing_agent.envs.types import ActionSpec, ActionType

# Imported from the probe script rather than restated, so the test cannot pass against a
# copy of the label sets that has drifted from the one the probe actually scores.
pytest.importorskip("numpy")
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from probe_out_of_list_vocabulary import (  # noqa: E402
    REQUIRED_OUT_OF_LIST_HEAD_WORDS,
    TIER1_COMMIT,
    TIER1_NAVIGATE,
    TIER2_COMMIT,
    TIER2_NAVIGATE,
)

A1_VOCABULARY = A1_POSITIVE_TOKENS | A1_NEGATIVE_TOKENS


# -- the frozen A1 vocabulary --------------------------------------------------------------


def test_a1_vocabulary_matches_the_frozen_document():
    """21 positive and 14 negative tokens, exactly as `2026-09-18-a0-a1-a2-frozen.md` says.

    The counts are quoted in that document's commit message, so a silent addition here
    would make the commit message false.
    """
    assert len(A1_POSITIVE_TOKENS) == 21
    assert len(A1_NEGATIVE_TOKENS) == 14
    assert not A1_POSITIVE_TOKENS & A1_NEGATIVE_TOKENS
    assert "submit" in A1_POSITIVE_TOKENS
    assert "cancel" in A1_NEGATIVE_TOKENS


def test_a1_is_an_indicator_not_a_tally():
    """`Place order` carries two positive tokens and still scores +1, as frozen.

    The frozen document says "1.0 when the label contains *any* token ... 0.0 otherwise"
    and works the example `Cancel order -> +1.2 - 1.2 = 0`. `probe_anchor_audit.py`
    implemented a count instead; both live in `label_priors` and this pins which is which,
    so the published audit number stays reproducible without the policy arm inheriting it.
    """
    assert a1_lexical_score("Place order") == 1.0
    assert a1_lexical_count_score("Place order") == 2.0
    assert a1_lexical_score("Cancel order") == 0.0
    assert a1_lexical_score("Continue") == 1.0
    assert a1_lexical_score("Back") == -1.0
    assert a1_lexical_score("") == 0.0


def test_a1_does_not_stem_and_the_probe_depends_on_it():
    """`payment` is not `pay` and `finalise` is not `finish`. Pinned, because the probe's
    whole premise is that the word list is literal."""
    assert a1_lexical_score("Authorise the payment") == 0.0
    assert a1_lexical_score("Finalise the booking") == 0.0
    assert a1_lexical_score("Ordering") == 0.0
    assert a1_lexical_score("ORDER") == 1.0  # case only, still a match


def test_tokenize_splits_on_non_alphanumerics():
    assert tokenize("Confirm & place-order!") == {"confirm", "place", "order"}
    assert tokenize("  ") == set()


# -- the out-of-list probe's premise ---------------------------------------------------------


@pytest.mark.parametrize("word", REQUIRED_OUT_OF_LIST_HEAD_WORDS)
def test_required_head_words_are_absent_from_a1(word):
    """The four head words the advisor named must not be in A1's list.

    This is the probe's premise, asserted programmatically as the handoff requires. If a
    future change adds one of them to A1, `reports/probe_out_of_list_vocabulary.*` stops
    measuring what it says it measures, and this test is the thing that notices.
    """
    assert word not in A1_VOCABULARY


def test_tier1_is_fully_outside_a1():
    """Every token of every tier-1 label is outside A1's vocabulary.

    Not just the head word: a label like `Finalise the order` would leak `order` into a
    tier that claims to have no lexical signal at all, and A1's AUC of 0.5 would then be a
    coincidence rather than a construction.
    """
    offenders = {label: sorted(tokenize(label) & A1_VOCABULARY)
                 for label in TIER1_COMMIT + TIER1_NAVIGATE
                 if tokenize(label) & A1_VOCABULARY}
    assert not offenders, f"tier 1 labels carry A1 tokens: {offenders}"


def test_tier1_a1_scores_are_identically_zero():
    """The consequence of the rule above, stated as the number the report prints."""
    scores = [a1_lexical_score(label) for label in TIER1_COMMIT + TIER1_NAVIGATE]
    assert scores == [0.0] * 24
    assert [a1_lexical_count_score(label) for label in TIER1_COMMIT + TIER1_NAVIGATE] \
        == [0.0] * 24


def test_tier2_lexical_signal_is_matched_across_categories():
    """Tier 2's word-list signal must be identical on both sides, or A1's 0.5 is an artefact.

    Each label carries exactly one A1 positive token and no negative one, and the two
    categories draw the same tokens in the same proportions -- so the word list is loud,
    and uninformative, which is the realistic partial-coverage regime tier 2 exists for.
    """
    def tokens(labels):
        return sorted(t for label in labels for t in sorted(tokenize(label) & A1_VOCABULARY))

    assert tokens(TIER2_COMMIT) == tokens(TIER2_NAVIGATE)
    for label in TIER2_COMMIT + TIER2_NAVIGATE:
        assert a1_lexical_score(label) == 1.0, label
        assert not tokenize(label) & A1_NEGATIVE_TOKENS, label


def test_tier2_head_verbs_are_outside_a1():
    """The first token of every tier-2 label -- its verb -- is still out of the word list."""
    for label in TIER2_COMMIT + TIER2_NAVIGATE:
        head = label.split()[0].lower()
        assert head not in A1_VOCABULARY, label


def test_probe_tiers_have_the_sizes_the_report_claims():
    assert (len(TIER1_COMMIT), len(TIER1_NAVIGATE)) == (12, 12)
    assert (len(TIER2_COMMIT), len(TIER2_NAVIGATE)) == (8, 8)
    assert len(set(TIER1_COMMIT + TIER1_NAVIGATE + TIER2_COMMIT + TIER2_NAVIGATE)) == 40


# -- A2's scorer ------------------------------------------------------------------------------


def test_a2_anchor_axis_is_positive_minus_negative_max():
    """The arithmetic, on vectors chosen so the answer is known without a model loaded.

    Rows are L2-normalised inside the scorer, so the second label's positive term is
    `0.1 / ||[0.9, 0.1]|| = 0.1104`, not `0.1`: the test states the normalised value
    because the un-normalised one would pass against a scorer that forgot to normalise.
    """
    positive = np.array([[1.0, 0.0], [0.9, 0.1]])
    negative = np.array([[0.0, 1.0]])
    labels = np.array([[1.0, 0.0], [0.0, 1.0]])
    scores = anchor_axis_scores(labels, positive, negative)
    assert scores[0] == pytest.approx(1.0)
    assert scores[1] == pytest.approx(0.1 / np.hypot(0.9, 0.1) - 1.0)
    assert scores[0] > scores[1]


def test_a2_anchor_sets_are_the_frozen_six_and_six():
    assert len(A2_POSITIVE_ANCHORS) == 6
    assert len(A2_NEGATIVE_ANCHORS) == 6
    assert A2_POSITIVE_ANCHORS[0] == "submit the form"
    assert A2_NEGATIVE_ANCHORS[0] == "cancel and discard changes"


# -- A0 is unchanged by A1/A2 existing ----------------------------------------------------------


def _specs():
    return [
        ActionSpec(index=0, action_type=ActionType.CLICK, element_id="Place order",
                   params={"navigational": True, "in_viewport": True, "href": "receipt.html"}),
        ActionSpec(index=1, action_type=ActionType.CLICK, element_id="Cancel order",
                   params={"navigational": True, "in_viewport": True, "href": "index.html"}),
        ActionSpec(index=2, action_type=ActionType.CLICK, element_id="Back",
                   params={"navigational": True, "in_viewport": True, "href": "order-3.html"}),
    ]


def _info(specs):
    return {"action_specs": specs, "newly_revealed": [], "state_key": "k"}


def test_default_policy_is_a0_and_takes_no_prior_branch():
    """The default constructor is A0: no prior, zero weight, and the same choice as before.

    A0's published rows were produced before `label_prior` existed. If the default ever
    starts scoring a prior, every one of those rows becomes unreproducible.
    """
    policy = HandPriorityPolicy(epsilon=0.0, seed=0)
    assert policy.label_prior is None
    assert policy.label_prior_weight == 0.0
    specs = _specs()
    # A0 cannot tell these three apart on the label; all its readable features are equal,
    # so it takes the registry's own priority order -- the lowest index.
    assert policy.act({}, _info(specs)) == 0
    assert policy.to_dict()["label_block_weight_l1"] == 0.0


def test_a1_prior_reorders_what_a0_cannot_separate():
    """On three otherwise identical navigational links, A1 promotes the commit label.

    Deliberately checked on `Cancel order` versus `Back`: A1 scores the first 0.0 (its
    positive and negative tokens cancel) and the second -1.0, which is the behaviour the
    frozen document's `Cancel order` example specifies.
    """
    policy = HandPriorityPolicy(epsilon=0.0, seed=0, label_prior=LexicalCommitPrior(),
                                label_prior_weight=W_LABEL_PRIOR)
    specs = _specs()
    assert policy.act({}, _info(specs)) == 0
    assert a1_lexical_score("Place order") > a1_lexical_score("Cancel order") \
        > a1_lexical_score("Back")


def test_a1_prior_can_change_the_choice_a0_makes():
    """A1 is not decorative: with the commit control placed last, A0 takes index 0 and A1
    does not. If this ever stops being true the arm is not varying anything."""
    specs = [
        ActionSpec(index=0, action_type=ActionType.CLICK, element_id="Back",
                   params={"navigational": True, "in_viewport": True, "href": "a.html"}),
        ActionSpec(index=1, action_type=ActionType.CLICK, element_id="Cancel order",
                   params={"navigational": True, "in_viewport": True, "href": "b.html"}),
        ActionSpec(index=2, action_type=ActionType.CLICK, element_id="Place order",
                   params={"navigational": True, "in_viewport": True, "href": "c.html"}),
    ]
    assert HandPriorityPolicy(epsilon=0.0, seed=0).act({}, _info(specs)) == 0
    a1 = HandPriorityPolicy(epsilon=0.0, seed=0, label_prior=LexicalCommitPrior(),
                            label_prior_weight=W_LABEL_PRIOR)
    assert a1.act({}, _info(specs)) == 2


def test_a1_reads_the_same_label_expression_the_hash_block_does():
    """`element_id or description`, so A1 is not given richer text than A0 refuses to read."""
    seen: list[str] = []

    def spy(label: str) -> float:
        seen.append(label)
        return 0.0

    policy = HandPriorityPolicy(epsilon=0.0, seed=0, label_prior=spy, label_prior_weight=1.2)
    specs = [ActionSpec(index=0, action_type=ActionType.CLICK, element_id="",
                        description="fallback text"),
             ActionSpec(index=1, action_type=ActionType.CLICK, element_id="Place order",
                        description="ignored")]
    policy.act({}, _info(specs))
    assert seen == ["fallback text", "Place order"]


def test_label_prior_without_a_weight_is_refused():
    with pytest.raises(ValueError, match="label_prior_weight"):
        HandPriorityPolicy(label_prior=LexicalCommitPrior(), label_prior_weight=0.0)


def test_label_block_stays_weighted_zero_for_every_arm():
    """The anti-cheating guarantee is unchanged by the prior: a prior reads the label
    string, it does not give the hashed block a non-zero weight."""
    for prior, weight in ((None, 0.0), (LexicalCommitPrior(), W_LABEL_PRIOR)):
        policy = HandPriorityPolicy(label_prior=prior, label_prior_weight=weight)
        assert policy.to_dict()["label_block_weight_l1"] == 0.0
