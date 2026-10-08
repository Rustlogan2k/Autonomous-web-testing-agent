"""Container and product-intake compatibility, without modifying the product.

* Docker: each app with a Dockerfile is built with **no network** (the product sandbox's
  rule) and started; its health endpoint must answer from inside the container. A suite v2
  app also gets its database: a home of its own on the suite's server
  (`e2e_<app>_docker`), reached through `host.docker.internal` (plan decision D9), or, when
  Docker Desktop's hairpin to a loopback-published port is unavailable, over the `wta-net`
  network by container name. Its health is only `ok` with the database reachable. Then an
  HTTP sign-in round trip (form POST, 303 with the session cookie, entry page 200), and the
  container's seed state must be byte-for-byte the host clean build's (the same sha).
* Product intake: the product's own `profile_repository` and `detect_build_definition` are
  called read-only on the clean build, to record what the product makes of each repo today.
  `wayfarer` has no Dockerfile on purpose: the expected result is the product's current
  refusal, which is the limitation it exists to exercise later.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from .dbctl import NETWORK, engine, ensure_network
from .launcher import CREATED_HOMES, URL_VARIABLES, free_port, http_json
from .manifest import AppManifest

HEALTH_TIMEOUT_S = 45.0
#: In order: Docker Desktop's name for the host, then the shared network fallback.
ROUTES = ("host", "network")


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=30,
                          check=False).returncode == 0


def _docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                          check=False)


def build_and_run(app: AppManifest, directory: Path, *, seed_sha: str | None = None,
                  timeout_s: int = 600) -> dict:
    tag = f"e2e-suite/{app.app_id}:clean"
    started = time.monotonic()
    build = subprocess.run(["docker", "build", "--network", "none", "-t", tag, str(directory)],
                           capture_output=True, text=True, timeout=timeout_s, check=False)
    result: dict = {"image": tag, "build_ok": build.returncode == 0,
                    "build_seconds": round(time.monotonic() - started, 1),
                    "build_tail": (build.stdout + build.stderr).strip().splitlines()[-3:]}
    if build.returncode != 0:
        return {**result, "passed": False}
    store, home = None, ""
    if app.database:
        store = engine(app.database)
        home = store.home_name(app.app_id, "docker")
        CREATED_HOMES.append((app.app_id, app.database, home))
    attempts = []
    try:
        for route in (ROUTES if store is not None else (None,)):
            if store is not None:
                store.create_home(home)
            attempt = _run_container(app, tag, store, home, route, seed_sha)
            attempts.append(attempt)
            if attempt["healthy"]:
                break
    finally:
        if store is not None:
            store.drop_home(home)
    last = attempts[-1]
    image = _docker("image", "inspect", tag, "--format", "{{.Size}}", timeout=30)
    size = int(image.stdout.strip()) if image.stdout.strip().isdigit() else None
    return {**result, "route": last["route"], "attempts": attempts,
            "container_healthy": last["healthy"], "detail": last["detail"],
            "sign_in": last.get("sign_in"),
            "image_mb": round(size / 1e6, 1) if size else None, "passed": last["healthy"]}


def _run_container(app: AppManifest, tag: str, store, home: str, route: str | None,
                   seed_sha: str | None) -> dict:
    port = free_port()
    name = f"e2e-{app.app_id}-{port}"
    command = ["run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1:{port}:8080",
               "-e", "PORT=8080", "-e", "HOST=0.0.0.0", "-e", "WTA_TEST_HOOKS=1"]
    if store is not None:
        command += ["-e", f"{URL_VARIABLES[app.database]}={store.container_url(home, route)}"]
        if route == "host":
            command += ["--add-host", "host.docker.internal:host-gateway"]
        else:
            ensure_network((store.CONTAINER,))
            command += ["--network", NETWORK]
    run = _docker(*command, tag)
    attempt: dict = {"route": route, "healthy": False, "detail": run.stderr.strip()[-200:]}
    base = f"http://127.0.0.1:{port}/"
    try:
        deadline = time.monotonic() + HEALTH_TIMEOUT_S
        while run.returncode == 0 and time.monotonic() < deadline:
            try:
                payload = http_json(base + "healthz", timeout=2)
                attempt["healthy"] = payload.get("ok") is True and payload.get("app") == app.app_id
                attempt["detail"] = str(payload)
                if attempt["healthy"]:
                    break
            except OSError as exc:
                attempt["detail"] = str(exc)
            if _docker("inspect", "-f", "{{.State.Running}}", name).stdout.strip() != "true":
                attempt["detail"] = "the container exited"
                break
            time.sleep(0.5)
        if not attempt["healthy"]:
            attempt["log_tail"] = _docker("logs", "--tail", "8", name).stdout.strip().splitlines()[-8:]
        elif store is not None and app.login_steps:
            attempt["sign_in"] = _sign_in_round_trip(app, base, seed_sha)
    finally:
        _docker("rm", "-f", name, timeout=60)
    return attempt


def _sign_in_round_trip(app: AppManifest, base: str, seed_sha: str | None) -> dict:
    """POST the sign-in form, follow to the entry page, compare the seed state with the host's."""
    from .db_checks import sign_in, signed_in

    session, reply = sign_in(base, app)
    landed = session.follow(app.data.get("entry", "/"))[-1]
    sha = session.state().get("sha")
    ok = signed_in(reply, session, app) and landed.status == 200 \
        and landed.path != app.login["path"]
    return {"passed": ok and seed_sha is not None and sha == seed_sha,
            "status": reply.status, "cookie": bool(session.sid), "entry_status": landed.status,
            "entry_path": landed.path, "container_sha": sha, "host_sha": seed_sha,
            "sign_in_ms": reply.ms}


def product_intake(app: AppManifest, directory: Path) -> dict:
    """What the product's current intake makes of this repository (read-only calls)."""
    from web_testing_agent.intake.repo_profile import profile_repository
    from web_testing_agent.intake.runner import detect_build_definition

    out: dict = {}
    try:
        profile = profile_repository(directory, name=app.app_id)
        data = profile.to_dict() if hasattr(profile, "to_dict") else dict(vars(profile))
        out["profile"] = {k: data.get(k) for k in ("languages", "frameworks", "stack", "runnable",
                                                   "build_definition", "entrypoints", "ports")
                          if k in data}
    except Exception as exc:  # noqa: BLE001 - recording the product's behaviour, not judging it
        out["profile_error"] = f"{type(exc).__name__}: {exc}"
    try:
        plan = detect_build_definition(directory)
        out["build_plan"] = {"kind": getattr(plan, "kind", None),
                             "path": str(getattr(plan, "path", ""))}
        out["product_can_build"] = True
    except Exception as exc:  # noqa: BLE001
        out["build_plan_error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
        out["product_can_build"] = False
    expected = app.has_dockerfile
    out["matches_expectation"] = out["product_can_build"] == expected
    out["expectation"] = ("buildable by the current intake" if expected else
                          "refused by the current intake (no Dockerfile): the limitation "
                          "this app exists to exercise")
    return out
