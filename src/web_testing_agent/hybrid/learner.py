"""The hybrid tester's learned decision layer: a Thompson-sampling bandit over semantic keys.

Each return-and-probe is one decision (which archived state, which operator on which
target) followed by one outcome, so a 2,000-interaction run makes about fifty decisions.
That is far too few for a neural network, and enough for a Bayesian bandit whose arms are
*meaningful and shared*: an outcome learned for "invalid email on a form page" informs the
next form page with an email field, wherever it is in the application.

Keys, from specific to general, back off to each other:
    (page kind, operator, target type)  ->  (operator, target type)  ->  (operator,)
The specific key's Beta posterior uses the more general keys' evidence, discounted, as its
prior, so a new combination starts from what similar ones did.

Rewards are the tester's own observations, never the seeded-fault oracle: the page showing
an alert, new abstract states reached, and generic-invariant candidates. Learning from the
oracle would leak the benchmark's answer key into the policy.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from .abstraction import PageView
from .archive import Archive, Cell
from .operators import INVALID_CATEGORIES
from .policy import Decision, Planner

#: Weight of each more general key's evidence in a specific key's prior.
BACKOFF = 0.3
#: Influence of the best candidate's posterior mean on which cell to return to.
CELL_VALUE_WEIGHT = 1.0


def target_type(operator: str, target: str, view: PageView | None) -> str:
    """What kind of thing an operator acts on, in words that recur across pages and apps."""
    if operator == "invalid_input":
        field_id, category = target.rsplit("|", 1)
        meaning = view.labels.meanings.get(field_id, "other") if view is not None else "other"
        return f"{meaning}:{category}"
    if operator == "edit_after_review":
        return target.rsplit("|", 1)[1]
    if operator == "combine":
        second = target.split("&", 1)[1].rsplit("|", 1)[1]
        return f"pair:{second if second in INVALID_CATEGORIES else 'option'}"
    return ""


class BanditPlanner(Planner):
    """Cells by count-based weight times learned value; operators by Thompson sampling."""

    def __init__(self, archive: Archive, seed: int = 0, prior: dict | None = None) -> None:
        super().__init__(archive, seed)
        #: key -> [successes, failures] (fractional outcomes allowed)
        self.counts: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0])
        if prior:
            for key, (s, f) in prior.items():
                self.counts[tuple(key)] = [float(s), float(f)]
        #: (cell, operator, target) -> target type, cached when the cell's page is seen
        self.ttypes: dict[tuple[str, str, str], str] = {}
        self.decisions = 0

    # -- posterior ------------------------------------------------------------------------------

    def _keys(self, kind: str, operator: str, ttype: str) -> list[tuple]:
        return [(kind, operator, ttype), ("*", operator, ttype), ("*", operator, "*")]

    def _posterior(self, kind: str, operator: str, ttype: str) -> tuple[float, float]:
        specific, *general = self._keys(kind, operator, ttype)
        alpha, beta = 1.0, 1.0
        for key in general:
            s, f = self.counts.get(key, (0.0, 0.0))
            alpha, beta = alpha + BACKOFF * s, beta + BACKOFF * f
        s, f = self.counts.get(specific, (0.0, 0.0))
        return alpha + s, beta + f

    def value(self, kind: str, operator: str, ttype: str) -> float:
        alpha, beta = self._posterior(kind, operator, ttype)
        return alpha / (alpha + beta)

    # -- the planner interface ------------------------------------------------------------------

    def register(self, cell: Cell, choices: list[tuple[str, str]], view: PageView) -> None:
        for operator, target in choices:
            self.ttypes[(cell.key, operator, target)] = target_type(operator, target, view)

    def _ttype(self, cell: Cell, operator: str, target: str) -> str:
        return self.ttypes.get((cell.key, operator, target)) or target_type(operator, target, None)

    def choose_cell(self, offered: dict[str, list[tuple[str, str]]]) -> Cell | None:
        weights = {}
        for key, cell in self.archive.cells.items():
            choices = offered.get(key, [])
            untried = len(set(choices) - cell.tried)
            base = self.archive.weight(cell, untried)
            best = max(
                (self.value(cell.kind, op, self._ttype(cell, op, t)) for op, t in choices),
                default=0.5,
            )
            weights[key] = base * (1.0 + CELL_VALUE_WEIGHT * best)
        return self.archive.choose(weights)

    def choose_operator(
        self, cell: Cell, choices: list[tuple[str, str]], view: PageView
    ) -> Decision | None:
        if not choices:
            return None
        self.decisions += 1
        choices = self.candidates(cell, choices)
        draws = []
        for operator, target in choices:
            alpha, beta = self._posterior(cell.kind, operator, self._ttype(cell, operator, target))
            draw = self.rng.beta(alpha, beta)
            if (operator, target) in cell.tried:
                draw *= 0.5  # repeating an identical probe is worth less than a new one
            draws.append(draw)
        operator, target = choices[int(np.argmax(draws))]
        return Decision(operator, target)

    def feedback(self, cell: Cell, decision: Decision, view: PageView, outcome: float) -> None:
        ttype = self._ttype(cell, decision.operator, decision.target)
        outcome = float(min(max(outcome, 0.0), 1.0))
        for key in self._keys(cell.kind, decision.operator, ttype):
            self.counts[key][0] += outcome
            self.counts[key][1] += 1.0 - outcome

    def state(self) -> dict:
        """The learned evidence, exportable as another run's prior (the cross-app prior)."""
        return {"|".join(k): v for k, v in sorted(self.counts.items())}
