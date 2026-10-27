"""The hybrid tester's abstraction, archive and operators (no browser, no model)."""

from __future__ import annotations

from web_testing_agent.envs.types import ActionSpec, ActionType
from web_testing_agent.hybrid.abstraction import (
    PageView,
    deterministic_labels,
    form_signature,
    forget_id_evidence,
    path_template,
    shows_alert,
)
from web_testing_agent.hybrid.archive import Archive, step_for
from web_testing_agent.hybrid.operators import Operator, options


def _spec(i, kind, dom=None, label="", **params):
    selector = f'[id="{dom}"]' if dom else None
    return ActionSpec(
        index=i,
        action_type=kind,
        selector=selector,
        element_id=label,
        params=params,
        description=f"{kind.value} {dom or ''}",
    )


HTML = (
    '<form><input id="email" name="email" type="email" value="">'
    '<input id="qty" name="qty" type="number" value="2"></form>'
    '<button id="save">Save</button><button id="edit">Edit</button>'
    '<button id="send">Send order</button><p class="notice">Check the email</p>'
)
SPECS = [
    _spec(0, ActionType.NO_OP),
    _spec(1, ActionType.BROWSER_BACK),
    _spec(2, ActionType.REFRESH),
    _spec(3, ActionType.CLICK, "save", "Save"),
    _spec(4, ActionType.RAPID_CLICK, "save", "Save", n=5),
    _spec(5, ActionType.CLICK, "edit", "Edit"),
    _spec(6, ActionType.CLICK, "send", "Send order"),
    _spec(7, ActionType.TYPE, "email", "email", category="valid_typical"),
    _spec(8, ActionType.TYPE, "email", "email", category="type_mismatch"),
    _spec(9, ActionType.TYPE, "qty", "qty", category="boundary_max"),
]


def _view(html=HTML):
    labels = deterministic_labels("http://h:1/orders/17/edit", html, SPECS)
    return PageView(
        "http://h:1/orders/17/edit",
        labels,
        form_signature(html),
        shows_alert(html),
        SPECS,
        html=html,
    )


def test_main_heading_separates_single_page_views():
    from web_testing_agent.hybrid.abstraction import main_heading

    assert main_heading("<h2>Review</h2><h1>Order 1042 confirmed</h1>") == "order {n} confirmed"
    assert main_heading("<h1 hidden>x</h1><h2>Basket</h2>") == "basket"


def _expected(route: str) -> str:
    return route.replace("2", "{n}")


def test_abstraction():
    assert path_template("http://h:9/orders/17#step-2") == "/orders/{n}#step-{n}"
    # Generated record ids are one page, not a new page per record ...
    for session in ("B1kKc_2nMg", "SJzq3_q2zl", "HJya9uqhGx"):
        assert path_template(f"http://h/session/{session}") == "/session/{id}"
    assert path_template("http://h/doc/550e8400-e29b-41d4-a716-446655440000/edit") == "/doc/{id}/edit"
    assert path_template("http://h/#/commit/9f86d081884c") == "/#/commit/{id}"
    # Letter-only ids are recognised once several different ones appear at the same place ...
    forget_id_evidence()
    assert path_template("http://h/session/SkGvNlChfl") == "/session/SkGvNlChfl"
    path_template("http://h/session/rJZtEeChze")
    assert path_template("http://h/session/SJYUVxRnzl") == "/session/{id}"
    assert path_template("http://h/session/SkGvNlChfl") == "/session/{id}"
    forget_id_evidence()
    # ... while route names stay distinct.
    for route in ("/settings", "/userProfile", "/checkout-step-two", "/myPosts", "/v2/api", "/strengthsList",
                  "/wizard/step-2", "/page2", "/getHTMLData", "/orderHistory", "/myAccount"):
        assert path_template(f"http://h{route}") == _expected(route)
    assert form_signature(HTML) == (("email", False), ("qty", True))
    assert shows_alert(HTML) and not shows_alert('<p class="notice"></p><br>')
    view = _view()
    assert view.labels.kind == "form"
    assert view.cell == "form|/orders/{n}/edit||partial|!|s"
    assert [s.index for s in view.controls_with_role("submit")] == [3, 6]


def test_site_chrome_controls_never_submit():
    from web_testing_agent.hybrid.abstraction import chrome_ids

    html = (
        '<header><nav><a id="express" href="/express">Express checkout</a></nav></header>'
        '<main><button id="update">Update bag</button>'
        '<a id="checkout" href="/checkout">Go to checkout</a></main>'
        '<div role="contentinfo"><button id="foot">Send feedback</button></div>'
    )
    assert chrome_ids(html) == {"express", "foot"}
    specs = [
        _spec(0, ActionType.CLICK, "express", "Express checkout"),
        _spec(1, ActionType.CLICK, "update", "Update bag"),
        _spec(2, ActionType.CLICK, "checkout", "Go to checkout"),
        _spec(3, ActionType.CLICK, "foot", "Send feedback"),
    ]
    labels = deterministic_labels("http://h:1/bag", html, specs)
    roles = {k[5:-2]: v for k, v in labels.roles.items()}
    assert roles == {
        "express": "navigate",
        "update": "submit",
        "checkout": "submit",
        "foot": "navigate",
    }


def test_operators_take_each_submit_control_in_turn():
    from web_testing_agent.hybrid.policy import HybridPolicy

    policy = HybridPolicy(seed=0)
    view = _view()
    first = Operator("complete_step", choose=policy._choose_submit)
    second = Operator("complete_step", choose=policy._choose_submit)
    view_filled = _view(HTML.replace('value=""', 'value="a@b.c"'))
    assert first.next(view_filled).index == 3  # save, first in page order
    assert second.next(view_filled).index == 6  # next visit: the other submit control
    assert Operator("complete_step").next(view_filled).index == 3  # no chooser: the first
    assert view.cell != view_filled.cell  # uses are counted per abstract page


def test_outcomes_reward_the_unexpected_response_not_any_alert():
    import pytest

    from web_testing_agent.hybrid.policy import HybridPolicy

    view = _view()  # shows an alert
    quiet = _view(HTML.replace('class="notice"', ""))
    with pytest.raises(ValueError):
        HybridPolicy(outcome="reward")

    def finished(outcome, name, *, submitted, alerted, page):
        policy = HybridPolicy(seed=0, outcome=outcome)
        policy.operator = Operator(name, probe_submitted=submitted)
        policy._operator_alerted = alerted
        return policy._outcome(page)

    # The v2 development default: any alert at the end is a success.
    assert finished("alert", "invalid_input", submitted=True, alerted=True, page=view) == 1.0
    # A validation message after invalid input is the expected response...
    assert finished("surprise", "invalid_input", submitted=True, alerted=True, page=view) == 0.0
    # ...its absence is the surprise, but only once the invalid value was submitted.
    assert finished("surprise", "invalid_input", submitted=True, alerted=False, page=quiet) == 1.0
    assert finished("surprise", "invalid_input", submitted=False, alerted=False, page=quiet) == 0.0
    # A valid path that is refused is a surprise too; "novelty" ignores alerts entirely.
    assert finished("surprise", "complete_step", submitted=True, alerted=True, page=view) == 1.0
    assert finished("novelty", "complete_step", submitted=True, alerted=True, page=view) == 0.0


PAIR_HTML = (
    '<form><select id="method" name="method"><option value="standard" selected>Standard'
    '</option><option value="express">Express</option></select>'
    '<input id="address" name="address" type="text" value="">'
    '<input id="name" name="name" type="text" value="">'
    '<button id="use">Use voucher</button></form>'
)
PAIR_SPECS = [
    _spec(0, ActionType.NO_OP),
    _spec(1, ActionType.SELECT, "method", "method", option="standard"),
    _spec(2, ActionType.SELECT, "method", "method", option="express"),
    _spec(3, ActionType.TYPE, "address", "address", category="valid_typical"),
    _spec(4, ActionType.TYPE, "address", "address", category="empty_string"),
    _spec(5, ActionType.TYPE, "name", "name", category="valid_typical"),
    _spec(6, ActionType.CLICK, "use", "Use voucher"),
]


def _pair_view(html=PAIR_HTML, *, form_submit=True):
    labels = deterministic_labels("http://h:1/checkout", html, PAIR_SPECS, form_submit=form_submit)
    return PageView("http://h:1/checkout", labels, form_signature(html), False, PAIR_SPECS,
                    html=html)


def test_a_button_that_submits_its_form_is_a_submit_control_when_asked():
    assert _pair_view().labels.roles['[id="use"]'] == "submit"
    assert _pair_view(form_submit=False).labels.roles['[id="use"]'] == "other"
    outside = PAIR_HTML.replace("</form>", "") + "</form>"  # still inside: unchanged
    assert _pair_view(outside).labels.roles['[id="use"]'] == "submit"
    loose = PAIR_HTML.replace("<form>", "").replace("</form>", "")
    assert _pair_view(loose).labels.roles['[id="use"]'] == "other"


def test_pairwise_operator_sets_two_deviations_then_completes_and_submits():
    view = _pair_view()
    assert not [o for o in options(view) if o[0] == "combine"]  # off by default
    pairs = [t for op, t in options(view, pairwise=True) if op == "combine"]
    target = '[id="method"]|express&[id="address"]|empty_string'
    assert target in pairs
    assert not any("&[id=\"method\"]" in t for t in pairs)  # a select is not paired with itself
    op = Operator("combine", target)
    assert [op.next(view).index for _ in range(3)] == [2, 4, 5]  # express, empty, then the name
    named = _pair_view(PAIR_HTML.replace('id="name" name="name" type="text" value=""',
                                         'id="name" name="name" type="text" value="A"'))
    assert op.next(named).index == 6  # the address stays empty; submit


def test_family_choice_offers_one_candidate_per_technique():
    from web_testing_agent.hybrid.archive import Cell
    from web_testing_agent.hybrid.policy import Planner

    planner = Planner(Archive(), seed=0)
    cell = Cell("k", [], "form", 0)
    choices = [("invalid_input", f"f{i}") for i in range(30)] + [("reload_and_resume", "")]
    assert planner.candidates(cell, choices) == choices
    planner.family_choice = True
    picked = planner.candidates(cell, choices)
    assert [c[0] for c in picked] == ["invalid_input", "reload_and_resume"]
    cell.tried = {c for c in choices if c[0] == "invalid_input" and c[1] != "f7"}
    assert planner.candidates(cell, choices)[0] == ("invalid_input", "f7")  # untried first


def test_archive_keeps_the_shortest_walk_and_discounts_deep_cells():
    archive = Archive(episode_steps=40)
    cell, created = archive.visit("a", [{"type": "CLICK"}] * 5, "form", 0)
    assert created and cell.depth == 5
    archive.visit("a", [{"type": "CLICK"}] * 2, "form", 1)
    assert cell.depth == 2 and cell.visits == 2
    deep, _ = archive.visit("b", [{"type": "CLICK"}] * 35, "form", 2)
    assert archive.weight(deep, 3) < archive.weight(cell, 3)
    assert step_for(SPECS[8]) == {
        "type": "TYPE",
        "id": "email",
        "params": {"category": "type_mismatch"},
    }


def test_operators_offered_and_sequenced():
    view = _view()
    offered = options(view)
    assert ("complete_step", "") in offered
    assert ("invalid_input", '[id="email"]|type_mismatch') in offered
    assert ("edit_after_review", '[id="edit"]|type_mismatch') in offered
    assert ("double_submit", '[id="save"]') in offered
    op = Operator("invalid_input", '[id="email"]|type_mismatch')
    assert op.next(view).index == 8  # the invalid value into the target field
    assert op.next(view).index == 3  # then submit (save before commit)
    assert op.probed is False and op.next(view).index == 7  # follow-through: complete the form
    steps = [op.next(view) for _ in range(40)]
    assert op.done and steps[-1] is None and op.steps <= 20  # bounded
    complete = Operator("complete_step")
    assert complete.next(view).index == 7  # fill the empty field validly first


def test_bandit_learns_and_backs_off_to_general_keys():
    from web_testing_agent.hybrid.learner import BanditPlanner, target_type
    from web_testing_agent.hybrid.policy import Decision

    archive = Archive()
    cell, _ = archive.visit("form|/x||", [], "form", 0)
    bandit = BanditPlanner(archive, seed=1)
    choices = [("complete_step", ""), ("reload_and_resume", "")]
    for _ in range(20):
        bandit.feedback(cell, Decision("reload_and_resume", ""), None, 1.0)
        bandit.feedback(cell, Decision("complete_step", ""), None, 0.0)
    picks = [bandit.choose_operator(cell, choices, None).operator for _ in range(30)]
    assert picks.count("reload_and_resume") >= 28
    # Evidence from form pages informs an unseen page kind through the general keys.
    assert bandit.value("list", "reload_and_resume", "") > bandit.value("list", "complete_step", "")
    assert (
        target_type("invalid_input", '[id="email"]|type_mismatch', _view()) == "email:type_mismatch"
    )
    restored = BanditPlanner(
        archive, prior={tuple(k.split("|")): v for k, v in bandit.state().items()}
    )
    assert restored.value("form", "reload_and_resume", "") == bandit.value(
        "form", "reload_and_resume", ""
    )


def test_bandit_backoff_zero_shares_nothing_and_decay_forgets():
    from web_testing_agent.hybrid.learner import BanditPlanner
    from web_testing_agent.hybrid.policy import Decision

    archive = Archive()
    cell, _ = archive.visit("form|/x||", [], "form", 0)
    isolated = BanditPlanner(archive, seed=1, backoff=0.0)
    for _ in range(10):
        isolated.feedback(cell, Decision("reload_and_resume", ""), None, 1.0)
    # With no back-off, form-page evidence says nothing about a list page.
    assert isolated.value("list", "reload_and_resume", "") == 0.5

    forgetful = BanditPlanner(archive, seed=1, decay=0.5)
    for _ in range(10):
        forgetful.feedback(cell, Decision("reload_and_resume", ""), None, 1.0)
    forgetful.feedback(cell, Decision("reload_and_resume", ""), None, 0.0)
    successes, failures = forgetful.counts[("form", "reload_and_resume", "")]
    assert successes < 2.0 and failures == 1.0  # ten early successes have mostly faded


def test_invariant_content_ignores_entries_timings_and_alerts():
    from web_testing_agent.hybrid.invariants import content_text

    page = (
        "<h1>Issue 1</h1><textarea>draft text</textarea><select><option>a</option></select>"
        '<p class="notice">Saved!</p><footer>Page: 4ms Template: 1.5 ms 10:42</footer>'
    )
    assert content_text(page) == "Issue 1 Page: {ms} Template: {ms} {time}"


def test_round_trip_collapses_whitespace_like_a_page():
    from web_testing_agent.hybrid.invariants import InvariantChecker

    checker = InvariantChecker()
    checker.entered['[id="c"]'] = ("multi-line\ncomment for testing", "free_text", "valid_typical")
    shown = _view("<p>multi-line comment for testing</p>")
    assert checker._round_trip(shown, 1) == []
    altered = _view("<p>multi-line comment for testin</p>")
    assert [c.invariant for c in checker._round_trip(altered, 2) if c] == ["round_trip"]


def test_prior_pools_runs_and_caps_each_key():
    from web_testing_agent.hybrid.prior import pool

    prior = pool(
        [
            {"form|invalid_input|email:type_mismatch": [6.0, 2.0]},
            {
                "form|invalid_input|email:type_mismatch": [2.0, 0.0],
                "*|reload_and_resume|": [0.0, 1.0],
            },
        ]
    )
    s, f = prior["form|invalid_input|email:type_mismatch"]
    assert abs(s + f - 5.0) < 1e-6 and abs(s / (s + f) - 0.8) < 1e-6  # mean kept, weight capped
    assert prior["*|reload_and_resume|"] == [0.0, 1.0]  # light evidence untouched


def test_attribution_follows_the_per_fault_control_rule(monkeypatch):
    from web_testing_agent.hybrid import invariants

    builds = {"clean": False, "A": True, "B": False}  # reproduces without A's patch only

    def fake_reproduce(factory, candidate, **_):
        return builds[factory()]

    monkeypatch.setattr(invariants, "reproduce", fake_reproduce)
    candidate = invariants.Candidate("round_trip", 3, "u", "d")
    out = invariants.attribute(candidate, lambda: "clean", {"A": lambda: "A", "B": lambda: "B"})
    assert out == {"on_clean": False, "faults": ["B"]}  # silenced when B's patch is removed
    builds["clean"] = True
    assert invariants.attribute(candidate, lambda: "clean", {})["on_clean"] is True


def test_complete_step_fills_fields_that_have_only_a_name_or_nothing_at_all():
    from web_testing_agent.hybrid.operators import fill_step

    # Petclinic-style markup: fields addressed by name, and one AngularJS field with neither.
    html = ('<input name="firstName" value="Ada"><input name="telephone" value="">'
            '<input type="date" ng-model="pet.birthDate" value="">')

    def typed(i, selector, label, filled):
        return ActionSpec(index=i, action_type=ActionType.TYPE, selector=selector, element_id=label,
                          params={"category": "valid_typical", "filled": filled}, description=f"type {label}")

    def view_of(specs):
        return PageView("http://h/x", deterministic_labels("http://h/x", html, specs), form_signature(html),
                        False, specs, html=html)

    specs = [typed(0, '[name="firstName"]', "firstName", True), typed(1, '[name="telephone"]', "telephone", False),
             typed(2, "input, textarea, select, button, a[href] >> nth=2", "date", False)]
    assert fill_step(view_of(specs)).index == 1          # the empty named field, found by its name
    assert fill_step(view_of(specs[:1] + specs[2:])).index == 2   # no id, no name: its own state


def test_complete_step_ticks_boxes_and_chooses_radios_after_text():
    from web_testing_agent.hybrid.operators import fill_step

    html = (
        '<input id="name" name="name" type="text" value="Ada">'
        '<input id="ack" name="ack" type="checkbox">'
        '<input id="r1" name="plan" type="radio"><input id="r2" name="plan" type="radio">'
        '<input id="done" name="done" type="checkbox" checked>'
    )
    specs = [
        _spec(0, ActionType.TYPE, "name", "name", category="valid_typical"),
        _spec(1, ActionType.CLICK, "ack", "ack"),
        _spec(2, ActionType.CLICK, "r1", "plan"),
        _spec(3, ActionType.CLICK, "r2", "plan"),
        _spec(4, ActionType.CLICK, "done", "done"),
    ]
    view = PageView(
        "http://h/x",
        deterministic_labels("http://h/x", html, specs),
        form_signature(html),
        False,
        specs,
        html=html,
    )
    assert view.fill_level == "full"
    assert fill_step(view).index == 1  # unticked checkbox first
    ticked = html.replace(
        'id="ack" name="ack" type="checkbox"', 'id="ack" name="ack" type="checkbox" checked'
    )
    view2 = PageView("http://h/x", view.labels, form_signature(ticked), False, specs, html=ticked)
    assert fill_step(view2).index == 2  # then the first radio of an unset group
    chosen = ticked.replace(
        'id="r1" name="plan" type="radio"', 'id="r1" name="plan" type="radio" checked'
    )
    view3 = PageView("http://h/x", view.labels, form_signature(chosen), False, specs, html=chosen)
    assert fill_step(view3) is None  # complete: nothing left to fill


def test_reload_flags_only_lost_entries_not_reset_messages():
    from web_testing_agent.hybrid.invariants import InvariantChecker

    checker = InvariantChecker()
    checker.entered['[id="n"]'] = ("Test User Input", "name", "valid_typical")
    refresh = SPECS[2]  # REFRESH
    shown = _view("<h1>Profile</h1><p>Name: Test User Input</p><p>Display name updated.</p>")
    reset = _view("<h1>Profile</h1><p>Name: Test User Input</p>")
    assert checker.check(shown, refresh, reset, 1) == []  # a one-time message may go
    lost = _view("<h1>Profile</h1><p>Name: </p>")
    assert [c.invariant for c in checker.check(shown, refresh, lost, 2)] == ["reload_stability"]


def test_paging_controls_and_the_browse_list_operator():
    from web_testing_agent.hybrid.operators import paging_controls

    html = ('<button id="sort">Sort by date</button><ul><li><a id="open-1" href="/i/1">View</a></li>'
            '</ul><a id="p1" href="?page=1">1</a><a id="p2" href="?page=2">2</a>'
            '<button id="more">Load more</button><button id="nxt">›</button>')
    specs = [
        _spec(0, ActionType.NO_OP),
        _spec(1, ActionType.CLICK, "sort", "Sort by date"),
        _spec(2, ActionType.CLICK, "open-1", "View"),
        _spec(3, ActionType.CLICK, "p1", "1", href="?page=1"),
        _spec(4, ActionType.CLICK, "p2", "2", href="?page=2"),
        _spec(5, ActionType.CLICK, "more", "Load more"),
        _spec(6, ActionType.CLICK, "nxt", "›"),
    ]
    labels = deterministic_labels("http://h:1/items", html, specs)
    view = PageView("http://h:1/items", labels, form_signature(html), False, specs, html=html)
    # "1" (the page already shown) is not forward; numbered pages come after named ones.
    assert [s.index for s in paging_controls(view)] == [5, 6, 4]
    offered = options(view, browse=True)
    assert ("browse_list", "") in offered and ("browse_list", '[id="sort"]') in offered
    assert not [o for o in options(view) if o[0] == "browse_list"]  # off by default
    op = Operator("browse_list", '[id="sort"]')
    assert [op.next(view).index for _ in range(4)] == [1, 5, 5, 2]  # sort, page, page, open


def test_failure_feedback_weights_cells_where_errors_followed_actions():
    archive = Archive(episode_steps=40, failure_bonus=2.0)
    cell, _ = archive.visit("a", [], "form", 0)
    plain = archive.weight(cell, 3)
    cell.failures = 3
    assert archive.weight(cell, 3) == plain * 3.0
    assert Archive(episode_steps=40).weight(cell, 3) == plain  # off by default
