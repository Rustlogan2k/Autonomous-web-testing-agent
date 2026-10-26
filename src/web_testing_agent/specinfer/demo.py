"""Run the tester with the inferred-specification oracle on any URL and write a summary.

    python -m web_testing_agent.specinfer.demo --name saucedemo-problem \\
        --url https://www.saucedemo.com/ --setup '[{"type":"TYPE","id":"user-name","params":{"value":"problem_user"}}, ...]'

Writes `<out>/session.json` (everything) and `<out>/summary.md` (rules, findings, verdicts).
Only point it at applications you are allowed to test.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..tester.session import SessionSettings, run_session, save_result


def _steps(trace: list[dict]) -> str:
    from .monitor import _describe

    return " → ".join(_describe(s) for s in trace) or "(no steps)"


def summary_markdown(name: str, url: str, result) -> str:  # noqa: ANN001
    spec = result.spec or {}
    lines = [f"# Inferred-specification run: {name}", "", f"Target: {url}  ", f"Interactions: {result.hunt.get('steps')}  ",
             f"Inference: {spec.get('inference')}", "", "## Rules the LLM proposed", "",
             "| Rule | Kind | Expected behaviour | Applied | Violated |", "|---|---|---|---|---|"]
    for r in spec.get("rules", []):
        lines.append(f"| {r['id']} | {r['kind']} | {r['description']} | {r['applied']} | {r['violated']} |")
    if spec.get("dropped_rules"):
        lines += ["", "Dropped as malformed: " + "; ".join(spec["dropped_rules"])]
    lines += ["", "## Findings", ""]
    for f in spec.get("findings", []):
        j = f.get("judgement") or {}
        lines += [f"### {j.get('title') or f['description']}  ({f['status']})", "",
                  f"- Rule {f['rule_id']} ({f['kind']}) on `{f['page']}`: {f['description']}",
                  f"- Observed: `{json.dumps(f['details'])[:400]}`",
                  f"- Replays: {f['replays']}; minimal reproduction ({len(f['minimized_trace'])} steps): {_steps(f['minimized_trace'])}",
                  f"- Judge: {j.get('verdict', '-')} ({j.get('confidence', '-')}): {j.get('explanation', '')}", ""]
    triggers = result.triggers or {}
    lines += ["## Generic triggers (no specification needed)", "",
              f"{triggers.get('verified', 0)} verified of {triggers.get('candidates', 0)} candidates.", ""]
    for f in triggers.get("findings", []):
        if f.get("verified"):
            lines.append(f"- {f['kind']} on `{f['page']}`: {f['detail']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--setup", default="[]", help="sign-in macro as JSON (scripted steps)")
    parser.add_argument("--budget", type=int, default=600)
    parser.add_argument("--warmup", type=int, default=150)
    parser.add_argument("--agent", default="hybrid_final")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=Path("runs/spec-demo"))
    args = parser.parse_args(argv)
    settings = SessionSettings(budget=args.budget, agent=args.agent, seed=args.seed, llm_reward=False,
                               llm_review=False, spec_oracle=True, spec_warmup=args.warmup,
                               setup_actions=tuple(json.loads(args.setup)))
    out = args.out / args.name
    out.mkdir(parents=True, exist_ok=True)
    result = run_session(args.url, settings, app_name=args.name, out_dir=out,
                         on_event=lambda kind, data: print(f"[{kind}] {data.get('message', '')}", flush=True)
                         if kind in ("stage", "warning") else None)
    save_result(result, out)
    (out / "summary.md").write_text(summary_markdown(args.name, args.url, result), encoding="utf-8")
    spec = result.spec or {}
    print(f"rules={len(spec.get('rules', []))} candidates={spec.get('candidates')} verified={spec.get('verified')} "
          f"rejected={spec.get('rejected')} not_reproduced={spec.get('not_reproduced')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
