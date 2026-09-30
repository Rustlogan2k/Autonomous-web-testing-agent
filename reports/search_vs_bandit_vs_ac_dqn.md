# Search + hand priority vs search + learned priority vs AC-DQN

## 1. Research question

Three arms share one fixture, one observation, one action space and mask, one reward, one archive and one evaluation protocol, and differ only in how an action is scored:

| rung | arm | scoring rule | learns | bootstraps |
|---|---|---|---|---|
| A | `hand_priority` | none - fixed hand-designed weights | False | False |
| A | `hand_priority_search` | none - fixed hand-designed weights | False | False |
| B | `contextual_bandit` | r_t (ContextualBandit.regression_target) | True | False |
| C | `ac_dqn` | r_t + gamma^n * max_a' Q_target(s_{t+n}, a') | True | True |

* **A vs B** — does learning an immediate action value beat a hand priority?
* **B vs C** — does bootstrapping beat myopic immediate-reward prediction?
* **A vs C** — the overall practical comparison, read only in light of the two above.

**B vs C does not isolate PER, dueling, Double DQN, n-step replay, the archive or the target network.** Those are shared between the two arms so that they cancel. The only claim it supports is myopic immediate-reward prediction versus bootstrapped sequential value learning under otherwise matched infrastructure.

## 2. Repository and configuration context

- **fixture**: tests/fixtures/deep_flow_site (DEEP-01: the receipt reports quantity 1 whatever was ordered; llm_required, cross_step, deep_flow; deterministic ceiling 0)
- **flow_depth_map**: {'index.html': 0, 'order-1.html': 1, 'order-2.html': 2, 'order-3.html': 3, 'order-4.html': 4, 'receipt.html': 5}
- **observation**: 6973-dim = 1664 perception + 9 episode context + 100x52 action features + 100 mask
- **action_space**: Discrete(100), page-rebuilt each step, valid-only masking
- **reward**: reward/functional_triggers.compose_reward with exploration shaping (novelty 1.0/sqrt(1+run visits), reveal 1.5/unpaid identity, repetition -0.05/repeat floored at -0.25, failed action -0.1, step -0.05); judge disabled (NullRewardModel)
- **encoders**: hash (default_encoders), as in every figure these checkpoints back
- **trained_arm_checkpoints**: loaded read-only; NOT retrained for this experiment
- **trained_arm_training_stats**: see reports/postmarkov_baseline_pr050.json and reports/contextual_bandit_pr050.json
- **prior_canonical_result**: reports/offline_eval_25ep.md — ac_dqn [24,0,25,23,1,2,24,17] mean 0.580; contextual_bandit [17,8,0,0,25,14,24,20] mean 0.540

## 3. Exact algorithms

- **`hand_priority`** (rung A) — target: `none - fixed hand-designed weights`. counters cleared every episode (stationary-policy reading).
- **`hand_priority_search`** (rung A) — target: `none - fixed hand-designed weights`. run-level counters carried across the 25 episodes (search reading).
- **`contextual_bandit`** (rung B) — target: `r_t (ContextualBandit.regression_target)`. no target network, no next state, no gamma.
- **`ac_dqn`** (rung C) — target: `r_t + gamma^n * max_a' Q_target(s_{t+n}, a')`. Double DQN, dueling head, PER, 3-step returns.

## 4. Search budget

- **unit**: environment steps - the unit run_go_explore --budget and compare_agents --train-steps both already use
- **arm_A_env_steps**: 4000
- **arm_B_C_train_steps**: 4000
- **matched**: True
- **episodes_per_search**: 100
- **archive**: ArchiveStartWrapper, p_return=0.5, same as the trained arms
- **route_replay**: outside the step budget (as in archive_start.py) and reported separately as replayed_actions / browser_actions_total
- **epsilon_schedule**: 1.0 -> 0.1 over 60% of the budget, identical to the trained arms
- **why_fair**: both arms are given the same number of environment interactions, the same archive at the same p_return, and the same exploration schedule; browser actions spent on route replay are reported rather than absorbed, so neither side gets free actions
- **what_arm_A_cannot_carry_forward**: nothing. A non-learning policy has no parameters to update, and the archive is disabled at evaluation for every arm. The search phase therefore measures coverage at a matched budget, not a trainable asset.

| seed | env steps | replayed | browser actions | archive cells | returns | failed | reanchored | states | max depth | completions/100 ep | runtime (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 4000 | 271 | 4271 | 23 | 44 | 5 | 322 | 29 | 5 | 1 | 1621.8 |
| 1 | 4000 | 153 | 4153 | 22 | 41 | 0 | 323 | 22 | 5 | 2 | 1395.0 |
| 2 | 4000 | 344 | 4344 | 21 | 47 | 0 | 332 | 24 | 5 | 3 | 1452.3 |
| 3 | 4000 | 327 | 4327 | 23 | 43 | 0 | 326 | 25 | 5 | 3 | 1445.8 |
| 4 | 4000 | 263 | 4263 | 17 | 52 | 0 | 342 | 20 | 5 | 1 | 1476.6 |
| 5 | 4000 | 279 | 4279 | 24 | 48 | 0 | 308 | 28 | 5 | 4 | 1485.8 |
| 6 | 4000 | 323 | 4323 | 25 | 56 | 0 | 332 | 29 | 5 | 5 | 1502.5 |
| 7 | 4000 | 292 | 4292 | 21 | 55 | 0 | 322 | 26 | 5 | 3 | 1447.2 |

Arms B and C were **not** retrained, so their training phases could not be re-instrumented for the same table. Their archive statistics from the runs that produced these checkpoints are carried in the JSON under `context.trained_arm_training_stats`.

## 5. Hand-priority definition

`score(a) = w . phi(a)`, where `phi` is the **same 52-dim per-action vector** `ActionFeatureExtractor` hands the two networks: `[action-type one-hot (10) | hashed label (32) | scalars (10)]`.

**All 32 label dimensions are weighted exactly zero** (measured L1 norm of the label-block weights: `0.0`), so the policy is provably invariant to renaming every control on the site. It cannot read a label, a URL or a page identity.

| scalar feature | weight | what it says |
|---|---|---|
| `is_newly_revealed` | +4.00 | a control that appeared in response to the previous action - the repository's own domain-general gated-flow progress signal |
| `is_form_control` | +1.50 | TYPE/SELECT: fill a form before trying to leave the page |
| `value_ordinal` | +1.00 | among generated values prefer a plausible one over a boundary or malformed one |
| `navigational` | +0.60 | expected to change the URL, i.e. forward progress |
| `in_viewport` | +0.30 | on screen; mirrors the registry's viewport-first priority |
| `has_href` | +0.10 | carries a link target at all (weak tie-breaker) |
| `opens_new_tab` | -1.00 | a popup leaves the flow |
| `is_fixed_action` | -2.00 | NO_OP/scroll/resize/back/forward/refresh cannot advance an application flow |
| `taken_this_episode` | -6.00 | already tried here this episode - what makes a deterministic argmax a search rather than a loop |
| `taken_here` | -3.00 | already tried here across the run |

| action type | weight |
|---|---|
| `CLICK` | +0.30 |
| `SELECT` | +0.30 |
| `TYPE` | +0.30 |
| `RAPID_CLICK` | -0.50 |
| `NO_OP` | -1.00 |
| `SCROLL` | +0.00 |
| `RESIZE_VIEWPORT` | -0.50 |
| `BROWSER_BACK` | +0.00 |
| `BROWSER_FORWARD` | -0.50 |
| `REFRESH` | -0.50 |

Ties: **lowest action index, which is action_registry.build_action_specs' own generic priority (viewport-first, then CLICK, TYPE(valid), SELECT, RAPID_CLICK, TYPE(edge cases), DOM order)**.

Verified by `tests/unit/test_hand_priority.py` (22 assertions): the label block is zero, a non-zero label weight is refused at construction, choices are unchanged when every control is relabelled, the module's executable source contains no fixture vocabulary, and the weights never move.

**Considered and deliberately not added**, because each was conceived after observing this fixture and adding it would have been benchmark-fitting:

- **A page-uniqueness ('control IDF') term** — demote an action identity that appears at many distinct states, since site chrome (nav bars, footers) appears everywhere while a page's own controls do not. It is generic, it is cheap, and it is a standard crawler idea. It was conceived *after* watching where Arm A stalls on this fixture, and tracing showed it would have promoted the stalling page's own controls. Adding it would therefore have been fitting the heuristic to the benchmark, which is exactly what this experiment exists to avoid. Recorded here instead of used.
- **A frontier term over unvisited href targets** — rejected for the same reason, and because reading a link's URL moves the policy toward responding to page identity, which the zero label weights exist to prevent.

## 6. Training protocol

- **_applies_to**: arms B and C, as run when their checkpoints were produced
- **train_steps**: 4000
- **episode_steps**: 40
- **seeds**: [0, 1, 2, 3, 4, 5, 6, 7]
- **p_return**: 0.5
- **exploration**: epsilon 1.0 -> 0.1 over 60% of training
- **n_step**: 3 for ac_dqn; forced to 1 for contextual_bandit (nothing to bootstrap)
- **encoders**: hash
- **judge**: disabled
- **source**: reports/postmarkov_baseline_pr050.json, reports/contextual_bandit_pr050.json
- **retrained_here**: False

## 7. Evaluation protocol

- **episodes_per_checkpoint**: 25
- **eval_epsilon**: 0.05
- **episode_steps**: 40
- **start**: landing page (index.html), no setup_actions
- **archive**: disabled at evaluation for every arm
- **encoders**: hash (default_encoders)
- **judge**: disabled (NullRewardModel)
- **checkpoint_selection**: FINAL
- **seed_scheme**: run_rollout's: env.reset(seed=seed + episode_index); each policy's epsilon RNG seeded once with the seed. Identical for all four rows.
- **action_feature_extractor**: fresh per row; verified bit-identical to a trained one because record_taken() is never called on the RL path
- **training_during_evaluation**: none - no optimiser is constructed, no buffer is written, and checkpoint SHA-256 is compared before and after

Checkpoints byte-identical after evaluation: `True`. Total episodes 800, wall clock 6.34 h.

## 8. Per-seed results

| arm | seed | done /25 | rate | review | S1 | mean R | median R | sd R | mean depth | max depth | uniq states | valid% | invalid | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hand_priority | 0 | **0** | 0.00 | 20 | 24 | +3.50 | +3.12 | 2.23 | 0.40 | 4 | 13.4 | 100 | 0 | none |
| hand_priority | 1 | **0** | 0.00 | 22 | 24 | +3.74 | +3.12 | 2.69 | 0.43 | 4 | 13.6 | 100 | 0 | none |
| hand_priority | 2 | **1** | 0.04 | 21 | 23 | +3.37 | +3.00 | 2.69 | 0.39 | 5 | 13.3 | 100 | 0 | none |
| hand_priority | 3 | **0** | 0.00 | 21 | 23 | +3.57 | +3.11 | 3.03 | 0.40 | 4 | 13.3 | 100 | 0 | none |
| hand_priority | 4 | **0** | 0.00 | 21 | 25 | +3.65 | +2.94 | 2.64 | 0.41 | 4 | 13.6 | 100 | 0 | none |
| hand_priority | 5 | **0** | 0.00 | 21 | 23 | +3.57 | +3.26 | 3.05 | 0.42 | 4 | 13.3 | 100 | 0 | none |
| hand_priority | 6 | **0** | 0.00 | 24 | 25 | +3.96 | +3.31 | 2.51 | 0.44 | 4 | 13.9 | 100 | 0 | none |
| hand_priority | 7 | **0** | 0.00 | 21 | 23 | +3.58 | +3.24 | 3.07 | 0.40 | 4 | 13.4 | 100 | 0 | none |
| hand_priority_search | 0 | **0** | 0.00 | 4 | 5 | -1.06 | -2.90 | 4.00 | 0.12 | 4 | 9.0 | 100 | 0 | none |
| hand_priority_search | 1 | **0** | 0.00 | 7 | 8 | -0.34 | -2.89 | 4.70 | 0.17 | 4 | 9.6 | 100 | 0 | none |
| hand_priority_search | 2 | **0** | 0.00 | 7 | 8 | -0.43 | -2.86 | 4.75 | 0.17 | 4 | 9.6 | 100 | 0 | none |
| hand_priority_search | 3 | **0** | 0.00 | 5 | 7 | -0.72 | -3.08 | 4.69 | 0.14 | 4 | 9.2 | 100 | 0 | none |
| hand_priority_search | 4 | **0** | 0.00 | 6 | 8 | -0.55 | -2.83 | 4.49 | 0.15 | 4 | 9.4 | 100 | 0 | none |
| hand_priority_search | 5 | **0** | 0.00 | 6 | 7 | -0.56 | -2.52 | 4.56 | 0.15 | 4 | 9.4 | 100 | 0 | none |
| hand_priority_search | 6 | **0** | 0.00 | 7 | 7 | -0.61 | -3.09 | 4.82 | 0.17 | 4 | 9.4 | 100 | 0 | none |
| hand_priority_search | 7 | **0** | 0.00 | 4 | 5 | -1.23 | -2.98 | 4.25 | 0.12 | 4 | 8.9 | 100 | 0 | none |
| contextual_bandit | 0 | **17** | 0.68 | 17 | 24 | +3.30 | +3.02 | 3.12 | 0.68 | 5 | 12.0 | 100 | 0 | none |
| contextual_bandit | 1 | **8** | 0.32 | 8 | 8 | -2.78 | -5.73 | 5.30 | 0.18 | 5 | 7.7 | 100 | 0 | none |
| contextual_bandit | 2 | **0** | 0.00 | 21 | 24 | +4.60 | +4.30 | 2.52 | 0.80 | 4 | 16.3 | 100 | 0 | none |
| contextual_bandit | 3 | **0** | 0.00 | 21 | 25 | +4.27 | +3.38 | 3.86 | 0.86 | 4 | 15.3 | 100 | 0 | none |
| contextual_bandit | 4 | **25** | 1.00 | 25 | 25 | +1.47 | +0.52 | 3.02 | 1.48 | 5 | 11.8 | 100 | 0 | none |
| contextual_bandit | 5 | **14** | 0.56 | 14 | 21 | +3.95 | +4.05 | 2.54 | 0.84 | 5 | 13.9 | 100 | 0 | none |
| contextual_bandit | 6 | **24** | 0.96 | 24 | 25 | +2.85 | +1.84 | 2.73 | 0.71 | 5 | 12.8 | 100 | 0 | none |
| contextual_bandit | 7 | **20** | 0.80 | 21 | 23 | +2.17 | +1.81 | 2.76 | 0.56 | 5 | 12.6 | 100 | 0 | none |
| ac_dqn | 0 | **24** | 0.96 | 25 | 25 | +5.06 | +3.67 | 3.65 | 1.55 | 5 | 17.2 | 100 | 0 | none |
| ac_dqn | 1 | **0** | 0.00 | 25 | 25 | +1.66 | +1.33 | 2.96 | 0.85 | 4 | 11.6 | 100 | 0 | none |
| ac_dqn | 2 | **25** | 1.00 | 25 | 25 | +3.67 | +2.91 | 2.66 | 2.11 | 5 | 15.5 | 100 | 0 | none |
| ac_dqn | 3 | **23** | 0.92 | 24 | 25 | +3.99 | +3.62 | 3.13 | 1.33 | 5 | 15.3 | 100 | 0 | none |
| ac_dqn | 4 | **1** | 0.04 | 1 | 25 | +3.41 | +2.97 | 2.20 | 0.81 | 5 | 13.5 | 100 | 0 | none |
| ac_dqn | 5 | **2** | 0.08 | 2 | 25 | +2.10 | +1.84 | 1.60 | 1.24 | 5 | 9.2 | 100 | 0 | none |
| ac_dqn | 6 | **24** | 0.96 | 24 | 25 | +2.86 | +2.02 | 2.40 | 0.86 | 5 | 13.4 | 100 | 0 | none |
| ac_dqn | 7 | **17** | 0.68 | 20 | 25 | +3.06 | +2.80 | 4.15 | 2.07 | 5 | 14.4 | 100 | 0 | none |

## 9. Arm-level statistics

The experimental unit is the **checkpoint / search replicate** (n=8 per arm). The bootstrap resamples those, never the 25 episodes inside one of them.

| arm | counts /25 | mean | median | sd | min | max | bootstrap 95% CI | >=1 | 25/25 | zero | pooled |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `hand_priority` | `[0, 0, 1, 0, 0, 0, 0, 0]` | 0.005 | 0.000 | 0.014 | 0.00 | 0.04 | [0.0, 0.015] | 1/8 | 0/8 | 7/8 | 1/200 |
| `hand_priority_search` | `[0, 0, 0, 0, 0, 0, 0, 0]` | 0.000 | 0.000 | 0.000 | 0.00 | 0.00 | [0.0, 0.0] | 0/8 | 0/8 | 8/8 | 0/200 |
| `contextual_bandit` | `[17, 8, 0, 0, 25, 14, 24, 20]` | 0.540 | 0.620 | 0.398 | 0.00 | 1.00 | [0.28, 0.79] | 6/8 | 1/8 | 2/8 | 108/200 |
| `ac_dqn` | `[24, 0, 25, 23, 1, 2, 24, 17]` | 0.580 | 0.800 | 0.458 | 0.00 | 1.00 | [0.27, 0.855] | 7/8 | 1/8 | 1/8 | 116/200 |

The `pooled` column is **descriptive only** — those episodes are correlated samples of one policy, not independent replicates.

| arm | mean review rate | mean S1 rate | mean reward | mean depth | mean uniq states | valid-action rate |
|---|---|---|---|---|---|---|
| `hand_priority` | 0.855 | 0.950 | +3.62 | 0.41 | 13.5 | 100.0% |
| `hand_priority_search` | 0.230 | 0.275 | -0.69 | 0.15 | 9.3 | 100.0% |
| `contextual_bandit` | 0.755 | 0.875 | +2.48 | 0.76 | 12.8 | 100.0% |
| `ac_dqn` | 0.730 | 1.000 | +3.23 | 1.35 | 13.8 | 100.0% |

## 10. A vs B

> Does learning immediate action value improve over a reasonable hand-designed action priority?

- `hand_priority` mean completion rate: **0.005** `[0.0, 0.0, 0.04, 0.0, 0.0, 0.0, 0.0, 0.0]`
- `contextual_bandit` mean completion rate: **0.540** `[0.68, 0.32, 0.0, 0.0, 1.0, 0.56, 0.96, 0.8]`
- difference (right - left): **+0.535**, bootstrap 95% CI `[0.27, 0.785]`
- CI excludes zero: **True**

**`contextual_bandit` is ahead of `hand_priority` by 0.535 on mean completion rate, 95% interval [+0.270, +0.785], which excludes zero. Eight replicates per arm on one fixture at one budget: a difference this experiment can see, not a general property of either algorithm.**

_Secondary reading (search mode): `contextual_bandit` is ahead of `hand_priority_search` by 0.540 on mean completion rate, 95% interval [+0.280, +0.790], which excludes zero. Eight replicates per arm on one fixture at one budget: a difference this experiment can see, not a general property of either algorithm._

## 11. B vs C

> Does bootstrapped sequential value learning improve over myopic immediate-reward prediction?

- `contextual_bandit` mean completion rate: **0.540** `[0.68, 0.32, 0.0, 0.0, 1.0, 0.56, 0.96, 0.8]`
- `ac_dqn` mean completion rate: **0.580** `[0.96, 0.0, 1.0, 0.92, 0.04, 0.08, 0.96, 0.68]`
- difference (right - left): **+0.040**, bootstrap 95% CI `[-0.36, 0.425]`
- CI excludes zero: **False**
- paired by training seed (both arms trained one run per seed under the same protocol): mean difference +0.040, per seed `[0.28, -0.32, 1.0, 0.92, -0.96, -0.48, 0.0, -0.12]`, 3 seeds favour `ac_dqn` / 4 favour `contextual_bandit`

**INCONCLUSIVE. `ac_dqn` is +0.040 on mean completion rate, but the 95% interval [-0.360, +0.425] contains zero. At 8 replicates per arm this experiment cannot separate them.**

## 12. A vs C

> Does the full AC-DQN improve over a hand-priority search? (read only in light of sections 10 and 11)

- `hand_priority` mean completion rate: **0.005** `[0.0, 0.0, 0.04, 0.0, 0.0, 0.0, 0.0, 0.0]`
- `ac_dqn` mean completion rate: **0.580** `[0.96, 0.0, 1.0, 0.92, 0.04, 0.08, 0.96, 0.68]`
- difference (right - left): **+0.575**, bootstrap 95% CI `[0.265, 0.845]`
- CI excludes zero: **True**

**`ac_dqn` is ahead of `hand_priority` by 0.575 on mean completion rate, 95% interval [+0.265, +0.845], which excludes zero. Eight replicates per arm on one fixture at one budget: a difference this experiment can see, not a general property of either algorithm.**

_Secondary reading (search mode): `ac_dqn` is ahead of `hand_priority_search` by 0.580 on mean completion rate, 95% interval [+0.265, +0.855], which excludes zero. Eight replicates per arm on one fixture at one budget: a difference this experiment can see, not a general property of either algorithm._

## 13. Failure and anomaly analysis

No run crashed, hung, selected an invalid action, or was excluded.

- runs requested: 32, completed: 32, excluded: **0**
- invalid action selections across every arm: **0**
- archive returns failed during Arm A's search: 5 of 386
- checkpoints modified by evaluation: **0**

No seed was rerun, dropped, or tuned against. No result was excluded.

## 13b. Where the measured gap lives (post hoc)

> POST HOC. Written after seeing the results, computed from the recorded episodes, nothing rerun and no arm changed. Read it as a description of where the measured gap sits, not as a pre-registered test.

completion = (reached the review page) x (passed the decision on it). An arm can lose on either factor, and the two say different things.

| arm | reaches review | completes **given** it reached review | completion rate |
|---|---|---|---|
| `hand_priority` | 0.855 | 0.006 | 0.005 |
| `hand_priority_search` | 0.230 | 0.000 | 0.000 |
| `contextual_bandit` | 0.755 | 0.744 | 0.540 |
| `ac_dqn` | 0.730 | 0.846 | 0.580 |

**Mechanism.** On the review page every candidate is a navigational, in-viewport link carrying an href, so every feature the hand priority is allowed to read is identical across them. The one thing that separates the control that completes the order from the one that returns to the home page is the control's *label*, and the label block is weighted exactly zero by construction. The hand priority is therefore structurally indifferent at precisely this decision — which is the same decision `reports/trained_action_q_diagnostic.md` independently identified as the deadlock for the trained arms.

**What it implies.** If the gap between Arm A and the learned arms is concentrated in this one decision rather than spread over the flow, then what the learned arms are buying on this benchmark is the ability to attach value to a particular control's identity - representation learning - and not sequential planning. B vs C is the comparison that speaks to planning, and it is inconclusive.

## 14. Limitations

- **Arm A's eight rows are not eight training runs.** A non-learning policy has nothing to vary across seeds except its epsilon stream, so Arm A's spread understates what a spread means for Arms B and C, whose rows carry genuine training variance. Its bootstrap interval is correspondingly narrow for a reason that is not a property of the algorithm.
- **Arm A reads two features that are structurally dead for Arms B and C.** `taken_here` and `taken_this_episode` are written only by `ActionFeatureExtractor.record_taken()`, which nothing on the RL path calls, so they are identically 0.0 in every observation the two networks have ever seen. Arm A calls it and reads them. This is an information advantage for Arm A; removing it would not level the comparison, it would delete the search, since a memoryless deterministic argmax repeats one action until truncation.
- **Arms B and C were not retrained.** Their finalized checkpoints were re-evaluated contemporaneously with Arm A. That keeps the checkpoints every published figure refers to intact and puts all rows in one harness state, but it means their training phase could not be re-instrumented for the search-metrics table.
- **The browser harness is intermittently non-deterministic on this configuration** (`reports/diag_harness_determinism.json`): two identical runs at one seed usually agree bit-for-bit and occasionally do not. Every row here is a single run.
- **n=8 per arm on one fixture at one budget.** Eight is a small sample. Nothing here generalises to another application, another step budget, or another reward.
- **Per-episode seeds overlap across rows** (row k uses env seeds k..k+24). This is the original experiment's scheme, reused unchanged and identical across all four rows.
- **One statistic per comparison, fixed in advance.** Difference in mean checkpoint completion rate with a bootstrap interval over replicates. No post-hoc test was run and none was selected after seeing the data.
- **Completion is a proxy for the research objective.** Reaching receipt.html is what makes DEEP-01 findable; it is not itself finding it. The deterministic ceiling on this fixture is 0, so no arm's `distinct_findings` is a bug-discovery figure.

## 15. Interpretation

**Ranking by mean checkpoint completion rate:** `ac_dqn` 0.580, `contextual_bandit` 0.540, `hand_priority` 0.005, `hand_priority_search` 0.000.

**1 of the three primary comparisons is inconclusive at this sample size** (B_vs_C). An inconclusive result is not evidence that the two arms are equal; it is evidence that eight replicates per arm cannot separate them.

**On the rung that matters most — B vs C — this experiment does not detect a contribution from bootstrapping.** Under matched infrastructure (same observation, action space, mask, reward, archive, PER buffer, network shape, epsilon schedule and budget), replacing the bootstrapped target with the immediate reward does not produce a difference this design can see. That is consistent with the four preceding null results and with the 25-episode re-evaluation, and it is a statement about *this* AC-DQN on *this* benchmark at *this* budget, not about temporal-difference learning.

**AC-DQN is ahead of the hand-priority floor.** Check whether that lead is carried by the eight checkpoints or by two of them before calling it a property of the algorithm: section 9's per-seed counts, not the mean, are what settle that.

**The hand priority is not losing because it cannot explore.** It reaches the final review page in 85.5% of episodes — more often than either learned arm (75.5% at best) — and then completes 0.6% of the time from there, against 84.6% for AC-DQN. The whole measured gap sits in one decision, not in the four gates before it. See section 13b, which is post hoc.

**What this cannot establish.** It does not isolate PER, dueling, Double DQN, n-step replay, the archive or the target network — all shared, all cancelling. It does not measure bug discovery, since the fixture's deterministic ceiling is 0. And it does not measure transfer, which is the property the action-conditioned representation exists for and which a single-fixture benchmark cannot see.

## 16. Exact commands

```bash
python -m pytest tests/unit/test_hand_priority.py tests/unit/test_contextual_bandit.py tests/unit/test_dueling.py tests/unit/test_action_features.py tests/unit/test_go_explore.py tests/unit/test_archive_start.py -q
python scripts/search_vs_bandit_vs_ac_dqn.py --seeds 0 1 2 3 4 5 6 7
```

## 17. Runtime

- search phase: 3.29 h (11827 s)
- evaluation phase: 3.05 h (10968 s)
- **total: 6.34 h**
