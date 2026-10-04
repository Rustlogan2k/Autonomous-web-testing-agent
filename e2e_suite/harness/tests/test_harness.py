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
