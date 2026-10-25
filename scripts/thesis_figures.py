"""Figures for the thesis (`thesis/images/`), drawn from the result files only.

    python scripts/thesis_figures.py              # plots
    python scripts/thesis_figures.py --screens    # also UI screenshots (web UI on :8000)

Plots: held-out time to first verified fault (Kaplan-Meier), held-out distinct faults by
declared difficulty, external branch coverage per application (round 1, round 2 and the
published numbers). Colours are the validated reference categorical palette, light mode.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "thesis" / "images"

BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
GREY, INK, MUTED, GRID = "#8a8985", "#0b0b0b", "#52514e", "#e4e3df"
HELDOUT_ARMS = {"random": GREY, "v1_dqn_pretrained": YELLOW,
                "hybrid_nolearn": BLUE, "hybrid_bandit": ORANGE}

plt.rcParams.update({
    "font.family": "serif", "font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.axisbelow": True, "legend.frameon": False, "savefig.dpi": 200,
})


def kaplan_meier() -> None:
    times = defaultdict(list)
    for path in sorted((REPO / "runs/v2-eval/results").glob("hunt__*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        job, hunt = record["job"], record["hunt"]
        if record.get("status") != "ok" or job.get("build") != "buggy":
            continue
        t = hunt["time_to_first_verified"]
        times[job["arm"]].append((hunt["budget"] if hunt["censored"] else t, not hunt["censored"]))
    fig, ax = plt.subplots(figsize=(6.2, 3.4))
    for arm, colour in HELDOUT_ARMS.items():
        runs = sorted(times[arm])
        at_risk, surv, xs, ys = len(runs), 1.0, [0], [1.0]
        for t, event in runs:
            if event:
                surv *= (at_risk - 1) / at_risk
                xs += [t, t]
                ys += [ys[-1], surv]
            at_risk -= 1
        xs.append(2000)
        ys.append(ys[-1])
        ax.plot(xs, ys, color=colour, linewidth=2, label=f"{arm} ({len(runs)} runs)")
    ax.set_xlim(0, 2000)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("Interactions")
    ax.set_ylabel("Share of runs with no\nverified fault yet")
    ax.legend(loc="upper right", fontsize=8.5)
    fig.tight_layout()
    fig.savefig(OUT / "heldout_km.pdf")
    plt.close(fig)


def difficulty() -> None:
    data = json.loads((REPO / "reports/heldout_difficulty.json").read_text(encoding="utf-8"))
    groups = [("difficulty:shallow (<=4)", "Shallow (≤4)"),
              ("difficulty:moderate (5-6)", "Moderate (5–6)"),
              ("difficulty:deep (>=7)", "Deep (≥7)")]
    fig, ax = plt.subplots(figsize=(6.2, 3.0))
    width = 0.19
    for i, (arm, colour) in enumerate(HELDOUT_ARMS.items()):
        xs = [g + (i - 1.5) * (width + 0.01) for g in range(len(groups))]
        found = [data["by_group"][key]["arms"][arm]["distinct_found"] for key, _ in groups]
        ax.bar(xs, found, width=width, color=colour, label=arm)
        for x, v in zip(xs, found):
            ax.text(x, v + 0.12, str(v), ha="center", va="bottom", fontsize=7.5, color=MUTED)
    totals = [data["by_group"][key]["faults"] for key, _ in groups]
    for g, total in enumerate(totals):
        ax.hlines(total, g - 0.42, g + 0.42, colors=INK, linestyles=(0, (3, 2)), linewidth=1)
        ax.text(g + 0.44, total, f"{total} faults", va="center", fontsize=7.5, color=INK)
    ax.set_xticks(range(len(groups)), [label for _, label in groups])
    ax.set_xlabel("Declared shortest trigger path (interactions)")
    ax.set_ylabel("Distinct faults verified")
    ax.set_ylim(0, 11.5)
    ax.grid(axis="x", visible=False)
    ax.legend(ncol=4, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, 1.16))
    fig.tight_layout()
    fig.savefig(OUT / "heldout_difficulty.pdf")
    plt.close(fig)


def external() -> None:
    data = json.loads((REPO / "reports/external_comparison.json").read_text(encoding="utf-8"))
    r1, r2 = data["primary"]["table"], data["round2"]["table"]
    published = {"WebExplor": {"dimeshift": 54.22, "pagekit": 25.79, "splittypie": 36.16,
                               "phoenix": 71.84, "retroboard": 60.23, "petclinic": 85.00},
                 "WebRLED": {"dimeshift": 55.37, "pagekit": 39.76, "splittypie": 44.61,
                             "phoenix": 82.89, "retroboard": 77.43, "petclinic": 85.00}}
    apps = ["dimeshift", "pagekit", "splittypie", "phoenix", "retroboard", "petclinic"]
    series = [
        ("hybrid_final, round 1", lambda a: r1[f"{a}|hybrid_final"]["coverage_mean"], "o", GREY, "none"),
        ("hybrid_final_v5, round 2", lambda a: r2[f"{a}|hybrid_final"]["coverage_mean"], "o", BLUE, BLUE),
        ("hybrid_curiosity_v5, round 2", lambda a: r2[f"{a}|hybrid_curiosity"]["coverage_mean"], "D", ORANGE, ORANGE),
        ("WebExplor (published)", lambda a: published["WebExplor"][a], "^", INK, "none"),
        ("WebRLED (published)", lambda a: published["WebRLED"][a], "s", INK, INK),
    ]
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    ys = list(range(len(apps)))[::-1]
    for y in ys:
        ax.axhline(y, color=GRID, linewidth=0.6, zorder=0)
    for k, (label, value, marker, colour, face) in enumerate(series):
        offset = (k - 2) * 0.11
        ax.scatter([value(a) for a in apps], [y + offset for y in ys], marker=marker, s=38,
                   edgecolors=colour, facecolors=face, linewidths=1.4, label=label, zorder=3)
    ax.set_yticks(ys, [a.capitalize() for a in apps])
    ax.set_xlabel("Client-side branch coverage (%)")
    ax.set_xlim(15, 95)
    ax.grid(axis="y", visible=False)
    ax.legend(fontsize=7.8, loc="upper center", bbox_to_anchor=(0.45, -0.17), ncol=3)
    fig.tight_layout()
    fig.savefig(OUT / "external_coverage.pdf")
    plt.close(fig)


def screens() -> None:
    from playwright.sync_api import sync_playwright

    pages = {"ui_new_test.png": "/new", "ui_run.png": "/runs/039-b7956599",
             "ui_report.png": "/runs/039-b7956599/report", "ui_evaluation.png": "/evaluation",
             "ui_dashboard.png": "/"}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=1.5)
        for name, path in pages.items():
            page.goto("http://127.0.0.1:8000" + path, wait_until="networkidle")
            page.wait_for_timeout(800)
            page.screenshot(path=str(OUT / name))
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--screens", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    kaplan_meier()
    difficulty()
    external()
    if args.screens:
        screens()


if __name__ == "__main__":
    main()
