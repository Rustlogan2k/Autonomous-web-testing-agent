"""Repo Intake: turning an uploaded repository into a live, understood test target.

Contains the security policy gate (`compose_policy`) that every uploaded build
definition must pass before anything is executed, and the `ApplicationProfile` schema
(`profile`) that the LLM repo profiler produces and the reward model consumes. The
auto-deploy runner and the profiler's extraction stage build on top of both.
"""

from .runner import (
    BOOT_TIMEOUT_S,
    BuildPlan,
    BuildTimeout,
    Deployment,
    HealthCheckTimeout,
    RepoIntakeError,
    deploy_repository,
    detect_build_definition,
)
from .profile import (
    PROFILE_CHARS_MAX,
    ApplicationProfile,
    Behaviour,
    Flow,
    Route,
    Rule,
    path_matches,
    window_paths,
)
# `RepositoryProfile` describes the *repository* (layout, languages, build, tests) for the
# deployer and the report. `ApplicationProfile` above describes application *intent* for
# the judge. Deliberately separate schemas — see repo_profile's module docstring.
from .repo_profile import (
    IGNORED_DIRS,
    Detection,
    LanguageStat,
    ProfileStats,
    RepositoryProfile,
    profile_for_run,
    profile_repository,
)

__all__ = [
    "BOOT_TIMEOUT_S",
    "BuildPlan",
    "BuildTimeout",
    "Deployment",
    "HealthCheckTimeout",
    "PROFILE_CHARS_MAX",
    "RepoIntakeError",
    "deploy_repository",
    "detect_build_definition",
    "ApplicationProfile",
    "Behaviour",
    "Flow",
    "Route",
    "Rule",
    "path_matches",
    "window_paths",
    "IGNORED_DIRS",
    "Detection",
    "LanguageStat",
    "ProfileStats",
    "RepositoryProfile",
    "profile_for_run",
    "profile_repository",
]
