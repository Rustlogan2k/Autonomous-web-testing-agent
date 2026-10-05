"""The contextual diagnostic's analysis and report (pre-registration §5).

Reads `probe_eval.json.gz` (frozen-weight scores, `probe_eval.py`) and the hunt results of
one run directory, computes diagnostics 1-7 and applies the pre-registered decision rules.
Its output is `reports/contextual_diagnostic.{md,json}` and nothing else: no number here is
ever pooled with the primary experiment's.

Analysis choices the pre-registration leaves open, fixed here before any diagnostic result
exists (this file is committed before the first diagnostic run):

- **Units.** A probe metric is computed per (arm, seed, variant) and seeds are averaged
  within a variant. "Training accuracy" is the mean over the four training variants;
  "accuracy on S3-S5" in H-app is the mean over those three variants, while H-lex's and
  H-ctx's "stays at or above 70%" requires each of them. The within-label AUROC (rule 2),
  the decoy rate and the Kendall-tau contrast are pooled over the eight held-out variants.
- **Intervals.** 95% percentile bootstrap, 10,000 resamples, two-way: the probe states of
  each variant are resampled with replacement within that variant, and the three seeds
  (one pretrained network per arm and seed, shared by every variant) are resampled with
  replacement. The same draws are used for every arm, so arm differences are paired.
- **Verdicts.** §5 states when a hypothesis is *supported*. A condition is checked on its
  point estimate (or, where the rule says so, on its interval). A hypothesis is
  *supported* when every condition of some branch holds; *not supported* when every
  branch has a condition that fails with its whole interval on the failing side; and
  *inconclusive* otherwise. The rules are applied to `ctx_full`; the other arms are
  reported as calibration.
- **Labels.** "The same label" in diagnostics 2 and 3 is the action's type, its semantic
  label and its value (`probe_eval.label_key`). An action is *useful* in a state when its
  executed effect there is `progress`.

    python -m web_testing_agent.ctxbench.analyze --out-dir runs/ctx-main
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..evaluation.survival import restricted_mean
from ..experiments.transfer import REPO_ROOT
from .runner import ARMS, HELD_OUT, SEEDS, TRAINING, CtxConfig

RESAMPLES = 10_000
CONTEXT_AUROC = 0.70
DECOY_VARIANTS = ("ctx_v0_s1_d1", "ctx_vu1_s5_d1")
STRUCTURE_VARIANTS = ("ctx_v0_s3_d0", "ctx_v0_s4_d0", "ctx_v0_s5_d0")
VOCAB_SYNONYM = "ctx_vs_s1_d0"
VOCAB_UNSEEN = ("ctx_vu1_s1_d0", "ctx_vu2_s1_d0")
VU2 = "ctx_vu2_s1_d0"
TAU_MIN_SHARED = 3
TAU_CONTRAST = 0.20
REPORT = REPO_ROOT / "reports" / "contextual_diagnostic"


# -- per-state quantities ------------------------------------------------------------------


def _auroc(positive: np.ndarray, negative: np.ndarray) -> float:
    """P(score of a positive > score of a negative), ties counted half."""
    diff = positive[:, None] - negative[None, :]
    return float(((diff > 0).sum() + 0.5 * (diff == 0).sum()) / diff.size)


def _kendall_tau(a: np.ndarray, b: np.ndarray) -> float | None:
    """Kendall's tau-b; None when either ordering is constant."""
    n = len(a)
    i, j = np.triu_indices(n, 1)
    da, db = np.sign(a[i] - a[j]), np.sign(b[i] - b[j])
    concordant = float((da * db).sum())
    ties_a, ties_b = float((da == 0).sum()), float((db == 0).sum())
    pairs = float(len(i))
    denominator = np.sqrt((pairs - ties_a) * (pairs - ties_b))
    return None if denominator == 0 else concordant / denominator


@dataclass
class VariantTables:
    """Everything one variant contributes, for one arm, as arrays over its probe states."""

    n_states: int
    # (seeds, states); NaN where the state has no progress action (or no decoy).
    top1: np.ndarray
    auroc: np.ndarray
    decoy_greedy: np.ndarray
    # Within-label pairs: state indices (useful, not useful) and per-seed outcome (seeds, P).
    label_pairs: tuple[np.ndarray, np.ndarray, np.ndarray]
    # Kendall-tau state pairs: indices, per-seed tau (seeds, P), and whether useful sets differ.
    tau_pairs: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]
    variance: list[dict] = field(default_factory=list)


def variant_tables(states: list[dict], scores_by_seed: list[dict]) -> VariantTables:
    n, seeds = len(states), len(scores_by_seed)
    top1 = np.full((seeds, n), np.nan)
    auroc = np.full((seeds, n), np.nan)
    decoy = np.full((seeds, n), np.nan)
    for k, state in enumerate(states):
        effects = np.array(state["effects"])
        progress = effects == "progress"
        has_decoy = bool((effects == "decoy").any())
        for s, scores in enumerate(scores_by_seed):
            q = np.asarray(scores[state["probe_id"]], dtype=np.float64)
            greedy = int(np.argmax(q))
            if progress.any():
                top1[s, k] = float(progress[greedy])
                if (~progress).any():
                    auroc[s, k] = _auroc(q[progress], q[~progress])
            if has_decoy:
                decoy[s, k] = float(effects[greedy] == "decoy")

    # Diagnostic 2: for each label, every (state where useful, state where not) pair.
    occurrences: dict[str, list[tuple[int, int, bool]]] = {}
    for k, state in enumerate(states):
        for row, (label, effect) in enumerate(zip(state["labels"], state["effects"], strict=True)):
            occurrences.setdefault(label, []).append((k, row, effect == "progress"))
    useful_i, other_i, outcome = [], [], []
    for rows in occurrences.values():
        good = [(k, r) for k, r, u in rows if u]
        bad = [(k, r) for k, r, u in rows if not u]
        for kg, rg in good:
            for kb, rb in bad:
                useful_i.append(kg)
                other_i.append(kb)
                outcome.append(
                    [
                        _cmp(
                            scores_by_seed[s][states[kg]["probe_id"]][rg],
                            scores_by_seed[s][states[kb]["probe_id"]][rb],
                        )
                        for s in range(seeds)
                    ]
                )
    label_pairs = (
        np.array(useful_i, dtype=int),
        np.array(other_i, dtype=int),
        np.array(outcome, dtype=np.float64).reshape(-1, seeds).T,
    )

    # Diagnostic 3: pairs of states sharing >= 3 labels.
    index = [{label: row for row, label in enumerate(state["labels"])} for state in states]
    useful_sets = [
        {label for label, e in zip(s["labels"], s["effects"], strict=True) if e == "progress"}
        for s in states
    ]
    ta, tb, taus, differ = [], [], [], []
    for a in range(n):
        for b in range(a + 1, n):
            shared = sorted(set(index[a]) & set(index[b]))
            if len(shared) < TAU_MIN_SHARED:
                continue
            row = []
            for s in range(seeds):
                qa = np.array(
                    [scores_by_seed[s][states[a]["probe_id"]][index[a][label]] for label in shared]
                )
                qb = np.array(
                    [scores_by_seed[s][states[b]["probe_id"]][index[b][label]] for label in shared]
                )
                tau = _kendall_tau(qa, qb)
                row.append(np.nan if tau is None else tau)
            ta.append(a)
            tb.append(b)
            taus.append(row)
            shared_set = set(shared)
            differ.append((useful_sets[a] & shared_set) != (useful_sets[b] & shared_set))
    tau_pairs = (
        np.array(ta, dtype=int),
        np.array(tb, dtype=int),
        np.array(taus, dtype=np.float64).reshape(-1, seeds).T,
        np.array(differ, dtype=bool),
    )

    variance = [_variance_shares(states, scores) for scores in scores_by_seed]
    return VariantTables(n, top1, auroc, decoy, label_pairs, tau_pairs, variance)


def _cmp(a: float, b: float) -> float:
    return 1.0 if a > b else 0.5 if a == b else 0.0


def _variance_shares(states: list[dict], scores: dict) -> dict:
    """Share of Q's variance explained by label, by state, and by label x state (residual)."""
    q, label, state = [], [], []
    for k, s in enumerate(states):
        for row, lab in enumerate(s["labels"]):
            q.append(scores[s["probe_id"]][row])
            label.append(lab)
            state.append(k)
    q = np.asarray(q, dtype=np.float64)
    total = float(((q - q.mean()) ** 2).sum())
    if total == 0:
        return {"label": 0.0, "state": 0.0, "label_x_state": 0.0}

    def between(groups: list) -> float:
        out = 0.0
        keys = np.array(groups, dtype=object)
        for g in set(groups):
            members = q[keys == g]
            out += len(members) * (members.mean() - q.mean()) ** 2
        return float(out)

    ss_label, ss_state = between(label), between(state)
    residual = max(total - ss_label - ss_state, 0.0)
    return {"label": ss_label / total, "state": ss_state / total, "label_x_state": residual / total}


# -- bootstrap -----------------------------------------------------------------------------


@dataclass
class Draws:
    """Resampling weights shared by every arm: seed counts and per-variant state counts."""

    seeds: np.ndarray  # (R, n_seeds)
    states: dict[str, np.ndarray]  # variant -> (R, n_states)

    @classmethod
    def make(
        cls, n_seeds: int, n_states: dict[str, int], resamples: int, seed: int = 20261004
    ) -> Draws:
        rng = np.random.default_rng(seed)
        seeds = rng.multinomial(n_seeds, [1 / n_seeds] * n_seeds, size=resamples).astype(np.float64)
        states = {
            v: rng.multinomial(n, [1 / n] * n, size=resamples).astype(np.float64)
            for v, n in sorted(n_states.items())
        }
        return cls(seeds, states)

    @property
    def resamples(self) -> int:
        return self.seeds.shape[0]


def _variant_mean(
    values: np.ndarray, seed_w: np.ndarray | None, state_w: np.ndarray | None
) -> np.ndarray | float:
    """Seed-averaged mean over states of a (seeds, states) table, ignoring NaN.

    Without weights: the point estimate. With weights (R, seeds) and (R, states): one value
    per resample, the weighted mean over states within each seed, then over seeds.
    """
    valid = ~np.isnan(values)
    filled = np.where(valid, values, 0.0)
    if seed_w is None:
        per_seed = np.array(
            [
                filled[s][valid[s]].mean() if valid[s].any() else np.nan
                for s in range(values.shape[0])
            ]
        )
        return float(np.nanmean(per_seed))
    num = state_w @ filled.T  # (R, seeds)
    den = state_w @ valid.T.astype(np.float64)  # (R, seeds)
    per_seed = np.divide(num, den, out=np.full_like(num, np.nan), where=den > 0)
    w = np.where(np.isnan(per_seed), 0.0, seed_w)
    return np.divide(
        (w * np.nan_to_num(per_seed)).sum(1),
        w.sum(1),
        out=np.full(w.shape[0], np.nan),
        where=w.sum(1) > 0,
    )


def _pooled_pairs(
    tables: dict[str, VariantTables],
    kind: str,
    draws: Draws | None,
    select: np.ndarray | None = None,
    chunk: int = 500,
):
    """Pooled pair statistic over variants: within-label AUROC or mean Kendall tau.

    Each pair is weighted by the product of its two states' resampling counts and by its
    seed's count, so the pooled value is the pair-level mean of the resampled data.
    """

    def parts(t: VariantTables):
        if kind == "label":
            i, j, values = t.label_pairs
            keep = np.ones(len(i), dtype=bool)
        else:
            i, j, values, differ = t.tau_pairs
            keep = (
                differ
                if select is True
                else ~differ
                if select is False
                else np.ones(len(i), dtype=bool)
            )
        return i[keep], j[keep], values[:, keep] if values.size else values

    if draws is None:
        num = den = 0.0
        for t in tables.values():
            i, j, values = parts(t)
            if len(i) == 0:
                continue
            valid = ~np.isnan(values)
            num += float(np.nansum(values))
            den += float(valid.sum())
        return num / den if den else float("nan")

    out = np.zeros(draws.resamples)
    total = np.zeros(draws.resamples)
    for variant, t in tables.items():
        i, j, values = parts(t)
        if len(i) == 0:
            continue
        valid = ~np.isnan(values)
        filled = np.where(valid, values, 0.0)
        sw = draws.states[variant]
        for start in range(0, draws.resamples, chunk):
            stop = start + chunk
            pair_w = sw[start:stop, i] * sw[start:stop, j]  # (r, P)
            seed_w = draws.seeds[start:stop]  # (r, S)
            out[start:stop] += np.einsum("rp,rs,sp->r", pair_w, seed_w, filled)
            total[start:stop] += np.einsum("rp,rs,sp->r", pair_w, seed_w, valid.astype(np.float64))
    return np.divide(out, total, out=np.full_like(out, np.nan), where=total > 0)


def _interval(samples: np.ndarray) -> list[float]:
    samples = samples[~np.isnan(samples)]
    if samples.size == 0:
        return [float("nan"), float("nan")]
    return [float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))]


# -- metrics per arm -----------------------------------------------------------------------


@dataclass
class ArmProbe:
    point: dict
    samples: dict


def arm_probe_metrics(tables: dict[str, VariantTables], draws: Draws) -> ArmProbe:
    point: dict = {"top1": {}, "auroc": {}, "variance": {}}
    samples: dict = {"top1": {}}
    for variant, t in tables.items():
        point["top1"][variant] = _variant_mean(t.top1, None, None)
        point["auroc"][variant] = _variant_mean(t.auroc, None, None)
        samples["top1"][variant] = _variant_mean(t.top1, draws.seeds, draws.states[variant])
        point["variance"][variant] = {
            k: float(np.mean([v[k] for v in t.variance]))
            for k in ("label", "state", "label_x_state")
        }

    held = {v: tables[v] for v in HELD_OUT if v in tables}
    point["within_label_auroc"] = _pooled_pairs(held, "label", None)
    samples["within_label_auroc"] = _pooled_pairs(held, "label", draws)
    for name, select in (("tau_differ", True), ("tau_agree", False)):
        point[name] = _pooled_pairs(held, "tau", None, select)
        samples[name] = _pooled_pairs(held, "tau", draws, select)
    point["tau_contrast"] = point["tau_agree"] - point["tau_differ"]
    samples["tau_contrast"] = samples["tau_agree"] - samples["tau_differ"]

    # Decoy rate: pooled over the D1 variants' probe states that contain the decoy.
    decoy_tables = [(v, tables[v]) for v in DECOY_VARIANTS if v in tables]
    point["decoy_rate"] = _pooled_states([t.decoy_greedy for _, t in decoy_tables], None, None)
    samples["decoy_rate"] = _pooled_states(
        [t.decoy_greedy for _, t in decoy_tables],
        draws.seeds,
        [draws.states[v] for v, _ in decoy_tables],
    )

    training = [v for v in TRAINING if v in tables]
    point["train_top1"] = float(np.mean([point["top1"][v] for v in training]))
    samples["train_top1"] = np.mean([samples["top1"][v] for v in training], axis=0)
    return ArmProbe(point, samples)


def _pooled_states(values: list[np.ndarray], seed_w, state_ws) -> float | np.ndarray:
    """Mean of a (seeds, states) indicator pooled over several variants' states."""
    if not values:
        return float("nan") if seed_w is None else np.full(1, np.nan)
    stacked = np.concatenate(values, axis=1)
    if seed_w is None:
        return _variant_mean(stacked, None, None)
    return _variant_mean(stacked, seed_w, np.concatenate(state_ws, axis=1))


# -- decision rules ------------------------------------------------------------------------


@dataclass
class Condition:
    text: str
    point: float
    samples: np.ndarray
    threshold: float
    direction: str  # ">=", "<=", "<", ">" applied to the point estimate
    on_interval: bool = False  # the rule is stated on the interval (e.g. "lower bound > 0.5")

    def holds(self) -> bool:
        if self.on_interval:
            low, high = _interval(self.samples)
            return {
                ">": low > self.threshold,
                "<": high < self.threshold,
                "includes": low <= self.threshold <= high,
            }[self.direction]
        return _compare(self.point, self.direction, self.threshold)

    def clearly_fails(self) -> bool:
        if self.holds():
            return False
        low, high = _interval(self.samples)
        if self.direction == "includes":
            return high < self.threshold or low > self.threshold
        if self.direction in (">=", ">"):
            return (
                _compare(high, "<", self.threshold)
                if self.direction == ">="
                else high <= self.threshold
            )
        return (
            _compare(low, ">", self.threshold) if self.direction == "<=" else low >= self.threshold
        )

    def to_dict(self) -> dict:
        return {
            "condition": self.text,
            "point": _round(self.point),
            "interval": [_round(x) for x in _interval(self.samples)],
            "threshold": self.threshold,
            "direction": self.direction,
            "holds": self.holds(),
            "clearly_fails": self.clearly_fails(),
        }


def _compare(value: float, direction: str, threshold: float) -> bool:
    if np.isnan(value):
        return False
    return {
        ">=": value >= threshold,
        "<=": value <= threshold,
        ">": value > threshold,
        "<": value < threshold,
    }[direction]


def _round(value: float) -> float | None:
    return None if value is None or np.isnan(value) else round(float(value), 4)


def verdict(branches: list[list[Condition]]) -> str:
    if any(all(c.holds() for c in branch) for branch in branches):
        return "supported"
    if all(any(c.clearly_fails() for c in branch) for branch in branches):
        return "not supported"
    return "inconclusive"


def _ratio(arm: ArmProbe, variant_or_list) -> tuple[float, np.ndarray]:
    variants = [variant_or_list] if isinstance(variant_or_list, str) else list(variant_or_list)
    # A ratio to a training accuracy of 0 is undefined (NaN): its condition stays unresolved.
    train = arm.point["train_top1"]
    point = (
        float(np.mean([arm.point["top1"][v] for v in variants])) / train
        if train > 0
        else float("nan")
    )
    numerator = np.mean([arm.samples["top1"][v] for v in variants], axis=0)
    samples = np.divide(
        numerator,
        arm.samples["train_top1"],
        out=np.full_like(numerator, np.nan),
        where=arm.samples["train_top1"] > 0,
    )
    return point, samples


def hypotheses(arms: dict[str, ArmProbe], subject: str = "ctx_full") -> dict:
    a = arms[subject]
    lex = arms["lexical_only"]
    act = arms["action_only"]

    def ratio_condition(variants, direction, threshold, text):
        point, samples = _ratio(a, variants)
        return Condition(text, point, samples, threshold, direction)

    rule2 = [
        Condition(
            "within-label AUROC >= 0.70",
            a.point["within_label_auroc"],
            a.samples["within_label_auroc"],
            CONTEXT_AUROC,
            ">=",
        ),
        Condition(
            "within-label AUROC interval lower bound > 0.50",
            a.point["within_label_auroc"],
            a.samples["within_label_auroc"],
            0.5,
            ">",
            on_interval=True,
        ),
    ]

    out = {}
    out["H-act"] = [
        [
            Condition(
                "decoy probe rate >= 0.50",
                a.point["decoy_rate"],
                a.samples["decoy_rate"],
                0.5,
                ">=",
            ),
            Condition(
                "within-label AUROC interval includes 0.50",
                a.point["within_label_auroc"],
                a.samples["within_label_auroc"],
                0.5,
                "includes",
                on_interval=True,
            ),
        ]
    ]

    lex_branch_a = [
        Condition(
            "min(top-1 VU1, VU2) / training < 0.50",
            min(_ratio(a, v)[0] for v in VOCAB_UNSEEN),
            np.minimum(*[_ratio(a, v)[1] for v in VOCAB_UNSEEN]),
            0.5,
            "<",
        )
    ]
    lex_branch_a += [
        ratio_condition(v, ">=", 0.7, f"top-1 {v} / training >= 0.70") for v in STRUCTURE_VARIANTS
    ]
    train_diff = a.samples["train_top1"] - lex.samples["train_top1"]
    vu2_diff_point = a.point["top1"][VU2] - lex.point["top1"][VU2]
    vu2_diff = a.samples["top1"][VU2] - lex.samples["top1"][VU2]
    lex_branch_b = [
        Condition(
            "ctx_full - lexical_only training top-1: interval includes 0",
            a.point["train_top1"] - lex.point["train_top1"],
            train_diff,
            0.0,
            "includes",
            on_interval=True,
        ),
        Condition("ctx_full - lexical_only top-1 on VU2 <= 0", vu2_diff_point, vu2_diff, 0.0, "<="),
    ]
    out["H-lex"] = [lex_branch_a, lex_branch_b]

    out["H-app"] = [
        [
            ratio_condition(STRUCTURE_VARIANTS, "<", 0.5, "mean top-1 S3-S5 / training < 0.50"),
            ratio_condition(VOCAB_SYNONYM, ">=", 0.7, "top-1 VS / training >= 0.70"),
        ]
    ]

    ctx = list(rule2)
    ctx.append(
        Condition(
            "decoy probe rate <= 0.25", a.point["decoy_rate"], a.samples["decoy_rate"], 0.25, "<="
        )
    )
    ctx.append(
        Condition(
            "lexical_only decoy rate - ctx_full decoy rate >= 0.25",
            lex.point["decoy_rate"] - a.point["decoy_rate"],
            lex.samples["decoy_rate"] - a.samples["decoy_rate"],
            0.25,
            ">=",
        )
    )
    ctx.append(ratio_condition(VOCAB_SYNONYM, ">=", 0.7, "top-1 VS / training >= 0.70"))
    ctx += [
        ratio_condition(v, ">=", 0.7, f"top-1 {v} / training >= 0.70") for v in STRUCTURE_VARIANTS
    ]
    for other_name, other in (("action_only", act), ("lexical_only", lex)):
        ctx.append(
            Condition(
                f"{other_name} decoy rate - ctx_full decoy rate > 0 (interval)",
                other.point["decoy_rate"] - a.point["decoy_rate"],
                other.samples["decoy_rate"] - a.samples["decoy_rate"],
                0.0,
                ">",
                on_interval=True,
            )
        )
        ctx.append(
            Condition(
                f"ctx_full - {other_name} within-label AUROC > 0 (interval)",
                a.point["within_label_auroc"] - other.point["within_label_auroc"],
                a.samples["within_label_auroc"] - other.samples["within_label_auroc"],
                0.0,
                ">",
                on_interval=True,
            )
        )
    out["H-ctx"] = [ctx]

    return {
        name: {
            "verdict": verdict(branches),
            "branches": [[c.to_dict() for c in branch] for branch in branches],
        }
        for name, branches in out.items()
    }


# -- hunts ---------------------------------------------------------------------------------


def hunt_summary(out_dir: Path, config: CtxConfig) -> dict:
    rows: dict[str, dict[str, list[dict]]] = {}
    failures = []
    for path in sorted((out_dir / "results").glob("hunt__*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        job = record["job"]
        if record.get("status") != "ok":
            failures.append(job["job_id"])
            continue
        hunt = record["hunt"]
        rows.setdefault(job["app"], {}).setdefault(job["arm"], []).append(
            {
                "seed": job["seed"],
                "time": float(hunt["time_to_first_verified"]),
                "censored": bool(hunt["censored"]),
                "verified": len(hunt["oracle"]["verified"]),
                "decoys_per_episode": record["decoys"]["per_episode"],
                "decoy_selections": record["decoys"]["selections"],
            }
        )
    out: dict = {"failures": failures, "apps": {}}
    for app, by_arm in sorted(rows.items()):
        budget = config.budget(app)
        out["apps"][app] = {}
        for arm, runs in sorted(by_arm.items()):
            out["apps"][app][arm] = {
                "runs": len(runs),
                "budget": budget,
                "restricted_mean_time": round(
                    restricted_mean([r["time"] for r in runs], budget), 1
                ),
                "discovered": sum(not r["censored"] for r in runs),
                "verified_faults_total": sum(r["verified"] for r in runs),
                "decoys_per_episode": round(
                    float(np.mean([r["decoys_per_episode"] for r in runs])), 4
                ),
            }
        for arm, row in out["apps"][app].items():
            for baseline in ("search_S", "random"):
                base = out["apps"][app].get(baseline)
                if base and arm != baseline:
                    row[f"rmst_ratio_vs_{baseline}"] = round(
                        row["restricted_mean_time"] / max(base["restricted_mean_time"], 1e-9), 3
                    )
    return out


# -- driver --------------------------------------------------------------------------------


def analyze(out_dir: Path, resamples: int = RESAMPLES) -> dict:
    with gzip.open(out_dir / "probe_eval.json.gz", "rb") as handle:
        probe_eval = json.loads(handle.read().decode("utf-8"))
    meta, scores = probe_eval["probes"], probe_eval["scores"]
    variants = [v for v in TRAINING + HELD_OUT if v in meta]
    draws = Draws.make(len(SEEDS), {v: len(meta[v]["states"]) for v in variants}, resamples)

    arms: dict[str, ArmProbe] = {}
    for arm in ARMS:
        tables = {
            v: variant_tables(meta[v]["states"], [scores[arm][str(s)][v] for s in SEEDS])
            for v in variants
        }
        arms[arm] = arm_probe_metrics(tables, draws)

    def public(arm: ArmProbe) -> dict:
        p = arm.point
        out = {
            "top1": {v: _round(x) for v, x in p["top1"].items()},
            "auroc": {v: _round(x) for v, x in p["auroc"].items()},
            "variance_shares": p["variance"],
            "train_top1": _round(p["train_top1"]),
        }
        for key in ("within_label_auroc", "tau_differ", "tau_agree", "tau_contrast", "decoy_rate"):
            out[key] = {
                "point": _round(p[key]),
                "interval": [_round(x) for x in _interval(arm.samples[key])],
            }
        out["context_sensitive_tau"] = bool(
            p["tau_contrast"] >= TAU_CONTRAST and _interval(arm.samples["tau_contrast"])[0] > 0
        )
        out["transfer_ratio"] = {
            v: _round(p["top1"][v] / p["train_top1"]) if p["train_top1"] else None
            for v in HELD_OUT
            if v in p["top1"]
        }
        return out

    payload = {
        "probe_sets": {
            v: {"sha256": meta[v]["sha256"], "states": len(meta[v]["states"])} for v in variants
        },
        "permutation": probe_eval["permutation"],
        "resamples": resamples,
        "probe": {arm: public(m) for arm, m in arms.items()},
        "hypotheses": hypotheses(arms),
        "hunts": hunt_summary(out_dir, CtxConfig()),
    }
    return payload


def render(payload: dict) -> str:
    """The report's numbers, as Markdown. Interpretation is written around it by hand."""
    lines = [
        "# Contextual-generalization diagnostic: results",
        "",
        "Secondary diagnostic, pre-registered in "
        "`docs/methodology/2026-10-04-contextual-diagnostic-preregistration.md`. "
        "Nothing here is pooled with the primary experiment.",
        "",
    ]
    perm = payload["permutation"]
    lines += [
        "## Permutation invariance (diagnostic 4)",
        "",
        f"{perm['states']} probe states × {perm['permutations']} permutations, tolerance "
        f"{perm['tolerance']}: **{'passed' if perm['passed'] else 'FAILED'}**.",
        "",
    ]
    lines += ["## Hypotheses (rules applied to `ctx_full`)", ""]
    for name, row in payload["hypotheses"].items():
        lines.append(f"- **{name}: {row['verdict']}**")
        for b, branch in enumerate(row["branches"]):
            for c in branch:
                mark = "holds" if c["holds"] else ("fails" if c["clearly_fails"] else "unresolved")
                lines.append(
                    f"  - {'branch ' + str(b + 1) + ': ' if len(row['branches']) > 1 else ''}"
                    f"{c['condition']}: {c['point']} {c['interval']} → {mark}"
                )
    lines += [
        "",
        "## Probe metrics by arm",
        "",
        "| arm | train top-1 | within-label AUROC | decoy rate | τ(agree) − τ(differ) |",
        "|---|---|---|---|---|",
    ]
    for arm, p in payload["probe"].items():
        lines.append(
            f"| {arm} | {p['train_top1']} | {p['within_label_auroc']['point']} "
            f"{p['within_label_auroc']['interval']} | {p['decoy_rate']['point']} "
            f"{p['decoy_rate']['interval']} | {p['tau_contrast']['point']} "
            f"{p['tau_contrast']['interval']} |"
        )
    lines += [
        "",
        "### Top-1 progress accuracy by variant",
        "",
        "| arm | " + " | ".join(TRAINING + HELD_OUT) + " |",
        "|---|" + "---|" * len(TRAINING + HELD_OUT),
    ]
    for arm, p in payload["probe"].items():
        lines.append(
            f"| {arm} | " + " | ".join(str(p["top1"].get(v)) for v in TRAINING + HELD_OUT) + " |"
        )
    lines += [
        "",
        "## Hunts (diagnostics 5-7)",
        "",
        "| app | arm | runs | RMST | found | vs search_S | vs random | decoys/episode |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for app, by_arm in payload["hunts"]["apps"].items():
        for arm, row in by_arm.items():
            lines.append(
                f"| {app} | {arm} | {row['runs']} | {row['restricted_mean_time']} | "
                f"{row['discovered']}/{row['runs']} | {row.get('rmst_ratio_vs_search_S', '')} | "
                f"{row.get('rmst_ratio_vs_random', '')} | {row['decoys_per_episode']} |"
            )
    if payload["hunts"]["failures"]:
        lines += ["", f"Failed hunt jobs: {payload['hunts']['failures']}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--resamples", type=int, default=RESAMPLES)
    parser.add_argument("--report", type=Path, default=REPORT)
    arguments = parser.parse_args(argv)
    payload = analyze(arguments.out_dir, arguments.resamples)
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, default=float) + "\n", encoding="utf-8"
    )
    arguments.report.with_name(arguments.report.name + "_numbers.md").write_text(
        render(payload), encoding="utf-8"
    )
    print(render(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
