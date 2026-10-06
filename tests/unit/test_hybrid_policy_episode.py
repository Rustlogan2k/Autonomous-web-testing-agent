"""The hybrid policy's episode logic through the real hunt loop, on a fake four-page app.

No browser and no model: a tiny state machine stands in for the environment, so the archive,
returns, pruning, operators and invariant hooks are exercised exactly as in a real hunt.
"""

from __future__ import annotations

from web_testing_agent.benchmark.hunt import run_hunt
from web_testing_agent.envs.types import ActionSpec, ActionType
from web_testing_agent.hybrid.learner import BanditPlanner
from web_testing_agent.hybrid.policy import HybridPolicy

FIXED = [ActionType.NO_OP, ActionType.SCROLL, ActionType.BROWSER_BACK, ActionType.REFRESH]


class FakeApp:
    """home -> form (email field, Next) -> review (Edit, Send) -> done; sessionless."""

    def __init__(self, max_steps: int = 40) -> None:
        self.max_steps = max_steps

    def _specs(self) -> list[ActionSpec]:
        specs = [
            ActionSpec(index=i, action_type=t, description=t.value) for i, t in enumerate(FIXED)
        ]

        def add(kind, dom, label, **params):
            specs.append(
                ActionSpec(
                    index=len(specs),
                    action_type=kind,
                    selector=f'[id="{dom}"]',
                    element_id=label,
                    params=params,
                    description=f"{kind.value} {dom}",
                )
            )

        if self.page == "home":
            add(ActionType.CLICK, "start", "Start", href="form", navigational=True)
        elif self.page == "form":
            for category in ("valid_typical", "type_mismatch", "empty_string"):
                add(ActionType.TYPE, "email", "email", category=category)
            add(ActionType.CLICK, "next", "Next")
        elif self.page == "review":
            add(ActionType.CLICK, "edit", "Edit")
            add(ActionType.CLICK, "send", "Send")
        else:
            add(ActionType.CLICK, "again", "Start again", href="home", navigational=True)
        return specs

    def _html(self) -> str:
        if self.page == "form":
            alert = '<p class="error">Enter a valid email</p>' if self.error else ""
            return f'<h1>Your details</h1><input id="email" name="email" type="email" value="{self.email}">{alert}'
        if self.page == "review":
            return f"<h1>Review</h1><p>{self.email}</p>"
        if self.page == "done":
            return "<h1>Sent</h1>"
        return "<h1>Welcome</h1>"

    def _info(self, spec=None) -> dict:
        self.specs = self._specs()
        html = self._html()
        return {
            "action_specs": self.specs,
            "state_key": f"{self.page}|{self.email}|{self.error}",
            "page": {"url": f"http://fake/{self.page}.html", "html": html, "visible_text": html},
            "action_spec": spec,
            "exec_info": {"success": True},
            "episode_context": [0.0] * 12,
        }

    def reset(self):
        self.page, self.email, self.error, self.steps = "home", "", False, 0
        return {}, self._info()

    def step(self, index: int):
        spec = self.specs[index]
        self.steps += 1
        dom = (spec.selector or "")[5:-2]
        if dom == "start":
            self.page = "form"
        elif dom == "email":
            category = spec.params["category"]
            self.email = {
                "valid_typical": "tester@example.com",
                "type_mismatch": "not-an-email",
            }.get(category, "")
        elif dom == "next":
            self.error = "@" not in self.email
            if not self.error:
                self.page = "review"
        elif dom == "edit":
            self.page = "form"
        elif dom == "send":
            self.page = "done"
        elif dom == "again" or spec.action_type is ActionType.BROWSER_BACK:
            self.page = "home"
        return {}, 0.0, False, self.steps >= self.max_steps, self._info(spec)

    def close(self) -> None:
        pass


class NullMonitor:
    verification_seconds = 0.0

    def start_episode(self, info): ...

    def observe(self, info, executed, available, step):
        return []

    def summary(self):
        return {"faults": []}


def test_hybrid_policy_explores_returns_and_probes():
    policy = HybridPolicy(seed=3, planner_factory=BanditPlanner)
    result = run_hunt(FakeApp(), policy, NullMonitor(), budget=400)
    summary = policy.summary()
    assert result.steps == 400 and summary["episodes"] == 10
    kinds = {cell.kind for cell in policy.archive.cells.values()}
    assert len(policy.archive) >= 4 and kinds  # one-field pages are not "form" (needs two)
    assert summary["returns"] >= 8 and summary["failed_returns"] == 0
    assert sum(summary["operators"].values()) >= 8
    deep = max(policy.archive.cells.values(), key=lambda c: c.depth)
    assert deep.depth <= 20  # walks are pruned, not whole episodes


def test_validation_consistency_is_raised_on_a_bypass():
    """The fake app rejects a malformed email at 'Next' and would accept it via review edit
    only if it had the bypass; it does not, so no validation-consistency candidate appears."""
    policy = HybridPolicy(seed=5)
    run_hunt(FakeApp(), policy, NullMonitor(), budget=400)
    assert not [c for c in policy.candidates if c.invariant == "validation_consistency"]


class BypassApp(FakeApp):
    """The same app with a seeded bypass: after 'Edit' from the review, Next skips the check."""

    def reset(self):
        self.editing = False
        return super().reset()

    def step(self, index: int):
        dom = (self.specs[index].selector or "")[5:-2]
        if dom == "edit":
            self.editing = True
        if dom == "next" and self.editing:
            spec = self.specs[index]
            self.steps += 1
            self.error, self.page = False, "review"
            return {}, 0.0, False, self.steps >= self.max_steps, self._info(spec)
        return super().step(index)


def test_validation_bypass_is_found_without_a_specification():
    policy = HybridPolicy(seed=1, planner_factory=BanditPlanner)
    run_hunt(BypassApp(), policy, NullMonitor(), budget=1200)
    raised = [c for c in policy.candidates if c.invariant == "validation_consistency"]
    assert raised, policy.summary()["operators"]
    assert raised[0].context_trace and raised[0].trace  # both walks kept for reproduction
