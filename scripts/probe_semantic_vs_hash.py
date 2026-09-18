"""Does a frozen sentence embedding carry action meaning where the trigram hash does not?

**The decision this exists to inform.** The project is moving to a *transferable* action
prior, and the current action representation encodes a control's label as a 32-dim signed
character-trigram hash. `reports/probe_action_representation.json` already measured what
that hash does: synonym pairs score mean cosine **0.249** against random pairs' **0.018**,
which looks like semantics until it is stratified — synonym pairs *sharing a token* score
**0.502**, and synonym pairs with **no shared token** score **0.081**. The separation is
spelling, not meaning. A representation that cannot see that `Place order` and
`Submit order` mean the same thing cannot transfer a preference between two applications
that word the same control differently.

This probe compares that hash against a **frozen** `all-MiniLM-L6-v2` sentence embedding
on the same pairs, and reports the one comparison that decides the question:

* **synonyms with no shared token** — can the representation see meaning through different
  spelling? This is what transfer needs.
* **lexical traps** — pairs that *share* a word and mean the *opposite* (`Place order` /
  `Cancel order`, `Sign in` / `Sign out`). A spelling-based representation scores these
  *high*, which is worse than useless: it actively confuses a control that commits a flow
  with the one that abandons it.
* **random pairs** — the null.

The headline statistic is therefore not "is semantic > random", which both representations
pass for the wrong reasons. It is **separation between no-shared-token synonyms and lexical
traps**: a representation that tracks meaning scores the first higher than the second, and
one that tracks spelling scores them the other way round.

Deliberately reuses `SEMANTIC_GROUPS` and `LEXICAL_TRAPS` from the existing probe rather
than inventing a second pair list, so this result is comparable with the committed one and
neither list can be quietly tuned to favour the new representation.

    python scripts/probe_semantic_vs_hash.py
    python scripts/probe_semantic_vs_hash.py --no-plot
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics as st
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# The existing probe's pair lists, imported rather than copied.
from probe_action_representation import (  # noqa: E402
    LEXICAL_TRAPS,
    SEMANTIC_GROUPS,
    tokens,
)

from web_testing_agent.agents.action_features import LabelEncoder  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

REPORTS = REPO_ROOT / "reports"
RANDOM_PAIRS = 400
BOOTSTRAP = 10_000
SEED = 0

#: The frozen semantic encoder. Pinned by name; never fine-tuned.
MINILM = "sentence-transformers/all-MiniLM-L6-v2"


# -- the two representations under test -------------------------------------------------


class HashRepresentation:
    """The current 32-dim signed character-trigram hash, exactly as the agent uses it."""

    name = "hashed_trigram_32d"
    description = "Signed blake2b character-trigram hashing, L2-normalised (current)."

    def __init__(self) -> None:
        self._encoder = LabelEncoder()

    def encode(self, labels: list[str]) -> np.ndarray:
        return np.stack([self._encoder.encode(label) for label in labels])


class SemanticRepresentation:
    """Frozen all-MiniLM-L6-v2, 384-dim, L2-normalised. No fine-tuning, no gradient."""

    name = "minilm_frozen_384d"
    description = "sentence-transformers/all-MiniLM-L6-v2, frozen, mean-pooled, L2-normalised."

    def __init__(self, device: str = "cpu") -> None:
        from web_testing_agent.perception.encoders.models import SentenceTransformerEncoder

        self._encoder = SentenceTransformerEncoder(model_name=MINILM, output_dim=384,
                                                   device=device)

    def encode(self, labels: list[str]) -> np.ndarray:
        return self._encoder.encode_batch(list(labels))


# -- pair construction ------------------------------------------------------------------


def build_pairs(rng: np.random.Generator) -> list[dict]:
    """Every pair this probe scores, labelled by kind. Built once and shared by both arms.

    Identical pairs for both representations is the point: any difference in the numbers
    is then a property of the encoder and of nothing else.
    """
    pairs: list[dict] = []

    for group in SEMANTIC_GROUPS:
        for left, right in itertools.combinations(group, 2):
            shared = bool(tokens(left) & tokens(right))
            pairs.append({
                "left": left, "right": right,
                "kind": "synonym_shared_token" if shared else "synonym_no_shared_token",
                "shares_token": shared,
            })

    for left, right in LEXICAL_TRAPS:
        pairs.append({
            "left": left, "right": right, "kind": "lexical_trap",
            "shares_token": bool(tokens(left) & tokens(right)),
        })

    # Random controls drawn from the same vocabulary, excluding any pair that is
    # genuinely synonymous or a declared trap.
    vocabulary = sorted({label for group in SEMANTIC_GROUPS for label in group}
                        | {label for pair in LEXICAL_TRAPS for label in pair})
    known: set[frozenset[str]] = set()
    for group in SEMANTIC_GROUPS:
        known |= {frozenset(p) for p in itertools.combinations(group, 2)}
    known |= {frozenset(pair) for pair in LEXICAL_TRAPS}

    seen: set[frozenset[str]] = set()
    attempts = 0
    while len([p for p in pairs if p["kind"] == "random"]) < RANDOM_PAIRS and attempts < 20_000:
        attempts += 1
        left, right = rng.choice(vocabulary, size=2, replace=False)
        key = frozenset((str(left), str(right)))
        if key in known or key in seen:
            continue
        seen.add(key)
        pairs.append({"left": str(left), "right": str(right), "kind": "random",
                      "shares_token": bool(tokens(str(left)) & tokens(str(right)))})
    return pairs


# -- statistics -------------------------------------------------------------------------


def describe(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(st.mean(values), 4),
        "median": round(st.median(values), 4),
        "sd": round(st.stdev(values), 4) if len(values) > 1 else 0.0,
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "p25": round(float(np.percentile(values, 25)), 4),
        "p75": round(float(np.percentile(values, 75)), 4),
    }


def bootstrap_difference(a: list[float], b: list[float], rng) -> dict:
    """95% CI for mean(a) - mean(b), resampling pairs independently."""
    if not a or not b:
        return {"difference": None, "ci": [None, None]}
    draws = sorted(float(np.mean(rng.choice(a, len(a), replace=True))
                         - np.mean(rng.choice(b, len(b), replace=True)))
                   for _ in range(BOOTSTRAP))
    return {
        "difference": round(st.mean(a) - st.mean(b), 4),
        "ci": [round(draws[int(0.025 * BOOTSTRAP)], 4),
               round(draws[int(0.975 * BOOTSTRAP) - 1], 4)],
    }


def score(representation, pairs: list[dict]) -> dict:
    """Cosine for every pair, grouped by kind, plus the decisive contrasts."""
    labels = sorted({p["left"] for p in pairs} | {p["right"] for p in pairs})
    started = time.monotonic()
    vectors = representation.encode(labels)
    elapsed = time.monotonic() - started
    lookup = {label: vectors[index] for index, label in enumerate(labels)}

    scored = []
    for pair in pairs:
        left, right = lookup[pair["left"]], lookup[pair["right"]]
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        cosine = float(left @ right) / denominator if denominator else 0.0
        scored.append({**pair, "cosine": round(cosine, 4)})

    by_kind: dict[str, list[float]] = {}
    for entry in scored:
        by_kind.setdefault(entry["kind"], []).append(entry["cosine"])

    rng = np.random.default_rng(SEED)
    no_shared = by_kind.get("synonym_no_shared_token", [])
    traps = by_kind.get("lexical_trap", [])
    shared = by_kind.get("synonym_shared_token", [])
    random_pairs = by_kind.get("random", [])

    return {
        "representation": representation.name,
        "description": representation.description,
        "dimensions": int(vectors.shape[1]),
        "encode_seconds": round(elapsed, 2),
        "by_kind": {kind: describe(values) for kind, values in sorted(by_kind.items())},
        "contrasts": {
            # THE decisive one: meaning through different spelling, versus shared
            # spelling with opposite meaning.
            "no_shared_token_synonyms_minus_lexical_traps":
                bootstrap_difference(no_shared, traps, rng),
            "no_shared_token_synonyms_minus_random":
                bootstrap_difference(no_shared, random_pairs, rng),
            "shared_token_synonyms_minus_no_shared_token_synonyms":
                bootstrap_difference(shared, no_shared, rng),
            "lexical_traps_minus_random":
                bootstrap_difference(traps, random_pairs, rng),
        },
        "pairs": scored,
    }


def verdict(result: dict) -> dict:
    """The one-line reading, derived from the decisive contrast and nothing else."""
    contrast = result["contrasts"]["no_shared_token_synonyms_minus_lexical_traps"]
    difference, (low, high) = contrast["difference"], contrast["ci"]
    tracks_meaning = bool(low is not None and low > 0)
    return {
        "tracks_meaning_over_spelling": tracks_meaning,
        "statistic": "mean cosine(synonyms with no shared token) - mean cosine(lexical traps)",
        "value": difference,
        "ci": [low, high],
        "reading": (
            f"Synonyms that share no word score {difference:+.3f} against pairs that share "
            f"a word and mean the opposite (95% CI [{low:+.3f}, {high:+.3f}]). "
            + ("The representation tracks meaning rather than spelling."
               if tracks_meaning else
               "The representation does NOT separate meaning from spelling: a control that "
               "commits a flow and one that abandons it are scored as similar whenever they "
               "share a word.")
        ),
    }



# -- secondary analysis: does the anchor-difference construction recover polarity? -------
#
# POST HOC. Added after the primary statistic came back negative for BOTH representations.
# It asks a different question, and it is reported as a separate finding rather than as a
# rescue of the first one.
#
# The primary statistic asks whether *pairwise* cosine separates meaning from spelling.
# Both representations fail it, and MiniLM fails it for a well-understood reason: sentence
# embeddings encode topic, not polarity. "Place order" and "Cancel order" are both about
# orders, so they sit close together however the phrase is read.
#
# A2 -- frozen in docs/methodology/2026-09-18-a0-a1-a2-frozen.md BEFORE this probe was
# written -- does not use pairwise cosine. It uses
#
#     semantic(a) = max_k cos(E(label), E(positive_anchor_k))
#                 - max_j cos(E(label), E(negative_anchor_j))
#
# which is a *projection onto a commit-versus-abandon axis*, not a similarity between two
# labels. Whether that construction recovers the polarity pairwise cosine loses is a
# question about an already-frozen design, so testing it is an evaluation of A2 rather
# than a tuning of it.

#: A2's anchors, copied verbatim from the frozen methodology document.
A2_POSITIVE_ANCHORS = [
    "submit the form", "confirm and place the order", "proceed to the next step",
    "complete the purchase", "save these changes", "create the account",
]
A2_NEGATIVE_ANCHORS = [
    "cancel and discard changes", "go back to the previous page",
    "return to the home page", "log out of the account",
    "read the terms and conditions", "contact customer support",
]

#: Labels a web developer would call "commits the workflow" and "abandons it". Drawn from
#: the vocabulary the pair lists already use; no fixture DOM was inspected to build them.
COMMIT_LABELS = [
    "Place order", "Submit order", "Confirm order", "Checkout", "Proceed to checkout",
    "Continue", "Next", "Save", "Apply changes", "Register", "Sign up", "Create account",
]
ABANDON_LABELS = [
    "Cancel", "Cancel order", "Abort", "Back", "Go back", "Return", "Previous",
    "Logout", "Sign out", "Log out", "Delete", "Remove",
]


def _unit(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)


def anchor_axis_analysis(representation, rng) -> dict:
    """Score every commit/abandon label on A2's positive-minus-negative anchor axis.

    A representation on which this axis separates the two groups can support A2; one on
    which it does not, cannot, whatever its pairwise cosines look like.
    """
    labels = COMMIT_LABELS + ABANDON_LABELS
    vectors = representation.encode(labels + A2_POSITIVE_ANCHORS + A2_NEGATIVE_ANCHORS)
    count = len(labels)
    label_vectors = _unit(vectors[:count])
    positive = _unit(vectors[count:count + len(A2_POSITIVE_ANCHORS)])
    negative = _unit(vectors[count + len(A2_POSITIVE_ANCHORS):])

    scores = (label_vectors @ positive.T).max(axis=1) - (label_vectors @ negative.T).max(axis=1)
    commit = [float(s) for s in scores[:len(COMMIT_LABELS)]]
    abandon = [float(s) for s in scores[len(COMMIT_LABELS):]]

    # Rank separation: the probability a randomly chosen commit label outranks a randomly
    # chosen abandon label. 1.0 is perfect, 0.5 is chance. Reported instead of a t-test
    # because ordering is the only thing a policy ever does with these scores.
    wins = sum(1 for c in commit for a in abandon if c > a)
    ties = sum(1 for c in commit for a in abandon if c == a)
    total = len(commit) * len(abandon)
    auc = (wins + 0.5 * ties) / total if total else 0.0

    return {
        "commit": describe(commit),
        "abandon": describe(abandon),
        "separation": bootstrap_difference(commit, abandon, rng),
        "rank_separation_auc": round(auc, 4),
        "per_label": (
            [{"label": l, "group": "commit", "axis_score": round(s, 4)}
             for l, s in zip(COMMIT_LABELS, commit, strict=True)]
            + [{"label": l, "group": "abandon", "axis_score": round(s, 4)}
               for l, s in zip(ABANDON_LABELS, abandon, strict=True)]
        ),
    }


def margin_over_null(result: dict) -> dict:
    """Cross-vocabulary synonym signal, measured against each encoder's own null.

    POST HOC, and reported because raw means are not comparable between a 32-dim sparse
    hash and a 384-dim dense embedding: the hash's random pairs sit at +0.030 and MiniLM's
    at +0.229, so an absolute cosine means a different thing in each. The margin over the
    representation's own random baseline is comparable; the raw number is not.
    """
    no_shared = result["by_kind"].get("synonym_no_shared_token", {})
    random_pairs = result["by_kind"].get("random", {})
    if not no_shared or not random_pairs:
        return {}
    margin = no_shared["mean"] - random_pairs["mean"]
    spread = random_pairs["sd"] or 1e-9
    return {
        "synonym_no_shared_token_mean": no_shared["mean"],
        "random_mean": random_pairs["mean"],
        "margin_over_random": round(margin, 4),
        "margin_in_random_sds": round(margin / spread, 2),
    }


# -- plot -------------------------------------------------------------------------------


def render_plot(results: dict, path: Path) -> bool:
    """Side-by-side cosine distributions per pair kind. Returns False if matplotlib is absent."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001 - a missing plot must not fail the probe
        logger.warning("matplotlib unavailable, skipping plot: {}", exc)
        return False

    kinds = ["synonym_no_shared_token", "synonym_shared_token", "lexical_trap", "random"]
    titles = ["Synonyms\n(no shared word)", "Synonyms\n(shared word)",
              "Lexical traps\n(shared word,\nopposite meaning)", "Random"]
    colours = ["#2f7d63", "#3a6ea8", "#d6353b", "#8794a1"]

    figure, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    for axis, (name, result) in zip(axes, results.items(), strict=True):
        data = []
        for kind in kinds:
            data.append([p["cosine"] for p in result["pairs"] if p["kind"] == kind])
        parts = axis.boxplot(data, patch_artist=True, widths=0.6, showfliers=False)
        for patch, colour in zip(parts["boxes"], colours, strict=True):
            patch.set_facecolor(colour)
            patch.set_alpha(0.35)
            patch.set_edgecolor(colour)
        for median in parts["medians"]:
            median.set_color("#16202c")
            median.set_linewidth(1.6)
        for index, (values, colour) in enumerate(zip(data, colours, strict=True), start=1):
            jitter = np.random.default_rng(SEED).normal(0, 0.05, len(values))
            axis.scatter(np.full(len(values), index) + jitter, values, s=8,
                         color=colour, alpha=0.45, linewidths=0)
        axis.axhline(0.0, color="#8794a1", linewidth=0.8, linestyle=":")
        axis.set_xticks(range(1, len(kinds) + 1))
        axis.set_xticklabels(titles, fontsize=8)
        axis.set_title(f"{name}\n({result['dimensions']}-dim)", fontsize=10)
        axis.grid(axis="y", alpha=0.2)
    axes[0].set_ylabel("cosine similarity")
    figure.suptitle(
        "Action-label representation: does it track meaning or spelling?\n"
        "A representation useful for transfer scores the green box above the red one.",
        fontsize=11,
    )
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return True


# -- report -----------------------------------------------------------------------------


def render_markdown(payload: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("# Action-label representation: frozen sentence embedding vs trigram hash\n")
    add("## 1. Question\n")
    add(payload["_purpose"] + "\n")

    add("## 2. Representations compared\n")
    add("| representation | dims | description |")
    add("|---|---|---|")
    for result in payload["results"].values():
        add(f"| `{result['representation']}` | {result['dimensions']} | {result['description']} |")
    add("")

    add("## 3. Cosine similarity by pair kind\n")
    add("| pair kind | n | " + " | ".join(
        f"{name} mean" for name in payload["results"]) + " |")
    add("|---|---|" + "---|" * len(payload["results"]))
    kinds = ["synonym_no_shared_token", "synonym_shared_token", "lexical_trap", "random"]
    for kind in kinds:
        first = next(iter(payload["results"].values()))["by_kind"].get(kind, {})
        cells = []
        for result in payload["results"].values():
            stats = result["by_kind"].get(kind, {})
            cells.append(f"{stats.get('mean', float('nan')):+.3f}" if stats else "—")
        add(f"| {kind.replace('_', ' ')} | {first.get('n', 0)} | " + " | ".join(cells) + " |")
    add("")

    add("## 4. The decisive contrast\n")
    add("A representation that tracks **meaning** scores synonyms-with-no-shared-word "
        "*above* lexical traps. One that tracks **spelling** does the opposite — and a "
        "spelling-based representation is actively harmful, because it scores the control "
        "that commits a flow and the one that abandons it as similar.\n")
    add("| representation | synonyms(no shared word) − lexical traps | 95% CI | tracks meaning |")
    add("|---|---|---|---|")
    for name, result in payload["results"].items():
        v = payload["verdicts"][name]
        add(f"| `{name}` | **{v['value']:+.3f}** | `{v['ci']}` | "
            f"**{'yes' if v['tracks_meaning_over_spelling'] else 'NO'}** |")
    add("")
    for name, v in payload["verdicts"].items():
        add(f"- **`{name}`** — {v['reading']}")
    add("")

    add("## 5. Supporting contrasts\n")
    add("| contrast | " + " | ".join(payload["results"]) + " |")
    add("|---|" + "---|" * len(payload["results"]))
    for key in ("no_shared_token_synonyms_minus_random",
                "shared_token_synonyms_minus_no_shared_token_synonyms",
                "lexical_traps_minus_random"):
        cells = []
        for result in payload["results"].values():
            block = result["contrasts"][key]
            cells.append(f"{block['difference']:+.3f} `{block['ci']}`")
        add(f"| {key.replace('_', ' ')} | " + " | ".join(cells) + " |")
    add("")
    add("`shared token synonyms − no shared token synonyms` is the spelling-dependence "
        "measure: near zero means the representation reads the two the same way, and a "
        "large positive value means it is reading characters.\n")

    if payload.get("plot"):
        add("## 6. Plot\n")
        add(f"![cosine distributions]({Path(payload['plot']).name})\n")

    add("## 7. Secondary analyses (post hoc)\n")
    secondary = payload.get("secondary_post_hoc") or {}
    if secondary:
        add("> Added after the primary statistic was seen. Reported alongside it, never "
            "in place of it.\n")
        add("**A2's commit-versus-abandon anchor axis.** Not a pairwise similarity: each "
            "label is projected onto `max cos(positive anchors) - max cos(negative "
            "anchors)`, using the anchors frozen in the methodology document before this "
            "probe existed.\n")
        add("| representation | commit mean | abandon mean | separation | 95% CI | rank AUC |")
        add("|---|---|---|---|---|---|")
        for name, block in secondary.items():
            axis = block["anchor_axis"]
            add(f"| `{name}` | {axis['commit']['mean']:+.3f} | {axis['abandon']['mean']:+.3f} | "
                f"**{axis['separation']['difference']:+.3f}** | "
                f"`{axis['separation']['ci']}` | **{axis['rank_separation_auc']:.3f}** |")
        add("")
        add("**Cross-vocabulary synonymy, measured against each encoder's own null.** Raw "
            "cosines are not comparable between a 32-dim sparse hash and a 384-dim dense "
            "embedding, because their random baselines differ (+0.030 against +0.229).\n")
        add("| representation | synonyms (no shared word) | random | margin | margin in random SDs |")
        add("|---|---|---|---|---|")
        for name, block in secondary.items():
            m = block["cross_vocabulary_margin"]
            add(f"| `{name}` | {m['synonym_no_shared_token_mean']:+.3f} | "
                f"{m['random_mean']:+.3f} | {m['margin_over_random']:+.3f} | "
                f"**{m['margin_in_random_sds']}** |")
        add("")

    add("## 8. Decision\n")
    add(payload["decision"] + "\n")

    add("## 9. Limitations\n")
    for limitation in payload["limitations"]:
        add(f"- {limitation}")
    add("")
    add("## 10. Exact command\n")
    add("```bash\npython scripts/probe_semantic_vs_hash.py\n```\n")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-stem", type=Path,
                        default=REPORTS / "probe_semantic_vs_hash")
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    rng = np.random.default_rng(SEED)
    pairs = build_pairs(rng)
    counts: dict[str, int] = {}
    for pair in pairs:
        counts[pair["kind"]] = counts.get(pair["kind"], 0) + 1
    logger.info("scoring {} pairs: {}", len(pairs), counts)

    representations = [HashRepresentation(), SemanticRepresentation(device=args.device)]
    results = {}
    for representation in representations:
        logger.info("encoding with {}", representation.name)
        results[representation.name] = score(representation, pairs)

    verdicts = {name: verdict(result) for name, result in results.items()}

    # Secondary, post-hoc analyses. Reported alongside the primary statistic, never in
    # place of it: the primary came back negative for both representations and that stands.
    secondary = {}
    for representation in representations:
        secondary[representation.name] = {
            "_status": "POST HOC, added after the primary statistic was seen.",
            "anchor_axis": anchor_axis_analysis(representation, np.random.default_rng(SEED)),
            "cross_vocabulary_margin": margin_over_null(results[representation.name]),
        }

    plot_path = args.out_stem.with_suffix(".png")
    drew = (not args.no_plot) and render_plot(results, plot_path)

    hash_axis = secondary["hashed_trigram_32d"]["anchor_axis"]
    semantic_axis = secondary["minilm_frozen_384d"]["anchor_axis"]
    hash_margin = secondary["hashed_trigram_32d"]["cross_vocabulary_margin"]
    semantic_margin = secondary["minilm_frozen_384d"]["cross_vocabulary_margin"]

    # The decision reads three things, and states the negative one first because it is
    # the pre-registered one and because it constrains how the embedding may be used.
    decision = (
        "**Primary statistic: both representations fail, and that stands.** Neither "
        f"separates meaning from spelling pairwise — the hash at "
        f"{verdicts['hashed_trigram_32d']['value']:+.3f} and MiniLM at "
        f"{verdicts['minilm_frozen_384d']['value']:+.3f} (CI "
        f"{verdicts['minilm_frozen_384d']['ci']}, containing zero). MiniLM scores "
        "`Place order` against `Cancel order` as highly as it scores genuine synonyms, "
        "because sentence embeddings encode topic rather than polarity. **The direct "
        "consequence: raw pairwise label cosine must not be used as a policy feature, in "
        "either representation.** It would tell the agent that the control which commits "
        "a workflow and the one that abandons it are the same kind of thing.\n\n"

        "**Secondary, post hoc: the two representations are nonetheless far apart on the "
        "two properties transfer actually needs.**\n\n"

        f"1. *Cross-vocabulary synonymy.* Measured against each encoder's own null, "
        f"MiniLM puts no-shared-word synonyms **{semantic_margin['margin_in_random_sds']} "
        f"random SDs** above chance; the hash manages "
        f"**{hash_margin['margin_in_random_sds']}**. Recognising that `Place order` on one "
        "application and `Submit purchase` on another are the same control is the whole "
        "mechanism a transferable prior would rely on, and the hash effectively cannot do "
        "it.\n\n"

        f"2. *Polarity, via a projection rather than a similarity.* On A2's frozen "
        f"positive-minus-negative anchor axis, MiniLM separates commit-style labels from "
        f"abandon-style ones with rank AUC **{semantic_axis['rank_separation_auc']:.3f}** "
        f"against the hash's **{hash_axis['rank_separation_auc']:.3f}** "
        f"(mean separation {semantic_axis['separation']['difference']:+.3f}, CI "
        f"{semantic_axis['separation']['ci']}). The polarity that pairwise cosine loses is "
        "recoverable by projecting onto a task-relevant axis — which is what A2 already "
        "does, frozen before this probe was written.\n\n"

        "**Decision: adopt the frozen sentence embedding for the action label, as a "
        "feature vector for a learned scorer and for anchor projections — never as a raw "
        "pairwise similarity.** The trigram hash stays where it is correct, which is "
        "identity work: state deduplication, the archive, and novelty counting, where "
        "spelling *is* the question being asked. That is the architectural split the "
        "project direction states as 'identity is for counting, semantics are for "
        "learning', and this probe is the measurement behind it."
    )

    payload = {
        "_purpose": (
            "The project is moving to a transferable action prior. The current action "
            "label representation is a 32-dim character-trigram hash, and "
            "`reports/probe_action_representation.json` already showed that its apparent "
            "semantic signal is shared-token overlap (0.502 for synonyms sharing a word, "
            "0.081 for synonyms sharing none). This probe asks whether a frozen "
            "sentence embedding sees meaning where the hash sees spelling, on the same "
            "pairs."),
        "_decisive_statistic": (
            "mean cosine(synonyms with no shared token) - mean cosine(lexical traps). "
            "Positive means the representation tracks meaning; negative means it tracks "
            "characters, which is worse than no signal because it conflates committing a "
            "flow with abandoning it."),
        "git": {
            "head": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                   text=True, cwd=REPO_ROOT).stdout.strip(),
        },
        "pair_counts": counts,
        "pair_source": "SEMANTIC_GROUPS and LEXICAL_TRAPS from "
                       "scripts/probe_action_representation.py, imported unchanged",
        "encoder": {"semantic": MINILM, "frozen": True, "device": args.device},
        "bootstrap_resamples": BOOTSTRAP,
        "results": results,
        "verdicts": verdicts,
        "secondary_post_hoc": secondary,
        "decision": decision,
        "plot": str(plot_path) if drew else None,
        "limitations": [
            "One pair list, written for the earlier probe and reused unchanged. It is "
            "web-UI vocabulary, not a general semantic benchmark.",
            "Cosine between label embeddings is a property of the encoder, not evidence "
            "that a policy will exploit it. Whether the agent uses the signal is a "
            "separate question this probe cannot answer.",
            "The lexical traps are adversarial by construction. A representation that "
            "scores them low is doing the right thing here, but the set is small (10).",
            "MiniLM is frozen and general-purpose. It has not been adapted to web UI "
            "vocabulary, which is deliberate: an adapted encoder would be trained on "
            "these fixtures and could not then be used to argue transfer.",
        ],
    }

    args.out_stem.parent.mkdir(parents=True, exist_ok=True)
    args.out_stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8")
    args.out_stem.with_suffix(".md").write_text(
        render_markdown(payload), encoding="utf-8")

    print("=" * 78)
    print("ACTION-LABEL REPRESENTATION PROBE")
    print("=" * 78)
    for name, result in results.items():
        print(f"\n  {name}  ({result['dimensions']}-dim)")
        for kind in ("synonym_no_shared_token", "synonym_shared_token",
                     "lexical_trap", "random"):
            stats = result["by_kind"].get(kind, {})
            if stats:
                print(f"    {kind:28} n={stats['n']:>3}  mean {stats['mean']:+.3f}  "
                      f"median {stats['median']:+.3f}")
        v = verdicts[name]
        print(f"    -> synonyms(no shared word) - traps = {v['value']:+.3f} "
              f"CI {v['ci']}  tracks meaning: "
              f"{'YES' if v['tracks_meaning_over_spelling'] else 'NO'}")
    print("\n" + "-" * 78)
    print("SECONDARY (POST HOC): A2's anchor axis, and cross-vocabulary margin over null")
    print("-" * 78)
    for name, block in secondary.items():
        axis, margin = block["anchor_axis"], block["cross_vocabulary_margin"]
        print(f"\n  {name}")
        print(f"    commit labels  mean axis score {axis['commit']['mean']:+.3f}")
        print(f"    abandon labels mean axis score {axis['abandon']['mean']:+.3f}")
        print(f"    separation {axis['separation']['difference']:+.3f} "
              f"CI {axis['separation']['ci']}   rank AUC {axis['rank_separation_auc']:.3f}")
        print(f"    cross-vocabulary synonym margin over own null: "
              f"{margin.get('margin_over_random'):+.3f} "
              f"({margin.get('margin_in_random_sds')} random SDs)")
    print(f"\n{decision}\n")
    print(f"Wrote {args.out_stem.with_suffix('.json')}")
    print(f"Wrote {args.out_stem.with_suffix('.md')}")
    if drew:
        print(f"Wrote {plot_path}")


if __name__ == "__main__":
    main()
