"""Verification for findings on an application that has no answer key.

On the benchmark, a seeded fault's assertion decides what counts as a finding. On an
application a user uploads there is no such assertion; what exists is the environment's
deterministic triggers -- an uncaught script error, a failed request, a navigation control
that goes nowhere, a page that never stops changing. Those fire during exploration and, like
a seeded-fault candidate, a single firing is not yet a finding: the page may have raced, the
harness may have interfered, a third-party script may have logged something once.

So each distinct trigger goes through the same discipline the seeded-fault oracle applies:

    candidate  ->  replay in a fresh session, twice  ->  both reproduce?
               ->  delta-debugging minimization, every trial a real replay
               ->  verified, with a minimized reproduction and evidence captured

A trigger that does not reproduce is kept in the report as an *unverified candidate*, never
silently dropped and never counted as a verified finding.

**Identity.** A finding is `(trigger kind, page path, detail)`, where the detail is the
normalised error message, the failing request, or the control's label. Two firings with the
same identity are one finding. Ports, numbers and query strings are removed from the
identity, because a finding that differs only in a request id is the same defect.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from ..envs.types import ActionSpec, BugSignals
from ..evaluation.scripted import ScriptedPolicy
from ..oracle import minimize_trace, observed_record
from ..utils.logging import get_logger

logger = get_logger(__name__)

#: Kinds whose firing depends on wall-clock timing. They are reported, but a replay that
#: fails to reproduce them says little either way.
TIMING_KINDS = frozenset({"slow_response"})

_VOLATILE = re.compile(r"(:\d{2,5})|(\b\d+\b)|(\?[^\s'\"]*)")


def _path(url: str) -> str:
    parts = urlsplit(url or "")
    return parts.path or "/"


def _normalise(text: str) -> str:
    """Volatile parts removed and whitespace collapsed, so one defect has one identity."""
    return " ".join(_VOLATILE.sub("", text or "").split())[:160]


def signatures(info: dict) -> list[tuple[str, str, str]]:
    """Every distinct trigger identity this step's outcome carries."""
    signals: BugSignals | None = info.get("bug_signals")
    if signals is None:
        return []
    page = _path((info.get("page") or {}).get("url", ""))
    spec: ActionSpec | None = info.get("action_spec")
    element = (spec.element_id or spec.description) if spec is not None else ""
    found: list[tuple[str, str, str]] = []
    for message in signals.console_errors[:3]:
        found.append(("console_error", page, _normalise(message)))
    for event in signals.unexpected_http_errors[:3]:
        kind = "document_http_error" if event.is_document else "http_error"
        found.append((kind, page, f"{event.response_status or 'failed'} {_path(event.url)}"))
    if signals.broken_navigation:
        found.append(("broken_navigation", page, _normalise(element)))
    if signals.slow_response:
        found.append(("slow_response", page, _normalise(element)))
    return list(dict.fromkeys(found))


@dataclass
class TriggerFinding:
    kind: str
    page: str
    detail: str
    first_seen_at: int
    occurrences: int = 1
    verified: bool = False
    status: str = "candidate"          # candidate | verified | not_reproduced | timing
    replays: list[bool] = field(default_factory=list)
    original_trace: list[dict] = field(default_factory=list)
    minimized_trace: list[dict] = field(default_factory=list)
    minimization: dict = field(default_factory=dict)
    verification_seconds: float = 0.0
    evidence: dict = field(default_factory=dict)

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.kind, self.page, self.detail)

    def to_dict(self) -> dict:
        return asdict(self)


class TriggerReplayer:
    """Replays a trace in a fresh session and reports which trigger identities fired."""

    def __init__(self, env_factory: Callable[[], object], max_steps: int = 120) -> None:
        self.env_factory = env_factory
        self.max_steps = max_steps

    def run(self, actions: list[dict], *, capture: bool = False) -> dict:
        env = self.env_factory()
        policy = ScriptedPolicy(list(actions), name="trigger-replay")
        fired: set[tuple[str, str, str]] = set()
        final: dict = {}
        try:
            observation, info = env.reset()
            policy.reset()
            budget = min(self.max_steps, max(len(actions), 1) * (policy.max_retries + 1))
            for _ in range(budget):
                if policy.finished:
                    break
                observation, _r, terminated, truncated, info = env.step(policy.act(observation, info))
                fired.update(signatures(info))
                if terminated or truncated:
                    break
            if capture:
                final = _capture(env, info)
        finally:
            env.close()
        return {"fired": fired, "misses": len(policy.misses), "final": final}


def _capture(env, info: dict) -> dict:  # noqa: ANN001
    """Screenshot and page of the replay's final state, as evidence."""
    import io

    from PIL import Image

    page = info.get("page") or {}
    out = {"url": page.get("url", ""), "html": page.get("html", ""),
           "visible_text": page.get("visible_text", "")}
    raw = getattr(env, "_last_raw_obs", None)
    if raw is not None and getattr(raw, "screenshot", None) is not None:
        buffer = io.BytesIO()
        Image.fromarray(raw.screenshot).save(buffer, format="PNG", optimize=True)
        out["screenshot_png"] = buffer.getvalue()
    return out


class TriggerMonitor:
    """Collects trigger candidates during a run and verifies each distinct one once.

    Same interface as `benchmark.FaultMonitor`, so the hunt harness drives either.
    """

    def __init__(self, replayer: TriggerReplayer, *, replays: int = 2,
                 max_minimization_trials: int = 20, verify: bool = True,
                 max_verifications: int = 25) -> None:
        self.replayer = replayer
        self.replays = replays
        self.max_minimization_trials = max_minimization_trials
        self.verify = verify
        self.max_verifications = max_verifications
        self.findings: dict[tuple[str, str, str], TriggerFinding] = {}
        self.verification_seconds = 0.0
        self.verification_steps = 0
        self._episode: list[dict] = []

    def start_episode(self, info: dict) -> None:
        self._episode = [observed_record(info)]

    def observe(self, info: dict, executed: ActionSpec | None, available: list[ActionSpec] | None,
                budget_used: int) -> list[TriggerFinding]:
        self._episode.append(observed_record(info, executed, available))
        verified: list[TriggerFinding] = []
        for identity in signatures(info):
            finding = self.findings.get(identity)
            if finding is not None:
                finding.occurrences += 1
                continue
            finding = TriggerFinding(*identity, first_seen_at=budget_used,
                                     original_trace=[r["action"] for r in self._episode
                                                     if r.get("action")])
            self.findings[identity] = finding
            if not self.verify:
                continue
            if sum(1 for f in self.findings.values() if f.replays) >= self.max_verifications:
                logger.warning("verification budget exhausted; {} left as a candidate", identity)
                continue
            if self._confirm(finding):
                verified.append(finding)
        return verified

    def _confirm(self, finding: TriggerFinding) -> bool:
        started = time.monotonic()
        identity = finding.identity
        trace = finding.original_trace
        for _ in range(self.replays):
            outcome = self.replayer.run(trace)
            self.verification_steps += len(trace)
            finding.replays.append(identity in outcome["fired"])
        if not all(finding.replays):
            finding.status = "timing" if finding.kind in TIMING_KINDS else "not_reproduced"
            finding.verification_seconds = round(time.monotonic() - started, 2)
            self.verification_seconds += finding.verification_seconds
            return False

        def reproduces(candidate: list[dict]) -> bool:
            self.verification_steps += len(candidate)
            return identity in self.replayer.run(candidate)["fired"]

        minimized = minimize_trace(trace, reproduces, max_trials=self.max_minimization_trials)
        finding.minimized_trace = minimized.trace
        finding.minimization = minimized.to_dict()
        finding.verified = True
        finding.status = "verified"
        finding.verification_seconds = round(time.monotonic() - started, 2)
        self.verification_seconds += finding.verification_seconds
        logger.info("trigger finding verified: {} ({} -> {} actions)", identity,
                    minimized.original_length, minimized.minimized_length)
        return True

    @property
    def first_verified_at(self) -> int | None:
        stamps = [f.first_seen_at for f in self.findings.values() if f.verified]
        return min(stamps) if stamps else None

    def summary(self) -> dict:
        findings = list(self.findings.values())
        return {
            "candidates": len(findings),
            "verified": sum(f.verified for f in findings),
            "not_reproduced": sum(f.status == "not_reproduced" for f in findings),
            "timing_dependent": sum(f.status == "timing" for f in findings),
            "first_verified_at": self.first_verified_at,
            "verification_seconds": round(self.verification_seconds, 2),
            "findings": [f.to_dict() for f in findings],
        }


class CompositeMonitor:
    """Several monitors driven as one by the hunt harness."""

    def __init__(self, **monitors) -> None:  # noqa: ANN003
        self.monitors = {name: m for name, m in monitors.items() if m is not None}

    def start_episode(self, info: dict) -> None:
        for monitor in self.monitors.values():
            monitor.start_episode(info)

    def observe(self, info, executed, available, budget_used):  # noqa: ANN001, ANN201
        confirmed = []
        for monitor in self.monitors.values():
            confirmed.extend(monitor.observe(info, executed, available, budget_used))
        return confirmed

    @property
    def verification_seconds(self) -> float:
        return sum(m.verification_seconds for m in self.monitors.values())

    def summary(self) -> dict:
        return {name: monitor.summary() for name, monitor in self.monitors.items()}
