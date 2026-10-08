"""Clean, buggy and per-fault-control builds of an application, and proof of what differs.

Clean is the app's `src/` as written. Buggy is clean with every fault's patch applied.
The control for fault F is buggy without F's patch. Each patch is a literal substitution
whose anchor must occur exactly once in its file (the primary builder's rule). The diff
audit proves the buggy build differs from clean **only** by the declared substitutions.
"""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

from .manifest import BUILD_ROOT, AppManifest, Patch

#: What a variant is built from: everything the app needs at runtime.
RUNTIME_PARTS = ("src", "seed", "vocab", "Dockerfile", "README.md", "app.json")
#: The pinned database drivers (pg8000 for Python, node-postgres for Node), shared by every
#: app and copied into each build as `vendor/`, the way a lockfile pins a dependency: the
#: product builds with no network, so a build must already contain what it imports.
VENDOR = Path(__file__).resolve().parents[1] / "vendor"


class PatchError(RuntimeError):
    pass


def control_key(fault_id: str) -> str:
    return f"without-{fault_id}"


def apply_patches(root: Path, patches: list[Patch]) -> None:
    """Apply `patches` in place under `root`. Raises on a missing or ambiguous anchor."""
    for patch in patches:
        path = root / patch.file
        if not path.is_file():
            raise PatchError(f"{patch.fault_id}: {patch.file} does not exist")
        text = path.read_text(encoding="utf-8")
        count = text.count(patch.find)
        if count != 1:
            raise PatchError(f"{patch.fault_id}: anchor occurs {count} times in {patch.file}")
        if patch.find == patch.replace:
            raise PatchError(f"{patch.fault_id}: patch changes nothing")
        path.write_text(text.replace(patch.find, patch.replace), encoding="utf-8", newline="")


def _copy_runtime(app: AppManifest, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for part in RUNTIME_PARTS:
        source = app.root / part
        if source.is_dir():
            shutil.copytree(source, dest / part,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules"))
        elif source.is_file():
            shutil.copy2(source, dest / part)
    if app.database == "postgres":
        shutil.copytree(VENDOR, dest / "vendor",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def build_variant(app: AppManifest, variant: str) -> Path:
    """Materialise one variant under `.build/<app>/<variant>` and return its directory."""
    dest = BUILD_ROOT / app.app_id / variant
    _copy_runtime(app, dest)
    if variant == "clean":
        return dest
    if variant == "buggy":
        apply_patches(dest, app.patches)
        return dest
    if variant.startswith("without-"):
        excluded = variant[len("without-"):]
        if excluded not in {p.fault_id for p in app.patches}:
            raise PatchError(f"unknown fault {excluded}")
        apply_patches(dest, [p for p in app.patches if p.fault_id != excluded])
        return dest
    raise PatchError(f"unknown variant {variant!r}")


def build_all(app: AppManifest, *, controls: bool = True) -> dict[str, Path]:
    variants = ["clean", "buggy"] + ([control_key(p.fault_id) for p in app.patches]
                                     if controls else [])
    return {v: build_variant(app, v) for v in variants}


def _files(root: Path) -> dict[str, Path]:
    return {str(p.relative_to(root)).replace("\\", "/"): p
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def audit_diff(app: AppManifest, clean: Path, buggy: Path) -> list[str]:
    """Problems with `buggy` versus `clean`; empty means it is exactly clean + the patches."""
    problems: list[str] = []
    clean_files, buggy_files = _files(clean), _files(buggy)
    if set(clean_files) != set(buggy_files):
        problems.append(f"file sets differ: only clean {sorted(set(clean_files) - set(buggy_files))}, "
                        f"only buggy {sorted(set(buggy_files) - set(clean_files))}")
    by_file: dict[str, list[Patch]] = {}
    for patch in app.patches:
        by_file.setdefault(patch.file.replace("\\", "/"), []).append(patch)
    for name in sorted(set(clean_files) & set(buggy_files)):
        if name not in by_file:
            if not filecmp.cmp(clean_files[name], buggy_files[name], shallow=False):
                problems.append(f"{name} differs but no patch targets it")
            continue
        expected = clean_files[name].read_text(encoding="utf-8")
        for patch in by_file[name]:
            expected = expected.replace(patch.find, patch.replace, 1)
        if buggy_files[name].read_text(encoding="utf-8") != expected:
            problems.append(f"{name}: buggy content is not clean + its declared patches")
    for patch in app.patches:
        clean_text = (clean / patch.file).read_text(encoding="utf-8")
        if clean_text.count(patch.find) != 1:
            problems.append(f"{patch.fault_id}: anchor occurs {clean_text.count(patch.find)} "
                            f"times in clean {patch.file}")
        if patch.replace in clean_text and patch.replace not in patch.find:
            problems.append(f"{patch.fault_id}: the faulty text already exists in the clean build")
    return problems


def patch_sizes(app: AppManifest) -> dict[str, dict]:
    """How big each seeded change is: lines touched, characters changed."""
    out = {}
    for patch in app.patches:
        out[patch.fault_id] = {
            "file": patch.file,
            "lines_removed": patch.find.count("\n") + 1,
            "lines_added": patch.replace.count("\n") + 1,
            "chars_changed": abs(len(patch.replace) - len(patch.find))
            + sum(a != b for a, b in zip(patch.find, patch.replace)),
        }
    return out
