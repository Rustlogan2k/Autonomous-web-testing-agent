# e2e suite admission

| app | admitted | checks | failed | browser | docker | seconds |
|---|---|---|---|---|---|---|
| bagstreet | yes | 20 | — | True | False | 819.3 |
| clinicflow | yes | 20 | — | True | False | 1393.8 |
| helpline | yes | 20 | — | True | False | 422.0 |
| ledgerly | yes | 20 | — | True | False | 946.2 |
| orbit | yes | 20 | — | True | False | 454.0 |
| stackroom | yes | 20 | — | True | False | 316.0 |
| tidewater | yes | 20 | — | True | False | 565.6 |
| wayfarer | yes | 19 | — | True | False | 345.7 |

## Seeded faults

| fault | subtype | oracle | witness | fires buggy | silent clean | needs own patch | confirmed | minimized | min. fires | min. silent clean |
|---|---|---|---|---|---|---|---|---|---|---|
| BAG-1 | stale-state | extension | 7 | True | True | True | True | 7 | True | True |
| BAG-2 | displayed-vs-submitted | existing | 9 | True | True | True | True | 7 | True | True |
| BAG-3 | missing-validation | existing | 7 | True | True | True | True | 7 | True | True |
| CLIN-1 | stale-state | extension | 15 | True | True | True | True | 15 | True | True |
| CLIN-2 | displayed-vs-submitted | existing | 14 | True | True | True | True | 13 | True | True |
| CLIN-3 | missing-validation | existing | 8 | True | True | True | True | 8 | True | True |
| HELP-1 | state-transition | existing | 6 | True | True | True | True | 6 | True | True |
| HELP-2 | workflow-completion | existing | 4 | True | True | True | True | 4 | True | True |
| HELP-3 | stale-state | existing | 3 | True | True | True | True | 3 | True | True |
| LED-1 | stale-state | extension | 4 | True | True | True | True | 3 | True | True |
| LED-2 | workflow-completion | existing | 10 | True | True | True | True | 10 | True | True |
| LED-3 | missing-validation | extension | 12 | True | True | True | True | 12 | True | True |
| ORB-1 | missing-validation | existing | 5 | True | True | True | True | 4 | True | True |
| ORB-2 | missing-validation | existing | 3 | True | True | True | True | 3 | True | True |
| ORB-3 | persistence | extension | 5 | True | True | True | True | 5 | True | True |
| STK-1 | incorrect-navigation | existing | 4 | True | True | True | True | 3 | True | True |
| STK-2 | wrong-target | existing | 4 | True | True | True | True | 4 | True | True |
| STK-3 | persistence | existing | 2 | True | True | True | True | 2 | True | True |
| TIDE-1 | missing-validation | existing | 7 | True | True | True | True | 7 | True | True |
| TIDE-2 | state-transition | existing | 6 | True | True | True | True | 6 | True | True |
| TIDE-3 | stale-state | existing | 5 | True | True | True | True | 5 | True | True |
| WAY-1 | persistence | extension | 4 | True | True | True | True | 4 | True | True |
| WAY-2 | cross-page | extension | 7 | True | True | True | True | 4 | True | True |

## Diversity

passed: True
max shared source lines between two apps: 3.2%
