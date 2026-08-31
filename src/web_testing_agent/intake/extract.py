"""Mechanically derive an Application Profile from a static site's own markup.

**Scope, and why it is this narrow.** This is the smallest producer that can answer one
question: *does profile context derived from source change the judge, as opposed to
context a human wrote?* Answering that needs a profile nobody hand-authored, and nothing
more. So this reads HTML and reports what the markup declares. It is deterministic, has
no LLM in it, and knows nothing about any particular application.

It is **not** the Repo Profiler of §3.0b. That needs tree-sitter over real server-side
code — Flask decorators, ORM models, pydantic schemas — and is weeks of per-framework
work. This is a static-site special case, built to serve one experiment.

**The limitation that decides how the result may be read.** On a static HTML site the
"source" and the runtime DOM are very nearly the same artifact. A `min="13"` attribute is
in the file *and* in the page the browser renders, and `judge/window.py` already surfaces
declared constraints into the window. So a rule extracted here is frequently information
the judge already had by another route. Any improvement measured with this extractor is
therefore **not** clean evidence for source grounding as §1 states it, which is a claim
about intent that is invisible at runtime — server-side validation, ORM constraints,
handler logic. Report accordingly: this is a bounded test on a target where source and
runtime overlap, and it cannot confirm the differentiator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..utils.logging import get_logger
from .profile import ApplicationProfile, Behaviour, Flow, Route, Rule

logger = get_logger(__name__)

# Attributes that state something the application will or will not accept. `type` is
# included because `type="email"` and `type="number"` are constraints in exactly the same
# sense as `min` — the browser and the app both treat them as one.
_CONSTRAINT_ATTRS = ("type", "min", "max", "minlength", "maxlength", "pattern", "required", "step")

# Attribute names are matched with both ends anchored. A bare-name search reports
# constraints that were never declared -- `min` is a prefix of `minlength`, a utility
# class like `max-w-full` contains the word `max`, and `\b` matches after the hyphen in
# `data-type`. The same defect was measured in `judge/window.py` on 30.7% of judge inputs.
_ATTR_START = r"(?<![-\w])"
_TAG = re.compile(r"<(?P<tag>input|textarea|select|a|form)\b(?P<attrs>[^>]*)>", re.IGNORECASE)
_ANCHOR_TEXT = re.compile(r"<a\b[^>]*>(.*?)</a>", re.DOTALL | re.IGNORECASE)
_STRIP_TAGS = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)


def _attr(attrs: str, name: str) -> str | None:
    """One attribute's value, or "" for a valueless boolean, or None if absent."""
    match = re.search(
        rf"{_ATTR_START}{name}(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))|(?=[\s/>]|$))",
        attrs,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return match.group(1) or match.group(2) or match.group(3) or ""


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


@dataclass(frozen=True, slots=True)
class ExtractionStats:
    """What was read and what came out, so the profile's coverage is inspectable."""

    files: int = 0
    routes: int = 0
    rules: int = 0
    flows: int = 0

    def to_dict(self) -> dict:
        return {"files": self.files, "routes": self.routes, "rules": self.rules, "flows": self.flows}


def _describe_constraint(attrs: str) -> str:
    """The declared constraint as a sentence, or "" when the control declares nothing."""
    parts: list[str] = []
    kind = _attr(attrs, "type")
    # `checkbox` and `radio` name a control *kind*, not a constraint on a value, and
    # "must be a valid checkbox" is noise that competes with real rules for the profile's
    # character budget. Excluded on that mechanical ground, not because of anything known
    # about a particular application.
    if kind and kind.lower() not in ("text", "submit", "button", "hidden", "checkbox", "radio"):
        parts.append(f"must be a valid {kind.lower()}")
    if _attr(attrs, "required") is not None:
        parts.append("is required")

    low, high = _attr(attrs, "min"), _attr(attrs, "max")
    if low and high:
        parts.append(f"must be between {low} and {high}")
    elif low:
        parts.append(f"must be at least {low}")
    elif high:
        parts.append(f"must be at most {high}")

    min_len, max_len = _attr(attrs, "minlength"), _attr(attrs, "maxlength")
    if max_len:
        parts.append(f"must be at most {max_len} characters")
    if min_len:
        parts.append(f"must be at least {min_len} characters")

    pattern = _attr(attrs, "pattern")
    if pattern:
        parts.append(f"must match the pattern {pattern}")
    return ", ".join(parts)


def extract_profile(
    root: Path,
    *,
    name: str,
    app_type: str = "static web application",
    summary: str = "",
    auth: str = "anonymous",
) -> tuple[ApplicationProfile, ExtractionStats]:
    """Read every `.html` under `root` and report what the markup declares.

    Four element kinds, each traceable to a `file:line`:

    * **routes** — one per page file, described by its `<title>`
    * **rules** — one per control that declares a constraint
    * **flows** — one per `<form>`, from the page it lives on to its `action`
    * **behaviours** — none. This is deliberate and is the honest part of the design: a
      "known-correct behaviour" is a judgement about intent that markup does not contain,
      and inventing them would put hand-authored knowledge into a profile whose entire
      purpose is to have none. The hand-authored arm has them; this one does not, and any
      difference traceable to their absence is a finding rather than a flaw.
    """
    root = Path(root)
    pages = sorted(p for p in root.rglob("*.html") if p.is_file())
    if not pages:
        raise ValueError(f"No .html files under {root}")

    routes: list[Route] = []
    rules: list[Rule] = []
    flows: list[Flow] = []

    for page in pages:
        text = page.read_text(encoding="utf-8", errors="replace")
        rel = page.relative_to(root).as_posix()
        route_path = f"/{rel}"

        title = _TITLE.search(text)
        purpose = " ".join(_STRIP_TAGS.sub("", title.group(1)).split()) if title else rel
        routes.append(Route(path=route_path, purpose=purpose, auth=auth, source=f"{rel}:1"))

        for match in _TAG.finditer(text):
            tag, attrs = match.group("tag").lower(), match.group("attrs")
            line = _line_of(text, match.start())

            if tag in ("input", "textarea", "select"):
                field = _attr(attrs, "name") or _attr(attrs, "id")
                declared = _describe_constraint(attrs)
                if field and declared:
                    rules.append(Rule(field=field, route=route_path, rule=declared,
                                      source=f"{rel}:{line}"))

            elif tag == "form":
                action = _attr(attrs, "action") or route_path
                method = (_attr(attrs, "method") or "get").upper()
                form_id = _attr(attrs, "id") or "form"
                flows.append(Flow(
                    name=f"{form_id} on {route_path}",
                    routes=[route_path, action if action.startswith("/") else f"/{action}"],
                    steps=[f"complete {form_id}", f"submit ({method})"],
                    outcome=f"the application handles the submission at {action}",
                    source=f"{rel}:{line}",
                ))
                # `novalidate` is a declared property of the form in exactly the way
                # `required` is a declared property of an input, so it is extracted on the
                # same mechanical footing. It is worth calling out because it is the one
                # element here that the *window* does not already carry: `judge/window.py`
                # reads constraints off input/select/textarea tags and never looks at the
                # form. That makes it source-derived relative to what the judge is shown --
                # though not source-only in principle, since it is still in the live DOM.
                # Included because excluding an attribute for helping would bias the
                # experiment as surely as inventing one would.
                if _attr(attrs, "novalidate") is not None:
                    rules.append(Rule(
                        field=form_id,
                        route=route_path,
                        # Worded without positional reference: rules render in document
                        # order, and the form tag precedes its own inputs, so "above"
                        # pointed at the wrong entries.
                        rule=("the form declares novalidate, so the browser will not "
                              "enforce this form's field constraints; anything enforcing "
                              "them has to be the application itself"),
                        source=f"{rel}:{line}",
                    ))

    profile = ApplicationProfile(
        name=name,
        app_type=app_type,
        summary=summary or f"{len(routes)} pages extracted from static markup under {root.name}",
        auth=auth,
        # The field that keeps this distinguishable from a hand-authored profile in every
        # downstream report. Without it a number obtained from a file a human wrote and
        # one a producer derived would be indistinguishable.
        provenance="profiler",
        routes=tuple(routes),
        rules=tuple(rules),
        flows=tuple(flows),
        behaviours=(),
        notes=(
            "Mechanically extracted from static HTML by intake/extract.py. No LLM, no "
            "hand-authored content, no behaviours. On a static site the markup is also "
            "the runtime DOM, so many of these rules are visible to the judge through "
            "the window as well; this profile is not evidence about server-side intent."
        ),
    )
    stats = ExtractionStats(files=len(pages), routes=len(routes), rules=len(rules), flows=len(flows))
    logger.info("extracted {} from {}: {}", name, root, stats.to_dict())
    return profile, stats
