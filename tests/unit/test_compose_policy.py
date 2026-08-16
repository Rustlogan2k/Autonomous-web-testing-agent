import pytest
import yaml

from web_testing_agent.intake.compose_policy import (
    Severity,
    UnsafeRepositoryError,
    validate_compose,
    validate_dockerfile,
)

SAFE = """
services:
  web:
    build: .
    ports: ["8080:80"]
    volumes:
      - app-data:/var/lib/app
volumes:
  app-data:
"""


def _check(text: str):
    return validate_compose(yaml.safe_load(text))


def _blocked_keys(result) -> set[str]:
    return {v.key for v in result.violations if v.severity is Severity.BLOCK}


def test_ordinary_compose_file_passes():
    result = _check(SAFE)
    assert not result.blocked
    assert result.services == ["web"]
    assert result.exposed_ports == [80]


@pytest.mark.parametrize(
    ("snippet", "key"),
    [
        ("privileged: true", "privileged"),
        ("cap_add: [SYS_ADMIN]", "cap_add"),
        ("devices: ['/dev/sda:/dev/sda']", "devices"),
        ("security_opt: ['seccomp:unconfined']", "security_opt"),
        ("userns_mode: host", "userns_mode"),
        ("pid: host", "pid"),
        ("ipc: host", "ipc"),
        ("cgroup_parent: /", "cgroup_parent"),
        ("sysctls: {net.ipv4.ip_forward: 1}", "sysctls"),
    ],
)
def test_privilege_escalating_keys_are_blocked(snippet, key):
    result = _check(f"services:\n  evil:\n    image: alpine\n    {snippet}\n")
    assert result.blocked
    assert key in _blocked_keys(result)


def test_host_network_mode_is_blocked():
    result = _check("services:\n  evil:\n    image: alpine\n    network_mode: host\n")
    assert result.blocked
    assert "network_mode" in _blocked_keys(result)


def test_privileged_false_is_not_blocked():
    result = _check("services:\n  web:\n    image: alpine\n    privileged: false\n    ports: ['80:80']\n")
    assert not result.blocked


def test_pid_namespace_within_the_project_is_allowed():
    """`pid: service:other` stays inside the sandbox; only host/container: escape it."""
    result = _check("services:\n  web:\n    image: alpine\n    pid: service:other\n    ports: ['80:80']\n")
    assert not result.blocked


@pytest.mark.parametrize(
    "mount",
    [
        "/:/host",
        "/var/run/docker.sock:/var/run/docker.sock",
        "/etc:/etc:ro",
        "./src:/app",
        "../../secrets:/secrets",
        "~/.ssh:/root/.ssh",
        "C:\\Users:/host",
    ],
)
def test_host_bind_mounts_are_blocked(mount):
    result = _check(f"services:\n  evil:\n    image: alpine\n    volumes: ['{mount}']\n")
    assert result.blocked
    assert "volumes" in _blocked_keys(result)


def test_named_volumes_are_allowed():
    result = _check("services:\n  web:\n    image: alpine\n    volumes: ['data:/var/lib']\n    ports: ['80:80']\n")
    assert not result.blocked


def test_long_syntax_bind_mount_is_blocked():
    result = _check(
        "services:\n  evil:\n    image: alpine\n    volumes:\n"
        "      - type: bind\n        source: /\n        target: /host\n"
    )
    assert result.blocked


def test_docker_socket_mount_is_reported_as_critical():
    result = _check("services:\n  evil:\n    image: alpine\n    volumes: ['/var/run/docker.sock:/sock']\n")
    detail = next(v.detail for v in result.violations if v.key == "volumes")
    assert "critical host resource" in detail


def test_group_add_is_blocked():
    """Joining the host docker group is daemon access without naming a socket."""
    result = _check("services:\n  evil:\n    image: alpine\n    group_add: ['docker']\n")
    assert result.blocked
    assert "group_add" in _blocked_keys(result)


# --- Definitions that reach outside the document being validated -------------------
#
# Every rule above reads the one document it was handed. A key that merges in a *second*
# file after validation defeats all of them at once, and a named volume backed by a host
# device defeats the "named volumes only" rule using the volume API as documented.


def test_extends_is_blocked_because_the_other_file_is_never_scanned():
    result = _check(
        "services:\n  app:\n    extends:\n      file: base.yml\n      service: privileged_base\n"
        "    ports: ['8080:80']\n"
    )
    assert result.blocked
    assert "extends" in _blocked_keys(result)


def test_top_level_include_is_blocked():
    result = _check("include:\n  - dangerous.yml\nservices:\n  app:\n    image: nginx\n    ports: ['80:80']\n")
    assert result.blocked
    assert "include" in _blocked_keys(result)


def test_named_volume_backed_by_a_host_device_is_blocked():
    """The documented way to make a named volume a bind mount of host `/`."""
    result = _check(
        "services:\n  app:\n    image: nginx\n    ports: ['80:80']\n"
        "    volumes: ['hostroot:/host']\n"
        "volumes:\n  hostroot:\n    driver: local\n"
        "    driver_opts:\n      type: none\n      device: /\n      o: bind\n"
    )
    assert result.blocked
    keys = _blocked_keys(result)
    # Reported twice on purpose: once against the declaration, once against the service
    # that receives it, so the message names the container holding the host path.
    assert "driver_opts" in keys
    assert "volumes" in keys
    locations = {v.location for v in result.blocking_violations}
    assert any(loc.endswith("volumes.hostroot") for loc in locations)
    assert any(loc.endswith("services.app") for loc in locations)


def test_long_syntax_reference_to_a_bind_backed_volume_is_blocked():
    """`type: volume` passes the bind check, so the declaration is what has to catch it."""
    result = _check(
        "services:\n  app:\n    image: nginx\n    ports: ['80:80']\n    volumes:\n"
        "      - type: volume\n        source: hostroot\n        target: /host\n"
        "volumes:\n  hostroot:\n    driver: local\n"
        "    driver_opts: {type: none, device: /, o: bind}\n"
    )
    assert result.blocked
    assert "volumes" in _blocked_keys(result)


def test_a_bind_backed_volume_is_blocked_even_without_the_o_flag():
    """`type: none` + `device` binds without `o: bind`, so the device is what is keyed on."""
    result = _check(
        "services:\n  app:\n    image: nginx\n    ports: ['80:80']\n    volumes: ['esc:/mnt']\n"
        "volumes:\n  esc:\n    driver: local\n    driver_opts: {type: none, device: /etc}\n"
    )
    assert result.blocked


def test_ordinary_named_volume_declaration_still_passes():
    """The safe form the gate must not become useless against."""
    result = _check(SAFE)
    assert not result.blocked


def test_compose_with_no_services_is_blocked():
    assert validate_compose({}).blocked


def test_raise_if_blocked_names_every_violation():
    result = _check("services:\n  evil:\n    image: alpine\n    privileged: true\n    volumes: ['/:/host']\n")
    with pytest.raises(UnsafeRepositoryError) as excinfo:
        result.raise_if_blocked()
    assert "privileged" in str(excinfo.value)
    assert "volumes" in str(excinfo.value)


def test_clean_result_does_not_raise():
    _check(SAFE).raise_if_blocked()


def test_missing_ports_is_a_warning_not_a_block():
    result = _check("services:\n  web:\n    image: alpine\n")
    assert not result.blocked
    assert any(v.key == "ports" and v.severity is Severity.WARN for v in result.violations)


def test_dockerfile_socket_reference_is_blocked():
    result = validate_dockerfile("FROM alpine\nRUN ls /var/run/docker.sock\n")
    assert result.blocked


def test_ordinary_dockerfile_passes():
    result = validate_dockerfile("FROM python:3.11\nCOPY . /app\nRUN pip install -r requirements.txt\n")
    assert not result.blocked
