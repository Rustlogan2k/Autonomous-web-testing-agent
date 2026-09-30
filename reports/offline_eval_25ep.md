# Offline re-evaluation at 25 episodes

## 1. Objective

Determine whether the completion-distribution difference seen at 5 evaluation episodes between AC-DQN and the contextual bandit survives a five-fold longer evaluation of the **same trained checkpoints**. No training was performed.

## 2. Evaluation protocol

- **episodes_per_checkpoint**: 25
- **eval_epsilon**: 0.05
- **episode_steps**: 40
- **start**: landing page (index.html), no setup_actions
- **archive**: disabled at evaluation for both arms
- **encoders**: hash (default_encoders)
- **judge**: disabled (NullRewardModel, as in the original runs)
- **checkpoint_selection**: FINAL, matching the original experiments' primary result
- **seed_scheme**: reused from run_rollout: env.reset(seed=training_seed + episode_index); TrainedPolicy epsilon RNG seeded once with training_seed. Identical for both arms.
- **action_feature_extractor**: fresh per checkpoint; verified bit-identical to a trained one because record_taken() is never called

All 16 checkpoints verified byte-identical after evaluation: `True`. Wall clock 98.7 min, 400 episodes total.

## 3. Checkpoint table

| arm | seed | completions /25 | rate | review | S1 | mean reward | mean depth | max depth | uniq states | valid% |
|---|---|---|---|---|---|---|---|---|---|---|
| ac_dqn | 0 | **24** | 0.96 | 25 | 25 | +5.06 | 1.55 | 5 | 17.2 | 100 |
| ac_dqn | 1 | **0** | 0.00 | 25 | 25 | +1.67 | 0.85 | 4 | 11.6 | 100 |
| ac_dqn | 2 | **25** | 1.00 | 25 | 25 | +3.67 | 2.11 | 5 | 15.5 | 100 |
| ac_dqn | 3 | **23** | 0.92 | 24 | 25 | +3.99 | 1.33 | 5 | 15.3 | 100 |
| ac_dqn | 4 | **1** | 0.04 | 1 | 25 | +3.41 | 0.81 | 5 | 13.5 | 100 |
| ac_dqn | 5 | **2** | 0.08 | 2 | 25 | +2.09 | 1.24 | 5 | 9.2 | 100 |
| ac_dqn | 6 | **24** | 0.96 | 24 | 25 | +2.86 | 0.86 | 5 | 13.4 | 100 |
| ac_dqn | 7 | **17** | 0.68 | 20 | 25 | +3.06 | 2.07 | 5 | 14.4 | 100 |
| contextual_bandit | 0 | **17** | 0.68 | 17 | 24 | +3.30 | 0.68 | 5 | 12.0 | 100 |
| contextual_bandit | 1 | **8** | 0.32 | 8 | 8 | -2.78 | 0.18 | 5 | 7.7 | 100 |
| contextual_bandit | 2 | **0** | 0.00 | 21 | 24 | +4.60 | 0.80 | 4 | 16.3 | 100 |
| contextual_bandit | 3 | **0** | 0.00 | 21 | 25 | +4.27 | 0.86 | 4 | 15.3 | 100 |
| contextual_bandit | 4 | **25** | 1.00 | 25 | 25 | +1.42 | 1.49 | 5 | 11.8 | 100 |
| contextual_bandit | 5 | **14** | 0.56 | 14 | 21 | +3.95 | 0.84 | 5 | 13.9 | 100 |
| contextual_bandit | 6 | **24** | 0.96 | 24 | 25 | +2.85 | 0.70 | 5 | 12.8 | 100 |
| contextual_bandit | 7 | **20** | 0.80 | 21 | 23 | +2.11 | 0.58 | 5 | 12.4 | 100 |

## 4. Per-arm summary

| arm | counts /25 | mean rate | median | sd | bootstrap 95% CI | >=1 | all-25 | zero |
|---|---|---|---|---|---|---|---|---|
| ac_dqn | `[24, 0, 25, 23, 1, 2, 24, 17]` | 0.580 | 0.800 | 0.458 | [0.27, 0.855] | 7/8 | 1/8 | 1/8 |
| contextual_bandit | `[17, 8, 0, 0, 25, 14, 24, 20]` | 0.540 | 0.620 | 0.398 | [0.28, 0.79] | 6/8 | 1/8 | 2/8 |

The bootstrap resamples **checkpoints**, not episodes. Pooled episode totals are reported in the JSON as descriptive only.

## 5. Completion-rate distribution

| arm | near 0% | near 50% | near 100% | other intermediate | **extreme** | **intermediate** |
|---|---|---|---|---|---|---|
| ac_dqn | 3 | 0 | 4 | 1 | **7/8** | **1/8** |
| contextual_bandit | 2 | 1 | 2 | 3 | **4/8** | **4/8** |

## 6. 5-episode vs 25-episode (descriptive)

| arm | 5-episode counts | 5-ep rates | 25-episode counts | 25-ep rates |
|---|---|---|---|---|
| ac_dqn | `[5, 0, 5, 5, 0, 0, 5, 5]` | `[1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 1.0]` | `[24, 0, 25, 23, 1, 2, 24, 17]` | `[0.96, 0.0, 1.0, 0.92, 0.04, 0.08, 0.96, 0.68]` |
| contextual_bandit | `[5, 2, 0, 0, 5, 1, 4, 3]` | `[1.0, 0.4, 0.0, 0.0, 1.0, 0.2, 0.8, 0.6]` | `[17, 8, 0, 0, 25, 14, 24, 20]` | `[0.68, 0.32, 0.0, 0.0, 1.0, 0.56, 0.96, 0.8]` |

## 7. Seed-1 archive forensic inspection

- log file present: `True`
- matching log lines found: 31
- reported failed returns for contextual_bandit seed 1: 7

## 8. Interpretation

_See the accompanying analysis; the numbers above are the evidence._

## 9. Limitations

- The experimental unit is the checkpoint (n=8 per arm); 25 episodes deepen each estimate but do not add training runs.
- Episode-to-episode variation comes almost entirely from the policy's epsilon stream; the fixture is static, so the env seed does little.
- Per-episode seeds overlap across training seeds (seed k uses k..k+24). This is the original experiment's scheme, reused unchanged and identical across arms.
- Single fixture, single budget. Nothing here generalises to another application.

## 10. Exact command

```bash
python scripts/offline_eval_25ep.py
```
