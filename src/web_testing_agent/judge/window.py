"""Turns captured trace records into the view the judge reasons over.

Two constraints shape everything here.

**Cross-step evidence.** Five of the seven llm_required bugs in the answer key are
`cross_step`: a dropped field is only visible by comparing what was typed against what
the confirmation echoes, and a preference that fails to persist needs check, save and
reload. A single transition cannot contain that evidence at all, so the window carries
`WINDOW_STEPS` transitions and the judge is asked about the last one in context.

**Raw HTML is the wrong input.** Six steps of before-and-after markup is both far too
large and mostly irrelevant — the judge does not need the stylesheet link, it needs to
know that `darkmode` went from `on` to `off` across a reload. So each page is reduced
to its behavioural surface (title, controls and their occupancy, hidden elements,
links, visible text) and consecutive pages are rendered as a *diff*. A dead control
then shows up as the conspicuous absence of any change, which is exactly the signal
BUG-02 turns on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from html.parser import HTMLParser

from ..annotation.trace import StepRecord
from ..perception.normalization import extract_structure

# The longest repro in the answer key (signup: nav, 4 fields, submit) is six steps.
WINDOW_STEPS = 6

_TEXT_LINE_MAX = 24
_TEXT_CHARS_MAX = 160
_LIST_MAX = 20
# Text diffs are the largest thing in the prompt by a wide margin, and the tail of a
# long diff is almost always boilerplate (nav labels, footers) rather than the change
# that matters. Capped tighter than the structural lists, which are short already.
_DIFF_MAX = 12
_SKIP_TEXT_TAGS = {"script", "style", "noscript", "template"}
_HIDDEN_TAG = re.compile(r"<(?P<tag>a|button|input|select|textarea)\b[^>]*\bdata-hidden=\"true\"[^>]*>")
# An attribute name starts where a hyphen or word character does *not* precede it. `\b`
# is not enough: it matches inside `data-id`, because the boundary between `-` and `i`
# is a word boundary, so `data-id="x"` was read as the element's own `id`. Every real
# framework emits `data-*` on interactive controls, so this fired constantly on Gitea.
_ATTR_START = r"(?<![-\w])"
_ID_ATTR = re.compile(rf"{_ATTR_START}id=\"([^\"]*)\"")
_TEXT_ATTR = re.compile(rf"{_ATTR_START}(?:value|name)=\"([^\"]*)\"")
_NAME_ATTR = re.compile(rf"{_ATTR_START}name=\"([^\"]*)\"")
_VALUE_ATTR = re.compile(rf"{_ATTR_START}value=\"([^\"]*)\"")
_INPUT_TAG = re.compile(r"<(?:input|textarea|select)\b[^>]*>")
# A validation bug is "the app accepts what it declared it would reject", so the
# declaration is half the evidence. Without it the judge sees `age: empty -> filled`
# and has no way to know 999999999 violates anything (BUG-05).
_CONSTRAINT_ATTRS = ("type", "min", "max", "minlength", "maxlength", "pattern", "required", "step")
# Matched with both ends anchored, because a bare-name search reported constraints that
# were never declared — and a false declaration is worse here than a missing one, since
# "the app accepts what it declared it would reject" is the whole verdict this feeds.
# Three distinct ways the earlier `\b{attr}(?:="([^"]*)")?` form invented one:
#   * `min` is a prefix of `minlength`, so `minlength="3"` also emitted a bare `min`
#   * the optional value group let *any* occurrence match, so `class="max-w-full"`
#     emitted a bare `max` — 16 times across the 187 captured pages
#   * `\b` matches after a hyphen, so `data-type="custom"` was rendered as `type=custom`
# The trailing branch is either a real `="value"` or a lookahead proving the attribute
# ended there, which is what a valueless boolean like `required` actually looks like.
_CONSTRAINT_PATTERNS = {
    attr: re.compile(
        rf"{_ATTR_START}{attr}"
        rf"(?:\s*=\s*(?:\"(?P<dq>[^\"]*)\"|'(?P<sq>[^']*)'|(?P<uq>[^\s\"'>]+))|(?=[\s/>]))"
    )
    for attr in _CONSTRAINT_ATTRS
}
# Framework-generated element ids carry no meaning and differ between renders. Gitea
# alone puts 20 `_aria_auto_id_N` menu entries in the hidden list of every page, which
# fills the whole display budget with the fact that a dropdown is closed and would
# crowd out the one genuinely hidden element a ui_regression depends on.
_AUTO_ID = re.compile(
    r"^(?:_aria_auto_id_\d+|:r[0-9a-z]+:|headlessui-[\w-]+|radix-[\w-]+|[0-9a-f]{16,})$"
)


class _TextExtractor(HTMLParser):
    """Visible text only: script bodies and stylesheets are not what a user sees."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in _SKIP_TEXT_TAGS:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TEXT_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = " ".join(data.split())
        if text:
            self.lines.append(text[:_TEXT_CHARS_MAX])


@dataclass(slots=True)
class PageView:
    """The behavioural surface of one page — what a tester would actually look at."""

    url: str = ""
    title: str = ""
    headings: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    hidden: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    constraints: dict[str, str] = field(default_factory=dict)
    # Links a user could actually follow right now. `links` includes hidden ones, so
    # reporting that as "still reachable" would assert the opposite of the truth.
    visible_links: list[str] = field(default_factory=list)
    # Current contents of each control. `fields` carries occupancy only, which is the
    # right granularity for state identity but hides a value changing from one string
    # to another — the common case when an explorer retypes a filled field.
    values: dict[str, str] = field(default_factory=dict)


def _constraints(html: str) -> dict[str, str]:
    """What each control *declares* about acceptable input."""
    declared: dict[str, str] = {}
    for match in _INPUT_TAG.finditer(html):
        tag = match.group(0)
        ident = _ID_ATTR.search(tag)
        name = _NAME_ATTR.search(tag)
        key = (name or ident).group(1) if (name or ident) else None
        if not key:
            continue
        parts = []
        for attr, pattern in _CONSTRAINT_PATTERNS.items():
            found = pattern.search(tag)
            if found is None:
                continue
            value = found.group("dq") or found.group("sq") or found.group("uq")
            parts.append(f"{attr}={value}" if value else attr)
        if parts:
            declared[key] = " ".join(parts)
    return declared


_ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.DOTALL | re.IGNORECASE)
_HREF_ATTR = re.compile(r'\bhref="([^"]*)"')
_TAG_STRIP = re.compile(r"<[^>]+>")


def _visible_links(html: str) -> list[str]:
    """Destinations a user could reach right now, excluding anything not rendered."""
    found: list[str] = []
    for match in _ANCHOR.finditer(html):
        attrs, inner = match.group(1), match.group(2)
        if "data-hidden" in attrs:
            continue
        href = _HREF_ATTR.search(attrs)
        if not href:
            continue
        label = " ".join(_TAG_STRIP.sub("", inner).split())[:40]
        found.append(f"{label or href.group(1)} -> {href.group(1)}")
    return found[:_LIST_MAX]


_VALUE_CHARS_MAX = 60
# URLs get a far more generous budget than form values, because a query string is
# often the evidence itself — BUG-01 is visible precisely as `?username=…&age=…` with
# no `email` parameter. Only a pathological URL (an explorer typing 2,000 characters
# into a field that ends up in a GET) should ever be shortened.
_URL_CHARS_MAX = 200


def _elide(value: str, limit: int = _VALUE_CHARS_MAX) -> str:
    """Shorten a value for display, saying so explicitly.

    A silent elision is indistinguishable from the application truncating the input,
    and the judge has no way to tell which it is looking at. Measured: an explorer
    typed a 400-character value, the renderer showed 60 of them, and the judge reported
    `broken_flow` — "the application silently truncates the user input, losing data
    without any error or warning" — about this renderer rather than the application.
    """
    return value if len(value) <= limit else f"{value[:limit]}…[+{len(value) - limit} chars]"


def _field_values(html: str) -> dict[str, str]:
    """What each control currently contains, truncated.

    Passwords never reach here: the environment excludes them from the serialized
    markup at capture time, since observations are written to disk and sent to a model.
    """
    values: dict[str, str] = {}
    for match in _INPUT_TAG.finditer(html):
        tag = match.group(0)
        name = _NAME_ATTR.search(tag) or _ID_ATTR.search(tag)
        value = _VALUE_ATTR.search(tag)
        if name and value:
            values[name.group(1)] = _elide(value.group(1))
    return values


# A step appears in up to WINDOW_STEPS windows and each side of every transition is
# re-derived on render, so one captured page is parsed many times over a corpus.
# Cheap on a 3 KB fixture page; on Gitea's 40 KB pages it dominated everything, making
# offline validation of 110 windows take 163 seconds. Pages are content-addressed, so
# identical HTML is genuinely the same view and caching it is exact, not approximate.
@lru_cache(maxsize=512)
def _page_view_cached(url: str, html: str) -> PageView:
    return _build_page_view(url, html)


def page_view(url: str, html: str) -> PageView:
    return _page_view_cached(url, html)


def _build_page_view(url: str, html: str) -> PageView:
    structure = extract_structure(html)
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
        extractor.close()
    except Exception:  # noqa: BLE001 - tolerant of malformed markup, same as extract_structure
        pass

    # Collapsed to (label -> count) so a closed menu reads as one fact rather than
    # twenty, leaving room for the hidden element that actually matters.
    hidden_counts: dict[str, int] = {}
    for match in _HIDDEN_TAG.finditer(html):
        tag = match.group(0)
        ident = _ID_ATTR.search(tag) or _TEXT_ATTR.search(tag)
        name = ident.group(1) if ident else ""
        if name and not _AUTO_ID.match(name):
            label = f"{match.group('tag')}#{name}"
        else:
            # Fall back to what the control *says* before giving up and calling it
            # unnamed. Collapsing every id-less element into "(unnamed) xN" was added to
            # stop Gitea's 37 `_aria_auto_id_N` menu entries flooding the budget, but it
            # also erased the identity of controls that have no id and plenty of meaning.
            # Measured against seeded ground truth: GITEA-03 hides Register and Sign In
            # at 375px, and the window said only "now hidden : a (unnamed) x41" — the
            # judge could not possibly know which links had gone.
            text = _hidden_label(html, match.start())
            label = f"{match.group('tag')} {text!r}" if text else f"{match.group('tag')} (unnamed)"
        hidden_counts[label] = hidden_counts.get(label, 0) + 1
    hidden = [
        label if count == 1 else f"{label} x{count}"
        for label, count in list(hidden_counts.items())[:_LIST_MAX]
    ]

    return PageView(
        url=url,
        title=structure.title,
        headings=structure.headings[:_LIST_MAX],
        fields=dict(structure.field_states[:_LIST_MAX]),
        hidden=hidden[:_LIST_MAX],
        links=[f"{text or href} -> {href}" for href, text in structure.anchors[:_LIST_MAX]],
        text=extractor.lines[:_TEXT_LINE_MAX],
        constraints=_constraints(html),
        visible_links=_visible_links(html),
        values=_field_values(html),
    )


_UNCLOSED_TAG = re.compile(r"<[^>]*$")
_HIDDEN_LABEL_MAX = 28
# How far past an opening tag to look for the control's own text. Long enough for a
# label wrapped in an icon span, short enough not to swallow a whole nav block.
_HIDDEN_LABEL_WINDOW = 400


def _hidden_label(html: str, start: int) -> str:
    """The visible text of a control that has no usable id.

    Read from the markup immediately following the opening tag, with any nested markup
    (Gitea wraps most nav labels in an `<svg>` icon) stripped out. Returns "" when the
    control genuinely has no text, so a decorative icon link stays "(unnamed)" rather
    than acquiring a misleading name.
    """
    segment = html[start : start + _HIDDEN_LABEL_WINDOW]
    body = segment.split(">", 1)[1] if ">" in segment else ""
    for closer in ("</a", "</button", "</span"):
        if closer in body:
            body = body.split(closer, 1)[0]
    # Drop a tag the fixed-size window cut in half before stripping complete ones:
    # `_TAG_STRIP` needs a closing `>`, so a severed `<path d="M2 2.75C2 1.784…` survives
    # it and is rendered as if it were the control's label. Gitea wraps every nav label
    # in an inline SVG, so this is the common case rather than an edge one.
    body = _UNCLOSED_TAG.sub("", body)
    text = " ".join(_TAG_STRIP.sub(" ", body).split())
    return text[:_HIDDEN_LABEL_MAX]


def _diff_lines(before: list[str], after: list[str]) -> tuple[list[str], list[str]]:
    """Set difference, order-preserving. Row reordering is not a behavioural change."""
    before_set, after_set = set(before), set(after)
    return (
        [line for line in after if line not in before_set][:_DIFF_MAX],
        [line for line in before if line not in after_set][:_DIFF_MAX],
    )


def _diff_fields(before: dict[str, str], after: dict[str, str]) -> list[str]:
    changes = []
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name, "absent"), after.get(name, "absent")
        if old != new:
            changes.append(f"{name}: {old} -> {new}")
    return changes[:_LIST_MAX]


_ERROR_CHARS_MAX = 160


def _action_error(error: str | None) -> str:
    """The reason an action failed, without the automation library's call log.

    Playwright appends a multi-line "Call log:" to every timeout, each line quoting a
    matched element's full opening tag. Measured on Gitea's Swagger page — hundreds of
    similar controls — a single failure contributed over 11,000 characters, 78% of the
    largest window in the corpus. The judge needs to know the action did not execute
    and roughly why; it is explicitly told not to blame the application for that.
    """
    if not error:
        return "action did not execute"
    first = error.strip().splitlines()[0].strip()
    return first[:_ERROR_CHARS_MAX] + ("…" if len(first) > _ERROR_CHARS_MAX else "")


def render_step(record: StepRecord, index: int) -> str:
    """One transition, rendered as what changed rather than as two page dumps."""
    before = page_view(record.before["path"], record.resolve_html("before"))
    after = page_view(record.after["path"], record.resolve_html("after"))

    action = record.action
    target = action.get("element") or action.get("selector") or ""
    params = action.get("params") or {}
    value = params.get("value") or params.get("option") or params.get("preset") or ""
    descriptor = f"{action.get('type', '?')}"
    if target:
        # Elided like any other displayed string: a Swagger endpoint label runs to
        # several lines, and an unmarked cut reads as the application mangling it.
        descriptor += f" on {_elide(' '.join(str(target).split()))!r}"
    if value:
        # Elided the same way as the rendered field value, so the two cannot appear to
        # disagree about length and imply a truncation the application never performed.
        descriptor += f" with value {_elide(str(value))!r}"

    lines = [f"STEP {index}", f"  action     : {descriptor}"]
    if action.get("selector"):
        lines.append(f"  selector   : {action['selector']}")
    blocked_target = record.exec.get("left_application")
    if not blocked_target and str(record.exec.get("error") or "").startswith("blocked:"):
        # Older traces predate the structured field; recover it from the message so
        # an existing corpus does not have to be recaptured to render correctly.
        match = re.search(r"\(([^)]+)\)\s*$", str(record.exec["error"]))
        blocked_target = match.group(1) if match else "another site"
    if blocked_target:
        # Reported distinctly from an ordinary failure. The environment refuses
        # navigations that leave the application and restores the previous page; if
        # that is rendered as "FAILED: ... did not navigate", the judge reasonably
        # concludes the *link* is broken. Measured on Gitea, where footer links point
        # off-site: 6 of 11 refusals were reported as broken_navigation, the single
        # largest false-positive class in the run — caused by this line's wording.
        lines.append(
            f"  BLOCKED    : the test harness refused this navigation to "
            f"{blocked_target} because it leaves the application under "
            f"test, and restored the previous page. This is a restriction of the test "
            f"setup, NOT application behaviour — the link itself may work correctly."
        )
    elif not record.exec.get("success"):
        lines.append(f"  FAILED     : {_action_error(record.exec.get('error'))}")

    lines.append(
        f"  url        : {_elide(before.url, _URL_CHARS_MAX)} -> {_elide(after.url, _URL_CHARS_MAX)}"
        if before.url != after.url
        else f"  url        : {_elide(after.url, _URL_CHARS_MAX)} (unchanged)"
    )
    if before.title != after.title:
        lines.append(f"  title      : {before.title!r} -> {after.title!r}")

    # A navigation replaces the whole document, so "everything on the old page is gone
    # and everything on the new page is new" is true by definition and says nothing.
    # Measured: `text gone` was 24% of all window characters and `text added` another
    # 14%, the two largest consumers in the prompt, and most of that volume sat on
    # navigation steps where it carried no information at all.
    navigated = before.url != after.url

    added, removed = _diff_lines(before.text, after.text)
    field_changes = _diff_fields(before.fields, after.fields)
    if navigated:
        # Controls ceasing to exist because the page they lived on was left is not a
        # change in form state. A field disappearing *without* navigating still is.
        field_changes = [change for change in field_changes if not change.endswith("-> absent")]
    if field_changes:
        lines.append(f"  form state : {'; '.join(field_changes)}")
    # Only for controls this step actually touched: the full declaration list is
    # already on the starting page, and repeating it every step is noise.
    touched = {name.split(":")[0] for name in field_changes}
    declared = [f"{name} declares [{after.constraints[name]}]"
                for name in sorted(touched) if name in after.constraints]
    if declared:
        lines.append(f"  constraints: {'; '.join(declared)}")

    # Occupancy alone cannot show a value being replaced, and an explorer retypes
    # filled fields constantly. Without this the window rendered *nothing* for such a
    # step and the no-change line below then fired, telling the judge the page was
    # byte-identical when it was not — measured as 21 false `dead_control` verdicts on
    # 39 TYPE windows of ordinary exploration.
    value_changes = [
        f"{name}: {before.values.get(name, '')!r} -> {after.values[name]!r}"
        for name in sorted(after.values)
        if before.values.get(name) != after.values[name]
    ][:_LIST_MAX]
    if value_changes and not navigated:
        lines.append(f"  field value: {'; '.join(value_changes)}")
    if navigated:
        # **The destination page's own text, not a diff against the page it replaced.**
        #
        # This line was previously `added` — the set difference — while being labelled
        # `page text`, and the comment here asserted that "on a navigation this is
        # simply the new page". It was not, and the gap deleted an entire bug class.
        #
        # Measured 2026-08-29 on a scripted DEEP-01 walk. `order-4.html` (the review
        # page) and `receipt.html` both render `Product:` / `Quantity:` / `Contact:`
        # and both echo the product and email correctly. Only the *quantity value*
        # differs — 42 on the review page, a hard-coded 1 on the receipt. Every shared
        # line was therefore suppressed as unchanged and the judge was handed:
        #
        #     page text  : ... | Order confirmed | Thank you. Your order has been
        #                  placed. | 1 | Back to home
        #
        # An orphaned `1` with no label attached. The one seeded defect on that fixture
        # was structurally unjudgeable, and both shipped prompt styles duly missed it
        # (compact flagged the step as `dead_control`; detailed returned is_bug=False).
        #
        # This generalises well beyond the fixture: **any bug of the form "a value
        # carried through a flow is echoed incorrectly on a later page" is invisible to
        # a text diff**, because the labels are shared between the two pages and get
        # differenced away, leaving values with nothing to identify them. That is the
        # whole `broken_flow` class. The toy site's cross-page BUG-01 survived only
        # because its evidence happened to sit in the URL rather than the page text.
        #
        # `render_page_context` already prints the *starting* page's full text, so
        # showing a diff for every subsequent page was also internally inconsistent —
        # the first page of a window and the third were described in different terms.
        #
        # `text gone` stays suppressed on navigation, which is where the measured
        # saving actually came from (24% of window characters against `added`'s 14%).
        # Bounded by `_TEXT_LINE_MAX`, applied when the PageView was built.
        if after.text:
            lines.append(f"  page text  : {' | '.join(after.text)}")
    else:
        if added:
            lines.append(f"  text added : {' | '.join(added)}")
        if removed:
            lines.append(f"  text gone  : {' | '.join(removed)}")

    became_hidden = [item for item in after.hidden if item not in before.hidden]
    became_shown = [item for item in before.hidden if item not in after.hidden]
    if became_hidden:
        lines.append(f"  now hidden : {', '.join(became_hidden)}")
        # The complement of "links still reachable", and the half that carries a
        # *positive* claim. Listing what survives lets a judge rule a hide harmless;
        # ruling it harmful requires noticing that something is missing from a list,
        # which is an inference from absence and one that models reliably fail to make.
        # Measured against seeded ground truth: GITEA-03 hides Register and Sign In at
        # 375px, the surviving-links line correctly stopped naming them, and the judge
        # still returned "not a bug" — it had never been told they were gone.
        # Suppressed across navigations, where every old link vanishes by definition.
        if not navigated:
            lost = [item for item in before.visible_links if item not in set(after.visible_links)]
            if lost:
                lines.append(f"  NO LONGER reachable: {'; '.join(lost[:_LIST_MAX])}")
        # Hiding something is only a defect relative to what is left. Responsive designs
        # routinely swap nav links for a menu, and a pure delta cannot distinguish that
        # from removing the last route to a flow — measured on BUG-08, where the judge
        # saw both signup links disappear and reasoned, reasonably, that "a responsive
        # design may hide or rearrange navigation elements on a smaller viewport".
        surviving = after.visible_links or ["(none)"]
        lines.append(f"  links still reachable: {'; '.join(surviving)}")
    if became_shown:
        lines.append(f"  now shown  : {', '.join(became_shown)}")

    # Console messages *and* uncaught exceptions. Playwright reports the two on
    # different events — `console` carries `console.error(...)`, `pageerror` carries a
    # genuine uncaught throw — and this renderer only ever read the first. The
    # deterministic trigger has always combined both (`detect_bug_signals`), so an
    # uncaught TypeError fired the trigger while being completely invisible to the
    # judge. Measured against seeded ground truth on Gitea: GITEA-05 is a real uncaught
    # TypeError that the triggers caught and the judge missed, because no line about it
    # ever reached the window. The most classic bug class there is was unjudgeable.
    console = list(record.after.get("console_errors") or []) + list(record.after.get("page_errors") or [])
    errors = [
        f"{event.get('status') or 'request failed'} {_elide(str(event.get('url') or ''), _URL_CHARS_MAX)}"
        for event in (record.after.get("network") or [])
        if event.get("failed") or (event.get("status") or 0) >= 400
    ]
    if console:
        # Browser console messages carry whole URLs, stack frames and minified
        # bundle text. Measured on Gitea: one console line was 5,937 characters,
        # the longest single line in the corpus, from a blocked third-party script.
        lines.append(f"  console    : {' | '.join(_action_error(c) for c in console[:5])}")
    if errors:
        lines.append(f"  http errors: {', '.join(errors[:5])}")
    if not record.settled:
        lines.append(f"  NOT SETTLED: the page was still mutating after {record.load_duration_s:.1f}s")

    rendered_nothing = not (
        added or removed or field_changes or value_changes or became_hidden or became_shown
    )
    # A `target="_blank"` link does not change the page it was clicked on — that is what
    # it is for. Whether the harness ends up following the new tab is a race:
    # `wait_settled()` returns once the *current* page stops mutating, which after such
    # a click is almost immediately, sometimes before Chromium has created the tab. Lose
    # the race and the env records a successful click that changed nothing, which the
    # no-change line below then reports as the strongest bug signal in the prompt.
    #
    # Measured on Gitea's landing page: clicks on `code.gitea.io/gitea`, `packaged`,
    # `run the binary` and `Powered by Gitea` each drew a 90-100%-confidence
    # `broken_navigation` verdict. None of those links is broken. This is the same shape
    # as the two defects already fixed here — a harness refusal rendered as a failure,
    # and BLOCKED emitted alongside NO OBSERVABLE CHANGE — and it is the third time the
    # window has asserted something about the application that was really a fact about
    # the harness.
    # `opened_offsite_tab` is the env stating that a tab really was opened and discarded;
    # `opens_new_tab` is the markup saying one was meant to be. The first is preferred
    # when present because it is an observation rather than an inference, but the second
    # still has to work: whether the harness ever sees the tab is the race described
    # below, and on the losing side there is nothing to observe.
    offsite_tab = str(record.exec.get("opened_offsite_tab") or "")
    opens_new_tab = bool(offsite_tab) or bool(params.get("opens_new_tab"))
    if opens_new_tab and rendered_nothing and not navigated and not blocked_target:
        destination = offsite_tab or str(params.get("href") or "another page")
        lines.append(
            f"  NEW TAB    : this link opens in a separate tab ({_elide(destination, _URL_CHARS_MAX)}). "
            f"The page it was clicked from is not supposed to change, and the test harness "
            f"does not follow the new tab. Nothing is known about where it leads — this is "
            f"NOT a dead control and NOT a broken link."
        )
    # A blocked step ends on the page it started on because the harness put it back,
    # not because the application did nothing. Emitting the no-change line here states
    # the strongest bug signal in the prompt about an action that never ran: measured
    # on Gitea, external links labelled "Docker", "packaged" and "Powered by Gitea"
    # drew confident dead_control and broken_navigation verdicts from exactly this.
    if rendered_nothing and not navigated and not blocked_target and not opens_new_tab:
        # Stated positively rather than left as an absence: "nothing happened" is the
        # entire signal for a dead control, and an LLM reading a list of headings will
        # not reliably notice that two of them were identical.
        #
        # But byte-identity is claimed only when the *document* really is identical —
        # `record.changed["html"]` is a hash comparison — never merely because this
        # renderer had nothing to say. Inferring it from an empty rendered diff is how
        # a rendering gap came to manufacture the strongest signal in the prompt:
        # measured as 21 confident dead-control verdicts on 39 TYPE windows of ordinary
        # exploration, on pages that had in fact changed.
        if record.changed.get("html"):
            # Deliberately not phrased as an absence. The document demonstrably
            # changed, so this is a limit of the summary rather than evidence of a
            # dead control, and saying "no observable change" here invites exactly
            # that misreading.
            lines.append(
                "  effect     : the page DID change, but not in the text, form or "
                "layout fields summarized here (this is a limit of this summary, "
                "not evidence that nothing happened)"
            )
        else:
            lines.append(
                "  effect     : NO OBSERVABLE CHANGE — the page is byte-identical after this action"
            )
    return "\n".join(lines)


def render_page_context(record: StepRecord) -> str:
    """The state the window opens in, so the first transition has somewhere to start."""
    view = page_view(record.before["path"], record.resolve_html("before"))
    lines = [f"STARTING PAGE: {view.url}", f"  title      : {view.title}"]
    if view.headings:
        lines.append(f"  headings   : {' | '.join(view.headings[:6])}")
    if view.fields:
        lines.append(f"  form state : {', '.join(f'{k}={v}' for k, v in view.fields.items())}")
    if view.constraints:
        lines.append(
            "  declared   : "
            + "; ".join(f"{k} [{v}]" for k, v in list(view.constraints.items())[:_LIST_MAX])
        )
    if view.links:
        lines.append(f"  links      : {'; '.join(view.links[:8])}")
    if view.hidden:
        lines.append(f"  hidden     : {', '.join(view.hidden)}")
    if view.text:
        lines.append(f"  text       : {' | '.join(view.text[:8])}")
    return "\n".join(lines)


@dataclass(slots=True)
class JudgeWindow:
    """`records[-1]` is the step under judgment; the rest is the context for it."""

    records: list[StepRecord]
    episode: int
    focus_global_step: int

    @property
    def focus(self) -> StepRecord:
        return self.records[-1]

    def render(self) -> str:
        body = [render_page_context(self.records[0]), ""]
        for offset, record in enumerate(self.records, start=1):
            body.append(render_step(record, offset))
            body.append("")
        body.append(f"Judge STEP {len(self.records)}.")
        return "\n".join(body)


def build_windows(
    records: list[StepRecord],
    window_steps: int = WINDOW_STEPS,
    skip_idle: bool = True,
) -> list[JudgeWindow]:
    """Slide a window over each episode, one judgment per step.

    Windows never span an episode boundary: a fresh browser context shares no state
    with the previous episode, so carrying records across would invite the judge to
    invent causal links that cannot exist.

    `skip_idle` drops steps where a NO_OP changed nothing and fired no trigger. That is
    a genuine no-evidence step, not a cheap filter on "the state didn't change" — a
    *deliberate* action that changes nothing is exactly BUG-02 and is always judged.
    """
    by_episode: dict[int, list[StepRecord]] = {}
    for record in records:
        by_episode.setdefault(record.episode, []).append(record)

    windows: list[JudgeWindow] = []
    for episode, episode_records in sorted(by_episode.items()):
        episode_records.sort(key=lambda r: r.step)
        for position, record in enumerate(episode_records):
            if skip_idle and _is_idle(record):
                continue
            start = max(0, position - window_steps + 1)
            windows.append(
                JudgeWindow(
                    records=episode_records[start : position + 1],
                    episode=episode,
                    focus_global_step=record.global_step,
                )
            )
    return windows


def _is_idle(record: StepRecord) -> bool:
    return (
        record.action.get("type") == "NO_OP"
        and not record.state_changed
        and not record.triggered
        and record.settled
    )
