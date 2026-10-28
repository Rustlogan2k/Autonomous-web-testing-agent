"""The archive of abstract states the hybrid tester has reached (plan §3 step 4).

Go-Explore's two remedies [ecoffet2021first]: remember every distinct state, and return to a
state before exploring from it. A cell keeps the shortest walk known to reach it, written
as scripted steps the oracle's replayer already understands (`ScriptedPolicy._match`), so
"return" is an exact replay in a fresh browser context.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..envs.types import ActionSpec, ActionType
from ..evaluation.scripted import ScriptedPolicy


def step_for(spec: ActionSpec, specs: list[ActionSpec] | None = None) -> dict:
    """A scripted step that resolves back to `spec` on a page offering the same control.

    A control without an id is named by a positional selector (`... >> nth=5`, or a chain
    of `nth-of-type`), which points at another control once the page gains or loses an
    element: a list that grew by one owner, a board added. Given the page's `specs`, the
    step also records the control's label and which of the same-labelled controls it was,
    so `resolve_step` can find it when the position has moved.
    """
    step: dict = {"type": spec.action_type.value}
    if spec.selector:
        if spec.selector.startswith('[id="') and spec.selector.endswith('"]'):
            step["id"] = spec.selector[5:-2]
        else:
            step["selector"] = spec.selector
    params = spec.params or {}
    for key in ("category", "press_enter", "option", "direction", "preset", "n"):
        if key in params:
            step.setdefault("params", {})[key] = params[key]
    if spec.action_type in (ActionType.CLICK, ActionType.RAPID_CLICK) and not spec.selector:
        step["text"] = spec.element_id
    if "selector" in step and spec.element_id.strip() and specs is not None:
        step["label"] = spec.element_id
        same = [s for s in specs if _labelled_as(step, s)]
        step["label_nth"] = next((i for i, s in enumerate(same) if s.index == spec.index), 0)
    return step


def _labelled_as(step: dict, spec: ActionSpec) -> bool:
    if spec.action_type is not ActionType(step["type"]):
        return False
    if spec.element_id.strip().lower() != step["label"].strip().lower():
        return False
    constraints = step.get("params") or {}
    return not any((spec.params or {}).get(key) != value for key, value in constraints.items())


def resolve_step(step: dict, specs: list[ActionSpec]) -> int | None:
    """The action index `step` names on this page, or None.

    The exact match (`ScriptedPolicy._match`) wins when it is the same control, or when the
    step has no label to check it by. A positional selector that now lands on a control with
    another label has drifted; the label then decides, taking the same-labelled control the
    step recorded (or the only one).
    """
    exact = ScriptedPolicy._match(step, specs)  # noqa: SLF001 - the oracle's own matcher
    if "label" not in step:
        return exact
    if exact is not None:
        spec = next(s for s in specs if s.index == exact)
        if _labelled_as(step, spec):
            return exact
    same = [s for s in specs if _labelled_as(step, s)]
    nth = int(step.get("label_nth", 0))
    if nth < len(same):
        return same[nth].index
    return same[0].index if len(same) == 1 else None


@dataclass
class Cell:
    key: str
    trace: list[dict]
    kind: str
    depth: int
    visits: int = 0
    returns: int = 0
    failed_returns: int = 0
    #: (operator, target) pairs already applied from this cell
    tried: set[tuple[str, str]] = field(default_factory=set)
    candidates: int = 0
    first_seen_step: int = 0
    #: Actions from this cell after which the browser reported an error (console, page or
    #: failed request); see `Archive.failure_bonus`.
    failures: int = 0

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind,
            "depth": self.depth,
            "visits": self.visits,
            "returns": self.returns,
            "failed_returns": self.failed_returns,
            "tried": sorted(f"{op}:{target}" for op, target in self.tried),
            "candidates": self.candidates,
            "trace_length": len(self.trace),
            "first_seen_step": self.first_seen_step,
        }


class Archive:
    def __init__(self, seed: int = 0, *, episode_steps: int = 40, depth_bias: float = 0.0,
                 failure_bonus: float = 0.0) -> None:
        self.cells: dict[str, Cell] = {}
        #: Extra weight for cells where errors have followed actions (0 = none): code that
        #: already misbehaves is likelier to misbehave again nearby.
        self.failure_bonus = failure_bonus
        #: Steps a return must leave free in the episode for the operator and exploration.
        self.min_room = 12
        self.episode_steps = episode_steps
        #: Preference for deeper cells (0 = none): faults sit many steps into a workflow.
        self.depth_bias = depth_bias
        self._rng = np.random.default_rng(seed)

    def __len__(self) -> int:
        return len(self.cells)

    def visit(self, key: str, trace: list[dict], kind: str, step: int) -> tuple[Cell, bool]:
        """Record a visit; keep the shorter walk when a cell is reached a new way."""
        cell = self.cells.get(key)
        if cell is None:
            cell = Cell(key, list(trace), kind, len(trace), first_seen_step=step)
            self.cells[key] = cell
            created = True
        else:
            created = False
            if len(trace) < len(cell.trace):
                cell.trace, cell.depth = list(trace), len(trace)
        cell.visits += 1
        return cell, created

    def rekey(self, rename) -> dict[str, str]:
        """Rename every cell by `rename(key)`, merging cells that now share a key.

        For keys made before a URL position was known to hold ids (`retemplate_key`). The
        merged cell keeps the shorter walk and the sum of the counts. A renamed cell's
        failed returns are dropped: they were returns to a key the page no longer produced,
        not walks that stopped working. Returns {old key: new key} for the keys that changed.
        """
        changed: dict[str, str] = {}
        cells: dict[str, Cell] = {}
        for key, cell in self.cells.items():
            new = rename(key)
            if new != key:
                changed[key] = new
                cell.failed_returns = 0
            cell.key = new
            kept = cells.get(new)
            if kept is None:
                cells[new] = cell
                continue
            if len(cell.trace) < len(kept.trace):
                kept.trace, kept.depth = cell.trace, cell.depth
            kept.visits += cell.visits
            kept.returns += cell.returns
            kept.failed_returns = min(kept.failed_returns, cell.failed_returns)
            kept.tried |= cell.tried
            kept.candidates += cell.candidates
            kept.failures += cell.failures
            kept.first_seen_step = min(kept.first_seen_step, cell.first_seen_step)
        self.cells = cells
        return changed

    def weight(self, cell: Cell, untried: int) -> float:
        """Count-based selection weight: rarely visited cells with untried operators first.

        Returns that keep failing (the walk no longer reaches the cell) are discounted, so a
        cell that cannot be reproduced stops absorbing the budget.
        """
        novelty = 1.0 / math.sqrt(1.0 + cell.visits)
        opportunity = 1.0 + min(untried, 10) / 5.0
        reliability = 1.0 / (1.0 + 2.0 * cell.failed_returns)
        room = 1.0 if self.episode_steps - cell.depth >= self.min_room else 0.1
        depth = 1.0 + self.depth_bias * min(cell.depth, 30) / 30.0
        failing = 1.0 + self.failure_bonus * min(cell.failures, 3) / 3.0
        return novelty * opportunity * reliability * room * depth * failing

    def choose(self, weights: dict[str, float]) -> Cell | None:
        keys = [k for k, w in weights.items() if w > 0]
        if not keys:
            return None
        values = np.array([weights[k] for k in keys], dtype=np.float64)
        return self.cells[keys[int(self._rng.choice(len(keys), p=values / values.sum()))]]

    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for cell in self.cells.values():
            kinds[cell.kind] = kinds.get(cell.kind, 0) + 1
        return {
            "cells": len(self.cells),
            "by_kind": kinds,
            "max_depth": max((c.depth for c in self.cells.values()), default=0),
            "returns": sum(c.returns for c in self.cells.values()),
            "failed_returns": sum(c.failed_returns for c in self.cells.values()),
        }
