# Learning to Test Web Applications: Reinforcement Learning, LLM Judgements and Verified Faults

*Final-year project report — working draft (started 2026-10-07). Sections marked
**[v2 pending]** are filled after the hybrid tester's evaluation; **[author]** marks text
for the author to write or approve. Every number below is taken from a committed report
in `reports/`, cited in brackets.*

---

## Abstract **[v2 pending]**

---

## 1. Introduction

Web applications fail in ways that are cheap to describe and expensive to find: a total
that ignores a changed quantity, a validation rule that holds on one path and not on
another, a value that is lost on reload. Automated exploratory testers can reach such
states, but two problems limit them. First, an explorer must *decide where to go* in a large,
stateful interface, and the decisions that matter are many steps deep. Second, an explorer
that finds something must *prove* it, or its reports are noise.

This project builds and evaluates an automated functional tester for web applications that
addresses both: learned exploration (reinforcement learning, later combined with structured
search) and a deterministic oracle that confirms a fault only when it reproduces, depends on
the fault, and survives minimisation. Security testing is out of scope.

**Contributions** **[author: confirm wording]**
1. A tester with a deterministic, replay-verified seeded-fault oracle (per-fault controls,
   two reproductions, delta-debugging minimisation) that produced **zero false positives**
   across every clean-build control run.
2. A pre-registered leave-one-application-out evaluation of transfer in deep RL web testing,
   with censoring-aware statistics — and its negative result for transfer.
3. A pre-registered diagnostic separating contextual learning from memorisation, built on a
   generated application family with execution-labelled probe states.
4. Two validated negative results for small local language models in testing: as a progress
   reward and as a page/control labeller, each measured against ground truth.
5. **[v2 pending]** A hybrid tester (structured archive exploration, test operators from
   standard test design, a semantic-key bandit, and specification-free invariants),
   evaluated on eight held-out applications with real backends.

## 2. Background and related work

*(Condensed from `docs/literature_review.md`; full citations there.)* RL web testers
(WebExplor, WebQT, WebRLED, WebCQ) learn online on the application under test and are scored
on coverage and console errors; cross-application learning has been shown on Android
(DinoDroid, 32 training apps) but, in the literature reviewed, not evaluated on the web
against ground-truth faults. LLM-driven testers act slowly per step; a 2026 empirical study
found them less efficient than RL testers. LLM-derived rewards and potentials exist for games
and control; their value in testing was untested. Generalisation research warns that agents
overfit even large training sets (Cobbe et al.; Procgen) and that state abstraction dominates
exploration (Tang et al.). Fuzzing methodology recommends ground-truth bugs, repeated trials
and censoring-aware time-to-bug analysis (Klees et al.; Magma).

## 3. The system

- **Environment**: Playwright, headless Chromium, fresh context per episode; an action
  registry of clicks, typed values in five categories (valid, boundary min/max, empty, wrong
  type), selects, toggles and navigation; DOM-quiescence settling.
- **v1 learner**: action-conditioned dueling Double DQN with one scorer shared by every
  action (Q(s, φ(a))), a zero-initialised residual head for adaptation, prioritised 3-step
  replay and search-guided ε-exploration; features v3.1 (95 per action, 25 for the state).
- **Dynamic LLM reward**: `qwen2.5:7b-instruct` judges task progress on each new page
  (anchored 0–10), turned into a progress-record term and an outcome bonus; cached by content.
- **Oracle**: declarative assertions over recorded pages; a candidate is a fault only if it
  does not occur on the build without that fault's patch, reproduces twice, and survives ddmin.
- **Product**: URL / static site / Dockerfile repository → exploration → evidence → report,
  with a web UI.

## 4. Method

All evaluations were pre-registered before any result existed (`docs/methodology/`);
every later change is a dated amendment stating what changed and whether results had been
seen. The application is the unit of inference; time to the first verified fault is
analysed as right-censored data (restricted mean time, paired by application, application
bootstrap, stratified log-rank).

- **Benchmark** (v1): six applications, twelve seeded faults in five classes, each admitted
  by mechanical checks.
- **Application suite** (v2 held-out): eight applications with real backends (Python and
  Node), 23 seeded faults in 11 subtypes, all admitted with browser and Docker checks.

## 5. Experiment 1 — Does pretraining transfer? (pre-registered)

291 jobs, 0 failures [`transfer_final.md`, `transfer_final_addendum.md`].

| Comparison | All folds | Pristine folds |
|---|---|---|
| Pretrained vs scratch DQN (primary) | median Δ +0 [−749, +290], p = 0.53 | −310, p = 0.66 |
| Pretrained vs scratch bandit | −280 [−732, +188], p = 0.086 | −280, p = 0.11 |
| With vs without the LLM reward | +17 (p = 0.75); −57 (p = 0.62) | — |
| Pretrained DQN vs search_A0 / random | p = 0.006 / 0.02 | p = 0.23 / 0.53 |

- **Transfer**: no evidence that pretraining speeds up verified-fault discovery.
- **Learning in-app**: learners verify faults in more runs (DQN 0.33, pretrained bandit 0.42)
  than search (0.17), random (0.11) and the hand-priority baseline (0.06); clearest effect,
  carried mainly by one development application.
- **LLM reward**: no detectable benefit in either ablation.
- **Oracle**: no verified finding on any clean build.

## 6. Experiment 2 — Context or memorisation? (pre-registered diagnostic)

Twelve generated variants of one workflow (vocabulary incl. pseudo-words, structure, decoys),
four input-masked arms; 228 jobs, 0 failures [`contextual_diagnostic.md`].

- **Genuine state–action contextual learning: not supported.** Within-label AUROC 0.51
  [0.44, 0.59] against a 0.70 criterion; label × state is 7% of Q's variance.
- Memorisation hypotheses (action, lexical, application): **inconclusive** at three seeds.
- Arms that see the state find faults on held-out variants in 19–20 of 24 runs, against
  2 of 24 for search and random; the label-blind arm does as well as the full one.

## 7. Why the language model did not help

- **As a reward** [`llm_reward_validation.md`]: the progress score peaks at fault-observable
  pages on the two development applications (AUROC 1.0) but not on the four pristine ones
  (0.38–0.73); within applications, more LLM reward goes with earlier discovery only on the
  development application; its terms largely coincide with novelty.
- **As a labeller** [`v2_stage0.md`, `v2_label_validation.md`]: page kind 0.68 / control role
  0.78 at Stage 0; after one revision, 0.93 / 0.83 on the data it was revised from but
  0.61 / 0.75 on fresh pages — a concrete instance of fitting the revision data. Field
  meanings were reliable (0.94–0.96).

## 8. The hybrid tester (v2) **[v2 pending: results]**

Design (`docs/methodology/2026-10-06-hybrid-v2-plan.md`): page abstraction from markup
(kind, location, main heading, form state, alert), an archive of abstract states with exact
replay (Go-Explore), test operators from equivalence partitioning, boundary values and
state-transition testing, a Thompson-sampling bandit over semantic keys (with an optional
cross-application prior), and specification-free invariants verified by replay.
Pre-registration: `docs/methodology/hybrid-v2-evaluation-preregistration.md`.

## 9. Claims — shown and not shown **[author: review]**

| Claim | Status |
|---|---|
| The oracle confirms seeded faults without false positives | shown (0 on clean builds) |
| Learned exploration finds more faults than non-learning baselines within budget | shown, narrowly (development-fold dominated) |
| Pretraining transfers to unseen applications | not shown (null) |
| The agent learns state–action context | not shown (not supported) |
| A small LLM's progress reward helps | not shown (null; validation explains why) |
| A small LLM can label pages/controls for the tester's state | not shown (two failed gates) |
| **[v2 pending]** | |

## 10. Limitations

Six and eight small applications built by one developer; within-family results only; the
v1 state had no page semantics; seeded faults are an incomplete proxy for real faults; three
to six seeds give wide intervals; the specification-free oracle is descriptive.

## 11. Future work **[author]**

## Appendix A — Amendments and deviations

All in `docs/methodology/` and in the reports' provenance sections; summarised in
`PROJECT_CONTEXT.md` §5.
