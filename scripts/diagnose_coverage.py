"""Which client-side branches of a benchmark application stay uncovered, and where.

Opens the application in Chromium, optionally performs a scripted interaction, then reads
Istanbul's `window.__coverage__` and lists every branch with an uncovered arm by file and
line. Comparing two interactions shows which one reaches which code: the diagnosis behind
the action-layer work (DIAGNOSTIC, post-hoc on the benchmark applications).

    python scripts/diagnose_coverage.py http://127.0.0.1:18005/ --out runs/diag/retroboard-load.json
    python scripts/diagnose_coverage.py http://127.0.0.1:18005/ --steps steps.json --out ...

`--steps` is a JSON list of {"do": "click"|"fill"|"press"|"goto"|"wait"|"hover", "selector",
"value"|"key"|"url"|"ms"} executed in order; a failing step is reported and skipped.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def uncovered(coverage: dict) -> tuple[dict, list[dict]]:
    total = covered = 0
    missing = []
    for path, data in coverage.items():
        for bid, counts in data.get("b", {}).items():
            meta = data["branchMap"][bid]
            for arm, hits in enumerate(counts):
                total += 1
                if hits:
                    covered += 1
                    continue
                loc = (meta.get("locations") or [meta["loc"]])[min(arm, len(meta.get("locations") or [meta["loc"]]) - 1)]
                missing.append({"file": path.split("/")[-1], "path": path, "line": loc["start"]["line"],
                                "type": meta.get("type"), "arm": arm})
    return {"branches": total, "covered": covered, "rate": round(covered / max(total, 1), 4)}, missing


def run(url: str, steps: list[dict]) -> tuple[dict, list[dict], list[str]]:
    log = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        for step in steps:
            try:
                kind = step["do"]
                if kind == "goto":
                    page.goto(step["url"], wait_until="domcontentloaded")
                elif kind == "click":
                    page.locator(step["selector"]).first.click(timeout=5000)
                elif kind == "fill":
                    page.locator(step["selector"]).first.fill(step["value"], timeout=5000)
                elif kind == "press":
                    page.locator(step["selector"]).first.press(step["key"], timeout=5000)
                elif kind == "hover":
                    page.locator(step["selector"]).first.hover(timeout=5000)
                page.wait_for_timeout(int(step.get("ms", 1200)))
                log.append(f"ok   {step}")
            except Exception as exc:  # noqa: BLE001 - report and carry on
                log.append(f"FAIL {step}: {str(exc).splitlines()[0][:120]}")
        coverage = page.evaluate("() => window.__coverage__ || {}")
        browser.close()
    summary, missing = uncovered(coverage)
    return summary, missing, log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--steps", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    steps = json.loads(args.steps.read_text(encoding="utf-8")) if args.steps else []
    summary, missing, log = run(args.url, steps)
    for line in log:
        print(line)
    print(json.dumps(summary))
    by_file: dict[str, list[int]] = {}
    for m in missing:
        by_file.setdefault(m["file"], []).append(m["line"])
    for name, lines in sorted(by_file.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(lines):3d} uncovered  {name}: lines {sorted(set(lines))[:25]}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"summary": summary, "missing": missing, "log": log}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
