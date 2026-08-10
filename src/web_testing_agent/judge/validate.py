"""Checks that a rendered window tells the truth about the step it describes.

Every judge defect found so far has had the same shape: the window asserted something
the record did not support, the judge reasoned correctly from it, and the resulting
verdict looked like a model failure. The list, all measured:

* `NO OBSERVABLE CHANGE — byte-identical` on pages whose HTML hash had changed
  (21 false dead-control verdicts on 39 TYPE windows).
* A harness refusal rendered as `FAILED: ... did not navigate`, read as a broken link
  (6 of 11 refusals on Gitea).
* A value silently shortened to 60 characters, read as the application truncating
  input.
* Playwright's call log pasted verbatim — 78% of the largest window in a corpus.
* Twenty framework-generated ids filling the hidden-element budget.

Each was found *after* a scoring run, by reading verdicts. That is the wrong feedback
loop: it costs a corpus of model calls and an hour of manual reading to discover a
defect that is decidable from the record alone, offline, in milliseconds.

So these are invariants over `(record, rendered_window)` — properties the renderer must
never violate regardless of the target application. They run in `--dry-run` before any
call is made, and as a preflight before scoring.

They deliberately check *honesty*, not quality. Whether a window is a good summary is a
judgment call and belongs in the manual read; whether it contradicts its own record is
a fact, and facts can be asserted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..annotation.trace import StepRecord
from .window import _VALUE_CHARS_MAX, JudgeWindow, render_step

# A rendered line far longer than any summary line has a reason to be is almost always
# raw machine output that escaped a truncation — a stack trace, a call log, a minified
# bundle. The longest legitimate line observed across both corpora is ~250 characters.
MAX_LINE_CHARS = 400

# Markers of raw tooling output that must never reach the prompt intact.
_RAW_OUTPUT = (
    "Call log:",
    "Traceback (most recent call last)",
    "    at Object.",
    "\n  - waiting for",
)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_NO_CHANGE = "NO OBSERVABLE CHANGE"


@dataclass(frozen=True, slots=True)
class WindowIssue:
    """One violated invariant, tied to the step that produced it."""

    episode: int
    global_step: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"ep{self.episode} step{self.global_step} [{self.rule}] {self.detail}"


def validate_step(record: StepRecord, rendered: str | None = None) -> list[WindowIssue]:
    """Invariants for one rendered transition."""
    text = render_step(record, 1) if rendered is None else rendered
    issues: list[WindowIssue] = []

    def add(rule: str, detail: str) -> None:
        issues.append(WindowIssue(record.episode, record.global_step, rule, detail))

    # The claim that most directly drives a verdict, so the one worth checking hardest.
    if _NO_CHANGE in text and record.changed.get("html"):
        add("false-no-change",
            "window asserts byte-identity but the HTML hash changed for this step")

    # A harness refusal is not application behaviour and must not read as one.
    if record.exec.get("left_application"):
        if "BLOCKED" not in text:
            add("unmarked-block", "an off-site navigation was refused but not marked BLOCKED")
        if "FAILED" in text:
            add("block-as-failure", "a harness refusal is rendered as a failed action")
        if _NO_CHANGE in text:
            add("block-as-no-change",
                "a blocked step claims no observable change — the page is unchanged "
                "because the harness restored it, which says nothing about the application")
        if record.after.get("console_errors"):
            add("offsite-console",
                "console output survives a blocked navigation; it may have been emitted "
                "by the off-site page rather than the application under test")

    # A `target="_blank"` click is not supposed to change the page it was clicked from,
    # and the harness does not follow the tab it opens. Claiming byte-identity there
    # states the strongest bug signal in the prompt about a link that behaved correctly:
    # measured on Gitea's landing page, four such links each drew a 90-100%-confidence
    # broken_navigation verdict.
    # Skipped when the step was blocked: there, the popup *was* adopted and then refused,
    # BLOCKED already says so, and emitting both lines would repeat the mistake of
    # stating two different explanations for one unchanged page.
    #
    # That the same link can go either way is the point. In one capture, step 1 clicked
    # `github.com/go-gitea/gitea` and the tab was never adopted; step 11 clicked
    # `docs.gitea.com/...` on the same page and it was. Whether the harness notices the
    # popup is a race, so both renderings have to be correct.
    if (
        (record.action.get("params") or {}).get("opens_new_tab")
        and not record.state_changed
        and not record.exec.get("left_application")
    ):
        if "NEW TAB" not in text:
            add("unmarked-new-tab",
                "a click on a target=\"_blank\" link is not marked NEW TAB, so the judge "
                "cannot tell it from a control that did nothing")
        if _NO_CHANGE in text:
            add("new-tab-as-no-change",
                "a new-tab link claims no observable change — the page it was clicked "
                "from is not supposed to change, which says nothing about the link")

    # Only lines that display a value *this renderer chose to shorten* can violate the
    # elision rule. Applied to the whole window it fired on quoted fragments inside
    # console messages and page copy — text the application produced, which the
    # renderer neither shortened nor claims to have shortened.
    #
    # The threshold is the renderer's own elision limit rather than a number chosen
    # here. A hand-picked 55 sat *below* `_VALUE_CHARS_MAX`, so any value between 55 and
    # 60 characters — shown in full, exactly as intended — was reported as a silent
    # elision: Gitea's Swagger page produced one on the complete 57-character label
    # "GET /version Returns the version of the Gitea application". A validator with its
    # own copy of a constant checks a rule the renderer is not following.
    for line in text.splitlines():
        if not line.lstrip().startswith(("field value:", "action ")):
            continue
        for match in re.finditer(rf"'([^'\n]{{{_VALUE_CHARS_MAX + 1},}})'", line):
            value = match.group(1)
            if "chars]" not in value and "…" not in value:
                add("silent-elision",
                    f"a {len(value)}-char value is shown without an elision marker")
                break

    for marker in _RAW_OUTPUT:
        if marker in text:
            add("raw-tool-output", f"contains {marker.strip()!r} — untruncated tooling output")
            break

    # Measured against the *longest item*, not the whole line. A line listing fifteen
    # of an application's links is long because the application has fifteen links; a
    # line carrying one 6,000-character blob is a truncation that failed. Only the
    # second is a defect, and conflating them makes the rule unactionable.
    for line in text.splitlines():
        longest = max((len(part) for part in re.split(r" \| |; ", line)), default=0)
        if longest > MAX_LINE_CHARS:
            add("overlong-item", f"{longest}-char single item: {line[:60]!r}…")
            break

    if _CONTROL_CHARS.search(text):
        add("control-characters", "rendered window contains control characters")

    return issues


def validate_window(window: JudgeWindow, context_chars: int | None = None) -> list[WindowIssue]:
    """Invariants for a whole window, including its size."""
    issues = [issue for record in window.records for issue in validate_step(record)]
    return issues + _context_issue(window, context_chars)


def _context_issue(window: JudgeWindow, context_chars: int | None) -> list[WindowIssue]:
    if context_chars is None:
        return []
    size = len(window.render())
    if size <= context_chars:
        return []
    return [WindowIssue(
        window.episode, window.focus_global_step, "over-context",
        f"{size} chars exceeds the {context_chars}-char budget; llama.cpp truncates "
        "from the front, which silently removes the system prompt",
    )]


def validate_corpus(windows: list[JudgeWindow], context_chars: int | None = None) -> list[WindowIssue]:
    """Every invariant over a whole corpus, deduplicated per (step, rule).

    Each step is validated once rather than once per window it appears in. A step
    occurs in up to `WINDOW_STEPS` windows, and rendering it means parsing two full
    pages — on a real application that redundancy made the check slower than the
    scoring run it exists to protect.
    """
    seen: set[tuple[int, str]] = set()
    issues: list[WindowIssue] = []
    validated: set[int] = set()

    for window in windows:
        for record in window.records:
            if record.global_step in validated:
                continue
            validated.add(record.global_step)
            for issue in validate_step(record):
                key = (issue.global_step, issue.rule)
                if key not in seen:
                    seen.add(key)
                    issues.append(issue)
        issues.extend(_context_issue(window, context_chars))
    return issues


def summarize_issues(issues: list[WindowIssue], limit: int = 10) -> str:
    if not issues:
        return "window validation: no issues"
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.rule] = counts.get(issue.rule, 0) + 1
    lines = [f"window validation: {len(issues)} issue(s) across {len(counts)} rule(s)"]
    lines += [f"  {rule:<20} {count}" for rule, count in sorted(counts.items(), key=lambda kv: -kv[1])]
    lines.append("  examples:")
    lines += [f"    {issue}" for issue in issues[:limit]]
    return "\n".join(lines)
