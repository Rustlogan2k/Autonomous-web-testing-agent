"""Turns raw findings into a report a person can act on.

This is §3.5, cut to what the evidence already supports. Two producers feed it and they
are **not** the same kind of claim, so the report never merges them into an
undifferentiated list of "bugs":

* **Deterministic triggers** — a console error, an unexpected HTTP status, a page that
  never settled, a navigation that went nowhere. Mechanical, reproducible, and the
  baseline the judge has to beat. These are facts about what happened.
* **Judge verdicts** — a language model's reading of a window of interactions. These are
  *opinions with citations*, and the citation is checkable.

Collapsing the two would destroy the distinction this project has spent its whole
measurement effort defending. A reader has to be able to see which findings would have
been caught without a model at all, so `source` is a required field and the summary
counts the two separately.

**Every reported bug carries its repro path.** "The application has a broken flow" is not
a finding; "click Start order, select folders-25, click Continue, type 42 — the
confirmation reports quantity 1" is. The action sequence already exists in the trace, so
there is no reason to emit the first.

**Ungrounded verdicts are reported separately and never counted as findings.** A verdict
whose quoted evidence does not occur in what the model was shown is the failure mode this
project measured at 68% on one 7B model. Silently including them would put fabricated
citations in a document a human is meant to trust; silently dropping them would hide a
judge that is misbehaving. They get their own section, labelled.

Severity is CVSS-*inspired* and deliberately not called CVSS: a 0-10 rescaling of the
judge's own 0-1 severity, and a fixed per-trigger value for the deterministic ones. It is
an ordering, not a vulnerability score, and the report says so.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..judge.scoring import is_grounded
from ..utils.logging import get_logger

logger = get_logger(__name__)

# What a deterministic trigger is worth on the 0-10 scale. These are not measured
# severities — no ground truth assigns them — they are an ordering chosen so a failed
# document load outranks a console warning. Stated here so it can be argued with rather
# than reverse-engineered from the output.
_TRIGGER_SEVERITY: dict[str, float] = {
    "document_http_error": 7.0,
    "broken_navigation": 6.0,
    "http_error": 5.0,
    "js_error": 5.0,
    "console_error": 4.0,
    "slow_response": 3.0,
}
_DEFAULT_TRIGGER_SEVERITY = 4.0

# Plain-language titles for trigger names that are otherwise internal jargon.
_TRIGGER_TITLES: dict[str, str] = {
    "console_error": "JavaScript error logged to the console",
    "http_error": "Unexpected HTTP error response",
    "document_http_error": "Page itself failed to load",
    "slow_response": "Page never finished updating",
    "broken_navigation": "Link or submit did not navigate",
}

SOURCE_DETERMINISTIC = "deterministic"
SOURCE_JUDGE = "judge"

# Actions that are noise in a repro path. The window is the last WINDOW_STEPS
# interactions whatever they were, so an explorer that idled before doing the
# interesting thing produces "1. NO_OP 2. NO_OP 3. NO_OP 4. Click Generate report".
# A person following the steps gains nothing from the first three, and their presence
# makes the real step harder to find.
_REPRO_SKIP = frozenset({"NO_OP"})


def describe_action(action: dict) -> str:
    """One recorded action as an instruction a person can follow."""
    kind = str(action.get("type", "?"))
    target = action.get("element") or action.get("selector") or ""
    params = action.get("params") or {}
    value = params.get("value") or params.get("option") or params.get("preset") or ""

    target = " ".join(str(target).split())[:60]
    if kind == "TYPE" and value:
        return f"Type {value!r} into {target or 'the field'}"
    if kind == "SELECT" and value:
        return f"Select {value!r} in {target or 'the dropdown'}"
    if kind == "RESIZE_VIEWPORT" and value:
        return f"Resize the viewport to {value}"
    if kind == "SCROLL":
        return f"Scroll {params.get('direction', 'down')}"
    if kind in ("CLICK", "RAPID_CLICK"):
        prefix = "Rapidly click" if kind == "RAPID_CLICK" else "Click"
        return f"{prefix} {target or 'the control'}"
    return f"{kind}{f' on {target}' if target else ''}"


@dataclass
class ReportedBug:
    """One finding, with everything needed to act on it."""

    title: str
    source: str
    severity: float
    bug_type: str = "other"
    url: str = ""
    repro: list[str] = field(default_factory=list)
    evidence: str = ""
    observed: str = ""
    expected: str = ""
    discrepancy: str = ""
    confidence: float = 0.0
    grounded: bool = True
    episode: int = 0
    step: int = 0
    times_seen: int = 1

    @property
    def severity_label(self) -> str:
        if self.severity >= 7.0:
            return "high"
        return "medium" if self.severity >= 4.0 else "low"


@dataclass
class BugReport:
    """Every finding from one run, plus what the run was."""

    target: str = ""
    generated_at: str = ""
    bugs: list[ReportedBug] = field(default_factory=list)
    # Kept out of `bugs` on purpose: a verdict whose citation is not in the window is
    # evidence about the judge, not about the application.
    ungrounded: list[ReportedBug] = field(default_factory=list)
    run_meta: dict[str, Any] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "total": len(self.bugs),
            "deterministic": sum(1 for b in self.bugs if b.source == SOURCE_DETERMINISTIC),
            "judge": sum(1 for b in self.bugs if b.source == SOURCE_JUDGE),
            "high": sum(1 for b in self.bugs if b.severity_label == "high"),
            "medium": sum(1 for b in self.bugs if b.severity_label == "medium"),
            "low": sum(1 for b in self.bugs if b.severity_label == "low"),
            "ungrounded_excluded": len(self.ungrounded),
        }

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "generated_at": self.generated_at,
            "counts": self.counts,
            "run": self.run_meta,
            "bugs": [asdict(b) for b in self.bugs],
            "ungrounded_excluded": [asdict(b) for b in self.ungrounded],
        }


def _from_trigger(finding: dict) -> ReportedBug:
    trigger = str(finding.get("trigger", "unknown"))
    action = finding.get("action") or ""
    element = finding.get("element") or ""
    described = " ".join(part for part in (str(action), str(element)) if part).strip()
    return ReportedBug(
        title=_TRIGGER_TITLES.get(trigger, trigger.replace("_", " ").capitalize()),
        source=SOURCE_DETERMINISTIC,
        severity=_TRIGGER_SEVERITY.get(trigger, _DEFAULT_TRIGGER_SEVERITY),
        bug_type=trigger,
        url=str(finding.get("url", "")),
        # A deterministic trigger records the single action that fired it rather than a
        # window, so the repro is that one step — stated as the one step it is rather
        # than padded out to look like a sequence.
        repro=[describe_action({"type": action, "element": element})] if described else [],
        evidence=str(finding.get("detail", "")),
        observed=str(finding.get("detail", "")),
        step=int(finding.get("first_seen_step", 0) or 0),
        times_seen=int(finding.get("times_seen", 1) or 1),
    )


def _from_verdict(verdict: dict) -> ReportedBug:
    evidence = str(verdict.get("evidence", ""))
    window_text = str(verdict.get("window_text", ""))
    # When the window was not retained the citation cannot be checked, and "unchecked"
    # must never silently read as "checked and passed".
    grounded = is_grounded(evidence, window_text) if window_text else False
    return ReportedBug(
        title=str(verdict.get("discrepancy") or verdict.get("bug_type", "other")).strip()[:160],
        source=SOURCE_JUDGE,
        severity=round(float(verdict.get("severity", 0.0) or 0.0) * 10.0, 1),
        bug_type=str(verdict.get("bug_type", "other")),
        url=str(verdict.get("url", "")),
        repro=[
            describe_action(a)
            for a in (verdict.get("repro") or [])
            if str(a.get("type", "")) not in _REPRO_SKIP
        ],
        evidence=evidence,
        observed=str(verdict.get("observed", "")),
        expected=str(verdict.get("expected", "")),
        discrepancy=str(verdict.get("discrepancy", "")),
        confidence=float(verdict.get("confidence", 0.0) or 0.0),
        grounded=grounded,
        episode=int(verdict.get("episode", 0) or 0),
        step=int(verdict.get("step", 0) or 0),
    )


def build_report(
    *,
    target: str,
    findings: list[dict] | None = None,
    verdicts: list[dict] | None = None,
    run_meta: dict | None = None,
    min_confidence: float = 0.0,
) -> BugReport:
    """Assemble one report from deterministic findings and judge verdicts.

    `min_confidence` mirrors the live reward path's threshold. A verdict the reward
    refused to pay for should not appear in the report as an established finding either,
    or the document and the signal that drove the run disagree about what was found.
    """
    report = BugReport(
        target=target,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        run_meta=dict(run_meta or {}),
    )

    for finding in findings or []:
        report.bugs.append(_from_trigger(finding))

    # Judge verdicts are deduplicated on the same principle the rollout already applies
    # to trigger hits: one broken element revisited on five steps is one defect, and
    # listing it five times makes the document worse, not more thorough. Measured on the
    # toy site: 17 raw positives collapse to 9 distinct findings, with the seeded 404
    # alone accounting for four of the duplicates.
    #
    # Keyed on (bug_type, evidence) rather than anything coarser. The evidence line is
    # what the verdict is anchored to, so two verdicts citing the same line are about the
    # same observation; two citing different lines may not be, and merging those would
    # discard a real finding to tidy the output.
    seen: dict[tuple[str, str], ReportedBug] = {}
    for verdict in verdicts or []:
        if not verdict.get("is_bug"):
            continue
        if float(verdict.get("confidence", 0.0) or 0.0) < min_confidence:
            continue
        bug = _from_verdict(verdict)
        key = (bug.bug_type, " ".join(bug.evidence.split()))
        existing = seen.get(key)
        if existing is not None:
            existing.times_seen += 1
            # Keep the most confident phrasing, and with it the repro path that earned it.
            if bug.confidence > existing.confidence:
                existing.confidence = bug.confidence
                existing.title, existing.repro = bug.title, bug.repro
                existing.observed, existing.expected = bug.observed, bug.expected
                existing.discrepancy = bug.discrepancy
            continue
        seen[key] = bug
        (report.bugs if bug.grounded else report.ungrounded).append(bug)

    # Most severe first, and within a severity the more confident one. A reader works
    # top-down and stops when they run out of time, so ordering is part of the output.
    report.bugs.sort(key=lambda b: (-b.severity, -b.confidence, b.step))
    report.ungrounded.sort(key=lambda b: (-b.severity, b.step))
    logger.info(
        "report: {} findings ({} deterministic, {} judge), {} ungrounded excluded",
        len(report.bugs), report.counts["deterministic"], report.counts["judge"],
        len(report.ungrounded),
    )
    return report


def _render_bug(bug: ReportedBug, index: int) -> list[str]:
    provenance = (
        "deterministic trigger (no language model involved)"
        if bug.source == SOURCE_DETERMINISTIC
        else f"LLM judge, {bug.confidence:.0%} confident, citation verified against the window"
    )
    lines = [
        f"### {index}. {bug.title}",
        "",
        f"**Severity** {bug.severity:.1f}/10 ({bug.severity_label}) · "
        f"**Type** `{bug.bug_type}` · **Found by** {provenance}",
    ]
    if bug.url:
        lines.append(f"**URL** {bug.url}")
    if bug.times_seen > 1:
        lines.append(f"**Occurrences** {bug.times_seen}")
    lines.append("")

    if bug.repro:
        lines.extend(["**Steps to reproduce**", ""])
        lines.extend(f"{n}. {step}" for n, step in enumerate(bug.repro, start=1))
        lines.append("")

    for label, value in (
        ("What should happen", bug.expected),
        ("What happened", bug.observed),
        ("Why that is wrong", bug.discrepancy),
    ):
        if value:
            lines.extend([f"**{label}** — {value}", ""])

    if bug.evidence:
        lines.extend(["**Evidence**", "", "```", bug.evidence.strip(), "```", ""])
    return lines


def render_markdown(report: BugReport) -> str:
    """The human-facing document."""
    counts = report.counts
    lines = [
        f"# Bug report — {report.target}",
        "",
        f"Generated {report.generated_at}",
        "",
        f"**{counts['total']} findings** — {counts['high']} high, {counts['medium']} medium, "
        f"{counts['low']} low.",
        "",
        f"{counts['deterministic']} were found by deterministic triggers and would have been "
        f"caught without a language model. {counts['judge']} required semantic judgment.",
        "",
    ]
    if counts["ungrounded_excluded"]:
        lines.extend([
            f"> {counts['ungrounded_excluded']} further verdict(s) were **excluded**: the "
            f"evidence they quoted does not occur in what the judge was shown. They are "
            f"listed at the end as evidence about the judge, not about the application.",
            "",
        ])
    lines.extend([
        "> Severity is a CVSS-*inspired* 0-10 ordering, not a CVSS score. Judge severities "
        "are the model's own 0-1 rating rescaled; deterministic ones are fixed per trigger.",
        "",
        "---",
        "",
    ])

    if not report.bugs:
        lines.extend(["No findings.", ""])
    for index, bug in enumerate(report.bugs, start=1):
        lines.extend(_render_bug(bug, index))

    if report.ungrounded:
        lines.extend([
            "---",
            "",
            "## Excluded: verdicts whose evidence was not in the window",
            "",
            "These are **not** findings about the application. Each one quotes something the "
            "judge was never shown, which makes the verdict unreliable regardless of whether "
            "it happens to be right.",
            "",
        ])
        for index, bug in enumerate(report.ungrounded, start=1):
            lines.extend(_render_bug(bug, index))

    if report.run_meta:
        lines.extend([
            "---", "", "## Run", "", "```json",
            json.dumps(report.run_meta, indent=2, default=str), "```", "",
        ])
    return "\n".join(lines)
