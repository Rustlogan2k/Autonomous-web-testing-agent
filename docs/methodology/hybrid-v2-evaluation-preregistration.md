# Hybrid tester (v2): evaluation pre-registration

**Status: FROZEN on owner approval, 2026-10-08.** Binding from the commit that records the
owner's approval (the line below is filled in by that commit). Written after five development
rounds and before any agent has run on any validation application; the only contact with
the validation set so far is its admission (`validation_suite/admission/*.json`: browser
checks, scripted witnesses and a clean random walk, no tester arm).

Owner's approval: **[pending]**

History: this replaces the 2026-10-06 draft (git history of this file), which named the
e2e suite as held out and seven arms. Amendment §12 of `2026-10-06-hybrid-v2-plan.md` made
the suite development data and the validation set held out; on 2026-10-08 the owner chose
to evaluate now, on the validation set as it stands, with the best development arms only
(§7). Design and history: `2026-10-06-hybrid-v2-plan.md`; development results:
`project-progress-report.md` §4.6, §5, §6. Nothing here is pooled with the frozen v1
experiment or the contextual diagnostic.

## 1. Questions

| | Question | Comparison |
|---|---|---|
| **H1 (primary)** | Does the hybrid tester find a verified seeded fault sooner than uniform random exploration on applications it has never seen? | `hybrid_nolearn` − `random` |
| H2 | Does the learned decision layer help, over the same tester choosing uniformly? | `hybrid_bandit` − `hybrid_nolearn` |
| H3 | Does the hybrid with the learner beat random? | `hybrid_bandit` − `random` |

Why these arms: across five development rounds (42 matched runs on 14 applications)
`hybrid_nolearn` verified a fault in the most runs (32/42) and `hybrid_bandit` was tied with
it (30/42; per-application mean difference −49, 95% interval [−279, +190]); every other
variant was no better, and the standalone curiosity-RL tester and `random` were
significantly worse (17/42 and 18/42). `random` is the floor. `hybrid_nolearn` is the
primary arm because it found the most and is the simpler; H2 tests the learner directly.

## 2. Applications, arms, budget

- **Applications:** the eight validation applications as admitted and committed at
  `5f45a26` (`validation_suite/apps`, tree `ffd4fda3a76b07800ef3665a011bcc2e5fb7895a`):
  dutyday, fosterly, larder, markbook, quizcraft, sideline, skylark, tally; 24 seeded
  faults. These are the versions without a database server or sign-in; the planned rebuild
  of the validation set (validation `PLAN.md` §11) does not apply to this evaluation.
- **Arms** (`hybrid/run.py`):

| Arm | What it is |
|---|---|
| `random` | uniform over the page's valid actions |
| `hybrid_nolearn` | hybrid pipeline (page abstraction, archive with exact returns, test operators with follow-through); untried operators chosen uniformly |
| `hybrid_bandit` | the same with the Thompson-sampling decision layer over semantic keys |

- **Hybrid configuration**, identical in both hybrid arms, as at the freeze commit:
  weighted free exploration, `depth_bias` 2.0, six free steps between operators, cells =
  page kind, path template, main heading, form fill level, alert, whether a submit action is
  offered; site navigation never submits; operators take a page's submit actions in turn;
  deterministic labels; operators complete_step, choose_option, invalid_input,
  edit_after_review, back_and_resume, reload_and_resume, double_submit, collection_churn.
  Development options stay off (pairwise, family choice, form-submit buttons, curiosity
  exploration, alternative outcome signals).
- **Budget:** 2,000 interactions per hunt (every browser action counts, replays included);
  40-step episodes; 80 ms settle window; seeds 0–4 for every arm on every application
  (120 hunts). Clean-build controls: every arm once per application on its clean build
  (24 hunts).
- **Execution:** local machine, 4 workers, no other workload (no application builds running).
  A job that fails for infrastructure reasons (e.g. a page-load timeout) is rerun unchanged
  and the rerun is reported.

## 3. Primary metric and statistics (the v1 primary's, unchanged)

- Time to the first verified seeded fault (two reproductions, per-fault control build,
  ddmin minimisation), right-censored at 2,000.
- Per application: restricted mean time over seeds. Paired difference per application;
  **median over the eight applications** with a 95% percentile bootstrap over applications;
  stratified log-rank test (strata = application) as secondary.
- **Decision rule for H1–H3:** *supported* if the median difference is negative and its
  interval excludes 0; *refuted* if positive and excluding 0; otherwise *inconclusive*.
  Reported with the per-application differences and the number of applications favouring
  each arm.
- Also reported for every arm: discovery rate (runs verifying ≥ 1 fault), faults verified per
  run, distinct faults verified per application, clean-build false positives.
- Analysis: `python -m web_testing_agent.hybrid.analyze --runs runs/v2-eval --out
  reports/v2_evaluation --protocol freeze-2026-10-08`, as committed at the freeze.

**Known before running:** with eight applications the median rule is strict. Applied to the
development data (14 applications), H1 was *inconclusive* by this rule (median −277,
interval [−730, +17]) although the log-rank test gave p = 0.007. An inconclusive verdict
here is therefore a real possibility and will be reported as such, never re-analysed with a
different rule.

## 4. The specification-free oracle, reported separately

Reproduced generic-invariant candidates per arm (reload stability, typed-value round trip,
validation consistency), their attribution to seeded faults by control builds, and those that
also reproduce on clean builds (each inspected by hand). Descriptive; never enters §3.

## 5. Claim limits

- The validation applications and the tester were built by the same developer. The
  validation set was planned by a separate process without access to the tester's code or
  results, and its fault designs were fixed before any tuning; still disclosed.
- Eight applications are the unit of inference; intervals will be wide.
- Results concern these eight small applications with seeded faults, in their versions
  without a database server or sign-in, not arbitrary production sites.

## 6. What is frozen

The code at the freeze commit (tag `v2-eval-freeze`): `src/web_testing_agent/hybrid/`, the v1
code it reuses, `e2e_suite/harness/`; the validation applications at tree `ffd4fda3`; this
document; the analysis protocol `freeze-2026-10-08`. Any later change is a dated amendment
stating what changed, why, and whether any held-out result had been seen.

## 7. Owner's decision recorded

On 2026-10-08 the owner chose to freeze and evaluate now, with the best development arms
only, rather than wait for the database/sign-in rebuild of all applications. Consequence,
accepted: this run is the held-out evaluation of the hybrid tester; no tuning follows it.
