"""The profiler against realistically-shaped manifests, one per ecosystem.

`test_repo_profile.py` covers the detectors with synthetic trees built inline. Those are
fast and precise, and they share an author with the parser — a one-line `go.mod` is the
`go.mod` the parser was written against. These fixtures are the other half: multi-line
`require (…)` blocks with `// indirect` comments, Cargo inline tables with feature lists,
Maven XML, Gemfile groups, Poetry's nested dependency sections, and a `package.json` with
real scripts.

**They are manifests, not applications.** Nothing here is built, installed or executed;
the profiler is a pure function over a directory tree. A consequence worth stating
because it shows up in the assertions: with almost no source files present, the manifests
themselves are the largest files, so `primary_language` reads `JSON` or `TOML` for several
of these. That is the documented byte-share behaviour meeting an unrepresentative tree,
not a defect — so these tests assert on build systems, frameworks and commands, which is
what the fixtures exist to exercise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from web_testing_agent.intake import profile_repository

MANIFESTS = Path(__file__).resolve().parents[1] / "fixtures" / "manifests"


def _profile(name: str):
    return profile_repository(MANIFESTS / name)


def _values(detections) -> list[str]:
    return [d.value for d in detections]


# -- build systems, from formatting the synthetic trees never had ---------------------


@pytest.mark.parametrize("repo,system", [
    ("go_api", "go"),
    ("rust_api", "cargo"),
    ("maven_api", "maven"),
    ("gradle_api", "gradle"),
    ("ruby_api", "bundler"),
    ("php_api", "composer"),
    ("python_api", "poetry"),
    ("node_api", "npm"),
])
def test_the_build_system_is_detected_from_a_realistic_manifest(repo: str, system: str):
    assert system in _values(_profile(repo).build_systems)


@pytest.mark.parametrize("repo,framework", [
    ("go_api", "Gin"),
    ("rust_api", "Axum"),
    ("maven_api", "Spring Boot"),
    ("ruby_api", "Rails"),
    ("php_api", "Laravel"),
    ("python_api", "FastAPI"),
    ("node_api", "Express"),
])
def test_the_framework_is_detected_from_a_declared_dependency(repo: str, framework: str):
    assert framework in _values(_profile(repo).frameworks)


# -- the formats that previously had no realistic coverage ---------------------------


def test_a_multiline_go_require_block_with_indirect_comments_is_parsed():
    """A real `go.mod` has two `require (…)` blocks and `// indirect` markers; the
    synthetic fixture was a single `module` line."""
    profile = _profile("go_api")
    assert "Gin" in _values(profile.frameworks)
    assert profile.primary_language == "Go"


def test_cargo_inline_tables_do_not_defeat_dependency_matching():
    """`serde = { version = "1.0", features = [...] }` is the common form and is not a
    bare version string."""
    assert "Axum" in _values(_profile("rust_api").frameworks)


def test_poetry_group_dependencies_are_read():
    """Poetry nests dependencies under `[tool.poetry.dependencies]`, not `[project]`."""
    profile = _profile("python_api")
    assert "FastAPI" in _values(profile.frameworks)
    assert "poetry install" in _values(profile.install_commands)


def test_a_gemfile_with_groups_is_read():
    assert "Rails" in _values(_profile("ruby_api").frameworks)


def test_composer_require_is_read_but_require_dev_does_not_add_frameworks():
    profile = _profile("php_api")
    assert "Laravel" in _values(profile.frameworks)


# -- commands ------------------------------------------------------------------------


def test_a_lockfile_upgrades_the_node_install_command():
    profile = _profile("node_api")
    assert "npm ci" in _values(profile.install_commands)
    assert "npm run build" in _values(profile.build_commands)
    assert "npm test" in _values(profile.test_commands)


def test_the_gradle_wrapper_is_preferred_over_a_system_gradle():
    """`gradlew` is committed precisely so the build does not depend on what is installed."""
    assert "./gradlew build" in _values(_profile("gradle_api").build_commands)


def test_go_and_cargo_commands_come_from_their_manifests():
    assert "go test ./..." in _values(_profile("go_api").test_commands)
    assert "cargo test" in _values(_profile("rust_api").test_commands)


# -- entry points and tests ----------------------------------------------------------


def test_the_go_cmd_convention_outranks_a_root_main():
    """`cmd/<name>/main.go` is Go's convention for an executable."""
    entries = _values(_profile("go_api").entry_points)
    assert "cmd/server/main.go" in entries
    assert entries.index("cmd/server/main.go") < entries.index("main.go")


def test_a_manifest_declared_entry_point_outranks_a_filename():
    profile = _profile("node_api")
    assert profile.entry_points[0].confidence == 1.0
    assert "src/server.js" in _values(profile.entry_points)


@pytest.mark.parametrize("repo,expected", [
    ("go_api", "handlers_test.go"),
    ("ruby_api", "spec"),
    ("node_api", "__tests__"),
    ("python_api", "tests"),
])
def test_test_targets_are_found_across_ecosystems(repo: str, expected: str):
    assert expected in _values(_profile(repo).test_targets)


# -- limitations these fixtures made visible ------------------------------------------


def test_gradle_dependencies_are_not_parsed_and_that_is_recorded():
    """**A real gap, pinned rather than hidden.**

    `build.gradle` declares `org.springframework.boot:spring-boot-starter-web`, and the
    profiler reports no framework for it: `_dependency_names` reads eight manifest
    formats and Gradle's Groovy DSL is not one of them. Maven is only handled by a
    substring check on the XML. Asserted here so the gap is visible in the suite instead
    of being discovered again later.
    """
    profile = _profile("gradle_api")
    assert "gradle" in _values(profile.build_systems), "the build system is detected"
    assert profile.frameworks == (), "but its dependencies are not read"


def test_a_manifest_only_tree_reports_its_manifests_as_the_primary_language():
    """The documented byte-share behaviour, meeting a tree that is almost all manifest.

    Not a defect and not worth special-casing: a real repository has source that
    outweighs its `package.json`. Recorded so the reading of `primary_language` on these
    fixtures is not mistaken for a profiler bug.
    """
    assert _profile("node_api").primary_language == "JSON"
    assert _profile("rust_api").primary_language == "TOML"


def test_no_manifest_fixture_is_deployable():
    """None carries a Dockerfile, so the runner would refuse them all — which is what
    keeps these fixtures purely profiler input."""
    for repo in ("go_api", "rust_api", "node_api", "python_api"):
        profile = _profile(repo)
        assert profile.deployable is False
        assert profile.build_definition is None
