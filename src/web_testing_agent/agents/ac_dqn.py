"""Action-conditioned Double DQN — the architecture that replaces the flat 100-slot head.

**What was wrong with the head this replaces.** `Linear(262 -> 100)` scored a fixed slot
per output, but the action space is rebuilt every step, so slot 47 means "click login" on
one page and "type a boundary value" on the next. Two consequences were measured rather
than argued:

1. The head could only ever *memorise* which slot was good on which page, which needs
   many visits per page and is exactly what a 4,000-step budget cannot afford.
2. Nothing it learned could transfer to an application it had not seen — and the product
   points this agent at a new application every time. A representation that cannot
   transfer is not a representation of the task.

**The replacement.** `Q(s, phi(a))` is scored once per action that actually exists, where
`phi(a)` describes the action itself — its type, its visible label, whether it is
navigational, whether it was *newly revealed* by the previous step, how often it has been
taken at this state. See `action_features`. Three things follow:

* The dynamic action space is handled natively. There are no unused slots to mask,
  because only real actions are scored. Masking remains, but now it only zeroes the
  zero-padded rows rather than doing the architectural work.
* What the network learns is stated in terms that hold on any site: "a newly-revealed
  CLICK labelled 'Continue' on a page whose form just became filled is valuable".
* The output layer has no per-page parameters at all, so the same weights are meaningful
  on an application never seen during training.

**Double DQN, not vanilla.** SB3's `DQN.train` computes the target as
`max_a' Q_target(s', a')`, which is the overestimating form. With ~100 candidate actions
per state the max is taken over a wide set and the overestimation is correspondingly
large, so action selection is done with the online network and evaluation with the
target network (van Hasselt et al., 2016).

**The masking lesson from `masked_dqn` is preserved.** All three selection paths are
masked — greedy, epsilon-greedy, and the warm-up phase that never reaches `predict` —
because missing any one of them is silent. Invalid actions are pushed to a large finite
sentinel rather than `-inf`, because `-inf` survives an argmax but produces NaN the first
time it reaches a subtraction in the Huber loss.
"""

from __future__ import annotations

import numpy as np
import torch as th
from gymnasium import spaces
from stable_baselines3 import DQN
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.utils import get_linear_fn, polyak_update
from stable_baselines3.dqn.policies import DQNPolicy, QNetwork
from torch import nn
from torch.nn import functional as F

from ..envs.types import EPISODE_CONTEXT_DIM, MAX_ACTIONS
from ..perception.fusion import FUSED_DIM, NETWORK_DIM, STRUCTURAL_DIM, VISUAL_DIM, FusionMLP
from ..utils.logging import get_logger
from .action_features import ACTION_FEATURE_DIM
from .replay import PrioritizedNStepReplayBuffer

logger = get_logger(__name__)

# Finite, not -inf. See the module docstring.
_MASKED_Q = -1e8

STATE_BLOCK = VISUAL_DIM + STRUCTURAL_DIM + NETWORK_DIM + EPISODE_CONTEXT_DIM
ACTION_BLOCK = MAX_ACTIONS * ACTION_FEATURE_DIM
AC_OBS_DIM = STATE_BLOCK + ACTION_BLOCK + MAX_ACTIONS


def split_observation(obs: th.Tensor):
    """`(perception, episode_context, action_features, mask)` from one observation batch."""
    perception = obs[..., : VISUAL_DIM + STRUCTURAL_DIM + NETWORK_DIM]
    context = obs[..., VISUAL_DIM + STRUCTURAL_DIM + NETWORK_DIM : STATE_BLOCK]
    actions = obs[..., STATE_BLOCK : STATE_BLOCK + ACTION_BLOCK]
    mask = obs[..., -MAX_ACTIONS:]
    return perception, context, actions.reshape(*obs.shape[:-1], MAX_ACTIONS, ACTION_FEATURE_DIM), mask


class ACStateExtractor(BaseFeaturesExtractor):
    """Fuses the three perception modalities and appends the episode context.

    Identical in intent to `FusionFeaturesExtractor` — the fusion MLP is still the only
    trainable part of the perception stack, and the episode-context scalars are still
    concatenated *after* fusion so a 1664->256 bottleneck cannot learn to discard them.
    The difference is only that the action block passes through untouched, because the
    Q-network needs it in its raw per-action form.
    """

    def __init__(self, observation_space: spaces.Box, features_dim: int = FUSED_DIM + EPISODE_CONTEXT_DIM) -> None:
        if observation_space.shape != (AC_OBS_DIM,):
            raise ValueError(
                f"ACStateExtractor expects Box({AC_OBS_DIM},) from SemanticPerceptionWrapper "
                f"with an ActionFeatureExtractor attached, got {observation_space.shape}"
            )
        super().__init__(observation_space, features_dim)
        self.fusion = FusionMLP(output_dim=features_dim - EPISODE_CONTEXT_DIM)

    def forward(self, observations: th.Tensor) -> th.Tensor:
        perception, context, _actions, _mask = split_observation(observations)
        visual, structural, network = th.split(
            perception, (VISUAL_DIM, STRUCTURAL_DIM, NETWORK_DIM), dim=-1
        )
        return th.cat([self.fusion(visual, structural, network), context], dim=-1)


class ActionConditionedQNetwork(QNetwork):
    """Scores every available action against the same state representation.

    `Q(s, a) = f([g(s) ; h(phi(a))])`, evaluated for all MAX_ACTIONS rows in one batched
    pass rather than looped, so the cost is one forward over `(B * MAX_ACTIONS, ...)` —
    about 2 ms, against a browser step of roughly 340 ms.
    """

    def __init__(self, *args, action_embed_dim: int = 64, hidden_dim: int = 128,
                 dueling: bool = True, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        state_dim = self.features_dim
        self.dueling = dueling
        self.action_encoder = nn.Sequential(
            nn.Linear(ACTION_FEATURE_DIM, action_embed_dim),
            nn.ReLU(),
        )
        # In dueling mode this head produces the *advantage*, not Q. Same shape either
        # way, so the two configurations differ only in how the output is combined.
        self.scorer = nn.Sequential(
            nn.Linear(state_dim + action_embed_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )
        if dueling:
            # State value, from the state alone — it must not see the action, or the
            # decomposition is not a decomposition.
            self.value = nn.Sequential(
                nn.Linear(state_dim, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, 1),
            )
        # `QNetwork.__init__` built a Linear(features_dim -> action_dim) head for the
        # flat architecture. It is never called; replacing it with Identity keeps it out
        # of the optimizer's parameter list rather than training a dead tensor.
        self.q_net = nn.Identity()

    def forward(self, obs: th.Tensor) -> th.Tensor:
        state = self.extract_features(obs, self.features_extractor)  # (B, state_dim)
        _perception, _context, action_features, mask = split_observation(obs)

        encoded = self.action_encoder(action_features)               # (B, A, embed)
        expanded = state.unsqueeze(1).expand(-1, MAX_ACTIONS, -1)    # (B, A, state_dim)
        scored = self.scorer(th.cat([expanded, encoded], dim=-1)).squeeze(-1)  # (B, A)

        # An all-zero mask would leave every action at the sentinel and make argmax
        # arbitrary. Slot 0 is NO_OP on every page so this should never happen, but a
        # page that failed to scan would otherwise fail silently instead of loudly.
        empty = mask.sum(dim=-1, keepdim=True) == 0
        mask = th.where(empty, th.ones_like(mask), mask)
        valid = mask > 0.5

        if self.dueling:
            # Q(s,a) = V(s) + A(s,a) - mean_a' A(s,a')   (Wang et al., 2016)
            #
            # **Why this is the change being tested.** Measured over training, the
            # within-state Q spread fell from 24% of |Q| to 5% while the mean magnitude
            # grew 23x: the action-level signal was not destroyed, it was outgrown by an
            # accumulating negative baseline, and the network converged on Q ~= V(s)
            # with a vestigial action term. Mean Q varied from -1.36 to -4.70 *between*
            # states while varying ~0.1 *within* one. Subtracting the mean advantage
            # forces the advantage stream to carry only within-state differences, so the
            # state's overall value can inflate without swamping the comparison between
            # the actions available in it.
            #
            # The mean is taken over **valid actions only**. Padding rows are zeros, and
            # including them would drag the baseline toward zero by a factor that varies
            # with how many controls a page happens to have — reintroducing exactly the
            # page-identity leak that `FusionFeaturesExtractor` discards the mask to
            # avoid.
            count = valid.sum(dim=-1, keepdim=True).clamp(min=1)
            advantage_sum = th.where(valid, scored, th.zeros_like(scored)).sum(dim=-1, keepdim=True)
            centred = scored - advantage_sum / count
            q_values = self.value(state) + centred
        else:
            q_values = scored

        return th.where(valid, q_values, th.full_like(q_values, _MASKED_Q))


class ACDQNPolicy(DQNPolicy):
    def make_q_net(self) -> ActionConditionedQNetwork:
        net_args = self._update_features_extractor(self.net_args, features_extractor=None)
        return ActionConditionedQNetwork(**net_args).to(self.device)


class ActionConditionedDQN(DQN):
    """DQN with an action-conditioned head, Double-DQN targets, PER and n-step returns.

    `policy` is forced to `ACDQNPolicy`: accepting "MlpPolicy" here would silently give
    back an ordinary flat agent while every log line still said action-conditioned.
    """

    # Read by `evaluation.rollout.TrainedPolicy` so its residual exploration samples
    # valid slots only. An attribute rather than an isinstance check, so `evaluation`
    # does not have to import this module (and therefore SB3).
    masks_actions = True

    def __init__(self, policy, env, *, n_step: int = 3, per_alpha: float = 0.6,
                 per_beta0: float = 0.4, **kwargs) -> None:
        if policy not in (ACDQNPolicy, "ACDQNPolicy"):
            logger.debug("ActionConditionedDQN overrides policy={} with ACDQNPolicy", policy)
        kwargs.setdefault("replay_buffer_class", PrioritizedNStepReplayBuffer)
        replay_kwargs = dict(kwargs.pop("replay_buffer_kwargs", None) or {})
        replay_kwargs.setdefault("n_step", n_step)
        replay_kwargs.setdefault("gamma", kwargs.get("gamma", 0.99))
        replay_kwargs.setdefault("alpha", per_alpha)
        replay_kwargs.setdefault("beta0", per_beta0)
        kwargs["replay_buffer_kwargs"] = replay_kwargs
        self.n_step = n_step
        super().__init__(ACDQNPolicy, env, **kwargs)

    # -- action selection ---------------------------------------------------------

    def predict(self, observation, state=None, episode_start=None, deterministic: bool = False):
        """Epsilon-greedy over *valid* actions only.

        Mirrors `DQN.predict` apart from where the random branch samples. Masking the
        Q-values alone would leave epsilon of every step uniform over all MAX_ACTIONS
        slots, and training begins at epsilon=1.0.
        """
        if not deterministic and np.random.rand() < self.exploration_rate:
            obs = np.asarray(observation)
            if obs.ndim == 1:
                return np.array(self._sample_valid(obs[-MAX_ACTIONS:])), state
            return np.array([self._sample_valid(row[-MAX_ACTIONS:]) for row in obs]), state
        return self.policy.predict(observation, state, episode_start, deterministic)

    def _sample_action(self, learning_starts: int, action_noise=None, n_envs: int = 1):
        """Mask the warm-up too — it calls `action_space.sample()` and never reaches `predict`.

        Measured on the flat architecture: with only `predict` masked, *every* invalid
        action in a 200-step run came from here, and they are the first transitions in
        the buffer, so the earliest gradients came almost entirely from no-ops.
        """
        if self.num_timesteps < learning_starts:
            obs = np.asarray(self._last_obs)
            actions = np.array([self._sample_valid(row[-MAX_ACTIONS:]) for row in obs])
            return actions, actions
        return super()._sample_action(learning_starts, action_noise, n_envs)

    def _sample_valid(self, mask: np.ndarray) -> int:
        valid = np.flatnonzero(mask > 0.5)
        if valid.size == 0:
            logger.warning("Action mask was empty; falling back to NO_OP")
            return 0
        return int(np.random.choice(valid))

    # -- learning -----------------------------------------------------------------

    def train(self, gradient_steps: int, batch_size: int = 100) -> None:
        """Double-DQN targets, n-step discounting, and PER importance weights.

        Replaces `DQN.train` rather than extending it, because all three changes land in
        the same six lines of target computation and a partial override would be harder
        to read than the whole thing.
        """
        self.policy.set_training_mode(True)
        self._update_learning_rate(self.policy.optimizer)
        buffer = self.replay_buffer
        prioritized = isinstance(buffer, PrioritizedNStepReplayBuffer)
        if prioritized:
            buffer.set_beta(self._current_progress_remaining)
        # Bootstrapping happens n steps later, so the discount applied to the target is
        # gamma^n rather than gamma. Using gamma here would systematically over-value
        # every bootstrapped state.
        discount = self.gamma ** self.n_step

        losses = []
        for _ in range(gradient_steps):
            data = buffer.sample(batch_size, env=self._vec_normalize_env)

            with th.no_grad():
                # Double DQN: the ONLINE net chooses, the TARGET net evaluates. Both are
                # masked, so an invalid action can never be chosen as the argmax and
                # then evaluated at its sentinel value.
                next_online = self.q_net(data.next_observations)
                next_actions = next_online.argmax(dim=1, keepdim=True)
                next_target = self.q_net_target(data.next_observations)
                next_q = th.gather(next_target, dim=1, index=next_actions)
                target_q = data.rewards + (1 - data.dones) * discount * next_q

            current_q = th.gather(self.q_net(data.observations), dim=1, index=data.actions.long())

            td_error = target_q - current_q
            if prioritized:
                # Huber per element, then weighted, then averaged. Reducing first would
                # discard the per-sample weights the correction exists to apply.
                elementwise = F.smooth_l1_loss(current_q, target_q, reduction="none")
                loss = (data.weights * elementwise).mean()
            else:
                loss = F.smooth_l1_loss(current_q, target_q)
            losses.append(loss.item())

            self.policy.optimizer.zero_grad()
            loss.backward()
            th.nn.utils.clip_grad_norm_(self.policy.parameters(), self.max_grad_norm)
            self.policy.optimizer.step()

            if prioritized:
                buffer.update_priorities(data.indices, td_error.detach().cpu().numpy())

        self._n_updates += gradient_steps
        self.logger.record("train/n_updates", self._n_updates, exclude="tensorboard")
        self.logger.record("train/loss", float(np.mean(losses)) if losses else 0.0)
        if prioritized:
            self.logger.record("train/per_beta", buffer.beta)
