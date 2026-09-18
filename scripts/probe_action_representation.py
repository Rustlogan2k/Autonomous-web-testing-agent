"""Does the hashed action label carry semantic similarity, or only surface-string overlap?

**The question.** `ActionConditionedQNetwork` scores `Q(s, phi(a))`, and for two actions of
the same type on the same page almost every component of `phi(a)` is identical: the
10-dim type one-hot matches, and of the 10 scalars `navigational`, `has_href`,
`is_form_control`, `value_ordinal`, `opens_new_tab` match while `taken_here` and
`taken_this_episode` are identically 0.0 because `record_taken()` is never called. What
is left to tell `Place order` from `Back` is a **32-dim signed character-trigram hash** of
the visible label. The audit recorded `cos(Place order, Cancel order) = +0.42` — the
action that abandons the flow being the nearest neighbour of the action that completes it
— but on one example. This measures it.

**What is being claimed, and what is not.** This probe measures *label embedding geometry*
only: whether labels a human calls equivalent land near each other. It says nothing about
whether the trained policy exploits that geometry, which would need training runs and is
explicitly out of scope. The secondary `--checkpoint` diagnostic reads an **untrained**
network and is labelled as such.

**Why the distinction between semantic and lexical matters here.** A trigram hash is a
bag-of-substrings model, so it can only see shared characters. Two labels meaning the same
thing in different words (`Next` / `Continue`) share nothing; two labels meaning opposite
things in similar words (`Place order` / `Cancel order`) share most of their characters.
The probe therefore stratifies every semantic pair by whether it shares a word, which is
what separates "this representation understands actions" from "this representation
measures spelling".

Offline by construction: the fixture's real action labels are harvested once into
`reports/deep_action_labels.json` and read from there, so reproducing the measurement
needs no browser.

    python scripts/probe_action_representation.py
    python scripts/probe_action_representation.py --harvest-labels    # regenerate the pool
    python scripts/probe_action_representation.py --checkpoint models/checkpoints/...zip
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from web_testing_agent.agents.action_features import (  # noqa: E402
    LABEL_DIM,
    LabelEncoder,
)
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

SEED = 0
LABEL_POOL_PATH = REPO_ROOT / "reports" / "deep_action_labels.json"
DEFAULT_OUT = REPO_ROOT / "reports" / "probe_action_representation.json"

# Groups whose members a human would call the same action, or near enough that a policy
# ought to transfer between them. Drawn from vocabulary this project actually meets: the
# deep-flow fixture, the toy site, and the Gitea captures. Deliberately small and
# hand-checked rather than generated — an inflated corpus would only add noise to an
# already small sample.
SEMANTIC_GROUPS: list[list[str]] = [
    ["Place order", "Submit order", "Confirm order"],
    ["Back", "Go back", "Return"],
    ["Cancel", "Cancel order", "Abort"],
    ["Search", "Find", "Search products"],
    ["Login", "Sign in", "Log in"],
    ["Logout", "Sign out", "Log out"],
    ["Add to cart", "Add item to cart"],
    ["Remove from cart", "Remove item"],
    ["Checkout", "Proceed to checkout"],
    ["Next", "Continue"],
    ["Previous", "Back"],
    ["Delete", "Remove"],
    ["Edit", "Modify"],
    ["Save", "Apply changes"],
    ["Home", "Go home"],
    ["Register", "Sign up", "Create account"],
]

# Pairs a human would call unrelated *and* that share a word, so the control is not
# artificially easy. These are the cases that decide whether the representation tracks
# meaning or spelling.
LEXICAL_TRAPS: list[tuple[str, str]] = [
    ("Place order", "Cancel order"),
    ("Add to cart", "Remove from cart"),
    ("Sign in", "Sign out"),
    ("Log in", "Log out"),
    ("Increase quantity", "Decrease quantity"),
    ("Select option A", "Select option B"),
    ("Search products", "Search orders"),
    ("Go back", "Go home"),
    ("Delete account", "Create account"),
    ("Order history", "Place order"),
]


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity, with the zero vector handled explicitly rather than by nan.

    `LabelEncoder.encode("")` returns an all-zero vector (there are no trigrams to hash),
    so this is a reachable input, not a defensive nicety. Defined as 0.0: an unlabelled
    control is not similar to anything, and returning nan would silently poison every
    aggregate downstream.
    """
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def trigrams(label: str) -> set[str]:
    """The exact trigram set `LabelEncoder` hashes, including its two-space padding."""
    text = (label or "").strip().lower()
    if not text:
        return set()
    padded = f"  {text}  "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def jaccard(left: set, right: set) -> float:
    if not left and not right:
        return 0.0
    return len(left & right) / len(left | right)


def tokens(label: str) -> set[str]:
    return set((label or "").strip().lower().split())


def describe(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    array = np.asarray(values, dtype=np.float64)
    return {
        "n": int(array.size),
        "mean": round(float(array.mean()), 6),
        "median": round(float(np.median(array)), 6),
        "sd": round(float(array.std(ddof=1)) if array.size > 1 else 0.0, 6),
        "min": round(float(array.min()), 6),
        "max": round(float(array.max()), 6),
        "p25": round(float(np.percentile(array, 25)), 6),
        "p75": round(float(np.percentile(array, 75)), 6),
    }


def pair_record(encoder: LabelEncoder, left: str, right: str, kind: str) -> dict:
    return {
        "a": left,
        "b": right,
        "kind": kind,
        "cosine": round(cosine(encoder.encode(left), encoder.encode(right)), 6),
        "trigram_jaccard": round(jaccard(trigrams(left), trigrams(right)), 6),
        "token_jaccard": round(jaccard(tokens(left), tokens(right)), 6),
        "shares_token": bool(tokens(left) & tokens(right)),
    }


# -- label pool ----------------------------------------------------------------------

def harvest_labels() -> dict:
    """One browser pass over the fixture, recording the labels the registry really emits.

    Run only with `--harvest-labels`. Everything else in this probe reads the cached JSON,
    so the measurement reproduces offline.
    """
    from web_testing_agent.envs import WebFunctionalEnv
    from web_testing_agent.utils.local_server import serve_directory

    site = REPO_ROOT / "tests" / "fixtures" / "deep_flow_site"
    pages = sorted(p.name for p in site.glob("*.html"))
    found: dict[str, list[dict]] = {}
    with serve_directory(site) as origin:
        for page in pages:
            env = WebFunctionalEnv(base_url=f"{origin}/{page}", max_steps=5, headless=True)
            try:
                _obs, info = env.reset(seed=0)
                found[page] = [
                    {"element_id": spec.element_id or "",
                     "description": spec.description,
                     "action_type": spec.action_type.value}
                    for spec in info["action_specs"]
                ]
            finally:
                env.close()
    payload = {
        "_source": "WebFunctionalEnv._build_action_specs on tests/fixtures/deep_flow_site",
        "pages": found,
    }
    LABEL_POOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    LABEL_POOL_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("harvested action labels from {} pages -> {}", len(found), LABEL_POOL_PATH)
    return payload


def load_pool() -> dict:
    if not LABEL_POOL_PATH.exists():
        raise SystemExit(
            f"{LABEL_POOL_PATH} is missing. Regenerate it once with:\n"
            f"    python scripts/probe_action_representation.py --harvest-labels"
        )
    return json.loads(LABEL_POOL_PATH.read_text(encoding="utf-8"))


# -- experiments ---------------------------------------------------------------------

def experiment_1(encoder: LabelEncoder) -> list[dict]:
    """Every within-group pair, plus the lexical traps."""
    records = []
    for group in SEMANTIC_GROUPS:
        for left, right in itertools.combinations(group, 2):
            records.append(pair_record(encoder, left, right, "semantic"))
    for left, right in LEXICAL_TRAPS:
        records.append(pair_record(encoder, left, right, "lexical_trap"))
    return records


def random_pairs(encoder: LabelEncoder, rng: np.random.Generator, count: int) -> list[dict]:
    """Unrelated pairs drawn from the same label vocabulary.

    Sampled from the union of the semantic-group members themselves, so the control has
    the same phrasing style and length distribution as the semantic set — comparing those
    phrases against short fixture labels like `Home` would make the control artificially
    easy and the separation artificially large.
    """
    pool = sorted({label for group in SEMANTIC_GROUPS for label in group})
    banned = {frozenset((a, b)) for group in SEMANTIC_GROUPS
              for a, b in itertools.combinations(group, 2)}
    banned |= {frozenset(pair) for pair in LEXICAL_TRAPS}
    records, seen = [], set()
    attempts = 0
    while len(records) < count and attempts < count * 200:
        attempts += 1
        i, j = rng.choice(len(pool), size=2, replace=False)
        left, right = pool[int(i)], pool[int(j)]
        key = frozenset((left, right))
        if key in banned or key in seen:
            continue
        seen.add(key)
        records.append(pair_record(encoder, left, right, "random"))
    return records


def experiment_2(semantic: list[float], random_sims: list[float], rng: np.random.Generator) -> dict:
    """Semantic vs random, with a bootstrap CI and a nonparametric test."""
    from scipy import stats  # local: scipy is absent from requirements-ci.txt

    s, r = np.asarray(semantic), np.asarray(random_sims)
    boot = np.array([
        rng.choice(s, s.size, replace=True).mean() - rng.choice(r, r.size, replace=True).mean()
        for _ in range(20000)
    ])
    u, p = stats.mannwhitneyu(s, r, alternative="two-sided")
    # Rank-biserial correlation: the effect size that belongs with Mann-Whitney, and
    # readable as "how often does a semantic pair outrank a random one".
    rank_biserial = 2.0 * u / (s.size * r.size) - 1.0
    return {
        "semantic": describe(semantic),
        "random": describe(random_sims),
        "mean_difference": round(float(s.mean() - r.mean()), 6),
        "median_difference": round(float(np.median(s) - np.median(r)), 6),
        "bootstrap_95ci_mean_difference": [
            round(float(np.percentile(boot, 2.5)), 6),
            round(float(np.percentile(boot, 97.5)), 6),
        ],
        "mann_whitney_u": round(float(u), 3),
        "mann_whitney_p": round(float(p), 6),
        "rank_biserial_effect_size": round(float(rank_biserial), 6),
        "prob_semantic_exceeds_random": round(float(u / (s.size * r.size)), 6),
    }


def experiment_3(encoder: LabelEncoder, rng: np.random.Generator, n_perm: int = 5000) -> dict:
    """Does the separation depend on the labels being semantically grouped?

    Shuffles which labels occupy which group slots, preserving the group sizes and
    therefore the pair count exactly, and preserving the label-length distribution
    exactly because the same multiset of labels is redealt. If the true separation is
    ordinary for this vocabulary, the observed value sits mid-distribution.
    """
    pool = [label for group in SEMANTIC_GROUPS for label in group]
    sizes = [len(group) for group in SEMANTIC_GROUPS]
    control = [record["cosine"]
               for record in random_pairs(encoder, np.random.default_rng(SEED), 120)]
    control_mean = float(np.mean(control))

    observed = float(np.mean([
        cosine(encoder.encode(a), encoder.encode(b))
        for group in SEMANTIC_GROUPS for a, b in itertools.combinations(group, 2)
    ])) - control_mean

    separations = []
    for _ in range(n_perm):
        shuffled = list(rng.permutation(pool))
        groups, cursor = [], 0
        for size in sizes:
            groups.append(shuffled[cursor : cursor + size])
            cursor += size
        sims = [cosine(encoder.encode(a), encoder.encode(b))
                for group in groups for a, b in itertools.combinations(group, 2)]
        separations.append(float(np.mean(sims)) - control_mean)

    separations = np.asarray(separations)
    # Two-sided permutation p, with the +1 correction so a p of exactly 0 is impossible.
    p_value = (1 + int((np.abs(separations) >= abs(observed)).sum())) / (n_perm + 1)
    return {
        "n_permutations": n_perm,
        "observed_separation": round(observed, 6),
        "permutation_mean": round(float(separations.mean()), 6),
        "permutation_sd": round(float(separations.std(ddof=1)), 6),
        "permutation_p95": round(float(np.percentile(separations, 95)), 6),
        "percentile_of_observed": round(
            float((separations < observed).mean() * 100), 2),
        "two_sided_p": round(p_value, 6),
    }


def experiment_4(encoder: LabelEncoder, pool: dict, top: int = 20) -> dict:
    """The real fixture vocabulary: how many distinct labels, and what is near what."""
    labels, identities = set(), set()
    for entries in pool["pages"].values():
        for entry in entries:
            if entry["element_id"]:
                labels.add(entry["element_id"])
            identities.add((entry["action_type"], entry["element_id"]))
    ordered = sorted(labels)
    pairs = [
        {"a": a, "b": b,
         "cosine": round(cosine(encoder.encode(a), encoder.encode(b)), 6),
         "trigram_jaccard": round(jaccard(trigrams(a), trigrams(b)), 6),
         "shares_token": bool(tokens(a) & tokens(b))}
        for a, b in itertools.combinations(ordered, 2)
    ]
    pairs.sort(key=lambda record: record["cosine"], reverse=True)
    sims = [record["cosine"] for record in pairs]
    return {
        "unique_labels": len(ordered),
        "unique_action_identities": len(identities),
        "labels": ordered,
        "pair_count": len(pairs),
        "cosine_distribution": describe(sims),
        "most_similar": pairs[:top],
        "least_similar": pairs[-top:][::-1],
        "unrelated_pairs_above_0_3": [
            record for record in pairs
            if record["cosine"] > 0.3 and not record["shares_token"]
        ][:20],
    }


def secondary_q_diagnostic(checkpoint: Path, encoder: LabelEncoder) -> dict:
    """SECONDARY, clearly labelled: does label geometry reach Q at initialisation?

    Loads an **untrained** network (`*_init.zip`) and scores a synthetic state whose
    action rows differ only in the label block — every action a CLICK with identical
    scalars, which is exactly the regime where the label hash is the only discriminator.
    Correlating pairwise label cosine against pairwise |Q difference| says whether the
    geometry is even visible to the head before any learning.

    This is not evidence about the trained policy. Nothing is retrained; the checkpoint is
    opened read-only.
    """
    import torch as th
    from scipy import stats

    from web_testing_agent.agents.ac_dqn import AC_OBS_DIM, STATE_BLOCK, ActionConditionedDQN
    from web_testing_agent.agents.action_features import (
        _ACTION_TYPES,
        ACTION_FEATURE_DIM,
    )
    from web_testing_agent.envs.types import MAX_ACTIONS, ActionType

    model = ActionConditionedDQN.load(str(checkpoint), device="cpu")
    labels = sorted({label for group in SEMANTIC_GROUPS for label in group})[:MAX_ACTIONS]

    obs = np.zeros(AC_OBS_DIM, dtype=np.float32)
    block = np.zeros((MAX_ACTIONS, ACTION_FEATURE_DIM), dtype=np.float32)
    click = _ACTION_TYPES.index(ActionType.CLICK)
    for row, label in enumerate(labels):
        block[row, click] = 1.0                                   # identical type one-hot
        block[row, len(_ACTION_TYPES) : len(_ACTION_TYPES) + LABEL_DIM] = encoder.encode(label)
        # scalars deliberately left at zero: identical for every row, which is the real
        # situation for two CLICKs on one page once record_taken() is dead.
    obs[STATE_BLOCK : STATE_BLOCK + MAX_ACTIONS * ACTION_FEATURE_DIM] = block.reshape(-1)
    obs[-MAX_ACTIONS:][: len(labels)] = 1.0

    with th.no_grad():
        q = model.q_net(th.as_tensor(obs).unsqueeze(0)).squeeze(0).numpy()[: len(labels)]

    cos_pairs, dq_pairs = [], []
    for i, j in itertools.combinations(range(len(labels)), 2):
        cos_pairs.append(cosine(encoder.encode(labels[i]), encoder.encode(labels[j])))
        dq_pairs.append(abs(float(q[i] - q[j])))
    rho, p = stats.spearmanr(cos_pairs, dq_pairs)
    return {
        "_caveat": "UNTRAINED network. Says nothing about the trained policy.",
        "checkpoint": str(checkpoint),
        "labels_scored": len(labels),
        "q_spread": round(float(q.max() - q.min()), 6),
        "spearman_cosine_vs_absolute_q_gap": round(float(rho), 6),
        "spearman_p": round(float(p), 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--harvest-labels", action="store_true",
                        help="one browser pass to regenerate reports/deep_action_labels.json")
    parser.add_argument("--checkpoint", type=Path, default=None,
                        help="optional SECONDARY diagnostic: an *_init.zip to read Q from")
    parser.add_argument("--random-pairs", type=int, default=120)
    parser.add_argument("--permutations", type=int, default=5000)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    from scipy import stats  # local: see experiment_2

    if args.harvest_labels:
        harvest_labels()

    encoder = LabelEncoder()
    rng = np.random.default_rng(SEED)
    pool = load_pool()

    pairs = experiment_1(encoder)
    randoms = random_pairs(encoder, rng, args.random_pairs)
    semantic = [p["cosine"] for p in pairs if p["kind"] == "semantic"]
    traps = [p["cosine"] for p in pairs if p["kind"] == "lexical_trap"]
    random_sims = [p["cosine"] for p in randoms]

    stats_block = experiment_2(semantic, random_sims, rng)
    permutation = experiment_3(encoder, np.random.default_rng(SEED), args.permutations)
    audit = experiment_4(encoder, pool)

    # The stratification that decides between "semantic" and "lexical".
    shares = [p["cosine"] for p in pairs if p["kind"] == "semantic" and p["shares_token"]]
    disjoint = [p["cosine"] for p in pairs if p["kind"] == "semantic" and not p["shares_token"]]
    all_sims = [p["cosine"] for p in pairs + randoms]
    all_tri = [p["trigram_jaccard"] for p in pairs + randoms]
    tri_rho, tri_p = stats.spearmanr(all_tri, all_sims)

    payload = {
        "_purpose": "Does the 32-dim trigram hash of an action label encode semantic "
                    "similarity, or only shared characters?",
        "_scope": "Label embedding geometry ONLY. Says nothing about whether a trained "
                  "policy exploits it.",
        "seed": SEED,
        "encoder": {"class": "LabelEncoder", "label_dim": LABEL_DIM,
                    "algorithm": "signed blake2b character-trigram hashing, L2-normalised"},
        "experiment_1_pairs": pairs + randoms,
        "experiment_2_statistics": stats_block,
        "experiment_3_permutation": permutation,
        "experiment_4_fixture_audit": audit,
        "lexical_stratification": {
            "semantic_sharing_a_token": describe(shares),
            "semantic_with_no_shared_token": describe(disjoint),
            "lexical_traps_unrelated_but_share_a_token": describe(traps),
            "spearman_trigram_overlap_vs_cosine": round(float(tri_rho), 6),
            "spearman_p": round(float(tri_p), 6),
        },
    }
    if args.checkpoint:
        payload["secondary_q_diagnostic"] = secondary_q_diagnostic(args.checkpoint, encoder)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # -- human-readable report --
    print("=" * 78)
    print("ACTION-LABEL REPRESENTATION PROBE  (measurement only, no training)")
    print("=" * 78)
    print(f"encoder: LabelEncoder, {LABEL_DIM}-dim signed trigram hash, L2-normalised")
    print(f"seed: {SEED}\n")

    print("-- Experiment 2: semantic vs random --------------------------------------")
    for name in ("semantic", "random"):
        d = stats_block[name]
        print(f"  {name:<9} n={d['n']:<4} mean {d['mean']:+.4f}  median {d['median']:+.4f}  "
              f"sd {d['sd']:.4f}  [{d['min']:+.3f}, {d['max']:+.3f}]")
    print(f"  mean difference   {stats_block['mean_difference']:+.4f}   "
          f"bootstrap 95% CI {stats_block['bootstrap_95ci_mean_difference']}")
    print(f"  median difference {stats_block['median_difference']:+.4f}")
    print(f"  Mann-Whitney U={stats_block['mann_whitney_u']}  "
          f"p={stats_block['mann_whitney_p']:.4f}  "
          f"rank-biserial={stats_block['rank_biserial_effect_size']:+.4f}")
    print(f"  P(semantic pair > random pair) = {stats_block['prob_semantic_exceeds_random']:.3f}\n")

    print("-- Experiment 3: permutation control -------------------------------------")
    print(f"  observed separation {permutation['observed_separation']:+.4f}")
    print(f"  permuted mean {permutation['permutation_mean']:+.4f} "
          f"sd {permutation['permutation_sd']:.4f}  p95 {permutation['permutation_p95']:+.4f}")
    print(f"  observed sits at the {permutation['percentile_of_observed']:.1f}th percentile "
          f"of {permutation['n_permutations']} permutations, two-sided p="
          f"{permutation['two_sided_p']:.4f}\n")

    print("-- Lexical stratification: the deciding split ----------------------------")
    for label, block in (("semantic, shares a word", describe(shares)),
                         ("semantic, NO shared word", describe(disjoint)),
                         ("UNRELATED but shares a word", describe(traps)),
                         ("random control", stats_block["random"])):
        print(f"  {label:<28} n={block['n']:<4} mean {block['mean']:+.4f}  "
              f"median {block['median']:+.4f}")
    print(f"  Spearman(trigram overlap, cosine) = {tri_rho:+.4f}  (p={tri_p:.2e})\n")

    print("-- Experiment 4: real deep-flow fixture vocabulary -----------------------")
    print(f"  unique labels {audit['unique_labels']}, unique action identities "
          f"{audit['unique_action_identities']}, pairs {audit['pair_count']}")
    d = audit["cosine_distribution"]
    print(f"  cosine over all fixture pairs: mean {d['mean']:+.4f} median {d['median']:+.4f} "
          f"[{d['min']:+.3f}, {d['max']:+.3f}]")
    print("  most similar:")
    for record in audit["most_similar"][:8]:
        flag = "shares a word" if record["shares_token"] else "NO shared word"
        print(f"    {record['cosine']:+.4f}  {record['a']!r} ~ {record['b']!r}   ({flag})")
    print("  least similar:")
    for record in audit["least_similar"][:5]:
        print(f"    {record['cosine']:+.4f}  {record['a']!r} ~ {record['b']!r}")
    if audit["unrelated_pairs_above_0_3"]:
        print("  unrelated (no shared word) yet cosine > 0.3:")
        for record in audit["unrelated_pairs_above_0_3"][:8]:
            print(f"    {record['cosine']:+.4f}  {record['a']!r} ~ {record['b']!r}")

    if args.checkpoint:
        q = payload["secondary_q_diagnostic"]
        print("\n-- SECONDARY (untrained network; not evidence about the trained policy) --")
        print(f"  {q['checkpoint']}")
        print(f"  Q spread over {q['labels_scored']} same-type actions: {q['q_spread']:.4f}")
        print(f"  Spearman(label cosine, |Q gap|) = "
              f"{q['spearman_cosine_vs_absolute_q_gap']:+.4f} (p={q['spearman_p']:.4f})")

    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
