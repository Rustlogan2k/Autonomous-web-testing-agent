"""The transferable learning tester.

    features    one state/action representation, shared by the learner and the search arm
    label_store persistent cache in front of the frozen label encoder
    network     the shared per-action scorer, with a residual head for adaptation
    replay      prioritized replay over variable-size action sets
    reward      env reward + dynamic LLM progress potential (shaping), logged per step
    agent       the Q-agent: acts, learns online, saves and loads
    search      the non-learning baseline on the same features

`agents/` holds the earlier, Stable-Baselines3-bound stack that every figure published
before the benchmark suite was measured on. It is left exactly as it was so those figures
stay reproducible; this package is the one the transfer experiment and the product use.
"""

from .agent import AgentConfig, QAgent
from .features import ACTION_DIM, FEATURE_VERSION, STATE_DIM, FeatureExtractor, describe_layout
from .network import TransferQNetwork
from .reward import RewardComposer, RewardConfig, RewardLog
from .search import FeatureSearchPolicy

__all__ = [
    "ACTION_DIM", "FEATURE_VERSION", "STATE_DIM", "AgentConfig", "FeatureExtractor",
    "FeatureSearchPolicy", "QAgent", "RewardComposer", "RewardConfig", "RewardLog",
    "TransferQNetwork", "describe_layout",
]
