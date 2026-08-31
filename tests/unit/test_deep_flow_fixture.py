"""The deep-flow fixture's deterministic ceiling, pinned against the harness.

`answer_key.json` claims the fixture's deterministic ceiling is 0 — every page returns
200, settles, and logs nothing, and its single seeded bug (DEEP-01) is `llm_required`.
That claim was true about the *fixture* and false about the *harness*: until 2026-08-27
`broken_navigation` fired 18-20 times per 200-step run on it, because the fixture is full
of links that point at the page they are already on and the trigger read "the URL did not
change" as "the link is broken". Those firings were published as distinct findings and
used to claim masked random out-discovered the DQN roughly 10x here.

These tests pin both halves so the pair cannot drift apart again: the fixture really does
contain self-links (so this is not a hypothetical), and the action registry really does
refuse to call them navigational (so they cannot become findings).
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import pytest

from web_testing_agent.envs.action_registry import build_action_specs
from web_testing_agent.envs.types import ActionType

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "deep_flow_site"
ORIGIN = "http://127.0.0.1:8000/"
PAGES = sorted(p.name for p in FIXTURE.glob("*.html"))


class _Anchors(HTMLParser):
    """Every `<a href>` on a page, with its text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict] = []
        self._open: dict | None = None

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attributes = {k.lower(): (v or "") for k, v in attrs}
        if "href" not in attributes:
            return
        self._open = {"href": attributes["href"], "id": attributes.get("id") or None, "text": ""}

    def handle_data(self, data):
        if self._open is not None:
            self._open["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._open is not None:
            self._open["text"] = " ".join(self._open["text"].split())[:60]
            self.anchors.append(self._open)
            self._open = None


def _anchors(page: str) -> list[dict]:
    parser = _Anchors()
    parser.feed((FIXTURE / page).read_text(encoding="utf-8"))
    parser.close()
    return parser.anchors


def _element(anchor: dict, index: int, page_url: str) -> dict:
    """The scanned-element dict `build_action_specs` consumes, as the browser produces it."""
    return {
        "index": index, "tag": "a", "type": "", "id": anchor["id"], "name": None,
        "href": anchor["href"], "resolvedHref": urljoin(page_url, anchor["href"]),
        "target": None, "text": anchor["text"], "disabled": False,
        "visible": True, "inViewport": True, "options": None,
    }


def _self_links(page: str) -> list[dict]:
    page_url = urljoin(ORIGIN, page)
    return [
        a for a in _anchors(page)
        if not a["href"].startswith("#")
        and urljoin(page_url, a["href"]).split("#")[0] == page_url
    ]


def test_the_fixture_really_does_contain_self_links():
    """Not a hypothetical: name them, so the count cannot quietly change."""
    found = {page: [a["id"] or a["text"] for a in _self_links(page)] for page in PAGES}
    non_empty = {page: ids for page, ids in found.items() if ids}
    assert non_empty, "fixture has no self-links; these tests would be vacuous"
    # Every page carries a nav bar linking to itself, and support.html adds three more.
    assert "support.html" in non_empty
    assert len(non_empty["support.html"]) >= 4, non_empty["support.html"]
    assert sum(len(ids) for ids in non_empty.values()) >= 10, non_empty


@pytest.mark.parametrize("page", PAGES)
def test_no_self_link_on_any_fixture_page_is_navigational(page: str):
    """The fix, applied to the real markup rather than to a synthetic element."""
    page_url = urljoin(ORIGIN, page)
    selfies = _self_links(page)
    if not selfies:
        pytest.skip(f"{page} has no self-links")
    specs = build_action_specs(
        [_element(a, i, page_url) for i, a in enumerate(selfies)], page_url=page_url
    )
    clicks = [s for s in specs if s.action_type is ActionType.CLICK]
    assert len(clicks) == len(selfies)
    offenders = [s.element_id for s in clicks if s.params["navigational"]]
    assert not offenders, f"{page}: self-links still marked navigational: {offenders}"


@pytest.mark.parametrize("page", PAGES)
def test_links_that_leave_the_page_stay_navigational(page: str):
    """The fix must not silence real navigation, or the trigger stops working entirely."""
    page_url = urljoin(ORIGIN, page)
    outgoing = [
        a for a in _anchors(page)
        if not a["href"].startswith("#")
        and urljoin(page_url, a["href"]).split("#")[0] != page_url
    ]
    if not outgoing:
        pytest.skip(f"{page} has no outgoing links")
    specs = build_action_specs(
        [_element(a, i, page_url) for i, a in enumerate(outgoing)], page_url=page_url
    )
    clicks = [s for s in specs if s.action_type is ActionType.CLICK]
    assert all(s.params["navigational"] for s in clicks), [
        s.element_id for s in clicks if not s.params["navigational"]
    ]


def test_the_answer_key_no_longer_claims_no_trigger_can_fire():
    """The claim that made this defect invisible for ten days."""
    key = json.loads((FIXTURE / "answer_key.json").read_text(encoding="utf-8"))
    notes = [note.lower() for note in key["notes"]]
    claim = "no trigger in reward/functional_triggers.py can fire"
    # The retracted sentence may still appear — the correction quotes it, which is the
    # point of keeping it — but only inside a note that marks it as retracted.
    for note in notes:
        if claim in note:
            assert "corrected 2026-08-27" in note and "false" in note, note
    assert any("corrected 2026-08-27" in note for note in notes)
    notes_text = " ".join(notes)
    # And the deterministic ceiling it does claim must match the bug list.
    deterministic = [b for b in key["bugs"] if "deterministic" in b["detectability"]]
    assert deterministic == [], deterministic
    assert re.search(r"deterministic ceiling is 0", notes_text)
