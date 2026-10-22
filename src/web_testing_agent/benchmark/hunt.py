"""One budgeted fault hunt: a policy, an application, a step budget, and the oracle watching.

This is the unit every quantitative result is made of. It runs a policy for exactly
`budget` browser interactions, split into fixed-length episodes that each start from a
fresh session, and reports when -- if ever -- the oracle confirmed a seeded fault.

**The budget counts interactions, not episodes and not seconds.** `env.step()` is one
interaction whatever it does, including a no-op and including an action the page refused.
A reset is not an interaction: it is the harness returning the application to its start
state, and no policy chooses it. Wall-clock time is recorded but is never the metric,
because it measures the machine.

**A run that finds nothing is censored, not failed.** It is recorded with
`censored = True` and `time = budget`: what is known is that the fault was not found
*within* the budget, not that it would never have been. The analysis keeps that
distinction (`evaluation.survival`); recoding it as a zero would bias every comparison
toward whichever arm happened to fail more completely.

**The run continues after the first confirmation**, to the end of the budget. The primary
metric only needs the first, but the discovery-rate and coverage metrics need the rest,
and stopping early would make them incomparable between a run that found a fault at step
90 and one that found it at step 1,900.

**The harness is policy-agnostic, and the learning hook is optional.** A policy that
learns online implements `observe_transition`; one that does not is simply never called.
That keeps a search baseline and a learning agent inside the same loop, with the same
budget accounting and the same oracle, which is the only way their numbers are comparable.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Protocol

from ..envs.types import ActionSpec
from ..utils.logging import get_logger
from .monitor import FaultMonitor

logger = get_logger(__name__)


class HuntPolicy(Protocol):
    """What a hunt needs from a policy. `observe_transition` is optional (see module doc)."""

    def reset(self) -> None: ...

    def act(self, observation: dict, info: dict) -> int: ...


@dataclass
class HuntResult:
    """One run. Everything the evaluation reads, and nothing it has to recompute."""

    budget: int
    steps: int = 0
    episodes: int = 0
    #: Interactions consumed when the first seeded fault was confirmed; `budget` when none
    #: was. Read together with `censored` -- on its own it is not a success indicator.
    time_to_first_verified: int = 0
    censored: bool = True
    oracle: dict = field(default_factory=dict)
    unique_states: int = 0
    #: Distinct states by the step at which they were first seen, sampled every 100 steps,
    #: so coverage can be compared at equal budget rather than only at the end.
    coverage_curve: list[list[int]] = field(default_factory=list)
    action_types: dict = field(default_factory=dict)
    failed_actions: int = 0
    state_changing_actions: int = 0
    total_reward: float = 0.0
    reward_components: dict = field(default_factory=dict)
    explore_seconds: float = 0.0
    verify_seconds: float = 0.0
    wall_clock_s: float = 0.0
    steps_per_second: float = 0.0
    #: What ended the run: the interaction budget, or the wall-clock limit (`deadline_s`).
    stopped_by: str = "budget"

    def to_dict(self) -> dict:
        return asdict(self)


#: Reward-breakdown keys summed over the run, when the env reports them.
_TRACKED_COMPONENTS = ("step_cost", "novelty_bonus", "reveal_bonus", "exploration_bonus",
                       "deterministic_bonus", "llm_base_reward")


def run_hunt(env, policy: HuntPolicy, monitor: FaultMonitor, *, budget: int,
             on_step: Callable[[dict], None] | None = None,
             deadline_s: float | None = None) -> HuntResult:
    """Run `policy` on `env` for `budget` interactions with `monitor` checking every step.

    `deadline_s` adds a wall-clock limit, for comparisons with tools evaluated on a time
    budget (the external benchmark's one-hour runs); the run stops at whichever comes first.
    Without it the run is budgeted in interactions only, exactly as before.

    `on_step` receives a small per-step dict (`step`, `episode`, `reward`, `state_key`,
    `action`, `info`) and exists for reward logging and traces; it must not mutate `info`.
    """
    result = HuntResult(budget=budget, time_to_first_verified=budget)
    seen_states: set[str] = set()
    action_types: Counter = Counter()
    components: Counter = Counter()
    learn = getattr(policy, "observe_transition", None)
    started = time.monotonic()

    def running() -> bool:
        if result.steps >= budget:
            return False
        if deadline_s is not None and time.monotonic() - started >= deadline_s:
            result.stopped_by = "deadline"
            return False
        return True

    while running():
        observation, info = env.reset()
        policy.reset()
        monitor.start_episode(info)
        begin = getattr(policy, "begin_episode", None)
        if callable(begin):
            begin(info)
        result.episodes += 1
        seen_states.add(info.get("state_key", ""))

        while running():
            available: list[ActionSpec] = list(info.get("action_specs") or [])
            state_before = info.get("state_key", "")
            action = int(policy.act(observation, info))
            previous_info = info
            observation, reward, terminated, truncated, info = env.step(action)
            result.steps += 1

            executed: ActionSpec | None = info.get("action_spec")
            if executed is not None:
                action_types[executed.action_type.value] += 1
            if not (info.get("exec_info") or {}).get("success", False):
                result.failed_actions += 1
            state_after = info.get("state_key", "")
            if state_after != state_before:
                result.state_changing_actions += 1
            seen_states.add(state_after)
            result.total_reward += float(reward)
            for key in _TRACKED_COMPONENTS:
                value = info.get(key)
                if isinstance(value, int | float):
                    components[key] += float(value)
            if result.steps % 100 == 0:
                result.coverage_curve.append([result.steps, len(seen_states)])

            before_verify = monitor.verification_seconds
            confirmed = monitor.observe(info, executed, available, result.steps)
            verify_cost = monitor.verification_seconds - before_verify
            if confirmed and result.censored:
                result.censored = False
                result.time_to_first_verified = result.steps

            done = bool(terminated or truncated) or result.steps >= budget
            if callable(learn):
                learn(previous_info, action, float(reward), info, bool(terminated), done)
            if on_step is not None:
                on_step({"step": result.steps, "episode": result.episodes,
                         "reward": float(reward), "state_key": state_after,
                         "action": executed, "info": info,
                         "verify_seconds": verify_cost})
            if terminated or truncated:
                break

    result.wall_clock_s = round(time.monotonic() - started, 2)
    result.verify_seconds = round(monitor.verification_seconds, 2)
    result.explore_seconds = round(result.wall_clock_s - result.verify_seconds, 2)
    result.steps_per_second = round(result.steps / max(result.explore_seconds, 1e-9), 3)
    result.unique_states = len(seen_states)
    if not result.coverage_curve or result.coverage_curve[-1][0] != result.steps:
        result.coverage_curve.append([result.steps, len(seen_states)])
    result.action_types = dict(action_types)
    result.reward_components = {k: round(v, 4) for k, v in components.items()}
    result.total_reward = round(result.total_reward, 4)
    result.oracle = monitor.summary()
    return result
