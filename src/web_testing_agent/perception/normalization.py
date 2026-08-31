"""Canonicalization of volatile page content before encoding or state comparison.

Real applications embed CSRF tokens, session ids, timestamps, and (on anything with a
built asset pipeline — Nextcloud, OpenCart) content-hashed CSS class and file names
directly in their HTML. Fed raw into CodeBERT, two visits to the *same logical page*
produce different embeddings, which destabilizes the 256-dim state the RL agents learn
over and makes "have I seen this state before?" unanswerable.

Everything here is deliberately pure string logic with no model dependency, so it is
unit-testable and runs identically in the env process and the encoder process.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Ordered: earlier patterns win, so specific forms (UUID) are masked before the
# generic long-hex rule can claim part of them.
_VOLATILE_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "uuid",
        re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
        "<UUID>",
    ),
    (
        "iso_datetime",
        re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?\b"),
        "<TIMESTAMP>",
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "<JWT>",
    ),
    (
        # Content-hashed asset names: app.a1b2c3d4.js, main-9f8e7d6c.css
        "hashed_asset",
        re.compile(r"([./-])[0-9a-fA-F]{8,32}(\.(?:js|css|woff2?|png|jpg|svg|map))\b"),
        r"\1<HASH>\2",
    ),
    (
        # CSRF tokens, session ids, API keys: long opaque hex/base64-ish runs.
        "long_token",
        re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
        "<TOKEN>",
    ),
    (
        "epoch_millis",
        re.compile(r"\b1[0-9]{12}\b"),
        "<EPOCH>",
    ),
    (
        "epoch_seconds",
        re.compile(r"\b1[0-9]{9}\b"),
        "<EPOCH>",
    ),
)

# Query parameters whose values are per-request noise rather than page identity.
_VOLATILE_QUERY_KEYS = frozenset(
    {"csrf", "csrf_token", "_csrf", "token", "authenticity_token", "nonce", "_", "ts", "timestamp", "cachebust", "v"}
)

# Headers whose *value* is per-request or per-session noise. The header **name** is kept
# in every case: "this response carried a Set-Cookie" and "this request carried an
# Authorization" are real signals about application behaviour (and Agent A's whole
# premise), while the rotating value behind them is not.
_VOLATILE_HEADERS = frozenset({
    "date", "age", "expires", "last-modified", "etag", "if-modified-since",
    "if-none-match", "set-cookie", "cookie", "authorization", "x-request-id",
    "request-id", "traceparent", "x-runtime", "x-csrf-token", "keep-alive",
})

# Coarse, log-ish buckets for request duration. A raw float is clock jitter — two
# identical navigations measured 16.00000006 ms and 15.99999983 ms — but *how slow* a
# request was is genuine signal the `slow_response` trigger is built on, so it is
# retained at a resolution the application can actually be responsible for.
_DURATION_BUCKETS: tuple[tuple[float, str], ...] = (
    (50.0, "<50ms"), (200.0, "<200ms"), (1000.0, "<1s"), (5000.0, "<5s"),
)

_SCRIPT_BODY = re.compile(r"(<script\b[^>]*>)(.*?)(</script>)", re.IGNORECASE | re.DOTALL)
_STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")


def canonicalize_text(text: str) -> str:
    """Replace volatile substrings (tokens, timestamps, hashes) with stable placeholders."""
    for _name, pattern, replacement in _VOLATILE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def canonicalize_url(url: str) -> str:
    """Strip per-request query noise and volatile path segments from a URL.

    Path ids are deliberately *kept*: `/issues/1` and `/issues/2` are genuinely
    different application states, unlike a rotating CSRF token.
    """
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _VOLATILE_QUERY_KEYS]
    query = urlencode([(k, canonicalize_text(v)) for k, v in sorted(kept)])
    return urlunsplit((parts.scheme, parts.netloc, canonicalize_text(parts.path), query, ""))


def _bucket_duration(duration_ms) -> str:
    if duration_ms is None:
        return "unknown"
    try:
        value = float(duration_ms)
    except (TypeError, ValueError):
        return "unknown"
    for ceiling, label in _DURATION_BUCKETS:
        if value < ceiling:
            return label
    return ">=5s"


def _canonicalize_headers(headers) -> dict:
    if not isinstance(headers, dict):
        return {}
    return {
        str(name).lower(): (
            "<VOLATILE>" if str(name).lower() in _VOLATILE_HEADERS
            else canonicalize_text(str(value))
        )
        for name, value in sorted(headers.items())
    }


def canonicalize_network_trace(network_json: str, page_url: str = "") -> str:
    """Reduce a captured network trace to the parts that identify application *state*.

    **This is the observation channel only.** `info["page"]["network"]` keeps the full
    trace, and the judge window, the trace corpus and the bug report all read that —
    forensic evidence (exact timings, exact headers, exact bodies) is untouched by
    anything here. What this exists for is the other consumer: the encoded observation
    the RL agent learns over.

    **The defect it fixes, measured 2026-08-27.** Two identical navigations
    (`index.html` -> `order-1.html`, same seed, same page) produced *unrelated* network
    vectors — cosine +0.018. Three fields differed: the HTTP `date` response header, the
    monotonic-clock `timestamp` (which the `epoch_*` patterns do not match, being 7
    digits rather than 10), and `duration_ms`, differing in its eighth decimal.
    `HashEmbeddingEncoder` maps any byte difference to an orthogonal vector, so **384 of
    1,664 observation dimensions were fresh noise on every step that touched the
    network** — and the visual and structural blocks were bit-identical, so it was the
    whole of the instability. DQN training was consequently not reproducible at a fixed
    seed (one toy seed moved from 6 unique states to 1) while the random baselines, which
    never read an observation, reproduced bit-for-bit.

    What is kept, because it distinguishes one application state from another: method,
    status, resource type, failure, the URL (origin-relative), header *names*, bodies,
    and a coarse duration bucket. What is dropped or masked: wall-clock and monotonic
    timestamps, sub-millisecond timing precision, and the values of per-request or
    per-session headers.

    `page_url` supplies the origin. Same-origin URLs collapse to `<ORIGIN>/path` so an
    ephemeral test-server port (`127.0.0.1:58614`, different on every run) stops making
    the same page look like a different one across processes; a *foreign* origin is left
    intact, because on-site versus off-site is exactly the distinction that matters.

    Falls back to plain `canonicalize_text` if the trace does not parse — a truncated or
    malformed trace should degrade, not raise, inside an observation pipeline.
    """
    try:
        events = json.loads(network_json) if network_json else []
    except (ValueError, TypeError):
        logger_fallback = canonicalize_text(network_json or "")
        return logger_fallback
    if not isinstance(events, list):
        return canonicalize_text(network_json or "")

    origin = ""
    if page_url:
        parts = urlsplit(page_url)
        origin = f"{parts.scheme}://{parts.netloc}"

    reduced = []
    for event in events:
        if not isinstance(event, dict):
            continue
        reduced.append({
            "url": canonicalize_url(str(event.get("url", ""))),
            "method": str(event.get("method", "")),
            "status": event.get("response_status"),
            "type": str(event.get("resource_type", "")),
            "failed": bool(event.get("failed", False)),
            "failure": canonicalize_text(str(event.get("failure_text") or "")),
            "duration": _bucket_duration(event.get("duration_ms")),
            "request_headers": _canonicalize_headers(event.get("request_headers")),
            "response_headers": _canonicalize_headers(event.get("response_headers")),
            "request_body": canonicalize_text(str(event.get("request_body") or ""))[:512],
            "response_body": canonicalize_text(str(event.get("response_body_snippet") or ""))[:512],
        })
    rendered = json.dumps(reduced, sort_keys=True, default=str)
    # Applied to the whole serialized trace rather than to the `url` field alone: the
    # origin also appears in `referer`, in an `origin` header, and inside response
    # bodies, and an ephemeral test-server port left in any one of them is enough to
    # make two identical states hash to unrelated vectors.
    if origin:
        rendered = rendered.replace(origin, "<ORIGIN>")
    return canonicalize_text(rendered)


def preprocess_for_structural_encoder(html: str, max_chars: int = 8_000) -> str:
    """Reduce raw page HTML to the compact, canonicalized form fed to CodeBERT.

    Per the spec: drop `<style>` blocks and script *bodies* while keeping inline event
    handlers (`onclick=...` is behaviour, and behaviour is what we are judging), and
    surface forms and anchors explicitly so the encoder does not have to rediscover
    them from raw markup inside a 512-token budget.
    """
    stripped = _COMMENT.sub("", html)
    stripped = _STYLE_BLOCK.sub("", stripped)
    stripped = _SCRIPT_BODY.sub(r"\1\3", stripped)

    structure = extract_structure(stripped)
    lines: list[str] = [f"TITLE: {structure.title}"] if structure.title else []
    for form in structure.forms:
        fields = " ".join(f"{name}:{ftype}" for name, ftype in form.fields)
        lines.append(f"FORM action={form.action} method={form.method} fields=[{fields}]")
    for href, text in structure.anchors:
        lines.append(f"LINK href={href} text={text}")
    for handler, value in structure.inline_handlers:
        lines.append(f"HANDLER {handler}={value}")
    if structure.headings:
        lines.append("HEADINGS: " + " | ".join(structure.headings))

    rendered = canonicalize_text("\n".join(lines))
    rendered = _WHITESPACE.sub(" ", rendered)
    return rendered[:max_chars]


@dataclass(slots=True)
class FormStructure:
    action: str = ""
    method: str = "get"
    fields: list[tuple[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class PageStructure:
    title: str = ""
    forms: list[FormStructure] = field(default_factory=list)
    anchors: list[tuple[str, str]] = field(default_factory=list)
    inline_handlers: list[tuple[str, str]] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    # (tag, identity) pairs for every interactive element, used for the state fingerprint.
    interactive: list[tuple[str, str]] = field(default_factory=list)
    # (identity, occupancy) for every form control: whether it is filled or checked,
    # never *what* it contains. See `state_fingerprint` for why the distinction matters.
    field_states: list[tuple[str, str]] = field(default_factory=list)


class _StructureParser(HTMLParser):
    """Tolerant structural extraction. stdlib-only: no bs4/lxml dependency for this."""

    _HEADINGS = {"h1", "h2", "h3"}
    _INTERACTIVE = {"a", "button", "input", "select", "textarea", "form"}
    # Controls with no user-alterable content of their own.
    _VALUELESS_INPUTS = {"hidden", "submit", "button", "image", "reset", "file"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.structure = PageStructure()
        self._open_form: FormStructure | None = None
        # Inputs outside any <form> still matter (SPAs frequently have none at all);
        # they accumulate here and are appended only if anything landed in them.
        self._orphan_form = FormStructure(action="<none>", method="none")
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._pending_href: str = ""
        self._pending_textarea: str = ""
        self._open_select: str | None = None
        self._select_has_selection: bool = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {k.lower(): (v or "") for k, v in attrs}

        for key, value in attributes.items():
            if key.startswith("on"):
                self.structure.inline_handlers.append((key, value[:120]))

        if tag in self._INTERACTIVE:
            identity = attributes.get("id") or attributes.get("name") or attributes.get("href") or ""
            self.structure.interactive.append((tag, canonicalize_url(identity) if identity else ""))

        if tag == "form":
            self._open_form = FormStructure(
                action=attributes.get("action", ""),
                method=attributes.get("method", "get").lower(),
            )
        elif tag in ("input", "select", "textarea"):
            name = attributes.get("name") or attributes.get("id") or ""
            ftype = attributes.get("type", "textarea" if tag == "textarea" else tag)
            target = self._open_form if self._open_form is not None else self._orphan_form
            target.fields.append((name, ftype))
            self._record_occupancy(tag, name, ftype, attributes)
        elif tag == "option":
            if self._open_select is not None and "selected" in attributes and attributes.get("value", ""):
                self._select_has_selection = True
        elif tag == "a":
            self._pending_href = attributes.get("href", "")
            self._capture, self._buffer = "a", []
        elif tag in self._HEADINGS:
            self._capture, self._buffer = "heading", []
        elif tag == "title":
            self._capture, self._buffer = "title", []

    def _record_occupancy(self, tag: str, name: str, ftype: str, attributes: dict[str, str]) -> None:
        """Note whether a control is filled/checked, never its contents."""
        if tag == "select":
            self._open_select, self._select_has_selection = name, False
            return
        if tag == "textarea":
            self._pending_textarea = name
            self._capture, self._buffer = "textarea", []
            return
        if ftype.lower() in self._VALUELESS_INPUTS:
            return
        if ftype.lower() in ("checkbox", "radio"):
            self.structure.field_states.append((name, "on" if "checked" in attributes else "off"))
            return
        self.structure.field_states.append((name, "filled" if attributes.get("value") else "empty"))

    def handle_endtag(self, tag: str) -> None:
        if tag == "select" and self._open_select is not None:
            self.structure.field_states.append(
                (self._open_select, "selected" if self._select_has_selection else "unselected")
            )
            self._open_select, self._select_has_selection = None, False
            return
        if tag == "textarea" and self._capture == "textarea":
            filled = bool("".join(self._buffer).strip())
            self.structure.field_states.append((self._pending_textarea, "filled" if filled else "empty"))
            self._capture, self._buffer = None, []
            return
        if tag == "form" and self._open_form is not None:
            self.structure.forms.append(self._open_form)
            self._open_form = None
            return
        if self._capture is None:
            return
        text = " ".join("".join(self._buffer).split())[:80]
        if tag == "a" and self._capture == "a":
            self.structure.anchors.append((canonicalize_url(self._pending_href), text))
        elif tag in self._HEADINGS and self._capture == "heading":
            if text:
                self.structure.headings.append(text)
        elif tag == "title" and self._capture == "title":
            self.structure.title = text
        else:
            return
        self._capture, self._buffer = None, []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._buffer.append(data)

    def finalize(self) -> PageStructure:
        if self._open_form is not None:  # unclosed <form> at EOF
            self.structure.forms.append(self._open_form)
            self._open_form = None
        if self._orphan_form.fields:
            self.structure.forms.append(self._orphan_form)
        return self.structure


def extract_structure(html: str) -> PageStructure:
    """Parse a page into its structural skeleton. Never raises on malformed markup."""
    parser = _StructureParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - HTMLParser can choke on pathological markup
        pass
    return parser.finalize()


def state_fingerprint(url: str, html: str) -> str:
    """A stable identity for "the agent is looking at this logical page".

    Deliberately coarse — built from the canonical URL plus the page's interactive
    skeleton, not the full markup. Two visits to the same form with different CSRF
    tokens, timestamps, or row ordering must produce the same fingerprint, or every
    novelty bonus degenerates into "everything is always new".

    Form *occupancy* is included, form *values* are not. An empty signup form and a
    completed one are genuinely different application states, and without this the
    agent earned no novelty credit for filling anything in — a silent disincentive
    against exactly the multi-step flows this project exists to exercise. Hashing the
    values themselves would reintroduce the degeneracy above, since every distinct
    typed string would mint a new state.
    """
    structure = extract_structure(html)
    skeleton = "|".join(f"{tag}:{identity}" for tag, identity in sorted(structure.interactive))
    occupancy = "|".join(f"{name}={state}" for name, state in sorted(structure.field_states))
    payload = (
        f"{canonicalize_url(url)}\n"
        f"{canonicalize_text(structure.title)}\n"
        f"{canonicalize_text(skeleton)}\n"
        f"{canonicalize_text(occupancy)}"
    )
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:32]
