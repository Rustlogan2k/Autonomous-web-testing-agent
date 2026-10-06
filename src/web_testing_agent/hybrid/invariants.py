"""Generic invariants: functional checks that need no specification (plan §5, oracle layer b).

They hold on any application, so they are the part of the oracle that can work on an
uploaded app with no `faults.json`. Each check reads only what the tester observed; a
violation is a *candidate*, which still has to reproduce on replay before it is reported
(`verify_candidates`). In the evaluation they are scored separately from the seeded-fault
oracle: how many seeded faults they alone would have flagged, and how many candidates they
raise on clean builds.

- `reload_stability`: reloading a page must not change its content. Alert and notice regions
  (one-time messages) and clock times are ignored; form values are not content.
- `round_trip`: a distinctive value the tester entered must not reappear altered (truncated,
  re-cased, partly replaced) on a later page. Values that never reappear are not evidence.
- `validation_consistency`: a value class (field meaning x input category) that the
  application rejected on one path must not be accepted on another.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from ..envs.types import ActionSpec, ActionType
from .abstraction import _ALERT_CLASS, _VOID, PageView
from .labels import control_identity

_CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_SKIP = {"script", "style", "noscript", "template"}
MIN_ROUND_TRIP_LENGTH = 6
NEAR_MATCH = 0.8


class _ContentText(HTMLParser):
    """Visible text minus alert-like regions, scripts and hidden elements."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID:
            return
        values = {k: (v or "") for k, v in attrs}
        hidden = values.get("data-hidden") == "true" or "hidden" in values
        alert = values.get("role") in ("alert", "status") or _ALERT_CLASS.search(
            values.get("class", "")
        )
        if self._skip or tag in _SKIP or hidden or alert:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if self._skip and tag not in _VOID:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def content_text(html: str) -> str:
    parser = _ContentText()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - unreadable markup has no comparable content
        return ""
    return _CLOCK.sub("{time}", " ".join(" ".join(parser.parts).split()))


@dataclass
class Candidate:
    invariant: str
    step: int
    url: str
    detail: str
    #: the episode's actions up to and including the one after which it was seen
    trace: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "invariant": self.invariant,
            "step": self.step,
            "url": self.url,
            "detail": self.detail,
            "trace_length": len(self.trace),
        }


class InvariantChecker:
    """Feed it every transition; it returns the candidates that transition raised."""

    def __init__(self) -> None:
        self.entered: dict[str, tuple[str, str, str]] = {}  # field -> (value, meaning, category)
        self.pending: dict[
            str, tuple[str, str]
        ] = {}  # field -> (meaning, category) since last submit
        self.verdicts: dict[
            tuple[str, str], set[str]
        ] = {}  # (meaning, category) -> {"rejected","accepted"}
        self.seen: set[tuple[str, str]] = set()  # (invariant, detail) already raised this run

    def begin_episode(self) -> None:
        self.entered.clear()
        self.pending.clear()

    def check(
        self, before: PageView, spec: ActionSpec, after: PageView, step: int
    ) -> list[Candidate]:
        found: list[Candidate] = []
        if spec.action_type is ActionType.REFRESH and before.url == after.url:
            old, new = content_text(before_html(before)), content_text(before_html(after))
            if old and new and old != new:
                found.append(self._raise("reload_stability", step, after, _diff(old, new)))
        if spec.action_type is ActionType.TYPE:
            self._record_entry(spec, before, after)
        if (
            spec.action_type in (ActionType.CLICK, ActionType.RAPID_CLICK)
            and before.role_of(spec) == "submit"
        ):
            found += self._judge_submission(after, step)
        found += self._round_trip(after, step)
        return [c for c in found if c is not None]

    # -- the checks -----------------------------------------------------------------------------

    def _record_entry(self, spec: ActionSpec, before: PageView, after: PageView) -> None:
        identity = control_identity(spec)
        meaning = before.labels.meanings.get(identity, "other")
        category = str((spec.params or {}).get("category", ""))
        value = field_value(after, identity)
        if value is not None:
            self.entered[identity] = (value, meaning, category)
        self.pending[identity] = (meaning, category)

    def _judge_submission(self, after: PageView, step: int) -> list[Candidate | None]:
        outcome = "rejected" if after.alert else "accepted"
        raised = []
        for meaning, category in self.pending.values():
            if category in ("", "valid_typical") or meaning == "other":
                continue
            seen = self.verdicts.setdefault((meaning, category), set())
            if outcome == "accepted" and "rejected" in seen:
                raised.append(
                    self._raise(
                        "validation_consistency",
                        step,
                        after,
                        f"{category} {meaning} rejected elsewhere, accepted here",
                    )
                )
            seen.add(outcome)
        self.pending.clear()
        return raised

    def _round_trip(self, after: PageView, step: int) -> list[Candidate | None]:
        text = content_text(before_html(after))
        raised = []
        for value, _meaning, category in self.entered.values():
            if category != "valid_typical" or len(value) < MIN_ROUND_TRIP_LENGTH:
                continue
            if not re.search(r"[A-Za-z]", value) or value in text:
                continue
            near = _near_match(value, text)
            if near is not None:
                raised.append(
                    self._raise("round_trip", step, after, f"entered {value!r} shown as {near!r}")
                )
        return raised

    def _raise(self, invariant: str, step: int, view: PageView, detail: str) -> Candidate | None:
        key = (invariant, detail)
        if key in self.seen:
            return None
        self.seen.add(key)
        return Candidate(invariant, step, view.url, detail)


def before_html(view: PageView) -> str:
    return view.html or ""


def field_value(view: PageView, identity: str) -> str | None:
    from ..rl.features import read_form_state

    form = read_form_state(before_html(view))
    dom = identity[5:-2] if identity.startswith('[id="') else identity
    control = form.by_id.get(dom) or form.by_name.get(dom)
    return control.value if control is not None and control.kind == "text" else None


def _near_match(value: str, text: str) -> str | None:
    """A window of `text` that resembles `value` closely without being it."""
    words = text.split()
    width = len(value.split())
    best, best_ratio = None, 0.0
    for size in {max(1, width - 1), width, width + 1}:
        for i in range(0, max(0, len(words) - size) + 1):
            window = " ".join(words[i : i + size])
            ratio = difflib.SequenceMatcher(None, value.lower(), window.lower()).ratio()
            if ratio > best_ratio:
                best, best_ratio = window, ratio
    if best is not None and best_ratio >= NEAR_MATCH and best != value:
        return best
    return None


def _diff(old: str, new: str) -> str:
    matcher = difflib.SequenceMatcher(None, old, new)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            return f"{old[max(0, i1 - 20):i2 + 20]!r} -> {new[max(0, j1 - 20):j2 + 20]!r}"
    return ""
