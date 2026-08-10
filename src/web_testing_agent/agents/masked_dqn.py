"""DQN that can only choose actions the current page actually offers.

This is §9 item 1b, and it is the single highest-value RL change available. The action
space is rebuilt every step and is almost never full: on the toy site the unmasked
agent spent **87.5%** of its steps on no-ops (out-of-range indices resolve to NO_OP),
and even after learning validity it was still at 54%. A budget spent that way is not
exploration, it is nothing happening.

It also makes the run-5 comparison a fair fight for the first time. Masked *random*
beat the DQN 3/3 seeded bugs to 1/3 — but masked random samples only legal slots and
the flat head structurally could not, so that gap measured **action masking**, not
"RL versus random". Handing the same ability to the agent is what turns the comparison
into a statement about learning.

**Both halves have to be masked, and missing either one is silent.**

* *Greedy* selection reads Q-values, so invalid slots are pushed to -inf before argmax.
* *Exploratory* selection does not touch Q-values at all — SB3's `DQN.predict` calls
  `action_space.sample()`, uniform over all 100 slots. Masking only the Q-values would
  leave ε of every step (100% of them at the start of training, 5% at the end) still
  choosing uniformly from a mostly-invalid space. Since training *begins* at ε=1.0,
  that alone would have left early exploration exactly as wasteful as before.

The mask travels in the observation's trailing `MAX_ACTIONS` dims — see
`perception.vec_wrapper.encode_modalities` for why that is the only channel available.
"""

from __future__ import annotations

import numpy as np
import torch
from stable_baselines3 import DQN
from stable_baselines3.dqn.policies import DQNPolicy, QNetwork

from ..envs.types import MAX_ACTIONS
from ..utils.logging import get_logger

logger = get_logger(__name__)

# Large and finite rather than -inf. A true -inf survives argmax fine but produces NaN
# the moment it reaches a subtraction in the Huber loss (`-inf - -inf`), which would
# poison the network weights rather than merely bias the choice.
_MASKED_Q = -1e8


def split_mask(observations: torch.Tensor | np.ndarray):
    """Separate an observation batch into (features, mask)."""
    return observations[..., :-MAX_ACTIONS], observations[..., -MAX_ACTIONS:]


class MaskedQNetwork(QNetwork):
    """Q-network whose invalid actions cannot win an argmax."""

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        q_values = super().forward(obs)
        _, mask = split_mask(obs)
        # An all-zero mask would leave every action at -1e8 and make argmax arbitrary.
        # It should never happen (slot 0 is always NO_OP), but a page that fails to scan
        # would otherwise fail silently and unpredictably rather than loudly.
        empty = mask.sum(dim=-1, keepdim=True) == 0
        mask = torch.where(empty, torch.ones_like(mask), mask)
        return torch.where(mask > 0.5, q_values, torch.full_like(q_values, _MASKED_Q))


class MaskedDQNPolicy(DQNPolicy):
    def make_q_net(self) -> MaskedQNetwork:
        net_args = self._update_features_extractor(self.net_args, features_extractor=None)
        return MaskedQNetwork(**net_args).to(self.device)


class MaskedDQN(DQN):
    """DQN with masking applied to greedy *and* exploratory action selection.

    `policy` is forced to `MaskedDQNPolicy`: passing "MlpPolicy" here would silently
    give back an ordinary unmasked agent while every log line still said "masked".
    """

    def __init__(self, policy, env, **kwargs) -> None:
        if policy not in (MaskedDQNPolicy, "MaskedDQNPolicy"):
            logger.debug("MaskedDQN overrides policy={} with MaskedDQNPolicy", policy)
        super().__init__(MaskedDQNPolicy, env, **kwargs)

    def predict(
        self,
        observation,
        state=None,
        episode_start=None,
        deterministic: bool = False,
    ):
        """Epsilon-greedy over *valid* actions only.

        Mirrors `DQN.predict` exactly apart from where the random branch samples from.
        """
        if not deterministic and np.random.rand() < self.exploration_rate:
            obs = np.asarray(observation)
            if obs.ndim == 1:  # single, unbatched observation
                return np.array(self._sample_valid(obs[-MAX_ACTIONS:])), state
            actions = np.array([self._sample_valid(row[-MAX_ACTIONS:]) for row in obs])
            return actions, state
        return self.policy.predict(observation, state, episode_start, deterministic)

    def _sample_action(self, learning_starts: int, action_noise=None, n_envs: int = 1):
        """Mask the warm-up phase too — it never reaches `predict`.

        SB3 fills the replay buffer for `learning_starts` steps by calling
        `action_space.sample()` directly, so masking `predict` alone leaves that whole
        phase uniform over 100 slots. Measured on a 7-valid-action fixture: with only
        `predict` masked, every invalid action taken across a 200-step run came from
        here. At the pilot configuration that is 500 of 8,000 steps, and it is worse
        than the raw count suggests — those are the *first* transitions in the buffer,
        so the network's earliest gradients come almost entirely from no-ops.
        """
        if self.num_timesteps < learning_starts:
            obs = np.asarray(self._last_obs)
            actions = np.array([self._sample_valid(row[-MAX_ACTIONS:]) for row in obs])
            return actions, actions
        return super()._sample_action(learning_starts, action_noise, n_envs)

    def _sample_valid(self, mask: np.ndarray) -> int:
        valid = np.flatnonzero(mask > 0.5)
        if valid.size == 0:
            # Slot 0 is NO_OP on every page, so this is always a legal fallback.
            logger.warning("Action mask was empty; falling back to NO_OP")
            return 0
        return int(np.random.choice(valid))
