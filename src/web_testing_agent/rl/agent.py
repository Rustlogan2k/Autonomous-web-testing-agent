"""The learning tester: an action-conditioned Q-agent that learns while it explores.

    STATE        `features.FeatureExtractor`: how the page looks as a workspace (control
                 mix, form fill, what has been tried) plus the env's episode context.
    ACTION       whichever concrete UI actions exist on the page right now, each described
                 by meaning (semantic label), kind, and the agent's own history with it.
    REWARD       `reward.RewardComposer`: exploration reward from the env plus the dynamic
                 LLM progress potential as shaping. Never a seeded-fault label.
    POLICY       epsilon-greedy over `Q(s, a)`, valid actions only.
    LEARNING     Double DQN with n-step returns, prioritized replay and a target network
                 -- or, with `algorithm="bandit"`, myopic regression onto the immediate
                 reward, which is the controlled "no bootstrapping" arm.
    EXPLORATION  epsilon, plus the count-decayed novelty inside the reward, plus the
                 tried/untried features that let the value function itself prefer what
                 has not been attempted.
    TRANSFER     pretrain on several applications; on a new one, freeze what was learned
                 and train a zero-initialised residual head (`network.TransferQNetwork`).

**It learns online, inside the budget.** There is no separate training phase on the
application under test. Every interaction the agent spends there is counted against the
same budget a search baseline gets, and whatever it learns about that application it
learns from those interactions. That is what makes "pretrained versus from scratch" a fair
question: both arms get the same steps on the target, and differ only in what they knew
before the first one.

**One class, three uses**, selected by configuration rather than by subclass so the arms
of the experiment cannot drift apart in anything but the thing being varied:

* from scratch -- random initialisation, `learn=True`;
* pretrained and adapting -- weights loaded, residual head attached, `learn=True`;
* zero-shot -- weights loaded, `learn=False`. It still updates its visit counts, because
  those are observations about the run, not learned parameters.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import torch as th
from torch.nn import functional as F  # noqa: N812

from ..envs.types import ActionSpec
from ..utils.logging import get_logger
from .features import ACTION_DIM, FEATURE_VERSION, STATE_DIM, Encoded, FeatureExtractor
from .network import TransferQNetwork
from .replay import PrioritizedReplay, Transition, collate
from .reward import RewardComposer

logger = get_logger(__name__)

CHECKPOINT_VERSION = 1


@dataclass(frozen=True)
class AgentConfig:
    #: "dqn" bootstraps; "bandit" regresses onto the immediate reward and nothing else.
    algorithm: str = "dqn"
    gamma: float = 0.95
    n_step: int = 3
    learning_rate: float = 3e-4
    batch_size: int = 64
    buffer_size: int = 30_000
    #: Interactions collected before the first gradient step.
    learning_starts: int = 100
    gradient_steps: int = 1
    #: Gradient steps between hard copies of the online network into the target.
    target_update: int = 200
    max_grad_norm: float = 10.0
    dueling: bool = True
    per_alpha: float = 0.6
    per_beta0: float = 0.4
    epsilon_start: float = 1.0
    epsilon_end: float = 0.10
    #: Interactions over which epsilon falls linearly from start to end.
    epsilon_decay_steps: int = 1200
    #: Interactions the run is expected to last; anneals the PER importance exponent.
    total_steps: int = 2000
    learn: bool = True
    seed: int = 0

    def __post_init__(self) -> None:
        if self.algorithm not in ("dqn", "bandit"):
            raise ValueError(f"algorithm must be 'dqn' or 'bandit', got {self.algorithm!r}")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Pending:
    state: np.ndarray
    actions: np.ndarray
    action: int
    reward: float
    next_state: np.ndarray
    next_actions: np.ndarray


@dataclass
class AgentStats:
    steps: int = 0
    random_actions: int = 0
    gradient_steps: int = 0
    target_updates: int = 0
    losses: list[float] = field(default_factory=list)
    mean_q: list[float] = field(default_factory=list)
    learn_seconds: float = 0.0

    def to_dict(self) -> dict:
        def tail_mean(values: list[float]) -> float | None:
            return round(float(np.mean(values[-200:])), 5) if values else None
        return {
            "steps": self.steps,
            "random_actions": self.random_actions,
            "gradient_steps": self.gradient_steps,
            "target_updates": self.target_updates,
            "recent_loss": tail_mean(self.losses),
            "recent_mean_q": tail_mean(self.mean_q),
            "learn_seconds": round(self.learn_seconds, 2),
        }


class QAgent:
    """Implements the hunt's policy protocol, including the optional learning hook."""

    def __init__(self, config: AgentConfig | None = None, *,
                 rewarder: RewardComposer | None = None,
                 network: TransferQNetwork | None = None, labels=None) -> None:
        self.config = config or AgentConfig()
        th.manual_seed(self.config.seed)
        self._rng = np.random.default_rng(self.config.seed)
        self.rewarder = rewarder or RewardComposer()
        self.network = network or TransferQNetwork(
            STATE_DIM, ACTION_DIM, dueling=self.config.dueling)
        self.target = TransferQNetwork(
            STATE_DIM, ACTION_DIM, dueling=self.config.dueling,
            residual=self.network.has_residual)
        self.target.load_state_dict(self.network.state_dict())
        self.target.requires_grad_(False)
        self.optimizer = self._make_optimizer()
        self.replay = PrioritizedReplay(
            self.config.buffer_size, alpha=self.config.per_alpha,
            beta0=self.config.per_beta0, seed=self.config.seed)
        self.stats = AgentStats()

        if labels is None:
            from .label_store import StoredLabelEncoder

            labels = StoredLabelEncoder()
        self._labels = labels
        self._anchor = None
        # One extractor per application: visit counts are a memory of *that* application,
        # and "Save" having been pressed on one site says nothing about another.
        self._extractors: dict[str, FeatureExtractor] = {}
        self._app = ""
        self.features = self._extractor_for("")

        self._pending: deque[_Pending] = deque()
        self._current: Encoded | None = None
        self._current_info_id = 0

    # -- construction helpers ------------------------------------------------------------

    def _make_optimizer(self) -> th.optim.Optimizer | None:
        parameters = self.network.trainable_parameters()
        if not parameters:
            return None
        return th.optim.Adam(parameters, lr=self.config.learning_rate)

    def _extractor_for(self, app: str) -> FeatureExtractor:
        extractor = self._extractors.get(app)
        if extractor is None:
            extractor = FeatureExtractor(labels=self._labels, anchor=self._anchor)
            self._anchor = extractor.anchor  # share the encoded anchors across extractors
            self._extractors[app] = extractor
        return extractor

    def set_application(self, app: str, context: str = "") -> None:
        """Tell the agent which application the next episodes belong to."""
        self._flush_pending(terminal=False)
        self._app = app
        self.features = self._extractor_for(app)
        self.rewarder.set_application(context)
        self._current = None

    def enable_adaptation(self, residual_scale: float = 0.5) -> None:
        """Freeze the pretrained scorer and attach a zero-output residual head to train."""
        self.network.add_residual(residual_scale)
        self.target = TransferQNetwork(
            STATE_DIM, ACTION_DIM, dueling=self.config.dueling, residual=True,
            residual_scale=residual_scale)
        self.target.load_state_dict(self.network.state_dict())
        self.target.requires_grad_(False)
        self.optimizer = self._make_optimizer()

    # -- the hunt's policy protocol ---------------------------------------------------------

    def reset(self) -> None:
        """Part of the protocol; episode setup happens in `begin_episode`, which has `info`."""

    def begin_episode(self, info: dict) -> None:  # noqa: ARG002 - protocol signature
        self._flush_pending(terminal=False)
        self.features.begin_episode()
        self._current = None

    @property
    def epsilon(self) -> float:
        config = self.config
        if not config.learn:
            return config.epsilon_end
        fraction = min(self.stats.steps / max(config.epsilon_decay_steps, 1), 1.0)
        return config.epsilon_start + fraction * (config.epsilon_end - config.epsilon_start)

    def q_values(self, encoded: Encoded) -> np.ndarray:
        """`Q(s, a)` for every available action, as a 1-D array."""
        with th.no_grad():
            state = th.as_tensor(encoded.state).unsqueeze(0)
            actions = th.as_tensor(encoded.actions).unsqueeze(0)
            mask = th.ones((1, encoded.count), dtype=th.bool)
            return self.network(state, actions, mask).squeeze(0).numpy()

    def act(self, observation: dict, info: dict) -> int:  # noqa: ARG002 - reads no pixels
        if self._current is None or self._current_info_id != id(info):
            self._current = self.features.encode(info)
            self._current_info_id = id(info)
        encoded = self._current
        if encoded.count == 0:
            return 0
        if self._rng.random() < self.epsilon:
            self.stats.random_actions += 1
            return int(self._rng.integers(0, encoded.count))
        values = self.q_values(encoded)
        self.stats.mean_q.append(float(values.mean()))
        return int(np.argmax(values))

    def observe_transition(self, before: dict, action: int, reward: float, after: dict,
                           terminated: bool, done: bool) -> None:
        """Record what the step did, compose its reward, store it, and learn from replay."""
        previous = self._current
        executed: ActionSpec | None = after.get("action_spec")
        self.features.record(before, executed, after)
        following = self.features.encode(after)
        self._current, self._current_info_id = following, id(after)
        self.stats.steps += 1

        total, _components = self.rewarder.compute(
            before, after, reward, extra={"app": self._app, "action_type":
                                          executed.action_type.value if executed else ""})
        if previous is None or previous.count == 0 or not self.config.learn:
            return

        self._pending.append(_Pending(
            state=previous.state, actions=previous.compact_actions(),
            action=min(int(action), previous.count - 1), reward=total,
            next_state=following.state, next_actions=following.compact_actions()))
        horizon = 1 if self.config.algorithm == "bandit" else self.config.n_step
        while len(self._pending) >= horizon:
            self._emit(horizon, terminal=False)
        if done or terminated:
            # Episodes are a fixed number of steps and the observation carries the step
            # fraction, so the agent can see the end coming: the final transitions are
            # terminal, not bootstrapped from a state it will never act in.
            self._flush_pending(terminal=True)

        if len(self.replay) >= max(self.config.learning_starts, self.config.batch_size):
            started = time.monotonic()
            for _ in range(self.config.gradient_steps):
                self._gradient_step()
            self.stats.learn_seconds += time.monotonic() - started

    # -- replay assembly --------------------------------------------------------------------

    def _emit(self, steps: int, *, terminal: bool) -> None:
        gamma = 1.0 if self.config.algorithm == "bandit" else self.config.gamma
        window = [self._pending[i] for i in range(steps)]
        accumulated = sum((gamma ** i) * item.reward for i, item in enumerate(window))
        first, last = window[0], window[-1]
        self.replay.add(Transition(
            state=first.state, actions=first.actions, action=first.action,
            reward=float(accumulated), next_state=last.next_state,
            next_actions=last.next_actions, terminal=terminal,
            discount=float(gamma ** steps), app=self._app))
        self._pending.popleft()

    def _flush_pending(self, *, terminal: bool) -> None:
        while self._pending:
            self._emit(len(self._pending), terminal=terminal)

    # -- learning -----------------------------------------------------------------------------

    def _gradient_step(self) -> None:
        if self.optimizer is None:
            return
        config = self.config
        progress = min(self.stats.steps / max(config.total_steps, 1), 1.0)
        batch, indices, weights = self.replay.sample(config.batch_size, progress)
        (states, actions, mask, chosen, rewards, next_states, next_actions, next_mask,
         terminals, discounts) = (th.as_tensor(array) for array in collate(batch))

        current = self.network(states, actions, mask).gather(1, chosen)
        with th.no_grad():
            if config.algorithm == "bandit":
                target = rewards
            else:
                # Double DQN: the online network chooses, the target network evaluates.
                best = self.network(next_states, next_actions, next_mask).argmax(1, keepdim=True)
                bootstrap = self.target(next_states, next_actions, next_mask).gather(1, best)
                target = rewards + (1.0 - terminals) * discounts * bootstrap

        elementwise = F.smooth_l1_loss(current, target, reduction="none")
        loss = (th.as_tensor(weights).unsqueeze(1) * elementwise).mean()
        self.optimizer.zero_grad()
        loss.backward()
        th.nn.utils.clip_grad_norm_(self.network.trainable_parameters(), config.max_grad_norm)
        self.optimizer.step()

        self.replay.update_priorities(indices, (target - current).detach().numpy())
        self.stats.gradient_steps += 1
        self.stats.losses.append(float(loss.item()))
        if config.algorithm == "dqn" and self.stats.gradient_steps % config.target_update == 0:
            self.target.load_state_dict(self.network.state_dict())
            self.stats.target_updates += 1

    # -- persistence ----------------------------------------------------------------------------

    def save(self, path: Path, meta: dict | None = None) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "checkpoint_version": CHECKPOINT_VERSION,
            "feature_version": FEATURE_VERSION,
            "state_dim": STATE_DIM, "action_dim": ACTION_DIM,
            "config": self.config.to_dict(),
            "residual": self.network.has_residual,
            "stats": self.stats.to_dict(),
            "meta": meta or {},
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        th.save({"header": json.dumps(header, default=str),
                 "network": self.network.state_dict()}, temporary)
        temporary.replace(path)
        return path

    @staticmethod
    def read_header(path: Path) -> dict:
        return json.loads(th.load(Path(path), map_location="cpu", weights_only=True)["header"])

    @classmethod
    def load(cls, path: Path, config: AgentConfig | None = None, *,
             rewarder: RewardComposer | None = None, labels=None) -> "QAgent":
        """Rebuild an agent around saved weights. `config` overrides the saved one.

        Refuses a checkpoint written for a different feature layout: the network would
        load -- the tensors have the right shapes -- and then score vectors whose columns
        mean something else, which is a silent failure rather than a loud one.
        """
        blob = th.load(Path(path), map_location="cpu", weights_only=True)
        header = json.loads(blob["header"])
        if (header.get("feature_version") != FEATURE_VERSION
                or header.get("state_dim") != STATE_DIM
                or header.get("action_dim") != ACTION_DIM):
            raise ValueError(
                f"{path} was written for features {header.get('feature_version')} "
                f"({header.get('state_dim')}, {header.get('action_dim')}); this build is "
                f"{FEATURE_VERSION} ({STATE_DIM}, {ACTION_DIM})")
        saved = AgentConfig(**header["config"])
        config = config or saved
        network = TransferQNetwork(STATE_DIM, ACTION_DIM, dueling=saved.dueling,
                                   residual=bool(header.get("residual")))
        network.load_state_dict(blob["network"])
        agent = cls(config, rewarder=rewarder, network=network, labels=labels)
        agent.loaded_from = {"path": str(path), "header": header}
        return agent

    def summary(self) -> dict:
        return {
            "config": self.config.to_dict(),
            "stats": self.stats.to_dict(),
            "replay_size": len(self.replay),
            "residual": self.network.has_residual,
            "trainable_parameters": int(sum(p.numel() for p in self.network.trainable_parameters())),
            "reward": self.rewarder.summary(),
            "loaded_from": getattr(self, "loaded_from", {}).get("path"),
        }
