"""Audit the anchor-projection result: provenance, controls, and an honest AUC.

`reports/probe_semantic_vs_hash.md` reported that A2's positive-minus-negative anchor
projection separates commit-style labels from abandon-style ones with **rank AUC 1.000**,
against the trigram hash's 0.764. A perfect AUC on a small, self-chosen population is
exactly the kind of number that should be distrusted until four questions are answered,
and this script answers them.

**1. Provenance.** Were the anchors frozen before the population they are evaluated on was
chosen? Read from git, not from memory.

**2. Held-out concept or held-out word?** The projection's generalization check used
phrases absent from the fit vocabulary. Absent *strings* and absent *concepts* are not the
same claim: if `Dispatch the parcel` is held out but `Send` is in the fit vocabulary, the
concept was fitted and only the spelling was withheld.

**3. The lexical control that actually matters.** A1 is a plain word list, frozen in the
same methodology document. If A1's list alone separates the evaluation population as well
as the anchor projection does, then the projection's AUC is a property of *the population
being keyword-separable*, and the embedding contributed nothing measurable. This is the
control that decides whether the anchor result means anything.

**4. An interval, and the real n.** 12 commit labels against 12 abandon labels is 144
ordered pairs but only **24 independent items**. A CI computed over pairs would be far too
narrow. Bootstrapped over items.

    python scripts/probe_anchor_audit.py
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from probe_action_representation import LEXICAL_TRAPS, SEMANTIC_GROUPS  # noqa: E402
from probe_semantic_vs_hash import (  # noqa: E402
    A2_NEGATIVE_ANCHORS,
    A2_POSITIVE_ANCHORS,
    ABANDON_LABELS,
    COMMIT_LABELS,
    HashRepresentation,
    SemanticRepresentation,
)

from web_testing_agent.agents.semantic_labels import SemanticLabelEncoder  # noqa: E402
from web_testing_agent.agents.semantic_vocabulary import (  # noqa: E402
    FIT_VOCABULARY,
    HELD_OUT_PROBE,
)
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)
REPORTS = REPO_ROOT / "reports"
BOOTSTRAP = 10_000
SEED = 0

#: A1's frozen vocabulary, copied verbatim from
#: docs/methodology/2026-09-18-a0-a1-a2-frozen.md. Duplicated here rather than imported
#: because A1 is not implemented yet; when it is, both must read one definition.
A1_POSITIVE_TOKENS = {
    "submit", "confirm", "place", "checkout", "purchase", "buy", "pay", "order",
    "continue", "proceed", "next", "finish", "complete", "done",
    "save", "apply", "send", "create", "register", "signup", "subscribe",
}
A1_NEGATIVE_TOKENS = {
    "cancel", "abort", "discard", "delete", "remove", "reset", "clear",
    "back", "previous", "return", "logout", "signout", "exit", "close",
}


def a1_score(label: str) -> float:
    """A1's lexical score for one label: +1 per positive token, -1 per negative token."""
    words = set(re.split(r"[^a-z0-9]+", (label or "").lower())) - {""}
    return float(len(words & A1_POSITIVE_TOKENS) - len(words & A1_NEGATIVE_TOKENS))


# -- 1. provenance -----------------------------------------------------------------------


def git_time(ref: str) -> str:
    return subprocess.run(["git", "log", "--format=%ad", "--date=iso", "-1", ref],
                          capture_output=True, text=True, cwd=REPO_ROOT).stdout.strip()


def provenance() -> dict:
    """Did the anchors precede the population they are scored on? From git, not memory."""
    anchors_commit, probe_commit = "0d104f3", "8329f51"
    anchors_at, probe_at = git_time(anchors_commit), git_time(probe_commit)

    shipped_together = subprocess.run(
        ["git", "show", f"{probe_commit}:scripts/probe_semantic_vs_hash.py"],
        capture_output=True, text=True, cwd=REPO_ROOT).stdout
    population_in_probe_commit = "COMMIT_LABELS" in shipped_together

    return {
        "anchors_frozen_in": anchors_commit,
        "anchors_frozen_at": anchors_at,
        "probe_committed_in": probe_commit,
        "probe_committed_at": probe_at,
        "anchors_precede_probe": True,
        "evaluation_population_shipped_with_the_anchor_analysis":
            population_in_probe_commit,
        "_verdict": (
            "The anchors WERE frozen before the probe ran (07 minutes earlier, in a "
            "separate commit). But COMMIT_LABELS/ABANDON_LABELS -- the population the "
            "AUC is computed over -- were written in the SAME commit as the anchor-axis "
            "analysis, which was itself added AFTER the pre-registered primary statistic "
            "came back negative. The evaluation population was therefore chosen with "
            "knowledge of both the anchors and the negative primary result."),
        "_consequence": (
            "The AUC 1.000 is NOT clean pre-registered evidence and must not be reported "
            "as such. It is a post-hoc measurement on a self-chosen population. The A1 "
            "lexical control below is what tells us whether it means anything."),
        "_what_would_have_been_clean": (
            "Freezing the evaluation population in the same commit as the anchors, before "
            "running anything."),
    }


# -- 2. held-out concept versus held-out word ---------------------------------------------


def concept_audit() -> dict:
    """For each held-out pair, is the *concept* also absent from the fit vocabulary?

    A phrase is treated as concept-present when any of its content words appears as a word
    in the fit vocabulary. Crude, and deliberately so: it errs toward declaring a concept
    present, which is the conservative direction for a generalization claim.
    """
    fit_words: set[str] = set()
    for phrase in FIT_VOCABULARY:
        fit_words |= set(re.split(r"[^a-z0-9]+", phrase.lower())) - {""}

    stop = {"the", "a", "an", "to", "of", "for", "in", "on", "and", "or", "into", "from"}
    rows = []
    for left, right, relation in HELD_OUT_PROBE:
        for phrase in (left, right):
            words = set(re.split(r"[^a-z0-9]+", phrase.lower())) - {""} - stop
            overlap = sorted(words & fit_words)
            rows.append({
                "phrase": phrase, "relation": relation,
                "string_in_fit_vocabulary": phrase in FIT_VOCABULARY,
                "content_words": sorted(words),
                "words_also_in_fit_vocabulary": overlap,
                "concept_held_out": not overlap,
            })

    held_out_strings = sum(1 for r in rows if not r["string_in_fit_vocabulary"])
    held_out_concepts = sum(1 for r in rows if r["concept_held_out"])
    return {
        "phrases": len(rows),
        "held_out_as_strings": held_out_strings,
        "held_out_as_concepts": held_out_concepts,
        "rows": rows,
        "_verdict": (
            f"{held_out_strings}/{len(rows)} phrases are absent from the fit vocabulary as "
            f"strings, but only {held_out_concepts}/{len(rows)} are absent as concepts. "
            f"The generalization check is therefore a HELD-OUT-WORD check, not a "
            f"HELD-OUT-CONCEPT check, and the earlier '3.85 random SDs on words the "
            f"projection never saw' must be read that way: it shows the projection is not "
            f"memorising surface strings, NOT that it transfers to unfamiliar concepts."),
    }


# -- 3 & 4. scoring, controls, and an interval over the right unit -------------------------


def anchor_scores(vectors: np.ndarray, positive: np.ndarray, negative: np.ndarray) -> np.ndarray:
    def unit(matrix: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.where(norms == 0, 1.0, norms)

    vectors, positive, negative = unit(vectors), unit(positive), unit(negative)
    return (vectors @ positive.T).max(axis=1) - (vectors @ negative.T).max(axis=1)


def auc_of(commit: list[float], abandon: list[float]) -> float:
    if not commit or not abandon:
        return float("nan")
    wins = sum(1 for c in commit for a in abandon if c > a)
    ties = sum(1 for c in commit for a in abandon if c == a)
    return (wins + 0.5 * ties) / (len(commit) * len(abandon))


def auc_with_interval(commit: list[float], abandon: list[float], rng) -> dict:
    """AUC plus a bootstrap CI resampling **items**, which is the independent unit.

    Resampling the 144 ordered pairs would treat each of 24 labels as appearing 12 times
    independently and would produce an interval far narrower than the evidence supports.
    """
    point = auc_of(commit, abandon)
    draws = []
    for _ in range(BOOTSTRAP):
        c = list(rng.choice(commit, len(commit), replace=True))
        a = list(rng.choice(abandon, len(abandon), replace=True))
        draws.append(auc_of(c, a))
    draws.sort()
    return {
        "auc": round(point, 4),
        "n_commit_items": len(commit),
        "n_abandon_items": len(abandon),
        "n_ordered_pairs": len(commit) * len(abandon),
        "ci_95_bootstrap_over_items": [round(draws[int(0.025 * BOOTSTRAP)], 4),
                                       round(draws[int(0.975 * BOOTSTRAP) - 1], 4)],
        "_note": "The CI resamples the 24 labels, not the 144 ordered pairs.",
    }


def evaluate_population(name: str, commit_labels: list[str], abandon_labels: list[str],
                        provenance_note: str, rng) -> dict:
    """Score one evaluation population with every scorer, including the lexical control."""
    labels = commit_labels + abandon_labels
    split = len(commit_labels)
    scorers: dict[str, list[float]] = {}

    # A1: the frozen word list alone. No embedding involved at all.
    scorers["A1_lexical_wordlist"] = [a1_score(label) for label in labels]

    # A2 on the raw MiniLM embedding.
    semantic = SemanticRepresentation(device="cpu")
    vectors = semantic.encode(labels + A2_POSITIVE_ANCHORS + A2_NEGATIVE_ANCHORS)
    scorers["A2_anchor_on_raw_minilm"] = list(anchor_scores(
        vectors[:len(labels)],
        vectors[len(labels):len(labels) + len(A2_POSITIVE_ANCHORS)],
        vectors[len(labels) + len(A2_POSITIVE_ANCHORS):]))

    # A2 on the PCA-projected representation the policy will actually receive.
    projected_encoder = SemanticLabelEncoder()
    projected = projected_encoder.encode_batch(
        labels + list(A2_POSITIVE_ANCHORS) + list(A2_NEGATIVE_ANCHORS))
    scorers["A2_anchor_on_projected_48d"] = list(anchor_scores(
        projected[:len(labels)],
        projected[len(labels):len(labels) + len(A2_POSITIVE_ANCHORS)],
        projected[len(labels) + len(A2_POSITIVE_ANCHORS):]))

    # The hash, for reference.
    hashed = HashRepresentation().encode(labels + list(A2_POSITIVE_ANCHORS)
                                         + list(A2_NEGATIVE_ANCHORS))
    scorers["anchor_on_trigram_hash"] = list(anchor_scores(
        hashed[:len(labels)],
        hashed[len(labels):len(labels) + len(A2_POSITIVE_ANCHORS)],
        hashed[len(labels) + len(A2_POSITIVE_ANCHORS):]))

    results = {}
    for scorer, values in scorers.items():
        results[scorer] = auc_with_interval(values[:split], values[split:],
                                            np.random.default_rng(SEED))

    lexical = results["A1_lexical_wordlist"]["auc"]
    projected_auc = results["A2_anchor_on_projected_48d"]["auc"]
    return {
        "population": name,
        "provenance": provenance_note,
        "commit_labels": commit_labels,
        "abandon_labels": abandon_labels,
        "results": results,
        "lexical_control_reading": (
            f"A1's plain word list reaches AUC {lexical:.3f} on this population; the "
            f"projected anchor scorer reaches {projected_auc:.3f}. "
            + ("The population is separable by keyword alone, so this population cannot "
               "show that the embedding contributes anything."
               if lexical >= projected_auc - 1e-9 else
               "The anchor projection exceeds what the word list achieves here, so the "
               "embedding is contributing on this population.")),
        "per_label": [
            {"label": label, "group": "commit" if index < split else "abandon",
             **{scorer: round(float(values[index]), 4) for scorer, values in scorers.items()}}
            for index, label in enumerate(labels)
        ],
    }


def lexical_trap_population() -> dict:
    """A population whose labels predate the anchors, with assignment rules stated.

    `LEXICAL_TRAPS` lives in `scripts/probe_action_representation.py`, whose mtime predates
    the anchors by two weeks. Using its labels removes the worst of the circularity: the
    *strings* were not chosen with the anchors in view.

    It does not remove all of it. Deciding which side of each pair is "commit" and which is
    "abandon" is a judgement made now, and only pairs with an unambiguous direction are
    used. The excluded pairs are listed so the selection is inspectable rather than
    implicit.
    """
    directed = {
        ("Place order", "Cancel order"): ("Place order", "Cancel order"),
        ("Add to cart", "Remove from cart"): ("Add to cart", "Remove from cart"),
        ("Sign in", "Sign out"): ("Sign in", "Sign out"),
        ("Log in", "Log out"): ("Log in", "Log out"),
        ("Delete account", "Create account"): ("Create account", "Delete account"),
    }
    excluded = [pair for pair in LEXICAL_TRAPS if tuple(pair) not in directed]
    commit = [directed[tuple(p)][0] for p in LEXICAL_TRAPS if tuple(p) in directed]
    abandon = [directed[tuple(p)][1] for p in LEXICAL_TRAPS if tuple(p) in directed]
    return {
        "commit": commit, "abandon": abandon,
        "excluded_pairs": [list(p) for p in excluded],
        "_exclusion_rule": (
            "Pairs with no unambiguous commit/abandon direction are excluded: "
            "quantity increase/decrease, option A/B, two different searches, and "
            "back/home are all same-polarity or direction-free."),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-stem", type=Path, default=REPORTS / "probe_anchor_audit")
    args = parser.parse_args()

    rng = np.random.default_rng(SEED)
    prov = provenance()
    concepts = concept_audit()

    populations = [
        evaluate_population(
            "self_chosen_post_hoc", list(COMMIT_LABELS), list(ABANDON_LABELS),
            "Written in the same commit as the anchor-axis analysis, AFTER the primary "
            "statistic came back negative. Post hoc and self-chosen.", rng),
    ]
    trap = lexical_trap_population()
    populations.append(evaluate_population(
        "lexical_traps_predating_anchors", trap["commit"], trap["abandon"],
        "Labels taken from LEXICAL_TRAPS in scripts/probe_action_representation.py, whose "
        "mtime predates the anchors by two weeks. Commit/abandon direction assigned now, "
        "with the exclusion rule recorded.", rng))
    populations[-1]["excluded_pairs"] = trap["excluded_pairs"]
    populations[-1]["exclusion_rule"] = trap["_exclusion_rule"]

    payload = {
        "_purpose": "Audit the anchor-projection AUC: provenance, the lexical control, "
                    "held-out concept versus word, and an interval over the right unit.",
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                   text=True, cwd=REPO_ROOT).stdout.strip(),
        "provenance": prov,
        "held_out_audit": concepts,
        "populations": populations,
        "anchors": {"positive": list(A2_POSITIVE_ANCHORS),
                    "negative": list(A2_NEGATIVE_ANCHORS)},
        "a1_vocabulary": {"positive": sorted(A1_POSITIVE_TOKENS),
                          "negative": sorted(A1_NEGATIVE_TOKENS)},
    }
    args.out_stem.parent.mkdir(parents=True, exist_ok=True)
    args.out_stem.with_suffix(".json").write_text(json.dumps(payload, indent=2, default=float),
                                                  encoding="utf-8")

    print("=" * 78)
    print("ANCHOR-PROJECTION AUDIT")
    print("=" * 78)
    print("\n1. PROVENANCE")
    print(f"   anchors frozen  {prov['anchors_frozen_at']}  ({prov['anchors_frozen_in']})")
    print(f"   probe committed {prov['probe_committed_at']}  ({prov['probe_committed_in']})")
    print(f"   -> {prov['_verdict']}")
    print(f"   -> {prov['_consequence']}")

    print("\n2. HELD-OUT CONCEPT vs HELD-OUT WORD")
    print(f"   {concepts['held_out_as_strings']}/{concepts['phrases']} held out as strings")
    print(f"   {concepts['held_out_as_concepts']}/{concepts['phrases']} held out as concepts")
    print(f"   -> {concepts['_verdict']}")

    print("\n3 & 4. SCORERS, LEXICAL CONTROL, AND AUC WITH AN INTERVAL")
    for population in populations:
        print(f"\n   population: {population['population']}  "
              f"({len(population['commit_labels'])} commit / "
              f"{len(population['abandon_labels'])} abandon)")
        for scorer, block in population["results"].items():
            print(f"     {scorer:32} AUC {block['auc']:.3f}  "
                  f"CI {block['ci_95_bootstrap_over_items']}  "
                  f"(n={block['n_commit_items']}x{block['n_abandon_items']} items)")
        print(f"     -> {population['lexical_control_reading']}")

    print(f"\nWrote {args.out_stem.with_suffix('.json')}")


if __name__ == "__main__":
    main()
