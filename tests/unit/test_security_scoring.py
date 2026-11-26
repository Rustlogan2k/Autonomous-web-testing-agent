"""The security study's scoring rule (security/campaign.py), on synthetic alerts."""

from __future__ import annotations

import json
from pathlib import Path

from web_testing_agent.security.campaign import APPS_FILE, score

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
