"""The benchmark agents' learners, without a browser: published settings for the tabular
learner, the deep curiosity DQN (`hybrid/deep_curiosity.py`) and the learned return policy
(`hybrid/cell_learner.py`)."""

from __future__ import annotations

import math

import numpy as np

from web_testing_agent.envs.types import ActionSpec, ActionType
from web_testing_agent.hybrid.archive import Archive
from web_testing_agent.hybrid.cell_learner import LearnedCellPlanner
from web_testing_agent.hybrid.curiosity import CuriosityQ, action_key
from web_testing_agent.hybrid.deep_curiosity import (
    LEARN_START,
    DeepCuriosityQ,
    action_tokens,
    state_tokens,
)
from web_testing_agent.hybrid.policy import HybridPolicy, Planner
from web_testing_agent.hybrid.run import (
    ARMS,
    HybridConfig,
    HybridJob,
    _episode_steps,
    _policy,
    _settings,
)


def _click(i, dom):
    return ActionSpec(index=i, action_type=ActionType.CLICK, selector=f'[id="{dom}"]',
                      element_id=dom, params={}, description=f"click {dom}")


A, B = _click(1, "a"), _click(2, "b")
FORM = "form|/owners/new|New owner|empty||s"
LIST = "list|/owners|Owners|||"


# -- A: the tabular learner at published settings ---------------------------------------------


def test_curiosity_learner_takes_published_settings():
    learner = CuriosityQ(alpha=0.95, gamma=0.65)
    assert math.isclose(learner._value("s", action_key(A)), 1.5 / 0.35)
    r = learner.update("s", A, "t", [A], done=True)
    assert math.isclose(learner.q[("s", action_key(A))], 1.5 / 0.35 + 0.95 * (r - 1.5 / 0.35))


def test_arm_episode_length_overrides_the_run():
    config = HybridConfig(episode_steps=40)
    spec = ARMS["hybrid_curiosity_v5_pub"]
    assert _episode_steps(config, spec) == 100
    assert _settings(config, spec).max_steps == 100
    assert _settings(config, ARMS["hybrid_curiosity_v5"]).max_steps == 40
    policy, _ = _policy(HybridJob("hybrid_curiosity_v5_pub", "app", 0), "app", config)
    assert policy.archive.episode_steps == 100
    assert (policy.curiosity.alpha, policy.curiosity.gamma) == (0.95, 0.65)


# -- B: deep curiosity ------------------------------------------------------------------------


def test_tokens_describe_state_and_action():
    tokens = state_tokens(FORM)
    assert {"kind:form", "path:/owners/new", "seg0:owners", "head:owner", "fill:empty",
            "offers:s"} <= set(tokens)
    typed = ActionSpec(index=3, action_type=ActionType.TYPE, selector='[id="telephone"]',
                       element_id="telephone", params={"category": "valid_typical",
                                                       "press_enter": True}, description="")
    assert {"type:TYPE", "word:telephone", "category:valid_typical", "enter"} <= set(
        action_tokens(typed))


def test_episodic_novelty_falls_with_repeats_and_resets_per_episode():
    learner = DeepCuriosityQ(seed=0)
    learner.begin_episode()
    s = learner._state(LIST)
    first = learner.episodic(s)
    learner.episode_memory.append(s)
    learner.episode_memory.append(s)
    repeated = learner.episodic(s)
    assert first == 1.0 and repeated < first
    assert math.isclose(repeated, 1 / (math.sqrt(3) + 1e-3), rel_tol=1e-3)
    learner.begin_episode()
    assert learner.episodic(s) == 1.0


def test_lifelong_multiplier_is_clipped_to_one_and_l():
    learner = DeepCuriosityQ(seed=0)
    for cell in (LIST, FORM, "detail|/owners/{n}|Owner|||", LIST, LIST):
        value = learner.lifelong(learner._state(cell))
        assert 1.0 <= value <= learner.max_lifelong


def test_deep_learner_trains_and_chooses_offered_actions():
    learner = DeepCuriosityQ(seed=1, epsilon=0.0)
    learner.begin_episode()
    for step in range(LEARN_START + 20):
        state, after = (LIST, FORM) if step % 2 == 0 else (FORM, LIST)
        reward = learner.update(state, A if step % 2 == 0 else B, after, [A, B], done=False)
        assert reward >= 0.0
    assert learner.updates == LEARN_START + 20
    assert np.isfinite(learner.loss)
    assert learner.choose(LIST, [A, B]).index in (1, 2)
    assert learner.choose(LIST, []) is None
    summary = learner.summary()
    assert summary["states"] == 2 and summary["learner"] == "deep_curiosity"


def test_deep_arm_builds_the_deep_learner_at_published_settings():
    policy, _ = _policy(HybridJob("hybrid_deep_v5", "app", 0), "app", HybridConfig())
    assert isinstance(policy.curiosity, DeepCuriosityQ)
    assert (policy.curiosity.epsilon, policy.curiosity.gamma,
            policy.curiosity.max_lifelong) == (0.4, 0.95, 5.0)


# -- C: learned return policy -----------------------------------------------------------------


def _archive_with_cells():
    archive = Archive(0, episode_steps=40)
    archive.visit("entry", [], "list", 0)
    archive.visit("deep", [{"type": "CLICK", "id": "x"}] * 5, "form", 1)
    archive.visit("dead", [{"type": "CLICK", "id": "y"}] * 3, "detail", 2)
    return archive


def test_learned_planner_learns_which_cells_pay():
    archive = _archive_with_cells()
    planner = LearnedCellPlanner(archive, seed=0)
    picks = []
    for _ in range(120):
        cell = planner.choose_cell({})
        picks.append(cell.key)
        cell.visits += 1
        cell.returns += 1
        planner.episode_feedback(cell, 4 if cell.key == "deep" else 0)
    assert planner.observations == 120
    assert picks[-40:].count("deep") > 25  # the paying cell dominates once learned
    assert planner.state()["decisions"] == 120


def test_feedback_for_another_cell_is_ignored():
    archive = _archive_with_cells()
    planner = LearnedCellPlanner(archive, seed=0)
    chosen = planner.choose_cell({})
    other = next(c for c in archive.cells.values() if c.key != chosen.key)
    planner.episode_feedback(other, 3)
    assert planner.observations == 0


def test_policy_reports_each_episode_to_the_planner():
    seen = []

    class Recording(Planner):
        def episode_feedback(self, cell, new_cells):  # noqa: ANN001
            seen.append((cell.key, new_cells))

    policy = HybridPolicy(planner_factory=Recording)
    policy.archive.visit("entry", [], "list", 0)
    policy.begin_episode({})
    target = policy.target
    policy.archive.visit("new1", [], "list", 1)
    policy.archive.visit("new2", [], "list", 2)
    policy.reset()
    policy.begin_episode({})
    assert seen == [(target.key, 2)]


def test_goexplore_arm_uses_the_learned_planner():
    policy, _ = _policy(HybridJob("hybrid_goexplore_v5", "app", 0), "app", HybridConfig())
    assert isinstance(policy.planner, LearnedCellPlanner)
    assert policy.summary()["cell_learner"]["decisions"] == 0


def test_v6_arms_keep_the_session_and_v5_arms_do_not():
    config = HybridConfig()
    for name in ("hybrid_final", "hybrid_curiosity", "hybrid_goexplore"):
        assert _settings(config, ARMS[f"{name}_v6"]).persist_session
        assert not _settings(config, ARMS[f"{name}_v5"]).persist_session
        v5 = {k: v for k, v in ARMS[f"{name}_v5"].items()}
        v6 = {k: v for k, v in ARMS[f"{name}_v6"].items() if k != "persist_session"}
        assert v5 == v6  # the session is the only difference
