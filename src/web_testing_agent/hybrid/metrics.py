"""Two run metrics the web-testing literature reports, recorded next to seeded faults.

- **Unique failures** (WebExplor [zheng2021webexplor], WebRLED [gu2025webrled]): distinct
  console errors, page errors and failed requests seen during a run, deduplicated by the
  product's own trigger identity (`tester.triggers.signatures`: kind, page path, normalised
  message). Not replayed: the papers count them the same way (deduplicated, unverified).
- **Client-side code coverage**: the share of the application's own JavaScript (same-origin
  scripts, inline ones included) that executed, from Chrome's V8 precise block coverage over
  the DevTools protocol, sampled after every step and unioned over the run.

The profiler is attached to each episode's page before it navigates
(`BrowserSession.on_new_page`): V8 tracks precise coverage only for code compiled after it
starts, so attaching later would miss a single-page app's whole script. Caveat (stated
wherever it is reported): this is executed-byte (block) coverage of client-side code, not the
Istanbul branch coverage the papers report; arms are compared on the same measure.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..tester.triggers import signatures
from ..utils.logging import get_logger

logger = get_logger(__name__)

#: Trigger kinds that are failures in the literature's sense (errors the browser reports).
FAILURE_KINDS = frozenset({"console_error", "http_error", "document_http_error"})


class FailureCounter:
    """Distinct failure identities over a run, with the step each was first seen."""

    def __init__(self) -> None:
        self.first_seen: dict[tuple[str, str, str], int] = {}
        self.other: dict[tuple[str, str, str], int] = {}

    def observe(self, info: dict, step: int) -> None:
        for identity in signatures(info):
            target = self.first_seen if identity[0] in FAILURE_KINDS else self.other
            target.setdefault(identity, step)

    def summary(self) -> dict:
        by_kind: dict[str, int] = {}
        for kind, _, _ in self.first_seen:
            by_kind[kind] = by_kind.get(kind, 0) + 1
        return {
            "unique_failures": len(self.first_seen),
            "by_kind": by_kind,
            "first_failure_at": min(self.first_seen.values(), default=None),
            "failures": [
                {"kind": k, "page": p, "detail": d, "first_seen_at": s}
                for (k, p, d), s in sorted(self.first_seen.items(), key=lambda x: x[1])
            ],
            "other_triggers": len(self.other),
        }


class CoverageCollector:
    """Union of executed byte ranges per same-origin script, from V8 precise coverage."""

    def __init__(self, origin: str) -> None:
        self.origin = origin
        #: script -> (length in bytes, bit set of executed byte offsets)
        self.scripts: dict[str, tuple[int, int]] = {}
        self._page = None
        self._cdp = None
        self.samples = 0
        self.errors = 0

    def install(self, env) -> None:  # noqa: ANN001
        """Profile every episode's page from before its first navigation."""
        env._browser.on_new_page = self.attach  # noqa: SLF001 - the session's documented hook

    def attach(self, page) -> None:  # noqa: ANN001
        try:
            self._cdp = page.context.new_cdp_session(page)
            self._cdp.send("Profiler.enable")
            self._cdp.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
            self._page = page
        except Exception as exc:  # noqa: BLE001 - no profiler means no coverage, not a failed run
            self.errors += 1
            self._page = self._cdp = None
            logger.debug("coverage profiler not attached: {}", exc)

    def sample(self, env) -> None:  # noqa: ANN001
        page = getattr(getattr(env, "_browser", None), "page", None)
        if page is None or page is not self._page or self._cdp is None:
            return
        try:
            result = self._cdp.send("Profiler.takePreciseCoverage")
        except Exception as exc:  # noqa: BLE001 - a closed page or target costs one sample
            self.errors += 1
            if self.errors <= 3:
                logger.debug("coverage sample skipped: {}", exc)
            self._page = None
            return
        self.samples += 1
        for script in result.get("result", []):
            url = script.get("url") or ""
            if not url.startswith(self.origin):
                continue
            functions = [f for f in script.get("functions") or [] if f.get("ranges")]
            if not functions:
                continue
            key = urlsplit(url)._replace(query="", fragment="").geturl()
            # The script's length is its top-level function's range (starting at 0). After
            # the first take V8 lists only functions that ran since the last one, so the
            # top-level entry may be absent: keep the length already known then.
            top = [f["ranges"][0]["endOffset"] for f in functions
                   if f["ranges"][0]["startOffset"] == 0]
            known = self.scripts.get(key)
            if top:
                length = max(top)
                covered = known[1] if known and known[0] == length else 0
            elif known:
                length, covered = known
            else:
                length = max(r["endOffset"] for f in functions for r in f["ranges"])
                covered = 0
            # Block coverage nests ranges; the innermost range decides a byte's count. Paint
            # outer to inner (start ascending, longer first) on a bit set, then keep any byte
            # ever executed (counts reset at every take, so samples are unioned).
            ranges = sorted(
                (r for f in functions for r in f.get("ranges", [])),
                key=lambda r: (r["startOffset"], -r["endOffset"]),
            )
            painted = 0
            for r in ranges:
                start, end = max(0, r["startOffset"]), min(length, r["endOffset"])
                if end <= start:
                    continue
                mask = ((1 << (end - start)) - 1) << start
                painted = painted | mask if r.get("count", 0) > 0 else painted & ~mask
            self.scripts[key] = (length, covered | painted)

    def summary(self) -> dict:
        total = sum(length for length, _ in self.scripts.values())
        covered = sum(bits.bit_count() for _, bits in self.scripts.values())
        return {
            "js_bytes": total,
            "js_bytes_covered": covered,
            "js_coverage": round(covered / total, 4) if total else None,
            "scripts": len(self.scripts),
            "samples": self.samples,
            "sample_errors": self.errors,
        }
