"""Prove that the diagnostics do not change training, before spending hours on them.

**The claim being tested.** `agents/diagnostics.py` adds a Q-probe callback, a
transition-tagging replay buffer and checkpoint saving to the `ac_dqn` arm. All three
are supposed to be read-only. "Supposed to be" is not a measurement, and this project's
record is that the expensive mistakes are exactly the ones nobody measured — 384 of
1,664 observation dimensions were clock jitter for ten days, and `broken_navigation`
fired 18-20 times a run on a fixture whose deterministic ceiling is zero.

**Why three runs and not two.** A single instrumented-vs-uninstrumented pair cannot
distinguish "the probe perturbed training" from "this harness is not deterministic at
this budget", and the second is a live possibility here: §5's 2026-08-28 entry
established bit-reproducibility at 600 steps for the flat head, and the 2026-08-29
addendum found that 4,000 steps at a fixed seed does *not* reproduce, most likely
through browser timing. So:

    A  no instrumentation
    B  no instrumentation, identical seed      -> A == B  establishes the control
    C  probe + tagging + checkpoints           -> A == C  establishes neutrality

If A != B the harness is not deterministic at this budget and the comparison is
inconclusive — which the script says, rather than reporting a false pass.

**Measured 2026-09-02: the control is intermittent, so A/B/C cannot certify anything.**
On the `ac_dqn` configuration, two *uninstrumented* runs at an identical seed usually
agree bit-for-bit and occasionally do not — across 12 uninstrumented 200-step runs
exactly one deviated, while 0 of 6 instrumented ones did (`--replicates`,
`reports/diag_harness_determinism.json`). At 600 steps the single uninstrumented pair
differed. So a lone A-vs-B agreement is not evidence that the harness is deterministic,
and a lone A-vs-C difference is not evidence that the instrumentation did it — in one
batch the two *instrumented* runs were the reproducible pair and an *uninstrumented* run
was the outlier.

The onset is diagnostic. Caught early (60 steps, `p_return=0`), a divergence is confined
to the **network block — 384 of 384 columns, in two stored observations, with `visual`,
`structural`, `action_features` and `mask` bit-identical and the action sequence
identical**: the agent chose the same actions and the environment answered with a
different network vector. By 600 steps it has propagated through the argmax and 355 of
600 actions differ. Archive-independent, and not the `duration_ms` buckets — six
identical scripted walks produced byte-identical traces with every duration under 32 ms
against a 50 ms edge. **The mechanism is unidentified and is a separate investigation.**

§5's 2026-08-28 bit-reproducibility result was measured on the **flat head** with no
archive and no action-feature block, and explicitly flagged larger budgets as untested;
this is a different configuration, so it narrows that result rather than contradicting
it. Whether `dqn_masked` still reproduces is **unmeasured**.

Consequence: this script is kept as the end-to-end control, as the instrument that
localises such a divergence to an observation block, and — via `--replicates` — as the
way to measure the harness's own variance before an A/B is run on it. The RNG-neutrality
claim itself is settled elsewhere, by
`tests/unit/test_diagnostics.py::test_instrumented_training_is_bit_identical_to_uninstrumented_training`,
which replays a fixed observation stream so that the browser cannot be the explanation.

**What is compared.** The whole of what training consumed and produced, read off the
model afterwards so that the uninstrumented runs stay genuinely uninstrumented: every
stored observation, action, reward and done flag in the replay buffer, the PER priority
tree, and the final weights of both the online and target networks.

    python scripts/check_probe_rng_neutrality.py --steps 600
    python scripts/check_probe_rng_neutrality.py --steps 600 --verify-order4
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from argparse import Namespace
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from stable_baselines3.common.monitor import Monitor  # noqa: E402
from stable_baselines3.common.vec_env import DummyVecEnv  # noqa: E402

from web_testing_agent.agents.ac_dqn import (  # noqa: E402
    AC_OBS_DIM,
    ACTION_BLOCK,
    STATE_BLOCK,
)
from web_testing_agent.agents.action_features import ActionFeatureExtractor  # noqa: E402
from web_testing_agent.agents.archive_start import ArchiveStartWrapper  # noqa: E402
from web_testing_agent.agents.diagnostics import QGapProbe  # noqa: E402
from web_testing_agent.agents.go_explore import Archive  # noqa: E402
from web_testing_agent.envs import WebFunctionalEnv  # noqa: E402
from web_testing_agent.perception.fusion import STRUCTURAL_DIM, VISUAL_DIM  # noqa: E402
from web_testing_agent.perception.vec_wrapper import (  # noqa: E402
    SemanticPerceptionWrapper,
    default_encoders,
)
from web_testing_agent.utils.local_server import serve_directory  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from compare_agents import build_ac_agent  # noqa: E402

logger = get_logger(__name__)

SITE = REPO_ROOT / "tests" / "fixtures" / "deep_flow_site"

# Walks the gated flow to order-4.html, so `--verify-order4` can confirm the probe
# resolves `Place order` / `Back` and reads the *initial* network there. Same schema as
# a repro script and as an archive route; replayed outside the step budget.
ROUTE_TO_ORDER_4 = [
    {"type": "CLICK", "id": "start-order"},
    {"type": "SELECT", "id": "product"},
    {"type": "CLICK", "id": "to-step-2"},
    {"type": "TYPE", "id": "quantity", "params": {"category": "valid_typical"}},
    {"type": "CLICK", "id": "to-step-3"},
    {"type": "TYPE", "id": "email", "params": {"category": "valid_typical"}},
    {"type": "CLICK", "id": "to-step-4"},
]


def agent_args(steps: int, tag: str | None, dueling: bool = True) -> Namespace:
    """Exactly the arguments `build_ac_agent` reads, so the agent under test is the
    agent the comparison script builds — not a second definition free to drift."""
    return Namespace(
        exploration_fraction=0.6, final_eps=0.10, n_step=3,
        train_steps=steps, tag_transitions=tag, dueling=dueling,
    )


def fingerprint(model) -> dict:  # noqa: ANN001
    """Everything training consumed and produced, as comparable arrays.

    Read off the model rather than recorded during the run, so that the uninstrumented
    arms carry no instrumentation at all — a recorder attached "only to compare" would
    make the control an instrumented run too.
    """
    buffer = model.replay_buffer
    upper = buffer.buffer_size if buffer.full else buffer.pos
    out = {
        "stored": int(upper),
        "observations": np.asarray(buffer.observations[:upper]).copy(),
        "actions": np.asarray(buffer.actions[:upper]).copy(),
        "rewards": np.asarray(buffer.rewards[:upper]).copy(),
        "dones": np.asarray(buffer.dones[:upper]).copy(),
        "timeouts": np.asarray(buffer.timeouts[:upper]).copy(),
        "priorities": np.asarray(buffer._tree._tree).copy(),
    }
    for name, net in (("q_net", model.q_net), ("q_net_target", model.q_net_target)):
        for key, tensor in net.state_dict().items():
            out[f"{name}.{key}"] = tensor.detach().cpu().numpy().copy()
    return out


def digests(print_: dict) -> dict:
    """Compact hashes of each recorded array, plus per-episode action hashes.

    The full arrays are 111 MB a run and are compared in memory; what goes into the
    report is this. The per-episode action digests are what let a reader see *which*
    episode two runs stopped agreeing on without holding either run's data.
    """
    def sha(array: np.ndarray) -> str:
        return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()[:16]

    actions = np.asarray(print_["actions"]).reshape(-1)
    weights = b"".join(
        np.ascontiguousarray(print_[k]).tobytes() for k in sorted(print_) if k.startswith("q_net.")
    )
    return {
        "actions": sha(actions),
        "observations": sha(print_["observations"]),
        "rewards": sha(print_["rewards"]),
        "dones": sha(print_["dones"]),
        "priorities": sha(print_["priorities"]),
        "q_net_weights": hashlib.sha256(weights).hexdigest()[:16],
        "actions_per_episode": [sha(actions[i:i + 40]) for i in range(0, actions.size, 40)],
        "first_20_actions": actions[:20].tolist(),
    }


def _observation_blocks(a: np.ndarray, b: np.ndarray) -> str:
    """Which part of the observation two runs disagree on.

    The observation is a concatenation with fixed boundaries, and *where* a divergence
    lands says what caused it. A difference confined to the network block is browser
    timing crossing one of `canonicalize_network_trace`'s duration buckets; a difference
    in the action block or the mask is the page itself having been scanned differently.
    Reported rather than left as a cell count, because the two have completely different
    consequences for an experiment.
    """
    columns = np.unique(np.argwhere(a != b)[:, -1]) if a.shape == b.shape else np.array([])
    bounds = [
        ("visual", 0, VISUAL_DIM),
        ("structural", VISUAL_DIM, VISUAL_DIM + STRUCTURAL_DIM),
        ("network", VISUAL_DIM + STRUCTURAL_DIM, 1664),
        ("episode_context", 1664, STATE_BLOCK),
        ("action_features", STATE_BLOCK, STATE_BLOCK + ACTION_BLOCK),
        ("mask", STATE_BLOCK + ACTION_BLOCK, AC_OBS_DIM),
    ]
    parts = [
        f"{name}={int(((columns >= lo) & (columns < hi)).sum())}/{hi - lo}"
        for name, lo, hi in bounds
    ]
    return "  differing columns by block: " + ", ".join(parts)


def compare(left: dict, right: dict) -> tuple[bool, list[str]]:
    """Bit-identity, field by field. Reports every difference, not just the first."""
    notes: list[str] = []
    identical = True
    for key in sorted(set(left) | set(right)):
        if key not in left or key not in right:
            identical = False
            notes.append(f"  {key:<40} MISSING on one side")
            continue
        a, b = left[key], right[key]
        if isinstance(a, int):
            if a != b:
                identical = False
                notes.append(f"  {key:<40} {a} != {b}")
            continue
        if a.shape != b.shape:
            identical = False
            notes.append(f"  {key:<40} shape {a.shape} != {b.shape}")
            continue
        if not np.array_equal(a, b):
            identical = False
            delta = float(np.max(np.abs(a.astype(np.float64) - b.astype(np.float64))))
            differing = int((a != b).sum())
            notes.append(f"  {key:<40} DIFFERS  max|delta|={delta:.3e}  cells={differing}/{a.size}")
            if key == "observations":
                notes.append(_observation_blocks(a, b))
                rows = np.unique(np.argwhere(a != b)[:, 0])
                notes.append(f"  first differing stored transition: {int(rows[0])} of {a.shape[0]}")
    return identical, notes


_BLOCKS = ("visual", "structural", "network", "episode_context", "action_features", "mask")


def _first_row(a: np.ndarray, b: np.ndarray) -> int | None:
    """Index of the first stored transition on which two arrays disagree."""
    if a.shape != b.shape:
        return 0
    rows = np.argwhere(a != b)
    return int(rows[:, 0].min()) if rows.size else None


def _blocks_at(a: np.ndarray, b: np.ndarray, row: int) -> dict[str, int]:
    """Differing columns per observation block, at one transition."""
    bounds = [
        (0, VISUAL_DIM), (VISUAL_DIM, VISUAL_DIM + STRUCTURAL_DIM),
        (VISUAL_DIM + STRUCTURAL_DIM, 1664), (1664, STATE_BLOCK),
        (STATE_BLOCK, STATE_BLOCK + ACTION_BLOCK), (STATE_BLOCK + ACTION_BLOCK, AC_OBS_DIM),
    ]
    columns = np.flatnonzero(a[row] != b[row])
    return {name: int(((columns >= lo) & (columns < hi)).sum())
            for name, (lo, hi) in zip(_BLOCKS, bounds, strict=True)}


def divergence_report(a: dict, b: dict) -> dict:
    """Where two nominally identical runs first parted, and which component did it.

    The classification is an ordering argument, not a guess. Every stored transition is
    `(obs_t, action_t, reward_t, done_t)` in step order, so whichever of those differs at
    the *lowest* index is the one that moved first, and everything after it is
    downstream. Three cases carry real information:

    * **observation before action** — the agent chose the same actions and the
      environment answered differently. That is environment-side, and the block profile
      says which modality.
    * **action before observation** — the environment answered identically and the agent
      chose differently. That would mean the RNG or the network diverged, which is the
      serious case.
    * **transitions all identical, weights differ** — training consumed identical data
      and produced different parameters. That is non-determinism inside the update
      itself (CUDA kernel reduction order is the usual cause).
    """
    obs_a, obs_b = a["observations"], b["observations"]
    i_obs = _first_row(obs_a, obs_b)
    i_act = _first_row(a["actions"], b["actions"])
    i_rew = _first_row(a["rewards"], b["rewards"])
    i_done = _first_row(a["dones"], b["dones"])
    weights_differ = any(
        not np.array_equal(a[k], b[k]) for k in a
        if k.startswith(("q_net.", "q_net_target."))
    )
    priorities_differ = not np.array_equal(a["priorities"], b["priorities"])

    firsts = {"observations": i_obs, "actions": i_act, "rewards": i_rew, "dones": i_done}
    candidates = {k: v for k, v in firsts.items() if v is not None}
    report: dict = {
        "identical": not candidates and not weights_differ and not priorities_differ,
        "first_divergence": firsts,
        "weights_differ": weights_differ,
        "priorities_differ": priorities_differ,
        "differing_transitions": int(np.unique(np.argwhere(obs_a != obs_b)[:, 0]).size)
        if i_obs is not None else 0,
        "differing_actions": int((a["actions"] != b["actions"]).sum()) if i_act is not None else 0,
        "transitions_compared": int(a["stored"]),
    }
    if report["identical"]:
        report["classification"] = "identical"
        return report

    if not candidates:
        # Identical data in, different parameters out.
        report["classification"] = "5. network update"
        report["earliest_divergence_step"] = None
        report["note"] = ("every stored transition is bit-identical; only the parameters "
                          "differ, so the divergence is inside the gradient update")
        return report

    earliest = min(candidates.values())
    first_fields = sorted(k for k, v in candidates.items() if v == earliest)
    report["earliest_divergence_step"] = earliest
    report["first_differing_fields"] = first_fields
    report["episode_of_divergence"] = earliest // 40

    if i_obs is not None:
        report["observation_blocks_at_divergence"] = _blocks_at(obs_a, obs_b, i_obs)

    if "observations" in first_fields and (i_act is None or i_act > earliest):
        blocks = report.get("observation_blocks_at_divergence", {})
        hot = [name for name, count in blocks.items() if count]
        report["actions_identical_through_step"] = (
            (i_act - 1) if i_act is not None else int(a["stored"]))
        if hot == ["network"]:
            report["classification"] = "1/2. environment -> observation (network block only)"
            report["note"] = ("the agent chose the same action and the environment returned "
                              "a different network trace; the page itself was identical")
        elif set(hot) <= {"action_features", "mask"}:
            report["classification"] = "1. environment/browser state (action set differed)"
        else:
            report["classification"] = "1/2. environment -> observation (page differed)"
            report["note"] = f"blocks differing at the divergence: {hot}"
    elif "actions" in first_fields:
        report["classification"] = "3. action selection"
        report["note"] = ("the environment returned identical observations and the agent "
                          "chose differently — RNG or network divergence, the serious case")
    elif "rewards" in first_fields:
        report["classification"] = "reward computation"
    else:
        report["classification"] = "7. unknown"
    return report


def run(base_url: str, seed: int, steps: int, *, instrumented: bool,
        probe_page: str, probe_actions: tuple[str, str], probe_every: int,
        setup_actions: list[dict] | None, checkpoint_dir: Path | None,
        p_return: float = 0.5, use_archive: bool = True) -> tuple[dict, QGapProbe | None]:
    """One training run. `instrumented=False` is the pre-diagnostics code path exactly.

    `use_archive=False` exists for the order-4 verification and is not optional there:
    `ArchiveStartWrapper.reset` assigns `self.env.setup_actions` on **every** reset, so a
    fixed route handed to the env would be overwritten by the archive's own (usually
    empty) one and the episodes would start at the landing page after all.
    """
    encoders = default_encoders()
    archive = Archive()
    action_features = ActionFeatureExtractor()

    def _make_env():  # noqa: ANN202
        inner = WebFunctionalEnv(
            base_url=base_url, max_steps=40, headless=True,
            setup_actions=list(setup_actions) if setup_actions else None,
        )
        if not use_archive:
            return Monitor(inner)
        return Monitor(ArchiveStartWrapper(inner, archive, p_return=p_return, seed=seed))

    venv = SemanticPerceptionWrapper(
        DummyVecEnv([_make_env]), encoders=encoders, action_features=action_features
    )
    args = agent_args(steps, "receipt.html" if instrumented else None)
    model = build_ac_agent(venv, seed, steps, args)
    probe = QGapProbe(probe_page, *probe_actions, probe_every=probe_every) if instrumented else None
    if instrumented and checkpoint_dir is not None:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        model.save(checkpoint_dir / f"rngcheck_seed{seed}_init.zip")
    try:
        model.learn(total_timesteps=steps, progress_bar=False, callback=probe)
    finally:
        venv.close()
    if instrumented and checkpoint_dir is not None:
        model.save(checkpoint_dir / f"rngcheck_seed{seed}_final.zip")
    return fingerprint(model), probe, run_metadata(model, venv, archive if use_archive else None)


def run_metadata(model, venv, archive) -> dict:  # noqa: ANN001
    """What the run did, read off the objects afterwards — no callback, no instrumentation.

    Everything here is a property the run already carries: SB3's `ep_info_buffer` is
    populated by `Monitor`, the archive keeps its own counters, and the replay buffer
    holds the whole transition record. Nothing was attached during training to collect
    it, which is the point — these replicates have to be the *uninstrumented* code path
    or they are not the control.
    """
    episodes = list(getattr(model, "ep_info_buffer", None) or [])
    meta = {
        "num_timesteps": int(model.num_timesteps),
        "exploration_rate_final": round(float(model.exploration_rate), 6),
        "episodes_finished": len(episodes),
        "episode_returns": [round(float(e["r"]), 4) for e in episodes],
        "episode_lengths": [int(e["l"]) for e in episodes],
        "mean_episode_return": (round(float(np.mean([e["r"] for e in episodes])), 4)
                                if episodes else None),
    }
    if archive is not None:
        meta["archive"] = {
            "cells": len(archive.cells),
            "returns_failed": archive.returns_failed,
            "shorter_routes_found": archive._replaced,
            "max_route_length": max((c.depth for c in archive.cells.values()), default=0),
            "urls_reached": sorted({c.url.rsplit("/", 1)[-1].split("?")[0]
                                    for c in archive.cells.values() if c.url}),
        }
    return meta


def replicate_study(args) -> None:  # noqa: ANN001
    """Pairwise agreement within a group of identical runs, instrumented and not.

    **Why this mode exists.** The A/B/C design assumes that if two identical runs agree
    then the harness is deterministic, full stop. Measured, that assumption is false: at
    200 steps one batch of four uninstrumented replicates agreed on all six pairs, and
    the next batch contained a single deviant run. One pair is therefore not evidence
    about either the harness or the instrumentation — which is exactly the mistake this
    project has made before with `--eval-episodes 5` on a greedy policy.

    `--groups` selects which are measured. Both is the comparative question (is the
    instrumentation what makes runs diverge); `uninstrumented` alone is the question
    "how deterministic is the harness", and at a 4,000-step budget the instrumented half
    costs another ~75 minutes to re-answer something the scripted-env bit-identity test
    in `tests/unit/test_diagnostics.py` already settles.

    Every replicate also records what it did — episode returns, archive coverage, and
    hashes of the action / observation / reward / priority / weight arrays — read off the
    objects **after** the run, so the uninstrumented replicates stay uninstrumented.
    """
    started = time.monotonic()
    common = dict(probe_page="index.html", probe_actions=("Start order", "Catalog"),
                  probe_every=args.probe_every, setup_actions=None, p_return=args.p_return)
    wanted = {
        "both": (("uninstrumented", False), ("instrumented", True)),
        "uninstrumented": (("uninstrumented", False),),
        "instrumented": (("instrumented", True),),
    }[args.groups]

    groups: dict[str, list[dict]] = {}
    metas: dict[str, list[dict]] = {}
    with serve_directory(SITE) as origin:
        base_url = f"{origin}/index.html"
        for label, instrumented in wanted:
            groups[label], metas[label] = [], []
            for index in range(args.replicates):
                logger.info("{} replicate {}/{} ({} steps)",
                            label, index + 1, args.replicates, args.steps)
                elapsed = time.monotonic()
                print_, _probe, meta = run(base_url, args.seed, args.steps,
                                           instrumented=instrumented, checkpoint_dir=None,
                                           **common)
                meta["wall_clock_s"] = round(time.monotonic() - elapsed, 1)
                meta["digests"] = digests(print_)
                groups[label].append(print_)
                metas[label].append(meta)
                logger.info("{} replicate {} done in {}s, mean return {}",
                            label, index, meta["wall_clock_s"], meta["mean_episode_return"])

    payload: dict = {
        "_purpose": "At this training budget, how often do two nominally identical runs "
                    "of the browser-based AC-DQN harness produce the same trajectory? A "
                    "single A-vs-B pair cannot separate an intermittent harness from an "
                    "instrumentation effect.",
        "_uninstrumented_means": "no callback, the unmodified PrioritizedNStepReplayBuffer, "
                                 "no checkpoint saves — the pre-diagnostics code path. "
                                 "Mid-training weight snapshots are deliberately absent: "
                                 "taking them needs a callback, and that would make the "
                                 "control an instrumented run.",
        "steps": args.steps, "seed": args.seed, "replicates": args.replicates,
        "p_return": args.p_return, "groups_measured": args.groups,
        "configuration": vars(agent_args(args.steps, None)) | {
            "episode_steps": 40, "encoders": "hash (default_encoders)",
            "archive": f"ArchiveStartWrapper(p_return={args.p_return}, seed={args.seed})",
            "device": "auto (CUDA if present)",
            "process": "all replicates run sequentially in ONE process, which is how "
                       "compare_agents.py runs its seeds",
        },
        "groups": {},
    }
    print(f"\n{args.replicates} replicates per group, seed {args.seed}, {args.steps} steps")
    print("=" * 78)
    for label, prints in groups.items():
        pairs, agreed = [], 0
        for i in range(len(prints)):
            for j in range(i + 1, len(prints)):
                report = divergence_report(prints[i], prints[j])
                agreed += report["identical"]
                pairs.append({"a": i, "b": j, **report})
        total = len(pairs)
        # Distinct trajectories is the honest summary: pairwise counts over N runs are
        # not independent, and "3 runs produced 2 distinct trajectories" says more than
        # "1 of 3 pairs agreed".
        classes: list[list[int]] = []
        for index in range(len(prints)):
            for group in classes:
                if divergence_report(prints[group[0]], prints[index])["identical"]:
                    group.append(index)
                    break
            else:
                classes.append([index])
        payload["groups"][label] = {
            "replicates": len(prints), "pairs": total, "identical_pairs": agreed,
            "divergent_pairs": total - agreed,
            "agreement_rate": round(agreed / total, 4) if total else None,
            "distinct_trajectories": len(classes),
            "equivalence_classes": classes,
            "pair_reports": pairs,
            "runs": metas[label],
        }
        print(f"\n  {label}: {agreed}/{total} pairs identical, "
              f"{len(classes)} distinct trajectories among {len(prints)} runs")
        for pair in pairs:
            if pair["identical"]:
                print(f"    run {pair['a']} vs {pair['b']}: IDENTICAL")
                continue
            print(f"    run {pair['a']} vs {pair['b']}: DIVERGED at step "
                  f"{pair.get('earliest_divergence_step')} "
                  f"(episode {pair.get('episode_of_divergence')}) — "
                  f"{pair['classification']}")
            blocks = pair.get("observation_blocks_at_divergence") or {}
            hot = {k: v for k, v in blocks.items() if v}
            if hot:
                print(f"        observation blocks differing there: {hot}")
            print(f"        actions identical through step "
                  f"{pair.get('actions_identical_through_step')}; "
                  f"{pair['differing_actions']} of {pair['transitions_compared']} "
                  f"actions differ overall")
    print("\n" + "=" * 78)
    payload["wall_clock_s"] = round(time.monotonic() - started, 1)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {args.out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps", type=int, default=600,
                        help="600 is the budget at which this harness was previously "
                             "shown bit-reproducible (PROJECT_CONTEXT, 2026-08-28).")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--probe-every", type=int, default=25)
    parser.add_argument(
        "--p-return", type=float, default=0.5,
        help="archive return probability, as the ac_dqn arm uses it. Pass 0 to take the "
             "archive out of the loop when locating a source of non-determinism.",
    )
    parser.add_argument(
        "--verify-order4", action="store_true",
        help="add a fourth run whose episodes start at order-4.html (replayed as "
             "setup_actions, outside the step budget), to confirm the probe resolves "
             "`Place order` / `Back` and reads the initial network there. A 600-step run "
             "from the landing page reaches order-4 far too rarely to test that.",
    )
    parser.add_argument(
        "--replicates", type=int, default=0, metavar="N",
        help="instead of A/B/C, run N uninstrumented and N instrumented replicates and "
             "compare every pair within each group. This is the mode that makes the "
             "A/B/C result interpretable: a single pair cannot tell an intermittent "
             "harness apart from an instrumentation effect, and this harness turns out "
             "to be intermittent. Reports the pairwise agreement rate of each group.",
    )
    parser.add_argument(
        "--groups", choices=["both", "uninstrumented", "instrumented"], default="both",
        help="with --replicates, which group(s) to run. 'uninstrumented' answers 'how "
             "deterministic is the harness' on its own, and at a 4,000-step budget it "
             "halves a ~2.5h measurement whose other half re-asks a question the "
             "scripted-env bit-identity test already settles.",
    )
    parser.add_argument("--out", type=Path,
                        default=REPO_ROOT / "reports" / "diag_probe_rng_neutrality.json")
    args = parser.parse_args()

    if args.replicates:
        replicate_study(args)
        return

    started = time.monotonic()
    payload: dict = {
        "_purpose": "Does the diagnostic instrumentation change training? Three runs: "
                    "two uninstrumented (the control for harness determinism) and one "
                    "instrumented.",
        "steps": args.steps, "seed": args.seed, "probe_every": args.probe_every,
        "p_return": args.p_return,
    }

    with serve_directory(SITE) as origin:
        base_url = f"{origin}/index.html"
        common = dict(
            # index.html is reached on step 0 of every episode, so the probe is
            # guaranteed to capture and to fire ~steps/probe_every times. Probing
            # order-4 here would test almost nothing: a 600-step run rarely reaches it.
            probe_page="index.html",
            probe_actions=("Start order", "Catalog"),
            probe_every=args.probe_every,
            setup_actions=None,
            checkpoint_dir=None,
            p_return=args.p_return,
        )
        logger.info("run A: uninstrumented")
        a, _ = run(base_url, args.seed, args.steps, instrumented=False, **common)
        logger.info("run B: uninstrumented (control for harness determinism)")
        b, _ = run(base_url, args.seed, args.steps, instrumented=False, **common)
        logger.info("run C: probe + tagging + checkpoints")
        # A subdirectory, so this check never sits beside the six preserved
        # diagnostic checkpoints README points at.
        checkpoints = REPO_ROOT / "models" / "checkpoints" / "rng_check"
        c, probe = run(base_url, args.seed, args.steps, instrumented=True,
                       **{**common, "checkpoint_dir": checkpoints})

        control_ok, control_notes = compare(a, b)
        neutral_ok, neutral_notes = compare(a, c)
        payload["control_A_vs_B_identical"] = control_ok
        payload["control_differences"] = control_notes
        payload["neutrality_A_vs_C_identical"] = neutral_ok
        payload["neutrality_differences"] = neutral_notes
        payload["fields_compared"] = len(a)
        payload["transitions_stored"] = a["stored"]
        payload["probe_readings"] = len(probe.trace) if probe else 0
        payload["probe_captured_at_step"] = probe.captured_at_step if probe else None

        if args.verify_order4:
            logger.info("run D: probe at order-4.html, episodes starting there")
            _, probe4 = run(
                base_url, args.seed, args.steps, instrumented=True,
                probe_page="order-4.html", probe_actions=("Place order", "Back"),
                probe_every=args.probe_every, setup_actions=ROUTE_TO_ORDER_4,
                checkpoint_dir=None, use_archive=False,
            )
            payload["order4_probe"] = probe4.to_dict() if probe4 else None

    payload["wall_clock_s"] = round(time.monotonic() - started, 1)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    print("\n" + "=" * 78)
    print(f"{args.steps}-step bit-identity check, seed {args.seed}, "
          f"{payload['fields_compared']} fields, {payload['transitions_stored']} transitions")
    print("=" * 78)
    print(f"control    A == B (harness is deterministic at this budget) : {control_ok}")
    for line in control_notes[:12]:
        print(line)
    print(f"neutrality A == C (instrumentation changes nothing)         : {neutral_ok}")
    for line in neutral_notes[:12]:
        print(line)
    print(f"probe readings taken: {payload['probe_readings']}, "
          f"captured at step {payload['probe_captured_at_step']}")
    if args.verify_order4 and payload.get("order4_probe"):
        initial = payload["order4_probe"]["initial_network"]
        print(f"order-4 probe: captured at step {payload['order4_probe']['captured_at_step']}, "
              f"initial-network gap {initial['gap'] if initial else None}")
    if not control_ok:
        print("\nINCONCLUSIVE: the two uninstrumented runs already differ, so any "
              "A-vs-C difference cannot be attributed to the instrumentation.")
        print("The RNG-neutrality claim is settled instead by test_diagnostics.py::"
              "test_instrumented_training_is_bit_identical_to_uninstrumented_training,")
        print("which replays a fixed observation stream so the browser cannot be the "
              "explanation.")
    print(f"\nWrote {args.out}")
    # Three outcomes, not two. Exiting 1 on an inconclusive control would report a
    # non-deterministic harness as an instrumentation defect, which is the specific
    # confusion this script exists to prevent.
    if not control_ok:
        sys.exit(2)
    sys.exit(0 if neutral_ok else 1)


if __name__ == "__main__":
    main()
