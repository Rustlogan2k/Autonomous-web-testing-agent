"""What a held-out application looks like before anything tests it.

    python scripts/heldout/probe.py <url> <coverage url> <out dir>
        [--click TEXT] [--login USER:PASSWORD ...]

Opens `url` in Chromium and keeps, for writing the application's entry in
configs/heldout_apps.json: the page after redirects (URL, title, HTML, screenshot), every
form control with its name, id, type and label, the buttons and links, and the JSON the
image's own coverage server returns before any interaction (its starting point).

`--click` presses a control by its text first and records the page again (`clicked.*`).
Each `--login` tries an account in a fresh browser: the field before the password field
gets the user, the password field the password, then Enter; the outcome (URL, title,
whether a password field is still shown, screenshot) goes to `login_<n>.*`.
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
    label: (el.labels && el.labels[0] ? el.labels[0].innerText.trim()
            : (el.placeholder || '')).slice(0, 60),
}))
"""


def _record(page, out: Path, name: str) -> dict:
    page.wait_for_timeout(3000)
    (out / f"{name}.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(out / f"{name}.png"), full_page=True)
    summary = {
        "final_url": page.url,
        "title": page.title(),
        "controls": page.evaluate(_CONTROLS),
        "password_field_shown": page.locator("input[type=password]:visible").count() > 0,
    }
    (out / f"{name}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def _try_login(browser, url: str, account: str, out: Path, name: str) -> dict:
    user, _, password = account.partition(":")
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    page.goto(url, wait_until="networkidle", timeout=120_000)
    secret = page.locator("input[type=password]:visible").first
    if not secret.count():
        return {"account": user, "error": "no password field"}
    fields = page.locator(
        "input:visible:not([type=password]):not([type=hidden]):not([type=checkbox])"
        ":not([type=submit]):not([type=radio])"
    )
    before = [
        i
        for i in range(fields.count())
        if fields.nth(i).evaluate(
            "(el, pw) => !!(el.compareDocumentPosition(pw) & 4)", secret.element_handle()
        )
    ]
    if before:
        fields.nth(before[-1]).fill(user)
    secret.fill(password)
    secret.press("Enter")
    page.wait_for_load_state("networkidle", timeout=60_000)
    summary = _record(page, out, name)
    page.close()
    return {
        "account": user,
        "final_url": summary["final_url"],
        "title": summary["title"],
        "password_field_shown": summary["password_field_shown"],
    }


def main() -> int:
    args = sys.argv[1:]
    url, coverage_url, out = args[0], args[1], Path(args[2])
    click = args[args.index("--click") + 1] if "--click" in args else None
    accounts = [args[i + 1] for i, a in enumerate(args) if a == "--login"]
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
        summary = {
            "requested": url,
            "final_url": page.url,
            "title": page.title(),
            "controls": page.evaluate(_CONTROLS),
        }
        (out / "page.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
        if click:
            page.get_by_text(click).first.click()
            clicked = _record(page, out, "clicked")
            print("after clicking", repr(click), clicked["final_url"])
            for control in clicked["controls"]:
                if control["tag"] in ("input", "select", "textarea", "button"):
                    print("   ", control)
        logins = [
            _try_login(browser, url, account, out, f"login_{n}")
            for n, account in enumerate(accounts)
        ]
        (out / "logins.json").write_text(json.dumps(logins, indent=1), encoding="utf-8")
        for outcome in logins:
            print("login", outcome)
        browser.close()
    print(json.dumps({k: summary[k] for k in ("requested", "final_url", "title")}))
    for control in summary["controls"]:
        if control["tag"] in ("input", "select", "textarea", "button"):
            print(" ", control)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
