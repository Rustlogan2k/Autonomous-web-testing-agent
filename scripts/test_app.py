"""Test a web application with the learning tester and write a readable report.

    python scripts/test_app.py --url https://example.org/            # a live site
    python scripts/test_app.py --site "demo app/www"                 # a static site folder
    python scripts/test_app.py --site mysite.zip                     # ... or a zip of one
    python scripts/test_app.py --repo path/to/repo                   # Dockerfile repository
    python scripts/test_app.py --benchmark booking_desk              # a benchmark app

Options worth knowing: `--budget` (browser interactions, default 600), `--agent rl|search|
random`, `--no-llm` (explore without the LLM reward and skip the LLM review), and
`--login-steps FILE` (a JSON list of scripted steps run at the start of every episode, for
applications behind a login).

Writes `session.json`, `report.md`, `report.html`, `reward_log.jsonl` and `evidence/` into
`--out` (default `reports/sessions/<name>-<timestamp>/`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from web_testing_agent.tester import SessionSettings, run_session  # noqa: E402
from web_testing_agent.tester.report import write_report  # noqa: E402
from web_testing_agent.tester.targets import provide  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--url")
    target.add_argument("--site", type=Path)
    target.add_argument("--repo", type=Path)
    target.add_argument("--benchmark")
    parser.add_argument("--budget", type=int, default=600)
    parser.add_argument("--agent", choices=["rl", "search", "random"], default="rl")
    parser.add_argument("--checkpoint", default="")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--settle-quiet-ms", type=int, default=None)
    parser.add_argument("--login-steps", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    kind, value = next((k, v) for k, v in (("url", args.url), ("site", args.site),
                                           ("repo", args.repo), ("benchmark", args.benchmark))
                       if v is not None)
    setup = tuple(json.loads(args.login_steps.read_text(encoding="utf-8"))) if args.login_steps else ()
    settings = SessionSettings(
        budget=args.budget, seed=args.seed, agent=args.agent, checkpoint=args.checkpoint,
        llm_reward=not args.no_llm, llm_review=not args.no_llm,
        settle_quiet_ms=args.settle_quiet_ms if args.settle_quiet_ms is not None
        else (80 if kind in ("site", "benchmark") else None),
        setup_actions=setup)

    def on_event(kind_: str, data: dict) -> None:
        if kind_ == "step":
            if data["step"] % 50 == 0:
                print(f"  step {data['step']:5d}  episode {data['episode']:3d}  {data['url'][-60:]}",
                      flush=True)
        else:
            print(f"[{kind_}] {data.get('message', data)}", flush=True)

    with provide(kind, str(value)) as app:
        name = "".join(c if c.isalnum() else "-" for c in app.name).strip("-")[:40] or "app"
        out = args.out or REPO_ROOT / "reports" / "sessions" / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
        print(f"Testing {app.name} at {app.url} ({kind}); budget {settings.budget}; report -> {out}")
        result = run_session(app.url, settings, app_name=app.name, out_dir=out,
                             seeded_app=app.seeded_app, clean_url=app.clean_url, control_urls=app.control_urls,
                             on_event=on_event)
    paths = write_report(result, out)
    from web_testing_agent.tester.report import verified_findings

    verified = len(verified_findings(result))
    print(f"\n{verified} verified finding(s); report: {paths['html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
