"""Pre-registration §2, factor 1: how close the held-out vocabularies are to training.

Reported, not thresholded: for each held-out profile, every control label's cosine
similarity to its nearest training label (V0 and V1, the same control keys the token
overlap check uses), under two embeddings -- the sentence model's raw 384-D vectors, and
the 48-D PCA projection of them that the Q-network actually receives.

    python -m web_testing_agent.ctxbench.vocabulary
"""

from __future__ import annotations

import json
import sys

import numpy as np

from ..experiments.transfer import REPO_ROOT
from .generate import CONTROL_KEYS, PROFILES, overlap_report

HELD_OUT = ("VS", "VU1", "VU2")
OUT = REPO_ROOT / "reports" / "ctx_vocabulary.json"


def _unit(rows: np.ndarray) -> np.ndarray:
    rows = np.asarray(rows, dtype=np.float64)
    return rows / np.maximum(np.linalg.norm(rows, axis=1, keepdims=True), 1e-12)


def nearest_neighbour(encode, held_out: list[str], training: list[str]) -> dict:
    sims = _unit(encode(held_out)) @ _unit(encode(training)).T
    best = sims.max(axis=1)
    return {
        "mean": round(float(best.mean()), 4),
        "median": round(float(np.median(best)), 4),
        "max": round(float(best.max()), 4),
        "per_label": {
            label: {"cosine": round(float(b), 4), "nearest": training[int(i)]}
            for label, b, i in zip(held_out, best, sims.argmax(axis=1), strict=True)
        },
    }


def report() -> dict:
    from ..agents.semantic_labels import SemanticLabelEncoder

    training = sorted({PROFILES[p][k] for p in ("V0", "V1") for k in CONTROL_KEYS})
    projected = SemanticLabelEncoder()
    raw = projected._load_encoder().encode_batch  # noqa: SLF001 - the unprojected vectors
    out = {
        "control_keys": list(CONTROL_KEYS),
        "training_labels": training,
        "token_overlap": overlap_report(),
        "profiles": {},
    }
    for name in HELD_OUT:
        labels = sorted({PROFILES[name][k] for k in CONTROL_KEYS})
        out["profiles"][name] = {
            "labels": len(labels),
            "sentence_model": nearest_neighbour(raw, labels, training),
            "network_input_projection": nearest_neighbour(projected.encode_batch, labels, training),
        }
    out["label_model"] = projected.model_name
    return out


def main() -> int:
    payload = report()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for name, row in payload["profiles"].items():
        print(
            f"{name}: token overlap {payload['token_overlap'][name] or 'none'}; mean NN cosine "
            f"{row['sentence_model']['mean']} (sentence model), "
            f"{row['network_input_projection']['mean']} (network input)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
