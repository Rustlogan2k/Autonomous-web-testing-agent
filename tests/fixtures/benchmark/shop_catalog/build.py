"""Generate the buggy build from the clean one by applying declared, auditable patches.

**Why generated rather than hand-maintained.** A clean build and a buggy build that are
edited independently drift. When they drift, a trace that behaves differently on the two
might be revealing a seeded fault or might be revealing an unrelated divergence, and there
is no way to tell after the fact. Generating the buggy build from the clean source means
the *only* differences are the ones listed in `PATCHES`, and that is checkable by diff.

Each patch names the fault it implements, so the injected code and the fault metadata in
`faults.json` cannot disagree about what was seeded.

    python tests/fixtures/benchmark/shop_catalog/build.py
    python tests/fixtures/benchmark/shop_catalog/build.py --check   # CI: verify in sync
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLEAN = HERE / "clean"
BUGGY = HERE / "buggy"


@dataclass(frozen=True)
class Patch:
    """One seeded fault, as a literal substitution in one file."""

    fault_id: str
    filename: str
    find: str
    replace: str
    why: str


PATCHES: tuple[Patch, ...] = (
    Patch(
        fault_id="SHOP-01",
        filename="confirmation.html",
        # The confirmation renders each line's quantity from the stored order. The seeded
        # fault renders the *number of distinct lines* instead, so a single-line order of
        # five units confirms as one unit. It is silent: the page is well-formed, returns
        # 200, logs nothing, and the basket page one step earlier showed the right number.
        find="'<td class=\"confirm-qty\">' + line.quantity + '</td>' +",
        replace="'<td class=\"confirm-qty\">' + order.lines.length + '</td>' +",
        why="state corruption: the confirmed quantity is not the quantity ordered",
    ),
    Patch(
        fault_id="SHOP-02",
        filename="checkout.html",
        # The postcode guard is dropped, so an order can be placed with an empty delivery
        # postcode. The email guard is deliberately left intact: a fault that disables
        # every check is trivially findable and does not discriminate between policies.
        find="      var postcodeOk = postcode.length >= 3;",
        replace="      var postcodeOk = true;  // seeded: postcode guard removed",
        why="constraint bypass: a declared validation rule is not enforced",
    ),
)


def _read_exact(path: Path) -> str:
    """Read without newline translation, so a comparison is byte-faithful."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def build(check_only: bool = False) -> int:
    if not CLEAN.is_dir():
        print(f"clean build missing at {CLEAN}", file=sys.stderr)
        return 2

    staged: dict[Path, str] = {}
    for source in sorted(CLEAN.rglob("*")):
        if source.is_dir():
            continue
        relative = source.relative_to(CLEAN)
        target = BUGGY / relative
        if source.suffix in (".html", ".js", ".css"):
            # `newline=""` on both read and write: without it Python translates line
            # endings and the generated build differs from the clean one on every line,
            # which makes the clean/buggy diff useless as an audit of what was seeded.
            with source.open("r", encoding="utf-8", newline="") as handle:
                text = handle.read()
            for patch in PATCHES:
                if patch.filename != relative.as_posix():
                    continue
                if patch.find not in text:
                    print(f"PATCH FAILED: {patch.fault_id} anchor not found in "
                          f"{patch.filename}. The clean build changed and the patch did "
                          f"not follow it.", file=sys.stderr)
                    return 3
                text = text.replace(patch.find, patch.replace, 1)
            staged[target] = text
        else:
            staged[target] = None  # copied verbatim

    if check_only:
        drift: list[str] = []
        for target, text in staged.items():
            if not target.is_file():
                drift.append(f"missing {target.relative_to(HERE)}")
            elif text is not None and _read_exact(target) != text:
                drift.append(f"stale {target.relative_to(HERE)}")
        for existing in BUGGY.rglob("*"):
            if existing.is_file() and existing not in staged:
                drift.append(f"orphan {existing.relative_to(HERE)}")
        if drift:
            print("buggy build is out of sync with clean:", file=sys.stderr)
            for item in drift:
                print(f"  {item}", file=sys.stderr)
            return 1
        print(f"buggy build in sync ({len(staged)} files, {len(PATCHES)} patches)")
        return 0

    if BUGGY.exists():
        shutil.rmtree(BUGGY)
    for target, text in staged.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            shutil.copy2(CLEAN / target.relative_to(BUGGY), target)
        else:
            with target.open("w", encoding="utf-8", newline="") as handle:
                handle.write(text)
    print(f"built {len(staged)} files with {len(PATCHES)} seeded faults: "
          f"{', '.join(p.fault_id for p in PATCHES)}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify the buggy build matches clean + patches; do not write")
    raise SystemExit(build(check_only=parser.parse_args().check))
