"""Failures found by one of our agents and by WebRLED, counted the same way (RQ4).

    python scripts/rq4_failures.py hybrid_final_v5 [--ours DIR ...] [--webrled DIR]
        [--json out.json]

The common definition is WebRLED's own (its src/webenv/config.py, update_errors): a failure
is a distinct browser console message of type "error" or an uncaught page error, identified
by its text. Both tools' texts are normalised by the same function
(`tester.triggers._normalise`: numbers, ports and query strings removed, whitespace
collapsed, 160 characters), so one defect reported with different ids counts once.

- Ours: the `console_error` entries of each result's `failures` (our runner records console
  errors and page errors under that kind, per page); deduplicated here by normalised text
  alone, ignoring the page, as WebRLED does.
- WebRLED: every "Console Error Log: <text>" line of its run log (it logs each new error
  once per run); a "PageError: " prefix is dropped, as our runner records page errors
  without one.

On top of that normalisation, both tools' texts get the same key (`key`): a page error's
stack frames are dropped (our runner keeps them, WebRLED's log does not) and the
application's own origin is written one way (our runner writes it `http://...`, WebRLED's
log `http://localhost`). Messages that are network failures of the browser
("Failed to load resource: net::ERR_...") are kept apart as `network`: they come from hosts
outside the application (live reload, analytics, avatars), which our runner stubs or drops.

Known differences in what each tool can see, stated wherever these counts are reported:
our runner drops console errors whose source is another origin and stubs analytics and
live-reload hosts (see hybrid/external.py, STUB_HOSTS); WebRLED keeps them. Our runner keeps
at most three console errors per step.

Per application: failures per run (mean, sd, n) for both tools, Mann-Whitney U p and
Vargha-Delaney A12; the union over all runs; how many of the union only one tool found.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from compare_with_webrled import OUR_RESULTS, a12, magnitude, mann_whitney  # noqa: E402

from web_testing_agent.tester.triggers import _normalise  # noqa: E402

LOG_LINE = re.compile(r"Console Error Log: (.*)$")
_STACK = re.compile(r"\s+at\s+(?:\S+\s+)?\(?https?://.*$")
_LOCAL_ORIGIN = re.compile(r"https?://(?:localhost|127\.0\.0\.1|\.\.\.)(?::\d+)?")
_NETWORK = re.compile(r"^Failed to load resource: net::ERR_")


def key(text: str) -> str:
    """One failure's identity, the same for both tools (see the module docstring)."""
    # The origin is rewritten before normalising, as our runner did before it kept the text,
    # so both tools' messages are cut at 160 characters at the same place.
    text = _LOCAL_ORIGIN.sub("http://...", text.removeprefix("PageError: "))
    return _STACK.sub("", _normalise(text)).strip()


def is_network(text: str) -> bool:
    return bool(_NETWORK.match(text))


def our_failures(directories: list[str], arm: str) -> dict[str, list[set[str]]]:
    """app -> one set of normalised failure texts per run."""
    runs: dict[str, list[set[str]]] = defaultdict(list)
    for directory in directories:
        for path in sorted(glob.glob(str(ROOT / directory / "*.json"))):
            try:
                record = json.loads(Path(path).read_text(encoding="utf-8"))
            except ValueError:
                continue
            if not isinstance(record, dict) or record.get("status") != "ok":
                continue
            job = record.get("job") or {}
            if job.get("arm") != arm or "failures" not in record:
                continue
            texts = {key(f["detail"]) for f in record["failures"].get("failures", [])
                     if f["kind"] == "console_error"}
            runs[job["app"]].append(texts)
    return runs


def webrled_failures(root: str, complete_only: bool = True) -> dict[str, list[set[str]]]:
    """app -> one set per WebRLED run, from each artifact's webrled.log and meta.json."""
    runs: dict[str, list[set[str]]] = defaultdict(list)
    for meta_path in sorted(glob.glob(str(ROOT / root / "**" / "meta.json"), recursive=True)):
        meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        log = Path(meta_path).parent / "webrled.log"
        if not log.exists():
            continue
        text = log.read_text(encoding="utf-8", errors="replace")
        if complete_only and "Reached 1 hours." not in text:
            continue
        found = set()
        for line in text.splitlines():
            match = LOG_LINE.search(line)
            if match:
                found.add(key(match.group(1).strip()))
        runs[meta["app"]].append(found)
    return runs


def summarise(ours: dict[str, list[set[str]]], theirs: dict[str, list[set[str]]],
              network: bool = False) -> list[dict]:
    """Per application; network failures are left out unless `network`."""
    if not network:
        ours = {a: [{t for t in s if not is_network(t)} for s in r] for a, r in ours.items()}
        theirs = {a: [{t for t in s if not is_network(t)} for s in r] for a, r in theirs.items()}
    rows = []
    for app in sorted(set(ours) | set(theirs)):
        a = [len(s) for s in ours.get(app, [])]
        b = [len(s) for s in theirs.get(app, [])]
        union_a = set().union(*ours.get(app, [set()]))
        union_b = set().union(*theirs.get(app, [set()]))
        row = {
            "app": app,
            "ours_mean": statistics.mean(a) if a else None,
            "ours_sd": statistics.stdev(a) if len(a) > 1 else 0.0,
            "ours_n": len(a),
            "webrled_mean": statistics.mean(b) if b else None,
            "webrled_sd": statistics.stdev(b) if len(b) > 1 else 0.0,
            "webrled_n": len(b),
            "mwu_p": mann_whitney(a, b) if a and b else None,
            "a12": a12(a, b) if a and b else None,
            "union_ours": len(union_a),
            "union_webrled": len(union_b),
            "only_ours": sorted(union_a - union_b),
            "only_webrled": sorted(union_b - union_a),
            "both": sorted(union_a & union_b),
        }
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("arm")
    parser.add_argument("--ours", nargs="*", default=OUR_RESULTS)
    parser.add_argument("--webrled", default="runs/gha-webrled/artifacts")
    parser.add_argument("--include-incomplete", action="store_true",
                        help="also count WebRLED runs that did not reach one hour")
    parser.add_argument("--json", type=Path, help="write the rows, failure texts included")
    parser.add_argument("--show", action="store_true", help="print the failure texts")
    parser.add_argument("--network", action="store_true",
                        help="count the browser's network failures too")
    args = parser.parse_args()
    rows = summarise(our_failures(args.ours, args.arm),
                     webrled_failures(args.webrled, not args.include_incomplete), args.network)
    print(f"{'app':11} {'ours':>6} {'sd':>5} {'n':>3}  {'WebRLED':>7} {'sd':>5} {'n':>3}  "
          f"{'MWU p':>6} {'A12':>5}            {'union':>11} {'only':>9} {'both':>4}")
    for r in rows:
        def cell(mean):  # noqa: ANN001, ANN202
            return f"{mean:6.1f}" if mean is not None else "     -"
        effect = (f"{r['mwu_p']:6.3f} {r['a12']:5.2f} {magnitude(r['a12']):10}"
                  if r["a12"] is not None else f"{'-':>6} {'-':>5} {'':10}")
        print(f"{r['app']:11} {cell(r['ours_mean'])} {r['ours_sd']:5.1f} {r['ours_n']:3}  "
              f"{cell(r['webrled_mean']):>7} {r['webrled_sd']:5.1f} {r['webrled_n']:3}  {effect} "
              f"{r['union_ours']:5}/{r['union_webrled']:<5} {len(r['only_ours']):4}/"
              f"{len(r['only_webrled']):<4} {len(r['both']):4}")
        if args.show:
            for label in ("only_ours", "only_webrled", "both"):
                for text in r[label]:
                    print(f"    {label:12} {text}")
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
