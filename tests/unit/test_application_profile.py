import json
from pathlib import Path

import pytest

from web_testing_agent.intake import ApplicationProfile, Behaviour, Route, Rule, path_matches
from web_testing_agent.judge.prompt import (
    COMPACT_SYSTEM_PROMPT,
    PROFILE_GUIDANCE,
    SYSTEM_PROMPT,
    build_messages,
    build_user_prompt,
    system_prompt,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GITEA_PROFILE = REPO_ROOT / "data" / "profiles" / "gitea.json"


def _profile(**overrides) -> ApplicationProfile:
    payload = {
        "name": "Demo",
        "app_type": "forge",
        "summary": "a demo",
        "auth": "session cookie",
        "provenance": "manual",
        "routes": [
            {"path": "/user/sign_up", "purpose": "registration form"},
            {"path": "/{owner}/{repo}/forks", "purpose": "fork list"},
        ],
        "rules": [{"field": "email", "route": "/user/sign_up", "rule": "required, valid address"}],
        "behaviours": [
            {"description": "global truth", "route": "*"},
            {"description": "forks may be empty", "route": "/{owner}/{repo}/forks"},
        ],
    }
    payload.update(overrides)
    return ApplicationProfile.from_dict(payload)


# --- route matching --------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("/user/sign_up", "/user/sign_up", True),
        ("/user/sign_up", "/user/login", False),
        ("/{owner}/{repo}/forks", "/tester/notes/forks", True),
        ("/{owner}/{repo}/forks", "/tester/notes", False),
        ("/{owner}/{repo}", "/tester/notes", True),
        ("/{owner}/{repo}", "/tester/notes/forks", False),
        ("/assets/*", "/assets/licenses.txt", True),
        ("/assets/*", "/assets/js/index.js", True),
        ("/assets/*", "/explore/repos", False),
        ("/", "/", True),
    ],
)
def test_path_matching(pattern, path, expected):
    assert path_matches(pattern, path) is expected


def test_query_strings_are_ignored_when_matching():
    """The explorer types into search boxes, so half the corpus's paths carry a query."""
    assert path_matches("/explore/repos", "/explore/repos?q=abc&sort=recentupdate")


# --- slicing ---------------------------------------------------------------------


def test_slice_keeps_only_the_routes_in_the_window():
    sliced = _profile().slice_for(["/user/sign_up"])
    assert [r.path for r in sliced.routes] == ["/user/sign_up"]
    assert [r.field for r in sliced.rules] == ["email"]


def test_slice_keeps_global_behaviours_but_drops_unrelated_scoped_ones():
    sliced = _profile().slice_for(["/user/sign_up"])
    assert [b.description for b in sliced.behaviours] == ["global truth"]


def test_slice_keeps_a_scoped_behaviour_for_a_matching_route():
    sliced = _profile().slice_for(["/tester/notes/forks"])
    assert "forks may be empty" in [b.description for b in sliced.behaviours]


def test_an_unknown_route_renders_nothing_but_global_behaviour():
    """A window about a route the profile never heard of must not gain a bare header."""
    text = _profile(behaviours=[]).render(["/totally/unknown/route"])
    assert text == ""


# --- rendering -------------------------------------------------------------------


def test_render_marks_routes_the_profile_does_not_describe():
    text = _profile().render(["/user/sign_up", "/some/unknown"])
    assert "/some/unknown" in text
    assert "not described in the profile" in text


def test_render_states_that_the_profile_is_incomplete():
    """A judge handed a spec reads omissions as defects unless told otherwise.

    The full rules live in the system turn (PROFILE_GUIDANCE), which is stated once and
    is cacheable; the per-window section carries only a one-line reminder. Repeating the
    rules in every window cost ~450 characters each — more than the whole constraints
    section — to say something already in the same request.
    """
    assert "absence from it is not a defect" in _profile().render(["/user/sign_up"])
    assert "INCOMPLETE" in PROFILE_GUIDANCE


def test_the_guidance_tells_the_judge_not_to_quote_the_profile_as_evidence():
    assert "never from the profile" in PROFILE_GUIDANCE
    assert "you do not have evidence" in PROFILE_GUIDANCE


def test_render_is_budgeted_and_says_what_it_left_out():
    big = _profile(routes=[{"path": f"/r{i}", "purpose": "x" * 200} for i in range(50)])
    text = big.render([f"/r{i}" for i in range(50)], budget=500)
    assert len(text) <= 900  # header + one guaranteed entry + the omission note
    assert "omitted for length" in text


def test_every_section_survives_the_budget():
    """Regression, measured on Gitea twice over.

    Strict priority let five signup constraints starve the known-correct behaviours out
    of 101 of 109 windows; an equal split then dropped *both* of those sections whenever
    their first entry overran its share, keeping the route list instead. A section is a
    category of grounding, so each one keeps at least its first entry.
    """
    crowded = _profile(
        rules=[{"field": f"f{i}", "route": "/user/sign_up", "rule": "x" * 200} for i in range(9)],
        behaviours=[{"description": "y" * 200, "route": "*"}],
    )
    text = crowded.render(["/user/sign_up"])
    assert "declared constraints" in text
    assert "known-correct behaviour" in text
    assert "routes in this window" in text


def test_the_highest_value_sections_get_more_of_the_budget():
    """Routes mostly restate the URL and title the window already shows."""
    wide = _profile(
        rules=[{"field": f"f{i}", "route": "/user/sign_up", "rule": "x" * 60} for i in range(8)],
        routes=[{"path": "/user/sign_up", "purpose": "z" * 60} for _ in range(8)],
    )
    text = wide.render(["/user/sign_up"])
    rules_shown = text.count("\n  - f")
    routes_shown = text.count("\n  - /user/sign_up")
    assert rules_shown > routes_shown


# --- provenance ------------------------------------------------------------------


def test_provenance_is_required():
    with pytest.raises(ValueError, match="provenance"):
        ApplicationProfile.from_dict(
            {"name": "x", "app_type": "y", "summary": "z", "auth": "none"}
        )


def test_provenance_must_be_a_known_value():
    with pytest.raises(ValueError, match="manual"):
        _profile(provenance="probably-extracted")


def test_manual_provenance_is_never_rendered_into_the_prompt():
    """It must not be able to influence a verdict; it exists to label the report."""
    assert "manual" not in _profile().render(["/user/sign_up"]).lower()


# --- prompt assembly -------------------------------------------------------------


def test_profile_guidance_is_added_only_when_a_profile_is_present():
    assert system_prompt("detailed", grounded=False) == SYSTEM_PROMPT
    assert system_prompt("compact", grounded=False) == COMPACT_SYSTEM_PROMPT
    assert PROFILE_GUIDANCE in system_prompt("detailed", grounded=True)


def test_the_profile_is_separated_from_the_window_in_the_user_turn():
    prompt = build_user_prompt("WINDOW BODY", profile_text="PROFILE BODY")
    assert prompt.index("PROFILE BODY") < prompt.index("WINDOW BODY")
    assert "=" * 70 in prompt


def test_an_empty_profile_leaves_the_prompt_byte_identical_to_the_ungrounded_one():
    """The A/B must compare grounding, not the cost of an empty header."""
    assert build_user_prompt("WINDOW") == build_user_prompt("WINDOW", profile_text="")


def test_few_shot_turns_never_carry_a_profile_section():
    """Three exchanges where a profile was present and never cited would teach the
    model to ignore it."""
    messages = build_messages("WINDOW", style="compact", profile_text="PROFILE BODY")
    example_turns = messages[1:-1]
    assert example_turns, "compact style should still carry few-shot turns"
    assert not any("PROFILE BODY" in turn["content"] for turn in example_turns)
    assert "PROFILE BODY" in messages[-1]["content"]


# --- the shipped Gitea profile ---------------------------------------------------


def test_the_gitea_profile_loads():
    profile = ApplicationProfile.load(GITEA_PROFILE)
    assert profile.name == "Gitea"
    assert profile.provenance == "manual"
    assert profile.routes and profile.rules and profile.flows and profile.behaviours


def test_the_gitea_profile_declares_it_is_hand_authored():
    """It produces prompts identical in shape to profiler output, so the file itself
    has to say which it is."""
    payload = json.loads(GITEA_PROFILE.read_text(encoding="utf-8"))
    assert payload["provenance"] == "manual"
    assert "Repo Profiler" in payload["notes"]


def test_every_gitea_rule_and_route_cites_a_source():
    """A constraint with no provenance is 'the code defines X' with no X to point at —
    which is the entire claim the profile exists to support."""
    profile = ApplicationProfile.load(GITEA_PROFILE)
    assert all(rule.source for rule in profile.rules)
    assert all(route.source for route in profile.routes)
    assert all(behaviour.source for behaviour in profile.behaviours)


def test_the_gitea_profile_covers_the_routes_the_corpus_actually_reaches():
    """Authored against the captured exploration, not against a route list on paper."""
    profile = ApplicationProfile.load(GITEA_PROFILE)
    reached = [
        "/", "/api/swagger", "/assets/licenses.txt", "/explore/repos",
        "/tester/notes", "/tester/notes/forks", "/tester/notes/packages",
        "/tester/notes/stars", "/user/login", "/user/sign_up",
    ]
    uncovered = [path for path in reached if not profile.covers(path)]
    assert not uncovered, f"profile does not describe: {uncovered}"
