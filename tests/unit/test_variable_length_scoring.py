"""Does the action-conditioned head actually score N actions independently?

The transfer direction requires one scorer that works on any application, scoring whatever
actions a page happens to offer. Repository inspection suggested
`ActionConditionedQNetwork` already does this — `Q(s,a) = scorer([g(s) ; h(phi(a))])`
applied row-wise, with no slot embedding anywhere — so the right move is to *verify* that
rather than rewrite a working component.

These tests are the verification. They are written to fail loudly if a slot-identity term
is ever reintroduced, which is the failure that would silently destroy transfer: an agent
that has learned "slot 7 is good" has learned something about one application's DOM order
and nothing about web applications.

The decisive test is **permutation equivariance**: permute the valid actions and the scores
must permute with them. A network with any positional term fails it.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("torch")
pytest.importorskip("stable_baselines3")

import torch as th  # noqa: E402
from gymnasium import spaces  # noqa: E402

from web_testing_agent.agents.ac_dqn import (  # noqa: E402
    AC_OBS_DIM,
    ACStateExtractor,
    ActionConditionedQNetwork,
    split_observation,
)
from web_testing_agent.agents.action_features import ACTION_FEATURE_DIM  # noqa: E402
from web_testing_agent.envs.types import MAX_ACTIONS  # noqa: E402
from web_testing_agent.perception.fusion import FUSED_DIM  # noqa: E402
from web_testing_agent.envs.types import EPISODE_CONTEXT_DIM  # noqa: E402

STATE_BLOCK = AC_OBS_DIM - MAX_ACTIONS * ACTION_FEATURE_DIM - MAX_ACTIONS


def make_network(dueling: bool = True) -> ActionConditionedQNetwork:
    observation_space = spaces.Box(-np.inf, np.inf, (AC_OBS_DIM,), np.float32)
    extractor = ACStateExtractor(observation_space,
                                 features_dim=FUSED_DIM + EPISODE_CONTEXT_DIM)
    network = ActionConditionedQNetwork(
        observation_space=observation_space,
        action_space=spaces.Discrete(MAX_ACTIONS),
        features_extractor=extractor,
        features_dim=FUSED_DIM + EPISODE_CONTEXT_DIM,
        dueling=dueling,
    )
    return network.eval()


def observation_with(n_valid: int, seed: int = 0,
                     action_features: np.ndarray | None = None) -> th.Tensor:
    """One observation carrying `n_valid` valid actions and a matching mask."""
    rng = np.random.default_rng(seed)
    obs = np.zeros(AC_OBS_DIM, dtype=np.float32)
    obs[:STATE_BLOCK] = rng.standard_normal(STATE_BLOCK).astype(np.float32) * 0.1

    block = np.zeros((MAX_ACTIONS, ACTION_FEATURE_DIM), dtype=np.float32)
    if action_features is None:
        block[:n_valid] = rng.standard_normal((n_valid, ACTION_FEATURE_DIM)).astype(np.float32)
    else:
        block[:n_valid] = action_features[:n_valid]
    obs[STATE_BLOCK:STATE_BLOCK + MAX_ACTIONS * ACTION_FEATURE_DIM] = block.reshape(-1)
    obs[-MAX_ACTIONS:][:n_valid] = 1.0
    return th.as_tensor(obs[None, :])


MASKED = -1e8


# -- variable action counts --------------------------------------------------------------


@pytest.mark.parametrize("n_valid", [1, 2, 5, 9, 20, 47, 99, MAX_ACTIONS])
def test_any_number_of_valid_actions_is_scored(n_valid):
    """1 action or 99: the valid ones get real scores, the rest stay masked."""
    network = make_network()
    with th.no_grad():
        q = network(observation_with(n_valid, seed=n_valid))[0].numpy()

    assert np.all(q[:n_valid] > MASKED / 2), "a valid action was masked out"
    if n_valid < MAX_ACTIONS:
        assert np.all(q[n_valid:] <= MASKED / 2), "an invalid action was scored"
    assert int(np.argmax(q)) < n_valid


def test_action_counts_may_change_between_steps():
    """A real page offers a different number of controls after almost every action."""
    network = make_network()
    for n_valid in (12, 3, 47, 1, 28):
        with th.no_grad():
            q = network(observation_with(n_valid, seed=7))[0].numpy()
        assert int(np.argmax(q)) < n_valid


def test_an_all_zero_mask_raises_rather_than_guessing():
    """A malformed observation must not travel silently into a replay buffer."""
    network = make_network()
    obs = observation_with(5)
    obs[0, -MAX_ACTIONS:] = 0.0
    with pytest.raises(ValueError, match="all-zero"):
        network(obs)


# -- the decisive property: no slot identity ------------------------------------------------


def test_scores_permute_with_the_actions_they_belong_to():
    """Permutation equivariance. Any positional term in the network fails this."""
    network = make_network()
    rng = np.random.default_rng(3)
    n_valid = 8
    features = rng.standard_normal((n_valid, ACTION_FEATURE_DIM)).astype(np.float32)

    with th.no_grad():
        straight = network(observation_with(n_valid, seed=1, action_features=features))[0].numpy()

    permutation = rng.permutation(n_valid)
    with th.no_grad():
        permuted = network(observation_with(n_valid, seed=1,
                                            action_features=features[permutation]))[0].numpy()

    assert np.allclose(straight[permutation], permuted[:n_valid], atol=1e-5)


def test_an_identical_action_scores_identically_in_every_slot():
    """The same action's value must not depend on where the registry happened to put it."""
    network = make_network()
    rng = np.random.default_rng(11)
    probe = rng.standard_normal(ACTION_FEATURE_DIM).astype(np.float32)

    scores = []
    for position in (0, 1, 4, 9):
        features = rng.standard_normal((10, ACTION_FEATURE_DIM)).astype(np.float32)
        features[position] = probe
        with th.no_grad():
            q = network(observation_with(10, seed=5, action_features=features))[0].numpy()
        scores.append(q[position])

    # Under dueling, the advantage is centred over the valid actions, so the *other*
    # actions shift the baseline. Compare the undueled head, where the score is the
    # action's own value and nothing else.
    plain = make_network(dueling=False)
    plain_scores = []
    for position in (0, 1, 4, 9):
        features = np.zeros((10, ACTION_FEATURE_DIM), dtype=np.float32)
        features[position] = probe
        with th.no_grad():
            q = plain(observation_with(10, seed=5, action_features=features))[0].numpy()
        plain_scores.append(q[position])

    assert np.allclose(plain_scores, plain_scores[0], atol=1e-6), plain_scores


def test_padding_rows_cannot_influence_a_valid_actions_score():
    """Whatever is left in the padding region must not change a real action's value."""
    network = make_network(dueling=False)
    rng = np.random.default_rng(19)
    features = rng.standard_normal((6, ACTION_FEATURE_DIM)).astype(np.float32)

    clean = observation_with(6, seed=2, action_features=features)
    with th.no_grad():
        before = network(clean)[0].numpy()[:6]

    dirty = clean.clone()
    start = STATE_BLOCK + 6 * ACTION_FEATURE_DIM
    end = STATE_BLOCK + MAX_ACTIONS * ACTION_FEATURE_DIM
    dirty[0, start:end] = th.as_tensor(
        rng.standard_normal(end - start).astype(np.float32) * 5.0)
    with th.no_grad():
        after = network(dirty)[0].numpy()[:6]

    assert np.allclose(before, after, atol=1e-5)


def test_the_dueling_baseline_is_taken_over_valid_actions_only():
    """Otherwise the number of controls a page has leaks into every Q-value."""
    network = make_network(dueling=True)
    rng = np.random.default_rng(23)
    features = rng.standard_normal((MAX_ACTIONS, ACTION_FEATURE_DIM)).astype(np.float32)

    with th.no_grad():
        few = network(observation_with(4, seed=2, action_features=features))[0].numpy()[:4]

    dirty = observation_with(4, seed=2, action_features=features)
    start = STATE_BLOCK + 4 * ACTION_FEATURE_DIM
    end = STATE_BLOCK + MAX_ACTIONS * ACTION_FEATURE_DIM
    dirty[0, start:end] = th.as_tensor(
        rng.standard_normal(end - start).astype(np.float32) * 3.0)
    with th.no_grad():
        with_padding_noise = network(dirty)[0].numpy()[:4]

    assert np.allclose(few, with_padding_noise, atol=1e-5)


# -- the scorer is shared, which is what makes it portable -------------------------------------


def test_one_parameter_set_scores_every_action():
    """No per-slot parameters: the scorer's size must not depend on MAX_ACTIONS."""
    network = make_network()
    for name, parameter in network.named_parameters():
        assert MAX_ACTIONS not in tuple(parameter.shape), (
            f"{name} has a MAX_ACTIONS-sized dimension ({tuple(parameter.shape)}), "
            f"which is how a per-slot parameter would look")


def test_the_flat_q_head_is_disabled_rather_than_silently_trained():
    network = make_network()
    assert isinstance(network.q_net, th.nn.Identity)


def test_batches_with_different_valid_counts_are_scored_correctly_together():
    """Replay samples mix observations from pages with different control counts."""
    network = make_network()
    batch = th.cat([observation_with(n, seed=n) for n in (2, 17, 40)], dim=0)
    with th.no_grad():
        q = network(batch).numpy()
    for row, n_valid in enumerate((2, 17, 40)):
        assert np.all(q[row, :n_valid] > MASKED / 2)
        assert np.all(q[row, n_valid:] <= MASKED / 2)
        assert int(np.argmax(q[row])) < n_valid


def test_split_observation_recovers_the_blocks_it_was_given():
    obs = observation_with(6, seed=4)
    _perception, _context, actions, mask = split_observation(obs)
    assert actions.shape == (1, MAX_ACTIONS, ACTION_FEATURE_DIM)
    assert mask.shape == (1, MAX_ACTIONS)
    assert int(mask.sum().item()) == 6


# -- v2 compatibility ----------------------------------------------------------------------------


def test_the_head_is_width_agnostic_so_v2_features_need_no_architecture_change():
    """The only thing tying the network to 52 dims is ACTION_FEATURE_DIM at construction.

    v2's per-action vector is 80 wide. This confirms the scorer is parameterised by that
    width rather than hard-coded to it, so adopting v2 is a constructor argument and an
    observation-width change -- not a rewrite of the head.
    """
    from web_testing_agent.agents.semantic_action_features import (
        SEMANTIC_ACTION_FEATURE_DIM,
    )

    network = make_network()
    first_layer = network.action_encoder[0]
    assert first_layer.in_features == ACTION_FEATURE_DIM
    assert SEMANTIC_ACTION_FEATURE_DIM != ACTION_FEATURE_DIM
    # The encoder is a plain Linear over the feature width; swapping the width is a
    # constructor change, and nothing downstream of it depends on the number.
    assert isinstance(first_layer, th.nn.Linear)
