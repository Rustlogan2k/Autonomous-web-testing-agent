"""Which exploration policies the product offers.

The product offers the project's reinforcement-learning agents and the random baseline they
are measured against, so a user can watch each learner on an application of their own. Each
is built by `tester.session.build_agent` from the experiments' own configuration and
checkpoints; the UI renders whatever `available_agents()` returns.

**Honesty rules enforced here, not in the templates.**

* An agent whose checkpoint is not on disk is offered as *unavailable* with the reason,
  rather than being listed and then failing when chosen.
* Each summary states what is known about the agent's results, so the choice is an
  informed one.
* Nothing here trains offline. Pretraining is an experiment, runs for hours, and is not
  something a web request may start; learners adapt online inside the run only.
"""

from __future__ import annotations

from typing import Any, Callable

from ..utils.logging import get_logger
from .models import AgentChoice

logger = get_logger(__name__)

AGENT_HYBRID_BANDIT = "hybrid_bandit"
AGENT_HYBRID_CURIOSITY = "hybrid_curiosity"
AGENT_V1_DQN = "v1_dqn_pretrained"
AGENT_RANDOM = "random"

#: The RL agents and the random baseline. The default is the hybrid tester with the bandit
#: learner: it needs no checkpoint, so it is always available, and it found the most
#: distinct faults in the held-out evaluation.
DEFAULT_AGENT = AGENT_HYBRID_BANDIT

#: Every offered agent runs through the test-session path (`tester.run_session`); the key is
#: also the session's name for it.
SESSION_AGENTS = {key: key for key in (AGENT_V1_DQN, AGENT_HYBRID_BANDIT,
                                       AGENT_HYBRID_CURIOSITY, AGENT_RANDOM)}


def _torch_available() -> bool:
    try:
        import torch  # noqa: F401
    except Exception:  # noqa: BLE001 - a partial install is as unusable as none
        return False
    return True


def _v1_checkpoint_ok() -> tuple[bool, str]:
    try:
        from ..hybrid.run import v1_checkpoint

        v1_checkpoint(0)
    except Exception as exc:  # noqa: BLE001 - absent checkpoints make the agent unavailable
        return False, f"{exc}"
    return True, ""


def available_agents() -> list[AgentChoice]:
    """The RL agents and the random baseline, resolved against this machine."""
    torch_ok = _torch_available()
    checkpoint_ok, why = _v1_checkpoint_ok()
    v1_ok = torch_ok and checkpoint_ok
    return [
        AgentChoice(
            key=AGENT_V1_DQN,
            name="AC-DQN, pretrained",
            method="Deep Q-learning",
            summary="Action-conditioned dueling double DQN (prioritised replay, n-step "
                    "returns) pretrained on the benchmark applications, adapting online "
                    "through a residual head. Held-out evaluation: a fault in 34 of 40 runs, "
                    "the lowest mean time to a first fault, 17 of 24 distinct faults.",
            research_stage=True,
            available=v1_ok,
            unavailable_reason=("" if v1_ok else
                                "Requires torch." if not torch_ok else
                                f"Pretraining checkpoints not on this machine ({why})."),
        ),
        AgentChoice(
            key=AGENT_HYBRID_BANDIT,
            name="Hybrid tester + bandit",
            method="Thompson-sampling bandit",
            summary="Structured exploration (pages abstracted by function, an archive of "
                    "reached states it returns to exactly, test operators from standard test "
                    "design) with a Thompson-sampling bandit that learns, inside this "
                    "application, which operators pay off where. Held-out evaluation: a "
                    "fault in 34 of 40 runs, 19 of 24 distinct faults, the most of any agent.",
        ),
        AgentChoice(
            key=AGENT_HYBRID_CURIOSITY,
            name="Hybrid tester + curiosity",
            method="Count-based Q-learning",
            summary="The hybrid tester choosing where to explore with tabular Q-learning "
                    "over page transitions, rewarded by curiosity (rarely seen transitions "
                    "and states new to the episode), after WebExplor and WebRLED. A tuning "
                    "round agent: not part of the held-out evaluation.",
            research_stage=True,
        ),
        AgentChoice(
            key=AGENT_RANDOM,
            name="Random (baseline)",
            method="No learning",
            summary="Uniform-random over the currently valid actions; the floor every "
                    "experiment is measured against. Held-out evaluation: a fault in 32 of 40 "
                    "runs, 13 of 24 distinct faults.",
        ),
    ]


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
    """A `policy_factory` for the original `pipeline.run_pipeline` path.

    Builds the same agent as the test-session path (`tester.session.build_agent`), so there
    is one definition of each. The pipeline path has no learning hook, so a learning agent
    acts there without updating; the test-session path, which every route uses, is where it
    learns and verifies.
    """
    choice = resolve(key)

    def factory(run_settings):  # noqa: ANN001, ANN202
        from ..tester.session import SessionSettings, build_agent

        session = SessionSettings(agent=SESSION_AGENTS[choice.key], seed=run_settings.seed,
                                  llm_reward=False, llm_review=False)
        policy, _description = build_agent(session, "target", [])
        return policy
    return factory
