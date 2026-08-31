"""Reward invariants: completing a flow must beat farming its gates.

**The failure these pin.** All three dueling seeds learned to reach `order-4.html` on
every evaluation episode and then refuse the final click, running
`order-4 -> Back -> order-3 -> TYPE email -> Continue -> order-4` instead. The Q-values
were correct: with `n_step=3`, the Bellman target at `order-4` saw `Back` worth +4.11
against `place order`'s +2.82, so the network fitted a target that genuinely preferred
the loop.

The mechanism was the reveal ledger's key. `Back` drops the query string, so
`order-3.html` and `order-3.html?product=a&quantity=42` are different `state_key`s
despite being the same page with the same gate — and the ledger, keyed on `state_key`,
paid the reveal bonus a second time. C1 keys it on the URL *path* instead.

These tests drive the real `ExplorationTracker` and `compose_reward` over a faithful
reconstruction of the two trajectories, rather than reimplementing the reward, so they
fail if either the tracker or the composer regresses.
"""

from __future__ import annotations

import pytest

from web_testing_agent.envs.types import ActionSpec, ActionType, BugSignals
from web_testing_agent.reward.base import RewardSignal
from web_testing_agent.reward.exploration import ExplorationTracker
from web_testing_agent.reward.functional_triggers import FunctionalRewardWeights, compose_reward

EXPECTED = RewardSignal(is_expected=True, severity=0.0)
W = FunctionalRewardWeights()
GAMMA = 0.99
HOST = "http://app"

CONTINUE = ("CLICK", "Continue")


def _click(label):
    return ActionSpec(index=0, action_type=ActionType.CLICK, selector=f"[id={label}]",
                      element_id=label, params={"navigational": True})


def _type(field):
    return ActionSpec(index=1, action_type=ActionType.TYPE, selector=f"[id={field}]",
                      element_id=field, params={"category": "valid_typical"})


def _select(field):
    return ActionSpec(index=2, action_type=ActionType.SELECT, selector=f"[id={field}]",
                      element_id=field, params={"option": "a"})


class Walk:
    """Replays a scripted trajectory through the real tracker and composer."""

    def __init__(self):
        self.t = ExplorationTracker()
        self.t.start_episode()
        self.t.observe_reset("s:index")
        self.rewards: list[float] = []
        self.terms: list[dict] = []

    def step(self, spec, url, state_key, revealed=()):
        signal = self.t.observe_step(spec, state_key, frozenset(revealed), url)
        total, breakdown = compose_reward(EXPECTED, BugSignals(), W, exploration=signal)
        self.rewards.append(total)
        self.terms.append(breakdown)
        return total


# The two trajectories, reconstructed faithfully. `Back` reaches order-3 with **no**
# query string, which is exactly what produced the duplicate payment.
def _flow(w: Walk) -> None:
    w.step(_click("Start order"), f"{HOST}/order-1.html", "s:o1")
    w.step(_select("product"), f"{HOST}/order-1.html", "s:o1+sel", [CONTINUE])
    w.step(_click("Continue"), f"{HOST}/order-2.html?product=a", "s:o2")
    w.step(_type("quantity"), f"{HOST}/order-2.html?product=a", "s:o2+qty", [CONTINUE])
    w.step(_click("Continue"), f"{HOST}/order-3.html?product=a&quantity=42", "s:o3")
    w.step(_type("email"), f"{HOST}/order-3.html?product=a&quantity=42", "s:o3+em", [CONTINUE])
    w.step(_click("Continue"), f"{HOST}/order-4.html?product=a&quantity=42&email=x", "s:o4")


def _flow_finish(w: Walk) -> None:
    w.step(_click("Place order"), f"{HOST}/receipt.html?product=a&quantity=42&email=x", "s:receipt")


def _farm_lap(w: Walk, lap: int) -> None:
    """One lap of the loop the trained policy learned.

    `Back` drops the query, so it reaches `order-3.html` with a **different state_key**
    from the `order-3.html?product=a&quantity=42` visited on the way down — that is what
    let the reveal pay twice. But every lap lands on the *same* state as every other
    lap, so novelty pays on lap 1 only. Recorded on the real fixture: lap1 +4.15,
    lap2 -0.50, lap3 -0.65. Giving each lap a distinct key would model a farm that does
    not exist and would make these tests pass against the wrong thing."""
    w.step(_click("Back"), f"{HOST}/order-3.html", "s:o3-back")
    w.step(_type("email"), f"{HOST}/order-3.html", "s:o3-back+em", [CONTINUE])
    w.step(_click("Continue"), f"{HOST}/order-4.html?email=x", "s:o4-back")


# -- the duplicate payment, which is the whole point ---------------------------------

def test_going_back_and_reopening_the_same_gate_pays_no_second_reveal():
    w = Walk()
    _flow(w)
    first = w.terms[5]["reveal_bonus"]          # TYPE email on the way down
    _farm_lap(w, 1)
    second = w.terms[8]["reveal_bonus"]         # TYPE email again after Back
    assert first == pytest.approx(W.reveal_bonus)
    assert second == 0.0, "same gate, same page — the reveal must not pay twice"


def test_navigating_away_and_returning_does_not_create_a_second_payment():
    """Not just Back: any route back to the same path must not re-open the ledger."""
    w = Walk()
    _flow(w)
    w.step(_click("Home"), f"{HOST}/index.html", "s:index2")
    w.step(_click("Start order"), f"{HOST}/order-1.html", "s:o1-again")
    w.step(_select("product"), f"{HOST}/order-1.html?x=1", "s:o1-again+sel", [CONTINUE])
    assert w.terms[-1]["reveal_bonus"] == 0.0


def test_a_first_time_gate_on_a_page_still_pays():
    """The correction must not suppress legitimate gates."""
    w = Walk()
    _flow(w)
    for index, expected in ((1, "order-1"), (3, "order-2"), (5, "order-3")):
        assert w.terms[index]["reveal_bonus"] == pytest.approx(W.reveal_bonus), expected


def test_the_same_control_label_on_a_different_page_is_a_different_gate():
    """Every stage reveals a control labelled 'Continue'. Scoping by path must keep
    those distinct, or the second and third gates would be silently unrewarded."""
    w = Walk()
    _flow(w)
    paid = [w.terms[i]["reveal_bonus"] for i in (1, 3, 5)]
    assert all(p == pytest.approx(W.reveal_bonus) for p in paid), paid


# -- the ordering invariants ---------------------------------------------------------

SHALLOW = ["catalog", "support", "about", "terms", "privacy", "status"]


def _explore(w: Walk, steps: int) -> None:
    """Spend the remaining budget on ordinary pages, as the FLOW arm does once the
    order is placed. First visits pay novelty; repeats do not."""
    for i in range(steps):
        page = SHALLOW[i % len(SHALLOW)]
        w.step(_click(page.title()), f"{HOST}/{page}.html", f"s:{page}")


def test_completing_the_flow_beats_farming_its_gates_at_equal_budget():
    """I1. **Equal budget on both arms** — the invariant is about what a policy does
    with the same number of steps, so FLOW must spend its remaining steps too. Measured
    on the real fixture at 40 steps: FLOW +15.20 against FARM +7.00."""
    budget = 16
    flow = Walk()
    _flow(flow)
    _flow_finish(flow)
    _explore(flow, budget - 8)

    farm = Walk()
    _flow(farm)
    for lap in range(1, 4):
        _farm_lap(farm, lap)

    assert len(flow.rewards) == len(farm.rewards) == budget
    assert sum(flow.rewards) > sum(farm.rewards), (sum(flow.rewards), sum(farm.rewards))


def test_place_order_beats_back_under_the_three_step_target():
    """I2, and the one that actually decides the policy's choice: `n_step=3`, so this is
    what the Bellman target at order-4 contains. Before C1 this was 2.82 against 4.11
    and the network correctly preferred the loop."""
    flow = Walk()
    _flow(flow)
    _flow_finish(flow)
    flow.step(_click("Back to home"), f"{HOST}/index.html", "s:index3")
    flow.step(_click("Catalog"), f"{HOST}/catalog.html", "s:cat")
    place = sum(GAMMA ** k * flow.rewards[7 + k] for k in range(3))

    farm = Walk()
    _flow(farm)
    _farm_lap(farm, 1)
    back = sum(GAMMA ** k * farm.rewards[7 + k] for k in range(3))

    assert place > back, f"place order {place:.3f} must beat Back {back:.3f}"


def test_a_second_farm_lap_is_strictly_unprofitable():
    """A slow leak becomes a farm over 4,000 steps, so laps must not merely decay."""
    w = Walk()
    _flow(w)
    _farm_lap(w, 1)
    _farm_lap(w, 2)
    lap2 = sum(w.rewards[10:13])
    assert lap2 < 0.0, lap2


# -- nothing else moved ---------------------------------------------------------------

def test_the_flow_return_is_unchanged_by_the_ledger_scope():
    """C1 removes only the duplicate payment. Every reward the FLOW trajectory earns is
    a first-time payment, so its return must be identical with either scope."""
    scoped = Walk()
    _flow(scoped)
    _flow_finish(scoped)

    # The pre-C1 behaviour is what the tracker does when no URL is supplied.
    legacy = Walk()
    legacy.step(_click("Start order"), "", "s:o1")
    legacy.step(_select("product"), "", "s:o1+sel", [CONTINUE])
    legacy.step(_click("Continue"), "", "s:o2")
    legacy.step(_type("quantity"), "", "s:o2+qty", [CONTINUE])
    legacy.step(_click("Continue"), "", "s:o3")
    legacy.step(_type("email"), "", "s:o3+em", [CONTINUE])
    legacy.step(_click("Continue"), "", "s:o4")
    legacy.step(_click("Place order"), "", "s:receipt")

    assert sum(scoped.rewards) == pytest.approx(sum(legacy.rewards))


def test_novelty_and_repetition_terms_are_untouched_by_the_change():
    w = Walk()
    _flow(w)
    _flow_finish(w)
    novelty = [t["novelty_bonus"] for t in w.terms]
    # Every state in the FLOW trajectory is reached for the first time.
    assert all(n == pytest.approx(W.novelty_bonus) for n in novelty), novelty
    assert all(t["step_cost"] == pytest.approx(W.step_penalty) for t in w.terms)
