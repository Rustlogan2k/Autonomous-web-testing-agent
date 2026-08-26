"""Runs a live rollout with the LLM judge in the reward loop.

This is §9 item 1e end to end: browser -> env -> gate -> judge -> reward, against a
real target, with the judge's verdicts landing in the reward the policy sees rather
than in an offline report.

It is deliberately the *evaluation* path, not the training path. At ~9 s per judged
window and ~60% of steps passing the gate, a 40-step episode costs 5-6 minutes of
judging. That is fine for finding bugs in an application and hopeless for the 30-50K
steps §8 plans for; training against a live judge needs a much faster judge or
off-policy relabelling of stored transitions, and neither exists yet.

Usage:
    python scripts/run_judged.py --base-url http://localhost:3000/ --profile data/profiles/gitea.json
    python scripts/run_judged.py --toy --episodes 2 --steps 12 --judge stub
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from web_testing_agent.envs.functional_env import WebFunctionalEnv  # noqa: E402
from web_testing_agent.envs.types import MAX_ACTIONS  # noqa: E402
from web_testing_agent.evaluation import RandomPolicy, run_rollout  # noqa: E402
from web_testing_agent.reporting import build_report, render_markdown  # noqa: E402
from web_testing_agent.intake import ApplicationProfile  # noqa: E402
from web_testing_agent.judge import OllamaJudge, StubJudge  # noqa: E402
from web_testing_agent.judge.ollama import DEFAULT_HOST, DEFAULT_NUM_CTX  # noqa: E402
from web_testing_agent.reward import JudgeRewardModel  # noqa: E402
from web_testing_agent.utils.local_server import serve_directory  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

TOY_SITE = REPO_ROOT / "tests" / "fixtures" / "toy_site"
REPORTS = REPO_ROOT / "reports"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--base-url", help="a running application to test")
    target.add_argument("--toy", action="store_true", help="serve and test the toy fixture")
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--judge", choices=["ollama", "stub"], default="ollama")
    parser.add_argument("--model", default="gpt-oss:120b-cloud")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX)
    parser.add_argument("--prompt", choices=["detailed", "compact"], default="detailed")
    parser.add_argument("--structured", action="store_true",
                        help="constrain decoding; :cloud models accept the schema and ignore it")
    parser.add_argument("--profile", type=Path, default=None, help="Application Profile JSON")
    parser.add_argument("--no-gate", action="store_true", help="judge every step (slow; for comparison)")
    parser.add_argument("--max-calls-per-episode", type=int, default=0, help="0 = unlimited")
    parser.add_argument("--min-confidence", type=float, default=0.0)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--report", type=Path, default=None,
        help="where to write the human-readable bug report (Markdown; a .json twin is "
             "written beside it). Defaults to <out>_report.md.",
    )
    return parser.parse_args()


def build_reward_model(args: argparse.Namespace) -> JudgeRewardModel:
    if args.judge == "stub":
        judge = StubJudge()
    else:
        judge = OllamaJudge(
            model=args.model, host=args.host, num_ctx=args.num_ctx,
            structured=args.structured, prompt_style=args.prompt,
        )
        # Fail now rather than once the browser is up and the first episode is running.
        judge.preflight()
    profile = ApplicationProfile.load(args.profile) if args.profile else None
    if profile is not None:
        logger.info("Grounding the judge in the {} profile (provenance={})", profile.name, profile.provenance)
    return JudgeRewardModel(
        judge,
        profile=profile,
        gated=not args.no_gate,
        max_calls_per_episode=args.max_calls_per_episode,
        min_confidence=args.min_confidence,
    )


def run(base_url: str, args: argparse.Namespace) -> dict:
    reward_model = build_reward_model(args)
    env = WebFunctionalEnv(base_url=base_url, max_steps=args.steps, headless=True, reward_model=reward_model)
    started = time.monotonic()
    try:
        report = run_rollout(
            env,
            RandomPolicy(MAX_ACTIONS, seed=args.seed, valid_only=True),
            episodes=args.episodes,
            label="judged-random",
            seed=args.seed,
        )
    finally:
        env.close()
    elapsed = time.monotonic() - started

    usage = reward_model.usage_summary()
    print("\n" + report.summary())
    print("\n" + "=" * 74)
    print("judge")
    print("=" * 74)
    print(f"calls            : {usage['calls']} (failed {usage['failed_calls']})")
    print(f"judge wall clock : {usage['judge_seconds']:.0f}s of {elapsed:.0f}s total "
          f"({usage['judge_seconds'] / max(elapsed, 1):.0%} of the run)")
    print(f"per call         : {usage['seconds_per_call']}s")
    if usage["gate"].get("enabled") is not False:
        gate = usage["gate"]
        print(f"gate             : judged {gate['judged']}/{gate['steps']} "
              f"({gate['judged_fraction']:.0%}), saving ~"
              f"{gate['skipped'] * usage['seconds_per_call'] / 60:.1f} min")
        for reason, count in gate["reasons"].items():
            print(f"    {count:>4}  {reason}")
    print(f"positives        : {usage['positives']}")
    for verdict in reward_model.verdicts:
        if verdict.get("is_bug"):
            print(f"  ep{verdict['episode']} step{verdict['step']} {verdict['bug_type']} "
                  f"({verdict.get('confidence', 0):.0%}) — {verdict.get('evidence', '')[:90]}")

    return {
        "target": base_url,
        "episodes": args.episodes,
        "steps_per_episode": args.steps,
        "seed": args.seed,
        "wall_clock_s": round(elapsed, 1),
        "rollout": report.to_dict(),
        "judge": usage,
        "verdicts": reward_model.verdicts,
    }


def write_bug_report(payload: dict, out_path: Path) -> Path:
    """Turn one run's raw output into the document a person actually reads.

    Both producers are passed in together and kept distinguishable by the engine: the
    rollout's deduplicated deterministic findings, and the judge's verdicts. The
    confidence floor is the same one the reward path used, so the report cannot claim a
    finding the run itself declined to pay for.
    """
    bug_report = build_report(
        target=payload["target"],
        findings=payload["rollout"].get("findings") or [],
        verdicts=payload.get("verdicts") or [],
        min_confidence=payload["judge"].get("min_confidence", 0.0),
        run_meta={
            "episodes": payload["episodes"],
            "steps_per_episode": payload["steps_per_episode"],
            "seed": payload["seed"],
            "wall_clock_s": payload["wall_clock_s"],
            "judge": payload["judge"].get("judge"),
            "judge_calls": payload["judge"].get("calls"),
        },
    )
    out_path.write_text(render_markdown(bug_report), encoding="utf-8")
    out_path.with_suffix(".json").write_text(
        json.dumps(bug_report.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    counts = bug_report.counts
    print()
    print("=" * 74)
    print("bug report")
    print("=" * 74)
    print(f"findings         : {counts['total']} "
          f"({counts['deterministic']} deterministic, {counts['judge']} judge)")
    print(f"severity         : {counts['high']} high, {counts['medium']} medium, {counts['low']} low")
    if counts["ungrounded_excluded"]:
        print(f"excluded         : {counts['ungrounded_excluded']} verdict(s) whose evidence "
              f"was not in the window")
    print(f"written to       : {out_path}")
    return out_path


def main() -> None:
    args = parse_args()
    if args.toy:
        with serve_directory(TOY_SITE) as origin:
            payload = run(f"{origin}/index.html", args)
    else:
        payload = run(args.base_url, args)

    suffix = "_grounded" if args.profile else ""
    out = args.out or REPORTS / f"judged_rollout{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote {}", out)

    write_bug_report(payload, (args.report or out.with_name(out.stem + "_report.md")))


if __name__ == "__main__":
    main()
