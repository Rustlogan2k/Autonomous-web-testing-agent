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


def step_for(spec: ActionSpec) -> dict:
    """A scripted step that resolves back to `spec` on a page offering the same control."""
    step: dict = {"type": spec.action_type.value}
    if spec.selector:
        if spec.selector.startswith('[id="') and spec.selector.endswith('"]'):
            step["id"] = spec.selector[5:-2]
        else:
            step["selector"] = spec.selector
    params = spec.params or {}
    for key in ("category", "option", "direction", "preset", "n"):
        if key in params:
            step.setdefault("params", {})[key] = params[key]
    if spec.action_type in (ActionType.CLICK, ActionType.RAPID_CLICK) and not spec.selector:
        step["text"] = spec.element_id
    return step


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
    def __init__(self, seed: int = 0, *, episode_steps: int = 40) -> None:
        self.cells: dict[str, Cell] = {}
        #: Steps a return must leave free in the episode for the operator and exploration.
        self.min_room = 12
        self.episode_steps = episode_steps
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

    def weight(self, cell: Cell, untried: int) -> float:
        """Count-based selection weight: rarely visited cells with untried operators first.

        Returns that keep failing (the walk no longer reaches the cell) are discounted, so a
        cell that cannot be reproduced stops absorbing the budget.
        """
        novelty = 1.0 / math.sqrt(1.0 + cell.visits)
        opportunity = 1.0 + min(untried, 10) / 5.0
        reliability = 1.0 / (1.0 + 2.0 * cell.failed_returns)
        room = 1.0 if self.episode_steps - cell.depth >= self.min_room else 0.1
        return novelty * opportunity * reliability * room

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
