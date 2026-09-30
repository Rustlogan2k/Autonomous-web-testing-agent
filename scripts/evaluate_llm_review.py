"""Precision and recall of the LLM outcome review against the benchmark's known faults.

    python scripts/evaluate_llm_review.py --out reports/llm_review_validation

The outcome review (`tester.review.OutcomeReviewer`) is the language model acting as a
*checker*: shown the page a trace reached and the actions that reached it, does it say
something is functionally wrong? The product reports its observations as unverified; this
script measures how far they can be trusted, on cases whose truth is known:

* **positive** -- each seeded fault's reachability witness replayed on the **faulty**
  build: the page shows the fault, and a correct checker raises a concern;
* **negative** -- the same witness on the **clean** build (identical actions, correct page),
  and every benign trace on both builds: a correct checker raises none.

The witness-on-clean negative is the sharp one: the actions are identical to the positive
case, so a concern there is a concern about the actions, not about the application.

Reported per application and overall: true/false positives and negatives, precision,
recall, and how many concerns were discarded as ungrounded (quote not on the page).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from web_testing_agent.benchmark import EnvSettings, discover, make_env, serve_app  # noqa: E402
from web_testing_agent.evaluation import ScriptedPolicy  # noqa: E402
from web_testing_agent.oracle import entered_value, replay_step_of  # noqa: E402
from web_testing_agent.tester.review import OutcomeReviewer  # noqa: E402


def walk(url: str, trace: list[dict], settings: EnvSettings) -> dict:
    env = make_env(url, settings, max_steps=120)
    policy = ScriptedPolicy(list(trace))
    observation, info = env.reset()
    steps, entered = [], {}
    try:
        while not policy.finished:
            available = list(info.get("action_specs") or [])
            observation, _r, _te, _tr, info = env.step(policy.act(observation, info))
            spec = info.get("action_spec")
            step = replay_step_of(spec, available)
            if step:
                steps.append(step)
            entered.update(entered_value(spec, info.get("exec_info")))
    finally:
        env.close()
    page = info.get("page") or {}
    return {"url": page.get("url", ""), "html": page.get("html", ""),
            "visible_text": page.get("visible_text"), "trace": steps, "entered": entered}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    reviewer = OutcomeReviewer()
    settings = EnvSettings(max_steps=40, settle_quiet_ms=80)
    cases = []
    for app in discover():
        with serve_app(app) as urls:
            for fault in app.manifest.faults:
                for build, truth in (("buggy", True), ("clean", False)):
                    state = walk(urls[build], list(fault.trigger_trace), settings)
                    review = reviewer.review(state["url"], state["html"], state["visible_text"],
                                             state["trace"], state["entered"])
                    cases.append({"app": app.app_id, "case": f"{fault.fault_id} witness on {build}",
                                  "fault_present": truth, **review.to_dict()})
            for number, trace in enumerate(app.manifest.benign_traces):
                for build in ("buggy", "clean"):
                    state = walk(urls[build], list(trace), settings)
                    review = reviewer.review(state["url"], state["html"], state["visible_text"],
                                             state["trace"], state["entered"])
                    cases.append({"app": app.app_id, "case": f"benign {number} on {build}",
                                  "fault_present": False, **review.to_dict()})
        print(f"{app.app_id}: {sum(c['app'] == app.app_id for c in cases)} cases", flush=True)

    def tally(rows: list[dict]) -> dict:
        tp = sum(r["fault_present"] and r["concern"] for r in rows)
        fp = sum((not r["fault_present"]) and r["concern"] for r in rows)
        fn = sum(r["fault_present"] and not r["concern"] for r in rows)
        tn = sum((not r["fault_present"]) and not r["concern"] for r in rows)
        return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                "recall": round(tp / (tp + fn), 3) if tp + fn else None,
                "ungrounded_discarded": sum(bool(r["evidence"]) and not r["grounded"] for r in rows),
                "errors": sum(bool(r["error"]) for r in rows)}

    for row in cases:
        row.pop("trace", None)
    payload = {"model": reviewer.model, "overall": tally(cases),
               "per_app": {a: tally([c for c in cases if c["app"] == a])
                           for a in sorted({c["app"] for c in cases})},
               "cases": cases}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload["overall"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
