"""Security validation for user-uploaded Dockerfile / docker-compose.yml before build.

The uploaded-repo pipeline runs arbitrary third-party code. Container resource limits
are necessary but **not sufficient**, because a `docker-compose.yml` does not merely
describe a workload — it specifies that workload's *security context*. Running an
untrusted compose file unmodified is a straightforward host compromise no quota can
prevent:

    services:
      evil:
        privileged: true                       # full host capabilities
        network_mode: host                     # host network namespace
        pid: host                              # see/signal host processes
        volumes: ["/:/host"]                   # host filesystem
                 ["/var/run/docker.sock:..."]  # control the daemon = root on host

So the file has to be *parsed and rejected* before anything is built. This module is
the policy gate; it is deliberately allowlist-shaped (deny unknown dangerous keys
rather than trying to enumerate every exploit) and has no Docker dependency, so it is
cheap to unit-test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from ..utils.logging import get_logger

logger = get_logger(__name__)


class Severity(str, Enum):
    BLOCK = "block"
    WARN = "warn"


@dataclass(frozen=True, slots=True)
class PolicyViolation:
    severity: Severity
    location: str
    key: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.location}: {self.key} — {self.detail}"


@dataclass
class PolicyResult:
    violations: list[PolicyViolation] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    exposed_ports: list[int] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(v.severity is Severity.BLOCK for v in self.violations)

    @property
    def blocking_violations(self) -> list[PolicyViolation]:
        return [v for v in self.violations if v.severity is Severity.BLOCK]

    def raise_if_blocked(self) -> None:
        if self.blocked:
            listing = "\n  ".join(str(v) for v in self.blocking_violations)
            raise UnsafeRepositoryError(f"Refusing to build uploaded repository:\n  {listing}")


class UnsafeRepositoryError(RuntimeError):
    """Raised when an uploaded repo's build definition would escape the sandbox."""


# Compose keys that hand a container privileges the sandbox is supposed to withhold.
# Values are the human-readable reason, used verbatim in the violation message.
_FORBIDDEN_SERVICE_KEYS: dict[str, str] = {
    "privileged": "grants effectively full host root",
    "cap_add": "re-adds capabilities the sandbox drops",
    "devices": "exposes host devices to the container",
    "device_cgroup_rules": "grants direct host device access",
    "userns_mode": "can disable user-namespace remapping",
    "security_opt": "can disable seccomp/AppArmor confinement",
    "sysctls": "mutates kernel parameters",
    "cgroup_parent": "escapes the sandbox's cgroup limits",
    "ipc": "can share the host IPC namespace",
    "uts": "can share the host UTS namespace",
    "pid": "can share the host PID namespace",
    "privileged_mode": "grants effectively full host root",
    "extra_hosts": "can redirect hostnames to internal addresses",
}

# Keys whose *value* decides whether they are safe.
_HOST_NAMESPACE_VALUES = {"host", "shareable"}

# Host paths that are catastrophic to bind-mount even read-only.
_CRITICAL_HOST_PATHS = ("/var/run/docker.sock", "/proc", "/sys", "/dev", "/etc", "/root", "/boot")

_ABSOLUTE_PATH = re.compile(r"^([a-zA-Z]:[\\/]|/|\\\\)")
_WINDOWS_DRIVE = re.compile(r"^[a-zA-Z]:[\\/]")
_PARENT_ESCAPE = re.compile(r"(^|/)\.\.(/|$)")

_DOCKERFILE_FORBIDDEN = (
    # `--privileged`-style build mounts and daemon socket access during build.
    (re.compile(r"^\s*RUN\s+.*--mount=type=bind.*source=/", re.IGNORECASE), "bind-mounts a host path at build time"),
    (re.compile(r"docker\.sock", re.IGNORECASE), "references the Docker daemon socket"),
    (re.compile(r"^\s*RUN\s+.*--security=insecure", re.IGNORECASE), "requests an insecure build sandbox"),
)


def _is_host_path_mount(volume: Any) -> tuple[bool, str]:
    """Whether a compose `volumes:` entry bind-mounts a host path (vs a named volume)."""
    if isinstance(volume, dict):
        if volume.get("type") == "bind":
            return True, str(volume.get("source", ""))
        return False, ""
    if not isinstance(volume, str):
        return False, ""
    if _WINDOWS_DRIVE.match(volume):
        # "C:\Users:/host" — the drive letter's colon is part of the source, not the
        # source/target separator, so a naive split on ":" yields a harmless-looking "C".
        source = volume[:2] + volume[2:].split(":", 1)[0]
    else:
        source = volume.split(":", 1)[0]
    if _ABSOLUTE_PATH.match(source) or source.startswith((".", "~")) or _PARENT_ESCAPE.search(source):
        return True, source
    return False, ""


def validate_compose(document: dict, location: str = "docker-compose.yml") -> PolicyResult:
    """Check a parsed compose document against the sandbox policy."""
    result = PolicyResult()
    services = document.get("services")
    if not isinstance(services, dict) or not services:
        result.violations.append(
            PolicyViolation(Severity.BLOCK, location, "services", "no services defined")
        )
        return result

    for name, service in services.items():
        where = f"{location}:services.{name}"
        result.services.append(str(name))
        if not isinstance(service, dict):
            result.violations.append(
                PolicyViolation(Severity.BLOCK, where, "<service>", "service definition is not a mapping")
            )
            continue

        for key, reason in _FORBIDDEN_SERVICE_KEYS.items():
            if key not in service:
                continue
            value = service[key]
            # pid/ipc/uts are only dangerous when they name a host or peer namespace.
            if key in ("pid", "ipc", "uts"):
                if str(value).lower() not in _HOST_NAMESPACE_VALUES and not str(value).startswith("container:"):
                    continue
            if key == "privileged" and value is False:
                continue
            result.violations.append(
                PolicyViolation(Severity.BLOCK, where, key, f"{reason} (found {value!r})")
            )

        network_mode = str(service.get("network_mode", "")).lower()
        if network_mode in ("host", "none") or network_mode.startswith("container:"):
            severity = Severity.BLOCK if network_mode != "none" else Severity.WARN
            result.violations.append(
                PolicyViolation(
                    severity, where, "network_mode",
                    f"{network_mode!r} bypasses the isolated bridge network the agent attaches to",
                )
            )

        for volume in service.get("volumes") or []:
            is_bind, source = _is_host_path_mount(volume)
            if not is_bind:
                continue
            critical = any(source.rstrip("/").startswith(path) for path in _CRITICAL_HOST_PATHS)
            result.violations.append(
                PolicyViolation(
                    Severity.BLOCK, where, "volumes",
                    f"bind-mounts host path {source!r}"
                    + (" (critical host resource)" if critical else "")
                    + " — only named volumes are permitted",
                )
            )

        if service.get("build") and not service.get("image"):
            # Building from the uploaded context is the expected case; flag only so the
            # caller knows a build (not just a pull) will run inside the sandbox.
            result.violations.append(
                PolicyViolation(Severity.WARN, where, "build", "builds from the uploaded context")
            )

        for port in service.get("ports") or []:
            parsed = _parse_container_port(port)
            if parsed is not None:
                result.exposed_ports.append(parsed)

    if not result.exposed_ports:
        result.violations.append(
            PolicyViolation(
                Severity.WARN, location, "ports",
                "no published ports found; the agent may have no URL to attach to",
            )
        )
    return result


def _parse_container_port(port: Any) -> int | None:
    if isinstance(port, int):
        return port
    if isinstance(port, dict):
        target = port.get("target")
        return int(target) if isinstance(target, (int, str)) and str(target).isdigit() else None
    if not isinstance(port, str):
        return None
    # "8080:80", "127.0.0.1:8080:80", "80", "8080:80/tcp"
    candidate = port.split("/", 1)[0].split(":")[-1]
    return int(candidate) if candidate.isdigit() else None


def validate_compose_file(path: Path) -> PolicyResult:
    """Parse and validate a compose file from disk. Malformed YAML is a blocking result."""
    try:
        # safe_load, never load: an untrusted YAML file can otherwise construct
        # arbitrary Python objects on parse, before any container is even created.
        document = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except yaml.YAMLError as exc:
        return PolicyResult(
            violations=[PolicyViolation(Severity.BLOCK, str(path), "<yaml>", f"unparseable: {exc}")]
        )
    if not isinstance(document, dict):
        return PolicyResult(
            violations=[PolicyViolation(Severity.BLOCK, str(path), "<yaml>", "top level is not a mapping")]
        )
    return validate_compose(document, location=str(path))


def validate_dockerfile(text: str, location: str = "Dockerfile") -> PolicyResult:
    """Check a Dockerfile for build-time escapes.

    Far narrower than the compose check: a Dockerfile cannot grant itself a security
    context, so the concern is limited to BuildKit features that reach outside the
    build context.
    """
    result = PolicyResult()
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern, reason in _DOCKERFILE_FORBIDDEN:
            if pattern.search(line):
                result.violations.append(
                    PolicyViolation(Severity.BLOCK, f"{location}:{line_no}", line.strip()[:80], reason)
                )
    return result


# Runtime flags the auto-deploy module must apply on top of a policy-clean definition.
# Documented here next to the policy so the two cannot drift apart.
SANDBOX_RUN_ARGS: dict[str, Any] = {
    "network_mode": "wta-target-net",  # internal bridge; the browser joins the same one
    "cap_drop": ["ALL"],
    "security_opt": ["no-new-privileges:true"],
    "read_only": True,
    "tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
    "mem_limit": "2g",
    "memswap_limit": "2g",
    "nano_cpus": 2_000_000_000,  # 2 CPUs
    "pids_limit": 256,
    "user": "1000:1000",
}

BUILD_TIMEOUT_S = 600
BOOT_TIMEOUT_S = 180
