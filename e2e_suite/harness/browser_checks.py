"""Browser checks, driven through the tester's own environment and action registry.

Every interaction here goes through `web_testing_agent`'s `WebFunctionalEnv` (extended
registry) and `ScriptedPolicy` — imported, never modified — so "reachable" means reachable
by the tester's own actions with its own generated values, not by a hand-written Playwright
script that could do things the tester cannot.
"""

from __future__ import annotations

import hashlib
import time
from urllib.parse import urlsplit

from web_testing_agent.benchmark.suite import EnvSettings, make_env
from web_testing_agent.envs.types import MAX_ACTIONS
from web_testing_agent.evaluation import ScriptedPolicy
from web_testing_agent.evaluation.rollout import RandomPolicy
from web_testing_agent.oracle import PlaywrightTrajectoryRunner, VerificationPipeline, verify_all, verify_fault
from web_testing_agent.oracle.replay import observed_record
from web_testing_agent.oracle.trajectory import build_trajectory, region_text, visible_text

from . import ext_oracle
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


class Session:
    """One fresh browser context on one variant: drive traces, read state, watch traffic."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        self.env = make_env(url, SETTINGS, max_steps=1000)
        self.observation, self.info = self.env.reset()
        self.records = [observed_record(self.info)]
        self.foreign: list[str] = []
        self.errors: list[str] = []
        self._watch()

    def _watch(self) -> None:
        browser = self.env._browser  # noqa: SLF001 - read-only access to the live page
        context, page = browser.context, browser.page
        context.on("request", self._on_request)
        context.on("response", self._on_response)
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

    def html(self) -> str:
        return self.records[-1].get("html", "") or ""

    def region(self, selector: str) -> str | None:
        return region_text(self.html(), selector)

    def digests(self) -> list[str]:
        return [hashlib.sha256(visible_text(r.get("html", "") or "").encode()).hexdigest()[:16]
                for r in self.records]

    def close(self) -> None:
        self.env.close()


def _runner(url: str) -> PlaywrightTrajectoryRunner:
    return PlaywrightTrajectoryRunner(lambda: make_env(url, SETTINGS, max_steps=REPLAY_MAX_STEPS),
                                      max_steps=REPLAY_MAX_STEPS)


def _contains(text: str | None, wanted: str) -> bool:
    return text is not None and " ".join(wanted.split()).lower() in " ".join(text.split()).lower()


# -- workflows, state-dependent actions, decoys -------------------------------------------------


def check_workflows(app: AppManifest, url: str, label: str) -> list[dict]:
    regions = app.data["regions"]
    out = []
    for workflow in app.data["workflows"]:
        session = Session(url)
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
            })
        finally:
            session.close()
    return out


def check_state_dependent(app: AppManifest, url: str) -> list[dict]:
    out = []
    for item in app.data["state_dependent"]:
        result = {"name": item["name"]}
        for side in ("useless", "useful"):
            session = Session(url)
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
        session = Session(url)
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


# -- reset, isolation, determinism -----------------------------------------------------------------


def check_reset_isolation_determinism(app: AppManifest, url: str) -> dict:
    workflow = next(w for w in app.data["workflows"] if w.get("commits", True))
    first = Session(url)
    try:
        seed_sha = first.state()["sha"]
        first.run(workflow["trace"])
        mutated_sha = first.state()["sha"]
        second = Session(url)  # concurrent, separate browser context
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
    third = Session(url)
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
    faulty, clean = _runner(urls["buggy"]), _runner(urls["clean"])
    controls = {f["fault_id"]: _runner(urls[f"without-{f['fault_id']}"]) for f in app.faults}
    pipeline = VerificationPipeline(oracle_app, faulty, control_runner=clean,
                                    control_runners={k: v for k, v in controls.items()
                                                     if k in {f.fault_id for f in oracle_app.faults}},
                                    max_minimization_trials=max_trials)
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
            observed = _runner(urls[build]).run(list(trace))
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
    taken = 0
    while taken < steps:
        session = Session(url)
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
        finally:
            session.close()
    return {"steps": steps, "seed": seed, "raw_candidates": candidates, "errors": errors[:10],
            "foreign_requests": foreign[:10],
            "passed": not candidates and not errors and not foreign}
