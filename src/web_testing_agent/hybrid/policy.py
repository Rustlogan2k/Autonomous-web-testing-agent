"""The hybrid tester as a hunt policy (plan §3 steps 4-6).

Each episode is one return-and-probe: choose an archived state, replay the walk that
reaches it, apply one test operator there, then explore freely for the rest of the episode,
adding every new abstract state to the archive. It implements the same protocol as every
v1 arm (`benchmark.hunt.run_hunt`), so the oracle, the budget and verification are
untouched; every replayed step is an ordinary interaction and counts toward the budget.

The decision layer is pluggable (`Planner`): the default chooses uniformly among untried
operators at a count-weighted cell, which is the "hybrid without learning" arm.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..envs.types import ActionSpec, ActionType
from ..evaluation.scripted import ScriptedPolicy
from .abstraction import PageView, observe
from .archive import Archive, Cell, step_for
from .operators import Operator, options

NO_OP_INDEX = 0
#: Free exploration's preference among roles (the operators do the targeted probing).
ROLE_WEIGHTS = {
    "advance": 3.0,
    "save": 3.0,
    "commit": 2.5,
    "open": 2.5,
    "add": 2.0,
    "edit": 1.5,
    "remove": 1.0,
    "search_or_filter": 1.5,
    "navigate": 1.2,
    "back": 0.6,
    "help": 0.4,
    "other": 1.0,
    "field": 2.0,
}
FIXED_WEIGHT = 0.15


@dataclass
class Decision:
    operator: str
    target: str


class Planner:
    """Where to return and what to try: count-weighted cells, uniform untried operators."""

    def __init__(self, archive: Archive, seed: int = 0) -> None:
        self.archive = archive
        self.rng = np.random.default_rng(seed + 1)

    def choose_cell(self, offered: dict[str, list[tuple[str, str]]]) -> Cell | None:
        weights = {
            key: self.archive.weight(cell, len(set(offered.get(key, [])) - cell.tried))
            for key, cell in self.archive.cells.items()
        }
        return self.archive.choose(weights)

    def choose_operator(
        self, cell: Cell, choices: list[tuple[str, str]], view: PageView
    ) -> Decision | None:
        if not choices:
            return None
        untried = [c for c in choices if c not in cell.tried]
        pool = untried or choices
        operator, target = pool[int(self.rng.integers(len(pool)))]
        return Decision(operator, target)

    def feedback(self, cell: Cell, decision: Decision, view: PageView, outcome: float) -> None:
        """Learning planners update here; this one does not learn."""


class HybridPolicy:
    def __init__(
        self,
        *,
        labeller=None,
        context: str = "",
        seed: int = 0,
        planner_factory=None,
        episode_steps: int = 40,
    ) -> None:
        self.labeller = labeller
        self.context = context
        self.archive = Archive(seed, episode_steps=episode_steps)
        self.planner = (planner_factory or Planner)(self.archive, seed)
        self.rng = np.random.default_rng(seed)
        self.offered: dict[str, list[tuple[str, str]]] = {}
        self.global_step = 0
        self.stats = {
            "episodes": 0,
            "returns": 0,
            "failed_returns": 0,
            "operators": {},
            "new_cells": 0,
            "label_fallbacks": 0,
            "pruned_steps": 0,
        }
        self._view_cache: tuple[int, PageView] | None = None
        self.reset()

    # -- the hunt protocol ----------------------------------------------------------------------

    def reset(self) -> None:
        self.phase = "start"
        self.trace: list[dict] = []
        self.target: Cell | None = None
        self.replay: list[dict] = []
        self.operator: Operator | None = None
        self.decision: Decision | None = None
        self.operator_cell: Cell | None = None
        self.cells_before = len(self.archive)
        self._last_page = ""
        self._last_prunable = False

    def begin_episode(self, info: dict) -> None:
        self.stats["episodes"] += 1
        self.target = self.planner.choose_cell(self.offered) if len(self.archive) else None
        self.replay = list(self.target.trace) if self.target is not None else []
        self.phase = "return" if self.replay else "operate"
        if self.target is not None:
            self.target.returns += 1
            self.stats["returns"] += 1

    def act(self, observation: dict, info: dict) -> int:  # noqa: ARG002 - reads no pixels
        view = self._view(info)
        page = str(info.get("state_key", ""))
        # A step after which the page did not change at all is dropped from the walk the
        # archive stores, so returns spend the budget on steps that matter. Entered values
        # are always kept: they can matter without changing the page.
        if self._last_prunable and page == self._last_page and self.trace:
            self.trace.pop()
            self.stats["pruned_steps"] += 1
        cell, created = self.archive.visit(
            view.cell, self.trace, view.labels.kind, self.global_step
        )
        if created:
            self.stats["new_cells"] += 1
        self.offered.setdefault(cell.key, options(view))

        spec = None
        if self.phase == "return":
            spec = self._return_step(view, cell)
        if self.phase == "operate":
            spec = self._operator_step(view, cell)
        if spec is None:
            self.phase = "explore"
            spec = self._explore_step(view, cell)
        self.trace.append(step_for(spec))
        self._last_page = page
        self._last_prunable = spec.action_type not in (ActionType.TYPE, ActionType.SELECT)
        self.global_step += 1
        return spec.index

    def observe_transition(
        self,
        before: dict,
        action: int,
        reward: float,
        after: dict,  # noqa: ARG002
        terminated: bool,
        done: bool,
    ) -> None:
        # An episode that ends mid-operator still reports the operator's outcome.
        if self.operator is not None and (terminated or done):
            self._finish_operator(self._view(after))

    def summary(self) -> dict:
        return {
            "policy": "hybrid",
            "archive": self.archive.summary(),
            **self.stats,
            "labeller": self.labeller.stats.to_dict() if self.labeller is not None else None,
        }

    # -- phases ---------------------------------------------------------------------------------

    def _return_step(self, view: PageView, cell: Cell) -> ActionSpec | None:
        if not self.replay:
            if self.target is not None and cell.key != self.target.key:
                self.target.failed_returns += 1
                self.stats["failed_returns"] += 1
            self.phase = "operate"
            return None
        step = self.replay[0]
        index = ScriptedPolicy._match(step, view.specs)  # noqa: SLF001 - the oracle's own matcher
        if index is None:
            if self.target is not None:
                self.target.failed_returns += 1
            self.stats["failed_returns"] += 1
            self.replay = []
            self.phase = "operate"
            return None
        self.replay.pop(0)
        return next(s for s in view.specs if s.index == index)

    def _operator_step(self, view: PageView, cell: Cell) -> ActionSpec | None:
        if self.operator is None:
            decision = self.planner.choose_operator(cell, self.offered.get(cell.key, []), view)
            if decision is None:
                return None
            self.decision, self.operator_cell = decision, cell
            self.operator = Operator(decision.operator, decision.target)
            cell.tried.add((decision.operator, decision.target))
            counts = self.stats["operators"]
            counts[decision.operator] = counts.get(decision.operator, 0) + 1
            self.cells_before = len(self.archive)
        spec = self.operator.next(view)
        if spec is None:
            self._finish_operator(view)
        return spec

    def _explore_step(self, view: PageView, cell: Cell) -> ActionSpec:
        """Weighted choice favouring controls not yet tried in this cell."""
        tried = {(t.get("type"), t.get("id"), str(t.get("params"))) for t in self.trace}
        weights = []
        for spec in view.specs:
            if spec.action_type is ActionType.NO_OP:
                weights.append(0.0)
                continue
            if spec.action_type in (ActionType.TYPE, ActionType.SELECT):
                weight = ROLE_WEIGHTS["field"]
            elif spec.action_type in (ActionType.CLICK, ActionType.RAPID_CLICK):
                weight = ROLE_WEIGHTS.get(view.role_of(spec), 1.0)
                if spec.action_type is ActionType.RAPID_CLICK:
                    weight *= 0.3
            else:
                weight = FIXED_WEIGHT
            step = step_for(spec)
            if (step.get("type"), step.get("id"), str(step.get("params"))) in tried:
                weight *= 0.25
            weights.append(weight)
        values = np.array(weights, dtype=np.float64)
        if values.sum() <= 0:
            return view.specs[NO_OP_INDEX]
        return view.specs[int(self.rng.choice(len(values), p=values / values.sum()))]

    def _finish_operator(self, view: PageView) -> None:
        new_cells = len(self.archive) - self.cells_before
        outcome = 1.0 if view.alert else min(new_cells, 2) / 2.0
        if self.operator_cell is not None and self.decision is not None:
            self.planner.feedback(self.operator_cell, self.decision, view, outcome)
        self.operator = None
        self.phase = "explore"

    def _view(self, info: dict) -> PageView:
        if self._view_cache is not None and self._view_cache[0] == id(info):
            return self._view_cache[1]
        view = observe(info, self.labeller, context=self.context)
        if self.labeller is not None and view.labels.source == "deterministic":
            self.stats["label_fallbacks"] += 1
        self._view_cache = (id(info), view)
        return view
