"""Graphs of the external benchmark: Katalyze against WebExplor and WebRLED.

    python scripts/plot_benchmark.py [--out Graphs]

Reads the per-run result files under runs/ (our agents and WebRLED as run on the same GitHub
runners, `scripts/webrled/score_webrled.py`) and the papers' published numbers
(`analyze_external.PUBLISHED`), and writes four PNG figures plus `benchmark_data.csv`, the
table every figure is drawn from.

Colour follows the tool in every figure: Katalyze blue, WebRLED orange, WebExplor aqua.
Published numbers (measured by the papers on their own machines) are hollow markers or pale
bars; numbers measured here are solid.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from analyze_external import PUBLISHED  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APPS = ["dimeshift", "pagekit", "splittypie", "phoenix", "retroboard", "petclinic"]
TITLES = {"dimeshift": "Dimeshift", "pagekit": "Pagekit", "splittypie": "Splittypie",
          "phoenix": "Phoenix", "retroboard": "Retroboard", "petclinic": "Petclinic"}
#: The agent with the best six-app average (one agent across all apps, not the best per app).
BEST = "hybrid_final_v5"
AGENT_NAMES = {
    "hybrid_final_v5": "Katalyze tuned (hybrid_final)",
    "hybrid_curiosity_v5_pub": "A: curiosity, published settings",
    "hybrid_goexplore_v5": "C: learned returns (Go-Explore)",
    "hybrid_curiosity_v5": "Curiosity Q-learning",
    "hybrid_final_v7": "Katalyze tuned + robust returns (v7)",
    "hybrid_combined_v7": "Combined agent (v7)",
    "hybrid_curiosity_v7": "Curiosity + robust returns (v7)",
    "hybrid_bandit_v5": "Bandit",
    "hybrid_deep_v5": "B: deep DQN",
}
#: Round 1 (AWS, 3 runs per app) is summarised in reports/external_comparison.md; its result
#: files predate the per-run layout used here, so its average is taken from that report.
ROUND1_BEST = ("Round 1 (3 runs, AWS)", 44.69)

INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
BLUE, ORANGE, AQUA, RED = "#2a78d6", "#eb6834", "#1baf7a", "#e34948"


def _runs(pattern: str) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {app: [] for app in APPS}
    for path in glob.glob(str(ROOT / pattern)):
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        if record.get("status") != "ok":
            continue
        app = record["job"]["app"]
        if app in out:
            out[app].append(100 * record["istanbul"]["branch_coverage"])
    return out


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else math.nan


def _sd(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def _average(per_app: dict[str, list[float]]) -> float:
    return statistics.mean(_mean(per_app[a]) for a in APPS)


def _welch_ci(a: list[float], b: list[float]) -> tuple[float, float]:
    """Difference of means and the half-width of its 95% interval (Welch)."""
    diff = _mean(a) - _mean(b)
    va, vb = _sd(a) ** 2 / len(a), _sd(b) ** 2 / len(b)
    se = math.sqrt(va + vb)
    if se == 0:
        return diff, 0.0
    dof = (va + vb) ** 2 / ((va ** 2 / (len(a) - 1)) + (vb ** 2 / (len(b) - 1)))
    from scipy import stats

    return diff, float(stats.t.ppf(0.975, dof) * se)


def _style(ax, *, xgrid: bool = False) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
        ax.spines[side].set_linewidth(1)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.grid(axis="x" if xgrid else "y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)


def _figure(width: float, height: float):
    fig, ax = plt.subplots(figsize=(width, height), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    return fig, ax


def _title(fig, title: str, subtitle: str) -> None:
    fig.text(0.012, 0.975, title, ha="left", va="top", fontsize=13, color=INK, weight="bold")
    fig.text(0.012, 0.915, subtitle, ha="left", va="top", fontsize=9, color=INK2)


def per_app(ours, webrled, out: Path) -> None:
    fig, ax = _figure(11, 5.4)
    _style(ax)
    width = 0.2
    for i, app in enumerate(APPS):
        for offset, runs, color in ((-width * 1.1, ours[app], BLUE), (0.0, webrled[app], ORANGE)):
            x = i + offset
            mean = _mean(runs)
            ax.bar(x, mean, width=width, color=color, zorder=2)
            ax.scatter([x] * len(runs), runs, s=14, color=SURFACE, edgecolors=INK2,
                       linewidths=0.8, zorder=3)
            # Inside the bar, at its base: the top is taken by the run dots and diamonds.
            ax.text(x, 2.5, f"{mean:.1f}", ha="center", va="bottom", fontsize=8.5,
                    color="#ffffff", weight="bold", zorder=5)
        ax.scatter(i, PUBLISHED["WebRLED"][app][0], marker="D", s=46, facecolors=SURFACE,
                   edgecolors=ORANGE, linewidths=2, zorder=4)
        ax.scatter(i + width * 1.1, PUBLISHED["WebExplor"][app][0], marker="D", s=46,
                   facecolors=SURFACE, edgecolors=AQUA, linewidths=2, zorder=4)
    ax.set_xticks(range(len(APPS)), [TITLES[a] for a in APPS], fontsize=10, color=INK)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Branch coverage after 1 hour (%)", color=INK2, fontsize=9)
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=BLUE, label=f"{AGENT_NAMES[BEST]}, mean of 5 runs"),
        plt.Rectangle((0, 0), 1, 1, color=ORANGE, label="WebRLED run by us, mean of 5 runs"),
        plt.Line2D([], [], marker="o", ls="", markerfacecolor=SURFACE, markeredgecolor=INK2,
                   label="one run"),
        plt.Line2D([], [], marker="D", ls="", markerfacecolor=SURFACE, markeredgecolor=ORANGE,
                   markeredgewidth=2, label="WebRLED, published"),
        plt.Line2D([], [], marker="D", ls="", markerfacecolor=SURFACE, markeredgecolor=AQUA,
                   markeredgewidth=2, label="WebExplor, published"),
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=8, frameon=False, ncol=2,
              labelcolor=INK2)
    _title(fig, "Branch coverage on WebExplor's six applications",
           "Katalyze and WebRLED both run on identical GitHub Actions machines, 1 hour per run, "
           "scored by the same coverage counter. Diamonds are the papers' own numbers.")
    fig.subplots_adjust(left=0.06, right=0.99, top=0.84, bottom=0.08)
    fig.savefig(out / "1 - Coverage per app.png", facecolor=SURFACE)
    plt.close(fig)


def averages(ours, webrled, round2, out: Path) -> None:
    rows = [
        (ROUND1_BEST[0] + ", best agent: curiosity", ROUND1_BEST[1], BLUE, 0.35),
        ("Round 2, 24 Oct (5 runs), best agent: curiosity", _average(round2), BLUE, 0.6),
        (f"Now, 27 Oct (5 runs), best agent: hybrid_final", _average(ours), BLUE, 1.0),
        ("WebExplor, published", statistics.mean(v[0] for v in PUBLISHED["WebExplor"].values()),
         AQUA, 0.45),
        ("WebRLED, published", statistics.mean(v[0] for v in PUBLISHED["WebRLED"].values()),
         ORANGE, 0.45),
        ("WebRLED, run by us (5 runs)", _average(webrled), ORANGE, 1.0),
    ]
    fig, ax = _figure(10, 4.4)
    _style(ax, xgrid=True)
    y = list(range(len(rows)))[::-1]
    for yi, (label, value, color, alpha) in zip(y, rows):
        ax.barh(yi, value, height=0.5, color=color, alpha=alpha, zorder=2)
        ax.text(value + 0.6, yi, f"{value:.1f}%", va="center", fontsize=9, color=INK)
    ax.set_yticks(y, [r[0] for r in rows], fontsize=9, color=INK)
    ax.set_xlim(0, 75)
    ax.set_xlabel("Branch coverage, average of the six applications (%)", color=INK2, fontsize=9)
    _title(fig, "Six-application average: where Katalyze started and where it is now",
           "Pale bars are numbers measured elsewhere (round 1 on AWS; the papers on their own "
           "machines). Solid bars were measured on identical GitHub machines.")
    fig.subplots_adjust(left=0.36, right=0.97, top=0.8, bottom=0.13)
    fig.savefig(out / "2 - Six-app average over time.png", facecolor=SURFACE)
    plt.close(fig)


def gaps(ours, webrled, out: Path) -> None:
    fig, ax = _figure(9, 4.2)
    _style(ax, xgrid=True)
    y = list(range(len(APPS)))[::-1]
    for yi, app in zip(y, APPS):
        diff, half = _welch_ci(ours[app], webrled[app])
        color = BLUE if diff >= 0 else RED
        ax.barh(yi, diff, height=0.45, color=color, zorder=2)
        ax.errorbar(diff, yi, xerr=half, color=INK2, capsize=3, linewidth=1, zorder=3)
        # On the other side of zero from the bar, where only the interval line runs.
        ax.text(-0.3 if diff >= 0 else 0.3, yi + 0.3, f"{diff:+.1f}" if diff else "0.0",
                va="center", ha="right" if diff >= 0 else "left", fontsize=9, color=INK)
    ax.axvline(0, color=AXIS, linewidth=1)
    ax.set_yticks(y, [TITLES[a] for a in APPS], fontsize=10, color=INK)
    ax.set_xlim(-14, 14)
    ax.set_xlabel("hybrid_final minus WebRLED, percentage points of branch coverage "
                  "(right of 0: Katalyze ahead)", color=INK2, fontsize=9)
    _title(fig, "Gap to WebRLED on each application",
           "Both run on identical machines, 5 runs each. Lines show the 95% interval of the "
           "difference: an interval crossing 0 is not a clear win or loss.")
    fig.subplots_adjust(left=0.13, right=0.97, top=0.8, bottom=0.14)
    fig.savefig(out / "3 - Gap to WebRLED per app.png", facecolor=SURFACE)
    plt.close(fig)


def agents(all_agents, webrled, out: Path) -> None:
    ranked = sorted(all_agents.items(), key=lambda kv: _average(kv[1]))
    fig, ax = _figure(10, 5.0)
    _style(ax, xgrid=True)
    for yi, (arm, per_app_runs) in enumerate(ranked):
        value = _average(per_app_runs)
        ax.barh(yi, value, height=0.55, color=BLUE, alpha=1.0 if arm == BEST else 0.45, zorder=2)
        # Inside the bar end: right of it run the reference lines.
        ax.text(value - 0.15, yi, f"{value:.1f}", va="center", ha="right", fontsize=8.5,
                color="#ffffff" if arm == BEST else INK, weight="bold", zorder=5)
    refs = [(_average(webrled), ORANGE, 2.0, 1.0, "right", "WebRLED run by us"),
            (statistics.mean(v[0] for v in PUBLISHED["WebRLED"].values()), ORANGE, 1.2, 0.6,
             "left", "WebRLED published"),
            (statistics.mean(v[0] for v in PUBLISHED["WebExplor"].values()), AQUA, 1.2, 0.8,
             "left", "WebExplor published")]
    for value, color, width, alpha, side, label in refs:
        ax.axvline(value, color=color, linewidth=width, alpha=alpha, zorder=4)
        nudge = 0.12 if side == "left" else -0.12
        ax.text(value + nudge, len(ranked) - 0.1, f"{label}\n{value:.1f}", ha=side, va="bottom",
                fontsize=8, color=INK2)
    ax.set_yticks(range(len(ranked)), [AGENT_NAMES[a] for a, _ in ranked], fontsize=9, color=INK)
    ax.set_xlim(50, 68)
    ax.set_ylim(-0.6, len(ranked) + 0.9)
    ax.set_xlabel("Branch coverage, average of the six applications (%), axis starts at 50",
                  color=INK2, fontsize=9)
    _title(fig, "Every Katalyze agent against the baselines",
           "All agents use the fixed action layer (27 Oct onwards); 5 one-hour runs per app on "
           "GitHub machines.")
    fig.subplots_adjust(left=0.3, right=0.97, top=0.82, bottom=0.11)
    fig.savefig(out / "4 - All agents vs baselines.png", facecolor=SURFACE)
    plt.close(fig)


def table(ours_by_arm, webrled, round2, out: Path) -> None:
    with open(out / "benchmark_data.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "app", "mean %", "sd", "runs", "values"])
        for name, data in [("WebRLED run by us", webrled), ("round 2 hybrid_curiosity", round2),
                           *[(arm, runs) for arm, runs in ours_by_arm.items()]]:
            for app in APPS:
                v = data[app]
                w.writerow([name, app, round(_mean(v), 2), round(_sd(v), 2), len(v),
                            " ".join(f"{x:.1f}" for x in sorted(v))])
            w.writerow([name, "AVERAGE", round(_average(data), 2), "", "", ""])
        for paper, values in PUBLISHED.items():
            for app in APPS:
                w.writerow([f"{paper} published", app, values[app][0], "", "", ""])
            w.writerow([f"{paper} published", "AVERAGE",
                        round(statistics.mean(v[0] for v in values.values()), 2), "", "", ""])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "Graphs")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    plt.rcParams["font.family"] = ["Segoe UI", "DejaVu Sans", "sans-serif"]

    sources = "runs/gha-fix2/results runs/gha-agents/results runs/gha-v7/results".split()
    all_agents: dict[str, dict[str, list[float]]] = {}
    for arm in AGENT_NAMES:
        per = {app: [] for app in APPS}
        for src in sources:
            for app, runs in _runs(f"{src}/*__{arm}__*.json").items():
                per[app] += runs
        all_agents[arm] = per
    webrled = _runs("runs/gha-webrled/results/*.json")
    round2 = _runs("runs/ext-v5-gha/results/*__hybrid_curiosity_v5__*.json")
    ours = all_agents[BEST]
    for name, data in [("ours", ours), ("webrled", webrled), ("round2", round2)]:
        short = [a for a in APPS if len(data[a]) != 5]
        if short:
            raise SystemExit(f"{name}: not 5 runs for {short}")

    per_app(ours, webrled, args.out)
    averages(ours, webrled, round2, args.out)
    gaps(ours, webrled, args.out)
    agents(all_agents, webrled, args.out)
    table(all_agents, webrled, round2, args.out)
    print(f"Katalyze {_average(ours):.1f}  WebRLED run by us {_average(webrled):.1f}  -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
