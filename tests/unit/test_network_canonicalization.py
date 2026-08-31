"""The observation's network channel must be stable across equivalent states.

Measured 2026-08-27: two identical navigations produced *unrelated* network vectors
(cosine +0.018) while the visual and structural blocks were bit-identical. Three fields
survived `canonicalize_text` — the HTTP `date` response header, a monotonic-clock
`timestamp`, and `duration_ms` differing in its eighth decimal — and a content-hash
encoder turns any byte difference into an orthogonal vector, so 384 of 1,664 observation
dimensions were clock jitter on every step that touched the network. DQN training was
therefore not reproducible at a fixed seed; the random baselines, which never read an
observation, reproduced bit-for-bit.

These tests pin both directions: jitter must not change the observation, and everything
that genuinely distinguishes one application state from another must still change it.
The last group pins the other half of the requirement — that the *forensic* trace handed
to the judge, the trace corpus and the bug report keeps full fidelity.
"""

from __future__ import annotations

import copy
import json

import numpy as np

from web_testing_agent.perception.normalization import canonicalize_network_trace
from web_testing_agent.perception.vec_wrapper import default_encoders, encode_modalities
from web_testing_agent.perception.fusion import NETWORK_DIM, STRUCTURAL_DIM, VISUAL_DIM

ORIGIN = "http://127.0.0.1:58614"
PAGE_URL = f"{ORIGIN}/index.html"

_EVENT = {
    "url": f"{ORIGIN}/order-1.html",
    "method": "GET",
    "request_headers": {"referer": f"{ORIGIN}/index.html", "accept": "text/html"},
    "request_body": None,
    "response_status": 200,
    "response_headers": {
        "date": "Thu, 27 Aug 2026 09:23:11 GMT",
        "content-type": "text/html",
        "content-length": "2414",
    },
    "response_body_snippet": "<!DOCTYPE html><html>",
    "timestamp": 1245416.984,
    "duration_ms": 16.00000006146729,
    "failed": False,
    "failure_text": None,
    "resource_type": "document",
}


def _trace(**overrides) -> str:
    event = copy.deepcopy(_EVENT)
    for key, value in overrides.items():
        if key in ("request_headers", "response_headers"):
            event[key] = {**event[key], **value}
        else:
            event[key] = value
    return json.dumps([event])


def _canon(**overrides) -> str:
    return canonicalize_network_trace(_trace(**overrides), PAGE_URL)


# -- jitter must be invisible -------------------------------------------------------

def test_clock_jitter_does_not_change_the_observation():
    """The exact defect: same navigation, different clock readings."""
    baseline = _canon()
    jittered = _canon(
        timestamp=1245417.968,
        duration_ms=15.999999828636646,
        response_headers={"date": "Thu, 27 Aug 2026 09:23:12 GMT"},
    )
    assert baseline == jittered


def test_an_ephemeral_server_port_does_not_change_the_observation():
    """`serve_directory` binds a new port every run, and it appears in the URL, the
    referer and any absolute link in a body. Left in, the same page hashes differently
    in two processes and no cross-run comparison means anything."""
    other = "http://127.0.0.1:49771"
    relocated = canonicalize_network_trace(
        _trace().replace(ORIGIN, other), f"{other}/index.html"
    )
    assert relocated == _canon()


def test_sub_bucket_duration_differences_are_invisible():
    assert _canon(duration_ms=12.0) == _canon(duration_ms=41.7)


def test_volatile_header_values_are_masked_but_their_names_survive():
    """Presence of an Authorization or Set-Cookie header is real signal about the
    application; the rotating value behind it is not."""
    canon = _canon(
        request_headers={"authorization": "Bearer abc123"},
        response_headers={"set-cookie": "sid=9f8e7d6c; Path=/"},
    )
    assert "authorization" in canon and "set-cookie" in canon
    assert "Bearer" not in canon and "9f8e7d6c" not in canon
    # And a different token in the same header does not change the observation.
    assert canon == _canon(
        request_headers={"authorization": "Bearer zzz999"},
        response_headers={"set-cookie": "sid=1a2b3c4d; Path=/"},
    )


# -- genuine differences must remain visible ----------------------------------------

def test_a_failing_request_is_distinguishable_from_a_successful_one():
    assert _canon(response_status=500) != _canon()
    assert _canon(failed=True, failure_text="net::ERR_ABORTED") != _canon()


def test_a_different_url_status_method_or_resource_type_is_visible():
    assert _canon(url=f"{ORIGIN}/order-2.html") != _canon()
    assert _canon(method="POST") != _canon()
    assert _canon(resource_type="xhr") != _canon()
    assert _canon(response_status=404) != _canon(response_status=200)


def test_a_genuinely_slow_request_is_still_visible():
    """`slow_response` is a real trigger; bucketing must not erase what it detects."""
    assert _canon(duration_ms=8000.0) != _canon(duration_ms=16.0)
    assert "5s" in _canon(duration_ms=8000.0)


def test_a_foreign_origin_stays_distinguishable_from_the_application():
    """On-site versus off-site is exactly the distinction that must survive."""
    assert _canon(url="https://evil.example.com/beacon") != _canon()
    assert "evil.example.com" in _canon(url="https://evil.example.com/beacon")


def test_response_body_differences_are_visible():
    assert _canon(response_body_snippet="database error") != _canon()


# -- degradation --------------------------------------------------------------------

def test_a_malformed_trace_degrades_instead_of_raising():
    """An observation pipeline must not raise on a truncated trace."""
    assert canonicalize_network_trace('[{"url": "http://x/y", "meth', PAGE_URL)
    assert canonicalize_network_trace("", PAGE_URL) == canonicalize_network_trace("[]", PAGE_URL)
    assert canonicalize_network_trace('{"not": "a list"}', PAGE_URL)


def test_an_empty_trace_is_stable():
    assert canonicalize_network_trace("[]", PAGE_URL) == canonicalize_network_trace("[]", "")


# -- the encoded observation, end to end --------------------------------------------

def test_the_encoded_network_block_is_identical_under_jitter():
    """The property that actually matters: same state -> same vector."""
    encoders = default_encoders()
    screenshot = np.zeros((8, 8, 3), dtype=np.uint8)
    page_a = {"url": PAGE_URL, "html": "<html></html>", "network": _trace()}
    page_b = {
        "url": PAGE_URL, "html": "<html></html>",
        "network": _trace(timestamp=1245417.968, duration_ms=15.9999998,
                          response_headers={"date": "Thu, 27 Aug 2026 09:23:12 GMT"}),
    }
    a, b = (
        encode_modalities(encoders, [screenshot], [page], [None], [5])[0]
        for page in (page_a, page_b)
    )
    lo, hi = VISUAL_DIM + STRUCTURAL_DIM, VISUAL_DIM + STRUCTURAL_DIM + NETWORK_DIM
    assert np.array_equal(a[lo:hi], b[lo:hi])
    assert np.array_equal(a, b)


def test_the_encoded_network_block_still_moves_on_a_real_error():
    encoders = default_encoders()
    screenshot = np.zeros((8, 8, 3), dtype=np.uint8)
    base = {"url": PAGE_URL, "html": "<html></html>", "network": _trace()}
    broken = {"url": PAGE_URL, "html": "<html></html>", "network": _trace(response_status=500)}
    a, b = (
        encode_modalities(encoders, [screenshot], [page], [None], [5])[0]
        for page in (base, broken)
    )
    lo, hi = VISUAL_DIM + STRUCTURAL_DIM, VISUAL_DIM + STRUCTURAL_DIM + NETWORK_DIM
    assert not np.array_equal(a[lo:hi], b[lo:hi])


# -- forensic fidelity is NOT reduced ------------------------------------------------

def test_the_reduction_is_not_applied_to_the_forensic_trace():
    """The judge window, the trace corpus and the bug report read
    `info["page"]["network"]`, which must keep exact timings and exact headers. This
    test states that requirement against the raw string the env emits: the canonical
    form drops these, the forensic form must not."""
    raw = _trace()
    canonical = canonicalize_network_trace(raw, PAGE_URL)

    # Present in the forensic trace...
    assert "1245416.984" in raw
    assert "16.00000006146729" in raw
    assert "Thu, 27 Aug 2026 09:23:11 GMT" in raw
    # ...and deliberately absent from the observation.
    assert "1245416.984" not in canonical
    assert "16.00000006146729" not in canonical
    assert "09:23:11" not in canonical
    # The evidence a report needs is still in the forensic trace, verbatim.
    event = json.loads(raw)[0]
    assert event["duration_ms"] == 16.00000006146729
    assert event["timestamp"] == 1245416.984
    assert event["response_headers"]["date"] == "Thu, 27 Aug 2026 09:23:11 GMT"
