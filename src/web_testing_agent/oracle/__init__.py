"""Deterministic seeded-fault verification, independent of the agent and of any model.

    FaultSpec (declared by the fixture)
        -> assertions over an observed trajectory   [assertions]
        -> candidate finding
        -> independent replay                       [verification]
        -> reproduction rule
        -> delta-debugging minimization
        -> ConfirmedFinding

**Independence is the property that makes this usable as ground truth.** Nothing in this
package imports an agent, a reward, a policy or a judge. The RL reward never receives a
seeded fault label, and no language model is consulted: a quantitative result that depended
on a model's opinion would be a measurement of the model.
"""

from .assertions import (
    AssertionResult,
    FaultVerdict,
    evaluate_assertion,
    evaluate_expression,
    verify_all,
    verify_fault,
)
from .faults import (
    AssertionKind,
    AssertionSpec,
    BenchmarkApp,
    FaultClass,
    FaultSpec,
    ReproductionRule,
)
from .trajectory import (
    ObservedStep,
    TrajectoryView,
    build_trajectory,
    region_text,
    visible_text,
)
from .verification import (
    ConfirmedFinding,
    MinimizationResult,
    PlaywrightTrajectoryRunner,
    ReplayOutcome,
    TrajectoryRunner,
    VerificationPipeline,
    minimize_trace,
)

__all__ = [
    "AssertionKind", "AssertionResult", "AssertionSpec", "BenchmarkApp",
    "ConfirmedFinding", "FaultClass", "FaultSpec", "FaultVerdict",
    "MinimizationResult", "ObservedStep", "PlaywrightTrajectoryRunner",
    "ReplayOutcome", "ReproductionRule", "TrajectoryRunner", "TrajectoryView",
    "VerificationPipeline", "build_trajectory", "evaluate_assertion",
    "evaluate_expression", "minimize_trace", "region_text", "verify_all",
    "verify_fault", "visible_text",
]
