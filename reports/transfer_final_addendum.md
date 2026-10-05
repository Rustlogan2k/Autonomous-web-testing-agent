# Transfer experiment — addendum to `transfer_final.md`

`transfer_final.md` / `.json` are the output of the frozen analysis
(`scripts/analyze_transfer.py runs/final-merged --dev-apps shop_catalog booking_desk`),
unchanged. This addendum records three things that report does not.

## 1. Completeness and provenance

- 291 results merged from `runs/final-local` (105 jobs: 78 planned + 27 added by the top-up
  rule) and `runs/final-cpu-1..4` (186 jobs: 180 planned + 6 bandit top-up); 0 failed jobs,
  0 infrastructure exclusions; identical `ExperimentConfig` in all five directories; every
  job ran from a clean tree.
- Jobs record three commits — `1fef49d` (6 local pretrainings), `c10699c` (all 186 AWS jobs
  and 6 local), `811094a` (93 local jobs after the resume). They differ only in documents
  and in the fix that makes cloud workers record their commit (`5ec9b3c`); nothing a job
  computes differs between them.
- The local half was suspended by Windows Modern Standby on 2026-10-04 (12/78 done) and
  resumed on 2026-10-05; finished jobs were kept, interrupted ones restarted from scratch.

## 2. A pre-registered comparison the script did not list

Amendment 2 §4 names `dqn_scratch − dqn_scratch_nollm` (the LLM reward's effect on a
learner without pretraining) as a secondary, descriptive comparison, but
`analyze_transfer.py`'s comparison table was not updated to include it. Computed with the
same functions (`paired_fold_difference`, `stratified_logrank`) on the same runs:

| comparison | folds | median Δ | folds favouring | log-rank p | per fold |
|---|---|---|---|---|---|
| dqn_scratch − dqn_scratch_nollm | all 6 | −57 | 3/6 | 0.62 | account_hub −114, admin_records 0, booking_desk 0, media_library +137, shop_catalog −170, support_portal −469 |
| dqn_scratch − dqn_scratch_nollm | pristine 4 | −57 | 2/4 | 0.72 | as above, pristine folds only |

## 3. Reading the results within the recorded claim limits (`PROJECT_CONTEXT.md` §0.4)

- **Primary (DQN transfer, `dqn_pretrained − dqn_scratch`)**: median Δ +0 over all folds
  (95% CI −749 to +290; 2/6 folds favour pretraining; log-rank p = 0.53) and −310 over the
  pristine folds (CI −879 to +469; 2/4; p = 0.66). **No evidence that pretraining speeds up
  verified-fault discovery for the DQN.** Two folds (admin_records, booking_desk) are
  uninformative for this pair: no DQN run on either found a fault in 2,000 interactions
  (0/6 each, including top-up seeds), so their difference is 0 by censoring.
- **Pre-registered C − B (bandit transfer)**: −280 over all folds (CI −732 to +188; 4/6; p =
  0.086) and −280 pristine (CI −1096 to +376; 3/4; p = 0.11). A direction favouring
  pretraining that the intervals do not resolve; reported as inconclusive.
- **Learned vs non-learning**: the learners confirm faults in more runs (discovery rate
  0.33 for both DQN arms, 0.42 pretrained bandit) than search_S (0.17), random (0.11) and
  search_A0 (0.06); `dqn_pretrained` vs search_A0 log-rank p = 0.006 and vs random p = 0.02
  over all folds, not significant on the pristine folds alone (p = 0.23, 0.53). The
  learned-vs-search advantage is the clearest effect in the experiment, and it is carried
  largely by the development fold shop_catalog (DQN 6/6 vs search_S 1/3).
- **Dynamic LLM reward**: no detectable benefit in either ablation (`dqn_pretrained −
  dqn_pretrained_nollm` +17, p = 0.75; pristine +175, 1/4 folds favour the LLM arm;
  `dqn_scratch − dqn_scratch_nollm` −57, p = 0.62). Per claim limit C, the experiment does
  not show that the LLM reward helps.
- **Online adaptation**: `dqn_pretrained − dqn_zeroshot` −84 (p = 0.53), not resolved.
- **Oracle soundness**: 0 verified findings on any clean build (2 raw assertion firings,
  both rejected by the per-fault control).
- With 4–6 folds and ≤ 6 seeds per fold the intervals are wide; per claim limit B these are
  within-family results on six designer-built applications, not general web
  generalization. Per claim limit A, nothing here bears on contextual learning; that is
  the frozen contextual diagnostic's question.
