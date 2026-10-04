"""The suite's isolated oracle extensions, for faults the primary grammar cannot state.

**Isolated by design.** The primary oracle (`web_testing_agent.oracle`) is frozen while the
primary experiment runs. Nothing here modifies it: this module reads the same recorded
`TrajectoryView` the primary oracle reads (imported, unchanged), evaluates any primary-kind
assertions with the primary `evaluate_assertion`, and minimizes with the primary
`minimize_trace`. Only two new assertion kinds are defined here:

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

Assertions in an extension fault are conjunctive, exactly as in the primary oracle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from web_testing_agent.oracle.assertions import evaluate_assertion
from web_testing_agent.oracle.faults import AssertionKind, AssertionSpec, BenchmarkApp
from web_testing_agent.oracle.trajectory import TrajectoryView, region_text
from web_testing_agent.oracle.verification import minimize_trace

EXTENSION_KINDS = ("region_stable_across_reload", "input_absent", "region_arithmetic")
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


def _evaluate(spec: dict, regions: dict, app: BenchmarkApp, trajectory: TrajectoryView) -> ExtResult:
    kind = spec["kind"]
    where = spec.get("where", ".*")
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
        lhs, _, rhs = expression.partition("==")
        try:
            left = eval(lhs, {"__builtins__": {}, "round": round}, dict(values))  # noqa: S307
            right = eval(rhs, {"__builtins__": {}, "round": round}, dict(values))  # noqa: S307
        except Exception as exc:  # noqa: BLE001
            return ExtResult(kind, False, True, f"expression failed: {exc}")
        violated = abs(float(left) - float(right)) > float(spec.get("tolerance", 0.005))
        return ExtResult(kind, violated, False, f"{expression} with {values}",
                         f"{lhs.strip()}={left:g} {rhs.strip()}={right:g}")
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


__all__ = ["EXTENSION_KINDS", "ExtFinding", "ExtVerdict", "confirm", "verify", "AssertionKind"]
