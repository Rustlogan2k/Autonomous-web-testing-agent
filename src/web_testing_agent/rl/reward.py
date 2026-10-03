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

**Why the default is not the potential form (measured, 2026-10-01).** Potential shaping is
designed to leave the optimal policy unchanged, and that is exactly what made it useless
here. The only depth-seeking term in `r_env` is count-decayed novelty, which is exhausted
once an application is covered; after that the optimal policy has no reason to reach an
outcome page, and shaping, by construction, cannot give it one. The in-distribution check
showed it: an agent pretrained for 3,000 steps on `shop_catalog` and then set loose on the
same application never reached its confirmation page in 1,500 steps, on either seed --
worse than an agent from scratch.

So the default mode is the **episodic progress record**:

    r_llm = lambda * max(0, Phi(s') - max_{k <= t} Phi(s_k))      within the episode

It pays when the agent reaches a page further into a task than anything earlier in the
same episode, and only then. Its properties are the ones the potential form was chosen for,
minus the one that hurt:

* **bounded** -- the total over an episode is at most `lambda * max Phi <= lambda`;
* **not farmable** -- it pays for a new *maximum*, so revisiting, oscillating or looping
  earns nothing; the only way to collect it again is a new episode, from the start page;
* **renewed every episode** -- unlike novelty it does not decay over the run, so reaching
  outcome pages stays worth doing for the whole budget, which is what finding faults that
  sit behind a workflow requires.

What it gives up is policy invariance: it changes what is optimal, deliberately, towards
completing tasks. That makes the quality of the model's judgments matter, which is why they
are validated against the benchmark's ground truth (`scripts/evaluate_llm_reward.py`). The
potential form remains available as `llm_mode="potential"`.

**The outcome bonus (added after the record alone was measured to fail, 2026-10-01).** The
model rates a product page whose quantity field holds its default as 8 of 10, so the
record bonus was collected almost entirely on reaching *any* form; completing the task
added 0.2. Traced on the pretrained agent: its highest Q-values were links to information
pages, because five of them are five novel states per episode, and nothing paid for
finishing. So a sparse term pays `outcome_bonus` the first time in an episode that each
distinct state the model places in the `outcome_displayed` stage is
reached. Bounded per episode by the number of outcome states, unfarmable for the same reason
as the record, and aimed exactly where functional faults become observable.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..reward.progress import ProgressScorer


@dataclass(frozen=True)
class RewardConfig:
    #: Weight on the LLM term. 0 disables it (and no model is consulted).
    llm_weight: float = 0.0
    #: "record" (episodic progress record, the default) or "potential" (shaping).
    llm_mode: str = "record"
    #: Sparse term: paid once per episode per distinct state the model places in the
    #: `outcome_displayed` stage (a task committed and its outcome shown). The stage, not a
    #: threshold on the score: a 9 means "between ready to commit and committed", and an
    #: unsubmitted edit form was measured to receive it.
    outcome_bonus: float = 3.0
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
        self._last_stage = ""
        #: Highest potential reached this episode; None until the episode's first step.
        self._episode_best: float | None = None
        self._episode_outcomes: set[str] = set()
        self.outcome_payments = 0

    def start_episode(self) -> None:
        """A new episode starts from the landing page; the progress record starts over."""
        self._episode_best = None
        self._episode_outcomes = set()

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
        marker = f"{state_key}:{hash(page.get('html', ''))}:{hash(page.get('visible_text') or '')}"
        if marker == self._last_potential[0]:
            return self._last_potential[1]
        judgment = self.scorer.potential(page.get("url", ""), page.get("html", ""),
                                         context=self.context,
                                         visible_text=page.get("visible_text"))
        value = judgment.value if judgment is not None else None
        self._last_potential = (marker, value)
        self._last_stage = judgment.stage if judgment is not None else ""
        return value

    def episode_state(self) -> tuple[float, float]:
        """`(best progress this episode, outcome bonuses paid this episode)`, normalised.

        Both LLM terms depend on them, so they belong in the observation: without them the
        same state and action can pay +3 on the first arrival of an episode and 0 on the
        next with an identical input, which is the non-Markov gap the env's own context
        slots were added to close for its novelty and gate terms.
        """
        best = self._episode_best if self._episode_best is not None else 0.0
        return float(best), min(len(self._episode_outcomes) / 3.0, 1.0)

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
            if config.llm_mode == "record":
                if self._episode_best is None:
                    self._episode_best = phi_before if phi_before is not None else 0.0
                if phi_after is None:
                    self.missing_potential += 1
                else:
                    shaping = config.llm_weight * max(0.0, phi_after - self._episode_best)
                    self._episode_best = max(self._episode_best, phi_after)
                    state_key = after.get("state_key", "")
                    if (config.outcome_bonus and self._last_stage == "outcome_displayed"
                            and state_key not in self._episode_outcomes):
                        self._episode_outcomes.add(state_key)
                        shaping += config.outcome_bonus
                        self.outcome_payments += 1
                    self.shaped_steps += 1
                    self.total_shaping += shaping
            elif phi_before is None or phi_after is None:
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
            "outcome_payments": self.outcome_payments,
            "scorer": self.scorer.stats.to_dict() if self.scorer is not None else None,
            "scorer_config": self.scorer.config() if self.scorer is not None else None,
        }
