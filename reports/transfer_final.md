# Transfer experiment: leave-one-application-out

- Directory: `runs\final-merged`
- Commit(s): 811094a390, c10699ce4f
- Budget: 2000 interactions per run; folds: 6 (account_hub, admin_records, booking_desk, media_library, shop_catalog, support_portal)
- Development folds (used to design the system, not transfer evidence): booking_desk, shop_catalog
- Pristine held-out folds: account_hub, admin_records, media_library, support_portal
- Runs analysed: 210; infrastructure exclusions: 0

## Time to first verified fault, per arm (pooled; descriptive)

| arm | runs | verified runs | discovery rate | restricted mean time | median time (verified runs) |
|---|---|---|---|---|---|
| bandit_pretrained | 24 | 10 | 0.42 | 1483 | 548.5 |
| bandit_scratch | 24 | 5 | 0.21 | 1727 | 628.0 |
| dqn_pretrained | 27 | 9 | 0.33 | 1520 | 550.0 |
| dqn_pretrained_nollm | 18 | 7 | 0.39 | 1402 | 571.0 |
| dqn_scratch | 27 | 9 | 0.33 | 1610 | 596.0 |
| dqn_scratch_nollm | 18 | 5 | 0.28 | 1748 | 1536.0 |
| dqn_zeroshot | 18 | 4 | 0.22 | 1664 | 418.0 |
| random | 18 | 2 | 0.11 | 1947 | 1525.0 |
| search_A0 | 18 | 1 | 0.06 | 1953 | 1160.0 |
| search_S | 18 | 3 | 0.17 | 1844 | 1159.0 |

## Paired fold-level comparisons, all folds

Negative = the first arm confirms a fault in fewer interactions. The interval resamples folds (the unit of inference).

| comparison | why | median Δ | 95% CI | folds favouring | log-rank p |
|---|---|---|---|---|---|
| dqn_pretrained − dqn_scratch | transfer: pretrained vs scratch, same learner (primary tester) | +0 | [-749, +290] | 2/6 | 0.527 |
| bandit_pretrained − bandit_scratch | transfer: the pre-registered C vs B comparison | -280 | [-732, +188] | 4/6 | 0.0862 |
| dqn_pretrained − search_S | learned vs search with identical features | -169 | [-884, +0] | 3/6 | 0.0871 |
| dqn_pretrained − search_A0 | learned vs the pre-registered search arm A | -170 | [-1212, +0] | 3/6 | 0.00641 |
| dqn_pretrained − random | learned vs uniform random | -151 | [-1212, +0] | 3/6 | 0.0225 |
| dqn_pretrained − dqn_pretrained_nollm | ablation: dynamic LLM reward on vs off | +17 | [-122, +378] | 2/6 | 0.754 |
| dqn_pretrained − dqn_zeroshot | ablation: online adaptation on vs off | -84 | [-430, +0] | 3/6 | 0.534 |
| dqn_scratch − bandit_scratch | ablation: bootstrapped vs myopic, from scratch | +0 | [-230, +261] | 2/6 | 0.72 |

## Paired fold-level comparisons, pristine held-out folds only

Negative = the first arm confirms a fault in fewer interactions. The interval resamples folds (the unit of inference).

| comparison | why | median Δ | 95% CI | folds favouring | log-rank p |
|---|---|---|---|---|---|
| dqn_pretrained − dqn_scratch | transfer: pretrained vs scratch, same learner (primary tester) | -310 | [-879, +469] | 2/4 | 0.662 |
| bandit_pretrained − bandit_scratch | transfer: the pre-registered C vs B comparison | -280 | [-1096, +376] | 3/4 | 0.108 |
| dqn_pretrained − search_S | learned vs search with identical features | -169 | [-1046, +0] | 2/4 | 0.379 |
| dqn_pretrained − search_A0 | learned vs the pre-registered search arm A | -170 | [-1148, +0] | 2/4 | 0.225 |
| dqn_pretrained − random | learned vs uniform random | -151 | [-1148, +0] | 2/4 | 0.528 |
| dqn_pretrained − dqn_pretrained_nollm | ablation: dynamic LLM reward on vs off | +175 | [-223, +441] | 1/4 | 0.583 |
| dqn_pretrained − dqn_zeroshot | ablation: online adaptation on vs off | -121 | [-619, +0] | 2/4 | 0.607 |
| dqn_scratch − bandit_scratch | ablation: bootstrapped vs myopic, from scratch | +0 | [-93, +522] | 1/4 | 0.724 |

## Supporting metrics per arm

| arm | reached | discovered | verified | candidates rejected | unique states | state-changing actions | minimized trace |
|---|---|---|---|---|---|---|---|
| bandit_pretrained | 0.52 | 0.29 | 0.23 | 3 | 239.6 | 0.81 | 9.36 |
| bandit_scratch | 0.48 | 0.12 | 0.10 | 1 | 239.8 | 0.80 | 8.6 |
| dqn_pretrained | 0.48 | 0.24 | 0.22 | 4 | 155.9 | 0.74 | 7.08 |
| dqn_pretrained_nollm | 0.44 | 0.25 | 0.25 | 0 | 190.1 | 0.71 | 7.11 |
| dqn_scratch | 0.54 | 0.30 | 0.28 | 5 | 211.9 | 0.76 | 7.6 |
| dqn_scratch_nollm | 0.42 | 0.22 | 0.17 | 9 | 277.3 | 0.74 | 7.33 |
| dqn_zeroshot | 0.36 | 0.14 | 0.14 | 0 | 114.8 | 0.68 | 7.2 |
| random | 0.19 | 0.06 | 0.06 | 0 | 85.4 | 0.48 | 12.0 |
| search_A0 | 0.14 | 0.06 | 0.03 | 1 | 66.7 | 0.56 | 7.0 |
| search_S | 0.47 | 0.14 | 0.08 | 5 | 121.4 | 0.85 | 9.33 |

## False-positive controls (clean builds)

| fold | arm | raw assertion firings | verified findings |
|---|---|---|---|
| account_hub | dqn_pretrained | 0 | 0 |
| admin_records | dqn_pretrained | 0 | 0 |
| booking_desk | dqn_pretrained | 0 | 0 |
| media_library | dqn_pretrained | 0 | 0 |
| shop_catalog | dqn_pretrained | 1 | 0 |
| support_portal | dqn_pretrained | 0 | 0 |
| account_hub | search_S | 0 | 0 |
| admin_records | search_S | 0 | 0 |
| booking_desk | search_S | 0 | 0 |
| media_library | search_S | 0 | 0 |
| shop_catalog | search_S | 1 | 0 |
| support_portal | search_S | 0 | 0 |

## Top-up rule (pre-registration §8)

- dqn_pretrained - dqn_scratch: admin_records, booking_desk, shop_catalog
- bandit_pretrained - bandit_scratch: admin_records, booking_desk
