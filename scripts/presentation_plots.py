"""Plots for the final-review presentation (`presentation/plots/*.png`), drawn from the result
files only, in the presentation template's colours (cream background, navy ink).

    python scripts/presentation_plots.py

Held-out evaluation: time to first verified fault (Kaplan-Meier), distinct faults verified,
faults by declared depth. Tuning round: distinct faults and browser errors per run.
External benchmark: round-1 averages, the Phoenix clickables follow-up, and round 2 per
application with the bandit follow-up. The deep-RL (v1) arm is deliberately left out: the
presentation covers the structured tester and its learners only.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "presentation" / "plots"

CREAM, NAVY, INK_SOFT, GRID = "#F4EDE2", "#303870", "#5A5F7D", "#DED5C6"
#: One colour per approach, the same on every plot.
COLOR = {
    "random": "#A39E93",
    "structured": "#303870",
    "bandit": "#D9A441",
    "curiosity": "#C8553D",
    "tuned": "#3A8F85",
    "WebExplor": "#8C93B5",
    "WebRLED": "#1C2140",
}
NAME = {
    "random": "Random",
    "structured": "Structured explorer",
    "bandit": "Structured + bandit",
    "curiosity": "Structured + curiosity RL",
    "tuned": "Katalyze (tuned)",
    "WebExplor": "WebExplor (published)",
    "WebRLED": "WebRLED (published)",
}
#: Arm identifiers in the result files, mapped to the presentation's names.
HELDOUT = {"random": "random", "hybrid_nolearn": "structured", "hybrid_bandit": "bandit"}
TUNING = {"random": "random", "hybrid_nolearn": "structured", "hybrid_bandit": "bandit",
          "dev_v3_curiosity": "curiosity", "dev_v3_combined": "tuned"}
APPS = ["dimeshift", "pagekit", "splittypie", "phoenix", "retroboard", "petclinic"]
PUBLISHED = {"WebExplor": [54.22, 25.79, 36.16, 71.84, 60.23, 85.00],
             "WebRLED": [55.37, 39.76, 44.61, 82.89, 77.43, 85.00]}

plt.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["Calibri", "Segoe UI", "DejaVu Sans"],
    "font.size": 13, "figure.facecolor": CREAM, "axes.facecolor": CREAM, "savefig.facecolor": CREAM,
    "axes.edgecolor": INK_SOFT, "axes.labelcolor": NAVY, "xtick.color": INK_SOFT, "ytick.color": INK_SOFT,
    "text.color": NAVY, "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True, "legend.frameon": False,
    "savefig.dpi": 220, "axes.titleweight": "bold", "axes.titlesize": 15, "axes.titlecolor": NAVY,
})


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _bars(ax, labels, values, colors, fmt="{:g}", horizontal=False):
    if horizontal:
        bars = ax.barh(labels, values, color=colors, height=0.62)
        for b, v in zip(bars, values):
            ax.text(b.get_width() + max(values) * 0.015, b.get_y() + b.get_height() / 2, fmt.format(v),
                    va="center", fontsize=13, fontweight="bold", color=NAVY)
        ax.grid(axis="y", visible=False)
    else:
        bars = ax.bar(labels, values, color=colors, width=0.62)
        for b, v in zip(bars, values):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + max(values) * 0.015, fmt.format(v),
                    ha="center", va="bottom", fontsize=13, fontweight="bold", color=NAVY)
        ax.grid(axis="x", visible=False)


def heldout() -> None:
    times = defaultdict(list)
    for path in sorted((REPO / "runs/v2-eval/results").glob("hunt__*.json")):
        record = _load(path)
        job, hunt = record["job"], record["hunt"]
        if record.get("status") != "ok" or job.get("build") != "buggy" or job["arm"] not in HELDOUT:
            continue
        t = hunt["budget"] if hunt["censored"] else hunt["time_to_first_verified"]
        times[HELDOUT[job["arm"]]].append((t, not hunt["censored"]))
    # Kaplan-Meier: share of runs still without a verified fault
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    for key in ("random", "structured", "bandit"):
        runs = sorted(times[key])
        at_risk, surv, xs, ys = len(runs), 1.0, [0], [1.0]
        for t, event in runs:
            if event:
                surv *= (at_risk - 1) / at_risk
                xs += [t, t]
                ys += [ys[-1], surv]
            at_risk -= 1
        xs.append(2000)
        ys.append(ys[-1])
        ax.plot(xs, ys, color=COLOR[key], linewidth=2.6, label=NAME[key])
    ax.set_xlim(0, 2000)
    ax.set_ylim(0, 1.03)
    ax.set_xlabel("Browser interactions")
    ax.set_ylabel("Runs with no verified fault yet")
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(OUT / "heldout_survival.png")
    plt.close(fig)

    # distinct faults verified (from the held-out per-arm table)
    diff = _load(REPO / "reports/heldout_difficulty.json")
    distinct = {k: sum(diff["by_group"][g]["arms"][arm]["distinct_found"]
                       for g in ("difficulty:shallow (<=4)", "difficulty:moderate (5-6)", "difficulty:deep (>=7)"))
                for arm, k in HELDOUT.items()}
    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    keys = ["random", "structured", "bandit"]
    _bars(ax, [NAME[k] for k in keys], [distinct[k] for k in keys], [COLOR[k] for k in keys], horizontal=True)
    ax.set_xlim(0, 24)
    ax.set_xlabel("Distinct seeded faults verified (of 24)")
    ax.invert_yaxis()
    fig.tight_layout()
    fig.savefig(OUT / "heldout_distinct.png")
    plt.close(fig)

    # by declared depth
    groups = [("difficulty:shallow (<=4)", "Shallow\n(≤ 4 steps)"), ("difficulty:moderate (5-6)", "Moderate\n(5–6 steps)"),
              ("difficulty:deep (>=7)", "Deep\n(≥ 7 steps)")]
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    width = 0.26
    for i, (arm, key) in enumerate(HELDOUT.items()):
        xs = [g + (i - 1) * (width + 0.02) for g in range(len(groups))]
        vals = [diff["by_group"][g]["arms"][arm]["distinct_found"] for g, _ in groups]
        bars = ax.bar(xs, vals, width=width, color=COLOR[key], label=NAME[key])
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.15, str(v), ha="center", fontsize=12, fontweight="bold", color=NAVY)
    for g, (key, _) in enumerate(groups):
        total = diff["by_group"][key]["faults"]
        ax.hlines(total, g - 0.45, g + 0.45, colors=NAVY, linestyles=(0, (4, 3)), linewidth=1.2)
        ax.text(g + 0.47, total, f"{total} faults", va="center", fontsize=11, color=NAVY)
    ax.set_xticks(range(len(groups)), [label for _, label in groups])
    ax.set_ylabel("Distinct faults verified")
    ax.set_ylim(0, 11.5)
    ax.grid(axis="x", visible=False)
    ax.legend(ncol=3, fontsize=11, loc="upper center", bbox_to_anchor=(0.5, -0.2))
    fig.tight_layout()
    fig.savefig(OUT / "heldout_depth.png")
    plt.close(fig)


def tuning() -> None:
    data = {a["arm"]: a for a in _load(REPO / "reports/tuning_v3.json")["arms"]}
    keys = list(TUNING.values())
    arms = list(TUNING)
    fig, (left, right) = plt.subplots(1, 2, figsize=(11.6, 4.0))
    labels = [NAME[k].replace("Structured + ", "+ ").replace("Structured explorer", "Structured") for k in keys]
    _bars(left, labels, [data[a]["distinct_faults"] for a in arms], [COLOR[k] for k in keys])
    left.set_title("Distinct seeded faults verified (of 41)")
    left.set_ylim(0, 26)
    _bars(right, labels, [data[a]["unique_failures_per_run"] for a in arms], [COLOR[k] for k in keys], fmt="{:.2f}")
    right.set_title("Distinct browser errors triggered per run")
    right.set_ylim(0, 6.8)
    for ax in (left, right):
        ax.tick_params(axis="x", labelsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "tuning.png")
    plt.close(fig)


def external() -> None:
    ext = _load(REPO / "reports/external_comparison.json")
    av = ext["primary"]["averages"]
    keys = ["tuned", "curiosity", "random", "WebExplor", "WebRLED"]
    src = {"tuned": "hybrid_final", "curiosity": "hybrid_curiosity", "random": "random", "WebExplor": "WebExplor", "WebRLED": "WebRLED"}
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    _bars(ax, [NAME[k].replace(" (published)", "").replace("Structured + ", "+ ") for k in keys],
          [round(av[src[k]]["coverage"], 1) for k in keys], [COLOR[k] for k in keys], fmt="{:.1f}%")
    ax.set_ylabel("Average branch coverage, 6 apps")
    ax.set_ylim(0, 72)
    ax.tick_params(axis="x", labelsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "external_round1.png")
    plt.close(fig)

    # Phoenix: round 1 vs the clickables follow-up (same hardware, GitHub runners)
    f = ext["followup_clickables"]
    fig, ax = plt.subplots(figsize=(5.6, 4.0))
    keys = ["tuned", "curiosity", "random"]
    width = 0.36
    for i, (key, arm) in enumerate(zip(keys, ["hybrid_final", "hybrid_curiosity", "random"])):
        before = f["without"][f"phoenix|{arm}"]["coverage_mean"]
        after = f["with"][f"phoenix|{arm}"]["coverage_mean"]
        ax.bar(i - width / 2, before, width, color=GRID, edgecolor=COLOR[key], linewidth=1.5)
        ax.bar(i + width / 2, after, width, color=COLOR[key])
        ax.text(i - width / 2, before + 1.5, f"{before:.0f}%", ha="center", fontsize=12, color=NAVY)
        ax.text(i + width / 2, after + 1.5, f"{after:.0f}%", ha="center", fontsize=12, fontweight="bold", color=NAVY)
    ax.set_xticks(range(3), ["Katalyze\n(tuned)", "+ curiosity RL", "Random"])
    ax.set_ylabel("Phoenix branch coverage")
    ax.set_ylim(0, 100)
    ax.grid(axis="x", visible=False)
    ax.legend(handles=[matplotlib.patches.Patch(facecolor=GRID, edgecolor=NAVY, label="HTML controls only"),
                       matplotlib.patches.Patch(facecolor=NAVY, label="+ clickable elements")], ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.2), fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "phoenix_clickables.png")
    plt.close(fig)

    # round 2 per application, with the bandit follow-up
    r2, bandit = ext["round2"]["table"], ext.get("bandit_followup", {}).get("table", {})
    series = [("tuned", [r2[f"{a}|hybrid_final"]["coverage_mean"] for a in APPS]),
              ("curiosity", [r2[f"{a}|hybrid_curiosity"]["coverage_mean"] for a in APPS])]
    if bandit:
        series.append(("bandit", [bandit[f"{a}|hybrid_bandit_v5"]["coverage_mean"] for a in APPS]))
    series += [("WebExplor", PUBLISHED["WebExplor"]), ("WebRLED", PUBLISHED["WebRLED"])]
    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    width = 0.8 / len(series)
    for i, (key, vals) in enumerate(series):
        xs = [g + (i - (len(series) - 1) / 2) * width for g in range(len(APPS))]
        ax.bar(xs, vals, width=width * 0.92, color=COLOR[key], label=NAME[key] + (" *" if key == "bandit" else ""))
    ax.set_xticks(range(len(APPS)), [a.capitalize() for a in APPS])
    ax.set_ylabel("Branch coverage (%)")
    ax.set_ylim(0, 100)
    ax.grid(axis="x", visible=False)
    ax.legend(ncol=3, fontsize=10.5, loc="upper center", bbox_to_anchor=(0.5, -0.1))
    fig.tight_layout()
    fig.savefig(OUT / "external_round2.png")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    heldout()
    tuning()
    external()
    print("\n".join(str(p.relative_to(REPO)) for p in sorted(OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
