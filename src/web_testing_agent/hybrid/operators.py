"""Test operators: the moves the hybrid tester applies at a chosen state (plan §4).

Each operator is a short, adaptive sequence of the environment's own actions, chosen from
standard test-design techniques rather than from any application's faults:

- equivalence partitioning / boundary values: `invalid_input` enters one field's boundary,
  empty or wrong-format value while the others are valid, then submits;
- state-transition testing: `edit_after_review`, `back_and_resume`, `reload_and_resume`,
  `double_submit`, `collection_churn`;
- `complete_step` is the valid path through one step, needed to get deep at all;
- list testing: `browse_list` optionally applies one sort/filter control, pages forward
  through the list (next page, load more) and opens an item on a later page, for faults in
  paging, sorting and filtering (optional; `options(browse=True)`);
- pairwise (combinatorial) testing: `combine` sets one select's option together with a second
  deviation on another field of the same form (a deviant value or another select's option),
  for faults that need two choices at once (optional; `options(pairwise=True)`).

An operator never sees the oracle; it only says which action to take next, step by step,
reading the page's labels, and stops when its sequence is done.
"""

from __future__ import annotations

from collections.abc import Callable
import re
from dataclasses import dataclass, field

from ..envs.types import ActionSpec, ActionType
from .abstraction import PageView
from .labels import control_identity

SUBMIT_ROLES = ("submit",)
INVALID_CATEGORIES = ("empty_string", "type_mismatch", "boundary_min", "boundary_max")
#: Operator names, frozen with the plan: the evaluation's decision layer chooses among these.
OPERATORS = (
    "complete_step",
    "choose_option",
    "invalid_input",
    "edit_after_review",
    "back_and_resume",
    "reload_and_resume",
    "double_submit",
    "collection_churn",
    "combine",
    "browse_list",
)
#: Operators whose probe submits a value the page should refuse: the expected response is
#: a validation message, so its absence is the surprise (see `HybridPolicy` outcomes).
DEVIANT_OPERATORS = frozenset({"invalid_input", "edit_after_review"})
MAX_OPERATOR_STEPS = 20
#: After its probe, an operator carries the task on (complete the form, submit) for up to
#: this many more submissions: a fault's effect usually shows at the end of a workflow.
FOLLOW_THROUGH_SUBMISSIONS = 4


def _category(spec: ActionSpec) -> str:
    return str((spec.params or {}).get("category", ""))


def _first(specs: list[ActionSpec], kind: ActionType) -> ActionSpec | None:
    return next((s for s in specs if s.action_type is kind), None)


def fill_step(view: PageView, *, skip: str = "") -> ActionSpec | None:
    """The next step of completing the form as a user would: a valid entry in each empty text
    field, then tick each unticked checkbox and choose the first option of any radio group
    with nothing chosen (selects already hold a value). `skip` names a field left alone."""
    spec = _text_fill(view, skip)
    return spec if spec is not None else _toggle_fill(view)


def _toggle_fill(view: PageView) -> ActionSpec | None:
    clicks = {
        (s.selector or "")[5:-2]: s
        for s in view.specs
        if s.action_type is ActionType.CLICK and (s.selector or "").startswith('[id="')
    }
    toggles = view.toggles()
    chosen_groups = {t.name for t in toggles if t.kind == "radio" and t.checked}
    for toggle in toggles:
        if toggle.dom_id not in clicks or toggle.checked:
            continue
        if toggle.kind == "checkbox":
            return clicks[toggle.dom_id]
        if toggle.name not in chosen_groups:
            return clicks[toggle.dom_id]
    return None


def _text_fill(view: PageView, skip: str) -> ActionSpec | None:
    filled = dict(view.signature)
    for identity, actions in view.fields().items():
        if identity == skip:
            continue
        dom = identity[5:-2] if identity.startswith('[id="') else identity
        if filled.get(dom, True):
            continue
        valid = next((a for a in actions if _category(a) == "valid_typical"), None)
        if valid is not None:
            return valid
    return None


#: Picks one of a page's submit controls (all of one role, in page order).
Chooser = Callable[[PageView, list[ActionSpec]], ActionSpec]


def submit_step(
    view: PageView,
    choose: Chooser | None = None,
    *,
    roles: tuple[str, ...] = SUBMIT_ROLES + ("add",),
) -> ActionSpec | None:
    """The page's primary action: a submit control, else (a form whose action is to add, as
    "Add to basket") its add control. With several, `choose` picks one (default: the first);
    the policy uses it to take each in turn across visits, since a page such as a basket
    offers both "Update" and "Go to checkout"."""
    for role in roles:
        controls = view.controls_with_role(role)
        if controls:
            return choose(view, controls) if choose is not None else controls[0]
    return None


@dataclass
class Operator:
    """One application of an operator, with its own progress through its sequence."""

    name: str
    target: str = ""
    steps: int = 0
    stage: int = 0
    done: bool = False
    probed: bool = False  # the operator's own sequence is finished; follow-through runs
    submissions: int = 0  # follow-through submissions so far
    log: list[str] = field(default_factory=list)
    choose: Chooser | None = None  # which of several submit controls (see `submit_step`)
    probe_submitted: bool = False  # the probe's own sequence submitted the page

    def next(self, view: PageView) -> ActionSpec | None:
        if self.done or self.steps >= MAX_OPERATOR_STEPS:
            self.done = True
            return None
        spec = None
        if not self.probed:
            spec = getattr(self, f"_{self.name}")(view)
            if spec is None:
                self.probed = True
        if spec is None:
            spec = self._follow_through(view)
        if spec is None:
            self.done = True
        else:
            self.steps += 1
            self.log.append(spec.description)
        return spec

    def _submit(self, view: PageView, *, roles: tuple[str, ...] = SUBMIT_ROLES + ("add",)):
        spec = submit_step(view, self.choose, roles=roles)
        if spec is not None and not self.probed:
            self.probe_submitted = True
        return spec

    def _follow_through(self, view: PageView) -> ActionSpec | None:
        """Carry the task on after the probe: complete the form, then submit."""
        if self.submissions >= FOLLOW_THROUGH_SUBMISSIONS:
            return None
        spec = fill_step(view)
        if spec is not None:
            return spec
        spec = self._submit(view)
        if spec is not None:
            self.submissions += 1
        return spec

    # -- the operators ------------------------------------------------------------------

    def _complete_step(self, view: PageView) -> ActionSpec | None:
        if self.stage == 0:
            spec = fill_step(view)
            if spec is not None:
                return spec
            self.stage = 1
            return self._submit(view)
        return None

    def _choose_option(self, view: PageView) -> ActionSpec | None:
        """Equivalence partitioning over a select's options: one non-default option, the
        rest of the form completed, then submit."""
        field_id, option = self.target.rsplit("|", 1)
        if self.stage == 0:
            self.stage = 1
            spec = _option(view, field_id, option)
            if spec is not None:
                return spec
        if self.stage == 1:
            spec = fill_step(view)
            if spec is not None:
                return spec
            self.stage = 2
            return self._submit(view)
        return None

    def _invalid_input(self, view: PageView) -> ActionSpec | None:
        field_id, category = self.target.rsplit("|", 1)
        if self.stage == 0:
            spec = fill_step(view, skip=field_id)
            if spec is not None:
                return spec
            self.stage = 1
            actions = view.fields().get(field_id, [])
            spec = next((a for a in actions if _category(a) == category), None)
            if spec is not None:
                return spec
        if self.stage == 1:
            self.stage = 2
            return self._submit(view)
        return None

    def _edit_after_review(self, view: PageView) -> ActionSpec | None:
        edit_id, category = self.target.rsplit("|", 1)
        if self.stage == 0:
            self.stage = 1
            return next(
                (s for s in view.controls_with_role("edit") if control_identity(s) == edit_id), None
            )
        if self.stage == 1:
            self.stage = 2
            for actions in view.fields().values():
                spec = next((a for a in actions if _category(a) == category), None)
                if spec is not None:
                    return spec
        if self.stage == 2:
            self.stage = 3
            return self._submit(view)
        if self.stage == 3:
            self.stage = 4
            return self._submit(view, roles=SUBMIT_ROLES)
        return None

    def _combine(self, view: PageView) -> ActionSpec | None:
        """Pairwise testing: a select's option, then a second field's deviant value or option,
        the rest of the form completed (the second field left as set), then submit."""
        first, second = self.target.split("&", 1)
        field_a, option = first.rsplit("|", 1)
        field_b, value = second.rsplit("|", 1)
        if self.stage == 0:
            self.stage = 1
            spec = _option(view, field_a, option)
            if spec is not None:
                return spec
        if self.stage == 1:
            self.stage = 2
            spec = _option(view, field_b, value) or next(
                (a for a in view.fields().get(field_b, []) if _category(a) == value), None
            )
            if spec is not None:
                return spec
        if self.stage == 2:
            spec = fill_step(view, skip=field_b)
            if spec is not None:
                return spec
            self.stage = 3
            return self._submit(view)
        return None

    def _browse_list(self, view: PageView) -> ActionSpec | None:
        """Apply the target sort/filter control (if any), page forward up to twice, then open
        an item on the later page; follow-through carries on from there."""
        if self.stage == 0:
            self.stage = 1
            if self.target:
                spec = next((s for s in view.specs if s.action_type is ActionType.CLICK
                             and control_identity(s) == self.target), None)
                if spec is not None:
                    return spec
        while self.stage in (1, 2):
            self.stage += 1
            controls = paging_controls(view)
            if controls:
                return controls[0]
        if self.stage == 3:
            self.stage = 4
            items = view.controls_with_role("open", "edit")
            return items[0] if items else None
        return None

    def _back_and_resume(self, view: PageView) -> ActionSpec | None:
        if self.stage == 0:
            self.stage = 1
            return _first(view.specs, ActionType.BROWSER_BACK)
        if self.stage == 1:
            self.stage = 2
            return _first(view.specs, ActionType.BROWSER_FORWARD)
        return self._resume(view)

    def _reload_and_resume(self, view: PageView) -> ActionSpec | None:
        if self.stage == 0:
            self.stage = 1
            return _first(view.specs, ActionType.REFRESH)
        return self._resume(view)

    def _resume(self, view: PageView) -> ActionSpec | None:
        """After a disruption, carry on with the step as a user would."""
        if self.stage < 5:
            spec = fill_step(view)
            if spec is not None:
                return spec
            self.stage = 5
            return self._submit(view)
        return None

    def _double_submit(self, view: PageView) -> ActionSpec | None:
        if self.stage == 0:
            spec = fill_step(view)
            if spec is not None:
                return spec
            self.stage = 1
            return next(
                (
                    s
                    for s in view.specs
                    if s.action_type is ActionType.RAPID_CLICK
                    and control_identity(s) == self.target
                ),
                None,
            )
        return None

    def _collection_churn(self, view: PageView) -> ActionSpec | None:
        adds, removes = view.controls_with_role("add"), view.controls_with_role("remove")
        plan = [
            adds[0] if adds else None,
            adds[1] if len(adds) > 1 else None,
            removes[0] if removes else None,
        ]
        while self.stage < len(plan):
            spec = plan[self.stage]
            self.stage += 1
            if spec is not None:
                return spec
        if self.stage == len(plan):
            self.stage += 1
            return self._submit(view)
        return None


def _option(view: PageView, field_id: str, option: str) -> ActionSpec | None:
    """The action choosing `option` in select `field_id`, if the page offers it."""
    return next(
        (
            a
            for a in view.fields().get(field_id, [])
            if a.action_type is ActionType.SELECT and str((a.params or {}).get("option")) == option
        ),
        None,
    )


#: A control that moves a list forward, in the words lists commonly use; or a link whose
#: target carries a page / offset / cursor parameter.
_PAGING = re.compile(
    r"^(?:next|more|load more|show more|see more|older|next page|›|»|>|→)(?=\W|$)"
    r"|\bpage\s*\d|^\d{1,3}$",
    re.I,
)
_PAGING_HREF = re.compile(r"[?&](page|p|offset|start|cursor|after)=", re.I)


def paging_controls(view: PageView) -> list[ActionSpec]:
    """Clicks that move a list forward; numbered pages after the first come last."""
    out = []
    for spec in view.specs:
        if spec.action_type is not ActionType.CLICK:
            continue
        label = (spec.element_id or "").strip()
        href = str((spec.params or {}).get("href") or "")
        if label in ("1",):
            continue
        if _PAGING.search(label) or _PAGING_HREF.search(href):
            out.append(spec)
    return sorted(out, key=lambda s: (s.element_id or "").strip().isdigit())


def options(view: PageView, *, pairwise: bool = False,
            browse: bool = False) -> list[tuple[str, str]]:
    """Every (operator, target) this page offers, in a stable order."""
    out: list[tuple[str, str]] = []
    fields = view.fields()
    if fields or submit_step(view) is not None:
        out.append(("complete_step", ""))
    for identity, actions in fields.items():
        categories = {_category(a) for a in actions}
        out += [("invalid_input", f"{identity}|{c}") for c in INVALID_CATEGORIES if c in categories]
    edits = view.controls_with_role("edit")
    if edits and view.controls_with_role("submit"):
        for spec in edits:
            out += [
                ("edit_after_review", f"{control_identity(spec)}|{c}")
                for c in ("type_mismatch", "empty_string")
            ]
    fixed = {s.action_type for s in view.specs}
    if ActionType.BROWSER_BACK in fixed:
        out.append(("back_and_resume", ""))
    if ActionType.REFRESH in fixed:
        out.append(("reload_and_resume", ""))
    for role in SUBMIT_ROLES:
        for spec in view.controls_with_role(role):
            identity = control_identity(spec)
            if any(
                s.action_type is ActionType.RAPID_CLICK and control_identity(s) == identity
                for s in view.specs
            ):
                out.append(("double_submit", identity))
    if view.controls_with_role("add"):
        out.append(("collection_churn", ""))
    for identity, actions in fields.items():
        for action in actions:
            if action.action_type is ActionType.SELECT and "option" in (action.params or {}):
                out.append(("choose_option", f"{identity}|{action.params['option']}"))
    if pairwise:
        out += _pairs(fields)
    if browse and (paging_controls(view) or view.controls_with_role("search_or_filter")):
        out.append(("browse_list", ""))
        out += [("browse_list", control_identity(s))
                for s in view.controls_with_role("search_or_filter")]
    return out


def _select_options(actions: list[ActionSpec]) -> list[str]:
    return [
        str(a.params["option"])
        for a in actions
        if a.action_type is ActionType.SELECT and "option" in (a.params or {})
    ]


def _pairs(fields: dict[str, list[ActionSpec]]) -> list[tuple[str, str]]:
    """Every pair of a select's option with another field's deviation: that field's deviant
    values, or (each unordered pair of selects once) its options."""
    out: list[tuple[str, str]] = []
    names = list(fields)
    for i, first in enumerate(names):
        for option in _select_options(fields[first]):
            for j, second in enumerate(names):
                if second == first:
                    continue
                others = _select_options(fields[second])
                if others:
                    if j > i:
                        out += [("combine", f"{first}|{option}&{second}|{o}") for o in others]
                    continue
                categories = {_category(a) for a in fields[second]}
                out += [
                    ("combine", f"{first}|{option}&{second}|{c}")
                    for c in INVALID_CATEGORIES
                    if c in categories
                ]
    return out
