"""The inferred-specification oracle, driven by the hunt harness like any other monitor.

    warm-up (no rules)  ->  LLM proposes rules from the pages seen so far
    every later step    ->  each rule checked; a violation is a candidate
    candidate           ->  replay twice in fresh sessions (the rule must be violated again)
                        ->  delta-debugging minimisation, every trial a real replay
                        ->  LLM adjudication of the minimal reproduction
                        ->  verified finding, or rejected with the judge's reason

Warm-up episodes are kept and checked as soon as the rules exist, so a bug met before the
rules were written is not lost. The judge sees only evidence the tester produced: the rule,
the observed values, the minimal steps and the page text at the violation.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from ..evaluation.scripted import ScriptedPolicy
from ..oracle import minimize_trace, observed_record
from ..utils.logging import get_logger
from . import llm
from .pages import snapshot, template_of, visible_text
from .rules import Rule, evaluate, parse_rules, violations

logger = get_logger(__name__)


@dataclass
class SpecFinding:
    rule_id: str
    kind: str
    description: str
    page: str
    first_seen_at: int
    details: dict = field(default_factory=dict)
    status: str = "candidate"   # candidate | verified | not_reproduced | rejected | unjudged
    replays: list[bool] = field(default_factory=list)
    original_trace: list[dict] = field(default_factory=list)
    minimized_trace: list[dict] = field(default_factory=list)
    minimization: dict = field(default_factory=dict)
    judgement: dict = field(default_factory=dict)
    verification_seconds: float = 0.0

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    def to_dict(self) -> dict:
        return {**asdict(self), "verified": self.verified}


class SpecReplayer:
    """Replays a trace in a fresh session and returns the records it observed."""

    def __init__(self, env_factory: Callable[[], object], max_steps: int = 120) -> None:
        self.env_factory = env_factory
        self.max_steps = max_steps

    def run(self, actions: list[dict]) -> list[dict]:
        env = self.env_factory()
        policy = ScriptedPolicy(list(actions), name="spec-replay")
        records: list[dict] = []
        try:
            observation, info = env.reset()
            policy.reset()
            records.append(observed_record(info))
            budget = min(self.max_steps, max(len(actions), 1) * (policy.max_retries + 1))
            for _ in range(budget):
                if policy.finished:
                    break
                observation, _r, terminated, truncated, info = env.step(policy.act(observation, info))
                records.append(observed_record(info, info.get("action_spec")))
                if terminated or truncated:
                    break
        finally:
            env.close()
        return records


def _describe(step: dict | None) -> str:
    if not step:
        return ""
    target = step.get("text") or step.get("id") or step.get("selector") or ""
    params = step.get("params") or {}
    value = params.get("value", params.get("option"))
    return f"{step.get('type', '?')} {target!s}" + (f" = {value!r}" if value is not None else "")


class SpecMonitor:
    def __init__(self, replayer: SpecReplayer, *, app_name: str, warmup: int = 150,
                 model: str = llm.DEFAULT_MODEL, replays: int = 2, max_minimization_trials: int = 15,
                 max_verifications: int = 12, max_verification_seconds: float | None = 1500.0,
                 judge: bool = True, on_event: Callable[[str, dict], None] | None = None) -> None:
        self.replayer = replayer
        #: Progress for a live run log (`kind`, `{"message": ...}`); silent when None.
        self.emit = on_event or (lambda kind, data: None)
        self.app_name = app_name
        self.warmup = warmup
        self.model = model
        self.replays = replays
        self.max_minimization_trials = max_minimization_trials
        self.max_verifications = max_verifications
        self.max_verification_seconds = max_verification_seconds
        self.judge = judge
        self.rules: list[Rule] | None = None
        self.dropped: list[str] = []
        self.rule_stats: dict[str, dict] = {}
        self.inference: dict = {}
        self.findings: dict[tuple[str, str], SpecFinding] = {}
        self.verification_seconds = 0.0
        self.verification_steps = 0
        self._episodes: list[list[dict]] = []
        self._episode: list[dict] = []
        self._attempted = 0

    # -- monitor interface ----------------------------------------------------------------------

    def start_episode(self, info: dict) -> None:
        if len(self._episode) > 1:
            self._episodes.append(self._episode)
            self._episodes = self._episodes[-60:]
        self._episode = [observed_record(info)]

    def observe(self, info: dict, executed, available, budget_used: int) -> list[SpecFinding]:  # noqa: ANN001
        self._episode.append(observed_record(info, executed, available))
        confirmed: list[SpecFinding] = []
        if self.rules is None:
            if budget_used < self.warmup:
                return confirmed
            self._infer()
            for episode in self._episodes + [self._episode[:-1]]:
                for rule in self.rules or []:
                    for i, outcome in violations(rule, episode)[:1]:
                        confirmed += self._candidate(rule, episode, i, outcome, budget_used)
        i = len(self._episode) - 1
        for rule in self.rules or []:
            outcome = evaluate(rule, self._episode, i)
            if outcome is not None:
                stats = self.rule_stats.setdefault(rule.id, {"applied": 0, "violated": 0})
                stats["applied"] += 1
                if not outcome.holds:
                    stats["violated"] += 1
                    confirmed += self._candidate(rule, self._episode, i, outcome, budget_used)
        return confirmed

    # -- inference ------------------------------------------------------------------------------

    def _infer(self) -> None:
        started = time.monotonic()
        pages: dict[str, dict] = {}
        for episode in self._episodes + [self._episode]:
            for rec in episode:
                if rec.get("html") and len(visible_text(rec["html"], 200)) > 40:
                    pages[template_of(rec["url"])] = rec
        snaps = [snapshot(rec["url"], rec["html"]) for rec in list(pages.values())[:14]]
        journeys = [[_describe(r.get("action")) for r in ep if r.get("action")][:25]
                    for ep in (self._episodes + [self._episode])[-6:]]
        try:
            raw = llm.propose_rules(self.app_name, snaps, journeys, model=self.model)
            self.rules, self.dropped = parse_rules(raw.get("rules", []))
        except llm.LLMError as exc:
            self.rules, self.dropped = [], [f"inference failed: {exc}"]
        self.inference = {"pages": len(snaps), "rules": len(self.rules), "dropped": len(self.dropped),
                          "seconds": round(time.monotonic() - started, 1), "model": self.model}
        logger.info("specification inferred: {} rules from {} pages ({} dropped)",
                    len(self.rules), len(snaps), len(self.dropped))
        self.emit("stage", {"message": f"Silent-bug finder: the LLM wrote {len(self.rules)} rules from "
                                       f"{len(snaps)} pages; hunting for violations"})

    # -- verification ---------------------------------------------------------------------------

    def _candidate(self, rule: Rule, episode: list[dict], i: int, outcome, budget_used: int) -> list[SpecFinding]:  # noqa: ANN001
        identity = (rule.id, template_of(episode[i]["url"]))
        if identity in self.findings:
            return []
        finding = SpecFinding(rule.id, rule.kind, rule.description, identity[1], budget_used,
                              details=outcome.details,
                              original_trace=[r["action"] for r in episode[: i + 1] if r.get("action")])
        self.findings[identity] = finding
        if self._attempted >= self.max_verifications or (
                self.max_verification_seconds is not None and self.verification_seconds >= self.max_verification_seconds):
            return []
        self._attempted += 1
        return [finding] if self._confirm(rule, finding) else []

    def _reproduces(self, rule: Rule, actions: list[dict]) -> tuple[bool, list[dict]]:
        records = self.replayer.run(actions)
        self.verification_steps += len(actions)
        return bool(violations(rule, records)), records

    def _confirm(self, rule: Rule, finding: SpecFinding) -> bool:
        started = time.monotonic()
        try:
            for _ in range(self.replays):
                ok, _records = self._reproduces(rule, finding.original_trace)
                finding.replays.append(ok)
            if not all(finding.replays):
                finding.status = "not_reproduced"
                return False
            minimized = minimize_trace(finding.original_trace, lambda t: self._reproduces(rule, t)[0],
                                       max_trials=self.max_minimization_trials)
            finding.minimized_trace = minimized.trace
            finding.minimization = minimized.to_dict()
            ok, records = self._reproduces(rule, finding.minimized_trace)
            hits = violations(rule, records)
            if hits:
                index, outcome = hits[0]
                finding.details = outcome.details
                page_text = visible_text(records[index]["html"], 3500)
            else:
                page_text = ""
            if not self.judge:
                finding.status = "unjudged"
                return False
            try:
                finding.judgement = llm.judge(rule.to_dict(), finding.details,
                                              [_describe(s) for s in finding.minimized_trace], page_text,
                                              model=self.model)
            except llm.LLMError as exc:
                finding.judgement = {"verdict": "unavailable", "explanation": str(exc)}
                finding.status = "unjudged"
                return False
            finding.status = "verified" if finding.judgement.get("verdict") == "bug" else "rejected"
            logger.info("spec finding {} on {}: {}", finding.rule_id, finding.page, finding.status)
            title = finding.judgement.get("title") or finding.description
            self.emit("stage" if finding.verified else "info",
                      {"message": (f"Silent bug verified: {title}" if finding.verified
                                   else f"Rule {finding.rule_id} violation not reported "
                                        f"({finding.judgement.get('verdict')})")})
            return finding.verified
        finally:
            finding.verification_seconds = round(time.monotonic() - started, 2)
            self.verification_seconds += finding.verification_seconds

    # -- results --------------------------------------------------------------------------------

    @property
    def first_verified_at(self) -> int | None:
        stamps = [f.first_seen_at for f in self.findings.values() if f.verified]
        return min(stamps) if stamps else None

    def summary(self) -> dict:
        findings = list(self.findings.values())
        return {
            "inference": self.inference,
            "rules": [{**r.to_dict(), **self.rule_stats.get(r.id, {"applied": 0, "violated": 0})} for r in self.rules or []],
            "dropped_rules": self.dropped,
            "candidates": len(findings),
            "verified": sum(f.verified for f in findings),
            "rejected": sum(f.status == "rejected" for f in findings),
            "not_reproduced": sum(f.status == "not_reproduced" for f in findings),
            "verification_seconds": round(self.verification_seconds, 1),
            "findings": [f.to_dict() for f in findings],
        }
