"""Captures HTTP request/response pairs on a Playwright page via its network events."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from ..utils.logging import get_logger
from .types import NetworkEvent

if TYPE_CHECKING:
    from playwright.sync_api import Page, Request, Response

logger = get_logger(__name__)

_MAX_RESPONSE_BODY_SNIPPET = 512
_MAX_REQUEST_BODY = 2048

# Reading a response body costs a full download+decode, so only do it where the bytes
# could plausibly carry a bug signal (an error page, a JSON API error). Fetching the
# body of every image/font/JS bundle would dominate per-step latency at 300K steps.
_TEXTUAL_CONTENT_TYPES = ("text/", "application/json", "application/xml", "application/javascript", "+json", "+xml")

# Requests that never receive a response (aborted navigations, long-poll sockets) would
# otherwise accumulate in `_pending` for the whole episode.
_MAX_PENDING = 512


class NetworkRecorder:
    """Buffers request/response pairs observed on a page since the last `drain()` call.

    Must be constructed after a fresh `Page` is created and stays attached for that
    page's lifetime; `WebTestingEnv` creates a new one per page (episode reset, and
    again if a click opens a popup that becomes the active page).
    """

    def __init__(self, page: "Page") -> None:
        self._page = page
        # Keyed by the Request object itself, NOT id(request): CPython reuses id()
        # values after garbage collection, and nothing else holds a reference to these
        # wrappers, so an id-keyed map can cross-attribute one request's method/headers
        # onto a later, unrelated request that happened to land at the same address.
        self._pending: dict[Any, dict] = {}
        self._events: list[NetworkEvent] = []
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)

    def _on_request(self, request: "Request") -> None:
        try:
            body = request.post_data
        except Exception:  # noqa: BLE001 - Playwright can raise on already-disposed requests
            body = None
        if len(self._pending) >= _MAX_PENDING:
            # Drop the oldest in-flight entry rather than growing without bound; losing
            # request metadata degrades an event, whereas a leak degrades the episode.
            self._pending.pop(next(iter(self._pending)), None)
        self._pending[request] = {
            "method": request.method,
            "headers": request.headers,
            "body": (body[:_MAX_REQUEST_BODY] if body else None),
            "resource_type": request.resource_type,
            "start": time.monotonic(),
        }

    def _on_response(self, response: "Response") -> None:
        request = response.request
        meta = self._pending.pop(request, None)
        start = meta["start"] if meta else time.monotonic()
        self._events.append(
            NetworkEvent(
                url=response.url,
                method=meta["method"] if meta else request.method,
                request_headers=meta["headers"] if meta else request.headers,
                request_body=meta["body"] if meta else None,
                response_status=response.status,
                response_headers=response.headers,
                response_body_snippet=self._body_snippet(response),
                timestamp=start,
                duration_ms=(time.monotonic() - start) * 1000,
                resource_type=meta["resource_type"] if meta else self._resource_type_of(request),
            )
        )

    def _on_request_failed(self, request: "Request") -> None:
        meta = self._pending.pop(request, None)
        start = meta["start"] if meta else time.monotonic()
        self._events.append(
            NetworkEvent(
                url=request.url,
                method=request.method,
                request_headers=meta["headers"] if meta else request.headers,
                request_body=meta["body"] if meta else None,
                response_status=None,
                response_headers={},
                response_body_snippet="",
                timestamp=start,
                duration_ms=(time.monotonic() - start) * 1000,
                failed=True,
                failure_text=request.failure,
                resource_type=meta["resource_type"] if meta else self._resource_type_of(request),
            )
        )

    @staticmethod
    def _resource_type_of(request: "Request") -> str:
        try:
            return request.resource_type
        except Exception:  # noqa: BLE001 - disposed request
            return "other"

    @staticmethod
    def _body_snippet(response: "Response") -> str:
        content_type = (response.headers or {}).get("content-type", "").lower()
        if not any(marker in content_type for marker in _TEXTUAL_CONTENT_TYPES):
            return ""
        try:
            return response.text()[:_MAX_RESPONSE_BODY_SNIPPET]
        except Exception:  # noqa: BLE001 - body may already be gone, or not decodable as text
            return ""

    def drain(self) -> list[NetworkEvent]:
        """Return every event captured since the last drain and clear the buffer."""
        events, self._events = self._events, []
        if self._pending:
            logger.debug("{} in-flight request(s) had no response before drain()", len(self._pending))
        return events
