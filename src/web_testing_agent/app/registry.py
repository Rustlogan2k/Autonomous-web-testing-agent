"""File-backed run registry. One directory per run, JSON inside it. No database.

**Why no database.** Everything the dashboard shows is already a file the research system
produces — `report.json`, `report.md`, the evidence tree — and a run is a handful of
scalars beside them. Adding PostgreSQL would mean a schema, a migration story and a
service to start before the UI works, in exchange for nothing the filesystem does not
already do at this scale. If concurrent multi-user access ever becomes a requirement this
is the module to replace, and it is the only one.

Layout, all under `var/app/` (gitignored):

    var/app/runs/<run_id>/run.json        the Run record
                         /report.json     BugReport.to_dict(), verbatim
                         /report.md       render_markdown(report), verbatim
                         /events.jsonl    the live activity stream, appended as it happens
                         /rollout.json    RolloutReport.to_dict()
                         /profile.json    RepositoryProfile
                         /evidence/       TraceRecorder output (pages, frames, trace.jsonl)

The report files are written by the *existing* renderers and are byte-identical to what
`scripts/run_repo.py` would have produced, which is what makes "Download JSON/Markdown"
an export of the research artifact rather than a reformatting of it.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Iterator

from ..utils.logging import get_logger
from .models import Run, RunEvent, RunStatus, new_run_id

logger = get_logger(__name__)

RUN_FILE = "run.json"
REPORT_JSON = "report.json"
REPORT_MD = "report.md"
ROLLOUT_JSON = "rollout.json"
PROFILE_JSON = "profile.json"
EVENTS_FILE = "events.jsonl"
EVIDENCE_DIR = "evidence"

#: How hard to try the atomic rename before giving up on atomicity. See `_write_json`.
_REPLACE_ATTEMPTS = 6
_REPLACE_BACKOFF_S = 0.04


class RunRegistry:
    """Create, persist and enumerate runs.

    Every mutating method takes the lock: the web thread reads runs while a worker thread
    writes them, and a half-written `run.json` read by the dashboard is exactly the kind
    of intermittent failure that is miserable to diagnose. Writes go through a temp file
    and `replace()` for the same reason.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- creation ------------------------------------------------------------------

    def next_sequence(self) -> int:
        with self._lock:
            existing = [self._read_sequence(d) for d in self.runs_dir.iterdir() if d.is_dir()]
            return max([s for s in existing if s], default=0) + 1

    def create(self, source, settings, agent_label: str = "") -> Run:  # noqa: ANN001
        with self._lock:
            sequence = self.next_sequence()
            run = Run(
                run_id=new_run_id(sequence),
                sequence=sequence,
                created_at=time.time(),
                status=RunStatus.CREATED,
                source=source,
                settings=settings,
                agent_label=agent_label,
            )
            self.directory(run.run_id).mkdir(parents=True, exist_ok=True)
            self.save(run)
            return run

    # -- paths ---------------------------------------------------------------------

    def directory(self, run_id: str) -> Path:
        """The run's directory. `run_id` is validated because it reaches the filesystem."""
        safe = "".join(c for c in str(run_id) if c.isalnum() or c in "-_")
        if not safe or safe != str(run_id):
            raise ValueError(f"invalid run id: {run_id!r}")
        return self.runs_dir / safe

    def evidence_dir(self, run_id: str) -> Path:
        return self.directory(run_id) / EVIDENCE_DIR

    def artifact(self, run_id: str, name: str) -> Path | None:
        path = self.directory(run_id) / name
        return path if path.is_file() else None

    # -- persistence ---------------------------------------------------------------

    def save(self, run: Run) -> None:
        with self._lock:
            self._write_json(self.directory(run.run_id) / RUN_FILE, run.to_dict())

    def get(self, run_id: str) -> Run | None:
        try:
            path = self.directory(run_id) / RUN_FILE
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            return Run.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001 - a corrupt run must not break the list
            logger.warning("unreadable run record {}: {}", path, exc)
            return None

    def list(self) -> list[Run]:
        """Newest first. A directory that cannot be parsed is skipped, not fatal."""
        with self._lock:
            runs = [self.get(d.name) for d in self.runs_dir.iterdir() if d.is_dir()]
        return sorted([r for r in runs if r is not None],
                      key=lambda r: r.created_at, reverse=True)

    def write_artifact(self, run_id: str, name: str, text: str) -> Path:
        """Write one artifact, through the same retrying atomic write `save` uses."""
        path = self.directory(run_id) / name
        self._write_text(path, text)
        return path

    def read_json(self, run_id: str, name: str) -> dict | None:
        path = self.artifact(run_id, name)
        if path is None:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("unreadable artifact {}: {}", path, exc)
            return None

    # -- events --------------------------------------------------------------------

    def append_event(self, run_id: str, event: RunEvent) -> None:
        """Append one line to `events.jsonl`.

        Line-delimited rather than a JSON array so a run killed mid-flight still leaves a
        readable log, and so appending costs no read of what is already there.
        """
        path = self.directory(run_id) / EVENTS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict()) + "\n")

    def events(self, run_id: str, offset: int = 0) -> list[dict]:
        """Every recorded event from `offset` onward, for a page load or a reconnect."""
        path = self.directory(run_id) / EVENTS_FILE
        if not path.is_file():
            return []
        out: list[dict] = []
        with path.open(encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index < offset or not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    # -- aggregates the dashboard and findings page need ---------------------------

    def findings(self) -> Iterator[dict]:
        """Every reported bug across every run, flattened, newest run first.

        Read from each run's `report.json` — the *existing* `BugReport.to_dict()` shape —
        so a finding shown in the UI is the same record the Markdown report renders and
        nothing is re-derived or invented.
        """
        for run in self.list():
            report = self.read_json(run.run_id, REPORT_JSON)
            if not report:
                continue
            for index, bug in enumerate(report.get("bugs") or []):
                yield {
                    **bug,
                    "index": index,
                    "run_id": run.run_id,
                    "run_display": run.display_id,
                    "application": run.source.name,
                    "discovered_at": run.finished_at or run.created_at,
                }

    def stats(self) -> dict:
        runs = self.list()
        findings = list(self.findings())
        applications = {r.source.name for r in runs if r.source.name}
        by_severity = {"high": 0, "medium": 0, "low": 0}
        for finding in findings:
            label = _severity_label(finding.get("severity", 0.0))
            by_severity[label] = by_severity.get(label, 0) + 1
        return {
            "total_runs": len(runs),
            "completed_runs": sum(1 for r in runs if r.status is RunStatus.COMPLETED),
            "failed_runs": sum(1 for r in runs if r.status is RunStatus.FAILED),
            "active_runs": sum(1 for r in runs if r.status.is_active),
            "findings": len(findings),
            "applications": len(applications),
            "by_severity": by_severity,
        }

    # -- internals -----------------------------------------------------------------

    def _read_sequence(self, directory: Path) -> int:
        path = directory / RUN_FILE
        if not path.is_file():
            return 0
        try:
            return int(json.loads(path.read_text(encoding="utf-8")).get("sequence", 0))
        except Exception:  # noqa: BLE001
            return 0

    @classmethod
    def _write_text(cls, path: Path, text: str) -> None:
        """Atomic write with the retry described in `_write_json`, for plain text."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                tmp.replace(path)
                return
            except (PermissionError, OSError):
                if attempt == _REPLACE_ATTEMPTS - 1:
                    break
                time.sleep(_REPLACE_BACKOFF_S * (attempt + 1))
        logger.warning("atomic replace kept failing for {}; writing in place", path.name)
        try:
            path.write_text(text, encoding="utf-8")
        finally:
            tmp.unlink(missing_ok=True)

    @classmethod
    def _write_json(cls, path: Path, payload: dict) -> None:
        """Write a JSON document atomically, retrying a transient lock on the destination.

        **Why the retry is not defensive padding.** This repository lives inside a
        OneDrive-synced folder (§10 of `PROJECT_CONTEXT.md` records the same problem for
        `.git`), and a sync agent or indexer holding `run.json` open for a few milliseconds
        makes `os.replace` fail with `WinError 5` — measured, not hypothetical: it killed a
        live run during the first end-to-end test. A run record is rewritten roughly once a
        second while a run is in flight, so a rare collision becomes a near-certainty over
        a few dozen runs.
        """
        cls._write_text(path, json.dumps(payload, indent=2, default=str))


def _severity_label(severity: float) -> str:
    """Matches `ReportedBug.severity_label` exactly; duplicated only because this reads
    a serialised dict rather than the dataclass."""
    value = float(severity or 0.0)
    if value >= 7.0:
        return "high"
    return "medium" if value >= 4.0 else "low"
