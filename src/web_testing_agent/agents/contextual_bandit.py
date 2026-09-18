"""A myopic baseline: learn `r_hat(s, a)`, pick the best valid action, never bootstrap.

**The question this exists to answer.** Four consecutive experiments have come back null —
the archive ablation, the Markov-context fix, and the trained-Q representation diagnostic.
What has never been tested is whether the *sequential* half of AC-DQN is buying anything at
all. This baseline removes exactly one thing: the bootstrapped future. It learns

    (s_t, a_t) -> r_t                       [this class]

against AC-DQN's

    (s_t, a_t) -> r_t + gamma^n * max_a' Q_target(s_{t+n}, a')

and keeps *everything else* identical — the same environment, the same 6973-dim
observation, the same 100-slot action interface and mask, the same hash encoder, the same
action features, the same reward function, the same epsilon schedule, the same archive and
`p_return`, the same episode budget and the same evaluation protocol. A difference between
the two arms is therefore attributable to the bootstrapped target, and to nothing else.

**What it does NOT isolate.** PER, the archive, the replay buffer and the action-conditioned
architecture are all *shared* with AC-DQN precisely so they cancel out. This experiment
cannot say anything about any of them individually; it separates myopic immediate-reward
prediction from bootstrapped sequential value learning, and that is all.

**Architecture, and why it is a reuse rather than a new network.**
`ActionConditionedQNetwork(dueling=False)` is already exactly the right shape: it fuses the
three perception modalities through `ACStateExtractor`, encodes each candidate action with
the same `action_encoder`, scores `f([g(s) ; h(phi(a))])` through the same MLP, and applies
the same mask. With `dueling=False` there is no value stream and no advantage centring —
the output is a single scalar per action and nothing about it is Bellman-shaped. Reusing it
keeps the two arms architecturally identical, which is the point of a controlled baseline;
writing a parallel network would introduce differences that have nothing to do with the
hypothesis.

**The scalar it emits is a predicted immediate reward, not a Q-value.** The class is
inherited for its architecture, not its semantics. `reward_net` and
`predicted_immediate_reward()` are the honest names, and the training target below is what
actually decides the meaning.

**Shared infrastructure, deliberately.** `PrioritizedNStepReplayBuffer` is reused with
`n_step` forced to **1**, so a stored transition's reward field is the single-step reward
the environment returned and no discounting or aggregation has touched it. PER still
prioritises by the size of the prediction error — that changes *which* examples are shown,
never *what* they are regressed onto. The learning target stays myopic.
"""

from __future__ import annotations

import numpy as np
import torch as th
from torch.nn import functional as F  # noqa: N812 - matches ac_dqn's import

from ..envs.types import MAX_ACTIONS
from ..utils.logging import get_logger
from .ac_dqn import AC_OBS_DIM, ActionConditionedDQN

logger = get_logger(__name__)

ALGORITHM = "contextual_bandit"


class ContextualBandit(ActionConditionedDQN):
    """Immediate-reward regression over the same state/action interface as AC-DQN.

    Inherits the rollout loop, the masked epsilon-greedy action selection (all three
    selection paths, including the warm-up that never reaches `predict`), the epsilon
    schedule and the checkpoint plumbing. Replaces the learning rule outright.

    Three things DQN provides are deliberately not used, and each is verified by a test
    rather than asserted here:

    * **no target network** — `q_net_target` is still constructed by SB3's policy, but
      `train()` never reads it and `_on_step()` does not polyak-update it.
    * **no next state** — `data.next_observations` is never referenced.
    * **no discount** — `self.gamma` is never referenced in the target. It is left at its
      default rather than set to 0.0 on purpose: a gamma of 0 would make an accidentally
      reintroduced Bellman target *look* correct, and the point is for that mistake to be
      caught by `regression_target` rather than hidden by a hyperparameter.
    """

    def __init__(self, policy, env, **kwargs) -> None:
        # `--n-step` stays on the CLI so both arms take the same flags, and is ignored
        # here: an n-step return is a bootstrapped quantity and there is nothing to
        # bootstrap. Forced to 1 so the buffer stores the raw one-step reward.
        requested = kwargs.pop("n_step", None)
        if requested not in (None, 1):
            logger.info(
                "ContextualBandit ignores n_step={} and uses 1: the target is the "
                "immediate reward, so there is no return to accumulate.", requested,
            )
        super().__init__(policy, env, n_step=1, **kwargs)
        # An *instance* attribute, deliberately: SB3 pickles `__dict__`, so this travels
        # with the checkpoint and is absent from one written by any other algorithm. As a
        # class attribute it would be present on every instance including a DQN loaded
        # through this class, and `load`'s guard below could never fire.
        self.algorithm = ALGORITHM

    # -- the model ----------------------------------------------------------------

    @property
    def reward_net(self):
        """The scoring network, under the name that describes what it predicts."""
        return self.q_net

    def predicted_immediate_reward(self, observation) -> np.ndarray:
        """`r_hat(s, a)` for every action slot, masked. The public, honestly-named API."""
        array = np.asarray(observation, dtype=np.float32)
        if array.ndim == 1:
            array = array[None, :]
        with th.no_grad():
            tensor = th.as_tensor(array, device=self.device)
            return self.reward_net(tensor).cpu().numpy()

    # -- the learning rule ---------------------------------------------------------

    @staticmethod
    def regression_target(data):
        """**The entire target: the observed immediate reward.**

        Isolated into one function so the claim "this does not bootstrap" is something a
        test can execute rather than something a reader has to audit. Everything a Bellman
        target would need — `data.next_observations`, `self.gamma`, `q_net_target`,
        `data.dones` — is absent by construction, not merely unused by accident.
        """
        return data.rewards

    def train(self, gradient_steps: int, batch_size: int = 100) -> None:
        """Regress the predicted immediate reward onto the observed one.

        Structurally parallel to `ActionConditionedDQN.train` so the diff between the two
        arms is legible: same optimizer handling, same Huber loss, same PER importance
        weights, same gradient clipping, same priority update. The only difference is that
        `target_q` is replaced by `regression_target(data)`.
        """
        from .replay import PrioritizedNStepReplayBuffer

        self.policy.set_training_mode(True)
        self._update_learning_rate(self.policy.optimizer)
        buffer = self.replay_buffer
        prioritized = isinstance(buffer, PrioritizedNStepReplayBuffer)
        if prioritized:
            buffer.set_beta(self._current_progress_remaining)

        losses, predictions, targets = [], [], []
        for _ in range(gradient_steps):
            data = buffer.sample(batch_size, env=self._vec_normalize_env)

            # No `th.no_grad()` block computing a bootstrapped value, because there is
            # nothing to compute: the target is already in the sampled batch.
            target = self.regression_target(data)
            predicted = th.gather(
                self.reward_net(data.observations), dim=1, index=data.actions.long()
            )

            error = target - predicted
            if prioritized:
                # Huber per element, then weighted, then averaged — reducing first would
                # discard the per-sample weights the correction exists to apply.
                elementwise = F.smooth_l1_loss(predicted, target, reduction="none")
                loss = (data.weights * elementwise).mean()
            else:
                loss = F.smooth_l1_loss(predicted, target)

            losses.append(loss.item())
            predictions.append(float(predicted.mean()))
            targets.append(float(target.mean()))

            self.policy.optimizer.zero_grad()
            loss.backward()
            th.nn.utils.clip_grad_norm_(self.policy.parameters(), self.max_grad_norm)
            self.policy.optimizer.step()

            if prioritized:
                # Prioritise by how wrong the *reward prediction* was. This changes which
                # transitions are replayed, never what they are regressed onto.
                buffer.update_priorities(data.indices, error.detach().cpu().numpy())

        self._n_updates += gradient_steps
        self.logger.record("train/n_updates", self._n_updates, exclude="tensorboard")
        self.logger.record("train/loss", float(np.mean(losses)) if losses else 0.0)
        self.logger.record("train/predicted_immediate_reward",
                           float(np.mean(predictions)) if predictions else 0.0)
        self.logger.record("train/observed_immediate_reward",
                           float(np.mean(targets)) if targets else 0.0)
        if prioritized:
            self.logger.record("train/per_beta", buffer.beta)

    def _on_step(self) -> None:
        """Advance the epsilon schedule. Deliberately does **not** update a target network.

        `DQN._on_step` polyak-copies `q_net` into `q_net_target` every
        `target_update_interval`. A myopic regression has no target network, so that half
        is omitted — leaving it in would spend compute maintaining a tensor nothing reads
        and would suggest to a reader that bootstrapping happens somewhere.
        """
        self._n_calls += 1
        self.exploration_rate = self.exploration_schedule(self._current_progress_remaining)
        self.logger.record("rollout/exploration_rate", self.exploration_rate)

    # -- checkpointing --------------------------------------------------------------

    def _excluded_save_params(self) -> list[str]:
        # `algorithm` is a class attribute and round-trips with the class itself; listing
        # it here would drop the identifier the loader validates.
        return super()._excluded_save_params()

    @classmethod
    def load(cls, path, env=None, device="auto", **kwargs):  # noqa: ANN001 - SB3 signature
        """Load, and refuse a checkpoint that is not one of ours or is the wrong width.

        A DQN checkpoint and a bandit checkpoint have identical tensor shapes — the
        difference is entirely in how they were trained — so nothing about the file itself
        would stop `ContextualBandit.load` from silently accepting an `ac_dqn` model and
        reporting myopic numbers for a bootstrapped policy.
        """
        from stable_baselines3.common.save_util import load_from_zip_file

        # The archive is inspected *before* delegating, because SB3's `load` calls
        # `cls.__init__` and only then applies the saved `__dict__`. `__init__` sets
        # `self.algorithm`, so by the time a loaded model exists the tag is present
        # whatever wrote the file — the discriminator has to come from the file itself.
        saved, _params, _tensors = load_from_zip_file(path, device=device,
                                                      print_system_info=False)
        if saved.get("algorithm") != ALGORITHM:
            raise ValueError(
                f"{path} was not saved by a ContextualBandit "
                f"(algorithm={saved.get('algorithm')!r}, expected {ALGORITHM!r})"
            )

        model = super().load(path, env=env, device=device, **kwargs)
        shape = model.observation_space.shape
        if shape != (AC_OBS_DIM,):
            raise ValueError(
                f"{path} carries observation shape {shape}, but this build expects "
                f"({AC_OBS_DIM},). The observation representation has changed since it "
                f"was written."
            )
        if int(model.action_space.n) != MAX_ACTIONS:
            raise ValueError(
                f"{path} carries {model.action_space.n} action slots, expected {MAX_ACTIONS}"
            )
        return model
