"""The live judge path: window fidelity, episode reset, gating, and failure handling."""

from dataclasses import dataclass

import numpy as np
import pytest

from web_testing_agent.envs.types import ActionSpec, ActionType, NetworkEvent, RawObservation
from web_testing_agent.intake import ApplicationProfile
from web_testing_agent.judge.verdict import Verdict
from web_testing_agent.reward.llm_judge import JudgeRewardModel


class RecordingJudge:
    """Returns a scripted verdict and keeps everything it was asked."""

    name = "recording"

    def __init__(self, verdicts=None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._verdicts = list(verdicts or [])

    def judge(self, window_text: str, profile_text: str = "") -> Verdict:
        self.calls.append((window_text, profile_text))
        if self._verdicts:
            return self._verdicts.pop(0)
        return Verdict(is_bug=False, confidence=0.9, evidence="nothing")


@dataclass
class FakeContext:
    """Stands in for envs.base_env.StepContext — only the fields the judge path reads."""

    exec_info: dict
    settled: bool = True
    opened_new_page: bool = False
    load_duration_s: float = 0.1


def _obs(url: str, html: str, console=(), network=()) -> RawObservation:
    return RawObservation(
        screenshot=np.zeros((720, 1280, 3), dtype=np.uint8),
        html=html,
        network_events=list(network),
        url=url,
        console_errors=list(console),
        page_errors=[],
    )


def _failed_request(status: int = 500) -> NetworkEvent:
    return NetworkEvent(
        url="http://x/api", method="GET", request_headers={}, request_body=None,
        response_status=status, response_headers={}, response_body_snippet="",
        timestamp=0.0, duration_ms=12.0, resource_type="xhr",
    )


def _spec(action_type=ActionType.CLICK, element="Save", selector="#save") -> ActionSpec:
    return ActionSpec(
        index=1,
        action_type=action_type,
        description=f"{action_type.value} {element}",
        selector=selector,
        element_id=element,
        params={"navigational": False},
    )


def _ctx(success=True, **kwargs) -> FakeContext:
    return FakeContext(exec_info={"success": success, **kwargs})


PAGE_A = "<html><head><title>A</title></head><body><h1>A</h1><a href='/b'>B</a></body></html>"
PAGE_B = "<html><head><title>B</title></head><body><h1>B</h1></body></html>"


def _model(judge=None, **kwargs) -> JudgeRewardModel:
    model = JudgeRewardModel(judge or RecordingJudge(), **kwargs)
    model.start_episode()
    return model


# --- the seam ---------------------------------------------------------------------


def test_score_without_a_context_is_refused():
    """Rendering without execution metadata would produce a window the judge was never
    measured against — a harness refusal would look identical to a broken link."""
    model = _model()
    with pytest.raises(ValueError, match="StepContext"):
        model.score(_obs("/a", PAGE_A), _spec(), _obs("/b", PAGE_B))


def test_start_episode_clears_the_rolling_window():
    """A fresh browser context shares no state with the previous episode, so records
    carried across would invite causal links that cannot exist."""
    judge = RecordingJudge()
    model = _model(judge)
    model.score(_obs("/a", PAGE_A), _spec(), _obs("/b", PAGE_B), context=_ctx())
    model.score(_obs("/b", PAGE_B), _spec(), _obs("/a", PAGE_A), context=_ctx())
    assert judge.calls[-1][0].count("\nSTEP ") == 2

    model.start_episode()
    judge.calls.clear()
    model.score(_obs("/a", PAGE_A), _spec(), _obs("/b", PAGE_B), context=_ctx())
    assert judge.calls[-1][0].count("\nSTEP ") == 1


def test_the_window_never_grows_past_the_configured_depth():
    judge = RecordingJudge()
    model = _model(judge, window_steps=3)
    for _ in range(6):
        model.score(_obs("/a", PAGE_A), _spec(), _obs("/b", PAGE_B), context=_ctx())
    assert judge.calls[-1][0].count("\nSTEP ") <= 3


# --- window fidelity --------------------------------------------------------------


def test_the_live_window_renders_from_the_real_page_bodies():
    """Held in memory rather than as hashes into a trace directory, but through the
    same renderer — a separate live view would let the two drift and quietly invalidate
    every offline measurement."""
    judge = RecordingJudge()
    model = _model(judge)
    model.score(_obs("http://x/a", PAGE_A), _spec(), _obs("http://x/b", PAGE_B), context=_ctx())
    window = judge.calls[-1][0]
    assert "STARTING PAGE" in window
    assert "'A' -> 'B'" in window  # the title diff, which needs both bodies resolved


def test_a_harness_refusal_is_rendered_as_blocked_not_as_a_failure():
    """Measured on Gitea: rendered as 'FAILED: ... did not navigate', 6 of 11 refusals
    became broken_navigation verdicts."""
    judge = RecordingJudge()
    model = _model(judge)
    model.score(
        _obs("http://x/a", PAGE_A),
        _spec(),
        _obs("http://x/a", PAGE_A),
        context=_ctx(success=False, left_application="https://github.com/x"),
    )
    # The refusal is gated out, so nothing is judged — but the record must still be
    # correct, because it becomes context for the next step's window.
    model.score(_obs("http://x/a", PAGE_A), _spec(), _obs("http://x/b", PAGE_B), context=_ctx())
    assert "BLOCKED" in judge.calls[-1][0]
    assert "FAILED" not in judge.calls[-1][0]


def test_byte_identity_is_claimed_only_when_the_documents_match():
    judge = RecordingJudge()
    model = _model(judge)
    model.score(_obs("http://x/a", PAGE_A), _spec(), _obs("http://x/a", PAGE_A), context=_ctx())
    assert "NO OBSERVABLE CHANGE" in judge.calls[-1][0]


def test_console_errors_and_failed_requests_reach_the_window():
    judge = RecordingJudge()
    model = _model(judge)
    after = _obs(
        "http://x/a", PAGE_A,
        console=["TypeError: x is not a function"],
        network=[_failed_request()],
    )
    model.score(_obs("http://x/a", PAGE_A), _spec(), after, context=_ctx())
    window = judge.calls[-1][0]
    assert "TypeError" in window
    assert "500" in window


# --- gating -----------------------------------------------------------------------


def test_a_promising_control_with_no_effect_is_judged():
    judge = RecordingJudge()
    model = _model(judge)
    model.score(_obs("http://x/a", PAGE_A), _spec(), _obs("http://x/a", PAGE_A), context=_ctx())
    assert len(judge.calls) == 1


def test_an_inert_action_that_changed_nothing_is_not_judged():
    judge = RecordingJudge()
    model = _model(judge)
    signal = model.score(
        _obs("http://x/a", PAGE_A), _spec(ActionType.SCROLL, "page", None),
        _obs("http://x/a", PAGE_A), context=_ctx(),
    )
    assert judge.calls == []
    assert signal.is_expected
    assert signal.detail["judge_skipped"]


def test_a_skipped_step_still_enters_the_next_window():
    """The gate chooses where the judge looks, never what it can see. Cross-step bugs
    depend on the typing that a gate would never judge on its own."""
    judge = RecordingJudge()
    model = _model(judge)
    model.score(
        _obs("http://x/f", PAGE_A), _spec(ActionType.TYPE, "email", "#email"),
        _obs("http://x/f", PAGE_A), context=_ctx(),
    )
    assert judge.calls == []
    model.score(_obs("http://x/f", PAGE_A), _spec(), _obs("http://x/done", PAGE_B), context=_ctx())
    assert judge.calls[-1][0].count("\nSTEP ") == 2


def test_gating_can_be_disabled():
    judge = RecordingJudge()
    model = _model(judge, gated=False)
    model.score(
        _obs("http://x/a", PAGE_A), _spec(ActionType.SCROLL, "page", None),
        _obs("http://x/a", PAGE_A), context=_ctx(),
    )
    assert len(judge.calls) == 1


def test_the_per_episode_call_budget_is_enforced():
    judge = RecordingJudge()
    model = _model(judge, max_calls_per_episode=2)
    for _ in range(5):
        model.score(_obs("http://x/a", PAGE_A), _spec(), _obs("http://x/a", PAGE_A), context=_ctx())
    assert len(judge.calls) == 2
    model.start_episode()
    model.score(_obs("http://x/a", PAGE_A), _spec(), _obs("http://x/a", PAGE_A), context=_ctx())
    assert len(judge.calls) == 3


# --- verdict translation ----------------------------------------------------------


def test_a_reported_bug_becomes_an_unexpected_signal_carrying_its_severity():
    judge = RecordingJudge([Verdict(is_bug=True, severity=0.7, confidence=0.9,
                                    bug_type="dead_control", discrepancy="Save did nothing")])
    model = _model(judge)
    signal = model.score(_obs("http://x/a", PAGE_A), _spec(), _obs("http://x/a", PAGE_A), context=_ctx())
    assert not signal.is_expected
    assert signal.severity == pytest.approx(0.7)
    assert signal.explanation == "Save did nothing"
    assert signal.detail["judge_bug_type"] == "dead_control"


def test_a_failed_judge_call_is_never_a_finding():
    """An outage that read as a bug would pay the agent for breaking the judge — a
    reward exploit with no code path to fix."""
    judge = RecordingJudge([Verdict.failed("connection refused")])
    model = _model(judge)
    signal = model.score(_obs("http://x/a", PAGE_A), _spec(), _obs("http://x/a", PAGE_A), context=_ctx())
    assert signal.is_expected
    assert signal.severity == 0.0
    assert model.failed_calls == 1
    assert "connection refused" in signal.detail["judge_error"]


def test_a_low_confidence_verdict_is_recorded_but_does_not_move_the_reward():
    judge = RecordingJudge([Verdict(is_bug=True, severity=0.9, confidence=0.3)])
    model = _model(judge, min_confidence=0.8)
    signal = model.score(_obs("http://x/a", PAGE_A), _spec(), _obs("http://x/a", PAGE_A), context=_ctx())
    assert signal.is_expected
    assert signal.severity == 0.0
    assert model.verdicts[-1]["is_bug"] is True


# --- profile grounding ------------------------------------------------------------


def test_the_profile_is_passed_separately_from_the_window():
    """It must never enter the grounding haystack: a judge that quoted the spec as
    evidence would score as perfectly grounded."""
    judge = RecordingJudge()
    profile = ApplicationProfile.from_dict({
        "name": "Demo", "app_type": "forge", "summary": "s", "auth": "none",
        "provenance": "manual",
        "routes": [{"path": "/a", "purpose": "the A page", "source": "observed"}],
    })
    model = _model(judge, profile=profile)
    model.score(_obs("http://x/a", PAGE_A), _spec(), _obs("http://x/a", PAGE_A), context=_ctx())
    window, profile_text = judge.calls[-1]
    assert "APPLICATION PROFILE" in profile_text
    assert "APPLICATION PROFILE" not in window
    assert "the A page" not in window


def test_no_profile_means_an_empty_profile_argument():
    judge = RecordingJudge()
    model = _model(judge)
    model.score(_obs("http://x/a", PAGE_A), _spec(), _obs("http://x/a", PAGE_A), context=_ctx())
    assert judge.calls[-1][1] == ""


# --- reporting --------------------------------------------------------------------


def test_usage_summary_reports_the_gate_and_the_profile_provenance():
    judge = RecordingJudge()
    model = _model(judge)
    model.score(_obs("http://x/a", PAGE_A), _spec(), _obs("http://x/a", PAGE_A), context=_ctx())
    model.score(
        _obs("http://x/a", PAGE_A), _spec(ActionType.SCROLL, "page", None),
        _obs("http://x/a", PAGE_A), context=_ctx(),
    )
    summary = model.usage_summary()
    assert summary["calls"] == 1
    assert summary["gate"]["skipped"] == 1
    assert summary["grounded"] is False


def test_the_recorded_step_is_the_episode_step_not_the_windows_internal_index():
    """`Verdict` carries its own `step` and it means something different.

    The prompt asks the model which step of the window it is judging, so `Verdict.step`
    is 1..WINDOW_STEPS. Spreading `verdict.to_dict()` over the harness's own fields let
    that overwrite the episode step, and a run of 50 steps produced eight findings all
    claiming to be at "step 6". A finding whose location is wrong cannot be reproduced,
    and nothing downstream could detect it -- the number was plausible.
    """
    from web_testing_agent.judge.verdict import Verdict

    class _FixedJudge:
        name = "fixed"

        def judge(self, window_text, profile_text=None):  # noqa: ANN001, ARG002
            # Always claims to be judging the last step of the window.
            return Verdict(is_bug=True, bug_type="dead_control", severity=0.5,
                           confidence=0.9, step=6, evidence="effect     : NO OBSERVABLE CHANGE")

    model = JudgeRewardModel(judge=_FixedJudge(), gated=False)
    model.start_episode()

    for index in range(9):
        # Alternate the pages so each step genuinely changes state and is judged.
        before, after = (PAGE_A, PAGE_B) if index % 2 == 0 else (PAGE_B, PAGE_A)
        model.score(_obs("/a", before), _spec(), _obs("/b", after), context=_ctx())

    steps = [v["step"] for v in model.verdicts]
    assert steps == list(range(1, 10)), f"episode steps were overwritten: {steps}"
    # The model's own answer is kept, just not where the harness's step belongs.
    assert {v["window_step"] for v in model.verdicts} == {6}
