"""The diagnostics must record training without touching it.

Two properties are pinned here, and they are the reason this module exists rather than a
throwaway script:

1. **RNG neutrality.** The probe and the tagging buffer must not consume a single random
   number from `numpy`, `random` or `torch`. If they did, an instrumented run would
   diverge from an uninstrumented one and every number the instrument produced would be
   about a different run than the one being reasoned about. These tests are the fast
   proof; `scripts/check_probe_rng_neutrality.py` is the end-to-end one, on a real
   600-step browser run.

2. **The probe reads what it claims to read.** It captures a *fixed* observation at the
   first arrival, resolves the two controls by visible label, and records what the
   *initial* network thought there — the one quantity that cannot be recovered after
   training starts, and the one the `order-4` deadlock diagnosis turns on.
"""

from __future__ import annotations

import random
import tempfile
from pathlib import Path

import gymnasium as gym
import numpy as np
import pytest
import torch as th
from gymnasium import spaces
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv

from web_testing_agent.agents.ac_dqn import (
    AC_OBS_DIM,
    ACStateExtractor,
    ActionConditionedDQN,
    ActionConditionedQNetwork,
)
from web_testing_agent.agents.diagnostics import (
    QGapProbe,
    TaggedPrioritizedNStepReplayBuffer,
    _slot_of,
)
from web_testing_agent.agents.replay import PrioritizedNStepReplayBuffer
from web_testing_agent.envs.types import EPISODE_CONTEXT_DIM, MAX_ACTIONS, ActionSpec, ActionType
from web_testing_agent.perception.fusion import FUSED_DIM

SPACE = spaces.Box(-np.inf, np.inf, (AC_OBS_DIM,), np.float32)
ACTION_SPACE = spaces.Discrete(MAX_ACTIONS)
N_VALID = 12


def _click(label: str) -> ActionSpec:
    return ActionSpec(index=0, action_type=ActionType.CLICK, selector=f"[id={label}]",
                      element_id=label, params={"navigational": True})


SPECS = [
    ActionSpec(index=0, action_type=ActionType.NO_OP, description="no-op"),
    *[_click(name) for name in
      ("Home", "Catalog", "Support", "About", "Place order", "Back", "Cancel order",
       "Terms", "Privacy", "Status")],
]


def _net(dueling: bool = True) -> ActionConditionedQNetwork:
    th.manual_seed(0)
    extractor = ACStateExtractor(SPACE, features_dim=FUSED_DIM + EPISODE_CONTEXT_DIM)
    return ActionConditionedQNetwork(
        observation_space=SPACE, action_space=ACTION_SPACE,
        features_extractor=extractor, features_dim=extractor.features_dim,
        net_arch=[], dueling=dueling,
    )


def _obs(n_valid: int = N_VALID) -> np.ndarray:
    rng = np.random.default_rng(7)
    obs = rng.normal(size=(1, AC_OBS_DIM)).astype(np.float32)
    obs[:, -MAX_ACTIONS:] = 0.0
    obs[:, -MAX_ACTIONS:][:, :n_valid] = 1.0
    return obs


class _StubModel:
    """The four attributes `QGapProbe` reads, and nothing else."""

    def __init__(self, observation: np.ndarray) -> None:
        self.q_net = _net()
        self._last_obs = observation
        self.device = th.device("cpu")
        self.num_timesteps = 0
        self.replay_buffer = None


def _info(url: str, specs=SPECS) -> dict:
    return {"page": {"url": url}, "action_specs": list(specs)}


def _drive(probe: QGapProbe, model: _StubModel, urls, dones=None) -> None:
    """Run the probe through a sequence of steps, as SB3's callback plumbing would."""
    probe.init_callback(model)
    probe.on_training_start({}, {})
    for index, url in enumerate(urls):
        model.num_timesteps = index + 1
        done = [bool(dones[index])] if dones is not None else [False]
        probe.locals = {"infos": [_info(url)], "dones": np.array(done)}
        probe.on_step()


# -- RNG neutrality: the contract ----------------------------------------------------

def _rng_state():
    return (np.random.get_state()[1].copy(), random.getstate(), th.random.get_rng_state().clone())


def _same_rng(before, after) -> bool:
    return (np.array_equal(before[0], after[0])
            and before[1] == after[1]
            and th.equal(before[2], after[2]))


def test_the_probe_consumes_no_randomness():
    """A probe that drew a single number would desynchronise every later action."""
    model = _StubModel(_obs())
    probe = QGapProbe("order-4.html", "Place order", "Back", probe_every=1)
    before = _rng_state()
    _drive(probe, model, ["/index.html", "/order-4.html?q=1", "/order-4.html?q=1"] * 5)
    assert _same_rng(before, _rng_state()), "the Q probe advanced an RNG"
    assert probe.trace, "the probe recorded nothing, so the check proved nothing"


def test_the_probe_does_not_touch_gradients_or_module_state():
    model = _StubModel(_obs())
    training_modes = {name: m.training for name, m in model.q_net.named_modules()}
    probe = QGapProbe("order-4.html", "Place order", "Back", probe_every=1)
    _drive(probe, model, ["/order-4.html"] * 4)
    assert {name: m.training for name, m in model.q_net.named_modules()} == training_modes
    assert all(p.grad is None for p in model.q_net.parameters())


def test_the_tagging_buffer_draws_exactly_what_the_plain_buffer_draws():
    """The subclass records; it must not sample differently."""
    def _fill(buffer):
        rng = np.random.default_rng(3)
        for step in range(40):
            done = np.array([step % 10 == 9])
            info = [{"page": {"url": "/receipt.html" if step % 7 == 0 else "/index.html"},
                     "TimeLimit.truncated": bool(done[0])}]
            buffer.add(rng.normal(size=(1, 4)).astype(np.float32),
                       rng.normal(size=(1, 4)).astype(np.float32),
                       np.array([[step % 3]]), np.array([float(step)]), done, info)
        return buffer

    kwargs = dict(buffer_size=64,
                  observation_space=spaces.Box(-np.inf, np.inf, (4,), np.float32),
                  action_space=ACTION_SPACE, n_step=3, gamma=0.99)
    plain = _fill(PrioritizedNStepReplayBuffer(**kwargs))
    tagged = _fill(TaggedPrioritizedNStepReplayBuffer(**kwargs, tag_match="receipt.html"))

    np.random.seed(11)
    plain_sample = plain.sample(16)
    np.random.seed(11)
    tagged_sample = tagged.sample(16)

    assert np.array_equal(plain_sample.indices, tagged_sample.indices)
    assert th.equal(plain_sample.weights, tagged_sample.weights)
    assert th.equal(plain_sample.rewards, tagged_sample.rewards)
    assert np.array_equal(np.asarray(plain.observations), np.asarray(tagged.observations))
    assert np.array_equal(np.asarray(plain._tree._tree), np.asarray(tagged._tree._tree))


def test_the_tagging_buffer_counts_the_windows_that_touch_the_page():
    buffer = TaggedPrioritizedNStepReplayBuffer(
        buffer_size=64, observation_space=spaces.Box(-np.inf, np.inf, (4,), np.float32),
        action_space=ACTION_SPACE, n_step=3, gamma=0.99, tag_match="receipt.html",
    )
    obs = np.zeros((1, 4), dtype=np.float32)
    urls = ["/a", "/b", "/receipt.html", "/c", "/d", "/e"]
    for url in urls:
        buffer.add(obs, obs, np.array([[0]]), np.array([0.0]), np.array([False]),
                   [{"page": {"url": url}}])
    # n=3, so the windows starting at steps 0, 1 and 2 all contain the receipt step;
    # the window starting at step 3 is the first that does not.
    stats = buffer.stats()
    assert stats["tagged_stored"] == 3
    assert stats["buffer_transitions"] == 4
    assert stats["total_draws"] == 0


# -- the probe reads what it says it reads -------------------------------------------

def test_slots_are_resolved_by_visible_label():
    assert _slot_of(SPECS, "Place order") == 5
    assert _slot_of(SPECS, "Back") == 6
    assert _slot_of(SPECS, "No such control") is None


def test_the_initial_network_is_recorded_at_the_first_arrival():
    """The whole point of the instrument: what the *untrained* net thought at order-4.

    It cannot be recovered later, because by the first arrival the weights have moved.
    """
    model = _StubModel(_obs())
    initial_q = _net()(th.as_tensor(_obs()))[0]
    expected_gap = float(initial_q[5] - initial_q[6])

    probe = QGapProbe("order-4.html", "Place order", "Back", probe_every=1000)
    probe.init_callback(model)
    probe.on_training_start({}, {})
    # Training moves the weights before the agent ever reaches order-4.
    with th.no_grad():
        for parameter in model.q_net.parameters():
            parameter.add_(0.05)
    model.num_timesteps = 1
    probe.locals = {"infos": [_info("/order-4.html?quantity=42")], "dones": np.array([False])}
    probe.on_step()

    assert probe.captured_at_step == 1
    assert probe.initial is not None
    assert probe.initial["gap"] == pytest.approx(expected_gap, abs=1e-5)
    assert probe.initial["gap"] != pytest.approx(probe.trace[0]["gap"], abs=1e-6), (
        "the initial reading must differ from the live one, or the snapshot is not a snapshot"
    )


def test_the_observation_is_captured_once_and_never_replaced():
    model = _StubModel(_obs())
    probe = QGapProbe("order-4.html", "Place order", "Back", probe_every=1)
    _drive(probe, model, ["/order-4.html?a=1", "/order-4.html?a=2", "/order-4.html?a=3"])
    assert probe.captured_url.endswith("a=1")
    # Same fixed observation each time and an unchanging network, so the readings agree.
    assert len({reading["gap"] for reading in probe.trace}) == 1


def test_nothing_is_captured_on_a_done_step():
    """On a done step SB3 has already auto-reset, so `_last_obs` is the next episode's."""
    model = _StubModel(_obs())
    probe = QGapProbe("order-4.html", "Place order", "Back", probe_every=1)
    _drive(probe, model, ["/order-4.html"], dones=[True])
    assert probe.captured_at_step is None
    assert probe.arrivals == 1, "the arrival is still counted; only the capture is deferred"


def test_capture_waits_for_an_arrival_where_both_controls_exist():
    """A gap measured against a slot that is not there is a number about nothing."""
    model = _StubModel(_obs())
    probe = QGapProbe("order-4.html", "Place order", "Back", probe_every=1)
    probe.init_callback(model)
    probe.on_training_start({}, {})
    truncated = [spec for spec in SPECS if spec.element_id != "Place order"]
    model.num_timesteps = 1
    probe.locals = {"infos": [_info("/order-4.html", truncated)], "dones": np.array([False])}
    probe.on_step()
    assert probe.captured_at_step is None

    model.num_timesteps = 2
    probe.locals = {"infos": [_info("/order-4.html")], "dones": np.array([False])}
    probe.on_step()
    assert probe.captured_at_step == 2


def test_a_page_that_is_never_reached_leaves_an_empty_but_honest_record():
    model = _StubModel(_obs())
    probe = QGapProbe("order-4.html", "Place order", "Back", probe_every=1)
    _drive(probe, model, ["/index.html", "/catalog.html"])
    record = probe.to_dict()
    assert record["captured_at_step"] is None
    assert record["initial_network"] is None
    assert record["trace"] == []
    assert record["arrivals_total"] == 0


# -- the decisive neutrality proof: a full training loop with the browser removed -----
#
# The end-to-end check in `scripts/check_probe_rng_neutrality.py` compares two real
# 600-step browser runs, and it turns out it CANNOT settle this question: two
# *uninstrumented* runs of the ac_dqn configuration already differ from each other, in
# the 384-dim network block of a handful of observations, with the archive on or off. So
# a browser A-vs-C difference is not attributable to the instrumentation.
#
# Removing the browser removes the confound. A scripted env replays a fixed observation
# stream, so anything that differs between an instrumented and an uninstrumented run is
# the instrumentation — and every RNG consumer in the loop is still live: the masked
# warm-up sampler, epsilon-greedy, PER's stratified draws, the optimizer, and the target
# network update.

class _ScriptedEnv(gym.Env):
    """A deterministic stand-in for the browser, in the AC observation layout.

    Observations are a fixed function of the step index, so the stream does not depend
    on the agent's actions. That is the point: it isolates "does the instrumentation
    perturb the agent" from "does the environment answer differently this time".
    """

    metadata: dict = {}

    def __init__(self, episode_steps: int = 20) -> None:
        self.observation_space = SPACE
        self.action_space = ACTION_SPACE
        self.episode_steps = episode_steps
        self._t = 0
        self._episode = 0

    def _observation(self) -> np.ndarray:
        rng = np.random.default_rng(1000 * self._episode + self._t)
        obs = rng.normal(size=AC_OBS_DIM).astype(np.float32)
        obs[-MAX_ACTIONS:] = 0.0
        obs[-MAX_ACTIONS:][:N_VALID] = 1.0
        return obs

    def _info(self) -> dict:
        # order-4 on every third step, so the probe captures and then reads repeatedly.
        url = "/order-4.html" if self._t % 3 == 0 else "/order-2.html"
        return {"page": {"url": url}, "action_specs": list(SPECS), "state_key": f"s{self._t}"}

    def reset(self, *, seed=None, options=None):  # noqa: ANN001, ANN201
        self._episode += 1
        self._t = 0
        return self._observation(), self._info()

    def step(self, action):  # noqa: ANN001, ANN201
        self._t += 1
        reward = float((self._t % 5) - 2)
        truncated = self._t >= self.episode_steps
        return self._observation(), reward, False, truncated, self._info()


def _train_scripted(*, instrumented: bool, steps: int = 160):
    """One full `learn()` over the scripted stream, with and without the diagnostics."""
    venv = DummyVecEnv([lambda: Monitor(_ScriptedEnv())])
    # Exactly what `compare_agents.build_ac_agent` does when --tag-transitions is set.
    extra: dict = {}
    if instrumented:
        extra = {
            "replay_buffer_class": TaggedPrioritizedNStepReplayBuffer,
            "replay_buffer_kwargs": {"tag_match": "order-4.html"},
        }
    model = ActionConditionedDQN(
        "ACDQNPolicy", venv, learning_rate=1e-4, buffer_size=500, batch_size=32, gamma=0.99,
        exploration_fraction=0.6, exploration_initial_eps=1.0, exploration_final_eps=0.10,
        target_update_interval=50, learning_starts=20, train_freq=4, gradient_steps=2,
        n_step=3, seed=0, verbose=0,
        policy_kwargs={
            "features_extractor_class": ACStateExtractor,
            "features_extractor_kwargs": {"features_dim": FUSED_DIM + EPISODE_CONTEXT_DIM},
            "net_arch": [], "dueling": True,
        },
        **extra,
    )
    if instrumented:
        assert isinstance(model.replay_buffer, TaggedPrioritizedNStepReplayBuffer)
    probe = (QGapProbe("order-4.html", "Place order", "Back", probe_every=20)
             if instrumented else None)
    if instrumented:
        # Checkpoint saving is part of what gets switched on, so it is part of the test.
        with tempfile.TemporaryDirectory() as tmp:
            model.save(Path(tmp) / "init.zip")
            model.learn(total_timesteps=steps, progress_bar=False, callback=probe)
            model.save(Path(tmp) / "final.zip")
    else:
        model.learn(total_timesteps=steps, progress_bar=False, callback=None)
    venv.close()

    buffer = model.replay_buffer
    upper = buffer.buffer_size if buffer.full else buffer.pos
    fingerprint = {
        "stored": upper,
        "observations": np.asarray(buffer.observations[:upper]).copy(),
        "actions": np.asarray(buffer.actions[:upper]).copy(),
        "rewards": np.asarray(buffer.rewards[:upper]).copy(),
        "dones": np.asarray(buffer.dones[:upper]).copy(),
        "priorities": np.asarray(buffer._tree._tree).copy(),
    }
    for name, net in (("q", model.q_net), ("t", model.q_net_target)):
        for key, tensor in net.state_dict().items():
            fingerprint[f"{name}.{key}"] = tensor.detach().cpu().numpy().copy()
    return fingerprint, probe


def test_instrumented_training_is_bit_identical_to_uninstrumented_training():
    """The contract, end to end: probe + tagging buffer + checkpoints change nothing.

    Compares every stored transition, the whole PER priority tree, and every weight of
    both networks after a real `learn()` — so a single random number drawn by the
    instrumentation would desynchronise epsilon-greedy and show up here immediately.

    Verified to be able to fail, because a neutrality test that cannot detect a
    violation proves nothing: a probe subclass adding one `np.random.rand()` per step
    diverges on **30** of these fields, including `actions` and every fusion weight,
    while the real instrumentation diverges on none.
    """
    bare, _ = _train_scripted(instrumented=False)
    instrumented, probe = _train_scripted(instrumented=True)

    assert bare["stored"] == instrumented["stored"] > 0
    differing = [
        key for key in bare
        if key != "stored" and not np.array_equal(bare[key], instrumented[key])
    ]
    assert not differing, f"instrumentation changed training: {differing}"

    # And the instrument did something, or the comparison above proved nothing.
    assert probe is not None and probe.captured_at_step is not None
    assert len(probe.trace) >= 2


# -- checkpoints ---------------------------------------------------------------------

@pytest.mark.parametrize("dueling", [True, False])
def test_a_saved_checkpoint_reloads_as_the_same_network(dueling: bool):
    """`--save-checkpoints` is only useful if what comes back is what was saved.

    The dueling flag is the part that could silently not survive: SB3 rebuilds the
    policy from `policy_kwargs` and `_get_constructor_parameters`, so a flag that is not
    in either reloads at its default. An undueled run reloading as a dueling one would
    make any comparison drawn off checkpoints quietly wrong.
    """
    venv = DummyVecEnv([lambda: Monitor(_ScriptedEnv())])
    model = ActionConditionedDQN(
        "ACDQNPolicy", venv, learning_rate=1e-4, buffer_size=100, batch_size=8,
        learning_starts=10, seed=0, verbose=0, device="cpu",
        policy_kwargs={
            "features_extractor_class": ACStateExtractor,
            "features_extractor_kwargs": {"features_dim": FUSED_DIM + EPISODE_CONTEXT_DIM},
            "net_arch": [], "dueling": dueling,
        },
    )
    observation = th.as_tensor(_obs())
    with th.no_grad():
        before = model.q_net(observation).clone()

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "check.zip"
        model.save(path)
        venv.close()
        reloaded = ActionConditionedDQN.load(path, device="cpu")

    assert reloaded.policy.dueling is dueling
    assert hasattr(reloaded.q_net, "value") is dueling
    with th.no_grad():
        after = reloaded.q_net(observation)
    assert th.equal(before, after)
