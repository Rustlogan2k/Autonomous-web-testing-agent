"""M2: the repository profile travelling in a run's metadata.

`scripts/run_repo.py` profiles the repository before it builds anything and passes the
result to `build_report` as `run_meta["repository_profile"]`. These tests pin that
composition — profile block, report, serialization — without needing Docker, a browser or
a model, which is the whole reason the profiler is a pure filesystem read.

The two properties worth defending are that a **failure is visible rather than fabricated**
(an unprofilable repository and an empty one must not look alike), and that a report built
**without** a profile is byte-for-byte what it was before M2, so the integration cannot
have changed existing behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from web_testing_agent.intake import profile_for_run
from web_testing_agent.reporting import build_report, render_markdown

DEMO_REPO = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "demo_repo"

# One deterministic trigger finding and one judge verdict, in the shapes `build_report`
# already consumes, so these tests exercise the real assembly rather than a stub of it.
_FINDING = {
    "trigger": "document_http_error", "url": "http://127.0.0.1:8080/pricing.html",
    "element": "Pricing", "detail": "404", "episode": 1, "step": 3,
    "action": {"type": "CLICK", "element": "Pricing"}, "count": 1,
}
# The evidence has to be long enough to be checkable: `is_grounded` refuses to certify a
# quote of only a word or two, because a two-word "citation" cannot distinguish a real
# one from a coincidence. A short fixture here is quarantined as ungrounded and the test
# would be measuring that rather than what it means to.
_WINDOW = (
    "STEP 5\n"
    "  action     : CLICK on 'Place order'\n"
    "  page text  : Order confirmed | Product: | folders-25 | Quantity: | 1\n"
)
_VERDICT = {
    "is_bug": True, "bug_type": "broken_flow", "severity": 0.8, "confidence": 0.9,
    "observed": "the confirmation reports quantity 1",
    "expected": "the confirmation should report the quantity that was ordered",
    "discrepancy": "the confirmation reports quantity 1 regardless of what was ordered",
    "evidence": "page text  : Order confirmed | Product: | folders-25 | Quantity: | 1",
    "episode": 1, "step": 7, "window_text": _WINDOW,
    "repro": [{"type": "CLICK", "element": "Place order"}],
}


def _report(**run_meta):
    return build_report(target="http://127.0.0.1:8080/", findings=[dict(_FINDING)],
                        verdicts=[], run_meta=run_meta or None)


# -- the profile block ---------------------------------------------------------------


def test_a_profile_is_generated_for_a_real_repository_run():
    """The repository `scripts/run_repo.py` actually deploys."""
    profile = profile_for_run(DEMO_REPO)
    assert profile["available"] is True
    assert profile["provenance"] == "detector"
    assert profile["deployable"] is True
    assert profile["build_definition"]["value"] == "dockerfile"
    assert profile["stats"]["files_scanned"] > 0


def test_the_profile_preserves_provenance_confidence_and_evidence():
    """A summary would drop exactly the fields that let a reader check a claim."""
    profile = profile_for_run(DEMO_REPO)
    detections = (profile["entry_points"] + profile["docs"]
                  + profile["build_systems"] + profile["frameworks"])
    assert detections, "the demo repo should yield at least one detection"
    for detection in detections:
        assert set(detection) >= {"value", "evidence", "confidence"}
        assert 0.0 < detection["confidence"] <= 1.0
        assert not Path(detection["evidence"]).is_absolute()


# -- failure handling ----------------------------------------------------------------


def test_a_repository_that_cannot_be_profiled_reports_unavailable(tmp_path: Path):
    profile = profile_for_run(tmp_path / "does-not-exist")
    assert profile["available"] is False
    assert "NotADirectoryError" in profile["error"]


def test_a_failed_profile_carries_no_fabricated_fields(tmp_path: Path):
    """The property that matters: a failure must not be mistakable for a finding about
    the repository. An empty `languages` list would read as 'we looked and found none'."""
    profile = profile_for_run(tmp_path / "does-not-exist")
    assert set(profile) == {"available", "error"}


def test_profiling_failure_never_raises(monkeypatch: pytest.MonkeyPatch):
    """A diagnostic must not fail the run it exists to describe."""
    import web_testing_agent.intake.repo_profile as module

    def boom(*_args, **_kwargs):
        raise PermissionError("mount went away mid-walk")

    monkeypatch.setattr(module, "profile_repository", boom)
    profile = module.profile_for_run(DEMO_REPO)
    assert profile["available"] is False
    assert "PermissionError" in profile["error"]


def test_a_failed_profile_still_serializes(tmp_path: Path):
    report = _report(repository_profile=profile_for_run(tmp_path / "nope"))
    payload = json.loads(json.dumps(report.to_dict(), default=str))
    assert payload["run"]["repository_profile"]["available"] is False


# -- flowing through the report ------------------------------------------------------


def test_the_profile_appears_in_run_meta_under_its_own_key():
    report = _report(repository="/somewhere", repository_profile=profile_for_run(DEMO_REPO))
    assert report.run_meta["repository_profile"]["available"] is True
    assert report.to_dict()["run"]["repository_profile"]["deployable"] is True


def test_the_profile_survives_a_json_round_trip_unchanged():
    """`to_dict()` is the machine-readable artifact; anything lost here is lost for good."""
    profile = profile_for_run(DEMO_REPO)
    report = _report(repository_profile=profile)
    restored = json.loads(json.dumps(report.to_dict(), default=str))["run"]["repository_profile"]
    assert restored == profile


def test_serialization_is_stable_across_repeated_profiling():
    first = _report(repository_profile=profile_for_run(DEMO_REPO)).to_dict()["run"]
    second = _report(repository_profile=profile_for_run(DEMO_REPO)).to_dict()["run"]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_profile_reaches_the_rendered_markdown_as_a_summary():
    """**Changed by M4, deliberately.** Until M4 this asserted that `render_markdown`
    dumped `run_meta` wholesale, which put ~350 lines of profile into every report. M4
    summarizes it instead and keeps the full block in JSON, so the assertion moved from
    "the raw key is present" to "the facts a reader needs are present".
    """
    markdown = render_markdown(_report(repository_profile=profile_for_run(DEMO_REPO)))
    assert "**Repository**" in markdown
    assert "- languages: HTML" in markdown
    assert "- deployable by this pipeline: yes" in markdown
    # The raw serialization is gone from the document but not from the artifact.
    assert '"provenance"' not in markdown


# -- existing behaviour is unchanged --------------------------------------------------


def test_a_report_without_a_profile_is_unchanged():
    """M2 must be additive. No profile, no difference."""
    report = _report()
    assert report.counts["total"] == 1
    assert report.counts["deterministic"] == 1
    assert report.bugs[0].bug_type == "document_http_error"
    assert report.run_meta == {}


def test_run_meta_without_a_profile_still_renders():
    markdown = render_markdown(_report(repository="/somewhere", seed=0))
    assert "## Run" in markdown
    assert "repository_profile" not in markdown


def test_findings_and_counts_do_not_depend_on_the_profile():
    without = _report().to_dict()
    with_profile = _report(repository_profile=profile_for_run(DEMO_REPO)).to_dict()
    assert without["bugs"] == with_profile["bugs"]
    assert without["counts"] == with_profile["counts"]


def test_a_minimal_profile_block_is_accepted_verbatim():
    """`run_meta` is free-form by design; the report must not validate or reshape it."""
    report = _report(repository_profile={"available": False, "error": "x"})
    assert report.to_dict()["run"]["repository_profile"] == {"available": False, "error": "x"}


def test_judge_verdicts_are_unaffected_by_the_profile():
    """The deterministic/judge split is the distinction the report exists to defend."""
    report = build_report(target="http://x/", findings=[dict(_FINDING)], verdicts=[dict(_VERDICT)],
                          run_meta={"repository_profile": profile_for_run(DEMO_REPO)})
    assert report.counts["deterministic"] == 1
    assert report.counts["judge"] == 1
