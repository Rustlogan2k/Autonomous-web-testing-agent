"""Which exploration policies the product offers, and how one is built.

**This module is the seam the finalized RL agent plugs into.** `pipeline.run_pipeline`
takes a `policy_factory` returning anything satisfying `evaluation.rollout.Policy`; this
file is the only place that knows which policies exist. Adding the finished RL model means
adding an `AgentChoice` and a branch in `build_policy` — no route, template, JavaScript or
data model changes, because the UI renders whatever `available_agents()` returns.

**Honesty rules enforced here, not in the templates.**

* A research checkpoint that is not on disk is offered as *unavailable* with the reason,
  rather than being listed and then failing when chosen.
* Every entry carries `research_stage`, and the UI renders a badge from it. The two
  checkpoint-backed agents were trained on one fixture at a 4,000-step budget and their
  learned policy is specific to it; running them against an arbitrary uploaded application
  is legitimate but is not the same claim as "a trained agent for your app", and the
  summary says so.
* Nothing here trains. Training is an experiment, runs for hours, and is not something a
  web request may start.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..utils.logging import get_logger
from .models import AgentChoice

logger = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKPOINTS = REPO_ROOT / "models" / "checkpoints"

#: Checkpoint used when a research agent is selected. Seed 0 of the canonical post-Markov
#: run for AC-DQN and of the contextual-bandit run — the same artifacts
#: `reports/offline_eval_25ep.md` reports, loaded read-only and never written.
_AC_DQN_CHECKPOINT = CHECKPOINTS / "postmarkov_baseline" / "ac_dqn_seed0_final.zip"
_BANDIT_CHECKPOINT = CHECKPOINTS / "contextual_bandit" / "contextual_bandit_seed0_final.zip"

AGENT_RANDOM = "random"
AGENT_SEARCH = "search"
AGENT_AC_DQN = "ac_dqn"
AGENT_BANDIT = "contextual_bandit"

DEFAULT_AGENT = AGENT_SEARCH


def _torch_available() -> bool:
    try:
        import stable_baselines3  # noqa: F401
        import torch  # noqa: F401
    except Exception:  # noqa: BLE001 - a partial install is as unusable as none
        return False
    return True


def available_agents() -> list[AgentChoice]:
    """Every policy the product can run right now, resolved against the filesystem."""
    torch_ok = _torch_available()
    missing_torch = "Requires the deep-learning stack (torch, stable-baselines3)."

    choices = [
        AgentChoice(
            key=AGENT_SEARCH,
            name="Search (hand priority)",
            summary="Deterministic search with a hand-designed, application-agnostic action "
                    "priority. Prefers newly revealed controls, then form fields, then "
                    "navigation, and avoids repeating itself. No training required, so it "
                    "works on any application.",
        ),
        AgentChoice(
            key=AGENT_RANDOM,
            name="Random (masked baseline)",
            summary="Uniform-random over the currently valid actions. The reference "
                    "baseline every experiment in this project is measured against.",
        ),
        AgentChoice(
            key=AGENT_AC_DQN,
            name="AC-DQN (research checkpoint)",
            summary="Action-conditioned Double DQN with a dueling head, prioritised replay "
                    "and 3-step returns. Research stage: the checkpoint was trained on the "
                    "bundled deep-flow fixture at a 4,000-step budget, so its learned "
                    "preferences are specific to that application.",
            research_stage=True,
            available=torch_ok and _AC_DQN_CHECKPOINT.is_file(),
            unavailable_reason=(
                missing_torch if not torch_ok
                else "" if _AC_DQN_CHECKPOINT.is_file()
                else "No trained checkpoint on this machine (models/checkpoints/ is "
                     "gitignored and absent from a fresh clone)."
            ),
        ),
        AgentChoice(
            key=AGENT_BANDIT,
            name="Contextual Bandit (research checkpoint)",
            summary="Myopic control that predicts immediate reward only, with no "
                    "bootstrapped future. Research stage: same fixture-specific training "
                    "caveat as AC-DQN.",
            research_stage=True,
            available=torch_ok and _BANDIT_CHECKPOINT.is_file(),
            unavailable_reason=(
                missing_torch if not torch_ok
                else "" if _BANDIT_CHECKPOINT.is_file()
                else "No trained checkpoint on this machine (models/checkpoints/ is "
                     "gitignored and absent from a fresh clone)."
            ),
        ),
    ]
    return choices


def agent_by_key(key: str) -> AgentChoice | None:
    return next((a for a in available_agents() if a.key == key), None)


def resolve(key: str) -> AgentChoice:
    """The requested agent if it can run, otherwise the default, never an exception.

    A run must not fail because a checkpoint was deleted between rendering the form and
    submitting it; falling back is logged and surfaced in the run's event stream.
    """
    choice = agent_by_key(key)
    if choice is not None and choice.available:
        return choice
    fallback = agent_by_key(DEFAULT_AGENT)
    assert fallback is not None, "the default agent must always be available"
    if choice is not None:
        logger.info("agent {} unavailable ({}); falling back to {}",
                    key, choice.unavailable_reason, fallback.key)
    return fallback


def build_policy(key: str, settings) -> Callable[[Any], Any]:  # noqa: ANN001
    """A `policy_factory` for `pipeline.run_pipeline`, for the named agent.

    Returns a callable taking `RunSettings` — the signature the pipeline already expects —
    so this drops into `PipelineDependencies(policy_factory=...)` unchanged.
    """
    choice = resolve(key)

    if choice.key == AGENT_RANDOM:
        def factory(run_settings):  # noqa: ANN001, ANN202
            from ..envs.types import MAX_ACTIONS
            from ..evaluation import RandomPolicy
            return RandomPolicy(MAX_ACTIONS, seed=run_settings.seed, valid_only=True)
        return factory

    if choice.key == AGENT_SEARCH:
        def factory(run_settings):  # noqa: ANN001, ANN202
            from ..agents.hand_priority import HandPriorityPolicy
            # Epsilon matches the evaluation value used throughout the research
            # protocol, and counters carry across episodes because that is what makes
            # this a search rather than a greedy loop.
            return HandPriorityPolicy(
                epsilon=0.05, seed=run_settings.seed,
                carry_counts_across_episodes=True,
            )
        return factory

    checkpoint = _AC_DQN_CHECKPOINT if choice.key == AGENT_AC_DQN else _BANDIT_CHECKPOINT

    def trained_factory(run_settings):  # noqa: ANN001, ANN202
        """Load the checkpoint read-only and wrap it in the research evaluation adapter.

        Uses `scripts/compare_agents.TrainedPolicy` — the same wrapper every published
        evaluation used — so a policy driven from the UI is driven exactly as it is in the
        experiments. The file is opened for reading and never written.
        """
        import sys

        scripts = str(REPO_ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from compare_agents import TrainedPolicy  # noqa: PLC0415

        from ..agents.action_features import ActionFeatureExtractor
        from ..perception.vec_wrapper import default_encoders

        if choice.key == AGENT_AC_DQN:
            from ..agents.ac_dqn import ActionConditionedDQN as cls
        else:
            from ..agents.contextual_bandit import ContextualBandit as cls

        model = cls.load(str(checkpoint), device="cpu")
        return TrainedPolicy(
            model, default_encoders(), epsilon=0.05, masked=True,
            seed=run_settings.seed, action_features=ActionFeatureExtractor(),
        )

    return trained_factory
