"""The LLM behind specification inference: rule proposal and finding adjudication.

Calls go to an Ollama server (`/api/chat`, JSON output, temperature 0). The default model is
`gpt-oss:120b-cloud`, which Ollama serves from its cloud: page text leaves the machine. Every
response is cached on disk by the hash of its request, so a re-run is reproducible and free.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import requests

from ..utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = os.environ.get("WTA_SPEC_MODEL", "gpt-oss:120b-cloud")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
CACHE = Path(__file__).resolve().parents[3] / "var" / "llm-cache" / "specinfer"


class LLMError(RuntimeError):
    pass


def chat_json(system: str, user: str, *, model: str = DEFAULT_MODEL, timeout: int = 420,
              retries: int = 2) -> dict:
    payload = {"model": model, "stream": False, "format": "json", "options": {"temperature": 0},
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    cached = CACHE / f"{key}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    last = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(f"{OLLAMA}/api/chat", json=payload, timeout=timeout)
            response.raise_for_status()
            content = response.json()["message"]["content"]
            data = json.loads(content[content.find("{"): content.rfind("}") + 1])
            CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(data, indent=1), encoding="utf-8")
            return data
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            last = exc
            logger.warning("LLM call failed (attempt {}): {}", attempt + 1, exc)
            time.sleep(3 * (attempt + 1))
    raise LLMError(f"LLM unavailable: {last}")


RULE_SYSTEM = """You are a senior software tester. You will see pages of a web application, captured while
an automated tester explored it. Infer what a CORRECT version of this application must do, and
write it as machine-checkable rules. The goal is to catch silent functional bugs: wrong numbers,
lost or altered data, broken sorting, actions that do not take effect, invalid input accepted.

Rules use ONLY these kinds and fields (selectors MUST be copied from the page snapshots):
- arithmetic: {"page": regex on the page path, "operands": {name: {"selector": css, "agg": "first"|"sum"|"count"}},
  "expression": e.g. "total == round(subtotal + tax, 2)", "tolerance": 0.01}
- echo: {"field": the input's selector or label, "page": regex of a LATER page, "selector": css where the value must appear (optional)}
- sorted: {"page": regex, "control": the select's selector, "option": option value, "items": css of the sorted values,
  "key": "number"|"text", "order": "asc"|"desc"}
- counter: {"page": regex of the page where the action is taken, "action": regex on the clicked control's label,
  "counter": css of a numeric counter, "delta": +1 or -1}
- consistency: {"a": {"page": regex, "selector": css}, "b": {"page": regex, "selector": css}, "compare": "number"|"text"}
  (the value shown at a must equal the value shown later at b, e.g. a cart total carried to checkout)
- blocked: {"form_page": regex, "field": a required input's selector or label, "submit": regex on the submit label,
  "forbidden_page": regex of the page that must NOT be reached while the field is empty}

The pages were captured from the application as it is, and it MAY CONTAIN BUGS. Never assume that
what a page shows is correct. Base every rule on what the labels, controls and the application's
domain imply a correct application does. Digits observed on the pages are masked as # on purpose:
reason about relationships between values, not about the values you happened to see.
If documentation or tests from the application's repository are given ("intended_behaviour"), treat
them as the statement of intended behaviour and prefer them over anything the pages show.
Only write rules a correct application must obey in EVERY case; avoid rules that depend on data you cannot see.
Prefer 8-15 specific, high-value rules. Each rule: {"id", "kind", "description" (what correct behaviour is,
one sentence), "rationale" (why you believe it), ...kind fields}.
Respond with JSON: {"rules": [...]}"""


JUDGE_SYSTEM = """You are a senior software tester reviewing a candidate bug found by an automated tester.
A rule describing expected behaviour was violated, reproducibly, by the minimal steps shown.
Decide whether this is a genuine functional bug of the application, behaviour the application
intends, or a mistake in the rule (wrong selector, wrong assumption, misapplied).
The application may be buggy: do not excuse behaviour just because the application does it.
If you answer "intended", you MUST give "evidence_quote": an exact quote, copied verbatim from the
page text or from the intended-behaviour documents, that shows the behaviour is intended. Without
such a quote, answer "bug" or "rule_error".
Respond with JSON: {"verdict": "bug"|"intended"|"rule_error", "confidence": 0-1,
"title": short bug title, "explanation": two sentences a developer can act on,
"evidence_quote": "..." (required for "intended")}"""


def propose_rules(app: str, snapshots: list[dict], journeys: list[list[str]], *,
                  existing: list[str] | None = None, intent: str = "", model: str = DEFAULT_MODEL) -> dict:
    payload = {"application": app, "pages": snapshots, "example_journeys": journeys[:6]}
    if intent:
        payload["intended_behaviour"] = intent
    if existing:
        payload["rules_already_written"] = existing
        payload["instruction"] = ("These pages are new. Write rules only for behaviour on them that the rules "
                                  "already written do not cover; do not repeat those rules.")
    user = json.dumps(payload, ensure_ascii=False)
    return chat_json(RULE_SYSTEM, user[:60000], model=model)


def judge(rule: dict, details: dict, steps: list[str], page_text: str, *, intent: str = "",
          model: str = DEFAULT_MODEL) -> dict:
    payload = {"rule": rule, "observed": details, "minimal_steps": steps, "page_text_at_violation": page_text[:3500]}
    if intent:
        payload["intended_behaviour"] = intent[:6000]
    user = json.dumps(payload, ensure_ascii=False)
    return chat_json(JUDGE_SYSTEM, user, model=model)
