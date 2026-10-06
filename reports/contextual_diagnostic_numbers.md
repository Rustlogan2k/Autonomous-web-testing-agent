# Contextual-generalization diagnostic: results

Secondary diagnostic, pre-registered in `docs/methodology/2026-10-04-contextual-diagnostic-preregistration.md`. Nothing here is pooled with the primary experiment.

## Permutation invariance (diagnostic 4)

100 probe states × 20 permutations, tolerance 1e-05: **passed**.

## Hypotheses (rules applied to `ctx_full`)

- **H-act: inconclusive**
  - decoy probe rate >= 0.50: 0.3089 [0.0, 0.9131] → unresolved
  - within-label AUROC interval includes 0.50: 0.5148 [0.4446, 0.5852] → holds
- **H-lex: inconclusive**
  - branch 1: min(top-1 VU1, VU2) / training < 0.50: 0.8767 [0.3454, 1.1785] → unresolved
  - branch 1: top-1 ctx_v0_s3_d0 / training >= 0.70: 0.3461 [0.0501, 0.7533] → unresolved
  - branch 1: top-1 ctx_v0_s4_d0 / training >= 0.70: 0.5768 [0.2669, 0.8902] → unresolved
  - branch 1: top-1 ctx_v0_s5_d0 / training >= 0.70: 0.965 [0.5656, 1.4853] → holds
  - branch 2: ctx_full - lexical_only training top-1: interval includes 0: 0.1103 [-0.0794, 0.3117] → holds
  - branch 2: ctx_full - lexical_only top-1 on VU2 <= 0: 0.1641 [-0.1253, 0.4615] → unresolved
- **H-app: inconclusive**
  - mean top-1 S3-S5 / training < 0.50: 0.6293 [0.443, 0.8786] → unresolved
  - top-1 VS / training >= 0.70: 1.0843 [0.704, 1.6115] → holds
- **H-ctx: not supported**
  - within-label AUROC >= 0.70: 0.5148 [0.4446, 0.5852] → fails
  - within-label AUROC interval lower bound > 0.50: 0.5148 [0.4446, 0.5852] → unresolved
  - decoy probe rate <= 0.25: 0.3089 [0.0, 0.9131] → unresolved
  - lexical_only decoy rate - ctx_full decoy rate >= 0.25: -0.3089 [-0.9131, 0.0] → fails
  - top-1 VS / training >= 0.70: 1.0843 [0.704, 1.6115] → holds
  - top-1 ctx_v0_s3_d0 / training >= 0.70: 0.3461 [0.0501, 0.7533] → unresolved
  - top-1 ctx_v0_s4_d0 / training >= 0.70: 0.5768 [0.2669, 0.8902] → unresolved
  - top-1 ctx_v0_s5_d0 / training >= 0.70: 0.965 [0.5656, 1.4853] → holds
  - action_only decoy rate - ctx_full decoy rate > 0 (interval): -0.3089 [-0.9131, 0.0] → fails
  - ctx_full - action_only within-label AUROC > 0 (interval): 0.0232 [-0.0924, 0.147] → unresolved
  - lexical_only decoy rate - ctx_full decoy rate > 0 (interval): -0.3089 [-0.9131, 0.0] → fails
  - ctx_full - lexical_only within-label AUROC > 0 (interval): 0.0103 [-0.1176, 0.156] → unresolved

## Probe metrics by arm

| arm | train top-1 | within-label AUROC | decoy rate | τ(agree) − τ(differ) |
|---|---|---|---|---|
| ctx_full | 0.5557 | 0.5148 [0.4446, 0.5852] | 0.3089 [0.0, 0.9131] | 0.1377 [0.0997, 0.183] |
| action_only | 0.4938 | 0.4916 [0.4171, 0.5727] | 0.0 [0.0, 0.0] | 0.0 [0.0, 0.0] |
| lexical_only | 0.4454 | 0.5045 [0.4068, 0.6003] | 0.0 [0.0, 0.0] | 0.0 [-0.0001, 0.0002] |
| label_blind | 0.4002 | 0.5629 [0.4992, 0.624] | 0.2304 [0.0541, 0.4059] | 0.1198 [0.0334, 0.1927] |
| search_S | 0.309 | 0.6213 [0.5774, 0.6627] | 0.3171 [0.1951, 0.4359] | 0.1226 [0.0995, 0.1469] |
| random | 0.1842 | 0.4986 [0.4663, 0.5312] | 0.1301 [0.0541, 0.2222] | -0.0079 [-0.0276, 0.0118] |

### Top-1 progress accuracy by variant

| arm | ctx_v0_s1_d0 | ctx_v0_s2_d0 | ctx_v1_s1_d0 | ctx_v1_s2_d0 | ctx_vs_s1_d0 | ctx_vu1_s1_d0 | ctx_vu2_s1_d0 | ctx_v0_s3_d0 | ctx_v0_s4_d0 | ctx_v0_s5_d0 | ctx_v0_s1_d1 | ctx_vu1_s5_d1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ctx_full | 0.5769 | 0.6092 | 0.5769 | 0.4598 | 0.6026 | 0.4872 | 0.5256 | 0.1923 | 0.3205 | 0.5362 | 0.4487 | 0.3043 |
| action_only | 0.3462 | 0.5517 | 0.5256 | 0.5517 | 0.3462 | 0.4359 | 0.5641 | 0.3718 | 0.3462 | 0.3768 | 0.4231 | 0.4348 |
| lexical_only | 0.3513 | 0.5448 | 0.359 | 0.5264 | 0.3564 | 0.3641 | 0.3615 | 0.2872 | 0.3 | 0.3884 | 0.3513 | 0.3768 |
| label_blind | 0.4329 | 0.3674 | 0.4329 | 0.3674 | 0.4329 | 0.4329 | 0.4329 | 0.3915 | 0.4286 | 0.4314 | 0.3312 | 0.3092 |
| search_S | 0.3462 | 0.2759 | 0.2692 | 0.3448 | 0.3077 | 0.2692 | 0.3077 | 0.6923 | 0.5385 | 0.3043 | 0.3077 | 0.2174 |
| random | 0.1795 | 0.1724 | 0.1667 | 0.2184 | 0.1154 | 0.1282 | 0.1538 | 0.0769 | 0.1282 | 0.2029 | 0.1026 | 0.1159 |

## Hunts (diagnostics 5-7)

| app | arm | runs | RMST | found | vs search_S | vs random | decoys/episode |
|---|---|---|---|---|---|---|---|
| account_hub | action_only | 3 | 2000.0 | 0/3 | 1.425 | 1.0 | 0.0 |
| account_hub | ctx_full | 3 | 522.0 | 3/3 | 0.372 | 0.261 | 0.0 |
| account_hub | label_blind | 3 | 2000.0 | 0/3 | 1.425 | 1.0 | 0.0 |
| account_hub | lexical_only | 3 | 1582.7 | 1/3 | 1.127 | 0.791 | 0.0 |
| account_hub | random | 3 | 2000.0 | 0/3 | 1.425 |  | 0.0 |
| account_hub | search_S | 3 | 1404.0 | 1/3 |  | 0.702 | 0.0 |
| admin_records | action_only | 3 | 2000.0 | 0/3 | 1.0 | 1.0 | 0.0 |
| admin_records | ctx_full | 3 | 1444.7 | 1/3 | 0.722 | 0.722 | 0.0 |
| admin_records | label_blind | 3 | 2000.0 | 0/3 | 1.0 | 1.0 | 0.0 |
| admin_records | lexical_only | 3 | 2000.0 | 0/3 | 1.0 | 1.0 | 0.0 |
| admin_records | random | 3 | 2000.0 | 0/3 | 1.0 |  | 0.0 |
| admin_records | search_S | 3 | 2000.0 | 0/3 |  | 1.0 | 0.0 |
| ctx_v0_s1_d1 | action_only | 3 | 1000.0 | 0/3 | 1.0 | 1.399 | 3.24 |
| ctx_v0_s1_d1 | ctx_full | 3 | 382.7 | 2/3 | 0.383 | 0.535 | 2.6133 |
| ctx_v0_s1_d1 | label_blind | 3 | 94.3 | 3/3 | 0.094 | 0.132 | 3.32 |
| ctx_v0_s1_d1 | lexical_only | 3 | 365.3 | 2/3 | 0.365 | 0.511 | 2.1067 |
| ctx_v0_s1_d1 | random | 3 | 714.7 | 1/3 | 0.715 |  | 1.4533 |
| ctx_v0_s1_d1 | search_S | 3 | 1000.0 | 0/3 |  | 1.399 | 2.7333 |
| ctx_v0_s3_d0 | action_only | 3 | 719.3 | 1/3 | 0.949 | 1.042 | 0.0 |
| ctx_v0_s3_d0 | ctx_full | 3 | 381.3 | 3/3 | 0.503 | 0.553 | 0.0 |
| ctx_v0_s3_d0 | label_blind | 3 | 928.7 | 1/3 | 1.225 | 1.346 | 0.0 |
| ctx_v0_s3_d0 | lexical_only | 3 | 1000.0 | 0/3 | 1.319 | 1.449 | 0.0 |
| ctx_v0_s3_d0 | random | 3 | 690.0 | 1/3 | 0.91 |  | 0.0 |
| ctx_v0_s3_d0 | search_S | 3 | 758.0 | 1/3 |  | 1.099 | 0.0 |
| ctx_v0_s4_d0 | action_only | 3 | 1000.0 | 0/3 | 1.0 | 1.0 | 0.0 |
| ctx_v0_s4_d0 | ctx_full | 3 | 335.7 | 3/3 | 0.336 | 0.336 | 0.0 |
| ctx_v0_s4_d0 | label_blind | 3 | 108.7 | 3/3 | 0.109 | 0.109 | 0.0 |
| ctx_v0_s4_d0 | lexical_only | 3 | 679.3 | 1/3 | 0.679 | 0.679 | 0.0 |
| ctx_v0_s4_d0 | random | 3 | 1000.0 | 0/3 | 1.0 |  | 0.0 |
| ctx_v0_s4_d0 | search_S | 3 | 1000.0 | 0/3 |  | 1.0 | 0.0 |
| ctx_v0_s5_d0 | action_only | 3 | 1000.0 | 0/3 | 1.0 | 1.0 | 0.0 |
| ctx_v0_s5_d0 | ctx_full | 3 | 767.3 | 1/3 | 0.767 | 0.767 | 0.0 |
| ctx_v0_s5_d0 | label_blind | 3 | 147.3 | 3/3 | 0.147 | 0.147 | 0.0 |
| ctx_v0_s5_d0 | lexical_only | 3 | 1000.0 | 0/3 | 1.0 | 1.0 | 0.0 |
| ctx_v0_s5_d0 | random | 3 | 1000.0 | 0/3 | 1.0 |  | 0.0 |
| ctx_v0_s5_d0 | search_S | 3 | 1000.0 | 0/3 |  | 1.0 | 0.0 |
| ctx_vs_s1_d0 | action_only | 3 | 678.7 | 1/3 | 0.679 | 0.679 | 0.0 |
| ctx_vs_s1_d0 | ctx_full | 3 | 355.7 | 3/3 | 0.356 | 0.356 | 0.0 |
| ctx_vs_s1_d0 | label_blind | 3 | 114.3 | 3/3 | 0.114 | 0.114 | 0.0 |
| ctx_vs_s1_d0 | lexical_only | 3 | 1000.0 | 0/3 | 1.0 | 1.0 | 0.0 |
| ctx_vs_s1_d0 | random | 3 | 1000.0 | 0/3 | 1.0 |  | 0.0 |
| ctx_vs_s1_d0 | search_S | 3 | 1000.0 | 0/3 |  | 1.0 | 0.0 |
| ctx_vu1_s1_d0 | action_only | 3 | 742.7 | 1/3 | 0.743 | 0.743 | 0.0 |
| ctx_vu1_s1_d0 | ctx_full | 3 | 540.7 | 2/3 | 0.541 | 0.541 | 0.0 |
| ctx_vu1_s1_d0 | label_blind | 3 | 393.3 | 2/3 | 0.393 | 0.393 | 0.0 |
| ctx_vu1_s1_d0 | lexical_only | 3 | 742.7 | 1/3 | 0.743 | 0.743 | 0.0 |
| ctx_vu1_s1_d0 | random | 3 | 1000.0 | 0/3 | 1.0 |  | 0.0 |
| ctx_vu1_s1_d0 | search_S | 3 | 1000.0 | 0/3 |  | 1.0 | 0.0 |
| ctx_vu1_s5_d1 | action_only | 3 | 1000.0 | 0/3 | 1.316 | 1.0 | 2.4133 |
| ctx_vu1_s5_d1 | ctx_full | 3 | 320.0 | 3/3 | 0.421 | 0.32 | 2.3467 |
| ctx_vu1_s5_d1 | label_blind | 3 | 111.7 | 3/3 | 0.147 | 0.112 | 1.96 |
| ctx_vu1_s5_d1 | lexical_only | 3 | 1000.0 | 0/3 | 1.316 | 1.0 | 3.2 |
| ctx_vu1_s5_d1 | random | 3 | 1000.0 | 0/3 | 1.316 |  | 1.3067 |
| ctx_vu1_s5_d1 | search_S | 3 | 760.0 | 1/3 |  | 0.76 | 2.44 |
| ctx_vu2_s1_d0 | action_only | 3 | 716.7 | 1/3 | 0.717 | 0.717 | 0.0 |
| ctx_vu2_s1_d0 | ctx_full | 3 | 546.3 | 2/3 | 0.546 | 0.546 | 0.0 |
| ctx_vu2_s1_d0 | label_blind | 3 | 417.3 | 2/3 | 0.417 | 0.417 | 0.0 |
| ctx_vu2_s1_d0 | lexical_only | 3 | 1000.0 | 0/3 | 1.0 | 1.0 | 0.0 |
| ctx_vu2_s1_d0 | random | 3 | 1000.0 | 0/3 | 1.0 |  | 0.0 |
| ctx_vu2_s1_d0 | search_S | 3 | 1000.0 | 0/3 |  | 1.0 | 0.0 |
| media_library | action_only | 3 | 2000.0 | 0/3 | 1.246 | 1.188 | 0.0 |
| media_library | ctx_full | 3 | 2000.0 | 0/3 | 1.246 | 1.188 | 0.0 |
| media_library | label_blind | 3 | 1443.7 | 1/3 | 0.899 | 0.858 | 0.0 |
| media_library | lexical_only | 3 | 2000.0 | 0/3 | 1.246 | 1.188 | 0.0 |
| media_library | random | 3 | 1683.3 | 2/3 | 1.049 |  | 0.0 |
| media_library | search_S | 3 | 1605.3 | 2/3 |  | 0.954 | 0.0 |
| support_portal | action_only | 3 | 2000.0 | 0/3 | 1.0 | 1.0 | 0.0 |
| support_portal | ctx_full | 3 | 2000.0 | 0/3 | 1.0 | 1.0 | 0.0 |
| support_portal | label_blind | 3 | 2000.0 | 0/3 | 1.0 | 1.0 | 0.0 |
| support_portal | lexical_only | 3 | 2000.0 | 0/3 | 1.0 | 1.0 | 0.0 |
| support_portal | random | 3 | 2000.0 | 0/3 | 1.0 |  | 0.0 |
| support_portal | search_S | 3 | 2000.0 | 0/3 |  | 1.0 | 0.0 |
