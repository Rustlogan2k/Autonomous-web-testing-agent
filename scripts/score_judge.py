"""Score an LLM judge against the captured trace corpora — the project's load-bearing number.

The deterministic triggers reach 3 of the 10 seeded bugs. Everything this project
claims about semantic judgment rests on whether a source-grounded LLM reaches the
other 7. This script measures that, offline, against fixed files.

Read the result as follows:

    5-7 of 7   the task is doable from the trace; proceed to a local model and live use
    3-4 of 7   real but partial; still beats the deterministic ceiling, report the split
    0-2 of 7   the evidence in the trace is insufficient, not the model. Fix the
               observation, do not tune prompts

The false-positive rate is not a secondary metric. In live use the verdict becomes
reward, so a judge that invents findings teaches the agent to seek out whatever
produced them. A high recall with a high false-positive rate is a failure.

Usage:
    python scripts/score_judge.py --dry-run                 # render prompts, no API calls
    python scripts/score_judge.py --judge stub              # pipeline check, always-clean
    python scripts/score_judge.py --corpus scripted         # the real run
    python scripts/score_judge.py --corpus both --limit 20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# Rendered windows contain whatever text the target app does — the toy site alone has
# "← Home". The Windows console defaults to cp1252 and raises on the first such
# character, which would abort a scoring run after the model calls were already paid for.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from web_testing_agent.annotation import load_trace  # noqa: E402
from web_testing_agent.judge import (  # noqa: E402
    WINDOW_STEPS,
    ClaudeJudge,
    Judgment,
    OllamaJudge,
    StubJudge,
    build_windows,
    score,
    summarize,
)
from web_testing_agent.intake import ApplicationProfile, window_paths  # noqa: E402
from web_testing_agent.reward.gating import GateStats, decide_for_record  # noqa: E402
from web_testing_agent.judge.scoring import is_grounded  # noqa: E402
from web_testing_agent.judge.validate import summarize_issues, validate_corpus  # noqa: E402
from web_testing_agent.judge.ollama import (  # noqa: E402
    DEFAULT_HOST as OLLAMA_HOST,
    DEFAULT_MODEL as OLLAMA_DEFAULT_MODEL,
    DEFAULT_NUM_CTX as OLLAMA_NUM_CTX,
)
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

ANNOTATIONS = REPO_ROOT / "data" / "annotations"
ANSWER_KEY = REPO_ROOT / "tests" / "fixtures" / "toy_site" / "answer_key.json"
REPORTS = REPO_ROOT / "reports"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", default="toy", help="capture run to score (default: toy)")
    parser.add_argument("--corpus", choices=["scripted", "random", "both"], default="scripted")
    parser.add_argument("--judge", choices=["ollama", "claude", "stub"], default="ollama")
    parser.add_argument("--model", default=None, help="override the judge model id")
    parser.add_argument("--window", type=int, default=WINDOW_STEPS)
    parser.add_argument("--limit", type=int, default=0, help="score only the first N windows")
    parser.add_argument("--no-thinking", action="store_true", help="claude only")
    parser.add_argument("--num-ctx", type=int, default=OLLAMA_NUM_CTX, help="ollama only")
    parser.add_argument("--host", default=None, help="ollama base url")
    parser.add_argument("--prompt", choices=["detailed", "compact"], default="detailed",
                        help="compact adds few-shot turns and shortens the instructions; "
                             "aimed at small local models")
    parser.add_argument(
        "--no-structured", action="store_true",
        help="ollama only: instruct JSON in the prompt instead of constraining decoding. "
             "Needed for :cloud models, which accept the format schema and ignore it.",
    )
    parser.add_argument("--keep-idle", action="store_true", help="also judge idle NO_OP steps")
    parser.add_argument(
        "--no-gate", dest="gate", action="store_false",
        help="judge every window instead of only those the live reward path would. "
             "Use to reproduce a pre-2026-08-10 ungated number; the default matches "
             "what actually ships.",
    )
    parser.set_defaults(gate=True)
    parser.add_argument(
        "--profile", type=Path, default=None,
        help="Application Profile JSON to ground the judge in (data/profiles/<app>.json). "
             "Omit for the ungrounded arm of the A/B. The profile is sliced per window "
             "and passed separately from the window, never merged into it.",
    )
    parser.add_argument("--dry-run", action="store_true", help="render prompts, validate them, and exit without calling a model")
    parser.add_argument("--ignore-window-issues", action="store_true",
                        help="score even when the rendered windows fail validation")
    parser.add_argument(
        "--answer-key", type=Path, default=None,
        help="ground truth for this corpus (default: the toy site's). Point at "
             "tests/fixtures/gitea_bugs/answer_key.json to score a real target.",
    )
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args()


def corpus_dirs(run_id: str, which: str) -> list[tuple[str, Path]]:
    names = ["scripted", "random"] if which == "both" else [which]
    found = []
    for name in names:
        path = ANNOTATIONS / f"{run_id}-{name}"
        if not (path / "trace.jsonl").is_file():
            raise SystemExit(
                f"No trace at {path}. Capture one first:\n"
                f"    python scripts/capture_traces.py --run-id {run_id}"
            )
        found.append((name, path))
    return found


def score_corpus(name: str, path: Path, judge, args: argparse.Namespace, profile=None) -> dict:
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    labels = {int(k): v for k, v in (meta.get("episode_labels") or {}).items()}
    records = list(load_trace(path))
    windows = build_windows(records, window_steps=args.window, skip_idle=not args.keep_idle)

    # The live reward path judges only what the gate lets through, so an offline score
    # that judges everything is measuring a pipeline that is not the one shipped — and
    # measuring it *worse*, since the gate's whole purpose is to decline windows whose
    # verdict is knowable without asking. Gating is therefore the default here, and
    # `--no-gate` is what you pass to reproduce a historical ungated number.
    gate_stats = GateStats()
    gate_skipped: list[dict] = []
    if args.gate:
        by_step = {r.global_step: r for r in records}
        kept = []
        for window in windows:
            decision = decide_for_record(by_step[window.focus_global_step])
            gate_stats.record(decision)
            if decision.judge:
                kept.append(window)
            else:
                gate_skipped.append(
                    {"episode": window.episode, "global_step": window.focus_global_step,
                     "reason": decision.reason}
                )
        logger.info(
            "{}: gate passes {}/{} windows ({:.0%}); {} skipped",
            name, gate_stats.judged, gate_stats.total, gate_stats.judged_fraction, gate_stats.skipped,
        )
        windows = kept

    if args.limit:
        windows = windows[: args.limit]

    if not labels:
        logger.info(
            "{}: no per-episode ground truth (this corpus measures false positives, "
            "so every positive verdict here is unattributed and reported as such)", name,
        )

    logger.info("{}: {} steps -> {} windows (K={})", name, len(records), len(windows), args.window)

    # Checked before a single call is made. Every judge defect found so far was a
    # window that contradicted its own record, and each cost a full scoring run plus a
    # manual read to notice. These are decidable offline in milliseconds.
    issues = validate_corpus(windows, context_chars=args.num_ctx * 3)
    if issues:
        print("\n" + summarize_issues(issues))
        if not args.ignore_window_issues:
            raise SystemExit(
                "\nRefusing to score: the rendered windows contradict their own records, "
                "so any verdict would be measuring the renderer rather than the judge.\n"
                "Fix them, or pass --ignore-window-issues to score anyway."
            )

    judgments: list[Judgment] = []
    profiled_windows = 0
    profile_chars = 0
    started = time.monotonic()
    for index, window in enumerate(windows, start=1):
        label = labels.get(window.episode, {})
        rendered = window.render()
        # Sliced per window to the routes it actually touched (PROJECT_CONTEXT 3.0b's
        # "relevant slice"), and passed as its own argument. `is_grounded` below still
        # checks quotes against `rendered` alone, so a judge cannot satisfy the evidence
        # requirement by quoting the specification back.
        profile_text = profile.render(window_paths(window)) if profile else ""
        if profile_text:
            profiled_windows += 1
            profile_chars += len(profile_text)
        verdict = judge.judge(rendered, profile_text)
        judgments.append(
            Judgment(
                episode=window.episode,
                global_step=window.focus_global_step,
                verdict=verdict,
                expected_bugs=list(label.get("bugs", [])),
                script=label.get("script", ""),
                labelled=bool(labels),
                grounded=is_grounded(verdict.evidence, rendered) if verdict.ok and verdict.is_bug else None,
            )
        )
        if verdict.is_bug:
            logger.info(
                "  [{}/{}] ep{} step{} -> {} ({:.0%}) {}",
                index, len(windows), window.episode, window.focus_global_step,
                verdict.bug_type, verdict.confidence, verdict.evidence[:90],
            )
        elif index % 25 == 0:
            logger.info("  [{}/{}] …", index, len(windows))

    elapsed = time.monotonic() - started
    report = score(judgments, corpus=name, judge=getattr(judge, "name", "?"))
    answer_key = json.loads((args.answer_key or ANSWER_KEY).read_text(encoding="utf-8"))

    print("\n" + "=" * 72)
    print(summarize(report, answer_key))
    print(f"wall clock            : {elapsed:.1f}s ({elapsed / max(len(windows), 1):.2f}s per window)")
    if profile is not None:
        print(
            f"profile grounding     : {profiled_windows}/{len(windows)} windows "
            f"(mean {profile_chars // max(profiled_windows, 1)} chars) "
            f"[{profile.name}, provenance={profile.provenance}]"
        )
    if args.gate:
        print(
            f"judge-call gate       : {gate_stats.judged}/{gate_stats.total} windows judged "
            f"({gate_stats.judged_fraction:.0%}), {gate_stats.skipped} skipped   "
            f"<- matches the live reward path"
        )
    else:
        print("judge-call gate       : DISABLED (--no-gate) — this is not what ships")
    print("=" * 72)

    return {
        **report.to_dict(),
        "window_steps": args.window,
        "skip_idle": not args.keep_idle,
        "wall_clock_s": round(elapsed, 1),
        # Recorded so a gated score can never be quoted as an ungated one. The two
        # differ by design and the difference is the point of the gate.
        "gated": args.gate,
        "gate": gate_stats.to_dict() if args.gate else {"enabled": False},
        "gate_skipped": gate_skipped,
        # Recorded on every report so a grounded number can never be quoted as an
        # ungrounded one, and a hand-authored profile can never be quoted as profiler
        # output. Both mistakes are otherwise invisible in the result.
        "profile": (
            {
                "name": profile.name,
                "provenance": profile.provenance,
                "windows_grounded": profiled_windows,
                "mean_profile_chars": profile_chars // max(profiled_windows, 1),
            }
            if profile is not None
            else None
        ),
        "judgments": [
            {
                "episode": j.episode,
                "global_step": j.global_step,
                "script": j.script,
                "expected_bugs": j.expected_bugs,
                "grounded": j.grounded,
                **j.verdict.to_dict(),
            }
            for j in judgments
        ],
    }


def _build_judge(args: argparse.Namespace):
    if args.judge == "stub":
        return StubJudge()
    if args.judge == "claude":
        return ClaudeJudge(model=args.model or "claude-opus-5", thinking=not args.no_thinking)

    judge = OllamaJudge(
        model=args.model or OLLAMA_DEFAULT_MODEL,
        host=args.host or OLLAMA_HOST,
        num_ctx=args.num_ctx,
        structured=not args.no_structured,
        prompt_style=args.prompt,
    )
    # Checked up front: a missing model or a stopped daemon should fail now, not after
    # a third of the corpus has already been judged.
    judge.preflight()
    return judge


def main() -> None:
    args = parse_args()
    corpora = corpus_dirs(args.run_id, args.corpus)

    if args.dry_run:
        for name, path in corpora:
            records = list(load_trace(path))
            windows = build_windows(records, window_steps=args.window, skip_idle=not args.keep_idle)
            rendered = [w.render() for w in windows]
            sizes = [len(text) for text in rendered]
            print(f"\n{'=' * 72}\n{name}: {len(records)} steps -> {len(windows)} windows")
            print(f"prompt chars: min {min(sizes)}, mean {sum(sizes) // len(sizes)}, max {max(sizes)}")
            print(f"approx input tokens for the run: {sum(sizes) // 4:,}")
            print(summarize_issues(validate_corpus(windows, context_chars=args.num_ctx * 3)))
            print("=" * 72)
            print(rendered[args.limit if args.limit < len(rendered) else 0])
        return

    judge = _build_judge(args)
    profile = ApplicationProfile.load(args.profile) if args.profile else None
    if profile is not None:
        logger.info(
            "Grounding the judge in the {} profile ({} routes, {} rules, {} flows, "
            "{} known behaviours, provenance={})",
            profile.name, len(profile.routes), len(profile.rules), len(profile.flows),
            len(profile.behaviours), profile.provenance,
        )

    results = {name: score_corpus(name, path, judge, args, profile) for name, path in corpora}
    payload = {
        "run_id": args.run_id,
        "judge": getattr(judge, "name", args.judge),
        "window_steps": args.window,
        "grounded": profile is not None,
        "profile_path": str(args.profile) if args.profile else None,
        "usage": judge.usage_summary() if hasattr(judge, "usage_summary") else {},
        "corpora": results,
    }

    suffix = "_grounded" if profile is not None else ""
    out = args.out or REPORTS / f"judge_{args.run_id}_{getattr(judge, 'name', args.judge)}{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote judge scores to {}", out)

    # Backends report different fields (a local model has no cache-read count, a hosted
    # one has no seconds-per-call), so print whatever this one actually reported rather
    # than assuming one backend's schema.
    if hasattr(judge, "usage_summary"):
        usage = judge.usage_summary()
        print("\nusage: " + "  ".join(
            f"{key}={value:,}" if isinstance(value, int) else f"{key}={value}"
            for key, value in usage.items()
        ))


if __name__ == "__main__":
    main()
