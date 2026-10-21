"""Unit tests of the harness itself: patches, diff audit, the extension oracle."""

from __future__ import annotations

import json

import pytest

from web_testing_agent.oracle.faults import BenchmarkApp
from web_testing_agent.oracle.trajectory import build_trajectory

from e2e_suite.harness import ext_oracle, variants
from e2e_suite.harness.manifest import AppManifest, Patch


def _app(tmp_path, files: dict[str, str], patches: list[Patch]) -> AppManifest:
    for name, text in files.items():
        path = tmp_path / "app" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return AppManifest(root=tmp_path / "app", data={"app_id": "app"}, patches=patches)


def test_a_patch_must_match_exactly_once(tmp_path):
    root = tmp_path / "r"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    with pytest.raises(variants.PatchError, match="2 times"):
        variants.apply_patches(root, [Patch("F1", "src/a.py", "x = 1", "x = 2")])
    with pytest.raises(variants.PatchError, match="0 times"):
        variants.apply_patches(root, [Patch("F1", "src/a.py", "y = 1", "y = 2")])


def test_the_diff_audit_accepts_exactly_the_patches_and_nothing_else(tmp_path):
    app = _app(tmp_path, {"src/a.py": "total = a + b\n", "src/b.py": "ok = True\n"},
               [Patch("F1", "src/a.py", "a + b", "a - b")])
    clean, buggy = tmp_path / "clean", tmp_path / "buggy"
    for dest in (clean, buggy):
        variants._copy_runtime(app, dest)  # noqa: SLF001
    variants.apply_patches(buggy, app.patches)
    assert variants.audit_diff(app, clean, buggy) == []
    (buggy / "src" / "b.py").write_text("ok = False\n", encoding="utf-8")  # an undeclared change
    assert any("no patch targets it" in p for p in variants.audit_diff(app, clean, buggy))


def _trajectory(steps):
    return build_trajectory([{"url": u, "html": h, "action": a, "entered": e} for u, h, a, e in steps])


APP = BenchmarkApp.from_dict({"app_id": "x", "regions": {}, "faults": []})


def test_region_stable_across_reload_fires_only_when_the_region_changes():
    fault = {"fault_id": "F", "assertions": [{"kind": "region_stable_across_reload",
                                              "region": "r", "where": ".*"}]}
    regions = {"r": "#r"}
    changed = _trajectory([("u", '<p id="r">3 done</p>', None, None),
                           ("u", '<p id="r">1 done</p>', {"type": "REFRESH"}, None)])
    same = _trajectory([("u", '<p id="r">3 done</p>', None, None),
                        ("u", '<p id="r">3  done</p>', {"type": "REFRESH"}, None)])
    no_reload = _trajectory([("u", '<p id="r">3 done</p>', None, None)])
    assert ext_oracle.verify(fault, regions, APP, changed).violated
    assert not ext_oracle.verify(fault, regions, APP, same).violated
    verdict = ext_oracle.verify(fault, regions, APP, no_reload)
    assert not verdict.violated and verdict.inconclusive


def test_input_absent_fires_when_a_typed_value_leaks_into_the_region():
    fault = {"fault_id": "F", "assertions": [{"kind": "input_absent", "region": "share",
                                              "input_field": "stop_name", "where": "share"}]}
    regions = {"share": "#share"}
    typed = {"type": "TYPE", "id": "stop", "params": {"value": "Test User Input"}}
    leaked = _trajectory([("http://h/edit", "", typed, {"stop_name": "Test User Input"}),
                          ("http://h/share", '<ul id="share">Test User Input</ul>', None, None)])
    clean = _trajectory([("http://h/edit", "", typed, {"stop_name": "Test User Input"}),
                         ("http://h/share", '<ul id="share">Harbour walk</ul>', None, None)])
    assert ext_oracle.verify(fault, regions, APP, leaked).violated
    assert not ext_oracle.verify(fault, regions, APP, clean).violated


def test_extension_assertions_are_conjunctive_with_primary_kinds():
    fault = {"fault_id": "F", "assertions": [
        {"kind": "input_absent", "region": "share", "input_field": "n", "where": ".*"},
        {"kind": "text_contains", "region": "share", "expected": "sealed", "where": ".*"}]}
    app = BenchmarkApp.from_dict({"app_id": "x", "regions": {"share": "#share"}, "faults": []})
    view = _trajectory([("u", '<ul id="share">Test User Input</ul>', None, {"n": "Test User Input"})])
    # input leaked (violated) but the text_contains is violated too ("sealed" missing) -> both
    assert ext_oracle.verify(fault, {"share": "#share"}, app, view).violated
    view2 = _trajectory([("u", '<ul id="share">Test User Input sealed</ul>', None, {"n": "Test User Input"})])
    assert not ext_oracle.verify(fault, {"share": "#share"}, app, view2).violated


def test_region_arithmetic_compares_numbers_on_the_same_page_under_a_guard():
    fault = {"fault_id": "F", "assertions": [{
        "kind": "region_arithmetic", "where": "bag", "expression": "discount == round(subtotal * 0.1, 2)",
        "operands": {"discount": "d", "subtotal": "s"}, "guard": {"region": "v", "contains": "10%"}}]}
    regions = {"d": "#d", "s": "#s", "v": "#v"}
    page = '<span id="v">{v}</span><span id="s">£1,008.00</span><span id="d">−£{d}</span>'
    stale = _trajectory([("http://h/bag", page.format(v="Welcome 10% off", d="2.40"), None, None)])
    fresh = _trajectory([("http://h/bag", page.format(v="Welcome 10% off", d="100.80"), None, None)])
    other = _trajectory([("http://h/bag", page.format(v="Free delivery", d="2.40"), None, None)])
    assert ext_oracle.verify(fault, regions, APP, stale).violated
    assert not ext_oracle.verify(fault, regions, APP, fresh).violated
    verdict = ext_oracle.verify(fault, regions, APP, other)
    assert not verdict.violated and not verdict.inconclusive


def test_region_arithmetic_accepts_a_boolean_rule():
    fault = {"fault_id": "F", "assertions": [{
        "kind": "region_arithmetic", "where": ".*", "expression": "total <= 50",
        "operands": {"total": "t"}, "guard": {"region": "s", "contains": "Approved"}}]}
    regions = {"t": "#t", "s": "#s"}
    over = _trajectory([("u", '<b id="s">Approved</b><b id="t">£78.12</b>', None, None)])
    under = _trajectory([("u", '<b id="s">Approved</b><b id="t">£42.00</b>', None, None)])
    pending = _trajectory([("u", '<b id="s">Awaiting approval</b><b id="t">£78.12</b>', None, None)])
    assert ext_oracle.verify(fault, regions, APP, over).violated
    assert not ext_oracle.verify(fault, regions, APP, under).violated
    assert not ext_oracle.verify(fault, regions, APP, pending).violated


# -- plan §9: crash-type faults (`console_or_request_error`) ---------------------------------------

CRASH = {"fault_id": "C", "oracle": "extension", "crash": "uncaught-exception",
         "assertions": [{"kind": "console_or_request_error", "where": "/tickets/\\d+",
                         "pattern": "TypeError", "sources": ["page", "console"]}]}


def _events(page=(), console=(), request=()):
    return {"page": list(page), "console": list(console), "request": list(request)}


def _event_view(steps):
    view = _trajectory([(url, "", None, None) for url, _events_ in steps])
    return ext_oracle.with_events(view, [events for _url, events in steps])


def test_crash_kind_fires_on_a_matching_event_on_a_matching_step_only():
    fired = _event_view([("http://h/queue", _events()),
                         ("http://h/tickets/7", _events(page=["TypeError: x is undefined"]))])
    elsewhere = _event_view([("http://h/queue", _events(page=["TypeError: x is undefined"])),
                             ("http://h/tickets/7", _events())])
    other_text = _event_view([("http://h/tickets/7", _events(page=["RangeError: too deep"]))])
    other_source = _event_view([("http://h/tickets/7", _events(request=["HTTP 500 GET /TypeError"]))])
    assert ext_oracle.verify(CRASH, {}, APP, fired).violated
    for quiet in (elsewhere, other_text, other_source):
        verdict = ext_oracle.verify(CRASH, {}, APP, quiet)
        assert not verdict.violated and not verdict.inconclusive


def test_crash_kind_is_inconclusive_without_a_matching_step_or_without_events():
    nowhere = _event_view([("http://h/queue", _events(page=["TypeError"]))])
    assert ext_oracle.verify(CRASH, {}, APP, nowhere).inconclusive
    plain = _trajectory([("http://h/tickets/7", "", None, None)])  # a primary view: no events
    verdict = ext_oracle.verify(CRASH, {}, APP, plain)
    assert verdict.inconclusive and not verdict.violated


def test_page_events_keeps_same_origin_errors_and_drops_harness_and_foreign_requests():
    network = json.dumps([
        {"url": "http://h:1/tickets/new", "method": "POST", "response_status": 500},
        {"url": "http://h:1/static/app.js", "method": "GET", "response_status": 200},
        {"url": "http://h:1/__wta/state", "method": "GET", "response_status": 404},
        {"url": "http://cdn.example/x.js", "method": "GET", "response_status": 404},
        {"url": "http://h:1/gone", "method": "GET", "response_status": None, "failed": True}])
    info = {"page": {"console_errors": ["boom"], "page_errors": ["TypeError: y"], "network": network}}
    assert ext_oracle.page_events(info, "http://h:1") == _events(
        page=["TypeError: y"], console=["boom"], request=["HTTP 500 POST http://h:1/tickets/new"])
    assert ext_oracle.page_events({}, "http://h:1") == _events()
    assert ext_oracle.page_events({"page": {"network": "not json"}}, "")["request"] == []


class _FakeEnv:
    """Stands in for the tester's env: each observation's `info["page"]` carries one response."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.closed = False
        self.extra = "delegated"

    def _info(self, status):
        row = {"url": "http://h:1/p", "method": "GET", "response_status": status}
        return {"page": {"url": "http://h:1/p", "html": "", "network": json.dumps([row])},
                "action_specs": []}

    def reset(self):
        return None, self._info(self.statuses.pop(0))

    def step(self, _index):
        return None, 0.0, False, False, self._info(self.statuses.pop(0))

    def close(self):
        self.closed = True


def test_the_recording_env_passes_results_through_and_records_one_entry_per_observation():
    fake = _FakeEnv([200, 503, 200])
    env = ext_oracle._RecordingEnv(fake, "http://h:1")  # noqa: SLF001
    _observation, info = env.reset()
    assert info["page"]["url"] == "http://h:1/p"
    env.step(0)
    env.step(1)
    env.close()  # the fake has no browser page, so nothing late is folded in
    assert fake.closed and env.extra == "delegated"
    assert [e["request"] for e in env.events] == [[], ["HTTP 503 GET http://h:1/p"], []]


def test_event_views_align_events_with_steps():
    view = _trajectory([("u1", "", None, None), ("u2", "", None, None)])
    padded = ext_oracle.with_events(view, [_events(page=["e"])])
    assert padded.steps is view.steps and padded.events == [_events(page=["e"]), _events()]
    records = [{"url": "u1", "html": "", "action": None, "events": _events(console=["c"])},
               {"url": "u2", "html": "", "action": None}]
    assert [e["console"] for e in ext_oracle.view_of_records(records).events] == [["c"], []]


def test_manifest_validation_of_crash_faults():
    from e2e_suite.harness.manifest import crash_problems

    assert crash_problems(CRASH) == []
    spec = CRASH["assertions"][0]
    everything = {**CRASH, "assertions": [{**spec, "pattern": ".*"}]}
    assert any("matches the empty string" in p for p in crash_problems(everything))
    no_where = {**CRASH, "assertions": [{"kind": "console_or_request_error", "pattern": "x"}]}
    assert any("where must be" in p for p in crash_problems(no_where))
    assert any("must read one of ['request']" in p
               for p in crash_problems({**CRASH, "crash": "server-error"}))
    assert any("crash must be one of" in p for p in crash_problems({**CRASH, "crash": "segfault"}))
    assert any("needs oracle extension" in p for p in crash_problems({**CRASH, "oracle": "existing"}))
    assert any("unknown console_or_request_error fields" in p
               for p in crash_problems({**CRASH, "assertions": [{**spec, "region": "r"}]}))
    stray = {"fault_id": "S", "crash": "server-error", "assertions": [{"kind": "text_contains"}]}
    assert crash_problems(stray) == [
        "S: declares crash 'server-error' but has no console_or_request_error assertion"]


def test_two_crash_faults_in_one_app_must_differ_in_class(tmp_path):
    from e2e_suite.harness.manifest import validate_manifest

    def app(classes):
        faults = [{"fault_id": f"C{i}", "oracle": "extension", "crash": crash,
                   "fault_class": "broken_transition", "subtype": "state-transition",
                   "patch": {"file": "a", "find": "b", "replace": "c"},
                   "trigger_trace": [{"type": "CLICK", "id": "x"}],
                   "assertions": [{"kind": "console_or_request_error", "where": "/t",
                                   "pattern": "Error|HTTP 500"}]}
                  for i, crash in enumerate(classes)]
        return AppManifest(root=tmp_path, data={"app_id": "a", "faults": faults}, patches=[])

    assert any("different crash classes" in p
               for p in validate_manifest(app(["server-error", "server-error"])))
    assert not any("crash" in p and "directory" not in p
                   for p in validate_manifest(app(["server-error", "uncaught-exception"])))


def test_the_clean_walk_fails_on_any_browser_error_whatever_its_text():
    from e2e_suite.harness.browser_checks import walk_passed

    assert walk_passed({}, [], [])
    assert not walk_passed({}, ["console.error: anything at all"], [])
    assert not walk_passed({}, ["HTTP 404 http://h/favicon.ico"], [])
    assert not walk_passed({"F": 1}, [], [])
    assert not walk_passed({}, [], ["http://cdn.example/x.js"])
