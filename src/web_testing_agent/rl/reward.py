"""The reward the learning agent is trained on, and a log of every term of it.

    r = r_env  +  lambda * ( gamma * Phi(s') - Phi(s) )
        -----     ------------------------------------
        deterministic        dynamic LLM reward
        exploration          (potential-based shaping)

**`r_env`** is what `WebFunctionalEnv` already computes: a step cost, count-decayed state
novelty, a bonus for opening a gate, a repetition penalty, and the deterministic trigger
bonuses (console error, failed request, broken navigation), each paid once. It contains no
language model and no seeded-fault label.

One correction is applied to it. The gate bonus is paid *per newly revealed control*, which
was sized on a multi-page fixture where a gate reveals one control. On a single-page
application every view switch reveals a dozen, and the term would dominate the reward there
and nowhere else -- so the scale of the reward would depend on how an application happens
to be built. It is capped here at one control's worth per step.

**The LLM term is potential-based** (Ng, Harada & Russell, 1999). `Phi` is the language
model's functional-progress assessment of a state (`reward.progress`). Shaping with
`gamma * Phi(s') - Phi(s)` has three properties that a raw "the model liked this step"
bonus does not:

* **It cannot be farmed.** Around any cycle the shaping terms telescope to a non-positive
  sum, so there is no loop the agent can repeat to accumulate it. Every reward exploit this
  project has recorded was a loop.
* **It is bounded.** `Phi` lies in [0, 1], so the term lies in [-lambda, +lambda].
* **It leaves the optimal policy of the unshaped problem unchanged.** The model steers
  exploration; it does not redefine the objective. If its judgments are poor the agent is
  slowed, not misdirected permanently.

That last guarantee is about the *optimal* policy and says nothing about a finite budget,
which is what this project measures -- the literature review is explicit about it. Whether
the term helps within 2,000 interactions is an empirical question, answered by the
with-and-without ablation rather than by the theorem.

**A missing judgment means no shaping**, for that transition only. If either potential is
unavailable (model down, offline cache miss) the term is zero and the miss is logged. A
potential invented for an unknown state would silently break the telescoping property.

For the myopic (bandit) learner, which has no discounting, the term is the plain
difference `Phi(s') - Phi(s)`: the progress this one action made.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..reward.progress import ProgressScorer


@dataclass(frozen=True)
class RewardConfig:
    #: Weight on the LLM shaping term. 0 disables it (and no model is consulted).
    llm_weight: float = 0.0
    #: Discount inside the shaping term. Set to the agent's gamma for the bootstrapped
    #: learner and to 1.0 for the myopic one.
    shaping_gamma: float = 0.95
    #: Most the gate-opening term may pay in one step (one control's worth by default).
    reveal_cap: float = 1.5
    #: Final bound on the composed reward.
    clip: float = 6.0

    def to_dict(self) -> dict:
        return asdict(self)


class RewardLog:
    """Append-only JSONL of every reward term on every step.

    Written as the run proceeds, one line per step, so a run that dies after four hours
    still has its reward record. This file is what the reward-correlation analysis reads.
    """

    def __init__(self, path: Path, *, run: dict | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")
        if run:
            self.write({"_run": run})

    def write(self, row: dict) -> None:
        self._handle.write(json.dumps(row, default=str) + "\n")

    def flush(self) -> None:
        self._handle.flush()

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()


class RewardComposer:
    """Combines the env's reward with the LLM shaping term. One instance per run."""

    def __init__(self, config: RewardConfig | None = None, scorer: ProgressScorer | None = None,
                 log: RewardLog | None = None) -> None:
        self.config = config or RewardConfig()
        if self.config.llm_weight and scorer is None:
            raise ValueError("llm_weight is non-zero but no ProgressScorer was supplied")
        self.scorer = scorer if self.config.llm_weight else None
        self.log = log
        self.context = ""
        self.steps = 0
        self.shaped_steps = 0
        self.missing_potential = 0
        self.total_shaping = 0.0
        self._last_potential: tuple[str, float | None] = ("", None)

    def set_application(self, context: str) -> None:
        """What the application is, in a phrase; given to the model with every snapshot."""
        self.context = (context or "").strip()[:120]
        self._last_potential = ("", None)

    def _potential(self, info: dict) -> float | None:
        if self.scorer is None:
            return None
        state_key = info.get("state_key", "")
        page = info.get("page") or {}
        # `s'` of one step is `s` of the next. The digest is recomputed only when the page
        # content differs; two states with one fingerprint can still differ in their text.
        marker = f"{state_key}:{hash(page.get('html', ''))}"
        if marker == self._last_potential[0]:
            return self._last_potential[1]
        judgment = self.scorer.potential(page.get("url", ""), page.get("html", ""),
                                         context=self.context,
                                         visible_text=page.get("visible_text"))
        value = judgment.value if judgment is not None else None
        self._last_potential = (marker, value)
        return value

    def compute(self, before: dict, after: dict, env_reward: float,
                extra: dict | None = None) -> tuple[float, dict]:
        """`(reward, components)` for one transition."""
        config = self.config
        self.steps += 1

        reveal = float(after.get("reveal_bonus", 0.0) or 0.0)
        base = float(env_reward) - reveal + min(reveal, config.reveal_cap)

        phi_before = phi_after = None
        shaping = 0.0
        if self.scorer is not None:
            phi_before = self._potential(before)
            phi_after = self._potential(after)
            if phi_before is None or phi_after is None:
                self.missing_potential += 1
            else:
                shaping = config.llm_weight * (config.shaping_gamma * phi_after - phi_before)
                self.shaped_steps += 1
                self.total_shaping += shaping

        total = max(-config.clip, min(config.clip, base + shaping))
        components = {
            "env": round(base, 5),
            "llm_shaping": round(shaping, 5),
            "phi_before": phi_before,
            "phi_after": phi_after,
            "total": round(total, 5),
        }
        if self.log is not None:
            exploration = after.get("exploration")
            row = {
                "t": self.steps,
                **components,
                "step_cost": after.get("step_cost"),
                "novelty": after.get("novelty_bonus"),
                "reveal": min(reveal, config.reveal_cap),
                "triggers": after.get("deterministic_bonus"),
                "novel_state": bool(getattr(exploration, "is_novel_state", False)),
                "run_visits": getattr(exploration, "state_run_visits", None),
                "repeat_count": getattr(exploration, "repeat_count", None),
                "state_changed": after.get("state_key") != before.get("state_key"),
                "exec_success": bool((after.get("exec_info") or {}).get("success", False)),
            }
            if extra:
                row.update(extra)
            self.log.write(row)
        return total, components

    def summary(self) -> dict:
        return {
            "config": self.config.to_dict(),
            "steps": self.steps,
            "shaped_steps": self.shaped_steps,
            "missing_potential": self.missing_potential,
            "total_shaping": round(self.total_shaping, 4),
            "scorer": self.scorer.stats.to_dict() if self.scorer is not None else None,
            "scorer_config": self.scorer.config() if self.scorer is not None else None,
        }
