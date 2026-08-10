import numpy as np
import pytest

from web_testing_agent.envs.types import NetworkEvent, RawObservation
from web_testing_agent.reward.base import RewardSignal
from web_testing_agent.reward.exploration import ExplorationSignal
from web_testing_agent.reward.functional_triggers import (
    ErrorBaseline,
    FindingLedger,
    FunctionalRewardWeights,
    compose_reward,
    detect_bug_signals,
)

EXPECTED = RewardSignal(is_expected=True, severity=0.0)


def _obs(console_errors=None, page_errors=None, network_events=None) -> RawObservation:
    return RawObservation(
        screenshot=np.zeros((2, 2, 3), dtype=np.uint8),
        html="<html></html>",
        network_events=network_events or [],
        url="http://x/",
        console_errors=console_errors or [],
        page_errors=page_errors or [],
    )


def _net_event(status: int | None, url: str = "http://x/api", resource_type: str = "xhr") -> NetworkEvent:
    return NetworkEvent(
        url=url,
        method="GET",
        request_headers={},
        request_body=None,
        response_status=status,
        response_headers={},
        response_body_snippet="",
        timestamp=0.0,
        duration_ms=1.0,
        resource_type=resource_type,
    )


def _explore(novel: bool = False, repeats: int = 0) -> ExplorationSignal:
    return ExplorationSignal(
        is_novel_state=novel, repeat_count=repeats, episode_states_seen=1, total_states_seen=1
    )


# --- bug signal detection -------------------------------------------------------


def test_no_signals_on_a_clean_step():
    signals = detect_bug_signals(_obs(), 0.5, pre_url="a", post_url="b", was_navigation_action=True)
    assert not signals.any_triggered


def test_4xx_response_flags_unexpected_http_error():
    signals = detect_bug_signals(
        _obs(network_events=[_net_event(404)]), 0.5, pre_url="a", post_url="b", was_navigation_action=False
    )
    assert signals.unexpected_http_errors
    assert signals.any_triggered


def test_2xx_response_does_not_flag_error():
    signals = detect_bug_signals(
        _obs(network_events=[_net_event(200)]), 0.5, pre_url="a", post_url="b", was_navigation_action=False
    )
    assert not signals.unexpected_http_errors


def test_document_error_is_distinguished_from_subresource_error():
    doc = detect_bug_signals(
        _obs(network_events=[_net_event(404, resource_type="document")]),
        0.5, pre_url="a", post_url="b", was_navigation_action=False,
    )
    sub = detect_bug_signals(
        _obs(network_events=[_net_event(404, resource_type="image")]),
        0.5, pre_url="a", post_url="b", was_navigation_action=False,
    )
    assert doc.document_http_error
    assert not sub.document_http_error


def test_baseline_errors_are_not_counted_as_bugs():
    """A favicon that 404s on every page load must not pay a bonus on every step."""
    favicon = _net_event(404, url="http://x/favicon.ico", resource_type="image")
    baseline = ErrorBaseline()
    baseline.learn([favicon])

    signals = detect_bug_signals(
        _obs(network_events=[favicon]), 0.5, pre_url="a", post_url="b",
        was_navigation_action=False, baseline=baseline,
    )
    assert not signals.unexpected_http_errors


def test_baseline_does_not_mask_a_genuinely_new_error():
    baseline = ErrorBaseline()
    baseline.learn([_net_event(404, url="http://x/favicon.ico")])

    signals = detect_bug_signals(
        _obs(network_events=[_net_event(500, url="http://x/api/orders")]), 0.5,
        pre_url="a", post_url="b", was_navigation_action=False, baseline=baseline,
    )
    assert len(signals.unexpected_http_errors) == 1


def test_baseline_ignores_query_strings_when_matching():
    baseline = ErrorBaseline()
    baseline.learn([_net_event(404, url="http://x/asset.js?v=1")])
    signals = detect_bug_signals(
        _obs(network_events=[_net_event(404, url="http://x/asset.js?v=2")]), 0.5,
        pre_url="a", post_url="b", was_navigation_action=False, baseline=baseline,
    )
    assert not signals.unexpected_http_errors


def test_click_that_does_not_navigate_is_flagged_as_broken_navigation():
    signals = detect_bug_signals(_obs(), 0.5, pre_url="a", post_url="a", was_navigation_action=True)
    assert signals.broken_navigation


def test_type_action_landing_on_same_url_is_not_broken_navigation():
    signals = detect_bug_signals(_obs(), 0.5, pre_url="a", post_url="a", was_navigation_action=False)
    assert not signals.broken_navigation


def test_link_that_opened_a_popup_is_not_broken_navigation():
    """target=_blank leaves this tab's URL alone; that is correct, not a dead link."""
    signals = detect_bug_signals(
        _obs(), 0.5, pre_url="a", post_url="a", was_navigation_action=True, opened_new_page=True
    )
    assert not signals.broken_navigation


def test_slow_response_comes_from_failure_to_settle_not_elapsed_time():
    # The old threshold (>5s elapsed) was unreachable: the settle wait caps out at 3s,
    # so a genuinely churning page never tripped it.
    churning = detect_bug_signals(_obs(), 1.2, pre_url="a", post_url="a", was_navigation_action=False, settled=False)
    quick = detect_bug_signals(_obs(), 9.9, pre_url="a", post_url="a", was_navigation_action=False, settled=True)
    assert churning.slow_response
    assert not quick.slow_response


def test_an_already_hung_page_is_not_re_reported_every_step():
    """One runaway timer must not blame every subsequent action on the page.

    Measured on the toy site before this was edge-triggered: BUG-07's stuck spinner
    produced four separate findings, three of them against innocent controls.
    """
    signals = detect_bug_signals(
        _obs(), 1.2, pre_url="a", post_url="a", was_navigation_action=False,
        settled=False, previously_settled=False,
    )
    assert not signals.slow_response


# --- reward composition ---------------------------------------------------------


def test_doing_nothing_is_not_rewarded():
    """The central reward-hacking guard.

    A guaranteed positive per-step reward makes NO_OP the highest-value action: with
    gamma=0.99 over 200 steps an all-NO_OP policy would bank ~43 discounted reward
    against ~10 for finding an actual bug, and ~70% of the 100-slot action space
    resolves to NO_OP on a typical page.
    """
    weights = FunctionalRewardWeights()
    clean = detect_bug_signals(_obs(), 0.1, "a", "a", False)
    reward, _ = compose_reward(EXPECTED, clean, weights, exploration=_explore())
    assert reward < 0
    assert weights.expected_reward == 0.0


def test_finding_a_bug_dominates_the_step_cost():
    weights = FunctionalRewardWeights()
    clean = detect_bug_signals(_obs(), 0.1, "a", "a", False)
    quiet, _ = compose_reward(EXPECTED, clean, weights, exploration=_explore())
    found, _ = compose_reward(
        RewardSignal(is_expected=False, severity=0.8), clean, weights, exploration=_explore()
    )
    assert found > quiet + 5


def test_compose_reward_unexpected_scales_with_severity():
    weights = FunctionalRewardWeights()
    clean = detect_bug_signals(_obs(), 0.1, "a", "a", False)
    low, _ = compose_reward(RewardSignal(is_expected=False, severity=0.2), clean, weights)
    high, _ = compose_reward(RewardSignal(is_expected=False, severity=0.8), clean, weights)
    assert high - low == weights.bug_severity_scale * 0.6


def test_compose_reward_adds_deterministic_bonus_on_top_of_llm_base():
    weights = FunctionalRewardWeights()
    buggy = detect_bug_signals(_obs(console_errors=["TypeError: x is undefined"]), 0.1, "a", "a", False)
    _, breakdown = compose_reward(EXPECTED, buggy, weights)
    assert breakdown["deterministic_bonus"] == weights.console_error_bonus


def test_compose_reward_stacks_multiple_deterministic_bonuses():
    weights = FunctionalRewardWeights()
    buggy = detect_bug_signals(
        _obs(console_errors=["err"], network_events=[_net_event(500, resource_type="document")]),
        load_duration_s=6.0, pre_url="a", post_url="a", was_navigation_action=True, settled=False,
    )
    _, breakdown = compose_reward(EXPECTED, buggy, weights)
    assert breakdown["deterministic_bonus"] == (
        weights.console_error_bonus
        + weights.http_error_bonus
        + weights.document_http_error_bonus
        + weights.slow_response_bonus
        + weights.broken_navigation_bonus
    )


def test_reaching_a_new_state_is_rewarded():
    weights = FunctionalRewardWeights()
    clean = detect_bug_signals(_obs(), 0.1, "a", "a", False)
    novel, _ = compose_reward(EXPECTED, clean, weights, exploration=_explore(novel=True))
    stale, _ = compose_reward(EXPECTED, clean, weights, exploration=_explore(novel=False))
    assert novel - stale == weights.novelty_bonus
    assert novel > 0


def test_repeating_an_action_is_penalized_and_floored():
    weights = FunctionalRewardWeights()
    clean = detect_bug_signals(_obs(), 0.1, "a", "a", False)
    once, _ = compose_reward(EXPECTED, clean, weights, exploration=_explore(repeats=1))
    twice, _ = compose_reward(EXPECTED, clean, weights, exploration=_explore(repeats=2))
    spammed, _ = compose_reward(EXPECTED, clean, weights, exploration=_explore(repeats=50))
    assert twice < once
    assert spammed == once + weights.max_repetition_penalty - weights.repetition_penalty


def _stacked_404() -> object:
    """A 404 *document* — the highest-paying deterministic finding, stacking 3 triggers."""
    return detect_bug_signals(
        _obs(
            console_errors=["Failed to load resource: 404"],
            network_events=[_net_event(404, url="http://x/pricing", resource_type="document")],
        ),
        0.1, pre_url="a", post_url="b", was_navigation_action=False,
    )


def test_camping_on_a_known_broken_element_stops_paying():
    """The reward-hacking scenario from the risk register, as an executable assertion.

    Uses the *stacked* 404-document case deliberately. An earlier version of this test
    used a lone console error (+2.0), where the -3.0 repetition floor does win — so it
    passed while the exploit was live. A trained DQN then found the real hole: a 404
    document pays console(+2) + http(+3) + document_http(+2) = +7.0 against a penalty
    floored at -3.0, netting +3.95/step forever. Always test the cheapest exploit
    available to the optimizer, not the cheapest one to write.
    """
    weights = FunctionalRewardWeights()
    ledger = FindingLedger()
    ledger.start_episode()
    buggy = _stacked_404()

    first, _ = compose_reward(
        EXPECTED, buggy, weights, exploration=_explore(novel=True, repeats=0),
        new_triggers=ledger.classify("state-404", "Pricing", buggy),
    )
    later = [
        compose_reward(
            EXPECTED, buggy, weights, exploration=_explore(novel=False, repeats=n),
            new_triggers=ledger.classify("state-404", "Pricing", buggy),
        )[0]
        for n in range(1, 12)
    ]

    assert first > 0, "discovering the bug must pay"
    assert all(r < 0 for r in later), "re-triggering a known finding must never pay"
    # The exploit was worth +3.95/step indefinitely; the whole tail must now be negative.
    assert sum(later) < 0


def test_the_measured_exploit_episode_is_no_longer_profitable():
    """Replays the exact policy the trained DQN converged on.

    Observed: CLICK the 404 link once, then REFRESH it 39 more times for +174.50 —
    65x the random baseline's return from two states and one real bug.
    """
    weights = FunctionalRewardWeights()
    ledger = FindingLedger()
    ledger.start_episode()
    buggy = _stacked_404()

    total = 0.0
    for step in range(40):
        reward, _ = compose_reward(
            EXPECTED, buggy, weights,
            exploration=_explore(novel=(step == 0), repeats=max(0, step - 1)),
            new_triggers=ledger.classify("state-404", "Pricing", buggy),
        )
        total += reward

    assert total < 10, f"camping on one 404 still returns {total:+.2f} (was +174.50)"


def test_a_second_distinct_finding_still_pays_full():
    """Deduplication must not suppress genuinely new bugs elsewhere in the app."""
    weights = FunctionalRewardWeights()
    ledger = FindingLedger()
    ledger.start_episode()
    buggy = _stacked_404()

    compose_reward(EXPECTED, buggy, weights, new_triggers=ledger.classify("state-a", "Pricing", buggy))
    elsewhere, breakdown = compose_reward(
        EXPECTED, buggy, weights, new_triggers=ledger.classify("state-b", "Generate report", buggy)
    )
    assert breakdown["deterministic_bonus"] == (
        weights.console_error_bonus + weights.http_error_bonus + weights.document_http_error_bonus
    )
    assert elsewhere > 0


def test_findings_are_payable_again_in_a_new_episode():
    """Episodic, not permanent: a permanent ledger would erase the learning signal."""
    weights = FunctionalRewardWeights()
    ledger = FindingLedger()
    buggy = _stacked_404()

    ledger.start_episode()
    ledger.classify("state-404", "Pricing", buggy)
    repeat, _ = compose_reward(EXPECTED, buggy, weights, new_triggers=ledger.classify("state-404", "Pricing", buggy))

    ledger.start_episode()
    fresh, _ = compose_reward(EXPECTED, buggy, weights, new_triggers=ledger.classify("state-404", "Pricing", buggy))
    assert fresh > repeat


def test_failed_action_costs_more_than_a_successful_one():
    weights = FunctionalRewardWeights()
    clean = detect_bug_signals(_obs(), 0.1, "a", "a", False)
    ok, _ = compose_reward(EXPECTED, clean, weights, exec_success=True)
    failed, _ = compose_reward(EXPECTED, clean, weights, exec_success=False)
    assert failed - ok == pytest.approx(weights.failed_action_penalty)
