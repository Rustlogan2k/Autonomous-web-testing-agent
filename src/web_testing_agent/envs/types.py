"""Shared dataclasses and enums for the browser environment layer."""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field

import numpy as np


class ActionType(str, enum.Enum):
    NO_OP = "NO_OP"
    CLICK = "CLICK"
    TYPE = "TYPE"
    SELECT = "SELECT"
    SCROLL = "SCROLL"
    RESIZE_VIEWPORT = "RESIZE_VIEWPORT"
    RAPID_CLICK = "RAPID_CLICK"
    BROWSER_BACK = "BROWSER_BACK"
    BROWSER_FORWARD = "BROWSER_FORWARD"
    REFRESH = "REFRESH"


class InputValueCategory(str, enum.Enum):
    VALID_TYPICAL = "valid_typical"
    BOUNDARY_MIN = "boundary_min"
    BOUNDARY_MAX = "boundary_max"
    EMPTY_STRING = "empty_string"
    TYPE_MISMATCH = "type_mismatch"


class ScrollDirection(str, enum.Enum):
    UP = "up"
    DOWN = "down"


VIEWPORT_PRESETS: dict[str, tuple[int, int]] = {
    "mobile": (375, 812),
    "tablet": (768, 1024),
    # Must equal CANONICAL_VIEWPORT: RESIZE_VIEWPORT("desktop") is the agent's way to
    # get *back* to the default, so a mismatch here would mean the "restore" action
    # never actually restores the starting geometry.
    "desktop": (1280, 720),
}

# The viewport every episode starts in, and the geometry every screenshot is
# normalized back to before it enters the observation.
CANONICAL_VIEWPORT: dict[str, int] = {"width": 1280, "height": 720}

# (height, width, channels) — the fixed shape of the `screenshot` observation.
# RESIZE_VIEWPORT genuinely changes the browser viewport (that is the point of the
# action), but the *tensor* handed to the perception layer must keep one shape or it
# violates the declared observation space, so captures are resized back to this.
SCREENSHOT_SHAPE: tuple[int, int, int] = (
    CANONICAL_VIEWPORT["height"],
    CANONICAL_VIEWPORT["width"],
    3,
)

# Fixed Discrete() size for both agents' action spaces (per spec: 100 slots, unused = NO-OP).
MAX_ACTIONS = 100

# Width of the episode-context vector appended to the perception features.
#
# Both the novelty bonus and the finding ledger make reward depend on *episode history*
# — the same (page, action) pair pays +7 the first time and ~0 afterwards. The page
# observation alone cannot distinguish those cases, which makes the process non-Markov
# and the Q-target unpredictable from the state. Exposing a compact summary of that
# history restores the agent's ability to predict its own reward.
#
# **Widened 6 -> 9 (2026-09-03).** The original six were introduced to fix exactly this
# defect, and then two reward terms were added *after* them that reintroduced it:
#
# * `novelty_bonus` divides by `sqrt(1 + state_run_visits)`, a **run-level** counter that
#   nothing in the observation reported. The same (page, action) therefore paid +1.00 on
#   its first visit of the run and +0.30 on its tenth, with the observation identical.
# * `reveal_bonus` pays only for identities absent from `_paid_reveals[(url_path, id)]`,
#   an **episodic hidden ledger**. Re-opening a gate already paid for this episode pays
#   0.0 where the first opening paid 1.5, again with an identical observation.
#
# See `WebTestingEnv._episode_context` for what each slot carries. The three added slots
# are observation-only: no reward term, counter, or scale was touched when they landed,
# which is what `tests/unit/test_markov_context.py` pins.
#
# **Two further slots were audited out before this shipped**, and the reasons are worth
# keeping because both are easy to re-propose:
#
# * a `remaining_budget` slot was exactly `1 - slot 0` in every reachable case, including
#   the overrun branch, so it was a collinear duplicate of a feature already present.
# * a `reveal_paid_this_step` slot reported `len(unpaid_reveals)` from the step that had
#   just executed. It is **post-action** information: at `o_t` it describes `a_{t-1}` and
#   never `a_t`, so it cannot predict `r_t` — traced on the live fixture, two steps with
#   an identical reveal reward of 0.00 carried 0.25 and 0.00 in that slot. Worse, it is a
#   scaled copy of a *reward component* sitting in the bootstrap target's own input,
#   which lets `V(s_{t+1})` be fitted from past reward rather than future return. Slot 8
#   already makes the reveal term fully observable at the correct temporal point.
EPISODE_CONTEXT_DIM = 9


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """One concrete, executable action bound to a Discrete() index for the *current* page.

    The action space is rebuilt every step, so `index` is only meaningful for the
    observation it was produced alongside — it is not a stable identifier across time.
    """

    index: int
    action_type: ActionType
    selector: str | None = None
    element_id: str = ""
    params: dict = field(default_factory=dict)
    description: str = ""


@dataclass(slots=True)
class NetworkEvent:
    url: str
    method: str
    request_headers: dict
    request_body: str | None
    response_status: int | None
    response_headers: dict
    response_body_snippet: str
    timestamp: float
    duration_ms: float | None
    failed: bool = False
    failure_text: str | None = None
    # Playwright resource type ("document", "xhr", "fetch", "image", "script", ...).
    # A failing *document* request is a real navigation bug; a failing favicon is noise.
    resource_type: str = "other"

    @property
    def is_error(self) -> bool:
        return self.failed or (self.response_status is not None and self.response_status >= 400)

    @property
    def is_document(self) -> bool:
        return self.resource_type == "document"

    def error_key(self) -> tuple[str, int]:
        """Identity used to match an error against the page's known baseline noise."""
        return (self.url.split("?", 1)[0], self.response_status or -1)


@dataclass(slots=True)
class RawObservation:
    """Unencoded multimodal observation for one timestep.

    This is the handoff point to the (future) semantic perception pipeline, which
    fuses `screenshot` + `html` + `network_events` into the shared 256-dim state.
    """

    screenshot: np.ndarray
    html: str
    network_events: list[NetworkEvent]
    url: str
    console_errors: list[str]
    page_errors: list[str]
    captured_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class BugSignals:
    """Deterministic (non-LLM) functional bug indicators collected during one step.

    `unexpected_http_errors` holds only errors that are *not* part of the target
    app's baseline noise (see `reward.functional_triggers.ErrorBaseline`). Real apps
    404 on favicons and sourcemaps on every single page load; counting those would
    pay the agent a constant bonus for merely reloading a page.
    """

    console_errors: list[str] = field(default_factory=list)
    unexpected_http_errors: list[NetworkEvent] = field(default_factory=list)
    slow_response: bool = False
    broken_navigation: bool = False
    load_duration_s: float = 0.0
    # True when one of the unexpected errors was the main document request itself,
    # rather than a subresource — a materially stronger signal.
    document_http_error: bool = False

    @property
    def any_triggered(self) -> bool:
        return bool(
            self.console_errors
            or self.unexpected_http_errors
            or self.slow_response
            or self.broken_navigation
        )
