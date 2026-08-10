"""Decides which steps are worth paying a judge call for.

The constraint is measured, not estimated: `gpt-oss:120b` costs ~7-9 s per window and a
local 7B costs 11-18 s, so judging every step of a 40-step episode costs 7-12 minutes.
At the 30-50K-step budget §8 plans for, judging every step is not slow — it is
impossible.

**The filter cannot be "the state changed".** A control that promises an effect and
produces none is a dead control, which is one of the bug classes the judge exists to
catch and the single most commonly missed one. Filtering on change would discard exactly
those windows. The rule is instead "was an action taken that *should* have changed
something" — a property of the action and its target, decided before the outcome is
known.

Three further principles, each traceable to a measured defect:

* **Never gate away a deterministic trigger.** If a trigger fired, something happened
  that is worth a verdict even if the action looked unpromising, and the judge's
  agreement or disagreement with the trigger is information either way.
* **Skip what the prompt already refuses to judge.** The judge is instructed not to
  report failed actions or harness-blocked navigations, so a call on those buys a
  guaranteed "not a bug" at full price. On the Gitea corpus that is 20% of all steps.
* **Skipping a step as a *focus* does not remove it from the window.** A TYPE step is
  usually not worth judging on its own, but it is essential context for the cross-step
  bugs (a dropped field is only visible by comparing what was typed against what a later
  page echoes). The rolling window keeps every step; the gate only decides where the
  judge is asked to look.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..envs.types import ActionType

# Actions that assert something should happen. A click on a control is a request for an
# effect; typing into a field is not, and neither is scrolling or resizing.
_PROMISING_ACTIONS = frozenset({ActionType.CLICK, ActionType.RAPID_CLICK, ActionType.SELECT})

# Actions whose *only* interesting outcome is a change they caused. REFRESH and the
# history moves promise nothing by themselves — the prompt says so explicitly — but a
# reload that loses a saved setting is `state_persistence`, and that is only visible as
# a change. So they are judged when something changed and skipped when nothing did.
_CHANGE_ONLY_ACTIONS = frozenset(
    {
        ActionType.REFRESH,
        ActionType.BROWSER_BACK,
        ActionType.BROWSER_FORWARD,
        ActionType.SCROLL,
        ActionType.RESIZE_VIEWPORT,
        ActionType.TYPE,
    }
)


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Whether to judge this step, and why — the reason is logged and reported."""

    judge: bool
    reason: str


def should_judge(
    action_type: ActionType,
    *,
    exec_success: bool,
    blocked: bool,
    state_changed: bool,
    triggered: bool,
    settled: bool = True,
) -> GateDecision:
    """Decide whether this transition is worth a judge call.

    Takes the action *type* rather than a full `ActionSpec` so the identical function
    can be replayed over a captured corpus, where only the recorded action dict exists.
    That is what makes the gate measurable before it is trusted: a filter whose cost
    saving is estimated but whose losses are not is exactly the kind of change this
    project has repeatedly found to be wrong only after running it.

    `state_changed` must mean the document or URL genuinely differs (a hash comparison),
    not that a summary rendered a diff — conflating the two is what previously fed the
    judge a false byte-identity claim on every retyped field.
    """
    if triggered or not settled:
        # A trigger fired, or the page never stopped mutating. Both are the deterministic
        # baseline saying something happened; the judge is being asked to characterize it.
        return GateDecision(True, "deterministic trigger fired")

    if state_changed:
        # Checked *before* the failure rules, and that ordering is the whole point.
        # "The action failed" and "the action had no effect" are different claims, and
        # Playwright routinely reports the first while the second is false: a RAPID_CLICK
        # navigates on its first click, so clicks 2-5 time out against a detached
        # element and the burst is recorded `success: False`. Measured on the toy
        # corpus — that exact step carried BUG-01's evidence in the URL it had just
        # navigated to (`?username=…&age=…` with no `email`), and an earlier version of
        # this gate skipped it as "did not execute", discarding a confident semantic
        # finding to save one call.
        return GateDecision(True, "the page changed")

    # Everything below is an action that left the page as it found it.

    if blocked:
        # The harness refused the navigation and put the page back. Nothing about the
        # application was exercised, and the prompt forbids reporting it.
        return GateDecision(False, "harness refused the action")
    if not exec_success:
        # A stale selector or a timeout with nothing to show for it is an explorer
        # problem. The prompt says so, so the verdict is knowable without asking — and
        # "nothing changed" is not dead-control evidence when the click never landed.
        return GateDecision(False, "action did not execute and changed nothing")

    if action_type is ActionType.NO_OP:
        # An idle no-op is the definition of no evidence. Mirrors
        # `build_windows(skip_idle=True)`, which drops these before judging anyway.
        return GateDecision(False, "idle no-op")

    if action_type in _PROMISING_ACTIONS:
        # The load-bearing case. Judged whether or not anything changed, because
        # *nothing* changing is precisely the dead-control signal.
        return GateDecision(True, "activated a control that promises an effect")

    if action_type in _CHANGE_ONLY_ACTIONS:
        # Reached only when the page is unchanged: the change case was decided above.
        return GateDecision(False, f"{action_type.value} promises no effect and changed nothing")

    return GateDecision(True, "unclassified action, judged by default")


def decide_for_record(record) -> GateDecision:  # noqa: ANN001 - annotation.trace.StepRecord
    """The same gate, applied to a *captured* step rather than a live one.

    Exists so the offline scorer, the gate-replay script and the live reward path all
    ask one function. Two copies of this decision would drift, and the drift would be
    invisible: the offline number would silently stop describing the shipped pipeline,
    which is the exact failure mode this project has already hit with window rendering.
    """
    try:
        action_type = ActionType(record.action.get("type", "NO_OP"))
    except ValueError:
        action_type = ActionType.NO_OP
    return should_judge(
        action_type,
        exec_success=bool(record.exec.get("success", False)),
        blocked=bool(record.exec.get("left_application")),
        state_changed=record.state_changed,
        triggered=record.triggered,
        settled=record.settled,
    )


@dataclass(slots=True)
class GateStats:
    """Running tally, so the saving is reported rather than assumed."""

    judged: int = 0
    skipped: int = 0
    reasons: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.reasons is None:
            self.reasons = {}

    def record(self, decision: GateDecision) -> None:
        if decision.judge:
            self.judged += 1
        else:
            self.skipped += 1
        self.reasons[decision.reason] = self.reasons.get(decision.reason, 0) + 1

    @property
    def total(self) -> int:
        return self.judged + self.skipped

    @property
    def judged_fraction(self) -> float:
        return self.judged / self.total if self.total else 0.0

    def to_dict(self) -> dict:
        return {
            "steps": self.total,
            "judged": self.judged,
            "skipped": self.skipped,
            "judged_fraction": round(self.judged_fraction, 3),
            "reasons": dict(sorted(self.reasons.items(), key=lambda kv: -kv[1])),
        }
