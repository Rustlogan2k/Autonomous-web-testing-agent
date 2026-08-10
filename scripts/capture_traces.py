"""Capture the trace corpus the LLM judge is developed and scored against.

Produces two independent corpora, because they answer two different questions that
are uninterpretable when mixed:

* **scripted** — one episode per seeded bug, walking the repro path from
  `answer_key.json`. Ground truth is known per episode, so this measures *judge
  accuracy*: can it recognize the bug when it is put in front of it? It also
  includes a `happy_path` episode of deliberately correct behaviour, because a judge
  that flags everything scores perfect recall and is worthless.

* **random** — masked-random exploration, the policy that reached the most states in
  the run-5 comparison. No ground truth per step, so this measures the *false
  positive rate* under ordinary traffic, and shows what a real explorer actually
  puts in front of the judge.

Capture once, then iterate on prompts and models against fixed files. A judge change
tested this way costs a second instead of a browser run, and is reproducible.

Usage:
    python scripts/capture_traces.py                     # both corpora, no screenshots
    python scripts/capture_traces.py --screenshots       # also store PNGs (BUG-08 is visual)
    python scripts/capture_traces.py --only scripted
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from web_testing_agent.annotation import TraceRecorder, trace_dir_summary  # noqa: E402
from web_testing_agent.envs.functional_env import WebFunctionalEnv  # noqa: E402
from web_testing_agent.envs.types import MAX_ACTIONS  # noqa: E402
from web_testing_agent.evaluation import RandomPolicy, ScriptedPolicy, run_rollout  # noqa: E402
from web_testing_agent.utils.local_server import serve_directory  # noqa: E402
from web_testing_agent.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

TOY_SITE = REPO_ROOT / "tests" / "fixtures" / "toy_site"
OUT_DIR = REPO_ROOT / "data" / "annotations"

# One entry per seeded bug, transcribed from the `repro` field of answer_key.json.
# `bugs` is the ground-truth label for every step of that episode; the judge's verdict
# is scored against it. Steps match on the DOM id, which is what the action registry
# builds its selector from — `element_id` there is a display label and would drift.
REPRO_SCRIPTS: list[dict] = [
    {
        "name": "signup_drops_email_and_skips_age_validation",
        "bugs": ["BUG-01", "BUG-05"],
        "steps": [
            {"type": "CLICK", "id": "nav-signup"},
            {"type": "TYPE", "id": "username", "params": {"category": "valid_typical"}},
            {"type": "TYPE", "id": "email", "params": {"category": "valid_typical"}},
            # boundary_max drives the declared-but-unenforced min=13/max=120 (BUG-05).
            {"type": "TYPE", "id": "age", "params": {"category": "boundary_max"}},
            {"type": "SELECT", "id": "country"},
            {"type": "CLICK", "id": "submit-signup"},
            {"type": "NO_OP"},
        ],
    },
    {
        "name": "export_button_is_dead",
        "bugs": ["BUG-02"],
        "steps": [
            {"type": "CLICK", "id": "nav-widgets"},
            {"type": "CLICK", "id": "btn-export"},
            {"type": "NO_OP"},
        ],
    },
    {
        "name": "pricing_link_404s",
        "bugs": ["BUG-03"],
        "steps": [
            {"type": "CLICK", "id": "nav-pricing"},
            {"type": "NO_OP"},
        ],
    },
    {
        "name": "generate_report_throws",
        "bugs": ["BUG-04"],
        "steps": [
            {"type": "CLICK", "id": "nav-widgets"},
            {"type": "CLICK", "id": "btn-report"},
            {"type": "NO_OP"},
        ],
    },
    {
        "name": "signup_double_submits",
        "bugs": ["BUG-06"],
        "steps": [
            {"type": "CLICK", "id": "nav-signup"},
            {"type": "TYPE", "id": "username", "params": {"category": "valid_typical"}},
            # Fill every field: the race is the thing under test, so nothing else
            # should be able to stop the submission from going through.
            {"type": "TYPE", "id": "email", "params": {"category": "valid_typical"}},
            {"type": "TYPE", "id": "age", "params": {"category": "valid_typical"}},
            {"type": "RAPID_CLICK", "id": "submit-signup"},
            {"type": "NO_OP"},
        ],
    },
    {
        "name": "sync_spinner_never_completes",
        "bugs": ["BUG-07"],
        "steps": [
            {"type": "CLICK", "id": "nav-widgets"},
            {"type": "CLICK", "id": "btn-sync"},
            {"type": "NO_OP"},
            {"type": "NO_OP"},
        ],
    },
    {
        "name": "cta_hidden_on_mobile",
        "bugs": ["BUG-08"],
        "steps": [
            {"type": "RESIZE_VIEWPORT", "params": {"preset": "mobile"}},
            {"type": "NO_OP"},
            {"type": "RESIZE_VIEWPORT", "params": {"preset": "desktop"}},
        ],
    },
    {
        "name": "dark_mode_does_not_persist",
        "bugs": ["BUG-09"],
        "steps": [
            {"type": "CLICK", "id": "nav-settings"},
            {"type": "CLICK", "id": "opt-darkmode"},
            {"type": "CLICK", "id": "btn-save"},
            {"type": "REFRESH"},
            {"type": "NO_OP"},
        ],
    },
    {
        "name": "archive_back_link_goes_home",
        "bugs": ["BUG-10"],
        "steps": [
            {"type": "CLICK", "id": "nav-widgets"},
            # The archive link is below the fold; scrolling is the documented repro.
            {"type": "SCROLL", "params": {"direction": "down"}},
            {"type": "CLICK", "id": "link-archive"},
            {"type": "CLICK", "id": "link-back-widgets"},
            {"type": "NO_OP"},
        ],
    },
    {
        # The negative control. Every step here is correct behaviour drawn from the
        # answer key's `false_positive_watch`: the working counter, and REFRESH keeping
        # the same URL. Any bug the judge reports on this episode is a false positive.
        "name": "happy_path",
        "bugs": [],
        "steps": [
            {"type": "CLICK", "id": "nav-widgets"},
            {"type": "CLICK", "id": "btn-working"},
            {"type": "CLICK", "id": "btn-working"},
            {"type": "REFRESH"},
            {"type": "SCROLL", "params": {"direction": "down"}},
            {"type": "CLICK", "id": "link-archive"},
            {"type": "NO_OP"},
            {"type": "BROWSER_BACK"},
            {"type": "NO_OP"},
        ],
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=["scripted", "random", "both"], default="both")
    parser.add_argument(
        "--base-url", default=None,
        help="target an already-running application instead of serving the toy fixture. "
             "Forces --only random: the repro scripts encode the toy site's answer key "
             "and are meaningless anywhere else.",
    )
    parser.add_argument("--random-episodes", type=int, default=4)
    parser.add_argument("--random-steps", type=int, default=40, help="max steps per random episode")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--screenshots", action="store_true", help="store PNGs (needed for visual bugs)")
    parser.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    return parser.parse_args()


def capture_scripted(base_url: str, args: argparse.Namespace) -> dict:
    """One episode per script, all into a single trace, with per-episode ground truth."""
    longest = max(len(script["steps"]) for script in REPRO_SCRIPTS)
    recorder = TraceRecorder(
        args.out,
        f"{args.run_id}-scripted",
        save_screenshots=args.screenshots,
        meta={"corpus": "scripted", "target": base_url, "seed": args.seed,
              "purpose": "judge accuracy — known ground truth per episode"},
    )
    env = WebFunctionalEnv(base_url=base_url, max_steps=longest, headless=True)
    episode_labels: dict[str, dict] = {}
    fidelity: list[dict] = []
    try:
        for offset, script in enumerate(REPRO_SCRIPTS):
            policy = ScriptedPolicy(script["steps"], name=script["name"])
            # Size the episode to the script. A shared env sized to the longest script
            # would pad every shorter one with idle NO_OP steps — 77 of 110 steps in the
            # first capture — which is noise the judge would then have to be scored on.
            # Each script's retry budget is included so a late-appearing element still fits.
            env.max_steps = len(policy.steps) + policy.max_retries
            first_step = recorder.global_step + 1
            run_rollout(env, policy, episodes=1, label=script["name"],
                        seed=args.seed + offset, recorder=recorder)
            # The recorder's episode counter is the authority: it counts resets.
            episode_labels[str(recorder.episode)] = {
                "script": script["name"],
                "bugs": script["bugs"],
                "first_global_step": first_step,
                "last_global_step": recorder.global_step,
            }
            fidelity.append({
                "script": script["name"],
                "steps": len(script["steps"]),
                "matched": policy.completed_steps,
                "missed": policy.misses,
            })
            if policy.misses:
                logger.warning("[{}] {} of {} scripted steps never matched",
                               script["name"], len(policy.misses), len(script["steps"]))
    finally:
        env.close()

    meta = recorder.close({"episode_labels": episode_labels, "script_fidelity": fidelity})
    return {"meta": meta, "fidelity": fidelity, "dir": recorder.root}


def capture_random(base_url: str, args: argparse.Namespace) -> dict:
    """Masked-random exploration — realistic traffic, no per-step ground truth."""
    recorder = TraceRecorder(
        args.out,
        f"{args.run_id}-random",
        save_screenshots=args.screenshots,
        meta={"corpus": "random", "target": base_url, "seed": args.seed,
              "policy": "RandomPolicy(valid_only=True)",
              "purpose": "false-positive rate under ordinary exploration"},
    )
    env = WebFunctionalEnv(base_url=base_url, max_steps=args.random_steps, headless=True)
    try:
        report = run_rollout(
            env,
            RandomPolicy(MAX_ACTIONS, seed=args.seed, valid_only=True),
            episodes=args.random_episodes,
            label="random-valid-only",
            seed=args.seed,
            recorder=recorder,
        )
    finally:
        env.close()
    logger.info("\n{}", report.summary())
    meta = recorder.close({"rollout": report.to_dict()})
    return {"meta": meta, "dir": recorder.root}


def main() -> None:
    args = parse_args()
    produced: list[dict] = []

    if args.base_url:
        # An external target has no answer key, so there is nothing for the scripted
        # corpus to be scripted against; forcing it here beats silently producing ten
        # episodes of NO_OP against selectors that do not exist.
        if args.only == "scripted":
            raise SystemExit("--only scripted is toy-site specific; use --only random with --base-url")
        logger.info("Capturing against external target {}", args.base_url)
        produced.append(capture_random(args.base_url, args))
    else:
        if not TOY_SITE.is_dir():
            raise SystemExit(f"Toy site fixture not found at {TOY_SITE}")
        with serve_directory(TOY_SITE) as origin:
            base_url = f"{origin}/index.html"
            logger.info("Toy validation site at {}", base_url)
            if args.only in ("scripted", "both"):
                produced.append(capture_scripted(base_url, args))
            if args.only in ("random", "both"):
                produced.append(capture_random(base_url, args))

    print("\n" + "=" * 78)
    print(f"Trace corpora — run {args.run_id}")
    print("=" * 78)
    for item in produced:
        summary = trace_dir_summary(item["dir"])
        meta = item["meta"]
        print(f"\n{meta['corpus']}: {item['dir']}")
        print(f"  steps / episodes    : {summary['steps']} / {summary['episodes']}")
        print(f"  distinct pages      : {meta['distinct_pages']}")
        print(f"  paths reached       : {', '.join(summary['distinct_paths'])}")
        print(f"  deterministic hits  : {summary['steps_with_triggers']} steps")
        print(f"  no visible change   : {summary['steps_with_no_visible_change']} steps")
        print(f"  action mix          : {summary['action_types']}")
        for entry in item.get("fidelity", []):
            status = "ok" if not entry["missed"] else f"MISSED {len(entry['missed'])}"
            print(f"    {entry['script']:<48} {entry['matched']}/{entry['steps']}  {status}")

    incomplete = [
        e["script"] for item in produced for e in item.get("fidelity", []) if e["missed"]
    ]
    if incomplete:
        print(f"\n!! {len(incomplete)} script(s) did not fully execute: {', '.join(incomplete)}")
        print("   Those bugs are NOT represented in the corpus; fix before scoring a judge.")
    print()

    index = {
        "run_id": args.run_id,
        "corpora": {item["meta"]["corpus"]: str(item["dir"].relative_to(REPO_ROOT)) for item in produced},
        "incomplete_scripts": incomplete,
    }
    (args.out / f"{args.run_id}-index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    logger.info("Wrote corpus index to {}", args.out / f"{args.run_id}-index.json")


if __name__ == "__main__":
    main()
