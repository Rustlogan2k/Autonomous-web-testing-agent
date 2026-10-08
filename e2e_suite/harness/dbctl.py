"""Bring the suite's database servers up or down, before and after admission runs.

    python -m e2e_suite.harness.dbctl up        # both servers ready, wta-net in place
    python -m e2e_suite.harness.dbctl status    # what is running and what it holds
    python -m e2e_suite.harness.dbctl reset     # drop every e2e_* database (servers stay up)
    python -m e2e_suite.harness.dbctl down      # stop and remove both containers
    python -m e2e_suite.harness.dbctl up --engine postgres

Every check starts a server on demand (`ensure_server`), and that is race-safe, but several
admission jobs starting at once would each wait out the same container start. Running `up`
first removes the wait and the race. `up` also creates the `wta-net` bridge network and
attaches both servers to it: the fallback route by which an app container reaches the
database when Docker Desktop's host.docker.internal hairpin is unavailable (docker_checks).

Only the two suite containers (`wta-postgres`, `wta-mongo`) are ever started, stopped or
removed here; their data lives on tmpfs, so `down` discards it.
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess

NETWORK = "wta-net"
ENGINES = ("postgres", "mongodb")
#: Every database the harness creates starts with this (launcher, docker_checks).
SUITE_PREFIX = "e2e_"


def engine(name: str):
    """The engine module (`postgres.py` or `mongo.py`) for a manifest's `database.engine`."""
    if name not in ENGINES:
        raise ValueError(f"unknown database engine {name!r}")
    return importlib.import_module(f".{'postgres' if name == 'postgres' else 'mongo'}", __package__)


def _docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                          check=False)


def ensure_network(containers: tuple[str, ...] = ()) -> None:
    """Create `wta-net` if needed and attach `containers` to it (idempotent)."""
    if _docker("network", "inspect", NETWORK).returncode != 0:
        created = _docker("network", "create", NETWORK)
        if created.returncode != 0 and "already exists" not in created.stderr:
            raise RuntimeError(f"cannot create network {NETWORK}: {created.stderr.strip()}")
    for container in containers:
        joined = _docker("network", "connect", NETWORK, container)
        if joined.returncode != 0 and "already exists" not in joined.stderr:
            raise RuntimeError(f"cannot attach {container} to {NETWORK}: {joined.stderr.strip()}")


def up(module) -> dict:
    module.ensure_server()
    ensure_network((module.CONTAINER,))
    return {"container": module.CONTAINER, "port": module.PORT, "ready": module.ready()}


def down(module) -> dict:
    removed = _docker("rm", "-f", module.CONTAINER)
    return {"container": module.CONTAINER, "removed": removed.returncode == 0}


def reset(module) -> dict:
    if not module.ready():
        return {"container": module.CONTAINER, "dropped": "not running"}
    return {"container": module.CONTAINER, "dropped": module.drop_prefixed(SUITE_PREFIX)}


def status(module) -> dict:
    state = _docker("inspect", "-f", "{{.State.Status}} {{.Config.Image}}", module.CONTAINER)
    ready = module.ready()
    homes = module.list_homes(SUITE_PREFIX) if ready else []
    networks = _docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", module.CONTAINER)
    return {"container": module.CONTAINER, "port": module.PORT, "ready": ready,
            "state": state.stdout.strip() or "absent", "pinned_image": module.IMAGE,
            "on_wta_net": f'"{NETWORK}"' in (networks.stdout or ""),
            "suite_databases": len(homes), "examples": homes[:8]}


COMMANDS = {"up": up, "down": down, "reset": reset, "status": status}


def run(command: str, engines: tuple[str, ...] = ENGINES) -> dict:
    """Apply `command` to each engine. An engine whose driver is not vendored yet is
    reported as such; it does not stop the command for the other engine."""
    out = {}
    for name in engines:
        try:
            module = engine(name)
        except ImportError as exc:
            out[name] = {"error": f"driver not vendored: {exc}"}
            continue
        out[name] = COMMANDS[command](module)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=tuple(COMMANDS))
    parser.add_argument("--engine", choices=(*ENGINES, "all"), default="all")
    args = parser.parse_args()
    result = run(args.command, ENGINES if args.engine == "all" else (args.engine,))
    print(json.dumps(result, indent=2))
    return 1 if any("error" in r for r in result.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
