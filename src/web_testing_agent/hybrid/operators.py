"""Test operators: the moves the hybrid tester applies at a chosen state (plan §4).

Each operator is a short, adaptive sequence of the environment's own actions, chosen from
standard test-design techniques rather than from any application's faults:

- equivalence partitioning / boundary values: `invalid_input` enters one field's boundary,
  empty or wrong-format value while the others are valid, then submits;
- state-transition testing: `edit_after_review`, `back_and_resume`, `reload_and_resume`,
  `double_submit`, `collection_churn`;
- `complete_step` is the valid path through one step, needed to get deep at all.

An operator never sees the oracle; it only says which action to take next, step by step,
reading the page's labels, and stops when its sequence is done.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..envs.types import ActionSpec, ActionType
from .abstraction import PageView
from .labels import control_identity

SUBMIT_ROLES = ("save", "advance", "commit")
INVALID_CATEGORIES = ("empty_string", "type_mismatch", "boundary_min", "boundary_max")
#: Operator names, frozen with the plan: the evaluation's decision layer chooses among these.
OPERATORS = (
    "complete_step",
    "invalid_input",
    "edit_after_review",
    "back_and_resume",
    "reload_and_resume",
    "double_submit",
    "collection_churn",
)
MAX_OPERATOR_STEPS = 8


def _category(spec: ActionSpec) -> str:
    return str((spec.params or {}).get("category", ""))


def _first(specs: list[ActionSpec], kind: ActionType) -> ActionSpec | None:
    return next((s for s in specs if s.action_type is kind), None)


def fill_step(view: PageView, *, skip: str = "") -> ActionSpec | None:
    """The next valid entry for an empty text field (selects already hold a value)."""
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


def submit_step(view: PageView) -> ActionSpec | None:
    for role in SUBMIT_ROLES:
        controls = view.controls_with_role(role)
        if controls:
            return controls[0]
    return None


@dataclass
class Operator:
    """One application of an operator, with its own progress through its sequence."""

    name: str
    target: str = ""
    steps: int = 0
    stage: int = 0
    done: bool = False
    log: list[str] = field(default_factory=list)

    def next(self, view: PageView) -> ActionSpec | None:
        if self.done or self.steps >= MAX_OPERATOR_STEPS:
            self.done = True
            return None
        spec = getattr(self, f"_{self.name}")(view)
        if spec is None:
            self.done = True
        else:
            self.steps += 1
            self.log.append(spec.description)
        return spec

    # -- the operators ------------------------------------------------------------------

    def _complete_step(self, view: PageView) -> ActionSpec | None:
        if self.stage == 0:
            spec = fill_step(view)
            if spec is not None:
                return spec
            self.stage = 1
            return submit_step(view)
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
            return submit_step(view)
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
            return submit_step(view)
        if self.stage == 3:
            self.stage = 4
            controls = view.controls_with_role("commit")
            return controls[0] if controls else None
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
            return submit_step(view)
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
            return submit_step(view)
        return None


def options(view: PageView) -> list[tuple[str, str]]:
    """Every (operator, target) this page offers, in a stable order."""
    out: list[tuple[str, str]] = []
    fields = view.fields()
    if fields or submit_step(view) is not None:
        out.append(("complete_step", ""))
    for identity, actions in fields.items():
        categories = {_category(a) for a in actions}
        out += [("invalid_input", f"{identity}|{c}") for c in INVALID_CATEGORIES if c in categories]
    edits = view.controls_with_role("edit")
    if edits and view.controls_with_role("commit"):
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
    return out
