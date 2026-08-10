import pytest

from web_testing_agent.judge import Verdict


def _payload(**overrides) -> str:
    import json

    data = {
        "is_bug": True, "bug_type": "dead_control", "severity": 0.5,
        "confidence": 0.9, "step": 2, "expected": "something", "actual": "nothing",
        "evidence": "NO OBSERVABLE CHANGE",
    }
    data.update(overrides)
    return json.dumps(data)


def test_parses_a_well_formed_verdict():
    verdict = Verdict.parse(_payload())
    assert verdict.ok and verdict.is_bug
    assert verdict.bug_type == "dead_control"
    assert verdict.severity == 0.5 and verdict.confidence == 0.9


@pytest.mark.parametrize(
    "wrapper",
    [
        "```json\n{body}\n```",
        "```\n{body}\n```",
        "Here is my assessment:\n{body}\nHope that helps.",
    ],
)
def test_tolerates_wrapped_json(wrapper):
    """Structured outputs guarantee clean JSON, but local models have no such guarantee."""
    verdict = Verdict.parse(wrapper.format(body=_payload()))
    assert verdict.ok and verdict.is_bug


def test_malformed_output_is_an_error_not_a_clean_verdict():
    """A parse failure must never be scored as 'no bug found' — that deflates recall silently."""
    verdict = Verdict.parse("I think the button might be broken?")
    assert not verdict.ok
    assert not verdict.is_bug
    assert "unparseable" in verdict.error


def test_a_json_array_is_rejected():
    verdict = Verdict.parse("[1, 2, 3]")
    assert not verdict.ok


def test_failed_verdicts_are_distinguishable_from_clean_ones():
    failed, clean = Verdict.failed("timeout"), Verdict(is_bug=False)
    assert failed.is_bug == clean.is_bug is False
    assert not failed.ok and clean.ok


def test_scores_are_clamped_to_the_unit_interval():
    verdict = Verdict.parse(_payload(severity=7, confidence=-2))
    assert verdict.severity == 1.0 and verdict.confidence == 0.0


def test_unknown_bug_type_falls_back_rather_than_failing():
    verdict = Verdict.parse(_payload(bug_type="cosmic_ray"))
    assert verdict.ok and verdict.bug_type == "other"


def test_non_numeric_scores_do_not_raise():
    verdict = Verdict.parse(_payload(severity="high", step="third"))
    assert verdict.ok and verdict.severity == 0.0 and verdict.step == 0


def test_long_fields_are_truncated():
    verdict = Verdict.parse(_payload(evidence="x" * 5000))
    assert len(verdict.evidence) == 500


# --- schema field order ----------------------------------------------------------


def test_the_verdict_is_the_last_field_generated():
    """Constrained decoding emits fields in declared order, so order *is* the reasoning order.

    Measured with is_bug first: llama3:8b wrote `actual` = "NO OBSERVABLE CHANGE — the
    page is byte-identical after this action", typed it `dead_control`, and returned
    is_bug=false anyway. The boolean was fixed before any analysis existed, and the
    remaining fields narrated a verdict already committed.
    """
    from web_testing_agent.judge.verdict import VERDICT_SCHEMA

    order = list(VERDICT_SCHEMA["properties"])
    assert order[-1] == "is_bug"
    for field in ("observed", "expected", "discrepancy", "evidence"):
        assert order.index(field) < order.index("is_bug")


def test_required_lists_every_property():
    from web_testing_agent.judge.verdict import VERDICT_SCHEMA

    assert set(VERDICT_SCHEMA["required"]) == set(VERDICT_SCHEMA["properties"])
    assert VERDICT_SCHEMA["additionalProperties"] is False


def test_analysis_fields_round_trip():
    verdict = Verdict.parse(_payload(observed="nothing changed", discrepancy="Export did nothing"))
    assert verdict.observed == "nothing changed"
    assert verdict.discrepancy == "Export did nothing"
    assert verdict.to_dict()["discrepancy"] == "Export did nothing"


def test_the_prompt_does_not_excuse_a_dead_control():
    """An earlier prompt listed 'a button with no visible effect' as correct behaviour,
    which explicitly excused the easiest semantic bug in the answer key."""
    from web_testing_agent.judge import SYSTEM_PROMPT

    # The no-change case must be named as reportable...
    assert "NO OBSERVABLE CHANGE" in SYSTEM_PROMPT
    assert "dead control and you must" in SYSTEM_PROMPT
    # ...and must not appear anywhere in the list of behaviours to stay silent about.
    correct_section = SYSTEM_PROMPT.split("must never be reported:")[1].split("\n\n")[0]
    for excuse in ("no visible effect", "cannot observe", "might have"):
        assert excuse not in correct_section


# --- prompt styles ---------------------------------------------------------------


def test_compact_style_demonstrates_instead_of_describing():
    """Few-shot lands as real turns: a 7B follows a shown exchange far more reliably
    than a described one. Measured: evidence grounding went 32% -> 100%."""
    from web_testing_agent.judge.prompt import build_messages

    messages = build_messages("STEP 1", style="compact")
    assert [m["role"] for m in messages[:3]] == ["system", "user", "assistant"]
    assert messages[-1]["role"] == "user" and "STEP 1" in messages[-1]["content"]
    assert sum(1 for m in messages if m["role"] == "assistant") >= 2


def test_compact_style_is_materially_shorter_than_detailed():
    from web_testing_agent.judge.prompt import COMPACT_SYSTEM_PROMPT, SYSTEM_PROMPT

    assert len(COMPACT_SYSTEM_PROMPT) < len(SYSTEM_PROMPT)


def test_few_shot_examples_are_valid_verdicts():
    """A malformed example teaches the model to emit malformed output."""
    from web_testing_agent.judge.prompt import FEW_SHOT

    for _window, answer in FEW_SHOT:
        verdict = Verdict.parse(answer)
        assert verdict.ok, f"unparseable few-shot answer: {answer[:80]}"
        assert verdict.evidence, "every example must model quoting evidence"


def test_few_shot_shows_both_outcomes():
    """Only-positive examples would teach the model to always say bug."""
    from web_testing_agent.judge.prompt import FEW_SHOT

    outcomes = {Verdict.parse(answer).is_bug for _w, answer in FEW_SHOT}
    assert outcomes == {True, False}


def test_few_shot_evidence_is_copied_from_its_own_window():
    """The examples must model the behaviour the grounding check enforces."""
    from web_testing_agent.judge.prompt import FEW_SHOT
    from web_testing_agent.judge.scoring import is_grounded

    for window, answer in FEW_SHOT:
        assert is_grounded(Verdict.parse(answer).evidence, window)


def test_few_shot_uses_a_different_application_than_the_fixture():
    """Examples drawn from the site under test would leak its specifics into the judge."""
    from web_testing_agent.judge.prompt import FEW_SHOT

    blob = " ".join(w + a for w, a in FEW_SHOT).lower()
    for fixture_term in ("widgets.html", "signup", "darkmode", "btn-export", "acme"):
        assert fixture_term not in blob


def test_unknown_prompt_style_is_rejected():
    from web_testing_agent.judge.prompt import build_messages

    with pytest.raises(ValueError, match="unknown prompt style"):
        build_messages("STEP 1", style="freestyle")
