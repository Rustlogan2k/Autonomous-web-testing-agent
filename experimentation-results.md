# Experimentation Results — RL-based Automated Web Testing

**All results to 9 October 2026.** Every number below is computed from the committed reports
under `reports/` or the run files under `runs/`; the tables in §6 and §2 were generated
directly from those files (`src/web_testing_agent/app/evaluation.py`, the same code behind
the web app's Evaluation page), not transcribed.

## Contents

1. [Summary](#1-summary)
2. [Experiment 1 — Does pretraining transfer? (v1, pre-registered)](#2-experiment-1--does-pretraining-transfer-v1-pre-registered)
3. [Experiment 2 — Context or memorisation? (v1, pre-registered)](#3-experiment-2--context-or-memorisation-v1-pre-registered)
4. [Is the LLM progress reward rewarding the right thing? (post-hoc)](#4-is-the-llm-progress-reward-rewarding-the-right-thing-post-hoc)
5. [Can an LLM label pages reliably? (gated)](#5-can-an-llm-label-pages-reliably-gated)
6. [Hybrid tester (v2): development rounds](#6-hybrid-tester-v2-development-rounds)
7. [Hybrid tester (v2): held-out evaluation (pre-registered)](#7-hybrid-tester-v2-held-out-evaluation-pre-registered)
8. [What can and cannot be claimed](#8-what-can-and-cannot-be-claimed)
9. [Reproducing these results](#9-reproducing-these-results)

## How to read the numbers

- A **run** (or hunt) is one agent exploring one application for a fixed budget of browser
  interactions (2,000 unless stated). Every browser action counts, including replays.
- A run **finds a fault** when a seeded fault is **verified**: it reproduces twice in fresh
  sessions, does not appear on the per-fault control build (every fault except that one),
  and is minimised by delta debugging. Console errors and LLM opinions never count.
- **Time** is interactions to the first verified fault; a run that finds nothing counts as
  2,000 (right-censored). **Restricted mean time (RMST)** averages that over runs.
- **Pre-registered** experiments had their hypotheses, analysis code and decision rule
  committed before any result existed. **Development** rounds are tuning data: they shaped
  the design and are not evidence of generalisation.
- Decision rule of every pre-registered comparison: *supported* if the median per-application
  difference is negative (faster) and its 95% bootstrap interval excludes zero; *refuted* if
  positive and excluding zero; otherwise *inconclusive*.

---

## 1. Summary

| # | Question | Result | Status |
|---|---|---|---|
| 1 | Does pretraining an RL tester on other apps help on a new app? (v1) | No measurable benefit (median Δ 0, 95% [−749, +290]) | pre-registered, frozen |
| 2 | Does the v1 agent learn which action helps in which state? | Not supported: within-label AUROC 0.51 (chance) | pre-registered, frozen |
| 3 | Does the LLM progress reward point at faults? | Only on the apps it was tuned on (AUROC 1.00 vs 0.38–0.73) | post-hoc, descriptive |
| 4 | Can a local LLM label pages and controls accurately enough? | No: both gates failed (0.68/0.78; 0.61/0.75 on fresh pages) | gated, thresholds fixed in advance |
| 5 | Does a learner help on top of the structured hybrid tester? (development) | Not distinguishable from no learner across 5 rounds and 7 variants | development |
| 6 | Does the literature's curiosity-driven RL find our faults? (development) | No: at random level (17/42 vs random 18/42), significantly worse than the hybrid | development |
| 7 | **Held-out:** does the hybrid beat random, v1's best AC-DQN, and does its learner help? | **All four comparisons inconclusive** (8 apps); descriptively the hybrid arms verified the most distinct faults, and v1's pretrained AC-DQN had the lowest mean time | pre-registered, frozen |

**In one paragraph.** Pretraining did not transfer and the v1 network did not learn context,
so v2 made the state explicit and replaced learning-heavy exploration with structure: a page
abstraction, an archive with exact returns (Go-Explore), and test operators from standard
test design. On development data that structure found faults in about 75% of runs with zero
false positives, and no learning component added to it; curiosity-driven RL from the
literature performed at random level on these functional faults. On the eight held-out
applications, no pre-registered comparison reached a verdict: with eight applications the
intervals are wide, the held-out faults turned out easier for random exploration than the
development ones (random verified a fault in 80% of runs), and the hybrid had one clear
blind spot (list paging, §7.6). Descriptively, the hybrid arms verified the most distinct
faults (19 and 18 of 24, against random's 13), including several no random run found, and
v1's pretrained AC-DQN had the lowest mean time to a first fault.

---

## 2. Experiment 1 — Does pretraining transfer? (v1, pre-registered)

**Report:** `reports/transfer_final.md`. Pre-registration: `docs/methodology/2026-09-18-transfer-preregistration.md` and amendments of 2026-10-01 and 2026-10-03.

- **Design:** leave-one-application-out over 6 benchmark apps (4 never touched before the
  run); 10 arms; 210 runs of 2,000 interactions; the LLM reward from `qwen2.5:7b-instruct`
  (local).
- **Learner:** an action-conditioned dueling double DQN, Q(s, φ(a)), with prioritised replay,
  3-step returns, a residual adaptation head, search-guided ε-exploration, and a dynamic LLM
  progress reward.

| Arm | What it is | Runs | Found a fault | Discovery rate | Restricted mean time | Median (found runs) |
|---|---|---|---|---|---|---|
| `dqn_pretrained_nollm` | As dqn_pretrained, without the LLM reward. | 18 | 7 | 0.39 | 1,402 | 571 |
| `bandit_pretrained` | The same, pretrained on the other apps; residual head adapts. | 24 | 10 | 0.42 | 1,483 | 548 |
| `dqn_pretrained` | v1 DQN pretrained on the other apps, adapting; the v1 primary tester. | 27 | 9 | 0.33 | 1,520 | 550 |
| `dqn_scratch` | v1 DQN from scratch, with the LLM reward. | 27 | 9 | 0.33 | 1,610 | 596 |
| `dqn_zeroshot` | The pretrained DQN frozen: no adaptation. | 18 | 4 | 0.22 | 1,664 | 418 |
| `bandit_scratch` | v1 action-conditioned network, γ = 0, from scratch on the target. | 24 | 5 | 0.21 | 1,727 | 628 |
| `dqn_scratch_nollm` | As dqn_scratch, without the LLM reward. | 18 | 5 | 0.28 | 1,748 | 1,536 |
| `search_S` | v1's fixed feature-search score; no learning. | 18 | 3 | 0.17 | 1,844 | 1,159 |
| `random` | Uniform choice among the page's valid actions; the floor. | 18 | 2 | 0.11 | 1,947 | 1,525 |
| `search_A0` | Fixed hand-weighted action priority; no learning. | 18 | 1 | 0.06 | 1,953 | 1,160 |

| Comparison | Question | Median Δ | 95% interval | Folds favouring | Log-rank p |
|---|---|---|---|---|---|
| `dqn_pretrained − dqn_scratch` | transfer: pretrained vs scratch, same learner (primary tester) | 0 | [-749, 290] | 2/6 | 0.527 |
| `bandit_pretrained − bandit_scratch` | transfer: the pre-registered C vs B comparison | -280 | [-732, 188] | 4/6 | 0.086 |
| `dqn_pretrained − search_S` | learned vs search with identical features | -169 | [-884, 0] | 3/6 | 0.087 |
| `dqn_pretrained − search_A0` | learned vs the pre-registered search arm A | -170 | [-1,212, 0] | 3/6 | 0.006 |
| `dqn_pretrained − random` | learned vs uniform random | -151 | [-1,212, 0] | 3/6 | 0.022 |
| `dqn_pretrained − dqn_pretrained_nollm` | ablation: dynamic LLM reward on vs off | 17 | [-122, 378] | 2/6 | 0.754 |
| `dqn_pretrained − dqn_zeroshot` | ablation: online adaptation on vs off | -84 | [-430, 0] | 3/6 | 0.534 |
| `dqn_scratch − bandit_scratch` | ablation: bootstrapped vs myopic, from scratch | 0 | [-230, 261] | 2/6 | 0.720 |

**Findings.**
- **No transfer benefit:** pretrained vs scratch, same learner: median Δ 0 [−749, +290],
  p = 0.53.
- **The LLM reward made no measurable difference** (p = 0.75).
- **Learned arms found faults in more runs than search or random** (0.33–0.42 vs
  0.06–0.17), but their fold-level intervals reach zero: descriptive, not proven.
- Faults deep in a workflow (9–16 contextual steps) were never found on two apps by any arm.

---

## 3. Experiment 2 — Context or memorisation? (v1, pre-registered)

**Report:** `reports/contextual_diagnostic.md`. 12 generated variants of one workflow
(synonyms, unseen words, pseudo-words; tabs, a modal, reordered steps; a decoy), 228 jobs,
probe sets fixed before any arm ran.

| Hypothesis | Verdict | Deciding numbers |
|---|---|---|
| **H-ctx** genuine state–action contextual learning | **not supported** | within-label AUROC 0.51 [0.44, 0.59] (rule ≥ 0.70); no better than the state-blind `action_only` (+0.02) or `lexical_only` (+0.01) arms |
| H-act action memorisation | inconclusive | decoy probe rate 0.31 [0.00, 0.91] |
| H-lex lexical memorisation | inconclusive | top-1 ratio on unseen vocabulary 0.88 [0.35, 1.18] |
| H-app application memorisation | inconclusive | top-1 ratio on new structures 0.63 [0.44, 0.88] |

**What it shows.** Only 7% of the Q-value variance came from the label × state interaction:
the state moves every action's value together and almost never reorders them. The agent does
rank useful actions above chance (top-1 0.56 vs random 0.18), robustly to vocabulary but not
to every layout change.

---

## 4. Is the LLM progress reward rewarding the right thing? (post-hoc)

**Report:** `reports/llm_reward_validation.md` (descriptive, run after both frozen experiments).

| App | Role | AUROC: fault-observable page vs random-walk pages |
|---|---|---|
| shop_catalog | development | **1.00** |
| booking_desk | development | **1.00** |
| support_portal | pristine | 0.73 |
| account_hub | pristine | 0.52 |
| media_library | pristine | 0.47 |
| admin_records | pristine | 0.38 |

The score tracks progress where its prompt was tuned and not elsewhere; it rewards the happy
path while many faults need deviations, and most of what it pays duplicates the novelty bonus.
Only on the development app did more LLM reward go with earlier discovery (Spearman −0.72).

---

## 5. Can an LLM label pages reliably? (gated)

Thresholds fixed before each run: page kind ≥ 0.85, control role ≥ 0.80.

| Test | Page kind | Control role | Gate |
|---|---|---|---|
| Stage 0 (generated variants; `reports/v2_stage0.md`) | 0.68 | 0.78 | failed |
| Revised labeller, 69 fresh pages (`reports/v2_label_validation.md`) | 0.61 | 0.75 | failed |
| Same revised labeller on the data it was revised from | 0.93 | 0.83 | (would have passed) |

Decision: deterministic, markup-based labels. The last row shows why the re-test had to use
fresh pages: judged on its own revision data, the labeller would have been adopted.

---

## 6. Hybrid tester (v2): development rounds

The hybrid tester: page abstraction (page kind, path template, heading, form fill level,
alert, whether a submit action is offered) → archive of abstract states with exact replay →
test operators (complete the step, choose each dropdown option, invalid input, edit after
review, back/reload and resume, double submit, collection churn; each follows the task
through) → optional decision layer (Thompson-sampling bandit) → v1's seeded-fault oracle plus
specification-free invariants (reported separately).

### 6.1 All arms compared on the same runs (suite rounds 2–5, 14 apps × 3 seeds)

Each arm against `hybrid_nolearn` on the (application, seed) pairs both completed; the
interval is a bootstrap over applications; negative = faster.

| Arm | What it is | Runs (shared with baseline) | Found a fault | Baseline found | Median | Mean | Per-app mean difference vs `hybrid_nolearn` [95% interval] | Apps faster / slower |
|---|---|---|---|---|---|---|---|---|
| `hybrid_nolearn` | Hybrid pipeline; untried operators chosen uniformly (no learner). | 42 | 32 | 32 | 742 | 889 | baseline |  |
| `hybrid_bandit` | Hybrid pipeline with the Thompson-sampling operator bandit. | 42 | 30 | 32 | 335 | 840 | -49 [-279, +190] | 7 / 6 |
| `dev_bandit_surprise` | Bandit rewarded for unexpected responses. | 42 | 31 | 32 | 586 | 844 | -46 [-257, +143] | 6 / 7 |
| `dev_bandit_novelty` | Bandit rewarded only for genuinely new pages. | 42 | 31 | 32 | 486 | 854 | -35 [-252, +190] | 8 / 5 |
| `dev_pairwise_bandit` | Pairwise operator + technique-first choice, with the bandit. | 42 | 27 | 32 | 757 | 943 | +53 [-121, +227] | 6 / 7 |
| `dev_hybrid_curiosity` | Hybrid with curiosity Q-learning choosing the free exploration. | 42 | 29 | 32 | 612 | 978 | +89 [-38, +202] | 4 / 9 |
| `dev_pairwise_nolearn` | Pairwise operator + technique-first choice; no learner. | 40 | 26 | 30 | 1,069 | 1,046 | +119 [-37, +281] | 3 / 9 |
| `dev_curiosity_rl` | Standalone curiosity Q-learning tester (WebExplor/WebRLED components). | 42 | 17 | 32 | 2,000 | 1,313 | +423 [+142, +744] **significant** | 4 / 9 |
| `random` | Uniform choice among the page's valid actions; the floor. | 42 | 18 | 32 | 2,000 | 1,355 | +465 [+178, +790] **significant** | 4 / 9 |

**Reading.** The top four are statistically tied: `hybrid_nolearn` verified a fault in the
most runs, `hybrid_bandit` was fastest when it did. No learner variant (three reward signals,
pairwise operator, curiosity Q-learning) improved on no learner. The standalone curiosity-RL
tester, built from the components of WebExplor [zheng2021webexplor] and WebRLED
[gu2025webrled], was fast on shallow faults but never found the deep ones, and was
significantly worse than the hybrid, level with random. It is a tester built from their
published components, not a reproduction of those tools; their published results (coverage
and crash-type failures over long runs) are not contradicted by it.

### 6.2 Every round

#### Benchmark 1 (`v2-dev`): First end-to-end run: bandit, no learner, random.

4 apps, seeds 0, 1, 2. **Decision:** Discarded (code edited while jobs started); replay bug found and fixed.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `hybrid_bandit` | 10 | 1 / 10 | 1 | 2,000 | 1,908 | — | 0 |
| `hybrid_nolearn` | 12 | 5 / 12 | 6 | 2,000 | 1,643 | — | 0 |
| `random` | 12 | 0 / 12 | 0 | 2,000 | 2,000 | — | 0 |

#### LLM-label smoke (`v2-dev-llm`): One short run of the LLM-labels arm.

1 apps, seeds 0. **Decision:** Infrastructure check only.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `hybrid_bandit_llm` | 1 | 1 / 1 | 1 | 76 | 76 | — | 0 |

#### Benchmark 2 (`v2-dev2`): Bandit vs no learner, with clean builds.

6 apps, seeds 0, 1, 2. **Decision:** Validation-consistency invariant narrowed; form and operator fixes.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `hybrid_bandit` | 18 | 4 / 18 | 4 | 2,000 | 1,771 | 0 / 6 | 0 |
| `hybrid_nolearn` | 18 | 5 / 18 | 5 | 2,000 | 1,679 | 0 / 6 | 0 |

#### Benchmark 3 (`v2-dev3`): Free-exploration variants.

6 apps, seeds 0, 1, 2. **Decision:** Depth preference + weighted exploration adopted for every hybrid arm.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `dev_bandit_deep` | 18 | 11 / 18 | 12 | 1,317 | 1,183 | — | 0 |
| `dev_bandit_search` | 18 | 5 / 18 | 5 | 2,000 | 1,580 | — | 0 |
| `dev_bandit_search_deep` | 18 | 10 / 18 | 10 | 1,423 | 1,235 | — | 0 |
| `hybrid_bandit` | 18 | 5 / 18 | 7 | 2,000 | 1,642 | — | 0 |
| `hybrid_nolearn` | 18 | 4 / 18 | 4 | 2,000 | 1,719 | — | 0 |

#### Benchmark 3 (clean) (`v2-dev3-clean`): The two best variants on clean builds.

6 apps, seeds 0. **Decision:** No false positives.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `dev_bandit_search_deep` | 6 | 3 / 6 | 3 | 1,246 | 1,226 | 0 / 6 | 0 |
| `hybrid_bandit` | 6 | 3 / 6 | 4 | 1,911 | 1,335 | 0 / 6 | 0 |

#### Benchmark 4 (`v2-dev4`): Cells record which submit actions a page offers.

6 apps, seeds 0, 1, 2. **Decision:** Split states; replaced by 'whether' in round 5.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `hybrid_bandit` | 18 | 6 / 18 | 8 | 2,000 | 1,511 | 0 / 6 | 0 |
| `hybrid_nolearn` | 18 | 4 / 18 | 5 | 2,000 | 1,604 | 0 / 6 | 0 |

#### Benchmark 5 (`v2-dev5`): Cells record whether a page offers a submit action.

3 apps, seeds 0, 1, 2. **Decision:** Kept.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `hybrid_bandit` | 9 | 4 / 9 | 5 | 2,000 | 1,214 | — | 0 |

#### Benchmark 6 (`v2-dev6`): Whole-word matching of role cues.

6 apps, seeds 0, 1, 2. **Decision:** Regression on 4 apps; replaced.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `hybrid_bandit` | 18 | 7 / 18 | 9 | 2,000 | 1,435 | — | 0 |
| `hybrid_nolearn` | 18 | 4 / 18 | 6 | 2,000 | 1,582 | — | 0 |

#### Benchmark 7 (`v2-dev7`): Prefix matching of role cues.

5 apps, seeds 0, 1, 2. **Decision:** Adopted.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `hybrid_bandit` | 15 | 10 / 15 | 12 | 547 | 980 | — | 0 |

#### Suite 1 (`v2-suite-dev1`): Bandit vs no learner on the 8 suite apps.

8 apps, seeds 0, 1, 2. **Decision:** Site navigation never submits; submit actions taken in turn.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `hybrid_bandit` | 24 | 18 / 24 | 25 | 294 | 744 | — | 0 |
| `hybrid_nolearn` | 24 | 19 / 24 | 25 | 206 | 670 | — | 0 |

#### Suite 2 (`v2-suite-dev2`): The same two arms after the fix, 14 apps.

14 apps, seeds 0, 1, 2. **Decision:** Fix kept; learner not ahead.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `hybrid_bandit` | 42 | 30 / 42 | 40 | 335 | 840 | — | 0 |
| `hybrid_nolearn` | 42 | 32 / 42 | 46 | 742 | 889 | — | 0 |

#### Suite 3 (`v2-suite-dev3`): Two corrected reward signals for the bandit.

14 apps, seeds 0, 1, 2. **Decision:** No difference: the reward is not the bottleneck.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `dev_bandit_novelty` | 42 | 31 / 42 | 38 | 486 | 854 | — | 0 |
| `dev_bandit_surprise` | 42 | 31 / 42 | 44 | 586 | 844 | — | 0 |

#### Suite 4 (`v2-suite-dev4`): Pairwise operator + technique-first choice.

14 apps, seeds 0, 1, 2. **Decision:** Worse; not adopted.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `dev_pairwise_bandit` | 42 | 27 / 42 | 38 | 757 | 943 | — | 0 |
| `dev_pairwise_nolearn` | 40 | 26 / 40 | 32 | 1,069 | 1,046 | — | 2 |

#### Suite 5 (`v2-suite-dev5`): Curiosity Q-learning (in the hybrid, and standalone) and random.

14 apps, seeds 0, 1, 2. **Decision:** Neither curiosity arm adopted: inside the hybrid no better; standalone no better than random.

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `dev_curiosity_rl` | 42 | 17 / 42 | 31 | 2,000 | 1,313 | — | 0 |
| `dev_hybrid_curiosity` | 42 | 29 / 42 | 39 | 612 | 978 | — | 0 |
| `random` | 42 | 18 / 42 | 29 | 2,000 | 1,355 | — | 0 |

#### v2-eval (`v2-eval`): 

8 apps, seeds 0, 1, 2, 3, 4. **Decision:** 

| Arm | Runs | Found a fault | Faults verified | Median | Mean | Clean-build false positives | Failed jobs |
|---|---|---|---|---|---|---|---|
| `hybrid_bandit` | 40 | 34 / 40 | 49 | 384 | 769 | 0 / 8 | 0 |
| `hybrid_nolearn` | 40 | 30 / 40 | 46 | 342 | 737 | 0 / 8 | 0 |
| `random` | 40 | 32 / 40 | 40 | 764 | 978 | 0 / 8 | 0 |
| `v1_dqn_pretrained` | 40 | 34 / 40 | 47 | 338 | 595 | 0 / 8 | 0 |

---

## 7. Hybrid tester (v2): held-out evaluation (pre-registered)

### 7.1 Design

Pre-registration: `docs/methodology/hybrid-v2-evaluation-preregistration.md`, frozen on the
owner's approval (commit `5592fd0`, tag `v2-eval-freeze`) before any agent ran on any
validation application.

- **Applications:** the 8 validation apps (tree `ffd4fda3`): dutyday, fosterly, larder,
  markbook, quizcraft, sideline, skylark, tally; 24 seeded faults. Planned independently of
  the tester's code and results; versions without a database server or sign-in.
- **Arms:** `random`; `hybrid_nolearn` (primary); `hybrid_bandit`; `v1_dqn_pretrained` (v1's
  best AC-DQN, `dqn_pretrained_nollm`, using the frozen experiment's own checkpoints,
  pretrained on benchmark apps; added at approval).
- **Runs:** seeds 0–4, 2,000 interactions: 160 hunts; plus 32 clean-build controls. Run
  2026-10-08 23:12 → 2026-10-09 06:35, 4 workers, no other workload.
- **Infrastructure:** 1 of 192 jobs failed (a Windows file lock while deleting the
  temporary build folder of a clean-build control, `random` on sideline); rerun unchanged as
  pre-registered: completed, no finding. The failed record is kept in
  `runs/v2-eval/infrastructure_failures/`.

### 7.2 Pre-registered verdicts

(`reports/v2_evaluation.md`, protocol `freeze-2026-10-08`.)

| | Comparison | Median Δ RMST | 95% interval | Apps favouring | Log-rank p | **Verdict** |
|---|---|---|---|---|---|---|
| **H1 (primary)** | `hybrid_nolearn − random` | −324 | [−665, +148] | 6/8 | 0.159 | **inconclusive** |
| H2 | `hybrid_bandit − hybrid_nolearn` | +86 | [−304, +285] | 4/8 | 0.781 | **inconclusive** |
| H3 | `hybrid_bandit − random` | −191 | [−801, +67] | 5/8 | 0.180 | **inconclusive** |
| H4 | `hybrid_nolearn − v1_dqn_pretrained` | +42 | [−196, +574] | 4/8 | 0.276 | **inconclusive** |

As anticipated in the pre-registration (§3), eight applications leave the median rule wide
intervals; no comparison is supported or refuted.

### 7.3 Per arm

| Arm | Runs | Runs that found a fault | RMST | Faults verified per run | Distinct faults verified (of 24) | Clean-build false positives |
|---|---|---|---|---|---|---|
| `v1_dqn_pretrained` | 40 | 34 (0.85) | **595** | 1.18 | 17 | 0 / 8 |
| `hybrid_nolearn` | 40 | 30 (0.75) | 737 | 1.15 | 18 | 0 / 8 |
| `hybrid_bandit` | 40 | 34 (0.85) | 769 | **1.23** | **19** | 0 / 8 |
| `random` | 40 | 32 (0.80) | 978 | 1.00 | 13 | 0 / 8 |

### 7.4 Per application (runs that found a fault, of 5; mean time)

| App | `random` | `v1_dqn_pretrained` | `hybrid_nolearn` | `hybrid_bandit` |
|---|---|---|---|---|
| dutyday | 4/5 · 1,217 | **5/5 · 281** | 4/5 · 788 | 4/5 · 778 |
| fosterly | 5/5 · 1,262 | 4/5 · 763 | 4/5 · 681 | **5/5 · 370** |
| larder | 5/5 · 411 | 5/5 · 407 | **5/5 · 211** | 5/5 · 393 |
| markbook | 1/5 · 1,805 | **4/5 · 567** | 3/5 · 1,141 | 4/5 · 1,442 |
| quizcraft | 5/5 · 299 | 5/5 · 97 | **5/5 · 81** | 5/5 · 366 |
| sideline | 2/5 · 1,623 | 1/5 · 1,759 | **4/5 · 620** | 3/5 · 822 |
| skylark | 5/5 · 980 | **5/5 · 614** | 0/5 · 2,000 | 3/5 · 1,696 |
| tally | 5/5 · **229** | 5/5 · 276 | 5/5 · 376 | 5/5 · 284 |

### 7.5 Per fault (runs of 5 that verified it)

| Fault | `random` | `v1_dqn_pretrained` | `hybrid_nolearn` | `hybrid_bandit` |
|---|---|---|---|---|
| DUTY-1 | 3 | 3 | 0 | 3 |
| DUTY-2 | 4 | 3 | 4 | 4 |
| DUTY-3 | 0 | 1 | 1 | 0 |
| FOST-1 | 5 | 3 | 4 | 5 |
| FOST-2 | 0 | 0 | 0 | 0 |
| FOST-3 | 0 | 2 | 1 | 1 |
| LARD-1 | 1 | 0 | 3 | 4 |
| LARD-2 | 5 | 5 | 5 | 5 |
| LARD-3 | 1 | 0 | 2 | 1 |
| MARK-1 | 0 | 0 | 2 | 2 |
| MARK-2 | 0 | 1 | 2 | 1 |
| MARK-3 | 1 | 4 | 1 | 1 |
| QUIZ-1 | 5 | 5 | 5 | 5 |
| QUIZ-2 | 0 | 2 | 1 | 2 |
| QUIZ-3 | 0 | 1 | 2 | 0 |
| SIDE-1 | 0 | 0 | 1 | 1 |
| SIDE-2 | 1 | 1 | 2 | 2 |
| SIDE-3 | 1 | 0 | 4 | 3 |
| SKY-1 | 0 | 3 | 0 | 1 |
| SKY-2 | 3 | 1 | 0 | 1 |
| SKY-3 | 5 | 5 | 0 | 2 |
| TALLY-1 | 5 | 5 | 5 | 5 |
| TALLY-2 | 0 | 2 | 1 | 0 |
| TALLY-3 | 0 | 0 | 0 | 0 |

22 of the 24 faults were verified by at least one arm; FOST-2 and TALLY-3 by none. Faults
verified by a hybrid arm and by no random run: DUTY-3, FOST-3, MARK-1, MARK-2, QUIZ-2,
QUIZ-3, SIDE-1, TALLY-2 (8). Faults random verified more often than either hybrid arm:
SKY-2, SKY-3, and DUTY-1 against `hybrid_nolearn`.

### 7.6 What the held-out data shows (descriptive)

1. **The held-out faults were easier for random exploration than the development ones.**
   Random verified a fault in 80% of runs here, against 43% on the development apps (18/42).
   Several faults sit one or two interactions from a page random visits constantly
   (LARD-2, QUIZ-1, TALLY-1: 5/5 for every arm), which compresses every difference.
2. **The hybrid arms reached the deeper faults.** Of the 11 faults random verified in at
   most 1 of 5 runs, the two hybrid arms verified 9 at least once (MARK-1, MARK-2, SIDE-1,
   SIDE-3, LARD-1, LARD-3, QUIZ-2, QUIZ-3, FOST-3). That is where test operators help:
   completing forms validly, invalid input, edits after review.
3. **One clear blind spot: list paging.** All three skylark faults sit behind its feed's
   "Load more" button (load every page; flag an item on page 2 and reload that page). Random
   presses it often by chance; the hybrid's operators work on forms and have no "page through
   a list" operator, so `hybrid_nolearn` reached the right pages in every run but never
   repeated the paging (0/5). This cost H1 one of eight applications. It is reported, not
   fixed: the code is frozen.
4. **v1's pretrained AC-DQN did better here than on its own benchmark.** Discovery 0.85 and
   the lowest mean time, notably on dutyday and markbook and with 5/5 on skylark. Its
   pretraining on other applications plus online adaptation transferred to these smaller
   apps; whether that reflects the method or the apps' shallower faults cannot be separated
   with eight applications (H4 inconclusive).
5. **The learner (H2).** `hybrid_bandit` found a fault in more runs (34 vs 30) and more
   distinct faults (19 vs 18) than `hybrid_nolearn`, but with a slightly higher mean time
   (769 vs 737). As in development, the learner is not distinguishable from no learner.
6. **Zero false positives.** No clean-build control verified a seeded fault (0/32 runs).

### 7.7 Specification-free oracle (reported separately, as pre-registered)

- Seeded faults flagged by the generic invariants alone: **0** for every arm.
- Reproduced candidates on clean builds: 3 (`hybrid_nolearn` 2, `hybrid_bandit` 1), all on
  quizcraft's editor, all "an entered value is gone after reload". Inspected by hand:
  quizcraft keeps its working copy in page memory until Save (documented in the app's README),
  so losing unsaved edits on reload is intended behaviour. **Classification: false alarms**;
  the reload-stability invariant cannot distinguish saved from unsaved state.

---

## 8. What can and cannot be claimed

**Can claim:**
- A pre-registered negative result: pretraining an RL web tester on five applications did
  not transfer to a sixth (Experiment 1).
- A diagnosed reason: the v1 network ranks actions without using the state (Experiment 2).
- A small local LLM's progress reward and page labels do not generalise beyond the data
  they were tuned on (§4, §5).
- On development applications: a structured tester (abstraction, archive with exact
  returns, test-design operators) found faults in most runs with zero false positives; no
  learning component added to it; curiosity-driven RL built from the literature's components
  performed at random level on these functional faults.
- On held-out applications: the hybrid arms verified the most distinct faults (19 and 18
  of 24), including 8 faults no random run found; zero false positives in 32 clean-build
  controls.

**Cannot claim:**
- That the hybrid tester finds faults sooner than random, than v1's best AC-DQN, or that its
  learner helps, on unseen applications: all four pre-registered comparisons are
  inconclusive.
- That WebExplor or WebRLED themselves would perform at random level here: we tested a
  tester built from their components, not their tools.
- Anything about production sites or real-world bug rates: these are small applications
  with seeded faults, built by the same developer as the tester (disclosed).
- Any security-testing result (out of scope).

---

## 9. Reproducing these results

```bash
# Held-out evaluation (resumable) and its frozen analysis
python -m web_testing_agent.hybrid.run --out-dir runs/v2-eval \
    --apps dutyday fosterly larder markbook quizcraft sideline skylark tally \
    --arms random hybrid_nolearn hybrid_bandit v1_dqn_pretrained --seeds 0 1 2 3 4 --clean --workers 4
python -m web_testing_agent.hybrid.analyze --runs runs/v2-eval --out reports/v2_evaluation \
    --protocol freeze-2026-10-08

# Development rounds: runs/v2-dev*, runs/v2-suite-dev1..5 (same runner, arms as in §6)
# v1 experiments: scripts/run_transfer.py, scripts/analyze_transfer.py; web_testing_agent.ctxbench.*
# Every table in §6 and §2, live: python -m web_testing_agent.app -> Research -> Evaluation
```
