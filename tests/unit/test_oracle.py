"""The deterministic fault oracle. Every transfer number depends on this being right.

`budget-to-first-verified-fault` is a count of confirmed faults, so an oracle that confirms
a fault the trajectory did not trigger inflates every arm, and one that misses a real
trigger censors runs that should not be censored. Both failure modes are tested for
directly, alongside the properties that make the result reproducible: purity, totality, and
independence from the agent.

No browser and no Docker: the `TrajectoryRunner` seam is filled with a fake that replays a
scripted world, which is what lets the whole pipeline -- replay, reproduction rule,
delta-debugging minimization -- be exercised in milliseconds.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from web_testing_agent.oracle import (
    AssertionKind,
    AssertionSpec,
    BenchmarkApp,
    FaultClass,
    FaultSpec,
    ReproductionRule,
    VerificationPipeline,
    build_trajectory,
    evaluate_expression,
    minimize_trace,
    region_text,
    verify_fault,
    visible_text,
)

# -- a small application to test against ---------------------------------------------------

RECEIPT_HTML = '<html><body><p id="r-qty">Quantity: 1</p><p id="r-total">Total: 9.99</p></body></html>'
GOOD_RECEIPT = '<html><body><p id="r-qty">Quantity: 42</p><p id="r-total">Total: 419.58</p></body></html>'


def app_with(*faults: FaultSpec) -> BenchmarkApp:
    return BenchmarkApp(
        app_id="testapp", name="Test App",
        regions={"quantity": "#r-qty", "total": "#r-total"},
        faults=tuple(faults),
    )


ECHO_FAULT = FaultSpec(
    fault_id="T-01",
    fault_class=FaultClass.STATE_CORRUPTION,
    description="The receipt reports a quantity other than the one ordered.",
    trigger_condition="Enter a quantity, then reach the receipt.",
    expected_behaviour="The receipt echoes the quantity entered.",
    observable_violation="The receipt shows a different number.",
    assertions=(AssertionSpec(kind=AssertionKind.ECHOES_INPUT, where=r"receipt",
                              region="quantity", input_field="quantity",
                              describes="receipt must echo the entered quantity"),),
    reproduction=ReproductionRule(replays=2, must_all_reproduce=True),
)


def typed(field: str, value: str) -> dict:
    return {"type": "TYPE", "id": field, "params": {"value": value}}


def clicked(label: str) -> dict:
    return {"type": "CLICK", "text": label}


def trajectory_reaching_receipt(quantity: str, receipt_html: str, filler: int = 0):
    records = [{"url": "http://app/index.html", "html": "<html><body>Shop</body></html>",
                "action": None}]
    for index in range(filler):
        records.append({"url": f"http://app/browse{index}.html",
                        "html": "<html><body>Browsing</body></html>",
                        "action": clicked(f"Filler {index}")})
    records.append({"url": "http://app/order.html",
                    "html": '<html><body><input id="quantity"></body></html>',
                    "action": typed("quantity", quantity)})
    records.append({"url": "http://app/receipt.html", "html": receipt_html,
                    "action": clicked("Place order")})
    return build_trajectory(records)


# -- text and region extraction -------------------------------------------------------------


def test_visible_text_strips_tags_scripts_and_styles():
    html = ('<html><head><style>p{color:red}</style></head>'
            '<body><script>var x = "hidden";</script><p>Hello  world</p></body></html>')
    text = visible_text(html)
    assert "Hello world" in text
    assert "hidden" not in text and "color:red" not in text


@pytest.mark.parametrize(("selector", "expected"), [
    ("#r-qty", "Quantity: 1"),
    ('[id="r-total"]', "Total: 9.99"),
])
def test_region_text_reads_the_named_element(selector, expected):
    assert region_text(RECEIPT_HTML, selector) == expected


def test_region_text_returns_none_when_the_element_is_absent():
    assert region_text(RECEIPT_HTML, "#nonexistent") is None


def test_visible_text_never_raises_on_malformed_markup():
    for html in ("<p>unclosed", "<<>>", "", "<script>oops", "<div class=>x</div>"):
        assert isinstance(visible_text(html), str)


# -- the trajectory view ----------------------------------------------------------------------


def test_typed_values_accumulate_forward_so_a_late_page_can_be_checked(extra=3):
    view = trajectory_reaching_receipt("42", RECEIPT_HTML, filler=extra)
    assert view.steps[-1].inputs["quantity"] == "42"
    assert len(view.actions) == extra + 2


def test_the_trajectory_carries_no_reward_or_policy_information():
    """The oracle's independence, enforced by what `ObservedStep` can hold."""
    view = trajectory_reaching_receipt("42", RECEIPT_HTML)
    step = view.steps[-1]
    for forbidden in ("reward", "q_value", "policy", "archive", "epsilon", "advantage"):
        assert not hasattr(step, forbidden)


# -- assertions ---------------------------------------------------------------------------------


def test_the_echo_fault_fires_when_the_receipt_disagrees_with_the_input():
    verdict = verify_fault(ECHO_FAULT, app_with(ECHO_FAULT),
                           trajectory_reaching_receipt("42", RECEIPT_HTML))
    assert verdict.violated and not verdict.inconclusive


def test_the_echo_fault_stays_silent_on_a_correct_receipt():
    verdict = verify_fault(ECHO_FAULT, app_with(ECHO_FAULT),
                           trajectory_reaching_receipt("42", GOOD_RECEIPT))
    assert not verdict.violated


def test_a_trajectory_that_never_reached_the_page_is_inconclusive_not_clean():
    """The distinction that keeps a broken check from reading as a clean application."""
    view = build_trajectory([{"url": "http://app/index.html", "html": "<p>Shop</p>"}])
    verdict = verify_fault(ECHO_FAULT, app_with(ECHO_FAULT), view)
    assert not verdict.violated
    assert verdict.inconclusive


def test_a_missing_region_is_inconclusive_rather_than_a_violation():
    view = trajectory_reaching_receipt("42", "<html><body><p>Thanks!</p></body></html>")
    verdict = verify_fault(ECHO_FAULT, app_with(ECHO_FAULT), view)
    assert verdict.inconclusive and not verdict.violated


def test_never_entering_the_input_is_inconclusive():
    view = build_trajectory([
        {"url": "http://app/receipt.html", "html": RECEIPT_HTML, "action": clicked("Place order")}])
    verdict = verify_fault(ECHO_FAULT, app_with(ECHO_FAULT), view)
    assert verdict.inconclusive


@pytest.mark.parametrize(("kind", "spec_kwargs", "html", "violated"), [
    (AssertionKind.TEXT_EQUALS, {"region": "quantity", "expected": "Quantity: 42"},
     RECEIPT_HTML, True),
    (AssertionKind.TEXT_EQUALS, {"region": "quantity", "expected": "Quantity: 1"},
     RECEIPT_HTML, False),
    (AssertionKind.TEXT_CONTAINS, {"region": "total", "expected": "419"}, RECEIPT_HTML, True),
    (AssertionKind.TEXT_ABSENT, {"region": "quantity", "expected": "Quantity: 1"},
     RECEIPT_HTML, True),
    (AssertionKind.URL_MATCHES, {"expected": r"confirmation\.html"}, RECEIPT_HTML, True),
    (AssertionKind.URL_MATCHES, {"expected": r"receipt\.html"}, RECEIPT_HTML, False),
])
def test_each_assertion_kind_decides_correctly(kind, spec_kwargs, html, violated):
    fault = FaultSpec(
        fault_id="K-01", fault_class=FaultClass.BROKEN_TRANSITION, description="",
        trigger_condition="", expected_behaviour="", observable_violation="",
        assertions=(AssertionSpec(kind=kind, where=r"receipt", **spec_kwargs),),
    )
    verdict = verify_fault(fault, app_with(fault),
                           trajectory_reaching_receipt("42", html))
    assert verdict.violated is violated


def test_numeric_equals_recomputes_the_shown_value_from_the_recorded_input():
    fault = FaultSpec(
        fault_id="N-01", fault_class=FaultClass.CALCULATION_ERROR, description="",
        trigger_condition="", expected_behaviour="", observable_violation="",
        assertions=(AssertionSpec(kind=AssertionKind.NUMERIC_EQUALS, where=r"receipt",
                                  region="total", expression="quantity * 9.99",
                                  tolerance=0.01),),
    )
    # 42 * 9.99 = 419.58; the buggy receipt shows 9.99, so the assertion must fire.
    assert verify_fault(fault, app_with(fault),
                        trajectory_reaching_receipt("42", RECEIPT_HTML)).violated
    assert not verify_fault(fault, app_with(fault),
                            trajectory_reaching_receipt("42", GOOD_RECEIPT)).violated


def test_conjunctive_faults_need_every_assertion_to_fire():
    fault = FaultSpec(
        fault_id="C-01", fault_class=FaultClass.STATE_CORRUPTION, description="",
        trigger_condition="", expected_behaviour="", observable_violation="",
        assertions=(
            AssertionSpec(kind=AssertionKind.ECHOES_INPUT, where=r"receipt",
                          region="quantity", input_field="quantity"),
            AssertionSpec(kind=AssertionKind.TEXT_CONTAINS, where=r"receipt",
                          region="total", expected="does not appear"),
        ),
    )
    verdict = verify_fault(fault, app_with(fault),
                           trajectory_reaching_receipt("42", RECEIPT_HTML))
    assert all(r.violated for r in verdict.results)
    assert verdict.violated

    partial = dataclasses.replace(fault, fault_id="C-02", assertions=(
        fault.assertions[0],
        AssertionSpec(kind=AssertionKind.TEXT_CONTAINS, where=r"receipt",
                      region="total", expected="Total"),
    ))
    assert not verify_fault(partial, app_with(partial),
                            trajectory_reaching_receipt("42", RECEIPT_HTML)).violated


# -- the expression evaluator is not `eval` ---------------------------------------------------------


def test_expressions_compute_arithmetic_over_recorded_inputs():
    assert evaluate_expression("quantity * 3", {"quantity": "14"}) == pytest.approx(42.0)
    assert evaluate_expression("a + b / 2", {"a": "1", "b": "4"}) == pytest.approx(3.0)


@pytest.mark.parametrize("expression", [
    "__import__('os').system('echo pwned')",
    "open('/etc/passwd').read()",
    "quantity.__class__",
    "[x for x in range(10)]",
    "lambda: 1",
])
def test_expressions_refuse_anything_that_is_not_arithmetic(expression):
    """The expression comes from a fixture's JSON; `eval` here would be a code path from
    an untrusted benchmark file into the harness that scores it."""
    with pytest.raises(ValueError):
        evaluate_expression(expression, {"quantity": "42"})


def test_a_malformed_expression_is_inconclusive_not_a_crash():
    fault = FaultSpec(
        fault_id="E-01", fault_class=FaultClass.CALCULATION_ERROR, description="",
        trigger_condition="", expected_behaviour="", observable_violation="",
        assertions=(AssertionSpec(kind=AssertionKind.NUMERIC_EQUALS, where=r"receipt",
                                  region="total", expression="quantity * *"),),
    )
    verdict = verify_fault(fault, app_with(fault),
                           trajectory_reaching_receipt("42", RECEIPT_HTML))
    assert verdict.inconclusive and not verdict.violated


# -- fixture validation -----------------------------------------------------------------------------


def test_an_assertion_naming_an_undefined_region_is_caught_at_load():
    bad = FaultSpec(
        fault_id="B-01", fault_class=FaultClass.STATE_CORRUPTION, description="",
        trigger_condition="", expected_behaviour="", observable_violation="",
        assertions=(AssertionSpec(kind=AssertionKind.TEXT_EQUALS, region="nope",
                                  expected="x"),),
    )
    problems = BenchmarkApp(app_id="a", name="a", regions={}, faults=(bad,)).validate()
    assert any("nope" in problem for problem in problems)


def test_an_application_with_no_faults_or_no_assertions_is_rejected():
    assert BenchmarkApp(app_id="a", name="a").validate()
    empty = FaultSpec(fault_id="X", fault_class=FaultClass.STATE_CORRUPTION, description="",
                      trigger_condition="", expected_behaviour="", observable_violation="")
    assert any("no assertion" in p for p in app_with(empty).validate())


def test_a_fault_library_round_trips_through_json(tmp_path):
    app = app_with(ECHO_FAULT)
    path = tmp_path / "faults.json"
    path.write_text(json.dumps(app.to_dict(), indent=2), encoding="utf-8")
    reloaded = BenchmarkApp.load(tmp_path)
    assert reloaded.app_id == app.app_id
    assert reloaded.fault("T-01").fault_class is FaultClass.STATE_CORRUPTION
    assert reloaded.fault("T-01").assertions[0].kind is AssertionKind.ECHOES_INPUT
    assert reloaded.validate() == []


# -- replay, reproduction and minimization ------------------------------------------------------------


class FakeRunner:
    """Replays an action sequence against a scripted world.

    The world's rule: the receipt is reached, and shows the buggy quantity, if and only if
    the trace contains a TYPE into `quantity` and a CLICK on `Place order`. Anything else
    in the trace is a distractor. That is exactly the structure delta debugging has to
    discover, so a minimizer that works here is doing the real job.
    """

    def __init__(self, *, flaky_after: int | None = None) -> None:
        self.calls = 0
        self.flaky_after = flaky_after

    def run(self, actions):  # noqa: ANN001
        self.calls += 1
        quantity = next((a["params"]["value"] for a in actions
                         if a.get("type") == "TYPE" and a.get("id") == "quantity"), None)
        placed = any(a.get("type") == "CLICK" and a.get("text") == "Place order"
                     for a in actions)
        records = [{"url": "http://app/index.html", "html": "<p>Shop</p>", "action": None}]
        for action in actions:
            records.append({"url": "http://app/step.html", "html": "<p>step</p>",
                            "action": action})
        if quantity is not None and placed:
            buggy = self.flaky_after is not None and self.calls > self.flaky_after
            html = GOOD_RECEIPT if buggy else RECEIPT_HTML
            records.append({"url": "http://app/receipt.html", "html": html,
                            "action": clicked("Place order")})
        return build_trajectory(records)


def full_trace(filler: int = 6) -> list[dict]:
    return ([clicked(f"Filler {i}") for i in range(filler)]
            + [typed("quantity", "42"), clicked("Place order")])


def test_a_candidate_is_confirmed_when_replays_reproduce_it():
    runner = FakeRunner()
    pipeline = VerificationPipeline(app_with(ECHO_FAULT), runner)
    trajectory = runner.run(full_trace())
    assert pipeline.candidates(trajectory)

    finding = pipeline.confirm(ECHO_FAULT, trajectory, budget_at_discovery=137)
    assert finding.confirmed
    assert finding.budget_at_discovery == 137
    assert len(finding.replays) == 2
    assert all(r["reproduced"] for r in finding.replays)


def test_minimization_strips_the_distractors_and_keeps_what_matters():
    runner = FakeRunner()
    pipeline = VerificationPipeline(app_with(ECHO_FAULT), runner)
    trajectory = runner.run(full_trace(filler=6))

    finding = pipeline.confirm(ECHO_FAULT, trajectory, budget_at_discovery=0)
    minimized = finding.minimized_trace
    assert finding.confirmed
    assert len(minimized) < len(finding.original_trace)
    assert any(a.get("type") == "TYPE" and a.get("id") == "quantity" for a in minimized)
    assert any(a.get("text") == "Place order" for a in minimized)


def test_the_minimized_trace_still_reproduces_the_violation():
    """The property that makes a minimized trace evidence rather than a shorter guess."""
    runner = FakeRunner()
    pipeline = VerificationPipeline(app_with(ECHO_FAULT), runner)
    trajectory = runner.run(full_trace())
    finding = pipeline.confirm(ECHO_FAULT, trajectory, budget_at_discovery=0)

    replayed = runner.run(finding.minimized_trace)
    assert verify_fault(ECHO_FAULT, app_with(ECHO_FAULT), replayed).violated


def test_a_flaky_violation_is_rejected_by_the_reproduction_rule():
    """A fault that reproduces once and then stops is not a deterministic seeded fault."""
    runner = FakeRunner(flaky_after=1)
    pipeline = VerificationPipeline(app_with(ECHO_FAULT), runner)
    trajectory = runner.run(full_trace())
    finding = pipeline.confirm(ECHO_FAULT, trajectory, budget_at_discovery=0)
    assert not finding.confirmed
    assert "reproduction rule" in finding.rejected_because


def test_a_replay_that_raises_is_recorded_not_propagated():
    class ExplodingRunner:
        def run(self, actions):  # noqa: ANN001, ARG002
            raise RuntimeError("browser died")

    pipeline = VerificationPipeline(app_with(ECHO_FAULT), ExplodingRunner())
    trajectory = trajectory_reaching_receipt("42", RECEIPT_HTML)
    finding = pipeline.confirm(ECHO_FAULT, trajectory, budget_at_discovery=0)
    assert not finding.confirmed
    assert any(r["inconclusive"] and "browser died" in r["error"] for r in finding.replays)


def test_a_candidate_whose_assertions_do_not_fire_is_rejected_without_replaying():
    runner = FakeRunner()
    pipeline = VerificationPipeline(app_with(ECHO_FAULT), runner)
    before = runner.calls
    finding = pipeline.confirm(ECHO_FAULT, trajectory_reaching_receipt("42", GOOD_RECEIPT),
                               budget_at_discovery=0)
    assert not finding.confirmed
    assert runner.calls == before, "a non-candidate must not cost a replay"


def test_minimization_is_bounded_and_returns_its_best_effort():
    runner = FakeRunner()
    pipeline = VerificationPipeline(app_with(ECHO_FAULT), runner, max_minimization_trials=3)
    trajectory = runner.run(full_trace(filler=20))
    finding = pipeline.confirm(ECHO_FAULT, trajectory, budget_at_discovery=0)
    assert finding.confirmed
    assert finding.minimization["trials"] <= 3
    assert finding.minimization["exhausted_budget"]


def test_pinned_actions_survive_minimization():
    kept = dataclasses.replace(ECHO_FAULT, fault_id="T-02",
                               minimization_keep=("Filler 0",))
    runner = FakeRunner()
    pipeline = VerificationPipeline(app_with(kept), runner)
    trajectory = runner.run(full_trace(filler=4))
    finding = pipeline.confirm(kept, trajectory, budget_at_discovery=0)
    assert finding.confirmed
    assert any(a.get("text") == "Filler 0" for a in finding.minimized_trace)


def test_minimize_trace_on_a_pure_predicate_finds_the_minimal_subset():
    """ddmin itself, without the pipeline: keep only the actions the predicate needs."""
    actions = [{"i": i} for i in range(12)]
    required = {3, 7}

    def reproduces(candidate):  # noqa: ANN001
        return required <= {a["i"] for a in candidate}

    result = minimize_trace(actions, reproduces, max_trials=200)
    assert required <= {a["i"] for a in result.trace}
    assert result.minimized_length < result.original_length
    assert reproduces(result.trace)


def test_verification_cost_is_reported_separately_from_the_search_budget():
    """Replay and minimization must not be charged to budget-to-first-verified-fault."""
    runner = FakeRunner()
    pipeline = VerificationPipeline(app_with(ECHO_FAULT), runner)
    trajectory = runner.run(full_trace())
    finding = pipeline.confirm(ECHO_FAULT, trajectory, budget_at_discovery=500)
    assert finding.budget_at_discovery == 500
    assert finding.verification_steps > 0
    assert finding.verification_steps != finding.budget_at_discovery


def test_a_pipeline_refuses_an_application_with_unusable_metadata():
    bad = FaultSpec(
        fault_id="B", fault_class=FaultClass.STATE_CORRUPTION, description="",
        trigger_condition="", expected_behaviour="", observable_violation="",
        assertions=(AssertionSpec(kind=AssertionKind.TEXT_EQUALS, region="missing"),),
    )
    with pytest.raises(ValueError, match="unusable fault metadata"):
        VerificationPipeline(app_with(bad), FakeRunner())


# -- independence ----------------------------------------------------------------------------------------


def test_the_oracle_package_imports_no_agent_reward_or_judge():
    """The property that lets this be ground truth rather than a measurement of a model."""
    import ast
    import pathlib

    import web_testing_agent.oracle as package

    root = pathlib.Path(package.__file__).parent
    offenders: list[str] = []
    for source in root.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            for name in names:
                if any(banned in name for banned in
                       ("judge", "reward", "ac_dqn", "contextual_bandit", "anthropic",
                        "openai", "llm")):
                    offenders.append(f"{source.name}: {name}")
    assert not offenders, offenders


# -- REGION_EMPTY, added while validating application 1 -----------------------------------


def test_region_empty_fires_on_a_blank_required_region():
    """The constraint-bypass symptom: a value was committed that the app never had."""
    fault = FaultSpec(
        fault_id="R-01", fault_class=FaultClass.CONSTRAINT_BYPASS, description="",
        trigger_condition="", expected_behaviour="", observable_violation="",
        assertions=(AssertionSpec(kind=AssertionKind.REGION_EMPTY, where=r"receipt",
                                  region="quantity"),),
    )
    blank = '<html><body><p id="r-qty"></p><p id="r-total">Total: 9.99</p></body></html>'
    assert verify_fault(fault, app_with(fault),
                        trajectory_reaching_receipt("42", blank)).violated
    assert not verify_fault(fault, app_with(fault),
                            trajectory_reaching_receipt("42", RECEIPT_HTML)).violated


def test_region_empty_is_inconclusive_when_the_region_is_absent_entirely():
    """An element not rendered and an element rendered blank are different observations.

    Only the second is evidence that the application committed a value it did not have;
    the first usually means the trajectory never reached the page being judged.
    """
    fault = FaultSpec(
        fault_id="R-02", fault_class=FaultClass.CONSTRAINT_BYPASS, description="",
        trigger_condition="", expected_behaviour="", observable_violation="",
        assertions=(AssertionSpec(kind=AssertionKind.REGION_EMPTY, where=r"receipt",
                                  region="quantity"),),
    )
    verdict = verify_fault(fault, app_with(fault),
                           trajectory_reaching_receipt("42", "<html><body>Thanks</body></html>"))
    assert verdict.inconclusive and not verdict.violated
