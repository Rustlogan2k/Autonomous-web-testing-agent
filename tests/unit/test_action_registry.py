from web_testing_agent.envs.action_registry import (
    MAX_ACTIONS,
    SCAN_SELECTOR,
    build_action_specs,
)
from web_testing_agent.envs.types import ActionType

_DEFAULTS = {
    "index": 0,
    "tag": "input",
    "type": "",
    "id": None,
    "name": None,
    "href": None,
    "resolvedHref": None,
    "target": None,
    "text": "",
    "disabled": False,
    "visible": True,
    "inViewport": True,
    "options": None,
}


def _el(**overrides) -> dict:
    element = dict(_DEFAULTS)
    element.update(overrides)
    return element


def test_empty_page_only_has_fixed_actions():
    specs = build_action_specs([])
    assert specs[0].action_type == ActionType.NO_OP
    assert not any(spec.action_type == ActionType.CLICK for spec in specs)


def test_never_exceeds_the_action_budget():
    elements = [_el(tag="input", index=i, type="text", name=f"field{i}") for i in range(50)]
    specs = build_action_specs(elements)
    assert len(specs) <= MAX_ACTIONS


def test_disabled_and_invisible_elements_are_skipped():
    elements = [
        _el(tag="button", index=0, text="Submit", disabled=True),
        _el(tag="button", index=1, text="Hidden", visible=False),
    ]
    specs = build_action_specs(elements)
    assert not any(spec.action_type == ActionType.CLICK for spec in specs)


def test_button_produces_click_and_rapid_click():
    elements = [_el(tag="button", index=0, text="Submit")]
    specs = build_action_specs(elements)
    types_present = {spec.action_type for spec in specs}
    assert ActionType.CLICK in types_present
    assert ActionType.RAPID_CLICK in types_present


def test_link_produces_click_only():
    elements = [_el(tag="a", index=0, href="/next", text="Next")]
    specs = build_action_specs(elements)
    dynamic = [spec for spec in specs if spec.selector is not None]
    assert len(dynamic) == 1
    assert dynamic[0].action_type == ActionType.CLICK


def test_text_input_produces_all_five_type_variants():
    elements = [_el(tag="input", index=0, type="text", name="username")]
    specs = build_action_specs(elements)
    type_specs = [s for s in specs if s.action_type == ActionType.TYPE and s.element_id == "username"]
    assert len(type_specs) == 5
    categories = {s.params["category"] for s in type_specs}
    assert len(categories) == 5


def test_select_targets_last_option():
    elements = [_el(tag="select", index=0, name="country", options=["us", "uk", "in"])]
    specs = build_action_specs(elements)
    select_specs = [s for s in specs if s.action_type == ActionType.SELECT]
    assert len(select_specs) == 1
    assert select_specs[0].params["option"] == "in"


def test_select_with_no_options_is_skipped():
    elements = [_el(tag="select", index=0, name="empty", options=[])]
    specs = build_action_specs(elements)
    assert not any(s.action_type == ActionType.SELECT for s in specs)


def test_checkbox_produces_click_not_type():
    elements = [_el(tag="input", index=0, type="checkbox", name="agree")]
    specs = build_action_specs(elements)
    dynamic = [spec for spec in specs if spec.selector is not None]
    assert len(dynamic) == 1
    assert dynamic[0].action_type == ActionType.CLICK


def test_hidden_and_file_inputs_produce_no_actions():
    elements = [
        _el(tag="input", index=0, type="hidden", name="csrf_token"),
        _el(tag="input", index=1, type="file", name="upload"),
    ]
    specs = build_action_specs(elements)
    assert not any(spec.selector is not None for spec in specs)


def test_indices_are_contiguous_and_unique():
    elements = [_el(tag="input", index=i, type="text", name=f"f{i}") for i in range(3)]
    specs = build_action_specs(elements)
    assert [spec.index for spec in specs] == list(range(len(specs)))


def test_id_attribute_preferred_over_name_for_selector():
    elements = [_el(tag="input", index=0, type="text", id="email-field", name="email")]
    specs = build_action_specs(elements)
    type_specs = [s for s in specs if s.action_type == ActionType.TYPE]
    assert type_specs[0].selector == '[id="email-field"]'


def test_id_selector_survives_css_metacharacters():
    """Framework ids routinely contain ':' or '.', which are combinators in a #-selector."""
    elements = [_el(tag="input", index=0, type="text", id="user:profile.email")]
    specs = build_action_specs(elements)
    selector = next(s.selector for s in specs if s.action_type == ActionType.TYPE)
    assert selector == '[id="user:profile.email"]'


def test_positional_fallback_is_indexed_against_the_scan_selector():
    """Regression: `button >> nth=<scan index>` counted within the wrong match set.

    `index` is the element's position among *all* scanned elements, so the fallback
    selector must be indexed against the same combined selector. Using `tag >> nth=i`
    resolved to a different element entirely (or to nothing) on any page whose
    elements lack ids and names.
    """
    # 4th scanned element overall, but only the 2nd button on the page.
    elements = [_el(tag="button", index=3, text="Save")]
    specs = build_action_specs(elements)
    click = next(s for s in specs if s.action_type == ActionType.CLICK)
    assert click.selector == f"{SCAN_SELECTOR} >> nth=3"


def test_links_are_navigational_but_fragments_and_popups_are_not():
    cases = {
        "/next": True,
        "#section": False,
        "javascript:void(0)": False,
        "mailto:a@b.co": False,
    }
    for href, expected in cases.items():
        specs = build_action_specs([_el(tag="a", index=0, href=href, text="x")])
        click = next(s for s in specs if s.action_type == ActionType.CLICK)
        assert click.params["navigational"] is expected, href

    blank = build_action_specs([_el(tag="a", index=0, href="/next", target="_blank", text="x")])
    click = next(s for s in blank if s.action_type == ActionType.CLICK)
    assert click.params["navigational"] is False


def test_a_link_back_to_the_page_it_is_on_is_not_navigational():
    """The self-link false positive: every `broken_navigation` firing on the deep-flow
    fixture came from one of these, and the fixture's answer key wrongly stated that no
    deterministic trigger could fire there at all. Clicking `nav-support` while already
    on support.html reloads the page and correctly leaves the URL unchanged, which
    `detect_bug_signals` read as a broken link."""
    page_url = "http://127.0.0.1:8000/support.html"
    for href in ("support.html", "/support.html", page_url):
        specs = build_action_specs(
            [_el(tag="a", index=0, href=href, text="Support")], page_url=page_url
        )
        click = next(s for s in specs if s.action_type == ActionType.CLICK)
        assert click.params["navigational"] is False, href


def test_a_link_to_a_different_page_stays_navigational():
    """The other half of the fix: it must not silence genuine broken navigation. The
    toy site's BUG-03 is a nav link to a page that 404s, and it has to keep firing."""
    page_url = "http://127.0.0.1:8000/index.html"
    specs = build_action_specs(
        [_el(tag="a", index=0, href="pricing.html", text="Pricing")], page_url=page_url
    )
    click = next(s for s in specs if s.action_type == ActionType.CLICK)
    assert click.params["navigational"] is True


def test_a_self_path_link_carrying_a_different_query_is_still_navigational():
    """A same-path link with a different query genuinely does change the URL, so it is
    deliberately not folded into the self-link rule."""
    specs = build_action_specs(
        [_el(tag="a", index=0, href="catalog.html?page=2", text="Next")],
        page_url="http://127.0.0.1:8000/catalog.html",
    )
    click = next(s for s in specs if s.action_type == ActionType.CLICK)
    assert click.params["navigational"] is True


def test_a_self_link_differing_only_by_fragment_is_not_navigational():
    specs = build_action_specs(
        [_el(tag="a", index=0, href="support.html#returns", text="Returns")],
        page_url="http://127.0.0.1:8000/support.html",
    )
    click = next(s for s in specs if s.action_type == ActionType.CLICK)
    assert click.params["navigational"] is False


def test_the_browsers_own_href_resolution_wins_over_the_raw_attribute():
    """`resolvedHref` comes from the DOM, so a `<base href>` that changes what every
    relative href means is honoured without reimplementing base resolution in Python."""
    specs = build_action_specs(
        [_el(tag="a", index=0, href="index.html",
             resolvedHref="http://127.0.0.1:8000/app/index.html", text="Home")],
        page_url="http://127.0.0.1:8000/app/index.html",
    )
    click = next(s for s in specs if s.action_type == ActionType.CLICK)
    assert click.params["navigational"] is False


def test_without_a_page_url_every_non_fragment_link_stays_navigational():
    """The pre-fix behaviour is what an omitted `page_url` restores, so a caller that
    predates this argument is unchanged rather than silently altered."""
    specs = build_action_specs([_el(tag="a", index=0, href="support.html", text="Support")])
    click = next(s for s in specs if s.action_type == ActionType.CLICK)
    assert click.params["navigational"] is True


def test_on_screen_elements_are_allocated_before_off_screen_ones():
    """Off-screen elements must not crowd out what the agent can actually see."""
    off_screen = [_el(tag="a", index=i, href=f"/f{i}", text=f"footer {i}", inViewport=False) for i in range(120)]
    on_screen = [_el(tag="button", index=200, text="Submit order", inViewport=True)]
    specs = build_action_specs(off_screen + on_screen)

    clicked = [s.element_id for s in specs if s.action_type == ActionType.CLICK]
    assert "Submit order" in clicked
    assert clicked[0] == "Submit order"


def test_off_screen_elements_still_included_when_budget_allows():
    elements = [
        _el(tag="button", index=0, text="visible", inViewport=True),
        _el(tag="button", index=1, text="below the fold", inViewport=False),
    ]
    specs = build_action_specs(elements)
    clicked = [s.element_id for s in specs if s.action_type == ActionType.CLICK]
    assert clicked == ["visible", "below the fold"]


def test_valid_typical_is_prioritized_over_boundary_variants_under_tight_budget():
    # 30 text fields * 5 variants = 150 candidates, well over the ~91-slot dynamic budget.
    elements = [_el(tag="input", index=i, type="text", name=f"f{i}") for i in range(30)]
    specs = build_action_specs(elements)
    type_specs = [s for s in specs if s.action_type == ActionType.TYPE]
    valid_count = sum(1 for s in type_specs if s.params["category"] == "valid_typical")
    # every field's primary (valid_typical) value should make it in before any secondary variant does
    assert valid_count == 30
