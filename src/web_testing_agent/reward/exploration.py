"""Coverage and repetition tracking for Agent B's exploration incentive.

Agent A's spec has an explicit -3 repetition penalty; Agent B's did not, which leaves
a value-based agent free to camp on one already-discovered buggy element and farm the
deterministic trigger bonus forever instead of covering the app. This module supplies
the two counter-signals the reward needs:

* **novelty** — did this step reach a page state the agent has not seen this episode?
* **repetition** — how many times has it already taken this exact action on this exact
  element in the recent past?

Novelty is *episodic* (reset each episode, as in NGU/RND-style episodic curiosity)
**and count-decayed across the run**. The episodic half keeps rewarding "get somewhere
new from the start state" rather than decaying to nothing once the site is covered; the
count-based half divides the bonus by `sqrt(1 + visits_this_run)` so that re-collecting
the same shallow page every episode pays progressively less without ever paying zero.

That second half is a fix for a measured failure. With a flat +1.0 per episodic
first-visit, reaching a static footer page paid exactly what advancing a gated flow
stage paid, and was far easier — the deep-flow fixture ships eleven shallow distractors
against a four-stage flow. On every seed of every run, `random_masked` earned the best
reward in the table while reaching the lowest flow depth of any policy. Reward and
objective pointed in different directions.

A third signal was added for the same reason: **newly-revealed actions**. A gated flow
reveals its next control once its input is valid, so the action set *grows* in response
to real progress, and that growth is measurable for free by diffing consecutive action
sets. It is the only term here that is genuinely depth-correlated.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..envs.types import ActionSpec, ActionType

# Action types whose effect can legitimately reveal a previously absent control, i.e.
# open a gate. RESIZE_VIEWPORT and SCROLL are excluded deliberately: they change what is
# *visible* without the application having changed state, so paying them a progress
# bonus would reward oscillating the viewport rather than advancing a flow.
def _path_of(url: str) -> str:
    """A URL reduced to scheme://host/path — the identity of *the page*.

    Query and fragment are dropped: they routinely carry flow state (`?product=a`) or
    in-page anchors, and neither changes which gate a page offers.
    """
    return url.split("#", 1)[0].split("?", 1)[0]


_GATE_OPENING_ACTIONS = frozenset({
    ActionType.CLICK.value, ActionType.TYPE.value,
    ActionType.SELECT.value, ActionType.RAPID_CLICK.value,
})


@dataclass(frozen=True, slots=True)
class ExplorationSignal:
    """Per-step exploration bookkeeping handed to the reward composer."""

    is_novel_state: bool
    repeat_count: int
    episode_states_seen: int
    total_states_seen: int
    # How many times this state has been reached across the WHOLE run, not just this
    # episode. The novelty bonus is divided by sqrt(1 + this), so re-collecting the same
    # shallow page every episode pays progressively less while never dropping to zero.
    # A flat episodic bonus was measured to make breadth strictly dominate depth:
    # `random_masked` earned the best reward on every seed of every deep-flow run while
    # reaching the lowest flow depth of any policy.
    state_run_visits: int = 0
    # Action identities that appeared in the action set as a *result* of this step.
    # A gated flow reveals its next control once its input is valid, so this is the
    # progress signal the flat novelty bonus lacked. Measured on the deep-flow fixture:
    # the gate-opening SELECT on order-1.html reveals exactly {('CLICK', 'Continue')}.
    newly_revealed: frozenset[tuple[str, str]] = frozenset()
    # The subset of `newly_revealed` not already paid for this episode.
    unpaid_reveals: frozenset[tuple[str, str]] = frozenset()

    @property
    def is_repetitive(self) -> bool:
        return self.repeat_count > 0


@dataclass
class ExplorationTracker:
    """Tracks state coverage and recent action repetition across an episode.

    `repetition_window` bounds how far back a repeat counts, so an agent that
    legitimately revisits an element much later in a long flow is not punished for it.
    """

    repetition_window: int = 20
    _episode_states: set[str] = field(default_factory=set, init=False)
    _all_states: set[str] = field(default_factory=set, init=False)
    _recent_actions: deque[tuple[str, str]] = field(default_factory=deque, init=False)
    # state_key -> times reached across the whole run. Drives the count-based decay of
    # the novelty bonus; kept separate from `_all_states`, which only answers "seen".
    _state_visits: dict[str, int] = field(default_factory=dict, init=False)
    # Action identities available at the previous step, for the newly-revealed signal.
    _previous_identities: set[tuple[str, str]] = field(default_factory=set, init=False)
    # The URL those identities came from. Without it a navigation reads as a page
    # revealing every one of its controls at once — see `observe_action_set`.
    _previous_url: str = field(default="", init=False)
    # (state_key, revealed identity) already paid this episode, so a control that can be
    # revealed and re-hidden — a checkbox toggling a panel — pays once rather than per
    # cycle. Same episodic-ledger pattern as `FindingLedger`, and for the same reason.
    _paid_reveals: set[tuple[str, tuple[str, str]]] = field(default_factory=set, init=False)

    @property
    def total_states_seen(self) -> int:
        """Distinct page states reached across every episode — the coverage metric."""
        return len(self._all_states)

    @property
    def episode_states_seen(self) -> int:
        return len(self._episode_states)

    def start_episode(self) -> None:
        self._episode_states = set()
        self._recent_actions = deque(maxlen=self.repetition_window)
        self._paid_reveals = set()
        # Nothing counts as newly revealed on the first observation of an episode: the
        # entire action set is new then, and treating that as a gate opening would pay
        # every landing page the full progress bonus.
        self._previous_identities = set()
        self._previous_url = ""

    def observe_reset(self, state_key: str) -> None:
        """Register the episode's starting state so it never counts as a discovery."""
        self._episode_states.add(state_key)
        self._all_states.add(state_key)
        self._state_visits[state_key] = self._state_visits.get(state_key, 0) + 1

    def observe_action_set(
        self, specs: list[ActionSpec], url: str = ""
    ) -> frozenset[tuple[str, str]]:
        """Record the action set now available and return the identities a *gate* revealed.

        Called by the env once per step, after the action space has been rebuilt. This
        is the **single** definition of "newly revealed": the reward reads it from here
        and the policy's action features read it from `info`, so the term the agent is
        paid for and the feature it is shown cannot drift apart.

        **`url` is load-bearing, and omitting it was a measured defect.** The first
        version diffed consecutive action sets with no notion of *where* they came from.
        A navigation replaces the entire action set, so every link on the destination
        page counted as newly revealed, and the bonus scaled with how many links that
        page happened to have. Decomposed on masked random over 200 steps of the
        deep-flow fixture: the reveal term paid **+87.00 of a +70.62 total return**, and
        **81.00 of that 87.00 — 93% — came from steps that changed page**. The
        identities it paid for were ordinary nav links (`Filing`, `Delivery times`,
        `Returns`). It was a breadth bonus wearing a depth bonus's name, and it made the
        reward/objective inversion it was built to fix strictly worse.

        A gate reveals a control on the page you are **already on** — every gate in the
        deep-flow fixture works that way (a SELECT on `order-1.html` reveals `Continue`
        on `order-1.html`). So three conditions must all hold:

        1. the URL is unchanged, so this is not a navigation;
        2. the action set genuinely **grew**, so a same-page swap of one control for
           another is not a reveal;
        3. the identities are new relative to the previous set.

        On a navigation the baseline is simply re-anchored to the new page and nothing
        is paid, so the next same-page gate on that page is still detected normally.
        """
        identities = {self._action_identity(spec) for spec in specs}
        previous, previous_url = self._previous_identities, self._previous_url
        self._previous_identities = identities
        self._previous_url = url

        if not previous or url != previous_url:
            # First observation of the episode, or a navigation. Re-anchor, pay nothing.
            return frozenset()
        if len(identities) <= len(previous):
            # Same page, no growth: a replacement, not a reveal.
            return frozenset()
        return frozenset(identities - previous)

    def state_visits(self, state_key: str) -> int:
        """Times this state has been reached across the whole run."""
        return self._state_visits.get(state_key, 0)

    def observe_step(
        self,
        spec: ActionSpec,
        state_key: str,
        newly_revealed: frozenset[tuple[str, str]] | None = None,
        url: str = "",
    ) -> ExplorationSignal:
        identity = self._action_identity(spec)
        repeat_count = sum(1 for entry in self._recent_actions if entry == identity)
        # NO_OP is counted like any other action. An earlier version exempted it, on the
        # reasoning that "the absence of an action isn't a repetition and the step
        # penalty already prices it". That made NO_OP the single action in the whole
        # space immune to a penalty reaching -3.0, so as soon as novelty ran out, doing
        # nothing strictly dominated every alternative. A trained DQN converged to 100%
        # NO_OP for exactly this reason. Repeatedly doing nothing is the behaviour this
        # penalty most needs to discourage, not the one it should exempt.
        self._recent_actions.append(identity)

        is_novel = state_key not in self._episode_states
        self._episode_states.add(state_key)
        self._all_states.add(state_key)
        visits_before = self._state_visits.get(state_key, 0)
        self._state_visits[state_key] = visits_before + 1

        revealed = newly_revealed or frozenset()
        # **The ledger is keyed on the URL *path*, not the state key.** Measured on the
        # deep-flow fixture: `Back` drops the query string, so `order-3.html` and
        # `order-3.html?product=a&quantity=42` are different `state_key`s even though
        # they are the same page with the same gate. Keying on the state therefore let
        # the same gate be re-opened for a second full payment, and the trained policy
        # found it — all three seeds learned the loop
        # `order-4 -> Back -> order-3 -> TYPE email -> Continue -> order-4`, whose
        # 3-step return (+4.11) beat completing the flow (+2.82) and so beat the one
        # action that reaches the seeded bug.
        #
        # A gate on a page is opened once per episode, however the URL was decorated on
        # the way in. That is a general statement about applications that carry flow
        # state in query parameters, not a rule about this fixture.
        #
        # Falling back to `state_key` when no URL is supplied keeps the older, narrower
        # scope for callers that predate this argument rather than silently widening the
        # ledger to a single global scope, which would suppress genuine gates on other
        # pages that happen to reveal an identically-labelled control.
        scope = _path_of(url) if url else state_key
        # Only the action types that can actually open a gate earn the reveal bonus.
        # RESIZE_VIEWPORT genuinely changes which controls are visible — that is the
        # point of the action and the reason BUG-08 is findable — so leaving it in would
        # let an agent farm progress by toggling between mobile and desktop width.
        unpaid = frozenset(
            identity for identity in revealed
            if identity[0] in _GATE_OPENING_ACTIONS
            and (scope, identity) not in self._paid_reveals
        )
        for identity in unpaid:
            self._paid_reveals.add((scope, identity))

        return ExplorationSignal(
            is_novel_state=is_novel,
            repeat_count=repeat_count,
            episode_states_seen=len(self._episode_states),
            total_states_seen=len(self._all_states),
            state_run_visits=visits_before,
            newly_revealed=revealed,
            unpaid_reveals=unpaid,
        )

    @staticmethod
    def _action_identity(spec: ActionSpec) -> tuple[str, str]:
        """What counts as "the same action" for repetition purposes.

        Keyed on the *semantic* target (action type + element identity + value category),
        not the action index — indices are rebuilt every step and mean nothing across
        states, so an index-keyed counter would never detect a genuine repeat.
        """
        target = spec.element_id or spec.selector or ""
        if spec.action_type is ActionType.TYPE:
            target = f"{target}#{spec.params.get('category', '')}"
        elif spec.action_type is ActionType.SELECT:
            target = f"{target}#{spec.params.get('option', '')}"
        elif spec.action_type is ActionType.RESIZE_VIEWPORT:
            target = str(spec.params.get("preset", ""))
        elif spec.action_type is ActionType.SCROLL:
            target = str(spec.params.get("direction", ""))
        return (spec.action_type.value, target)
