"""Reproduce what ended every WebRLED run on Time Off: submitting "Import employees".

    python scripts/heldout/repro_timeoff_import.py <url> <coverage url> <out dir>

All five WebRLED runs on Time Off (held-out run 41013996747) ended the same way, 37-54 min
in: WebRLED opened /users/import/, ran its form action (fill the form's text fields, click
its submit button twice), and its next read of the image's coverage server
(:6969/coverage, served by the application's own process) got a body that was not JSON.
WebRLED does not catch that (src/webenv/config.py, get_cov_jacoco) and exited with status 1;
our wrapper's own read of the coverage server afterwards failed too.

This script does the same steps without any tester, in a fresh container, and records
whether the application and its coverage server still answer afterwards:

  1. register a company (the image starts with no account), which signs in;
  2. read the coverage server (it must answer before anything is imported);
  3. open /users/import/ and keep the page;
  4. "empty": fill the form's text fields, click its submit button twice, as WebRLED does;
  5. read the application and the coverage server again;
  6. if both still answer, "junk": the same with a non-CSV file attached.

Outputs in <out dir>: repro.json (each step's outcome), import_page.html/.png,
after_<variant>.html/.png. The workflow adds the container's log (container.log), where an
exception in the application's process would show.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

EMAIL = "katalyze.repro@example.com"
PASSWORD = "Katalyze-repro-1"

#: WebRLED's form action, as in its environment.py (form_action_normal): text-like fields of
#: the form get a value, then a submit control is clicked twice.
_FORM = """
(form) => {
  const fields = [...form.querySelectorAll('input, textarea')].filter(el =>
      !['hidden', 'submit', 'button', 'file', 'checkbox', 'radio'].includes(el.type));
  const submits = [...form.querySelectorAll('button, input[type=submit]')].filter(el =>
      (el.outerHTML || '').toLowerCase().includes('submit'));
  return {fields: fields.length, submits: submits.length,
          html: form.outerHTML.slice(0, 4000)};
}
"""


def probe_url(url: str, timeout: float = 30.0) -> dict:
    """Status and the start of the body of one GET, or the error."""
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - local app
            body = response.read()
            outcome = {"status": response.status, "bytes": len(body),
                       "body_start": body[:300].decode("utf-8", "replace")}
        try:
            json.loads(body)
            outcome["json"] = True
        except ValueError:
            outcome["json"] = False
    except urllib.error.HTTPError as exc:
        outcome = {"status": exc.code, "body_start": exc.read(300).decode("utf-8", "replace"),
                   "json": False}
    except Exception as exc:  # noqa: BLE001 - recorded: an unreachable server is a result
        outcome = {"status": None, "error": f"{type(exc).__name__}: {exc}", "json": False}
    outcome["seconds"] = round(time.monotonic() - started, 2)
    return outcome


def register(page, base: str) -> dict:
    page.goto(base.rstrip("/") + "/register/")
    page.wait_for_load_state("networkidle")
    filled = {}
    for field in page.locator("form input:visible").all():
        kind = (field.get_attribute("type") or "text").lower()
        name = field.get_attribute("name") or field.get_attribute("id") or "?"
        if kind in ("hidden", "submit", "button", "checkbox", "radio"):
            continue
        if kind == "password":
            value = PASSWORD
        elif kind == "email" or "email" in name.lower():
            value = EMAIL
        else:
            value = "Katalyze"
        field.fill(value)
        filled[name] = value if kind != "password" else "<password>"
    page.locator("form button[type=submit], form input[type=submit]").first.click()
    page.wait_for_load_state("networkidle")
    return {"filled": filled, "url_after": page.url}


def submit_import(page, out: Path, variant: str, attach: Path | None) -> dict:
    form = page.locator("form:has(input[type=file])").first
    shape = form.evaluate(_FORM)
    for field in form.locator("input:visible, textarea:visible").all():
        kind = (field.get_attribute("type") or "text").lower()
        if kind not in ("hidden", "submit", "button", "file", "checkbox", "radio"):
            field.fill("secret")
    if attach is not None:
        form.locator("input[type=file]").first.set_input_files(str(attach))
    submit = form.locator("button[type=submit], input[type=submit]").first
    clicks = []
    for _ in range(2):  # WebRLED clicks its submit action twice
        try:
            submit.click(timeout=5000)
            clicks.append("ok")
        except Exception as exc:  # noqa: BLE001 - the second click may find no button
            clicks.append(f"{type(exc).__name__}: {str(exc)[:200]}")
    page.wait_for_timeout(4000)
    try:
        (out / f"after_{variant}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(out / f"after_{variant}.png"), full_page=True)
    except Exception as exc:  # noqa: BLE001
        clicks.append(f"capture failed: {exc}")
    return {"form": {k: shape[k] for k in ("fields", "submits")}, "clicks": clicks,
            "url_after": page.url, "title_after": page.title()}


def main(base: str, coverage: str, out_dir: str) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    record: dict = {"base": base, "coverage": coverage}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        record["register"] = register(page, base)
        record["before"] = {"app": probe_url(base), "coverage": probe_url(coverage)}
        page.goto(base.rstrip("/") + "/users/import/")
        page.wait_for_load_state("networkidle")
        (out / "import_page.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(out / "import_page.png"), full_page=True)
        record["import_page_url"] = page.url
        junk = out / "not-a-csv.bin"
        junk.write_bytes(bytes(range(256)) * 8)
        for variant, attach in (("empty", None), ("junk", junk)):
            if variant == "junk" and not record["empty"]["after"]["coverage"]["json"]:
                break
            if variant == "junk":
                page.goto(base.rstrip("/") + "/users/import/")
                page.wait_for_load_state("networkidle")
            record[variant] = submit_import(page, out, variant, attach)
            time.sleep(3)
            record[variant]["after"] = {"app": probe_url(base), "coverage": probe_url(coverage)}
        browser.close()
    crashed = [v for v in ("empty", "junk")
               if v in record and not record[v]["after"]["coverage"]["json"]]
    record["verdict"] = (f"the coverage server stopped returning JSON after: {crashed}"
                         if crashed else "the application and coverage server kept answering")
    (out / "repro.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    print(json.dumps(record, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:4]))
