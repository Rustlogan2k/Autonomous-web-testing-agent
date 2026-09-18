"""The hand priority's defining property: it is generic, and it does not learn.

Arm A of the search ladder is only worth running if two claims hold, and both are cheap to
break by accident:

* **It contains no solution to the fixture it is measured on.** The heuristic must not be
  able to recognise `Place order`, `Continue`, `42`, `order-4.html` or anything else
  specific to DEEP-01. That is asserted three independent ways below — the label block is
  weighted exactly zero, the policy's choices are invariant under relabelling every
  control, and the module's own source is scanned for fixture vocabulary.
* **It does not learn.** No target, no optimiser, no weight movement. The weight vector
  after a hundred steps must be the weight vector it started with.

The rest pins the properties that make A-vs-B a controlled comparison: the same
`ActionFeatureExtractor` representation, the same valid-only action support, and the same
`Policy` interface `run_rollout` scores every other arm through.

Deliberately torch-free, so it runs in the portable CI subset alongside the fixture tests
rather than only on a machine with the deep-learning stack.
"""

from __future__ import annotations

import numpy as np
import pytest

from web_testing_agent.agents import hand_priority
from web_testing_agent.agents.action_features import (
    ACTION_FEATURE_DIM,
    LABEL_DIM,
    SCALAR_FIELDS,
    TYPE_DIM,
    ActionFeatureExtractor,
    action_identity,
)
from web_testing_agent.agents.hand_priority import (
    ALGORITHM,
    HAND_PRIORITY_TYPE_WEIGHTS,
    HAND_PRIORITY_WEIGHTS,
    HandPriorityPolicy,
    build_weight_vector,
)
from web_testing_agent.envs.types import MAX_ACTIONS, ActionSpec, ActionType


def spec(index: int, action_type: ActionType = ActionType.CLICK, label: str = "x", **params):
    return ActionSpec(
        index=index, action_type=action_type, selector=f"[id=\"e{index}\"]",
        element_id=label, params=params, description=f"{action_type.value} {label}",
    )


def info_for(specs: list[ActionSpec], revealed=(), state_key: str = "s0") -> dict:
    return {"action_specs": specs, "newly_revealed": list(revealed), "state_key": state_key}


# -- the anti-cheating guarantees -----------------------------------------------------


def test_the_hashed_label_block_is_weighted_exactly_zero():
    """The only block that can carry a control's *name* must contribute nothing."""
    weights = build_weight_vector()
    label_block = weights[TYPE_DIM : TYPE_DIM + LABEL_DIM]
    assert label_block.shape == (LABEL_DIM,)
    assert np.count_nonzero(label_block) == 0


def test_a_policy_with_a_non_zero_label_weight_is_refused():
    tampered = build_weight_vector()
    tampered[TYPE_DIM] = 1e-6
    with pytest.raises(ValueError, match="label block must be weighted zero"):
        HandPriorityPolicy(weights=tampered)


def test_choices_are_invariant_under_relabelling_every_control():
    """Rename every control on the page; the policy must choose the same slot.

    This is the strong form of "no fixture knowledge": not merely that the heuristic does
    not *mention* `Place order`, but that it cannot behave differently if that control is
    renamed to anything at all.
    """
    original = [
        spec(0, ActionType.NO_OP, "no-op"),
        spec(1, ActionType.CLICK, "Place order", navigational=True, in_viewport=True, href="r.html"),
        spec(2, ActionType.CLICK, "Cancel order", navigational=True, in_viewport=True, href="i.html"),
        spec(3, ActionType.SELECT, "product", in_viewport=True, option="folders-25"),
        spec(4, ActionType.TYPE, "quantity", in_viewport=True, category="valid_typical"),
    ]
    relabelled = [
        spec(0, ActionType.NO_OP, "zzz"),
        spec(1, ActionType.CLICK, "wibble", navigational=True, in_viewport=True, href="r.html"),
        spec(2, ActionType.CLICK, "flob", navigational=True, in_viewport=True, href="i.html"),
        spec(3, ActionType.SELECT, "qux", in_viewport=True, option="folders-25"),
        spec(4, ActionType.TYPE, "grault", in_viewport=True, category="valid_typical"),
    ]
    a = HandPriorityPolicy(epsilon=0.0, seed=0)
    b = HandPriorityPolicy(epsilon=0.0, seed=0)
    a.reset()
    b.reset()
    assert a.act({}, info_for(original)) == b.act({}, info_for(relabelled))


def test_the_executable_source_contains_no_fixture_vocabulary():
    """A last line of defence against a helpful edit that hardcodes the answer.

    Scans the module's **code**, not its prose: docstrings are stripped and comments are
    dropped by `ast.unparse`, while every remaining string literal is kept — so a smuggled
    `if spec.element_id == "Place order"` would still be caught, and the docstring
    explaining that the policy *cannot* recognise that string would not be.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(hand_priority))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            node.body.pop(0)
            if not node.body:
                node.body.append(ast.Pass())
    code = ast.unparse(tree).lower()

    for forbidden in (
        "place order", "start order", "cancel order", "continue", "quantity", "receipt",
        "order-1", "order-2", "order-3", "order-4", "deep-01", "deep_flow", "42",
        "product", "email", "href=", "url",
    ):
        assert forbidden not in code, f"fixture-specific token {forbidden!r} in the heuristic"


def test_a0_is_label_blind_and_a1_is_not():
    """`element_id` and `description` left the forbidden list above; this replaces them.

    They were in it as a proxy for "this module never reads a label", which was true while
    A0 was the only arm. A1 and A2 read the label *by definition* -- that is the single
    thing they vary -- so the proxy now forbids the mechanism under test rather than a
    smuggled answer. The guarantee it stood for is behavioural and is asserted here
    directly: relabelling every control must not move A0, and must move A1, on one page
    where the label is the only discriminator.

    `test_choices_are_invariant_under_relabelling_every_control` above is the same
    guarantee for A0 on a mixed page; this one adds the contrast that makes it meaningful.
    """
    from web_testing_agent.agents.label_priors import W_LABEL_PRIOR, LexicalCommitPrior

    original = [
        spec(0, ActionType.CLICK, "Cancel order", navigational=True, in_viewport=True, href="i.html"),
        spec(1, ActionType.CLICK, "Place order", navigational=True, in_viewport=True, href="r.html"),
    ]
    relabelled = [
        spec(0, ActionType.CLICK, "wibble", navigational=True, in_viewport=True, href="i.html"),
        spec(1, ActionType.CLICK, "flob", navigational=True, in_viewport=True, href="r.html"),
    ]

    a0 = hand_priority.HandPriorityPolicy(epsilon=0.0, seed=0)
    assert a0.act({}, info_for(original)) == hand_priority.HandPriorityPolicy(
        epsilon=0.0, seed=0).act({}, info_for(relabelled)) == 0

    def a1():
        return hand_priority.HandPriorityPolicy(
            epsilon=0.0, seed=0, label_prior=LexicalCommitPrior(),
            label_prior_weight=W_LABEL_PRIOR)

    assert a1().act({}, info_for(original)) == 1, "A1 must prefer the commit-labelled control"
    assert a1().act({}, info_for(relabelled)) == 0, "A1 must fall back to A0's order"


def test_no_weight_is_placed_on_an_unknown_feature_name():
    assert set(HAND_PRIORITY_WEIGHTS) <= set(SCALAR_FIELDS)
    with pytest.raises(ValueError, match="unknown scalar feature"):
        build_weight_vector(scalars={"not_a_feature": 1.0})


# -- the representation is shared, not parallel ---------------------------------------


def test_the_type_one_hot_order_matches_the_live_encoder():
    """`_TYPE_ORDER` is declared locally; this pins it to what the extractor actually emits.

    If `ActionFeatureExtractor` ever reorders its one-hot, the weights would silently
    attach to the wrong action types. Checked against a real encode rather than against the
    private tuple, so the test fails on a behaviour change rather than on a rename.
    """
    extractor = ActionFeatureExtractor()
    for position, action_type in enumerate(hand_priority._TYPE_ORDER):
        block = extractor.encode([spec(0, action_type)], "s", set())
        assert block[0][:TYPE_DIM].argmax() == position
        assert block[0][position] == 1.0


def test_the_weight_vector_is_one_row_of_the_action_feature_block():
    extractor = ActionFeatureExtractor()
    block = extractor.encode([spec(0)], "s", set())
    assert build_weight_vector().shape == block[0].shape == (ACTION_FEATURE_DIM,)


# -- the ordering the heuristic is supposed to express --------------------------------


def test_a_newly_revealed_control_outranks_an_ordinary_link():
    """The gate-opening signal is the strongest term, and it is what makes depth reachable."""
    specs = [
        spec(0, ActionType.CLICK, "ordinary", navigational=True, in_viewport=True, href="a.html"),
        spec(1, ActionType.CLICK, "revealed", navigational=True, in_viewport=True, href="b.html"),
    ]
    policy = HandPriorityPolicy(epsilon=0.0, seed=0)
    policy.reset()
    assert policy.act({}, info_for(specs, revealed=[action_identity(specs[1])])) == 1


def test_a_form_control_outranks_a_navigational_link():
    specs = [
        spec(0, ActionType.CLICK, "link", navigational=True, in_viewport=True, href="a.html"),
        spec(1, ActionType.SELECT, "field", in_viewport=True, option="o"),
    ]
    policy = HandPriorityPolicy(epsilon=0.0, seed=0)
    policy.reset()
    assert policy.act({}, info_for(specs)) == 1


def test_a_plausible_typed_value_outranks_a_malformed_one():
    specs = [
        spec(0, ActionType.TYPE, "f", in_viewport=True, category="type_mismatch"),
        spec(1, ActionType.TYPE, "f", in_viewport=True, category="empty_string"),
        spec(2, ActionType.TYPE, "f", in_viewport=True, category="valid_typical"),
    ]
    policy = HandPriorityPolicy(epsilon=0.0, seed=0)
    policy.reset()
    assert policy.act({}, info_for(specs)) == 2


def test_a_page_independent_control_loses_to_anything_on_the_page():
    specs = [
        spec(0, ActionType.NO_OP, "no-op"),
        spec(1, ActionType.BROWSER_BACK, "back"),
        spec(2, ActionType.SCROLL, "scroll", direction="down"),
        spec(3, ActionType.CLICK, "link", navigational=False, in_viewport=False),
    ]
    policy = HandPriorityPolicy(epsilon=0.0, seed=0)
    policy.reset()
    assert policy.act({}, info_for(specs)) == 3


def test_repetition_demotes_an_action_so_the_argmax_enumerates():
    """Without this the deterministic argmax is a loop, not a search."""
    specs = [
        spec(0, ActionType.CLICK, "a", navigational=True, in_viewport=True, href="a.html"),
        spec(1, ActionType.CLICK, "b", navigational=True, in_viewport=True, href="b.html"),
        spec(2, ActionType.CLICK, "c", navigational=True, in_viewport=True, href="c.html"),
    ]
    policy = HandPriorityPolicy(epsilon=0.0, seed=0)
    policy.reset()
    assert [policy.act({}, info_for(specs)) for _ in range(3)] == [0, 1, 2]


def test_ties_are_broken_by_the_action_registrys_own_index_order():
    specs = [spec(i, ActionType.CLICK, f"l{i}", navigational=True, in_viewport=True) for i in range(4)]
    policy = HandPriorityPolicy(epsilon=0.0, seed=0)
    policy.reset()
    assert policy.act({}, info_for(specs)) == 0
    assert policy.ties_broken == 1


# -- action support, determinism, and the absence of learning -------------------------


def test_an_invalid_slot_is_never_selected():
    """Five valid actions out of a hundred slots: every choice must be in 0..4."""
    specs = [spec(i, ActionType.CLICK, f"l{i}", in_viewport=True) for i in range(5)]
    for epsilon in (0.0, 0.5, 1.0):
        policy = HandPriorityPolicy(epsilon=epsilon, seed=3)
        policy.reset()
        chosen = [policy.act({}, info_for(specs)) for _ in range(200)]
        assert min(chosen) >= 0
        assert max(chosen) < len(specs) < MAX_ACTIONS


def test_epsilon_one_samples_across_the_valid_slots_only():
    specs = [spec(i, ActionType.CLICK, f"l{i}", in_viewport=True) for i in range(6)]
    policy = HandPriorityPolicy(epsilon=1.0, seed=7)
    policy.reset()
    chosen = {policy.act({}, info_for(specs)) for _ in range(400)}
    assert chosen == set(range(6))
    assert policy.exploratory_steps == 400


def test_epsilon_zero_is_deterministic_across_two_instances():
    specs = [
        spec(0, ActionType.CLICK, "a", navigational=True, in_viewport=True),
        spec(1, ActionType.SELECT, "b", in_viewport=True, option="o"),
        spec(2, ActionType.TYPE, "c", in_viewport=True, category="boundary_max"),
    ]
    runs = []
    for seed in (0, 1234):
        policy = HandPriorityPolicy(epsilon=0.0, seed=seed)
        policy.reset()
        runs.append([policy.act({}, info_for(specs)) for _ in range(9)])
    assert runs[0] == runs[1]


def test_the_weights_never_move():
    """The entire claim of Arm A: it is a fixed rule, not a learner."""
    specs = [spec(i, ActionType.CLICK, f"l{i}", in_viewport=True) for i in range(5)]
    policy = HandPriorityPolicy(epsilon=0.3, seed=1)
    policy.reset()
    before = policy.weights.copy()
    for _ in range(100):
        policy.act({}, info_for(specs))
    assert np.array_equal(policy.weights, before)
    assert not hasattr(policy, "optimizer")
    assert not hasattr(policy, "replay_buffer")


def test_an_empty_action_set_falls_back_to_slot_zero():
    policy = HandPriorityPolicy(epsilon=0.0, seed=0)
    policy.reset()
    assert policy.act({}, {"action_specs": [], "state_key": "s"}) == 0


# -- the two evaluation readings of this arm ------------------------------------------


def test_reset_clears_the_counts_when_they_are_not_carried():
    specs = [spec(i, ActionType.CLICK, f"l{i}", in_viewport=True) for i in range(3)]
    policy = HandPriorityPolicy(epsilon=0.0, seed=0, carry_counts_across_episodes=False)
    policy.reset()
    assert [policy.act({}, info_for(specs)) for _ in range(3)] == [0, 1, 2]
    policy.reset()
    assert [policy.act({}, info_for(specs)) for _ in range(3)] == [0, 1, 2]


def test_run_level_counts_survive_reset_when_they_are_carried():
    specs = [spec(i, ActionType.CLICK, f"l{i}", in_viewport=True) for i in range(3)]
    policy = HandPriorityPolicy(epsilon=0.0, seed=0, carry_counts_across_episodes=True)
    policy.reset()
    assert policy.act({}, info_for(specs)) == 0
    policy.reset()
    # Slot 0 keeps its run-level penalty, so the search moves on instead of repeating.
    assert policy.act({}, info_for(specs)) == 1


# -- reporting identity ----------------------------------------------------------------


def test_the_policy_reports_its_own_identity():
    payload = HandPriorityPolicy(epsilon=0.05, seed=0).to_dict()
    assert payload["algorithm"] == ALGORITHM == "hand_priority_search"
    assert payload["learns"] is False
    assert payload["label_block_weight_l1"] == 0.0
    assert payload["scalar_weights"] == HAND_PRIORITY_WEIGHTS
    assert set(payload["type_weights"]) == {t.value for t in HAND_PRIORITY_TYPE_WEIGHTS}


def test_it_satisfies_the_rollout_policy_protocol():
    """`Policy` is a plain (non-runtime-checkable) Protocol, so this checks it structurally.

    What `run_rollout` actually requires is `reset()` and `act(observation, info)` with
    those parameter names, which is what is asserted rather than a nominal isinstance.
    """
    import inspect

    from web_testing_agent.evaluation import Policy

    policy = HandPriorityPolicy()
    for method in ("reset", "act"):
        assert callable(getattr(policy, method))
        expected = list(inspect.signature(getattr(Policy, method)).parameters)
        actual = list(inspect.signature(getattr(policy, method)).parameters)
        assert actual == expected[1:] or actual == expected, (method, actual, expected)
