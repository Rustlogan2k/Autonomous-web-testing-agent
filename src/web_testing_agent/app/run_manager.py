"""Drives one test run on a worker thread and reports what actually happened.

**This is an orchestration layer, not a reimplementation.** The whole run is one call to
`pipeline.run_pipeline` with adapters injected:

    deployer        -> intake.deploy_repository (upload) | serve_directory (demo)
    detector        -> intake.detect_build_definition (upload) | static stand-in (demo)
    policy_factory  -> app.agents.build_policy
    recorder_factory-> TraceRecorder, wrapped so steps also reach the event stream
    observer        -> stage callbacks turned into events and status transitions

Nothing about the reward, observation, action space, agent, environment or report is
touched. The report this writes is `build_report(...)`'s output and the Markdown is
`render_markdown(...)`'s, both verbatim, so a report downloaded from the UI is the same
artifact `scripts/run_repo.py` writes.

**Live events are real.** They come from three places, all of them the running system:
`run_pipeline`'s stage observer, the rollout's own `Recorder.on_step` hook, and caught
exceptions. There is no timer inventing plausible-looking activity.

**Cancellation.** `stop()` sets a flag the step recorder checks. On the next step the
recorder raises `RunStopped`, which unwinds through `run_rollout` into the deployer's
`finally` — so the browser closes and the container is removed by the same teardown path a
successful run uses. Nothing is killed from outside, because a killed worker leaks a
container.
"""

from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from ..utils.logging import get_logger
from . import agents as agent_registry
from .models import ComponentState, Run, RunEvent, RunStatus, SourceInfo, TestSettings
from .registry import (
    PROFILE_JSON,
    REPORT_JSON,
    REPORT_MD,
    ROLLOUT_JSON,
    RunRegistry,
)

logger = get_logger(__name__)

#: Guard on a single run's total wall clock. A repository that builds forever or an
#: application that never settles must not pin a worker thread indefinitely.
RUN_DEADLINE_S = 45 * 60


class RunStopped(RuntimeError):
    """Raised inside the rollout when the user asks for a stop. Not an error condition."""


class _StepStream:
    """A `Recorder` that forwards to the real one and emits live events on the way.

    Implements the `evaluation.rollout.Recorder` protocol (`on_reset`, `on_step`), so
    `run_rollout` drives it exactly as it drives a `TraceRecorder`. Wrapping rather than
    replacing means evidence capture is unaffected: every call is passed through first.

    `emit_every` throttles the *event* stream only — the underlying recorder always sees
    every step, because the trace is evidence and must not be sampled.
    """

    def __init__(self, inner, on_event, should_stop, progress, emit_every: int = 1) -> None:  # noqa: ANN001
        self._inner = inner
        self._on_event = on_event
        self._should_stop = should_stop
        self._progress = progress
        self._emit_every = max(1, emit_every)
        self._states: set[str] = set()

    def on_reset(self, observation: dict, info: dict) -> None:
        if self._inner is not None:
            self._inner.on_reset(observation, info)
        self._progress.episodes_done += 1
        url = _url_of(info)
        self._progress.current_url = url
        self._on_event("episode", f"Episode {self._progress.episodes_done} started at {_short(url)}",
                       {"episode": self._progress.episodes_done, "url": url})

    def on_step(self, action: int, observation: dict, reward: float,  # noqa: ANN001, PLR0913
                terminated: bool, truncated: bool, info: dict) -> None:
        if self._inner is not None:
            self._inner.on_step(action, observation, reward, terminated, truncated, info)

        self._progress.steps += 1
        key = str(info.get("state_key", ""))
        if key and key not in self._states:
            self._states.add(key)
            self._progress.states = len(self._states)
        url = _url_of(info)
        self._progress.current_url = url

        signals = info.get("bug_signals")
        if signals is not None and getattr(signals, "any_triggered", False):
            self._progress.findings += 1
            self._on_event("finding", f"Signal detected at {_short(url)}",
                           {"url": url, "step": self._progress.steps})

        if self._progress.steps % self._emit_every == 0:
            self._on_event("action", _describe_step(info, reward),
                           {"step": self._progress.steps, "url": url,
                            "reward": round(float(reward), 3)})

        # Checked after the step is recorded, so a stop never discards evidence that has
        # already been produced.
        if self._should_stop():
            raise RunStopped("stopped by user")

    def close(self) -> None:
        if self._inner is not None:
            self._inner.close()

    @property
    def root(self):  # noqa: ANN201
        """Forwarded so `pipeline._evidence_index` finds the real recorder's directory."""
        return getattr(self._inner, "root", None)


def _url_of(info: dict) -> str:
    return str((info.get("page") or {}).get("url", ""))


def _short(url: str) -> str:
    if not url:
        return "(unknown)"
    tail = url.rsplit("/", 1)[-1]
    return tail or url


def _describe_step(info: dict, reward: float) -> str:
    """One human line describing what the agent just did, from the real action spec."""
    spec = info.get("action_spec")
    if spec is None:
        return f"Step executed (reward {reward:+.2f})"
    kind = getattr(getattr(spec, "action_type", None), "value", "ACTION")
    label = (getattr(spec, "element_id", "") or getattr(spec, "description", "")).strip()
    label = label[:60]
    where = _short(_url_of(info))
    if label:
        return f'{kind} "{label}" on {where}'
    return f"{kind} on {where}"


class RunManager:
    """Owns every in-flight run: starts them, tracks them, stops them, cleans up."""

    def __init__(self, registry: RunRegistry) -> None:
        self.registry = registry
        self._threads: dict[str, threading.Thread] = {}
        self._stops: dict[str, threading.Event] = {}
        self._subscribers: dict[str, list] = {}
        self._lock = threading.RLock()

    # -- lifecycle -----------------------------------------------------------------

    def start(self, run: Run, repo: Path, deployer, detector,  # noqa: ANN001, PLR0913
              on_cleanup=None, overrides: dict | None = None) -> None:
        """Launch the worker for `run`. Returns immediately; the UI polls or streams.

        `overrides` replaces named `PipelineDependencies` fields — the same injection seam
        `run_pipeline` already provides, exposed one level up. It exists so the
        application layer can be tested without a browser or a daemon, and so a future
        caller can substitute a stage without this module growing a branch for it. Left
        `None` in production, where every stage is the real one.
        """
        stop = threading.Event()
        thread = threading.Thread(
            target=self._execute, name=f"run-{run.run_id}",
            args=(run, Path(repo), deployer, detector, stop, on_cleanup, overrides or {}),
            daemon=True,
        )
        with self._lock:
            self._stops[run.run_id] = stop
            self._threads[run.run_id] = thread
        thread.start()

    def stop(self, run_id: str) -> bool:
        """Ask a run to stop at its next step. Teardown then follows the normal path."""
        with self._lock:
            event = self._stops.get(run_id)
        if event is None:
            return False
        event.set()
        return True

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            thread = self._threads.get(run_id)
        return bool(thread and thread.is_alive())

    def shutdown(self, timeout: float = 30.0) -> None:
        """Ask every live run to stop and wait, so no container outlives the process."""
        with self._lock:
            events = list(self._stops.values())
            threads = list(self._threads.values())
        for event in events:
            event.set()
        deadline = time.time() + timeout
        for thread in threads:
            remaining = max(0.0, deadline - time.time())
            thread.join(timeout=remaining)
        alive = [t.name for t in threads if t.is_alive()]
        if alive:
            logger.warning("runs still finishing at shutdown: {}", alive)

    # -- event fan-out --------------------------------------------------------------

    def subscribe(self, run_id: str):  # noqa: ANN201
        """A queue receiving this run's events live. Used by the SSE endpoint."""
        import queue

        channel: "queue.Queue[dict]" = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.setdefault(run_id, []).append(channel)
        return channel

    def unsubscribe(self, run_id: str, channel) -> None:  # noqa: ANN001
        with self._lock:
            channels = self._subscribers.get(run_id) or []
            if channel in channels:
                channels.remove(channel)

    def _publish(self, run_id: str, payload: dict) -> None:
        with self._lock:
            channels = list(self._subscribers.get(run_id) or [])
        for channel in channels:
            try:
                channel.put_nowait(payload)
            except Exception:  # noqa: BLE001 - a slow client must not stall the run
                continue

    # -- the worker ------------------------------------------------------------------

    def _execute(self, run: Run, repo: Path, deployer, detector,  # noqa: ANN001, PLR0913, PLR0915
                 stop: threading.Event, on_cleanup, overrides: dict | None = None) -> None:
        from ..pipeline import (
            STAGE_DEPLOYED,
            STAGE_DETECTED,
            STAGE_PROFILED,
            STAGE_REPORT,
            STAGE_ROLLOUT,
            PipelineDependencies,
            RunSettings,
            run_pipeline,
        )

        deadline = time.time() + RUN_DEADLINE_S

        def emit(kind: str, message: str, data: dict | None = None) -> None:
            event = RunEvent(at=time.time(), kind=kind, message=message, data=data or {})
            self.registry.append_event(run.run_id, event)
            self._publish(run.run_id, {"event": event.to_dict(), "run": run.to_dict()})

        def persist(status: RunStatus | None = None) -> None:
            if status is not None:
                run.status = status
            self.registry.save(run)

        def should_stop() -> bool:
            if stop.is_set():
                return True
            if time.time() > deadline:
                stop.set()
                emit("error", "Run exceeded its time limit and was stopped.")
                return True
            return False

        run.started_at = time.time()
        persist(RunStatus.VALIDATING)
        emit("status", f"Run {run.display_id} started")
        emit("info", f"Agent: {run.agent_label}")

        settings = RunSettings(
            episodes=run.settings.episodes,
            steps=run.settings.steps,
            seed=run.settings.seed,
            judge="stub",
            label=f"app-{run.run_id}",
            capture_screenshots=True,
        )
        run.progress.episodes_total = run.settings.episodes
        run.progress.steps_total = run.settings.episodes * run.settings.steps

        recorder_holder: dict[str, Any] = {}

        def observer(stage: str, data: dict) -> None:
            if stage == STAGE_PROFILED:
                profile = data.get("profile") or {}
                self.registry.write_artifact(
                    run.run_id, PROFILE_JSON, _json(profile))
                emit("stage", "Repository profiled")
            elif stage == STAGE_DETECTED:
                plan = data.get("plan")
                kind = getattr(plan, "kind", "")
                path = getattr(plan, "path", "")
                emit("stage", f"Build definition: {kind} ({Path(str(path)).name})")
                run.status = RunStatus.BUILDING
                run.components["environment"] = ComponentState.ACTIVE.value
                persist()
            elif stage == STAGE_DEPLOYED:
                deployment = data.get("deployment")
                run.base_url = getattr(deployment, "base_url", "")
                run.image_tag = getattr(deployment, "image_tag", "")
                run.container_id = str(getattr(deployment, "container_id", ""))[:12]
                run.preview_url = run.base_url
                run.components["environment"] = ComponentState.DONE.value
                run.components["browser"] = ComponentState.ACTIVE.value
                persist(RunStatus.STARTING)
                emit("stage", f"Application is answering at {run.base_url}",
                     {"base_url": run.base_url})
                if run.container_id:
                    emit("info", f"Container {run.container_id} running under the sandbox profile")
            elif stage == STAGE_ROLLOUT:
                run.components["agent"] = ComponentState.DONE.value
                persist(RunStatus.REPORTING)
                emit("stage", "Exploration finished; generating report")
            elif stage == STAGE_REPORT:
                emit("stage", "Report generated")

        def recorder_factory(evidence_dir, run_settings):  # noqa: ANN001
            from ..pipeline import default_recorder_factory

            inner = default_recorder_factory(evidence_dir, run_settings)
            recorder_holder["inner"] = inner
            run.components["browser"] = ComponentState.DONE.value
            run.components["agent"] = ComponentState.ACTIVE.value
            persist(RunStatus.TESTING)
            emit("stage", "Browser connected; agent exploring")
            stream = _StepStream(inner, emit_event(emit), should_stop, run.progress)
            recorder_holder["stream"] = stream
            return stream

        def emit_event(emitter):  # noqa: ANN001, ANN202
            last_saved = [0.0]

            def on_event(kind: str, message: str, data: dict) -> None:
                emitter(kind, message, data)
                # The run record carries live counters the dashboard reads; saving on
                # every step would be one fsync per browser action, so it is throttled.
                now = time.time()
                if now - last_saved[0] > 1.0:
                    last_saved[0] = now
                    try:
                        self.registry.save(run)
                    except OSError as exc:
                        # A progress snapshot is a convenience, not the record. The
                        # durable log is `events.jsonl`, already appended above, and the
                        # terminal save still has to succeed. Losing one mid-run snapshot
                        # to a transient file lock must not abort a run that is working —
                        # which is exactly what it did before this was caught.
                        logger.warning("progress snapshot skipped for {}: {}",
                                       run.run_id, exc)

            return on_event

        deps = PipelineDependencies(
            deployer=deployer,
            detector=detector,
            policy_factory=agent_registry.build_policy(run.settings.agent, run.settings),
            recorder_factory=recorder_factory,
        )
        if overrides:
            # `replace` rather than a fresh construction, so an override of one stage
            # cannot silently discard the observer/recorder wiring set up above.
            import dataclasses

            deps = dataclasses.replace(deps, **overrides)

        try:
            result = run_pipeline(
                repo, settings, deps,
                observer=observer,
                evidence_dir=self.registry.evidence_dir(run.run_id),
            )
            self._finish(run, result, emit, persist)

        except RunStopped:
            run.status = RunStatus.STOPPED
            run.finished_at = time.time()
            run.components = {k: ComponentState.DONE.value if v == ComponentState.ACTIVE.value
                              else v for k, v in run.components.items()}
            persist()
            emit("status", "Run stopped by user. Sandbox torn down.")

        except Exception as exc:  # noqa: BLE001 - every failure is reported, never raw
            message, detail = _explain(exc)
            run.status = RunStatus.FAILED
            run.finished_at = time.time()
            run.error = message
            run.error_detail = detail
            for name, state in run.components.items():
                if state == ComponentState.ACTIVE.value:
                    run.components[name] = ComponentState.ERROR.value
            persist()
            # The full traceback goes to the server log only; the UI gets the sentence.
            logger.error("run {} failed: {}", run.run_id, detail)
            emit("error", message)

        finally:
            with self._lock:
                self._stops.pop(run.run_id, None)
                self._threads.pop(run.run_id, None)
            if on_cleanup is not None:
                try:
                    on_cleanup()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("cleanup for run {} failed: {}", run.run_id, exc)
            self._publish(run.run_id, {"event": None, "run": run.to_dict(), "final": True})

    def _finish(self, run: Run, result, emit, persist) -> None:  # noqa: ANN001
        """Persist the research artifacts verbatim and close the run out."""
        from ..reporting import render_markdown

        if result.stopped_after_deploy:
            run.status = RunStatus.STOPPED
            run.finished_at = time.time()
            persist()
            return

        if result.rollout is not None:
            self.registry.write_artifact(
                run.run_id, ROLLOUT_JSON, _json(result.rollout.to_dict()))
            run.progress.states = int(result.rollout.unique_states or run.progress.states)

        if result.report is not None:
            payload = result.report.to_dict()
            self.registry.write_artifact(run.run_id, REPORT_JSON, _json(payload))
            self.registry.write_artifact(run.run_id, REPORT_MD, render_markdown(result.report))
            run.counts = dict(payload.get("counts") or {})
            run.progress.findings = int(run.counts.get("total", 0))
            emit("info", f"{run.counts.get('total', 0)} finding(s) reported")

        run.status = RunStatus.COMPLETED
        run.finished_at = time.time()
        run.components = {k: ComponentState.DONE.value for k in run.components}
        persist()
        emit("status", f"Run completed in {run.duration_human}")


def _json(payload: Any) -> str:
    import json

    return json.dumps(payload, indent=2, default=str)


def _explain(exc: BaseException) -> tuple[str, str]:
    """(user-facing sentence, server-log detail).

    Every branch names a cause the user can act on. The raw exception never reaches the
    browser; `detail` is the traceback and goes to the log.
    """
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    name = type(exc).__name__
    text = str(exc)

    from ..intake import BuildTimeout, HealthCheckTimeout, RepoIntakeError

    if isinstance(exc, HealthCheckTimeout):
        return ("The container started but the application never answered on its port. "
                "Check that it listens on 0.0.0.0 and that the Dockerfile EXPOSEs the "
                "right port.", detail)
    if isinstance(exc, BuildTimeout):
        return ("The Docker image took too long to build and was cancelled.", detail)
    if isinstance(exc, RepoIntakeError):
        return (text or "The repository could not be deployed.", detail)

    lowered = f"{name}: {text}".lower()
    if "docker" in lowered and ("connect" in lowered or "pipe" in lowered
                                or "daemon" in lowered or "not found" in lowered):
        return ("Could not reach the Docker daemon. Start Docker Desktop (or dockerd) and "
                "try again.", detail)
    if "playwright" in lowered or "browser" in lowered or "chromium" in lowered:
        return ("The browser could not be started or lost its connection. Run "
                "`python -m playwright install chromium` and try again.", detail)
    if isinstance(exc, TimeoutError) or "timeout" in lowered:
        return ("The run timed out waiting for the application.", detail)

    return (f"The run failed with an unexpected {name}. The server log has the details.",
            detail)
