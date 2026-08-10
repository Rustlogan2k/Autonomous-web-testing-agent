"""Builds the dynamic, page-specific Discrete(100) action space for WebFunctionalEnv."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

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
    };
})
"""


def scan_page_elements(page: "Page") -> list[dict]:
    """Single round-trip JS scan of every interactive element on the current page."""
    try:
        return page.eval_on_selector_all(SCAN_SELECTOR, _SCAN_JS)
    except Exception:  # noqa: BLE001 - navigation mid-scan, detached DOM, etc.
        logger.warning("Failed to scan page elements; returning empty element list")
        return []


def _escape_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _selector_for(el: dict) -> str:
    """Build a Playwright selector that resolves to exactly the element that was scanned.

    `id`/`name` are preferred because they survive small DOM reshuffles between the
    scan and the action executing one step later. The positional fallback is indexed
    against `SCAN_SELECTOR` — the same match set `el["index"]` came from.
    """
    if el.get("id"):
        # Attribute form, not `#id`: framework-generated ids routinely contain `:`, `.`
        # or `/`, which are CSS combinators and would produce an invalid `#`-selector.
        return f'[id="{_escape_attr(el["id"])}"]'
    if el.get("name"):
        return f'{el["tag"]}[name="{_escape_attr(el["name"])}"]'
    return f"{SCAN_SELECTOR} >> nth={el['index']}"


def _is_navigational(el: dict) -> bool:
    """Whether clicking this element is *expected* to change the URL.

    Only links and submit buttons are. Fragment (`#...`), `javascript:` and `mailto:`
    hrefs legitimately leave the URL path unchanged, and a `target="_blank"` link opens
    a popup rather than navigating the current page — none of those are broken links.
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
    return True


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


def build_action_specs(elements: list[dict]) -> list[ActionSpec]:
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
    """
    fixed = _fixed_action_specs()
    budget = MAX_ACTIONS - len(fixed)

    clicks: list[ActionSpec] = []
    primary_types: list[ActionSpec] = []
    selects: list[ActionSpec] = []
    rapid_clicks: list[ActionSpec] = []
    secondary_types: list[ActionSpec] = []

    # Stable partition: on-screen elements keep their DOM order, then off-screen ones do.
    candidates = [el for el in elements if not el.get("disabled") and el.get("visible")]
    ordered_elements = [el for el in candidates if el.get("inViewport")] + [
        el for el in candidates if not el.get("inViewport")
    ]

    for el in ordered_elements:
        tag = el["tag"]
        el_type = el.get("type") or ""
        in_viewport = bool(el.get("inViewport"))

        is_clickable = tag in ("a", "button") or (tag == "input" and el_type in ("button", "submit"))
        if is_clickable:
            selector = _selector_for(el)
            label = el.get("text") or el.get("href") or el.get("name") or selector
            clicks.append(
                ActionSpec(
                    index=-1,
                    action_type=ActionType.CLICK,
                    selector=selector,
                    element_id=label,
                    params={
                        "navigational": _is_navigational(el),
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
            selector = _selector_for(el)
            label = el.get("name") or selector
            clicks.append(
                ActionSpec(
                    index=-1,
                    action_type=ActionType.CLICK,
                    selector=selector,
                    element_id=label,
                    params={"navigational": False, "in_viewport": in_viewport},
                    description=f"toggle {label}",
                )
            )
            continue

        if tag == "select":
            options = el.get("options") or []
            if not options:
                continue
            selector = _selector_for(el)
            target_option = options[-1]
            selects.append(
                ActionSpec(
                    index=-1,
                    action_type=ActionType.SELECT,
                    selector=selector,
                    element_id=el.get("name") or selector,
                    params={"option": target_option, "in_viewport": in_viewport},
                    description=f"select {target_option!r} in {selector}",
                )
            )
            continue

        if tag in ("input", "textarea") and el_type not in _NON_TEXT_INPUT_TYPES:
            selector = _selector_for(el)
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
