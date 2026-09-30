"""Watches an exploration for seeded-fault candidates and has the oracle verify them.

This is the piece that connects the oracle to a running agent. Until it existed the oracle
was reachable only from its own tests: no rollout asked it anything, so
`budget-to-first-verified-fault` could not be computed for any policy.

**What it does on every step.** The episode so far is assembled into the oracle's
`TrajectoryView`, and each seeded fault not yet confirmed is asserted against it. That is
a pure function over text already captured -- no browser, no model -- so it costs
microseconds and can run on every step of every run. Only when an assertion fires does the
expensive part happen: the oracle replays the trace in fresh sessions, applies the
clean-build control and the reproduction rule, and minimizes.

**Three distinct events, recorded separately**, because collapsing them hides exactly the
distinctions the evaluation is about:

* **reached** -- the page that carries the fault was observed and the assertion could be
  evaluated. The agent got to where the fault *would* show.
* **discovered** -- the assertion fired: a candidate.
* **verified** -- the candidate survived the control, the reproduction rule and
  minimization. Only this one feeds the primary metric.

**The agent is told nothing.** The monitor returns findings to the harness that called it.
No reward term reads them and no policy input contains them, which is what the
pre-registration means by "the RL reward never receives seeded fault labels".

**Verification cost is bounded, and the bound is reported.** A candidate rejected as a
trace artefact tends to recur: the pattern that tripped the assertion on a correct build
stays in the episode, so every later step would re-raise it. Re-verifying each one would
let a single spurious pattern consume more browser time than the exploration it
interrupts. Two rules prevent that:

1. within an episode, a fault is re-verified only when what the assertion *saw* changes --
   a different observed value, a different expectation, or different content on the page
   that carries the violation. Scrolling on a page that already failed verification does
   not buy another attempt; reaching that page again with different contents does.
2. a per-fault cap on rejected verifications across the run, set far above anything a
   sound fixture produces. It exists to stop a pathological loop, and reaching it is
   recorded as `cap_hit`, so a run censored with the cap exhausted is visibly
   distinguishable from one censored because the agent never found the fault.

Rule 1 keys on the violating page's content deliberately rather than on the evidence
strings alone. Two different action sequences can produce the same strings -- "entered 42,
page shows 1" is what the seeded fault looks like *and* what one spurious pattern looks
like -- and deduplicating on the strings would let the spurious one suppress the real one.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field

from ..envs.types import ActionSpec
from ..oracle import (
    ConfirmedFinding,
    FaultVerdict,
    VerificationPipeline,
    build_trajectory,
    observed_record,
    verify_fault,
)
from ..utils.logging import get_logger

logger = get_logger(__name__)

#: Rejected verifications allowed per fault per run before the monitor stops retrying.
DEFAULT_REJECTION_CAP = 200


def _evidence_of(verdict: FaultVerdict, trajectory) -> tuple:  # noqa: ANN001
    """What a verdict was based on, so a repeat of the same evidence is recognisable."""
    pages = []
    for result in verdict.results:
        html = trajectory.steps[result.step_index].html if result.step_index >= 0 else ""
        pages.append(hashlib.sha1(html.encode("utf-8", "replace")).hexdigest()[:16])  # noqa: S324
    return tuple((r.kind, r.observed, r.expected, page)
                 for r, page in zip(verdict.results, pages, strict=True))


@dataclass
class FaultRecord:
    """Everything the monitor learned about one seeded fault during one run."""

    fault_id: str
    #: Interaction count at which each event first happened; None when it never did.
    reached_at: int | None = None
    discovered_at: int | None = None
    verified_at: int | None = None
    candidates: int = 0
    rejections: Counter = field(default_factory=Counter)
    cap_hit: bool = False
    finding: ConfirmedFinding | None = None

    def to_dict(self) -> dict:
        return {
            "fault_id": self.fault_id,
            "reached_at": self.reached_at,
            "discovered_at": self.discovered_at,
            "verified_at": self.verified_at,
            "candidates": self.candidates,
            "rejections": dict(self.rejections),
            "cap_hit": self.cap_hit,
            "finding": self.finding.to_dict() if self.finding else None,
        }


class FaultMonitor:
    """Online seeded-fault detection and verification for one run on one application."""

    def __init__(self, pipeline: VerificationPipeline, *,
                 rejection_cap: int = DEFAULT_REJECTION_CAP, verify: bool = True) -> None:
        self.pipeline = pipeline
        self.rejection_cap = rejection_cap
        #: With `verify=False` candidates are counted but never replayed. Used to measure
        #: how often the raw assertions fire, which is a property of the oracle.
        self.verify = verify
        self.records: dict[str, FaultRecord] = {
            fault.fault_id: FaultRecord(fault.fault_id) for fault in pipeline.app.faults}
        self.verification_steps = 0
        self.verification_seconds = 0.0
        self._episode: list[dict] = []
        self._episode_evidence: dict[str, set[tuple]] = {}

    # -- episode bookkeeping ------------------------------------------------------------

    def start_episode(self, info: dict) -> None:
        """Begin an episode from the `info` that `env.reset()` returned."""
        self._episode = [observed_record(info)]
        self._episode_evidence = {}

    def observe(self, info: dict, executed: ActionSpec | None,
                available: list[ActionSpec] | None, budget_used: int) -> list[ConfirmedFinding]:
        """Record one step and return any fault confirmed as a result of it.

        `budget_used` is the run's interaction count *including* this step. It is what a
        confirmed finding is stamped with, so the primary metric is the budget consumed up
        to and including the action that made the violation observable.
        """
        self._episode.append(observed_record(info, executed, available))
        trajectory = build_trajectory(self._episode)
        confirmed: list[ConfirmedFinding] = []

        for fault in self.pipeline.app.faults:
            record = self.records[fault.fault_id]
            if record.verified_at is not None:
                continue
            verdict = verify_fault(fault, self.pipeline.app, trajectory)
            if record.reached_at is None and not verdict.inconclusive:
                record.reached_at = budget_used
            if not verdict.violated:
                continue

            record.candidates += 1
            if record.discovered_at is None:
                record.discovered_at = budget_used
            if not self.verify or record.cap_hit:
                continue
            evidence = _evidence_of(verdict, trajectory)
            seen = self._episode_evidence.setdefault(fault.fault_id, set())
            if evidence in seen:
                continue
            seen.add(evidence)

            finding = self.pipeline.confirm(fault, trajectory, budget_at_discovery=budget_used)
            self.verification_steps += finding.verification_steps
            self.verification_seconds += finding.wall_clock_s
            if finding.confirmed:
                record.verified_at = budget_used
                record.finding = finding
                confirmed.append(finding)
                logger.info("{} verified at budget {}", fault.fault_id, budget_used)
            else:
                record.rejections[finding.rejection_kind or "unknown"] += 1
                if sum(record.rejections.values()) >= self.rejection_cap:
                    record.cap_hit = True
                    logger.warning("{}: rejection cap ({}) reached; no further verification "
                                   "attempts this run", fault.fault_id, self.rejection_cap)
        return confirmed

    # -- results -------------------------------------------------------------------------

    @property
    def first_verified_at(self) -> int | None:
        """Budget consumed at the first confirmed fault, or None when there was none."""
        stamps = [r.verified_at for r in self.records.values() if r.verified_at is not None]
        return min(stamps) if stamps else None

    @property
    def verified_faults(self) -> list[str]:
        return sorted(fid for fid, r in self.records.items() if r.verified_at is not None)

    def summary(self) -> dict:
        records = list(self.records.values())
        rejections: Counter = Counter()
        for record in records:
            rejections.update(record.rejections)
        return {
            "seeded": len(records),
            "reached": sum(r.reached_at is not None for r in records),
            "discovered": sum(r.discovered_at is not None for r in records),
            "verified": sum(r.verified_at is not None for r in records),
            "first_verified_at": self.first_verified_at,
            "candidates": sum(r.candidates for r in records),
            "rejections": dict(rejections),
            "cap_hit": any(r.cap_hit for r in records),
            "verification_steps": self.verification_steps,
            "verification_seconds": round(self.verification_seconds, 2),
            "faults": [r.to_dict() for r in records],
        }
