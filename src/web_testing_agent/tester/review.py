"""Qualitative review of outcome pages by a language model. Reported, never counted.

The deterministic triggers catch what an application *announces* as broken: an error, a
failed request, a dead link. The faults this project is about are the silent ones -- a
confirmation that shows the wrong quantity, a total that does not add up -- and on an
application with no answer key nothing deterministic can say what the right value was.

A language model can at least read an outcome page against the actions that led to it and
point at something that looks inconsistent. It is also, as this project measured on its own
fixtures, wrong often enough that its opinion cannot be a finding. So this review is kept
in its own lane:

* it runs only on **outcome pages** -- states the progress potential rates as a completed
  or nearly completed task, which is where a silent functional fault becomes visible;
* its output must quote the page: an observation whose evidence is not literally present in
  the snapshot is discarded as ungrounded (`judge.verdict.is_grounded`);
* its surviving observations appear in the report under "qualitative observations -- not
  verified", with the trace that reached the page, and are **never** merged with verified
  findings or counted in any metric.

Its precision and recall against known faults are measured separately on the benchmark
(`scripts/evaluate_llm_review.py`), which is what tells a reader how much to trust it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

from ..judge.scoring import is_grounded
from ..reward.progress import DEFAULT_HOST, DEFAULT_MODEL, page_digest
from ..utils.logging import get_logger

logger = get_logger(__name__)

REVIEW_PROMPT_VERSION = "outcome-review-v1"

SYSTEM_PROMPT = """\
You review one page of a web application that a tester reached by performing the listed \
actions. Your job is to spot a FUNCTIONAL inconsistency visible on this page: a value that \
contradicts what the tester entered or chose, a total or count that does not follow from \
the values shown, required information that is missing, or an outcome that does not match \
the action taken. Do not comment on style, wording, layout or missing features. If nothing \
on the page contradicts the actions, say so.

Answer with JSON only: {"evidence": "<an exact short quote copied from the PAGE text, or \
empty>", "observation": "<at most 40 words>", "concern": <true or false>}"""

SCHEMA = {
    "type": "object",
    "properties": {"evidence": {"type": "string"}, "observation": {"type": "string"},
                   "concern": {"type": "boolean"}},
    "required": ["evidence", "observation", "concern"],
}


@dataclass
class Observation:
    url: str
    concern: bool
    observation: str
    evidence: str
    grounded: bool
    actions: list[str]
    trace: list[dict]
    potential: float | None = None
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def describe_action(step: dict | None) -> str:
    if not step:
        return ""
    target = step.get("text") or step.get("id") or step.get("selector") or ""
    params = step.get("params") or {}
    extra = params.get("category") or params.get("option") or ""
    return " ".join(part for part in (step.get("type", "").lower(), repr(target) if target else "",
                                      f"[{extra}]" if extra else "") if part)


class OutcomeReviewer:
    """Asks a local model whether an outcome page contradicts the actions that reached it."""

    def __init__(self, *, model: str = DEFAULT_MODEL, host: str | None = None,
                 timeout_s: float = 120.0) -> None:
        self.model = model
        self.host = (host or DEFAULT_HOST).rstrip("/")
        self.timeout_s = timeout_s
        self.calls = 0

    def review(self, url: str, html: str, visible_text: str | None, trace: list[dict],
               entered: dict[str, str], potential: float | None = None) -> Observation:
        digest = page_digest(url, html, visible_text)
        actions = [describe_action(step) for step in trace if step][-12:]
        values = "; ".join(f"{k} = {v[:40]!r}" for k, v in list(entered.items())[:15])
        user = (f"ACTIONS (oldest first):\n- " + "\n- ".join(actions or ["(none)"])
                + (f"\n\nVALUES ENTERED: {values}" if values else "")
                + f"\n\n{digest}")
        request = {"model": self.model, "stream": False, "format": SCHEMA,
                   "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": user}],
                   "options": {"temperature": 0.0, "seed": 7, "num_ctx": 4096, "num_predict": 160}}
        self.calls += 1
        try:
            with urllib.request.urlopen(urllib.request.Request(  # noqa: S310
                    self.host + "/api/chat", data=json.dumps(request).encode("utf-8"),
                    headers={"Content-Type": "application/json"}), timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = (payload.get("message") or {}).get("content") or ""
            parsed = json.loads(content[content.find("{"): content.rfind("}") + 1])
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return Observation(url, False, "", "", False, actions, trace, potential,
                               error=f"{type(exc).__name__}: {exc}")
        evidence = str(parsed.get("evidence", ""))[:200]
        concern = bool(parsed.get("concern"))
        grounded = bool(evidence) and is_grounded(evidence, digest)
        return Observation(url=url, concern=concern and grounded,
                           observation=str(parsed.get("observation", ""))[:400],
                           evidence=evidence, grounded=grounded, actions=actions,
                           trace=trace, potential=potential)
