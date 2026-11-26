"""bWAPP's ground truth for the security study, from the authors' own bug list.

    python scripts/security/bwapp_truth.py bugs.txt > bwapp_vulnerabilities.json

bugs.txt ships in the bWAPP image (/app/bugs.txt: "<name>,<page>" per bug, grouped under
OWASP Top 10 headings). Every bug becomes one documented vulnerability on its page; a page
listed twice (bWAPP repeats some under A9) is one entry. Its CWE and whether OWASP ZAP's
default active rules cover its class come from the name, by the fixed table below (written
2026-11-26, before any scan of bWAPP). Classes outside the table are documented but not
detectable by either front end's scan (session management, access control, CSRF,
misconfiguration, cryptography, denial of service): they are kept in the list and counted
only in the "documented" total.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

#: (name pattern, CWEs, detectable by ZAP's default active rules). First match wins.
CLASSES: list[tuple[str, list[int], bool]] = [
    (r"Cross-Site Scripting|XSS", [79], True),
    (r"HTML Injection|iFrame Injection", [79, 80], True),
    (r"Drupal SQL Injection|SQL Injection", [89], True),
    (r"OS Command Injection|Shellshock", [78], True),
    (r"PHP Code Injection|PHP Eval|SQLiteManager PHP Code Injection", [94, 95], True),
    (r"Server-Side Includes", [97], True),
    (r"XML/XPath Injection", [643], True),
    (r"LDAP Injection", [90], True),
    (r"Directory Traversal|File Inclusion", [22, 98], True),
    (r"XML External Entity", [611], True),
    (r"HTTP Response Splitting", [113], True),
    (r"Unvalidated Redirects", [601], True),
]


def classify(name: str) -> tuple[list[int], bool]:
    for pattern, cwes, detectable in CLASSES:
        if re.search(pattern, name, flags=re.IGNORECASE):
            return cwes, detectable
    return [0], False


def build(text: str) -> list[dict]:
    seen: dict[str, dict] = {}
    for line in text.splitlines():
        if "," not in line:
            continue
        name, page = (part.strip() for part in line.rsplit(",", 1))
        if page == "portal.php" or not page.endswith((".php", ".php?")) and "?" not in page:
            continue
        path = "/" + page.split("?", 1)[0].lstrip("./")
        if path in seen:
            seen[path]["title"] += f" / {name}"
            continue
        cwes, detectable = classify(name)
        seen[path] = {"id": f"BW-{len(seen) + 1:03d}", "title": name, "cwe": cwes,
                      "paths": [path], "detectable": detectable}
    return list(seen.values())


def main() -> int:
    vulns = build(Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace"))
    print(json.dumps(vulns, indent=1))
    detectable = sum(v["detectable"] for v in vulns)
    print(f"{len(vulns)} documented, {detectable} detectable", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
