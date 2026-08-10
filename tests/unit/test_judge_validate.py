"""The validator asserts that a window does not contradict its own record.

Every judge defect in this project has had one shape: the window said something the
record did not support, the judge reasoned correctly from it, and the verdict looked
like a model failure. Each was found *after* a scoring run by reading verdicts — the
wrong feedback loop, costing a corpus of model calls and an hour of manual reading to
find something decidable offline in milliseconds.

These tests pin the invariants themselves. Two of them describe defects the validator
found in its own first run, which is the point: a rule that is too blunt is not a
safety net, it is noise that trains you to ignore it.
"""

import hashlib
from pathlib import Path

from web_testing_agent.annotation.trace import StepRecord
from web_testing_agent.judge.validate import (
    MAX_LINE_CHARS,
    validate_corpus,
    validate_step,
    validate_window,
)
from web_testing_agent.judge.window import build_windows


class _Corpus:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "pages").mkdir(parents=True, exist_ok=True)

    def blob(self, html: str) -> str:
        payload = html.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        (self.root / "pages" / f"{digest}.html").write_bytes(payload)
        return digest

    def record(self, before_html: str, after_html: str, **overrides) -> StepRecord:
        payload = {
            "episode": 1, "step": 1, "global_step": 1,
            "action": {"type": "CLICK", "selector": '[id="b"]', "element": "Go",
                       "description": "click 'Go'", "params": {}},
            "before": {"url": "/a", "path": "/a", "html": self.blob(before_html),
                       "console_errors": [], "page_errors": [], "network": []},
            "after": {"url": "/a", "path": "/a", "html": self.blob(after_html),
                      "console_errors": [], "page_errors": [], "network": []},
            "exec": {"success": True, "error": None},
            "settled": True,
            "changed": {"url": False, "html": before_html != after_html, "screenshot": False},
        }
        payload.update(overrides)
        return StepRecord.from_dict(payload, root=self.root)


SAME = "<html><body><p>x</p></body></html>"


def _rules(issues) -> set[str]:
    return {issue.rule for issue in issues}


def test_a_truthful_window_has_no_issues(tmp_path):
    corpus = _Corpus(tmp_path)
    assert validate_step(corpus.record(SAME, SAME)) == []


def test_a_false_no_change_claim_is_caught(tmp_path):
    """The claim that most directly drives a verdict."""
    corpus = _Corpus(tmp_path)
    record = corpus.record(SAME, SAME)
    record.changed["html"] = True
    assert "false-no-change" in _rules(validate_step(record, rendered="  effect: NO OBSERVABLE CHANGE"))


def test_a_refusal_rendered_as_a_failure_is_caught(tmp_path):
    corpus = _Corpus(tmp_path)
    record = corpus.record(SAME, SAME, exec={
        "success": False, "error": "blocked: left the application",
        "left_application": "https://elsewhere.test/",
    })
    rules = _rules(validate_step(record, rendered="  FAILED     : did not navigate"))
    assert "block-as-failure" in rules and "unmarked-block" in rules


def test_a_correctly_marked_refusal_passes(tmp_path):
    corpus = _Corpus(tmp_path)
    record = corpus.record(SAME, SAME, exec={
        "success": False, "error": "blocked: left the application",
        "left_application": "https://elsewhere.test/",
    })
    assert validate_step(record) == []


def test_raw_tool_output_is_caught(tmp_path):
    corpus = _Corpus(tmp_path)
    rendered = "  FAILED     : Timeout\nCall log:\n  - waiting for locator"
    assert "raw-tool-output" in _rules(validate_step(corpus.record(SAME, SAME), rendered=rendered))


def test_control_characters_are_caught(tmp_path):
    corpus = _Corpus(tmp_path)
    assert "control-characters" in _rules(
        validate_step(corpus.record(SAME, SAME), rendered="  action: click\x08here")
    )


def test_an_unmarked_elision_is_caught_on_a_value_line(tmp_path):
    corpus = _Corpus(tmp_path)
    rendered = "  field value: q: '' -> '" + "A" * 80 + "'"
    assert "silent-elision" in _rules(validate_step(corpus.record(SAME, SAME), rendered=rendered))


def test_a_marked_elision_passes(tmp_path):
    corpus = _Corpus(tmp_path)
    rendered = "  field value: q: '' -> '" + "A" * 60 + "…[+340 chars]'"
    assert "silent-elision" not in _rules(validate_step(corpus.record(SAME, SAME), rendered=rendered))


def test_quoted_text_in_page_content_is_not_an_elision(tmp_path):
    """The validator's own first-run defect: it flagged text the renderer never touched.

    A CSP console message quotes a long directive; the renderer neither shortened it
    nor claims to have. Scoping the rule to value lines is what makes it actionable.
    """
    corpus = _Corpus(tmp_path)
    rendered = "  console    : Loading '" + "https://cdn.example/x" * 4 + "' violates policy"
    assert "silent-elision" not in _rules(validate_step(corpus.record(SAME, SAME), rendered=rendered))


def test_a_long_aggregated_line_is_not_an_overlong_item(tmp_path):
    """A page with many links makes a long line; that is the application, not a defect."""
    corpus = _Corpus(tmp_path)
    rendered = "  links still reachable: " + "; ".join(f"Item {i} -> /page{i}" for i in range(60))
    assert len(rendered) > MAX_LINE_CHARS
    assert "overlong-item" not in _rules(validate_step(corpus.record(SAME, SAME), rendered=rendered))


def test_one_enormous_item_is_caught(tmp_path):
    """A single blob that size is a truncation that failed."""
    corpus = _Corpus(tmp_path)
    rendered = "  console    : " + "x" * (MAX_LINE_CHARS + 50)
    assert "overlong-item" in _rules(validate_step(corpus.record(SAME, SAME), rendered=rendered))


def test_a_window_over_the_context_budget_is_caught(tmp_path):
    """llama.cpp truncates from the front, silently removing the system prompt."""
    corpus = _Corpus(tmp_path)
    windows = build_windows([corpus.record(SAME, SAME)])
    assert "over-context" in _rules(validate_window(windows[0], context_chars=10))
    assert "over-context" not in _rules(validate_window(windows[0], context_chars=100_000))


def test_corpus_validation_deduplicates_per_step_and_rule(tmp_path):
    """A step appears in up to K windows; reporting it K times buries everything else."""
    corpus = _Corpus(tmp_path)
    records = []
    for index in range(4):
        record = corpus.record(SAME, SAME)
        record.step, record.global_step = index + 1, index + 1
        record.changed["html"] = True
        records.append(record)
    issues = validate_corpus(build_windows(records))
    assert len(issues) == len({(i.global_step, i.rule) for i in issues})


def test_a_blocked_step_must_not_claim_no_observable_change(tmp_path):
    """It ends where it started because the harness put it back, not because the app idled.

    Measured on Gitea: external links labelled "Docker", "packaged" and "Powered by
    Gitea" drew confident dead_control and broken_navigation verdicts from this line.
    """
    corpus = _Corpus(tmp_path)
    record = corpus.record(SAME, SAME, exec={
        "success": False, "error": "blocked: left the application",
        "left_application": "https://github.com/x",
    })
    rendered = "  BLOCKED    : refused\n  effect     : NO OBSERVABLE CHANGE — byte-identical"
    assert "block-as-no-change" in _rules(validate_step(record, rendered=rendered))


def test_console_output_surviving_a_block_is_flagged(tmp_path):
    """An off-site page runs its own scripts before the restore completes.

    Its analytics and CSP violations then read as defects in the application under
    test — 5 confident js_error verdicts on Gitea came from someone else's website.
    """
    corpus = _Corpus(tmp_path)
    record = corpus.record(SAME, SAME, exec={
        "success": False, "error": "blocked: left the application",
        "left_application": "https://docs.gitea.com/",
    })
    record.after["console_errors"] = ["Minified React error #418"]
    assert "offsite-console" in _rules(validate_step(record))


def test_a_clean_blocked_step_passes(tmp_path):
    corpus = _Corpus(tmp_path)
    record = corpus.record(SAME, SAME, exec={
        "success": False, "error": "blocked: left the application",
        "left_application": "https://github.com/x",
    })
    assert validate_step(record) == []
