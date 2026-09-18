"""The five episode-context slots that close the non-Markov gap, and the reward they explain.

**The defect.** `EPISODE_CONTEXT_DIM = 6` was introduced precisely so the observation
could predict a history-dependent reward. Two terms were then added *after* it that
reintroduced the problem:

* `novelty_bonus` divides by `sqrt(1 + state_run_visits)`, a **run-level** counter.
* `reveal_bonus` pays only for identities absent from `_paid_reveals[(url_path, id)]`,
  an **episodic hidden ledger**.

Neither was in the observation, so the same observed `(s, a)` paid different rewards
depending on history the agent could not see. Slots 6-8 expose exactly those two
quantities. Nothing about the reward changed, and the last group of tests here is what
holds that line: every term is recomputed through the shipped `compose_reward` and
checked against values recorded before the observation widened.

The tests run against `ExplorationTracker` and a stub env rather than a browser, because
what is under test is bookkeeping, not rendering.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from web_testing_agent.envs.base_env import WebTestingEnv
from web_testing_agent.envs.types import (
    EPISODE_CONTEXT_DIM,
    ActionSpec,
    ActionType,
    BugSignals,
)
from web_testing_agent.reward.base import RewardSignal
from web_testing_agent.reward.exploration import ExplorationTracker
from web_testing_agent.reward.functional_triggers import (
    FindingLedger,
    FunctionalRewardWeights,
    compose_reward,
)

# Slot names, so a failure says which feature moved rather than which index.
STEP_FRACTION, EPISODE_STATES, IS_NOVEL, REPEAT_COUNT = 0, 1, 2, 3
FINDINGS_HERE, FINDINGS_TOTAL = 4, 5
VISITS_LOG, NOVELTY_DECAY, PAID_REVEALS_HERE = 6, 7, 8

_A = "http://app/order-3.html"
_A_DECORATED = "http://app/order-3.html?product=a&quantity=42"
_B = "http://app/order-4.html"


def _click(label: str) -> ActionSpec:
    return ActionSpec(index=0, action_type=ActionType.CLICK, element_id=label,
                      description=f"click {label}")


class _ContextEnv(WebTestingEnv):
    """`_episode_context` exercised without a browser.

    Subclasses the real env and stubs only the three abstract methods, so the function
    under test is the shipped one rather than a reimplementation of it.
    """

    def __init__(self, max_steps: int = 40) -> None:
        # Deliberately skips WebTestingEnv.__init__, which launches Playwright.
        self.max_steps = max_steps
        self._steps_taken = 0
        self.exploration = ExplorationTracker(repetition_window=20)
        self.finding_ledger = FindingLedger()

    def _build_action_specs(self):  # pragma: no cover - never called
        return []

    def _execute_action(self, spec):  # pragma: no cover - never called
        return {}

    def _compute_reward(self, context):  # pragma: no cover - never called
        return 0.0, {}

    def context(self, state_key: str, url: str = "", signal=None) -> np.ndarray:
        return self._episode_context(state_key, signal, url)

    def arrive(self, state_key: str, url: str, spec: ActionSpec | None = None):
        """One step into `state_key`, through the real tracker."""
        specs = [spec] if spec else []
        revealed = self.exploration.observe_action_set(specs, url) if specs else frozenset()
        signal = self.exploration.observe_step(spec or _click("noop"), state_key, revealed, url)
        self._steps_taken += 1
        return signal


# -- F. shape and space consistency --------------------------------------------------

def test_context_width_matches_the_declared_constant():
    env = _ContextEnv()
    env.exploration.observe_reset("s")
    assert env.context("s", _A).shape == (EPISODE_CONTEXT_DIM,)


def test_the_perception_wrapper_accepts_the_new_width_and_rejects_the_old_one():
    """The width is validated in exactly one place; it must agree with the constant."""
    from web_testing_agent.perception.vec_wrapper import default_encoders, encode_modalities

    encoders = default_encoders()
    page = {"html": "<html></html>", "network": "[]", "url": _A}
    good = encode_modalities(encoders, [np.zeros((8, 8, 3), np.uint8)], [page],
                             [np.zeros(EPISODE_CONTEXT_DIM, np.float32)], [5])
    assert good.shape[1] > EPISODE_CONTEXT_DIM
    with pytest.raises(ValueError, match="episode_context must have width"):
        encode_modalities(encoders, [np.zeros((8, 8, 3), np.uint8)], [page],
                          [np.zeros(6, np.float32)], [5])


def test_every_slot_stays_inside_the_normalised_range():
    """The docstring promises roughly [0,1]; a term outside it would dominate the input."""
    env = _ContextEnv(max_steps=4)
    env.exploration.observe_reset("s")
    for _ in range(12):                      # deliberately overruns the step budget
        signal = env.arrive("s", _A, _click("Continue"))
        vector = env.context("s", _A, signal)
        assert np.all(vector >= 0.0) and np.all(vector <= 1.0), vector


# -- A. the features move when the hidden reward state moves -------------------------

def test_visit_features_track_the_run_level_counter_the_novelty_bonus_divides_by():
    env = _ContextEnv()
    env.exploration.observe_reset("s")
    seen = []
    for _ in range(5):
        signal = env.arrive("s", _A)
        seen.append((signal.state_run_visits, env.context("s", _A, signal)))

    # Slot 7 is the multiplier `compose_reward` actually applies, to the last bit.
    for visits, vector in seen:
        assert vector[NOVELTY_DECAY] == pytest.approx(1.0 / math.sqrt(1.0 + visits))
        assert vector[VISITS_LOG] == pytest.approx(min(math.log1p(visits) / 4.0, 1.0))

    decay = [v[NOVELTY_DECAY] for _, v in seen]
    logs = [v[VISITS_LOG] for _, v in seen]
    assert decay == sorted(decay, reverse=True), "novelty decay must fall as visits rise"
    assert logs == sorted(logs), "the visit count must rise"
    assert len(set(decay)) == len(decay), "every additional visit must be distinguishable"


def test_paid_reveal_slot_rises_when_a_gate_is_paid_and_stays_up_on_a_revisit():
    """The gate pays once per page per episode; the observation must say which side we are on."""
    env = _ContextEnv()
    env.exploration.observe_reset("s")
    before = env.context("s", _A)
    assert before[PAID_REVEALS_HERE] == 0.0

    env.exploration.observe_action_set([_click("Back")], _A)
    revealed = env.exploration.observe_action_set([_click("Back"), _click("Continue")], _A)
    signal = env.exploration.observe_step(_click("email"), "s", revealed, _A)
    assert signal.unpaid_reveals, "precondition: the gate paid this step"
    paid = env.context("s", _A, signal)
    assert paid[PAID_REVEALS_HERE] > 0.0

    # Re-opening the same gate pays nothing, and the observation now shows why.
    env.exploration.observe_action_set([_click("Back")], _A)
    again = env.exploration.observe_action_set([_click("Back"), _click("Continue")], _A)
    signal2 = env.exploration.observe_step(_click("email"), "s", again, _A)
    assert not signal2.unpaid_reveals
    repeat = env.context("s", _A, signal2)
    assert repeat[PAID_REVEALS_HERE] > 0.0, "the ledger state must remain visible"


def test_paid_reveal_slot_is_scoped_by_path_exactly_as_the_ledger_is():
    """`?product=a` is the same page and the same gate — the reward says so, so must the feature."""
    env = _ContextEnv()
    env.exploration.observe_reset("s")
    env.exploration.observe_action_set([_click("Back")], _A)
    revealed = env.exploration.observe_action_set([_click("Back"), _click("Continue")], _A)
    env.exploration.observe_step(_click("email"), "s", revealed, _A)

    assert env.context("s", _A_DECORATED)[PAID_REVEALS_HERE] > 0.0, "query string must not hide it"
    assert env.context("s", _B)[PAID_REVEALS_HERE] == 0.0, "a different page has its own gate"


# -- C. identical pages with different hidden counters look different ----------------

def test_two_arrivals_at_one_state_differ_only_in_the_reward_relevant_slots():
    """The core of the defect: same page and action, different reward, once one observation."""
    env = _ContextEnv()
    env.exploration.observe_reset("s")
    first = env.context("s", _A, env.arrive("s", _A))
    for _ in range(7):
        env.arrive("s", _A)
    later = env.context("s", _A, env.arrive("s", _A))

    assert not np.array_equal(first, later)
    moved = {i for i in range(EPISODE_CONTEXT_DIM) if first[i] != later[i]}
    assert {VISITS_LOG, NOVELTY_DECAY} <= moved
    # Under the pre-2026-09-03 layout these two arrivals differed only in step fraction,
    # which is why the novelty decay was invisible.
    assert first[VISITS_LOG] != later[VISITS_LOG]
    assert first[NOVELTY_DECAY] != later[NOVELTY_DECAY]


# -- B. step and budget agree ---------------------------------------------------------

def test_step_fraction_tracks_the_step_count_against_the_budget():
    """A `remaining_budget` slot was audited out as exactly `1 - this`; slot 0 stands."""
    env = _ContextEnv(max_steps=10)
    env.exploration.observe_reset("s")
    for expected in range(1, 11):
        signal = env.arrive("s", _A)
        vector = env.context("s", _A, signal)
        assert vector[STEP_FRACTION] == pytest.approx(expected / 10)
    assert vector[STEP_FRACTION] == pytest.approx(1.0), "budget is exhausted at max_steps"


# -- E. what resets on a new episode, and what deliberately does not ------------------

def test_episodic_slots_reset_and_the_run_level_visit_count_deliberately_does_not():
    """`_paid_reveals` is episodic and `_state_visits` is not; the reward treats them that way.

    Resetting the visit count here would make the feature disagree with the term it
    exists to explain — the novelty bonus keeps dividing by the run-level counter across
    the episode boundary.
    """
    env = _ContextEnv()
    env.exploration.observe_reset("s")
    env.exploration.observe_action_set([_click("Back")], _A)
    revealed = env.exploration.observe_action_set([_click("Back"), _click("Continue")], _A)
    signal = env.exploration.observe_step(_click("email"), "s", revealed, _A)
    for _ in range(4):
        env.arrive("s", _A)
    end_of_episode = env.context("s", _A, signal)
    assert end_of_episode[PAID_REVEALS_HERE] > 0.0

    env.exploration.start_episode()
    env._steps_taken = 0
    env.finding_ledger.start_episode()
    env.exploration.observe_reset("s")
    fresh = env.context("s", _A)

    assert fresh[PAID_REVEALS_HERE] == 0.0, "the reveal ledger is episodic"
    assert fresh[EPISODE_STATES] <= end_of_episode[EPISODE_STATES]
    assert fresh[STEP_FRACTION] == 0.0
    # And the one that must survive, because the reward's divisor does.
    assert fresh[VISITS_LOG] > 0.0, "run-level visits must NOT reset"
    assert fresh[NOVELTY_DECAY] < 1.0


def test_the_reset_path_reports_the_same_visit_count_the_step_path_would():
    """`observe_reset` increments before the context is built; the paths must still agree."""
    env = _ContextEnv()
    env.exploration.observe_reset("s")           # first arrival of the run
    at_reset = env.context("s", _A)
    assert at_reset[NOVELTY_DECAY] == pytest.approx(1.0), "first arrival: no decay yet"
    assert at_reset[VISITS_LOG] == pytest.approx(0.0)

    env.exploration.start_episode()
    env.exploration.observe_reset("s")           # second arrival, new episode
    assert env.context("s", _A)[NOVELTY_DECAY] == pytest.approx(1.0 / math.sqrt(2.0))


# -- D. the reward itself is untouched ------------------------------------------------

@pytest.mark.parametrize(
    ("visits", "expected"),
    # Recorded from `compose_reward` before the observation widened. If widening the
    # observation moved any of these, the change was not observation-only.
    [(0, 1.0), (1, 0.7071067811865475), (2, 0.5773502691896258), (8, 0.3333333333333333)],
)
def test_novelty_magnitude_is_unchanged_at_every_visit_count(visits: int, expected: float):
    """Each arrival is in a fresh episode, because novelty only pays on an episode-novel state.

    That is the real regime the decay was built for: revisiting the same shallow page at
    the start of every episode should pay progressively less across the run.
    """
    tracker = ExplorationTracker()
    for _ in range(visits):
        tracker.start_episode()
        tracker.observe_step(_click("x"), "s", frozenset(), _A)
    tracker.start_episode()
    signal = tracker.observe_step(_click("x"), "s", frozenset(), _A)
    assert signal.state_run_visits == visits
    assert signal.is_novel_state, "precondition: novelty only pays on an episode-novel state"

    _reward, info = compose_reward(
        RewardSignal(is_expected=True, severity=0.0, explanation=""),
        bug_signals=BugSignals(), weights=FunctionalRewardWeights(), exploration=signal,
    )
    assert info["novelty_bonus"] == pytest.approx(expected)


def test_every_reward_term_is_unchanged_for_a_fixed_transition():
    """One transition, all six live terms, against values recorded before this change."""
    tracker = ExplorationTracker()
    tracker.observe_reset("origin")
    tracker.observe_action_set([_click("Back")], _A)
    revealed = tracker.observe_action_set([_click("Back"), _click("Continue")], _A)
    signal = tracker.observe_step(_click("email"), "gate", revealed, _A)

    reward, info = compose_reward(
        RewardSignal(is_expected=True, severity=0.0, explanation=""),
        bug_signals=BugSignals(), weights=FunctionalRewardWeights(), exploration=signal,
    )
    assert info["novelty_bonus"] == pytest.approx(1.0)
    assert info["reveal_bonus"] == pytest.approx(1.5)
    assert info["step_cost"] == pytest.approx(-0.05)
    assert info["deterministic_bonus"] == pytest.approx(0.0)
    # The repetition penalty has no key of its own; it is the remainder of the
    # exploration bonus once novelty and reveal are accounted for.
    repetition = info["exploration_bonus"] - info["novelty_bonus"] - info["reveal_bonus"]
    assert repetition == pytest.approx(0.0)
    assert info["exploration_bonus"] == pytest.approx(2.5)
    assert reward == pytest.approx(2.45)


def test_the_context_never_feeds_back_into_the_reward():
    """Building the observation must not mutate the counters the reward reads.

    A read-only accessor is easy to write and easy to break — `paid_reveals_at` could
    trivially have been implemented with `setdefault`. This pins that composing the
    observation twice leaves the reward for the next step identical.
    """
    def run(build_context: bool) -> float:
        env = _ContextEnv()
        env.exploration.observe_reset("origin")
        env.exploration.observe_action_set([_click("Back")], _A)
        revealed = env.exploration.observe_action_set([_click("Back"), _click("Continue")], _A)
        signal = env.exploration.observe_step(_click("email"), "gate", revealed, _A)
        if build_context:
            for _ in range(3):
                env.context("gate", _A, signal)
                env.context("gate", _A_DECORATED, signal)
                env.context("other", _B, signal)
        second = env.exploration.observe_step(_click("email"), "gate", frozenset(), _A)
        reward, _info = compose_reward(
            RewardSignal(is_expected=True, severity=0.0, explanation=""),
            bug_signals=BugSignals(), weights=FunctionalRewardWeights(), exploration=second,
        )
        return reward

    assert run(build_context=True) == run(build_context=False)
