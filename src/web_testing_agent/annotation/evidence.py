"""Turning a captured trace into evidence references a bug report can carry.

**This adds no storage.** `TraceRecorder` already content-addresses every artifact a
finding could cite: page bodies to `pages/<sha256>.html`, frames to
`screenshots/<sha256>.png`, with network and console events summarized inline on each
record. A second artifact store would duplicate that and immediately risk disagreeing
with it. This module only *reads* a trace and reduces it to the references for the
handful of steps a report actually mentions.

**The join key already existed.** `run_rollout` calls `recorder.on_step(...)` and then
increments `report.steps`, and `TraceRecorder.on_step` increments `global_step` first, so
the two counters carry the same value for the same transition. `Finding.first_seen_step`
is that counter, and `_from_trigger` copies it into `ReportedBug.step`; the live judge
records the same global step on every verdict. So a reported bug already names the exact
transition that produced it — what was missing was anything to look the number up in.
`tests/unit/test_evidence.py` pins the alignment rather than trusting this paragraph.

**Only the cited steps are indexed.** A 200-step rollout produces 200 records, and a
report mentioning four of them has no use for the rest — they stay in the trace on disk,
where the full record is still available to anyone who wants it. Indexing everything
would put the bulk of a trace into `run_meta`, which is the thing this module exists to
avoid.

**References, never contents.** Digests and relative paths only. A screenshot embedded in
a report is a report nobody can diff and a document that grows without bound; a digest is
16 characters that resolve to the exact bytes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..utils.logging import get_logger

logger = get_logger(__name__)

# How many network events to name per step. The full trace keeps all of them; a report
# needs enough to see what went wrong, not a transcript.
_NETWORK_MAX = 5
# Console text is untrusted application output of unbounded length.
_TEXT_MAX = 200


def _side(payload: dict) -> dict:
    """The reference half of one side of a transition: digests, never bodies."""
    reference = {"url": payload.get("url", ""), "html": payload.get("html") or ""}
    shot = payload.get("screenshot")
    if shot:
        reference["screenshot"] = shot
    return reference


def _network_errors(events: Iterable[dict] | None) -> list[dict]:
    """Only the failing requests. A successful fetch is not evidence of anything."""
    failures = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        status = event.get("status")
        if event.get("failed") or (isinstance(status, int) and status >= 400):
            failures.append({
                "url": str(event.get("url", ""))[:_TEXT_MAX],
                "status": status,
                "method": event.get("method", ""),
            })
        if len(failures) >= _NETWORK_MAX:
            break
    return failures


def evidence_for_record(record: dict) -> dict:
    """One trace record reduced to what a finding needs to cite."""
    before, after = record.get("before") or {}, record.get("after") or {}
    action = record.get("action") or {}
    entry = {
        "episode": record.get("episode", 0),
        "step_in_episode": record.get("step", 0),
        "url": after.get("url", ""),
        "action": " ".join(
            part for part in (str(action.get("type", "")), str(action.get("element") or ""))
            if part
        ).strip(),
        "before": _side(before),
        "after": _side(after),
        "changed": record.get("changed") or {},
    }
    console = [str(text)[:_TEXT_MAX] for text in (after.get("console_errors") or [])]
    page_errors = [str(text)[:_TEXT_MAX] for text in (after.get("page_errors") or [])]
    if console:
        entry["console_errors"] = console[:_NETWORK_MAX]
    if page_errors:
        entry["page_errors"] = page_errors[:_NETWORK_MAX]
    failures = _network_errors(after.get("network"))
    if failures:
        entry["network_errors"] = failures
    signals = record.get("bug_signals") or {}
    if signals.get("any_triggered"):
        entry["triggered"] = True
    return entry


def build_evidence_index(
    trace_root: Path,
    steps: Iterable[int] | None = None,
    *,
    base: Path | None = None,
) -> dict:
    """Reference the artifacts for `steps`, read from a trace directory.

    `steps` are global step numbers — the value a `ReportedBug` already carries. `None`
    indexes every recorded step, which is useful for a short diagnostic run and wasteful
    for a long one.

    `base` makes `trace_dir` relative to it, so a report can be moved alongside its
    evidence without the reference breaking. Falls back to an absolute path when the two
    are on different roots, because a wrong relative path is worse than a long one.

    Never raises: a missing or malformed trace yields an index that says so. Evidence is
    a decoration on a finding, and a finding that was real without it is still real.
    """
    trace_root = Path(trace_root)
    wanted = {int(s) for s in steps} if steps is not None else None

    location = str(trace_root)
    if base is not None:
        try:
            location = trace_root.resolve().relative_to(Path(base).resolve()).as_posix()
        except ValueError:
            location = str(trace_root)

    index: dict = {
        "trace_dir": location,
        "pages_dir": "pages",
        "screenshots_dir": "screenshots",
        "by_step": {},
    }

    jsonl = trace_root / "trace.jsonl"
    if not jsonl.is_file():
        index["available"] = False
        index["error"] = f"no trace.jsonl under {trace_root}"
        logger.warning("evidence index: {}", index["error"])
        return index

    matched = 0
    try:
        with jsonl.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                step = int(record.get("global_step", 0) or 0)
                if wanted is not None and step not in wanted:
                    continue
                index["by_step"][str(step)] = evidence_for_record(record)
                matched += 1
    except OSError as exc:  # noqa: BLE001 - a decoration must not fail the report
        index["available"] = False
        index["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("evidence index could not be read: {}", index["error"])
        return index

    index["available"] = True
    index["indexed_steps"] = matched
    if wanted is not None:
        missing = sorted(wanted - {int(k) for k in index["by_step"]})
        if missing:
            # Recorded rather than ignored: a cited step with no record means the trace
            # and the report disagree about what happened, which is worth seeing.
            index["missing_steps"] = missing
    return index
