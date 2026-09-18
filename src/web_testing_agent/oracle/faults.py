"""Seeded-fault metadata and the deterministic assertions that decide whether one fired.

**Why a declarative assertion language rather than a Python callback per fault.**

A benchmark application ships its faults as data (`faults.json` beside the fixture), and a
callback would mean importing and executing code that travels with a fixture. Three
reasons that is the wrong trade here:

* **Reproducibility.** A declarative assertion is inspectable, diffable and serialisable
  into the finding, so a confirmed result carries the exact rule that confirmed it. A
  callback carries a function name and a promise.
* **Independence.** The oracle must be independent of the agent, the reward and any model.
  A fixed evaluator over a fixed grammar cannot accidentally acquire a dependency on the
  policy; an arbitrary callback can.
* **Uniformity.** Six applications written over weeks will drift if each may express its
  fault however it likes. A grammar forces every fault into the same shape, which is what
  makes `budget-to-first-verified-fault` mean the same thing on all six.

The grammar is deliberately small. It covers the fault taxonomy in the pre-registration
(`docs/methodology/2026-09-18-transfer-preregistration.md` §9) and nothing else; a fault
that cannot be expressed in it is a signal that the taxonomy needs a new class, which is a
methodology decision rather than a coding one.

**What an assertion sees.** A `TrajectoryView` — the sequence of observed steps, each with
a URL, the page text, the action that produced it, and any value the agent typed. It never
sees the reward, the policy, the archive, or a model's opinion.
"""

from __future__ import annotations

import enum
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..utils.logging import get_logger

logger = get_logger(__name__)


class FaultClass(str, enum.Enum):
    """The taxonomy frozen in the pre-registration. One class per seeded fault."""

    STATE_CORRUPTION = "state_corruption"
    CONSTRAINT_BYPASS = "constraint_bypass"
    BROKEN_TRANSITION = "broken_transition"
    CALCULATION_ERROR = "calculation_error"
    PERSISTENCE_FAILURE = "persistence_failure"


class AssertionKind(str, enum.Enum):
    """Every check the oracle can make. Deliberately closed.

    Each is a statement about *observed page state*, evaluated without a browser: the
    trajectory has already been captured, so verification is a pure function of the record
    and is therefore exactly reproducible.
    """

    #: A page region's text must equal a literal. Violation when it does not.
    TEXT_EQUALS = "text_equals"
    #: A page region's text must contain a literal.
    TEXT_CONTAINS = "text_contains"
    #: A page region's text must NOT contain a literal.
    TEXT_ABSENT = "text_absent"
    #: A value the agent typed earlier must be echoed on a later page. The core
    #: state-corruption check: it compares the application against *the user's own input*
    #: rather than against a hard-coded expectation, so it survives a fixture edit.
    ECHOES_INPUT = "echoes_input"
    #: The URL of the step must match a regex. Used for broken transitions.
    URL_MATCHES = "url_matches"
    #: A number shown on the page must equal an arithmetic expression over recorded inputs.
    NUMERIC_EQUALS = "numeric_equals"
    #: A guarded state was reached even though the input that guards it was invalid.
    REACHED_WITH_INVALID_INPUT = "reached_with_invalid_input"


@dataclass(frozen=True, slots=True)
class AssertionSpec:
    """One deterministic check. Serialisable, inspectable, and evaluated by `assertions`.

    `where` selects the step the check applies to: a regex matched against the step's URL.
    The *last* matching step is used, because a fault is judged on the final observed state
    of the page that carries it, not on a transient intermediate render.
    """

    kind: AssertionKind
    where: str = ".*"
    #: Named region of the page to read. `""` means the whole page text. Regions are
    #: resolved by the application's `regions` map, so an assertion never contains a CSS
    #: selector -- keeping selector churn inside the fixture rather than inside the oracle.
    region: str = ""
    expected: str = ""
    #: For ECHOES_INPUT: which recorded input to compare against, by field name.
    input_field: str = ""
    #: For NUMERIC_EQUALS: an expression over recorded inputs, e.g. "quantity * 3".
    expression: str = ""
    tolerance: float = 1e-9
    #: Human-readable statement of what this assertion means, carried into the finding.
    describes: str = ""

    def to_dict(self) -> dict:
        return {**asdict(self), "kind": self.kind.value}

    @classmethod
    def from_dict(cls, data: dict) -> "AssertionSpec":
        payload = dict(data)
        payload["kind"] = AssertionKind(payload["kind"])
        allowed = {f for f in cls.__slots__}
        return cls(**{k: v for k, v in payload.items() if k in allowed})


@dataclass(frozen=True, slots=True)
class ReproductionRule:
    """How many independent replays must reproduce the violation for it to be confirmed.

    **Not a formality.** A fault that reproduces once in three replays is either a flaky
    application or a flaky harness, and either way it is not a deterministic seeded fault.
    The default demands every replay agree, which is what "deterministic" has to mean if
    `budget-to-first-verified-fault` is going to be comparable across applications.
    """

    replays: int = 2
    must_all_reproduce: bool = True

    def satisfied_by(self, reproduced: list[bool]) -> bool:
        if len(reproduced) < self.replays:
            return False
        window = reproduced[: self.replays]
        return all(window) if self.must_all_reproduce else any(window)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FaultSpec:
    """One seeded fault, completely described.

    Everything the pre-registration requires a fault to declare lives here, so a fixture
    cannot ship a fault that is missing its trigger, its expectation or its assertion.
    """

    fault_id: str
    fault_class: FaultClass
    description: str
    #: What the agent has to do for the fault to become observable, in prose. Documentation
    #: for a reader; the machine-checkable form is `assertions`.
    trigger_condition: str
    expected_behaviour: str
    observable_violation: str
    assertions: tuple[AssertionSpec, ...] = ()
    reproduction: ReproductionRule = field(default_factory=ReproductionRule)
    #: Steps the minimizer must never remove, by URL regex -- a step that sets up the
    #: session, for instance. Empty for most faults.
    minimization_keep: tuple[str, ...] = ()
    #: Delayed-observation horizon: how many steps separate the triggering action from the
    #: step where the violation becomes visible. Documented for later use; the
    #: delayed-credit experiment is deferred.
    observation_horizon: int = 0
    #: Shortest known fault-triggering path length, for benchmark characterization.
    shortest_path: int = 0

    def to_dict(self) -> dict:
        return {
            "fault_id": self.fault_id,
            "fault_class": self.fault_class.value,
            "description": self.description,
            "trigger_condition": self.trigger_condition,
            "expected_behaviour": self.expected_behaviour,
            "observable_violation": self.observable_violation,
            "assertions": [a.to_dict() for a in self.assertions],
            "reproduction": self.reproduction.to_dict(),
            "minimization_keep": list(self.minimization_keep),
            "observation_horizon": self.observation_horizon,
            "shortest_path": self.shortest_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FaultSpec":
        return cls(
            fault_id=str(data["fault_id"]),
            fault_class=FaultClass(data["fault_class"]),
            description=str(data.get("description", "")),
            trigger_condition=str(data.get("trigger_condition", "")),
            expected_behaviour=str(data.get("expected_behaviour", "")),
            observable_violation=str(data.get("observable_violation", "")),
            assertions=tuple(AssertionSpec.from_dict(a) for a in data.get("assertions", [])),
            reproduction=ReproductionRule(**(data.get("reproduction") or {})),
            minimization_keep=tuple(data.get("minimization_keep") or ()),
            observation_horizon=int(data.get("observation_horizon", 0) or 0),
            shortest_path=int(data.get("shortest_path", 0) or 0),
        )


@dataclass(frozen=True, slots=True)
class BenchmarkApp:
    """An application's fault library and the page regions its assertions read.

    `regions` maps a region name to a CSS selector. Assertions name regions, never
    selectors, so a fixture can restructure its DOM without every assertion changing --
    which matters because the benchmark deliberately varies DOM structure.
    """

    app_id: str
    name: str
    entry: str = "index.html"
    regions: dict[str, str] = field(default_factory=dict)
    faults: tuple[FaultSpec, ...] = ()
    #: Characterization metrics from the pre-registration, filled in by measurement.
    characterization: dict[str, Any] = field(default_factory=dict)

    def fault(self, fault_id: str) -> FaultSpec | None:
        return next((f for f in self.faults if f.fault_id == fault_id), None)

    def to_dict(self) -> dict:
        return {
            "app_id": self.app_id, "name": self.name, "entry": self.entry,
            "regions": dict(self.regions),
            "faults": [f.to_dict() for f in self.faults],
            "characterization": dict(self.characterization),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BenchmarkApp":
        return cls(
            app_id=str(data["app_id"]),
            name=str(data.get("name", data["app_id"])),
            entry=str(data.get("entry", "index.html")),
            regions=dict(data.get("regions") or {}),
            faults=tuple(FaultSpec.from_dict(f) for f in data.get("faults", [])),
            characterization=dict(data.get("characterization") or {}),
        )

    @classmethod
    def load(cls, path: Path) -> "BenchmarkApp":
        """Read `faults.json` from a fixture directory, or from the file itself."""
        path = Path(path)
        if path.is_dir():
            path = path / "faults.json"
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def validate(self) -> list[str]:
        """Problems that would make this application's faults unusable. Empty means sound.

        Run at load time by the verification pipeline rather than trusted: a fixture with
        an assertion naming a region it does not define would otherwise fail silently at
        run time, hours into an experiment, and look like "the agent never found the bug".
        """
        problems: list[str] = []
        if not self.faults:
            problems.append(f"{self.app_id}: declares no seeded faults")
        seen: set[str] = set()
        for fault in self.faults:
            if fault.fault_id in seen:
                problems.append(f"{self.app_id}: duplicate fault id {fault.fault_id!r}")
            seen.add(fault.fault_id)
            if not fault.assertions:
                problems.append(f"{fault.fault_id}: declares no assertion")
            for index, assertion in enumerate(fault.assertions):
                if assertion.region and assertion.region not in self.regions:
                    problems.append(
                        f"{fault.fault_id}[{index}]: names region {assertion.region!r}, "
                        f"which {self.app_id} does not define")
                try:
                    re.compile(assertion.where)
                except re.error as exc:
                    problems.append(f"{fault.fault_id}[{index}]: bad `where` regex: {exc}")
                if assertion.kind is AssertionKind.ECHOES_INPUT and not assertion.input_field:
                    problems.append(f"{fault.fault_id}[{index}]: echoes_input needs input_field")
                if assertion.kind is AssertionKind.NUMERIC_EQUALS and not assertion.expression:
                    problems.append(f"{fault.fault_id}[{index}]: numeric_equals needs expression")
        return problems
