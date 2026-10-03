"""Pretraining one agent on several applications at once.

The agent sees the training applications **interleaved, one episode at a time**, in a fixed
round-robin order. Training on them one after another would let the last application
overwrite what the first taught (the network would be tuned to whichever site it saw most
recently); interleaving keeps every application in the replay buffer throughout, so the
value function has to fit all of them with one set of weights. That is the only thing that
can make it useful on an application outside the set.

Every application keeps its own environment for the whole run, so the run-level novelty
counts and the agent's per-application visit memory behave exactly as they would in a
single-application run. The environments share one Chromium, each in its own context.

No oracle runs during pretraining. The reward contains no fault information, and a
verified fault found during pretraining would be a fact about a *training* application,
which the transfer claim is not about.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..benchmark.suite import EnvSettings, SuiteApp, make_env
from ..utils.logging import get_logger
from .agent import QAgent

logger = get_logger(__name__)


@dataclass
class PretrainReport:
    apps: list[str]
    steps_per_app: int
    steps: dict[str, int] = field(default_factory=dict)
    episodes: dict[str, int] = field(default_factory=dict)
    unique_states: dict[str, int] = field(default_factory=dict)
    total_reward: dict[str, float] = field(default_factory=dict)
    wall_clock_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "apps": self.apps, "steps_per_app": self.steps_per_app, "steps": self.steps,
            "episodes": self.episodes, "unique_states": self.unique_states,
            "total_reward": {k: round(v, 3) for k, v in self.total_reward.items()},
            "wall_clock_s": self.wall_clock_s,
        }


def pretrain(agent: QAgent, apps: list[SuiteApp], urls: dict[str, str], *,
             steps_per_app: int, settings: EnvSettings | None = None,
             on_episode: Callable[[str, int, dict], None] | None = None) -> PretrainReport:
    """Train `agent` for `steps_per_app` interactions on each application, interleaved.

    `urls` maps `app_id` to the entry URL of the build to train on.
    """
    settings = settings or EnvSettings()
    report = PretrainReport(apps=[a.app_id for a in apps], steps_per_app=steps_per_app)
    started = time.monotonic()
    envs = {app.app_id: make_env(urls[app.app_id], settings) for app in apps}
    seen: dict[str, set[str]] = {app.app_id: set() for app in apps}
    for app in apps:
        report.steps[app.app_id] = report.episodes[app.app_id] = 0
        report.total_reward[app.app_id] = 0.0
    try:
        while any(report.steps[a.app_id] < steps_per_app for a in apps):
            for app in apps:
                app_id = app.app_id
                remaining = steps_per_app - report.steps[app_id]
                if remaining <= 0:
                    continue
                env = envs[app_id]
                agent.set_application(app_id, app.manifest.name)
                observation, info = env.reset()
                agent.begin_episode(info)
                seen[app_id].add(info.get("state_key", ""))
                episode_reward = 0.0
                for _ in range(min(settings.max_steps, remaining)):
                    action = agent.act(observation, info)
                    before = info
                    observation, reward, terminated, truncated, info = env.step(action)
                    report.steps[app_id] += 1
                    seen[app_id].add(info.get("state_key", ""))
                    episode_reward += float(reward)
                    # Only a real episode end is terminal. Running out of this application's
                    # budget mid-episode is not: those pending windows are flushed with a
                    # bootstrap when the agent switches application.
                    done = bool(terminated or truncated)
                    agent.observe_transition(before, action, float(reward), info,
                                             bool(terminated), done)
                    if terminated or truncated:
                        break
                report.episodes[app_id] += 1
                report.total_reward[app_id] += episode_reward
                if on_episode is not None:
                    on_episode(app_id, report.episodes[app_id],
                               {"steps": report.steps[app_id], "episode_reward": episode_reward,
                                "unique_states": len(seen[app_id]),
                                "epsilon": round(agent.epsilon, 3), **agent.stats.to_dict()})
    finally:
        for env in envs.values():
            env.close()
    report.unique_states = {k: len(v) for k, v in seen.items()}
    report.wall_clock_s = round(time.monotonic() - started, 2)
    return report
