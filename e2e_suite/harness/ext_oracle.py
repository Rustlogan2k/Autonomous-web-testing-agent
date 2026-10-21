"""The suite's isolated oracle extensions, for faults the primary grammar cannot state.

**Isolated by design.** The primary oracle (`web_testing_agent.oracle`) is frozen while the
primary experiment runs. Nothing here modifies it: this module reads the same recorded
`TrajectoryView` the primary oracle reads (imported, unchanged), evaluates any primary-kind
assertions with the primary `evaluate_assertion`, and minimizes with the primary
`minimize_trace`. The assertion kinds defined here:

* ``region_stable_across_reload`` — the region's text must be identical immediately before
  and after a page reload (a REFRESH action). A committed value that changes or vanishes on
  reload is a persistence failure. Evaluated on the **last** reload whose preceding step
  matches `where`; inconclusive when no reload happened or the region is absent on either
  side.
* ``input_absent`` — on the last step matching `where`, the region must NOT contain the
  value entered for `input_field`. A draft value leaking onto a page that must show only
  committed data is a cross-page state fault. Inconclusive when nothing was entered.
* ``region_arithmetic`` — on the last step matching `where`, numbers read from several
  regions of the same page must satisfy `expression` (e.g. ``discount == subtotal * 0.1``),
  within `tolerance`. The primary `numeric_equals` compares a page number with the user's
  *inputs*; this compares a page's numbers with *each other*, which is how an internally
  inconsistent page (a discount that no longer matches its subtotal) is stated without a
  path-dependent expected value. An optional `guard` ({region, contains}) restricts the
  check to pages where the rule applies; a page where the guard does not hold satisfies the
  assertion. Inconclusive when a region is absent or carries no number.
* ``console_or_request_error`` -- a crash-type fault (plan §9): violated when, on any step
  whose URL matches `where`, the browser reported an event of one of `sources` whose text
  matches `pattern`. The sources are ``page`` (an uncaught exception or unhandled rejection: its
  message without the type, e.g. "Cannot read properties of null (reading 'sku')"),
  ``console`` (a `console.error`, or the browser's own "Failed to load resource" line) and
  ``request`` (a same-origin response with status >= 400, written
  ``HTTP <status> <METHOD> <url>``; the harness's `/__wta/` endpoints excepted). Inconclusive
  when no step matched `where`, or when the trajectory carries no browser events at all.

**Where the browser events come from.** The primary `ObservedStep` deliberately carries
four facts (URL, HTML, action, inputs), and the primary runner builds it with
`observed_record`, so console and network events never reach a `TrajectoryView`. The
environment does capture them, per step, in `info["page"]` (`console_errors`, `page_errors`
and the `network` JSON). `EventTrajectoryRunner` therefore wraps the env the primary runner
drives (`_RecordingEnv`): every `reset`/`step` result passes through unchanged and its
events are recorded alongside, one entry per observed step; the runner returns an
`EventView`, the primary view plus `events`. Nothing in `web_testing_agent` is changed, and
a plain `TrajectoryView` makes this kind inconclusive.

Assertions in an extension fault are conjunctive, exactly as in the primary oracle.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from web_testing_agent.oracle.assertions import evaluate_assertion
from web_testing_agent.oracle.faults import AssertionKind, AssertionSpec, BenchmarkApp
from web_testing_agent.oracle.trajectory import TrajectoryView, build_trajectory, region_text
from web_testing_agent.oracle.verification import PlaywrightTrajectoryRunner, minimize_trace

EXTENSION_KINDS = ("region_stable_across_reload", "input_absent", "region_arithmetic",
                   "console_or_request_error")
#: The browser event streams `console_or_request_error` may read (see the module docstring).
EVENT_SOURCES = ("page", "console", "request")
#: The crash classes of plan §9 and the sources each must read: a fault that uses
#: `console_or_request_error` names its class as `crash`.
CRASH_CLASSES = {"uncaught-exception": {"page", "console"},
                 "server-error": {"request"},
                 "broken-resource": {"request", "console"}}
#: How long a replay waits after its last step for events still in flight (a response, or an
#: exception, that lands after the page settled) before the browser is closed.
LATE_EVENT_WAIT_MS = 250
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SAFE_EXPRESSION = re.compile(r"^[\w\s.,+*/()<>=!-]+$")


def _first_number(text: str) -> float | None:
    match = _NUMBER.search(text or "")
    return float(match.group(0).replace(",", "")) if match else None


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


@dataclass
class ExtResult:
    kind: str
    violated: bool
    inconclusive: bool = False
    detail: str = ""
    observed: str = ""


@dataclass
class ExtVerdict:
    fault_id: str
    violated: bool
    inconclusive: bool
    results: list[ExtResult] = field(default_factory=list)


# -- browser events per observed step ---------------------------------------------------------


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def no_events() -> dict[str, list[str]]:
    return {source: [] for source in EVENT_SOURCES}


def page_events(info: dict | None, origin: str) -> dict[str, list[str]]:
    """The browser events one env observation carries, by source.

    `console` and `page` are the env's own same-origin captures (`console_errors`,
    `page_errors`); `request` is every response in the step's network log with status >= 400
    on `origin`, harness endpoints (`/__wta/`) excepted, as ``HTTP <status> <METHOD> <url>``.
    A request that never got a response (an aborted navigation) is not an error here: any
    click away from a loading page produces one.
    """
    page = (info or {}).get("page") or {}
    events = no_events()
    events["console"] = [str(m) for m in page.get("console_errors") or []]
    events["page"] = [str(e) for e in page.get("page_errors") or []]
    try:
        rows = json.loads(page.get("network") or "[]")
    except (TypeError, ValueError):
        rows = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        status, url = row.get("response_status"), str(row.get("url") or "")
        if not isinstance(status, int) or status < 400 or "/__wta/" in url:
            continue
        if origin and origin_of(url) != origin:
            continue
        events["request"].append(f"HTTP {status} {row.get('method') or 'GET'} {url}")
    return events


@dataclass(slots=True)
class EventView(TrajectoryView):
    """A primary `TrajectoryView` plus the browser events of each observed step
    (`events[i]` belongs to `steps[i]`)."""

    events: list[dict] = field(default_factory=list)


def with_events(view: TrajectoryView, events: list[dict]) -> EventView:
    """`view` with `events` attached, aligned to its steps (missing entries are empty)."""
    aligned = [dict(e) for e in events[:len(view.steps)]]
    aligned += [no_events() for _ in range(len(view.steps) - len(aligned))]
    return EventView(steps=view.steps, actions=view.actions, replay_misses=view.replay_misses,
                     events=aligned)


def view_of_records(records: list[dict]) -> EventView:
    """An `EventView` from per-step records that carry an `events` entry (the harness
    `Session` adds one to each `observed_record`)."""
    return with_events(build_trajectory(records), [r.get("events") or no_events() for r in records])


class _RecordingEnv:
    """The env the primary runner drives, with every observation's events recorded.

    `reset` and `step` return exactly what the wrapped env returns; anything else is
    delegated. `close` first waits `LATE_EVENT_WAIT_MS` and folds events that arrived after
    the last observation into the last step, so a trace that ends on its trigger still sees
    the response or exception it caused.
    """

    def __init__(self, env, origin: str) -> None:  # noqa: ANN001 - a WebFunctionalEnv
        self._env = env
        self._origin = origin
        self.events: list[dict] = []

    def reset(self, *args, **kwargs):
        observation, info = self._env.reset(*args, **kwargs)
        self.events.append(page_events(info, self._origin))
        return observation, info

    def step(self, action):
        result = self._env.step(action)
        self.events.append(page_events(result[-1], self._origin))
        return result

    def _late_events(self) -> dict[str, list[str]]:
        env = self._env
        page = getattr(getattr(env, "_browser", None), "page", None)
        if page is None:
            return no_events()
        page.wait_for_timeout(LATE_EVENT_WAIT_MS)
        recorder = getattr(env, "_network_recorder", None)
        network = [{"url": e.url, "method": e.method, "response_status": e.response_status}
                   for e in (recorder.drain() if recorder is not None else [])]
        late = {"console_errors": list(getattr(env, "_console_buffer", None) or []),
                "page_errors": list(getattr(env, "_page_error_buffer", None) or []),
                "network": json.dumps(network)}
        return page_events({"page": late}, self._origin)

    def close(self) -> None:
        try:
            if self.events:
                for source, texts in self._late_events().items():
                    self.events[-1].setdefault(source, []).extend(texts)
        except Exception:  # noqa: BLE001 - a page already gone has nothing late to report
            pass
        close = getattr(self._env, "close", None)
        if callable(close):
            close()

    def __getattr__(self, name: str):
        return getattr(self._env, name)


class EventTrajectoryRunner(PlaywrightTrajectoryRunner):
    """The primary replay runner, unchanged, returning an `EventView` (see the module
    docstring). `origin` is the application's, for the same-origin request rule."""

    def __init__(self, env_factory: Callable[[], object], max_steps: int = 60, *,
                 origin: str = "") -> None:
        self._recorder: _RecordingEnv | None = None
        self._origin = origin

        def recording_factory() -> _RecordingEnv:
            self._recorder = _RecordingEnv(env_factory(), self._origin)
            return self._recorder

        super().__init__(recording_factory, max_steps=max_steps)

    def run(self, actions: list[dict]) -> EventView:
        self._recorder = None
        view = PlaywrightTrajectoryRunner.run(self, actions)
        return with_events(view, self._recorder.events if self._recorder else [])


def _crash_event(spec: dict, trajectory: TrajectoryView) -> ExtResult:
    kind = spec["kind"]
    events = getattr(trajectory, "events", None)
    if events is None:
        return ExtResult(kind, False, True, "the trajectory carries no browser events")
    where = re.compile(spec["where"])
    pattern = re.compile(spec["pattern"])
    sources = spec.get("sources") or list(EVENT_SOURCES)
    matched = 0
    for step, step_events in zip(trajectory.steps, events):
        if not where.search(step.url):
            continue
        matched += 1
        for source in sources:
            for text in step_events.get(source, []):
                if pattern.search(text):
                    return ExtResult(kind, True, False,
                                     f"{source} event matching {spec['pattern']!r} on a step "
                                     f"matching {spec['where']!r}",
                                     f"step {step.index} {source}: {text[:160]}")
    if not matched:
        return ExtResult(kind, False, True, f"no step matched where={spec['where']!r}")
    return ExtResult(kind, False, False,
                     f"no {'/'.join(sources)} event matching {spec['pattern']!r} on {matched} step(s)")


def _evaluate(spec: dict, regions: dict, app: BenchmarkApp, trajectory: TrajectoryView) -> ExtResult:
    kind = spec["kind"]
    where = spec.get("where", ".*")
    if kind == "console_or_request_error":
        return _crash_event(spec, trajectory)
    if kind == "region_stable_across_reload":
        selector = regions[spec["region"]]
        pattern = re.compile(where)
        pairs = [(before, after) for before, after in zip(trajectory.steps, trajectory.steps[1:])
                 if (after.action or {}).get("type") == "REFRESH" and pattern.search(before.url)]
        if not pairs:
            return ExtResult(kind, False, True, "no reload after a matching step")
        before, after = pairs[-1]
        was, now = region_text(before.html, selector), region_text(after.html, selector)
        if was is None or now is None:
            return ExtResult(kind, False, True,
                             f"region {spec['region']!r} absent {'before' if was is None else 'after'} reload")
        violated = _norm(was) != _norm(now)
        return ExtResult(kind, violated, False,
                         f"region {spec['region']!r} must survive a reload unchanged",
                         f"before={was[:80]!r} after={now[:80]!r}")
    if kind == "input_absent":
        steps = trajectory.matching(where)
        if not steps:
            return ExtResult(kind, False, True, f"no step matched where={where!r}")
        step = steps[-1]
        entered = step.inputs.get(spec["input_field"])
        if entered is None or not entered.strip():
            return ExtResult(kind, False, True, f"nothing entered for {spec['input_field']!r}")
        text = region_text(step.html, regions[spec["region"]])
        if text is None:
            return ExtResult(kind, False, False, "region absent, so the value is absent")
        violated = _norm(entered) in _norm(text)
        return ExtResult(kind, violated, False,
                         f"region {spec['region']!r} must not show {spec['input_field']!r}",
                         text[:120])
    if kind == "region_arithmetic":
        steps = trajectory.matching(where)
        if not steps:
            return ExtResult(kind, False, True, f"no step matched where={where!r}")
        step = steps[-1]
        guard = spec.get("guard")
        if guard:
            text = region_text(step.html, regions[guard["region"]])
            if text is None or _norm(guard["contains"]) not in _norm(text):
                return ExtResult(kind, False, False, "guard does not hold: the rule does not apply here")
        values = {}
        for name, region in spec["operands"].items():
            text = region_text(step.html, regions[region])
            number = _first_number(text) if text is not None else None
            if number is None:
                return ExtResult(kind, False, True, f"operand {name!r} (region {region!r}) has no number")
            values[name] = number
        expression = spec["expression"]
        if not _SAFE_EXPRESSION.match(expression) or "__" in expression:
            return ExtResult(kind, False, True, f"refusing expression {expression!r}")
        scope = {"__builtins__": {}, "round": round, "abs": abs, "min": min, "max": max}
        try:
            if "==" in expression:
                lhs, _, rhs = expression.partition("==")
                left, right = eval(lhs, scope, dict(values)), eval(rhs, scope, dict(values))  # noqa: S307
                violated = abs(float(left) - float(right)) > float(spec.get("tolerance", 0.005))
                observed = f"{lhs.strip()}={left:g} {rhs.strip()}={right:g}"
            else:  # a boolean rule such as `total <= 50`
                violated = not bool(eval(expression, scope, dict(values)))  # noqa: S307
                observed = f"{values}"
        except Exception as exc:  # noqa: BLE001
            return ExtResult(kind, False, True, f"expression failed: {exc}")
        return ExtResult(kind, violated, False, f"{expression} with {values}", observed)
    # A primary kind inside an extension fault: evaluated by the primary oracle, unchanged.
    result = evaluate_assertion(AssertionSpec.from_dict(spec), app, trajectory)
    return ExtResult(result.kind, result.violated, result.inconclusive, result.detail,
                     str(result.observed)[:120])


def verify(fault: dict, regions: dict, app: BenchmarkApp, trajectory: TrajectoryView) -> ExtVerdict:
    results = [_evaluate(spec, regions, app, trajectory) for spec in fault["assertions"]]
    inconclusive = any(r.inconclusive for r in results)
    violated = bool(results) and all(r.violated for r in results) and not inconclusive
    return ExtVerdict(fault["fault_id"], violated, inconclusive, results)


@dataclass
class ExtFinding:
    fault_id: str
    confirmed: bool
    rejected_because: str = ""
    replays: list[bool] = field(default_factory=list)
    minimized_trace: list[dict] = field(default_factory=list)
    minimization: dict = field(default_factory=dict)


def confirm(fault: dict, regions: dict, app: BenchmarkApp, *, faulty, control, clean,
            witness: list[dict], max_trials: int = 30) -> ExtFinding:
    """The primary pipeline's confirmation rule, applied to an extension fault.

    Control first (the faulty build without this fault's patch, then the clean build): a
    violation there means the symptom is not this fault's. Then two replays on the faulty
    build must both reproduce. Then ddmin, every candidate held to the same rule, and the
    minimized trace re-checked in full.
    """
    def violated_on(runner, trace) -> bool:
        observed = runner.run(trace)
        return observed.replay_misses == 0 and verify(fault, regions, app, observed).violated

    if violated_on(control, witness):
        return ExtFinding(fault["fault_id"], False, "fires_without_its_own_patch")
    if violated_on(clean, witness):
        return ExtFinding(fault["fault_id"], False, "fires_on_clean_build")
    replays = [violated_on(faulty, witness) for _ in range(2)]
    if not all(replays):
        return ExtFinding(fault["fault_id"], False, "not_reproduced", replays)

    def reproduces(trace: list[dict]) -> bool:
        return violated_on(faulty, trace) and not violated_on(control, trace)

    result = minimize_trace(list(witness), reproduces, max_trials=max_trials)
    trace = result.trace
    if not (all(violated_on(faulty, trace) for _ in range(2))
            and not violated_on(control, trace) and not violated_on(clean, trace)):
        trace = list(witness)  # the minimized trace failed the full rule: keep the original
    return ExtFinding(fault["fault_id"], True, "", replays, trace, result.to_dict())


__all__ = ["CRASH_CLASSES", "EVENT_SOURCES", "EXTENSION_KINDS", "EventTrajectoryRunner", "EventView",
           "ExtFinding", "ExtVerdict", "confirm", "page_events", "verify", "view_of_records",
           "with_events", "AssertionKind"]
