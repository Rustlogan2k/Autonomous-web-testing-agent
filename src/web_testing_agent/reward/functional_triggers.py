"""Deterministic (non-LLM) functional bug-trigger detection and reward composition.

Per the spec, these "supplement" the LLM reward model - WebFunctionalEnv produces
a meaningful reward signal from these alone, before any LLM reward model exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..envs.types import BugSignals, NetworkEvent, RawObservation
from .base import RewardSignal
from .exploration import ExplorationSignal


@dataclass
class ErrorBaseline:
    """The HTTP errors a target app emits as a matter of course, per page.

    Every one of the five training targets 404s on something harmless on every page
    load — a favicon, a sourcemap, an optional locale bundle. Counting those as bug
    signals would pay a flat bonus for merely reloading a page, which makes REFRESH an
    infinite reward pump. Errors observed while the episode is still resetting are
    therefore recorded here and excluded from later steps' signals.
    """

    _known: set[tuple[str, int]] = field(default_factory=set, init=False)

    def learn(self, events: list[NetworkEvent]) -> None:
        for event in events:
            if event.is_error:
                self._known.add(event.error_key())

    def is_known(self, event: NetworkEvent) -> bool:
        return event.error_key() in self._known

    def filter_unexpected(self, events: list[NetworkEvent]) -> list[NetworkEvent]:
        return [event for event in events if event.is_error and not self.is_known(event)]

    def __len__(self) -> int:
        return len(self._known)


@dataclass
class FindingLedger:
    """Tracks which distinct findings have already been paid out this episode.

    **This is the fix for a measured reward-hacking exploit.** Without it, a trigger
    pays its bonus on every step its condition holds, so the highest-return policy is to
    reach one broken page and stay there. A DQN trained on the toy site found exactly
    that: click the 404 link once, then REFRESH for the remaining 117 steps. A 404
    *document* stacks console_error (+2) + http_error (+3) + document_http_error (+2) =
    +7.0/step against a repetition penalty floored at -3.0, netting +3.95/step
    indefinitely — 65x the random baseline's return, from two states and one real bug.

    The rollout harness already deduplicated findings for *reporting*; the asymmetry was
    that the reward did not deduplicate for *learning*. A bug is a discovery, and a
    discovery happens once.

    The ledger is **episodic**, not permanent: resetting it each episode keeps the
    association learnable (a permanent ledger would make the signal vanish after episode
    one, so the agent could never learn what caused the finding) while removing any
    within-episode farm. Same rationale as episodic novelty in `reward.exploration`.
    """

    _paid: set[tuple] = field(default_factory=set, init=False)

    def start_episode(self) -> None:
        self._paid = set()

    def classify(self, state_key: str, element: str, bug_signals: BugSignals) -> frozenset[str]:
        """Return the subset of fired triggers not already paid for in this episode."""
        fired: list[str] = []
        if bug_signals.console_errors:
            fired.append("console_errors")
        if bug_signals.unexpected_http_errors:
            fired.append("unexpected_http_errors")
        if bug_signals.document_http_error:
            fired.append("document_http_error")
        if bug_signals.slow_response:
            fired.append("slow_response")
        if bug_signals.broken_navigation:
            fired.append("broken_navigation")

        novel = set()
        for trigger in fired:
            # Keyed on (trigger, page state, element) — the same identity the evaluation
            # harness uses for a "distinct finding", so reward and score agree on what
            # counts as one bug.
            key = (trigger, state_key, element)
            if key not in self._paid:
                self._paid.add(key)
                novel.add(trigger)
        return frozenset(novel)

    def paid_at(self, state_key: str) -> int:
        """How many distinct findings have already been paid for at this page state."""
        return sum(1 for trigger, state, _element in self._paid if state == state_key)

    def __len__(self) -> int:
        return len(self._paid)


def detect_bug_signals(
    after: RawObservation,
    load_duration_s: float,
    pre_url: str,
    post_url: str,
    was_navigation_action: bool,
    settled: bool = True,
    baseline: ErrorBaseline | None = None,
    opened_new_page: bool = False,
    previously_settled: bool = True,
) -> BugSignals:
    """Inspect one step's outcome for the deterministic bug indicators from the spec.

    `slow_response` is the spec's "loading spinner visible >5s" trigger, expressed as
    "the DOM never stopped mutating before the agent's patience ran out" (`settled` is
    False). Detecting an actual spinner icon would need a vision model; this is an
    honest proxy measured at the same place a user's patience would run out.

    It is reported only on the *transition* into an unsettled page (`previously_settled`).
    A page stuck in a runaway timer never settles again, so without this edge-trigger one
    genuine hang is re-reported against every innocent action taken afterwards — measured
    on the toy site, a single stuck spinner produced four separate "findings" blaming a
    viewport resize and a working counter button.
    """
    baseline = baseline if baseline is not None else ErrorBaseline()
    unexpected_http_errors = baseline.filter_unexpected(after.network_events)
    document_http_error = any(event.is_document for event in unexpected_http_errors)

    # A link click that opened a popup did navigate — just not in this tab.
    broken_navigation = (
        was_navigation_action
        and not opened_new_page
        and (post_url == pre_url or document_http_error)
    )

    return BugSignals(
        console_errors=list(after.console_errors) + list(after.page_errors),
        unexpected_http_errors=unexpected_http_errors,
        slow_response=not settled and previously_settled,
        broken_navigation=broken_navigation,
        load_duration_s=load_duration_s,
        document_http_error=document_http_error,
    )


@dataclass(frozen=True, slots=True)
class FunctionalRewardWeights:
    """Tunable magnitudes for combining the LLM judgment with deterministic triggers.

    `bug_severity_scale` matches the project spec's LLM reward formula exactly. The
    rest are this implementation's addition for the "deterministic triggers supplement
    LLM reward" requirement - treat them as a hyperparameter surface to retune once
    real training data exists, not a fixed design decision.

    **`expected_reward` is 0.0, not the spec's +0.5, and this is deliberate.** Paying a
    guaranteed, zero-variance +0.5 for every step where nothing bad happened makes
    doing nothing the single most reliable source of return: with gamma=0.99 over a
    200-step episode an all-NO_OP policy banks ~43 discounted reward, against ~10 for
    actually finding a bug. Worse, out-of-range action indices resolve to NO_OP, so on
    a page with ~30 real actions roughly 70% of the 100-slot space pays that bonus for
    free. Agent A's spec counterweights its rewards with -0.5/step; Agent B's inherited
    the bonus without the counterweight. Progress is rewarded here through
    `novelty_bonus` (reaching a new state) instead of through mere survival.
    """

    expected_reward: float = 0.0
    bug_severity_scale: float = 10.0
    step_penalty: float = -0.05
    console_error_bonus: float = 2.0
    http_error_bonus: float = 3.0
    document_http_error_bonus: float = 2.0
    slow_response_bonus: float = 1.0
    broken_navigation_bonus: float = 2.0
    # Exploration shaping (see reward.exploration).
    #
    # The repetition penalty was originally sized (-0.5/repeat, floored at -3.0) to be
    # the *sole* defence against bug-farming, so it had to outweigh a stacked trigger
    # bonus. It lost that job to `FindingLedger`, and at that magnitude it kept a second,
    # unintended effect: on a small site novelty is exhausted within a few steps, after
    # which every remaining action costs up to -3.0 and passivity wins. Now that it only
    # has to discourage pointless repetition, it is scaled to sit below `novelty_bonus`
    # — exploring somewhere new must always beat standing still.
    novelty_bonus: float = 1.0
    repetition_penalty: float = -0.15
    max_repetition_penalty: float = -1.0
    # A stale selector or timed-out click is legitimate data, but shouldn't be free.
    failed_action_penalty: float = -0.1
    # What an *already-discovered* finding pays when its condition still holds. Zero:
    # a bug is discovered once. Any positive value re-opens the farming exploit that
    # `FindingLedger` exists to close, and it re-opens it multiplied by however many
    # triggers a single broken page happens to stack.
    rediscovery_bonus: float = 0.0


def compose_reward(
    llm_signal: RewardSignal,
    bug_signals: BugSignals,
    weights: FunctionalRewardWeights = FunctionalRewardWeights(),
    exploration: ExplorationSignal | None = None,
    exec_success: bool = True,
    new_triggers: frozenset[str] | None = None,
) -> tuple[float, dict]:
    """Combine the LLM judgment, deterministic triggers, and exploration shaping.

    `new_triggers` comes from `FindingLedger.classify` and names the triggers that fired
    on a finding **not already paid for this episode**. Triggers outside it are
    re-discoveries and pay `rediscovery_bonus` (0.0 by default). Passing `None` pays
    every fired trigger in full, which is the behaviour that produced the measured
    reward-hacking exploit — it exists only so the composer stays usable standalone in
    tests, and callers driving a real episode should always supply the ledger's verdict.

    Returns `(total_reward, breakdown)`; the breakdown is surfaced in `info` so training
    runs can log each term separately and diagnose which one the agent is optimizing.
    """
    base = (
        weights.expected_reward
        if llm_signal.is_expected
        else weights.bug_severity_scale * llm_signal.severity
    )

    def _trigger_bonus(name: str, full_value: float) -> float:
        if new_triggers is None or name in new_triggers:
            return full_value
        return weights.rediscovery_bonus

    bug_bonus = 0.0
    if bug_signals.console_errors:
        bug_bonus += _trigger_bonus("console_errors", weights.console_error_bonus)
    if bug_signals.unexpected_http_errors:
        bug_bonus += _trigger_bonus("unexpected_http_errors", weights.http_error_bonus)
    if bug_signals.document_http_error:
        bug_bonus += _trigger_bonus("document_http_error", weights.document_http_error_bonus)
    if bug_signals.slow_response:
        bug_bonus += _trigger_bonus("slow_response", weights.slow_response_bonus)
    if bug_signals.broken_navigation:
        bug_bonus += _trigger_bonus("broken_navigation", weights.broken_navigation_bonus)

    exploration_bonus = 0.0
    if exploration is not None:
        if exploration.is_novel_state:
            exploration_bonus += weights.novelty_bonus
        if exploration.repeat_count:
            # Linear in the repeat count so hammering one element degrades fast, but
            # floored so a single sticky element cannot dominate the whole return.
            exploration_bonus += max(
                weights.repetition_penalty * exploration.repeat_count,
                weights.max_repetition_penalty,
            )

    step_cost = weights.step_penalty
    if not exec_success:
        step_cost += weights.failed_action_penalty

    total = base + bug_bonus + exploration_bonus + step_cost
    breakdown = {
        "llm_base_reward": base,
        "llm_is_expected": llm_signal.is_expected,
        "llm_severity": llm_signal.severity,
        "llm_explanation": llm_signal.explanation,
        "deterministic_bonus": bug_bonus,
        "new_triggers": sorted(new_triggers) if new_triggers is not None else None,
        "exploration_bonus": exploration_bonus,
        "step_cost": step_cost,
        "bug_signals": bug_signals,
        "exploration": exploration,
    }
    return total, breakdown
