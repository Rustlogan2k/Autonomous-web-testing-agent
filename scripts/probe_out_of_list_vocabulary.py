"""Does A2 keep discriminating when the important vocabulary is outside A1's word list?

**The question, and why it is the only one this script answers.**
`reports/probe_anchor_audit.json` found A1's plain word list at AUC 0.990 against the
anchor projection's 1.000 on a post-hoc population, and concluded -- correctly -- that a
near-tie on a self-chosen population cannot show the embedding contributes anything. It
also named the reason the tie is uninformative: that population's vocabulary is inside
A1's list, so A1 was never asked to do the thing a word list cannot do. A lexical prior's
expected failure mode is **coverage**. This probe manufactures the coverage failure on
purpose and measures who survives it.

**No RL training. No fixture. No environment.** Two label sets, four scorers, one AUC each.
It runs in seconds and is a diagnostic, not an experiment about policies.

**Design, pre-registered.** Two tiers, both written *before* any scorer was run on them,
both frozen in git before this script was executed (see the commit that adds this file).

* **Tier 1 -- OUT_OF_LIST.** 12 commit-like and 12 navigate-like labels in which *every*
  token is absent from A1's positive and negative lists. Head words include the four the
  advisor named -- Finalise, Authorise, Ratify, Dispatch -- none of which is in A1.
  A1's score here is **0.0 on all 24 labels by construction**, so its AUC is exactly 0.5.
  That is arithmetic, not a discovery, and this script says so in its own output rather
  than presenting a definitional result as a measurement. The measurement is A2's.

* **Tier 2 -- PARTIAL_COVERAGE.** 8 commit-like and 8 navigate-like labels whose head verb
  is again outside A1's list, but which each carry exactly one A1 *positive* token, and
  the same tokens on both sides (`order`, `purchase`, `checkout`). This is the realistic
  regime rather than the extreme one: the word list fires, and fires *identically* on both
  categories, so it is actively misled rather than merely silent. A1 is again pinned at
  0.5 by construction; again the measurement is A2's.

  Tier 2 exists because Tier 1 alone lets a critic say the probe only shows that a scorer
  which outputs a constant cannot rank. Tier 2 shows the same thing when the scorer is
  loud and wrong, which is what actually happens on a real application.

**Pre-registered prediction, recorded here before the first run.** If the anchor
projection is doing semantic work rather than exploiting vocabulary overlap with its own
anchors, its AUC on Tier 1 should stay well above chance -- the pre-specified reading is
**>= 0.80 with a bootstrap 95% lower bound above 0.5**. If instead it lands near 0.5, or
its interval spans chance, then the 1.000 on the post-hoc population was carried by
vocabulary the anchors happened to share, and the embedding has no demonstrated advantage
to retain. Either outcome is reported; neither changes the decision rule for A0/A1/A2,
which lives in `docs/methodology/2026-09-18-a0-a1-a2-frozen.md` and is untouched by this.

**Disclosed asymmetries, because they favour A2 and would otherwise be invisible.**

1. A2's projection is fitted on `semantic_vocabulary.FIT_VOCABULARY`. Some Tier 1 content
   words appear there (`Finalize` in US spelling, `Invoice`, `Archive`, `Approve`). The
   projection is therefore *not* naive about this vocabulary the way A1 is, and this
   script prints the exact overlap rather than claiming the tiers are held out from it.
2. `Dispatch the parcel` and `Authorise the transfer` appear in `HELD_OUT_PROBE`, which is
   excluded from the projection fit but was written on 2026-09-18 alongside the anchors.
   Their head words were chosen by the advisor, so they stay; the overlap is printed.
3. Assigning each label to "commit" or "navigate" is a judgement made by the author of the
   list. The categories were fixed before scoring and are not revised.

    python scripts/probe_out_of_list_vocabulary.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from web_testing_agent.agents.label_priors import (  # noqa: E402
    A1_NEGATIVE_TOKENS,
    A1_POSITIVE_TOKENS,
    A1_VOCABULARY_VERSION,
    A2_ANCHORS_VERSION,
    A2_NEGATIVE_ANCHORS,
    A2_POSITIVE_ANCHORS,
    a1_lexical_count_score,
    a1_lexical_score,
    anchor_axis_scores,
    tokenize,
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

PROBE_VERSION = "2026-09-18.out-of-list.1"

# =========================================================================================
# THE FROZEN PROBE POPULATIONS
#
# Written once, before any scorer was run on them. The only edit permitted after this
# point is one that `tests/unit/test_label_priors.py` demands -- i.e. removing a label that
# violates the tier's own token rule -- and any such edit bumps PROBE_VERSION.
# =========================================================================================

#: Tier 1, commit-like. Every token is outside A1's positive and negative lists, asserted
#: by `test_out_of_list_tier1_is_fully_outside_a1`. Head words: finalise, authorise,
#: ratify, dispatch (the four named by the advisor), plus eight more of the same kind.
TIER1_COMMIT: tuple[str, ...] = (
    "Finalise the booking",
    "Authorise the transaction",
    "Ratify the agreement",
    "Dispatch the parcel",
    "Endorse the document",
    "Execute the contract",
    "Enact the amendment",
    "Lodge the appeal",
    "Remit the balance",
    "Countersign the lease",
    "Commission the artwork",
    "Validate the entry",
)

#: Tier 1, navigate-like. Same token rule. These are the class A2 must rank *below* the
#: commit-like set: browsing, reading and moving around an application without committing
#: anything. On the deep fixture this is exactly the distractor class -- every alternative
#: on `order-4.html` is a navigational link.
TIER1_NAVIGATE: tuple[str, ...] = (
    "Browse the archive",
    "Peruse the listings",
    "Revisit the earlier screen",
    "Wander through the gallery",
    "Skim the announcements",
    "Inspect the directory",
    "Traverse the sitemap",
    "Navigate to the lobby",
    "Explore the showroom",
    "Scan the bulletin",
    "Visit the reading room",
    "Tour the exhibition",
)

#: Tier 2, commit-like. Head verb outside A1; exactly one A1 positive token per label.
TIER2_COMMIT: tuple[str, ...] = (
    "Finalise the order",
    "Ratify the purchase",
    "Authorise the checkout",
    "Dispatch the order",
    "Countersign the order",
    "Endorse the purchase",
    "Execute the order",
    "Validate the checkout",
)

#: Tier 2, navigate-like. Head verb outside A1; the *same* A1 positive tokens, in the same
#: proportions, so the lexical signal is matched across the two categories and cannot
#: separate them. Four `order`, two `purchase`, two `checkout` on each side, pinned by
#: `test_tier2_lexical_signal_is_matched_across_categories`.
TIER2_NAVIGATE: tuple[str, ...] = (
    "Browse the order history",
    "Peruse the purchase records",
    "Revisit the checkout guide",
    "Skim the order summary",
    "Inspect the order archive",
    "Tour the purchase gallery",
    "Scan the order listings",
    "Survey the checkout statistics",
)

TIERS = {
    "tier1_out_of_list": {
        "commit": TIER1_COMMIT,
        "navigate": TIER1_NAVIGATE,
        "rule": "every token of every label is absent from A1's positive and negative lists",
        "a1_is_analytic": "A1 scores 0.0 on all 24 labels, so its AUC is exactly 0.5 by "
                          "construction. Reported, not discovered.",
    },
    "tier2_partial_coverage": {
        "commit": TIER2_COMMIT,
        "navigate": TIER2_NAVIGATE,
        "rule": "head verb absent from A1; exactly one A1 positive token per label; the "
                "same token multiset on both sides",
        "a1_is_analytic": "A1 scores +1.0 on all 16 labels, so its AUC is exactly 0.5 by "
                          "construction. The word list is loud and uninformative here, "
                          "rather than silent as in tier 1.",
    },
}

#: The head words the advisor named explicitly. Asserted absent from A1 by the test suite
#: and re-asserted at runtime here, so a probe run cannot silently proceed on a word list
#: that has since acquired one of them.
REQUIRED_OUT_OF_LIST_HEAD_WORDS = ("finalise", "authorise", "ratify", "dispatch")


# -- scoring -------------------------------------------------------------------------------


def auc_of(positive: list[float], negative: list[float]) -> float:
    """P(a random commit label outranks a random navigate label), ties at 0.5.

    The same statistic `probe_semantic_vs_hash` and `probe_anchor_audit` report, so the
    numbers here sit on the same scale as the 1.000 and 0.990 this probe exists to qualify.
    """
    if not positive or not negative:
        return float("nan")
    wins = sum(1 for p in positive for n in negative if p > n)
    ties = sum(1 for p in positive for n in negative if p == n)
    return (wins + 0.5 * ties) / (len(positive) * len(negative))


def auc_with_interval(positive: list[float], negative: list[float]) -> dict:
    """AUC plus a bootstrap CI resampling **items**, which is the independent unit.

    Resampling the ordered pairs would treat each label as appearing once per opposing
    label and produce an interval far narrower than 24 items support. Seeded, so two runs
    of this script agree exactly.
    """
    rng = np.random.default_rng(SEED)
    point = auc_of(positive, negative)
    draws = sorted(
        auc_of(list(rng.choice(positive, len(positive), replace=True)),
               list(rng.choice(negative, len(negative), replace=True)))
        for _ in range(BOOTSTRAP)
    )
    return {
        "auc": round(point, 4),
        "n_commit_items": len(positive),
        "n_navigate_items": len(negative),
        "n_independent_items": len(positive) + len(negative),
        "n_ordered_pairs": len(positive) * len(negative),
        "ci_95_bootstrap_over_items": [round(draws[int(0.025 * BOOTSTRAP)], 4),
                                       round(draws[int(0.975 * BOOTSTRAP) - 1], 4)],
        "_ci_unit": "labels, not ordered pairs",
    }


def hash_vectors(labels: list[str]) -> np.ndarray:
    """The old 32-dim signed character-trigram hash, as a floor control.

    It is in this table because it is the representation the project shipped before the
    embedding, and because a scorer that cannot see meaning at all is the right reference
    for "did the embedding see meaning".
    """
    from web_testing_agent.agents.action_features import LabelEncoder

    encoder = LabelEncoder()
    return np.stack([encoder.encode(label) for label in labels])


def raw_minilm_vectors(labels: list[str], encoder: SemanticLabelEncoder) -> np.ndarray:
    """The unprojected 384-dim MiniLM embedding, reusing the arm's own pinned encoder."""
    model = encoder._load_encoder()  # noqa: SLF001 - one pinned encoder, not a second copy
    return np.asarray(model.encode(labels, convert_to_numpy=True, show_progress_bar=False),
                      dtype=np.float64)


def score_tier(name: str, commit: tuple[str, ...], navigate: tuple[str, ...],
               encoder: SemanticLabelEncoder) -> dict:
    """Every scorer, on one tier, with per-label values kept for inspection."""
    labels = list(commit) + list(navigate)
    split = len(commit)
    scorers: dict[str, list[float]] = {
        "A1_lexical_frozen_indicator": [a1_lexical_score(label) for label in labels],
        "A1_lexical_count_variant": [a1_lexical_count_score(label) for label in labels],
    }

    projected = encoder.encode_batch(
        labels + list(A2_POSITIVE_ANCHORS) + list(A2_NEGATIVE_ANCHORS))
    scorers["A2_anchor_on_projected_48d"] = [
        float(v) for v in anchor_axis_scores(
            projected[:len(labels)],
            projected[len(labels):len(labels) + len(A2_POSITIVE_ANCHORS)],
            projected[len(labels) + len(A2_POSITIVE_ANCHORS):])]

    raw = raw_minilm_vectors(
        labels + list(A2_POSITIVE_ANCHORS) + list(A2_NEGATIVE_ANCHORS), encoder)
    scorers["A2_anchor_on_raw_minilm_384d"] = [
        float(v) for v in anchor_axis_scores(
            raw[:len(labels)],
            raw[len(labels):len(labels) + len(A2_POSITIVE_ANCHORS)],
            raw[len(labels) + len(A2_POSITIVE_ANCHORS):])]

    hashed = hash_vectors(labels + list(A2_POSITIVE_ANCHORS) + list(A2_NEGATIVE_ANCHORS))
    scorers["anchor_on_trigram_hash_32d"] = [
        float(v) for v in anchor_axis_scores(
            hashed[:len(labels)],
            hashed[len(labels):len(labels) + len(A2_POSITIVE_ANCHORS)],
            hashed[len(labels) + len(A2_POSITIVE_ANCHORS):])]

    results = {scorer: auc_with_interval(values[:split], values[split:])
               for scorer, values in scorers.items()}

    return {
        "tier": name,
        "rule": TIERS[name]["rule"],
        "a1_is_analytic": TIERS[name]["a1_is_analytic"],
        "commit_labels": list(commit),
        "navigate_labels": list(navigate),
        "n_commit": len(commit),
        "n_navigate": len(navigate),
        "results": results,
        "per_label": [
            {"label": label,
             "category": "commit" if index < split else "navigate",
             "tokens": sorted(tokenize(label)),
             **{scorer: round(values[index], 4) for scorer, values in scorers.items()}}
            for index, label in enumerate(labels)
        ],
    }


# -- coverage bookkeeping, printed rather than assumed ---------------------------------------


def coverage_audit() -> dict:
    """Exactly which probe tokens A1 knows, and which the projection's fit corpus knows.

    Two different questions, deliberately answered separately. A1's coverage decides
    whether tier 1 is what it claims to be. The fit corpus's coverage is an *advantage* for
    A2 and is printed so it cannot be omitted from the write-up.
    """
    a1_vocabulary = A1_POSITIVE_TOKENS | A1_NEGATIVE_TOKENS
    fit_tokens: set[str] = set()
    for phrase in FIT_VOCABULARY:
        fit_tokens |= tokenize(phrase)
    held_out_phrases = {phrase for left, right, _ in HELD_OUT_PROBE for phrase in (left, right)}

    rows = []
    for tier, block in TIERS.items():
        for category in ("commit", "navigate"):
            for label in block[category]:
                words = tokenize(label)
                rows.append({
                    "tier": tier, "category": category, "label": label,
                    "tokens_in_a1": sorted(words & a1_vocabulary),
                    "tokens_in_projection_fit_corpus": sorted(words & fit_tokens),
                    "label_string_in_held_out_probe": label in held_out_phrases,
                })

    tier1 = [r for r in rows if r["tier"] == "tier1_out_of_list"]
    tier2 = [r for r in rows if r["tier"] == "tier2_partial_coverage"]
    return {
        "a1_vocabulary_version": A1_VOCABULARY_VERSION,
        "a1_vocabulary_size": len(a1_vocabulary),
        "required_head_words_absent_from_a1": {
            word: word not in a1_vocabulary for word in REQUIRED_OUT_OF_LIST_HEAD_WORDS},
        "tier1_labels_with_any_a1_token": [r["label"] for r in tier1 if r["tokens_in_a1"]],
        "tier2_labels_with_no_a1_token": [r["label"] for r in tier2 if not r["tokens_in_a1"]],
        "tier1_tokens_also_in_projection_fit_corpus": sorted(
            {t for r in tier1 for t in r["tokens_in_projection_fit_corpus"]}),
        "labels_appearing_in_held_out_probe": [
            r["label"] for r in rows if r["label_string_in_held_out_probe"]],
        "_asymmetry": (
            "A1 has never seen any tier-1 token. The projection was fitted on a corpus "
            "that contains some of them. That asymmetry is the experiment's subject, not "
            "a flaw -- a word list cannot generalise past its words and an embedding is "
            "supposed to -- but it means a tier-1 win for A2 is evidence about *this* "
            "fitted projection, not about sentence embeddings in general."),
        "rows": rows,
    }


# -- reporting -------------------------------------------------------------------------------


def git_head() -> dict:
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              cwd=REPO_ROOT).stdout.strip()
    return {"head": run("rev-parse", "HEAD"),
            "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(run("status", "--porcelain"))}


def read_prediction(tier1: dict) -> str:
    """Apply the pre-registered reading. Stated in the docstring before the first run."""
    block = tier1["results"]["A2_anchor_on_projected_48d"]
    auc, (low, _high) = block["auc"], block["ci_95_bootstrap_over_items"]
    if auc >= 0.80 and low > 0.5:
        return (f"PREDICTION MET. A2 holds at AUC {auc:.3f} (95% CI lower bound {low:.3f}) "
                f"on vocabulary A1 has never seen, where A1 is pinned at 0.500 by "
                f"construction. The anchor projection retains discrimination outside the "
                f"word list; that is a concrete, named advantage rather than a vague one.")
    if low <= 0.5:
        return (f"PREDICTION NOT MET. A2 reaches AUC {auc:.3f} but its 95% interval "
                f"[{low:.3f}, ...] includes chance, so this probe does not establish that "
                f"the projection generalises past A1's vocabulary. At n="
                f"{block['n_independent_items']} items the probe is underpowered rather "
                f"than negative; do not read it as evidence of equality.")
    return (f"PREDICTION PARTIALLY MET. A2 reaches AUC {auc:.3f} with a 95% lower bound of "
            f"{low:.3f} -- above chance, below the pre-specified 0.80. Some discrimination "
            f"survives outside A1's vocabulary, less than the clean reading required.")


def render(payload: dict) -> str:
    lines = ["# Out-of-list vocabulary probe", ""]
    lines.append(f"*{payload['generated_at']} · probe `{payload['probe_version']}` · "
                 f"git `{payload['git']['head'][:7]}`*")
    lines.append("")
    lines.append("Does A2's anchor projection keep discriminating commit-like from "
                 "navigate-like controls when every head word is outside A1's frozen word "
                 "list? No RL training; four scorers, two frozen label sets.")
    lines.append("")
    lines.append("## Pre-registered reading")
    lines.append("")
    lines.append("> A2 on the 48-D projection reaches **AUC >= 0.80 with a bootstrap 95% "
                 "lower bound above 0.5** on tier 1, or the probe does not establish that "
                 "the projection generalises past A1's vocabulary.")
    lines.append("")
    lines.append(f"**{payload['verdict']}**")
    lines.append("")

    for tier in payload["tiers"]:
        lines.append(f"## {tier['tier']}  ({tier['n_commit']} commit / "
                     f"{tier['n_navigate']} navigate)")
        lines.append("")
        lines.append(f"Rule: {tier['rule']}.")
        lines.append("")
        lines.append(f"> {tier['a1_is_analytic']}")
        lines.append("")
        lines.append("| scorer | AUC | 95% CI (over items) | items | ordered pairs |")
        lines.append("|---|---|---|---|---|")
        for scorer, block in tier["results"].items():
            low, high = block["ci_95_bootstrap_over_items"]
            lines.append(f"| `{scorer}` | **{block['auc']:.3f}** | "
                         f"[{low:.3f}, {high:.3f}] | {block['n_independent_items']} | "
                         f"{block['n_ordered_pairs']} |")
        lines.append("")
        lines.append("<details><summary>per-label scores</summary>")
        lines.append("")
        scorer_names = list(tier["results"].keys())
        lines.append("| label | category | " + " | ".join(f"`{s}`" for s in scorer_names) + " |")
        lines.append("|---|---|" + "---|" * len(scorer_names))
        for row in tier["per_label"]:
            lines.append(f"| {row['label']} | {row['category']} | "
                         + " | ".join(f"{row[s]:+.4f}" for s in scorer_names) + " |")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    coverage = payload["coverage"]
    lines.append("## Coverage bookkeeping")
    lines.append("")
    lines.append(f"A1 vocabulary `{coverage['a1_vocabulary_version']}`, "
                 f"{coverage['a1_vocabulary_size']} tokens. Required head words absent "
                 f"from it: " + ", ".join(
                     f"`{w}` {'yes' if ok else 'NO'}"
                     for w, ok in coverage["required_head_words_absent_from_a1"].items()) + ".")
    lines.append("")
    lines.append(f"Tier-1 labels carrying any A1 token: "
                 f"{coverage['tier1_labels_with_any_a1_token'] or 'none'}.")
    lines.append("")
    lines.append(f"Tier-1 tokens that *are* in the projection's fit corpus: "
                 f"{', '.join('`' + t + '`' for t in coverage['tier1_tokens_also_in_projection_fit_corpus']) or 'none'}.")
    lines.append("")
    lines.append(f"Probe labels that also appear in `HELD_OUT_PROBE`: "
                 f"{coverage['labels_appearing_in_held_out_probe'] or 'none'}.")
    lines.append("")
    lines.append(f"> {coverage['_asymmetry']}")
    lines.append("")
    lines.append("## What this cannot establish")
    lines.append("")
    for limitation in payload["limitations"]:
        lines.append(f"- {limitation}")
    lines.append("")
    return "\n".join(lines)


LIMITATIONS = [
    "**This is not an experiment about policies.** Nothing here runs an environment, a "
    "browser or a learner. A scorer that ranks labels well may still not change what an "
    "agent does, and the A0/A1/A2 run is what speaks to that.",
    "**The label sets were written by one author who knew A2's anchors.** They were frozen "
    "in git before the first run, which rules out tuning against results, not the "
    "possibility that the phrasing unconsciously suits the anchors. The trigram-hash row "
    "is the floor control that makes that visible: if the hash also scores well, the "
    "population is separable by surface form rather than by meaning.",
    "**Category assignment is a judgement.** 'Commit-like' and 'navigate-like' were "
    "assigned by the author before scoring and not revised. Someone else might place "
    "`Inspect the directory` differently.",
    "**n is 24 items in tier 1 and 16 in tier 2.** The intervals resample labels and are "
    "correspondingly wide. This is a cheap diagnostic, and it is powered to separate 'holds "
    "up' from 'collapses', not to resolve small differences.",
    "**A2's projection was fitted on a corpus sharing some of these tokens; A1's list was "
    "not.** The coverage table above gives the exact overlap. A tier-1 win for A2 is "
    "evidence about this fitted projection, not about frozen sentence embeddings in general.",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-stem", type=Path,
                        default=REPORTS / "probe_out_of_list_vocabulary")
    args = parser.parse_args()

    a1_vocabulary = A1_POSITIVE_TOKENS | A1_NEGATIVE_TOKENS
    present = [w for w in REQUIRED_OUT_OF_LIST_HEAD_WORDS if w in a1_vocabulary]
    if present:
        raise SystemExit(
            f"STOP: {present} are in A1's vocabulary. This probe measures what happens "
            f"when they are NOT; adding them to A1 would invalidate it. Do not run.")

    encoder = SemanticLabelEncoder()
    tiers = [score_tier(name, block["commit"], block["navigate"], encoder)
             for name, block in TIERS.items()]

    payload = {
        "_purpose": "Does A2 keep discriminating when the vocabulary is outside A1's list?",
        "probe_version": PROBE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": git_head(),
        "bootstrap": {"draws": BOOTSTRAP, "seed": SEED, "unit": "labels"},
        "a1": {"version": A1_VOCABULARY_VERSION,
               "positive_tokens": sorted(A1_POSITIVE_TOKENS),
               "negative_tokens": sorted(A1_NEGATIVE_TOKENS)},
        "a2": {"version": A2_ANCHORS_VERSION,
               "positive_anchors": list(A2_POSITIVE_ANCHORS),
               "negative_anchors": list(A2_NEGATIVE_ANCHORS),
               "encoder": encoder.model_name,
               "projection_fingerprint": encoder.fingerprint()},
        "pre_registered_reading":
            "A2 on the 48-D projection reaches AUC >= 0.80 with a bootstrap 95% lower "
            "bound above 0.5 on tier 1, or the probe does not establish generalisation "
            "past A1's vocabulary.",
        "tiers": tiers,
        "coverage": coverage_audit(),
        "limitations": LIMITATIONS,
    }
    payload["verdict"] = read_prediction(tiers[0])

    args.out_stem.parent.mkdir(parents=True, exist_ok=True)
    args.out_stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, default=float), encoding="utf-8")
    args.out_stem.with_suffix(".md").write_text(render(payload), encoding="utf-8")

    print("=" * 78)
    print("OUT-OF-LIST VOCABULARY PROBE")
    print("=" * 78)
    for tier in tiers:
        print(f"\n{tier['tier']}  ({tier['n_commit']} commit / {tier['n_navigate']} navigate)")
        print(f"  {tier['a1_is_analytic']}")
        for scorer, block in tier["results"].items():
            low, high = block["ci_95_bootstrap_over_items"]
            print(f"    {scorer:34} AUC {block['auc']:.3f}  CI [{low:.3f}, {high:.3f}]  "
                  f"n={block['n_independent_items']} items")
    print(f"\n{payload['verdict']}")
    print(f"\nWrote {args.out_stem.with_suffix('.json')}")
    print(f"Wrote {args.out_stem.with_suffix('.md')}")


if __name__ == "__main__":
    main()
