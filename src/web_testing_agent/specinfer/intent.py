"""Intended behaviour from outside the running application: its documentation and tests.

A page shows what the application *does*, bugs included. A repository's README, docs and
test suite say what it is *meant* to do, independently of any buggy page, so the rule writer
and the judge are given them when a repository is at hand (an upload, a site folder, a
bundled demo application).

Only documentation and tests are read, never the application's source: the point is a
statement of intent, not a second copy of the implementation. Files that look like an
answer key (fault manifests, patches, anything naming a seeded fault such as `HELP-2`) are
skipped, so a seeded evaluation application cannot leak its faults into the rules.
"""

from __future__ import annotations

import re
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", "vendor", "seed",
             ".next", "coverage", ".pytest_cache", "site-packages"}
DOC_SUFFIXES = {".md", ".rst", ".txt"}
TEST_SUFFIXES = {".py", ".js", ".ts", ".jsx", ".tsx", ".feature"}
#: An answer key, or a file that talks about seeded faults: never read.
LEAK = re.compile(r"(?i)\bfaults?\b|\bseeded\b|\bpatch(es)?\b|answer key"
                  r"|(?-i:\b(?!UTF-|ISO-|RFC-|SHA-|ES-|HTTP-)[A-Z]{2,8}-\d{1,3}\b)")
SKIP_NAMES = re.compile(r"(?i)(fault|patch|manifest|app\.json|\.diff$|\.patch$|licen[cs]e|changelog)")


def _is_test(path: Path, root: Path) -> bool:
    parts = {p.lower() for p in path.relative_to(root).parts[:-1]}
    name = path.name.lower()
    return path.suffix in TEST_SUFFIXES and (
        bool(parts & {"test", "tests", "spec", "specs", "__tests__", "e2e", "features"})
        or name.startswith("test_") or ".test." in name or ".spec." in name or name.endswith("_test.py"))


def _is_doc(path: Path, root: Path) -> bool:
    parts = {p.lower() for p in path.relative_to(root).parts[:-1]}
    return path.suffix.lower() in DOC_SUFFIXES and (path.name.lower().startswith("readme") or "docs" in parts
                                                     or "doc" in parts or path.parent == root)


def collect_intent(root: str | Path | None, *, limit: int = 12000, per_file: int = 3000) -> tuple[str, list[str]]:
    """(text, files read): README and docs first, then tests, within `limit` characters."""
    if not root:
        return "", []
    root = Path(root)
    if not root.is_dir():
        return "", []
    docs, tests = [], []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if SKIP_NAMES.search(path.name) or path.stat().st_size > 200_000:
            continue
        if _is_doc(path, root):
            docs.append(path)
        elif _is_test(path, root):
            tests.append(path)
    docs.sort(key=lambda p: (not p.name.lower().startswith("readme"), len(p.parts), p.as_posix().lower()))
    text, used = [], []
    total = 0
    for path in docs + tests:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not content.strip() or LEAK.search(content):
            continue
        chunk = f"=== {path.relative_to(root).as_posix()} ===\n{content[:per_file]}"
        if total + len(chunk) > limit:
            break
        text.append(chunk)
        used.append(path.relative_to(root).as_posix())
        total += len(chunk)
    return "\n\n".join(text), used


def quote_supported(quote: str, *sources: str) -> bool:
    """Whether a judge's quote really appears in the evidence it was given."""
    def norm(s: str) -> str:
        return " ".join(re.sub(r"[^\w%$.,'-]+", " ", (s or "").lower()).split())

    q = norm(quote)
    return len(q) >= 8 and any(q in norm(src) for src in sources)
