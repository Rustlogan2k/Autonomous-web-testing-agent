"""Every evaluation result of the project, gathered for one page.

Read-only and computed from the files the experiments wrote, never from a copy:

- the frozen v1 transfer experiment (`reports/transfer_final.json`);
- the frozen contextual diagnostic (`reports/contextual_diagnostic.json`);
- the LLM page-labeller gates (`reports/v2_stage0.json`, `reports/v2_label_validation.json`);
- every hybrid (v2) development round (`runs/v2-*/results/*.json`), summarised per arm, and
  the arms of the suite rounds compared on the runs they share (same application, seed).

Development rounds are tuning data, not evidence of generalisation; the page says so. The
result is cached until a result file is added or changed, so a round still running shows
up as it progresses.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORTS = REPO_ROOT / "reports"
RUNS = REPO_ROOT / "runs"
BUDGET = 2000
#: The suite rounds share one code baseline (round-2 configuration plus options that are off
#: by default), so their arms can be compared on the (application, seed) pairs they share.
SUITE_ROUNDS = ("v2-suite-dev2", "v2-suite-dev3", "v2-suite-dev4", "v2-suite-dev5")
BASELINE_ARM = "hybrid_nolearn"
BOOTSTRAP_RESAMPLES = 5000

#: What each development round compared and what was decided (progress report §6).
ROUNDS: dict[str, dict[str, str]] = {
    "v2-dev": {"label": "Benchmark 1", "apps": "benchmark",
               "what": "First end-to-end run: bandit, no learner, random.",
               "decision": "Discarded (code edited while jobs started); replay bug found and fixed."},
    "v2-dev-llm": {"label": "LLM-label smoke", "apps": "benchmark",
                   "what": "One short run of the LLM-labels arm.",
                   "decision": "Infrastructure check only."},
    "v2-dev2": {"label": "Benchmark 2", "apps": "benchmark",
                "what": "Bandit vs no learner, with clean builds.",
                "decision": "Validation-consistency invariant narrowed; form and operator fixes."},
    "v2-dev3": {"label": "Benchmark 3", "apps": "benchmark",
                "what": "Free-exploration variants.",
                "decision": "Depth preference + weighted exploration adopted for every hybrid arm."},
    "v2-dev3-clean": {"label": "Benchmark 3 (clean)", "apps": "benchmark",
                      "what": "The two best variants on clean builds.",
                      "decision": "No false positives."},
    "v2-dev4": {"label": "Benchmark 4", "apps": "benchmark",
                "what": "Cells record which submit actions a page offers.",
                "decision": "Split states; replaced by 'whether' in round 5."},
    "v2-dev5": {"label": "Benchmark 5", "apps": "benchmark",
                "what": "Cells record whether a page offers a submit action.",
                "decision": "Kept."},
    "v2-dev6": {"label": "Benchmark 6", "apps": "benchmark",
                "what": "Whole-word matching of role cues.",
                "decision": "Regression on 4 apps; replaced."},
    "v2-dev7": {"label": "Benchmark 7", "apps": "benchmark",
                "what": "Prefix matching of role cues.",
                "decision": "Adopted."},
    "v2-suite-dev1": {"label": "Suite 1", "apps": "suite",
                      "what": "Bandit vs no learner on the 8 suite apps.",
                      "decision": "Site navigation never submits; submit actions taken in turn."},
    "v2-suite-dev2": {"label": "Suite 2", "apps": "suite + benchmark",
                      "what": "The same two arms after the fix, 14 apps.",
                      "decision": "Fix kept; learner not ahead."},
    "v2-suite-dev3": {"label": "Suite 3", "apps": "suite + benchmark",
                      "what": "Two corrected reward signals for the bandit.",
                      "decision": "No difference: the reward is not the bottleneck."},
    "v2-suite-dev4": {"label": "Suite 4", "apps": "suite + benchmark",
                      "what": "Pairwise operator + technique-first choice.",
                      "decision": "Worse; not adopted."},
    "v2-suite-dev5": {"label": "Suite 5", "apps": "suite + benchmark",
                      "what": "Curiosity Q-learning (in the hybrid, and standalone) and random.",
                      "decision": "In progress."},
}

#: What each arm is (progress report §5).
ARMS: dict[str, str] = {
    "random": "Uniform choice among the page's valid actions; the floor.",
    "search_S": "v1's fixed feature-search score; no learning.",
    "search_A0": "Fixed hand-weighted action priority; no learning.",
    "bandit_scratch": "v1 action-conditioned network, γ = 0, from scratch on the target.",
    "bandit_pretrained": "The same, pretrained on the other apps; residual head adapts.",
    "dqn_scratch": "v1 DQN from scratch, with the LLM reward.",
    "dqn_pretrained": "v1 DQN pretrained on the other apps, adapting; the v1 primary tester.",
    "dqn_zeroshot": "The pretrained DQN frozen: no adaptation.",
    "dqn_pretrained_nollm": "As dqn_pretrained, without the LLM reward.",
    "dqn_scratch_nollm": "As dqn_scratch, without the LLM reward.",
    "v1_dqn": "v1 DQN from scratch without the LLM reward, in the hybrid runner.",
    "hybrid_nolearn": "Hybrid pipeline; untried operators chosen uniformly (no learner).",
    "hybrid_bandit": "Hybrid pipeline with the Thompson-sampling operator bandit.",
    "hybrid_bandit_prior": "As hybrid_bandit, starting from the development apps' evidence.",
    "hybrid_bandit_llm": "As hybrid_bandit, with the LLM's page labels (exploratory).",
    "dev_bandit_search": "Free exploration by v1's search score; no depth preference.",
    "dev_bandit_deep": "Weighted exploration + preference for deeper states (adopted).",
    "dev_bandit_search_deep": "v1 search score + depth preference.",
    "dev_bandit_novelty": "Bandit rewarded only for genuinely new pages.",
    "dev_bandit_surprise": "Bandit rewarded for unexpected responses.",
    "dev_pairwise_nolearn": "Pairwise operator + technique-first choice; no learner.",
    "dev_pairwise_bandit": "Pairwise operator + technique-first choice, with the bandit.",
    "dev_hybrid_curiosity": "Hybrid with curiosity Q-learning choosing the free exploration.",
    "dev_curiosity_rl": "Standalone curiosity Q-learning tester (WebExplor/WebRLED components).",
}

_cache: dict = {"signature": None, "data": None}


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or partial file is reported as absent
        return None


def _signature() -> tuple:
    out = []
    for path in sorted(RUNS.glob("v2-*/results")) if RUNS.is_dir() else []:
        files = list(path.glob("*.json"))
        out.append((path.parent.name, len(files), max((f.stat().st_mtime for f in files), default=0)))
    for name in ("transfer_final.json", "contextual_diagnostic.json"):
        path = REPORTS / name
        out.append((name, path.stat().st_mtime if path.is_file() else 0))
    return tuple(out)


def load() -> dict:
    """Everything the evaluation page shows; recomputed only when a result file changes."""
    signature = _signature()
    if _cache["signature"] != signature:
        _cache["data"] = {
            "v1": _v1(),
            "diagnostic": _diagnostic(),
            "labeller": _labeller(),
            "rounds": _rounds(),
            "comparison": _comparison(),
        }
        _cache["signature"] = signature
    return _cache["data"]


# -- frozen experiments ----------------------------------------------------------------------


def _v1() -> dict | None:
    data = _read(REPORTS / "transfer_final.json")
    if data is None:
        return None
    arms = [
        {"arm": arm, "about": ARMS.get(arm, ""), **{k: s.get(k) for k in (
            "runs", "verified_runs", "discovery_rate", "restricted_mean_time",
            "median_time_uncensored")}}
        for arm, s in data.get("arm_survival", {}).items()
    ]
    arms.sort(key=lambda a: (a["restricted_mean_time"] or BUDGET))
    comparisons = [
        {
            "comparison": f"{c['treatment']} − {c['control']}",
            "why": c.get("why", ""),
            "median": c.get("median_difference"),
            "ci": c.get("bootstrap_95ci_median"),
            "favouring": f"{c.get('folds_favouring_treatment')}/{c.get('n_folds')}",
            "p": (c.get("stratified_logrank") or {}).get("p_value"),
        }
        for c in data.get("primary_and_secondary_all_folds", [])
    ]
    return {"budget": data.get("budget"), "folds": data.get("folds", []), "arms": arms,
            "comparisons": comparisons, "runs": data.get("runs")}


def _diagnostic() -> list[dict]:
    data = _read(REPORTS / "contextual_diagnostic.json")
    names = {"H-ctx": "genuine state–action contextual learning",
             "H-act": "action memorisation", "H-lex": "lexical memorisation",
             "H-app": "application memorisation"}
    if data is None:
        return []
    return [{"hypothesis": key, "about": names.get(key, ""), "verdict": value.get("verdict")}
            for key, value in data.get("hypotheses", {}).items()]


def _labeller() -> list[dict]:
    out = []
    stage0 = _read(REPORTS / "v2_stage0.json")
    if stage0:
        summary = stage0.get("summary", {}).get("meaningful_vocabularies", {})
        out.append({"test": "Stage 0 (generated variants)",
                    "page": summary.get("page_kind_accuracy"), "role": summary.get("role_accuracy"),
                    "passed": stage0.get("summary", {}).get("gate_passed")})
    fresh = _read(REPORTS / "v2_label_validation.json")
    if fresh:
        out.append({"test": "Revised labeller, fresh pages",
                    "page": fresh.get("page_kind_accuracy"), "role": fresh.get("role_accuracy"),
                    "passed": False})
    return out


# -- development rounds ----------------------------------------------------------------------


#: One summary row per result file, kept while the file is unchanged: a result file is
#: large (archive, learner state) and a round has hundreds, so each is parsed once.
_rows: dict[str, tuple[float, dict | None]] = {}


def _results(round_dir: Path) -> list[dict]:
    out = []
    for path in sorted((round_dir / "results").glob("*.json")):
        mtime = path.stat().st_mtime
        cached = _rows.get(str(path))
        if cached is None or cached[0] != mtime:
            cached = (mtime, _row(path))
            _rows[str(path)] = cached
        if cached[1] is not None:
            out.append(cached[1])
    return out


def _row(path: Path) -> dict | None:
    data = _read(path)
    if data is None:
        return None
    job = data.get("job") or {}
    hunt = data.get("hunt")
    return {
        "arm": job.get("arm", "?"), "app": job.get("app", "?"), "seed": job.get("seed"),
        "build": job.get("build", "buggy"), "ok": hunt is not None,
        "found": bool(hunt) and not hunt.get("censored", True),
        "time": min(hunt.get("time_to_first_verified", BUDGET), BUDGET) if hunt else None,
        "verified": ((hunt or {}).get("oracle") or {}).get("verified", 0),
    }


def _arm_row(arm: str, rows: list[dict]) -> dict:
    buggy = [r for r in rows if r["build"] == "buggy" and r["ok"]]
    clean = [r for r in rows if r["build"] != "buggy" and r["ok"]]
    times = [r["time"] for r in buggy]
    return {
        "arm": arm, "about": ARMS.get(arm, ""),
        "runs": len(buggy), "found": sum(r["found"] for r in buggy),
        "faults": sum(r["verified"] for r in buggy),
        "median": statistics.median(times) if times else None,
        "mean": round(statistics.mean(times)) if times else None,
        "clean_runs": len(clean), "false_positives": sum(r["found"] for r in clean),
        "failed": sum(not r["ok"] for r in rows),
        "apps": len({r["app"] for r in buggy}),
    }


def _rounds() -> list[dict]:
    if not RUNS.is_dir():
        return []
    out = []
    for round_dir in sorted(RUNS.glob("v2-*"), key=lambda p: p.stat().st_mtime):
        if not (round_dir / "results").is_dir():
            continue
        rows = _results(round_dir)
        if not rows:
            continue
        meta = ROUNDS.get(round_dir.name, {})
        arms = sorted({r["arm"] for r in rows})
        out.append({
            "name": round_dir.name, "label": meta.get("label", round_dir.name),
            "apps_set": meta.get("apps", ""), "what": meta.get("what", ""),
            "decision": meta.get("decision", ""),
            "apps": len({r["app"] for r in rows}),
            "seeds": sorted({r["seed"] for r in rows if r["seed"] is not None}),
            "arms": [_arm_row(arm, [r for r in rows if r["arm"] == arm]) for arm in arms],
        })
    return out


def _comparison() -> dict:
    """Each suite-round arm against the no-learner arm, on the (app, seed) pairs both have.

    Pairs, not one common set: a round still running would otherwise shrink every arm's
    comparison to the few runs all arms have finished.
    """
    by_arm: dict[str, dict[tuple[str, int], dict]] = {}
    for name in SUITE_ROUNDS:
        if not (RUNS / name / "results").is_dir():
            continue
        for row in _results(RUNS / name):
            if row["build"] == "buggy" and row["ok"]:
                by_arm.setdefault(row["arm"], {})[(row["app"], row["seed"])] = row
    base_rows = by_arm.get(BASELINE_ARM)
    if not base_rows:
        return {"arms": [], "baseline": BASELINE_ARM}
    rng = np.random.default_rng(0)
    arms = []
    for arm, rows in by_arm.items():
        shared = sorted(set(rows) & set(base_rows))
        if not shared:
            continue
        apps = sorted({app for app, _ in shared})
        diffs = [
            statistics.mean(rows[k]["time"] for k in shared if k[0] == app)
            - statistics.mean(base_rows[k]["time"] for k in shared if k[0] == app)
            for app in apps
        ]
        values = np.asarray(diffs, dtype=float)
        boots = values[rng.integers(0, len(values), (BOOTSTRAP_RESAMPLES, len(values)))].mean(1)
        lo, hi = (float(q) for q in np.percentile(boots, [2.5, 97.5]))
        times = [rows[k]["time"] for k in shared]
        arms.append({
            "arm": arm, "about": ARMS.get(arm, ""), "baseline": arm == BASELINE_ARM,
            "pairs": len(shared), "apps": len(apps),
            "found": sum(rows[k]["found"] for k in shared),
            "base_found": sum(base_rows[k]["found"] for k in shared),
            "faults": sum(rows[k]["verified"] for k in shared),
            "median": statistics.median(times), "mean": round(statistics.mean(times)),
            "diff": round(statistics.mean(diffs)), "ci": [round(lo), round(hi)],
            "faster": sum(d < 0 for d in diffs), "slower": sum(d > 0 for d in diffs),
            "significant": arm != BASELINE_ARM and (hi < 0 or lo > 0),
        })
    arms.sort(key=lambda a: (not a["baseline"], a["diff"]))
    # One axis for every interval bar, symmetric about zero, in round hundreds.
    widest = max((abs(v) for a in arms for v in a["ci"]), default=100)
    scale = max(100, int(-(-widest // 100) * 100))
    for arm in arms:
        lo, hi = arm["ci"]
        arm["bar"] = {"left": 50 + 50 * lo / scale, "width": 50 * (hi - lo) / scale,
                      "point": 50 + 50 * arm["diff"] / scale}
    return {"arms": arms, "baseline": BASELINE_ARM, "scale": scale}
