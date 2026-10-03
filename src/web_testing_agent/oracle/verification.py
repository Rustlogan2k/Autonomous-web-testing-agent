"""Candidate finding -> replay -> reproduction -> minimization -> confirmed finding.

**Why replay is not optional.** A trajectory that trips an assertion once is a *candidate*.
The application may have been left in a state by earlier exploration, the harness may have
raced, or the assertion may have matched a transient render. `budget-to-first-verified-fault`
counts confirmed faults, and a fault is confirmed only when an independent replay from a
fresh session reproduces the violation the required number of times.

**Why minimization is part of verification and not a reporting nicety.** A 400-step
exploration trace that ends in a violation does not tell a developer what caused it, and it
does not tell *us* that the agent found the fault rather than stumbled through every state
in the application. A minimized trace of four actions that still trips the assertion is
evidence about the fault; the 400-step original is evidence about the search.

The minimizer is **delta debugging** (Zeller's ddmin) over the action sequence. Each trial
is a real replay, so the cost is bounded deliberately: `max_trials` caps the number of
replays, and the minimizer returns the best sequence found so far when it runs out. A
minimizer that runs unbounded would dominate the experiment's wall clock.

**Why a clean-build control is part of confirmation.** An assertion is a statement about
observed pages, and some traces trip it on a correct application: typing a quantity and
then adding the same product twice makes the confirmation disagree with "the quantity
entered" on a build with no fault in it. Such a trace reproduces perfectly, every time,
and is not a finding. A seeded fault is a *difference between the two builds*, so the
definition is applied literally: when a control runner is supplied, a candidate is
confirmed only if the trace violates on the faulty build and does **not** violate on the
clean one. The control is deterministic and consults no model, so the oracle's
independence is unchanged.

**The control is per fault, not merely "the clean build" (code review, 2026-10-03).** A
faulty build carries every seeded patch of its application, so fault A's patch can open a
page on which fault B's assertion fires for a reason that has nothing to do with B -- and
the fully clean build, never reaching that page, is merely inconclusive and passes. Three
of the twelve benchmark faults could be credited that way. So when per-fault controls are
supplied, the control for fault F is the faulty build *minus F's own patch*: F is confirmed
only if the violation needs F's patch. A fault whose own patch is what opens the page (a
constraint bypass) is still confirmed, because on its control the page is not reached.

A control replay that raises is a failed control, never a passed one: the candidate is
rejected as `control_error` and may be verified again on a later sighting.

**The replayer is the existing `ScriptedPolicy`.** It already resolves a step against the
live action space by id or visible label, already retries a step whose target has not
rendered yet, and already records misses. Writing a second replay path would mean two
notions of "the same action" and they would disagree on the first fixture that repeats a
label.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

from ..utils.logging import get_logger
from .assertions import FaultVerdict, verify_fault
from .faults import BenchmarkApp, FaultSpec
from .trajectory import TrajectoryView, build_trajectory

logger = get_logger(__name__)

#: Cap on replays spent shrinking one trace. At roughly a second per replayed action on a
#: local fixture, 60 trials is about a minute per confirmed finding -- affordable once per
#: run, which is how often a first verified fault happens.
DEFAULT_MAX_TRIALS = 60


class TrajectoryRunner(Protocol):
    """Runs an action sequence against a fresh instance and returns what was observed.

    The seam between the oracle and the browser. Implemented for real by
    `PlaywrightTrajectoryRunner`; implemented by a fake in the tests, which is what lets
    the whole verification pipeline be tested without Playwright or Docker.
    """

    def run(self, actions: list[dict]) -> TrajectoryView: ...


@dataclass(slots=True)
class ReplayOutcome:
    """One independent replay of a candidate trace."""

    reproduced: bool
    inconclusive: bool
    steps_observed: int
    verdict: dict = field(default_factory=dict)
    error: str = ""
    #: Scripted steps the replay could not resolve on the live page.
    misses: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class ConfirmedFinding:
    """A seeded fault the oracle stands behind, with everything needed to act on it."""

    fault_id: str
    app_id: str
    confirmed: bool
    #: Interactions consumed before the candidate was first observed. This is the number
    #: that feeds `budget-to-first-verified-fault`; replay and minimization cost is
    #: reported separately and never counted against the search budget, because it is
    #: verification rather than exploration.
    budget_at_discovery: int = 0
    original_trace: list[dict] = field(default_factory=list)
    minimized_trace: list[dict] = field(default_factory=list)
    replays: list[dict] = field(default_factory=list)
    candidate_verdict: dict = field(default_factory=dict)
    minimization: dict = field(default_factory=dict)
    verification_steps: int = 0
    wall_clock_s: float = 0.0
    rejected_because: str = ""
    #: Why a candidate was not confirmed, as a closed vocabulary so rejections can be
    #: tallied: `assertion_silent`, `fires_on_clean_build`, `not_reproduced`. Empty when
    #: the finding is confirmed.
    rejection_kind: str = ""
    #: The clean-build control: whether one ran, and what it concluded.
    control: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# -- minimization ------------------------------------------------------------------------


@dataclass(slots=True)
class MinimizationResult:
    trace: list[dict]
    original_length: int
    minimized_length: int
    trials: int
    exhausted_budget: bool
    kept_by_rule: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def minimize_trace(actions: list[dict], reproduces: Callable[[list[dict]], bool],
                   *, max_trials: int = DEFAULT_MAX_TRIALS,
                   keep: Callable[[dict], bool] | None = None) -> MinimizationResult:
    """Delta debugging over an action sequence. Returns the shortest reproducing prefix set.

    Standard ddmin: try removing complements of increasingly fine partitions, and whenever
    a removal still reproduces, keep it and restart at coarse granularity. `keep` marks
    actions the fault declares un-removable (session setup, for instance).

    The caller's `reproduces` runs a real replay, so `max_trials` is a hard stop. Returning
    the best-so-far on exhaustion is the right failure mode: a partially minimized trace is
    still far more useful than the original, and a minimizer that never terminates is not.
    """
    keep = keep or (lambda _action: False)
    pinned = [index for index, action in enumerate(actions) if keep(action)]
    current = list(actions)
    trials = 0
    granularity = 2

    while len(current) > 1 and trials < max_trials:
        chunk = max(1, len(current) // granularity)
        reduced = False

        for start in range(0, len(current), chunk):
            if trials >= max_trials:
                break
            complement = current[:start] + current[start + chunk:]
            # Never propose a candidate that drops a pinned action.
            if any(keep(action) for action in current[start:start + chunk]):
                continue
            if not complement:
                continue
            trials += 1
            if reproduces(complement):
                current = complement
                granularity = max(granularity - 1, 2)
                reduced = True
                break

        if not reduced:
            if granularity >= len(current):
                break
            granularity = min(len(current), granularity * 2)

    return MinimizationResult(
        trace=current, original_length=len(actions), minimized_length=len(current),
        trials=trials, exhausted_budget=trials >= max_trials, kept_by_rule=len(pinned),
    )


# -- the pipeline --------------------------------------------------------------------------


class VerificationPipeline:
    """Turns candidate findings into confirmed ones, or rejects them with a reason.

    Holds no reference to the policy, the reward, the archive or any model. Its only
    collaborators are the application's fault library and a `TrajectoryRunner`.
    """

    def __init__(self, app: BenchmarkApp, runner: TrajectoryRunner,
                 *, control_runner: TrajectoryRunner | None = None,
                 control_runners: dict[str, TrajectoryRunner] | None = None,
                 max_minimization_trials: int = DEFAULT_MAX_TRIALS) -> None:
        problems = app.validate()
        if problems:
            # Loud and early. A fixture whose assertions name a region it does not define
            # would otherwise read as "the agent never found the bug", hours in.
            raise ValueError(f"{app.app_id} has unusable fault metadata: {problems}")
        self.app = app
        self.runner = runner
        #: Replays a trace against the build with no seeded fault. Optional, because an
        #: application under test in the field has no clean twin; on the benchmark it is
        #: always supplied, and it is what keeps a trace artefact out of the metric.
        self.control_runner = control_runner
        #: `{fault_id: runner on the faulty build minus that fault's patch}`. Takes
        #: precedence over `control_runner` for the faults it names.
        self.control_runners = dict(control_runners or {})
        self.max_minimization_trials = max_minimization_trials

    def control_for(self, fault: FaultSpec) -> TrajectoryRunner | None:
        return self.control_runners.get(fault.fault_id, self.control_runner)

    def _control_kind(self, fault: FaultSpec) -> str:
        if fault.fault_id in self.control_runners:
            return "faulty build without this fault's patch"
        return "clean build" if self.control_runner is not None else "none"

    # -- candidate detection ---------------------------------------------------------

    def candidates(self, trajectory: TrajectoryView) -> list[FaultVerdict]:
        """Faults whose assertions fire on this trajectory. Candidates, not findings."""
        return [verdict for verdict in
                (verify_fault(fault, self.app, trajectory) for fault in self.app.faults)
                if verdict.violated]

    # -- confirmation ------------------------------------------------------------------

    def confirm(self, fault: FaultSpec, trajectory: TrajectoryView,
                budget_at_discovery: int) -> ConfirmedFinding:
        """Replay, reproduce, minimize. Returns a finding, confirmed or rejected."""
        started = time.monotonic()
        finding = ConfirmedFinding(
            fault_id=fault.fault_id, app_id=self.app.app_id, confirmed=False,
            budget_at_discovery=budget_at_discovery,
            original_trace=list(trajectory.actions),
        )
        candidate = verify_fault(fault, self.app, trajectory)
        finding.candidate_verdict = candidate.to_dict()
        if not candidate.violated:
            finding.rejected_because = "the candidate's assertions did not fire"
            finding.rejection_kind = "assertion_silent"
            finding.wall_clock_s = round(time.monotonic() - started, 2)
            return finding

        spent = 0
        control_runner = self.control_for(fault)

        # The control runs first because it is one replay and the commonest reason a
        # candidate is not a finding: rejecting here saves the reproduction replays.
        if control_runner is not None:
            control = self._replay_once(fault, trajectory.actions, control_runner)
            spent += control.steps_observed
            finding.control = {"checked": True, "against": self._control_kind(fault),
                               "violated_on_control": control.reproduced,
                               "inconclusive": control.inconclusive, "error": control.error,
                               "misses": control.misses}
            if control.error or control.reproduced:
                finding.rejected_because = (
                    f"the control replay failed ({control.error})" if control.error else
                    f"the same trace violates the assertion on the {self._control_kind(fault)}, "
                    f"so the violation does not depend on this fault's patch")
                finding.rejection_kind = "control_error" if control.error else "fires_on_clean_build"
                finding.verification_steps = spent
                finding.wall_clock_s = round(time.monotonic() - started, 2)
                return finding
        else:
            finding.control = {"checked": False, "against": "none"}

        def reproduction(actions: list[dict]) -> tuple[bool, list[ReplayOutcome], int]:
            outcomes, used = [], 0
            for _attempt in range(fault.reproduction.replays):
                outcome = self._replay_once(fault, actions)
                used += outcome.steps_observed
                outcomes.append(outcome)
            return fault.reproduction.satisfied_by([o.reproduced for o in outcomes]), outcomes, used

        satisfied, outcomes, used = reproduction(trajectory.actions)
        spent += used
        for attempt, outcome in enumerate(outcomes, 1):
            logger.info("{} replay {}/{}: reproduced={} inconclusive={}", fault.fault_id,
                        attempt, len(outcomes), outcome.reproduced, outcome.inconclusive)
        finding.replays = [o.to_dict() for o in outcomes]

        if not satisfied:
            finding.rejected_because = (
                f"replay did not satisfy the reproduction rule "
                f"({sum(o.reproduced for o in outcomes)}/{len(outcomes)} reproduced, "
                f"rule requires {fault.reproduction.replays} "
                f"{'all' if fault.reproduction.must_all_reproduce else 'any'})")
            finding.rejection_kind = "not_reproduced"
            finding.verification_steps = spent
            finding.wall_clock_s = round(time.monotonic() - started, 2)
            return finding

        keep_patterns = tuple(fault.minimization_keep)

        def keep(action: dict) -> bool:
            target = f"{action.get('type', '')} {action.get('id', '')} {action.get('text', '')}"
            return any(pattern in target for pattern in keep_patterns)

        trial_steps = [0]

        def reproduces(candidate_actions: list[dict]) -> bool:
            outcome = self._replay_once(fault, candidate_actions)
            trial_steps[0] += outcome.steps_observed
            if not outcome.reproduced or control_runner is None:
                return outcome.reproduced
            # A shorter trace must still be a trace of *this fault*. Delta debugging will
            # happily shrink towards any sequence that trips the assertion, including one
            # that trips it without the fault's patch, so every accepted reduction is held
            # to the same definition the candidate was. A failed control is not a pass.
            control = self._replay_once(fault, candidate_actions, control_runner)
            trial_steps[0] += control.steps_observed
            return not control.reproduced and not control.error

        minimized = minimize_trace(
            trajectory.actions, reproduces,
            max_trials=self.max_minimization_trials, keep=keep)
        spent += trial_steps[0]
        # Each accepted reduction was checked with a single replay. The trace reported as
        # the reproduction must meet the full reproduction rule, like the original did;
        # if it does not, the original (which did) is reported instead.
        if minimized.trace != list(trajectory.actions):
            holds, _outcomes, used = reproduction(minimized.trace)
            spent += used
            if not holds:
                logger.warning("{}: minimized trace failed the reproduction rule; keeping "
                               "the original trace", fault.fault_id)
                minimized = MinimizationResult(
                    trace=list(trajectory.actions), original_length=minimized.original_length,
                    minimized_length=minimized.original_length, trials=minimized.trials,
                    exhausted_budget=minimized.exhausted_budget, kept_by_rule=minimized.kept_by_rule)
        finding.minimized_trace = minimized.trace
        finding.minimization = minimized.to_dict()

        finding.confirmed = True
        finding.verification_steps = spent
        finding.wall_clock_s = round(time.monotonic() - started, 2)
        logger.info("{} CONFIRMED: {} actions -> {} after {} trials",
                    fault.fault_id, minimized.original_length,
                    minimized.minimized_length, minimized.trials)
        return finding

    def _replay_once(self, fault: FaultSpec, actions: list[dict],
                     runner: TrajectoryRunner | None = None) -> ReplayOutcome:
        """One independent replay, asserted afresh. Never raises into the caller."""
        try:
            observed = (runner or self.runner).run(list(actions))
        except Exception as exc:  # noqa: BLE001 - a failed replay is data, not a crash
            logger.warning("replay of {} raised: {}: {}", fault.fault_id,
                           type(exc).__name__, exc)
            return ReplayOutcome(reproduced=False, inconclusive=True, steps_observed=0,
                                 error=f"{type(exc).__name__}: {exc}")
        verdict = verify_fault(fault, self.app, observed)
        return ReplayOutcome(
            reproduced=verdict.violated, inconclusive=verdict.inconclusive,
            steps_observed=len(observed.actions), verdict=verdict.to_dict(),
            misses=observed.replay_misses)


# -- the real runner -------------------------------------------------------------------------


class PlaywrightTrajectoryRunner:
    """Replays an action sequence against a live application and observes the result.

    Uses `ScriptedPolicy` -- the same matcher the repro-script tooling and the archive's
    route replay already use -- so "the same action" means one thing across the project.

    `env_factory` returns a **fresh** `WebFunctionalEnv` each call. Freshness is the point:
    a replay that reuses a browser context inherits cookies and history from the run that
    produced the candidate, and would confirm a fault that only reproduces given that
    state.
    """

    def __init__(self, env_factory: Callable[[], object], max_steps: int = 60) -> None:
        self.env_factory = env_factory
        self.max_steps = max_steps

    def run(self, actions: list[dict]) -> TrajectoryView:
        from ..evaluation import ScriptedPolicy
        from .replay import observed_record

        env = self.env_factory()
        records: list[dict] = []
        policy = ScriptedPolicy(list(actions), name="oracle-replay")
        try:
            observation, info = env.reset()
            policy.reset()
            records.append(observed_record(info))
            # The bound is on *iterations*, not on scripted steps. A step whose target has
            # not rendered yet is retried, and each retry is an iteration that executes a
            # no-op; bounding by `len(actions)` let one transient miss push the final
            # action off the end of the loop, so the trace that was asserted was missing
            # the very step that triggers the fault.
            budget = min(self.max_steps, max(len(actions), 1) * (policy.max_retries + 1))
            for _ in range(budget):
                if policy.finished:
                    break
                available = list(info.get("action_specs") or [])
                index = policy.act(observation, info)
                observation, _reward, terminated, truncated, info = env.step(index)
                records.append(observed_record(info, info.get("action_spec"), available))
                if terminated or truncated:
                    break
        finally:
            close = getattr(env, "close", None)
            if callable(close):
                close()
        view = build_trajectory(records)
        view.replay_misses = len(policy.misses) + (0 if policy.finished else 1)
        return view
