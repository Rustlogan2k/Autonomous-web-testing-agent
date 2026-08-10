"""Shared reward-model interface consumed by WebFunctionalEnv.

Defines only the seam, so the env is fully runnable — off the deterministic triggers
alone — independently of whichever judge is plugged into it.

Two additions the LLM judge forced, both on the ABC rather than on one implementation:

**`start_episode()`.** Five of the seven semantic bugs in the answer key are cross-step:
a dropped field is only visible by comparing what was typed against what a later page
echoes back. A judge therefore keeps a rolling window of its own, and that window must
be cleared when a new episode begins — a fresh browser context shares no state with the
previous episode, so carrying records across invites causal links that cannot exist.
Default no-op, so a stateless model ignores it.

**`context` on `score()`.** `(before, action, after)` is not enough to render the window
the judge was measured against. Whether the action executed, whether the harness refused
it, whether the page ever settled, and whether the document actually changed are all
either invisible in the two observations or expensive to recompute — and every one of
them corresponds to a defect this project has already been bitten by (a harness refusal
read as a broken link, a rendered no-change claim contradicting the record). Passing the
`StepContext` the env already assembled keeps the live window byte-identical to the
offline one. It is optional and keyword-only so existing implementations are unaffected.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..envs.types import ActionSpec, RawObservation

if TYPE_CHECKING:  # avoids a cycle: envs.base_env imports this module's package
    from ..envs.base_env import StepContext


@dataclass(frozen=True, slots=True)
class RewardSignal:
    is_expected: bool
    severity: float  # 0.0-1.0, only meaningful when is_expected is False
    explanation: str = ""
    # Free-form per-step detail for `info`, so a live judgment can be traced back to the
    # verdict that produced it without the env knowing anything about judges.
    detail: dict[str, Any] | None = None


class FunctionalRewardModel(ABC):
    """Interface for Agent B's per-step judge."""

    @abstractmethod
    def score(
        self,
        before: RawObservation,
        action: ActionSpec,
        after: RawObservation,
        *,
        context: "StepContext | None" = None,
    ) -> RewardSignal:
        raise NotImplementedError

    def start_episode(self) -> None:
        """Called from `reset()`. Override to clear any cross-step state."""


class NullRewardModel(FunctionalRewardModel):
    """Default no-op model, so WebFunctionalEnv runs on deterministic triggers alone.

    Always reports "expected, not a bug". Swap in a real model via
    `WebFunctionalEnv(reward_model=...)` without touching any env code.
    """

    def score(
        self,
        before: RawObservation,
        action: ActionSpec,
        after: RawObservation,
        *,
        context: "StepContext | None" = None,
    ) -> RewardSignal:
        return RewardSignal(is_expected=True, severity=0.0, explanation="LLM reward model not configured")
