"""Archive bookkeeping for the return-then-explore explorer.

The load-bearing property is that **a stored route really reaches its cell**. A cell
whose route does not reproduce is worse than a missing cell: it gets selected as a
springboard, silently returns somewhere else, and every state found from there is
recorded under a prefix that never led to it. These tests pin the cases where the route
must be refused rather than guessed.
"""

from __future__ import annotations

import random

from web_testing_agent.agents.go_explore import Archive, Cell, to_replay_step
from web_testing_agent.envs.types import ActionSpec, ActionType


def _spec(action_type: ActionType, **kwargs) -> ActionSpec:
    return ActionSpec(index=kwargs.pop("index", 1), action_type=action_type, **kwargs)


# --- serializing an action into a replayable step ---------------------------------


def test_a_click_with_an_id_serializes_to_the_exact_selector_form():
    step = to_replay_step(_spec(ActionType.CLICK, selector='[id="to-step-2"]', element_id="Continue"))
    assert step == {"type": "CLICK", "id": "to-step-2"}


def test_a_click_without_an_id_falls_back_to_its_visible_label():
    """Real applications mostly have no ids; Gitea's footer and tab bar carry none."""
    step = to_replay_step(_spec(ActionType.CLICK, selector="nav >> nth=2", element_id="Explore"))
    assert step == {"type": "CLICK", "text": "Explore"}


def test_type_is_replayed_by_category_not_by_generated_value():
    """The registry regenerates values per page, so the category is the stable handle."""
    step = to_replay_step(
        _spec(ActionType.TYPE, selector='[id="qty"]', element_id="qty",
              params={"value": "42", "category": "valid_typical"})
    )
    assert step == {"type": "TYPE", "id": "qty", "params": {"category": "valid_typical"}}


def test_select_carries_the_option_that_opens_the_gate():
    step = to_replay_step(
        _spec(ActionType.SELECT, selector='[id="product"]', element_id="product",
              params={"option": "a4-paper"})
    )
    assert step["params"] == {"option": "a4-paper"}


def test_unreplayable_actions_are_refused():
    """A route is a sequence to re-execute; these do not survive being re-executed.

    NO_OP has no effect to reproduce, and the history moves depend on a browser history
    the replay never builds — a stored BROWSER_BACK would go somewhere else on the way
    back, which is the one failure the archive must never introduce silently.
    """
    for action_type in (ActionType.NO_OP, ActionType.BROWSER_BACK, ActionType.BROWSER_FORWARD):
        assert to_replay_step(_spec(action_type)) is None


# --- the archive ------------------------------------------------------------------


def test_a_new_state_is_reported_as_a_discovery_and_a_repeat_is_not():
    archive = Archive()
    assert archive.observe("aaa", [{"type": "CLICK", "id": "x"}]) is True
    assert archive.observe("aaa", [{"type": "CLICK", "id": "x"}]) is False
    assert len(archive.cells) == 1


def test_a_shorter_route_to_a_known_cell_replaces_the_stored_one():
    """Returning is paid on every selection, so route length is a running cost."""
    archive = Archive()
    long_route = [{"type": "CLICK", "id": "a"}, {"type": "CLICK", "id": "b"}, {"type": "CLICK", "id": "c"}]
    archive.observe("target", long_route)
    archive.observe("target", [{"type": "CLICK", "id": "direct"}])
    assert archive.cells["target"].path == [{"type": "CLICK", "id": "direct"}]
    assert archive.to_dict()["shorter_routes_found"] == 1


def test_a_longer_route_never_replaces_a_shorter_one():
    archive = Archive()
    archive.observe("target", [{"type": "CLICK", "id": "direct"}])
    archive.observe("target", [{"type": "CLICK", "id": "a"}, {"type": "CLICK", "id": "b"}])
    assert archive.cells["target"].path == [{"type": "CLICK", "id": "direct"}]


# --- selection: the part that decides whether the archive climbs ------------------


def test_selection_decays_with_how_often_a_cell_was_chosen():
    """Count-based decay is what stops one cell absorbing the whole budget."""
    fresh, used = Cell(key="fresh", path=[]), Cell(key="used", path=[], chosen=100)
    assert fresh.weight() > used.weight()


def test_deeper_cells_are_preferred_over_equally_explored_shallow_ones():
    """The term that makes the archive climb rather than mill around the landing page.

    Measured: without it every weight converges to ~1/sqrt(chosen) once the easy pages
    are exhausted, and a 600-step run spent its budget re-exploring `terms.html` and
    never accumulated a gate-open state at all.
    """
    shallow = Cell(key="terms", path=[{"type": "CLICK", "id": "f-terms"}])
    deep = Cell(key="order-2", path=[{"type": "CLICK", "id": "start-order"},
                                     {"type": "SELECT", "id": "product"},
                                     {"type": "CLICK", "id": "to-step-2"}])
    assert deep.weight() > shallow.weight()

    # And the preference is tunable off, for targets where route length says nothing
    # about progress.
    assert deep.weight(depth_bias=0.0) == shallow.weight(depth_bias=0.0)


def test_selection_returns_none_on_an_empty_archive():
    """The first iteration has nowhere to return to and must start from the landing page."""
    assert Archive().select(random.Random(0)) is None


def test_every_cell_stays_reachable_by_selection():
    """Decay must not drive a weight to zero: an exhausted cell is still a valid route."""
    archive = Archive()
    for i in range(5):
        archive.observe(f"cell-{i}", [{"type": "CLICK", "id": str(i)}])
    archive.cells["cell-0"].chosen = 10_000
    assert archive.cells["cell-0"].weight() > 0

    rng = random.Random(0)
    seen = {archive.select(rng).key for _ in range(400)}
    assert seen == {f"cell-{i}" for i in range(5)}
