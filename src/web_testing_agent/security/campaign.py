"""One run of the security study: a navigation front end, then ZAP's scan, then scoring.

    python -m web_testing_agent.security.campaign --app wackopicko --front katalyze \\
        --seed 0 --explore-min 20 --scan-min 40 --zap http://127.0.0.1:18090 \\
        --out runs/sec/results

The application and a ZAP daemon run in containers on one Docker network (the application
is reachable from ZAP by its container name, e.g. http://wp/); the browser reaches the
application only through ZAP's proxy, so ZAP records every request the front end makes.

Front ends (equal navigation budget `--explore-min`):
  zap       ZAP's traditional spider (half the budget at most) then its AJAX spider (the rest)
            on the application's start URL: how ZAP is normally run, unauthenticated.
  katalyze  the tester's explorer (`--arm`, default hybrid_final_v5, unchanged) from the same
            start URL, unauthenticated, its browser routed through ZAP.

Then, identically for both: ZAP's passive scan is drained and its active scan (default
policy) runs over everything in scope that the front end left in ZAP's site tree, for at
most `--scan-min`. Detection is ZAP's alone. Scoring uses configs/security_apps.json:
a documented vulnerability is found when an alert (confidence above "False Positive")
carries one of its CWEs on one of its paths (and its parameter, when one is documented).
Alerts of the documented classes that match no documented vulnerability are kept for
manual triage: each is a false positive or an undocumented vulnerability, decided by hand
and reported as such.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from ..utils.logging import get_logger
from .zap import Zap

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[3]
APPS_FILE = ROOT / "configs" / "security_apps.json"


def load_app(name: str, apps_file: Path = APPS_FILE) -> dict:
    apps = json.loads(apps_file.read_text(encoding="utf-8"))
    if name not in apps or name.startswith("_"):
        raise SystemExit(f"{name} is not in {apps_file}")
    return apps[name]


# -- scoring ---------------------------------------------------------------------------


def _cwe(alert: dict) -> int:
    try:
        return int(alert.get("cweid") or -1)
    except ValueError:
        return -1


def _path(url: str) -> str:
    return urlsplit(url).path or "/"


def score(alerts: list[dict], vulnerabilities: list[dict]) -> dict:
    """Which documented vulnerabilities the alerts find, and the alerts left over."""
    alerts = [a for a in alerts if a.get("confidence") != "False Positive"]
    found, used = {}, set()
    for vuln in vulnerabilities:
        hits = [i for i, a in enumerate(alerts)
                if _cwe(a) in vuln["cwe"] and _path(a.get("url", "")) in vuln["paths"]
                and (not vuln.get("param") or a.get("param") == vuln["param"])]
        used.update(hits)
        found[vuln["id"]] = {
            "found": bool(hits), "detectable": vuln["detectable"], "title": vuln["title"],
            "alerts": [{k: alerts[i].get(k) for k in ("pluginId", "alert", "risk",
                                                       "confidence", "url", "param")}
                       for i in hits[:5]],
        }
    classes = {c for v in vulnerabilities for c in v["cwe"]}
    unmatched = [
        {k: a.get(k) for k in ("pluginId", "alert", "risk", "confidence", "cweid", "url",
                               "param", "method")}
        for i, a in enumerate(alerts) if i not in used and _cwe(a) in classes
    ]
    detectable = [v for v in vulnerabilities if v["detectable"]]
    return {
        "found": sum(f["found"] for f in found.values()),
        "found_detectable": sum(found[v["id"]]["found"] for v in detectable),
        "detectable": len(detectable),
        "documented": len(vulnerabilities),
        "by_vulnerability": found,
        "unmatched_in_documented_classes": unmatched,
        "alerts_by_risk": dict(Counter(a.get("risk") for a in alerts)),
        "alerts_by_rule": dict(Counter(f"{a.get('pluginId')} {a.get('alert')}"
                                       for a in alerts)),
    }


# -- front ends ------------------------------------------------------------------------


def explore_with_zap(zap: Zap, url: str, explore_s: float) -> dict:
    started = time.monotonic()
    minutes = max(1, int(explore_s // 60))
    scan = zap.spider(url, max_minutes=max(1, minutes // 2))
    while not zap.spider_done(scan) and time.monotonic() - started < explore_s / 2:
        time.sleep(5)
    spider_s = time.monotonic() - started
    left = explore_s - spider_s
    if left >= 60:
        zap.ajax_spider(url, max_minutes=max(1, int(left // 60)))
        time.sleep(5)
        while zap.ajax_spider_running() and time.monotonic() - started < explore_s:
            time.sleep(5)
    zap.stop_crawlers()
    return {"front": "zap", "spider_s": round(spider_s, 1),
            "explore_s": round(time.monotonic() - started, 1)}


def explore_with_katalyze(zap: Zap, url: str, explore_s: float, arm: str, seed: int,
                          app: str) -> dict:
    from ..benchmark import make_env, run_hunt
    from ..hybrid.external import UNLIMITED, ExternalConfig, NullMonitor
    from ..hybrid.run import ARMS, HybridJob, _policy, _settings

    config = ExternalConfig(time_budget_s=explore_s)
    hybrid = config.hybrid()
    job = HybridJob(arm, app, seed, build="external")
    policy, _ = _policy(job, app, hybrid)
    env = make_env(url, _settings(hybrid, ARMS[arm], [], None))
    env._browser.proxy = zap.proxy  # noqa: SLF001 - the session's documented option
    started = time.monotonic()
    try:
        result = run_hunt(env, policy, NullMonitor(), budget=UNLIMITED, deadline_s=explore_s)
    finally:
        env.close()
    hunt = result.to_dict()
    return {"front": "katalyze", "arm": arm, "explore_s": round(time.monotonic() - started, 1),
            "steps": hunt.get("steps"), "episodes": hunt.get("episodes"),
            "unique_states": hunt.get("unique_states")}


# -- one run ---------------------------------------------------------------------------


def run(app_name: str, front: str, seed: int, explore_min: float, scan_min: int, zap: Zap,
        arm: str = "hybrid_final_v5") -> dict:
    app = load_app(app_name)
    url = app["url"]
    base = url.rstrip("/")
    site = urlsplit(url).netloc
    version = zap.wait_ready()
    zap.new_session()
    zap.include_in_scope(f"{base}.*")
    started = time.time()
    if front == "zap":
        navigation = explore_with_zap(zap, url, explore_min * 60)
    elif front == "katalyze":
        navigation = explore_with_katalyze(zap, url, explore_min * 60, arm, seed, app_name)
    else:
        raise SystemExit(f"unknown front end {front}")
    left = zap.wait_passive()
    reached = {
        "urls": len(zap.urls(base)),
        "messages": zap.messages_count(base),
        "params": len(zap.params(site)),
        "paths": len({_path(u) for u in zap.urls(base)}),
    }
    scan_started = time.monotonic()
    scan = zap.active_scan(url, max_minutes=scan_min, context_id=zap.context_id())
    while zap.active_scan_progress(scan) < 100:
        if time.monotonic() - scan_started > scan_min * 60 + 300:
            logger.warning("active scan still running after its budget; stopping")
            zap.call("ascan", "action", "stopAllScans")
            break
        time.sleep(10)
    zap.wait_passive()
    alerts = zap.alerts(base)
    return {
        "app": app_name, "front": front, "seed": seed, "zap_version": version,
        "explore_min": explore_min, "scan_min": scan_min, "started": started,
        "navigation": navigation, "passive_left_before_scan": left,
        "reached_before_scan": reached,
        "scan_s": round(time.monotonic() - scan_started, 1),
        "score": score(alerts, app["vulnerabilities"]),
        "alerts": len(alerts),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", required=True)
    parser.add_argument("--front", choices=("zap", "katalyze"), required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--arm", default="hybrid_final_v5")
    parser.add_argument("--explore-min", type=float, default=20)
    parser.add_argument("--scan-min", type=int, default=40)
    parser.add_argument("--zap", default="http://127.0.0.1:18090")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    record = run(args.app, args.front, args.seed, args.explore_min, args.scan_min,
                 Zap(proxy=args.zap), arm=args.arm)
    args.out.mkdir(parents=True, exist_ok=True)
    name = f"sec__{args.app}__{args.front}__{args.seed}.json"
    (args.out / name).write_text(json.dumps(record, indent=1), encoding="utf-8")
    s = record["score"]
    logger.info("{} {} seed {}: {} of {} detectable found ({} of {} documented)", args.app,
                args.front, args.seed, s["found_detectable"], s["detectable"], s["found"],
                s["documented"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
