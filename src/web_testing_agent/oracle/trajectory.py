"""What an assertion is allowed to see: observed steps, and nothing about the agent.

The oracle's independence is enforced here rather than promised in a docstring. An
`ObservedStep` carries a URL, the page's text, the page's HTML and the action that produced
it. It carries no reward, no Q-value, no policy identity, no archive state and no model
output, because `from_info()` only copies those four things out of the environment's
`info` dict.

**Why the page text is extracted here and not by each assertion.** Six applications will
structure their DOM differently on purpose. If every assertion did its own extraction, six
applications would drift into six extraction conventions and a `text_equals` on one would
not mean what it means on another. One extractor, one convention.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_TAG = re.compile(r"<[^>]+>")
_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WHITESPACE = re.compile(r"\s+")


def visible_text(html: str) -> str:
    """Page text with scripts, styles and tags removed, whitespace collapsed.

    Deliberately regex-based rather than a parser: it runs on every step of every replay
    during minimization, it must never raise on malformed markup, and the assertions only
    ever ask whether a short literal appears. A parser would be more correct and slower,
    and correctness beyond this is not needed to compare a rendered value against an input.
    """
    if not html:
        return ""
    without_code = _SCRIPT_OR_STYLE.sub(" ", html)
    return _WHITESPACE.sub(" ", _TAG.sub(" ", without_code)).strip()


def region_text(html: str, selector: str) -> str | None:
    """Text inside the element a region's selector names, or None when it is absent.

    Supports the selector forms the fixtures use -- `#id`, `[id="..."]`, `.class` and a
    bare tag -- which is enough for fixtures this project authors and keeps the oracle free
    of a DOM dependency it would otherwise need during offline verification.
    """
    if not html or not selector:
        return None

    identifier = None
    if selector.startswith("#"):
        identifier = selector[1:]
    elif selector.startswith('[id="') and selector.endswith('"]'):
        identifier = selector[5:-2]

    if identifier:
        pattern = re.compile(
            rf'<([a-zA-Z][\w-]*)[^>]*\bid\s*=\s*["\']{re.escape(identifier)}["\'][^>]*>'
            rf'(.*?)</\1>', re.IGNORECASE | re.DOTALL)
    elif selector.startswith("."):
        cls = selector[1:]
        pattern = re.compile(
            rf'<([a-zA-Z][\w-]*)[^>]*\bclass\s*=\s*["\'][^"\']*\b{re.escape(cls)}\b[^"\']*["\'][^>]*>'
            rf'(.*?)</\1>', re.IGNORECASE | re.DOTALL)
    else:
        tag = re.escape(selector.strip())
        pattern = re.compile(rf'<({tag})\b[^>]*>(.*?)</\1>', re.IGNORECASE | re.DOTALL)

    match = pattern.search(html)
    if match is None:
        return None
    return visible_text(match.group(2))


@dataclass(slots=True)
class ObservedStep:
    """One step as the oracle sees it. Four facts, and deliberately no fifth."""

    index: int
    url: str
    html: str = ""
    #: The action that produced this observation, in the replayable schema
    #: (`go_explore.to_replay_step`), or None for the episode's first observation.
    action: dict | None = None
    #: Values the agent entered, by field name, accumulated up to and including this step.
    #: This is what lets `echoes_input` compare the application against the user's own
    #: input rather than against a literal baked into the fault.
    inputs: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return visible_text(self.html)

    def region(self, selector: str) -> str | None:
        return region_text(self.html, selector)


@dataclass(slots=True)
class TrajectoryView:
    """An ordered run of observed steps, plus the replayable action sequence.

    `actions` is what the replayer and the minimizer operate on; `steps` is what the
    assertions read. They are kept side by side because a minimized *action* sequence has
    to be re-observed before it can be re-asserted -- you cannot minimize the observations.
    """

    steps: list[ObservedStep] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)

    def matching(self, where: str) -> list[ObservedStep]:
        """Steps whose URL matches `where`, in order."""
        pattern = re.compile(where)
        return [step for step in self.steps if pattern.search(step.url)]

    def inputs_at_end(self) -> dict[str, str]:
        return dict(self.steps[-1].inputs) if self.steps else {}

    def to_dict(self) -> dict:
        return {
            "n_steps": len(self.steps),
            "n_actions": len(self.actions),
            "urls": [step.url for step in self.steps],
            "actions": list(self.actions),
        }


def input_field_of(action: dict | None) -> tuple[str, str] | None:
    """`(field, value)` when this action entered a value, else None.

    Reads the replay schema rather than an `ActionSpec`, so a trajectory reconstructed
    from a saved trace behaves identically to a live one.
    """
    if not action or action.get("type") not in ("TYPE", "SELECT"):
        return None
    field_name = str(action.get("id") or action.get("text") or "").strip()
    params = action.get("params") or {}
    value = params.get("value")
    if value is None:
        value = params.get("option") if action.get("type") == "SELECT" else params.get("category")
    if not field_name or value is None:
        return None
    return field_name, str(value)


def build_trajectory(records: list[dict]) -> TrajectoryView:
    """Assemble a `TrajectoryView` from per-step dicts.

    Each record needs `url`, optionally `html`, and optionally `action` in the replay
    schema. Typed values are accumulated forward, so a page late in a flow can be checked
    against a value entered much earlier -- which is exactly what a delayed-observation
    fault requires.
    """
    view = TrajectoryView()
    inputs: dict[str, str] = {}
    for index, record in enumerate(records):
        action = record.get("action")
        # `entered` is the authoritative record when the caller has one: the live runner
        # reads the field's human-facing name and the value actually typed off the
        # `ActionSpec`. Parsing the replay step is the fallback, and it can only see the
        # DOM id -- which would tie a fault's `input_field` to the application's markup
        # and break the moment a fixture renames an element.
        explicit = record.get("entered")
        if isinstance(explicit, dict):
            inputs.update({str(k): str(v) for k, v in explicit.items()})
        else:
            entered = input_field_of(action)
            if entered is not None:
                inputs[entered[0]] = entered[1]
        view.steps.append(ObservedStep(
            index=index,
            url=str(record.get("url", "")),
            html=str(record.get("html", "") or ""),
            action=action,
            inputs=dict(inputs),
        ))
        if action:
            view.actions.append(action)
    return view
