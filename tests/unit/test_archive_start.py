"""The archive as a start-state curriculum for a learner.

The failure it addresses is structural rather than about learning: passing stage 1 of the
deep flow is roughly a 1% event per arrival and four gates compound to about 1e-6, so the
replay buffer contained **zero** successful trajectories and TD error had nothing to
propagate. Starting some episodes at an archived state puts deep transitions in the
buffer.

The dangerous failure mode here is not a crash. It is an archive full of cells whose
stored route does not actually reach them: those look reachable, get selected as
springboards, and silently land the agent somewhere else — and every cell discovered
"from" them inherits the false claim. Most of these tests are about that.
"""

from __future__ import annotations

import gymnasium as gym
import pytest

from web_testing_agent.agents.archive_start import ArchiveStartWrapper
from web_testing_agent.agents.go_explore import Archive
from web_testing_agent.envs.types import ActionSpec, ActionType


class FakeEnv(gym.Env):
    """A scripted stand-in with the surface `ArchiveStartWrapper` actually touches.

    Not a mock of the browser: it reproduces the contract (`setup_actions` replayed at
    reset, `state_key`/`exec_info`/`page` in info, an action list rebuilt per step) so the
    wrapper is tested against the shape of the real env rather than against its own
    assumptions.
    """

    observation_space = gym.spaces.Discrete(1)
    action_space = gym.spaces.Discrete(4)

    def __init__(self, transitions: dict[tuple[str, str], str] | None = None) -> None:
        self.setup_actions: list[dict] = []
        # (state, label) -> next state. Missing pairs leave the state unchanged.
        self.transitions = transitions or {}
        self.state = "home"
        self.exec_success = True
        self.replay_lands: str | None = None
        self.back_lands: str | None = None
        self._action_specs: list[ActionSpec] = []
        self.reset_count = 0

    def _specs(self) -> list[ActionSpec]:
        return [
            ActionSpec(index=0, action_type=ActionType.NO_OP),
            ActionSpec(index=1, action_type=ActionType.CLICK, selector='[id="a"]',
                       element_id="a", params={"navigational": True}),
            ActionSpec(index=2, action_type=ActionType.CLICK, selector='[id="b"]',
                       element_id="b", params={"navigational": True}),
            ActionSpec(index=3, action_type=ActionType.BROWSER_BACK),
        ]

    def _info(self) -> dict:
        return {
            "state_key": self.state,
            "action_specs": self._action_specs,
            "exec_info": {"success": self.exec_success},
            "page": {"url": f"http://app/{self.state}.html"},
            "bootstrap_steps": len(self.setup_actions),
        }

    def reset(self, *, seed=None, options=None):  # noqa: ANN201
        self.reset_count += 1
        self.state = "home"
        if self.setup_actions:
            if self.replay_lands:
                self.state = self.replay_lands
            else:
                # Walk the route through the transition table, the way
                # `_run_setup_actions` replays it against the live action space —
                # rather than teleporting to the last step's target, which would make a
                # replay test pass without the replay being correct.
                for step in self.setup_actions:
                    target = step.get("id") or step.get("text", "")
                    self.state = self.transitions.get((self.state, target), self.state)
        self._action_specs = self._specs()
        return 0, self._info()

    def step(self, action):  # noqa: ANN201
        spec = self._action_specs[action]
        if self.exec_success and spec.action_type is ActionType.BROWSER_BACK:
            # A history move genuinely relocates the browser and cannot be written into
            # a route, which is the case the wrapper has to notice.
            self.state = self.back_lands or self.state
        elif self.exec_success and spec.element_id:
            self.state = self.transitions.get((self.state, spec.element_id), self.state)
        self._action_specs = self._specs()
        return 0, 0.0, False, False, self._info()


def _wrapper(env, **kwargs) -> ArchiveStartWrapper:
    return ArchiveStartWrapper(env, Archive(), seed=0, **kwargs)


# -- archiving ----------------------------------------------------------------------

def test_states_reached_are_archived_with_the_route_that_reached_them():
    env = FakeEnv({("home", "a"): "one", ("one", "b"): "two"})
    wrapper = _wrapper(env, p_return=0.0)
    wrapper.reset()
    wrapper.step(1)
    wrapper.step(2)
    assert set(wrapper.archive.cells) == {"home", "one", "two"}
    assert [s["id"] for s in wrapper.archive.cells["two"].path] == ["a", "b"]


def test_an_action_that_changes_nothing_leaves_the_route_usable():
    """A dead control is not a broken route."""
    env = FakeEnv({("home", "a"): "one"})
    wrapper = _wrapper(env, p_return=0.0)
    wrapper.reset()
    wrapper.step(1)
    wrapper.step(2)  # 'b' has no transition from 'one'
    assert wrapper.archive.cells["one"].path == [{"type": "CLICK", "id": "a"}]


def test_an_unreplayable_action_that_moves_the_state_stops_archiving():
    """BROWSER_BACK cannot be written into a route, so anything reached after it would
    be recorded with a path that does not reach it."""
    env = FakeEnv({("home", "a"): "one", ("back-page", "b"): "two"})
    env.back_lands = "back-page"
    wrapper = _wrapper(env, p_return=0.0)
    wrapper.reset()
    wrapper.step(1)
    before = set(wrapper.archive.cells)

    wrapper.step(3)   # BROWSER_BACK: moves, and is unreplayable
    wrapper.step(2)   # reaches "two", but by a path no route describes

    assert wrapper._route_valid is False
    assert "back-page" not in wrapper.archive.cells
    assert "two" not in wrapper.archive.cells
    assert set(wrapper.archive.cells) == before


def test_a_failed_action_does_not_extend_the_route():
    env = FakeEnv({("home", "a"): "one"})
    wrapper = _wrapper(env, p_return=0.0)
    wrapper.reset()
    env.exec_success = False
    wrapper.step(1)
    assert wrapper.archive.cells["home"].path == []


def test_routes_are_capped_in_length():
    env = FakeEnv()
    wrapper = _wrapper(env, p_return=0.0, max_route_length=2)
    wrapper.reset()
    for _ in range(6):
        wrapper.step(1)
    assert all(len(cell.path) <= 2 for cell in wrapper.archive.cells.values())


# -- returning ----------------------------------------------------------------------

def test_a_return_replays_the_stored_route_as_setup_actions():
    env = FakeEnv({("home", "a"): "one"})
    wrapper = _wrapper(env, p_return=1.0)
    wrapper.reset()
    wrapper.step(1)
    wrapper.reset()
    assert wrapper.stats.returns_attempted >= 1 or not wrapper.archive.cells["home"].path


def test_replay_steps_are_counted_as_cost_even_though_they_are_not_env_steps():
    """Route replay is outside the step budget and emphatically not outside the cost.
    A comparison matched on env steps alone hands this agent free browser actions."""
    env = FakeEnv({("home", "a"): "one", ("one", "b"): "two"})
    wrapper = _wrapper(env, p_return=1.0)
    wrapper.reset()
    wrapper.step(1)
    wrapper.step(2)
    for _ in range(5):
        wrapper.reset()
    assert wrapper.stats.replayed_actions > 0


def test_a_return_that_lands_somewhere_else_is_counted_and_archives_nothing():
    """The failure that poisons an archive: a route that silently stops working, with
    every cell found afterwards inheriting it as a prefix."""
    env = FakeEnv({("home", "a"): "one"})
    wrapper = _wrapper(env, p_return=1.0)
    wrapper.reset()
    wrapper.step(1)
    known = set(wrapper.archive.cells)

    env.replay_lands = "somewhere-else"
    env.transitions[("somewhere-else", "b")] = "new-state"
    wrapper.reset()
    wrapper.step(2)

    assert wrapper.stats.returns_failed >= 1
    assert "new-state" not in wrapper.archive.cells
    assert set(wrapper.archive.cells) == known


def test_p_return_zero_always_starts_at_the_landing_page():
    env = FakeEnv({("home", "a"): "one"})
    wrapper = _wrapper(env, p_return=0.0)
    for _ in range(5):
        wrapper.reset()
        wrapper.step(1)
    assert wrapper.stats.returns_attempted == 0
    assert env.setup_actions == []


def test_some_episodes_still_start_at_the_landing_page():
    """A policy trained only from archived states forgets the entry point, which is
    where evaluation begins and where a real user starts."""
    env = FakeEnv({("home", "a"): "one", ("one", "b"): "two"})
    wrapper = _wrapper(env, p_return=0.5)
    wrapper.reset()
    wrapper.step(1)
    wrapper.step(2)
    starts = []
    for _ in range(40):
        wrapper.reset()
        starts.append(bool(env.setup_actions))
    assert any(starts) and not all(starts)


def test_the_archive_is_shared_so_two_episodes_accumulate_one_map():
    shared = Archive()
    env = FakeEnv({("home", "a"): "one"})
    wrapper = ArchiveStartWrapper(env, shared, p_return=0.0, seed=0)
    wrapper.reset()
    wrapper.step(1)
    assert shared.cells is wrapper.archive.cells
    assert "one" in shared.cells


# -- re-anchoring after a lost route -------------------------------------------------
#
# Measured on the seed-0 diagnostic: BROWSER_BACK (152), NO_OP (130) and BROWSER_FORWARD
# (111) are ~4 actions per 40-step episode, so `_route_valid` went False early in most
# episodes and nothing was archived afterwards. 8 cells were found in the first 100 steps
# and 1 in the remaining 3,900. Training reached order-2.html on seven steps and archived
# zero depth-2 cells. Arriving at a state the archive already holds restores the only
# thing that was lost — knowing a route to where we are.

def test_a_deeper_state_after_a_history_action_can_still_be_archived():
    """The defect this fixes. Previously everything after the BACK was lost."""
    env = FakeEnv({("home", "a"): "one", ("one", "b"): "deep"})
    env.back_lands = "home"          # BACK returns to a state the archive already holds
    wrapper = _wrapper(env, p_return=0.0)
    wrapper.reset()
    wrapper.step(1)                  # home -> one, archived
    wrapper.step(3)                  # BROWSER_BACK -> home: route lost, then re-anchored
    assert wrapper._route_valid is True
    assert wrapper.stats.reanchored == 1

    env.transitions[("home", "a")] = "one"
    wrapper.step(1)                  # home -> one again
    env.transitions[("one", "b")] = "deep"
    wrapper.step(2)                  # one -> deep, and this must now be archived
    assert "deep" in wrapper.archive.cells
    assert [s["id"] for s in wrapper.archive.cells["deep"].path] == ["a", "b"]


def test_re_anchoring_only_claims_a_route_the_archive_already_verified():
    """It adopts the stored path of the cell it landed on — nothing new is asserted."""
    env = FakeEnv({("home", "a"): "one", ("one", "b"): "two"})
    env.back_lands = "one"
    wrapper = _wrapper(env, p_return=0.0)
    wrapper.reset()
    wrapper.step(1)                  # home -> one   (route [a])
    wrapper.step(2)                  # one  -> two   (route [a, b])
    wrapper.step(3)                  # BACK -> one   : re-anchor to one's stored route
    assert wrapper._route_valid is True
    assert wrapper._route == wrapper.archive.cells["one"].path == [{"type": "CLICK", "id": "a"}]


def test_landing_somewhere_unknown_after_a_history_action_still_archives_nothing():
    """Re-anchoring must not become a licence to invent routes. An unknown state reached
    by an unreplayable action has no describable path and must stay unarchived."""
    env = FakeEnv({("home", "a"): "one"})
    env.back_lands = "never-seen"
    wrapper = _wrapper(env, p_return=0.0)
    wrapper.reset()
    wrapper.step(1)
    known = set(wrapper.archive.cells)

    wrapper.step(3)                  # BACK to a state the archive does not hold
    assert wrapper._route_valid is False
    assert wrapper.stats.reanchored == 0
    assert "never-seen" not in wrapper.archive.cells

    env.transitions[("never-seen", "b")] = "downstream"
    wrapper.step(2)
    assert "downstream" not in wrapper.archive.cells
    assert set(wrapper.archive.cells) == known


def test_a_lost_route_recovers_later_by_reaching_a_known_state():
    """Recovery is not limited to the step that lost the route."""
    env = FakeEnv({("home", "a"): "one", ("unknown", "a"): "home"})
    env.back_lands = "unknown"
    wrapper = _wrapper(env, p_return=0.0)
    wrapper.reset()
    wrapper.step(1)
    wrapper.step(3)                  # BACK to an unknown state: route lost
    assert wrapper._route_valid is False
    wrapper.step(1)                  # wanders back to 'home', which IS archived
    assert wrapper._route_valid is True
    assert wrapper.stats.reanchored == 1


def test_re_anchoring_does_not_disturb_ordinary_archiving():
    """The invariant: with no unreplayable actions, behaviour is exactly as before."""
    env = FakeEnv({("home", "a"): "one", ("one", "b"): "two"})
    wrapper = _wrapper(env, p_return=0.0)
    wrapper.reset()
    wrapper.step(1)
    wrapper.step(2)
    assert set(wrapper.archive.cells) == {"home", "one", "two"}
    assert wrapper.stats.reanchored == 0
    assert [s["id"] for s in wrapper.archive.cells["two"].path] == ["a", "b"]


def test_replay_of_a_re_anchored_route_still_lands_where_it_claims():
    """End to end: a route recorded after a re-anchor must replay correctly."""
    env = FakeEnv({("home", "a"): "one", ("one", "b"): "deep"})
    env.back_lands = "home"
    wrapper = _wrapper(env, p_return=0.0)
    wrapper.reset()
    wrapper.step(1)
    wrapper.step(3)                  # BACK -> home, re-anchored to []
    wrapper.step(1)                  # home -> one
    wrapper.step(2)                  # one  -> deep
    route = wrapper.archive.cells["deep"].path

    replay = FakeEnv({("home", "a"): "one", ("one", "b"): "deep"})
    replay.setup_actions = route
    _obs, info = replay.reset()
    assert info["state_key"] == "deep"
