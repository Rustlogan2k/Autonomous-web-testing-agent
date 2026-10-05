"""The contextual diagnostic's metrics and decision rules, on synthetic probe data."""

from __future__ import annotations

import numpy as np
import pytest

from web_testing_agent.ctxbench import analyze as A  # noqa: N812
from web_testing_agent.ctxbench.runner import HELD_OUT, TRAINING

LABELS = [
    "CLICK|Save|",
    "CLICK|Add|",
    "CLICK|Next|",
    "SCROLL|down|",
    "CLICK|Help|",
    "CLICK|Proceed|",
]


def _states(variant: str, n: int = 12, decoy: bool = False, seed: int = 0) -> list[dict]:
    """States where the useful label rotates: every label is useful in some states only."""
    rng = np.random.default_rng(seed)
    out = []
    for k in range(n):
        useful = k % 3
        effects = ["progress" if r == useful else "no-op" for r in range(len(LABELS))]
        if decoy and k % 2 == 0:
            effects[5] = "decoy"
        order = rng.permutation(len(LABELS))
        out.append(
            {
                "probe_id": f"{variant}:{k:03d}",
                "labels": [LABELS[i] for i in order],
                "effects": [effects[i] for i in order],
            }
        )
    return out


def _meta() -> dict:
    return {
        v: _states(v, decoy=v in A.DECOY_VARIANTS, seed=i)
        for i, v in enumerate(TRAINING + HELD_OUT)
    }


def _scores(meta: dict, kind: str, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    label_value = {label: rng.random() for label in LABELS}
    label_value["CLICK|Proceed|"] = 5.0  # the training "advance" word, valued everywhere
    out = {}
    for v, states in meta.items():
        out[v] = {}
        for s in states:
            if kind == "contextual":
                q = [
                    1.0 + 0.01 * rng.random() if e == "progress" else -1.0 + 0.01 * rng.random()
                    for e in s["effects"]
                ]
            elif kind == "label":
                q = [label_value[label] for label in s["labels"]]
            else:
                q = rng.random(len(s["labels"])).tolist()
            out[v][s["probe_id"]] = q
    return out


def _arms(kinds: dict[str, str], resamples: int = 400) -> dict:
    meta = _meta()
    draws = A.Draws.make(3, {v: len(s) for v, s in meta.items()}, resamples)
    arms = {}
    for arm, kind in kinds.items():
        tables = {
            v: A.variant_tables(meta[v], [_scores(meta, kind, s)[v] for s in range(3)])
            for v in meta
        }
        arms[arm] = A.arm_probe_metrics(tables, draws)
    return arms


def test_auroc_and_tau_basics():
    assert A._auroc(np.array([2.0, 3.0]), np.array([1.0])) == 1.0
    assert A._auroc(np.array([1.0]), np.array([1.0])) == 0.5
    assert A._kendall_tau(np.array([1, 2, 3.0]), np.array([1, 2, 3.0])) == pytest.approx(1.0)
    assert A._kendall_tau(np.array([1, 2, 3.0]), np.array([3, 2, 1.0])) == pytest.approx(-1.0)
    assert A._kendall_tau(np.array([1, 1, 1.0]), np.array([1, 2, 3.0])) is None


def test_bootstrap_with_unit_weights_is_the_point_estimate():
    values = np.array([[1.0, 0.0, np.nan, 1.0], [0.0, 0.0, 1.0, np.nan]])
    point = A._variant_mean(values, None, None)
    sampled = A._variant_mean(values, np.ones((1, 2)), np.ones((1, 4)))
    assert sampled[0] == pytest.approx(point)


def test_contextual_scorer_is_contextual_and_label_scorer_is_not():
    arms = _arms({"ctx": "contextual", "label": "label", "rand": "random"})
    ctx, label, rand = arms["ctx"].point, arms["label"].point, arms["rand"].point
    assert ctx["within_label_auroc"] == pytest.approx(1.0)
    assert ctx["train_top1"] == pytest.approx(1.0)
    assert ctx["decoy_rate"] == pytest.approx(0.0)
    # A label-only scorer ranks a label the same in every state: within-label AUROC is 0.5
    # exactly (all ties), and it picks the decoy wherever the decoy is offered.
    assert label["within_label_auroc"] == pytest.approx(0.5)
    assert label["decoy_rate"] == pytest.approx(1.0)
    assert 0.35 < rand["within_label_auroc"] < 0.65
    assert ctx["tau_contrast"] > label["tau_contrast"]


def test_verdicts_follow_the_rules():
    arms = _arms({"ctx_full": "contextual", "lexical_only": "label", "action_only": "label"})
    out = A.hypotheses(arms)
    assert out["H-ctx"]["verdict"] == "supported"
    assert out["H-act"]["verdict"] == "not supported"
    assert out["H-app"]["verdict"] == "not supported"

    arms = _arms({"ctx_full": "label", "lexical_only": "label", "action_only": "label"})
    out = A.hypotheses(arms)
    assert out["H-act"]["verdict"] == "supported"
    assert out["H-ctx"]["verdict"] == "not supported"


def test_condition_states():
    samples = np.linspace(0.6, 0.8, 101)
    assert A.Condition("x", 0.72, samples, 0.70, ">=").holds()
    unresolved = A.Condition("x", 0.68, samples, 0.70, ">=")
    assert not unresolved.holds() and not unresolved.clearly_fails()
    assert A.Condition("x", 0.5, np.linspace(0.4, 0.6, 101), 0.70, ">=").clearly_fails()
    assert A.verdict([[unresolved]]) == "inconclusive"
