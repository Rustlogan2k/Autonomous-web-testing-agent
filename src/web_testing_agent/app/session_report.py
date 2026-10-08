"""A test session's result in the report shape the dashboard reads.

Runs started before the test-session path wrote `report.json` (`BugReport.to_dict()`).
Runs started through it (`tester.run_session`) write `session/session.json`,
`session/report.md` and `session/report.html` instead, and nothing at the top level, so
every page that read `report.json` lost them: the reports list, the findings list, the
dashboard's counts and the JSON download.

This module reads `session.json` and returns the same shape, built from the tester's own
`verified_findings` (one finding per root cause, as the session's Markdown and HTML report
it) and the reporting module's severity table, so a finding shown here is the one the
session report shows and nothing is re-derived.
"""

from __future__ import annotations

from dataclasses import fields

from ..reporting.report import _DEFAULT_TRIGGER_SEVERITY, _TRIGGER_SEVERITY
from ..tester.report import verified_findings
from ..tester.review import describe_action
from ..tester.session import SessionResult

#: A seeded fault confirmed by its control build is ground truth: it ranks above any trigger.
SEEDED_SEVERITY = 8.0


def _severity_label(value: float) -> str:
    return "high" if value >= 7.0 else "medium" if value >= 4.0 else "low"


def session_result(data: dict) -> SessionResult:
    """`SessionResult` from its serialised form; unknown keys are ignored."""
    names = {f.name for f in fields(SessionResult)}
    return SessionResult(**{k: v for k, v in data.items() if k in names})


def report_from_session(data: dict, *, generated_at: float | None = None) -> dict:
    result = session_result(data)
    bugs = []
    for item in verified_findings(result):
        seeded = item["key"].startswith("seeded_")
        kind = item.get("kind", "seeded_fault")
        severity = SEEDED_SEVERITY if seeded else _TRIGGER_SEVERITY.get(
            kind, _DEFAULT_TRIGGER_SEVERITY)
        bugs.append({
            "title": item["title"],
            "source": "seeded-fault oracle" if seeded else "deterministic",
            "severity": severity,
            "bug_type": kind,
            "url": item.get("where", ""),
            "repro": [describe_action(step) for step in item.get("reproduction") or [] if step],
            "evidence": item.get("detail", ""),
            "observed": item.get("detail", ""),
            "expected": "",
            "discrepancy": "",
            "confidence": 1.0,
            "grounded": True,
            "verified": True,
            "step": item.get("first_seen_at"),
            "times_seen": item.get("occurrences", 1),
        })
    candidates = [f for f in result.triggers.get("findings", []) if not f.get("verified")]
    observations = [r for r in result.reviews if r.get("concern") and r.get("grounded")]
    labels = [_severity_label(b["severity"]) for b in bugs]
    hunt = result.hunt or {}
    return {
        "format": "session",
        "target": result.target,
        "generated_at": generated_at,
        "counts": {
            "total": len(bugs),
            "deterministic": sum(b["source"] == "deterministic" for b in bugs),
            "seeded": sum(b["source"] != "deterministic" for b in bugs),
            "judge": 0,
            "high": labels.count("high"),
            "medium": labels.count("medium"),
            "low": labels.count("low"),
            "candidates": len(candidates),
            "observations": len(observations),
        },
        "run": {
            "agent": (result.agent or {}).get("agent", ""),
            "steps": hunt.get("steps"),
            "episodes": hunt.get("episodes"),
            "unique_states": hunt.get("unique_states"),
            "wall_clock_s": result.wall_clock_s,
        },
        "bugs": bugs,
        "observations": observations,
        "degradations": list(result.degradations),
    }
