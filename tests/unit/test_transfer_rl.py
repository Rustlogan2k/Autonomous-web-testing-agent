"""The transferable learning tester: features, network, replay, agent, reward and search.

The label encoder is replaced by a deterministic stand-in so nothing here loads a sentence
model; everything else is the production code.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest
import torch as th

from web_testing_agent.envs.types import EPISODE_CONTEXT_DIM, ActionSpec, ActionType
from web_testing_agent.rl import (
    ACTION_DIM,
    STATE_DIM,
    AgentConfig,
    FeatureExtractor,
    FeatureSearchPolicy,
    QAgent,
    RewardComposer,
    RewardConfig,
    TransferQNetwork,
)
from web_testing_agent.rl.features import LABEL_SLICE, numeric_index, role_of
from web_testing_agent.rl.replay import PrioritizedReplay, Transition, collate


class FakeLabels:
    """Deterministic unit vectors per string, the shape of the real 48-D projection."""

    dim = 48
    fingerprint = "fake"
    model_name = "fake"

    def encode_batch(self, labels):  # noqa: ANN001
        rows = []
        for label in labels:
            if not label:
                rows.append(np.zeros(self.dim, dtype=np.float32))
                continue
            seed = int(hashlib.sha256(label.encode()).hexdigest()[:8], 16)
            vector = np.random.default_rng(seed).normal(size=self.dim).astype(np.float32)
            rows.append(vector / np.linalg.norm(vector))
        return np.stack(rows) if rows else np.zeros((0, self.dim), dtype=np.float32)

    def encode(self, label):  # noqa: ANN001
        return self.encode_batch([label])[0]


def page(state_key: str, specs: list[ActionSpec], html: str = "", novel: float = 0.0) -> dict:
    context = np.zeros(EPISODE_CONTEXT_DIM, dtype=np.float32)
    context[2] = novel
    return {"state_key": state_key, "action_specs": specs, "episode_context": context,
            "page": {"url": f"http://app/{state_key}", "html": html}, "newly_revealed": []}


SPECS = [
    ActionSpec(index=0, action_type=ActionType.NO_OP, description="no-op"),
    ActionSpec(index=1, action_type=ActionType.CLICK, selector='[id="go"]', element_id="Reserve",
               params={"navigational": True}),
    ActionSpec(index=2, action_type=ActionType.TYPE, selector='[id="n"]', element_id="nights",
               params={"value": "42", "category": "valid_typical", "label_text": "Nights"}),
    ActionSpec(index=3, action_type=ActionType.CLICK, selector='[id="opt"]', element_id="extras",
               description="toggle extras"),
]
HTML = '<input id="n" name="nights" value="42"><input id="opt" type="checkbox" checked>'


@pytest.fixture
def extractor() -> FeatureExtractor:
    return FeatureExtractor(labels=FakeLabels(), anchor=lambda label: 0.25)


# -- features -----------------------------------------------------------------------------


def test_encoding_shapes_and_the_form_state_of_the_targeted_control(extractor):
    encoded = extractor.encode(page("s", SPECS, HTML))
    assert encoded.state.shape == (STATE_DIM,)
    assert encoded.actions.shape == (len(SPECS), ACTION_DIM)
    typed = encoded.actions[2]
    assert typed[numeric_index("field_is_filled")] == 1.0
    assert typed[numeric_index("value_already_set")] == 1.0
    assert encoded.actions[3][numeric_index("toggle_is_on")] == 1.0
    assert role_of(SPECS[3]) == "toggle"


def test_the_visible_label_is_what_gets_embedded(extractor):
    encoded = extractor.encode(page("s", SPECS, HTML))
    expected = FakeLabels().encode("Nights")
    assert np.allclose(encoded.actions[2][LABEL_SLICE], expected)


def test_no_feature_changes_when_only_identity_changes(extractor):
    """Renaming DOM ids and moving the app to another URL must not change what the policy sees."""
    renamed = [ActionSpec(index=s.index, action_type=s.action_type,
                          selector=(s.selector or "").replace('[id="', '[id="x-') or None,
                          element_id=s.element_id, params=dict(s.params),
                          description=s.description) for s in SPECS]
    html = HTML.replace('id="n"', 'id="x-n"').replace('id="opt"', 'id="x-opt"')
    one = extractor.encode(page("s", SPECS, HTML)).actions
    other = FeatureExtractor(labels=FakeLabels(), anchor=lambda label: 0.25)
    two = other.encode({**page("t", renamed, html), "page": {"url": "http://elsewhere:9/z",
                                                              "html": html}}).actions
    assert np.allclose(one, two)


def test_recording_a_step_updates_the_counts_the_policy_sees(extractor):
    before = page("s", SPECS, HTML)
    assert extractor.encode(before).actions[1][numeric_index("tried_here")] == 0.0
    extractor.record(before, SPECS[1], page("t", SPECS, novel=1.0))
    row = extractor.encode(before).actions[1]
    assert row[numeric_index("tried_here")] == 1.0
    assert row[numeric_index("changed_state_rate")] == 1.0
    assert row[numeric_index("reached_new_rate")] == 1.0


# -- network ----------------------------------------------------------------------------------


def test_the_scorer_is_permutation_equivariant_over_actions():
    th.manual_seed(0)
    network = TransferQNetwork(STATE_DIM, ACTION_DIM)
    state = th.randn(1, STATE_DIM)
    actions = th.randn(1, 5, ACTION_DIM)
    mask = th.ones(1, 5, dtype=th.bool)
    permutation = th.tensor([3, 0, 4, 1, 2])
    q = network(state, actions, mask)
    assert th.allclose(q[:, permutation], network(state, actions[:, permutation], mask), atol=1e-6)


def test_the_residual_head_starts_as_an_exact_no_op_and_freezes_the_base():
    th.manual_seed(0)
    network = TransferQNetwork(STATE_DIM, ACTION_DIM)
    state, actions = th.randn(2, STATE_DIM), th.randn(2, 6, ACTION_DIM)
    mask = th.ones(2, 6, dtype=th.bool)
    before = network(state, actions, mask)
    network.add_residual()
    assert th.equal(before, network(state, actions, mask))
    assert all(not p.requires_grad for p in network.base.parameters())
    assert network.trainable_parameters()


def test_padding_never_changes_a_valid_actions_value():
    th.manual_seed(0)
    network = TransferQNetwork(STATE_DIM, ACTION_DIM)
    state, actions = th.randn(1, STATE_DIM), th.randn(1, 3, ACTION_DIM)
    padded = th.cat([actions, th.zeros(1, 4, ACTION_DIM)], dim=1)
    mask = th.tensor([[True, True, True, False, False, False, False]])
    q_short = network(state, actions, th.ones(1, 3, dtype=th.bool))
    q_padded = network(state, padded, mask)
    assert th.allclose(q_short, q_padded[:, :3], atol=1e-6)
    assert (q_padded[:, 3:] < -1e7).all()


# -- replay -------------------------------------------------------------------------------------


def _transition(n: int, reward: float = 0.0) -> Transition:
    return Transition(np.zeros(STATE_DIM, np.float32), np.ones((n, ACTION_DIM), np.float16), 0,
                      reward, np.zeros(STATE_DIM, np.float32), np.ones((n + 1, ACTION_DIM), np.float16),
                      False, 0.9)


def test_a_batch_is_padded_to_its_widest_action_set_and_masked():
    arrays = collate([_transition(2), _transition(5)])
    _states, actions, mask, *_rest, next_mask, _terminals, discounts = arrays
    assert actions.shape[1] == 5 and mask.sum(axis=1).tolist() == [2, 5]
    assert next_mask.sum(axis=1).tolist() == [3, 6]
    assert discounts[:, 0].tolist() == pytest.approx([0.9, 0.9])


def test_prioritized_sampling_prefers_high_error_transitions():
    replay = PrioritizedReplay(100, alpha=1.0, seed=0)
    for _ in range(10):
        replay.add(_transition(2))
    replay.update_priorities(np.arange(10), np.array([10.0] + [0.01] * 9))
    _batch, indices, weights = replay.sample(1000)
    assert (indices == 0).mean() > 0.8
    assert weights.max() == pytest.approx(1.0)


# -- reward --------------------------------------------------------------------------------------


class FixedScorer:
    """A potential table, standing in for the language model."""

    def __init__(self, table: dict[str, float | None]) -> None:
        self.table = table
        self.stats = type("S", (), {"to_dict": lambda self: {}})()

    def potential(self, url, html, *, context="", visible_text=None):  # noqa: ANN001, ARG002
        value = self.table.get(url.rsplit("/", 1)[-1])
        if value is None:
            return None
        return type("J", (), {"value": value})()

    def config(self) -> dict:
        return {}


def test_potential_shaping_telescopes_around_a_cycle():
    scorer = FixedScorer({"a": 0.0, "b": 0.8, "c": 0.3})
    composer = RewardComposer(RewardConfig(llm_weight=2.0, shaping_gamma=1.0,
                                           llm_mode="potential"), scorer)
    total = 0.0
    for start, end in (("a", "b"), ("b", "c"), ("c", "a")):
        _reward, parts = composer.compute(page(start, []), page(end, []), 0.0)
        total += parts["llm_shaping"]
    assert total == pytest.approx(0.0)


def test_the_progress_record_pays_only_new_episode_maxima_and_resets_each_episode():
    scorer = FixedScorer({"home": 0.0, "form": 0.6, "info": 0.0, "done": 1.0})
    composer = RewardComposer(RewardConfig(llm_weight=2.0, outcome_bonus=0.0), scorer)
    composer.start_episode()
    paid = [composer.compute(page(a, []), page(b, []), 0.0)[1]["llm_shaping"]
            for a, b in (("home", "form"), ("form", "info"), ("info", "form"),
                         ("form", "done"), ("done", "form"), ("form", "done"))]
    assert paid == pytest.approx([1.2, 0.0, 0.0, 0.8, 0.0, 0.0])
    assert sum(paid) <= 2.0  # bounded by lambda * max Phi per episode
    composer.start_episode()
    assert composer.compute(page("home", []), page("form", []), 0.0)[1]["llm_shaping"] ==         pytest.approx(1.2)


def test_the_outcome_bonus_pays_once_per_outcome_state_per_episode():
    scorer = FixedScorer({"home": 0.0, "form": 0.6, "done": 1.0, "receipt": 0.9})
    composer = RewardComposer(RewardConfig(llm_weight=0.0001, outcome_bonus=3.0), scorer)
    composer.start_episode()
    paid = [composer.compute(page(a, []), page(b, []), 0.0)[1]["llm_shaping"]
            for a, b in (("home", "form"), ("form", "done"), ("done", "form"),
                         ("form", "done"), ("done", "receipt"))]
    assert [round(x) for x in paid] == [0, 3, 0, 0, 3]
    composer.start_episode()
    assert round(composer.compute(page("form", []), page("done", []), 0.0)[1]["llm_shaping"]) == 3


def test_a_missing_potential_means_no_shaping_not_a_guess():
    composer = RewardComposer(RewardConfig(llm_weight=1.0, llm_mode="potential"),
                              FixedScorer({"a": 0.2, "b": None}))
    reward, parts = composer.compute(page("a", []), page("b", []), 0.5)
    assert parts["llm_shaping"] == 0.0 and reward == pytest.approx(0.5)
    assert composer.missing_potential == 1


def test_the_gate_bonus_is_capped_at_one_control_per_step():
    composer = RewardComposer(RewardConfig(reveal_cap=1.5))
    after = {**page("b", []), "reveal_bonus": 15.0}
    reward, _parts = composer.compute(page("a", []), after, 15.0 - 0.05)
    assert reward == pytest.approx(1.45)


# -- agent -------------------------------------------------------------------------------------


def test_the_agent_learns_to_prefer_the_rewarded_action(tmp_path):
    config = AgentConfig(algorithm="bandit", learning_starts=16, batch_size=16, seed=0,
                         epsilon_start=1.0, epsilon_end=1.0, epsilon_decay_steps=1,
                         learning_rate=3e-3)
    agent = QAgent(config, labels=FakeLabels())
    agent.set_application("app")
    info = page("s", SPECS, HTML)
    agent.begin_episode(info)
    for _ in range(300):
        action = agent.act({}, info)
        reward = 1.0 if action == 1 else 0.0
        info = page("s", SPECS, HTML)
        agent.observe_transition(info, action, reward, info, False, False)
    values = agent.q_values(agent.features.encode(page("s", SPECS, HTML)))
    assert int(np.argmax(values)) == 1

    path = agent.save(tmp_path / "agent.pt", meta={"note": "test"})
    loaded = QAgent.load(path, labels=FakeLabels())
    assert np.allclose(loaded.q_values(loaded.features.encode(page("s", SPECS, HTML))), values,
                       atol=1e-6)
    assert QAgent.read_header(path)["meta"] == {"note": "test"}


def test_zero_shot_mode_never_updates_weights():
    agent = QAgent(AgentConfig(learn=False, learning_starts=1, batch_size=1), labels=FakeLabels())
    snapshot = {k: v.clone() for k, v in agent.network.state_dict().items()}
    info = page("s", SPECS, HTML)
    agent.begin_episode(info)
    for _ in range(20):
        action = agent.act({}, info)
        agent.observe_transition(info, action, 1.0, info, False, False)
    assert all(th.equal(snapshot[k], v) for k, v in agent.network.state_dict().items())
    assert len(agent.replay) == 0


# -- search --------------------------------------------------------------------------------------


def test_search_avoids_a_control_already_used_elsewhere_in_the_run():
    labels = FakeLabels()
    policy = FeatureSearchPolicy(epsilon=0.0, features=FeatureExtractor(labels=labels,
                                                                        anchor=lambda _l: 0.0))
    nav = [ActionSpec(index=0, action_type=ActionType.CLICK, selector="a >> nth=0",
                      element_id="Store", params={"navigational": True, "href": "/"}),
           ActionSpec(index=1, action_type=ActionType.CLICK, selector="a >> nth=1",
                      element_id="Help", params={"navigational": True, "href": "/h"})]
    first = page("p1", nav)
    choice = policy.act({}, first)
    policy.observe_transition(first, choice, 0.0, {**page("p2", nav), "action_spec": nav[choice]},
                              False, False)
    assert policy.act({}, page("p2", nav)) != choice


def test_n_step_windows_are_committed_once_with_the_right_horizon_and_terminality():
    """An episode of five steps with n=3, walked by hand.

    Expected windows (start step, steps, terminal): (0,3,F), (1,3,F), (2,3,T), (3,2,T),
    (4,1,T). The (2,3) window ends on the episode's last step, so it must be terminal; an
    earlier version committed it through the non-terminal path before flushing.
    """
    config = AgentConfig(algorithm="dqn", n_step=3, gamma=0.5, learning_starts=10_000, seed=0)
    agent = QAgent(config, labels=FakeLabels())
    agent.set_application("app")
    info = page("s0", SPECS, HTML)
    agent.begin_episode(info)
    rewards = [1.0, 2.0, 3.0, 4.0, 5.0]
    for t, r in enumerate(rewards):
        agent.act({}, info)
        after = page(f"s{t + 1}", SPECS, HTML)
        agent.observe_transition(info, 1, r, after, False, t == len(rewards) - 1)
        info = after
    stored = [agent.replay._items[i] for i in range(len(agent.replay))]
    shaped = [agent.rewarder.config]  # reward composer adds only env reward here (no LLM)
    assert len(stored) == 5
    expected = [(1 + 0.5 * 2 + 0.25 * 3, 0.125, False), (2 + 0.5 * 3 + 0.25 * 4, 0.125, False),
                (3 + 0.5 * 4 + 0.25 * 5, 0.125, True), (4 + 0.5 * 5, 0.25, True), (5, 0.5, True)]
    for transition, (reward, discount, terminal) in zip(stored, expected, strict=True):
        assert transition.reward == pytest.approx(reward)
        assert transition.discount == pytest.approx(discount)
        assert transition.terminal is terminal
    assert shaped
