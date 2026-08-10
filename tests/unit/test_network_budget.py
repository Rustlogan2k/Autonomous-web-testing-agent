"""The network trace must stay parseable when it exceeds its byte budget.

The original implementation serialized every event and sliced the resulting *string*,
which cuts mid-object. Every consumer parses that field, so a page over budget lost the
whole trace for that step rather than its tail — and it lost it silently. The toy
site's payloads are orders of magnitude too small to reach the budget, so nothing here
was reachable by any existing test; a real application will hit it on its busiest
steps, which are exactly the steps where a failed request matters most.
"""

import json

from web_testing_agent.envs.base_env import _NETWORK_TEXT_MAX, WebTestingEnv
from web_testing_agent.envs.types import NetworkEvent


def _event(url: str, status: int = 200, resource_type: str = "image", body: str = "") -> NetworkEvent:
    return NetworkEvent(
        url=url, method="GET", request_headers={}, request_body=None,
        response_status=status, response_headers={}, response_body_snippet=body,
        timestamp=0.0, duration_ms=1.0, resource_type=resource_type,
    )


def _bulky(url: str, **kwargs) -> NetworkEvent:
    """One event large enough that a handful of them blow the budget."""
    return _event(url, body="x" * 20_000, **kwargs)


def test_a_small_trace_is_untouched():
    events = [_event(f"/a{i}.png") for i in range(5)]
    payload, truncated = WebTestingEnv._serialize_network(events)
    assert not truncated
    assert len(json.loads(payload)) == 5


def test_an_oversized_trace_stays_valid_json():
    """The whole point: parseable output, so consumers lose the tail and not the lot."""
    events = [_bulky(f"/a{i}.png") for i in range(20)]
    payload, truncated = WebTestingEnv._serialize_network(events)
    assert truncated
    parsed = json.loads(payload)          # must not raise
    assert 0 < len(parsed) < 20
    assert len(payload) <= _NETWORK_TEXT_MAX


def test_errors_survive_the_budget_ahead_of_successful_subresources():
    """A dropped image is noise; a dropped 500 is the finding."""
    events = [_bulky(f"/asset{i}.png") for i in range(20)]
    events.append(_bulky("/api/save", status=500, resource_type="xhr"))
    payload, truncated = WebTestingEnv._serialize_network(events)

    assert truncated
    urls = [row["url"] for row in json.loads(payload)]
    assert "/api/save" in urls


def test_document_requests_survive_the_budget():
    """A failing document request is the strongest deterministic signal there is."""
    events = [_bulky(f"/asset{i}.png") for i in range(20)]
    events.append(_bulky("/pricing.html", status=404, resource_type="document"))
    payload, _ = WebTestingEnv._serialize_network(events)
    assert "/pricing.html" in [row["url"] for row in json.loads(payload)]


def test_a_failed_request_survives_even_with_a_2xx_status():
    events = [_bulky(f"/asset{i}.png") for i in range(20)]
    dropped = _bulky("/api/poll", resource_type="xhr")
    dropped.failed, dropped.failure_text = True, "net::ERR_CONNECTION_REFUSED"
    events.append(dropped)
    payload, _ = WebTestingEnv._serialize_network(events)
    assert "/api/poll" in [row["url"] for row in json.loads(payload)]


def test_one_enormous_event_does_not_shut_out_everything_behind_it():
    """Skipping an oversized row beats ending the loop on it."""
    giant = _event("/huge.json", status=500, resource_type="xhr", body="x" * (_NETWORK_TEXT_MAX * 2))
    events = [giant] + [_event(f"/small{i}.png") for i in range(5)]
    payload, truncated = WebTestingEnv._serialize_network(events)

    assert truncated
    urls = [row["url"] for row in json.loads(payload)]
    assert "/huge.json" not in urls
    assert len(urls) == 5


def test_arrival_order_is_preserved_within_each_priority_group():
    events = [_bulky(f"/a{i}.png") for i in range(30)]
    payload, _ = WebTestingEnv._serialize_network(events)
    urls = [row["url"] for row in json.loads(payload)]
    assert urls == sorted(urls, key=lambda u: int(u[2:-4]))


def test_an_empty_trace_serializes_cleanly():
    payload, truncated = WebTestingEnv._serialize_network([])
    assert json.loads(payload) == [] and not truncated


def test_page_info_flags_whether_the_trace_was_budgeted():
    """Consumers cannot otherwise tell a quiet page from a trimmed one."""
    from web_testing_agent.envs.types import RawObservation
    import numpy as np

    def observation(events):
        return RawObservation(
            screenshot=np.zeros((2, 2, 3), dtype=np.uint8), html="<html></html>",
            network_events=events, url="http://h/x", console_errors=[], page_errors=[],
        )

    assert WebTestingEnv._page_info(observation([_event("/a.png")]))["network_truncated"] is False
    assert WebTestingEnv._page_info(observation([_bulky(f"/a{i}.png") for i in range(20)]))[
        "network_truncated"
    ] is True


def test_the_recorder_reads_a_budgeted_trace_without_loss(tmp_path):
    """End to end: what the env emits under budget is what the corpus records."""
    from web_testing_agent.annotation.trace import _summarize_network

    events = [_bulky(f"/a{i}.png") for i in range(20)]
    events.append(_bulky("/api/save", status=500, resource_type="xhr"))
    payload, _ = WebTestingEnv._serialize_network(events)

    summarized = _summarize_network(payload)
    assert summarized, "a budgeted trace must not degrade to nothing"
    assert any(row["url"] == "/api/save" and row["status"] == 500 for row in summarized)
