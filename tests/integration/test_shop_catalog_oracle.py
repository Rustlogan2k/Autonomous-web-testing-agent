"""End-to-end: does the oracle confirm the seeded faults on a real browser, and only there?

This is the validation that matters for the whole benchmark. Everything else in the oracle
test suite runs against a fake runner; this runs the real pipeline — Playwright, the real
`WebFunctionalEnv`, the real `ScriptedPolicy` replay, the real assertions — against both
builds of application 1.

Two directions, and both have to hold:

* on the **buggy** build the fault is confirmed, replays reproduce it, and minimization
  shrinks the trace while keeping it reproducing;
* on the **clean** build the identical trace confirms **nothing**.

The second is the one that makes the first meaningful. An oracle that fires on the clean
build is measuring the trace, not the application, and every transfer number computed
through it would be noise.

Marked `requires_browser`; skipped without Chromium.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from web_testing_agent.oracle import (
    BenchmarkApp,
    PlaywrightTrajectoryRunner,
    VerificationPipeline,
    verify_fault,
)
from web_testing_agent.utils.local_server import serve_directory

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "benchmark" / "shop_catalog"

#: The shortest trace that triggers SHOP-01: choose a product, set a quantity above one,
#: add it, continue to checkout, supply valid contact details, confirm.
#:
#: **Steps carry `category`, not a literal value.** The agent cannot type arbitrary text --
#: `envs/input_values.py` generates exactly five values per field, and `valid_typical` for a
#: number field is `42`. A trace naming a value the action space cannot produce is not a
#: trace the agent could ever have taken, so it would test the oracle against a fiction.
#: This matches what `go_explore.to_replay_step` emits, which is what the archive replays.
SHOP_01_TRACE = [
    {"type": "CLICK", "text": "Shelter"},
    {"type": "CLICK", "text": "Ridgeline 2 tent"},
    {"type": "TYPE", "id": "qty", "params": {"category": "valid_typical"}},
    {"type": "CLICK", "text": "Add to basket"},
    {"type": "CLICK", "text": "Continue to checkout"},
    {"type": "TYPE", "id": "contact", "params": {"category": "valid_typical"}},
    {"type": "TYPE", "id": "postcode", "params": {"category": "valid_typical"}},
    {"type": "CLICK", "text": "Confirm and place the order"},
]

#: SHOP-02 differs by one action: the postcode is never entered.
SHOP_02_TRACE = [step for step in SHOP_01_TRACE if step.get("id") != "postcode"]


def runner_for(build: str):  # noqa: ANN201
    """A trajectory runner bound to one build of the application, served locally."""
    from web_testing_agent.envs import WebFunctionalEnv

    context = serve_directory(FIXTURE / build)
    origin = context.__enter__()

    def make_env():  # noqa: ANN202
        return WebFunctionalEnv(base_url=f"{origin}/index.html", max_steps=40, headless=True)

    return PlaywrightTrajectoryRunner(make_env, max_steps=40), context


@pytest.fixture
def app() -> BenchmarkApp:
    loaded = BenchmarkApp.load(FIXTURE)
    assert loaded.validate() == [], loaded.validate()
    return loaded


@pytest.mark.requires_browser
@pytest.mark.parametrize(("fault_id", "trace"), [
    ("SHOP-01", SHOP_01_TRACE),
    ("SHOP-02", SHOP_02_TRACE),
])
def test_the_seeded_fault_is_confirmed_on_the_buggy_build(app, fault_id, trace):
    runner, context = runner_for("buggy")
    try:
        pipeline = VerificationPipeline(app, runner, max_minimization_trials=12)
        observed = runner.run(trace)
        assert any(step.url.endswith("confirmation.html") for step in observed.steps), \
            f"the trace never reached the confirmation page: {[s.url for s in observed.steps]}"

        finding = pipeline.confirm(app.fault(fault_id), observed, budget_at_discovery=len(trace))
        assert finding.confirmed, finding.rejected_because
        assert all(replay["reproduced"] for replay in finding.replays)
        assert finding.minimized_trace
        assert finding.minimization["minimized_length"] <= finding.minimization["original_length"]
    finally:
        context.__exit__(None, None, None)


@pytest.mark.requires_browser
@pytest.mark.parametrize(("fault_id", "trace"), [
    ("SHOP-01", SHOP_01_TRACE),
    ("SHOP-02", SHOP_02_TRACE),
])
def test_the_same_trace_confirms_nothing_on_the_clean_build(app, fault_id, trace):
    """The control that makes the positive result mean something."""
    runner, context = runner_for("clean")
    try:
        observed = runner.run(trace)
        verdict = verify_fault(app.fault(fault_id), app, observed)
        assert not verdict.violated, (
            f"{fault_id} fired on the CLEAN build -- the assertion is measuring the trace, "
            f"not the fault: {verdict.to_dict()}")
    finally:
        context.__exit__(None, None, None)


@pytest.mark.requires_browser
def test_the_minimized_trace_still_reproduces_on_the_buggy_build(app):
    runner, context = runner_for("buggy")
    try:
        pipeline = VerificationPipeline(app, runner, max_minimization_trials=12)
        observed = runner.run(SHOP_01_TRACE)
        finding = pipeline.confirm(app.fault("SHOP-01"), observed, budget_at_discovery=0)
        assert finding.confirmed

        replayed = runner.run(finding.minimized_trace)
        assert verify_fault(app.fault("SHOP-01"), app, replayed).violated
    finally:
        context.__exit__(None, None, None)


@pytest.mark.requires_browser
def test_a_correct_order_confirms_no_fault_on_the_buggy_build(app):
    """SHOP-01 must not fire when the quantity happens to equal the line count.

    Ordering a single unit makes the buggy render coincidentally correct. An assertion
    that still fired there would be reporting the fault's *presence* rather than its
    *observation*, and budget-to-first-verified-fault counts observations.
    """
    runner, context = runner_for("buggy")
    try:
        # Drop the quantity step entirely: the field defaults to 1, so the buggy render
        # (line count = 1) coincidentally equals the ordered quantity.
        trace = [step for step in SHOP_01_TRACE if step.get("id") != "qty"]
        observed = runner.run(trace)
        assert not verify_fault(app.fault("SHOP-01"), app, observed).violated
    finally:
        context.__exit__(None, None, None)
