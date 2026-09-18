"""FastAPI application: routes, API, and the server-sent event stream.

**Thin by design.** Every route either renders a template from the registry's files or
hands work to `RunManager`. There is no test logic here and no knowledge of rewards,
policies or environments — those live in the research package and reach this layer only
through `pipeline.run_pipeline`.

Page routes render server-side with Jinja2; the only JavaScript is a small progressive
enhancement for live updates and form handling. That choice keeps the whole product
inspectable with View Source, which matters more for a demonstrable final-year artifact
than a build toolchain would.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..utils.logging import get_logger
from . import agents as agent_registry
from . import targets as target_registry
from . import uploads
from .models import RunStatus, SourceInfo, TestSettings
from .registry import REPORT_JSON, REPORT_MD, ROLLOUT_JSON, RunRegistry
from .run_manager import RunManager

logger = get_logger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[2]
#: Runtime state lives outside the source tree and is gitignored.
VAR_DIR = REPO_ROOT / "var" / "app"

PRODUCT_NAME = "Autonomous Web Testing Platform"


def create_app(var_dir: Path | None = None) -> FastAPI:
    """Build the application. `var_dir` is a parameter so tests get an isolated registry."""
    registry = RunRegistry(Path(var_dir or VAR_DIR))
    manager = RunManager(registry)

    app = FastAPI(title=PRODUCT_NAME, docs_url="/api/docs", redoc_url=None)
    app.state.registry = registry
    app.state.manager = manager

    templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
    templates.env.globals["product_name"] = PRODUCT_NAME
    templates.env.filters["ago"] = _ago
    templates.env.filters["severity"] = _severity_label
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    def page(request: Request, name: str, **context):  # noqa: ANN202
        return templates.TemplateResponse(
            request=request, name=name,
            context={"nav": name.split(".")[0], **context},
        )

    # -- lifecycle -----------------------------------------------------------------

    @app.on_event("startup")
    def _startup() -> None:
        swept = uploads.sweep_orphan_workspaces()
        if swept:
            logger.info("swept {} orphaned upload workspace(s)", swept)
        _mark_interrupted_runs(registry)
        logger.info("{} ready — state in {}", PRODUCT_NAME, registry.root)

    @app.on_event("shutdown")
    def _shutdown() -> None:
        """Stop live runs so no sandbox container outlives the server."""
        logger.info("shutting down; asking active runs to stop")
        manager.shutdown()

    # -- pages ---------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):  # noqa: ANN202
        runs = registry.list()
        findings = list(registry.findings())
        return page(request, "dashboard.html",
                    stats=registry.stats(), runs=runs[:6], findings=findings[:6],
                    docker_ok=_docker_available())

    @app.get("/new", response_class=HTMLResponse)
    def new_test(request: Request):  # noqa: ANN202
        return page(request, "new.html",
                    demos=target_registry.demo_targets(),
                    # Serialised here rather than in the template: `AgentChoice` is a
                    # slotted dataclass and has no `__dict__` for `tojson` to walk.
                    # Jinja resolves `agent.key` on a dict just as it does on an object,
                    # so the markup is unchanged.
                    agents=[a.to_dict() for a in agent_registry.available_agents()],
                    default_agent=agent_registry.DEFAULT_AGENT,
                    docker_ok=_docker_available(),
                    max_mb=uploads.MAX_UPLOAD_BYTES // (1024 * 1024))

    @app.get("/runs", response_class=HTMLResponse)
    def runs_page(request: Request):  # noqa: ANN202
        return page(request, "runs.html", runs=registry.list())

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str):  # noqa: ANN202
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        return page(request, "run.html", run=run,
                    events=registry.events(run_id),
                    report=registry.read_json(run_id, REPORT_JSON))

    @app.get("/runs/{run_id}/report", response_class=HTMLResponse)
    def report_page(request: Request, run_id: str):  # noqa: ANN202
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        report = registry.read_json(run_id, REPORT_JSON)
        rollout = registry.read_json(run_id, ROLLOUT_JSON) or {}
        return page(request, "report.html", run=run, report=report, rollout=rollout)

    @app.get("/findings", response_class=HTMLResponse)
    def findings_page(request: Request):  # noqa: ANN202
        return page(request, "findings.html", findings=list(registry.findings()))

    @app.get("/reports", response_class=HTMLResponse)
    def reports_page(request: Request):  # noqa: ANN202
        rows = []
        for run in registry.list():
            report = registry.read_json(run.run_id, REPORT_JSON)
            if report:
                rows.append({"run": run, "report": report})
        return page(request, "reports.html", rows=rows)

    # -- actions --------------------------------------------------------------------

    @app.post("/api/runs/demo")
    def start_demo(target: str = Form(...), agent: str = Form(agent_registry.DEFAULT_AGENT),
                   episodes: int = Form(2), steps: int = Form(25)):  # noqa: ANN202
        """Start a run against a bundled fixture. No upload, and no user code executed."""
        demo = target_registry.demo_target(target)
        if demo is None or not demo.available:
            raise HTTPException(400, "Unknown demo target")
        if demo.needs_docker and not _docker_available():
            raise HTTPException(
                400, "This demo builds a container and the Docker daemon is not reachable. "
                     "Start Docker Desktop, or pick a demo that does not need it.")

        settings = TestSettings(episodes=episodes, steps=steps, agent=agent).clamped()
        choice = agent_registry.resolve(settings.agent)
        settings.agent = choice.key

        size, count = uploads.directory_stats(demo.path)
        source = SourceInfo(
            kind="demo", name=demo.name, size_bytes=size, file_count=count,
            workspace=str(demo.path),
            build_kind="docker" if demo.needs_docker else "static",
            build_file="Dockerfile" if demo.needs_docker else demo.entry,
            dockerfile_found=demo.needs_docker,
            validation_ok=True,
            validation_message="Bundled fixture shipped with this repository.",
        )
        run = registry.create(source, settings, agent_label=choice.name)

        if demo.needs_docker:
            deployer = target_registry.docker_deployer()
            detector = None
            repo = demo.path
        else:
            deployer = target_registry.static_deployer(demo.path, demo.entry)
            detector = target_registry.static_detector(demo.path)
            repo = demo.path

        _launch(manager, run, repo, deployer, detector, cleanup=None)
        return JSONResponse({"run_id": run.run_id, "url": f"/runs/{run.run_id}"})

    @app.post("/api/uploads")
    async def upload_repository(file: UploadFile = File(...)):  # noqa: ANN202
        """Validate an archive and describe what is in it. Nothing is executed here."""
        data = await file.read()
        try:
            extracted = uploads.extract_zip(data, file.filename or "repository.zip")
        except uploads.UploadError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        info = _describe_repository(extracted)
        if not info["validation_ok"]:
            # An archive we cannot deploy is not kept on disk.
            shutil.rmtree(extracted.workspace, ignore_errors=True)
            return JSONResponse({"ok": False, "error": info["validation_message"],
                                 "detail": info}, status_code=400)
        return JSONResponse({"ok": True, **info})

    @app.post("/api/runs")
    def start_upload_run(workspace: str = Form(...), repo: str = Form(...),  # noqa: ANN202
                         name: str = Form(...),
                         agent: str = Form(agent_registry.DEFAULT_AGENT),
                         episodes: int = Form(2), steps: int = Form(25)):
        """Start a sandboxed run against a previously validated upload."""
        workspace_path = Path(workspace)
        repo_path = Path(repo)
        if not _is_upload_workspace(workspace_path) or not repo_path.is_dir():
            raise HTTPException(400, "That upload is no longer available. Upload it again.")
        if not _within(repo_path, workspace_path):
            raise HTTPException(400, "Invalid workspace")
        if not _docker_available():
            shutil.rmtree(workspace_path, ignore_errors=True)
            raise HTTPException(
                400, "Testing an uploaded repository requires Docker, and the daemon is "
                     "not reachable. Start Docker Desktop and try again.")

        settings = TestSettings(episodes=episodes, steps=steps, agent=agent).clamped()
        choice = agent_registry.resolve(settings.agent)
        settings.agent = choice.key

        size, count = uploads.directory_stats(repo_path)
        detail = _inspect_build(repo_path)
        source = SourceInfo(
            kind="upload", name=name[:80] or "repository", size_bytes=size,
            file_count=count, workspace=str(workspace_path),
            build_kind=detail.get("build_kind", ""), build_file=detail.get("build_file", ""),
            dockerfile_found=bool(detail.get("dockerfile_found")),
            exposed_ports=list(detail.get("exposed_ports") or []),
            languages=list(detail.get("languages") or []),
            validation_ok=True,
            validation_message=detail.get("validation_message", ""),
            policy_warnings=list(detail.get("policy_warnings") or []),
        )
        run = registry.create(source, settings, agent_label=choice.name)

        def cleanup() -> None:
            shutil.rmtree(workspace_path, ignore_errors=True)
            logger.info("removed upload workspace for run {}", run.run_id)

        _launch(manager, run, repo_path, target_registry.docker_deployer(), None, cleanup)
        return JSONResponse({"run_id": run.run_id, "url": f"/runs/{run.run_id}"})

    @app.post("/api/runs/{run_id}/stop")
    def stop_run(run_id: str):  # noqa: ANN202
        if registry.get(run_id) is None:
            raise HTTPException(404, "Run not found")
        return JSONResponse({"stopping": manager.stop(run_id)})

    # -- read APIs -------------------------------------------------------------------

    @app.get("/api/stats")
    def api_stats():  # noqa: ANN202
        return registry.stats()

    @app.get("/api/runs")
    def api_runs():  # noqa: ANN202
        return [run.to_dict() for run in registry.list()]

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: str):  # noqa: ANN202
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        return {"run": run.to_dict(), "active": manager.is_active(run_id)}

    @app.get("/api/runs/{run_id}/events")
    def api_events(run_id: str, offset: int = 0):  # noqa: ANN202
        if registry.get(run_id) is None:
            raise HTTPException(404, "Run not found")
        return {"events": registry.events(run_id, offset)}

    @app.get("/api/runs/{run_id}/report")
    def api_report(run_id: str):  # noqa: ANN202
        report = registry.read_json(run_id, REPORT_JSON)
        if report is None:
            raise HTTPException(404, "No report for this run")
        return report

    @app.get("/api/agents")
    def api_agents():  # noqa: ANN202
        return [a.to_dict() for a in agent_registry.available_agents()]

    @app.get("/api/runs/{run_id}/stream")
    async def api_stream(run_id: str, request: Request):  # noqa: ANN202
        """Server-sent events: the live activity stream for one run.

        SSE rather than WebSockets because the traffic is one-way and SSE reconnects by
        itself. Events are the real ones the worker publishes; the keep-alive comment is
        the only thing this endpoint generates on its own.
        """
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(404, "Run not found")

        channel = manager.subscribe(run_id)

        async def stream():  # noqa: ANN202
            try:
                yield _sse({"run": run.to_dict(), "event": None})
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        payload = await asyncio.get_running_loop().run_in_executor(
                            None, channel.get, True, 15.0)
                    except Exception:  # noqa: BLE001 - queue.Empty on a quiet run
                        yield ": keep-alive\n\n"
                        continue
                    yield _sse(payload)
                    if payload.get("final"):
                        break
            finally:
                manager.unsubscribe(run_id, channel)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    # -- downloads --------------------------------------------------------------------

    @app.get("/api/runs/{run_id}/download/{fmt}")
    def download(run_id: str, fmt: str):  # noqa: ANN202
        """The existing research artifacts, byte-for-byte. Nothing is reformatted."""
        names = {"json": (REPORT_JSON, "application/json"),
                 "markdown": (REPORT_MD, "text/markdown"),
                 "rollout": (ROLLOUT_JSON, "application/json")}
        if fmt not in names:
            raise HTTPException(404, "Unknown format")
        name, media = names[fmt]
        path = registry.artifact(run_id, name)
        if path is None:
            raise HTTPException(404, "Not available for this run")
        run = registry.get(run_id)
        stem = (run.source.name if run else run_id).replace(" ", "-")
        return FileResponse(path, media_type=media,
                            filename=f"{stem}-{run_id}-{name}")

    @app.get("/runs/{run_id}/evidence/{path:path}")
    def evidence(run_id: str, path: str):  # noqa: ANN202
        """Serve one captured artifact, confined to the run's own evidence directory."""
        root = registry.evidence_dir(run_id).resolve()
        target = (root / path).resolve()
        if not _within(target, root) or not target.is_file():
            raise HTTPException(404, "Evidence not found")
        return FileResponse(target)

    return app


# -- helpers ---------------------------------------------------------------------------


def _launch(manager: RunManager, run, repo: Path, deployer, detector, cleanup) -> None:  # noqa: ANN001
    from ..pipeline import default_detector

    manager.start(run, repo, deployer, detector or default_detector, on_cleanup=cleanup)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _within(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False
    return True


def _is_upload_workspace(path: Path) -> bool:
    """Only a directory this application created may be started or deleted.

    `workspace` arrives from a form field, so without this an arbitrary path could be
    handed to the deployer or to `rmtree`.
    """
    import tempfile

    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return (resolved.is_dir()
            and resolved.name.startswith(uploads.WORKSPACE_PREFIX)
            and _within(resolved, Path(tempfile.gettempdir())))


def _docker_available() -> bool:
    """Whether the daemon answers. Cheap, and re-checked per request rather than cached,
    because Docker Desktop is commonly started *after* this server."""
    try:
        import docker
    except Exception:  # noqa: BLE001
        return False
    try:
        client = docker.from_env(timeout=3)
        client.ping()
        client.close()
    except Exception:  # noqa: BLE001
        return False
    return True


def _describe_repository(extracted: uploads.ExtractedUpload) -> dict:
    """What the New Test page shows after an upload: identity, build, validation."""
    detail = _inspect_build(extracted.root)
    return {
        "name": extracted.name,
        "workspace": str(extracted.workspace),
        "repo": str(extracted.root),
        "size_bytes": extracted.uncompressed_bytes,
        "size_human": _bytes_human(extracted.uncompressed_bytes),
        "file_count": extracted.file_count,
        **detail,
    }


def _inspect_build(repo: Path) -> dict:
    """Run the *existing* detector and policy gate over an extracted repository.

    Deliberately `intake.detect_build_definition`, which is the authority on
    deployability and already applies `validate_dockerfile` / `validate_compose_file`.
    A second opinion implemented here could disagree with the one that actually runs.
    """
    from ..intake import RepoIntakeError, detect_build_definition
    from ..intake.repo_profile import profile_repository

    out: dict = {"dockerfile_found": False, "build_kind": "", "build_file": "",
                 "exposed_ports": [], "languages": [], "policy_warnings": [],
                 "validation_ok": False, "validation_message": ""}

    try:
        profile = profile_repository(repo)
        out["languages"] = [stat.language for stat in getattr(profile, "languages", [])[:4]]
    except Exception as exc:  # noqa: BLE001 - profiling is descriptive, never fatal
        logger.debug("profiling failed for {}: {}", repo, exc)

    try:
        plan = detect_build_definition(repo)
    except RepoIntakeError as exc:
        # The detector's message embeds the workspace path, which is a host temp
        # directory and has no business in a browser. Logged, then replaced.
        logger.info("no build definition in {}: {}", repo, exc)
        out["validation_message"] = (
            "No Dockerfile was found in the archive root or one level down. This version "
            "deploys repositories that describe their own build with a Dockerfile.")
        return out

    out["build_kind"] = plan.kind
    out["build_file"] = str(Path(plan.path).name)
    out["dockerfile_found"] = plan.kind == "dockerfile"

    if plan.kind == "compose":
        out["validation_message"] = (
            "This repository uses a compose file. Compose is detected and policy-checked "
            "but deliberately not executed — v1 deploys a single Dockerfile only.")
        return out

    policy = getattr(plan, "policy", None)
    if policy is not None:
        violations = list(getattr(policy, "violations", []) or [])
        out["policy_warnings"] = [str(v) for v in violations][:8]
        if violations:
            out["validation_message"] = (
                "The build definition was refused by the security policy gate.")
            return out

    out["exposed_ports"] = _exposed_ports(Path(plan.path))
    out["validation_ok"] = True
    out["validation_message"] = "Dockerfile found and accepted by the policy gate."
    return out


def _exposed_ports(dockerfile: Path) -> list[int]:
    ports: list[int] = []
    try:
        for line in dockerfile.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("EXPOSE"):
                for token in stripped.split()[1:]:
                    head = token.split("/")[0]
                    if head.isdigit():
                        ports.append(int(head))
    except OSError:
        return []
    return ports[:6]


def _mark_interrupted_runs(registry: RunRegistry) -> None:
    """A run that was live when the process died cannot still be running.

    Left as-is it shows a spinner forever. Marked FAILED with an honest reason instead.
    """
    for run in registry.list():
        if run.status.is_active:
            run.status = RunStatus.FAILED
            run.error = "The server stopped while this run was in progress."
            run.finished_at = run.finished_at or time.time()
            registry.save(run)


def _bytes_human(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _ago(value: float) -> str:
    if not value:
        return "—"
    delta = max(0, int(time.time() - float(value)))
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _severity_label(value: float) -> str:
    score = float(value or 0.0)
    if score >= 7.0:
        return "high"
    return "medium" if score >= 4.0 else "low"


app = create_app()
