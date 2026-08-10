"""Package: reward"""

from .base import FunctionalRewardModel, NullRewardModel, RewardSignal
from .functional_triggers import FunctionalRewardWeights, compose_reward, detect_bug_signals
from .gating import GateDecision, GateStats, should_judge
from .llm_judge import JudgeRewardModel

__all__ = [
    "FunctionalRewardModel",
    "FunctionalRewardWeights",
    "GateDecision",
    "GateStats",
    "JudgeRewardModel",
    "NullRewardModel",
    "RewardSignal",
    "compose_reward",
    "detect_bug_signals",
    "should_judge",
]
