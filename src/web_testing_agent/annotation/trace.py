"""Records every `(before, action, after)` transition of a rollout to a replayable corpus.

This is the input the LLM judge is built and scored against, and it exists so that
judge development is decoupled from browser execution: capture once, then iterate on
prompts, models and parsers over a fixed corpus at zero browser cost. Without it the
only way to test a judge change is a fresh Playwright run, which is roughly four
orders of magnitude slower and not reproducible between runs.

What is recorded is deliberately the *whole* transition rather than a distilled
prompt. Which fields a judge should see is exactly the open question — a text-only
view of `html` cannot decide BUG-08 (a CTA hidden by a `max-width:600px` media query
is still present in the markup), so that call belongs to the prompt builder, not to
capture. Recording raw and distilling later keeps that decision revisable without
re-running the browser.

Layout on disk::

    <out_dir>/<run_id>/
        meta.json                 run config, policy, step/episode counts, script fidelity
        trace.jsonl               one JSON record per step, in order
        pages/<sha256>.html       deduplicated page bodies, referenced by hash
        screenshots/<sha256>.png  optional, same addressing

Page bodies are content-addressed because a rollout revisits the same handful of
pages constantly — the toy site has six — and a NO_OP step stores the identical
document twice. Hashing collapses that, and it scales to targets where a single
page is hundreds of kilobytes.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)

# Anything else in `info` is either not JSON-serializable (ActionSpec, ndarray,
# BugSignals) or is captured explicitly in its own field below.
_INFO_SKIP = frozenset(
    {
        "action_spec",
        "action_specs",
        "bug_signals",
        "episode_context",
        "exec_info",
        "load_duration_s",
        "network_settled",
        "opened_new_page",
        "page",
        "step",
    }
)
_JSON_SCALARS = (int, float, bool, str, type(None))
_RESPONSE_SNIPPET_MAX = 500


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def relative_path(url: str) -> str:
    """Origin-independent page identity: `/widgets.html?x=1`.

    The toy site is served on an ephemeral port, so raw URLs differ between every
    capture run. Labels and judge prompts key off this instead so a corpus stays
    comparable across runs — and so a trace captured against localhost:64142 can be
    scored against an answer key written in terms of `widgets.html`.
    """
    parsed = urlparse(url)
    if not parsed.scheme.startswith("http"):
        return url
    path = parsed.path or "/"
    return f"{path}?{parsed.query}" if parsed.query else path


def _json_safe(payload: dict) -> dict:
    """Keep every scalar entry, drop what cannot be serialized.

    Preferred over whitelisting known keys: the environment gains new per-step signals
    over time, and a recorder that keeps only the fields it was told about loses them
    silently — which is exactly how `left_application` went missing.
    """
    return {
        key: value
        for key, value in payload.items()
        if isinstance(value, (str, int, float, bool, type(None)))
    }


def _summarize_network(raw: str | None) -> list[dict]:
    """Compact per-request view, dropping headers.

    `info["page"]["network"]` is a JSON *string*. The env now budgets it by dropping
    whole events (`_serialize_network`), so it should always parse; this guard is
    belt-and-braces against a future producer that slices the string instead, which is
    what the env originally did and which cost the entire trace for the step rather
    than its tail.
    """
    if not raw:
        return []
    try:
        events = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("Network trace was truncated mid-JSON; recording no network events for this step")
        return []
    summary = []
    for event in events:
        if not isinstance(event, dict):
            continue
        summary.append(
            {
                "url": relative_path(event.get("url", "")),
                "method": event.get("method"),
                "status": event.get("response_status"),
                "resource_type": event.get("resource_type", "other"),
                "failed": bool(event.get("failed")),
                "failure_text": event.get("failure_text"),
                "duration_ms": event.get("duration_ms"),
                "body_snippet": (event.get("response_body_snippet") or "")[:_RESPONSE_SNIPPET_MAX],
            }
        )
    return summary


def _serialize_signals(signals: Any) -> dict | None:
    """The deterministic triggers that fired — the baseline the judge must beat."""
    if signals is None:
        return None
    return {
        "console_errors": list(signals.console_errors),
        "unexpected_http_errors": [
            {
                "url": relative_path(event.url),
                "status": event.response_status,
                "resource_type": event.resource_type,
                "failed": event.failed,
            }
            for event in signals.unexpected_http_errors
        ],
        "slow_response": bool(signals.slow_response),
        "broken_navigation": bool(signals.broken_navigation),
        "document_http_error": bool(signals.document_http_error),
        "load_duration_s": round(float(signals.load_duration_s), 3),
        "any_triggered": bool(signals.any_triggered),
    }


@dataclass(slots=True)
class StepRecord:
    """One recorded transition, as read back from `trace.jsonl`.

    Page bodies and screenshots are hashes; `resolve_html` reads the blob on demand so
    that iterating a large trace does not load every document into memory.
    """

    episode: int
    step: int
    global_step: int
    action: dict
    before: dict
    after: dict
    exec: dict = field(default_factory=dict)
    settled: bool = True
    opened_new_page: bool = False
    load_duration_s: float = 0.0
    bug_signals: dict | None = None
    reward: float = 0.0
    reward_breakdown: dict = field(default_factory=dict)
    terminated: bool = False
    truncated: bool = False
    changed: dict = field(default_factory=dict)
    _root: Path | None = None
    # Page bodies held directly rather than as hashes into a trace directory. Used by
    # the live reward path, which builds records in memory and never writes a corpus:
    # `judge.window` renders from `StepRecord`, so sharing the type is what guarantees a
    # live window is byte-identical to the offline one that every judge measurement was
    # made against. A second, parallel "live record" type would let the two drift, and
    # the drift would invalidate the offline numbers without failing anything.
    _inline_html: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, payload: dict, root: Path | None = None) -> "StepRecord":
        known = {f for f in cls.__slots__ if not f.startswith("_")}
        return cls(**{k: v for k, v in payload.items() if k in known}, _root=root)

    def resolve_html(self, side: str) -> str:
        """Read back the `before` or `after` page body. Empty string if unavailable."""
        if self._inline_html is not None:
            return self._inline_html.get(side, "")
        digest = (self.before if side == "before" else self.after).get("html")
        if not digest or self._root is None:
            return ""
        blob = self._root / "pages" / f"{digest}.html"
        return blob.read_text(encoding="utf-8") if blob.is_file() else ""

    @property
    def triggered(self) -> bool:
        """Whether any deterministic trigger fired — the 3-of-10 baseline."""
        return bool(self.bug_signals and self.bug_signals.get("any_triggered"))

    @property
    def state_changed(self) -> bool:
        return bool(self.changed.get("url") or self.changed.get("html"))


class TraceRecorder:
    """Collects a rollout into a replayable trace directory.

    Driven by `run_rollout`'s optional `recorder` hook, so no environment or policy
    code knows it exists. `on_reset` supplies the `before` side of the episode's first
    step; every subsequent step reuses the previous step's `after`, which is exact —
    the env's own `pre_obs` *is* the previous `post_obs` (see `WebTestingEnv.step`).
    """

    def __init__(
        self,
        out_dir: Path,
        run_id: str,
        *,
        save_screenshots: bool = False,
        meta: dict | None = None,
    ) -> None:
        self.root = Path(out_dir) / run_id
        self.run_id = run_id
        self.save_screenshots = save_screenshots
        self.meta = dict(meta or {})

        self._pages_dir = self.root / "pages"
        self._shots_dir = self.root / "screenshots"
        self.root.mkdir(parents=True, exist_ok=True)
        self._pages_dir.mkdir(exist_ok=True)
        if save_screenshots:
            self._shots_dir.mkdir(exist_ok=True)

        self._handle = (self.root / "trace.jsonl").open("w", encoding="utf-8")
        self._seen_blobs: set[str] = set()
        self._prev_page: dict | None = None
        self._prev_shot: str | None = None
        self._episode = 0
        self._step_in_episode = 0
        self.global_step = 0
        self._started = time.monotonic()

    @property
    def episode(self) -> int:
        """1-based index of the episode currently being recorded (0 before the first reset)."""
        return self._episode

    # -- run_rollout hooks -----------------------------------------------------

    def on_reset(self, observation: dict, info: dict) -> None:
        self._episode += 1
        self._step_in_episode = 0
        self._prev_page = self._page_side(info.get("page") or {})
        self._prev_shot = self._store_screenshot(observation)

    def on_step(
        self,
        action: int,
        observation: dict,
        reward: float,
        terminated: bool,
        truncated: bool,
        info: dict,
    ) -> None:
        if self._prev_page is None:  # on_step before on_reset — nothing to diff against
            return

        self._step_in_episode += 1
        self.global_step += 1

        after = self._page_side(info.get("page") or {})
        after_shot = self._store_screenshot(observation)
        before, before_shot = self._prev_page, self._prev_shot

        record = {
            "episode": self._episode,
            "step": self._step_in_episode,
            "global_step": self.global_step,
            "action": self._action_side(action, info),
            "before": {**before, "screenshot": before_shot},
            "after": {**after, "screenshot": after_shot},
            # Every JSON-safe key is preserved, not just success/error. Whitelisting
            # two fields silently dropped `left_application`, the flag that marks a
            # navigation the harness refused — so downstream the refusal was
            # indistinguishable from an ordinary failure and got rendered, and judged,
            # as a broken link. A recorder that discards what it does not recognize
            # will keep doing this every time the env learns to report something new.
            "exec": _json_safe(info.get("exec_info") or {}),
            "settled": bool(info.get("network_settled", True)),
            "opened_new_page": bool(info.get("opened_new_page")),
            "load_duration_s": round(float(info.get("load_duration_s", 0.0)), 3),
            "bug_signals": _serialize_signals(info.get("bug_signals")),
            "reward": round(float(reward), 4),
            "reward_breakdown": {
                k: v for k, v in info.items() if k not in _INFO_SKIP and isinstance(v, _JSON_SCALARS)
            },
            "terminated": bool(terminated),
            "truncated": bool(truncated),
            # Precomputed because it is the judge-call gating filter: a step that
            # changed nothing is the *interesting* case for a dead control (BUG-02),
            # not the one to skip.
            "changed": {
                "url": before["url"] != after["url"],
                "html": before["html"] != after["html"],
                "screenshot": before_shot != after_shot,
            },
        }
        self._handle.write(json.dumps(record, default=str) + "\n")
        self._prev_page, self._prev_shot = after, after_shot

    # -- internals -------------------------------------------------------------

    def _page_side(self, page: dict) -> dict:
        html = page.get("html") or ""
        return {
            "url": page.get("url", ""),
            "path": relative_path(page.get("url", "")),
            "html": self._store_blob(html),
            "console_errors": list(page.get("console_errors") or []),
            "page_errors": list(page.get("page_errors") or []),
            "network": _summarize_network(page.get("network")),
        }

    def _store_blob(self, html: str) -> str:
        if not html:
            return ""
        payload = html.encode("utf-8")
        digest = _sha256(payload)
        if digest not in self._seen_blobs:
            (self._pages_dir / f"{digest}.html").write_bytes(payload)
            self._seen_blobs.add(digest)
        return digest

    def _store_screenshot(self, observation: dict) -> str | None:
        """Content-address the frame. Hashes the array, not the PNG, so dedup is exact."""
        if not self.save_screenshots:
            return None
        frame = (observation or {}).get("screenshot")
        if not isinstance(frame, np.ndarray):
            return None
        digest = _sha256(np.ascontiguousarray(frame).tobytes())
        target = self._shots_dir / f"{digest}.png"
        if not target.is_file():
            from PIL import Image

            Image.fromarray(frame).save(target, format="PNG", optimize=True)
        return digest

    @staticmethod
    def _action_side(action: int, info: dict) -> dict:
        spec = info.get("action_spec")
        valid = int(info.get("num_valid_actions") or 0)
        if spec is None:
            return {"type": "?", "chosen_index": int(action), "was_valid": False}
        return {
            "type": spec.action_type.value,
            "chosen_index": int(action),
            "resolved_index": spec.index,
            # NO_OP is reachable both as a real choice (index 0) and as the fallback for
            # an out-of-range index; only the latter means the policy picked nothing.
            "was_valid": int(action) < valid,
            "selector": spec.selector,
            "element": spec.element_id,
            "description": spec.description,
            "params": {k: v for k, v in (spec.params or {}).items() if isinstance(v, _JSON_SCALARS)},
        }

    def close(self, extra_meta: dict | None = None) -> dict:
        self._handle.close()
        self.meta.update(extra_meta or {})
        self.meta.update(
            {
                "run_id": self.run_id,
                "episodes": self._episode,
                "steps": self.global_step,
                "distinct_pages": len(self._seen_blobs),
                "screenshots": self.save_screenshots,
                "wall_clock_s": round(time.monotonic() - self._started, 2),
            }
        )
        (self.root / "meta.json").write_text(json.dumps(self.meta, indent=2, default=str), encoding="utf-8")
        logger.info(
            "Trace {} -> {} ({} steps, {} episodes, {} distinct pages)",
            self.run_id, self.root, self.global_step, self._episode, len(self._seen_blobs),
        )
        return dict(self.meta)


def load_trace(run_dir: Path) -> Iterator[StepRecord]:
    """Stream a captured trace back as typed records, in order."""
    run_dir = Path(run_dir)
    path = run_dir / "trace.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"No trace.jsonl in {run_dir}")
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield StepRecord.from_dict(json.loads(line), root=run_dir)


def trace_dir_summary(run_dir: Path) -> dict:
    """Coverage counts for a captured trace, for a quick 'is this corpus usable?' check."""
    records = list(load_trace(run_dir))
    return {
        "steps": len(records),
        "episodes": len({r.episode for r in records}),
        # Query strings carry typed input values, and a boundary_max value is 400+
        # characters — unreadable in a coverage summary. Coverage is about which
        # documents were reached, so the path alone is the right granularity.
        "distinct_paths": sorted({r.after["path"].split("?", 1)[0] for r in records}),
        "steps_with_triggers": sum(1 for r in records if r.triggered),
        "steps_with_no_visible_change": sum(1 for r in records if not r.state_changed),
        "action_types": _counts(r.action.get("type", "?") for r in records),
    }


def _counts(values: Iterator[str]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for value in values:
        tally[value] = tally.get(value, 0) + 1
    return dict(sorted(tally.items(), key=lambda kv: -kv[1]))
