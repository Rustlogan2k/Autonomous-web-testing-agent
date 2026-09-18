"""Read-only instrumentation for AC-DQN training runs.

**Why this is a separate module.** Every diagnostic the RL half of this project has ever
relied on — the `order-4` Q-gap trace, the receipt-transition collection counts, the PER
draw counts — was produced by scripts that were never committed. Only their JSON outputs
survive (`reports/diag_phase3_replay_seed{0,1,2}.json` and friends), so the central RL
diagnosis cannot currently be re-run or extended. This module is those instruments,
written once, in the package, with tests.

**The one requirement that overrides every other design choice: instrumentation must not
change training.** A diagnostic that perturbs the run it is measuring is worse than no
diagnostic, because the number it produces looks like a measurement. Three rules follow,
and `tests/unit/test_diagnostics.py` pins all three:

1. **No RNG is consumed.** Forward passes run under `th.no_grad()`; nothing samples,
   nothing initialises a module, nothing calls `np.random`, `random` or `th.rand*`.
   `deepcopy` of a network copies tensors and draws no numbers.
2. **No optimiser or module state is mutated.** The probe does not call
   `set_training_mode`, does not touch `.grad`, and never runs a backward pass. (The
   networks here are `Linear`/`ReLU` only, so train and eval mode are numerically
   identical anyway — but relying on that silently is how the next defect gets in.)
3. **The replay buffer subclass changes bookkeeping only.** `_commit` and `sample` are
   overridden to *record* what the base class did, then delegate; the draws themselves
   come from `super().sample`, unchanged.

**What the probe measures, and why the initial network is snapshotted.**
The failure this project stopped on is the `order-4` decision between `Place order` and
`Back`. `reports/diag_phase3_replay_seed{0,2}.json` show the Q-gap widening monotonically
in whichever direction it started, and never changing sign — seed 0 positive before any
receipt transition existed, seed 2 negative. The obvious question is therefore what the
*untrained* network thought, at the observation the run actually encountered. That cannot
be recovered afterwards: the initial weights are gone by the time the agent first reaches
`order-4`. So the initial `q_net` is deep-copied at training start and evaluated on the
first-arrival observation alongside the live one. It costs one state-dict copy and one
forward pass, and it is the only way to get the number at all.

Nothing here decides anything. It records.
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np
import torch as th
from stable_baselines3.common.callbacks import BaseCallback

from ..envs.types import MAX_ACTIONS
from ..utils.logging import get_logger
from .replay import PrioritizedNStepReplayBuffer

logger = get_logger(__name__)


def _page_url(info: dict) -> str:
    return str((info.get("page") or {}).get("url", "")) if isinstance(info, dict) else ""


def _mean_or_none(values: np.ndarray, mask: np.ndarray) -> float | None:
    return round(float(values[mask].mean()), 6) if mask.any() else None


def _slot_of(specs: list, label: str) -> int | None:
    """Index of the action whose `element_id` is `label`, or None.

    Matched on `element_id` because that is what the action registry sets to the
    control's visible text ("Place order", "Back") and what
    `ActionFeatureExtractor` hashes into the label block — so the slot found here is
    the slot whose features the network is actually discriminating on.
    """
    for index, spec in enumerate(specs[:MAX_ACTIONS]):
        if getattr(spec, "element_id", None) == label:
            return index
    return None


class QGapProbe(BaseCallback):
    """Records `Q(action_a) - Q(action_b)` at one fixed observation, over training.

    The observation is captured the **first** time the agent reaches a page whose URL
    contains `page_match`, and then never changes — so every later probe is a
    measurement of the network, not of which variant of the page the agent happened to
    be looking at. That matters here: with the hash encoders,
    `order-4.html?quantity=42` and `order-4.html?quantity=0` are orthogonal vectors, so
    probing "wherever the agent is now" would confound the network's movement with the
    page's.

    Deliberately a plain class rather than a dataclass: `BaseCallback` annotates `model`
    at class level with no default, which `@dataclass` would collect as a required field
    and put ahead of every argument here.
    """

    def __init__(
        self,
        page_match: str,
        action_a: str = "Place order",
        action_b: str = "Back",
        probe_every: int = 250,
    ) -> None:
        super().__init__(verbose=0)
        self.page_match = page_match
        self.action_a = action_a
        self.action_b = action_b
        self.probe_every = probe_every
        # Filled in as training proceeds; read by the caller after `learn()` returns.
        self.trace: list[dict] = []
        self.captured_at_step: int | None = None
        self.captured_url: str = ""
        self.initial: dict | None = None
        self.arrivals: int = 0
        self._obs: np.ndarray | None = None
        self._slot_a: int | None = None
        self._slot_b: int | None = None
        self._initial_net: Any = None

    # -- lifecycle ----------------------------------------------------------------

    def _on_training_start(self) -> None:
        # The untrained network, kept for the whole run. `deepcopy` copies tensors; it
        # does not construct new layers, so no initialiser runs and no RNG is drawn.
        self._initial_net = copy.deepcopy(self.model.q_net)
        for parameter in self._initial_net.parameters():
            parameter.requires_grad_(False)

    def _on_step(self) -> bool:
        infos = self.locals.get("infos") or []
        dones = self.locals.get("dones")
        info = infos[0] if infos and isinstance(infos[0], dict) else {}
        url = _page_url(info)
        if self.page_match and self.page_match in url:
            self.arrivals += 1

        if self._obs is None and self.page_match and self.page_match in url:
            # On a done step SB3's VecEnv has already auto-reset, so `_last_obs` belongs
            # to the *next* episode while `info` still describes the terminal one.
            # Capturing there would pair this page's action slots with another page's
            # observation — the same modality mismatch `SemanticPerceptionWrapper`
            # guards against on its own boundary.
            if dones is not None and bool(np.asarray(dones).reshape(-1)[0]):
                return True
            self._capture(info, url)

        if self._obs is not None and self.num_timesteps % max(1, self.probe_every) == 0:
            self.trace.append(self._measure())
        return True

    # -- internals ----------------------------------------------------------------

    def _capture(self, info: dict, url: str) -> None:
        specs = info.get("action_specs") or []
        slot_a, slot_b = _slot_of(specs, self.action_a), _slot_of(specs, self.action_b)
        if slot_a is None or slot_b is None:
            # Arrived at the page but one of the two controls is not in the action set
            # (truncated by the 100-slot budget, or off-screen and dropped). Recording a
            # gap against a missing slot would be a number about nothing, so wait for an
            # arrival where both exist rather than capturing a wrong one.
            logger.debug(
                "QGapProbe: {} present at {} but slots {}/{} are {}/{}; not capturing yet",
                self.page_match, url[:80], self.action_a, self.action_b, slot_a, slot_b,
            )
            return
        observation = np.asarray(self.model._last_obs)
        self._obs = observation[:1].copy()
        self._slot_a, self._slot_b = slot_a, slot_b
        self.captured_at_step = int(self.num_timesteps)
        self.captured_url = url
        self.initial = self._measure(net=self._initial_net) | {"which": "initial_network"}
        self.trace.append(self._measure())
        logger.info(
            "QGapProbe captured {} at step {} (slots {}={}, {}={}); "
            "initial-network gap {:+.6f}",
            self.page_match, self.captured_at_step, self.action_a, slot_a,
            self.action_b, slot_b, self.initial["gap"],
        )

    def _measure(self, net: Any = None) -> dict:
        """One reading. No RNG, no grad, no module-state change — see the module docstring."""
        network = net if net is not None else self.model.q_net
        tensor = th.as_tensor(self._obs, device=self.model.device)
        with th.no_grad():
            q_values = network(tensor)[0]
        q_a = float(q_values[self._slot_a])
        q_b = float(q_values[self._slot_b])
        buffer = getattr(self.model, "replay_buffer", None)
        record = {
            "step": int(self.num_timesteps),
            "q_a": round(q_a, 6),
            "q_b": round(q_b, 6),
            "gap": round(q_a - q_b, 6),
            "a_wins": q_a > q_b,
            "arrivals": self.arrivals,
        }
        if isinstance(buffer, TaggedPrioritizedNStepReplayBuffer):
            record |= buffer.stats()
        return record

    def to_dict(self) -> dict:
        return {
            "page_match": self.page_match,
            "action_a": self.action_a,
            "action_b": self.action_b,
            "probe_every": self.probe_every,
            "captured_at_step": self.captured_at_step,
            "captured_url": self.captured_url,
            "arrivals_total": self.arrivals,
            # The number the whole instrument exists for: what the *untrained* network
            # thought at the observation this run actually met.
            "initial_network": self.initial,
            "trace": list(self.trace),
        }


class TaggedPrioritizedNStepReplayBuffer(PrioritizedNStepReplayBuffer):
    """The production buffer, plus a count of how often one kind of transition is drawn.

    A subclass rather than a flag on `PrioritizedNStepReplayBuffer`, so that the buffer
    every published run was measured on is not edited at all. Both overrides delegate to
    the base class and then record; neither changes what is stored or which indices come
    back.

    `tag_match` is matched against `info["page"]["url"]` of every transition in an
    n-step window: a window is tagged if it *touches* the page, which is the question
    "was the final transition of the flow in this batch?" rather than "did it start
    there".
    """

    def __init__(self, *args, tag_match: str = "", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.tag_match = tag_match
        self.tagged_indices: set[int] = set()
        self.tagged_draws = 0
        self.total_draws = 0

    def _commit(self, length: int) -> None:
        index = self.pos
        window = list(self._pending)[:length]
        tagged = bool(self.tag_match) and any(
            self.tag_match in _page_url(info)
            for entry in window
            for info in (entry.infos or [])
        )
        super()._commit(length)
        # `super()._commit` wrote at `index`; a wrap-around overwrite must clear the old
        # tag or the count drifts upward forever on a buffer that actually cycles.
        if tagged:
            self.tagged_indices.add(index)
        else:
            self.tagged_indices.discard(index)

    def sample(self, batch_size: int, env=None):  # noqa: ANN001, ANN201 - matches the base signature
        samples = super().sample(batch_size, env=env)
        self.total_draws += len(samples.indices)
        self.tagged_draws += int(sum(1 for i in samples.indices if int(i) in self.tagged_indices))
        return samples

    def reset(self) -> None:
        super().reset()
        self.tagged_indices = set()
        self.tagged_draws = 0
        self.total_draws = 0

    def stats(self) -> dict:
        """Counts only. Every ratio this project has derived from them has needed a
        lifetime correction (`diag_phase3_replay_seed2.json`'s `per_over_uniform` was
        misleading for exactly that reason), so ratios are left to the analysis."""
        stored = self.buffer_size if self.full else self.pos
        priorities = np.array([self._tree.get(i) for i in range(stored)]) if stored else np.zeros(0)
        tagged = np.array(sorted(self.tagged_indices), dtype=int)
        tagged = tagged[tagged < stored] if tagged.size else tagged
        mask = np.zeros(stored, dtype=bool)
        if tagged.size:
            mask[tagged] = True
        return {
            "tagged_stored": int(mask.sum()),
            "buffer_transitions": int(stored),
            "tagged_draws": int(self.tagged_draws),
            "total_draws": int(self.total_draws),
            "mean_priority_tagged": _mean_or_none(priorities, mask),
            "mean_priority_other": _mean_or_none(priorities, ~mask),
        }
