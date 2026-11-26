"""A small client for the OWASP ZAP API (stdlib only).

ZAP runs as a daemon whose proxy port also serves its API at the special host `zap`, so
every call goes through the proxy: `http://zap/JSON/<component>/<view|action>/<name>/`.
The API key is disabled in the study's containers (they listen only on the runner).
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class ZapError(RuntimeError):
    pass


@dataclass
class Zap:
    proxy: str = "http://127.0.0.1:18090"
    timeout_s: float = 120.0

    def _opener(self) -> urllib.request.OpenerDirector:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))

    def call(self, component: str, kind: str, name: str, **params) -> dict:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"http://zap/JSON/{component}/{kind}/{name}/" + (f"?{query}" if query else "")
        try:
            with self._opener().open(url, timeout=self.timeout_s) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise ZapError(f"{component}/{kind}/{name}: {exc.code} "
                           f"{exc.read()[:300].decode('utf-8', 'replace')}") from exc

    # -- lifecycle -----------------------------------------------------------

    def wait_ready(self, timeout_s: float = 300.0) -> str:
        deadline, last = time.monotonic() + timeout_s, ""
        while time.monotonic() < deadline:
            try:
                return self.call("core", "view", "version")["version"]
            except Exception as exc:  # noqa: BLE001 - ZAP still starting
                last = str(exc)
                time.sleep(3)
        raise TimeoutError(f"ZAP not ready after {timeout_s:.0f}s: {last}")

    def new_session(self) -> None:
        self.call("core", "action", "newSession", overwrite="true")

    def include_in_scope(self, regex: str, context: str = "app") -> None:
        with contextlib.suppress(ZapError):  # it may already exist
            self.call("context", "action", "newContext", contextName=context)
        self.call("context", "action", "includeInContext", contextName=context, regex=regex)

    # -- crawling (the baseline front end) -----------------------------------

    def spider(self, url: str, max_minutes: int, context: str = "app") -> str:
        self.call("spider", "action", "setOptionMaxDuration", Integer=max_minutes)
        return self.call("spider", "action", "scan", url=url, contextName=context,
                         recurse="true")["scan"]

    def spider_done(self, scan: str) -> bool:
        return int(self.call("spider", "view", "status", scanId=scan)["status"]) >= 100

    def ajax_spider(self, url: str, max_minutes: int, context: str = "app") -> None:
        self.call("ajaxSpider", "action", "setOptionMaxDuration", Integer=max_minutes)
        self.call("ajaxSpider", "action", "setOptionBrowserId", String="firefox-headless")
        self.call("ajaxSpider", "action", "scan", url=url, contextName=context,
                  inScope="true")

    def ajax_spider_running(self) -> bool:
        return self.call("ajaxSpider", "view", "status")["status"] == "running"

    def stop_crawlers(self) -> None:
        for component, name in (("spider", "stopAllScans"), ("ajaxSpider", "stop")):
            with contextlib.suppress(ZapError):  # nothing to stop
                self.call(component, "action", name)

    # -- scanning (identical for every front end) ----------------------------

    def wait_passive(self, timeout_s: float = 600.0) -> int:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            left = int(self.call("pscan", "view", "recordsToScan")["recordsToScan"])
            if left == 0:
                return 0
            time.sleep(2)
        return left

    def active_scan(self, url: str, max_minutes: int, context_id: str | None = None) -> str:
        self.call("ascan", "action", "setOptionMaxScanDurationInMins", Integer=max_minutes)
        return self.call("ascan", "action", "scan", url=url, recurse="true",
                         inScopeOnly="true", contextId=context_id)["scan"]

    def active_scan_progress(self, scan: str) -> int:
        return int(self.call("ascan", "view", "status", scanId=scan)["status"])

    def context_id(self, context: str = "app") -> str:
        return self.call("context", "view", "context", contextName=context)["context"]["id"]

    # -- results ---------------------------------------------------------------

    def alerts(self, base_url: str) -> list[dict]:
        out, start, page = [], 0, 500
        while True:
            batch = self.call("core", "view", "alerts", baseurl=base_url, start=start,
                              count=page)["alerts"]
            out += batch
            if len(batch) < page:
                return out
            start += page

    def urls(self, base_url: str) -> list[str]:
        return [u for u in self.call("core", "view", "urls", baseurl=base_url)["urls"]]

    def messages_count(self, base_url: str) -> int:
        return int(self.call("core", "view", "numberOfMessages", baseurl=base_url)
                   ["numberOfMessages"])

    def params(self, site: str) -> list[dict]:
        """ZAP's record of every parameter it saw on `site` (host[:port])."""
        found = self.call("params", "view", "params", site=site).get("Parameters", [])
        return [p for entry in found for p in entry.get("Parameter", [])]
