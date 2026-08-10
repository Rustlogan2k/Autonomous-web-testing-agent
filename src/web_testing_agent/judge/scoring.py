"""Scores judge verdicts against the seeded-bug answer key.

The headline number is recall on the seven `llm_required` bugs, because that is the
headroom the deterministic triggers cannot reach and therefore the only thing the
judge can be said to add. It is reported alongside the false-positive rate, since
recall alone is trivially maximized by reporting everything — and a judge that does
that is worse than useless once its verdict becomes reward.

Attribution is per *episode*, not per step. A scripted episode is one repro path with
one known ground truth, so the question is whether the bug was found anywhere in that
episode. Demanding the exact step would score presentation rather than detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .verdict import Verdict

_WORD = re.compile(r"[a-z0-9#/._-]+")
# A quote is treated as grounded if most of its content words occur in the window.
# Not an exact substring match: models legitimately reflow whitespace and drop the
# label part of a rendered line while keeping the value.
_GROUNDING_THRESHOLD = 0.8
_MIN_QUOTE_WORDS = 3


def is_grounded(evidence: str, window_text: str) -> bool:
    """Whether the judge's quoted evidence actually appears in what it was shown.

    The check the other metrics cannot make. Recall says whether a bug was flagged and
    discrimination says whether flagging tracks the truth, but neither notices a verdict
    built on something that was never in the window. Measured on qwen2.5:7b: it reported
    "after scrolling, the counter value is reset to 0" — SCROLL resets nothing, and no
    such observation existed — at confidence 1.0. A reward model paying out for invented
    evidence is the reward-hacking failure this project already documented once.
    """
    words = _WORD.findall(evidence.lower())
    if len(words) < _MIN_QUOTE_WORDS:
        return False
    haystack = set(_WORD.findall(window_text.lower()))
    hits = sum(1 for word in words if word in haystack)
    return hits / len(words) >= _GROUNDING_THRESHOLD


@dataclass(slots=True)
class Judgment:
    """One verdict, tied back to where it came from."""

    episode: int
    global_step: int
    verdict: Verdict
    expected_bugs: list[str] = field(default_factory=list)
    script: str = ""
    # Whether this episode has ground truth at all. The distinction matters: the
    # scripted `happy_path` episode is *known-correct*, so a positive there is a false
    # positive. A random-exploration episode is merely *unlabelled* — it genuinely
    # contains bugs, so scoring its positives as false alarms would invent a failure.
    labelled: bool = True
    # Whether the quoted evidence actually occurs in the window it was quoting from.
    # None when not checked. See `evidence_grounded` on the report for why this exists.
    grounded: bool | None = None

    @property
    def is_control(self) -> bool:
        """A labelled episode with no seeded bug — every positive here is a false positive."""
        return self.labelled and not self.expected_bugs

    @property
    def is_unattributed(self) -> bool:
        return not self.labelled


@dataclass(slots=True)
class ScoreReport:
    corpus: str = ""
    judge: str = ""
    windows: int = 0
    failed_calls: int = 0
    positives: int = 0
    episodes_scored: int = 0
    detected: dict[str, list[int]] = field(default_factory=dict)
    missed: list[str] = field(default_factory=list)
    false_positive_episodes: dict[int, int] = field(default_factory=dict)
    control_windows: int = 0
    control_positives: int = 0
    unattributed_windows: int = 0
    unattributed_positives: int = 0
    bug_windows: int = 0
    bug_positives: int = 0
    grounded_positives: int = 0
    checked_positives: int = 0

    @property
    def recall(self) -> float:
        total = len(self.detected) + len(self.missed)
        return len(self.detected) / total if total else 0.0

    @property
    def false_positive_rate(self) -> float:
        """Share of *known-correct* windows that produced a positive verdict."""
        return self.control_positives / self.control_windows if self.control_windows else 0.0

    @property
    def unattributed_rate(self) -> float:
        """Positive rate on unlabelled traffic. Not a false-positive rate — these need review."""
        return self.unattributed_positives / self.unattributed_windows if self.unattributed_windows else 0.0

    @property
    def evidence_grounded(self) -> float:
        """Share of positive verdicts whose quoted evidence occurs in the window.

        Below 1.0 means the judge is inventing observations. Unlike recall and
        discrimination, this is objective — it does not depend on the answer key at
        all, so it is the one judge-quality metric that carries over to a real target
        where no ground truth exists.
        """
        return self.grounded_positives / self.checked_positives if self.checked_positives else 1.0

    @property
    def discrimination(self) -> float:
        """Positive rate on buggy windows minus positive rate on known-correct ones.

        The number recall alone cannot tell you. A judge that answers "bug" to almost
        everything scores perfect recall while being worthless — measured on llama3:8b,
        which reached 7/7 semantic bugs at an 88%/71% split, i.e. no signal at all.
        Near zero means the verdict is independent of whether a bug was present.
        """
        if not self.bug_windows or not self.control_windows:
            return 0.0
        return self.bug_positives / self.bug_windows - self.control_positives / self.control_windows

    def to_dict(self) -> dict:
        return {
            "corpus": self.corpus,
            "judge": self.judge,
            "windows": self.windows,
            "failed_calls": self.failed_calls,
            "positive_verdicts": self.positives,
            "episodes_scored": self.episodes_scored,
            "detected": {bug: sorted(steps) for bug, steps in sorted(self.detected.items())},
            "missed": sorted(self.missed),
            "recall": round(self.recall, 3),
            "control_windows": self.control_windows,
            "control_positives": self.control_positives,
            "false_positive_rate": round(self.false_positive_rate, 3),
            "false_positive_episodes": dict(sorted(self.false_positive_episodes.items())),
            "unattributed_windows": self.unattributed_windows,
            "unattributed_positives": self.unattributed_positives,
            "unattributed_rate": round(self.unattributed_rate, 3),
            "bug_windows": self.bug_windows,
            "bug_positives": self.bug_positives,
            "discrimination": round(self.discrimination, 3),
            "checked_positives": self.checked_positives,
            "grounded_positives": self.grounded_positives,
            "evidence_grounded": round(self.evidence_grounded, 3),
        }


def score(judgments: list[Judgment], corpus: str = "", judge: str = "") -> ScoreReport:
    """Attribute verdicts to seeded bugs, per episode.

    A positive verdict anywhere in an episode counts as detecting every bug that
    episode was written to exercise. That is generous by construction — the signup
    episode carries both BUG-01 and BUG-05, and one verdict cannot distinguish them —
    so the number is an upper bound on detection and is reported as such.
    """
    report = ScoreReport(corpus=corpus, judge=judge, windows=len(judgments))
    expected_by_episode: dict[int, list[str]] = {}
    positives_by_episode: dict[int, list[int]] = {}

    for judgment in judgments:
        verdict = judgment.verdict
        if not verdict.ok:
            report.failed_calls += 1
            continue
        if judgment.is_unattributed:
            report.unattributed_windows += 1
            if verdict.is_bug:
                report.positives += 1
                report.unattributed_positives += 1
                if judgment.grounded is not None:
                    report.checked_positives += 1
                    report.grounded_positives += bool(judgment.grounded)
            continue

        expected_by_episode.setdefault(judgment.episode, list(judgment.expected_bugs))
        if judgment.is_control:
            report.control_windows += 1
        else:
            report.bug_windows += 1
            report.bug_positives += bool(verdict.is_bug)
        if verdict.is_bug:
            report.positives += 1
            if judgment.grounded is not None:
                report.checked_positives += 1
                report.grounded_positives += bool(judgment.grounded)
            positives_by_episode.setdefault(judgment.episode, []).append(judgment.global_step)
            if judgment.is_control:
                report.control_positives += 1
                report.false_positive_episodes[judgment.episode] = (
                    report.false_positive_episodes.get(judgment.episode, 0) + 1
                )

    report.episodes_scored = len(expected_by_episode)
    for episode, bugs in expected_by_episode.items():
        hits = positives_by_episode.get(episode, [])
        for bug in bugs:
            if hits:
                report.detected.setdefault(bug, []).extend(hits)
            elif bug not in report.detected:
                report.missed.append(bug)
    # A bug can be listed in two episodes; a hit anywhere wins.
    report.missed = [bug for bug in dict.fromkeys(report.missed) if bug not in report.detected]
    return report


def summarize(report: ScoreReport, answer_key: dict) -> str:
    """Human-readable comparison against the deterministic baseline."""
    bugs = {bug["id"]: bug for bug in answer_key["bugs"]}
    semantic = {bid for bid, bug in bugs.items() if "deterministic" not in bug["detectability"]}
    deterministic = set(bugs) - semantic

    found_semantic = sorted(semantic & set(report.detected))
    found_deterministic = sorted(deterministic & set(report.detected))

    lines = [
        f"corpus                : {report.corpus}",
        f"judge                 : {report.judge}",
        f"windows judged        : {report.windows}  (failed calls: {report.failed_calls})",
        f"positive verdicts     : {report.positives}",
    ]
    if report.unattributed_windows:
        # No ground truth here, so nothing can be scored: this corpus measures how
        # often the judge speaks up on ordinary traffic, and those findings need a
        # human read to tell a real discovery from a false alarm.
        lines += [
            "",
            f"unlabelled windows    : {report.unattributed_windows}  (no ground truth — not scorable)",
            f"  positive verdicts   : {report.unattributed_positives}  (rate {report.unattributed_rate:.1%})",
            "  -> review these by hand; they are neither detections nor false positives",
        ]
        return "\n".join(lines)

    lines += [
        "",
        f"semantic bugs found   : {len(found_semantic)}/{len(semantic)}  {found_semantic}",
        f"  missed              : {sorted(semantic - set(report.detected))}",
        f"deterministic found   : {len(found_deterministic)}/{len(deterministic)}  {found_deterministic}",
        "",
        f"control windows       : {report.control_windows}",
        f"false positives       : {report.control_positives}  (rate {report.false_positive_rate:.1%})",
    ]
    if report.false_positive_episodes:
        lines.append(f"  in episodes         : {dict(report.false_positive_episodes)}")

    bug_rate = report.bug_positives / report.bug_windows if report.bug_windows else 0.0
    lines += [
        "",
        f"positive rate, buggy  : {report.bug_positives}/{report.bug_windows} ({bug_rate:.0%})",
        f"positive rate, clean  : {report.control_positives}/{report.control_windows} "
        f"({report.false_positive_rate:.0%})",
        f"DISCRIMINATION        : {report.discrimination:+.0%}   <- recall is meaningless without this",
        f"evidence grounded     : {report.grounded_positives}/{report.checked_positives} "
        f"({report.evidence_grounded:.0%})   <- quotes that really occur in the window",
        "",
        f"deterministic ceiling : {len(deterministic)}/{len(bugs)} of all seeded bugs",
        f"judge total           : {len(report.detected)}/{len(bugs)}",
    ]
    return "\n".join(lines)
