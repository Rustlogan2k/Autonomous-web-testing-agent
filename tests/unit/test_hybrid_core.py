"""The hybrid tester's abstraction, archive and operators (no browser, no model)."""

from __future__ import annotations

from web_testing_agent.envs.types import ActionSpec, ActionType
from web_testing_agent.hybrid.abstraction import (
    PageView,
    deterministic_labels,
    form_signature,
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


def test_abstraction():
    assert path_template("http://h:9/orders/17#step-2") == "/orders/{n}#step-{n}"
    assert form_signature(HTML) == (("email", False), ("qty", True))
    assert shows_alert(HTML) and not shows_alert('<p class="notice"></p><br>')
    view = _view()
    assert view.labels.kind == "form"
    assert view.cell == "form|/orders/{n}/edit||partial|!"
    assert [s.index for s in view.controls_with_role("submit")] == [3, 6]


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
