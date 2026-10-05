"""Frozen-weight probe evaluation (pre-registration §4(a)).

For every arm, seed and variant, the score each arm gives every available action in every
probe state. The learned arms are their pretrained checkpoints exactly as saved: no
adaptation residual, no learning, no exploration. Features are rebuilt from each probe's
recorded history with a fresh extractor (`FeatureExtractor.begin_episode`, then `record`
for every step of the prefix, then `encode` of the probe state), so the numbers are a pure
function of the weights. `search_S` is the fixed score `FeatureSearchPolicy.scores` over
the same unmasked features; `random` is a seeded uniform draw (the chance level of every
metric).

Also here: the permutation-invariance check (diagnostic 4), which reorders the encoded
action rows and must give back the same Q-values (within 1e-5) and the same greedy choice.

    python -m web_testing_agent.ctxbench.probe_eval --out-dir runs/ctx-main
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from ..rl.features import FeatureExtractor, semantic_label
from . import probes as probe_sets
from .analyze import greedy_set
from .runner import ARMS, HELD_OUT, LEARNED, SEEDS, TRAINING, checkpoint_path

PERMUTATION_STATES = 100
PERMUTATIONS = 20
PERMUTATION_TOLERANCE = 1e-5


def label_key(action: dict) -> str:
    """What "the same action" means across states: type, label text and value."""
    spec = probe_sets.spec_from_dict(action)
    params = action.get("params") or {}
    value = params.get("category", params.get("option", ""))
    return f"{action['action_type']}|{semantic_label(spec)}|{value}"


def encode_probe(state: dict, labels, anchor):
    """The probe state's `Encoded`, as an agent that had just walked its prefix would see it."""
    extractor = FeatureExtractor(labels=labels, anchor=anchor)
    extractor.begin_episode()
    infos = [probe_sets.info_from_dict(row) for row in state["history"]["infos"]]
    for step, index in enumerate(state["history"]["actions"]):
        before, after = infos[step], infos[step + 1]
        spec = next(s for s in before["action_specs"] if s.index == index)
        extractor.record(before, spec, after)
    encoded = extractor.encode(infos[-1])
    recorded = [a["index"] for a in state["actions"]]
    encoded_order = [s.index for s in infos[-1]["action_specs"]][: encoded.count]
    if recorded != encoded_order:
        raise ValueError(
            f"{state['probe_id']}: labelled actions do not line up with the encoded rows"
        )
    return encoded


def _random_scores(arm_seed: int, probe_id: str, count: int) -> np.ndarray:
    digest = hashlib.sha256(f"random|{arm_seed}|{probe_id}".encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "little")).random(count)


def score_variants(out_dir: Path, variants: tuple[str, ...]) -> dict:
    """`{arm: {seed: {variant: {probe_id: [score per action]}}}}` plus the probe metadata."""
    from ..rl import QAgent
    from ..rl.label_store import StoredLabelEncoder
    from ..rl.search import FeatureSearchPolicy

    labels = StoredLabelEncoder()
    anchor = FeatureExtractor(labels=labels).anchor
    agents = {
        (arm, seed): QAgent.load(checkpoint_path(out_dir, arm, seed), labels=labels)
        for arm in LEARNED
        for seed in SEEDS
    }
    search = FeatureSearchPolicy(seed=0, epsilon=0.0)

    scores: dict = {arm: {str(seed): {} for seed in SEEDS} for arm in ARMS}
    meta: dict = {}
    for variant in variants:
        probes = probe_sets.load(variant)
        meta[variant] = {"sha256": _probe_hash(variant), "states": []}
        for arm in ARMS:
            for seed in SEEDS:
                scores[arm][str(seed)][variant] = {}
        for state in probes["states"]:
            encoded = encode_probe(state, labels, anchor)
            meta[variant]["states"].append(
                {
                    "probe_id": state["probe_id"],
                    "trace": state["trace"],
                    "prefix_length": state["prefix_length"],
                    "effects": [a["effect"] for a in state["actions"]],
                    "labels": [label_key(a) for a in state["actions"]],
                }
            )
            for (arm, seed), agent in agents.items():
                scores[arm][str(seed)][variant][state["probe_id"]] = agent.q_values(
                    encoded
                ).tolist()
            fixed = search.scores(encoded.actions).tolist()
            for seed in SEEDS:
                scores["search_S"][str(seed)][variant][state["probe_id"]] = fixed
                scores["random"][str(seed)][variant][state["probe_id"]] = _random_scores(
                    seed, state["probe_id"], encoded.count
                ).tolist()
    return {"scores": scores, "probes": meta}


def permutation_check(out_dir: Path, variants: tuple[str, ...], seed: int = 0) -> dict:
    """Diagnostic 4, for every learned arm and seed, on the same 100 probe states."""
    import torch as th

    from ..rl import QAgent
    from ..rl.label_store import StoredLabelEncoder

    labels = StoredLabelEncoder()
    anchor = FeatureExtractor(labels=labels).anchor
    pool = [(v, s) for v in variants for s in probe_sets.load(v)["states"] if len(s["actions"]) > 1]
    rng = np.random.default_rng(seed)
    chosen = [
        pool[i]
        for i in sorted(
            rng.choice(len(pool), size=min(PERMUTATION_STATES, len(pool)), replace=False)
        )
    ]
    encoded = [encode_probe(state, labels, anchor) for _, state in chosen]
    orders = [[rng.permutation(e.count) for _ in range(PERMUTATIONS)] for e in encoded]

    out = {
        "states": len(chosen),
        "permutations": PERMUTATIONS,
        "tolerance": PERMUTATION_TOLERANCE,
        "arms": {},
    }
    for arm in LEARNED:
        for model_seed in SEEDS:
            agent = QAgent.load(checkpoint_path(out_dir, arm, model_seed), labels=labels)
            worst, greedy_mismatches, positional = 0.0, 0, 0
            for e, perms in zip(encoded, orders, strict=True):
                base = agent.q_values(e)
                for order in perms:
                    with th.no_grad():
                        q = (
                            agent.network(
                                th.as_tensor(e.state).unsqueeze(0),
                                th.as_tensor(e.actions[order]).unsqueeze(0),
                                th.ones((1, e.count), dtype=th.bool),
                            )
                            .squeeze(0)
                            .numpy()
                        )
                    restored = np.empty_like(q)
                    restored[order] = q
                    worst = max(worst, float(np.abs(restored - base).max()))
                    # The greedy choice must be the same action: the same set of actions tied
                    # for the best score. Which one of a tie `argmax` takes is positional, and
                    # is counted separately.
                    same_set = np.array_equal(greedy_set(restored), greedy_set(base))
                    greedy_mismatches += int(not same_set)
                    positional += int(order[int(np.argmax(q))] != int(np.argmax(base)))
            out["arms"][f"{arm}|{model_seed}"] = {
                "max_abs_difference": worst,
                "greedy_mismatches": greedy_mismatches,
                "positional_tie_breaks_differing": positional,
                "passed": worst <= PERMUTATION_TOLERANCE and greedy_mismatches == 0,
            }
    out["passed"] = all(row["passed"] for row in out["arms"].values())
    return out


def _probe_hash(variant: str) -> str:
    return hashlib.sha256((probe_sets.PROBE_DIR / f"{variant}.json.gz").read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)
    variants = TRAINING + HELD_OUT
    payload = score_variants(arguments.out_dir, variants)
    payload["permutation"] = permutation_check(arguments.out_dir, HELD_OUT)
    path = arguments.out_dir / "probe_eval.json.gz"
    with gzip.GzipFile(path, "wb", mtime=0) as handle:
        handle.write(json.dumps(payload, sort_keys=True).encode("utf-8"))
    print(
        f"probe scores -> {path}; permutation invariance "
        f"{'passed' if payload['permutation']['passed'] else 'FAILED'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
