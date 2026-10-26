"""What a page shows, in a form an LLM can reason over and a rule can point at.

Every element is named by a CSS selector taken from the real page (an id, a `data-test`
attribute, or a tag with its classes), so a proposed rule can only refer to things that
exist. Parsed pages are cached by content, because rules re-read the same page many times.
"""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_IDENT = re.compile(r"^[A-Za-z_][\w-]*$")
_ID_SEGMENT = re.compile(r"/\d+(?=/|$)|/[0-9a-f]{8,}(?=/|$)|/[0-9A-HJKMNP-TV-Z]{20,}(?=/|$)")


@lru_cache(maxsize=512)
def soup_of(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return soup


def path_of(url: str) -> str:
    parts = urlsplit(url or "")
    path = parts.path or "/"
    if parts.fragment.startswith("/"):  # hash-routed single-page apps
        path = parts.fragment.split("?", 1)[0]
    return path


def template_of(url: str) -> str:
    """The page's path with ids replaced, so `/product/12` and `/product/7` are one page."""
    return _ID_SEGMENT.sub("/{id}", path_of(url))


def number_in(text: str) -> float | None:
    match = _NUMBER.search((text or "").replace("−", "-"))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def text_of(el: Tag) -> str:
    if el.name in ("input", "textarea"):
        return (el.get("value") or "").strip()
    if el.name == "select":
        chosen = el.find("option", selected=True) or el.find("option")
        return chosen.get_text(" ", strip=True) if chosen else ""
    return " ".join(el.get_text(" ", strip=True).split())


def select_all(html: str, selector: str) -> list[Tag]:
    try:
        return soup_of(html).select(selector)
    except Exception:  # noqa: BLE001 - a malformed selector selects nothing
        return []


def visible_text(html: str, limit: int = 4000) -> str:
    return " ".join(soup_of(html).get_text(" ", strip=True).split())[:limit]


def selector_for(el: Tag) -> str | None:
    if el.get("id") and _IDENT.match(el["id"]):
        return f"#{el['id']}"
    for attr in ("data-test", "data-testid", "data-test-id", "data-cy"):
        if el.get(attr):
            return f'[{attr}="{el[attr]}"]'
    if el.get("name") and el.name in ("input", "select", "textarea"):
        return f'{el.name}[name="{el["name"]}"]'
    classes = [c for c in el.get("class") or [] if _IDENT.match(c)][:2]
    if classes:
        return el.name + "".join(f".{c}" for c in classes)
    return None


def snapshot(url: str, html: str, *, max_elements: int = 45) -> dict:
    """The page as the rule writer sees it: text, values, lists, inputs, controls."""
    soup = soup_of(html)
    seen: dict[str, dict] = {}
    for el in soup.find_all(True):
        if el.name in ("html", "body", "head", "form", "main", "section", "header", "footer", "nav", "ul", "ol", "table", "tbody"):
            continue
        sel = selector_for(el)
        if not sel:
            continue
        text = text_of(el)
        if not text or len(text) > 90 or len(el.find_all(True)) > 6:
            continue
        has_value = bool(_NUMBER.search(text)) or el.name in ("h1", "h2", "h3") or bool(el.get("id") or el.get("data-test"))
        if not has_value:
            continue
        entry = seen.setdefault(sel, {"selector": sel, "count": 0, "samples": []})
        entry["count"] += 1
        if len(entry["samples"]) < 3 and text not in entry["samples"]:
            entry["samples"].append(text)
    elements = sorted(seen.values(), key=lambda e: (-(e["count"] > 1), e["selector"]))[:max_elements]
    inputs = []
    for el in soup.find_all(["input", "textarea", "select"]):
        if el.get("type") in ("hidden", "submit", "button", "checkbox", "radio"):
            continue
        label = ""
        if el.get("id"):
            lab = soup.find("label", attrs={"for": el["id"]})
            label = lab.get_text(" ", strip=True) if lab else ""
        entry = {"selector": selector_for(el), "kind": el.name if el.name != "input" else (el.get("type") or "text"),
                 "label": label or el.get("aria-label") or el.get("placeholder") or el.get("name") or el.get("id") or ""}
        if el.name == "select":
            entry["options"] = [{"value": o.get("value", ""), "text": o.get_text(" ", strip=True)}
                                for o in el.find_all("option")][:12]
        inputs.append(entry)
    controls = []
    for el in soup.find_all(["button", "a"]):
        label = " ".join(el.get_text(" ", strip=True).split()) or el.get("aria-label") or ""
        if label and label not in controls:
            controls.append(label[:50])
    title = soup.find(["h1", "h2"]) or soup.find("title")
    return {"page": template_of(url), "title": title.get_text(" ", strip=True)[:80] if title else "",
            "text": visible_text(html, 700), "elements": elements, "inputs": inputs[:20],
            "controls": controls[:30]}
