"""Hunting on the end-to-end application suite (`e2e_suite/`, the v2 held-out set).

Two pieces the v1 benchmark path does not need:

- **Private builds.** The suite harness materialises variants into one shared directory per
  application; two parallel jobs on one application would rebuild each other's running
  servers. Each job here builds its clean, faulty and per-fault-control variants into its
  own temporary directory, with the harness's own copy and patch functions.
- **Both oracles.** Suite faults are stated either in the primary assertion grammar or with
  the suite's three extension kinds. `SuiteMonitor` runs the primary `FaultMonitor` for the
  former and an extension monitor, held to the same rules (per-fault control first, two
  reproductions, ddmin; bounded retries per piece of evidence), for the latter, and reports
  one summary in `FaultMonitor`'s shape so the analysis reads both alike.

Every server scopes its state to the session cookie (`e2e_suite/CONVENTIONS.md`), so each
episode's fresh browser context starts from a clean application state.
"""

from __future__ import annotations

import contextlib
import hashlib
import tempfile
from collections.abc import Iterator
from pathlib import Path

from ..benchmark.monitor import DEFAULT_REJECTION_CAP, MAX_ATTEMPTS, FaultMonitor, FaultRecord
from ..envs.types import ActionSpec
from ..oracle import build_trajectory, observed_record
from ..utils.logging import get_logger

logger = get_logger(__name__)


def _harness():
    from e2e_suite.harness import ext_oracle, launcher, manifest, variants

    return ext_oracle, launcher, manifest, variants


#: The v2 held-out validation set (plan amendment §12), laid out like the suite.
VALIDATION_APPS = Path(__file__).resolve().parents[3] / "validation_suite" / "apps"


def load_suite_app(app_id: str):
    """A suite or validation-set application by id (suite first)."""
    manifest = _harness()[2]
    if (manifest.APPS_ROOT / app_id / "app.json").is_file():
        return manifest.load(app_id)
    return manifest.load(VALIDATION_APPS / app_id)


def suite_app_ids() -> list[str]:
    return [app.app_id for app in _harness()[2].discover()]


@contextlib.contextmanager
def serve_suite_app(app, *, controls: bool = True) -> Iterator[dict[str, str]]:
    """Build every variant into a private directory, start the servers, yield their URLs."""
    _, launcher, _, variants = _harness()
    names = ["clean", "buggy"] + (
        [variants.control_key(p.fault_id) for p in app.patches] if controls else []
    )
    with tempfile.TemporaryDirectory(prefix=f"v2-{app.app_id}-") as workspace:
        directories = {}
        for name in names:
            dest = Path(workspace) / name
            variants._copy_runtime(app, dest)  # noqa: SLF001 - the harness's own build steps
            if name == "buggy":
                variants.apply_patches(dest, app.patches)
            elif name.startswith("without-"):
                excluded = name[len("without-") :]
                variants.apply_patches(dest, [p for p in app.patches if p.fault_id != excluded])
            directories[name] = dest
        entry = (app.data.get("entry") or "/").lstrip("/")
        with launcher.serve(app, directories) as urls:
            yield {name: url + entry for name, url in urls.items()}


class ExtensionMonitor:
    """`FaultMonitor`'s online detection and verification, for extension-oracle faults."""

    def __init__(
        self,
        app,
        *,
        faulty,
        clean,
        controls: dict,
        max_trials: int = 30,
        rejection_cap: int = DEFAULT_REJECTION_CAP,
        origin: str = "",
    ) -> None:
        ext_oracle = _harness()[0]
        #: The app's origin, for crash faults' same-origin request rule; each step's browser
        #: events (console, page, failed requests) are kept beside the trajectory, which the
        #: crash-fault kind (`console_or_request_error`) reads.
        self.origin = origin
        self._events: list[dict] = []
        self._ext = ext_oracle
        self.app = app
        self.oracle_app = app.oracle_app()
        self.regions = app.data.get("regions", {})
        self.faults = app.faults_by_oracle("extension")
        self.faulty, self.clean, self.controls = faulty, clean, controls
        self.max_trials, self.rejection_cap = max_trials, rejection_cap
        self.records = {f["fault_id"]: FaultRecord(f["fault_id"]) for f in self.faults}
        self.verification_seconds = 0.0
        self.verification_steps = 0
        self._episode: list[dict] = []
        self._attempts: dict[str, dict[tuple, int]] = {}

    def start_episode(self, info: dict) -> None:
        self._episode = [observed_record(info)]
        self._events = [self._ext.page_events(info, self.origin)]
        self._attempts = {}

    def observe(
        self,
        info: dict,
        executed: ActionSpec | None,
        available: list[ActionSpec] | None,
        budget_used: int,
    ) -> list:
        import time

        self._episode.append(observed_record(info, executed, available))
        self._events.append(self._ext.page_events(info, self.origin))
        trajectory = self._ext.with_events(build_trajectory(self._episode), self._events)
        confirmed = []
        for fault in self.faults:
            record = self.records[fault["fault_id"]]
            if record.verified_at is not None or record.cap_hit:
                continue
            verdict = self._ext.verify(fault, self.regions, self.oracle_app, trajectory)
            if record.reached_at is None and not verdict.inconclusive:
                record.reached_at = budget_used
            if not verdict.violated:
                continue
            if record.discovered_at is None:
                record.discovered_at = budget_used
            page = trajectory.steps[-1].html if getattr(trajectory, "steps", None) else ""
            evidence = (
                tuple(f"{r.kind}:{getattr(r, 'observed', '')}" for r in verdict.results),
                hashlib.sha256((page or "").encode("utf-8", "replace")).hexdigest()[:16],
            )
            attempts = self._attempts.setdefault(fault["fault_id"], {})
            if evidence not in attempts:
                record.candidates += 1
                attempts[evidence] = 0
            if attempts[evidence] < 0 or attempts[evidence] >= MAX_ATTEMPTS:
                continue
            attempts[evidence] += 1
            started = time.monotonic()
            finding = self._ext.confirm(
                fault,
                self.regions,
                self.oracle_app,
                faulty=self.faulty,
                control=self.controls[fault["fault_id"]],
                clean=self.clean,
                witness=list(trajectory.actions),
                max_trials=self.max_trials,
            )
            self.verification_seconds += time.monotonic() - started
            if finding.confirmed:
                record.verified_at = budget_used
                confirmed.append(finding)
                logger.info("{} verified at budget {}", fault["fault_id"], budget_used)
            else:
                reason = finding.rejected_because or "unknown"
                record.rejections[reason] += 1
                if reason in ("fires_without_its_own_patch", "fires_on_clean_build"):
                    attempts[evidence] = -1
                if sum(record.rejections.values()) >= self.rejection_cap:
                    record.cap_hit = True
        return confirmed


class SuiteMonitor:
    """The primary and the extension monitor driven as one, summarised as one."""

    def __init__(self, primary: FaultMonitor | None, extension: ExtensionMonitor) -> None:
        self.primary, self.extension = primary, extension

    @property
    def verification_seconds(self) -> float:
        return (
            self.primary.verification_seconds if self.primary else 0.0
        ) + self.extension.verification_seconds

    def start_episode(self, info: dict) -> None:
        if self.primary is not None:
            self.primary.start_episode(info)
        self.extension.start_episode(info)

    def observe(self, info, executed, available, budget_used):  # noqa: ANN001, ANN201
        found = (
            list(self.primary.observe(info, executed, available, budget_used))
            if self.primary
            else []
        )
        return found + self.extension.observe(info, executed, available, budget_used)

    def summary(self) -> dict:
        records = list(self.extension.records.values())
        base = (
            self.primary.summary()
            if self.primary is not None
            else {
                "seeded": 0,
                "reached": 0,
                "discovered": 0,
                "verified": 0,
                "first_verified_at": None,
                "candidates": 0,
                "rejections": {},
                "cap_hit": False,
                "verification_steps": 0,
                "verification_seconds": 0.0,
                "faults": [],
            }
        )
        stamps = [r.verified_at for r in records if r.verified_at is not None]
        first = [
            s for s in (base["first_verified_at"], min(stamps) if stamps else None) if s is not None
        ]
        rejections = dict(base["rejections"])
        for record in records:
            for reason, count in record.rejections.items():
                rejections[reason] = rejections.get(reason, 0) + count
        return {
            "seeded": base["seeded"] + len(records),
            "reached": base["reached"] + sum(r.reached_at is not None for r in records),
            "discovered": base["discovered"] + sum(r.discovered_at is not None for r in records),
            "verified": base["verified"] + sum(r.verified_at is not None for r in records),
            "first_verified_at": min(first) if first else None,
            "candidates": base["candidates"] + sum(r.candidates for r in records),
            "rejections": rejections,
            "cap_hit": base["cap_hit"] or any(r.cap_hit for r in records),
            "verification_steps": base["verification_steps"],
            "verification_seconds": round(self.verification_seconds, 2),
            "faults": base["faults"] + [{**r.to_dict(), "oracle": "extension"} for r in records],
        }
