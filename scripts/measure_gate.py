"""Replays the judge-call gate over captured corpora and scored verdicts.

A filter that saves calls is worthless if it discards the calls that found something,
and the only way to know is to replay it against runs where the verdicts are already
known. Both halves are reported:

* **saving** — the share of steps the gate declines to judge, and why.
* **loss** — for a corpus that has been scored, how many *positive* verdicts sat on
  steps the gate would have skipped. That number is the one that decides whether the
  gate is safe, and it is checked per bug type, because losing every `dead_control`
  while keeping everything else would be invisible in a total.

Usage:
    python scripts/measure_gate.py --run-id gitea3 --report reports/judge_gitea3_ungrounded.json
    python scripts/measure_gate.py --run-id toy --corpus scripted --report reports/judge_toy_gpt-oss-120b_lean.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from web_testing_agent.annotation import load_trace  # noqa: E402
from web_testing_agent.envs.types import ActionType  # noqa: E402
from web_testing_agent.judge import build_windows  # noqa: E402
from web_testing_agent.reward.gating import GateStats, should_judge  # noqa: E402

ANNOTATIONS = REPO_ROOT / "data" / "annotations"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", default="gitea3")
    parser.add_argument("--corpus", choices=["scripted", "random"], default="random")
    parser.add_argument("--report", type=Path, default=None, help="a scored judge report to check losses against")
    parser.add_argument("--seconds-per-call", type=float, default=8.0)
    return parser.parse_args()


def decide(record) -> object:  # noqa: ANN001 - annotation.trace.StepRecord
    """Gate decision for one recorded step, from the fields the recorder preserved."""
    raw = record.action.get("type", "NO_OP")
    try:
        action_type = ActionType(raw)
    except ValueError:
        action_type = ActionType.NO_OP
    return should_judge(
        action_type,
        exec_success=bool(record.exec.get("success", False)),
        blocked=bool(record.exec.get("left_application")),
        state_changed=record.state_changed,
        triggered=record.triggered,
        settled=record.settled,
    )


def main() -> None:
    args = parse_args()
    path = ANNOTATIONS / f"{args.run_id}-{args.corpus}"
    records = list(load_trace(path))
    # Windows, not raw steps: `build_windows(skip_idle=True)` already drops idle no-ops
    # before any judging happens, so the gate's marginal saving has to be measured
    # against what actually reaches the judge, not against every captured step.
    windows = build_windows(records)
    by_step = {r.global_step: r for r in records}

    stats = GateStats()
    skipped_steps: set[int] = set()
    for window in windows:
        decision = decide(by_step[window.focus_global_step])
        stats.record(decision)
        if not decision.judge:
            skipped_steps.add(window.focus_global_step)

    print("=" * 74)
    print(f"gate replay — {args.run_id}/{args.corpus}: {len(records)} steps, {len(windows)} windows reach the judge")
    print("=" * 74)
    print(f"judged      : {stats.judged}/{stats.total} ({stats.judged_fraction:.0%})")
    print(f"skipped     : {stats.skipped} ({1 - stats.judged_fraction:.0%})")
    saved = stats.skipped * args.seconds_per_call
    print(f"time saved  : {saved / 60:.1f} min at {args.seconds_per_call:.0f}s/call "
          f"({stats.total * args.seconds_per_call / 60:.1f} min -> {stats.judged * args.seconds_per_call / 60:.1f} min)")
    print("\nreasons:")
    for reason, count in stats.to_dict()["reasons"].items():
        print(f"  {count:>4}  {reason}")

    if not args.report:
        print("\n(no --report given, so losses are unmeasured — the saving alone does not "
              "tell you whether the gate is safe)")
        return

    payload = json.loads(args.report.read_text(encoding="utf-8"))
    judgments = [j for corpus in payload["corpora"].values() for j in corpus["judgments"]]
    positives = [j for j in judgments if j.get("is_bug")]
    lost = [j for j in positives if j["global_step"] in skipped_steps]

    print("\n" + "=" * 74)
    print(f"losses against {args.report.name}")
    print("=" * 74)
    print(f"positive verdicts     : {len(positives)}")
    print(f"on steps the gate skips: {len(lost)}")
    if lost:
        print("\nWOULD HAVE BEEN LOST:")
        for j in lost:
            print(f"  ep{j['episode']} step{j['global_step']} {j['bug_type']} "
                  f"(conf {j.get('confidence', 0):.0%}) — {j.get('evidence', '')[:80]}")
        print("\nby type: " + str(dict(Counter(j["bug_type"] for j in lost))))
    else:
        print("\nNo positive verdict sits on a step the gate would skip.")
    kept = Counter(j["bug_type"] for j in positives if j["global_step"] not in skipped_steps)
    print(f"\nkept by type: {dict(kept)}")


if __name__ == "__main__":
    main()
