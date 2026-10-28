"""Returns that survive a changed page (`HybridPolicy.robust_returns`): steps on controls
without an id carry their label (`archive.step_for`, `archive.resolve_step`), and cells named
before a URL position counted as an id are re-keyed (`abstraction.retemplate_key`)."""

from __future__ import annotations

import pytest

from web_testing_agent.envs.types import ActionSpec, ActionType
from web_testing_agent.hybrid.abstraction import (
    ID_EVIDENCE,
    forget_id_evidence,
    id_evidence_version,
    path_template,
    retemplate_key,
)
from web_testing_agent.hybrid.archive import Archive, resolve_step, step_for
from web_testing_agent.hybrid.policy import HybridPolicy
from web_testing_agent.hybrid.run import ARMS, HybridConfig, HybridJob, _policy

SCAN = "input, textarea, select, button, a[href]"


@pytest.fixture(autouse=True)
def _fresh_id_evidence():
    """The id evidence is module state; leave none behind for other tests."""
    forget_id_evidence()
    yield
    forget_id_evidence()


def _link(index: int, position: int, label: str) -> ActionSpec:
    return ActionSpec(index=index, action_type=ActionType.CLICK,
                      selector=f"{SCAN} >> nth={position}", element_id=label,
                      params={}, description=f"click {label}")


# -- steps that name their control ------------------------------------------------------------


def test_step_records_label_and_which_of_the_same_labels():
    page = [_link(1, 0, "Home"), _link(2, 1, "Edit"), _link(3, 2, "Edit")]
    step = step_for(page[2], page)
    assert step["selector"].endswith("nth=2")
    assert (step["label"], step["label_nth"]) == ("Edit", 1)
    assert "label" not in step_for(page[2])  # without the page: as before


def test_drifted_position_resolves_by_label():
    recorded = [_link(1, 4, "Find owners"), _link(2, 5, "Veterinarians")]
    step = step_for(recorded[1], recorded)
    # One more control before them: nth=5 is now "Find owners", "Veterinarians" is nth=6.
    now = [_link(1, 4, "Home"), _link(2, 5, "Find owners"), _link(3, 6, "Veterinarians")]
    assert resolve_step(step, now) == 3


def test_exact_match_with_the_same_label_wins():
    page = [_link(1, 0, "Edit"), _link(2, 1, "Edit")]
    step = step_for(page[1], page)
    assert resolve_step(step, page) == 2


def test_label_missing_from_the_page_does_not_resolve():
    recorded = [_link(1, 5, "Veterinarians")]
    step = step_for(recorded[0], recorded)
    assert resolve_step(step, [_link(1, 5, "Home")]) is None


def test_steps_without_a_label_resolve_as_before():
    spec = ActionSpec(index=7, action_type=ActionType.CLICK, selector='[id="save"]',
                      element_id="Save", params={}, description="")
    step = step_for(spec, [spec])
    assert step == {"type": "CLICK", "id": "save"}
    assert resolve_step(step, [spec]) == 7


# -- cells named before a position counted as an id -------------------------------------------


def _sessions(n: int) -> list[str]:
    return [path_template(f"http://app/session/{token}")
            for token in ("SkJCqqCnGl", "HyQvTnRaWe", "ByzZpQjTfe")[:n]]


def test_key_is_retemplated_once_the_position_counts_as_an_id():
    forget_id_evidence()
    first = _sessions(ID_EVIDENCE - 1)
    assert first[0] == "/session/SkJCqqCnGl" and id_evidence_version() == 0
    _sessions(ID_EVIDENCE)
    assert id_evidence_version() == 1
    assert retemplate_key("form|/session/SkJCqqCnGl|Board|empty||s") == \
        "form|/session/{id}|Board|empty||s"
    assert retemplate_key("list|/#!/owners/{n}|Owners|||") == "list|/#!/owners/{n}|Owners|||"
    forget_id_evidence()
    assert id_evidence_version() == 0


def test_whole_position_catches_ids_the_shape_test_misses():
    for whole, expected in ((False, "/session/BkxmocRhze"), (True, "/session/{id}")):
        forget_id_evidence(whole_position=whole)
        _sessions(ID_EVIDENCE)
        assert path_template("http://app/session/BkxmocRhze") == expected
        assert path_template("http://app/session/new") == "/session/new"
    forget_id_evidence()


def test_rekey_merges_cells_that_now_share_a_key():
    archive = Archive(0)
    archive.visit("form|/session/SkJCqqCnGl|||", [{"type": "CLICK", "id": "a"}] * 4, "form", 3)
    archive.visit("form|/session/{id}|||", [{"type": "CLICK", "id": "b"}] * 2, "form", 9)
    stale = archive.cells["form|/session/SkJCqqCnGl|||"]
    stale.failed_returns, stale.returns = 5, 6
    stale.tried.add(("complete_step", ""))
    changed = archive.rekey(lambda key: key.replace("SkJCqqCnGl", "{id}"))
    assert changed == {"form|/session/SkJCqqCnGl|||": "form|/session/{id}|||"}
    (cell,) = archive.cells.values()
    assert cell.key == "form|/session/{id}|||"
    assert len(cell.trace) == 2 and cell.first_seen_step == 3
    assert (cell.visits, cell.returns, cell.failed_returns) == (2, 6, 0)
    assert ("complete_step", "") in cell.tried


def test_policy_rekeys_its_archive_when_new_id_evidence_arrives():
    policy = HybridPolicy(robust_returns=True)
    policy.archive.visit("form|/session/SkJCqqCnGl||||", [], "form", 0)
    policy.offered["form|/session/SkJCqqCnGl||||"] = [("complete_step", "")]
    _sessions(ID_EVIDENCE)
    policy.begin_episode({})
    assert list(policy.archive.cells) == ["form|/session/{id}||||"]
    assert policy.offered == {"form|/session/{id}||||": [("complete_step", "")]}
    assert policy.stats["rekeyed_cells"] == 1


def test_policy_without_the_option_keeps_its_keys():
    policy = HybridPolicy()
    policy.archive.visit("form|/session/SkJCqqCnGl||||", [], "form", 0)
    _sessions(ID_EVIDENCE)
    policy.begin_episode({})
    assert list(policy.archive.cells) == ["form|/session/SkJCqqCnGl||||"]


# -- the v7 arms ------------------------------------------------------------------------------


def test_v7_arms_add_only_robust_returns():
    for name in ("hybrid_final", "hybrid_curiosity", "hybrid_goexplore"):
        v7 = {k: v for k, v in ARMS[f"{name}_v7"].items() if k != "robust_returns"}
        assert ARMS[f"{name}_v7"]["robust_returns"] and v7 == ARMS[f"{name}_v5"]
    combined = ARMS["hybrid_combined_v7"]
    assert combined["planner"] == "learned_cells" and combined["explore"] == "weighted"
    assert combined["browse"] and combined["failure_bonus"] == ARMS["hybrid_final_v5"]["failure_bonus"]
    policy, _ = _policy(HybridJob("hybrid_combined_v7", "app", 0), "app", HybridConfig())
    assert policy.robust_returns and policy.browse
    assert policy.summary()["cell_learner"]["decisions"] == 0
