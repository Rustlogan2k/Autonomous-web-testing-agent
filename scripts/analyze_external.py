"""Analysis of the external comparison (PROJECT_CONTEXT §0.8, tag v3-freeze).

Collects the counted runs, following the pre-registered rules:
- Dimeshift, Pagekit and Splittypie come from the laptop (`runs/ext-eval`).
- Phoenix, Retroboard and Petclinic come from AWS (`runs/ext-eval-aws*`). AWS completed all
  nine runs of each, so the GitHub Actions runs (`runs/ext-eval-gha`) are a separate
  replication.
- Phoenix runs made before the sign-in fix (commit 96d30ef) are discarded on every platform.

It then reports, per application and arm, branch coverage (Istanbul) and unique failures
(mean, min, max over the runs), the six-application averages beside the published WebExplor
and WebRLED numbers (WebRLED, arXiv 2504.19237, Table 3), the per-application differences
between our arms (median over applications, bootstrap interval; descriptive, n = 6), and the
same for the replication.

    python scripts/analyze_external.py --out reports/external_comparison
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APPS = ("dimeshift", "pagekit", "splittypie", "phoenix", "retroboard", "petclinic")
ARMS = ("hybrid_final", "hybrid_curiosity", "random")
#: Commits the Phoenix runs were launched from before its sign-in fix (96d30ef): AWS
#: `ext-eval-aws1` (cce3c20) and `aws2..4` (59c0c45), the first GitHub workflow (c2e6f3b).
#: Phoenix runs made with them are discarded.
PHOENIX_BEFORE_FIX = frozenset({"cce3c20", "59c0c45", "c2e6f3b"})
#: WebRLED, arXiv 2504.19237, Table 3: (branch coverage %, failures), 1-hour runs, 5 repetitions.
PUBLISHED = {
    "WebExplor": {"dimeshift": (54.22, 3.0), "pagekit": (25.79, 6.6), "splittypie": (36.16, 4.0),
                  "phoenix": (71.84, 0.0), "retroboard": (60.23, 1.0), "petclinic": (85.00, 3.8)},
    "WebRLED": {"dimeshift": (55.37, 5.4), "pagekit": (39.76, 7.4), "splittypie": (44.61, 5.0),
                "phoenix": (82.89, 2.0), "retroboard": (77.43, 1.0), "petclinic": (85.00, 3.8)},
}


_URL = re.compile(r"https?://([^/'\"\s)]+)")


def console_failures(failures: list[dict]) -> int:
    """POST-HOC measure, closer to the papers' manual count: distinct console error messages.

    The pre-registered count also includes failed requests and counts a message once per
    page; the papers count console errors and merge those that "differ only in parameters".
    Mechanically: console errors only; a message naming another host than the application's
    own (shown as "http://...") is dropped as third-party; ids, numbers, hex strings, quoted
    values and upper-case codes are replaced by placeholders; distinct results are counted.
    """
    distinct = set()
    for f in failures:
        if f.get("kind") != "console_error":
            continue
        message = f.get("detail", "")
        if any(host != "..." for host in _URL.findall(message)):
            continue
        message = re.sub(r"'[^']*'|\"[^\"]*\"", "<v>", message)
        message = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", message)
        message = re.sub(r"\b[A-Z]{2,}\b", "<C>", message)
        message = re.sub(r"\d+", "<n>", message)
        distinct.add(message.strip()[:120])
    return len(distinct)


def load(paths: list[Path]) -> list[dict]:
    runs = []
    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("status") != "ok":
            continue
        job = record["job"]
        commit = (record.get("provenance", {}).get("git", {}).get("commit") or "")[:7]
        if job["app"] == "phoenix" and commit in PHOENIX_BEFORE_FIX:
            continue
        cov = (record.get("istanbul") or {}).get("branch_coverage")
        runs.append({
            "app": job["app"], "arm": job["arm"], "seed": job["seed"],
            "coverage": None if cov is None else 100.0 * cov,
            "failures": record.get("failures", {}).get("unique_failures", 0),
            "console": console_failures(record.get("failures", {}).get("failures", [])),
            "v8": (record.get("coverage") or {}).get("js_coverage"),
            "steps": record.get("hunt", {}).get("steps"),
            "failure_list": record.get("failures", {}).get("failures", []),
        })
    return runs


def primary(root: Path) -> list[dict]:
    laptop = [r for r in load(sorted((root / "runs/ext-eval/results").glob("*.json")))
              if r["app"] in ("dimeshift", "pagekit", "splittypie")]
    aws = [r for r in load(sorted(root.glob("runs/ext-eval-aws*/results/*.json")))
           if r["app"] in ("phoenix", "retroboard", "petclinic")]
    return laptop + aws


def summarise(runs: list[dict]) -> dict:
    cells = defaultdict(list)
    for r in runs:
        cells[(r["app"], r["arm"])].append(r)
    table = {}
    for (app, arm), rs in cells.items():
        covs = [r["coverage"] for r in rs if r["coverage"] is not None]
        fails = [r["failures"] for r in rs]
        console = [r["console"] for r in rs]
        table[f"{app}|{arm}"] = {
            "runs": len(rs),
            "coverage_mean": statistics.mean(covs) if covs else None,
            "coverage_min": min(covs) if covs else None,
            "coverage_max": max(covs) if covs else None,
            "failures_mean": statistics.mean(fails),
            "failures_min": min(fails), "failures_max": max(fails),
            "console_mean": statistics.mean(console),
            "console_min": min(console), "console_max": max(console),
            "v8_mean": statistics.mean([r["v8"] for r in rs if r["v8"] is not None] or [0]),
            "steps_mean": statistics.mean([r["steps"] or 0 for r in rs]),
        }
    return table


def averages(table: dict, apps=APPS) -> dict:
    out = {}
    for arm in ARMS:
        cov = [table[f"{a}|{arm}"]["coverage_mean"] for a in apps if f"{a}|{arm}" in table]
        fail = [table[f"{a}|{arm}"]["failures_mean"] for a in apps if f"{a}|{arm}" in table]
        con = [table[f"{a}|{arm}"]["console_mean"] for a in apps if f"{a}|{arm}" in table]
        out[arm] = {"apps": len(cov), "coverage": statistics.mean(cov) if cov else None,
                    "failures": statistics.mean(fail) if fail else None,
                    "console": statistics.mean(con) if con else None}
    for tool, values in PUBLISHED.items():
        rows = [values[a] for a in apps]
        out[tool] = {"apps": len(rows), "coverage": statistics.mean(v[0] for v in rows),
                     "failures": statistics.mean(v[1] for v in rows),
                     "console": statistics.mean(v[1] for v in rows)}
    return out


def differences(table: dict, a: str, b: str, measure: str, apps=APPS, seed: int = 0) -> dict:
    diffs = [table[f"{x}|{a}"][measure] - table[f"{x}|{b}"][measure] for x in apps
             if f"{x}|{a}" in table and f"{x}|{b}" in table]
    rng = random.Random(seed)
    boots = sorted(statistics.median(rng.choice(diffs) for _ in diffs) for _ in range(10000))
    return {"median": statistics.median(diffs), "interval": [boots[249], boots[9749]],
            "apps_higher": sum(d > 0 for d in diffs), "apps_lower": sum(d < 0 for d in diffs),
            "per_app": dict(zip([x for x in apps if f"{x}|{a}" in table], diffs))}


def markdown(result: dict) -> str:
    t = result["primary"]["table"]
    lines = ["# External comparison (WebExplor / WebRLED first benchmark)", "",
             "Branch coverage (%) and unique failures per run; mean [min, max] over 3 runs.", "",
             "| App | " + " | ".join(ARMS) + " | WebExplor | WebRLED |", "|---|" + "---|" * (len(ARMS) + 2)]
    for app in APPS:
        cells = []
        for arm in ARMS:
            c = t.get(f"{app}|{arm}")
            cells.append("-" if not c else
                         f"{c['coverage_mean']:.1f} [{c['coverage_min']:.1f}, {c['coverage_max']:.1f}]"
                         f" / {c['failures_mean']:.1f} [{c['failures_min']}, {c['failures_max']}]")
        cells += [f"{PUBLISHED[k][app][0]:.1f} / {PUBLISHED[k][app][1]:.1f}" for k in ("WebExplor", "WebRLED")]
        lines.append(f"| {app} | " + " | ".join(cells) + " |")
    lines += ["", "## Post-hoc: distinct console error messages per run (closer to the papers' count)", "",
              "| App | " + " | ".join(ARMS) + " | WebExplor | WebRLED |", "|---|" + "---|" * (len(ARMS) + 2)]
    for app in APPS:
        cells = [f"{t[f'{app}|{arm}']['console_mean']:.1f} [{t[f'{app}|{arm}']['console_min']}, "
                 f"{t[f'{app}|{arm}']['console_max']}]" if f"{app}|{arm}" in t else "-" for arm in ARMS]
        cells += [f"{PUBLISHED[k][app][1]:.1f}" for k in ("WebExplor", "WebRLED")]
        lines.append(f"| {app} | " + " | ".join(cells) + " |")
    lines += ["", "## Averages over the six applications", "",
              "| | Coverage % | Failures (pre-registered) | Console failures (post-hoc) |", "|---|---|---|---|"]
    for k, v in result["primary"]["averages"].items():
        lines.append(f"| {k} | {v['coverage']:.2f} | {v['failures']:.2f} | {v['console']:.2f} |")
    lines += ["", "## Between our arms (per-application mean difference; median over apps, 95% bootstrap)", "",
              "| Comparison | Measure | Median | Interval | Apps higher / lower |", "|---|---|---|---|---|"]
    for key, d in result["primary"]["differences"].items():
        comp, measure = key.split("::")
        lines.append(f"| {comp} | {measure} | {d['median']:+.2f} | [{d['interval'][0]:+.2f}, "
                     f"{d['interval'][1]:+.2f}] | {d['apps_higher']} / {d['apps_lower']} |")
    rep = result.get("replication")
    if rep:
        lines += ["", "## Replication on GitHub Actions (Phoenix, Retroboard, Petclinic)", "",
                  "| App | Arm | AWS coverage / console failures | GitHub coverage / console failures |", "|---|---|---|---|"]
        for app in ("phoenix", "retroboard", "petclinic"):
            for arm in ARMS:
                p, g = t.get(f"{app}|{arm}"), rep["table"].get(f"{app}|{arm}")
                if p and g:
                    lines.append(f"| {app} | {arm} | {p['coverage_mean']:.1f} / {p['console_mean']:.1f} | "
                                 f"{g['coverage_mean']:.1f} / {g['console_mean']:.1f} |")
    return "\n".join(lines) + "\n"


CLICKABLES = {"hybrid_final": "hybrid_final_clickables",
              "hybrid_curiosity": "hybrid_curiosity_clickables", "random": "random_clickables"}


def followup(root: Path) -> dict:
    """POST-HOC follow-up (tag v4-clickables): each arm with and without custom clickables,
    all on GitHub Actions. Without: `runs/ext-v4-gha-base` (Dimeshift, Pagekit, Splittypie)
    and the replication `runs/ext-eval-gha` (Phoenix, Retroboard, Petclinic); with:
    `runs/ext-v4-gha`."""
    base = load(sorted((root / "runs/ext-v4-gha-base/results").glob("*.json")))
    base += [r for r in load(sorted((root / "runs/ext-eval-gha/results").glob("*.json")))
             if r["app"] in ("phoenix", "retroboard", "petclinic")]
    with_clickables = load(sorted((root / "runs/ext-v4-gha/results").glob("*.json")))
    reverse = {v: k for k, v in CLICKABLES.items()}
    for r in with_clickables:
        r["arm"] = reverse[r["arm"]]
    without, with_ = summarise(base), summarise(with_clickables)
    gains = {}
    for arm in ARMS:
        for measure in ("coverage_mean", "console_mean"):
            per_app = {a: with_[f"{a}|{arm}"][measure] - without[f"{a}|{arm}"][measure] for a in APPS
                       if f"{a}|{arm}" in with_ and f"{a}|{arm}" in without}
            values = list(per_app.values())
            rng = random.Random(0)
            boots = sorted(statistics.median(rng.choice(values) for _ in values)
                           for _ in range(10000))
            gains[f"{arm}::{measure}"] = {"median": statistics.median(values),
                                          "interval": [boots[249], boots[9749]],
                                          "apps_higher": sum(v > 0 for v in values),
                                          "apps_lower": sum(v < 0 for v in values),
                                          "per_app": per_app}
    return {"without": without, "with": with_, "averages_without": averages(without),
            "averages_with": averages(with_), "gains": gains,
            "runs": {"without": len(base), "with": len(with_clickables)}}


def followup_markdown(f: dict) -> str:
    lines = ["", "## POST-HOC follow-up: custom clickables (all runs on GitHub Actions)", "",
             "Branch coverage % (mean over 3 runs): without -> with clickables.", "",
             "| App | " + " | ".join(ARMS) + " | WebExplor | WebRLED |", "|---|" + "---|" * (len(ARMS) + 2)]
    for app in APPS:
        cells = [f"{f['without'][f'{app}|{arm}']['coverage_mean']:.1f} -> "
                 f"{f['with'][f'{app}|{arm}']['coverage_mean']:.1f}" for arm in ARMS]
        cells += [f"{PUBLISHED[k][app][0]:.1f}" for k in ("WebExplor", "WebRLED")]
        lines.append(f"| {app} | " + " | ".join(cells) + " |")
    lines += ["", "| | Coverage without | Coverage with | Console failures without | with |", "|---|---|---|---|---|"]
    for arm in ARMS:
        w, c = f["averages_without"][arm], f["averages_with"][arm]
        lines.append(f"| {arm} | {w['coverage']:.2f} | {c['coverage']:.2f} | {w['console']:.2f} | {c['console']:.2f} |")
    for tool in ("WebExplor", "WebRLED"):
        v = f["averages_with"][tool]
        lines.append(f"| {tool} (published) | | {v['coverage']:.2f} | | {v['console']:.2f} |")
    lines += ["", "| Arm | Measure | Gain, median over apps | Interval | Apps up / down |", "|---|---|---|---|---|"]
    for key, g in f["gains"].items():
        arm, measure = key.split("::")
        lines.append(f"| {arm} | {measure} | {g['median']:+.2f} | [{g['interval'][0]:+.2f}, "
                     f"{g['interval'][1]:+.2f}] | {g['apps_higher']} / {g['apps_lower']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="output stem (.json and .md)")
    args = parser.parse_args()
    runs = primary(REPO)
    table = summarise(runs)
    result = {"primary": {
        "runs": len(runs), "table": table, "averages": averages(table),
        "differences": {f"{a} - {b}::{m}": differences(table, a, b, m)
                        for a, b in (("hybrid_final", "random"), ("hybrid_curiosity", "random"),
                                     ("hybrid_final", "hybrid_curiosity"))
                        for m in ("coverage_mean", "failures_mean", "console_mean")},
    }}
    replication = load(sorted((REPO / "runs/ext-eval-gha/results").glob("*.json")))
    if replication:
        rtable = summarise(replication)
        result["replication"] = {"runs": len(replication), "table": rtable}
    if (REPO / "runs/ext-v4-gha/results").is_dir():
        result["followup_clickables"] = followup(REPO)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    text = markdown(result)
    if "followup_clickables" in result:
        text += followup_markdown(result["followup_clickables"])
    args.out.with_suffix(".md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
