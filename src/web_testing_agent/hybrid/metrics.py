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

For applications whose client code is instrumented with Istanbul (the external benchmark's
subject applications, as WebExplor and WebRLED measured them), `IstanbulCollector` reads the
instrumented code's own counters (`window.__coverage__`) and reports **branch coverage** in
the papers' sense.
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


#: Compact view of Istanbul's counters: per file, the branch ids and which of each branch's
#: arms have executed ("1"/"0" per arm), plus statement hits. Kept small because it is read
#: after every step.
_ISTANBUL_SNAPSHOT = """() => {
  const out = {};
  const pack = (cov) => {
    for (const [file, data] of Object.entries(cov || {})) {
      const b = {};
      for (const [id, arms] of Object.entries(data.b || {})) {
        b[id] = arms.map((c) => (c > 0 ? "1" : "0")).join("");
      }
      const s = Object.keys(data.s || {}).length;
      const sh = Object.values(data.s || {}).filter((c) => c > 0).length;
      out[file] = Object.assign(out[file] || {}, { b, s, sh });
    }
  };
  try { pack(JSON.parse(sessionStorage.getItem("__wta_istanbul_prev") || "{}")); } catch (e) {}
  pack(window.__coverage__);
  return out;
}"""

#: Before a page unloads (a full navigation resets `window.__coverage__`), keep the counters
#: it reached in the tab's session storage so the next sample still sees them.
_ISTANBUL_KEEP_ON_UNLOAD = """
window.addEventListener("pagehide", () => {
  try {
    if (window.__coverage__) {
      sessionStorage.setItem("__wta_istanbul_prev", JSON.stringify(window.__coverage__));
    }
  } catch (e) {}
});
"""


class IstanbulCollector:
    """Branch coverage of Istanbul-instrumented client code, unioned over a run.

    Every episode starts a fresh browser context, and multi-page applications reset the
    counters on each navigation; the collector therefore keeps, per file, the set of branch
    arms ever seen executed, and the number of arms each branch has. Coverage is covered arms
    over all arms of the files the application loaded, as Istanbul's own report computes it.
    """

    def __init__(self) -> None:
        #: file -> branch id -> (arms, covered-arm bit set)
        self.branches: dict[str, dict[str, tuple[int, int]]] = {}
        #: file -> (statements, statements ever hit), the hit count kept as a maximum
        self.statements: dict[str, tuple[int, int]] = {}
        self.samples = 0
        self.errors = 0

    def install(self, env) -> None:  # noqa: ANN001
        """Chain onto the session's new-page hook, keeping any hook already installed."""
        session = env._browser  # noqa: SLF001 - the session's documented hook
        previous = session.on_new_page

        def hook(page) -> None:  # noqa: ANN001
            if previous is not None:
                previous(page)
            try:
                page.add_init_script(_ISTANBUL_KEEP_ON_UNLOAD)
            except Exception as exc:  # noqa: BLE001 - coverage must never fail a run
                self.errors += 1
                logger.debug("istanbul unload hook not installed: {}", exc)

        session.on_new_page = hook

    def sample(self, env) -> None:  # noqa: ANN001
        page = getattr(getattr(env, "_browser", None), "page", None)
        if page is None:
            return
        try:
            snapshot = page.evaluate(_ISTANBUL_SNAPSHOT)
        except Exception as exc:  # noqa: BLE001 - a navigating page costs one sample
            self.errors += 1
            if self.errors <= 3:
                logger.debug("istanbul sample skipped: {}", exc)
            return
        self.samples += 1
        self.merge(snapshot or {})

    def merge(self, snapshot: dict) -> None:
        for file, data in snapshot.items():
            known = self.branches.setdefault(file, {})
            for branch_id, arms in (data.get("b") or {}).items():
                bits = int(arms[::-1], 2) if arms else 0
                count, covered = known.get(branch_id, (len(arms), 0))
                known[branch_id] = (max(count, len(arms)), covered | bits)
            total, hit = self.statements.get(file, (0, 0))
            self.statements[file] = (max(total, int(data.get("s", 0))),
                                     max(hit, int(data.get("sh", 0))))

    def summary(self) -> dict:
        arms = sum(n for branches in self.branches.values() for n, _ in branches.values())
        covered = sum(bits.bit_count() for branches in self.branches.values()
                      for _, bits in branches.values())
        statements = sum(t for t, _ in self.statements.values())
        hit = sum(h for _, h in self.statements.values())
        return {
            "files": len(self.branches),
            "branches": arms,
            "branches_covered": covered,
            "branch_coverage": round(covered / arms, 4) if arms else None,
            "statements": statements,
            "statements_hit_max": hit,
            "samples": self.samples,
            "sample_errors": self.errors,
        }
