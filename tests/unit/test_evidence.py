"""M4: findings traceable to the artifacts that produced them.

The load-bearing claim is that **no new identifier was needed**. `run_rollout` increments
`report.steps` immediately after calling `recorder.on_step`, and `TraceRecorder.on_step`
increments `global_step` first, so the two carry the same value for the same transition —
which makes `Finding.first_seen_step` (copied into `ReportedBug.step`) already a key into
the trace. `test_the_finding_step_indexes_the_trace_record` drives the **real**
`run_rollout` and the **real** `TraceRecorder` against a fake environment and checks that,
rather than trusting the reasoning.

Everything else follows from it: the index is a reduction of a trace that already exists,
so there is no second artifact store to keep in sync.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from web_testing_agent.annotation import TraceRecorder, build_evidence_index, evidence_for_record
from web_testing_agent.envs.types import ActionSpec, ActionType, BugSignals
from web_testing_agent.evaluation.rollout import run_rollout
from web_testing_agent.reporting import build_report, render_markdown

PAGE = "<html><body><h1>Broken</h1></body></html>"
TRIGGER_STEP = 3  # the step (1-based, global) at which the fake env fires a console error


class FakePolicy:
    def reset(self) -> None: ...

    def act(self, observation, info) -> int:
        return 0


class FakeEnv:
    """The smallest thing `run_rollout` and `TraceRecorder` will both accept.

    Fires a console error on one known step so a finding exists with a known step number,
    which is what the traceability assertion needs.
    """

    base_url = "http://127.0.0.1:8080/"

    def __init__(self, steps_per_episode: int = 5):
        self.steps_per_episode = steps_per_episode
        self._n = 0
        self._global = 0

    def _obs(self):
        return {"screenshot": np.zeros((4, 4, 3), dtype=np.uint8)}

    def _info(self, triggered: bool):
        spec = ActionSpec(index=0, action_type=ActionType.CLICK, selector='[id="x"]',
                          element_id="Pricing", params={}, description="click 'Pricing'")
        signals = BugSignals(console_errors=["TypeError: boom"] if triggered else [])
        return {
            "action_spec": spec, "action_specs": [spec], "num_valid_actions": 1,
            "exec_success": True, "network_settled": True, "opened_new_page": False,
            "load_duration_s": 0.1, "bug_signals": signals, "state_key": "s",
            "page": {"url": f"{self.base_url}pricing.html", "html": PAGE,
                     "console_errors": ["TypeError: boom"] if triggered else [],
                     "page_errors": [], "network": json.dumps(
                         [{"url": f"{self.base_url}pricing.html", "method": "GET",
                           "response_status": 404, "resource_type": "document",
                           "failed": False}] if triggered else [])},
        }

    def reset(self, seed=None):
        self._n = 0
        return self._obs(), self._info(False)

    def step(self, action):
        self._n += 1
        self._global += 1
        info = self._info(self._global == TRIGGER_STEP)
        done = self._n >= self.steps_per_episode
        return self._obs(), 0.0, False, done, info


def _run(tmp_path: Path, *, screenshots: bool = True, episodes: int = 1):
    recorder = TraceRecorder(tmp_path, "evidence-test", save_screenshots=screenshots)
    report = run_rollout(FakeEnv(), FakePolicy(), episodes=episodes, label="t",
                         seed=0, recorder=recorder)
    recorder.close()
    return recorder, report


# -- the alignment the whole design rests on ------------------------------------------


def test_the_finding_step_indexes_the_trace_record(tmp_path: Path):
    """`Finding.first_seen_step` and `TraceRecorder.global_step` must name the same step.

    Verified against the real rollout loop and the real recorder, because this is an
    invariant across two modules that neither one states.
    """
    recorder, report = _run(tmp_path)
    assert report.findings, "the fake env should have produced a finding"
    finding = report.findings[0]
    assert finding.first_seen_step == TRIGGER_STEP

    index = build_evidence_index(recorder.root, [finding.first_seen_step])
    entry = index["by_step"][str(finding.first_seen_step)]
    assert entry["triggered"] is True, "the indexed step is the one that fired the trigger"
    assert entry["console_errors"] == ["TypeError: boom"]


def test_the_recorder_and_the_rollout_agree_on_every_step(tmp_path: Path):
    recorder, report = _run(tmp_path, episodes=2)
    assert recorder.global_step == report.steps


# -- artifact capture and content addressing ------------------------------------------


def test_page_bodies_and_frames_are_written_content_addressed(tmp_path: Path):
    recorder, _ = _run(tmp_path)
    pages = list((recorder.root / "pages").glob("*.html"))
    shots = list((recorder.root / "screenshots").glob("*.png"))
    assert pages and shots
    # The filename is the digest of the contents, so identical pages are stored once.
    assert len(pages) == 1, "every step served the same body; it should be stored once"
    assert pages[0].read_text(encoding="utf-8") == PAGE


def test_a_reference_resolves_to_the_stored_artifact(tmp_path: Path):
    """The point of a digest: it has to actually find the bytes."""
    recorder, report = _run(tmp_path)
    entry = build_evidence_index(recorder.root, [report.findings[0].first_seen_step])
    digest = entry["by_step"][str(TRIGGER_STEP)]["after"]["html"]
    assert (recorder.root / "pages" / f"{digest}.html").read_text(encoding="utf-8") == PAGE


def test_screenshots_can_be_turned_off(tmp_path: Path):
    recorder, report = _run(tmp_path, screenshots=False)
    index = build_evidence_index(recorder.root, [report.findings[0].first_seen_step])
    assert "screenshot" not in index["by_step"][str(TRIGGER_STEP)]["after"]
    assert not (recorder.root / "screenshots").exists()


def test_the_index_holds_references_not_contents(tmp_path: Path):
    """A screenshot pasted into a report is a document nobody can diff."""
    recorder, report = _run(tmp_path)
    index = build_evidence_index(recorder.root, [report.findings[0].first_seen_step])
    assert PAGE not in json.dumps(index)


def test_only_the_cited_steps_are_indexed(tmp_path: Path):
    recorder, _ = _run(tmp_path)
    assert build_evidence_index(recorder.root, [TRIGGER_STEP])["indexed_steps"] == 1
    assert build_evidence_index(recorder.root, None)["indexed_steps"] == 5


def test_network_failures_are_carried_but_successes_are_not(tmp_path: Path):
    recorder, _ = _run(tmp_path)
    entry = build_evidence_index(recorder.root, [TRIGGER_STEP])["by_step"][str(TRIGGER_STEP)]
    assert entry["network_errors"][0]["status"] == 404
    other = build_evidence_index(recorder.root, [1])["by_step"]["1"]
    assert "network_errors" not in other, "a successful fetch is not evidence of anything"


# -- failure handling -----------------------------------------------------------------


def test_the_trace_directory_is_recorded_relative_to_a_base(tmp_path: Path):
    """So a report can be moved alongside its evidence without the reference breaking."""
    recorder, _ = _run(tmp_path / "reports" / "run_evidence")
    index = build_evidence_index(recorder.root, [TRIGGER_STEP], base=tmp_path)
    assert index["trace_dir"] == "reports/run_evidence/evidence-test"


def test_an_unrelatable_base_falls_back_to_an_absolute_path(tmp_path: Path):
    """A wrong relative path is worse than a long absolute one."""
    recorder, _ = _run(tmp_path)
    index = build_evidence_index(recorder.root, [TRIGGER_STEP], base=tmp_path / "elsewhere" / "x")
    assert Path(index["trace_dir"]).is_absolute()


def test_a_missing_trace_yields_an_unavailable_index(tmp_path: Path):
    index = build_evidence_index(tmp_path / "nothing-here", [1])
    assert index["available"] is False
    assert "no trace.jsonl" in index["error"]


def test_a_cited_step_with_no_record_is_reported(tmp_path: Path):
    """The trace and the report disagreeing is worth seeing, not silently dropping."""
    recorder, _ = _run(tmp_path)
    index = build_evidence_index(recorder.root, [TRIGGER_STEP, 999])
    assert index["missing_steps"] == [999]


def test_a_malformed_trace_line_is_skipped_rather_than_fatal(tmp_path: Path):
    recorder, _ = _run(tmp_path)
    with (recorder.root / "trace.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    assert build_evidence_index(recorder.root, None)["available"] is True


def test_evidence_for_record_tolerates_an_empty_record():
    entry = evidence_for_record({})
    assert entry["url"] == "" and entry["before"] == {"url": "", "html": ""}


# -- the report -----------------------------------------------------------------------


def _report_with_evidence(index: dict, step: int = TRIGGER_STEP):
    return build_report(
        target="http://127.0.0.1:8080/",
        findings=[{"trigger": "console_error", "url": "http://127.0.0.1:8080/pricing.html",
                   "element": "Pricing", "action": "CLICK", "detail": "TypeError: boom",
                   "first_seen_step": step, "times_seen": 1}],
        run_meta={"repository": "/r", "evidence": index},
    )


def test_a_finding_renders_its_recorded_artifacts(tmp_path: Path):
    recorder, report = _run(tmp_path)
    index = build_evidence_index(recorder.root, [TRIGGER_STEP])
    markdown = render_markdown(_report_with_evidence(index))
    assert "**Recorded evidence**" in markdown
    assert "pages/" in markdown and "screenshots/" in markdown
    assert "console: TypeError: boom" in markdown


def test_an_uncaught_page_error_is_rendered_as_evidence():
    """Found on the demo fixture, where the seeded defect is a `pageerror`, not a
    `console.error`. Playwright reports the two on different channels and
    `detect_bug_signals` merges them, so rendering only `console_errors` dropped every
    uncaught exception from the evidence of a finding caused by one."""
    index = {"available": True, "trace_dir": "t", "indexed_steps": 1, "by_step": {
        "12": {"episode": 1, "step_in_episode": 12, "after": {"html": "a" * 64},
               "before": {}, "page_errors": ["Cannot read properties of undefined"]}}}
    markdown = render_markdown(_report_with_evidence(index, step=12))
    assert "- uncaught error: Cannot read properties of undefined" in markdown


def test_the_evidence_summary_names_the_trace_directory(tmp_path: Path):
    recorder, _ = _run(tmp_path)
    index = build_evidence_index(recorder.root, [TRIGGER_STEP])
    markdown = render_markdown(_report_with_evidence(index))
    assert "trace directory" in markdown
    assert "steps referenced: 1" in markdown


def test_a_finding_without_a_matching_record_renders_without_artifacts(tmp_path: Path):
    recorder, _ = _run(tmp_path)
    index = build_evidence_index(recorder.root, [TRIGGER_STEP])
    markdown = render_markdown(_report_with_evidence(index, step=999))
    assert "**Recorded evidence**" not in markdown
    assert "JavaScript error logged to the console" in markdown, "the finding still renders"


def test_the_full_index_survives_json_serialization(tmp_path: Path):
    recorder, _ = _run(tmp_path)
    index = build_evidence_index(recorder.root, [TRIGGER_STEP])
    report = _report_with_evidence(index)
    restored = json.loads(json.dumps(report.to_dict(), default=str))
    assert restored["run"]["evidence"]["by_step"][str(TRIGGER_STEP)]["triggered"] is True


def test_digests_are_shown_abbreviated_in_markdown_and_full_in_json(tmp_path: Path):
    recorder, _ = _run(tmp_path)
    index = build_evidence_index(recorder.root, [TRIGGER_STEP])
    digest = index["by_step"][str(TRIGGER_STEP)]["after"]["html"]
    report = _report_with_evidence(index)
    assert digest not in render_markdown(report), "the full 64-char digest would be noise"
    assert digest[:12] in render_markdown(report)
    assert digest in json.dumps(report.to_dict())


# -- backwards compatibility ----------------------------------------------------------


def test_a_report_with_no_evidence_renders_as_before():
    report = build_report(
        target="http://x/",
        findings=[{"trigger": "console_error", "url": "u", "element": "e",
                   "first_seen_step": 2}],
        run_meta={"repository": "/r", "seed": 0},
    )
    markdown = render_markdown(report)
    assert "**Recorded evidence**" not in markdown
    assert "**Evidence**\n" not in markdown
    assert '"repository": "/r"' in markdown, "ordinary run_meta is still dumped verbatim"


def test_a_report_with_no_run_meta_still_renders():
    markdown = render_markdown(build_report(target="http://x/", findings=[]))
    assert "## Run" not in markdown
    assert "No findings." in markdown


def test_an_unavailable_evidence_index_is_stated_not_hidden():
    report = _report_with_evidence({"available": False, "error": "trace vanished"})
    markdown = render_markdown(report)
    assert "Evidence** — unavailable (trace vanished)" in markdown


# -- the profile summary (M4 goal 6) --------------------------------------------------


def test_the_profile_is_summarized_in_markdown_not_dumped():
    """A serialized profile runs to hundreds of lines; the JSON report keeps it in full."""
    profile = {
        "available": True, "provenance": "detector", "deployable": True,
        "primary_language": "HTML",
        "languages": [{"language": "HTML", "files": 4, "bytes": 100, "share": 0.6}],
        "build_systems": [{"value": "pip", "evidence": "requirements.txt", "confidence": 0.7}],
        "frameworks": [{"value": "Flask", "evidence": "requirements.txt", "confidence": 1.0}],
        "stats": {"files_scanned": 6}, "notes": ["a note"],
        "entry_points": [], "test_targets": [], "docs": [], "dependency_manifests": [],
        "install_commands": [], "build_commands": [], "test_commands": [],
        "build_definition": {"value": "dockerfile", "evidence": "Dockerfile", "confidence": 1.0},
    }
    report = build_report(target="http://x/", findings=[],
                          run_meta={"repository": "/r", "repository_profile": profile})
    markdown = render_markdown(report)
    assert "- languages: HTML 60%" in markdown
    assert "- build systems: pip" in markdown
    assert "- frameworks: Flask" in markdown
    assert "- deployable by this pipeline: yes" in markdown
    assert "- note: a note" in markdown
    # The giveaway that it was dumped rather than summarized.
    assert '"provenance"' not in markdown
    assert "dependency_manifests" not in markdown
    # ...but the JSON report still carries every field.
    assert report.to_dict()["run"]["repository_profile"] == profile


def test_an_unavailable_profile_is_stated_in_markdown():
    report = build_report(target="http://x/", findings=[], run_meta={
        "repository_profile": {"available": False, "error": "NotADirectoryError: x"}})
    assert "profile unavailable (NotADirectoryError: x)" in render_markdown(report)


@pytest.mark.parametrize("deployable,expected", [(True, "yes"), (False, "no")])
def test_the_summary_says_whether_the_pipeline_could_deploy_it(deployable, expected):
    report = build_report(target="http://x/", findings=[], run_meta={
        "repository_profile": {"available": True, "deployable": deployable,
                               "languages": [], "build_systems": [], "stats": {}}})
    assert f"- deployable by this pipeline: {expected}" in render_markdown(report)
