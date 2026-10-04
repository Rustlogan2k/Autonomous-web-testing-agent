"""The benchmark harness: replay serialization, the clean-build control, the fault monitor,
the patch builder, the widened action registry, survival statistics and the LLM snapshot.

None of these tests needs a browser, a GPU stack or a language model. Each pins one
behaviour the transfer experiment's numbers depend on.
"""

from __future__ import annotations

import json

import pytest

from web_testing_agent.benchmark.builder import Patch, build
from web_testing_agent.benchmark.monitor import FaultMonitor
from web_testing_agent.envs.action_registry import (
    EXTENDED_OPTIONS,
    LEGACY_OPTIONS,
    build_action_specs,
)
from web_testing_agent.envs.types import ActionSpec, ActionType
from web_testing_agent.evaluation.scripted import ScriptedPolicy
from web_testing_agent.evaluation.survival import (
    Run,
    kaplan_meier,
    paired_fold_difference,
    restricted_mean,
    stratified_logrank,
)
from web_testing_agent.oracle import (
    AssertionKind,
    AssertionSpec,
    BenchmarkApp,
    FaultClass,
    FaultSpec,
    VerificationPipeline,
    build_trajectory,
    entered_value,
    replay_step_of,
)
from web_testing_agent.reward.progress import JudgmentCache, Judgment, page_digest

# -- replay serialization -----------------------------------------------------------------


def _click(index: int, label: str, selector: str | None = None) -> ActionSpec:
    return ActionSpec(index=index, action_type=ActionType.CLICK, element_id=label,
                      selector=selector or f"a >> nth={index}", params={"href": "x"})


def test_a_repeated_label_replays_to_the_control_that_was_used():
    specs = [_click(9, "Edit"), _click(10, "Edit"), _click(11, "Edit")]
    step = replay_step_of(specs[2], specs)
    assert step == {"type": "CLICK", "text": "Edit", "nth": 2}
    assert ScriptedPolicy._match(step, specs) == 11


def test_history_and_reload_actions_are_replayable():
    """A persistence fault is defined by a reload; dropping it from the trace loses the fault."""
    for action_type in (ActionType.REFRESH, ActionType.BROWSER_BACK, ActionType.BROWSER_FORWARD):
        spec = ActionSpec(index=7, action_type=action_type, description=action_type.value)
        step = replay_step_of(spec, [spec])
        assert step == {"type": action_type.value}
        assert ScriptedPolicy._match(step, [spec]) == 7
    assert replay_step_of(ActionSpec(index=0, action_type=ActionType.NO_OP)) is None


def test_a_refused_fill_enters_nothing():
    spec = ActionSpec(index=3, action_type=ActionType.TYPE, element_id="quantity",
                      params={"value": "not-a-number", "category": "type_mismatch"})
    assert entered_value(spec, {"success": False}) == {}
    assert entered_value(spec, {"success": True}) == {"quantity": "not-a-number"}


def test_an_explicit_empty_entry_blocks_the_category_fallback():
    records = [{"url": "u", "html": "", "action": None},
               {"url": "u", "html": "", "entered": {},
                "action": {"type": "TYPE", "id": "qty", "params": {"category": "valid_typical"}}}]
    assert build_trajectory(records).steps[-1].inputs == {}


# -- the clean-build control ---------------------------------------------------------------

ECHO = FaultSpec(
    fault_id="F", fault_class=FaultClass.STATE_CORRUPTION, description="", trigger_condition="",
    expected_behaviour="", observable_violation="",
    assertions=(AssertionSpec(kind=AssertionKind.ECHOES_INPUT, where="receipt",
                              region="qty", input_field="quantity"),))
APP = BenchmarkApp(app_id="a", name="a", regions={"qty": "#qty"}, faults=(ECHO,))


class World:
    """A replayable world: the receipt shows `shown` whatever quantity was typed."""

    def __init__(self, shown: str) -> None:
        self.shown = shown
        self.calls = 0

    def run(self, actions):  # noqa: ANN001
        self.calls += 1
        records = [{"url": "http://a/", "html": "", "action": None}]
        for action in actions:
            entered = ({"quantity": "42"} if action.get("type") == "TYPE" else {})
            records.append({"url": "http://a/step", "html": "", "action": action,
                            "entered": entered})
        if any(a.get("text") == "Place" for a in actions):
            records.append({"url": "http://a/receipt", "action": {"type": "CLICK", "text": "Place"},
                            "html": f'<td id="qty">{self.shown}</td>', "entered": {}})
        return build_trajectory(records)


TRACE = [{"type": "CLICK", "text": "Filler"}, {"type": "TYPE", "id": "q"},
         {"type": "CLICK", "text": "Place"}]


def test_a_violation_the_clean_build_shares_is_rejected_as_an_artefact():
    faulty, clean = World("1"), World("1")  # both builds show 1: the trace, not the fault
    pipeline = VerificationPipeline(APP, faulty, control_runner=clean)
    finding = pipeline.confirm(ECHO, faulty.run(TRACE), budget_at_discovery=3)
    assert not finding.confirmed
    assert finding.rejection_kind == "fires_on_clean_build"


def test_a_real_difference_between_builds_is_confirmed_and_minimized_soundly():
    faulty, clean = World("1"), World("42")
    pipeline = VerificationPipeline(APP, faulty, control_runner=clean)
    finding = pipeline.confirm(ECHO, faulty.run(TRACE), budget_at_discovery=3)
    assert finding.confirmed, finding.rejected_because
    assert finding.control["checked"] and not finding.control["violated_on_control"]
    assert finding.control["against"] == "clean build"
    assert [a.get("text") or a.get("id") for a in finding.minimized_trace] == ["q", "Place"]


# -- the monitor -----------------------------------------------------------------------------


def _info(url: str, html: str = "", spec: ActionSpec | None = None) -> dict:
    return {"page": {"url": url, "html": html}, "action_spec": spec,
            "exec_info": {"success": True}}


def test_the_monitor_stamps_reached_discovered_and_verified_separately():
    faulty, clean = World("1"), World("42")
    monitor = FaultMonitor(VerificationPipeline(APP, faulty, control_runner=clean))
    monitor.start_episode(_info("http://a/"))
    typed = ActionSpec(index=1, action_type=ActionType.TYPE, element_id="quantity",
                       selector='[id="q"]', params={"value": "42", "category": "valid_typical"})
    place = _click(2, "Place")
    assert monitor.observe(_info("http://a/step", spec=typed), typed, [typed], 1) == []
    confirmed = monitor.observe(_info("http://a/receipt", '<td id="qty">1</td>', place),
                                place, [place], 2)
    assert [f.fault_id for f in confirmed] == ["F"]
    summary = monitor.summary()
    assert (summary["reached"], summary["discovered"], summary["verified"]) == (1, 1, 1)
    assert summary["first_verified_at"] == 2


def test_the_monitor_does_not_reverify_unchanged_evidence_within_an_episode():
    faulty, clean = World("1"), World("1")
    monitor = FaultMonitor(VerificationPipeline(APP, faulty, control_runner=clean))
    monitor.start_episode(_info("http://a/"))
    typed = ActionSpec(index=1, action_type=ActionType.TYPE, element_id="quantity",
                       selector='[id="q"]', params={"value": "42", "category": "valid_typical"})
    monitor.observe(_info("http://a/step", spec=typed), typed, [typed], 1)
    receipt = '<td id="qty">1</td>'
    scroll = ActionSpec(index=1, action_type=ActionType.SCROLL, params={"direction": "down"})
    monitor.observe(_info("http://a/receipt", receipt, _click(2, "Place")), _click(2, "Place"), [], 2)
    calls = clean.calls
    monitor.observe(_info("http://a/receipt", receipt, scroll), scroll, [scroll], 3)
    assert clean.calls == calls  # same page, same evidence: no second control replay
    assert monitor.records["F"].rejections["fires_on_clean_build"] == 1


# -- the patch builder -------------------------------------------------------------------------


def test_the_builder_derives_the_faulty_build_and_detects_drift(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "app.js").write_text("var total = rate * nights;\n", encoding="utf-8")
    (clean / "logo.png").write_bytes(b"\x89PNG....")
    patch = Patch("X-01", "app.js", "rate * nights", "rate * (nights - 1)", "why")
    assert build(tmp_path, (patch,)) == 0
    assert "(nights - 1)" in (tmp_path / "buggy" / "app.js").read_text(encoding="utf-8")
    assert build(tmp_path, (patch,), check_only=True) == 0
    (tmp_path / "buggy" / "app.js").write_text("edited by hand", encoding="utf-8")
    assert build(tmp_path, (patch,), check_only=True) == 1


def test_an_ambiguous_patch_anchor_fails_the_build(tmp_path):
    (tmp_path / "clean").mkdir()
    (tmp_path / "clean" / "a.js").write_text("x; x;", encoding="utf-8")
    assert build(tmp_path, (Patch("X", "a.js", "x;", "y;", "why"),)) == 3


# -- the widened action registry ---------------------------------------------------------------


def _element(**kw) -> dict:
    base = {"index": 0, "tag": "input", "type": "", "id": None, "name": None, "href": None,
            "resolvedHref": None, "target": None, "text": "", "disabled": False,
            "visible": True, "inViewport": True, "options": None, "label": ""}
    base.update(kw)
    return base


def test_the_legacy_registry_is_unchanged_and_the_extended_one_widens_selects():
    select = _element(tag="select", name="nights", options=["1", "2", "3", "4", "5"])
    legacy = [s for s in build_action_specs([select]) if s.action_type is ActionType.SELECT]
    extended = [s for s in build_action_specs([select], options=EXTENDED_OPTIONS)
                if s.action_type is ActionType.SELECT]
    assert [s.params["option"] for s in legacy] == ["5"]
    assert [s.params["option"] for s in extended] == ["5", "1", "2", "3"]
    assert build_action_specs([select], options=LEGACY_OPTIONS) == build_action_specs([select])


def test_radios_sharing_a_name_get_distinct_selectors_and_labels_are_captured():
    radios = [_element(index=i, type="radio", name="plan", label=f"Plan {i}") for i in (0, 1)]
    specs = [s for s in build_action_specs(radios, options=EXTENDED_OPTIONS)
             if s.action_type is ActionType.CLICK]
    assert len({s.selector for s in specs}) == 2
    assert [s.params["label_text"] for s in specs] == ["Plan 0", "Plan 1"]


# -- survival statistics -------------------------------------------------------------------------


def test_kaplan_meier_keeps_censored_runs_at_risk():
    curve = kaplan_meier([10, 20, 2000, 2000], [False, False, True, True])
    assert curve == [(0.0, 1.0), (10.0, 0.75), (20.0, 0.5)]
    assert restricted_mean([10, 20, 2000, 2000], 2000) == pytest.approx(1007.5)


def test_the_paired_statistic_averages_seeds_within_folds_before_comparing():
    runs = [Run("f1", "A", s, t, False) for s, t in enumerate([100, 300])] + \
           [Run("f1", "B", s, t, False) for s, t in enumerate([500, 700])] + \
           [Run("f2", "A", 0, 2000, True), Run("f2", "B", 0, 1000, False)]
    comparison = paired_fold_difference(runs, "A", "B", 2000, resamples=200)
    assert comparison.per_fold == {"f1": -400.0, "f2": 1000.0}
    assert comparison.folds_favouring_treatment == 1


def test_the_stratified_logrank_detects_a_consistently_faster_arm():
    runs = []
    for fold in ("f1", "f2", "f3"):
        for seed in range(4):
            runs.append(Run(fold, "fast", seed, 100 + seed, False))
            runs.append(Run(fold, "slow", seed, 2000, True))
    result = stratified_logrank(runs, "fast", "slow")
    assert result["p_value"] < 0.001
    assert result["observed_minus_expected_a"] > 0


def test_the_top_up_rule_is_decided_on_complete_initial_seeds_only():
    from web_testing_agent.experiments.transfer import top_up_folds

    def pair(fold, pretrained, scratch, seeds=(0, 1, 2)):
        return ([Run(fold, "dqn_pretrained", s, t, t >= 2000) for s, t in zip(seeds, pretrained)]
                + [Run(fold, "dqn_scratch", s, t, t >= 2000) for s, t in zip(seeds, scratch)])

    runs = (pair("close", [900, 1000, 1100], [1000, 1100, 1150])      # |delta| = 83 -> top up
            + pair("apart", [300, 400, 500], [1500, 2000, 2000])     # |delta| = 1433 -> no
            + pair("censored", [2000] * 3, [2000] * 3)               # both censored -> top up
            + pair("missing", [900, 1000], [950, 1000], seeds=(0, 1))  # undecided
            # seeds 3-5 already run must not move the decision for "apart"
            + pair("apart", [2000] * 3, [300] * 3, seeds=(3, 4, 5)))
    decided = top_up_folds(runs, 2000)
    assert decided["dqn_pretrained - dqn_scratch"] == ["censored", "close"]
    assert decided["bandit_pretrained - bandit_scratch"] == []


# -- the LLM progress snapshot and its cache ------------------------------------------------------


def test_the_snapshot_drops_the_port_and_prefers_rendered_text():
    html = ('<html><title>Basket</title><body><h1>Your basket</h1>'
            '<div class="hidden">Your basket is empty.</div><p>Ridgeline tent x 2</p></body></html>')
    one = page_digest("http://127.0.0.1:5001/basket.html", html, "Your basket\nRidgeline tent x 2")
    two = page_digest("http://127.0.0.1:6002/basket.html", html, "Your basket\nRidgeline tent x 2")
    assert one == two
    assert "empty" not in one.split("TEXT:", 1)[1].split("\n", 1)[0]
    assert "empty" in page_digest("http://h/basket.html", html)  # markup alone cannot tell


def test_the_snapshot_names_the_current_step_of_a_multi_step_flow():
    rail = ('<ol><li><button>1. Session</button></li>'
            '<li><button aria-current="step"><span>2.</span> Catering</button></li>'
            '<li><button disabled>3. Details</button></li></ol><h1>Catering</h1>')
    assert "STEP: 2. Catering (marked as the current step)" in page_digest("http://h/i.html", rail)
    # A navigation bar's current page is not a step of a task.
    nav = '<nav><a aria-current="page" href="/">Rooms</a></nav><h1>Rooms</h1>'
    assert "STEP:" not in page_digest("http://h/i.html", nav)


def test_typed_values_are_summarised_not_copied():
    html = f'<input name="note" value="{"A" * 2048}"><input name="qty" value="42">'
    digest = page_digest("http://h/p", html)
    assert "long text (2048 chars)" in digest and '"42"' in digest


def test_the_judgment_cache_round_trips_and_compacts(tmp_path):
    cache = JudgmentCache(tmp_path)
    cache.put(Judgment(key="k1", progress=8, stage="ready_to_commit", reason="r", digest="d"))
    fresh = JudgmentCache(tmp_path)
    assert fresh.get("k1").value == pytest.approx(0.8)
    assert fresh.get("k1").source == "cache"
    target = fresh.compact()
    assert target.name == "progress.jsonl"
    assert [json.loads(line)["key"] for line in target.read_text().splitlines()] == ["k1"]
    assert not list(tmp_path.glob("progress.shard.*.jsonl"))


def test_a_miss_reads_what_other_processes_appended_including_a_row_finished_later(tmp_path):
    reader = JudgmentCache(tmp_path)
    assert reader.get("k1", refresh=True) is None
    other = tmp_path / "progress.shard.4242.jsonl"
    row = json.dumps(Judgment(key="k1", progress=6, digest="d").to_row())
    half = json.dumps(Judgment(key="k2", progress=8, digest="e").to_row())
    other.write_text(row + "\n" + half[:20], encoding="utf-8")  # k2 mid-write
    assert reader.get("k1") is None  # no refresh: memory only
    assert reader.get("k1", refresh=True).progress == 6
    assert reader.get("k2", refresh=True) is None
    with other.open("a", encoding="utf-8") as handle:
        handle.write(half[20:] + "\n")
    assert reader.get("k2", refresh=True).progress == 8


def test_compaction_keeps_a_shard_written_after_the_merge_read_it(tmp_path, monkeypatch):
    JudgmentCache(tmp_path).put(Judgment(key="k1", progress=2, stage="browsing", digest="d"))
    late = tmp_path / "progress.shard.99999.jsonl"
    compactor = JudgmentCache(tmp_path)
    original = compactor.reload

    def reload_then_another_process_writes():
        original()
        late.write_text(json.dumps(Judgment(key="k2", progress=4, digest="e").to_row()) + "\n")

    monkeypatch.setattr(compactor, "reload", reload_then_another_process_writes)
    compactor.compact()
    assert late.is_file()  # its row was not merged, so it was not deleted
    assert JudgmentCache(tmp_path).get("k2") is not None


def test_a_per_fault_control_rejects_a_violation_caused_by_another_faults_patch():
    """Both builds show the wrong value: the faulty one, and the faulty one *without this
    fault's patch*. The violation does not depend on this fault, so it is not this fault."""
    faulty, without_this_patch, clean = World("1"), World("1"), World("42")
    pipeline = VerificationPipeline(APP, faulty, control_runner=clean,
                                    control_runners={"F": without_this_patch})
    finding = pipeline.confirm(ECHO, faulty.run(TRACE), budget_at_discovery=3)
    assert not finding.confirmed
    assert finding.rejection_kind == "fires_on_clean_build"
    assert finding.control["against"] == "faulty build without this fault's patch"


def test_a_control_replay_that_crashes_rejects_rather_than_passes():
    class Broken:
        def run(self, actions):  # noqa: ANN001, ARG002
            raise RuntimeError("browser died")

    pipeline = VerificationPipeline(APP, World("1"), control_runner=Broken())
    finding = pipeline.confirm(ECHO, World("1").run(TRACE), budget_at_discovery=3)
    assert not finding.confirmed and finding.rejection_kind == "control_error"


def test_a_worker_without_git_records_the_commit_the_launcher_passed(tmp_path, monkeypatch):
    from web_testing_agent.experiments import transfer

    monkeypatch.setattr(transfer, "REPO_ROOT", tmp_path)  # a code archive: no .git
    monkeypatch.setenv("WTA_GIT_COMMIT", "0123abcd")
    assert transfer.git_state() == {"commit": "0123abcd", "dirty": False, "source": "launcher"}
    monkeypatch.delenv("WTA_GIT_COMMIT")
    assert transfer.git_state()["commit"] == ""
