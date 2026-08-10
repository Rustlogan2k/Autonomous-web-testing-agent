"""Repo Intake: turning an uploaded repository into a live, understood test target.

Contains the security policy gate (`compose_policy`) that every uploaded build
definition must pass before anything is executed, and the `ApplicationProfile` schema
(`profile`) that the LLM repo profiler produces and the reward model consumes. The
auto-deploy runner and the profiler's extraction stage build on top of both.
"""

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

__all__ = [
    "PROFILE_CHARS_MAX",
    "ApplicationProfile",
    "Behaviour",
    "Flow",
    "Route",
    "Rule",
    "path_matches",
    "window_paths",
]
