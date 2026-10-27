"""Go-Explore with a learned return policy: which archived state to go back to.

Go-Explore [ecoffet2021first] chooses the cell to return to by a fixed count-based score
(`Archive.weight`). Here that choice is learned during the run: each episode is one decision
(return to cell c) and one outcome (how many new abstract states the episode found), and a
Bayesian linear model predicts the outcome from the cell's features:

    novelty 1/√(1+visits), returns, failed-return rate, depth, untried operators, room left
    in the episode, browser errors seen there, the cell's own past yield, its page kind.

Cells are chosen by Thompson sampling: draw weights from the posterior, return to the cell
with the highest predicted yield. The model is shared across cells, so what it learns (for
example that deep list pages keep paying, or that cells whose returns fail never do)
applies to cells it has never returned to. About one decision per episode makes a few
hundred decisions an hour: enough for a linear model with a dozen features, not for a
network.

The operators and free exploration are unchanged; this planner replaces only the cell
choice (operators: uniform among the untried, as `Planner`).
"""

from __future__ import annotations

import math
import zlib

import numpy as np

from .archive import Archive, Cell
from .policy import Planner

#: Page kinds are hashed into this many indicator features.
KIND_BUCKETS = 4
#: Prior precision of the weights and the outcome noise variance (outcomes are log1p of new
#: cells, mostly 0-2).
PRIOR_PRECISION = 1.0
NOISE_VARIANCE = 0.25


class LearnedCellPlanner(Planner):
    """Cells by Thompson sampling on a linear model of return yield; operators as `Planner`."""

    learns_cells = True

    def __init__(self, archive: Archive, seed: int = 0) -> None:
        super().__init__(archive, seed)
        self.dim = 10 + KIND_BUCKETS
        self.precision = PRIOR_PRECISION * np.eye(self.dim)
        self.weighted = np.zeros(self.dim)  # Σ x y / σ²
        #: cell -> [returns with feedback, total yield]
        self.yields: dict[str, list[float]] = {}
        self._pending: tuple[str, np.ndarray] | None = None
        self.decisions = 0
        self.observations = 0

    def features(self, cell: Cell, untried: int) -> np.ndarray:
        archive = self.archive
        returns, total = self.yields.get(cell.key, (0.0, 0.0))
        x = np.zeros(self.dim)
        x[0] = 1.0
        x[1] = 1.0 / math.sqrt(1.0 + cell.visits)
        x[2] = math.log1p(cell.returns) / 3.0
        x[3] = cell.failed_returns / (1.0 + cell.returns)
        x[4] = min(cell.depth / max(archive.episode_steps, 1), 1.0)
        x[5] = min(untried, 10) / 10.0
        x[6] = 1.0 if archive.episode_steps - cell.depth >= archive.min_room else 0.0
        x[7] = min(cell.failures, 3) / 3.0
        x[8] = total / returns if returns else 0.0
        x[9] = 1.0 if returns else 0.0
        x[10 + zlib.crc32(cell.kind.encode("utf-8")) % KIND_BUCKETS] = 1.0
        return x

    def _sample_weights(self) -> np.ndarray:
        covariance = np.linalg.inv(self.precision)
        mean = covariance @ self.weighted
        return self.rng.multivariate_normal(mean, covariance, method="cholesky")

    def choose_cell(self, offered: dict[str, list[tuple[str, str]]]) -> Cell | None:
        if not self.archive.cells:
            return None
        self.decisions += 1
        weights = self._sample_weights()
        keys, rows = [], []
        for key, cell in self.archive.cells.items():
            untried = len(set(offered.get(key, [])) - cell.tried)
            keys.append(key)
            rows.append(self.features(cell, untried))
        scores = np.stack(rows) @ weights
        best = np.flatnonzero(scores >= scores.max() - 1e-9)
        index = int(best[self.rng.integers(len(best))])
        self._pending = (keys[index], rows[index])
        return self.archive.cells[keys[index]]

    def episode_feedback(self, cell: Cell, new_cells: int) -> None:
        """The outcome of the episode that returned to `cell`: new abstract states found."""
        if self._pending is None or self._pending[0] != cell.key:
            return
        _, x = self._pending
        self._pending = None
        y = math.log1p(max(new_cells, 0))
        self.precision += np.outer(x, x) / NOISE_VARIANCE
        self.weighted += x * y / NOISE_VARIANCE
        record = self.yields.setdefault(cell.key, [0.0, 0.0])
        record[0] += 1.0
        record[1] += y
        self.observations += 1

    def state(self) -> dict:
        covariance = np.linalg.inv(self.precision)
        return {
            "decisions": self.decisions,
            "observations": self.observations,
            "weights_mean": [round(float(w), 4) for w in covariance @ self.weighted],
        }
