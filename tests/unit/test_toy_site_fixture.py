"""Contract tests on the seeded-bug fixture itself.

The answer key is ground truth for every judge and baseline measurement, so a bug
that stops reproducing silently invalidates results rather than failing loudly. These
assert the markup preconditions each seeded bug depends on.
"""

import json
import re
from pathlib import Path

import pytest

TOY_SITE = Path(__file__).resolve().parents[1] / "fixtures" / "toy_site"
ANSWER_KEY = json.loads((TOY_SITE / "answer_key.json").read_text(encoding="utf-8"))
BUGS = {bug["id"]: bug for bug in ANSWER_KEY["bugs"]}


def _page(name: str) -> str:
    return (TOY_SITE / name).read_text(encoding="utf-8")


def test_signup_form_disables_native_validation():
    """Without `novalidate`, Chromium enforces the constraints the app fails to enforce.

    Measured: with native validation on, `age=999999999` (BUG-05) and an empty
    `required` email (BUG-06) both block submission before the submit handler runs.
    submit-count stayed 0 and the form never navigated, so BUG-01, BUG-05 and BUG-06
    — three of the seven llm_required bugs — were all unreachable.
    """
    form = re.search(r"<form id=\"signup-form\"[^>]*>", _page("signup.html"))
    assert form is not None, "signup form is missing"
    assert "novalidate" in form.group(0)


def test_signup_declares_the_constraints_it_never_enforces():
    """BUG-05 is 'declared in markup, never enforced in code' — both halves must hold."""
    age_input = re.search(r"<input[^>]*id=\"age\"[^>]*>", _page("signup.html"))
    assert age_input is not None
    assert 'min="13"' in age_input.group(0) and 'max="120"' in age_input.group(0)
    # ...and the handler must not have grown a validation branch.
    handler = _page("signup.html").split("addEventListener('submit'", 1)[1]
    assert not re.search(r"\b(checkValidity|reportValidity|if\s*\(\s*age\b)", handler)


def test_signup_handler_drops_the_email_field():
    """BUG-01: email is read, then never added to the outgoing params."""
    handler = _page("signup.html").split("addEventListener('submit'", 1)[1]
    assert "const email" in handler
    assert "params.set('email'" not in handler


def test_signup_has_no_double_submit_guard():
    """BUG-06: nothing disables the button or gates on an in-flight submission."""
    handler = _page("signup.html").split("addEventListener('submit'", 1)[1]
    assert ".disabled" not in handler


def test_pricing_page_really_is_missing():
    """BUG-03 is a 404; a well-meaning `git add` of pricing.html would silence it."""
    assert 'id="nav-pricing"' in _page("index.html")
    assert not (TOY_SITE / "pricing.html").exists()


def test_mobile_media_query_hides_every_route_into_signup():
    """BUG-08 is only a bug if *no* route survives.

    Hiding one of two links to the same page is ordinary responsive design. An earlier
    fixture hid only #cta-signup while #nav-signup stayed visible, so signup was still
    reachable and there was no defect to find — a judge correctly reported "none".
    Every element that links to signup.html must be hidden below the breakpoint.
    """
    index = _page("index.html")
    routes = set(re.findall(r'<a[^>]*href="signup\.html"[^>]*id="([^"]+)"', index))
    assert routes, "no links into the signup flow"

    hidden = re.search(r"@media \(max-width:\s*600px\)\s*\{([^}]*)\{[^}]*display:\s*none", index)
    assert hidden is not None, "the mobile breakpoint rule is missing"
    hidden_ids = set(re.findall(r"#([\w-]+)", hidden.group(1)))
    assert routes <= hidden_ids, (
        f"{sorted(routes - hidden_ids)} still reach signup.html on mobile, so BUG-08 does not reproduce"
    )


def test_archive_back_link_points_at_the_wrong_page():
    """BUG-10: the URL does change, so no deterministic trigger fires."""
    link = re.search(r"<a[^>]*id=\"link-back-widgets\"[^>]*>", _page("archive.html"))
    assert link is not None
    assert 'href="index.html"' in link.group(0)


def test_dark_mode_write_and_read_keys_still_disagree():
    """BUG-09 is a one-character casing mismatch; a tidy-up would 'fix' it by accident."""
    settings = _page("settings.html")
    assert "pref.darkMode" in settings and "pref.darkmode" in settings


@pytest.mark.parametrize("bug_id", sorted(BUGS))
def test_every_seeded_bug_names_pages_that_exist(bug_id):
    """Except BUG-03, whose missing page is the bug."""
    for page in BUGS[bug_id]["pages"]:
        if bug_id == "BUG-03" and page == "pricing.html":
            continue
        assert (TOY_SITE / page).is_file(), f"{bug_id} references missing page {page}"


def test_deterministic_coverage_claim_matches_the_key():
    """The 3-of-10 split is the headroom every 'the LLM adds value' claim is measured against."""
    deterministic = [b for b in BUGS.values() if "deterministic" in b["detectability"]]
    assert {b["id"] for b in deterministic} == {"BUG-03", "BUG-04", "BUG-07"}
    assert len(BUGS) == 10


# --- prompt leakage --------------------------------------------------------------

# Rendered page text reaches the LLM judge's prompt verbatim. Any hint that this is a
# test fixture with known planted bugs primes the judge to report them, which inflates
# recall and the false-positive rate at once and makes the measurement worthless.
# Measured: the original copy ("a deliberately small app with a known set of seeded
# functional bugs. See answer_key.json for the ground truth") appeared in every single
# window of every corpus. Keep such notes in HTML comments, which are not rendered.
_LEAKING_TERMS = (
    "seeded", "answer key", "answer_key", "fixture", "ground truth", "known bug",
    "deliberately", "test site", "validation site", "reward signal", "DQN", "RL agent",
    "BUG-",
)


@pytest.mark.parametrize("page", sorted(p.name for p in TOY_SITE.glob("*.html")))
def test_rendered_text_does_not_reveal_that_this_is_a_bug_fixture(page):
    from web_testing_agent.judge.window import page_view

    visible = " ".join(page_view(f"/{page}", _page(page)).text).lower()
    leaked = [term for term in _LEAKING_TERMS if term.lower() in visible]
    assert not leaked, (
        f"{page} leaks {leaked} into the judge prompt via rendered text. "
        "Move it into an HTML comment."
    )


@pytest.mark.parametrize("page", sorted(p.name for p in TOY_SITE.glob("*.html")))
def test_titles_and_headings_do_not_reveal_the_fixture(page):
    from web_testing_agent.perception.normalization import extract_structure

    structure = extract_structure(_page(page))
    surface = " ".join([structure.title, *structure.headings]).lower()
    assert not [term for term in _LEAKING_TERMS if term.lower() in surface]


def test_the_bug_annotations_are_still_present_as_comments():
    """The notes must survive the fix — invisible to the judge, visible to a maintainer."""
    assert "BUG-08" in _page("index.html")
    assert "BUG-03" in _page("index.html")
