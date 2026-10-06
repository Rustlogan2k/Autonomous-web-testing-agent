"""The hybrid tester's page labeller and its Stage 0 scoring (no model calls)."""

from __future__ import annotations

from web_testing_agent.envs.types import ActionSpec, ActionType
from web_testing_agent.hybrid import stage0
from web_testing_agent.hybrid.labels import (
    CONTROL_ROLES,
    FIELD_MEANINGS,
    PAGE_KINDS,
    SemanticLabeller,
    control_identity,
    describe_controls,
)

HTML = """<form><input id="mail" name="contact_email" type="email">
<input id="who" name="contact_name" type="text"><textarea id="note" name="note"></textarea>
<select id="qty" name="quantity"><option>1</option><option>2</option></select>
<button id="save">Save</button></form><a id="help" href="help.html">Help</a>"""


def _spec(i, kind, dom, label, **params):
    return ActionSpec(
        index=i,
        action_type=kind,
        selector=f'[id="{dom}"]',
        element_id=label,
        params=params,
        description="",
    )


SPECS = [
    ActionSpec(index=0, action_type=ActionType.SCROLL, params={"direction": "down"}),
    _spec(1, ActionType.CLICK, "save", "Save", href="", navigational=False),
    _spec(2, ActionType.RAPID_CLICK, "save", "Save", n=5),
    _spec(3, ActionType.CLICK, "help", "Help", href="help.html", navigational=True),
    _spec(
        4, ActionType.TYPE, "mail", "contact_email", category="valid_typical", label_text="Email"
    ),
    _spec(
        5, ActionType.TYPE, "mail", "contact_email", category="type_mismatch", label_text="Email"
    ),
    _spec(6, ActionType.TYPE, "who", "contact_name", category="valid_typical", label_text="Name"),
    _spec(7, ActionType.TYPE, "note", "note", category="valid_typical"),
    _spec(8, ActionType.SELECT, "qty", "quantity", option="1", label_text="Quantity"),
    _spec(9, ActionType.SELECT, "qty", "quantity", option="2", label_text="Quantity"),
]


def test_one_entry_per_control_with_deterministic_meanings():
    controls = describe_controls(SPECS, HTML)
    assert [c.key for c in controls] == ["c1", "c2", "f1", "f2", "f3", "f4"]
    by_key = {c.key: c for c in controls}
    assert by_key["c1"].line == 'button "Save"'
    assert by_key["c2"].line == 'link "Help" -> help.html'
    assert by_key["f1"].deterministic == "email"  # type="email"
    assert by_key["f2"].deterministic == ""  # type="text": the model decides
    assert by_key["f3"].deterministic == "free_text"  # textarea
    assert by_key["f4"].line.endswith("options: 1, 2")
    assert control_identity(SPECS[1]) == control_identity(SPECS[2])


def test_schema_constrains_every_asked_control():
    asked = [c for c in describe_controls(SPECS, HTML) if not c.deterministic]
    schema = SemanticLabeller()._schema(asked)
    assert schema["properties"]["page_kind"]["enum"] == list(PAGE_KINDS)
    props = schema["properties"]["controls"]["properties"]
    assert props["c1"]["enum"] == list(CONTROL_ROLES)
    assert props["f2"]["enum"] == list(FIELD_MEANINGS)
    assert set(schema["properties"]["controls"]["required"]) == {c.key for c in asked}


def test_ground_truth_lookup_and_views():
    assert stage0.truth_for("add-laser", stage0.GT_ROLE) == (True, {"add"})
    assert stage0.truth_for("decoy-advance", stage0.GT_ROLE) == (True, None)
    assert stage0.truth_for("unknown", stage0.GT_ROLE) == (False, None)
    assert stage0.view_of("http://x:1/index.html#review") == "review"
    assert stage0.view_of("http://x:1/index.html") == "home"
    assert stage0.view_of("http://x:1/configure.html") == "configure"


def test_concordance():
    assert stage0._concordance([0.1, 0.5, 0.9], [1, 2, 3]) == 1.0
    assert stage0._concordance([0.9, 0.5, 0.1], [1, 2, 3]) == 0.0
    assert stage0._concordance([0.5, 0.5], [1, 2]) == 0.5
