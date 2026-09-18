"""Accepting an untrusted ZIP and turning it into a directory, without trusting it.

**The threat here is the archive itself, before any container exists.** Extraction runs on
the host as the server process, so every classic archive attack applies and has to be
refused rather than contained:

* **Zip-slip.** An entry named `../../../.ssh/authorized_keys` or `C:\\Windows\\...`
  escapes the destination. Every entry's resolved path is checked to be inside the
  workspace, and the check is on the *resolved* path so `a/../../b` cannot pass by
  looking relative.
* **Symlinks.** A ZIP can carry a symlink entry (`S_IFLNK` in the external attributes);
  extracting one and then writing "through" it escapes the workspace just as effectively.
  Link entries are refused outright.
* **Zip bombs.** A 1 MB archive can expand to gigabytes. Both the compressed upload and
  the declared uncompressed total are capped before a single byte is written, and the
  running total is checked again during extraction because the declared size is attacker-
  controlled metadata.
* **Entry-count exhaustion.** Hundreds of thousands of tiny files exhaust inodes and make
  cleanup slow. Capped.
* **Absolute and device paths.** Refused.

**What this module does not claim.** Passing these checks means the archive is safe to
*unpack*, not that the repository is safe to *run*. Running it is `deploy_repository`'s
job, under `SANDBOX_RUN_ARGS`, and that boundary has its own documented limitation: the
repository's Dockerfile `RUN` lines execute during build, before any `docker run`
restriction exists. See `SECURITY.md` in this package's docs section of the README.
"""

from __future__ import annotations

import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..utils.logging import get_logger

logger = get_logger(__name__)

#: Caps. Generous enough for a real application repository, small enough that a hostile
#: archive cannot fill the disk before being refused.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024          # 200 MB compressed
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024   # 1 GB expanded
MAX_ENTRIES = 20_000
MAX_RATIO = 200                               # expanded : compressed

#: Temp workspaces are named so an orphan is identifiable in `%TEMP%` and can be swept.
WORKSPACE_PREFIX = "wta-upload-"


class UploadError(ValueError):
    """An archive this application refuses to unpack, with a user-safe message.

    The message is written to be shown in the UI: it says what is wrong and what to do,
    and never leaks a host path or a stack trace.
    """


@dataclass(frozen=True, slots=True)
class ExtractedUpload:
    """Where an accepted archive landed, and what was in it."""

    workspace: Path
    root: Path
    name: str
    compressed_bytes: int
    uncompressed_bytes: int
    file_count: int

    def cleanup(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)


def _is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    """True for a link entry, read from the Unix mode in the external attributes."""
    mode = info.external_attr >> 16
    return bool(mode) and stat.S_ISLNK(mode)


def _unsafe_name(name: str) -> str | None:
    """Why this entry name is unacceptable, or None if it is fine."""
    if not name or name in (".", ".."):
        return "an empty or relative entry name"
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return f"an absolute path ({name!r})"
    # Windows drive letters and UNC paths.
    if len(normalized) >= 2 and normalized[1] == ":":
        return f"a drive-qualified path ({name!r})"
    if normalized.startswith("//"):
        return f"a UNC path ({name!r})"
    if any(part == ".." for part in normalized.split("/")):
        return f"a parent-directory traversal ({name!r})"
    return None


def inspect_archive(data: bytes, filename: str) -> None:
    """Refuse an archive before writing anything. Raises `UploadError` with a reason."""
    if not data:
        raise UploadError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(
            f"The archive is {len(data) / (1024 * 1024):.0f} MB, above the "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."
        )
    if not str(filename).lower().endswith(".zip"):
        raise UploadError("Only .zip archives are accepted.")


def extract_zip(data: bytes, filename: str) -> ExtractedUpload:
    """Validate and unpack into a fresh temp workspace outside the project tree.

    The workspace comes from `tempfile.mkdtemp()`, so it is under the OS temp directory
    and never inside the repository — an uploaded archive cannot overwrite project files
    even if every other check were to fail.
    """
    inspect_archive(data, filename)

    workspace = Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX))
    root = workspace / "repo"
    root.mkdir(parents=True, exist_ok=True)

    try:
        return _extract_into(data, filename, workspace, root)
    except UploadError:
        shutil.rmtree(workspace, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001 - anything else is still a bad archive
        shutil.rmtree(workspace, ignore_errors=True)
        logger.warning("archive extraction failed for {}: {}: {}", filename,
                       type(exc).__name__, exc)
        raise UploadError("The archive could not be read. It may be corrupt.") from exc


def _extract_into(data: bytes, filename: str, workspace: Path, root: Path) -> ExtractedUpload:
    import io

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.testzip() is not None:
            raise UploadError("The archive is corrupt (failed its own CRC check).")

        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise UploadError(
                f"The archive contains {len(entries)} entries, above the "
                f"{MAX_ENTRIES} limit."
            )

        declared = sum(info.file_size for info in entries)
        if declared > MAX_UNCOMPRESSED_BYTES:
            raise UploadError(
                f"The archive expands to {declared / (1024 ** 3):.1f} GB, above the "
                f"{MAX_UNCOMPRESSED_BYTES // (1024 ** 3)} GB limit."
            )
        if len(data) and declared / max(len(data), 1) > MAX_RATIO:
            raise UploadError(
                "The archive's compression ratio looks like a zip bomb and was refused."
            )

        for info in entries:
            reason = _unsafe_name(info.filename)
            if reason is not None:
                raise UploadError(f"The archive contains {reason} and was refused.")
            if _is_symlink_entry(info):
                raise UploadError(
                    f"The archive contains a symbolic link ({info.filename!r}), which is "
                    f"not supported and was refused."
                )

        written = 0
        files = 0
        resolved_root = root.resolve()
        for info in entries:
            target = (root / info.filename).resolve()
            # The decisive check, on the resolved path: whatever the entry name looked
            # like, the file must land inside the workspace.
            if not _within(target, resolved_root):
                raise UploadError(
                    f"The archive tried to write outside the workspace "
                    f"({info.filename!r}) and was refused."
                )
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            written += info.file_size
            if written > MAX_UNCOMPRESSED_BYTES:
                raise UploadError("The archive expanded past its size limit during extraction.")

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink, length=1 << 20)
            files += 1

    if files == 0:
        raise UploadError("The archive contains no files.")

    return ExtractedUpload(
        workspace=workspace,
        root=collapse_single_root(root),
        name=_repo_name(filename),
        compressed_bytes=len(data),
        uncompressed_bytes=written,
        file_count=files,
    )


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def collapse_single_root(root: Path) -> Path:
    """Descend through a single wrapper directory, which is how GitHub zips are shaped.

    `repo-main.zip` unpacks to `repo-main/<the actual repository>`, and pointing the
    deployer at the wrapper means `detect_build_definition` looks for a Dockerfile one
    level too high. Only collapses when there is exactly one entry and it is a directory,
    so a repository that genuinely has one top-level folder plus a Dockerfile is untouched.
    """
    current = root
    for _ in range(3):
        entries = [e for e in current.iterdir() if not e.name.startswith("__MACOSX")]
        if len(entries) == 1 and entries[0].is_dir():
            current = entries[0]
            continue
        break
    return current


def directory_stats(root: Path) -> tuple[int, int]:
    """(bytes, file count) for an extracted repository, for the UI's summary."""
    total = 0
    count = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
            count += 1
    return total, count


def _repo_name(filename: str) -> str:
    stem = Path(str(filename)).stem or "repository"
    return stem[:80]


def sweep_orphan_workspaces() -> int:
    """Delete leftover upload workspaces from previous processes. Returns how many.

    Called at startup. A run killed with SIGKILL cannot clean up after itself, and these
    directories are otherwise invisible until the disk fills.
    """
    removed = 0
    temp_root = Path(tempfile.gettempdir())
    try:
        candidates = list(temp_root.glob(f"{WORKSPACE_PREFIX}*"))
    except OSError:
        return 0
    for path in candidates:
        if not path.is_dir():
            continue
        try:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
        except OSError:
            continue
    return removed
