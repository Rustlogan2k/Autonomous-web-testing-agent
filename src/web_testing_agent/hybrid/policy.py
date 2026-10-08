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
from .invariants import Candidate, InvariantChecker
from .labels import control_identity
from .operators import DEVIANT_OPERATORS, Operator, options

NO_OP_INDEX = 0
#: Free steps between probes: after an operator finishes, explore this many steps from
#: where it landed, then decide again there (several probes per episode, not one).
EXPLORE_BETWEEN_PROBES = 6
#: What a finished operator reports to the planner, besides invariant candidates (always 1):
#: - "alert": the page shows an alert (development rounds 1-7), else new cells;
#: - "novelty": new cells only, not counting a known cell that merely shows an alert;
#: - "surprise": a response other than the expected one (a deviant probe accepted without a
#:   message, or a valid one refused), else as "novelty". A validation message after
#:   invalid input is the application working, so "alert" rewards the wrong thing.
OUTCOMES = ("alert", "novelty", "surprise")
#: Free exploration's preference among roles (the operators do the targeted probing).
ROLE_WEIGHTS = {
    "submit": 3.0,
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

    #: Choose a technique (operator family) first, then its target: one candidate per family,
    #: so a family's chance does not grow with how many targets the page gives it.
    family_choice = False

    def __init__(self, archive: Archive, seed: int = 0) -> None:
        self.archive = archive
        self.rng = np.random.default_rng(seed + 1)

    def candidates(self, cell: Cell, choices: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """The choices to decide among: all of them, or with `family_choice` one per family
        (an untried target if the family has one), families in a stable order."""
        if not self.family_choice:
            return choices
        families: dict[str, list[tuple[str, str]]] = {}
        for choice in choices:
            families.setdefault(choice[0], []).append(choice)
        out = []
        for name in sorted(families):
            members = families[name]
            pool = [c for c in members if c not in cell.tried] or members
            out.append(pool[int(self.rng.integers(len(pool)))])
        return out

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
        choices = self.candidates(cell, choices)
        untried = [c for c in choices if c not in cell.tried]
        pool = untried or choices
        operator, target = pool[int(self.rng.integers(len(pool)))]
        return Decision(operator, target)

    def register(self, cell: Cell, choices: list[tuple[str, str]], view: PageView) -> None:
        """Called once per cell with the page it was first seen on; learning planners cache
        what they need from it."""

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
        explore: str = "weighted",
        depth_bias: float = 0.0,
        outcome: str = "alert",
        pairwise: bool = False,
        family_choice: bool = False,
        form_submit: bool = False,
    ) -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {outcome!r}; expected one of {OUTCOMES}")
        self.outcome = outcome
        #: Development options (suite round 4): pairwise operators, technique-first choice,
        #: form-submitting buttons as submit controls. Off: the behaviour of rounds 1-3.
        self.pairwise, self.form_submit = pairwise, form_submit
        self.labeller = labeller
        self.context = context
        self.archive = Archive(seed, episode_steps=episode_steps, depth_bias=depth_bias)
        #: Free exploration: role-weighted random ("weighted") or v1's fixed feature-search
        #: score ("search"), which keeps its own visit memory over every step taken.
        self.explore = explore
        self.search = None
        if explore == "search":
            from ..rl.search import FeatureSearchPolicy

            self.search = FeatureSearchPolicy(seed=seed, epsilon=0.05)
        self.planner = (planner_factory or Planner)(self.archive, seed)
        self.planner.family_choice = family_choice
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
        self.invariants = InvariantChecker()
        self.candidates: list[Candidate] = []
        self._operator_candidates = 0
        #: (cell, control) -> times an operator submitted with it; see `_choose_submit`.
        self.submit_uses: dict[tuple[str, str], int] = {}
        #: Cells without their alert flag: a page and the same page showing a message are
        #: one state for the "novelty" and "surprise" outcomes.
        self.base_cells: set[str] = set()
        self._base_before = 0
        self._operator_alerted = False
        self.reset()

    # -- the hunt protocol ----------------------------------------------------------------------

    def reset(self) -> None:
        if getattr(self, "search", None) is not None:
            self.search.reset()
        self.phase = "start"
        self._explore_left = 0
        self.trace: list[dict] = []
        self.target: Cell | None = None
        self.replay: list[dict] = []
        self.operator: Operator | None = None
        self.decision: Decision | None = None
        self.operator_cell: Cell | None = None
        self.cells_before = len(self.archive)
        self._last_page = ""
        self._last_cell = ""
        self._last_prunable = False

    def begin_episode(self, info: dict) -> None:
        self.stats["episodes"] += 1
        self.invariants.begin_episode()
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
        # The abstract state must be unchanged too: a step that only raised a notice leaves
        # the page fingerprint alone but changes the cell, and replay must reproduce it.
        same = page == self._last_page and view.cell == self._last_cell
        if self._last_prunable and same and self.trace:
            self.trace.pop()
            self.stats["pruned_steps"] += 1
        cell, created = self.archive.visit(
            view.cell, self.trace, view.labels.kind, self.global_step
        )
        if created:
            self.stats["new_cells"] += 1
        self.base_cells.add(_without_alert(view.cell))
        if cell.key not in self.offered:
            self.offered[cell.key] = options(view, pairwise=self.pairwise)
            self.planner.register(cell, self.offered[cell.key], view)

        spec = None
        if self.phase == "explore" and self._explore_left <= 0 and self.operator is None:
            self.phase = "operate"  # the free steps are spent: decide again from here
        if self.phase == "return":
            spec = self._return_step(view, cell)
        if self.phase == "operate":
            spec = self._operator_step(view, cell)
        if spec is None:
            if self.phase != "explore":
                self._explore_left = EXPLORE_BETWEEN_PROBES
            self.phase = "explore"
            spec = self._explore_step(view, cell, observation, info)
            self._explore_left -= 1
        self.trace.append(step_for(spec))
        self._last_page = page
        self._last_cell = view.cell
        self._last_prunable = spec.action_type not in (ActionType.TYPE, ActionType.SELECT)
        self.global_step += 1
        return spec.index

    def observe_transition(
        self,
        before: dict,
        action: int,
        reward: float,
        after: dict,
        terminated: bool,
        done: bool,
    ) -> None:
        before_view, after_view = self._view(before), self._view(after)
        spec = next((s for s in before_view.specs if s.index == action), None)
        if spec is not None:
            found = self.invariants.check(
                before_view, spec, after_view, self.global_step, self.trace
            )
            for candidate in found:
                self.candidates.append(candidate)
                self._operator_candidates += 1
        if self.operator is not None and after_view.alert:
            self._operator_alerted = True
        if self.search is not None:
            self.search.observe_transition(before, action, reward, after, terminated, done)
        # An episode that ends mid-operator still reports the operator's outcome.
        if self.operator is not None and (terminated or done):
            self._finish_operator(self._view(after))

    def summary(self) -> dict:
        return {
            "policy": "hybrid",
            "archive": self.archive.summary(),
            **self.stats,
            "invariant_candidates": [c.to_dict() for c in self.candidates],
            "labeller": self.labeller.stats.to_dict() if self.labeller is not None else None,
        }

    # -- phases ---------------------------------------------------------------------------------

    def _return_step(self, view: PageView, cell: Cell) -> ActionSpec | None:
        if not self.replay:
            if self.target is not None and cell.key != self.target.key:
                self.target.failed_returns += 1
                self.stats["failed_returns"] += 1
                self._log_failure("arrived elsewhere", reached=cell.key)
            self.phase = "operate"
            return None
        step = self.replay[0]
        index = ScriptedPolicy._match(step, view.specs)  # noqa: SLF001 - the oracle's own matcher
        if index is None:
            if self.target is not None:
                self.target.failed_returns += 1
            self.stats["failed_returns"] += 1
            self._log_failure("step did not resolve", reached=cell.key, step=step)
            self.replay = []
            self.phase = "operate"
            return None
        self.replay.pop(0)
        return next(s for s in view.specs if s.index == index)

    def _log_failure(self, reason: str, *, reached: str, step: dict | None = None) -> None:
        """Keep the first few failed returns whole, for diagnosis."""
        log = self.stats.setdefault("return_failures", [])
        if len(log) < 25 and self.target is not None:
            log.append(
                {
                    "reason": reason,
                    "target": self.target.key,
                    "reached": reached,
                    "step": step,
                    "trace_length": len(self.target.trace),
                    "remaining": len(self.replay),
                }
            )

    def _operator_step(self, view: PageView, cell: Cell) -> ActionSpec | None:
        if self.operator is None:
            decision = self.planner.choose_operator(cell, self.offered.get(cell.key, []), view)
            if decision is None:
                return None
            self.decision, self.operator_cell = decision, cell
            self.operator = Operator(
                decision.operator, decision.target, choose=self._choose_submit
            )
            cell.tried.add((decision.operator, decision.target))
            counts = self.stats["operators"]
            counts[decision.operator] = counts.get(decision.operator, 0) + 1
            self.cells_before = len(self.archive)
            self._base_before = len(self.base_cells)
            self._operator_alerted = False
        spec = self.operator.next(view)
        if spec is None:
            self._finish_operator(view)
        return spec

    def _choose_submit(self, view: PageView, controls: list[ActionSpec]) -> ActionSpec:
        """Of a page's submit controls, the one operators have used least on this abstract
        page (page order breaks ties), so repeated visits take each in turn."""
        cell = view.cell
        spec = min(
            controls, key=lambda s: self.submit_uses.get((cell, control_identity(s)), 0)
        )
        key = (cell, control_identity(spec))
        self.submit_uses[key] = self.submit_uses.get(key, 0) + 1
        return spec

    def _explore_step(self, view: PageView, cell: Cell, observation=None, info=None) -> ActionSpec:
        if self.search is not None and info is not None:
            position = self.search.act(observation or {}, info)
            if 0 <= position < len(view.specs):
                return view.specs[position]
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

    def _outcome(self, view: PageView) -> float:
        if self._operator_candidates:
            return 1.0
        if self.outcome == "alert":
            new_cells = len(self.archive) - self.cells_before
            return 1.0 if view.alert else min(new_cells, 2) / 2.0
        if self.outcome == "surprise" and self.operator is not None:
            if self.operator.name in DEVIANT_OPERATORS:
                surprised = self.operator.probe_submitted and not self._operator_alerted
            else:
                surprised = self._operator_alerted
            if surprised:
                return 1.0
        return min(len(self.base_cells) - self._base_before, 2) / 2.0

    def _finish_operator(self, view: PageView) -> None:
        outcome = self._outcome(view)
        if self.operator_cell is not None and self.decision is not None:
            self.planner.feedback(self.operator_cell, self.decision, view, outcome)
        self.operator = None
        self._operator_candidates = 0
        self.phase = "explore"
        self._explore_left = EXPLORE_BETWEEN_PROBES

    def _view(self, info: dict) -> PageView:
        if self._view_cache is not None and self._view_cache[0] == id(info):
            return self._view_cache[1]
        view = observe(info, self.labeller, context=self.context, form_submit=self.form_submit)
        if self.labeller is not None and view.labels.source == "deterministic":
            self.stats["label_fallbacks"] += 1
        self._view_cache = (id(info), view)
        return view


def _without_alert(cell: str) -> str:
    """A cell key with its alert flag cleared (`PageView.cell`'s fifth field)."""
    parts = cell.split("|")
    if len(parts) >= 6:
        parts[-2] = ""
    return "|".join(parts)
