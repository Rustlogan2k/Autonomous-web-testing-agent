"""Builds the dynamic, page-specific Discrete(100) action space for WebFunctionalEnv."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from ..utils.logging import get_logger
from .input_values import generate_input_value
from .types import (
    MAX_ACTIONS,
    ActionSpec,
    ActionType,
    InputValueCategory,
    ScrollDirection,
    VIEWPORT_PRESETS,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = get_logger(__name__)

_NON_TEXT_INPUT_TYPES = {"checkbox", "radio", "hidden", "file", "button", "submit", "image", "reset"}

# The one selector every scanned element is indexed against. `_selector_for`'s
# positional fallback MUST use this same selector, otherwise `nth=` counts within a
# different match set than the scan did and silently resolves to the wrong element.
SCAN_SELECTOR = "input, textarea, select, button, a[href]"

@dataclass(frozen=True, slots=True)
class RegistryOptions:
    """What the action space offers beyond the original registry. Defaults are the original.

    Every figure published before the benchmark suite existed was measured through the
    default, so the default is frozen: one SELECT action per dropdown (its last option),
    and form controls named by their DOM `name`. Both limits are real restrictions on what
    an agent can do to an application, and the benchmark lifts them explicitly rather than
    by changing what the old experiments would do on a re-run.

    * `max_select_options` -- how many of a dropdown's options become actions. With one, a
      booking form offering four time slots can only ever be submitted with the last; a
      fault that depends on any other choice is unreachable by construction.
    * `capture_field_labels` -- attach the text a *person* reads beside a control (its
      `<label>`, `aria-label` or placeholder) as `params["label_text"]`. A field's DOM name
      (`qty`, `f_3`) is identity; its label (`How many?`) is meaning, and meaning is what
      a representation has to carry to a site it has never seen.
    * `disambiguate_shared_names` -- radios in a group share a `name` by design, so a
      name-based selector resolves every one of them to the first. With this set, a
      non-unique name falls back to the positional selector.
    * `custom_clickables` -- also offer controls the native scan cannot see: elements with
      a clickable ARIA role, an inline click handler, keyboard focus (`tabindex`), or a
      pointer cursor they set themselves. Single-page applications attach clicks to plain
      `div`s and `li`s; a learned detector of such controls is one of WebRLED's ablated
      gains [gu2025webrled], and this is its rule-based counterpart.
    * `avoid_labels` -- controls not to offer, matched against an element's visible text,
      id and name (case, spaces, hyphens and underscores ignored). A crawler's "don't
      click" list: with a sign-in macro, pressing *Sign out* only ends the session the
      macro opened, and the rest of the episode is spent on the login page.
    """

    max_select_options: int = 1
    capture_field_labels: bool = False
    disambiguate_shared_names: bool = False
    custom_clickables: bool = False
    avoid_labels: tuple[str, ...] = ()


#: The registry as it was for every pre-benchmark measurement.
LEGACY_OPTIONS = RegistryOptions()
#: The registry the benchmark suite and the product path use.
EXTENDED_OPTIONS = RegistryOptions(
    max_select_options=4, capture_field_labels=True, disambiguate_shared_names=True)


# Single JS round-trip: pull every attribute we need for action-building in one call
# instead of paying a Python<->browser round trip per element.
_SCAN_JS = """
els => els.map((el, i) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const visible = rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    // Visible in CSS terms is not the same as "the agent can currently see it":
    // an element 4000px down a long page is display:block but off-screen.
    const inViewport = visible
        && rect.bottom > 0 && rect.top < window.innerHeight
        && rect.right > 0 && rect.left < window.innerWidth;
    const options = el.tagName === 'SELECT'
        ? Array.from(el.options).map(o => o.value).filter(v => v !== '')
        : null;
    return {
        index: i,
        tag: el.tagName.toLowerCase(),
        type: (el.getAttribute('type') || '').toLowerCase(),
        id: el.id || null,
        name: el.getAttribute('name') || null,
        href: el.getAttribute('href') || null,
        // The DOM's own resolution of that href against the document base. Needed
        // because a link whose href resolves to the page it is already on cannot
        // change the URL, and reading the raw attribute cannot tell you that:
        // `href="support.html"` on support.html looks like an ordinary relative link.
        // Taken from the DOM rather than resolved in Python so a `<base href>` tag,
        // which changes what every relative href means, is honoured for free.
        resolvedHref: el.tagName === 'A' ? (el.href || '') : null,
        target: el.getAttribute('target') || null,
        // Marked when cut, so a label shortened here is not mistaken downstream for
        // the application mangling its own text. The judge sees these labels and has
        // no other way to tell a truncation from the real content.
        text: (() => {
            const raw = (el.innerText || el.value || '').trim();
            return raw.length > 60 ? raw.slice(0, 60) + '…' : raw;
        })(),
        disabled: !!el.disabled,
        visible: visible,
        inViewport: inViewport,
        options: options,
        // The text a person reads beside a form control, in the order a browser's own
        // accessible-name computation prefers it: an explicit aria-label, a <label for>,
        // a wrapping <label>, then the placeholder. Empty for links and buttons, whose
        // visible text is already their label.
        label: (() => {
            if (!['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) return '';
            const clean = t => (t || '').replace(/\\s+/g, ' ').trim().slice(0, 60);
            const aria = clean(el.getAttribute('aria-label'));
            if (aria) return aria;
            if (el.labels && el.labels.length) {
                const viaLabel = clean(el.labels[0].innerText);
                if (viaLabel) return viaLabel;
            }
            return clean(el.getAttribute('placeholder')) || clean(el.getAttribute('title'));
        })(),
    };
})
"""


#: Most custom clickables taken from one page, in document order.
MAX_CUSTOM_CLICKABLES = 30

_CUSTOM_SCAN_JS = """
(limit) => {
    const NATIVE = 'a[href], button, input, select, textarea, option, label, summary';
    const ROLES = new Set(['button', 'link', 'tab', 'menuitem', 'menuitemradio',
        'menuitemcheckbox', 'option', 'checkbox', 'radio', 'switch', 'treeitem']);
    const cssPath = el => {
        const parts = [];
        let node = el;
        while (node && node.nodeType === 1 && node !== document.body) {
            if (node.id) { parts.unshift(`[id="${node.id.replace(/"/g, '\\"')}"]`); break; }
            const tag = node.tagName.toLowerCase();
            let n = 1, sib = node;
            while ((sib = sib.previousElementSibling)) if (sib.tagName === node.tagName) n++;
            parts.unshift(`${tag}:nth-of-type(${n})`);
            node = node.parentElement;
        }
        if (!node || node === document.body) parts.unshift('body');
        return parts.join(' > ');
    };
    const out = [];
    for (const el of document.body.querySelectorAll('*')) {
        if (out.length >= limit) break;
        if (el.matches(NATIVE) || el.closest(NATIVE)) continue;
        const style = window.getComputedStyle(el);
        const role = (el.getAttribute('role') || '').toLowerCase();
        const tab = el.getAttribute('tabindex');
        const parent = el.parentElement;
        const ownPointer = style.cursor === 'pointer' && !(parent
            && window.getComputedStyle(parent).cursor === 'pointer');
        if (!(ROLES.has(role) || el.hasAttribute('onclick') || (tab !== null && tab !== '-1')
              || ownPointer)) continue;
        const rect = el.getBoundingClientRect();
        const visible = rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden'
            && style.display !== 'none';
        if (!visible) continue;
        const raw = (el.getAttribute('aria-label') || el.innerText || el.title || '').trim();
        out.push({
            tag: el.tagName.toLowerCase(), type: '', id: el.id || null, name: null,
            href: null, resolvedHref: null, target: null,
            text: raw.length > 60 ? raw.slice(0, 60) + '…' : raw,
            disabled: el.getAttribute('aria-disabled') === 'true', visible: true,
            inViewport: rect.bottom > 0 && rect.top < window.innerHeight
                && rect.right > 0 && rect.left < window.innerWidth,
            options: null, label: '', custom: true, cssPath: cssPath(el),
        });
    }
    return out;
}
"""


def scan_custom_clickables(page: "Page", limit: int = MAX_CUSTOM_CLICKABLES) -> list[dict]:
    """Clickable controls outside the native scan (see `RegistryOptions.custom_clickables`)."""
    try:
        return page.evaluate(_CUSTOM_SCAN_JS, limit)
    except Exception:  # noqa: BLE001 - navigation mid-scan, detached DOM, etc.
        logger.warning("Failed to scan custom clickables; offering none")
        return []


def scan_page_elements(page: "Page") -> list[dict]:
    """Single round-trip JS scan of every interactive element on the current page."""
    try:
        return page.eval_on_selector_all(SCAN_SELECTOR, _SCAN_JS)
    except Exception:  # noqa: BLE001 - navigation mid-scan, detached DOM, etc.
        logger.warning("Failed to scan page elements; returning empty element list")
        return []


def _escape_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _selector_for(el: dict, shared_names: frozenset[str] = frozenset()) -> str:
    """Build a Playwright selector that resolves to exactly the element that was scanned.

    `id`/`name` are preferred because they survive small DOM reshuffles between the
    scan and the action executing one step later. The positional fallback is indexed
    against `SCAN_SELECTOR` — the same match set `el["index"]` came from.

    `shared_names` lists names carried by more than one scanned element. A name-based
    selector for any of those resolves to the first, so they take the positional form.
    """
    if el.get("id"):
        # Attribute form, not `#id`: framework-generated ids routinely contain `:`, `.`
        # or `/`, which are CSS combinators and would produce an invalid `#`-selector.
        return f'[id="{_escape_attr(el["id"])}"]'
    if el.get("name") and el["name"] not in shared_names:
        return f'{el["tag"]}[name="{_escape_attr(el["name"])}"]'
    return f"{SCAN_SELECTOR} >> nth={el['index']}"


def _strip_fragment(url: str) -> str:
    return url.split("#", 1)[0]


def _is_self_link(el: dict, page_url: str) -> bool:
    """Whether this anchor points at the document it is already in.

    A nav bar that highlights the current section still links to it — `nav-support` on
    `support.html`, `sup-returns` on `support.html`, `nav-home` on `index.html`. Clicking
    one reloads the page and correctly leaves the URL unchanged, which is exactly the
    condition `detect_bug_signals` reads as `broken_navigation`. Measured on the
    deep-flow fixture: **every one** of the 18-20 `broken_navigation` firings per run,
    across every policy, came from a self-link, and the answer key's claim that no
    deterministic trigger can fire on that fixture was false because of it. That made
    `distinct_findings` there a count of how many distinct self-links a policy happened
    to click rather than a bug-discovery figure.

    Fragment-only hrefs are handled separately by `_is_navigational` and never reach
    here; a same-path link carrying a *different* query genuinely does change the URL
    and is deliberately not folded in.
    """
    resolved = (el.get("resolvedHref") or "").strip()
    href = (el.get("href") or "").strip()
    if not page_url or not (resolved or href):
        return False
    target = resolved or urljoin(page_url, href)
    return _strip_fragment(target) == _strip_fragment(page_url)


def _is_navigational(el: dict, page_url: str = "") -> bool:
    """Whether clicking this element is *expected* to change the URL.

    Only links and submit buttons are. Fragment (`#...`), `javascript:` and `mailto:`
    hrefs legitimately leave the URL path unchanged, a `target="_blank"` link opens a
    popup rather than navigating the current page, and a link resolving to the current
    document cannot change the URL by definition — none of those are broken links.
    """
    tag = el["tag"]
    el_type = el.get("type") or ""
    if tag == "input" and el_type == "submit":
        return True
    if tag == "button" and el_type == "submit":
        return True
    if tag != "a":
        return False
    if (el.get("target") or "").lower() == "_blank":
        return False
    href = (el.get("href") or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return False
    return not _is_self_link(el, page_url)


def _opens_new_tab(el: dict) -> bool:
    return el["tag"] == "a" and (el.get("target") or "").lower() == "_blank"


def _link_href(el: dict) -> str:
    """The raw href, recorded so a judge can be told where a click was meant to go.

    Load-bearing rather than diagnostic. Popup adoption is a race — `wait_settled()`
    returns as soon as the *current* page stops mutating, which for a `target="_blank"`
    click is almost immediately, sometimes before Chromium has created the new tab.
    When the race is lost the env sees a successful click that changed nothing, and the
    window then asserts the page is byte-identical: measured on Gitea's landing page,
    where clicks on `code.gitea.io/gitea`, `packaged` and `Powered by Gitea` each drew a
    90-100%-confidence `broken_navigation` verdict. Those links are not broken. The
    renderer needs the href to say what actually happened.
    """
    return (el.get("href") or "").strip()[:200] if el["tag"] == "a" else ""


def _fixed_action_specs() -> list[ActionSpec]:
    specs = [ActionSpec(index=0, action_type=ActionType.NO_OP, description="no-op")]
    i = 1
    for direction in (ScrollDirection.UP, ScrollDirection.DOWN):
        specs.append(
            ActionSpec(
                index=i,
                action_type=ActionType.SCROLL,
                params={"direction": direction.value, "amount": 600},
                description=f"scroll {direction.value}",
            )
        )
        i += 1
    for preset in VIEWPORT_PRESETS:
        specs.append(
            ActionSpec(
                index=i,
                action_type=ActionType.RESIZE_VIEWPORT,
                params={"preset": preset},
                description=f"resize viewport to {preset}",
            )
        )
        i += 1
    for action_type in (ActionType.BROWSER_BACK, ActionType.BROWSER_FORWARD, ActionType.REFRESH):
        specs.append(ActionSpec(index=i, action_type=action_type, description=action_type.value))
        i += 1
    return specs


def _select_choices(options: list[str], limit: int) -> list[str]:
    """Which of a dropdown's options become actions: the last, then the leading ones.

    The last option is always kept and listed first so that with `limit=1` this is exactly
    the original registry. Beyond that the leading options are added in document order,
    which is where an application puts its defaults and its commonest choices.
    """
    if limit <= 1 or len(options) <= 1:
        return options[-1:]
    chosen = [options[-1]]
    for option in options[:-1]:
        if len(chosen) >= limit:
            break
        if option not in chosen:
            chosen.append(option)
    return chosen


def _squash(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _avoided(el: dict, avoid: tuple[str, ...]) -> bool:
    """Whether `el` is one of the controls `RegistryOptions.avoid_labels` excludes."""
    if not avoid:
        return False
    names = {_squash(str(el.get(key) or "")) for key in ("text", "id", "name")} - {""}
    return any(_squash(word) in name for word in avoid for name in names)


def build_action_specs(elements: list[dict], page_url: str = "",
                       options: RegistryOptions = LEGACY_OPTIONS) -> list[ActionSpec]:
    """Turn raw scanned elements into a prioritized, budget-truncated list of ActionSpecs.

    Two orthogonal priorities decide what survives truncation:

    1. **Viewport first.** Elements currently on-screen are allocated before off-screen
       ones. On a long page the raw DOM order would otherwise let footer links crowd out
       the form the agent is looking at, and it makes SCROLL genuinely necessary to reach
       the rest of the page — which mirrors how a human explores.
    2. **Action kind.** Fixed page-independent actions, then CLICK on buttons/links
       (needed to progress any flow), then TYPE(valid_typical) so forms can actually be
       submitted, then SELECT and RAPID_CLICK, then the lower-priority TYPE boundary/
       edge-case variants fill whatever budget remains.

    Anything past MAX_ACTIONS is dropped; the env treats an out-of-range index as NO_OP.

    `page_url` is the document the scan came from. It is only consulted to decide
    whether a link points back at that same document (see `_is_self_link`); passing it
    empty preserves the pre-2026-08-27 behaviour, in which every non-fragment link was
    called navigational and a self-link therefore read as a broken one.

    `options` widens the action space; the default reproduces the original registry
    exactly. See `RegistryOptions`.
    """
    registry_options = options
    shared_names: frozenset[str] = frozenset()
    if registry_options.disambiguate_shared_names:
        counts = Counter(el["name"] for el in elements if el.get("name") and not el.get("id"))
        shared_names = frozenset(name for name, count in counts.items() if count > 1)

    def label_params(el: dict) -> dict:
        text = (el.get("label") or "").strip() if registry_options.capture_field_labels else ""
        return {"label_text": text} if text else {}

    fixed = _fixed_action_specs()
    budget = MAX_ACTIONS - len(fixed)

    clicks: list[ActionSpec] = []
    primary_types: list[ActionSpec] = []
    selects: list[ActionSpec] = []
    rapid_clicks: list[ActionSpec] = []
    secondary_types: list[ActionSpec] = []

    # Stable partition: on-screen elements keep their DOM order, then off-screen ones do.
    candidates = [el for el in elements if not el.get("disabled") and el.get("visible")
                  and not _avoided(el, registry_options.avoid_labels)]
    ordered_elements = [el for el in candidates if el.get("inViewport")] + [
        el for el in candidates if not el.get("inViewport")
    ]

    for el in ordered_elements:
        tag = el["tag"]
        el_type = el.get("type") or ""
        in_viewport = bool(el.get("inViewport"))

        native = tag in ("a", "button") or (tag == "input" and el_type in ("button", "submit"))
        is_clickable = native or bool(el.get("custom"))
        if is_clickable:
            selector = (f'[id="{_escape_attr(el["id"])}"]' if el.get("custom") and el.get("id")
                        else el["cssPath"] if el.get("custom") else _selector_for(el))
            label = el.get("text") or el.get("href") or el.get("name") or selector
            clicks.append(
                ActionSpec(
                    index=-1,
                    action_type=ActionType.CLICK,
                    selector=selector,
                    element_id=label,
                    params={
                        "navigational": _is_navigational(el, page_url),
                        "in_viewport": in_viewport,
                        "opens_new_tab": _opens_new_tab(el),
                        "href": _link_href(el),
                    },
                    description=f"click {label!r}",
                )
            )
            if tag == "button" or el_type == "submit":
                rapid_clicks.append(
                    ActionSpec(
                        index=-1,
                        action_type=ActionType.RAPID_CLICK,
                        selector=selector,
                        element_id=label,
                        params={"n": 5, "in_viewport": in_viewport},
                        description=f"rapid-click {label!r} x5",
                    )
                )
            continue

        if tag == "input" and el_type in ("checkbox", "radio"):
            selector = _selector_for(el, shared_names)
            label = el.get("name") or selector
            clicks.append(
                ActionSpec(
                    index=-1,
                    action_type=ActionType.CLICK,
                    selector=selector,
                    element_id=label,
                    params={"navigational": False, "in_viewport": in_viewport,
                            **label_params(el)},
                    description=f"toggle {label}",
                )
            )
            continue

        if tag == "select":
            choices = el.get("options") or []
            if not choices:
                continue
            selector = _selector_for(el, shared_names)
            for target_option in _select_choices(choices, registry_options.max_select_options):
                selects.append(
                    ActionSpec(
                        index=-1,
                        action_type=ActionType.SELECT,
                        selector=selector,
                        element_id=el.get("name") or selector,
                        params={"option": target_option, "in_viewport": in_viewport,
                                **label_params(el)},
                        description=f"select {target_option!r} in {selector}",
                    )
                )
            continue

        if tag in ("input", "textarea") and el_type not in _NON_TEXT_INPUT_TYPES:
            selector = _selector_for(el, shared_names)
            field_label = el.get("name") or el.get("id") or selector
            html_type = "textarea" if tag == "textarea" else (el_type or "text")
            for category, bucket in (
                (InputValueCategory.VALID_TYPICAL, primary_types),
                (InputValueCategory.BOUNDARY_MIN, secondary_types),
                (InputValueCategory.BOUNDARY_MAX, secondary_types),
                (InputValueCategory.EMPTY_STRING, secondary_types),
                (InputValueCategory.TYPE_MISMATCH, secondary_types),
            ):
                bucket.append(
                    ActionSpec(
                        index=-1,
                        action_type=ActionType.TYPE,
                        selector=selector,
                        element_id=field_label,
                        params={
                            "value": generate_input_value(html_type, category),
                            "category": category.value,
                            "in_viewport": in_viewport,
                            **label_params(el),
                        },
                        description=f"type {category.value} into {field_label!r}",
                    )
                )

    ordered = clicks + primary_types + selects + rapid_clicks + secondary_types
    dynamic = ordered[:budget]
    if len(ordered) > budget:
        dropped_onscreen = sum(1 for spec in ordered[budget:] if spec.params.get("in_viewport"))
        logger.debug(
            "Truncated action candidates from {} to budget {} ({} of the dropped were on-screen)",
            len(ordered),
            budget,
            dropped_onscreen,
        )

    return fixed + [replace(spec, index=len(fixed) + offset) for offset, spec in enumerate(dynamic)]
