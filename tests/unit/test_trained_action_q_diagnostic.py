"""Tests for the trained-Q diagnostic's arithmetic and for the capture's integrity.

What must be trustworthy here is the measurement, not the model. These pin the pieces that
could silently produce a wrong number — the cosine, the label the encoder is actually fed,
the pair enumeration, the rank/argmax bookkeeping — plus the structural claims the capture
file makes about itself, since the whole diagnostic rests on those observations being real
and complete.

The captured states are treated as a fixture: if `reports/trained_action_q_states.json` is
absent the capture tests skip rather than fail, because regenerating it needs the fixture
server and these tests must stay runnable in the portable subset.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


diag = _load("diagnose_trained_action_q")
capture_mod = _load("capture_action_q_states")

STATES_PATH = REPO_ROOT / "reports" / "trained_action_q_states.json"
_HAS_CAPTURE = STATES_PATH.exists()
needs_capture = pytest.mark.skipif(
    not _HAS_CAPTURE, reason="run scripts/capture_action_q_states.py first")


@pytest.fixture(scope="module")
def capture() -> dict:
    return json.loads(STATES_PATH.read_text(encoding="utf-8"))


# -- cosine and helpers ---------------------------------------------------------------

def test_cosine_basic_cases():
    v = np.array([0.3, -0.7, 0.2], dtype=np.float32)
    assert diag.cosine(v, v) == pytest.approx(1.0)
    assert diag.cosine(v, -v) == pytest.approx(-1.0)
    assert diag.cosine(np.array([1.0, 0.0]), np.array([0.0, 1.0])) == pytest.approx(0.0)
    assert diag.cosine(v, 4.0 * v) == pytest.approx(1.0), "must be scale invariant"


def test_cosine_zero_vector_is_zero_not_nan():
    zero = np.zeros(3, dtype=np.float32)
    other = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    for a, b in ((zero, other), (other, zero), (zero, zero)):
        assert diag.cosine(a, b) == 0.0
        assert not np.isnan(diag.cosine(a, b))


def test_label_of_matches_what_the_production_extractor_hashes():
    """`_encode_one` uses `element_id or description`; a mismatch would mis-key every pair."""
    assert diag.label_of({"element_id": "Place order", "description": "click x"}) == "Place order"
    assert diag.label_of({"element_id": None, "description": "no-op"}) == "no-op"
    assert diag.label_of({"element_id": "", "description": "scroll up"}) == "scroll up"
    assert diag.label_of({}) == ""


def test_describe_and_correlate_handle_degenerate_input():
    assert diag.describe([]) == {"n": 0}
    assert diag.describe([1.0, 3.0])["mean"] == pytest.approx(2.0)
    constant = diag.correlate([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])
    assert constant["pearson_r"] is None and "note" in constant
    assert diag.correlate([1.0], [2.0])["pearson_r"] is None


def test_correlate_recovers_a_known_relationship():
    x = list(range(20))
    y = [3.0 * v + 1.0 for v in x]
    result = diag.correlate(x, y)
    assert result["pearson_r"] == pytest.approx(1.0)
    assert result["spearman_rho"] == pytest.approx(1.0)
    assert diag.correlate(x, [-v for v in y])["pearson_r"] == pytest.approx(-1.0)


def test_binned_partitions_every_row_exactly_once():
    rows = [{"cosine": c, "abs_q_gap": 1.0}
            for c in (-0.9, -0.3, -0.1, 0.0, 0.1, 0.3, 0.6, 1.0)]
    bins = diag.binned(rows)
    assert sum(b["n"] for b in bins) == len(rows), "bins must tile the cosine range"


# -- the capture file's own integrity -------------------------------------------------

@needs_capture
def test_every_captured_observation_has_the_current_dimension(capture):
    from web_testing_agent.agents.ac_dqn import AC_OBS_DIM

    assert capture["observation_dim"] == AC_OBS_DIM == 6973
    assert capture["states"], "capture is empty"
    for state in capture["states"]:
        assert len(state["observation"]) == AC_OBS_DIM, state["state_id"]


@needs_capture
def test_mask_agrees_with_the_valid_action_count_and_the_specs(capture):
    """A mask that disagrees with the spec list would silently score padding rows."""
    from web_testing_agent.envs.types import MAX_ACTIONS

    for state in capture["states"]:
        mask = np.asarray(state["observation"][-MAX_ACTIONS:], dtype=np.float32)
        n = state["num_valid_actions"]
        assert int((mask > 0.5).sum()) == n, state["state_id"]
        assert mask[:n].min() == 1.0 and (mask[n:] == 0.0).all()
        assert len(state["action_specs"]) == n
        assert len(state["labels"]) == n


@needs_capture
def test_action_specs_carry_the_fields_the_diagnostic_reads(capture):
    for state in capture["states"]:
        for spec in state["action_specs"]:
            assert spec["action_type"], state["state_id"]
            assert "in_viewport" in spec and "navigational" in spec and "href" in spec
            assert diag.label_of(spec) != "" or spec["action_type"] == "NO_OP"


@needs_capture
def test_observation_hash_matches_the_stored_vector(capture):
    """Guards against a JSON round-trip silently losing float precision."""
    import hashlib

    for state in capture["states"]:
        vector = np.asarray(state["observation"], dtype=np.float32)
        assert hashlib.sha256(vector.tobytes()).hexdigest() == state["observation_sha256"]


@needs_capture
def test_the_targeted_pairs_are_genuinely_co_valid_where_claimed(capture):
    """The coverage table must be derivable from the states, not asserted beside them."""
    by_id = {s["state_id"]: set(s["labels"]) for s in capture["states"]}
    for row in capture["coverage"]:
        a, b = row["pair"]
        for state_id in row["co_valid_states"]:
            assert a in by_id[state_id] and b in by_id[state_id], (a, b, state_id)
        assert row["n_states"] == len(row["co_valid_states"])
        if not row["co_valid_states"]:
            assert not any(a in labels and b in labels for labels in by_id.values())


@needs_capture
def test_the_deadlock_state_contains_the_three_actions_the_case_study_needs(capture):
    state = next(s for s in capture["states"] if s["state_id"] == diag.DEADLOCK)
    assert {"Place order", "Back", "Cancel order"} <= set(state["labels"])
    assert "order-4.html" in state["url"]
    assert "product=" in state["url"], "must be the walked state, not a direct page load"


@needs_capture
def test_gated_states_were_reached_by_walking_rather_than_loading(capture):
    """`Continue` is `class=hidden`; its presence is the proof the walk really happened."""
    by_id = {s["state_id"]: s for s in capture["states"]}
    assert "Continue" in by_id["flow:order-1-gated"]["labels"]
    assert "Continue" not in by_id["page:order-1.html"]["labels"]
    assert by_id["flow:order-1-gated"]["route_length"] > 0
    assert by_id["page:order-1.html"]["route_length"] == 0


# -- pair enumeration and ranking ------------------------------------------------------

class _StubModel:
    """Returns a fixed Q row, so rank/argmax bookkeeping can be checked exactly."""

    def __init__(self, values):
        self.values = values

    class _Net:
        def __init__(self, values):
            self.values = values

        def __call__(self, tensor):
            import torch as th

            row = list(self.values) + [0.0] * (100 - len(self.values))
            return th.tensor([row], dtype=th.float32)

    @property
    def q_net(self):
        return self._Net(self.values)


def test_build_rows_enumerates_every_pair_and_ranks_correctly():
    from web_testing_agent.agents.action_features import LabelEncoder

    state = {
        "state_id": "s", "page_file": "p.html", "num_valid_actions": 3,
        "observation": [0.0] * 6973,
        "action_specs": [
            {"element_id": "Place order", "description": "", "action_type": "CLICK"},
            {"element_id": "Cancel order", "description": "", "action_type": "CLICK"},
            {"element_id": "Back", "description": "", "action_type": "CLICK"},
        ],
    }
    ckpt = {"seed": 0, "phase": "final", "model": _StubModel([0.5, 2.0, -1.0])}
    actions, pairs = diag.build_rows([state], [ckpt], LabelEncoder())

    assert len(actions) == 3
    assert len(pairs) == 3, "3 valid actions -> 3 unordered pairs"
    ranks = {a["label"]: a["rank"] for a in actions}
    assert ranks == {"Cancel order": 0, "Place order": 1, "Back": 2}
    assert [a["label"] for a in actions if a["is_argmax"]] == ["Cancel order"]

    pair = next(p for p in pairs if {p["a"], p["b"]} == {"Place order", "Cancel order"})
    assert pair["q_gap"] == pytest.approx(0.5 - 2.0)
    assert pair["abs_q_gap"] == pytest.approx(1.5)
    assert pair["cosine"] == pytest.approx(0.4244, abs=1e-3), "the known misleading cosine"
    assert pair["same_type"] is True


def test_build_rows_never_scores_beyond_the_valid_actions():
    """Padding rows are masked to a sentinel; including them would poison every statistic."""
    from web_testing_agent.agents.action_features import LabelEncoder

    state = {
        "state_id": "s", "page_file": "p.html", "num_valid_actions": 2,
        "observation": [0.0] * 6973,
        "action_specs": [
            {"element_id": "A", "description": "", "action_type": "CLICK"},
            {"element_id": "B", "description": "", "action_type": "CLICK"},
            {"element_id": "C", "description": "", "action_type": "CLICK"},
        ],
    }
    ckpt = {"seed": 0, "phase": "init", "model": _StubModel([1.0, 2.0, -1e8])}
    actions, pairs = diag.build_rows([state], [ckpt], LabelEncoder())
    assert {a["label"] for a in actions} == {"A", "B"}
    assert len(pairs) == 1
    assert all(abs(p["q_gap"]) < 1e6 for p in pairs), "sentinel leaked into a gap"
