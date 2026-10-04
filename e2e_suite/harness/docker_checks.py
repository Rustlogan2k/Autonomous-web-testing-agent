"""Container and product-intake compatibility, without modifying the product.

* Docker: each app with a Dockerfile is built with **no network** (the product sandbox's
  rule) and started; its health endpoint must answer from inside the container.
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

from .launcher import free_port, http_json
from .manifest import AppManifest


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=30,
                          check=False).returncode == 0


def build_and_run(app: AppManifest, directory: Path, *, timeout_s: int = 600) -> dict:
    tag = f"e2e-suite/{app.app_id}:clean"
    started = time.monotonic()
    build = subprocess.run(["docker", "build", "--network", "none", "-t", tag, str(directory)],
                           capture_output=True, text=True, timeout=timeout_s, check=False)
    result = {"image": tag, "build_ok": build.returncode == 0,
              "build_seconds": round(time.monotonic() - started, 1),
              "build_tail": (build.stdout + build.stderr).strip().splitlines()[-3:]}
    if build.returncode != 0:
        return {**result, "passed": False}
    port = free_port()
    name = f"e2e-{app.app_id}-{port}"
    run = subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1:{port}:8080",
                          "-e", "PORT=8080", "-e", "HOST=0.0.0.0", tag],
                         capture_output=True, text=True, timeout=120, check=False)
    healthy, detail = False, run.stderr.strip()[-200:]
    try:
        deadline = time.monotonic() + 40
        while run.returncode == 0 and time.monotonic() < deadline:
            try:
                payload = http_json(f"http://127.0.0.1:{port}/healthz", timeout=2)
                healthy = payload.get("ok") is True and payload.get("app") == app.app_id
                detail = str(payload)
                if healthy:
                    break
            except OSError as exc:
                detail = str(exc)
            time.sleep(0.5)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60, check=False)
    image = subprocess.run(["docker", "image", "inspect", tag, "--format", "{{.Size}}"],
                           capture_output=True, text=True, timeout=30, check=False)
    size = int(image.stdout.strip()) if image.stdout.strip().isdigit() else None
    return {**result, "container_healthy": healthy, "detail": detail,
            "image_mb": round(size / 1e6, 1) if size else None, "passed": healthy}


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
