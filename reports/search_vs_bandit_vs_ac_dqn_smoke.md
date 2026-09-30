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
- **arm_A_env_steps**: 80
- **arm_B_C_train_steps**: 4000
- **matched**: False
- **episodes_per_search**: 2
- **archive**: ArchiveStartWrapper, p_return=0.5, same as the trained arms
- **route_replay**: outside the step budget (as in archive_start.py) and reported separately as replayed_actions / browser_actions_total
- **epsilon_schedule**: 1.0 -> 0.1 over 60% of the budget, identical to the trained arms
- **why_fair**: both arms are given the same number of environment interactions, the same archive at the same p_return, and the same exploration schedule; browser actions spent on route replay are reported rather than absorbed, so neither side gets free actions
- **what_arm_A_cannot_carry_forward**: nothing. A non-learning policy has no parameters to update, and the archive is disabled at evaluation for every arm. The search phase therefore measures coverage at a matched budget, not a trainable asset.

| seed | env steps | replayed | browser actions | archive cells | returns | failed | reanchored | states | max depth | completions/100 ep | runtime (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 80 | 0 | 80 | 13 | 0 | 0 | 6 | 0 | 3 | 0 | 42.0 |

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

- **episodes_per_checkpoint**: 3
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

Checkpoints byte-identical after evaluation: `True`. Total episodes 12, wall clock 0.08 h.

## 8. Per-seed results

| arm | seed | done /25 | rate | review | S1 | mean R | median R | sd R | mean depth | max depth | uniq states | valid% | invalid | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hand_priority | 0 | **0** | 0.00 | 1 | 3 | +7.11 | +8.62 | 3.00 | 0.39 | 4 | 12.0 | 100 | 0 | none |
| hand_priority_search | 0 | **0** | 0.00 | 1 | 2 | +5.35 | +8.69 | 6.11 | 0.27 | 4 | 10.7 | 100 | 0 | none |
| contextual_bandit | 0 | **3** | 1.00 | 3 | 3 | +8.90 | +7.89 | 1.95 | 0.72 | 5 | 13.0 | 100 | 0 | none |
| ac_dqn | 0 | **3** | 1.00 | 3 | 3 | +13.21 | +11.76 | 5.27 | 1.78 | 5 | 19.3 | 100 | 0 | none |

## 9. Arm-level statistics

The experimental unit is the **checkpoint / search replicate** (n=8 per arm). The bootstrap resamples those, never the 25 episodes inside one of them.

| arm | counts /25 | mean | median | sd | min | max | bootstrap 95% CI | >=1 | 25/25 | zero | pooled |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `hand_priority` | `[0]` | 0.000 | 0.000 | 0.000 | 0.00 | 0.00 | [0.0, 0.0] | 0/1 | 0/1 | 1/1 | 0/3 |
| `hand_priority_search` | `[0]` | 0.000 | 0.000 | 0.000 | 0.00 | 0.00 | [0.0, 0.0] | 0/1 | 0/1 | 1/1 | 0/3 |
| `contextual_bandit` | `[3]` | 1.000 | 1.000 | 0.000 | 1.00 | 1.00 | [1.0, 1.0] | 1/1 | 1/1 | 0/1 | 3/3 |
| `ac_dqn` | `[3]` | 1.000 | 1.000 | 0.000 | 1.00 | 1.00 | [1.0, 1.0] | 1/1 | 1/1 | 0/1 | 3/3 |

The `pooled` column is **descriptive only** — those episodes are correlated samples of one policy, not independent replicates.

| arm | mean review rate | mean S1 rate | mean reward | mean depth | mean uniq states | valid-action rate |
|---|---|---|---|---|---|---|
| `hand_priority` | 0.333 | 1.000 | +7.11 | 0.39 | 12.0 | 100.0% |
| `hand_priority_search` | 0.333 | 0.667 | +5.35 | 0.27 | 10.7 | 100.0% |
| `contextual_bandit` | 1.000 | 1.000 | +8.90 | 0.72 | 13.0 | 100.0% |
| `ac_dqn` | 1.000 | 1.000 | +13.21 | 1.78 | 19.3 | 100.0% |

## 10. A vs B

> Does learning immediate action value improve over a reasonable hand-designed action priority?

- `hand_priority` mean completion rate: **0.000** `[0.0]`
- `contextual_bandit` mean completion rate: **1.000** `[1.0]`
- difference (right - left): **+1.000**, bootstrap 95% CI `[1.0, 1.0]`
- CI excludes zero: **True**

**`contextual_bandit` is ahead of `hand_priority` by 1.000 on mean completion rate, 95% interval [+1.000, +1.000], which excludes zero. Eight replicates per arm on one fixture at one budget: a difference this experiment can see, not a general property of either algorithm.**

_Secondary reading (search mode): `contextual_bandit` is ahead of `hand_priority_search` by 1.000 on mean completion rate, 95% interval [+1.000, +1.000], which excludes zero. Eight replicates per arm on one fixture at one budget: a difference this experiment can see, not a general property of either algorithm._

## 11. B vs C

> Does bootstrapped sequential value learning improve over myopic immediate-reward prediction?

- `contextual_bandit` mean completion rate: **1.000** `[1.0]`
- `ac_dqn` mean completion rate: **1.000** `[1.0]`
- difference (right - left): **+0.000**, bootstrap 95% CI `[0.0, 0.0]`
- CI excludes zero: **False**
- paired by training seed (both arms trained one run per seed under the same protocol): mean difference +0.000, per seed `[0.0]`, 0 seeds favour `ac_dqn` / 0 favour `contextual_bandit`

**INCONCLUSIVE. `ac_dqn` is +0.000 on mean completion rate, but the 95% interval [+0.000, +0.000] contains zero. At 8 replicates per arm this experiment cannot separate them.**

## 12. A vs C

> Does the full AC-DQN improve over a hand-priority search? (read only in light of sections 10 and 11)

- `hand_priority` mean completion rate: **0.000** `[0.0]`
- `ac_dqn` mean completion rate: **1.000** `[1.0]`
- difference (right - left): **+1.000**, bootstrap 95% CI `[1.0, 1.0]`
- CI excludes zero: **True**

**`ac_dqn` is ahead of `hand_priority` by 1.000 on mean completion rate, 95% interval [+1.000, +1.000], which excludes zero. Eight replicates per arm on one fixture at one budget: a difference this experiment can see, not a general property of either algorithm.**

_Secondary reading (search mode): `ac_dqn` is ahead of `hand_priority_search` by 1.000 on mean completion rate, 95% interval [+1.000, +1.000], which excludes zero. Eight replicates per arm on one fixture at one budget: a difference this experiment can see, not a general property of either algorithm._

## 13. Failure and anomaly analysis

No run crashed, hung, selected an invalid action, or was excluded.

- runs requested: 4, completed: 4, excluded: **0**
- invalid action selections across every arm: **0**
- archive returns failed during Arm A's search: 0 of 0
- checkpoints modified by evaluation: **0**

No seed was rerun, dropped, or tuned against. No result was excluded.

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

**Ranking by mean checkpoint completion rate:** `contextual_bandit` 1.000, `ac_dqn` 1.000, `hand_priority` 0.000, `hand_priority_search` 0.000.

**1 of the three primary comparisons are inconclusive at this sample size** (B_vs_C). An inconclusive result is not evidence that the two arms are equal; it is evidence that eight replicates per arm cannot separate them.

**On the rung that matters most — B vs C — this experiment does not detect a contribution from bootstrapping.** Under matched infrastructure (same observation, action space, mask, reward, archive, PER buffer, network shape, epsilon schedule and budget), replacing the bootstrapped target with the immediate reward does not produce a difference this design can see. That is consistent with the four preceding null results and with the 25-episode re-evaluation, and it is a statement about *this* AC-DQN on *this* benchmark at *this* budget, not about temporal-difference learning.

**AC-DQN is ahead of the hand-priority floor.** Check whether that lead is carried by the eight checkpoints or by two of them before calling it a property of the algorithm: section 9's per-seed counts, not the mean, are what settle that.

**What this cannot establish.** It does not isolate PER, dueling, Double DQN, n-step replay, the archive or the target network — all shared, all cancelling. It does not measure bug discovery, since the fixture's deterministic ceiling is 0. And it does not measure transfer, which is the property the action-conditioned representation exists for and which a single-fixture benchmark cannot see.

## 16. Exact commands

```bash
python -m pytest tests/unit/test_hand_priority.py tests/unit/test_contextual_bandit.py tests/unit/test_dueling.py tests/unit/test_action_features.py tests/unit/test_go_explore.py tests/unit/test_archive_start.py -q
python scripts/search_vs_bandit_vs_ac_dqn.py --seeds 0
```

## 17. Runtime

- search phase: 0.01 h (42 s)
- evaluation phase: 0.06 h (224 s)
- **total: 0.08 h**
