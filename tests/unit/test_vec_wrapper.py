"""Regression tests for the perception wrapper's VecEnv contract.

Both cases here are integration bugs that unit-testing the encoders in isolation could
never have caught — they only appear at an episode boundary inside SB3's auto-reset.
"""

from __future__ import annotations

import numpy as np
import pytest
from gymnasium import spaces

from web_testing_agent.envs.types import EPISODE_CONTEXT_DIM, MAX_ACTIONS, SCREENSHOT_SHAPE
from web_testing_agent.perception.fusion import NETWORK_DIM, STRUCTURAL_DIM, VISUAL_DIM
from web_testing_agent.perception.vec_wrapper import SemanticPerceptionWrapper, default_encoders

# +EPISODE_CONTEXT_DIM: history features ride alongside the three encoded modalities.
# +MAX_ACTIONS: the valid-action mask, which rides here because the observation is the
# only channel an SB3 policy can read (see agents.masked_dqn).
FEATURE_DIM = VISUAL_DIM + STRUCTURAL_DIM + NETWORK_DIM + EPISODE_CONTEXT_DIM + MAX_ACTIONS


def _frame(fill: int) -> np.ndarray:
    return np.full(SCREENSHOT_SHAPE, fill, dtype=np.uint8)


def _page(marker: str) -> dict:
    return {"url": f"http://h/{marker}", "html": f"<html><title>{marker}</title></html>", "network": "[]"}


class _FakeVecEnv:
    """Minimal stand-in mimicking DummyVecEnv's auto-reset behaviour on `done`."""

    def __init__(self) -> None:
        self.num_envs = 1
        self.observation_space = spaces.Dict(
            {"screenshot": spaces.Box(low=0, high=255, shape=SCREENSHOT_SHAPE, dtype=np.uint8)}
        )
        self.action_space = spaces.Discrete(100)
        self.reset_infos = [{"page": _page("start")}]
        self.render_mode = None
        self._next = None

    def reset(self):
        self.reset_infos = [{"page": _page("start")}]
        return {"screenshot": np.stack([_frame(0)])}

    def queue_done_step(self) -> None:
        """Next step ends the episode, exactly as DummyVecEnv reports it."""
        self._next = "done"

    def step_wait(self):
        if self._next == "done":
            self._next = None
            # DummyVecEnv: observation returned is the NEW episode's reset obs;
            # info holds the terminal observation and the TERMINAL page.
            self.reset_infos = [{"page": _page("fresh")}]
            infos = [{
                "page": _page("terminal"),
                "terminal_observation": {"screenshot": _frame(200)},
            }]
            return {"screenshot": np.stack([_frame(9)])}, np.array([1.0]), np.array([True]), infos
        return (
            {"screenshot": np.stack([_frame(5)])},
            np.array([0.0]),
            np.array([False]),
            [{"page": _page("mid")}],
        )

    def step_async(self, actions):  # pragma: no cover - unused
        return None

    def close(self):  # pragma: no cover - unused
        return None


@pytest.fixture()
def wrapped() -> SemanticPerceptionWrapper:
    return SemanticPerceptionWrapper(_FakeVecEnv(), encoders=default_encoders())


def test_wrapper_exposes_a_flat_box_of_the_right_width(wrapped):
    assert wrapped.observation_space.shape == (FEATURE_DIM,)
    assert wrapped.reset().shape == (1, FEATURE_DIM)


def test_missing_encoder_is_rejected_at_construction():
    with pytest.raises(ValueError, match="missing encoder"):
        SemanticPerceptionWrapper(_FakeVecEnv(), encoders={"visual": default_encoders()["visual"]})


def test_terminal_observation_is_encoded_not_left_raw(wrapped):
    """Regression: SB3 wrote the raw `{"screenshot": ...}` dict into a float buffer.

    Any VecEnvWrapper that changes the observation space must convert
    `info["terminal_observation"]` too, or the replay buffer receives a dict where it
    expects a feature vector and `_store_transition` raises `TypeError: float()
    argument must be ... not 'dict'` the first time any episode ends.
    """
    wrapped.reset()
    wrapped.venv.queue_done_step()
    _, _, dones, infos = wrapped.step_wait()

    assert dones[0]
    terminal = infos[0]["terminal_observation"]
    assert isinstance(terminal, np.ndarray)
    assert terminal.shape == (FEATURE_DIM,)
    assert terminal.dtype == np.float32


def test_post_reset_observation_is_paired_with_the_new_episodes_page(wrapped):
    """Regression: the auto-reset observation was encoded against the terminal page.

    On a done step the returned screenshot already belongs to the *next* episode while
    `info["page"]` still describes the one that just ended. Pairing them silently feeds
    the model a visual modality from one state and a structural modality from another.
    """
    wrapped.reset()
    wrapped.venv.queue_done_step()
    encoded, _, _, infos = wrapped.step_wait()

    structural_slice = slice(VISUAL_DIM, VISUAL_DIM + STRUCTURAL_DIM)
    expected = wrapped.encoders["structural"].encode_batch(
        [_structural_input(_page("fresh"))]
    )[0]
    wrong = wrapped.encoders["structural"].encode_batch(
        [_structural_input(_page("terminal"))]
    )[0]

    assert np.allclose(encoded[0, structural_slice], expected)
    assert not np.allclose(encoded[0, structural_slice], wrong)


def _structural_input(page: dict) -> str:
    from web_testing_agent.perception.normalization import preprocess_for_structural_encoder

    return preprocess_for_structural_encoder(page["html"])


# --- action mask ------------------------------------------------------------------


def test_the_mask_marks_exactly_the_valid_leading_slots():
    """Valid actions are always the leading slots: `_resolve_action` accepts
    0 <= a < len(action_specs) and treats everything above as NO_OP, so a count fully
    describes the mask and no per-slot vector has to cross the process boundary."""
    from web_testing_agent.perception.vec_wrapper import action_mask

    mask = action_mask(7)
    assert mask.shape == (MAX_ACTIONS,)
    assert mask[:7].all() and not mask[7:].any()


def test_a_missing_count_yields_an_all_valid_mask():
    """Degrading to all-valid reproduces the old unmasked behaviour; degrading to
    all-invalid would leave a policy with no legal action at all."""
    from web_testing_agent.perception.vec_wrapper import action_mask

    assert action_mask(None).all()


def test_the_mask_is_clamped_to_the_action_space():
    from web_testing_agent.perception.vec_wrapper import action_mask

    assert action_mask(10_000).all()
    assert not action_mask(0).any()
    assert not action_mask(-5).any()


def test_the_mask_is_the_trailing_slice_of_the_observation():
    """agents.masked_dqn slices it as a fixed [..., -MAX_ACTIONS:], so its position
    must not depend on how the encoders upstream are configured."""
    from web_testing_agent.perception.vec_wrapper import encode_modalities

    encoded = encode_modalities(
        default_encoders(), [np.zeros(SCREENSHOT_SHAPE, dtype=np.uint8)],
        [{"html": "<html></html>", "network": "[]"}], [None], [4],
    )
    assert encoded.shape == (1, FEATURE_DIM)
    assert encoded[0, -MAX_ACTIONS:][:4].all()
    assert not encoded[0, -MAX_ACTIONS:][4:].any()
