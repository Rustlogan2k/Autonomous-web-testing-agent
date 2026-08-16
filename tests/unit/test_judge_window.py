import hashlib
from pathlib import Path

from web_testing_agent.annotation.trace import StepRecord
from web_testing_agent.judge import WINDOW_STEPS, build_windows, page_view
from web_testing_agent.judge.window import render_step


class _Corpus:
    """Writes page blobs the way TraceRecorder does, so StepRecord.resolve_html works."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "pages").mkdir(parents=True, exist_ok=True)

    def blob(self, html: str) -> str:
        payload = html.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        (self.root / "pages" / f"{digest}.html").write_bytes(payload)
        return digest

    def record(self, step: int, before_html: str, after_html: str, *, episode: int = 1,
               action: dict | None = None, **overrides) -> StepRecord:
        payload = {
            "episode": episode,
            "step": step,
            "global_step": overrides.pop("global_step", step),
            "action": action or {"type": "CLICK", "selector": '[id="b"]', "element": "Go",
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


FORM = (
    '<html><head><title>Sign up</title></head><body><form>'
    '<input type="number" id="age" name="age" min="13" max="120"{age}>'
    '<input type="checkbox" id="dark" name="darkmode"{dark}>'
    '</form><p>{text}</p></body></html>'
)


# --- page view -------------------------------------------------------------------


def test_page_view_reports_declared_constraints():
    """A validation bypass is 'accepted what it said it would reject' — both halves needed."""
    view = page_view("/signup", FORM.format(age="", dark="", text="hi"))
    assert "min=13" in view.constraints["age"] and "max=120" in view.constraints["age"]


def test_a_constraint_is_never_reported_unless_it_was_declared():
    """A false declaration is worse than a missing one.

    The judge's validation verdict is "the app accepted what it declared it would
    reject", so an invented `min`/`max` supplies half the evidence for a bug that does
    not exist. Three real sources, all measured on captured pages: `min` is a prefix of
    `minlength`, a utility class like `max-w-full` contains the bare word `max`, and
    `data-type` looks like `type` to a word-boundary match.
    """
    view = page_view(
        "/p",
        '<html><body><form>'
        '<input name="bio" class="max-w-full min-h-0" minlength="3" maxlength="10" data-type="custom">'
        '</form></body></html>',
    )
    # Exactly the two attributes the markup declares — no bare `min`, no bare `max`,
    # and no `type` borrowed from `data-type`.
    assert view.constraints["bio"] == "minlength=3 maxlength=10"


def test_valueless_and_unquoted_constraints_are_read_correctly():
    """`required` has no value; `maxlength=10` has one without quotes. Both are legal HTML."""
    view = page_view("/p", '<html><body><input name="u" required maxlength=10></body></html>')
    assert view.constraints["u"] == "maxlength=10 required"


def test_data_prefixed_attributes_do_not_impersonate_the_real_ones():
    """`data-name`/`data-value` are not the control's name and value."""
    view = page_view(
        "/p",
        '<html><body><input data-name="decoy" name="real" data-value="nope" value="yes"></body></html>',
    )
    assert view.values == {"real": "yes"}


def test_page_view_ignores_script_and_style_text():
    html = "<html><body><script>var secret = 1;</script><style>p{color:red}</style><p>Real</p></body></html>"
    assert page_view("/p", html).text == ["Real"]


def test_page_view_lists_hidden_elements():
    html = '<html><body><a id="cta" data-hidden="true">Buy</a><a id="ok">Other</a></body></html>'
    view = page_view("/p", html)
    assert view.hidden == ["a#cta"]


# --- step rendering --------------------------------------------------------------


def test_a_step_with_no_effect_says_so_explicitly(tmp_path):
    """BUG-02's entire signal is an absence; an LLM will not reliably notice one."""
    corpus = _Corpus(tmp_path)
    same = FORM.format(age="", dark="", text="unchanged")
    rendered = render_step(corpus.record(1, same, same), 1)
    assert "NO OBSERVABLE CHANGE" in rendered


def test_a_real_change_does_not_claim_no_change(tmp_path):
    corpus = _Corpus(tmp_path)
    rendered = render_step(
        corpus.record(1, FORM.format(age="", dark="", text="before"),
                      FORM.format(age="", dark="", text="after")), 1)
    assert "NO OBSERVABLE CHANGE" not in rendered
    assert "after" in rendered


def test_form_state_transitions_are_rendered(tmp_path):
    corpus = _Corpus(tmp_path)
    rendered = render_step(
        corpus.record(1, FORM.format(age="", dark="", text="x"),
                      FORM.format(age="", dark=" checked", text="x")), 1)
    assert "darkmode: off -> on" in rendered


def test_constraints_are_shown_for_touched_fields_only(tmp_path):
    corpus = _Corpus(tmp_path)
    rendered = render_step(
        corpus.record(1, FORM.format(age="", dark="", text="x"),
                      FORM.format(age=' value="999999999"', dark="", text="x")), 1)
    assert "age declares [type=number min=13 max=120]" in rendered
    assert "darkmode declares" not in rendered


def test_visibility_changes_are_rendered(tmp_path):
    corpus = _Corpus(tmp_path)
    rendered = render_step(
        corpus.record(1, '<html><body><a id="cta">Buy</a></body></html>',
                      '<html><body><a id="cta" data-hidden="true">Buy</a></body></html>'), 1)
    assert "now hidden : a#cta" in rendered


def test_a_failed_action_is_flagged(tmp_path):
    """So the judge does not blame the application for an explorer problem."""
    corpus = _Corpus(tmp_path)
    same = FORM.format(age="", dark="", text="x")
    record = corpus.record(1, same, same, exec={"success": False, "error": "element detached"})
    assert "FAILED" in render_step(record, 1)


def test_console_errors_and_http_failures_are_rendered(tmp_path):
    corpus = _Corpus(tmp_path)
    same = FORM.format(age="", dark="", text="x")
    record = corpus.record(1, same, same)
    record.after["console_errors"] = ["TypeError: undefined is not a function"]
    record.after["network"] = [{"url": "/pricing.html", "status": 404, "failed": False}]
    rendered = render_step(record, 1)
    assert "TypeError" in rendered and "404 /pricing.html" in rendered


# --- window construction ---------------------------------------------------------


def _chain(corpus: _Corpus, count: int, episode: int = 1, start: int = 1) -> list[StepRecord]:
    return [
        corpus.record(i, f"<html><body>{i}</body></html>", f"<html><body>{i + 1}</body></html>",
                      episode=episode, global_step=start + i - 1)
        for i in range(1, count + 1)
    ]


def test_one_window_per_step(tmp_path):
    corpus = _Corpus(tmp_path)
    windows = build_windows(_chain(corpus, 4))
    assert len(windows) == 4
    assert [w.focus.step for w in windows] == [1, 2, 3, 4]


def test_a_window_never_exceeds_the_configured_depth(tmp_path):
    corpus = _Corpus(tmp_path)
    windows = build_windows(_chain(corpus, 10))
    assert max(len(w.records) for w in windows) == WINDOW_STEPS
    assert len(windows[-1].records) == WINDOW_STEPS


def test_early_windows_are_short_rather_than_padded(tmp_path):
    corpus = _Corpus(tmp_path)
    windows = build_windows(_chain(corpus, 3))
    assert [len(w.records) for w in windows] == [1, 2, 3]


def test_windows_never_span_an_episode_boundary(tmp_path):
    """A fresh context shares no state, so cross-episode context invites invented causality."""
    corpus = _Corpus(tmp_path)
    records = _chain(corpus, 3, episode=1, start=1) + _chain(corpus, 3, episode=2, start=4)
    windows = build_windows(records)
    for window in windows:
        assert len({r.episode for r in window.records}) == 1
    assert [len(w.records) for w in windows if w.episode == 2] == [1, 2, 3]


def test_idle_no_ops_are_skipped(tmp_path):
    corpus = _Corpus(tmp_path)
    same = "<html><body>same</body></html>"
    idle = corpus.record(1, same, same, action={"type": "NO_OP", "description": "no-op", "params": {}})
    windows = build_windows([idle])
    assert windows == []


def test_a_deliberate_action_that_changes_nothing_is_always_judged(tmp_path):
    """The dead-control case must never be filtered out as 'nothing happened'."""
    corpus = _Corpus(tmp_path)
    same = "<html><body>same</body></html>"
    click = corpus.record(1, same, same, action={"type": "CLICK", "selector": '[id="btn-export"]',
                                                 "element": "Export data", "params": {}})
    assert len(build_windows([click])) == 1


def test_a_no_op_that_reveals_a_change_is_still_judged(tmp_path):
    """A hang keeps mutating the page while the agent does nothing (BUG-07)."""
    corpus = _Corpus(tmp_path)
    record = corpus.record(1, "<html><body>a</body></html>", "<html><body>b</body></html>",
                           action={"type": "NO_OP", "description": "no-op", "params": {}},
                           settled=False)
    assert len(build_windows([record])) == 1


def test_render_names_the_step_under_judgment(tmp_path):
    corpus = _Corpus(tmp_path)
    windows = build_windows(_chain(corpus, 8))
    rendered = windows[-1].render()
    assert rendered.rstrip().endswith(f"Judge STEP {WINDOW_STEPS}.")
    assert "STARTING PAGE" in rendered


def test_non_ascii_page_content_survives_rendering(tmp_path):
    """Real apps are full of non-ASCII; the toy site alone has "← Home" and "Choose…".

    The Windows console defaults to cp1252 and raises on the first such character. A
    scoring run that crashes on print has already paid for the model calls it made.
    """
    corpus = _Corpus(tmp_path)
    rendered = render_step(
        corpus.record(1, "<html><body><p>← Home</p></body></html>",
                      "<html><body><p>Choose… — déjà vu 中文</p></body></html>"), 1)
    assert "Choose…" in rendered
    rendered.encode("utf-8")
    # The guard the scoring script installs on stdout: never raise, substitute instead.
    assert rendered.encode("cp1252", errors="replace").decode("cp1252")


def test_hidden_links_are_excluded_from_what_is_reachable(tmp_path):
    """Reporting a hidden link as reachable asserts the opposite of the truth."""
    html = ('<html><body><a id="nav" href="/signup" data-hidden="true">Sign up</a>'
            '<a id="w" href="/widgets">Widgets</a></body></html>')
    view = page_view("/p", html)
    assert view.visible_links == ["Widgets -> /widgets"]
    assert len(view.links) == 2  # the raw inventory still has both


def test_hiding_something_reports_what_survives(tmp_path):
    """BUG-08: hiding is only a defect relative to what is left.

    The judge saw both signup links disappear and reasoned, defensibly, that "a
    responsive design may hide or rearrange navigation elements". It could not tell
    that nothing replaced them, because a pure delta never says what remains.
    """
    corpus = _Corpus(tmp_path)
    before = ('<html><body><a id="nav" href="/signup">Sign up</a>'
              '<a id="w" href="/widgets">Widgets</a></body></html>')
    after = ('<html><body><a id="nav" href="/signup" data-hidden="true">Sign up</a>'
             '<a id="w" href="/widgets">Widgets</a></body></html>')
    rendered = render_step(corpus.record(1, before, after), 1)
    assert "now hidden : a#nav" in rendered
    assert "links still reachable: Widgets -> /widgets" in rendered
    assert "/signup" not in rendered.split("links still reachable")[1]


def test_hiding_the_last_route_reports_none(tmp_path):
    corpus = _Corpus(tmp_path)
    before = '<html><body><a id="nav" href="/signup">Sign up</a></body></html>'
    after = '<html><body><a id="nav" href="/signup" data-hidden="true">Sign up</a></body></html>'
    assert "links still reachable: (none)" in render_step(corpus.record(1, before, after), 1)


def test_surviving_links_are_only_listed_when_something_was_hidden(tmp_path):
    """Otherwise every step carries a link inventory and the signal is buried in noise."""
    corpus = _Corpus(tmp_path)
    html = '<html><body><a id="w" href="/widgets">Widgets</a></body></html>'
    assert "links still reachable" not in render_step(corpus.record(1, html, html), 1)


# --- prompt efficiency -----------------------------------------------------------


def _nav(corpus, before_text: str, after_text: str, before_url="/a", after_url="/b"):
    record = corpus.record(
        1,
        f"<html><body><p>{before_text}</p><form><input name='q' value='x'></form></body></html>",
        f"<html><body><p>{after_text}</p></body></html>",
    )
    record.before["path"], record.after["path"] = before_url, after_url
    return record


def test_a_navigation_does_not_list_the_old_pages_text_as_gone(tmp_path):
    """Leaving a page removes its text by definition, and it is already in the window.

    Measured: `text gone` was 24% of all window characters, the single largest item in
    the prompt, and almost all of that volume sat on navigation steps carrying nothing.
    """
    corpus = _Corpus(tmp_path)
    rendered = render_step(_nav(corpus, "old page copy", "new page copy"), 1)
    assert "text gone" not in rendered
    assert "old page copy" not in rendered


def test_a_navigation_labels_the_new_content_as_page_text(tmp_path):
    """Calling a page load "added" invites reading an ordinary navigation as a change."""
    corpus = _Corpus(tmp_path)
    rendered = render_step(_nav(corpus, "old", "new page copy"), 1)
    assert "page text" in rendered and "text added" not in rendered
    assert "new page copy" in rendered


def test_text_removed_without_navigating_is_still_reported(tmp_path):
    """A spinner or error clearing in place is a real change and must survive the cut."""
    corpus = _Corpus(tmp_path)
    rendered = render_step(
        corpus.record(1, "<html><body><p>Syncing…</p><p>Done</p></body></html>",
                      "<html><body><p>Done</p></body></html>"), 1)
    assert "text gone" in rendered and "Syncing" in rendered


def test_fields_vanishing_because_the_page_was_left_are_not_form_state_changes(tmp_path):
    corpus = _Corpus(tmp_path)
    rendered = render_step(_nav(corpus, "old", "new"), 1)
    assert "-> absent" not in rendered


def test_a_field_vanishing_without_navigating_is_still_reported(tmp_path):
    corpus = _Corpus(tmp_path)
    rendered = render_step(
        corpus.record(1, "<html><body><form><input name='q' value='x'></form></body></html>",
                      "<html><body></body></html>"), 1)
    assert "q: filled -> absent" in rendered


def test_text_diffs_are_capped_tighter_than_structural_lists(tmp_path):
    """The tail of a long diff is boilerplate; the head carries the change."""
    from web_testing_agent.judge.window import _DIFF_MAX, _LIST_MAX

    assert _DIFF_MAX < _LIST_MAX
    corpus = _Corpus(tmp_path)
    lines = "".join(f"<p>item {i}</p>" for i in range(40))
    rendered = render_step(
        corpus.record(1, "<html><body></body></html>", f"<html><body>{lines}</body></html>"), 1)
    assert rendered.count(" | ") <= _DIFF_MAX


# --- honesty of the no-change claim ----------------------------------------------


def test_a_value_replacement_is_rendered(tmp_path):
    """Occupancy cannot show one string replacing another, and explorers retype constantly."""
    corpus = _Corpus(tmp_path)
    before = '<html><body><form><input name="q" value="old"></form></body></html>'
    after = '<html><body><form><input name="q" value="new"></form></body></html>'
    rendered = render_step(corpus.record(1, before, after), 1)
    assert "field value: q: 'old' -> 'new'" in rendered
    assert "NO OBSERVABLE CHANGE" not in rendered


def test_byte_identity_is_only_claimed_when_the_html_really_is_identical(tmp_path):
    """The renderer must not assert byte-identity it has not checked.

    Measured: a TYPE into an already-filled field changed the HTML but rendered no
    diff, so this line fired and told the judge the page was byte-identical. It
    produced 21 confident dead-control verdicts on 39 TYPE windows of ordinary
    exploration — a rendering gap manufacturing the strongest signal in the prompt.
    """
    corpus = _Corpus(tmp_path)
    same = "<html><body><p>unchanged</p></body></html>"
    record = corpus.record(1, same, same)
    record.changed["html"] = True          # the hash says it changed...
    rendered = render_step(record, 1)      # ...even though this view sees no diff
    assert "NO OBSERVABLE CHANGE" not in rendered
    assert "the page DID change" in rendered


def test_a_genuinely_identical_page_still_reports_no_change(tmp_path):
    """BUG-02 depends on this line, so the fix must not suppress the real case."""
    corpus = _Corpus(tmp_path)
    same = "<html><body><p>unchanged</p></body></html>"
    record = corpus.record(1, same, same)
    assert record.changed["html"] is False
    assert "NO OBSERVABLE CHANGE" in render_step(record, 1)


def test_field_values_are_not_diffed_across_a_navigation(tmp_path):
    """Different pages have different forms; that is not a value change."""
    corpus = _Corpus(tmp_path)
    record = _nav(corpus, "old", "new")
    assert "field value" not in render_step(record, 1)


def test_the_detailed_prompt_scopes_dead_controls_to_clicks():
    """The rule existed only in the compact prompt, and the detailed one over-generalized."""
    from web_testing_agent.judge import SYSTEM_PROMPT

    flat = " ".join(SYSTEM_PROMPT.split())
    assert "CLICK or RAPID_CLICK on a control" in flat
    assert "It is NOT a finding after any other action" in flat
    assert "TYPE, SELECT, SCROLL" in flat


def test_no_source_file_contains_stray_control_characters():
    """A shell heredoc turned `\\b` into a literal backspace inside a regex.

    The pattern then matched a control character followed by `name=`, which occurs in
    no document, so the extractor silently returned nothing — and the byte is invisible
    in every normal view of the file, including a diff.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in list((root / "src").rglob("*.py")) + list((root / "scripts").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for char in ("\x07", "\x08", "\x0b", "\x0c", "\x1b"):
            if char in text:
                offenders.append(f"{path.name}: {char!r}")
    assert not offenders, f"stray control characters: {offenders}"


def test_a_shortened_value_says_that_it_was_shortened(tmp_path):
    """A silent elision is indistinguishable from the app truncating the input.

    Measured: an explorer typed a 400-character value, the renderer displayed 60, and
    the judge reported broken_flow — "the application silently truncates the user
    input, losing data without any error or warning" — about the renderer.
    """
    corpus = _Corpus(tmp_path)
    long_value = "A" * 400
    before = '<html><body><form><input name="q" value=""></form></body></html>'
    after = f'<html><body><form><input name="q" value="{long_value}"></form></body></html>'
    rendered = render_step(corpus.record(1, before, after), 1)
    assert "[+340 chars]" in rendered


def test_the_action_value_is_elided_the_same_way(tmp_path):
    """Two different lengths for the same string implies a truncation that never happened."""
    corpus = _Corpus(tmp_path)
    long_value = "A" * 400
    action = {"type": "TYPE", "selector": '[id="q"]', "element": "q",
              "description": "type boundary_max into 'q'", "params": {"value": long_value}}
    before = '<html><body><form><input name="q" value=""></form></body></html>'
    after = f'<html><body><form><input name="q" value="{long_value}"></form></body></html>'
    rendered = render_step(corpus.record(1, before, after, action=action), 1)
    assert rendered.count("[+340 chars]") == 2
    assert "A" * 100 not in rendered


def test_a_short_value_is_left_alone(tmp_path):
    corpus = _Corpus(tmp_path)
    before = '<html><body><form><input name="q" value=""></form></body></html>'
    after = '<html><body><form><input name="q" value="hello"></form></body></html>'
    rendered = render_step(corpus.record(1, before, after), 1)
    assert "field value: q: '' -> 'hello'" in rendered
    assert "chars]" not in rendered


def test_an_automation_call_log_is_not_pasted_into_the_window(tmp_path):
    """Playwright appends a multi-line call log quoting every matched element's tag.

    Measured on Gitea's Swagger page: one failure contributed over 11,000 characters,
    78% of the largest window in the corpus. The judge needs the reason, not the log.
    """
    corpus = _Corpus(tmp_path)
    same = "<html><body><p>x</p></body></html>"
    playwright_error = (
        "Timeout 5000ms exceeded.\nCall log:\n"
        + "\n".join(f'  - locator resolved to <button id="b{i}">…</button>' for i in range(40))
    )
    rendered = render_step(
        corpus.record(1, same, same, exec={"success": False, "error": playwright_error}), 1)
    assert "Timeout 5000ms exceeded." in rendered
    assert "locator resolved to" not in rendered
    assert len(rendered) < 600


def test_a_short_error_is_shown_in_full(tmp_path):
    corpus = _Corpus(tmp_path)
    same = "<html><body><p>x</p></body></html>"
    rendered = render_step(
        corpus.record(1, same, same, exec={"success": False, "error": "element is detached"}), 1)
    assert "FAILED     : element is detached" in rendered


def test_framework_generated_hidden_ids_are_collapsed():
    """Gitea puts 20 `_aria_auto_id_N` menu entries in every page's hidden list."""
    html = "<html><body>" + "".join(
        f'<a id="_aria_auto_id_{i}" href="/x" data-hidden="true">m</a>' for i in range(20)
    ) + '<a id="cta-signup" href="/s" data-hidden="true">Sign up</a></body></html>'
    view = page_view("/p", html)
    # Still one line for the twenty, which is the point — but labelled by what they say
    # rather than anonymised. Collapsing protects the budget; anonymising destroyed the
    # signal, and against seeded ground truth on Gitea that cost GITEA-03: Register and
    # Sign In disappeared at 375px and the window said only "a (unnamed) x41".
    assert "a 'm' x20" in view.hidden
    assert "a#cta-signup" in view.hidden, "a meaningful id must survive the collapse"
    assert len(view.hidden) == 2


def test_a_harness_refusal_is_not_rendered_as_a_failed_action(tmp_path):
    """The env refuses off-site navigation and restores the page.

    Rendered as "FAILED ... did not navigate", the judge reasonably concludes the link
    is broken. Measured on Gitea: 6 of 11 refusals became broken_navigation verdicts,
    the largest false-positive class in the run.
    """
    corpus = _Corpus(tmp_path)
    same = "<html><body><p>x</p></body></html>"
    record = corpus.record(1, same, same, exec={
        "success": False,
        "error": "blocked: navigation left the application (https://about.gitea.com/)",
        "left_application": "https://about.gitea.com/",
    })
    rendered = render_step(record, 1)
    assert "BLOCKED" in rendered
    assert "FAILED" not in rendered
    assert "NOT application behaviour" in rendered
    assert "https://about.gitea.com/" in rendered


def test_an_ordinary_failure_still_renders_as_failed(tmp_path):
    corpus = _Corpus(tmp_path)
    same = "<html><body><p>x</p></body></html>"
    record = corpus.record(1, same, same, exec={"success": False, "error": "element is detached"})
    rendered = render_step(record, 1)
    assert "FAILED" in rendered and "BLOCKED" not in rendered


def test_an_unsummarized_change_is_not_phrased_as_an_absence(tmp_path):
    """Saying "no observable change" about a page that changed invites a dead-control read."""
    corpus = _Corpus(tmp_path)
    same = "<html><body><p>x</p></body></html>"
    record = corpus.record(1, same, same)
    record.changed["html"] = True
    rendered = render_step(record, 1)
    assert "the page DID change" in rendered
    assert "NO OBSERVABLE CHANGE" not in rendered


def test_both_prompts_exempt_blocked_steps():
    from web_testing_agent.judge import SYSTEM_PROMPT
    from web_testing_agent.judge.prompt import COMPACT_SYSTEM_PROMPT

    for prompt in (SYSTEM_PROMPT, COMPACT_SYSTEM_PROMPT):
        assert "BLOCKED" in prompt


# --- target="_blank" links -------------------------------------------------------

PLAIN = "<html><head><title>Home</title></head><body><p>Home</p></body></html>"

_NEW_TAB_ACTION = {
    "type": "CLICK",
    "selector": '[id="docs"]',
    "element": "Powered by Gitea",
    "description": "click 'Powered by Gitea'",
    "params": {"navigational": False, "opens_new_tab": True, "href": "https://about.gitea.com"},
}


def test_a_new_tab_click_is_not_reported_as_byte_identical(tmp_path):
    """Regression, measured on Gitea's landing page.

    Popup adoption is a race: wait_settled() returns as soon as the current page stops
    mutating, which after a target="_blank" click is almost immediately — sometimes
    before Chromium has created the tab. Lose the race and the env records a successful
    click that changed nothing. Clicks on `code.gitea.io/gitea`, `packaged`, `run the
    binary` and `Powered by Gitea` each then drew a 90-100%-confidence broken_navigation
    verdict from a window asserting the page was byte-identical. None is broken.
    """
    corpus = _Corpus(tmp_path)
    record = corpus.record(1, PLAIN, PLAIN, action=_NEW_TAB_ACTION)
    text = render_step(record, 1)
    assert "NEW TAB" in text
    assert "NO OBSERVABLE CHANGE" not in text


def test_the_new_tab_line_names_the_destination(tmp_path):
    corpus = _Corpus(tmp_path)
    text = render_step(corpus.record(1, PLAIN, PLAIN, action=_NEW_TAB_ACTION), 1)
    assert "https://about.gitea.com" in text


def test_the_new_tab_line_says_it_is_neither_dead_nor_broken(tmp_path):
    """The line has to do the judge's reasoning for it: 'nothing changed' after a click
    is the strongest bug signal in the prompt, so contradicting it must be explicit."""
    corpus = _Corpus(tmp_path)
    text = render_step(corpus.record(1, PLAIN, PLAIN, action=_NEW_TAB_ACTION), 1)
    assert "NOT a dead control" in text and "NOT a broken link" in text


def test_an_ordinary_dead_control_still_reports_no_observable_change(tmp_path):
    """The fix must not suppress the bug class the judge exists to catch."""
    corpus = _Corpus(tmp_path)
    action = {"type": "CLICK", "selector": '[id="export"]', "element": "Export data",
              "description": "click 'Export data'", "params": {"navigational": False}}
    text = render_step(corpus.record(1, PLAIN, PLAIN, action=action), 1)
    assert "NO OBSERVABLE CHANGE" in text
    assert "NEW TAB" not in text


def test_a_new_tab_click_that_did_change_the_page_reports_the_change(tmp_path):
    """opens_new_tab suppresses only the no-change claim, never a real diff."""
    corpus = _Corpus(tmp_path)
    after = "<html><head><title>Home</title></head><body><p>Home</p><p>Extra</p></body></html>"
    text = render_step(corpus.record(1, PLAIN, after, action=_NEW_TAB_ACTION), 1)
    assert "Extra" in text
    assert "NO OBSERVABLE CHANGE" not in text


# --- observation defects found by seeded ground truth on Gitea --------------------


def test_uncaught_exceptions_reach_the_window(tmp_path):
    """Regression, GITEA-05.

    Playwright reports console.error() on `console` and a genuine uncaught throw on
    `pageerror`. The deterministic trigger has always combined both; this renderer read
    only the first, so an uncaught TypeError fired the trigger while being completely
    invisible to the judge. The most classic bug class there is was unjudgeable.
    """
    corpus = _Corpus(tmp_path)
    record = corpus.record(
        1, PLAIN, PLAIN,
        after={"url": "/a", "path": "/a", "html": corpus.blob(PLAIN),
               "console_errors": [], "page_errors": ["TypeError: cannot read 'render' of null"],
               "network": []},
    )
    text = render_step(record, 1)
    assert "TypeError" in text


def test_console_and_page_errors_are_both_shown(tmp_path):
    corpus = _Corpus(tmp_path)
    record = corpus.record(
        1, PLAIN, PLAIN,
        after={"url": "/a", "path": "/a", "html": corpus.blob(PLAIN),
               "console_errors": ["console side"], "page_errors": ["thrown side"],
               "network": []},
    )
    text = render_step(record, 1)
    assert "console side" in text and "thrown side" in text


def test_a_hidden_link_is_named_by_its_text_when_it_has_no_id():
    """Regression, GITEA-03. Collapsing every id-less element into '(unnamed) xN'
    protected the budget from Gitea's 37 _aria_auto_id_N entries, and in doing so
    erased which links a viewport change had removed."""
    html = (
        '<html><body>'
        '<a href="/user/sign_up" data-hidden="true"><svg viewBox="0 0 16 16">'
        '<path d="M10.5 5a2.5 2.5 0 1 0-5 0"/></svg> Register</a>'
        '<a href="/user/login" data-hidden="true">Sign In</a>'
        '</body></html>'
    )
    hidden = page_view("/p", html).hidden
    assert any("Register" in item for item in hidden)
    assert any("Sign In" in item for item in hidden)


def test_a_hidden_label_never_leaks_markup():
    """The fixed-size lookahead can sever a tag, and `_TAG_STRIP` needs a closing '>'.
    Gitea wraps every nav label in an inline SVG, so a severed <path d="..."> was
    rendered as though it were the control's name."""
    html = '<html><body><a href="/x" data-hidden="true"><svg><path d="' + "M2 2.75C2 1.784 " * 60 + '"/></svg></a></body></html>'
    for item in page_view("/p", html).hidden:
        assert "<" not in item and "path d=" not in item


def test_a_decorative_hidden_link_stays_unnamed():
    """Inventing a name for a control that has none would be worse than admitting it."""
    html = '<html><body><a href="/x" data-hidden="true"><svg><circle r="2"/></svg></a></body></html>'
    assert any("(unnamed)" in item for item in page_view("/p", html).hidden)
