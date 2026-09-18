"""Do the trained checkpoints rank actions the way the label hash arranges them?

**The question this answers, and the one it does not.** The offline probe established that
`LabelEncoder`'s 32-dim trigram hash tracks spelling rather than meaning — `Place order` is
+0.647 from `Start order`, +0.424 from `Cancel order`, and exactly 0.000 from `Continue`.
That was a statement about the *encoder*. This asks whether the geometry is visible in the
Q-values of models that actually trained on it, by scoring real captured observations
against all 16 post-Markov checkpoints. It is a diagnostic, not an intervention: a
relationship here would show the trained head is sensitive to the geometry, and would still
not show the geometry caused the order-4 deadlock.

**One structural fact that has to frame every number below.** The head is dueling, so
`Q(s,a) = V(s) + A(s,a) - mean_a' A(s,a')`. Within one state `V(s)` and the mean advantage
are constants shared by every action, so they cancel exactly in any within-state Q gap. The
gap is therefore *by construction* a function of the two action embeddings and the state
features alone — a non-zero cosine/gap relationship is close to guaranteed, and a p-value
against "no relationship" would be answering a straw question. What carries information is
the **effect size, the sign, and the magnitude of the gaps against the reward scale**
(a novelty payment is ~1.0, a reveal ~1.5, so ~1.5-2.5 is the scale that matters).

Runs entirely offline against `reports/trained_action_q_states.json`; no browser, no
training, and checkpoints are opened read-only.

    python scripts/capture_action_q_states.py          # once, needs the fixture server
    python scripts/diagnose_trained_action_q.py
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics as st
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from web_testing_agent.agents.action_features import LabelEncoder  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

STATES_PATH = REPO_ROOT / "reports" / "trained_action_q_states.json"
CKPT_DIR = REPO_ROOT / "models" / "checkpoints" / "postmarkov_baseline"
OUT_JSON = REPO_ROOT / "reports" / "trained_action_q_diagnostic.json"
OUT_MD = REPO_ROOT / "reports" / "trained_action_q_diagnostic.md"

SEEDS = list(range(8))
PHASES = ("init", "final")
DEADLOCK = "flow:order-4-deadlock"
REWARD_SCALE = (1.5, 2.5)
COSINE_BINS = [(-1.01, -0.25), (-0.25, 0.0), (0.0, 0.25), (0.25, 0.5), (0.5, 1.01)]

# Hypothesis categories, not measured facts. Named so the report cannot quietly present
# them as findings: they encode what a human means by the labels, which is exactly the
# thing the representation is suspected of getting wrong.
PAIR_CATEGORY = {
    ("Place order", "Cancel order"): "misleadingly similar (opposite outcomes)",
    ("Place order", "Back"): "behaviourally distinct",
}


def label_of(spec: dict) -> str:
    """What `ActionFeatureExtractor._encode_one` hashes: element_id, else description."""
    return spec.get("element_id") or spec.get("description") or ""


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def describe(values) -> dict:
    values = list(values)
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
    }


def correlate(x, y) -> dict:
    """Pearson and Spearman, with the degenerate cases reported rather than raising."""
    from scipy import stats

    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return {"n": len(x), "pearson_r": None, "spearman_rho": None,
                "note": "insufficient or constant sample"}
    pr, pp = stats.pearsonr(x, y)
    sr, sp = stats.spearmanr(x, y)
    return {"n": len(x),
            "pearson_r": round(float(pr), 6), "pearson_p": float(f"{pp:.3e}"),
            "spearman_rho": round(float(sr), 6), "spearman_p": float(f"{sp:.3e}")}


def load_checkpoints() -> list[dict]:
    from web_testing_agent.agents.ac_dqn import ActionConditionedDQN

    found = []
    for phase in PHASES:
        for seed in SEEDS:
            path = CKPT_DIR / f"ac_dqn_seed{seed}_{phase}.zip"
            if not path.exists():
                raise SystemExit(f"missing checkpoint: {path}")
            found.append({"seed": seed, "phase": phase, "path": path,
                          "model": ActionConditionedDQN.load(str(path), device="cpu")})
            logger.info("loaded {}", path.name)
    return found


def score(model, observation: np.ndarray, n_valid: int) -> np.ndarray:
    """Q for the valid actions of one state. Read-only; no gradients, no RNG."""
    import torch as th

    with th.no_grad():
        q = model.q_net(th.as_tensor(observation, dtype=th.float32).unsqueeze(0))
    return q.squeeze(0).numpy()[:n_valid]


def build_rows(states: list[dict], checkpoints: list[dict], encoder: LabelEncoder):
    """Per-action Q rows, and per-pair (cosine, gap) rows, for every checkpoint x state."""
    embeddings = {}
    for state in states:
        for spec in state["action_specs"]:
            label = label_of(spec)
            if label not in embeddings:
                embeddings[label] = encoder.encode(label)

    action_rows, pair_rows = [], []
    for ckpt in checkpoints:
        for state in states:
            n = state["num_valid_actions"]
            q = score(ckpt["model"], np.asarray(state["observation"], dtype=np.float32), n)
            order = np.argsort(-q)
            rank = {int(idx): int(r) for r, idx in enumerate(order)}
            specs = state["action_specs"][:n]
            for i, spec in enumerate(specs):
                action_rows.append({
                    "seed": ckpt["seed"], "phase": ckpt["phase"],
                    "state_id": state["state_id"], "page": state["page_file"],
                    "index": i, "label": label_of(spec), "action_type": spec["action_type"],
                    "q": round(float(q[i]), 6), "rank": rank[i],
                    "is_argmax": rank[i] == 0, "n_valid": n,
                })
            for i, j in itertools.combinations(range(n), 2):
                la, lb = label_of(specs[i]), label_of(specs[j])
                pair_rows.append({
                    "seed": ckpt["seed"], "phase": ckpt["phase"],
                    "state_id": state["state_id"],
                    "a": la, "b": lb,
                    "type_a": specs[i]["action_type"], "type_b": specs[j]["action_type"],
                    "same_type": specs[i]["action_type"] == specs[j]["action_type"],
                    "cosine": round(cosine(embeddings[la], embeddings[lb]), 6),
                    "q_a": round(float(q[i]), 6), "q_b": round(float(q[j]), 6),
                    "q_gap": round(float(q[i] - q[j]), 6),
                    "abs_q_gap": round(abs(float(q[i] - q[j])), 6),
                    "rank_a": rank[i], "rank_b": rank[j],
                    "a_is_argmax": rank[i] == 0, "b_is_argmax": rank[j] == 0,
                    "n_valid": n,
                })
    return action_rows, pair_rows


def binned(rows: list[dict]) -> list[dict]:
    out = []
    for lo, hi in COSINE_BINS:
        subset = [r for r in rows if lo <= r["cosine"] < hi]
        out.append({
            "cosine_bin": f"[{lo:+.2f}, {hi:+.2f})",
            "n": len(subset),
            "mean_abs_q_gap": round(st.mean(r["abs_q_gap"] for r in subset), 6) if subset else None,
            "median_abs_q_gap": (round(st.median(r["abs_q_gap"] for r in subset), 6)
                                 if subset else None),
        })
    return out


def analysis_a(pair_rows: list[dict]) -> dict:
    final = [r for r in pair_rows if r["phase"] == "final"]
    subsets = {
        "all_pairs_final": final,
        "click_click_final": [r for r in final
                              if r["type_a"] == r["type_b"] == "CLICK"],
        "type_type_final": [r for r in final if r["type_a"] == r["type_b"] == "TYPE"],
        "same_type_final": [r for r in final if r["same_type"]],
        "cross_type_final": [r for r in final if not r["same_type"]],
    }
    result = {}
    for name, rows in subsets.items():
        result[name] = {
            "correlation_cosine_vs_abs_gap": correlate(
                [r["cosine"] for r in rows], [r["abs_q_gap"] for r in rows]),
            "correlation_cosine_vs_signed_gap": correlate(
                [r["cosine"] for r in rows], [r["q_gap"] for r in rows]),
            "abs_q_gap": describe(r["abs_q_gap"] for r in rows),
            "cosine": describe(r["cosine"] for r in rows),
            "bins": binned(rows),
        }
    # Per-seed, because pooling 8 seeds x 17 states treats repeated measures of the same
    # pair as independent observations, which they are not.
    per_seed = []
    for phase in PHASES:
        for seed in SEEDS:
            rows = [r for r in pair_rows
                    if r["seed"] == seed and r["phase"] == phase
                    and r["type_a"] == r["type_b"] == "CLICK"]
            corr = correlate([r["cosine"] for r in rows], [r["abs_q_gap"] for r in rows])
            per_seed.append({"seed": seed, "phase": phase, **corr,
                             "mean_abs_q_gap": describe(r["abs_q_gap"] for r in rows)["mean"]})
    result["per_seed_click_click"] = per_seed
    return result


def analysis_b(pair_rows: list[dict]) -> dict:
    """The two requested target pairs, wherever they are genuinely co-valid."""
    out = {}
    for a, b in (("Place order", "Cancel order"), ("Place order", "Back")):
        rows = [r for r in pair_rows
                if {r["a"], r["b"]} == {a, b}]
        detail = []
        for r in rows:
            # Normalise orientation so `q_gap` always reads A-minus-B.
            flip = r["a"] != a
            detail.append({
                "seed": r["seed"], "phase": r["phase"], "state_id": r["state_id"],
                "cosine": r["cosine"],
                "q_place": r["q_b"] if flip else r["q_a"],
                "q_other": r["q_a"] if flip else r["q_b"],
                "gap_place_minus_other": -r["q_gap"] if flip else r["q_gap"],
                "rank_place": r["rank_b"] if flip else r["rank_a"],
                "rank_other": r["rank_a"] if flip else r["rank_b"],
                "n_valid": r["n_valid"],
            })
        by_phase = {}
        for phase in PHASES:
            sub = [d for d in detail if d["phase"] == phase]
            if not sub:
                continue
            gaps = [d["gap_place_minus_other"] for d in sub]
            by_phase[phase] = {
                "n": len(sub),
                "cosine": sub[0]["cosine"],
                "gap": describe(gaps),
                "abs_gap": describe(abs(g) for g in gaps),
                "place_order_ranked_higher": sum(1 for g in gaps if g > 0),
                "other_ranked_higher": sum(1 for g in gaps if g < 0),
                "mean_rank_place": round(st.mean(d["rank_place"] for d in sub), 3),
                "mean_rank_other": round(st.mean(d["rank_other"] for d in sub), 3),
            }
        out[f"{a} vs {b}"] = {
            "category_hypothesis": PAIR_CATEGORY.get((a, b), "unclassified"),
            "n_observations": len(detail),
            "by_phase": by_phase,
            "observations": detail,
        }
    return out


def analysis_c(pair_rows: list[dict]) -> dict:
    """Does training amplify, ignore, or flatten the geometry?"""
    out = {}
    for phase in PHASES:
        rows = [r for r in pair_rows
                if r["phase"] == phase and r["type_a"] == r["type_b"] == "CLICK"]
        out[phase] = {
            "correlation_cosine_vs_abs_gap": correlate(
                [r["cosine"] for r in rows], [r["abs_q_gap"] for r in rows]),
            "abs_q_gap": describe(r["abs_q_gap"] for r in rows),
            "bins": binned(rows),
        }
    init_gap = out["init"]["abs_q_gap"]["mean"]
    final_gap = out["final"]["abs_q_gap"]["mean"]
    out["q_gap_growth_factor"] = round(final_gap / init_gap, 3) if init_gap else None
    out["reward_scale_for_reference"] = list(REWARD_SCALE)
    out["final_mean_gap_as_fraction_of_reward_scale"] = round(
        final_gap / REWARD_SCALE[0], 4) if final_gap else None
    return out


def analysis_d(pair_rows: list[dict], states: list[dict]) -> dict:
    """Controls drawn only from pairs that are genuinely co-valid in a captured state.

    Two contrasts the encoder probe predicts should exist: labels sharing no word yet
    landing at high cosine, and labels a human would call related landing at low cosine.
    Selected from the captured states, never manufactured.
    """
    final = [r for r in pair_rows
             if r["phase"] == "final" and r["type_a"] == r["type_b"] == "CLICK"]
    unique: dict[tuple, dict] = {}
    for r in final:
        unique.setdefault((r["state_id"], r["a"], r["b"]), r)

    def shares_word(a: str, b: str) -> bool:
        return bool(set(a.lower().split()) & set(b.lower().split()))

    high_unrelated = sorted(
        (r for r in unique.values() if r["cosine"] > 0.3 and not shares_word(r["a"], r["b"])),
        key=lambda r: -r["cosine"])
    low_related = sorted(
        (r for r in unique.values() if r["cosine"] < 0.15 and shares_word(r["a"], r["b"])),
        key=lambda r: r["cosine"])

    def summarise(rows, limit=12):
        keys = {(r["state_id"], r["a"], r["b"]) for r in rows}
        matching = [r for r in final if (r["state_id"], r["a"], r["b"]) in keys]
        return {
            "n_distinct_pairs": len(keys),
            "n_observations": len(matching),
            "cosine": describe(r["cosine"] for r in matching),
            "abs_q_gap": describe(r["abs_q_gap"] for r in matching),
            "examples": [{"a": r["a"], "b": r["b"], "state_id": r["state_id"],
                          "cosine": r["cosine"]} for r in rows[:limit]],
        }

    return {
        "high_cosine_no_shared_word": summarise(high_unrelated),
        "low_cosine_shared_word": summarise(low_related),
        "_note": "Both groups are genuinely co-valid in a captured state. Category labels "
                 "are human judgements about meaning, not measured properties.",
    }


def analysis_e(action_rows: list[dict]) -> dict:
    """Complete valid-action ranking at the order-4 deadlock state, per checkpoint."""
    rows = [r for r in action_rows if r["state_id"] == DEADLOCK]
    per_ckpt = []
    for phase in PHASES:
        for seed in SEEDS:
            sub = sorted((r for r in rows if r["seed"] == seed and r["phase"] == phase),
                         key=lambda r: r["rank"])
            lookup = {r["label"]: r for r in sub}
            place = lookup.get("Place order")
            per_ckpt.append({
                "seed": seed, "phase": phase,
                "argmax": sub[0]["label"], "argmax_type": sub[0]["action_type"],
                "rank_place_order": place["rank"] if place else None,
                "q_place_order": place["q"] if place else None,
                "rank_cancel_order": lookup["Cancel order"]["rank"],
                "q_cancel_order": lookup["Cancel order"]["q"],
                "rank_back": lookup["Back"]["rank"],
                "q_back": lookup["Back"]["q"],
                "n_valid": sub[0]["n_valid"],
                "top5": [{"label": r["label"], "q": r["q"]} for r in sub[:5]],
            })
    finals = [c for c in per_ckpt if c["phase"] == "final"]
    return {
        "state_id": DEADLOCK,
        "per_checkpoint": per_ckpt,
        "final_summary": {
            "n_seeds": len(finals),
            "place_order_is_argmax": sum(1 for c in finals if c["argmax"] == "Place order"),
            "place_order_rank": describe(c["rank_place_order"] for c in finals),
            "place_above_cancel": sum(1 for c in finals
                                      if c["rank_place_order"] < c["rank_cancel_order"]),
            "place_above_back": sum(1 for c in finals
                                    if c["rank_place_order"] < c["rank_back"]),
            "argmax_labels": sorted({c["argmax"] for c in finals}),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--states", type=Path, default=STATES_PATH)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    args = parser.parse_args()

    if not args.states.exists():
        raise SystemExit(
            f"{args.states} is missing. Capture the states first:\n"
            f"    python scripts/capture_action_q_states.py --verify")
    capture = json.loads(args.states.read_text(encoding="utf-8"))
    states = capture["states"]
    encoder = LabelEncoder()
    checkpoints = load_checkpoints()

    action_rows, pair_rows = build_rows(states, checkpoints, encoder)
    result = {
        "_purpose": "Are the trained checkpoints' within-state action rankings sensitive "
                    "to the action-label hash geometry?",
        "_scope": "Diagnostic only. A relationship here shows sensitivity, NOT that the "
                  "representation caused the order-4 deadlock.",
        "_dueling_note": "Q = V(s) + A(s,a) - mean A, so V(s) cancels within a state and a "
                         "non-zero cosine/gap relationship is structurally expected. Read "
                         "effect size and magnitude against the ~1.5-2.5 reward scale.",
        "n_states": len(states),
        "n_checkpoints": len(checkpoints),
        "n_action_observations": len(action_rows),
        "n_pair_observations": len(pair_rows),
        "analysis_A_within_state_geometry": analysis_a(pair_rows),
        "analysis_B_targeted_pairs": analysis_b(pair_rows),
        "analysis_C_init_vs_final": analysis_c(pair_rows),
        "analysis_D_controls": analysis_d(pair_rows, states),
        "analysis_E_order4_deadlock": analysis_e(action_rows),
        "analysis_F_coverage": capture["coverage"],
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    args.out_md.write_text(render_markdown(result, capture), encoding="utf-8")

    a = result["analysis_A_within_state_geometry"]["click_click_final"]
    e = result["analysis_E_order4_deadlock"]["final_summary"]
    print("=" * 78)
    print("TRAINED-CHECKPOINT ACTION-Q DIAGNOSTIC")
    print("=" * 78)
    print(f"states {result['n_states']}  checkpoints {result['n_checkpoints']}  "
          f"action rows {result['n_action_observations']}  "
          f"pair rows {result['n_pair_observations']}")
    print(f"\nCLICK-CLICK, final checkpoints: cosine vs |Q gap|  "
          f"Pearson {a['correlation_cosine_vs_abs_gap']['pearson_r']:+.4f}  "
          f"Spearman {a['correlation_cosine_vs_abs_gap']['spearman_rho']:+.4f}  "
          f"(n={a['correlation_cosine_vs_abs_gap']['n']})")
    print(f"  mean |Q gap| {a['abs_q_gap']['mean']:.4f}   reward scale {REWARD_SCALE}")
    for row in a["bins"]:
        if row["n"]:
            print(f"    {row['cosine_bin']:<18} n={row['n']:<6} "
                  f"mean |Q gap| {row['mean_abs_q_gap']:.4f}")
    print(f"\norder-4 deadlock, final: Place order is argmax in "
          f"{e['place_order_is_argmax']}/{e['n_seeds']} seeds; "
          f"ranked above Cancel order in {e['place_above_cancel']}/{e['n_seeds']}, "
          f"above Back in {e['place_above_back']}/{e['n_seeds']}")
    print(f"  argmax labels observed: {e['argmax_labels']}")
    print(f"\nWrote {args.out_json}\nWrote {args.out_md}")


def render_markdown(result: dict, capture: dict) -> str:
    a_all = result["analysis_A_within_state_geometry"]
    click = a_all["click_click_final"]
    c = result["analysis_C_init_vs_final"]
    e = result["analysis_E_order4_deadlock"]
    lines: list[str] = []
    add = lines.append

    add("# Trained-checkpoint action-Q diagnostic\n")
    add("## 1. Executive conclusion\n")
    corr = click["correlation_cosine_vs_abs_gap"]
    add(f"Across {corr['n']} genuine within-state CLICK-CLICK action pairs scored by the 8 "
        f"final post-Markov checkpoints, action-label cosine correlates with |Q gap| at "
        f"Pearson {corr['pearson_r']:+.4f} / Spearman {corr['spearman_rho']:+.4f}. "
        f"Mean |Q gap| is {click['abs_q_gap']['mean']:.4f} against a reward scale of "
        f"{REWARD_SCALE[0]}-{REWARD_SCALE[1]}.\n")
    add("**Read the magnitude, not the p-value.** The dueling decomposition makes "
        "`V(s)` cancel within a state, so some relationship is structurally guaranteed; "
        "what matters is whether the induced Q differences are large enough to change a "
        "ranking at the scale the reward operates on.\n")

    add("## 2. Capture methodology\n")
    add(f"{capture['n_states']} real observations captured from the deep fixture by "
        "`scripts/capture_action_q_states.py`, built with the production "
        "`encode_modalities` call that `evaluation.TrainedPolicy.act` uses. Gated states "
        "were reached by walking the flow through `setup_actions`, so `Continue` appears "
        "only where the gate genuinely opens. No state was constructed offline.\n")

    add("## 3. Dataset and coverage\n")
    add(f"- states: {result['n_states']}, checkpoints: {result['n_checkpoints']} "
        f"(8 seeds x init/final)")
    add(f"- per-action Q observations: {result['n_action_observations']}")
    add(f"- within-state pair observations: {result['n_pair_observations']}\n")
    add("| Requested pair | Co-valid state captured? | N states | N checkpoint evals |")
    add("|---|---|---|---|")
    for row in result["analysis_F_coverage"]:
        pair = " / ".join(row["pair"])
        yes = "yes" if row["n_states"] else "**no** - " + row["status"]
        add(f"| {pair} | {yes} | {row['n_states']} | {row['n_states'] * 16} |")
    add("")

    add("## 4. Within-state Q-ranking analysis\n")
    add("| subset | n | Pearson | Spearman | mean \\|Q gap\\| |")
    add("|---|---|---|---|---|")
    for name in ("all_pairs_final", "same_type_final", "click_click_final",
                 "type_type_final", "cross_type_final"):
        block = a_all[name]
        k = block["correlation_cosine_vs_abs_gap"]
        pr = f"{k['pearson_r']:+.4f}" if k.get("pearson_r") is not None else "n/a"
        sr = f"{k['spearman_rho']:+.4f}" if k.get("spearman_rho") is not None else "n/a"
        add(f"| {name} | {k['n']} | {pr} | {sr} | {block['abs_q_gap'].get('mean')} |")
    add("\n### Mean |Q gap| by cosine bin (CLICK-CLICK, final)\n")
    add("| cosine bin | n | mean \\|Q gap\\| | median |")
    add("|---|---|---|---|")
    for row in click["bins"]:
        if row["n"]:
            add(f"| {row['cosine_bin']} | {row['n']} | {row['mean_abs_q_gap']} | "
                f"{row['median_abs_q_gap']} |")
    add("")

    for key in ("Place order vs Cancel order", "Place order vs Back"):
        block = result["analysis_B_targeted_pairs"][key]
        add(f"## {'5' if 'Cancel' in key else '6'}. {key}\n")
        add(f"Hypothesis category: *{block['category_hypothesis']}*. "
            f"{block['n_observations']} observations.\n")
        add("| phase | n | cosine | mean gap (Place-other) | Place higher | other higher |")
        add("|---|---|---|---|---|---|")
        for phase, stats_ in block["by_phase"].items():
            add(f"| {phase} | {stats_['n']} | {stats_['cosine']:+.4f} | "
                f"{stats_['gap']['mean']:+.4f} | {stats_['place_order_ranked_higher']} | "
                f"{stats_['other_ranked_higher']} |")
        add("")

    add("## 7. Init vs final\n")
    add("| phase | n | Pearson | mean \\|Q gap\\| |")
    add("|---|---|---|---|")
    for phase in PHASES:
        k = c[phase]["correlation_cosine_vs_abs_gap"]
        add(f"| {phase} | {k['n']} | {k['pearson_r']:+.4f} | {c[phase]['abs_q_gap']['mean']} |")
    add(f"\nQ-gap growth factor init to final: **{c['q_gap_growth_factor']}x**. "
        f"Final mean |Q gap| is {c['final_mean_gap_as_fraction_of_reward_scale']} of the "
        f"lower reward-scale bound.\n")

    add("## 8. Order-4 deadlock analysis\n")
    s = e["final_summary"]
    add(f"At `{DEADLOCK}` ({e['per_checkpoint'][0]['n_valid']} valid actions), across the "
        f"{s['n_seeds']} final checkpoints:\n")
    add(f"- `Place order` is the argmax in **{s['place_order_is_argmax']}/{s['n_seeds']}** seeds")
    add(f"- ranked above `Cancel order` in **{s['place_above_cancel']}/{s['n_seeds']}**")
    add(f"- ranked above `Back` in **{s['place_above_back']}/{s['n_seeds']}**")
    add(f"- observed argmax labels: {s['argmax_labels']}\n")
    add("| seed | argmax | rank(Place) | Q(Place) | rank(Cancel) | rank(Back) |")
    add("|---|---|---|---|---|---|")
    for row in e["per_checkpoint"]:
        if row["phase"] == "final":
            add(f"| {row['seed']} | {row['argmax']} | {row['rank_place_order']} | "
                f"{row['q_place_order']:+.4f} | {row['rank_cancel_order']} | "
                f"{row['rank_back']} |")
    add("")

    add("## 9. Limitations\n")
    add("- Five of the seven requested pairs have **zero** co-valid states; three of those "
        "labels (`Next`, `Previous`, `Login`/`Sign in`) do not exist in any fixture. "
        "Report them as unavailable, not as negative evidence.")
    add("- One fixture, 17 states, one action vocabulary. Nothing here generalises to "
        "another application.")
    add("- Repeated measures: the same pair is scored by 8 seeds, so pooled n overstates "
        "independence. Per-seed figures are reported alongside.")
    add("- A cosine/|Q gap| relationship is structurally expected under the dueling head "
        "and is not by itself evidence of a defect.")
    add("- This is an observational diagnostic. It cannot establish that the "
        "representation caused the deadlock; only an intervention could.\n")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
