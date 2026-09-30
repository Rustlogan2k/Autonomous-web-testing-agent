"""Generate an application's faulty build from its clean build by declared patches.

**Why generated rather than hand-maintained.** A clean build and a faulty build edited
independently drift. Once they drift, a trace that behaves differently on the two might be
revealing a seeded fault or an unrelated divergence, and there is no way to tell after the
fact. Generating the faulty build from the clean source means the *only* differences are
the patches listed, which is checkable by diff -- and the oracle's clean-build control is
only a control under exactly that condition.

Each patch names the fault it implements, so the injected code and the fault metadata in
`faults.json` cannot disagree about what was seeded.

An application's `build.py` declares its patches and delegates here:

    from web_testing_agent.benchmark.builder import Patch, main

    PATCHES = (
        Patch("BOOK-01", "app.js", find="rate * nights", replace="rate * (nights - 1)",
              why="calculation error: the last night is not charged"),
    )

    if __name__ == "__main__":
        raise SystemExit(main(__file__, PATCHES))
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

#: Text files are patched; everything else is copied byte for byte.
_TEXT_SUFFIXES = frozenset({".html", ".js", ".css", ".json", ".svg", ".txt"})


@dataclass(frozen=True)
class Patch:
    """One seeded fault, as a literal substitution in one file of the clean build.

    `find` must occur **exactly once** in the file. A patch whose anchor is ambiguous
    would seed the fault somewhere its author did not look, and one whose anchor is
    missing means the clean build changed without the patch following it; both fail the
    build rather than produce a faulty build nobody audited.
    """

    fault_id: str
    filename: str
    find: str
    replace: str
    why: str


def _read_exact(path: Path) -> str:
    """Read without newline translation, so a comparison is byte-faithful."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def build(here: Path, patches: tuple[Patch, ...], *, check_only: bool = False) -> int:
    """Write (or verify) `here/buggy` from `here/clean` plus `patches`. Returns an exit code."""
    clean, buggy = here / "clean", here / "buggy"
    if not clean.is_dir():
        print(f"clean build missing at {clean}", file=sys.stderr)
        return 2

    applied: set[str] = set()
    staged: dict[Path, str | None] = {}
    for source in sorted(clean.rglob("*")):
        if source.is_dir():
            continue
        relative = source.relative_to(clean)
        target = buggy / relative
        if source.suffix.lower() not in _TEXT_SUFFIXES:
            staged[target] = None
            continue
        # `newline=""` on read and write: otherwise Python translates line endings and the
        # generated build differs from the clean one on every line, which makes the
        # clean/faulty diff useless as an audit of what was seeded.
        text = _read_exact(source)
        for patch in patches:
            if patch.filename != relative.as_posix():
                continue
            occurrences = text.count(patch.find)
            if occurrences != 1:
                print(f"PATCH FAILED: {patch.fault_id} anchor occurs {occurrences} times in "
                      f"{patch.filename} (must be exactly 1).", file=sys.stderr)
                return 3
            if patch.find == patch.replace:
                print(f"PATCH FAILED: {patch.fault_id} replaces its anchor with itself.",
                      file=sys.stderr)
                return 3
            text = text.replace(patch.find, patch.replace, 1)
            applied.add(f"{patch.fault_id}@{patch.filename}")
        staged[target] = text

    missing = [f"{p.fault_id}@{p.filename}" for p in patches
               if f"{p.fault_id}@{p.filename}" not in applied]
    if missing:
        print(f"PATCH FAILED: no such file in the clean build for {missing}", file=sys.stderr)
        return 3

    if check_only:
        drift: list[str] = []
        for target, text in staged.items():
            if not target.is_file():
                drift.append(f"missing {target.relative_to(here)}")
            elif text is not None and _read_exact(target) != text:
                drift.append(f"stale {target.relative_to(here)}")
            elif text is None and target.read_bytes() != (
                    clean / target.relative_to(buggy)).read_bytes():
                drift.append(f"stale {target.relative_to(here)}")
        if buggy.is_dir():
            for existing in buggy.rglob("*"):
                if existing.is_file() and existing not in staged:
                    drift.append(f"orphan {existing.relative_to(here)}")
        if drift:
            print("faulty build is out of sync with clean:", file=sys.stderr)
            for item in drift:
                print(f"  {item}", file=sys.stderr)
            return 1
        print(f"faulty build in sync ({len(staged)} files, {len(patches)} patches)")
        return 0

    if buggy.exists():
        shutil.rmtree(buggy)
    for target, text in staged.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            shutil.copy2(clean / target.relative_to(buggy), target)
        else:
            with target.open("w", encoding="utf-8", newline="") as handle:
                handle.write(text)
    print(f"built {len(staged)} files with {len(patches)} seeded patches: "
          f"{', '.join(sorted({p.fault_id for p in patches}))}")
    return 0


def main(build_file: str, patches: tuple[Patch, ...]) -> int:
    """Entry point for an application's `build.py`."""
    parser = argparse.ArgumentParser(description="Generate the faulty build from the clean one.")
    parser.add_argument("--check", action="store_true",
                        help="verify the faulty build matches clean + patches; do not write")
    arguments = parser.parse_args()
    return build(Path(build_file).resolve().parent, patches, check_only=arguments.check)
