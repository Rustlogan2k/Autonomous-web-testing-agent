"""The security study's scoring rule (security/campaign.py), on synthetic alerts."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from web_testing_agent.security.campaign import (
    APPS_FILE,
    check_explorer_signed_in,
    run_abandoning_after,
    score,
    site_root,
)

VULNS = [
    {"id": "R", "title": "reflected", "cwe": [79], "paths": ["/search.php"], "param": "q",
     "detectable": True},
    {"id": "S", "title": "stored", "cwe": [89], "paths": ["/register.php", "/similar.php"],
     "detectable": True},
    {"id": "L", "title": "logic", "cwe": [840], "paths": ["/cart.php"], "detectable": False},
]


def alert(cwe, url, param="", confidence="Medium", risk="High"):
    return {"cweid": str(cwe), "url": url, "param": param, "confidence": confidence,
            "risk": risk, "pluginId": "1", "alert": "x", "method": "GET"}


def test_an_alert_counts_only_with_its_cwe_path_and_documented_parameter():
    result = score([alert(79, "http://wp/search.php?q=1", "q"),
                    alert(79, "http://wp/search.php?x=1", "x")], VULNS)
    assert result["by_vulnerability"]["R"]["found"]
    assert len(result["by_vulnerability"]["R"]["alerts"]) == 1
    # the same class on another parameter of the same page is left for triage
    assert [a["param"] for a in result["unmatched_in_documented_classes"]] == ["x"]


def test_a_stored_vulnerability_matches_its_source_or_its_sink():
    assert score([alert(89, "http://wp/similar.php")], VULNS)["by_vulnerability"]["S"]["found"]
    assert score([alert(89, "http://wp/register.php")], VULNS)["by_vulnerability"]["S"]["found"]


def test_false_positive_confidence_and_other_classes_are_ignored():
    result = score([alert(79, "http://wp/search.php", "q", confidence="False Positive"),
                    alert(200, "http://wp/search.php", "q")], VULNS)
    assert result["found"] == 0
    assert result["unmatched_in_documented_classes"] == []


def test_counts_separate_detectable_from_documented():
    result = score([alert(79, "http://wp/search.php", "q")], VULNS)
    assert (result["found"], result["found_detectable"], result["detectable"],
            result["documented"]) == (1, 1, 2, 3)


def test_the_study_config_is_well_formed():
    apps = json.loads(Path(APPS_FILE).read_text(encoding="utf-8"))
    for name, app in apps.items():
        if name.startswith("_"):
            continue
        ids = [v["id"] for v in app["vulnerabilities"]]
        assert len(ids) == len(set(ids))
        for vuln in app["vulnerabilities"]:
            assert vuln["cwe"] and vuln["paths"] and isinstance(vuln["detectable"], bool)
            assert all(p.startswith("/") for p in vuln["paths"])


def test_the_site_root_drops_the_start_pages_path():
    # bWAPP starts on a page; its other pages are not under it.
    assert site_root("http://bwapp/login.php") == "http://bwapp"
    assert site_root("http://wp/") == "http://wp"
    assert site_root("http://dvwa:8080/setup.php?x=1") == "http://dvwa:8080"


def test_login_macros_name_controls_the_way_the_explorers_registry_does():
    # The explorer matches a step's selector as an exact string against its registry's
    # own: [id="..."] for an element with an id, tag[name="..."] with quotes otherwise.
    # bWAPP's input[name=login] matched nothing, and every Katalyze run explored signed out.
    registry_form = re.compile(r'^(\[id="[^"]+"\]|[a-z]+\[name="[^"]+"\])$')
    apps = json.loads(Path(APPS_FILE).read_text(encoding="utf-8"))
    for name, app in apps.items():
        for step in (app.get("login") or []) if isinstance(app, dict) else []:
            if step.get("selector"):
                assert registry_form.match(step["selector"]), (name, step)
            else:
                assert step.get("id") or step.get("text"), (name, step)


class _Locator:
    def __init__(self, n):
        self.n = n

    def count(self):
        return self.n


class _Page:
    url = "http://app/portal.php"

    def __init__(self, fields):
        self.fields = fields

    def locator(self, selector):
        return _Locator(self.fields.get(selector, 0))


class _Env:
    def __init__(self, landed, fields):
        self._bootstrap_steps = 0
        self._landed = landed
        self._browser = type("B", (), {"page": _Page(fields)})()

    def reset(self):
        self._bootstrap_steps = self._landed


LOGIN = [{"type": "TYPE", "id": "login", "params": {"value": "u"}},
         {"type": "TYPE", "id": "password", "params": {"value": "p"}},
         {"type": "CLICK", "selector": 'button[name="form"]'}]


def test_the_explorers_sign_in_must_land_every_step_and_leave_the_form():
    check_explorer_signed_in(_Env(3, {}), LOGIN)
    with pytest.raises(RuntimeError, match="0/3"):
        check_explorer_signed_in(_Env(0, {'[id="login"]': 1}), LOGIN)
    with pytest.raises(RuntimeError):
        # every step matched, yet the sign-in form is still there
        check_explorer_signed_in(_Env(3, {'[id="login"]': 1}), LOGIN)


def test_a_hung_explorer_is_abandoned_and_an_error_still_raises():
    import threading

    release = threading.Event()
    assert run_abandoning_after(release.wait, 0.2) is True
    release.set()
    assert run_abandoning_after(lambda: None, 5) is False

    def fail():
        raise RuntimeError("sign-in")

    with pytest.raises(RuntimeError, match="sign-in"):
        run_abandoning_after(fail, 5)


def test_the_browsers_host_gate_aborts_requests_to_other_hosts():
    from web_testing_agent.envs.browser_session import _host_gate

    class Route:
        def __init__(self, url):
            self.request = type("R", (), {"url": url})()
            self.outcome = None

        def continue_(self):
            self.outcome = "continue"

        def abort(self, reason):
            self.outcome = reason

    gate = _host_gate({"bwapp"})
    inside, outside = Route("http://bwapp/portal.php?x=1"), Route("https://www.facebook.com/")
    gate(inside)
    gate(outside)
    assert (inside.outcome, outside.outcome) == ("continue", "blockedbyclient")
