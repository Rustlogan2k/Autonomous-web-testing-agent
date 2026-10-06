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
        "http://h:1/orders/17/edit", labels, form_signature(html), shows_alert(html), SPECS
    )


def test_abstraction():
    assert path_template("http://h:9/orders/17#step-2") == "/orders/{n}#step-{n}"
    assert form_signature(HTML) == (("email", False), ("qty", True))
    assert shows_alert(HTML) and not shows_alert('<p class="notice"></p><br>')
    view = _view()
    assert view.labels.kind == "form"
    assert view.cell == "form|/orders/{n}/edit|email=0,qty=1|!"
    assert [s.index for s in view.controls_with_role("save")] == [3]
    assert [s.index for s in view.controls_with_role("commit")] == [6]


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
    assert op.next(view) is None and op.done
    complete = Operator("complete_step")
    assert complete.next(view).index == 7  # fill the empty field validly first
