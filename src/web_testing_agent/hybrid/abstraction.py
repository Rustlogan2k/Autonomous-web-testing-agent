"""The abstract state the hybrid tester reasons over: what kind of page, where in the
application, which fields are filled, and whether the page is showing a problem.

Two label sources produce the same shape (`PageView`): the LLM labeller (`labels.py`) and
a deterministic fallback that uses only markup facts, which is also the plan's "no LLM
labels" ablation arm. Everything that depends on typed values — the form signature and
the alert flag — is read deterministically from the page, never from the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse

from ..envs.types import ActionSpec, ActionType
from ..rl.features import read_form_state
from .labels import PageLabels, control_identity, page_facts

_NUMBER = re.compile(r"\d+")
_ALERT_CLASS = re.compile(r"(?:^|[\s_-])(error|invalid|warning|alert|notice)(?:$|[\s_-])", re.I)
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)
_FIXED = {
    ActionType.NO_OP,
    ActionType.SCROLL,
    ActionType.RESIZE_VIEWPORT,
    ActionType.BROWSER_BACK,
    ActionType.BROWSER_FORWARD,
    ActionType.REFRESH,
}


def path_template(url: str) -> str:
    """The page's location without host, port or ids: `/orders/17#edit` -> `/orders/{n}#edit`."""
    parsed = urlparse(url or "")
    path = _NUMBER.sub("{n}", parsed.path or "/")
    return path + (f"#{_NUMBER.sub('{n}', parsed.fragment)}" if parsed.fragment else "")


class _AlertParser(HTMLParser):
    """Is the page showing a message of the kind validation and failures produce?"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.alert = False
        self._depth = 0  # >0 while inside an alert-like element

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: (v or "") for k, v in attrs}
        if values.get("aria-invalid") == "true":
            self.alert = True
        if tag in _VOID:
            return
        if self._depth:
            self._depth += 1
            return
        alert_like = values.get("role") in ("alert", "status") or _ALERT_CLASS.search(
            values.get("class", "")
        )
        if alert_like and values.get("data-hidden") != "true" and "hidden" not in values:
            self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._depth and tag not in _VOID:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth and data.strip():
            self.alert = True


def shows_alert(html: str) -> bool:
    parser = _AlertParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - unreadable markup shows no alert
        return False
    return parser.alert


def form_signature(html: str) -> tuple[tuple[str, bool], ...]:
    """Visible fields and whether each holds a value (a toggle: whether it is on), sorted.

    Reads the markup the env captured, in which live values are projected into attributes,
    so this is the field state the user would see.
    """
    form = read_form_state(html)
    seen: set[int] = set()
    out: dict[str, bool] = {}
    for table in (form.by_id, form.by_name):
        for name, control in table.items():
            if control.hidden or id(control) in seen:
                continue
            seen.add(id(control))
            out[name] = control.checked if control.kind == "toggle" else bool(control.value.strip())
    return tuple(sorted(out.items()))


# -- the deterministic fallback labeller -----------------------------------------------------

#: English cues for a control's role, used only when no model labels are available. Their
#: limits are the point of the ablation: they fail on unfamiliar vocabulary.
_ROLE_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "submit",
        (
            "place order",
            "submit",
            "send",
            "confirm",
            "pay",
            "book",
            "finish",
            "complete",
            "save",
            "store",
            "apply",
            "update",
            "next",
            "continue",
            "proceed",
            "review",
            "checkout",
        ),
    ),
    ("edit", ("edit", "change", "modify", "amend")),
    ("remove", ("remove", "delete", "discard", "clear")),
    ("add", ("add", "new item", "include")),
    ("back", ("back", "cancel", "close", "return")),
    ("search_or_filter", ("search", "filter", "sort", "find")),
    ("help", ("help", "faq", "support", "about")),
    ("open", ("start", "begin", "open", "new", "create", "view")),
)


def deterministic_labels(url: str, html: str, specs: list[ActionSpec]) -> PageLabels:
    facts = page_facts(specs, html)
    controls = facts.controls
    labels = PageLabels(key="", kind="other", model="deterministic", source="deterministic")
    labels.kind = facts.kind or ("home" if path_template(url) in ("/", "/index.html") else "other")
    for control in controls:
        if control.kind == "field":
            labels.meanings[control.identity] = control.deterministic or "other"
            labels.deterministic.append(control.identity)
            continue
        text = control.line.lower()
        role = next((r for r, cues in _ROLE_CUES if any(cue in text for cue in cues)), "")
        if not role:
            role = "navigate" if text.startswith("link") else "other"
        labels.roles[control.identity] = role
        labels.deterministic.append(control.identity)
    return labels


# -- the abstract state ----------------------------------------------------------------------


@dataclass
class PageView:
    """One observation, as the hybrid tester reasons about it."""

    url: str
    labels: PageLabels
    signature: tuple[tuple[str, bool], ...]
    alert: bool
    specs: list[ActionSpec] = field(default_factory=list)

    @property
    def cell(self) -> str:
        filled = ",".join(f"{name}={'1' if value else '0'}" for name, value in self.signature)
        return f"{self.labels.kind}|{path_template(self.url)}|{filled}|{'!' if self.alert else ''}"

    def role_of(self, spec: ActionSpec) -> str:
        if spec.action_type in _FIXED:
            return spec.action_type.value.lower()
        return self.labels.role(spec) or "other"

    def controls_with_role(self, *roles: str) -> list[ActionSpec]:
        """Clicks (not rapid clicks) whose control has one of `roles`, in page order."""
        return [
            s
            for s in self.specs
            if s.action_type is ActionType.CLICK
            and self.labels.roles.get(control_identity(s)) in roles
        ]

    def fields(self) -> dict[str, list[ActionSpec]]:
        """Field identity -> its TYPE / SELECT actions (one per value category or option)."""
        out: dict[str, list[ActionSpec]] = {}
        for spec in self.specs:
            if spec.action_type in (ActionType.TYPE, ActionType.SELECT):
                out.setdefault(control_identity(spec), []).append(spec)
        return out


def observe(info: dict, labeller=None, *, context: str = "") -> PageView:
    """The `PageView` for an env `info`, labelled by `labeller` or deterministically."""
    page = info.get("page") or {}
    url, html = page.get("url", ""), page.get("html", "")
    specs = list(info.get("action_specs") or [])
    labels = None
    if labeller is not None:
        labels = labeller.label(
            url, html, specs, context=context, visible_text=page.get("visible_text")
        )
    if labels is None:
        labels = deterministic_labels(url, html, specs)
    return PageView(url, labels, form_signature(html), shows_alert(html), specs)
