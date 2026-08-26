"""The offline judge, wired into the live reward loop.

Closes §9 item 1e. Everything load-bearing was already built and measured offline —
window rendering, the verdict schema, the backends, and the scoring that produced
10/10 against a 3/10 deterministic ceiling. This module adds only what live use needs
and deliberately nothing else:

* a rolling window of the last K transitions, cleared per episode (`start_episode`),
* a gate (`reward.gating`) so a judge call is paid for only where it can inform,
* a translation from `Verdict` to `RewardSignal`.

**The window is rendered by exactly the same code as the offline corpus.** Live steps
are assembled into the same `StepRecord` type and passed through the same
`JudgeWindow.render()`. That is the point: every judge number this project reports was
measured on offline windows, and a live path that rendered its own variant would make
those numbers describe a component that is no longer in use — without failing anything.

**Latency is the binding constraint and it is not solved here.** Measured: ~7-9 s per
window hosted, 11-18 s locally. With gating that is roughly 6-9 minutes per 40-step
episode on Gitea rather than 12-16, which makes evaluation rollouts practical and
still leaves 30-50K-step *training* out of reach by two orders of magnitude. Treat this
as the evaluation and bug-finding path; training against a live judge needs either a
much faster judge or off-policy relabelling of stored transitions, and neither exists
yet.

**A failed judge call is never a finding.** An outage, a timeout or an unparseable
response returns the neutral signal, because a reward model that pays out when it
cannot see is strictly worse than one that is switched off.
"""

from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING

from ..annotation.trace import StepRecord, relative_path
from ..envs.types import ActionSpec, RawObservation
from ..judge.window import WINDOW_STEPS, JudgeWindow
from ..utils.logging import get_logger
from .base import FunctionalRewardModel, RewardSignal
from .gating import GateStats, should_judge

if TYPE_CHECKING:
    from ..envs.base_env import StepContext
    from ..intake.profile import ApplicationProfile
    from ..judge.client import Judge

logger = get_logger(__name__)


class JudgeRewardModel(FunctionalRewardModel):
    """Scores transitions with an LLM judge over a rolling window.

    `max_calls_per_episode` is a hard budget rather than advice. Without it a single
    pathological episode — one that changes state on every step — costs unbounded wall
    clock, and the failure shows up as "the run hung" rather than as a number.
    """

    def __init__(
        self,
        judge: "Judge",
        *,
        profile: "ApplicationProfile | None" = None,
        window_steps: int = WINDOW_STEPS,
        gated: bool = True,
        max_calls_per_episode: int = 0,
        min_confidence: float = 0.0,
    ) -> None:
        self.judge = judge
        self.profile = profile
        self.window_steps = window_steps
        self.gated = gated
        self.max_calls_per_episode = max_calls_per_episode
        # Verdicts below this confidence are recorded but do not move the reward. The
        # default keeps every verdict, so raising it is a deliberate, reportable choice
        # rather than a silent filter tuned until the numbers look better.
        self.min_confidence = min_confidence

        self._records: deque[StepRecord] = deque(maxlen=window_steps)
        self._episode = 0
        self._step = 0
        self._calls_this_episode = 0
        self.gate_stats = GateStats()
        self.calls = 0
        self.failed_calls = 0
        self.judge_seconds = 0.0
        self.verdicts: list[dict] = []

    # -- FunctionalRewardModel -------------------------------------------------

    def start_episode(self) -> None:
        self._episode += 1
        self._step = 0
        self._calls_this_episode = 0
        self._records.clear()

    def score(
        self,
        before: RawObservation,
        action: ActionSpec,
        after: RawObservation,
        *,
        context: "StepContext | None" = None,
    ) -> RewardSignal:
        if context is None:
            # Rendering a window without the execution metadata would produce a view the
            # judge was never measured against — most damagingly, a harness-refused
            # navigation would be indistinguishable from a broken link. Refuse rather
            # than silently judge something else.
            raise ValueError(
                "JudgeRewardModel needs the StepContext to render a faithful window. "
                "WebFunctionalEnv passes it; a caller invoking score() directly must too."
            )

        self._step += 1
        record = self._build_record(before, action, after, context)
        self._records.append(record)

        decision = should_judge(
            action.action_type,
            exec_success=bool(context.exec_info.get("success", False)),
            blocked=bool(context.exec_info.get("left_application")),
            state_changed=record.state_changed,
            triggered=record.triggered,
            settled=context.settled,
        )
        if self.gated:
            self.gate_stats.record(decision)
            if not decision.judge:
                return RewardSignal(True, 0.0, "", {"judge_skipped": decision.reason})
            if self.max_calls_per_episode and self._calls_this_episode >= self.max_calls_per_episode:
                return RewardSignal(True, 0.0, "", {"judge_skipped": "episode call budget spent"})

        window = JudgeWindow(
            records=list(self._records),
            episode=self._episode,
            focus_global_step=self._step,
        )
        rendered = window.render()
        profile_text = self._profile_text(window)

        started = time.monotonic()
        verdict = self.judge.judge(rendered, profile_text)
        elapsed = time.monotonic() - started
        self.judge_seconds += elapsed
        self.calls += 1
        self._calls_this_episode += 1

        if not verdict.ok:
            # Never a finding. An outage that read as a bug would pay the agent for
            # breaking the judge, which is a reward exploit with no code path to fix.
            self.failed_calls += 1
            logger.warning("Judge call failed at episode {} step {}: {}", self._episode, self._step, verdict.error)
            return RewardSignal(True, 0.0, "", {"judge_error": verdict.error})

        self.verdicts.append(
            {
                # The model's own fields go first and the harness's authoritative ones
                # overwrite them, never the other way round. `Verdict` carries its own
                # `step` -- the index *within the window*, 1..WINDOW_STEPS, which is what
                # the prompt asks the model to name -- and spreading it last silently
                # replaced the episode step with it. Every verdict logged before
                # 2026-08-26 therefore reports a number between 1 and 6 as its location,
                # which is why eight findings in one run all claimed to be at "step 6".
                # A finding whose location is wrong is not reproducible.
                **verdict.to_dict(),
                "episode": self._episode,
                "step": self._step,
                "window_step": verdict.step,
                "seconds": round(elapsed, 2),
                # The actions that led here, oldest first. A verdict without them names
                # a defect nobody can reproduce, and the window the judge was shown is
                # exactly the sequence a reader would have to repeat. Kept as the raw
                # recorded action dicts rather than prose so the report engine can both
                # render them for a human and replay them as a script.
                "repro": [dict(record.action) for record in window.records],
                # Retained so `is_grounded` can be checked against what the model was
                # actually shown. Re-rendering the window later is not equivalent — the
                # renderer changes, and a citation must be checked against the text that
                # produced it, not against today's version of it.
                "window_text": rendered,
            }
        )
        counted = verdict.is_bug and verdict.confidence >= self.min_confidence
        if verdict.is_bug:
            logger.info(
                "[episode {} step {}] judge: {} ({:.0%} confident, severity {:.2f}){} — {}",
                self._episode, self._step, verdict.bug_type, verdict.confidence,
                verdict.severity, "" if counted else " [below min_confidence, not rewarded]",
                verdict.evidence[:100],
            )
        return RewardSignal(
            is_expected=not counted,
            severity=verdict.severity if counted else 0.0,
            explanation=verdict.discrepancy if counted else "",
            detail={
                "judge_is_bug": verdict.is_bug,
                "judge_bug_type": verdict.bug_type,
                "judge_confidence": round(verdict.confidence, 3),
                "judge_seconds": round(elapsed, 2),
            },
        )

    # -- internals -------------------------------------------------------------

    def _profile_text(self, window: JudgeWindow) -> str:
        if self.profile is None:
            return ""
        from ..intake.profile import window_paths  # local: keeps intake off the env's import path

        return self.profile.render(window_paths(window))

    def _build_record(
        self,
        before: RawObservation,
        action: ActionSpec,
        after: RawObservation,
        context: "StepContext",
    ) -> StepRecord:
        """Assemble the same record type `TraceRecorder` writes, held in memory.

        Field-for-field equivalent to `TraceRecorder.on_step`'s output, because
        `judge.window` renders from these fields and any divergence would make the live
        window differ from the corpus every judge measurement was made on.
        """
        return StepRecord(
            episode=self._episode,
            step=self._step,
            global_step=self._step,
            action={
                "type": action.action_type.value,
                "selector": action.selector,
                "element": action.element_id,
                "description": action.description,
                "params": {
                    k: v for k, v in (action.params or {}).items()
                    if isinstance(v, (str, int, float, bool, type(None)))
                },
            },
            before=self._page_side(before),
            after=self._page_side(after),
            exec={
                k: v for k, v in context.exec_info.items()
                if isinstance(v, (str, int, float, bool, type(None)))
            },
            settled=bool(context.settled),
            opened_new_page=bool(context.opened_new_page),
            load_duration_s=round(float(context.load_duration_s), 3),
            changed={
                # Identity of the documents themselves, never of a rendered summary.
                # Conflating the two is what previously had the window assert
                # byte-identity on pages that had in fact changed, manufacturing 21
                # false dead-control verdicts out of 39 TYPE windows.
                "url": before.url != after.url,
                "html": before.html != after.html,
            },
            _inline_html={"before": before.html, "after": after.html},
        )

    @staticmethod
    def _page_side(obs: RawObservation) -> dict:
        return {
            "url": obs.url,
            "path": relative_path(obs.url),
            # The hash slot is unused live; `_inline_html` carries the body instead.
            "html": "",
            "console_errors": list(obs.console_errors or []),
            "page_errors": list(obs.page_errors or []),
            "network": [
                {
                    "url": relative_path(event.url),
                    "method": event.method,
                    "status": event.response_status,
                    "resource_type": event.resource_type,
                    "failed": bool(event.failed),
                    "failure_text": event.failure_text,
                    "duration_ms": event.duration_ms,
                    "body_snippet": (event.response_body_snippet or "")[:500],
                }
                for event in (obs.network_events or [])
            ],
        }

    # -- reporting -------------------------------------------------------------

    def usage_summary(self) -> dict:
        return {
            "judge": getattr(self.judge, "name", "?"),
            "grounded": self.profile is not None,
            "profile": None if self.profile is None else
                       {"name": self.profile.name, "provenance": self.profile.provenance},
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            # Surfaced so the bug report can apply the same floor the reward did. A
            # document that lists a finding the run itself declined to pay for would
            # disagree with the signal that produced it.
            "min_confidence": self.min_confidence,
            "judge_seconds": round(self.judge_seconds, 1),
            "seconds_per_call": round(self.judge_seconds / self.calls, 2) if self.calls else 0.0,
            "gate": self.gate_stats.to_dict() if self.gated else {"enabled": False},
            "positives": sum(1 for v in self.verdicts if v.get("is_bug")),
        }
