"""Browser checks, driven through the tester's own environment and action registry.

Every interaction here goes through `web_testing_agent`'s `WebFunctionalEnv` (extended
registry) and `ScriptedPolicy` — imported, never modified — so "reachable" means reachable
by the tester's own actions with its own generated values, not by a hand-written Playwright
script that could do things the tester cannot. The sign-in macro (suite v2) runs through the
env's own session bootstrap (`setup_actions`), exactly as the tester signs in. Only the
sign-in checks address pages directly (`Session.goto`): "this path is protected" and "this
role is refused here" are statements about addresses, not about what the UI links to.
"""

from __future__ import annotations

import hashlib
import time
from urllib.parse import urljoin, urlsplit

from web_testing_agent.benchmark.suite import EnvSettings, make_env
from web_testing_agent.envs.types import MAX_ACTIONS
from web_testing_agent.evaluation import ScriptedPolicy
from web_testing_agent.evaluation.rollout import RandomPolicy
from web_testing_agent.oracle import PlaywrightTrajectoryRunner, VerificationPipeline, verify_all, verify_fault
from web_testing_agent.oracle.replay import observed_record
from web_testing_agent.oracle.trajectory import build_trajectory, region_text, visible_text

from . import ext_oracle
from .db_checks import percentile
from .manifest import AppManifest

#: The tester treats a page as settled once its DOM has been quiet for `settle_quiet_ms`
#: (`envs/browser_session.py`); it does not wait for requests still in flight. An SPA whose
#: click awaits an API response before re-rendering is therefore recorded too early if the
#: response takes longer than the window. Measured on tidewater under CPU load: one replay
#: in two of a 6-step witness lost its final page at 150 ms. 400 ms gives these backend apps
#: headroom; the underlying limitation is the tester's, recorded for the product work.
SETTINGS = EnvSettings(max_steps=60, settle_quiet_ms=400, capture_screenshots=False)
REPLAY_MAX_STEPS = 120

_STATE_JS = ("async () => { const r = await fetch('/__wta/state', {credentials: 'same-origin'});"
             " return await r.json(); }")
_RESOURCES_JS = "() => performance.getEntriesByType('resource').map(e => e.name)"
#: plan §4 `responses_fast`: clean-walk responses (sign-in excepted) within these bounds.
RESPONSE_P95_MS = 150.0
RESPONSE_MAX_MS = 1000.0


class Session:
    """One fresh browser context on one variant: drive traces, read state, watch traffic.

    `login` is the app's sign-in macro (suite v2), run by the tester's own session bootstrap
    (`setup_actions`) before the first observation, as for any login-protected site.
    """

    def __init__(self, url: str, login: list[dict] | None = None) -> None:
        self.url = url
        self.origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        self.env = make_env(url, SETTINGS, max_steps=1000, setup_actions=list(login or []) or None)
        self.observation, self.info = self.env.reset()
        self.signed_in_steps = self.env._bootstrap_steps  # noqa: SLF001 - read-only
        self.records = [observed_record(self.info)]
        self.foreign: list[str] = []
        self.errors: list[str] = []
        #: (method, path, milliseconds) of every same-origin request that completed,
        #: measured by the browser (request start to the response's last byte).
        self.timings: list[tuple[str, str, float]] = []
        self._watch()

    def _watch(self) -> None:
        browser = self.env._browser  # noqa: SLF001 - read-only access to the live page
        context, page = browser.context, browser.page
        context.on("request", self._on_request)
        context.on("response", self._on_response)
        context.on("requestfinished", self._on_finished)
        page.on("pageerror", lambda exc: self.errors.append(f"pageerror: {exc}"))
        page.on("console", lambda m: self.errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        for name in page.evaluate(_RESOURCES_JS):
            self._check_url(name)

    def _check_url(self, url: str) -> None:
        if url.startswith(("data:", "blob:", "about:")):
            return
        if not url.startswith(self.origin):
            self.foreign.append(url)

    def _on_request(self, request) -> None:
        self._check_url(request.url)

    def _on_response(self, response) -> None:
        if response.url.startswith(self.origin) and response.status >= 400 \
                and "/__wta/" not in response.url:
            self.errors.append(f"HTTP {response.status} {response.url}")

    def _on_finished(self, request) -> None:
        if not request.url.startswith(self.origin) or "/__wta/" in request.url:
            return
        took = (request.timing or {}).get("responseEnd", -1)
        if took is not None and took >= 0:
            self.timings.append((request.method, urlsplit(request.url).path, round(took, 1)))

    @property
    def page(self):
        return self.env._browser.page  # noqa: SLF001

    def run(self, steps: list[dict]) -> int:
        """Execute `steps`; return how many could not be resolved (0 = all executed)."""
        policy = ScriptedPolicy(list(steps), name="e2e-harness")
        budget = max(len(steps), 1) * (policy.max_retries + 1)
        for _ in range(budget):
            if policy.finished:
                break
            available = list(self.info.get("action_specs") or [])
            index = policy.act(self.observation, self.info)
            self.observation, _r, _te, _tr, self.info = self.env.step(index)
            self.records.append(observed_record(self.info, self.info.get("action_spec"), available))
        return len(policy.misses) + (0 if policy.finished else 1)

    def state(self) -> dict:
        return self.page.evaluate(_STATE_JS)

    @property
    def path(self) -> str:
        return urlsplit(self.page.url).path

    def goto(self, path: str):
        """Open `path` on this origin in the live page and wait until it settles; return the
        main document's response (None for a same-document, hash-only move)."""
        response = self.page.goto(urljoin(self.origin + "/", path))
        self.env._browser.wait_settled()  # noqa: SLF001 - the tester's own settle rule
        return response

    def live_region(self, selector: str) -> str | None:
        """A region as the page shows it now (after `goto` or `sign_in`, which record no step)."""
        return region_text(self.page.content(), selector)

    def sign_in(self, steps: list[dict]) -> int:
        """Run `steps` through the env's own session bootstrap in this (already watched)
        browser context; return how many landed. The env's observation is not refreshed,
        so a check calls this last, or reads the page only through `live_region`/`state`."""
        self.env.setup_actions = list(steps)
        return self.env._run_setup_actions()  # noqa: SLF001 - the tester's sign-in, unchanged

    def html(self) -> str:
        return self.records[-1].get("html", "") or ""

    def region(self, selector: str) -> str | None:
        return region_text(self.html(), selector)

    def digests(self) -> list[str]:
        return [hashlib.sha256(visible_text(r.get("html", "") or "").encode()).hexdigest()[:16]
                for r in self.records]

    def close(self) -> None:
        self.env.close()


def _runner(url: str, login: list[dict] | None = None) -> PlaywrightTrajectoryRunner:
    setup = list(login or []) or None
    return PlaywrightTrajectoryRunner(
        lambda: make_env(url, SETTINGS, max_steps=REPLAY_MAX_STEPS, setup_actions=setup),
        max_steps=REPLAY_MAX_STEPS)


def _contains(text: str | None, wanted: str) -> bool:
    return text is not None and " ".join(wanted.split()).lower() in " ".join(text.split()).lower()


# -- workflows, state-dependent actions, decoys -------------------------------------------------


def check_workflows(app: AppManifest, url: str, label: str) -> list[dict]:
    regions = app.data["regions"]
    out = []
    for workflow in app.data["workflows"]:
        session = Session(url, app.login_steps)
        try:
            before = session.state()["sha"]
            misses = session.run(workflow["trace"])
            failed = [e for e in workflow["expect"]
                      if ("contains" in e and not _contains(session.region(regions[e["region"]]), e["contains"]))
                      or ("absent" in e and _contains(session.region(regions[e["region"]]) or "", e["absent"]))]
            after = session.state()["sha"]
            changed = before != after
            want_change = workflow.get("commits", True)
            out.append({
                "name": f"{workflow['name']} [{label}]", "steps": len(workflow["trace"]),
                "passed": misses == 0 and not failed and changed == want_change,
                "misses": misses,
                "failed_expectations": [
                    {**e, "observed": (session.region(regions[e["region"]]) or "<absent>")[:120]}
                    for e in failed],
                "committed_state_changed": changed, "expected_state_change": want_change,
                "errors": session.errors[:5], "foreign_requests": session.foreign[:5],
                "urls": sorted({r.get("url", "") for r in session.records if r.get("url")}),
            })
        finally:
            session.close()
    return out


def check_state_dependent(app: AppManifest, url: str) -> list[dict]:
    out = []
    for item in app.data["state_dependent"]:
        result = {"name": item["name"]}
        for side in ("useless", "useful"):
            session = Session(url, app.login_steps)
            try:
                prefix_misses = session.run(item[side]["trace"])
                before = session.state()["sha"]
                action_misses = session.run([item[side]["action"]])
                after = session.state()["sha"]
                result[side] = {"prefix_misses": prefix_misses, "action_misses": action_misses,
                                "state_changed": before != after}
            finally:
                session.close()
        result["passed"] = (result["useless"]["prefix_misses"] == 0
                            and result["useful"]["prefix_misses"] == 0
                            and result["useful"]["action_misses"] == 0
                            and not result["useless"]["state_changed"]
                            and result["useful"]["state_changed"])
        out.append(result)
    return out


def check_decoys(app: AppManifest, url: str) -> list[dict]:
    out = []
    for decoy in app.data["decoys"]:
        session = Session(url, app.login_steps)
        try:
            prefix_misses = session.run(decoy.get("trace", []))
            before = session.state()["sha"]
            decoy_misses = session.run([decoy["decoy"]])
            after = session.state()["sha"]
            out.append({"name": decoy["name"], "prefix_misses": prefix_misses,
                        "decoy_clicked": decoy_misses == 0, "committed_state_changed": before != after,
                        "passed": prefix_misses == 0 and decoy_misses == 0 and before == after})
        finally:
            session.close()
    return out


# -- sign-in (suite v2) ----------------------------------------------------------------------------


def check_login(app: AppManifest, url: str, seed_sha: str | None) -> dict:
    """The app is behind its sign-in page; the macro signs in and lands on the seed state; a
    wrong password is refused without an error status and without creating a session."""
    login = app.login
    steps = app.login_steps
    regions = app.data["regions"]
    sign_in_path = login["path"]

    anonymous = Session(url)
    try:
        landing = {"entry": anonymous.path}
        for path in login["protected"]:
            anonymous.goto(path)
            landing[path] = anonymous.path
        public = {}
        for path in login.get("public", []):
            response = anonymous.goto(path)
            public[path] = {"status": response.status if response else None,
                            "redirected": bool(response and response.request.redirected_from),
                            "path": anonymous.path}
        protected_errors = list(anonymous.errors)
    finally:
        anonymous.close()
    protected_ok = (all(where == sign_in_path for where in landing.values())
                    and not protected_errors
                    and all(p["status"] == 200 and not p["redirected"] and p["path"] == path
                            for path, p in public.items()))

    signed = Session(url, steps)
    try:
        completed = signed.signed_in_steps
        banner = signed.region(regions[login["signed_in_region"]])
        state = signed.state()
        signed_path = signed.path
    finally:
        signed.close()
    macro_ok = (completed == len(steps) and signed_path != sign_in_path
                and _contains(banner, app.macro_user["name"])
                and seed_sha is not None and state.get("sha") == seed_sha)

    wrong = [dict(step, params={**step["params"], "value": login["password_value"] + "-wrong"})
             if (step.get("params") or {}).get("value") == login["password_value"] else step
             for step in steps]
    refused = Session(url)  # watched from the first request, then signs in wrongly
    try:
        refused.sign_in(wrong)
        refusal = refused.live_region(regions[login["error_region"]])
        refused_state = refused.state()
        refused_path, refused_errors = refused.path, list(refused.errors)
    finally:
        refused.close()
    refused_ok = (refused_path == sign_in_path and bool((refusal or "").strip())
                  and not refused_errors and refused_state.get("session") is None)
    return {
        "login_protected": {"passed": protected_ok, "landing": landing, "public": public,
                            "errors": protected_errors[:5]},
        "login_macro_signs_in": {"passed": macro_ok, "steps_completed": completed,
                                 "steps": len(steps), "path": signed_path,
                                 "signed_in_as": (banner or "<absent>")[:120],
                                 "sha_equals_seed": state.get("sha") == seed_sha},
        "login_wrong_password_refused": {"passed": refused_ok, "path": refused_path,
                                         "error_region": (refusal or "<absent>")[:120],
                                         "session": refused_state.get("session"),
                                         "errors": refused_errors[:5]},
    }


def check_sign_out(app: AppManifest, url: str) -> dict:
    """Sign-out lands on the sign-in page and protects the app again; signing in again in the
    same browser context resumes the same data (the partition is kept, CONVENTIONS.md §3)."""
    login = app.login
    workflow = next(w for w in app.data["workflows"] if w.get("commits", True))
    session = Session(url, app.login_steps)
    try:
        misses = session.run(workflow["trace"])
        before = session.state()
        out_misses = session.run(login["sign_out"])
        landed = session.path
        signed_out = session.state()
        session.goto(app.data.get("entry", "/"))
        protected_again = session.path
        again = session.sign_in(app.login_steps)
        after = session.state()
        resumed_path, errors = session.path, list(session.errors)
    finally:
        session.close()
    passed = (misses == 0 and out_misses == 0 and landed == login["path"]
              and signed_out.get("user") is None and protected_again == login["path"]
              and again == len(app.login_steps) and resumed_path != login["path"]
              and before.get("sha") is not None and after.get("sha") == before.get("sha")
              and after.get("user") == app.macro_user["username"] and not errors)
    return {"passed": passed, "workflow": workflow["name"], "workflow_misses": misses,
            "sign_out_misses": out_misses, "landed_on": landed,
            "user_after_sign_out": signed_out.get("user"), "entry_lands_on": protected_again,
            "sign_in_again_steps": again, "sha_resumed": after.get("sha") == before.get("sha"),
            "errors": errors[:5]}


def check_breadth(app: AppManifest, url: str, workflows: list[dict]) -> dict:
    """Plan §8: every declared path view renders for its user (200, no error, not redirected
    away), every workflow view's URL pattern was visited by its passing workflow, and every
    workflow an entity operation or a pattern names passed (default vocabulary)."""
    import re

    from .manifest import breadth_problems

    spec = app.data.get("breadth") or {}
    ran = {w["name"].rsplit(" [", 1)[0]: w for w in workflows if w["name"].endswith("[default vocabulary]")}
    views: list[dict] = []
    by_user: dict = {}
    for view in spec.get("views", []):
        if "path" in view:
            by_user.setdefault(view.get("as", app.macro_user.get("username")), []).append(view)
        else:
            result = ran.get(view["workflow"])
            seen = [u for u in (result or {}).get("urls", []) if re.search(view["where"], u)]
            views.append({"name": view["name"], "workflow": view["workflow"],
                          "passed": bool(result and result["passed"] and seen), "visited": seen[:2]})
    for user, group in by_user.items():
        steps = [] if user is None else app.login_steps_as(user)
        session = Session(url, steps or None)
        try:
            for view in group:
                before = len(session.errors)
                response = session.goto(view["path"])
                wanted = urlsplit(urljoin(session.origin + "/", view["path"])).path
                status = response.status if response is not None else None
                ok = status in (200, None) and session.path == wanted and len(session.errors) == before
                views.append({"name": view["name"], "path": view["path"], "as": user, "status": status,
                              "landed": session.path, "errors": session.errors[before:][:3], "passed": ok})
        finally:
            session.close()
    needed = {w for ops in spec.get("entities", {}).values() for w in (ops or {}).values()}
    needed |= set(spec.get("patterns", {}).values())
    failing = sorted(w for w in needed if not (ran.get(w) or {}).get("passed"))
    size = breadth_problems(app)
    return {"passed": not size and not failing and all(v["passed"] for v in views),
            "size_problems": size, "workflows_failing": failing, "views": views,
            "counts": {"views": len(spec.get("views", [])), "entities": len(spec.get("entities", {})),
                       "patterns": sorted(spec.get("patterns", {}))}}


def check_role_restrictions(app: AppManifest, url: str) -> list[dict]:
    """Each restricted (user, path): signed in as that user the way the tester signs in, the
    path answers 200 and shows its access notice, and nothing committed changes."""
    regions = app.data["regions"]
    login = app.login
    out = []
    for entry in login.get("restricted", []):
        user = app.user(entry["user"])
        session = Session(url, app.login_steps_as(entry["user"]))
        try:
            completed = session.signed_in_steps
            banner = session.region(regions[login["signed_in_region"]])
            before = session.state()
            response = session.goto(entry["path"])
            status = response.status if response is not None else None
            notice = session.live_region(regions[entry["region"]])
            after = session.state()
            errors = list(session.errors)
        finally:
            session.close()
        passed = (completed == len(app.login_steps) and _contains(banner, user.get("name", "?"))
                  and after.get("user") == entry["user"]
                  and status in (200, None) and notice is not None
                  and before.get("sha") == after.get("sha") and not errors)
        out.append({"user": entry["user"], "path": entry["path"], "passed": passed,
                    "signed_in_as": (banner or "<absent>")[:80], "status": status,
                    "access_region": (notice or "<absent>")[:120],
                    "state_unchanged": before.get("sha") == after.get("sha"), "errors": errors[:5]})
    return out


# -- reset, isolation, determinism -----------------------------------------------------------------


def check_reset_isolation_determinism(app: AppManifest, url: str) -> dict:
    workflow = next(w for w in app.data["workflows"] if w.get("commits", True))
    first = Session(url, app.login_steps)
    try:
        seed_sha = first.state()["sha"]
        first.run(workflow["trace"])
        mutated_sha = first.state()["sha"]
        second = Session(url, app.login_steps)  # concurrent, separate browser context
        try:
            second_initial = second.state()["sha"]
            second.run(workflow["trace"])
            first_after_second = first.state()["sha"]
            digests_a = first.digests()
            digests_b = second.digests()
            second_final = second.state()["sha"]
        finally:
            second.close()
    finally:
        first.close()
    third = Session(url, app.login_steps)
    try:
        third_initial = third.state()["sha"]
    finally:
        third.close()
    return {
        "reset_to_seed": {"passed": second_initial == seed_sha and third_initial == seed_sha,
                          "seed_sha": seed_sha, "fresh_context_sha": [second_initial, third_initial]},
        "session_isolation": {"passed": first_after_second == mutated_sha and seed_sha != mutated_sha,
                              "detail": "a second session's workflow left the first session's "
                                        "committed state untouched"},
        "deterministic": {"passed": digests_a == digests_b and mutated_sha == second_final,
                          "page_digests_equal": digests_a == digests_b,
                          "final_state_equal": mutated_sha == second_final},
    }


# -- seeded faults -----------------------------------------------------------------------------------


def check_faults(app: AppManifest, urls: dict[str, str], *, max_trials: int = 30) -> dict:
    oracle_app = app.oracle_app()
    faulty, clean = _runner(urls["buggy"], app.login_steps), _runner(urls["clean"], app.login_steps)
    controls = {f["fault_id"]: _runner(urls[f"without-{f['fault_id']}"], app.login_steps)
                for f in app.faults}
    # The primary pipeline refuses an app with no primary-grammar faults (wayfarer's are all
    # extension faults), so it is built only when there is something for it to verify.
    pipeline = VerificationPipeline(oracle_app, faulty, control_runner=clean,
                                    control_runners={k: v for k, v in controls.items()
                                                     if k in {f.fault_id for f in oracle_app.faults}},
                                    max_minimization_trials=max_trials) if oracle_app.faults else None
    regions = app.data["regions"]
    specs = {f.fault_id: f for f in oracle_app.faults}
    results = []
    for fault in app.faults:
        fid = fault["fault_id"]
        witness = list(fault["trigger_trace"])
        started = time.monotonic()
        entry = {"fault_id": fid, "subtype": fault["subtype"], "oracle": fault.get("oracle", "existing"),
                 "fault_class": fault["fault_class"], "witness_length": len(witness)}

        if entry["oracle"] == "existing":
            spec = specs[fid]

            def verdict(runner, trace, spec=spec):
                observed = runner.run(trace)
                return observed, verify_fault(spec, oracle_app, observed)
        else:
            def verdict(runner, trace, fault=fault):
                observed = runner.run(trace)
                return observed, ext_oracle.verify(fault, regions, oracle_app, observed)

        observed, on_buggy = verdict(faulty, witness)
        _, on_clean = verdict(clean, witness)
        _, on_control = verdict(controls[fid], witness)
        entry.update({
            "witness_resolves": observed.replay_misses == 0,
            "witness_fires_on_buggy": on_buggy.violated,
            "witness_silent_on_clean": not on_clean.violated,
            "witness_needs_its_own_patch": not on_control.violated,
            "buggy_detail": [f"{r.kind}: violated={r.violated} inconclusive={r.inconclusive} "
                             f"{getattr(r, 'observed', '')!s:.80}" for r in on_buggy.results],
        })
        if entry["oracle"] == "existing":
            finding = pipeline.confirm(spec, observed, budget_at_discovery=len(witness))
            confirmed, minimized = finding.confirmed, finding.minimized_trace
            entry.update({"confirmed": confirmed, "rejected_because": finding.rejected_because,
                          "minimized_length": finding.minimization.get("minimized_length"),
                          "minimization_trials": finding.minimization.get("trials"),
                          "replays": len(finding.replays)})
        else:
            finding = ext_oracle.confirm(fault, regions, oracle_app, faulty=faulty,
                                         control=controls[fid], clean=clean, witness=witness,
                                         max_trials=max_trials)
            confirmed, minimized = finding.confirmed, finding.minimized_trace
            entry.update({"confirmed": confirmed, "rejected_because": finding.rejected_because,
                          "minimized_length": len(minimized) if confirmed else None,
                          "minimization_trials": finding.minimization.get("trials"),
                          "replays": len(finding.replays)})
        if confirmed:
            _, again = verdict(faulty, minimized)
            _, minimized_clean = verdict(clean, minimized)
            _, minimized_control = verdict(controls[fid], minimized)
            entry.update({"minimized_trace": minimized,
                          "minimized_still_fires": again.violated,
                          "minimized_silent_on_clean": not minimized_clean.violated,
                          "minimized_needs_its_own_patch": not minimized_control.violated})
        entry["passed"] = bool(
            entry["witness_resolves"] and entry["witness_fires_on_buggy"]
            and entry["witness_silent_on_clean"] and entry["witness_needs_its_own_patch"]
            and confirmed and entry.get("minimized_still_fires")
            and entry.get("minimized_silent_on_clean") and entry.get("minimized_needs_its_own_patch"))
        entry["seconds"] = round(time.monotonic() - started, 1)
        results.append(entry)
    return {"faults": results}


def check_benign(app: AppManifest, urls: dict[str, str]) -> list[dict]:
    oracle_app = app.oracle_app()
    regions = app.data["regions"]
    ext = app.faults_by_oracle("extension")
    out = []
    for number, trace in enumerate(app.data["benign_traces"]):
        for build in ("clean", "buggy"):
            observed = _runner(urls[build], app.login_steps).run(list(trace))
            fired = [v.fault_id for v in verify_all(oracle_app, observed) if v.violated]
            fired += [f["fault_id"] for f in ext
                      if ext_oracle.verify(f, regions, oracle_app, observed).violated]
            out.append({"trace": number, "build": build, "resolves": observed.replay_misses == 0,
                        "fired": fired, "passed": observed.replay_misses == 0 and not fired})
    return out


def random_walk_clean(app: AppManifest, url: str, *, steps: int = 150, seed: int = 0,
                      episode_steps: int = 40) -> dict:
    """Uniform random exploration of the clean build: every fault assertion (both grammars)
    is evaluated on every step's trajectory-so-far, and console/page/HTTP errors watched."""
    oracle_app = app.oracle_app()
    regions = app.data["regions"]
    ext = app.faults_by_oracle("extension")
    policy = RandomPolicy(MAX_ACTIONS, seed=seed, valid_only=True)
    candidates: dict[str, int] = {}
    errors: list[str] = []
    foreign: list[str] = []
    timings: list[tuple[str, str, float]] = []
    taken = 0
    while taken < steps:
        session = Session(url, app.login_steps)
        try:
            for _ in range(min(episode_steps, steps - taken)):
                available = list(session.info.get("action_specs") or [])
                index = policy.act(session.observation, session.info)
                session.observation, _r, _te, _tr, session.info = session.env.step(index)
                session.records.append(observed_record(session.info,
                                                       session.info.get("action_spec"), available))
                taken += 1
                view = build_trajectory(session.records)
                for verdict in verify_all(oracle_app, view):
                    if verdict.violated:
                        candidates[verdict.fault_id] = candidates.get(verdict.fault_id, 0) + 1
                for f in ext:
                    if ext_oracle.verify(f, regions, oracle_app, view).violated:
                        candidates[f["fault_id"]] = candidates.get(f["fault_id"], 0) + 1
            errors += session.errors
            foreign += session.foreign
            timings += session.timings
        finally:
            session.close()
    return {"steps": steps, "seed": seed, "raw_candidates": candidates, "errors": errors[:10],
            "foreign_requests": foreign[:10], "responses": response_times(app, timings),
            "passed": not candidates and not errors and not foreign}


def response_times(app: AppManifest, timings: list[tuple[str, str, float]]) -> dict:
    """How fast the app answered during the walk, sign-in posts excepted (they pay for the
    credential check and the partition): plan §4 `responses_fast`."""
    action = (app.login.get("form") or {}).get("action")
    kept = [t for t in timings if not (t[0] == "POST" and t[1] == action)]
    took = [t[2] for t in kept]
    p95 = percentile(took, 0.95)
    worst = max(took, default=0.0)
    return {"count": len(took), "p50_ms": percentile(took, 0.5), "p95_ms": p95, "max_ms": worst,
            "slowest": sorted(kept, key=lambda t: -t[2])[:5],
            "limits_ms": {"p95": RESPONSE_P95_MS, "max": RESPONSE_MAX_MS},
            "passed": bool(took) and p95 <= RESPONSE_P95_MS and worst <= RESPONSE_MAX_MS}
