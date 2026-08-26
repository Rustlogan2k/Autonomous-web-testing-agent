"""The Bug Report Engine.

The report is the one artifact a human reads and acts on, so the properties worth pinning
are the ones that would mislead that human: a fabricated citation presented as a finding,
a machine-detectable bug credited to the language model, or a defect with no way to
reproduce it.
"""

from __future__ import annotations

from web_testing_agent.reporting import (
    SOURCE_DETERMINISTIC,
    SOURCE_JUDGE,
    build_report,
    describe_action,
    render_markdown,
)

WINDOW = (
    "STEP 1\n"
    "  action     : CLICK on 'Start order'\n"
    "  url        : /index.html -> /order-1.html\n"
    "STEP 2\n"
    "  action     : SELECT on 'product' with value 'folders-25'\n"
    "  form state : product: empty -> filled\n"
    "STEP 3\n"
    "  action     : CLICK on 'Continue'\n"
    "  page text  : Quantity 1 | Order confirmed\n"
)


def _verdict(**overrides) -> dict:
    base = {
        "episode": 1,
        "step": 3,
        "is_bug": True,
        "bug_type": "broken_flow",
        "severity": 0.8,
        "confidence": 0.9,
        "observed": "The confirmation reports quantity 1",
        "expected": "The confirmation should report the quantity that was ordered",
        "discrepancy": "The confirmation reports quantity 1 regardless of what was ordered",
        "evidence": "page text  : Quantity 1 | Order confirmed",
        "window_text": WINDOW,
        "repro": [
            {"type": "CLICK", "element": "Start order"},
            {"type": "SELECT", "element": "product", "params": {"option": "folders-25"}},
            {"type": "CLICK", "element": "Continue"},
        ],
    }
    return {**base, **overrides}


def _finding(**overrides) -> dict:
    base = {
        "trigger": "document_http_error",
        "url": "http://localhost/missing.html",
        "action": "CLICK",
        "element": "Archive",
        "detail": "404 http://localhost/missing.html",
        "first_seen_step": 7,
        "times_seen": 3,
    }
    return {**base, **overrides}


# --- the distinction the whole project rests on -----------------------------------


def test_deterministic_and_judge_findings_are_counted_separately():
    """A reader must be able to see what a language model was actually needed for.

    Merging them into one list would erase the 3/10-vs-10/10 comparison this project
    exists to make, inside the very document that reports the result.
    """
    report = build_report(target="app", findings=[_finding()], verdicts=[_verdict()])
    assert report.counts == {
        "total": 2, "deterministic": 1, "judge": 1,
        "high": 2, "medium": 0, "low": 0, "ungrounded_excluded": 0,
    }
    sources = {bug.source for bug in report.bugs}
    assert sources == {SOURCE_DETERMINISTIC, SOURCE_JUDGE}


def test_an_ungrounded_verdict_is_excluded_from_findings():
    """A citation that is not in the window is evidence about the judge, not the app.

    Measured at 68% fabricated citations on one 7B model. Including these would put
    invented quotes into a document a human is meant to trust.
    """
    invented = _verdict(evidence="after scrolling, the counter value is reset to 0")
    report = build_report(target="app", verdicts=[invented])

    assert report.bugs == []
    assert len(report.ungrounded) == 1
    assert report.ungrounded[0].grounded is False
    assert report.counts["ungrounded_excluded"] == 1


def test_an_ungrounded_verdict_is_still_reported_rather_than_silently_dropped():
    """Dropping it would hide a misbehaving judge; the run must stay auditable."""
    report = build_report(target="app", verdicts=[_verdict(evidence="entirely invented text here")])
    markdown = render_markdown(report)
    assert "Excluded: verdicts whose evidence was not in the window" in markdown
    assert "not** findings about the application" in markdown


def test_a_verdict_whose_window_was_not_retained_is_not_assumed_grounded():
    """"Unchecked" must never render as "checked and passed"."""
    report = build_report(target="app", verdicts=[_verdict(window_text="")])
    assert report.bugs == []
    assert len(report.ungrounded) == 1


# --- reproducibility ---------------------------------------------------------------


def test_a_judge_finding_carries_the_action_sequence_that_produced_it():
    report = build_report(target="app", verdicts=[_verdict()])
    assert report.bugs[0].repro == [
        "Click Start order",
        "Select 'folders-25' in product",
        "Click Continue",
    ]


def test_repro_steps_are_rendered_as_a_numbered_list():
    markdown = render_markdown(build_report(target="app", verdicts=[_verdict()]))
    assert "**Steps to reproduce**" in markdown
    assert "1. Click Start order" in markdown
    assert "3. Click Continue" in markdown


def test_typed_values_appear_in_the_repro_step():
    """A validation bug is not reproducible without the value that triggered it."""
    step = describe_action({"type": "TYPE", "element": "age", "params": {"value": "999999999"}})
    assert step == "Type '999999999' into age"


# --- severity and ordering ---------------------------------------------------------


def test_judge_severity_is_rescaled_to_the_ten_point_ordering():
    report = build_report(target="app", verdicts=[_verdict(severity=0.8)])
    assert report.bugs[0].severity == 8.0
    assert report.bugs[0].severity_label == "high"


def test_findings_are_ordered_most_severe_first():
    """A reader works top-down and stops when time runs out, so order is output."""
    report = build_report(
        target="app",
        findings=[_finding(trigger="slow_response", detail="3.0s without quiescence")],
        verdicts=[_verdict(severity=0.9)],
    )
    assert [round(bug.severity, 1) for bug in report.bugs] == [9.0, 3.0]


def test_a_verdict_below_min_confidence_is_not_reported_as_a_finding():
    """The report and the reward signal must agree on what counts as found."""
    report = build_report(target="app", verdicts=[_verdict(confidence=0.3)], min_confidence=0.5)
    assert report.bugs == [] and report.ungrounded == []


def test_non_bug_verdicts_never_reach_the_report():
    assert build_report(target="app", verdicts=[_verdict(is_bug=False)]).bugs == []


# --- the document itself -----------------------------------------------------------


def test_an_empty_run_produces_a_report_that_says_so():
    """Finding nothing is a result, not a failure, and must render as one."""
    markdown = render_markdown(build_report(target="app"))
    assert "**0 findings**" in markdown
    assert "No findings." in markdown


def test_the_report_states_that_severity_is_not_cvss():
    """The spec says 'CVSS-inspired'; the document must not imply it is a CVSS score."""
    markdown = render_markdown(build_report(target="app", verdicts=[_verdict()]))
    assert "not a CVSS score" in markdown


def test_the_report_says_how_many_findings_needed_a_language_model():
    markdown = render_markdown(build_report(target="app", findings=[_finding()], verdicts=[_verdict()]))
    assert "1 were found by deterministic triggers" in markdown
    assert "1 required semantic judgment" in markdown


def test_the_json_form_round_trips_every_field():
    payload = build_report(target="app", findings=[_finding()], verdicts=[_verdict()]).to_dict()
    assert payload["counts"]["total"] == 2
    assert payload["target"] == "app"
    assert {bug["source"] for bug in payload["bugs"]} == {SOURCE_DETERMINISTIC, SOURCE_JUDGE}
