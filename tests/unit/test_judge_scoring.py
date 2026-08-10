import json
from pathlib import Path

from web_testing_agent.judge import Judgment, Verdict, score, summarize

ANSWER_KEY = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "toy_site" / "answer_key.json").read_text(encoding="utf-8")
)


def _j(episode: int, bugs: list[str], is_bug: bool, step: int = 1, error: str | None = None) -> Judgment:
    verdict = Verdict.failed(error) if error else Verdict(is_bug=is_bug, confidence=0.9)
    return Judgment(episode=episode, global_step=step, verdict=verdict, expected_bugs=bugs)


def test_a_positive_anywhere_in_an_episode_detects_its_bugs():
    report = score([_j(1, ["BUG-02"], False), _j(1, ["BUG-02"], True, step=2)])
    assert "BUG-02" in report.detected
    assert report.missed == []


def test_an_episode_with_no_positive_counts_as_missed():
    report = score([_j(1, ["BUG-02"], False), _j(1, ["BUG-02"], False)])
    assert report.detected == {}
    assert report.missed == ["BUG-02"]


def test_an_episode_carrying_two_bugs_credits_both():
    """Generous by construction — one verdict cannot separate BUG-01 from BUG-05."""
    report = score([_j(1, ["BUG-01", "BUG-05"], True)])
    assert set(report.detected) == {"BUG-01", "BUG-05"}


def test_positives_on_a_control_episode_are_false_positives():
    report = score([_j(9, [], True), _j(9, [], False), _j(9, [], True)])
    assert report.control_windows == 3
    assert report.control_positives == 2
    assert report.false_positive_rate == 2 / 3
    assert report.false_positive_episodes == {9: 2}


def test_a_clean_control_episode_produces_no_false_positives():
    report = score([_j(9, [], False) for _ in range(5)])
    assert report.control_positives == 0 and report.false_positive_rate == 0.0


def test_failed_calls_are_counted_and_never_scored():
    """An outage must not read as 'the judge found nothing'."""
    report = score([_j(1, ["BUG-02"], False, error="timeout"), _j(1, ["BUG-02"], False, error="500")])
    assert report.failed_calls == 2
    assert report.episodes_scored == 0
    assert report.detected == {} and report.missed == []


def test_recall_counts_only_episodes_that_were_actually_scored():
    report = score([_j(1, ["BUG-02"], True), _j(2, ["BUG-06"], False)])
    assert report.recall == 0.5


def test_a_bug_seen_in_two_episodes_counts_as_found_if_either_hits():
    report = score([_j(1, ["BUG-03"], False), _j(2, ["BUG-03"], True)])
    assert "BUG-03" in report.detected and report.missed == []


def test_summary_separates_semantic_from_deterministic_bugs():
    """The 7 semantic bugs are the headroom; the 3 deterministic ones prove nothing new."""
    report = score(
        [_j(1, ["BUG-02"], True), _j(2, ["BUG-03"], True), _j(3, ["BUG-09"], False)],
        corpus="scripted", judge="test",
    )
    text = summarize(report, ANSWER_KEY)
    assert "semantic bugs found   : 1/7" in text
    assert "deterministic found   : 1/3" in text
    assert "BUG-09" in text


def test_report_round_trips_to_json():
    report = score([_j(1, ["BUG-02"], True), _j(9, [], True)], corpus="scripted", judge="test")
    payload = json.loads(json.dumps(report.to_dict()))
    assert payload["corpus"] == "scripted"
    assert payload["detected"]["BUG-02"] == [1]
    assert payload["false_positive_rate"] == 1.0


def test_unlabelled_positives_are_not_false_positives():
    """Random exploration genuinely finds bugs; scoring its hits as false alarms invents a failure."""
    report = score([
        Judgment(episode=1, global_step=i, verdict=Verdict(is_bug=i < 3), labelled=False)
        for i in range(5)
    ])
    assert report.unattributed_windows == 5
    assert report.unattributed_positives == 3
    assert report.control_windows == 0
    assert report.false_positive_rate == 0.0
    assert report.detected == {} and report.missed == []


def test_unlabelled_windows_do_not_count_as_scored_episodes():
    report = score([Judgment(episode=1, global_step=1, verdict=Verdict(is_bug=True), labelled=False)])
    assert report.episodes_scored == 0
    assert report.positives == 1


def test_a_known_correct_episode_is_still_a_control_when_labelled():
    report = score([Judgment(episode=9, global_step=1, verdict=Verdict(is_bug=True),
                             expected_bugs=[], labelled=True)])
    assert report.control_windows == 1 and report.control_positives == 1
    assert report.unattributed_windows == 0


def test_summary_reports_unlabelled_corpora_without_claiming_recall():
    report = score([Judgment(episode=1, global_step=1, verdict=Verdict(is_bug=True), labelled=False)],
                   corpus="random", judge="test")
    text = summarize(report, ANSWER_KEY)
    assert "not scorable" in text
    assert "semantic bugs found" not in text


# --- discrimination --------------------------------------------------------------


def test_discrimination_is_zero_when_the_judge_always_says_bug():
    """The failure recall alone cannot see: 7/7 recall at 88%/71% is no signal at all."""
    judgments = [_j(1, ["BUG-02"], True) for _ in range(8)] + [_j(9, [], True) for _ in range(8)]
    report = score(judgments)
    assert report.recall == 1.0
    assert report.discrimination == 0.0


def test_discrimination_is_zero_when_the_judge_never_says_bug():
    judgments = [_j(1, ["BUG-02"], False) for _ in range(8)] + [_j(9, [], False) for _ in range(8)]
    assert score(judgments).discrimination == 0.0


def test_a_perfect_judge_discriminates_fully():
    judgments = [_j(1, ["BUG-02"], True) for _ in range(8)] + [_j(9, [], False) for _ in range(8)]
    report = score(judgments)
    assert report.discrimination == 1.0
    assert report.false_positive_rate == 0.0


def test_discrimination_needs_both_classes_present():
    """A corpus with no control episode cannot support the claim, so it reports 0."""
    assert score([_j(1, ["BUG-02"], True)]).discrimination == 0.0


def test_summary_puts_discrimination_next_to_recall():
    report = score([_j(1, ["BUG-02"], True), _j(9, [], True)], corpus="scripted", judge="test")
    assert "DISCRIMINATION" in summarize(report, ANSWER_KEY)


# --- evidence grounding ----------------------------------------------------------

WINDOW = """STEP 1
  action     : CLICK on 'Export data'
  url        : /widgets.html (unchanged)
  effect     : NO OBSERVABLE CHANGE — the page is byte-identical after this action"""


def test_a_verbatim_quote_is_grounded():
    from web_testing_agent.judge.scoring import is_grounded

    assert is_grounded("NO OBSERVABLE CHANGE — the page is byte-identical", WINDOW)


def test_a_quote_that_drops_the_rendered_label_is_still_grounded():
    """Reflowing whitespace and dropping the "effect :" label is not fabrication."""
    from web_testing_agent.judge.scoring import is_grounded

    assert is_grounded("the page is byte-identical after this action", WINDOW)


def test_the_threshold_sits_in_the_gap_between_quoting_and_not():
    """Chosen from the measured distribution, not picked to make a test pass.

    On the scripted corpus: gpt-oss:120b scored exactly 1.00 on all 12 of its positives,
    while qwen2.5:7b put 17 of 25 below 0.8, trailing down to 0.22. Nothing landed near
    the boundary, so the exact value is not load-bearing.
    """
    from web_testing_agent.judge.scoring import _GROUNDING_THRESHOLD

    assert 0.7 <= _GROUNDING_THRESHOLD <= 0.9


def test_an_invented_observation_is_not_grounded():
    """Measured on qwen2.5:7b at confidence 1.0: SCROLL resets no counter, and no such
    line existed in the window it was shown."""
    from web_testing_agent.judge.scoring import is_grounded

    assert not is_grounded("After scrolling, the counter value is reset to 0 instead of 2", WINDOW)


def test_an_empty_or_trivial_quote_is_not_grounded():
    from web_testing_agent.judge.scoring import is_grounded

    assert not is_grounded("", WINDOW)
    assert not is_grounded("bug", WINDOW)


def test_grounding_is_only_scored_on_positives():
    report = score([
        Judgment(episode=1, global_step=1, verdict=Verdict(is_bug=True), expected_bugs=["BUG-02"], grounded=True),
        Judgment(episode=1, global_step=2, verdict=Verdict(is_bug=False), expected_bugs=["BUG-02"], grounded=None),
    ])
    assert report.checked_positives == 1
    assert report.evidence_grounded == 1.0


def test_ungrounded_positives_lower_the_score():
    report = score([
        Judgment(episode=1, global_step=i, verdict=Verdict(is_bug=True), expected_bugs=["BUG-02"], grounded=i < 3)
        for i in range(4)
    ])
    assert report.checked_positives == 4
    assert report.evidence_grounded == 0.75


def test_grounding_defaults_to_one_when_never_checked():
    """An unchecked run must not look like a failing one."""
    assert score([_j(1, ["BUG-02"], True)]).evidence_grounded == 1.0
