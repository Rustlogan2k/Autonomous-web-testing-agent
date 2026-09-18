"""The application layer's own vocabulary: runs, their lifecycle, and their events.

**Deliberately separate from the research types.** `BugReport`, `ReportedBug`,
`RolloutReport` and `RepositoryProfile` are the research system's data model and are used
here unchanged — this module adds only what a *product* needs and the research layer has
no reason to know about: a run identity, a lifecycle state machine, an event log, and the
handful of knobs a user (rather than a researcher) is allowed to turn.

Nothing in `web_testing_agent.app` is imported by anything outside it. The dependency
arrow points one way: the product layer calls the research layer, never the reverse.
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


class RunStatus(str, enum.Enum):
    """The lifecycle of one test run.

    Ordered as the run experiences them. `COMPLETED`, `FAILED` and `STOPPED` are
    terminal; everything else is transient and implies a worker thread is alive and a
    sandbox may exist.
    """

    CREATED = "created"
    VALIDATING = "validating"
    BUILDING = "building"
    STARTING = "starting"
    TESTING = "testing"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.STOPPED)

    @property
    def is_active(self) -> bool:
        return not self.is_terminal

    @property
    def label(self) -> str:
        return {
            RunStatus.CREATED: "Created",
            RunStatus.VALIDATING: "Validating",
            RunStatus.BUILDING: "Building image",
            RunStatus.STARTING: "Starting application",
            RunStatus.TESTING: "Testing",
            RunStatus.REPORTING: "Generating report",
            RunStatus.COMPLETED: "Completed",
            RunStatus.FAILED: "Failed",
            RunStatus.STOPPED: "Stopped",
        }[self]


#: Coarse component health shown in the run page's status column. These are *observed*
#: states derived from real pipeline events, not a script.
class ComponentState(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AgentChoice:
    """One selectable exploration policy, and an honest description of what it is.

    `available` is resolved at runtime — a research checkpoint that is not on disk must
    not be offered, and the UI says why rather than failing when it is chosen.

    **This is the seam the finalized RL model plugs into.** Adding an agent means adding
    an entry here and a branch in `agents.build_policy`; no template, route or frontend
    file changes.
    """

    key: str
    name: str
    summary: str
    research_stage: bool = False
    available: bool = True
    unavailable_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class RunEvent:
    """One line of the live activity stream.

    Every event originates in the real pipeline — a stage callback, a rollout step, or a
    caught exception. Nothing here is synthesised to make the stream look busy.
    """

    at: float
    kind: str
    message: str
    data: dict = field(default_factory=dict)

    @property
    def clock(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.at))

    def to_dict(self) -> dict:
        return {"at": self.at, "clock": self.clock, "kind": self.kind,
                "message": self.message, "data": self.data}


@dataclass(slots=True)
class RunProgress:
    """Live counters, all read off the running rollout rather than estimated."""

    episodes_done: int = 0
    episodes_total: int = 0
    steps: int = 0
    steps_total: int = 0
    states: int = 0
    findings: int = 0
    current_url: str = ""

    @property
    def fraction(self) -> float:
        if self.steps_total <= 0:
            return 0.0
        return min(1.0, self.steps / self.steps_total)

    def to_dict(self) -> dict:
        return {**asdict(self), "fraction": round(self.fraction, 4)}


@dataclass(slots=True)
class TestSettings:
    """The knobs a *user* is allowed to turn.

    Deliberately four, not forty. The research hyperparameters (reward weights, n-step,
    PER alpha/beta, epsilon schedule, archive `p_return`, observation width) are not
    exposed and are not settable from here — they belong to the experiment protocol and
    changing them from a web form would silently invalidate every published figure.
    """

    episodes: int = 2
    steps: int = 25
    agent: str = "random"
    seed: int = 0

    def clamped(self) -> "TestSettings":
        """Bound to what the product supports, so a hand-edited request cannot ask for a
        12-hour run or a zero-step one."""
        return TestSettings(
            episodes=max(1, min(int(self.episodes), 20)),
            steps=max(5, min(int(self.steps), 200)),
            agent=self.agent,
            seed=max(0, min(int(self.seed), 2**31 - 1)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class SourceInfo:
    """What is being tested and where it came from.

    `kind` is `"demo"` for a bundled fixture and `"upload"` for a user-supplied archive.
    `workspace` is the temporary directory an upload was extracted into; it is deleted on
    cleanup and is never inside the project tree.
    """

    kind: str = "upload"
    name: str = ""
    size_bytes: int = 0
    file_count: int = 0
    workspace: str = ""
    build_kind: str = ""
    build_file: str = ""
    dockerfile_found: bool = False
    exposed_ports: list[int] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    validation_ok: bool = False
    validation_message: str = ""
    policy_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def new_run_id(sequence: int) -> str:
    """`001`-style display ids, with a uuid suffix so two processes cannot collide."""
    return f"{sequence:03d}-{uuid.uuid4().hex[:8]}"


@dataclass(slots=True)
class Run:
    """One test run, start to finish. Serialised to `run.json` inside the run directory."""

    run_id: str
    sequence: int
    created_at: float
    status: RunStatus = RunStatus.CREATED
    source: SourceInfo = field(default_factory=SourceInfo)
    settings: TestSettings = field(default_factory=TestSettings)
    started_at: float = 0.0
    finished_at: float = 0.0
    base_url: str = ""
    preview_url: str = ""
    image_tag: str = ""
    container_id: str = ""
    error: str = ""
    error_detail: str = ""
    progress: RunProgress = field(default_factory=RunProgress)
    components: dict[str, str] = field(default_factory=lambda: {
        "environment": ComponentState.PENDING.value,
        "browser": ComponentState.PENDING.value,
        "agent": ComponentState.PENDING.value,
    })
    counts: dict[str, int] = field(default_factory=dict)
    agent_label: str = ""

    @property
    def display_id(self) -> str:
        return f"#{self.sequence:03d}"

    @property
    def is_active(self) -> bool:
        """Forwarded from the status so templates can ask the run directly.

        `to_dict()` already exposes this to the JSON API; without the property the server
        -rendered templates and the API would have to spell the same question two ways.
        """
        return self.status.is_active

    @property
    def duration_s(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    @property
    def duration_human(self) -> str:
        total = int(self.duration_s)
        if total < 60:
            return f"{total}s"
        return f"{total // 60}m {total % 60:02d}s"

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "sequence": self.sequence,
            "display_id": self.display_id,
            "created_at": self.created_at,
            "status": self.status.value,
            "status_label": self.status.label,
            "is_active": self.status.is_active,
            "source": self.source.to_dict(),
            "settings": self.settings.to_dict(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.duration_s, 1),
            "duration_human": self.duration_human,
            "base_url": self.base_url,
            "preview_url": self.preview_url,
            "image_tag": self.image_tag,
            "container_id": self.container_id,
            "error": self.error,
            "progress": self.progress.to_dict(),
            "components": dict(self.components),
            "counts": dict(self.counts),
            "agent_label": self.agent_label,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Run":
        """Rebuild from `run.json`. Unknown/missing keys fall back to defaults so a run
        written by an older build still loads rather than crashing the dashboard."""
        run = cls(
            run_id=str(data.get("run_id", "")),
            sequence=int(data.get("sequence", 0)),
            created_at=float(data.get("created_at", 0.0)),
            status=_status_of(data.get("status")),
            started_at=float(data.get("started_at", 0.0) or 0.0),
            finished_at=float(data.get("finished_at", 0.0) or 0.0),
            base_url=str(data.get("base_url", "")),
            preview_url=str(data.get("preview_url", "")),
            image_tag=str(data.get("image_tag", "")),
            container_id=str(data.get("container_id", "")),
            error=str(data.get("error", "")),
            error_detail=str(data.get("error_detail", "")),
            counts=dict(data.get("counts") or {}),
            agent_label=str(data.get("agent_label", "")),
        )
        source = dict(data.get("source") or {})
        run.source = SourceInfo(**{k: v for k, v in source.items()
                                   if k in SourceInfo.__slots__})
        settings = dict(data.get("settings") or {})
        run.settings = TestSettings(**{k: v for k, v in settings.items()
                                       if k in TestSettings.__slots__})
        progress = dict(data.get("progress") or {})
        run.progress = RunProgress(**{k: v for k, v in progress.items()
                                      if k in RunProgress.__slots__})
        run.components = dict(data.get("components") or run.components)
        return run


def _status_of(value: Any) -> RunStatus:
    try:
        return RunStatus(str(value))
    except ValueError:
        return RunStatus.FAILED
