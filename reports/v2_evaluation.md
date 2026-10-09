# Hybrid tester evaluation — numbers

Budget 2000 interactions; applications: dutyday, fosterly, larder, markbook, quizcraft, sideline, skylark, tally.

| arm | runs | discovery | RMST | faults/run | distinct faults | spec-less flagged | returns failed |
|---|---|---|---|---|---|---|---|
| hybrid_bandit | 40 | 0.85 | 769 | 1.23 | 19 | 0 | 27/1960 |
| hybrid_nolearn | 40 | 0.75 | 737 | 1.15 | 18 | 0 | 14/1960 |
| random | 40 | 0.80 | 978 | 1.00 | 13 | 0 | 0/0 |
| v1_dqn_pretrained | 40 | 0.85 | 595 | 1.18 | 17 | 0 | 0/0 |

| comparison | median Δ RMST | 95% CI | apps favouring | log-rank p | verdict |
|---|---|---|---|---|---|
| primary: hybrid_nolearn − random | -324 | [-665, +148] | 6/8 | 0.15871 | inconclusive |
| secondary: hybrid_bandit − hybrid_nolearn | +86 | [-304, +285] | 4/8 | 0.78072 | inconclusive |
| secondary: hybrid_bandit − random | -191 | [-801, +67] | 5/8 | 0.18002 | inconclusive |
| secondary: hybrid_nolearn − v1_dqn_pretrained | +42 | [-196, +574] | 4/8 | 0.27589 | inconclusive |

Clean-build controls: {"hybrid_bandit": {"jobs": 8, "verified_seeded": 0, "reproduced_invariant_candidates": 1}, "hybrid_nolearn": {"jobs": 8, "verified_seeded": 0, "reproduced_invariant_candidates": 2}, "random": {"jobs": 8, "verified_seeded": 0, "reproduced_invariant_candidates": 0}, "v1_dqn_pretrained": {"jobs": 8, "verified_seeded": 0, "reproduced_invariant_candidates": 0}}
