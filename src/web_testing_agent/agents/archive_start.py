"""Go-Explore's archive, used as a start-state curriculum for a learning agent.

**The problem this solves is not a learning problem.** Instrumenting the deep-flow gate
explains the failure exactly: `order-1.html` offers 19 valid actions, one of which is the
SELECT that opens the gate and nine of which leave the page. Once the SELECT reveals
`Continue`, that link is 1 of 20 while nine exits remain. Passing stage 1 is roughly a 1%
event per arrival, and four gates compound to about 1e-6. Across 60 evaluation episodes
of the most recent 3-seed run, **no policy of any kind reached stage 2**.

A DQN cannot learn from a reward it never receives. TD error propagates value backward
from returns that were *observed*, and with zero successful trajectories in the replay
buffer there is nothing to propagate. The agent was never failing at credit assignment —
no credit was ever assigned to it. Shaping the reward does not fix this, because the
failure is structural: this is Go-Explore's **derailment**. The explorer does reach the
gate-open state regularly, and exploration from the start state almost never returns to
it.

**So the archive supplies start states, and the learner does the learning.** Each episode
begins either at the landing page or, with probability `p_return`, at an archived cell
reached by replaying the stored route. Deep transitions then enter the replay buffer, and
everything downstream — n-step returns, prioritization, the action-conditioned head —
finally has something to work with.

Three deliberate choices:

* **`p_return` is well under 1.** Half the episodes still start at the entry point. A
  policy trained only from archived states forgets the landing page, and the landing page
  is where evaluation begins and where a real user starts.
* **Route replay is outside the step budget**, matching what `setup_actions` already does
  for session bootstrap. It is not free, though — it costs real browser actions and real
  wall clock — so `replayed_actions` is counted and reported. A comparison matched on env
  steps alone silently hands this agent ~2.6x the browser actions of its baselines.
* **Archiving stops the moment a route stops describing where the agent is.** A cell
  recorded with a route that does not reach it is worse than a missing cell: it looks
  reachable, gets selected as a springboard, and silently lands somewhere else.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import gymnasium as gym

from ..utils.logging import get_logger
from .go_explore import Archive, to_replay_step

logger = get_logger(__name__)


@dataclass
class ArchiveStats:
    """What the archive did, in the terms the comparison is made on."""

    episodes: int = 0
    returns_attempted: int = 0
    returns_failed: int = 0
    cells_discovered: int = 0
    # Browser actions spent replaying routes. Outside the step budget by design, and
    # emphatically not outside the cost.
    replayed_actions: int = 0
    # Times a lost route was recovered by arriving at a state the archive already holds.
    # Reported rather than hidden: it is the difference between an archive that keeps
    # working after a history move and one that goes blind for the rest of the episode.
    reanchored: int = 0

    def to_dict(self) -> dict:
        return {
            "episodes": self.episodes,
            "returns_attempted": self.returns_attempted,
            "returns_failed": self.returns_failed,
            "cells_discovered": self.cells_discovered,
            "replayed_actions": self.replayed_actions,
            "reanchored": self.reanchored,
            "return_failure_rate": round(
                self.returns_failed / self.returns_attempted, 4
            ) if self.returns_attempted else 0.0,
        }


class ArchiveStartWrapper(gym.Wrapper):
    """Starts some episodes at an archived state, and archives what the agent reaches.

    Sits between the env and the vectorizer so that `reset()` can choose the start state
    *before* the underlying env replays it — which is why this is a `Wrapper` and not an
    SB3 callback. A callback fires after `VecEnv` auto-reset has already happened, by
    which point the episode has begun at the landing page.
    """

    def __init__(
        self,
        env,  # noqa: ANN001 - WebFunctionalEnv
        archive: Archive | None = None,
        *,
        p_return: float = 0.5,
        depth_bias: float = 1.0,
        max_route_length: int = 12,
        seed: int = 0,
    ) -> None:
        super().__init__(env)
        self.archive = archive if archive is not None else Archive()
        self.p_return = p_return
        self.depth_bias = depth_bias
        self.max_route_length = max_route_length
        self.stats = ArchiveStats()
        self._rng = random.Random(seed)
        self._route: list[dict] = []
        self._route_valid = True
        self._episode = 0
        # Tracked here rather than read off the env: the env rebuilds its action space
        # and state during `step`, so by the time it returns there is no "before" left
        # to read.
        self._previous_key: str = ""

    # -- gym API ------------------------------------------------------------------

    def reset(self, **kwargs):  # noqa: ANN201
        cell = None
        if self.archive.cells and self._rng.random() < self.p_return:
            cell = self.archive.select(self._rng, self.depth_bias)
        route = list(cell.path) if cell is not None else []

        self.env.setup_actions = route
        if route:
            self.stats.returns_attempted += 1

        obs, info = self.env.reset(**kwargs)
        self._episode += 1
        self.stats.episodes += 1
        self.stats.replayed_actions += int(info.get("bootstrap_steps", 0) or 0)

        landed = info.get("state_key", "")
        self._route = route
        self._route_valid = True

        if cell is not None and landed != cell.key:
            # The stored route no longer reaches the cell it claims to. Explore from
            # wherever the partial replay left the browser — those steps are not wasted
            # and anything found is real — but archive nothing, because neither `route`
            # nor `[]` honestly describes this state.
            self.archive.returns_failed += 1
            self.stats.returns_failed += 1
            logger.debug(
                "return failed: expected {} got {} after replaying {} step(s)",
                cell.key[:12], landed[:12], len(route),
            )
            self._route_valid = False
        else:
            self.archive.observe(landed, route, self._episode, url=_url_of(info))

        self._previous_key = landed
        return obs, info

    def step(self, action):  # noqa: ANN201
        previous_key = self._previous_key
        spec_before = self._spec_for(action)
        obs, reward, terminated, truncated, info = self.env.step(action)

        current_key = info.get("state_key", "")
        executed = bool((info.get("exec_info") or {}).get("success", True))
        step = to_replay_step(spec_before) if spec_before is not None else None

        if step is not None and executed and len(self._route) < self.max_route_length:
            self._route = self._route + [step]
        elif previous_key and current_key != previous_key:
            # The state moved but this action cannot be written into a route — it is
            # unreplayable (NO_OP, a history move), it failed part-way, or the route hit
            # its cap. Whatever `_route` holds no longer reaches here.
            self._route_valid = False

        # **Re-anchor rather than staying blind for the rest of the episode.**
        #
        # Measured on the seed-0 diagnostic: `BROWSER_BACK` (152), `NO_OP` (130) and
        # `BROWSER_FORWARD` (111) account for roughly four actions per 40-step episode,
        # so `_route_valid` went False early in most episodes and *nothing* was archived
        # afterwards. The archive found 8 cells in the first 100 steps and 1 more in the
        # remaining 3,900. Training reached `order-2.html` on seven steps and archived
        # **zero** depth-2 cells — it missed the only states worth having.
        #
        # Arriving at a state the archive already holds restores the one thing that was
        # lost, which is knowing a route to where we are. The route re-adopted here was
        # verified by construction when that cell was stored, so this claims nothing new;
        # it only stops one history move from discarding the rest of an episode.
        #
        # Deliberately *not* done by making history moves replayable. `history_move`
        # returns False when the URL does not change and refuses to leave the app, and
        # `ScriptedPolicy._match` resolves a step by type plus id/text — a BACK carries
        # no matchable target, so a stored BACK would be matched by position and would
        # depend on the replayed history having exactly the same depth. That is a wider
        # change with a failure mode (a route that silently returns somewhere else) far
        # worse than the one being fixed.
        if not self._route_valid:
            known = self.archive.cells.get(current_key)
            if known is not None:
                self._route = list(known.path)
                self._route_valid = True
                self.stats.reanchored += 1

        if self._route_valid and self.archive.observe(
            current_key, self._route, self._episode, url=_url_of(info)
        ):
            self.stats.cells_discovered += 1

        self._previous_key = current_key
        info = {**info, "archive_cells": len(self.archive.cells)}
        return obs, reward, terminated, truncated, info

    # -- internals ----------------------------------------------------------------

    def _spec_for(self, action: int):  # noqa: ANN202
        """The ActionSpec the env will resolve `action` to, read before the step.

        Read from the env's *current* action list rather than from the post-step `info`,
        because the action space is rebuilt during the step and the post-step list
        describes the page the action landed on, not the one it was chosen from.
        """
        specs = getattr(self.env, "_action_specs", None) or []
        if 0 <= action < len(specs):
            return specs[action]
        return None


def _url_of(info: dict) -> str:
    return str((info.get("page") or {}).get("url", ""))
