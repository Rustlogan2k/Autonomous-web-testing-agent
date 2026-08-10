"""Manual end-to-end smoke test for WebFunctionalEnv against a local fixture site.

Usage:
    python scripts/smoke_test_functional_env.py

Spins up a throwaway local HTTP server over tests/fixtures/smoke_site/, drives
WebFunctionalEnv through a scripted sequence of actions covering every action
type, and prints what happened at each step so a human can eyeball correctness
without needing Docker or a real training target (Gitea etc.) running.

Several steps assert an *absence* of bug signals — those encode regressions that were
only findable by running a real browser: a refresh keeping the same URL is correct, a
fragment link not changing the path is correct, and a popup link leaving this tab's
URL alone is correct. All three previously registered as broken navigation.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from web_testing_agent.envs.functional_env import WebFunctionalEnv  # noqa: E402
from web_testing_agent.envs.types import ActionSpec, ActionType  # noqa: E402
from web_testing_agent.utils.local_server import serve_directory  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "smoke_site"


def _find(specs: list[ActionSpec], action_type: ActionType, predicate=lambda s: True) -> int | None:
    for spec in specs:
        if spec.action_type == action_type and predicate(spec):
            return spec.index
    return None


def _by_element(action_type: ActionType, needle: str):
    return lambda specs: _find(specs, action_type, lambda s: needle.lower() in s.element_id.lower())


def main() -> int:
    with serve_directory(FIXTURE_DIR) as origin:
        base_url = f"{origin}/index.html"
        logger.info("Serving fixture site at {}", base_url)

        env = WebFunctionalEnv(base_url=base_url, max_steps=40, headless=True)
        failures: list[str] = []
        try:
            _, info = env.reset()
            specs = info["action_specs"]
            logger.info(
                "reset() ok: {} action slots, {} baseline error(s) learned, url={}",
                len(specs), info["baseline_errors"], info["page"]["url"],
            )

            # (label, action selector, assertion over (bug_signals, step_info) or None)
            script = [
                (
                    "type username",
                    lambda s: _find(
                        s, ActionType.TYPE,
                        lambda sp: sp.element_id == "username" and sp.params["category"] == "valid_typical",
                    ),
                    None,
                ),
                (
                    "type email",
                    lambda s: _find(
                        s, ActionType.TYPE,
                        lambda sp: sp.element_id == "email" and sp.params["category"] == "valid_typical",
                    ),
                    None,
                ),
                ("select country", lambda s: _find(s, ActionType.SELECT), None),
                ("toggle checkbox", _by_element(ActionType.CLICK, "agree"), None),
                (
                    "fragment link must NOT flag broken navigation",
                    _by_element(ActionType.CLICK, "Jump to section two"),
                    lambda bug, _: not bug.broken_navigation,
                ),
                (
                    "scroll down",
                    lambda s: _find(s, ActionType.SCROLL, lambda sp: sp.params.get("direction") == "down"),
                    None,
                ),
                (
                    "below-the-fold button is reachable after scrolling",
                    _by_element(ActionType.CLICK, "Below-the-fold"),
                    lambda _, step: step["exec_success"],
                ),
                (
                    "resize mobile",
                    lambda s: _find(s, ActionType.RESIZE_VIEWPORT, lambda sp: sp.params.get("preset") == "mobile"),
                    None,
                ),
                (
                    "resize desktop",
                    lambda s: _find(s, ActionType.RESIZE_VIEWPORT, lambda sp: sp.params.get("preset") == "desktop"),
                    None,
                ),
                ("rapid-click submit", lambda s: _find(s, ActionType.RAPID_CLICK), None),
                (
                    "JS-error button flags a console error",
                    _by_element(ActionType.CLICK, "Trigger JS error"),
                    lambda bug, _: bool(bug.console_errors),
                ),
                (
                    "broken link flags http + broken navigation",
                    _by_element(ActionType.CLICK, "Broken link"),
                    lambda bug, _: bug.broken_navigation and bug.document_http_error,
                ),
                (
                    "refresh must NOT flag broken navigation",
                    lambda s: _find(s, ActionType.REFRESH),
                    lambda bug, _: not bug.broken_navigation,
                ),
                ("browser back", lambda s: _find(s, ActionType.BROWSER_BACK), None),
                (
                    "popup link is adopted, not reported as broken",
                    _by_element(ActionType.CLICK, "new tab"),
                    lambda bug, step: step["opened_new_page"] and not bug.broken_navigation,
                ),
            ]

            current = specs
            for label, selector_fn, assertion in script:
                action = selector_fn(current)
                if action is None:
                    logger.warning("[{}] no matching action in the current space; using NO_OP", label)
                    action = 0
                _, reward, terminated, truncated, step_info = env.step(action)
                bug = step_info["bug_signals"]
                explore = step_info["exploration"]
                logger.info(
                    "[{}] reward={:+.2f} exec={} novel={} repeats={} "
                    "bugs(console={}, http={}, doc_http={}, slow={}, broken_nav={}) url={}",
                    label, reward, step_info["exec_success"],
                    explore.is_novel_state, explore.repeat_count,
                    bool(bug.console_errors), bool(bug.unexpected_http_errors),
                    bug.document_http_error, bug.slow_response, bug.broken_navigation,
                    step_info["page"]["url"],
                )
                if assertion is not None and not assertion(bug, step_info):
                    failures.append(label)
                    logger.error("[{}] ASSERTION FAILED", label)

                current = step_info["action_specs"]
                if terminated or truncated:
                    logger.info("Episode ended early at '{}'", label)
                    break

            logger.info("States covered this run: {}", env.exploration.total_states_seen)
        finally:
            env.close()

    if failures:
        logger.error("Smoke test FAILED ({} assertion(s)): {}", len(failures), failures)
        return 1
    logger.info("Smoke test complete — all assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
