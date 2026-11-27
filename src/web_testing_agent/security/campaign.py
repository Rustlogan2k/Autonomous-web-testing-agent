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

Signed-in condition (`--auth`, for applications with a "login" macro in their spec): both
front ends get the same account. Katalyze runs the macro at every episode start (its usual
session bootstrap, as on Pagekit); for ZAP the same macro is replayed in a browser routed
through ZAP, and that HTTP session is made ZAP's active session, so its spiders and scanner
send it. The spec's "logout" pattern is kept out of ZAP's context, as ZAP's documentation
advises. An application's one-time "init" steps (e.g. creating DVWA's database) run first,
through ZAP, before ZAP's session is reset.

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


def site_root(url: str) -> str:
    """The application's origin, without a path: what "init" paths, ZAP's scope, its site
    tree, its alerts and the active scan are relative to. The start URL can be a page
    (bWAPP's is /login.php), so it is not a prefix of the application's other pages."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


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


# -- browser macros (application set-up and ZAP's sign-in) ------------------------------


def run_macro(page, steps: list[dict]) -> None:  # noqa: ANN001 - playwright Page
    """The sign-in macro format of the app specs (TYPE by selector or id, CLICK by text or
    selector, NO_OP), replayed with Playwright."""
    for step in steps:
        kind = step["type"]
        if kind == "NO_OP":
            page.wait_for_timeout(1000)
            continue
        if step.get("selector"):
            target = page.locator(step["selector"]).first
        elif step.get("id"):
            target = page.locator(f"#{step['id']}").first
        else:
            target = page.get_by_text(step["text"], exact=False).first
        if kind == "TYPE":
            target.fill(str(step.get("params", {}).get("value", "")))
        elif kind == "CLICK":
            target.click()
            page.wait_for_load_state("networkidle")
        else:
            raise ValueError(f"unsupported macro step {kind}")


def browse_through_zap(zap: Zap, url: str, steps: list[dict],
                       check_signed_in: bool = False) -> str:
    """Open `url` in a Chromium routed through ZAP, run `steps`, return the final URL.

    With `check_signed_in`, `steps` is a sign-in macro and it must work: its first field is
    on the page before and gone after, or a RuntimeError ends the run. A run whose sign-in
    failed would explore the sign-in page for its whole budget and still look like a result
    (it did, on bWAPP, before `site_root`)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(proxy={"server": zap.proxy}, ignore_https_errors=True)
        page = context.new_page()
        page.goto(url)
        page.wait_for_load_state("networkidle")
        selector = _login_field(steps) if check_signed_in else None
        field = None
        if selector is not None:
            field = page.locator(selector)
            if field.count() == 0:
                raise RuntimeError(f"no sign-in form at {page.url}: "
                                   f"{page.inner_text('body')[:200]!r}")
        run_macro(page, steps)
        if field is not None and field.count() > 0:
            raise RuntimeError(f"the sign-in did not leave the sign-in page ({page.url})")
        final = page.url
        browser.close()
    return final


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


def _login_field(steps: list[dict]) -> str | None:
    """The CSS selector of a sign-in macro's first field, the one the checks look for."""
    first = next((s for s in steps if s["type"] == "TYPE"), None)
    if first is None:
        return None
    return first.get("selector") or f'[id="{first["id"]}"]'


def check_explorer_signed_in(env, login: list[dict]) -> None:  # noqa: ANN001 - an env
    """Raise unless the explorer's own session bootstrap signs in.

    The explorer resolves a macro step against its action registry (`ScriptedPolicy`),
    not with the browser, and a step that resolves to nothing is only logged: on bWAPP a
    macro written as `input[name=login]` matched no registry action (the registry names
    that field `[id="login"]`), so every Katalyze run of run 45951931563 explored signed
    out while ZAP's own sign-in check, which uses the browser, passed. One reset before
    the hunt, with the same check as `browse_through_zap`: every step landed and the
    first field is gone."""
    env.reset()
    landed = getattr(env, "_bootstrap_steps", 0)
    field = _login_field(login)
    page = env._browser.page  # noqa: SLF001
    left = page is None or (field is not None and page.locator(field).count() > 0)
    if landed != len(login) or left:
        raise RuntimeError(
            f"the explorer's sign-in did not work: {landed}/{len(login)} macro steps "
            f"matched an action, now at {page.url if page else '?'}")


# The explorer's whole run happens on one worker thread (Playwright's sync API belongs to
# the thread that started it). A browser call that never returns (two of five bWAPP runs
# of run 45951931563 stopped logging mid-exploration and sat until the job's time limit)
# would otherwise take the run with it; past its budget plus this grace the explorer is
# abandoned, the run is marked `hung`, and the scan goes on over what ZAP recorded.
HANG_GRACE_S = 300


def run_abandoning_after(fn, timeout_s: float) -> bool:  # noqa: ANN001 - a callable
    """Run `fn` on a daemon thread; True if it was still running after `timeout_s` (it is
    then abandoned). An exception it raised is re-raised here."""
    import threading

    box: dict = {}

    def work() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=work, name="katalyze-explorer", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        import sys
        import traceback

        frame = sys._current_frames().get(worker.ident)  # noqa: SLF001 - diagnosis only
        stack = "".join(traceback.format_stack(frame)) if frame is not None else "?"
        logger.error("the explorer has not returned after {:.0f} s; abandoned. It was in:\n{}",
                     timeout_s, stack)
        return True
    if "error" in box:
        raise box["error"]
    return False


def explore_with_katalyze(zap: Zap, url: str, explore_s: float, arm: str, seed: int,
                          app: str, login: list[dict] | None = None) -> dict:
    from ..benchmark import make_env, run_hunt
    from ..hybrid.external import UNLIMITED, ExternalConfig, NullMonitor
    from ..hybrid.run import ARMS, HybridJob, _policy, _settings

    started = time.monotonic()
    box: dict = {}

    def explore() -> None:
        config = ExternalConfig(time_budget_s=explore_s)
        hybrid = config.hybrid()
        job = HybridJob(arm, app, seed, build="external")
        policy, _ = _policy(job, app, hybrid)
        env = make_env(url, _settings(hybrid, ARMS[arm], list(login or []), None),
                       setup_actions=list(login) if login else None)
        env._browser.proxy = zap.proxy  # noqa: SLF001 - the session's documented option
        try:
            if login:
                check_explorer_signed_in(env, login)
            left = explore_s - (time.monotonic() - started)
            box["hunt"] = run_hunt(env, policy, NullMonitor(), budget=UNLIMITED,
                                   deadline_s=left).to_dict()
        finally:
            env.close()

    hung = run_abandoning_after(explore, explore_s + HANG_GRACE_S)
    navigation = {"front": "katalyze", "arm": arm, "hung": hung}
    hunt = box.get("hunt") or {}
    navigation.update({
        "explore_s": round(time.monotonic() - started, 1),
        "steps": hunt.get("steps"), "episodes": hunt.get("episodes"),
        "unique_states": hunt.get("unique_states")})
    return navigation


# -- one run ---------------------------------------------------------------------------


def run(app_name: str, front: str, seed: int, explore_min: float, scan_min: int, zap: Zap,
        arm: str = "hybrid_final_v5", auth: bool = False) -> dict:
    app = load_app(app_name)
    url = app["url"]
    base = site_root(url)
    site = urlsplit(url).netloc
    if urlsplit(url).port is None:
        site += ":443" if urlsplit(url).scheme == "https" else ":80"
    # An application whose pages all sit behind a sign-in is always run signed in.
    auth = auth or app.get("condition") == "auth"
    login = app.get("login") if auth else None
    if auth and not login:
        raise SystemExit(f"{app_name} has no login macro for the signed-in condition")
    version = zap.wait_ready()
    for step in app.get("init") or []:
        browse_through_zap(zap, base + step["path"], step.get("macro") or [])
    if login:
        # Before ZAP's session is reset, so this check leaves nothing in its site tree.
        browse_through_zap(zap, url, login, check_signed_in=True)
    zap.new_session()
    zap.include_in_scope(f"{base}.*")
    if app.get("logout"):
        zap.exclude_from_scope(app["logout"])
    started = time.time()
    session = None
    if front == "zap":
        if login:
            browse_through_zap(zap, url, login, check_signed_in=True)
            session = zap.activate_latest_session(site)
            if session is None:
                raise RuntimeError("the sign-in through ZAP left no HTTP session to activate")
        navigation = explore_with_zap(zap, url, explore_min * 60)
    elif front == "katalyze":
        navigation = explore_with_katalyze(zap, url, explore_min * 60, arm, seed, app_name,
                                           login=login)
    else:
        raise SystemExit(f"unknown front end {front}")
    navigation["auth"] = bool(login)
    navigation["zap_session"] = session
    left = zap.wait_passive()
    urls = zap.urls(base)
    paths = sorted({_path(u) for u in urls})
    documented = {p for v in app["vulnerabilities"] for p in v["paths"]}
    reached = {
        "urls": len(urls),
        "messages": zap.messages_count(base),
        "params": len(zap.params(site)),
        "paths": len(paths),
        # Descriptive, added after run 45951931563 (both front ends 0 of 60 on bWAPP and
        # nothing recorded to say which pages either had reached): the site tree itself,
        # and how many of the paths the documented vulnerabilities sit on it holds.
        "documented_paths_reached": len(documented & set(paths)),
        "documented_paths": len(documented),
        "path_list": paths,
    }
    scan_started = time.monotonic()
    if url != base + "/":
        # The scan starts from the site root and ZAP scans only what its tree holds; a start
        # page such as /login.php leaves the root unrequested. Identical for both front
        # ends, and after `reached` was measured.
        zap.call("core", "action", "accessUrl", url=base + "/", followRedirects="false")
    scan = zap.active_scan(base + "/", max_minutes=scan_min, context_id=zap.context_id())
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
    parser.add_argument("--auth", action="store_true",
                        help="both front ends signed in with the spec's account")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    record = run(args.app, args.front, args.seed, args.explore_min, args.scan_min,
                 Zap(proxy=args.zap), arm=args.arm, auth=args.auth)
    args.out.mkdir(parents=True, exist_ok=True)
    condition = "auth" if record["navigation"]["auth"] else "anon"
    name = f"sec__{args.app}__{args.front}__{condition}__{args.seed}.json"
    (args.out / name).write_text(json.dumps(record, indent=1), encoding="utf-8")
    s = record["score"]
    logger.info("{} {} seed {}: {} of {} detectable found ({} of {} documented)", args.app,
                args.front, args.seed, s["found_detectable"], s["detectable"], s["found"],
                s["documented"])
    if record["navigation"].get("hung"):
        # The abandoned explorer thread and its browser driver would hold the process
        # open; the record is written, so leave without waiting for them.
        import os
        import sys

        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
