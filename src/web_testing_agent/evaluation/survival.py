"""Time-to-first-verified-fault, analysed as what it is: censored time-to-event data.

A run either confirms a seeded fault after `t` interactions or reaches the budget `B`
without one. The second outcome is **right-censored**: the fault was not found *by* `B`,
which is not the same as never. Three consequences shape every function here:

* A censored run is never dropped and never recoded as a failure count. Dropping it would
  leave only the successes and make every arm look fast; averaging `t` with censored runs
  set to `B` understates the true mean by an unknowable amount.
* The per-run summary used for paired comparisons is the **restricted mean time** --
  `min(t, B)` -- whose mean over runs is the area under the Kaplan-Meier curve up to `B`
  (the RMST) when there is no censoring before `B`, which is the case here by design:
  every run is observed for the full budget. It is in interaction steps, so a difference
  between arms reads directly as "steps saved".
* The **application is the unit of inference** (the pre-registration's section 6). Seeds
  are averaged within a fold first; a confidence interval resamples folds, not runs.
  Runs on one application are not independent observations of anything but that
  application.

Kaplan-Meier curves pooled over folds are *descriptive*: pooling violates the independence
the log-rank test assumes. The stratified log-rank (strata = application) is the
censoring-aware test that respects the design, and is reported as secondary.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class Run:
    """One run's outcome: `time` in interactions, `censored` when nothing was confirmed."""

    fold: str
    arm: str
    seed: int
    time: float
    censored: bool


def kaplan_meier(times: list[float], censored: list[bool]) -> list[tuple[float, float]]:
    """`[(t, S(t))]` at each event time, with S the probability of *no* fault by t.

    The "survival" being estimated is the fault's: S(t) is the fraction of runs that have
    not yet confirmed a fault after t interactions. 1 - S(t) is the discovery curve.
    """
    order = sorted(zip(times, censored, strict=True))
    at_risk = len(order)
    survival = 1.0
    curve = [(0.0, 1.0)]
    index = 0
    while index < len(order):
        t = order[index][0]
        events = removed = 0
        while index < len(order) and order[index][0] == t:
            events += 0 if order[index][1] else 1
            removed += 1
            index += 1
        if events and at_risk:
            survival *= 1.0 - events / at_risk
            curve.append((float(t), survival))
        at_risk -= removed
    return curve


def restricted_mean(times: list[float], budget: float) -> float:
    """Mean of `min(t, B)` over runs; for fully-followed runs this equals the KM RMST."""
    if not times:
        return float("nan")
    return float(np.mean([min(t, budget) for t in times]))


def discovery_rate(runs: list[Run]) -> float:
    return float(np.mean([not r.censored for r in runs])) if runs else float("nan")


def fold_means(runs: list[Run], budget: float) -> dict[tuple[str, str], float]:
    """`{(fold, arm): restricted mean time}`, seeds averaged within the fold."""
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for run in runs:
        grouped[(run.fold, run.arm)].append(min(run.time, budget))
    return {key: float(np.mean(values)) for key, values in grouped.items()}


@dataclass
class PairedComparison:
    """Arm `treatment` minus arm `control`, per fold and summarised across folds."""

    treatment: str
    control: str
    per_fold: dict[str, float]
    median: float
    mean: float
    ci_low: float
    ci_high: float
    folds_favouring_treatment: int
    n_folds: int

    def to_dict(self) -> dict:
        return {
            "treatment": self.treatment, "control": self.control,
            "per_fold_difference": {k: round(v, 2) for k, v in self.per_fold.items()},
            "median_difference": round(self.median, 2),
            "mean_difference": round(self.mean, 2),
            "bootstrap_95ci_median": [round(self.ci_low, 2), round(self.ci_high, 2)],
            "folds_favouring_treatment": self.folds_favouring_treatment,
            "n_folds": self.n_folds,
            "reading": ("negative = treatment reaches a verified fault in fewer interactions"),
        }


def paired_fold_difference(runs: list[Run], treatment: str, control: str, budget: float,
                           *, resamples: int = 10_000, seed: int = 0) -> PairedComparison:
    """The pre-registered primary statistic: median over folds of `mean(T) - mean(C)`.

    The 95% interval is a percentile bootstrap that resamples **folds** with replacement.
    With four to six folds it is coarse, and it is reported as such.
    """
    means = fold_means(runs, budget)
    folds = sorted({f for f, a in means if a == treatment} & {f for f, a in means if a == control})
    per_fold = {f: means[(f, treatment)] - means[(f, control)] for f in folds}
    values = np.array(list(per_fold.values()), dtype=float)
    if values.size == 0:
        nan = float("nan")
        return PairedComparison(treatment, control, {}, nan, nan, nan, nan, 0, 0)
    rng = np.random.default_rng(seed)
    samples = rng.choice(values, size=(resamples, values.size), replace=True)
    medians = np.median(samples, axis=1)
    return PairedComparison(
        treatment=treatment, control=control, per_fold=per_fold,
        median=float(np.median(values)), mean=float(values.mean()),
        ci_low=float(np.percentile(medians, 2.5)), ci_high=float(np.percentile(medians, 97.5)),
        folds_favouring_treatment=int((values < 0).sum()), n_folds=int(values.size))


def stratified_logrank(runs: list[Run], arm_a: str, arm_b: str) -> dict:
    """Two-arm log-rank test stratified by fold. Returns chi-square (1 df) and p-value.

    Within each application the observed-minus-expected events and their hypergeometric
    variances are computed at every distinct event time, then summed across applications,
    so no comparison is ever made between runs on different applications.
    """
    observed_minus_expected = 0.0
    variance = 0.0
    for fold in sorted({r.fold for r in runs}):
        stratum = [r for r in runs if r.fold == fold and r.arm in (arm_a, arm_b)]
        event_times = sorted({r.time for r in stratum if not r.censored})
        for t in event_times:
            at_risk = [r for r in stratum if r.time >= t]
            n = len(at_risk)
            n_a = sum(1 for r in at_risk if r.arm == arm_a)
            d = sum(1 for r in at_risk if r.time == t and not r.censored)
            d_a = sum(1 for r in at_risk if r.time == t and not r.censored and r.arm == arm_a)
            if n <= 1:
                continue
            observed_minus_expected += d_a - d * n_a / n
            variance += d * (n_a / n) * (1 - n_a / n) * (n - d) / (n - 1)
    if variance <= 0:
        return {"arm_a": arm_a, "arm_b": arm_b, "chi2": None, "p_value": None,
                "note": "no events in the compared arms"}
    chi2 = observed_minus_expected ** 2 / variance
    p_value = math.erfc(math.sqrt(chi2 / 2.0))  # survival function of chi-square, 1 df
    return {"arm_a": arm_a, "arm_b": arm_b, "chi2": round(chi2, 4), "p_value": round(p_value, 5),
            "observed_minus_expected_a": round(observed_minus_expected, 3),
            "reading": "positive O-E for arm_a = arm_a confirms faults sooner than expected"}


def arm_summary(runs: list[Run], budget: float) -> dict[str, dict]:
    """Per-arm descriptive numbers: discovery rate, restricted mean time, pooled KM curve."""
    out: dict[str, dict] = {}
    for arm in sorted({r.arm for r in runs}):
        subset = [r for r in runs if r.arm == arm]
        times = [r.time for r in subset]
        out[arm] = {
            "runs": len(subset),
            "verified_runs": sum(not r.censored for r in subset),
            "discovery_rate": round(discovery_rate(subset), 4),
            "restricted_mean_time": round(restricted_mean(times, budget), 1),
            "median_time_uncensored": (round(float(np.median([r.time for r in subset if not r.censored])), 1)
                                       if any(not r.censored for r in subset) else None),
            "km_curve_pooled": [[round(t, 1), round(s, 4)] for t, s in
                                kaplan_meier(times, [r.censored for r in subset])],
        }
    return out
