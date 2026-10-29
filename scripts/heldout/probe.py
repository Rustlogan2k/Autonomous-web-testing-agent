"""What a held-out application looks like before anything tests it.

    python scripts/heldout/probe.py <url> <coverage url> <out dir>

Opens `url` in Chromium and keeps, for writing the application's entry in
configs/heldout_apps.json: the page after redirects (URL, title, HTML, screenshot), every
form control with its name, id, type and label, the buttons and links, and the JSON the
image's own coverage server returns before any interaction (its starting point).
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

_CONTROLS = """
() => [...document.querySelectorAll('input, select, textarea, button, a[href]')].map(el => ({
    tag: el.tagName.toLowerCase(), type: el.type || null, name: el.name || null, id: el.id || null,
    text: (el.innerText || el.value || '').trim().slice(0, 60),
    href: el.getAttribute('href'),
    label: (el.labels && el.labels[0] ? el.labels[0].innerText.trim() : (el.placeholder || '')).slice(0, 60),
}))
"""


def main() -> int:
    url, coverage_url, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(coverage_url, timeout=60) as response:  # noqa: S310 - local
            (out / "coverage_start.json").write_bytes(response.read())
    except Exception as exc:  # noqa: BLE001 - recorded, the page is still probed
        (out / "coverage_start.json").write_text(json.dumps({"error": str(exc)}), encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.goto(url, wait_until="networkidle", timeout=120_000)
        page.wait_for_timeout(3000)
        (out / "page.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(out / "page.png"), full_page=True)
        summary = {"requested": url, "final_url": page.url, "title": page.title(),
                   "controls": page.evaluate(_CONTROLS)}
        (out / "page.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
        browser.close()
    print(json.dumps({k: summary[k] for k in ("requested", "final_url", "title")}))
    for control in summary["controls"]:
        if control["tag"] in ("input", "select", "textarea", "button"):
            print(" ", control)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
