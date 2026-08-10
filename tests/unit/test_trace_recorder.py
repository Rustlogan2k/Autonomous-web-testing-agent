import json

import numpy as np
import pytest

from web_testing_agent.annotation import TraceRecorder, load_trace, trace_dir_summary
from web_testing_agent.envs.types import ActionSpec, ActionType, BugSignals, NetworkEvent


def _page(url: str, html: str, console: list[str] | None = None, network: str = "[]") -> dict:
    return {
        "url": url,
        "html": html,
        "network": network,
        "console_errors": console or [],
        "page_errors": [],
    }


def _obs(fill: int = 0) -> dict:
    return {"screenshot": np.full((8, 8, 3), fill, dtype=np.uint8)}


def _info(page: dict, spec: ActionSpec, **overrides) -> dict:
    info = {
        "step": 1,
        "action_spec": spec,
        "exec_info": {"success": True, "error": None},
        "network_settled": True,
        "opened_new_page": False,
        "load_duration_s": 0.25,
        "num_valid_actions": 20,
        "page": page,
        "episode_context": np.zeros(6, dtype=np.float32),
        "bug_signals": BugSignals(),
        "exec_success": True,
    }
    info.update(overrides)
    return info


CLICK = ActionSpec(
    index=12,
    action_type=ActionType.CLICK,
    selector='[id="btn-export"]',
    element_id="Export data",
    params={"navigational": False, "in_viewport": True},
    description="click 'Export data'",
)


def _record_one(tmp_path, *, before, after, spec=CLICK, **info_overrides):
    recorder = TraceRecorder(tmp_path, "run")
    recorder.on_reset(_obs(0), _info(before, spec))
    recorder.on_step(spec.index, _obs(1), -0.05, False, False, _info(after, spec, **info_overrides))
    recorder.close()
    return list(load_trace(recorder.root))


# --- transition chaining --------------------------------------------------------


def test_before_side_comes_from_the_previous_observation(tmp_path):
    """The env's pre_obs *is* the previous post_obs, so chaining info["page"] is exact."""
    records = _record_one(
        tmp_path,
        before=_page("http://h:1/a.html", "<html>A</html>"),
        after=_page("http://h:1/b.html", "<html>B</html>"),
    )
    assert len(records) == 1
    assert records[0].before["path"] == "/a.html"
    assert records[0].after["path"] == "/b.html"
    assert records[0].resolve_html("before") == "<html>A</html>"
    assert records[0].resolve_html("after") == "<html>B</html>"


def test_a_multi_step_episode_chains_each_after_into_the_next_before(tmp_path):
    recorder = TraceRecorder(tmp_path, "run")
    recorder.on_reset(_obs(0), _info(_page("http://h:1/a.html", "A"), CLICK))
    for index, (url, html) in enumerate([("http://h:1/b.html", "B"), ("http://h:1/c.html", "C")]):
        recorder.on_step(1, _obs(index + 1), 0.0, False, False, _info(_page(url, html), CLICK))
    recorder.close()

    records = list(load_trace(recorder.root))
    assert [r.before["path"] for r in records] == ["/a.html", "/b.html"]
    assert [r.after["path"] for r in records] == ["/b.html", "/c.html"]
    assert [r.step for r in records] == [1, 2]


def test_on_step_before_on_reset_records_nothing(tmp_path):
    """There is no `before` side yet; inventing one would fabricate a transition."""
    recorder = TraceRecorder(tmp_path, "run")
    recorder.on_step(1, _obs(), 0.0, False, False, _info(_page("http://h:1/a", "A"), CLICK))
    recorder.close()
    assert list(load_trace(recorder.root)) == []


def test_episodes_are_numbered_and_do_not_chain_across_a_reset(tmp_path):
    recorder = TraceRecorder(tmp_path, "run")
    recorder.on_reset(_obs(0), _info(_page("http://h:1/a.html", "A"), CLICK))
    recorder.on_step(1, _obs(1), 0.0, False, True, _info(_page("http://h:1/b.html", "B"), CLICK))
    recorder.on_reset(_obs(0), _info(_page("http://h:1/a.html", "A"), CLICK))
    recorder.on_step(1, _obs(1), 0.0, False, True, _info(_page("http://h:1/z.html", "Z"), CLICK))
    recorder.close()

    records = list(load_trace(recorder.root))
    assert [(r.episode, r.step, r.global_step) for r in records] == [(1, 1, 1), (2, 1, 2)]
    # The second episode's `before` is its own reset page, not the first episode's end.
    assert records[1].before["path"] == "/a.html"


# --- URL normalization ----------------------------------------------------------


def test_paths_are_origin_independent(tmp_path):
    """The toy site gets an ephemeral port, so raw URLs differ between capture runs."""
    a = _record_one(tmp_path / "one", before=_page("http://127.0.0.1:64142/x.html", "A"),
                    after=_page("http://127.0.0.1:64142/y.html?q=1", "B"))
    b = _record_one(tmp_path / "two", before=_page("http://127.0.0.1:51001/x.html", "A"),
                    after=_page("http://127.0.0.1:51001/y.html?q=1", "B"))
    assert a[0].after["path"] == b[0].after["path"] == "/y.html?q=1"


# --- content addressing ---------------------------------------------------------


def test_identical_pages_are_stored_once(tmp_path):
    recorder = TraceRecorder(tmp_path, "run")
    recorder.on_reset(_obs(0), _info(_page("http://h:1/a", "SAME"), CLICK))
    for _ in range(4):
        recorder.on_step(1, _obs(0), 0.0, False, False, _info(_page("http://h:1/a", "SAME"), CLICK))
    meta = recorder.close()
    assert meta["distinct_pages"] == 1
    assert len(list((recorder.root / "pages").glob("*.html"))) == 1


def test_screenshots_are_stored_only_when_enabled(tmp_path):
    off = _record_one(tmp_path / "off", before=_page("http://h:1/a", "A"), after=_page("http://h:1/b", "B"))
    assert off[0].after["screenshot"] is None
    assert not (tmp_path / "off" / "run" / "screenshots").exists()

    recorder = TraceRecorder(tmp_path / "on", "run", save_screenshots=True)
    recorder.on_reset(_obs(0), _info(_page("http://h:1/a", "A"), CLICK))
    recorder.on_step(1, _obs(200), 0.0, False, False, _info(_page("http://h:1/b", "B"), CLICK))
    recorder.close()
    records = list(load_trace(recorder.root))
    assert records[0].before["screenshot"] != records[0].after["screenshot"]
    assert len(list((recorder.root / "screenshots").glob("*.png"))) == 2


# --- signals, gating, robustness ------------------------------------------------


def test_deterministic_triggers_are_recorded_as_the_baseline(tmp_path):
    signals = BugSignals(
        console_errors=["TypeError: undefined"],
        unexpected_http_errors=[
            NetworkEvent(url="http://h:1/pricing.html", method="GET", request_headers={},
                         request_body=None, response_status=404, response_headers={},
                         response_body_snippet="", timestamp=0.0, duration_ms=5.0,
                         resource_type="document")
        ],
        document_http_error=True,
        broken_navigation=True,
        load_duration_s=0.4,
    )
    records = _record_one(tmp_path, before=_page("http://h:1/a", "A"),
                          after=_page("http://h:1/pricing.html", "404"), bug_signals=signals)
    recorded = records[0].bug_signals
    assert records[0].triggered
    assert recorded["console_errors"] == ["TypeError: undefined"]
    assert recorded["document_http_error"] is True
    assert recorded["unexpected_http_errors"][0]["url"] == "/pricing.html"


def test_a_step_that_changes_nothing_is_flagged(tmp_path):
    """The dead-control case (BUG-02): no change is the *signal*, not a reason to skip."""
    same = _page("http://h:1/widgets.html", "<html>unchanged</html>")
    records = _record_one(tmp_path, before=same, after=dict(same))
    assert records[0].changed == {"url": False, "html": False, "screenshot": False}
    assert not records[0].state_changed
    assert trace_dir_summary(tmp_path / "run")["steps_with_no_visible_change"] == 1


def test_a_truncated_network_trace_degrades_instead_of_failing(tmp_path):
    """The env caps the network JSON at a byte budget, which can cut it mid-object."""
    broken = '[{"url": "http://h:1/a", "method": "GET", "response_stat'
    records = _record_one(tmp_path, before=_page("http://h:1/a", "A"),
                          after=_page("http://h:1/a", "B", network=broken))
    assert records[0].after["network"] == []


def test_network_events_are_summarized_without_headers(tmp_path):
    events = json.dumps([{
        "url": "http://h:1/api/data?token=abc", "method": "POST",
        "request_headers": {"cookie": "secret"}, "response_headers": {"x": "y"},
        "response_status": 500, "resource_type": "xhr", "failed": False,
        "duration_ms": 12.5, "response_body_snippet": "boom",
    }])
    records = _record_one(tmp_path, before=_page("http://h:1/a", "A"),
                          after=_page("http://h:1/a", "B", network=events))
    event = records[0].after["network"][0]
    assert event == {
        "url": "/api/data?token=abc", "method": "POST", "status": 500,
        "resource_type": "xhr", "failed": False, "failure_text": None,
        "duration_ms": 12.5, "body_snippet": "boom",
    }


def test_action_side_records_what_the_policy_chose_and_whether_it_was_valid(tmp_path):
    records = _record_one(tmp_path, before=_page("http://h:1/a", "A"),
                          after=_page("http://h:1/b", "B"), num_valid_actions=5)
    action = records[0].action
    # chosen_index 12 against 5 valid slots: the policy picked an empty slot.
    assert action["chosen_index"] == 12 and action["was_valid"] is False
    assert action["type"] == "CLICK"
    assert action["selector"] == '[id="btn-export"]'
    assert action["element"] == "Export data"


def test_every_record_is_json_serializable(tmp_path):
    """Guards the ndarray/dataclass fields in `info` that must never reach the file."""
    records = _record_one(tmp_path, before=_page("http://h:1/a", "A"), after=_page("http://h:1/b", "B"))
    for line in (tmp_path / "run" / "trace.jsonl").read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        assert "episode_context" not in payload["reward_breakdown"]
        assert "action_specs" not in payload["reward_breakdown"]
    assert records[0].reward == pytest.approx(-0.05)


def test_meta_records_run_shape(tmp_path):
    recorder = TraceRecorder(tmp_path, "run", meta={"corpus": "scripted"})
    recorder.on_reset(_obs(0), _info(_page("http://h:1/a", "A"), CLICK))
    recorder.on_step(1, _obs(1), 0.0, False, True, _info(_page("http://h:1/b", "B"), CLICK))
    meta = recorder.close({"extra": 1})

    on_disk = json.loads((recorder.root / "meta.json").read_text(encoding="utf-8"))
    assert on_disk == meta
    assert meta["corpus"] == "scripted" and meta["extra"] == 1
    assert meta["steps"] == 1 and meta["episodes"] == 1


def test_load_trace_requires_a_trace_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        list(load_trace(tmp_path / "nope"))
