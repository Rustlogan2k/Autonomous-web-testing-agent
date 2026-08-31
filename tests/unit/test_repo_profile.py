"""Repository profiling: deterministic detection over fixture trees.

Every test builds a real directory and profiles it. No Docker, no browser, no model, no
network — which is the point of the module and the reason these run in milliseconds.

The negative cases matter as much as the positive ones. A profiler that reports a
framework nobody depends on, a test command for a Makefile with no `test:` rule, or
"JavaScript" for a repository whose only JavaScript is a vendored `node_modules`, is
worse than one that reports nothing: it puts guesses into a document meant to be trusted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from web_testing_agent.intake import RepositoryProfile, profile_repository
from web_testing_agent.intake.repo_profile import MAX_DEPTH


def _write(root: Path, relative: str, content: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _values(detections) -> list[str]:
    return [d.value for d in detections]


# -- structure and languages ---------------------------------------------------------


def test_a_repository_is_described_by_its_largest_language(tmp_path: Path):
    _write(tmp_path, "app.py", "x = 1\n" * 400)
    _write(tmp_path, "static/site.css", "body{}")
    profile = profile_repository(tmp_path)
    assert profile.primary_language == "Python"
    assert "CSS" in [entry.language for entry in profile.languages]


def test_language_shares_are_by_bytes_and_sum_to_one(tmp_path: Path):
    _write(tmp_path, "big.py", "a" * 900)
    _write(tmp_path, "small.js", "b" * 100)
    profile = profile_repository(tmp_path)
    assert profile.languages[0].language == "Python"
    assert sum(entry.share for entry in profile.languages) == pytest.approx(1.0)


def test_vendored_dependencies_do_not_decide_the_primary_language(tmp_path: Path):
    """The case that makes `IGNORED_DIRS` load-bearing.

    A checked-in `node_modules` is larger than most repositories and says nothing about
    what the authors wrote. Without pruning, every repo with one profiles as JavaScript.
    """
    _write(tmp_path, "app.py", "x = 1\n" * 100)
    for index in range(30):
        _write(tmp_path, f"node_modules/pkg{index}/index.js", "y" * 5_000)
    profile = profile_repository(tmp_path)
    assert profile.primary_language == "Python"
    assert "JavaScript" not in [entry.language for entry in profile.languages]
    assert profile.stats.ignored_directories >= 1


@pytest.mark.parametrize("ignored", ["venv", ".git", "__pycache__", "dist", "target", ".tox"])
def test_generated_and_tooling_directories_are_pruned(tmp_path: Path, ignored: str):
    _write(tmp_path, "main.py", "x = 1")
    _write(tmp_path, f"{ignored}/junk.py", "y = 2\n" * 500)
    profile = profile_repository(tmp_path)
    assert profile.stats.files_scanned == 1


def test_an_empty_repository_profiles_without_raising(tmp_path: Path):
    profile = profile_repository(tmp_path)
    assert profile.primary_language == ""
    assert profile.build_definition is None
    assert profile.deployable is False
    assert any("no recognised source" in note.lower() for note in profile.notes)


def test_a_missing_directory_is_an_error_not_an_empty_profile(tmp_path: Path):
    with pytest.raises(NotADirectoryError):
        profile_repository(tmp_path / "nope")


# -- build systems -------------------------------------------------------------------


def test_a_lockfile_names_the_package_manager_and_outranks_the_generic_guess(tmp_path: Path):
    _write(tmp_path, "package.json", json.dumps({"name": "x"}))
    _write(tmp_path, "yarn.lock", "")
    profile = profile_repository(tmp_path)
    assert "yarn" in _values(profile.build_systems)
    assert "npm" not in _values(profile.build_systems), "the lockfile is more specific"


def test_poetry_is_read_from_pyproject_rather_than_assumed(tmp_path: Path):
    _write(tmp_path, "pyproject.toml",
           '[build-system]\nbuild-backend = "poetry.core.masonry.api"\n\n[tool.poetry]\nname = "x"\n')
    profile = profile_repository(tmp_path)
    assert "poetry" in _values(profile.build_systems)
    assert "poetry install" in _values(profile.install_commands)


def test_a_plain_pyproject_without_a_declared_backend_stays_pip(tmp_path: Path):
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    profile = profile_repository(tmp_path)
    assert "pip" in _values(profile.build_systems)
    assert "poetry" not in _values(profile.build_systems)


def test_a_malformed_manifest_degrades_instead_of_raising(tmp_path: Path):
    """An unparseable manifest is a repository fact, not a crash in the profiler."""
    _write(tmp_path, "package.json", "{ this is not json")
    _write(tmp_path, "pyproject.toml", "[[[not toml")
    profile = profile_repository(tmp_path)
    assert isinstance(profile, RepositoryProfile)
    assert "npm" in _values(profile.build_systems), "presence still counts even if unreadable"


def test_a_polyglot_repository_reports_every_build_system(tmp_path: Path):
    _write(tmp_path, "package.json", json.dumps({"name": "x"}))
    _write(tmp_path, "go.mod", "module example.com/x\n")
    _write(tmp_path, "Cargo.toml", '[package]\nname = "x"\n')
    profile = profile_repository(tmp_path)
    for system in ("npm", "go", "cargo"):
        assert system in _values(profile.build_systems)


# -- frameworks ----------------------------------------------------------------------


def test_a_framework_is_reported_only_when_a_manifest_depends_on_it(tmp_path: Path):
    _write(tmp_path, "requirements.txt", "Flask>=2.0\ngunicorn==21.2.0\n")
    profile = profile_repository(tmp_path)
    assert "Flask" in _values(profile.frameworks)
    assert all(d.confidence == 1.0 for d in profile.frameworks)


def test_a_framework_is_not_invented_from_directory_layout(tmp_path: Path):
    """A repo that merely *looks* like Django must not be reported as Django."""
    _write(tmp_path, "manage.py", "")
    _write(tmp_path, "myapp/models.py", "")
    _write(tmp_path, "myapp/views.py", "")
    profile = profile_repository(tmp_path)
    assert profile.frameworks == (), "no manifest declares Django, so nothing may claim it"


def test_javascript_dependencies_are_read_from_every_section(tmp_path: Path):
    _write(tmp_path, "package.json", json.dumps({
        "name": "x", "dependencies": {"express": "^4"}, "devDependencies": {"react": "^18"},
    }))
    profile = profile_repository(tmp_path)
    assert set(_values(profile.frameworks)) == {"Express", "React"}


def test_version_specifiers_and_extras_do_not_defeat_dependency_matching(tmp_path: Path):
    _write(tmp_path, "requirements.txt", "fastapi[all]>=0.100,<1.0 ; python_version>'3.8'\n")
    profile = profile_repository(tmp_path)
    assert "FastAPI" in _values(profile.frameworks)


def test_comments_and_pip_flags_in_requirements_are_ignored(tmp_path: Path):
    _write(tmp_path, "requirements.txt", "# a comment\n-r other.txt\n--index-url https://x\nflask\n")
    profile = profile_repository(tmp_path)
    assert "Flask" in _values(profile.frameworks)


# -- entry points --------------------------------------------------------------------


def test_a_manifest_entry_point_outranks_a_filename_convention(tmp_path: Path):
    _write(tmp_path, "package.json", json.dumps({"name": "x", "main": "src/server.js"}))
    _write(tmp_path, "index.js", "")
    profile = profile_repository(tmp_path)
    assert profile.entry_points[0].value == "src/server.js"
    assert profile.entry_points[0].confidence == 1.0


def test_a_dockerfile_cmd_is_an_entry_point(tmp_path: Path):
    _write(tmp_path, "Dockerfile", 'FROM busybox\nEXPOSE 8080\nCMD ["httpd", "-f"]\n')
    profile = profile_repository(tmp_path)
    assert any("httpd" in d.value for d in profile.entry_points)


def test_the_go_cmd_convention_is_recognised(tmp_path: Path):
    _write(tmp_path, "go.mod", "module example.com/x\n")
    _write(tmp_path, "cmd/server/main.go", "package main\n")
    profile = profile_repository(tmp_path)
    assert "cmd/server/main.go" in _values(profile.entry_points)


def test_a_bare_main_py_is_reported_as_a_weaker_signal(tmp_path: Path):
    """Confidence has to distinguish `manage.py` from a guess, or it is decoration."""
    _write(tmp_path, "main.py", "")
    _write(tmp_path, "manage.py", "")
    profile = profile_repository(tmp_path)
    by_value = {d.value: d.confidence for d in profile.entry_points}
    assert by_value["manage.py"] > by_value["main.py"]


# -- tests and docs ------------------------------------------------------------------


def test_test_directories_and_filenames_are_both_detected(tmp_path: Path):
    _write(tmp_path, "tests/test_thing.py", "")
    _write(tmp_path, "pkg/widget_test.go", "")
    profile = profile_repository(tmp_path)
    values = _values(profile.test_targets)
    assert "tests" in values
    assert "pkg/widget_test.go" in values


def test_pytest_configuration_is_detected_from_pyproject(tmp_path: Path):
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n\n[tool.pytest.ini_options]\naddopts = "-q"\n')
    profile = profile_repository(tmp_path)
    assert "pytest" in _values(profile.test_targets)


def test_readme_and_docs_directory_are_both_documentation(tmp_path: Path):
    _write(tmp_path, "README.md", "# x")
    _write(tmp_path, "docs/guide.md", "g")
    profile = profile_repository(tmp_path)
    values = _values(profile.docs)
    assert "README.md" in values and "docs" in values


def test_a_script_that_merely_starts_with_a_doc_word_is_not_documentation(tmp_path: Path):
    """Caught by profiling this repository: `install.py` matched the `install` prefix and
    was reported as an installation guide. A prose prefix needs a prose suffix."""
    _write(tmp_path, "install.py", "print('installing')")
    _write(tmp_path, "INSTALL.md", "# how to install")
    profile = profile_repository(tmp_path)
    values = _values(profile.docs)
    assert "INSTALL.md" in values
    assert "install.py" not in values


def test_extensionless_documentation_still_counts(tmp_path: Path):
    _write(tmp_path, "README", "x")
    _write(tmp_path, "CHANGELOG", "y")
    values = _values(profile_repository(tmp_path).docs)
    assert "README" in values and "CHANGELOG" in values


# -- commands ------------------------------------------------------------------------


def test_npm_ci_is_proposed_only_when_a_lockfile_exists(tmp_path: Path):
    _write(tmp_path, "package.json", json.dumps({"name": "x"}))
    assert "npm install" in _values(profile_repository(tmp_path).install_commands)
    _write(tmp_path, "package-lock.json", "{}")
    assert "npm ci" in _values(profile_repository(tmp_path).install_commands)


def test_declared_scripts_become_build_and_test_commands(tmp_path: Path):
    _write(tmp_path, "package.json", json.dumps({
        "name": "x", "scripts": {"build": "tsc", "test": "jest"},
    }))
    profile = profile_repository(tmp_path)
    assert "npm run build" in _values(profile.build_commands)
    assert "npm test" in _values(profile.test_commands)


def test_no_test_command_is_proposed_when_the_manifest_declares_none(tmp_path: Path):
    _write(tmp_path, "package.json", json.dumps({"name": "x", "scripts": {"build": "tsc"}}))
    profile = profile_repository(tmp_path)
    assert profile.test_commands == ()


def test_a_makefile_target_must_exist_before_its_command_is_proposed(tmp_path: Path):
    """`make test` against a Makefile with no `test:` rule is a proposal that fails."""
    _write(tmp_path, "Makefile", "build:\n\tgcc main.c\n")
    profile = profile_repository(tmp_path)
    assert "make build" in _values(profile.build_commands)
    assert "make test" not in _values(profile.test_commands)


def test_go_commands_are_derived_from_the_module_file(tmp_path: Path):
    _write(tmp_path, "go.mod", "module example.com/x\n")
    profile = profile_repository(tmp_path)
    assert "go build ./..." in _values(profile.build_commands)
    assert "go test ./..." in _values(profile.test_commands)


# -- build definition and deployability ----------------------------------------------


def test_a_dockerfile_repository_is_deployable(tmp_path: Path):
    _write(tmp_path, "Dockerfile", "FROM busybox\n")
    profile = profile_repository(tmp_path)
    assert profile.build_definition.value == "dockerfile"
    assert profile.deployable is True


def test_a_compose_repository_is_detected_but_not_deployable(tmp_path: Path):
    """`intake/runner.py` refuses to execute compose files, and the profile must say so."""
    _write(tmp_path, "docker-compose.yml", "services: {}\n")
    profile = profile_repository(tmp_path)
    assert profile.build_definition.value == "compose"
    assert profile.deployable is False
    assert any("refuses to execute compose" in note for note in profile.notes)


def test_compose_wins_when_both_exist_matching_the_runner(tmp_path: Path):
    """The profile and `runner.detect_build_definition` must not disagree about a repo."""
    _write(tmp_path, "Dockerfile", "FROM busybox\n")
    _write(tmp_path, "docker-compose.yml", "services: {}\n")
    assert profile_repository(tmp_path).build_definition.value == "compose"


def test_a_repository_with_no_build_definition_says_why_it_cannot_deploy(tmp_path: Path):
    _write(tmp_path, "app.py", "")
    profile = profile_repository(tmp_path)
    assert profile.deployable is False
    assert any("cannot deploy" in note for note in profile.notes)


# -- determinism, serialization and bounds -------------------------------------------


def test_profiling_is_deterministic(tmp_path: Path):
    """The property the whole module rests on: same tree, same profile, every time."""
    _write(tmp_path, "package.json", json.dumps({"name": "x", "dependencies": {"express": "^4"}}))
    _write(tmp_path, "src/index.js", "console.log(1)")
    _write(tmp_path, "tests/app.test.js", "")
    first = profile_repository(tmp_path).to_dict()
    second = profile_repository(tmp_path).to_dict()
    assert first == second


def test_the_profile_serializes_to_json(tmp_path: Path):
    _write(tmp_path, "Dockerfile", "FROM busybox\n")
    _write(tmp_path, "app.py", "x = 1")
    payload = profile_repository(tmp_path).to_dict()
    assert json.loads(json.dumps(payload))["provenance"] == "detector"


def test_evidence_paths_are_repository_relative_and_posix(tmp_path: Path):
    """A profile is read on other machines; absolute host paths make it unusable there."""
    _write(tmp_path, "src/app/main.py", "")
    _write(tmp_path, "requirements.txt", "flask\n")
    profile = profile_repository(tmp_path)
    everything = (profile.build_systems + profile.frameworks + profile.entry_points
                  + profile.test_targets + profile.docs + profile.dependency_manifests)
    for detection in everything:
        assert not Path(detection.evidence).is_absolute(), detection
        assert "\\" not in detection.evidence, detection


def test_provenance_distinguishes_this_from_an_application_profile(tmp_path: Path):
    """`ApplicationProfile` uses manual|profiler. Sharing a vocabulary would invite
    reading one artifact as the other."""
    assert profile_repository(tmp_path).provenance == "detector"


def test_a_deep_tree_is_truncated_loudly_rather_than_silently(tmp_path: Path):
    deep = "/".join(f"level{i}" for i in range(MAX_DEPTH + 3))
    _write(tmp_path, f"{deep}/buried.py", "x = 1")
    profile = profile_repository(tmp_path)
    assert "max_depth" in profile.stats.truncated
    assert any("truncated" in note.lower() for note in profile.notes)


def test_the_demo_repo_fixture_profiles_as_a_deployable_static_site():
    """The one real repository in the tree, so the detectors meet something not authored
    for them. `tests/fixtures/demo_repo` is what `scripts/run_repo.py` deploys."""
    root = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "demo_repo"
    profile = profile_repository(root)
    assert profile.deployable is True
    assert profile.build_definition.value == "dockerfile"
    assert profile.primary_language == "HTML"
    assert "README.md" in _values(profile.docs)
    assert any("httpd" in d.value for d in profile.entry_points)
