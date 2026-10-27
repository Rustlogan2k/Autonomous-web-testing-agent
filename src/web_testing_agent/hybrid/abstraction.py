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


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX = re.compile(r"^(?=.*\d)[0-9a-f]{8,}$", re.I)
#: Letters and digits interleaved (`B1kKc_2nMg`), unlike route names that end in a number (`step-2`).
_MIXED = re.compile(r"^(?=.*[A-Za-z]\d+[A-Za-z])[A-Za-z0-9_-]{6,32}$")
_TOKEN_CHARS = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
#: Case chunks, acronyms kept whole: `getHTMLData` -> get, HTML, Data; `SkGvNlChfl` -> Sk, Gv, Nl, Chfl.
_CHUNK = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def _looks_generated(segment: str) -> bool:
    """A letter-only token that reads like a generated id, not words: mixed case, and it splits
    into at least three case chunks averaging under 3.5 letters (`SkGvNlChfl`), where route
    names split into words (`userProfile`, `strengthsList`, `getHTMLData`)."""
    if not _TOKEN_CHARS.match(segment) or segment.islower() or segment.isupper():
        return False
    chunks = _CHUNK.findall(segment)
    return len(chunks) >= 3 and sum(map(len, chunks)) / len(chunks) < 3.5
#: Distinct such tokens seen at one place in a URL before that place counts as an id.
ID_EVIDENCE = 3
#: Per URL prefix, the distinct candidate tokens seen after it (at most ID_EVIDENCE kept).
_SEEN: dict[str, set[str]] = {}


def _id_like(segment: str) -> bool:
    """A generated identifier by its shape alone: a UUID, a hex hash, or a token with letters
    and digits interleaved (`B1kKc_2nMg`), unlike route names that end in a number (`step-2`)."""
    return bool(_UUID.match(segment) or _HEX.match(segment) or _MIXED.match(segment))


def _template(part: str) -> str:
    """Ids by shape, and letter-only ids (`SkGvNlChfl`) by evidence: once `ID_EVIDENCE`
    different mixed-case tokens have appeared after the same prefix (`/session/`), that
    position holds generated ids. A route name such as `strengthsList` is never one of several
    siblings minted at run time, so it stays itself."""
    out: list[str] = []
    for seg in part.split("/"):
        if _id_like(seg):
            out.append("{id}")
            continue
        if _looks_generated(seg):
            seen = _SEEN.setdefault("/".join(out), set())
            if len(seen) < ID_EVIDENCE:
                seen.add(seg)
            if len(seen) >= ID_EVIDENCE:
                out.append("{id}")
                continue
        out.append(seg)
    return _NUMBER.sub("{n}", "/".join(out))


def forget_id_evidence() -> None:
    """Start a new application: the evidence about where its URLs carry ids is per application."""
    _SEEN.clear()


def path_template(url: str) -> str:
    """The page's location without host, port or ids: `/orders/17#edit` -> `/orders/{n}#edit`,
    `/session/B1kKc_2nMg` -> `/session/{id}`."""
    parsed = urlparse(url or "")
    path = _template(parsed.path or "/")
    return path + (f"#{_template(parsed.fragment)}" if parsed.fragment else "")


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


class _HeadingParser(HTMLParser):
    """The page's main heading: its first visible h1, else its first h2."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: dict[str, str] = {}
        self._open: str | None = None
        self._hidden_depth = 0
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: (v or "") for k, v in attrs}
        visible = values.get("data-hidden") != "true" and "hidden" not in values
        if tag in ("h1", "h2") and tag not in self.found and self._open is None and visible:
            self._open, self._buffer = tag, []

    def handle_endtag(self, tag: str) -> None:
        if tag == self._open:
            self.found[tag] = " ".join("".join(self._buffer).split())
            self._open = None

    def handle_data(self, data: str) -> None:
        if self._open:
            self._buffer.append(data)


def main_heading(html: str) -> str:
    """Normalised main heading (lower case, digits as `{n}`, at most 48 characters).

    Within one application it separates views that share a URL (single-page apps put the
    review, the outcome and a list on one address). It is part of a cell's identity only,
    which is per application; the learner's keys use page kinds, never headings.
    """
    parser = _HeadingParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - unreadable markup has no heading
        return ""
    text = parser.found.get("h1") or parser.found.get("h2") or ""
    return _NUMBER.sub("{n}", text.lower())[:48]


@dataclass
class Toggle:
    """A checkbox or radio button as the page currently shows it."""

    dom_id: str
    name: str
    kind: str  # "checkbox" | "radio"
    checked: bool


class _ToggleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.toggles: list[Toggle] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: (v or "") for k, v in attrs}
        kind = values.get("type", "").lower()
        if tag == "input" and kind in ("checkbox", "radio") and values.get("data-hidden") != "true":
            self.toggles.append(
                Toggle(values.get("id", ""), values.get("name", ""), kind, "checked" in values)
            )


_CHROME_TAGS = frozenset({"nav", "header", "footer"})
_CHROME_ROLES = frozenset({"navigation", "banner", "contentinfo"})


class _ChromeParser(HTMLParser):
    """Ids of the elements inside the site's navigation, header or footer."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self._depth = 0  # >0 while inside chrome

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: (v or "") for k, v in attrs}
        opens = tag in _CHROME_TAGS or values.get("role") in _CHROME_ROLES
        if (self._depth or opens) and values.get("id"):
            self.ids.add(values["id"])
        if tag in _VOID:
            return
        if self._depth or opens:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._depth and tag not in _VOID:
            self._depth -= 1


def chrome_ids(html: str) -> frozenset[str]:
    parser = _ChromeParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - unreadable markup has no chrome
        return frozenset()
    return frozenset(parser.ids)


class _SubmitParser(HTMLParser):
    """Ids of the controls that submit a form: a button of type submit (the default) or an
    input of type submit or image, inside a form or naming one with `form=`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self._forms = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: (v or "") for k, v in attrs}
        if tag == "form":
            self._forms += 1
            return
        kind = values.get("type", "").lower()
        submits = (tag == "button" and kind in ("", "submit")) or (
            tag == "input" and kind in ("submit", "image")
        )
        if submits and (self._forms or values.get("form")) and values.get("id"):
            self.ids.add(values["id"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._forms:
            self._forms -= 1


def submit_ids(html: str) -> frozenset[str]:
    parser = _SubmitParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - unreadable markup submits nothing
        return frozenset()
    return frozenset(parser.ids)


def toggles_of(html: str) -> list[Toggle]:
    parser = _ToggleParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - unreadable markup has no toggles
        return []
    return parser.toggles


def text_fields(html: str) -> dict[str, bool]:
    """Visible text-entry fields (inputs and textareas, not selects or toggles) -> filled."""
    form = read_form_state(html)
    seen: set[int] = set()
    out: dict[str, bool] = {}
    for table in (form.by_id, form.by_name):
        for name, control in table.items():
            if control.hidden or control.kind != "text" or id(control) in seen:
                continue
            seen.add(id(control))
            out[name] = bool(control.value.strip())
    return out


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


_LINK_SUBMIT_CUES = ("checkout", "continue", "proceed", "next", "review")


def _has_cue(label: str, cue: str) -> bool:
    """Cue at the start of a word, any ending: catches "edits", "saving", "reviewed".

    Whole-word matching (tried 2026-10-07) lost those inflections and cut fault discovery on
    four development applications; the shop's "Store" link, which motivated it, is handled
    by the rule that a link is a submission only with a progression cue.
    """
    return re.search(rf"(?<![a-z]){re.escape(cue)}", label) is not None


def _dom_id(identity: str) -> str:
    return identity[5:-2] if identity.startswith('[id="') and identity.endswith('"]') else ""


def deterministic_labels(
    url: str, html: str, specs: list[ActionSpec], *, form_submit: bool = False
) -> PageLabels:
    """Roles from English cues; with `form_submit`, a button that submits its form and has
    no cue ("Use voucher", "Go") is a submit control: a markup fact, not a word."""
    facts = page_facts(specs, html)
    controls = facts.controls
    labels = PageLabels(key="", kind="other", model="deterministic", source="deterministic")
    labels.kind = facts.kind or ("home" if path_template(url) in ("/", "/index.html") else "other")
    chrome = chrome_ids(html)
    submitting = submit_ids(html) if form_submit else frozenset()
    for control in controls:
        if control.kind == "field":
            labels.meanings[control.identity] = control.deterministic or "other"
            labels.deterministic.append(control.identity)
            continue
        text = control.line.lower()
        is_link = text.startswith("link")
        label = control.line.split('"')[1].lower() if '"' in control.line else ""
        role = next((r for r, cues in _ROLE_CUES if any(_has_cue(label, cue) for cue in cues)), "")
        if not role and not is_link and _dom_id(control.identity) in submitting:
            role = "submit"
        # A link is a submission only when it moves a task on ("Continue to checkout");
        # a link reading "Store" or "Save" is navigation.
        if is_link and role == "submit" and not any(_has_cue(label, c) for c in _LINK_SUBMIT_CUES):
            role = "navigate"
        # Site navigation, header and footer are on every page: whatever a control there
        # says ("Express checkout", "Book now"), it starts a journey, it does not submit
        # the one under way.
        if role == "submit" and _dom_id(control.identity) in chrome:
            role = "navigate"
        if not role:
            role = "navigate" if is_link else "other"
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
    heading: str = ""
    html: str = ""

    @property
    def cell(self) -> str:
        """Coarse on purpose: a page's form is empty, partly or fully filled, never the exact
        combination of fields and toggles (which made one development app 150-225 cells).

        It does record whether the page offers any submit action: an empty basket and a
        filled one share a page, a heading and no fields, but only the second can go to
        checkout, and a return to "the basket" must know which it is returning to. Only
        whether, not which: recording the exact set split account_hub's pages by every
        inline editor opened (about 20 cells -> 50) and halved its fault discovery.
        """
        alert = "!" if self.alert else ""
        offers = "s" if self.controls_with_role("submit") else ""
        kind, path = self.labels.kind, path_template(self.url)
        return f"{kind}|{path}|{self.heading}|{self.fill_level}|{alert}|{offers}"

    @property
    def fill_level(self) -> str:
        """'' with no text fields; otherwise 'empty', 'partial' or 'full' over text fields."""
        text = text_fields(self.html)
        if not text:
            return ""
        filled = sum(text.values())
        return "empty" if filled == 0 else "full" if filled == len(text) else "partial"

    def toggles(self) -> list[Toggle]:
        return toggles_of(self.html)

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


def observe(
    info: dict, labeller=None, *, context: str = "", form_submit: bool = False
) -> PageView:
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
        labels = deterministic_labels(url, html, specs, form_submit=form_submit)
    return PageView(
        url,
        labels,
        form_signature(html),
        shows_alert(html),
        specs=specs,
        heading=main_heading(html),
        html=html,
    )
