"""Capture real 6973-D observations from the deep fixture, once, for offline Q analysis.

**Why a capture step exists at all.** The trained-checkpoint Q diagnostic needs the exact
observation an `ActionConditionedDQN` was trained on, and nothing in the repository stores
one: `TraceRecorder` drops `action_specs`, `episode_context` and `num_valid_actions` from
every record, so the five committed corpora cannot supply it. Building one offline is not
possible faithfully either — an observation needs a screenshot, the network trace, the
env's *serialized* HTML (with `_SERIALIZE_JS`-injected `value`/`data-hidden` attributes,
not the raw fixture file) and full `ActionSpec.params` that only a live DOM scan produces.
So the states are captured once, here, and every later analysis runs offline against this
file.

**The observation is built by production code, not reimplemented.** `encode_modalities` is
the same free function `evaluation.TrainedPolicy.act` calls to score a trained policy, fed
from the same `(obs, info)` a real episode produces. Nothing in `src/` is modified,
imported differently, or approximated.

**How the targeted states are reached.** `WebFunctionalEnv.setup_actions` replays a macro
after the landing page and before the first observation — the same mechanism the archive
uses to return to a cell, and the same schema as a repro script. So the order-4 deadlock
state is reached by *walking the flow*, exactly as the agent does, rather than by loading
`order-4.html` directly. Those are different states: the walked one carries
`?product=...&quantity=...&email=...`, and only it is the state the deadlock occurs in.

    python scripts/capture_action_q_states.py
    python scripts/capture_action_q_states.py --verify   # capture twice, diff the two
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from web_testing_agent.agents.ac_dqn import AC_OBS_DIM  # noqa: E402
from web_testing_agent.agents.action_features import ActionFeatureExtractor  # noqa: E402
from web_testing_agent.envs import WebFunctionalEnv  # noqa: E402
from web_testing_agent.envs.types import MAX_ACTIONS  # noqa: E402
from web_testing_agent.perception.vec_wrapper import (  # noqa: E402
    default_encoders,
    encode_modalities,
)
from web_testing_agent.utils.local_server import serve_directory  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

SITE = REPO_ROOT / "tests" / "fixtures" / "deep_flow_site"
DEFAULT_OUT = REPO_ROOT / "reports" / "trained_action_q_states.json"
SEED = 0

# The gated flow, in the repro-script schema `setup_actions` already accepts. Prefixes of
# this list reach each stage, so every gated state below is a genuine walk rather than a
# direct load — `Continue` is `class="hidden"` until its gate opens and cannot appear in
# an action set any other way.
FLOW = [
    {"type": "CLICK", "id": "start-order"},
    {"type": "SELECT", "id": "product"},
    {"type": "CLICK", "id": "to-step-2"},
    {"type": "TYPE", "id": "quantity", "params": {"category": "valid_typical"}},
    {"type": "CLICK", "id": "to-step-3"},
    {"type": "TYPE", "id": "email", "params": {"category": "valid_typical"}},
    {"type": "CLICK", "id": "to-step-4"},
    {"type": "CLICK", "id": "place-order"},
]

# (state_id, landing page, route). A route of [] is a fresh page load; a route is a walk.
# Fresh loads give breadth across the fixture's vocabulary; walks give the states the
# agent actually meets, including the one the deadlock happens in.
CAPTURES: list[tuple[str, str, list[dict]]] = [
    *[(f"page:{name}", name, []) for name in sorted(p.name for p in SITE.glob("*.html"))],
    ("flow:order-1-gated", "index.html", FLOW[:2]),
    ("flow:order-2-gated", "index.html", FLOW[:4]),
    ("flow:order-3-gated", "index.html", FLOW[:6]),
    # The deadlock state: order-4 reached through the flow, carrying the query string.
    ("flow:order-4-deadlock", "index.html", FLOW[:7]),
    ("flow:receipt", "index.html", FLOW[:8]),
]

# Pairs the diagnostic was asked to analyse. Recorded here so the coverage table is
# produced from the capture itself rather than asserted afterwards.
REQUESTED_PAIRS = [
    ("Place order", "Cancel order"),
    ("Place order", "Back"),
    ("Place order", "Start order"),
    ("Place order", "Continue"),
    ("Next", "Continue"),
    ("Login", "Sign in"),
    ("Back", "Previous"),
]


def spec_to_dict(spec) -> dict:  # noqa: ANN001 - ActionSpec
    """Everything the offline side needs to rebuild the action's identity and features."""
    record = asdict(spec)
    record["action_type"] = spec.action_type.value
    params = record.get("params") or {}
    # Promoted to top level because the diagnostic reads them per action and a missing
    # key should read as absent rather than as False.
    record["in_viewport"] = params.get("in_viewport")
    record["navigational"] = params.get("navigational")
    record["href"] = params.get("href")
    record["category"] = params.get("category")
    record["option"] = params.get("option")
    return record


def capture_one(base_url: str, state_id: str, page: str, route: list[dict],
                encoders: dict) -> dict:
    """One real observation, built by the same call the evaluation harness uses."""
    env = WebFunctionalEnv(
        base_url=f"{base_url}/{page}", max_steps=40, headless=True,
        setup_actions=route or None,
    )
    try:
        obs, info = env.reset(seed=SEED)
        specs = info["action_specs"]
        # A fresh extractor per state, started as an episode would start it, so
        # `taken_here` / `taken_this_episode` carry the same zeros a real first
        # observation carries rather than counts leaked from a previous capture.
        features = ActionFeatureExtractor()
        features.start_episode()
        blocks = [features.encode(specs, info.get("state_key", ""),
                                  {tuple(e) for e in (info.get("newly_revealed") or [])})]
        vector = encode_modalities(
            encoders,
            [obs["screenshot"]],
            [info.get("page", {})],
            [info.get("episode_context")],
            [info.get("num_valid_actions")],
            blocks,
        )[0]
        if vector.shape != (AC_OBS_DIM,):
            raise RuntimeError(f"{state_id}: observation is {vector.shape}, expected {AC_OBS_DIM}")
        mask = vector[-MAX_ACTIONS:]
        valid = int(info["num_valid_actions"])
        if int((mask > 0.5).sum()) != valid:
            raise RuntimeError(
                f"{state_id}: mask has {int((mask > 0.5).sum())} valid slots but the env "
                f"reports {valid}")
        if len(specs) != valid:
            raise RuntimeError(f"{state_id}: {len(specs)} specs against {valid} valid actions")
        values = [float(x) for x in vector]
        return {
            "state_id": state_id,
            "landing_page": page,
            "route": route,
            "route_length": len(route),
            "bootstrap_steps": info.get("bootstrap_steps", 0),
            "url": info["page"]["url"],
            "page_file": info["page"]["url"].rsplit("/", 1)[-1],
            "state_key": info.get("state_key", ""),
            "num_valid_actions": valid,
            "episode_context": [float(x) for x in info["episode_context"]],
            "observation": values,
            "observation_sha256": hashlib.sha256(
                np.asarray(vector, dtype=np.float32).tobytes()).hexdigest(),
            "action_specs": [spec_to_dict(spec) for spec in specs],
            "labels": [spec.element_id or "" for spec in specs],
        }
    finally:
        env.close()


def capture_all() -> dict:
    encoders = default_encoders()
    states = []
    with serve_directory(SITE) as origin:
        for state_id, page, route in CAPTURES:
            logger.info("capturing {} ({} route steps)", state_id, len(route))
            states.append(capture_one(origin, state_id, page, route, encoders))

    labels_by_state = {s["state_id"]: set(s["labels"]) for s in states}
    coverage = []
    for a, b in REQUESTED_PAIRS:
        present = sorted(sid for sid, labels in labels_by_state.items()
                         if a in labels and b in labels)
        anywhere = {"a": any(a in labels for labels in labels_by_state.values()),
                    "b": any(b in labels for labels in labels_by_state.values())}
        coverage.append({
            "pair": [a, b],
            "co_valid_states": present,
            "n_states": len(present),
            "label_a_exists_anywhere": anywhere["a"],
            "label_b_exists_anywhere": anywhere["b"],
            "status": "captured" if present else (
                "not present in current fixture"
                if not (anywhere["a"] and anywhere["b"])
                else "both labels exist but never co-valid in any captured state"),
        })

    return {
        "_purpose": "Real observations from the deep fixture, for offline trained-Q "
                    "analysis against the post-Markov checkpoints.",
        "_provenance": "WebFunctionalEnv + encode_modalities (the same call "
                       "evaluation.TrainedPolicy.act uses). No state was constructed "
                       "offline or approximated.",
        "seed": SEED,
        "observation_dim": AC_OBS_DIM,
        "encoders": "hash (default_encoders)",
        "n_states": len(states),
        "coverage": coverage,
        "states": states,
    }


def compare_captures(left: dict, right: dict) -> dict:
    """Two captures of the same states, block by block.

    The harness has a measured run-to-run divergence: `_bucket_duration` quantises request
    latency, and a request that stalls past 50ms flips `<50ms` -> `<200ms`, which
    `HashEmbeddingEncoder` maps to an orthogonal 384-dim network block. So a byte-identical
    repeat is expected but not guaranteed, and this reports which block moved rather than
    only whether something did.
    """
    from web_testing_agent.agents.ac_dqn import ACTION_BLOCK, STATE_BLOCK
    from web_testing_agent.perception.fusion import STRUCTURAL_DIM, VISUAL_DIM

    bounds = [
        ("visual", 0, VISUAL_DIM),
        ("structural", VISUAL_DIM, VISUAL_DIM + STRUCTURAL_DIM),
        ("network", VISUAL_DIM + STRUCTURAL_DIM, 1664),
        ("episode_context", 1664, STATE_BLOCK),
        ("action_features", STATE_BLOCK, STATE_BLOCK + ACTION_BLOCK),
        ("mask", STATE_BLOCK + ACTION_BLOCK, AC_OBS_DIM),
    ]
    rows = []
    for a, b in zip(left["states"], right["states"], strict=True):
        va = np.asarray(a["observation"], dtype=np.float32)
        vb = np.asarray(b["observation"], dtype=np.float32)
        differing = np.flatnonzero(va != vb)
        rows.append({
            "state_id": a["state_id"],
            "identical": bool(differing.size == 0),
            "sha_match": a["observation_sha256"] == b["observation_sha256"],
            "labels_match": a["labels"] == b["labels"],
            "differing_dims": int(differing.size),
            "blocks": {name: int(((differing >= lo) & (differing < hi)).sum())
                       for name, lo, hi in bounds} if differing.size else {},
        })
    return {
        "states_compared": len(rows),
        "identical": sum(1 for r in rows if r["identical"]),
        "per_state": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--verify", action="store_true",
                        help="capture a second time and report block-level differences")
    args = parser.parse_args()

    payload = capture_all()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("=" * 78)
    print(f"CAPTURED {payload['n_states']} REAL STATES  ({AC_OBS_DIM}-D observations)")
    print("=" * 78)
    print(f"{'state_id':<26}{'page':<18}{'valid':>6}{'CLICK':>7}{'TYPE':>6}{'SELECT':>7}  sha")
    print("-" * 92)
    for state in payload["states"]:
        kinds = [s["action_type"] for s in state["action_specs"]]
        print(f"{state['state_id']:<26}{state['page_file'][:17]:<18}"
              f"{state['num_valid_actions']:>6}{kinds.count('CLICK'):>7}"
              f"{kinds.count('TYPE'):>6}{kinds.count('SELECT'):>7}  "
              f"{state['observation_sha256'][:12]}")

    print("\ncoverage of the requested pairs:")
    for row in payload["coverage"]:
        a, b = row["pair"]
        print(f"  {a + ' / ' + b:<32} {row['n_states']:>2} state(s)   {row['status']}")
        for sid in row["co_valid_states"]:
            print(f"       -> {sid}")

    if args.verify:
        print("\nre-capturing to check reproducibility ...")
        second = capture_all()
        report = compare_captures(payload, second)
        print(f"  {report['identical']}/{report['states_compared']} states byte-identical")
        for row in report["per_state"]:
            if not row["identical"]:
                hot = {k: v for k, v in row["blocks"].items() if v}
                print(f"    {row['state_id']}: {row['differing_dims']} dims differ -> {hot}")
        payload["reproducibility"] = report
        args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
