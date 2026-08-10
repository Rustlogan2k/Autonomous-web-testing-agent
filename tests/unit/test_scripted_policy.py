from web_testing_agent.envs.types import ActionSpec, ActionType
from web_testing_agent.evaluation import ScriptedPolicy


def _specs() -> list[ActionSpec]:
    """A stand-in action space shaped like the one build_action_specs produces."""
    return [
        ActionSpec(index=0, action_type=ActionType.NO_OP, description="no-op"),
        ActionSpec(index=1, action_type=ActionType.SCROLL, params={"direction": "up", "amount": 600}),
        ActionSpec(index=2, action_type=ActionType.SCROLL, params={"direction": "down", "amount": 600}),
        ActionSpec(index=3, action_type=ActionType.RESIZE_VIEWPORT, params={"preset": "mobile"}),
        ActionSpec(index=4, action_type=ActionType.RESIZE_VIEWPORT, params={"preset": "desktop"}),
        ActionSpec(index=5, action_type=ActionType.REFRESH),
        ActionSpec(index=6, action_type=ActionType.CLICK, selector='[id="btn-export"]',
                   element_id="Export data", params={"navigational": False}),
        ActionSpec(index=7, action_type=ActionType.CLICK, selector='[id="btn-report"]',
                   element_id="Generate report", params={"navigational": False}),
        ActionSpec(index=8, action_type=ActionType.TYPE, selector='[id="age"]', element_id="age",
                   params={"value": "30", "category": "valid_typical"}),
        ActionSpec(index=9, action_type=ActionType.TYPE, selector='[id="age"]', element_id="age",
                   params={"value": "999999999", "category": "boundary_max"}),
        ActionSpec(index=10, action_type=ActionType.RAPID_CLICK, selector='[id="submit-signup"]',
                   element_id="Create account", params={"n": 5}),
    ]


def _info(specs=None) -> dict:
    return {"action_specs": _specs() if specs is None else specs}


def test_steps_execute_in_order():
    policy = ScriptedPolicy([
        {"type": "CLICK", "id": "btn-export"},
        {"type": "REFRESH"},
        {"type": "CLICK", "id": "btn-report"},
    ])
    assert [policy.act({}, _info()) for _ in range(3)] == [6, 5, 7]
    assert policy.finished


def test_matching_is_by_dom_id_not_display_label():
    """element_id is a human label ("Export data") and drifts with copy edits; ids do not."""
    policy = ScriptedPolicy([{"type": "CLICK", "id": "btn-report"}])
    assert policy.act({}, _info()) == 7


def test_param_constraints_disambiguate_same_element_actions():
    """Both TYPE actions target #age; only the category separates them (BUG-05)."""
    policy = ScriptedPolicy([{"type": "TYPE", "id": "age", "params": {"category": "boundary_max"}}])
    assert policy.act({}, _info()) == 9


def test_param_constraints_disambiguate_parameterized_fixed_actions():
    policy = ScriptedPolicy([
        {"type": "RESIZE_VIEWPORT", "params": {"preset": "mobile"}},
        {"type": "SCROLL", "params": {"direction": "down"}},
    ])
    assert [policy.act({}, _info()) for _ in range(2)] == [3, 2]


def test_an_unmatched_step_retries_then_skips():
    """A click that navigates may need a step to land, but a wrong id must not stall."""
    policy = ScriptedPolicy([{"type": "CLICK", "id": "does-not-exist"}, {"type": "REFRESH"}],
                            max_retries=2)
    assert [policy.act({}, _info()) for _ in range(3)] == [0, 0, 0]  # NO_OP while retrying
    assert policy.act({}, _info()) == 5                              # skipped, moved on
    assert policy.misses == [{"index": 0, "step": {"type": "CLICK", "id": "does-not-exist"}}]


def test_a_step_that_appears_late_is_matched_within_the_retry_budget():
    policy = ScriptedPolicy([{"type": "CLICK", "id": "btn-export"}])
    assert policy.act({}, _info(specs=[])) == 0        # page not ready yet
    assert policy.act({}, _info()) == 6                # element appeared
    assert policy.misses == []


def test_completed_steps_excludes_skips():
    policy = ScriptedPolicy([{"type": "CLICK", "id": "nope"}, {"type": "REFRESH"}], max_retries=0)
    for _ in range(3):
        policy.act({}, _info())
    assert policy.finished
    assert len(policy.misses) == 1
    assert policy.completed_steps == 1


def test_idles_on_no_op_once_the_script_is_exhausted():
    policy = ScriptedPolicy([{"type": "REFRESH"}])
    assert policy.act({}, _info()) == 5
    assert [policy.act({}, _info()) for _ in range(3)] == [0, 0, 0]


def test_reset_restarts_the_script():
    policy = ScriptedPolicy([{"type": "REFRESH"}, {"type": "CLICK", "id": "btn-export"}])
    policy.act({}, _info())
    policy.reset()
    assert policy.act({}, _info()) == 5


def test_an_empty_action_space_never_raises():
    policy = ScriptedPolicy([{"type": "CLICK", "id": "btn-export"}])
    assert policy.act({}, {}) == 0
