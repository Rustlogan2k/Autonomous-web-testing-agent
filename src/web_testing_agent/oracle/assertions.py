"""Evaluating a fault's assertions against an observed trajectory. Pure, and total.

**Pure**: no browser, no network, no model, no policy. Given the same trajectory and the
same `AssertionSpec`, the result is the same in any process, which is what makes a
confirmed finding reproducible rather than merely repeatable.

**Total**: every branch returns an `AssertionResult`, including the ones where the page is
missing, the region is absent or the expression is malformed. An oracle that raises
mid-experiment turns a research result into a crash report, and an oracle that silently
returns "no violation" on a malformed rule is worse -- it reports the application clean
because the *check* was broken. Both are avoided by a third outcome: `inconclusive`.

`violated` and `inconclusive` are separate fields on purpose. Only `violated` counts toward
`budget-to-first-verified-fault`; `inconclusive` is an infrastructure signal that belongs in
the run log and in the exclusions discussion, never in the metric.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import asdict, dataclass

from ..utils.logging import get_logger
from .faults import AssertionKind, AssertionSpec, BenchmarkApp, FaultSpec
from .trajectory import TrajectoryView

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AssertionResult:
    """What one check concluded, and enough context to see why."""

    kind: str
    violated: bool
    inconclusive: bool = False
    detail: str = ""
    observed: str = ""
    expected: str = ""
    step_index: int = -1
    step_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FaultVerdict:
    """Whether a fault's assertions, taken together, say it fired."""

    fault_id: str
    violated: bool
    inconclusive: bool
    results: tuple[AssertionResult, ...]
    #: Index of the observed step at which the violation became visible; -1 when none did.
    violation_step: int = -1

    def to_dict(self) -> dict:
        return {
            "fault_id": self.fault_id,
            "violated": self.violated,
            "inconclusive": self.inconclusive,
            "violation_step": self.violation_step,
            "results": [r.to_dict() for r in self.results],
        }


# -- a deliberately tiny arithmetic evaluator ------------------------------------------

_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
}


def evaluate_expression(expression: str, variables: dict[str, str]) -> float:
    """Arithmetic over recorded inputs. Raises `ValueError` on anything else.

    Parsed with `ast` and walked over a four-operator allow-list rather than `eval`,
    because the expression comes from a fixture's JSON and `eval` on fixture-supplied text
    would make a benchmark application able to execute code in the harness that scores it.
    """
    try:
        tree = ast.parse(expression, mode="eval").body
    except SyntaxError as exc:
        raise ValueError(f"unparseable expression {expression!r}") from exc

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise ValueError(f"expression references unknown input {node.id!r}")
            try:
                return float(str(variables[node.id]).strip())
            except ValueError as exc:
                raise ValueError(
                    f"input {node.id!r} is {variables[node.id]!r}, not a number") from exc
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return _BINARY[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -walk(node.operand)
        raise ValueError(f"unsupported expression element {type(node).__name__}")

    return walk(tree)


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _numbers_in(text: str) -> list[float]:
    return [float(match.group()) for match in _NUMBER.finditer(text or "")]


# -- the evaluator ----------------------------------------------------------------------


def _read(step, app: BenchmarkApp, spec: AssertionSpec) -> tuple[str | None, str]:
    """(text, what-was-read) for the assertion's region, or (None, reason) when absent."""
    if not spec.region:
        return step.text, "whole page"
    selector = app.regions.get(spec.region)
    if not selector:
        return None, f"region {spec.region!r} is not defined by {app.app_id}"
    text = step.region(selector)
    if text is None:
        return None, f"region {spec.region!r} ({selector}) not present on the page"
    return text, f"region {spec.region!r}"


def evaluate_assertion(spec: AssertionSpec, app: BenchmarkApp,
                       trajectory: TrajectoryView) -> AssertionResult:
    """Evaluate one assertion against the trajectory. Never raises."""
    candidates = trajectory.matching(spec.where)
    if not candidates:
        return AssertionResult(
            kind=spec.kind.value, violated=False, inconclusive=True,
            detail=f"no observed step matched where={spec.where!r}")

    # The last matching step: a fault is judged on the final state of the page carrying it,
    # not on a transient intermediate render of the same URL.
    step = candidates[-1]
    text, source = _read(step, app, spec)
    common = {"kind": spec.kind.value, "step_index": step.index, "step_url": step.url}

    if text is None:
        # A missing region is an infrastructure fact, not a clean application. The one
        # exception is TEXT_ABSENT, where "the region is not there" genuinely satisfies
        # "this text is not there".
        if spec.kind is AssertionKind.TEXT_ABSENT:
            return AssertionResult(**common, violated=False, detail=source)
        return AssertionResult(**common, violated=False, inconclusive=True, detail=source)

    try:
        return _apply(spec, step, text, source, common, trajectory)
    except ValueError as exc:
        return AssertionResult(**common, violated=False, inconclusive=True,
                               detail=f"assertion could not be evaluated: {exc}")


def _apply(spec: AssertionSpec, step, text: str, source: str,  # noqa: PLR0911
           common: dict, trajectory: TrajectoryView) -> AssertionResult:
    kind = spec.kind

    if kind is AssertionKind.TEXT_EQUALS:
        violated = text.strip() != spec.expected.strip()
        return AssertionResult(**common, violated=violated, observed=text[:200],
                               expected=spec.expected,
                               detail=f"{source}: expected exactly {spec.expected!r}")

    if kind is AssertionKind.TEXT_CONTAINS:
        violated = spec.expected.lower() not in text.lower()
        return AssertionResult(**common, violated=violated, observed=text[:200],
                               expected=spec.expected,
                               detail=f"{source}: expected to contain {spec.expected!r}")

    if kind is AssertionKind.TEXT_ABSENT:
        violated = spec.expected.lower() in text.lower()
        return AssertionResult(**common, violated=violated, observed=text[:200],
                               expected=f"absence of {spec.expected!r}",
                               detail=f"{source}: must not contain {spec.expected!r}")

    if kind is AssertionKind.ECHOES_INPUT:
        entered = step.inputs.get(spec.input_field)
        if entered is None:
            return AssertionResult(**common, violated=False, inconclusive=True,
                                   detail=f"no value was ever entered for "
                                          f"{spec.input_field!r}, so nothing to echo")
        violated = entered.strip().lower() not in text.lower()
        return AssertionResult(**common, violated=violated, observed=text[:200],
                               expected=entered,
                               detail=f"{source}: must echo the value entered for "
                                      f"{spec.input_field!r} ({entered!r})")

    if kind is AssertionKind.URL_MATCHES:
        violated = re.search(spec.expected, step.url) is None
        return AssertionResult(**common, violated=violated, observed=step.url,
                               expected=spec.expected,
                               detail=f"URL must match {spec.expected!r}")

    if kind is AssertionKind.NUMERIC_EQUALS:
        wanted = evaluate_expression(spec.expression, step.inputs)
        shown = _numbers_in(text)
        if not shown:
            return AssertionResult(**common, violated=False, inconclusive=True,
                                   detail=f"{source}: no number found to compare against "
                                          f"{spec.expression!r}")
        # Any number in the region matching is enough: a region may legitimately carry a
        # label and a value, and demanding the region contain exactly one number would make
        # the assertion a formatting check rather than a correctness one.
        violated = not any(abs(value - wanted) <= spec.tolerance for value in shown)
        return AssertionResult(**common, violated=violated,
                               observed=", ".join(str(v) for v in shown[:6]),
                               expected=f"{spec.expression} = {wanted:g}",
                               detail=f"{source}: expected {spec.expression!r}")

    if kind is AssertionKind.REACHED_WITH_INVALID_INPUT:
        entered = step.inputs.get(spec.input_field)
        if entered is None:
            return AssertionResult(**common, violated=False, inconclusive=True,
                                   detail=f"no value entered for {spec.input_field!r}")
        invalid = re.search(spec.expected, entered) is not None
        return AssertionResult(**common, violated=invalid, observed=entered,
                               expected=f"input not matching {spec.expected!r}",
                               detail=f"guarded page reached with {spec.input_field!r}="
                                      f"{entered!r}, which the rule declares invalid")

    return AssertionResult(**common, violated=False, inconclusive=True,
                           detail=f"unhandled assertion kind {kind!r}")


def verify_fault(fault: FaultSpec, app: BenchmarkApp,
                 trajectory: TrajectoryView) -> FaultVerdict:
    """Evaluate every assertion for one fault.

    **Conjunctive**: the fault is violated only when *every* assertion says so, and
    inconclusive when any could not be evaluated. A disjunctive rule would let a single
    loosely-written assertion confirm a fault the trajectory never actually triggered, and
    the metric this feeds is a count of confirmed faults.
    """
    results = tuple(evaluate_assertion(spec, app, trajectory) for spec in fault.assertions)
    if not results:
        return FaultVerdict(fault.fault_id, False, True, results)

    inconclusive = any(r.inconclusive for r in results)
    violated = bool(results) and all(r.violated for r in results) and not inconclusive
    step = max((r.step_index for r in results if r.violated), default=-1) if violated else -1
    return FaultVerdict(fault.fault_id, violated, inconclusive, results, step)


def verify_all(app: BenchmarkApp, trajectory: TrajectoryView) -> list[FaultVerdict]:
    """Every seeded fault, in declaration order."""
    return [verify_fault(fault, app, trajectory) for fault in app.faults]
