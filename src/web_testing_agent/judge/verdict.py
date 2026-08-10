"""The judge's output contract.

Deliberately narrow. The judge is asked for a decision plus the evidence it used,
and nothing else: free-form commentary cannot be scored against an answer key, and a
verdict without a citation cannot be audited when it turns out to be wrong.

`evidence` is load-bearing rather than decorative. The failure mode that matters most
here is not a missed bug — it is a *confidently reported* bug that never happened,
because a reward model that pays for hallucinated findings is worse than no reward
model at all. Requiring the judge to name the step and quote what changed makes that
failure visible instead of silent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Categories mirror the `type` field of answer_key.json so verdicts can be scored
# against ground truth without a translation layer. `other` exists so an unanticipated
# real bug is still reportable rather than being forced into a wrong category.
BUG_TYPES = (
    "broken_flow",
    "dead_control",
    "broken_navigation",
    "js_error",
    "validation_bypass",
    "race_condition",
    "hang",
    "ui_regression",
    "state_persistence",
    "other",
)

# Passed to `output_config={"format": ...}` (Anthropic) and `format=...` (Ollama), both
# of which constrain decoding to conforming JSON.
#
# **Property order is the contract, not a style choice.** Constrained decoding emits
# fields in the order declared here, so whatever comes first is decided first. An
# earlier version put `is_bug` first and measured the consequence: llama3:8b filled
# `actual` with "NO OBSERVABLE CHANGE — the page is byte-identical after this action",
# typed it `dead_control`, and still returned is_bug=false on all 39 windows bar one.
# The boolean was committed before any analysis existed to inform it, and the analysis
# fields degenerated into narration of a verdict already fixed.
#
# So the observation, the standard it is judged against, and the comparison between
# them are all generated *before* the verdict. `is_bug` comes last, when everything
# needed to answer it has already been written down.
VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "observed": {
            "type": "string",
            "description": "What the application actually did at the final step. Quote the concrete change, or state that nothing changed.",
        },
        "expected": {
            "type": "string",
            "description": "What a CORRECT application should have done in response to that same action. Do not repeat the observation here — describe correct behaviour.",
        },
        "discrepancy": {
            "type": "string",
            "description": "Compare the two. State plainly whether what was observed contradicts what a correct application should have done, and why. Write 'none' if they agree.",
        },
        "evidence": {
            "type": "string",
            "description": "The exact line from the window that supports this: a URL, a quoted attribute, an error message, or the no-change statement. Must appear verbatim in the window.",
        },
        "bug_type": {"type": "string", "enum": list(BUG_TYPES)},
        "severity": {"type": "number", "description": "0.0 to 1.0. Ignored when is_bug is false."},
        "confidence": {"type": "number", "description": "0.0 to 1.0."},
        "step": {
            "type": "integer",
            "description": "The step number in the window where the incorrect behaviour is observable.",
        },
        "is_bug": {
            "type": "boolean",
            "description": "Your verdict, decided from the discrepancy you just described. True if and only if that discrepancy is a real defect in the application.",
        },
    },
    "required": [
        "observed", "expected", "discrepancy", "evidence",
        "bug_type", "severity", "confidence", "step", "is_bug",
    ],
    "additionalProperties": False,
}


@dataclass(slots=True)
class Verdict:
    """One judgment over one window."""

    is_bug: bool
    bug_type: str = "other"
    severity: float = 0.0
    confidence: float = 0.0
    step: int = 0
    observed: str = ""
    expected: str = ""
    discrepancy: str = ""
    evidence: str = ""
    # Set when the judge could not be reached or its output could not be parsed. A
    # failed call must never masquerade as "no bug found" — that would silently
    # deflate recall and make an outage look like a negative result.
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def failed(cls, reason: str) -> "Verdict":
        return cls(is_bug=False, error=reason)

    @classmethod
    def parse(cls, payload: str) -> "Verdict":
        """Build a verdict from the judge's raw text output.

        Tolerates a model that wraps its JSON in prose or a fenced block even though
        the schema forbids it, because a local model without structured-output support
        has to run through this same path.
        """
        try:
            data = json.loads(_extract_json(payload))
        except (ValueError, TypeError) as exc:
            return cls.failed(f"unparseable judge output: {exc}")
        if not isinstance(data, dict):
            return cls.failed("judge output was not a JSON object")

        bug_type = str(data.get("bug_type", "other"))
        return cls(
            is_bug=bool(data.get("is_bug", False)),
            bug_type=bug_type if bug_type in BUG_TYPES else "other",
            severity=_clamp(data.get("severity", 0.0)),
            confidence=_clamp(data.get("confidence", 0.0)),
            step=_as_int(data.get("step", 0)),
            observed=str(data.get("observed", ""))[:500],
            expected=str(data.get("expected", ""))[:500],
            discrepancy=str(data.get("discrepancy", ""))[:500],
            evidence=str(data.get("evidence", ""))[:500],
        )

    def to_dict(self) -> dict:
        return {
            "is_bug": self.is_bug,
            "bug_type": self.bug_type,
            "severity": round(self.severity, 3),
            "confidence": round(self.confidence, 3),
            "step": self.step,
            "observed": self.observed,
            "expected": self.expected,
            "discrepancy": self.discrepancy,
            "evidence": self.evidence,
            "error": self.error,
        }


def _extract_json(payload: str) -> str:
    text = (payload or "").strip()
    # Reasoning models (deepseek-r1 and friends) emit their chain of thought inline.
    # It routinely contains draft JSON, so scanning for the first `{` without stripping
    # this first would parse a discarded intermediate answer instead of the final one.
    text = _THINK_BLOCK.sub("", text).strip()
    if text.startswith("```"):
        body = text.split("```", 2)
        text = body[1] if len(body) > 1 else text
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text


def _clamp(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
