"""Archive-based exploration: return to a promising state, then explore from it.

**Why this exists, and why it is not another reward tweak.**

The deep-flow fixture was never solved by any policy — DQN masked or unmasked, random
masked or unmasked, hash encoders or semantic, three seeds each. `order-2.html` was not
reached once in 4,800 evaluated steps. Instrumenting the gate explains why, and the
explanation is not about learning:

* `order-1.html` offers 19 valid actions. One is the SELECT that opens the gate; nine
  are links that leave the page.
* Once the SELECT reveals `Continue`, that link is 1 of 20 actions while nine others
  still leave. Odds of wandering off before continuing: **9 : 1**.
* So passing stage 1 is roughly a 1% event per arrival, and four gates compound it to
  somewhere around 10^-6.

A DQN cannot learn from a reward it never receives. TD error propagates value backward
from *observed* returns, and with zero successful trajectories in the replay buffer
there is nothing to propagate. The agent was not failing at credit assignment; no credit
was ever assigned to it.

Shaping the reward does not fix this on its own either, because the failure is
structural. It is Go-Explore's **derailment**: the explorer *does* reach the gate-open
state regularly, and then random exploration from the start state almost never returns
to it. Any state whose only route is a long precise sequence is effectively unreachable
no matter how attractive you make it, because attractiveness only helps a policy that
can get there to try again.

**The fix is to stop requiring exploration to re-derive the route.** Keep an archive of
states that have been reached, remember the action sequence that reached each one, and
begin each episode by *replaying* that sequence to return there before exploring. Each
gate then becomes an independent ~10% problem attempted many times, instead of one
compounded 10^-6 problem attempted never. This is "first return, then explore"
(Ecoffet et al., *Nature* 2021).

Three things make it a natural fit here rather than an import:

1. `WebFunctionalEnv.setup_actions` already replays a step sequence at the start of an
   episode, **outside the step budget**, resolved against the live action space by the
   same matcher repro scripts use. That is exactly the "return" primitive, and it was
   built for session bootstrap without this use in mind.
2. `state_fingerprint` already provides the cell representation, and using the env's own
   `state_key` keeps "somewhere I have been" identical to what the reward means by it.
3. Web testing wants **coverage**, not a deployable policy. Go-Explore optimises for
   reaching many distinct states, which is the actual objective; a DQN's learned policy
   is a means the objective never asked for.

**Known limitation, stated because it decides where this is valid.** Returning by replay
assumes the route reproduces. That holds on the fixtures and largely holds on Gitea, but
any state that depends on time, a counter or a prior mutation may not be recoverable
this way. Go-Explore's own answer is a robustification phase that trains a policy to
imitate the archive's trajectories; that is not built here. Cells whose replay lands on a
different fingerprint than expected are counted in `returns_failed` and dropped, so a
non-deterministic target degrades into ordinary random exploration rather than silently
reporting states it never actually revisited.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from ..envs.types import ActionSpec, ActionType
from ..utils.logging import get_logger

logger = get_logger(__name__)

# Actions that cannot be replayed as a route and are pointless inside a stored path.
# NO_OP carries no effect, and the history moves depend on a browser history the replay
# does not reconstruct — a stored BROWSER_BACK would go somewhere else on the way back.
_UNREPLAYABLE = frozenset({ActionType.NO_OP, ActionType.BROWSER_BACK, ActionType.BROWSER_FORWARD})

# Params that identify *which* variant of an action to replay. The scripted matcher
# compares these as constraints, so they must discriminate without over-constraining:
# TYPE carries both a generated `value` and its `category`, and matching on the category
# is what repro scripts already do (the value is regenerated per page).
_DISCRIMINATING_PARAMS = {
    ActionType.TYPE: ("category",),
    ActionType.SELECT: ("option",),
    ActionType.RESIZE_VIEWPORT: ("preset",),
    ActionType.SCROLL: ("direction",),
}


def to_replay_step(spec: ActionSpec) -> dict | None:
    """Serialize an executed action into the `setup_actions` schema, or None.

    Mirrors `ScriptedPolicy._match`: `id` when the element has one (exact and stable
    across copy edits), the visible label otherwise, plus whichever params distinguish
    this variant from its siblings.
    """
    if spec.action_type in _UNREPLAYABLE:
        return None

    step: dict = {"type": spec.action_type.value}
    selector = spec.selector or ""
    if selector.startswith('[id="') and selector.endswith('"]'):
        step["id"] = selector[5:-2]
    elif spec.element_id:
        step["text"] = spec.element_id
    elif spec.action_type not in _DISCRIMINATING_PARAMS:
        # Nothing to match on and no distinguishing params: not addressable on replay.
        return None

    params = {
        key: spec.params[key]
        for key in _DISCRIMINATING_PARAMS.get(spec.action_type, ())
        if key in (spec.params or {})
    }
    if params:
        step["params"] = params
    return step


@dataclass
class Cell:
    """One archived state, and the cheapest known route back to it."""

    key: str
    path: list[dict]
    # How often this cell has been *chosen as a starting point*, which is what the
    # selection weight decays — not how often it has been seen. A cell reached
    # incidentally a hundred times is still an unexplored springboard.
    chosen: int = 0
    # New cells discovered while exploring from here. A cell that keeps paying is worth
    # returning to; one that has never produced anything is not.
    discoveries: int = 0
    first_seen_iteration: int = 0

    @property
    def depth(self) -> int:
        return len(self.path)

    def weight(self, depth_bias: float = 1.0) -> float:
        """Prefer cells that are under-explored, productive, and deep.

        `1/sqrt(1+chosen)` is the standard count-based decay: it keeps returning to a
        cell while it is still yielding and moves on once it is exhausted, without ever
        dropping it to zero.

        The `depth` term is what makes the archive *climb*, and leaving it out is a
        measured mistake rather than a hypothetical one. With only `discoveries` and
        `chosen`, every weight converges to roughly `1/sqrt(chosen)` once the easy pages
        are exhausted — uniform over cells — so a 600-step run spent most of its budget
        re-exploring `terms.html` and `privacy.html` and never accumulated a single
        gate-open state.

        Route length is a sound progress proxy *here* because a cell's stored path is
        the shortest one known to it (see `Archive.observe`), and on a gated flow the
        gated states are exactly the ones whose shortest route is long. It would be a
        poor proxy on a site where depth and distance are unrelated, which is why it is
        a tunable bias rather than a hard ordering.
        """
        return (1.0 + self.discoveries) * (1.0 + self.depth) ** depth_bias / math.sqrt(1.0 + self.chosen)


@dataclass
class Archive:
    """Every distinct state reached, keyed by the env's own `state_key`."""

    cells: dict[str, Cell] = field(default_factory=dict)
    # Cells whose stored route stopped reproducing. Reported rather than hidden: on a
    # non-deterministic target this number is the difference between "explored" and
    # "believed it had explored".
    returns_failed: int = 0
    _replaced: int = 0

    def observe(self, key: str, path: list[dict], iteration: int = 0) -> bool:
        """Record a state. Returns True when it had not been seen before.

        A shorter route to a known cell replaces the stored one. Returning is paid for
        on every episode that selects the cell, so route length is a running cost, and a
        shorter path is also less likely to contain a step that fails to reproduce.
        """
        existing = self.cells.get(key)
        if existing is None:
            self.cells[key] = Cell(key=key, path=list(path), first_seen_iteration=iteration)
            return True
        if len(path) < len(existing.path):
            existing.path = list(path)
            self._replaced += 1
        return False

    def select(self, rng: random.Random, depth_bias: float = 1.0) -> Cell | None:
        """Sample a cell to return to, weighted by `Cell.weight`."""
        if not self.cells:
            return None
        cells = list(self.cells.values())
        chosen = rng.choices(cells, weights=[c.weight(depth_bias) for c in cells], k=1)[0]
        chosen.chosen += 1
        return chosen

    def to_dict(self) -> dict:
        deepest = max(self.cells.values(), key=lambda c: c.depth, default=None)
        return {
            "cells": len(self.cells),
            "returns_failed": self.returns_failed,
            "shorter_routes_found": self._replaced,
            "max_route_length": deepest.depth if deepest else 0,
        }


@dataclass
class GoExploreStats:
    """What the run did, in the terms the comparison is made on."""

    iterations: int = 0
    env_steps: int = 0
    returns_attempted: int = 0
    returns_failed: int = 0
    cells_discovered: int = 0

    def to_dict(self) -> dict:
        return {
            "iterations": self.iterations,
            "env_steps": self.env_steps,
            "returns_attempted": self.returns_attempted,
            "returns_failed": self.returns_failed,
            "cells_discovered": self.cells_discovered,
        }


def run_go_explore(
    env,  # noqa: ANN001 - WebFunctionalEnv, not imported to keep agents/ independent of envs/
    *,
    iterations: int = 100,
    explore_steps: int = 3,
    max_route_length: int = 12,
    depth_bias: float = 1.0,
    seed: int = 0,
    on_step=None,  # noqa: ANN001 - optional (info, reward) callback for recording
) -> tuple[Archive, GoExploreStats]:
    """Return-then-explore over a live browser environment.

    One iteration = select a cell, replay its route to get back there (free, outside the
    step budget), then take `explore_steps` uniform-random *valid* actions from it,
    archiving anything new. Random rather than learned, deliberately: the point being
    tested is whether the **archive** solves the flow, and mixing in a learned policy
    would leave it ambiguous which half did the work.

    `max_route_length` caps how deep a stored route may get. Every step of a route is
    replayed on every return, so unbounded growth turns each iteration into a long
    replay; it also bounds the damage when a target is only partly deterministic.

    **`explore_steps` is small on purpose.** Nine of the nineteen actions on a flow page
    are links that leave it, so an explorer let loose for ten steps spends one or two of
    them at the cell it was sent to and the rest wandering. Since returning costs wall
    clock but no env steps, a short burst followed by another return spends far more of
    the *step* budget where the frontier actually is. Measured on this fixture: ten
    steps per return never accumulated a gate-open state at all.
    """
    rng = random.Random(seed)
    archive = Archive()
    stats = GoExploreStats()

    for iteration in range(iterations):
        cell = archive.select(rng, depth_bias)
        route = list(cell.path) if cell else []

        env.setup_actions = route
        if route:
            stats.returns_attempted += 1
        _obs, info = env.reset()

        # Did the replay actually land where the archive claims? A route that silently
        # stops working would otherwise have this iteration explore from the landing
        # page while every new cell it finds is recorded with that route as its prefix —
        # producing an archive full of unreachable states that all look reachable.
        landed = info.get("state_key", "")

        # Whether `route` still describes how to get where we are. Everything archived
        # below is a claim that replaying `route` reaches that state, and a false claim
        # here is worse than a missing cell: it fills the archive with states that look
        # reachable, are selected as springboards, and then silently return somewhere
        # else — the exact failure this check exists to detect, propagated.
        route_valid = True

        if cell is not None and landed != cell.key:
            archive.returns_failed += 1
            stats.returns_failed += 1
            logger.debug(
                "return failed: expected {} got {} after replaying {} step(s)",
                cell.key[:12], landed[:12], len(route),
            )
            # A partial replay leaves the browser at a state reached by some unknown
            # subsequence, so neither `route` nor `[]` describes it. Explore from here
            # anyway — the steps are not wasted and any findings are real — but archive
            # nothing until a route can honestly be stated again, which for this burst
            # is never.
            route_valid = False

        if route_valid:
            archive.observe(landed, route, iteration)

        for _ in range(explore_steps):
            specs = info.get("action_specs") or []
            if not specs:
                break
            spec = specs[rng.randrange(len(specs))]
            previous_key = info.get("state_key", "")
            _obs, reward, terminated, truncated, info = env.step(spec.index)
            stats.env_steps += 1
            if on_step is not None:
                on_step(info, reward)

            current_key = info.get("state_key", "")
            executed = bool((info.get("exec_info") or {}).get("success", True))
            step = to_replay_step(spec)

            if step is not None and executed and len(route) < max_route_length:
                route = route + [step]
            elif current_key != previous_key:
                # The state moved, but this action cannot be written into a route — it is
                # unreplayable (NO_OP, a history move), it failed part-way, or the route
                # is at its cap. Whatever `route` holds no longer reaches here, so stop
                # archiving for the rest of this burst rather than storing a path that
                # would return somewhere else.
                route_valid = False
            # Otherwise the action changed nothing, so `route` still describes where we
            # are and remains usable. A dead control is not a broken route.

            if route_valid and archive.observe(current_key, route, iteration):
                stats.cells_discovered += 1
                if cell is not None:
                    cell.discoveries += 1

            if terminated or truncated:
                break

        stats.iterations = iteration + 1

    logger.info(
        "go-explore: {} cells from {} env steps ({} returns, {} failed)",
        len(archive.cells), stats.env_steps, stats.returns_attempted, stats.returns_failed,
    )
    return archive, stats
