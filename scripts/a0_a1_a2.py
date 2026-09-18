"""A0 / A1 / A2 versus B and C: does label information alone resolve the deep decision?

**The confound this exists to break.** `reports/search_vs_bandit_vs_ac_dqn.*` compared a
non-learning hand-priority search (A0) against a contextual bandit (B) and AC-DQN (C), and
A0 lost -- but not by failing to explore. A0 reaches the deep fixture's final review page
in 85.5% of episodes, more often than either learned arm, and then completes 0.6% of the
time from there against AC-DQN's 84.6%. The entire measured gap is **one decision on one
page**, and on that page every feature A0 may read is identical across candidates: all of
them are navigational, in-viewport links carrying an href. The only discriminator is the
control's **label**, and A0's weights on the hashed-label block are exactly zero.

So A-vs-B varied *learning* and *access to label information* at the same time. A1 and A2
are the controls that separate them. Neither learns; both read the label:

| arm | learns? | reads labels? | isolates |
|---|---|---|---|
| A0 | no | **no** | search with no label access |
| A1 | no | yes, **lexically** | label access via a frozen word list |
| A2 | no | yes, **semantically** | label access via a frozen sentence embedding |
| B  | yes | yes (hashed) | learning an immediate action value |
| C  | yes | yes (hashed) | learning a bootstrapped one |

Everything else -- observation, action space, mask, reward, epsilon, episode budget,
evaluation protocol -- is shared and cancels.

**The definitions and the decision rule were frozen before this script existed**, in
`docs/methodology/2026-09-18-a0-a1-a2-frozen.md` (commit `0d104f3`). This script implements
them; it does not get to choose them. The rule it applies is §5's, on per-seed solve
counts, and it is applied mechanically by `apply_decision_rule` so that no threshold can be
selected after the numbers are seen.

**A1/A2 on this fixture are an UPPER BOUND, not clean evidence.** `reports/
audit_a1_provenance.json` establishes why: A1's word list was frozen 39 days after the
fixture was committed, by an author who had already traced A0's stall to `order-4.html`,
and its five fixture-relevant tokens rank the correct control first at 5 of 5 gated
decisions. The freeze is a real pre-registration against *tuning*; it is not independence
from the fixture. This experiment therefore answers "can label information resolve this
decision at all", which is what it was approved to answer, and not "would a prior written
without this fixture in view have resolved it".

**One documented deviation from the frozen protocol, and the argument for it.** The frozen
table lists a 4,000-step search budget per seed for the A arms. That phase is *not read by
evaluation*: `search_vs_bandit_vs_ac_dqn.run_search_phase` builds its own policy and its
own `Archive`, returns neither, and every evaluation row is built from a fresh policy by
`factories`. It exists to report search coverage at a budget matched to the learners'
training budget, and it costs ~1,478 s per arm per seed -- 9.9 hours across three A arms
and eight seeds, for a coverage table the decision rule does not consult. It is therefore
**off by default** (`--search-steps 0`) and available with `--search-steps 4000`. The
evaluation rows are bit-for-bit what they would be either way.

    python scripts/a0_a1_a2.py                     # decision rows, ~6 h
    python scripts/a0_a1_a2.py --search-steps 4000 # + the coverage table, ~16 h
    python scripts/a0_a1_a2.py --smoke             # 3 episodes, 2 seeds, wiring check
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# The protocol is imported, never restated. If a constant below ever diverges from the
# published A0/B/C run, it diverges because that run's own script changed.
from offline_eval_25ep import sha256  # noqa: E402
from search_vs_bandit_vs_ac_dqn import (  # noqa: E402
    EPISODE_STEPS,
    EPISODES,
    EPS_INITIAL,
    EVAL_EPSILON,
    P_RETURN,
    SEARCH_STEPS,
    SITE,
    TRAINED_ARMS,
    TrainedPolicy,
    evaluate,
    run_search_phase,
    summarise_arm,
)

from web_testing_agent.agents.action_features import ActionFeatureExtractor  # noqa: E402
from web_testing_agent.agents.hand_priority import HandPriorityPolicy  # noqa: E402
from web_testing_agent.agents.label_priors import (  # noqa: E402
    W_LABEL_PRIOR,
    AnchorProjectionPrior,
    LexicalCommitPrior,
)
from web_testing_agent.perception.vec_wrapper import default_encoders  # noqa: E402
from web_testing_agent.utils.local_server import serve_directory  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)
REPORTS = REPO_ROOT / "reports"
BOOTSTRAP_SEED = 0

#: The five rungs, plus the "search reading" of each A arm. Every A arm gets both readings
#: for the same reason the A0 run reported both: an argmax with per-episode counters and
#: one with run-level counters are two honest readings of what a search *is*, and
#: comparing A1 to A0 under different readings would not be comparing A1 to A0.
ARM_IDENTITY = {
    "a0_hand_priority": {
        "rung": "A0", "learns": False, "reads_labels": False,
        "note": "counters cleared every episode (stationary-policy reading)"},
    "a0_hand_priority_search": {
        "rung": "A0", "learns": False, "reads_labels": False,
        "note": "run-level counters carried across the 25 episodes (search reading)"},
    "a1_lexical": {
        "rung": "A1", "learns": False, "reads_labels": "frozen word list",
        "note": "stationary-policy reading"},
    "a1_lexical_search": {
        "rung": "A1", "learns": False, "reads_labels": "frozen word list",
        "note": "search reading"},
    "a2_anchor": {
        "rung": "A2", "learns": False, "reads_labels": "frozen MiniLM anchor axis",
        "note": "stationary-policy reading"},
    "a2_anchor_search": {
        "rung": "A2", "learns": False, "reads_labels": "frozen MiniLM anchor axis",
        "note": "search reading"},
    "contextual_bandit": {
        "rung": "B", "learns": True, "reads_labels": "hashed",
        "note": "re-evaluated from the finalized checkpoint, not retrained"},
    "ac_dqn": {
        "rung": "C", "learns": True, "reads_labels": "hashed",
        "note": "re-evaluated from the finalized checkpoint, not retrained"},
}

ARM_ORDER = list(ARM_IDENTITY)

#: The arms whose search phase is run when `--search-steps` is non-zero. One phase per
#: A rung; the two readings of a rung share it, exactly as in the A0/B/C run.
SEARCH_ARMS = ("a0_hand_priority", "a1_lexical", "a2_anchor")

#: §5 of the frozen document, as numbers. Not to be edited after a result is seen.
DECISION_RULE = {
    "unit": "the seed; statistic is the per-seed completion count out of 25",
    "match_within": 5,
    "match_min_seeds": 6,
    "match_median_fraction": 0.60,
    "closes_gap_median_fraction": 0.50,
    "reference_arm": "contextual_bandit",
    "_source": "docs/methodology/2026-09-18-a0-a1-a2-frozen.md §5, commit 0d104f3",
}


# -- the arms ------------------------------------------------------------------------------


def build_factories(seed: int, encoders, anchor_prior: AnchorProjectionPrior) -> dict:
    """One policy factory per arm, at one seed.

    `anchor_prior` is constructed once for the whole run and shared: it holds a frozen
    encoder and a memo of label scores, and re-encoding the same three dozen labels per
    seed would cost minutes and change nothing. The prior carries no episode state, so
    sharing it across seeds cannot leak anything between them -- the only mutable field is
    the label -> score cache, whose contents are a pure function of the label.
    """
    factories = {
        "a0_hand_priority": lambda s=seed: HandPriorityPolicy(
            epsilon=EVAL_EPSILON, seed=s, carry_counts_across_episodes=False),
        "a0_hand_priority_search": lambda s=seed: HandPriorityPolicy(
            epsilon=EVAL_EPSILON, seed=s, carry_counts_across_episodes=True),
        "a1_lexical": lambda s=seed: HandPriorityPolicy(
            epsilon=EVAL_EPSILON, seed=s, carry_counts_across_episodes=False,
            label_prior=LexicalCommitPrior(), label_prior_weight=W_LABEL_PRIOR),
        "a1_lexical_search": lambda s=seed: HandPriorityPolicy(
            epsilon=EVAL_EPSILON, seed=s, carry_counts_across_episodes=True,
            label_prior=LexicalCommitPrior(), label_prior_weight=W_LABEL_PRIOR),
        "a2_anchor": lambda s=seed: HandPriorityPolicy(
            epsilon=EVAL_EPSILON, seed=s, carry_counts_across_episodes=False,
            label_prior=anchor_prior, label_prior_weight=W_LABEL_PRIOR),
        "a2_anchor_search": lambda s=seed: HandPriorityPolicy(
            epsilon=EVAL_EPSILON, seed=s, carry_counts_across_episodes=True,
            label_prior=anchor_prior, label_prior_weight=W_LABEL_PRIOR),
    }
    for arm, (directory, cls) in TRAINED_ARMS.items():
        path = directory / f"{arm}_seed{seed}_final.zip"

        def _trained(p=path, c=cls, s=seed):
            model = c.load(str(p), device="cpu")
            return TrainedPolicy(model, encoders, epsilon=EVAL_EPSILON, masked=True,
                                 seed=s, action_features=ActionFeatureExtractor())
        factories[arm] = _trained
    return factories


def search_policy(arm: str, seed: int, anchor_prior: AnchorProjectionPrior):
    """The search-phase policy for one A rung. Run-level counters, as in the A0 run.

    Epsilon is set to `EPS_INITIAL` here and then driven by `run_search_phase`'s own
    `ScheduledEpsilon`, which reproduces the learners' linear schedule. A0's arm passes no
    prior at all rather than a zero-weighted one, so its scores are the same floats the
    published A0 search phase produced.
    """
    priors = {
        "a0_hand_priority": (None, 0.0),
        "a1_lexical": (LexicalCommitPrior(), W_LABEL_PRIOR),
        "a2_anchor": (anchor_prior, W_LABEL_PRIOR),
    }
    prior, weight = priors[arm]
    return HandPriorityPolicy(epsilon=EPS_INITIAL, seed=seed,
                              carry_counts_across_episodes=True,
                              label_prior=prior, label_prior_weight=weight)


# -- the frozen decision rule, applied mechanically --------------------------------------------


def apply_decision_rule(rows_by_arm: dict, episodes: int) -> dict:
    """§5 of the frozen document, evaluated. No threshold is chosen here.

    Applied to A1 as well as A2, because the same question -- does a fixed prior match the
    learned arm -- is the one A1 answers lexically. The document specifies the rule for A2;
    running it for A1 adds a row and changes nothing about how A2 is read.
    """
    reference = DECISION_RULE["reference_arm"]
    if reference not in rows_by_arm or not rows_by_arm[reference]:
        return {"_error": f"no rows for the reference arm {reference}"}

    by_seed = {row["seed"]: row["completion_count"] for row in rows_by_arm[reference]}
    reference_median = st.median(by_seed.values())

    verdicts = {}
    for arm in ("a1_lexical", "a2_anchor"):
        rows = rows_by_arm.get(arm) or []
        if not rows:
            continue
        paired = [(row["seed"], row["completion_count"], by_seed[row["seed"]])
                  for row in rows if row["seed"] in by_seed]
        within = [seed for seed, arm_count, ref_count in paired
                  if abs(arm_count - ref_count) <= DECISION_RULE["match_within"]]
        arm_median = st.median(count for _s, count, _r in paired)
        fraction = (arm_median / reference_median) if reference_median else float("inf")

        # The frozen rule is written for 8 paired seeds and requires 6 of them. Below 6
        # paired seeds the match criterion is arithmetically unreachable, so a partial run
        # -- and this report is persisted after every seed -- would otherwise print
        # "CLOSES MOST OF THE A0 GAP" for an arm the rule simply could not have matched
        # yet. Say so instead of letting n do the arguing.
        reachable = len(paired) >= DECISION_RULE["match_min_seeds"]
        matches = (reachable and len(within) >= DECISION_RULE["match_min_seeds"]
                   and fraction >= DECISION_RULE["match_median_fraction"])
        closes = fraction >= DECISION_RULE["closes_gap_median_fraction"]

        verdicts[arm] = {
            "match_criterion_reachable_at_this_n": reachable,
            "_partial_run_warning": None if reachable else (
                f"Only {len(paired)} paired seeds. The frozen rule needs "
                f"{DECISION_RULE['match_min_seeds']}, so MATCHES B cannot be declared yet "
                f"regardless of the numbers. This verdict is provisional."),
            "paired_seeds": [{"seed": s, arm: a, reference: r, "difference": a - r}
                             for s, a, r in paired],
            "n_paired": len(paired),
            "seeds_within_5": len(within),
            "seeds_within_5_list": within,
            "arm_median": arm_median,
            "reference_median": reference_median,
            "median_fraction_of_reference": round(fraction, 4)
            if reference_median else None,
            "declared_match": matches,
            "declared_closes_most_of_the_gap": closes,
            "verdict": ("MATCHES B" if matches
                        else "CLOSES MOST OF THE A0 GAP" if closes
                        else f"INCONCLUSIVE at n={len(paired)}")
            + ("" if reachable else "  (PROVISIONAL: too few seeds for the match rule)"),
            "_rule": (f"match requires |diff| <= {DECISION_RULE['match_within']} on >= "
                      f"{DECISION_RULE['match_min_seeds']} of {len(paired)} seeds AND "
                      f"median >= {DECISION_RULE['match_median_fraction']:.0%} of B's "
                      f"median"),
        }

    a2 = verdicts.get("a2_anchor", {})
    if a2 and not a2.get("match_criterion_reachable_at_this_n"):
        return {
            "rule": DECISION_RULE,
            "reference_arm": reference,
            "episodes_per_seed": episodes,
            "verdicts": verdicts,
            "conditional_experiment": (
                f"UNDECIDED. Only {a2['n_paired']} paired seeds so far; the frozen rule "
                f"needs {DECISION_RULE['match_min_seeds']}. Neither CASE A nor CASE B may "
                f"be declared from a partial run."),
            "_frozen_before_results": (
                "Every threshold above is read from DECISION_RULE, which transcribes §5 of "
                "a document committed in 0d104f3 before any arm was implemented."),
        }
    if a2.get("declared_match"):
        conditional = (
            "CASE A. A2 matches B under the frozen rule, so a fixed prior already explains "
            "the decision and there is little evidence that learning is necessary for it. "
            "The conditional three-way learned-feature experiment -- B(lexical) vs "
            "B(anchor) vs B(raw embedding) -- is NOT run. Choose the primary fixed prior "
            "on the out-of-list vocabulary probe.")
    elif a2:
        conditional = (
            "CASE B. A2 does not match B under the frozen rule, so A2 meaningfully trails "
            "B and the conditional three-way learned-feature experiment IS licensed: "
            "B(lexical) vs B(anchor projection) vs B(raw sentence embedding), which tests "
            "whether the LEARNED component benefits from the representation.")
    else:
        conditional = "No A2 rows; the conditional experiment cannot be decided."

    return {
        "rule": DECISION_RULE,
        "reference_arm": reference,
        "episodes_per_seed": episodes,
        "verdicts": verdicts,
        "conditional_experiment": conditional,
        "_frozen_before_results": (
            "Every threshold above is read from DECISION_RULE, which transcribes §5 of a "
            "document committed in 0d104f3 before any arm was implemented."),
    }


# -- reporting ---------------------------------------------------------------------------------


def git_state() -> dict:
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              cwd=REPO_ROOT).stdout.strip()
    return {"head": run("rev-parse", "HEAD"),
            "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(run("status", "--porcelain"))}


def assemble(args, rows_by_arm: dict, search_rows: list, manifest: list,
             anomalies: list, elapsed: float, episodes: int, complete: bool) -> dict:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    summaries = {arm: summarise_arm(rows, rng, episodes)
                 for arm, rows in rows_by_arm.items() if rows}
    return {
        "_purpose": "A0/A1/A2 vs B and C: does label information alone resolve the deep "
                    "fixture's one hard decision?",
        "_complete": complete,
        "_smoke": args.smoke,
        "_inferential_unit": "the seed / checkpoint, never the episode",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": git_state(),
        "protocol": {
            "fixture": str(SITE.relative_to(REPO_ROOT)),
            "seeds": list(args.seeds),
            "episodes_per_seed": episodes,
            "episode_steps": EPISODE_STEPS,
            "eval_epsilon": EVAL_EPSILON,
            "search_steps_per_arm_per_seed": args.search_steps,
            "archive_p_return_during_search": P_RETURN,
            "evaluation_start": "landing page, archive disabled",
            "judge": "disabled (NullRewardModel)",
            "_source": "imported from scripts/search_vs_bandit_vs_ac_dqn.py, not restated",
            "_deviation": (
                "search_steps=0 by default. The search phase builds its own policy and "
                "archive and returns neither, so no evaluation row reads it; it reports "
                "search coverage only. Run with --search-steps 4000 to produce that table."
            ) if not args.search_steps else "none",
        },
        "arm_identity": ARM_IDENTITY,
        "provenance_warning": (
            "A1's word list is NOT independent of this fixture (reports/"
            "audit_a1_provenance.json): frozen 39 days after the fixture, by an author who "
            "had traced A0's stall to order-4.html, and its five fixture-relevant tokens "
            "rank the correct control first at 5 of 5 gated decisions. A1/A2 numbers here "
            "are an UPPER BOUND on what a generic fixed prior achieves."),
        "checkpoints": manifest,
        "all_checkpoints_unmodified": all(
            e.get("unmodified", True) for e in manifest) if manifest else None,
        "per_checkpoint": rows_by_arm,
        "search_phase": search_rows,
        "arm_summaries": summaries,
        "decision": apply_decision_rule(rows_by_arm, episodes),
        "anomalies": anomalies,
        "wall_clock_s": round(elapsed, 1),
        "limitations": LIMITATIONS,
    }


LIMITATIONS = [
    "**A1 and A2 are not independent of this fixture.** See `reports/"
    "audit_a1_provenance.json`. They are frozen against tuning, not against having been "
    "written by someone who knew which decision they had to resolve. Every number here is "
    "an upper bound on a generic prior's performance.",
    "**The A arms have no training variance.** Eight rows of a non-learning policy vary "
    "only in the epsilon stream, so their spread understates what a spread means for B and "
    "C, and their intervals are narrow for a reason that is not a property of the method.",
    "**The A arms read two features that are structurally dead for B and C.** `taken_here` "
    "and `taken_this_episode` are written only by `ActionFeatureExtractor.record_taken()`, "
    "which nothing on the RL path calls. This is an information advantage for every A arm, "
    "and it is identical across A0, A1 and A2 -- so it does not affect A0-vs-A1-vs-A2, "
    "which is what this experiment is for.",
    "**B and C were not retrained.** Their finalized checkpoints were re-evaluated, with "
    "SHA-256 verified before and after.",
    "**The browser harness is intermittently non-deterministic on this configuration** "
    "(`reports/diag_harness_determinism.json`). Every row here is a single run.",
    "**n=8 seeds, one fixture, one budget.** The deep fixture is excluded from all transfer "
    "and generalization evaluation under the current project direction. Nothing here is "
    "evidence about transfer.",
    "**Completion is a proxy.** Reaching receipt.html is what makes DEEP-01 findable; it is "
    "not finding it. The deterministic ceiling on this fixture is 0.",
]


def persist(stem: Path, payload: dict) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, default=float), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(range(8)))
    parser.add_argument("--episodes", type=int, default=EPISODES)
    parser.add_argument("--search-steps", type=int, default=0,
                        help=f"0 (default) skips the coverage phase; {SEARCH_STEPS} runs it")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--out-stem", type=Path, default=REPORTS / "a0_a1_a2")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stem = args.out_stem.with_name(args.out_stem.name + "_smoke") if args.smoke else args.out_stem
    episodes = 3 if args.smoke else args.episodes
    search_steps = 80 if (args.smoke and args.search_steps) else args.search_steps
    if args.smoke:
        args.seeds = args.seeds[:2]

    # Fail before spending hours if a checkpoint is missing, and hash them first.
    manifest = []
    for arm, (directory, _cls) in TRAINED_ARMS.items():
        for seed in args.seeds:
            path = directory / f"{arm}_seed{seed}_final.zip"
            if not path.exists():
                raise SystemExit(f"STOP: missing checkpoint {path}")
            manifest.append({"arm": arm, "seed": seed, "path": str(path),
                             "sha256_before": sha256(path), "bytes": path.stat().st_size})

    encoders = default_encoders()
    # Built once, before the server starts, so a model download cannot be mistaken for a
    # slow first episode. Shared across seeds; see `build_factories`.
    anchor_prior = AnchorProjectionPrior()
    logger.info("A2 anchor prior ready ({} anchors, projection {})",
                len(anchor_prior._positive) + len(anchor_prior._negative),
                anchor_prior.to_dict()["projection_fingerprint"])

    rows_by_arm: dict[str, list[dict]] = {arm: [] for arm in ARM_ORDER}
    search_rows: list[dict] = []
    anomalies: list[dict] = []
    started = time.monotonic()

    with serve_directory(SITE) as origin:
        base_url = f"{origin}/index.html"
        logger.info("deep fixture at {}", base_url)

        for seed in args.seeds:
            logger.info("############ seed {} ############", seed)

            if search_steps:
                for arm in SEARCH_ARMS:
                    try:
                        row = run_search_phase(
                            base_url, seed, search_steps, EPISODE_STEPS,
                            policy=search_policy(arm, seed, anchor_prior))
                        row["arm"] = arm
                        search_rows.append(row)
                        logger.info("search {} seed {}: {}", arm, seed, row["flow"])
                    except Exception as exc:  # noqa: BLE001 - recorded, never hidden
                        anomalies.append({"arm": f"{arm}_search_phase", "seed": seed,
                                          "what": f"search phase raised {exc!r}",
                                          "action_taken": "recorded; seed retained"})
                        logger.error("search {} seed {} FAILED: {}", arm, seed, exc)

            factories = build_factories(seed, encoders, anchor_prior)
            for arm in ARM_ORDER:
                try:
                    row = evaluate(base_url, arm, seed, factories[arm], episodes)
                    row["errors"] = None
                    if arm in TRAINED_ARMS:
                        row["checkpoint"] = str(
                            TRAINED_ARMS[arm][0] / f"{arm}_seed{seed}_final.zip")
                    rows_by_arm[arm].append(row)
                    logger.info("{} seed {}: {}/{} completions", arm, seed,
                                row["completion_count"], episodes)
                    if row["invalid_actions"]:
                        anomalies.append({
                            "arm": arm, "seed": seed,
                            "what": f"{row['invalid_actions']} out-of-range selections",
                            "action_taken": "recorded; run retained in every analysis"})
                except Exception as exc:  # noqa: BLE001
                    anomalies.append({"arm": arm, "seed": seed,
                                      "what": f"evaluation raised {exc!r}",
                                      "action_taken": "recorded; seed retained, not rerun"})
                    logger.error("{} seed {} FAILED: {}", arm, seed, exc)

            # Persisted after every seed: a run interrupted at seed k leaves a complete,
            # analysable report at n=k rather than nothing.
            persist(stem, assemble(args, rows_by_arm, search_rows, manifest, anomalies,
                                   time.monotonic() - started, episodes, complete=False))

    for entry in manifest:
        entry["sha256_after"] = sha256(Path(entry["path"]))
        entry["unmodified"] = entry["sha256_after"] == entry["sha256_before"]

    payload = assemble(args, rows_by_arm, search_rows, manifest, anomalies,
                       time.monotonic() - started, episodes, complete=True)
    persist(stem, payload)

    print("=" * 78)
    print("A0 / A1 / A2  vs  B and C")
    print("=" * 78)
    print(f"\n{'arm':26} {'rung':5} per-seed completions /{episodes}")
    for arm in ARM_ORDER:
        rows = rows_by_arm[arm]
        if not rows:
            continue
        counts = [r["completion_count"] for r in rows]
        print(f"{arm:26} {ARM_IDENTITY[arm]['rung']:5} {counts}  "
              f"mean {st.mean(counts):.2f}  median {st.median(counts):.1f}")

    decision = payload["decision"]
    print("\nFROZEN DECISION RULE (§5, commit 0d104f3)")
    for arm, block in decision.get("verdicts", {}).items():
        print(f"  {arm:14} within-5 on {block['seeds_within_5']}/{block['n_paired']} seeds, "
              f"median {block['arm_median']} vs B's {block['reference_median']} "
              f"({block['median_fraction_of_reference']}) -> {block['verdict']}")
    print(f"\n{decision.get('conditional_experiment', '')}")
    print(f"\nWrote {stem.with_suffix('.json')}  ({payload['wall_clock_s'] / 3600:.2f} h)")


if __name__ == "__main__":
    main()
