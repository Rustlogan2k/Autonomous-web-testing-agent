"""The A0/A1/A2 runner's arm wiring, and the frozen decision rule's arithmetic.

Two classes of defect these exist to catch, both of which would produce a plausible-looking
report full of wrong numbers rather than an error:

**An arm scoring the wrong prior.** `run_search_phase` builds its own A0 policy when none
is passed, so an earlier draft of `a0_a1_a2.py` computed A0's search coverage three times
and labelled the rows A0, A1 and A2. Nothing crashed. `test_search_policy_*` and
`test_build_factories_*` pin every arm to the prior its name claims.

**A decision rule that drifts from the document that froze it.** `DECISION_RULE`
transcribes §5 of `2026-09-18-a0-a1-a2-frozen.md`; the thresholds are checked here against
the numbers that document states, and the verdict logic is exercised on constructed counts
where the right answer is known by hand rather than on a real run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

torch = pytest.importorskip("torch", reason="a0_a1_a2 imports the learned arms")

from a0_a1_a2 import (  # noqa: E402
    ARM_IDENTITY,
    ARM_ORDER,
    DECISION_RULE,
    SEARCH_ARMS,
    apply_decision_rule,
    build_factories,
    search_policy,
)

from web_testing_agent.agents.label_priors import (  # noqa: E402
    W_LABEL_PRIOR,
    LexicalCommitPrior,
)


class StubAnchorPrior:
    """Stands in for A2's prior so no model is downloaded to test the wiring around it."""

    name = "A2_anchor_projection"

    def __call__(self, label: str) -> float:
        return 0.0

    def to_dict(self) -> dict:
        return {"prior": self.name, "projection_fingerprint": "stub"}


# -- arm wiring --------------------------------------------------------------------------


@pytest.mark.parametrize(("arm", "expects_prior"), [
    ("a0_hand_priority", None),
    ("a0_hand_priority_search", None),
    ("a1_lexical", LexicalCommitPrior),
    ("a1_lexical_search", LexicalCommitPrior),
    ("a2_anchor", StubAnchorPrior),
    ("a2_anchor_search", StubAnchorPrior),
])
def test_build_factories_gives_each_arm_the_prior_its_name_claims(arm, expects_prior):
    stub = StubAnchorPrior()
    policy = build_factories(seed=3, encoders=None, anchor_prior=stub)[arm]()
    if expects_prior is None:
        assert policy.label_prior is None
        assert policy.label_prior_weight == 0.0
    else:
        assert isinstance(policy.label_prior, expects_prior)
        assert policy.label_prior_weight == W_LABEL_PRIOR


@pytest.mark.parametrize("arm", ARM_ORDER)
def test_every_declared_arm_has_a_factory(arm):
    assert arm in build_factories(seed=0, encoders=None, anchor_prior=StubAnchorPrior())


def test_the_two_readings_of_each_a_rung_differ_only_in_counter_carrying():
    """A0/A1/A2 each appear twice; the pair must differ in exactly one field."""
    factories = build_factories(seed=0, encoders=None, anchor_prior=StubAnchorPrior())
    for stationary, searching in (("a0_hand_priority", "a0_hand_priority_search"),
                                  ("a1_lexical", "a1_lexical_search"),
                                  ("a2_anchor", "a2_anchor_search")):
        left, right = factories[stationary](), factories[searching]()
        assert left.carry_counts_across_episodes is False
        assert right.carry_counts_across_episodes is True
        assert type(left.label_prior) is type(right.label_prior)
        assert left.label_prior_weight == right.label_prior_weight
        assert left.epsilon == right.epsilon


@pytest.mark.parametrize(("arm", "expects_prior"), [
    ("a0_hand_priority", None),
    ("a1_lexical", LexicalCommitPrior),
    ("a2_anchor", StubAnchorPrior),
])
def test_search_policy_gives_each_rung_the_prior_its_name_claims(arm, expects_prior):
    """The regression test for the bug that made this file: a search phase given no policy
    silently measures A0 three times."""
    policy = search_policy(arm, seed=2, anchor_prior=StubAnchorPrior())
    assert policy.carry_counts_across_episodes is True
    if expects_prior is None:
        assert policy.label_prior is None
    else:
        assert isinstance(policy.label_prior, expects_prior)
        assert policy.label_prior_weight == W_LABEL_PRIOR


def test_search_arms_are_one_per_rung_and_all_have_policies():
    assert len(SEARCH_ARMS) == 3
    assert {ARM_IDENTITY[arm]["rung"] for arm in SEARCH_ARMS} == {"A0", "A1", "A2"}
    for arm in SEARCH_ARMS:
        assert search_policy(arm, seed=0, anchor_prior=StubAnchorPrior()) is not None


# -- the frozen decision rule ------------------------------------------------------------


def test_decision_rule_thresholds_are_the_frozen_ones():
    """§5: within 5 of B on at least 6 of 8 seeds, median at least 60% of B's; 50% for
    'closes most of the gap'. Transcribed, and pinned so it cannot drift."""
    assert DECISION_RULE["match_within"] == 5
    assert DECISION_RULE["match_min_seeds"] == 6
    assert DECISION_RULE["match_median_fraction"] == 0.60
    assert DECISION_RULE["closes_gap_median_fraction"] == 0.50
    assert DECISION_RULE["reference_arm"] == "contextual_bandit"


def _rows(counts, episodes=25):
    return [{"seed": seed, "completion_count": count, "completion_rate": count / episodes}
            for seed, count in enumerate(counts)]


def test_a_matching_arm_is_declared_a_match():
    """A2 within 5 of B on all 8 seeds, median well above 60% of B's."""
    result = apply_decision_rule({
        "a2_anchor": _rows([20, 21, 19, 22, 20, 18, 23, 21]),
        "contextual_bandit": _rows([21, 20, 20, 21, 19, 20, 22, 20]),
    }, episodes=25)
    a2 = result["verdicts"]["a2_anchor"]
    assert a2["seeds_within_5"] == 8
    assert a2["declared_match"] is True
    assert a2["verdict"] == "MATCHES B"
    assert "CASE A" in result["conditional_experiment"]


def test_an_arm_that_trails_badly_is_inconclusive_and_licenses_case_b():
    """Within-5 on too few seeds and a median below half of B's."""
    result = apply_decision_rule({
        "a2_anchor": _rows([0, 1, 0, 2, 0, 0, 1, 0]),
        "contextual_bandit": _rows([20, 22, 18, 21, 19, 23, 20, 21]),
    }, episodes=25)
    a2 = result["verdicts"]["a2_anchor"]
    assert a2["seeds_within_5"] == 0
    assert a2["declared_match"] is False
    assert a2["declared_closes_most_of_the_gap"] is False
    assert a2["verdict"] == "INCONCLUSIVE at n=8"
    assert "CASE B" in result["conditional_experiment"]


def test_the_within_5_count_and_the_median_gate_are_both_required():
    """Six seeds within 5 but a median under 60% of B's must NOT be declared a match.

    This is the case §5's median clause exists for: a one-sided difference that the
    within-5 count alone would read as a tie.
    """
    result = apply_decision_rule({
        # seeds 0-5 within 5 of B; seeds 6-7 far below. Median 7 vs B's 20 -> 35%.
        "a2_anchor": _rows([7, 7, 7, 6, 6, 7, 0, 0]),
        "contextual_bandit": _rows([11, 11, 11, 11, 11, 11, 20, 20]),
    }, episodes=25)
    a2 = result["verdicts"]["a2_anchor"]
    assert a2["seeds_within_5"] >= DECISION_RULE["match_min_seeds"]
    assert a2["median_fraction_of_reference"] < DECISION_RULE["match_median_fraction"]
    assert a2["declared_match"] is False


def test_a1_is_scored_by_the_same_rule_as_a2():
    result = apply_decision_rule({
        "a1_lexical": _rows([20, 21, 19, 22, 20, 18, 23, 21]),
        "a2_anchor": _rows([0, 0, 0, 0, 0, 0, 0, 0]),
        "contextual_bandit": _rows([21, 20, 20, 21, 19, 20, 22, 20]),
    }, episodes=25)
    assert result["verdicts"]["a1_lexical"]["verdict"] == "MATCHES B"
    assert result["verdicts"]["a2_anchor"]["verdict"] == "INCONCLUSIVE at n=8"
    # The conditional experiment is decided on A2 only, per the frozen document.
    assert "CASE B" in result["conditional_experiment"]


def test_pairing_is_by_seed_not_by_position():
    """Rows arriving in a different order must still pair on the seed they carry."""
    bandit = _rows([0, 25, 0, 25])
    a2 = list(reversed(_rows([0, 25, 0, 25])))
    result = apply_decision_rule({"a2_anchor": a2, "contextual_bandit": bandit}, episodes=25)
    pairs = {p["seed"]: (p["a2_anchor"], p["contextual_bandit"])
             for p in result["verdicts"]["a2_anchor"]["paired_seeds"]}
    assert pairs == {0: (0, 0), 1: (25, 25), 2: (0, 0), 3: (25, 25)}
    assert result["verdicts"]["a2_anchor"]["seeds_within_5"] == 4


def test_a_partial_run_cannot_declare_a_verdict():
    """The report is persisted after every seed, so partial reports exist and get read.

    With fewer than 6 paired seeds the frozen match criterion is arithmetically
    unreachable. An arm that looks like a match must be labelled provisional and neither
    CASE A nor CASE B may be declared, or an interrupted overnight run will be read as a
    finished experiment.
    """
    result = apply_decision_rule({
        "a2_anchor": _rows([20, 21, 19]),
        "contextual_bandit": _rows([21, 20, 20]),
    }, episodes=25)
    a2 = result["verdicts"]["a2_anchor"]
    assert a2["match_criterion_reachable_at_this_n"] is False
    assert a2["declared_match"] is False
    assert "PROVISIONAL" in a2["verdict"]
    assert a2["_partial_run_warning"]
    # The message names both cases in order to refuse them, so the assertion is on what it
    # *declares* -- the leading token -- not on whether the words appear.
    assert result["conditional_experiment"].startswith("UNDECIDED")


def test_a_full_run_can_declare_a_verdict():
    """The complement: at 8 paired seeds the criterion is reachable and a case is declared."""
    result = apply_decision_rule({
        "a2_anchor": _rows([20, 21, 19, 22, 20, 18, 23, 21]),
        "contextual_bandit": _rows([21, 20, 20, 21, 19, 20, 22, 20]),
    }, episodes=25)
    a2 = result["verdicts"]["a2_anchor"]
    assert a2["match_criterion_reachable_at_this_n"] is True
    assert a2["_partial_run_warning"] is None
    assert "PROVISIONAL" not in a2["verdict"]


def test_a_missing_reference_arm_is_reported_not_guessed():
    assert "_error" in apply_decision_rule({"a2_anchor": _rows([1, 2])}, episodes=25)
