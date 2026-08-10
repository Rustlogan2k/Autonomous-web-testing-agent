from web_testing_agent.perception.normalization import (
    canonicalize_text,
    canonicalize_url,
    extract_structure,
    preprocess_for_structural_encoder,
    state_fingerprint,
)

_PAGE = """
<html><head><title>Issues</title><style>.a{{color:red}}</style></head>
<body>
  <h1>Open issues</h1>
  <form action="/issues/new" method="post">
    <input type="hidden" name="_csrf" value="{token}">
    <input type="text" name="title">
    <textarea name="body"></textarea>
    <button type="submit" onclick="track('submit')">Create</button>
  </form>
  <a href="/issues/1?_csrf={token}">First issue</a>
  <script>var t = "{token}"; doThings();</script>
</body></html>
"""


def _page(token: str) -> str:
    return _PAGE.format(token=token)


def test_uuids_and_tokens_are_masked():
    text = "id=550e8400-e29b-41d4-a716-446655440000 csrf=a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8"
    masked = canonicalize_text(text)
    assert "550e8400" not in masked
    assert "<UUID>" in masked
    assert "<TOKEN>" in masked


def test_timestamps_are_masked():
    assert "<TIMESTAMP>" in canonicalize_text("Updated 2026-08-01T12:30:00Z")


def test_hashed_asset_names_are_masked():
    masked = canonicalize_text('<script src="/static/app.9f8e7d6c5b4a3210.js">')
    assert "<HASH>" in masked
    assert masked.endswith('.js">')


def test_ordinary_content_survives_canonicalization():
    text = "Welcome back. You have 3 open issues."
    assert canonicalize_text(text) == text


def test_volatile_query_params_are_stripped_but_real_ones_kept():
    canonical = canonicalize_url("http://h/issues?state=open&_csrf=abc123&page=2")
    assert "state=open" in canonical
    assert "page=2" in canonical
    assert "_csrf" not in canonical


def test_path_ids_are_preserved():
    """/issues/1 and /issues/2 are genuinely different states, unlike a rotating token."""
    assert canonicalize_url("http://h/issues/1") != canonicalize_url("http://h/issues/2")


def test_same_page_with_different_csrf_tokens_has_one_fingerprint():
    first = state_fingerprint("http://h/issues/new", _page("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"))
    second = state_fingerprint("http://h/issues/new", _page("ffeeddccbbaa99887766554433221100"))
    assert first == second


def test_structurally_different_pages_have_different_fingerprints():
    issues = state_fingerprint("http://h/issues/new", _page("tok"))
    settings = state_fingerprint(
        "http://h/settings", "<html><title>Settings</title><input name='email'></html>"
    )
    assert issues != settings


def test_structure_extraction_finds_forms_links_and_handlers():
    structure = extract_structure(_page("tok"))
    assert structure.title == "Issues"
    assert structure.headings == ["Open issues"]

    form = next(f for f in structure.forms if f.action == "/issues/new")
    assert form.method == "post"
    field_names = {name for name, _ in form.fields}
    assert {"title", "body", "_csrf"} <= field_names

    assert any(handler == "onclick" for handler, _ in structure.inline_handlers)
    assert any(text == "First issue" for _, text in structure.anchors)


def test_structural_preprocessing_drops_script_bodies_but_keeps_handlers():
    rendered = preprocess_for_structural_encoder(_page("tok"))
    assert "doThings" not in rendered
    assert "color:red" not in rendered
    assert "onclick" in rendered
    assert "FORM action=/issues/new" in rendered


def test_structural_preprocessing_is_bounded():
    huge = "<html><body>" + "<a href='/x'>link</a>" * 20_000 + "</body></html>"
    assert len(preprocess_for_structural_encoder(huge, max_chars=4_000)) <= 4_000


def test_malformed_markup_does_not_raise():
    assert extract_structure("<html><body><form><input name=x</body>") is not None
    assert state_fingerprint("http://h/", "<<<>>not really html")


def test_inputs_outside_a_form_are_still_captured():
    structure = extract_structure("<html><body><input name='q' type='search'></body></html>")
    assert any(("q", "search") in form.fields for form in structure.forms)


# --- form occupancy in the state fingerprint ------------------------------------


def _form(username_value: str = "", darkmode: str = "", country_selected: str = "") -> str:
    return (
        "<html><head><title>Signup</title></head><body>"
        f'<form id="f"><input type="text" id="username" name="username"{username_value}>'
        f'<input type="checkbox" id="dark" name="darkmode"{darkmode}>'
        f'<select id="country" name="country"><option value="">-</option>'
        f'<option value="us"{country_selected}>US</option></select>'
        "</form></body></html>"
    )


def test_filling_a_field_changes_the_fingerprint():
    """Progressing through a flow must register as a new state, or it earns no novelty."""
    empty = state_fingerprint("http://a/signup", _form())
    filled = state_fingerprint("http://a/signup", _form(username_value=' value="Ada"'))
    assert empty != filled


def test_the_fingerprint_ignores_which_value_was_typed():
    """Otherwise every distinct input string mints a new state and novelty degenerates."""
    one = state_fingerprint("http://a/signup", _form(username_value=' value="Ada"'))
    two = state_fingerprint("http://a/signup", _form(username_value=' value="Grace"'))
    assert one == two


def test_checking_a_box_changes_the_fingerprint():
    """BUG-09's entire repro is checkbox state; it must be part of page identity."""
    off = state_fingerprint("http://a/settings", _form())
    on = state_fingerprint("http://a/settings", _form(darkmode=" checked"))
    assert off != on


def test_selecting_an_option_changes_the_fingerprint():
    before = state_fingerprint("http://a/signup", _form())
    after = state_fingerprint("http://a/signup", _form(country_selected=" selected"))
    assert before != after


def test_occupancy_does_not_reintroduce_token_sensitivity():
    """A rotating CSRF token in a hidden field must still fold to one state."""
    def page(token: str) -> str:
        return (
            "<html><head><title>Signup</title></head><body><form id='f'>"
            f'<input type="hidden" name="csrf" value="{token}">'
            '<input type="text" id="username" name="username"></form></body></html>'
        )

    assert state_fingerprint("http://a/s", page("a" * 40)) == state_fingerprint("http://a/s", page("b" * 40))


def test_textarea_occupancy_reads_its_text_content():
    def page(body: str) -> str:
        return f"<html><body><form><textarea name='bio'>{body}</textarea></form></body></html>"

    assert state_fingerprint("http://a/p", page("")) != state_fingerprint("http://a/p", page("hello"))
    assert state_fingerprint("http://a/p", page("hello")) == state_fingerprint("http://a/p", page("world"))


def test_buttons_and_submits_have_no_occupancy():
    """Their `value` is a label, not user input, and never changes."""
    structure = extract_structure(
        '<form><input type="submit" name="go" value="Send">'
        '<input type="text" name="q"></form>'
    )
    assert structure.field_states == [("q", "empty")]
