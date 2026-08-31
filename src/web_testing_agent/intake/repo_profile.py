"""What a repository *is*: its structure, languages, build system, entry points and tests.

**This is not `ApplicationProfile`, and merging the two would spoil both.**
`intake/profile.py` describes application *intent* — routes, declared validation rules,
flows, known-correct behaviours — and its consumer is the judge, which needs to decide
whether an observation contradicts a rule. This module describes the *repository* — how
it is laid out, what it is written in, how it would be installed, built and tested — and
its consumers are the deployer and the bug report, which need to know what they are
looking at. Different producers, different consumers, different lifetimes. The two are
kept apart deliberately.

**Why this is justified, and what it is deliberately not justified by.** §5's 2026-08-26
A/B measured that a hand-authored `ApplicationProfile` changed the judge's recall, false
positives, discrimination and evidence grounding *by nothing at all*. So nothing here is
motivated by an expectation that profile context improves judgment — that claim is
unproven and stays unproven. This exists because the deployer and the report currently
have no idea what repository they are working on, which is concrete and fixable.

**Everything here is deterministic and mechanical.** No LLM, no network, no Docker, no
browser. The same directory tree always produces the same profile, which is what makes it
testable in milliseconds against fixture trees. Where a fact cannot be read off the
repository it is *not* reported: an absent detection is information, and inventing a
plausible one would put guesses into a document a human is meant to trust.

**Confidence is a stated convention, not a score.** Three levels, so a reader can tell a
declaration from an inference:

* ``1.0`` — the repository says so itself (a manifest field, a declared script).
* ``0.7`` — a filename or directory that means one thing by strong convention
  (``manage.py``, ``tests/``, ``go.mod``).
* ``0.5`` — a heuristic that is often right and sometimes not (a bare ``main.py``).

**Vendored and generated trees are excluded, and that is load-bearing.** A repository
with ``node_modules`` checked in is overwhelmingly JavaScript by file count and tells you
nothing about what its authors wrote. `IGNORED_DIRS` is pruned during the walk rather
than filtered afterwards, so the cost is skipped too.

**Known limitation, measured on this repository.** Language share is bytes over
recognised extensions, so committed *data* that happens to have a source extension counts
as source: profiling this project reports HTML as the primary language, because the
seeded-bug fixtures and the captured page blobs under ``data/annotations`` outweigh the
Python. That is arguably the honest answer to "what is in this tree", but it is not the
answer to "what is this project written in". Excluding paths that merely look like data
would be a repository-specific rule dressed up as a general one, so the number is left as
it is and the limitation is stated here instead.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..utils.logging import get_logger
from .runner import COMPOSE_NAMES, DOCKERFILE_NAMES

logger = get_logger(__name__)

# Directories that contain dependencies, build output or tooling state rather than the
# repository's own source. Pruned during the walk: a vendored tree is usually far larger
# than the repository itself, so filtering afterwards would mean paying to read it.
IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn", ".idea", ".vscode",
    "node_modules", "bower_components", "vendor", "third_party",
    "venv", ".venv", "env", ".env.d", "virtualenv", "site-packages",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox",
    "dist", "build", "out", "target", ".next", ".nuxt", ".output",
    "coverage", "htmlcov", ".gradle", ".terraform",
})

# Bounds, so a pathological repository cannot make profiling the expensive step. Each is
# recorded in `ProfileStats.truncated` when it bites, because a silently partial profile
# is worse than a loud one.
MAX_FILES = 20_000
MAX_DEPTH = 12
# Manifests are small; anything larger is not a manifest we can use. This caps the read,
# not the file, so a huge lockfile costs one seek rather than its size.
MAX_MANIFEST_BYTES = 512_000

LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".java": "Java", ".kt": "Kotlin", ".scala": "Scala", ".cs": "C#",
    ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++",
    ".swift": "Swift", ".m": "Objective-C",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "CSS", ".sass": "CSS",
    ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell",
    ".sql": "SQL", ".md": "Markdown", ".rst": "Markdown",
    ".yml": "YAML", ".yaml": "YAML", ".json": "JSON", ".toml": "TOML",
}

# Manifest filename -> (build system, ecosystem). Presence alone is a strong convention
# signal; contents refine it below (a lockfile names the actual package manager).
_MANIFESTS: dict[str, tuple[str, str]] = {
    "package.json": ("npm", "javascript"),
    "requirements.txt": ("pip", "python"),
    "pyproject.toml": ("pip", "python"),
    "setup.py": ("setuptools", "python"),
    "Pipfile": ("pipenv", "python"),
    "go.mod": ("go", "go"),
    "Cargo.toml": ("cargo", "rust"),
    "pom.xml": ("maven", "java"),
    "build.gradle": ("gradle", "java"),
    "build.gradle.kts": ("gradle", "java"),
    "Gemfile": ("bundler", "ruby"),
    "composer.json": ("composer", "php"),
    "Makefile": ("make", "*"),
    "CMakeLists.txt": ("cmake", "c"),
}

# Lockfiles decide *which* JavaScript package manager, which changes the install command
# (`npm ci` is not `yarn install`). Ordered because a repo can contain more than one.
_JS_LOCKFILES: tuple[tuple[str, str], ...] = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
)

# Dependency name -> framework. Deliberately small and conservative: a framework claim
# is only made when a manifest actually depends on it, so this maps declarations rather
# than guessing from directory shapes.
_FRAMEWORKS: dict[str, str] = {
    "flask": "Flask", "django": "Django", "fastapi": "FastAPI", "starlette": "Starlette",
    "tornado": "Tornado", "pyramid": "Pyramid", "bottle": "Bottle",
    "express": "Express", "next": "Next.js", "nuxt": "Nuxt", "react": "React",
    "vue": "Vue", "svelte": "Svelte", "@angular/core": "Angular", "koa": "Koa",
    "nestjs": "NestJS", "@nestjs/core": "NestJS", "fastify": "Fastify",
    "rails": "Rails", "sinatra": "Sinatra",
    "laravel/framework": "Laravel", "symfony/framework-bundle": "Symfony",
    "gin-gonic/gin": "Gin", "labstack/echo": "Echo", "gofiber/fiber": "Fiber",
    "actix-web": "Actix Web", "rocket": "Rocket", "axum": "Axum",
    "spring-boot-starter-web": "Spring Boot",
}

# Conventional entry-point filenames, with the confidence each deserves. `manage.py` is
# unambiguous; a bare `main.py` is a decent guess and nothing more.
_ENTRY_POINTS: dict[str, float] = {
    "manage.py": 0.7, "wsgi.py": 0.7, "asgi.py": 0.7,
    "main.py": 0.5, "app.py": 0.5, "application.py": 0.5, "__main__.py": 0.7,
    "server.js": 0.5, "index.js": 0.5, "app.js": 0.5, "main.js": 0.5,
    "server.ts": 0.5, "index.ts": 0.5, "main.ts": 0.5,
    "main.go": 0.7, "main.rs": 0.7,
}

_TEST_DIR_NAMES = frozenset({"tests", "test", "spec", "specs", "__tests__", "testing"})
_DOC_FILE_PREFIXES = ("readme", "contributing", "install", "getting-started", "changelog")
# A documentation *prefix* is not enough: `install.py` is a script, not an installation
# guide, and reporting it as documentation was a real false positive caught by profiling
# this repository. The suffix has to agree — a prose extension, or none at all, which is
# how `LICENSE` and `CHANGELOG` are usually written.
_DOC_SUFFIXES = frozenset({".md", ".rst", ".txt", ".adoc", ".org", ""})
_DOC_DIR_NAMES = frozenset({"docs", "doc", "documentation"})


@dataclass(frozen=True, slots=True)
class Detection:
    """One detected fact, with the file that justifies it.

    `evidence` is a repository-relative path, never absolute: a profile is a document
    about a repository, and embedding the machine it was produced on makes it unusable
    anywhere else and needlessly discloses a local filesystem layout.
    """

    value: str
    evidence: str
    confidence: float
    detail: str = ""

    def to_dict(self) -> dict:
        payload = {"value": self.value, "evidence": self.evidence, "confidence": self.confidence}
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True, slots=True)
class LanguageStat:
    """How much of the repository is written in one language."""

    language: str
    files: int
    bytes: int
    share: float  # of counted source bytes, 0-1

    def to_dict(self) -> dict:
        return {"language": self.language, "files": self.files,
                "bytes": self.bytes, "share": round(self.share, 4)}


@dataclass(frozen=True, slots=True)
class ProfileStats:
    """What the walk actually saw, so a partial profile is visible as partial."""

    files_scanned: int = 0
    directories_scanned: int = 0
    ignored_directories: int = 0
    source_bytes: int = 0
    truncated: str = ""  # "" | "max_files" | "max_depth" | both, comma-joined

    def to_dict(self) -> dict:
        return {
            "files_scanned": self.files_scanned,
            "directories_scanned": self.directories_scanned,
            "ignored_directories": self.ignored_directories,
            "source_bytes": self.source_bytes,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class RepositoryProfile:
    """A structured, mechanically-derived description of one repository.

    `provenance` is `"detector"` — a distinct vocabulary from `ApplicationProfile`'s
    `manual | profiler`, because these are different artifacts and a shared vocabulary
    would invite reading one as the other. If an LLM-assisted producer is added later it
    gets its own value rather than borrowing this one.
    """

    name: str
    root: str
    provenance: str = "detector"
    languages: tuple[LanguageStat, ...] = ()
    build_systems: tuple[Detection, ...] = ()
    frameworks: tuple[Detection, ...] = ()
    entry_points: tuple[Detection, ...] = ()
    test_targets: tuple[Detection, ...] = ()
    dependency_manifests: tuple[Detection, ...] = ()
    docs: tuple[Detection, ...] = ()
    build_definition: Detection | None = None
    install_commands: tuple[Detection, ...] = ()
    build_commands: tuple[Detection, ...] = ()
    test_commands: tuple[Detection, ...] = ()
    stats: ProfileStats = field(default_factory=ProfileStats)
    notes: tuple[str, ...] = ()

    @property
    def primary_language(self) -> str:
        """The largest language by source bytes, or "" when nothing was recognised."""
        return self.languages[0].language if self.languages else ""

    @property
    def deployable(self) -> bool:
        """Whether the runner could deploy this today.

        True only for a Dockerfile: `intake/runner.py` detects compose files, policy-gates
        them and then deliberately refuses to execute them, so a compose-only repository
        is *detected* but not deployable by this pipeline.
        """
        return self.build_definition is not None and self.build_definition.value == "dockerfile"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "root": self.root,
            "provenance": self.provenance,
            "primary_language": self.primary_language,
            "deployable": self.deployable,
            "languages": [entry.to_dict() for entry in self.languages],
            "build_systems": [entry.to_dict() for entry in self.build_systems],
            "frameworks": [entry.to_dict() for entry in self.frameworks],
            "entry_points": [entry.to_dict() for entry in self.entry_points],
            "test_targets": [entry.to_dict() for entry in self.test_targets],
            "dependency_manifests": [entry.to_dict() for entry in self.dependency_manifests],
            "docs": [entry.to_dict() for entry in self.docs],
            "build_definition": self.build_definition.to_dict() if self.build_definition else None,
            "install_commands": [entry.to_dict() for entry in self.install_commands],
            "build_commands": [entry.to_dict() for entry in self.build_commands],
            "test_commands": [entry.to_dict() for entry in self.test_commands],
            "stats": self.stats.to_dict(),
            "notes": list(self.notes),
        }


# -- walking -------------------------------------------------------------------------


@dataclass
class _Walk:
    """Everything one pass over the tree collected."""

    files: list[Path] = field(default_factory=list)          # repo-relative
    stats_files: int = 0
    stats_dirs: int = 0
    stats_ignored: int = 0
    truncated: list[str] = field(default_factory=list)


def _walk(root: Path) -> _Walk:
    """Every source file, with vendored trees pruned and the traversal bounded."""
    walk = _Walk()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        depth = len(current.relative_to(root).parts)
        if depth >= MAX_DEPTH:
            walk.stats_ignored += len(dirnames)
            dirnames[:] = []
            if "max_depth" not in walk.truncated:
                walk.truncated.append("max_depth")
            continue

        keep = []
        for name in dirnames:
            if name in IGNORED_DIRS or (name.startswith(".") and name not in (".github",)):
                walk.stats_ignored += 1
                continue
            # A symlinked directory can point back up the tree; `followlinks=False`
            # stops os.walk descending, but the entry would still be listed.
            if (current / name).is_symlink():
                walk.stats_ignored += 1
                continue
            keep.append(name)
        dirnames[:] = sorted(keep)
        walk.stats_dirs += 1

        for name in sorted(filenames):
            if walk.stats_files >= MAX_FILES:
                if "max_files" not in walk.truncated:
                    walk.truncated.append("max_files")
                dirnames[:] = []
                break
            path = current / name
            if path.is_symlink():
                continue
            walk.files.append(path.relative_to(root))
            walk.stats_files += 1
    walk.files.sort()
    return walk


def _read_text(path: Path) -> str:
    """A manifest's text, bounded and never raising."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(MAX_MANIFEST_BYTES)
    except OSError as exc:  # noqa: BLE001 - an unreadable manifest is a missing one
        logger.debug("could not read {}: {}", path, exc)
        return ""


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(_read_text(path) or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(_read_text(path))
    except (tomllib.TOMLDecodeError, ValueError):
        return {}


def _posix(path: Path) -> str:
    """Repository-relative, forward-slashed. Profiles are read on other machines."""
    return path.as_posix()


# -- detectors ------------------------------------------------------------------------


def _languages(root: Path, files: list[Path]) -> tuple[LanguageStat, ...]:
    counts: dict[str, int] = {}
    sizes: dict[str, int] = {}
    for rel in files:
        language = LANGUAGE_BY_SUFFIX.get(rel.suffix.lower())
        if not language:
            continue
        try:
            size = (root / rel).stat().st_size
        except OSError:
            size = 0
        counts[language] = counts.get(language, 0) + 1
        sizes[language] = sizes.get(language, 0) + size

    total = sum(sizes.values()) or 1
    stats = [
        LanguageStat(language=name, files=counts[name], bytes=sizes[name], share=sizes[name] / total)
        for name in counts
    ]
    # Bytes, then files, then name: a stable order that does not depend on dict insertion.
    stats.sort(key=lambda s: (-s.bytes, -s.files, s.language))
    return tuple(stats)


def _build_definition(root: Path, files: list[Path]) -> Detection | None:
    """The repo's Docker build definition, using the runner's own filename lists.

    Compose wins when both exist, matching `runner.detect_build_definition`, so the two
    cannot disagree about what a repository is. Kept as a *detection* rather than a call
    into the runner because the runner raises when nothing is found, and "this repository
    has no Dockerfile" is a fact a profile should record rather than an error.
    """
    by_name: dict[str, Path] = {}
    for rel in files:
        if len(rel.parts) <= 2 and rel.name not in by_name:
            by_name[rel.name] = rel
    for name in COMPOSE_NAMES:
        if name in by_name:
            return Detection("compose", _posix(by_name[name]), 1.0,
                             "detected and policy-gated, but not executed by intake/runner.py")
    for name in DOCKERFILE_NAMES:
        if name in by_name:
            return Detection("dockerfile", _posix(by_name[name]), 1.0)
    return None


def _build_systems(root: Path, files: list[Path]) -> tuple[Detection, ...]:
    found: dict[str, Detection] = {}
    names = {rel.name: rel for rel in files if len(rel.parts) <= 2}

    for filename, (system, _eco) in _MANIFESTS.items():
        rel = names.get(filename)
        if rel is None:
            continue
        found.setdefault(system, Detection(system, _posix(rel), 0.7))

    # A lockfile is the repository stating which package manager it actually uses, so it
    # both upgrades confidence and replaces the generic `npm` guess.
    for lockfile, manager in _JS_LOCKFILES:
        rel = names.get(lockfile)
        if rel is not None:
            found.pop("npm", None)
            found[manager] = Detection(manager, _posix(rel), 1.0, "named by its lockfile")
            break

    # pyproject can declare poetry or hatch explicitly; that outranks the generic pip guess.
    pyproject = names.get("pyproject.toml")
    if pyproject is not None:
        data = _read_toml(root / pyproject)
        backend = str(((data.get("build-system") or {}).get("build-backend") or "")).lower()
        tools = data.get("tool") or {}
        if "poetry" in backend or "poetry" in tools:
            found.pop("pip", None)
            found["poetry"] = Detection("poetry", _posix(pyproject), 1.0, "declared in pyproject.toml")
        elif "hatchling" in backend or "hatch" in tools:
            found.pop("pip", None)
            found["hatch"] = Detection("hatch", _posix(pyproject), 1.0, "declared in pyproject.toml")

    return tuple(sorted(found.values(), key=lambda d: d.value))


def _dependency_names(root: Path, files: list[Path]) -> list[tuple[str, str]]:
    """(dependency name, evidence path) from every manifest that declares dependencies."""
    names = {rel.name: rel for rel in files if len(rel.parts) <= 2}
    out: list[tuple[str, str]] = []

    rel = names.get("package.json")
    if rel is not None:
        data = _read_json(root / rel)
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            block = data.get(section)
            if isinstance(block, dict):
                out.extend((str(dep).lower(), _posix(rel)) for dep in block)

    rel = names.get("requirements.txt")
    if rel is not None:
        for line in _read_text(root / rel).splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "-")):
                continue
            # Strip version specifiers and extras: `Flask[async]>=2.0` -> `flask`.
            token = line.split(";")[0].split("[")[0]
            for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "="):
                token = token.split(sep)[0]
            if token.strip():
                out.append((token.strip().lower(), _posix(rel)))

    rel = names.get("pyproject.toml")
    if rel is not None:
        data = _read_toml(root / rel)
        for dep in (data.get("project") or {}).get("dependencies") or []:
            token = str(dep).split(";")[0].split("[")[0]
            for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "="):
                token = token.split(sep)[0]
            if token.strip():
                out.append((token.strip().lower(), _posix(rel)))
        poetry_deps = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
        if isinstance(poetry_deps, dict):
            out.extend((str(dep).lower(), _posix(rel)) for dep in poetry_deps)

    rel = names.get("go.mod")
    if rel is not None:
        for line in _read_text(root / rel).splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("module ", "go ", "require (", ")")):
                continue
            token = stripped.removeprefix("require ").split()[0] if stripped else ""
            if "/" in token:
                # `github.com/gin-gonic/gin` -> also match on `gin-gonic/gin`.
                out.append((token.lower(), _posix(rel)))
                out.append(("/".join(token.lower().split("/")[-2:]), _posix(rel)))

    rel = names.get("Cargo.toml")
    if rel is not None:
        data = _read_toml(root / rel)
        for section in ("dependencies", "dev-dependencies"):
            block = data.get(section)
            if isinstance(block, dict):
                out.extend((str(dep).lower(), _posix(rel)) for dep in block)

    rel = names.get("composer.json")
    if rel is not None:
        data = _read_json(root / rel)
        block = data.get("require")
        if isinstance(block, dict):
            out.extend((str(dep).lower(), _posix(rel)) for dep in block)

    rel = names.get("Gemfile")
    if rel is not None:
        for line in _read_text(root / rel).splitlines():
            stripped = line.strip()
            if stripped.startswith("gem "):
                token = stripped[4:].strip().strip("\"'").split("\"")[0].split("'")[0]
                if token:
                    out.append((token.lower(), _posix(rel)))

    rel = names.get("pom.xml")
    if rel is not None:
        text = _read_text(root / rel)
        for marker, name in (("spring-boot-starter-web", "spring-boot-starter-web"),):
            if marker in text:
                out.append((name, _posix(rel)))
    return out


def _frameworks(root: Path, files: list[Path]) -> tuple[Detection, ...]:
    """Frameworks the repository *declares a dependency on*. Never inferred from layout."""
    found: dict[str, Detection] = {}
    for dependency, evidence in _dependency_names(root, files):
        label = _FRAMEWORKS.get(dependency)
        if label and label not in found:
            found[label] = Detection(label, evidence, 1.0, f"declared dependency {dependency!r}")
    return tuple(sorted(found.values(), key=lambda d: d.value))


def _entry_points(root: Path, files: list[Path]) -> tuple[Detection, ...]:
    found: list[Detection] = []
    seen: set[str] = set()

    def add(value: str, evidence: str, confidence: float, detail: str = "") -> None:
        key = f"{value}@{evidence}"
        if key not in seen:
            seen.add(key)
            found.append(Detection(value, evidence, confidence, detail))

    names = {rel.name: rel for rel in files if len(rel.parts) <= 2}

    # A manifest naming its own entry point outranks any filename convention.
    rel = names.get("package.json")
    if rel is not None:
        data = _read_json(root / rel)
        main = data.get("main")
        if isinstance(main, str) and main:
            add(main, _posix(rel), 1.0, "package.json \"main\"")
        start = ((data.get("scripts") or {}) if isinstance(data.get("scripts"), dict) else {}).get("start")
        if isinstance(start, str) and start:
            add(start, _posix(rel), 1.0, "package.json scripts.start")

    definition = _build_definition(root, files)
    if definition is not None and definition.value == "dockerfile":
        for line in _read_text(root / definition.evidence).splitlines():
            stripped = line.strip()
            for keyword in ("CMD", "ENTRYPOINT"):
                if stripped.upper().startswith(keyword + " "):
                    add(stripped[len(keyword):].strip(), definition.evidence, 1.0,
                        f"Dockerfile {keyword}")

    for rel in files:
        # `cmd/<name>/main.go` is Go's convention for an executable and is stronger than
        # a bare `main.go` somewhere in the tree.
        if rel.name == "main.go" and len(rel.parts) >= 3 and rel.parts[0] == "cmd":
            add(_posix(rel), _posix(rel), 0.7, "Go cmd/ convention")
            continue
        if rel.name == "main.rs" and len(rel.parts) == 2 and rel.parts[0] == "src":
            add(_posix(rel), _posix(rel), 0.7, "Cargo binary convention")
            continue
        confidence = _ENTRY_POINTS.get(rel.name)
        if confidence is not None and len(rel.parts) <= 2:
            add(_posix(rel), _posix(rel), confidence, "conventional entry-point filename")

    found.sort(key=lambda d: (-d.confidence, d.value))
    return tuple(found)


def _test_targets(root: Path, files: list[Path]) -> tuple[Detection, ...]:
    found: dict[str, Detection] = {}

    for rel in files:
        parts = [part.lower() for part in rel.parts[:-1]]
        for index, part in enumerate(parts):
            if part in _TEST_DIR_NAMES:
                directory = _posix(Path(*rel.parts[: index + 1]))
                found.setdefault(directory, Detection(directory, directory, 0.7, "test directory"))
                break
        name = rel.name
        stem = rel.stem
        if (name.startswith("test_") and rel.suffix == ".py") or (
            stem.endswith("_test") and rel.suffix in (".py", ".go")
        ) or any(stem.endswith(suffix) for suffix in (".test", ".spec")):
            found.setdefault(_posix(rel), Detection(_posix(rel), _posix(rel), 0.7,
                                                    "test filename convention"))

    names = {rel.name: rel for rel in files if len(rel.parts) <= 2}
    for filename in ("pytest.ini", "tox.ini", "jest.config.js", "jest.config.ts",
                     "vitest.config.ts", ".mocharc.json", "phpunit.xml"):
        rel = names.get(filename)
        if rel is not None:
            found.setdefault(filename, Detection(filename, _posix(rel), 1.0, "test configuration"))

    rel = names.get("pyproject.toml")
    if rel is not None:
        tools = (_read_toml(root / rel).get("tool") or {})
        if "pytest" in tools:
            found.setdefault("pytest", Detection("pytest", _posix(rel), 1.0,
                                                 "[tool.pytest] in pyproject.toml"))

    return tuple(sorted(found.values(), key=lambda d: d.value))


def _docs(root: Path, files: list[Path]) -> tuple[Detection, ...]:
    found: dict[str, Detection] = {}
    for rel in files:
        lowered = rel.name.lower()
        if (len(rel.parts) == 1 and lowered.startswith(_DOC_FILE_PREFIXES)
                and rel.suffix.lower() in _DOC_SUFFIXES):
            found.setdefault(_posix(rel), Detection(_posix(rel), _posix(rel), 1.0, "documentation file"))
        elif rel.parts and rel.parts[0].lower() in _DOC_DIR_NAMES:
            directory = rel.parts[0]
            found.setdefault(directory, Detection(directory, directory, 0.7, "documentation directory"))
    return tuple(sorted(found.values(), key=lambda d: d.value))


def _commands(
    root: Path, files: list[Path], build_systems: tuple[Detection, ...],
) -> tuple[tuple[Detection, ...], tuple[Detection, ...], tuple[Detection, ...]]:
    """Install / build / test commands implied by the detected build systems.

    These are *proposals*, not verified recipes — nothing here runs them, and §6.2's scope
    cut means the pipeline deploys a Dockerfile rather than executing these. They are
    recorded because "how would this be built" is a question a report should be able to
    answer, and because if the Dockerfile-only cut is ever relaxed this is the input a
    fallback build path would need.
    """
    install: list[Detection] = []
    build: list[Detection] = []
    test: list[Detection] = []
    systems = {d.value: d for d in build_systems}
    names = {rel.name: rel for rel in files if len(rel.parts) <= 2}

    def declared_scripts() -> dict:
        rel = names.get("package.json")
        if rel is None:
            return {}
        scripts = _read_json(root / rel).get("scripts")
        return scripts if isinstance(scripts, dict) else {}

    for manager in ("npm", "yarn", "pnpm"):
        if manager not in systems:
            continue
        evidence = systems[manager].evidence
        lockfile = names.get("package-lock.json")
        if manager == "npm":
            install.append(Detection("npm ci" if lockfile is not None else "npm install",
                                     evidence, 1.0 if lockfile is not None else 0.7))
        else:
            install.append(Detection(f"{manager} install", evidence, 1.0))
        scripts = declared_scripts()
        if "build" in scripts:
            build.append(Detection(f"{manager} run build", "package.json", 1.0, "scripts.build"))
        if "test" in scripts:
            test.append(Detection(f"{manager} test", "package.json", 1.0, "scripts.test"))

    if "pip" in systems:
        rel = names.get("requirements.txt")
        if rel is not None:
            install.append(Detection("pip install -r requirements.txt", _posix(rel), 1.0))
        elif names.get("pyproject.toml") is not None:
            install.append(Detection("pip install .", "pyproject.toml", 0.7))
    if "poetry" in systems:
        install.append(Detection("poetry install", systems["poetry"].evidence, 1.0))
    if "hatch" in systems:
        install.append(Detection("hatch env create", systems["hatch"].evidence, 0.7))
    if "setuptools" in systems and "pip" not in systems:
        install.append(Detection("pip install .", systems["setuptools"].evidence, 0.7))
    if "pipenv" in systems:
        install.append(Detection("pipenv install", systems["pipenv"].evidence, 1.0))

    if "go" in systems:
        evidence = systems["go"].evidence
        install.append(Detection("go mod download", evidence, 1.0))
        build.append(Detection("go build ./...", evidence, 1.0))
        test.append(Detection("go test ./...", evidence, 1.0))
    if "cargo" in systems:
        evidence = systems["cargo"].evidence
        build.append(Detection("cargo build", evidence, 1.0))
        test.append(Detection("cargo test", evidence, 1.0))
    if "maven" in systems:
        evidence = systems["maven"].evidence
        build.append(Detection("mvn -B package", evidence, 1.0))
        test.append(Detection("mvn -B test", evidence, 1.0))
    if "gradle" in systems:
        evidence = systems["gradle"].evidence
        runner = "./gradlew" if names.get("gradlew") is not None else "gradle"
        build.append(Detection(f"{runner} build", evidence, 1.0))
        test.append(Detection(f"{runner} test", evidence, 1.0))
    if "bundler" in systems:
        install.append(Detection("bundle install", systems["bundler"].evidence, 1.0))
    if "composer" in systems:
        install.append(Detection("composer install", systems["composer"].evidence, 1.0))

    # A Makefile only implies a command when it actually declares that target, so the
    # file is read rather than assumed. `make test` on a Makefile without a `test:` rule
    # is a proposal that fails the moment anyone tries it.
    rel = names.get("Makefile")
    if rel is not None:
        targets = {
            line.split(":", 1)[0].strip()
            for line in _read_text(root / rel).splitlines()
            if ":" in line and not line.startswith(("\t", " ", "#"))
        }
        if "install" in targets:
            install.append(Detection("make install", _posix(rel), 1.0, "declared target"))
        if "build" in targets or "all" in targets:
            build.append(Detection("make build" if "build" in targets else "make", _posix(rel),
                                   1.0, "declared target"))
        if "test" in targets:
            test.append(Detection("make test", _posix(rel), 1.0, "declared target"))

    if names.get("pytest.ini") is not None or names.get("pyproject.toml") is not None:
        if any(d.value in ("pytest", "tests", "test") or d.value.startswith("tests/")
               for d in _test_targets(root, files)):
            test.append(Detection("pytest", "pytest.ini" if names.get("pytest.ini") else
                                  "pyproject.toml", 0.7, "pytest layout detected"))

    key = lambda d: (-d.confidence, d.value)  # noqa: E731 - a sort key, not a function
    return tuple(sorted(install, key=key)), tuple(sorted(build, key=key)), tuple(sorted(test, key=key))


# -- entry point ----------------------------------------------------------------------


def profile_repository(root: str | Path, *, name: str | None = None) -> RepositoryProfile:
    """Describe a repository from its contents alone.

    Deterministic: no network, no Docker, no model, no clock. The same tree always
    produces the same profile, which is what makes this testable against fixtures rather
    than against a running system.
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    walk = _walk(root)
    files = walk.files
    languages = _languages(root, files)
    build_systems = _build_systems(root, files)
    definition = _build_definition(root, files)
    install, build, test = _commands(root, files, build_systems)

    manifests = tuple(
        Detection(rel.name, _posix(rel), 1.0)
        for rel in files
        if len(rel.parts) <= 2 and rel.name in _MANIFESTS
    )

    notes: list[str] = []
    if definition is None:
        notes.append(
            "No Dockerfile or compose file found, so intake/runner.py cannot deploy this "
            "repository. v1 requires the repository to describe its own build (see §6.2)."
        )
    elif definition.value == "compose":
        notes.append(
            "Compose project: detected and policy-gated, but intake/runner.py deliberately "
            "refuses to execute compose files, so this repository is not deployable by the "
            "current pipeline."
        )
    if not languages:
        notes.append("No recognised source files; the profile is structural only.")
    if walk.truncated:
        notes.append(
            f"Traversal was truncated ({', '.join(walk.truncated)}); the profile describes "
            f"part of the repository, not all of it."
        )

    profile = RepositoryProfile(
        name=name or root.resolve().name,
        root=str(root),
        languages=languages,
        build_systems=build_systems,
        frameworks=_frameworks(root, files),
        entry_points=_entry_points(root, files),
        test_targets=_test_targets(root, files),
        dependency_manifests=manifests,
        docs=_docs(root, files),
        build_definition=definition,
        install_commands=install,
        build_commands=build,
        test_commands=test,
        stats=ProfileStats(
            files_scanned=walk.stats_files,
            directories_scanned=walk.stats_dirs,
            ignored_directories=walk.stats_ignored,
            source_bytes=sum(entry.bytes for entry in languages),
            truncated=",".join(walk.truncated),
        ),
        notes=tuple(notes),
    )
    logger.info(
        "profiled {}: {} files, primary language {!r}, build systems {}, deployable={}",
        profile.name, profile.stats.files_scanned, profile.primary_language,
        [d.value for d in profile.build_systems] or "none", profile.deployable,
    )
    return profile


def profile_for_run(root: str | Path) -> dict:
    """A JSON-safe profile block for a run's metadata, or an explicit unavailable marker.

    **Never raises, and never fabricates.** Profiling is a diagnostic that decorates a
    run; a repository that cannot be profiled — unreadable, a broken symlink, a
    permission error — must not be able to fail the run that was going to test it. But
    the failure has to be *visible*, because a silently absent profile and a repository
    with nothing in it look identical downstream, and a plausible-looking empty profile
    would be worse than either.

    So both outcomes carry `available`, explicitly:

    * success — the full `RepositoryProfile.to_dict()` plus ``"available": True``
    * failure — ``{"available": False, "error": "<ExceptionType>: <message>"}`` and
      nothing else, so no consumer can mistake a failure for a finding about the repo.

    The broad `except` is deliberate: the failure modes are filesystem-shaped and
    open-ended (permissions, encoding, races against a tree being written), and every one
    of them should degrade this block rather than abort the run.
    """
    try:
        payload = profile_repository(root).to_dict()
    except Exception as exc:  # noqa: BLE001 - a diagnostic must not fail the run it describes
        logger.warning("repository profiling failed for {}: {}: {}", root, type(exc).__name__, exc)
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    payload["available"] = True
    return payload
