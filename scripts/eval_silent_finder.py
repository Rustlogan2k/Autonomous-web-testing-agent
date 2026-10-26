"""Evaluation of the silent-bug finder (LLM-inferred rules) on applications with known faults.

POST-HOC. The finder was built after every pre-registered evaluation and has never run on the
validation applications. Each application runs twice with its seeded checks HIDDEN, so only
generic triggers and the finder's own rules can find anything, as on an unknown application:

* faulty build: each finding's minimal reproduction is replayed on the faulty build and on
  every per-fault control build; it rediscovers fault X when the violation happens on the
  faulty build and not on the build without X's patch;
* clean build: every verified finding is a false finding (or a real, unseeded bug, which a
  person must check).

    python scripts/eval_silent_finder.py --apps dutyday larder markbook --budget 800 --out runs/spec-eval
    python scripts/eval_silent_finder.py --analyze runs/spec-eval
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))


def _env_factory(url: str):  # noqa: ANN202
    from web_testing_agent.benchmark.suite import EnvSettings, make_env
    from web_testing_agent.envs.action_registry import EXTENDED_OPTIONS

    settings = EnvSettings(max_steps=40, settle_quiet_ms=80,
                           registry=replace(EXTENDED_OPTIONS, custom_clickables=True, listener_clickables=True))
    return lambda: make_env(url, settings, navigation_needs_change=True, max_steps=120)


def _attribute(result, urls: dict, faults: list[str]) -> list[dict]:  # noqa: ANN001
    """Which seeded fault each minimised finding depends on (control-build replay)."""
    from web_testing_agent.specinfer.monitor import SpecReplayer
    from web_testing_agent.specinfer.rules import Rule, violations
    from web_testing_agent.tester.triggers import TriggerReplayer

    rules = {r["id"]: Rule(id=r["id"], kind=r["kind"], description=r["description"], spec=r["spec"],
                           rationale=r.get("rationale", "")) for r in (result.spec or {}).get("rules", [])}
    out = []
    for f in (result.spec or {}).get("findings", []):
        if not f.get("minimized_trace") or f["rule_id"] not in rules:
            continue
        rule = rules[f["rule_id"]]

        def violated(url: str, trace=f["minimized_trace"], rule=rule) -> bool:  # noqa: ANN001
            return bool(violations(rule, SpecReplayer(_env_factory(url)).run(trace)))

        on_faulty = violated(urls["buggy"])
        caused_by = [x for x in faults if on_faulty and not violated(urls[f"without-{x}"])]
        out.append({"source": "rule", "rule_id": f["rule_id"], "kind": f["kind"], "status": f["status"],
                    "description": f["description"], "verdict": (f.get("judgement") or {}).get("verdict"),
                    "reproduces_on_faulty": on_faulty, "caused_by": caused_by})
    for t in (result.triggers or {}).get("findings", []):
        if not t.get("verified"):
            continue
        identity = (t["kind"], t["page"], t["detail"])

        def fired(url: str, trace=t["minimized_trace"], identity=identity) -> bool:  # noqa: ANN001
            return identity in TriggerReplayer(_env_factory(url)).run(trace)["fired"]

        on_faulty = fired(urls["buggy"])
        caused_by = [x for x in faults if on_faulty and not fired(urls[f"without-{x}"])]
        out.append({"source": "trigger", "kind": t["kind"], "detail": t["detail"], "status": "verified",
                    "reproduces_on_faulty": on_faulty, "caused_by": caused_by})
    return out


def run_job(app_id: str, build: str, budget: int, out: Path) -> None:
    from web_testing_agent.hybrid.suite import VALIDATION_APPS, load_suite_app, serve_suite_app
    from web_testing_agent.tester.session import SessionSettings, run_session, save_result

    app = load_suite_app(app_id)
    faults = [p.fault_id for p in app.patches]
    settings = SessionSettings(budget=budget, agent="hybrid_final", llm_reward=False, llm_review=False,
                               spec_oracle=True, spec_warmup=budget // 2, settle_quiet_ms=80,
                               spec_intent_dir=str(VALIDATION_APPS / app_id))
    target = out / app_id / build
    target.mkdir(parents=True, exist_ok=True)
    started = time.time()
    # Seeded checks are hidden: no seeded_app / suite_app is given to the session.
    with serve_suite_app(app, controls=(build == "buggy")) as urls:
        result = run_session(urls[build], settings, app_name=app_id, out_dir=target)
        save_result(result, target)
        attribution = _attribute(result, urls, faults) if build == "buggy" else []
    (target / "evaluation.json").write_text(json.dumps({
        "app": app_id, "build": build, "budget": budget, "faults": faults, "attribution": attribution,
        "wall_clock_s": round(time.time() - started, 1)}, indent=2), encoding="utf-8")


def analyze(out: Path) -> dict:
    apps = sorted(p.name for p in out.iterdir() if p.is_dir())
    rows, total = [], {"faults": 0, "rule_found": 0, "trigger_found": 0, "any_found": 0, "judged_away": 0,
                       "clean_rule_findings": 0, "clean_trigger_findings": 0, "needs_review": 0}
    for app in apps:
        ev = json.loads((out / app / "buggy" / "evaluation.json").read_text(encoding="utf-8"))
        sess = json.loads((out / app / "buggy" / "session.json").read_text(encoding="utf-8"))
        clean = json.loads((out / app / "clean" / "session.json").read_text(encoding="utf-8")) \
            if (out / app / "clean" / "session.json").exists() else {}
        att = ev["attribution"]
        rule_found = sorted({x for a in att if a["source"] == "rule" and a["status"] == "verified" for x in a["caused_by"]})
        trig_found = sorted({x for a in att if a["source"] == "trigger" for x in a["caused_by"]})
        judged_away = sorted({x for a in att if a["source"] == "rule" and a["status"] != "verified" for x in a["caused_by"]}
                             - set(rule_found))
        spec = sess.get("spec") or {}
        cspec = clean.get("spec") or {}
        row = {
            "app": app, "faults": ev["faults"], "found_by_rules": rule_found, "found_by_triggers": trig_found,
            "found_any": sorted(set(rule_found) | set(trig_found)), "real_fault_judged_not_a_bug": judged_away,
            "verified_rule_findings": sum(1 for a in att if a["source"] == "rule" and a["status"] == "verified"),
            "verified_rule_findings_matching_a_fault": sum(1 for a in att if a["source"] == "rule"
                                                           and a["status"] == "verified" and a["caused_by"]),
            "needs_review": spec.get("needs_review", 0),
            "contradicted_rule_findings": spec.get("contradicted", 0),
            "rules": len(spec.get("rules", [])),
            "rules_exercised": sum(1 for r in spec.get("rules", []) if r.get("applied")),
            "rule_rounds": len(spec.get("inferences", [])),
            "intent_files": spec.get("intent_files", []),
            "clean_verified_rule_findings": cspec.get("verified", 0),
            "clean_verified_triggers": (clean.get("triggers") or {}).get("verified", 0),
            "clean_findings": [f.get("description") for f in cspec.get("findings", []) if f.get("verified")],
            "minutes": round((ev["wall_clock_s"]) / 60, 1),
        }
        rows.append(row)
        total["faults"] += len(ev["faults"])
        total["rule_found"] += len(rule_found)
        total["trigger_found"] += len(trig_found)
        total["any_found"] += len(row["found_any"])
        total["judged_away"] += len(judged_away)
        total["needs_review"] += row["needs_review"]
        total["clean_rule_findings"] += row["clean_verified_rule_findings"]
        total["clean_trigger_findings"] += row["clean_verified_triggers"]
    summary = {"apps": rows, "total": total}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    lines = ["# Silent-bug finder evaluation (post-hoc)", "",
             "Seeded checks hidden; findings attributed by replay on per-fault control builds.", "",
             "| App | Faults | Found by LLM rules | Found by generic triggers | Real fault judged not a bug | "
             "Verified rule findings (matching a fault) | Needs review | Rules (exercised) | Clean-build verified findings |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['app']} | {len(r['faults'])} | {', '.join(r['found_by_rules']) or '-'} | "
                     f"{', '.join(r['found_by_triggers']) or '-'} | {', '.join(r['real_fault_judged_not_a_bug']) or '-'} | "
                     f"{r['verified_rule_findings']} ({r['verified_rule_findings_matching_a_fault']}) | {r['needs_review']} | "
                     f"{r['rules']} ({r['rules_exercised']}) | {r['clean_verified_rule_findings']} rules, "
                     f"{r['clean_verified_triggers']} triggers |")
    t = total
    lines += ["", f"**Total:** {t['any_found']} of {t['faults']} seeded faults rediscovered "
                  f"({t['rule_found']} by LLM rules, {t['trigger_found']} by generic triggers); "
                  f"{t['judged_away']} real faults judged not a bug; {t['needs_review']} sent to review; "
                  f"clean builds: {t['clean_rule_findings']} rule findings, {t['clean_trigger_findings']} trigger findings."]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apps", nargs="*", default=["dutyday", "larder", "markbook"])
    parser.add_argument("--budget", type=int, default=800)
    parser.add_argument("--out", type=Path, default=REPO / "runs" / "spec-eval")
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--job", help="(internal) app:build")
    parser.add_argument("--analyze", type=Path)
    args = parser.parse_args()
    if args.analyze:
        analyze(args.analyze)
        return 0
    if args.job:
        app, build = args.job.split(":")
        run_job(app, build, args.budget, args.out)
        return 0
    jobs = [f"{a}:{b}" for a in args.apps for b in ("buggy", "clean")]
    running: list[tuple[str, subprocess.Popen]] = []
    args.out.mkdir(parents=True, exist_ok=True)
    while jobs or running:
        while jobs and len(running) < args.parallel:
            job = jobs.pop(0)
            log = open(args.out / f"{job.replace(':', '-')}.log", "w", encoding="utf-8")  # noqa: SIM115
            cmd = [sys.executable, __file__, "--job", job, "--budget", str(args.budget), "--out", str(args.out)]
            running.append((job, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)))
            print(f"started {job}", flush=True)
        time.sleep(10)
        for job, proc in list(running):
            if proc.poll() is not None:
                print(f"finished {job} (exit {proc.returncode})", flush=True)
                running.remove((job, proc))
    analyze(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
