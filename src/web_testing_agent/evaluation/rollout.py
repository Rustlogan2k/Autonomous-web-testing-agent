"""Policy-agnostic rollout harness and the metrics every experiment reports.

Exists primarily so that **the random-action baseline is measured before any RL is
trained.** In GUI-testing research, uniform-random ("monkey") exploration is a
notoriously strong baseline; a DQN that does not beat it at equal step budget has
contributed nothing, and that is far cheaper to discover here than after a 300K-step
training run. The same harness scores a trained policy, so the comparison is exactly
like-for-like.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from ..envs.types import ActionSpec, ActionType
from ..utils.logging import get_logger

logger = get_logger(__name__)


class Policy(Protocol):
    """Anything that can choose an action index given an observation and step info."""

    def reset(self) -> None: ...

    def act(self, observation: dict, info: dict) -> int: ...


class RandomPolicy:
    """Uniform-random over the full Discrete(MAX_ACTIONS) space — the honest baseline.

    Deliberately samples the *whole* action space rather than only the currently valid
    slots, because that is exactly what SB3's DQN does during epsilon-greedy
    exploration: it has no action masking, so out-of-range indices resolve to NO_OP.
    Sampling only valid actions here would flatter the baseline relative to the agent
    it is meant to bound. `valid_only=True` measures the masked variant for comparison.
    """

    def __init__(self, num_actions: int, seed: int | None = None, valid_only: bool = False) -> None:
        self.num_actions = num_actions
        self.valid_only = valid_only
        self._rng = np.random.default_rng(seed)

    def reset(self) -> None:
        return None

    def act(self, observation: dict, info: dict) -> int:
        if self.valid_only:
            specs = info.get("action_specs") or []
            if specs:
                return int(self._rng.integers(0, len(specs)))
        return int(self._rng.integers(0, self.num_actions))


class TrainedPolicy:
    """Adapts a trained SB3 model to the same `Policy` protocol the baseline uses.

    Evaluation runs against the **raw** env rather than through
    `SemanticPerceptionWrapper`, so that a trained agent and the random baseline are
    scored by byte-identical harness code. The encoding the wrapper would have done is
    therefore done here instead, via the same `encode_modalities` call — anything else
    would silently compare two different measurement pipelines.
    """

    def __init__(self, model, encoders: dict, deterministic: bool = True) -> None:  # noqa: ANN001
        from ..perception.vec_wrapper import encode_modalities

        self._model = model
        self._encoders = encoders
        self._encode = encode_modalities
        self.deterministic = deterministic

    def reset(self) -> None:
        return None

    def act(self, observation: dict, info: dict) -> int:
        features = self._encode(
            self._encoders,
            [observation["screenshot"]],
            [info.get("page", {})],
            [info.get("episode_context")],
        )
        action, _ = self._model.predict(features, deterministic=self.deterministic)
        return int(np.asarray(action).reshape(-1)[0])


@dataclass
class Finding:
    """One deduplicated deterministic-trigger hit, as a report-ready record."""

    trigger: str
    url: str
    action: str
    element: str
    detail: str = ""
    first_seen_step: int = 0
    times_seen: int = 1

    def key(self) -> tuple[str, str, str]:
        return (self.trigger, self.url, self.element)


@dataclass
class RolloutReport:
    """Everything needed to compare two policies on the same target."""

    label: str = ""
    target_url: str = ""
    episodes: int = 0
    steps: int = 0
    wall_clock_s: float = 0.0
    total_reward: float = 0.0
    episode_rewards: list[float] = field(default_factory=list)
    unique_states: int = 0
    valid_action_steps: int = 0
    exec_success_steps: int = 0
    noop_steps: int = 0
    trigger_counts: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    @property
    def steps_per_second(self) -> float:
        return self.steps / self.wall_clock_s if self.wall_clock_s else 0.0

    @property
    def mean_episode_reward(self) -> float:
        return sum(self.episode_rewards) / len(self.episode_rewards) if self.episode_rewards else 0.0

    @property
    def valid_action_rate(self) -> float:
        return self.valid_action_steps / self.steps if self.steps else 0.0

    @property
    def exec_success_rate(self) -> float:
        return self.exec_success_steps / self.steps if self.steps else 0.0

    @property
    def distinct_findings(self) -> int:
        return len(self.findings)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "target_url": self.target_url,
            "episodes": self.episodes,
            "steps": self.steps,
            "wall_clock_s": round(self.wall_clock_s, 2),
            "steps_per_second": round(self.steps_per_second, 3),
            "total_reward": round(self.total_reward, 3),
            "mean_episode_reward": round(self.mean_episode_reward, 3),
            "episode_rewards": [round(r, 3) for r in self.episode_rewards],
            "unique_states": self.unique_states,
            "valid_action_rate": round(self.valid_action_rate, 4),
            "exec_success_rate": round(self.exec_success_rate, 4),
            "noop_steps": self.noop_steps,
            "distinct_findings": self.distinct_findings,
            "trigger_counts": dict(sorted(self.trigger_counts.items())),
            "findings": [asdict(f) for f in self.findings],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        logger.info("Wrote rollout report to {}", path)

    def summary(self) -> str:
        return (
            f"{self.label}: {self.episodes} episodes / {self.steps} steps in "
            f"{self.wall_clock_s:.1f}s ({self.steps_per_second:.2f} steps/s)\n"
            f"  mean episode reward : {self.mean_episode_reward:+.2f}\n"
            f"  unique states       : {self.unique_states}\n"
            f"  distinct findings   : {self.distinct_findings}\n"
            f"  valid-action rate   : {self.valid_action_rate:.1%}\n"
            f"  exec-success rate   : {self.exec_success_rate:.1%}\n"
            f"  NO_OP steps         : {self.noop_steps} ({self.noop_steps / max(self.steps, 1):.1%})\n"
            f"  triggers            : {dict(sorted(self.trigger_counts.items())) or '{}'}"
        )


def _describe(spec: ActionSpec) -> tuple[str, str]:
    return (spec.action_type.value, spec.element_id or spec.selector or spec.description)


class Recorder(Protocol):
    """Optional observer of a rollout — see `annotation.TraceRecorder`.

    Kept as a hook rather than something the metrics code knows about, so that trace
    capture cannot change what a comparison measures: every policy is scored by the
    same code path whether or not anything is recording.
    """

    def on_reset(self, observation: dict, info: dict) -> None: ...

    def on_step(
        self, action: int, observation: dict, reward: float,
        terminated: bool, truncated: bool, info: dict,
    ) -> None: ...


def run_rollout(
    env,  # noqa: ANN001 - WebTestingEnv, untyped to avoid importing Playwright here
    policy: Policy,
    episodes: int = 5,
    label: str = "rollout",
    seed: int | None = None,
    recorder: Recorder | None = None,
) -> RolloutReport:
    """Run `episodes` episodes of `policy` against `env` and collect comparable metrics."""
    report = RolloutReport(label=label, target_url=getattr(env, "base_url", ""), episodes=episodes)
    findings: dict[tuple[str, str, str], Finding] = {}
    triggers: Counter[str] = Counter()
    started = time.monotonic()

    for episode in range(episodes):
        observation, info = env.reset(seed=None if seed is None else seed + episode)
        policy.reset()
        if recorder is not None:
            recorder.on_reset(observation, info)
        episode_reward = 0.0

        while True:
            action = policy.act(observation, info)
            num_valid = len(info.get("action_specs") or [])
            observation, reward, terminated, truncated, info = env.step(action)
            if recorder is not None:
                recorder.on_step(action, observation, float(reward), terminated, truncated, info)

            report.steps += 1
            episode_reward += float(reward)
            if action < num_valid:
                report.valid_action_steps += 1
            if info.get("exec_success"):
                report.exec_success_steps += 1

            spec: ActionSpec | None = info.get("action_spec")
            if spec is not None and spec.action_type is ActionType.NO_OP:
                report.noop_steps += 1

            _record_findings(info, report.steps, findings, triggers)

            if terminated or truncated:
                break

        report.episode_rewards.append(episode_reward)
        report.total_reward += episode_reward
        logger.info(
            "[{}] episode {}/{} finished: reward={:+.2f} steps={}",
            label, episode + 1, episodes, episode_reward, report.steps,
        )

    report.wall_clock_s = time.monotonic() - started
    report.trigger_counts = dict(triggers)
    report.findings = sorted(findings.values(), key=lambda f: f.first_seen_step)
    tracker = getattr(env, "exploration", None)
    report.unique_states = tracker.total_states_seen if tracker is not None else 0
    return report


def _record_findings(
    info: dict,
    step: int,
    findings: dict[tuple[str, str, str], Finding],
    triggers: Counter,
) -> None:
    """Deduplicate trigger hits into distinct findings.

    Counting raw trigger firings would let one persistently broken element inflate the
    score arbitrarily; what matters for comparing policies is how many *distinct* real
    problems were reached.
    """
    signals = info.get("bug_signals")
    if signals is None or not signals.any_triggered:
        return

    spec: ActionSpec | None = info.get("action_spec")
    action, element = _describe(spec) if spec is not None else ("?", "")
    url = (info.get("page") or {}).get("url", "")

    hits: list[tuple[str, str]] = []
    if signals.console_errors:
        hits.append(("console_error", signals.console_errors[0][:200]))
    if signals.unexpected_http_errors:
        first = signals.unexpected_http_errors[0]
        hits.append(("http_error", f"{first.response_status} {first.url}"))
    if signals.document_http_error:
        hits.append(("document_http_error", ""))
    if signals.slow_response:
        hits.append(("slow_response", f"{signals.load_duration_s:.1f}s without quiescence"))
    if signals.broken_navigation:
        hits.append(("broken_navigation", url))

    for trigger, detail in hits:
        triggers[trigger] += 1
        finding = Finding(
            trigger=trigger, url=url, action=action, element=element,
            detail=detail, first_seen_step=step,
        )
        existing = findings.get(finding.key())
        if existing is None:
            findings[finding.key()] = finding
        else:
            existing.times_seen += 1
