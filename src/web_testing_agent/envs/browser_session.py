"""Owns the Playwright process/browser/context/page lifecycle for one episode.

Uses Playwright's *sync* API deliberately: Gymnasium/Stable-Baselines3 expect
`reset()`/`step()` to be plain synchronous methods (SB3 parallelizes via
subprocesses, not asyncio), so bridging an asyncio event loop into every single
step would add real complexity for no benefit here. The "async pipeline" called
for in the project spec is specifically about the semantic perception layer
running its three encoders concurrently — that happens at a later call site, once
this raw observation has already been captured.
"""

from __future__ import annotations

from typing import Callable
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from ..utils.logging import get_logger
from .types import CANONICAL_VIEWPORT

logger = get_logger(__name__)

DEFAULT_VIEWPORT = dict(CANONICAL_VIEWPORT)
NAVIGATION_TIMEOUT_MS = 15_000

# How long the DOM must stay unmutated before we call the page settled, and the hard
# ceiling on waiting for that. `networkidle` is deliberately NOT used: it requires 500ms
# of *zero* network activity, which apps with polling or websockets (Nextcloud, Gitea's
# notification poller) never reach — so every step would pay the full timeout.
DOM_QUIET_MS = 250
SETTLE_TIMEOUT_MS = 3_000

_QUIESCENCE_JS = """
([quietMs, timeoutMs]) => new Promise(resolve => {
    let settleTimer = null;
    let done = false;
    const finish = (quiesced) => {
        if (done) return;
        done = true;
        observer.disconnect();
        clearTimeout(settleTimer);
        clearTimeout(hardStop);
        resolve(quiesced);
    };
    const observer = new MutationObserver(() => {
        clearTimeout(settleTimer);
        settleTimer = setTimeout(() => finish(true), quietMs);
    });
    observer.observe(document, {
        childList: true, subtree: true, attributes: true, characterData: true,
    });
    settleTimer = setTimeout(() => finish(true), quietMs);
    const hardStop = setTimeout(() => finish(false), timeoutMs);
})
"""


class BrowserSession:
    """One Playwright instance -> one Chromium browser -> one fresh context+page per episode."""

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._known_pages: set[int] = set()

    def start(self) -> None:
        if self._playwright is not None:
            return
        logger.info("Launching Playwright Chromium (headless={})", self._headless)
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)

    def new_episode(self, base_url: str) -> Page:
        """Fresh context (no cookies/storage carried over from any previous episode) + navigate."""
        self.start()
        assert self._browser is not None
        if self.context is not None:
            self.context.close()
        self.context = self._browser.new_context(viewport=dict(CANONICAL_VIEWPORT))
        self.context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
        self.page = self.context.new_page()
        logger.info("Navigating to {}", base_url)
        self.page.goto(base_url, wait_until="domcontentloaded")
        self.wait_settled()
        return self.page

    def mark_pages(self) -> None:
        """Record which tabs exist *before* an action runs.

        Without this, `take_new_page` cannot tell a tab the current action opened from
        one an earlier action opened and that Playwright only got round to reporting
        now. That distinction is not academic: popup creation is racy (see
        `take_new_page`), so a tab opened at step 1 routinely first appears in
        `context.pages` at step 2 or later, and is then adopted and attributed to
        whatever action happened to be running.

        Measured on Gitea: a click on the *internal* link `/user/login?redirect_to=%2f`
        was recorded as `left_application: https://github.com/go-gitea/gitea`, and so
        was a click on the `remember` **checkbox** — which cannot navigate anywhere. The
        harness then "restored" the page it had never left, and the reload wiped the
        typed form values, which the judge reported as the application clearing the
        user's input at 90-95% confidence. Three of six positives in one run came from
        this, plus a `broken_navigation` verdict on a link that is perfectly correct.
        """
        self._known_pages = {id(p) for p in (self.context.pages if self.context else [])}

    def take_new_page(self, accept: "Callable[[str], bool] | None" = None) -> tuple[Page | None, str]:
        """Adopt a tab the *current* action opened, if it is one we want to follow.

        A `target="_blank"` link or `window.open()` creates a page Playwright does not
        follow automatically. Without adopting it the env keeps observing the old, now
        stale page and reports the (correct) unchanged URL as a broken link.

        `context.pages` is the authority rather than a buffered `page` event, because
        the event is only dispatched when the sync API next yields to its event loop —
        checking a buffer immediately after `click()` returns is reliably too early.
        Callers must therefore invoke this *after* the post-action settle wait, having
        called `mark_pages()` before the action.

        `accept` decides whether a new tab is worth following. A tab pointing outside
        the application is closed rather than adopted: adopting it only to refuse and
        restore leaves the harness reporting a navigation the current page never made.
        Returns `(adopted_page_or_None, offsite_url_or_empty)`.
        """
        if self.context is None or self.page is None:
            return None, ""
        known = getattr(self, "_known_pages", set())
        candidates = [
            p for p in self.context.pages
            if p is not self.page and not p.is_closed() and id(p) not in known
        ]
        # Anything left over from an earlier step is stale by construction: its own step
        # has already been observed, scored and recorded. Close it so it cannot be
        # adopted later and blamed on an unrelated action.
        for page in self.context.pages:
            if page is not self.page and not page.is_closed() and id(page) in known:
                try:
                    logger.debug("Closing stale tab from an earlier step: {}", page.url[:80])
                    page.close()
                except Exception:  # noqa: BLE001 - best-effort teardown
                    logger.debug("Could not close stale page")
        if not candidates:
            return None, ""

        adopted = candidates[-1]
        if accept is not None and not accept(adopted.url):
            # An off-site tab tells us nothing about the application under test, and the
            # page the click came from correctly did not change. Close it and say so, so
            # the renderer can report "this link opens in a new tab" rather than either
            # "nothing happened" or "the harness blocked a navigation".
            offsite = adopted.url
            logger.info("Discarding off-site tab opened by this action: {}", offsite[:100])
            for page in candidates:
                try:
                    page.close()
                except Exception:  # noqa: BLE001 - best-effort teardown
                    logger.debug("Could not close off-site popup")
            return None, offsite
        logger.info("Adopting popup/new tab as active page: {}", adopted.url)
        previous = self.page
        self.page = adopted
        try:
            adopted.set_viewport_size(dict(CANONICAL_VIEWPORT))
        except Exception:  # noqa: BLE001 - best effort, popup may still be initializing
            logger.debug("Could not normalize popup viewport")
        # Close every superseded page so a later step cannot re-adopt a stale one.
        for page in [previous, *candidates[:-1]]:
            if page is None or page is adopted or page.is_closed():
                continue
            try:
                page.close()
            except Exception:  # noqa: BLE001 - best-effort teardown
                logger.debug("Could not close superseded page")
        return adopted, ""

    def history_move(self, direction: str, timeout_ms: int = NAVIGATION_TIMEOUT_MS) -> bool:
        """Go back/forward in history, refusing to leave the application.

        A fresh Playwright context starts on `about:blank`, so the episode's opening
        `goto()` leaves that entry in history: BROWSER_BACK from the landing page lands
        on `about:blank`, whose empty netloc reads as "left the target domain" and ends
        the episode. Measured on the toy site, a DQN learned to fire this on step 1 of
        every episode — with per-step rewards mostly negative, ending immediately caps
        the loss, so mean episode length collapsed to 1.05 steps.

        The same guard covers real targets: Gitea and Nextcloud are full of download
        links and `blob:`/`data:` URLs that are not navigations out of the app either.

        Returns True if the move landed somewhere still inside the app.
        """
        if self.page is None:
            return False
        before = self.page.url
        try:
            if direction == "back":
                self.page.go_back(timeout=timeout_ms)
            else:
                self.page.go_forward(timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001 - no history entry is a legitimate no-op
            logger.debug("history_move({}) failed: {}", direction, exc)
            return False

        after = self.page.url
        if after == before:
            return False
        if self.is_in_app(after, before):
            return True

        logger.debug("history_move({}) escaped the app ({} -> {}); restoring", direction, before, after)
        try:
            self.page.goto(before, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:  # noqa: BLE001 - best effort restore
            logger.warning("Could not restore {} after an escaping history move", before)
        return False

    def restore(self, url: str, timeout_ms: int = NAVIGATION_TIMEOUT_MS) -> bool:
        """Return to `url` after a navigation left the application.

        Preferring `go_back` keeps the history stack coherent — a `goto` would append a
        new entry, so BROWSER_BACK would then walk straight back onto the off-site page
        the agent was just refused. Falls back to `goto` when history cannot deliver.
        """
        if self.page is None:
            return False
        try:
            self.page.go_back(timeout=timeout_ms)
            if self.is_in_app(self.page.url, url):
                return True
        except Exception as exc:  # noqa: BLE001 - no history entry, or a blocked back
            logger.debug("restore() could not go back: {}", exc)
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return True
        except Exception:  # noqa: BLE001
            logger.warning("Could not restore {} after leaving the application", url)
            return False

    @staticmethod
    def is_in_app(url: str, reference: str) -> bool:
        """Whether `url` is a real in-app page relative to `reference`.

        Non-http(s) schemes (`about:`, `blob:`, `data:`, `chrome:`) are never in-app,
        and are also never a legitimate "the agent navigated away" signal — they are
        artifacts of downloads, blank pages, and browser internals.
        """
        if not url.lower().startswith(("http://", "https://")):
            return False
        return BrowserSession.domain_of(url) == BrowserSession.domain_of(reference)

    def reset_viewport(self) -> None:
        """Restore the canonical viewport (used at episode start, after RESIZE_VIEWPORT)."""
        if self.page is None:
            return
        try:
            self.page.set_viewport_size(dict(CANONICAL_VIEWPORT))
        except Exception:  # noqa: BLE001
            logger.debug("Could not reset viewport")

    def wait_settled(self, timeout_ms: int = SETTLE_TIMEOUT_MS, quiet_ms: int = DOM_QUIET_MS) -> bool:
        """Wait until the DOM stops mutating. Returns False if it never settled in time.

        A False return is the honest signal behind the spec's "loading spinner visible
        >5s" bug trigger: the page is still churning when the agent's patience runs out.
        """
        if self.page is None:
            return False
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:  # noqa: BLE001 - still navigating; the quiescence probe below still applies
            logger.debug("domcontentloaded not reached within {}ms", timeout_ms)
        for attempt in (1, 2):
            try:
                return bool(self.page.evaluate(_QUIESCENCE_JS, [quiet_ms, timeout_ms]))
            except Exception as exc:  # noqa: BLE001
                # A navigation commits mid-evaluate and destroys the execution context.
                # That is normal after a link click; retry once against the new document.
                if attempt == 1:
                    logger.debug("Quiescence probe lost its execution context, retrying: {}", exc)
                    continue
                logger.debug("Quiescence probe failed twice: {}", exc)
                return False
        return False

    @staticmethod
    def domain_of(url: str) -> str:
        """Normalized host[:port] used for the 'left the target domain' episode check.

        `localhost` and `127.0.0.1` are folded together, and a leading `www.` is dropped,
        so a legitimate redirect between those forms does not end the episode.
        """
        netloc = urlparse(url).netloc.lower()
        host, _, port = netloc.partition(":")
        if host == "127.0.0.1" or host == "[::1]":
            host = "localhost"
        if host.startswith("www."):
            host = host[4:]
        return f"{host}:{port}" if port else host

    def close(self) -> None:
        if self.context is not None:
            try:
                self.context.close()
            except Exception:  # noqa: BLE001 - best-effort teardown
                logger.warning("Error closing browser context")
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:  # noqa: BLE001
                logger.warning("Error closing browser")
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:  # noqa: BLE001
                logger.warning("Error stopping Playwright")
        self.context = None
        self.page = None
        self._browser = None
        self._playwright = None
