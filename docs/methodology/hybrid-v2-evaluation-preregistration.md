# Hybrid tester (v2): evaluation pre-registration

**Status: DRAFT (2026-10-06/07, overnight).** Becomes binding when the owner approves it and
it is committed with the frozen code; the commit that changes this line to "FROZEN" is the
timestamp. Items marked **[freeze]** are filled from development results before that commit.
No agent has run on any held-out application; the only contact so far is the declared
infrastructure test (`reports/v2_suite_smoke.json`).

Design and history: `2026-10-06-hybrid-v2-plan.md` (+ addenda §8–§10). This evaluation is
separate from the frozen v1 experiment and the contextual diagnostic; no statistic is pooled
with theirs.

## 1. Questions

| | Question | Comparison |
|---|---|---|
| **H1 (primary)** | Does the hybrid tester find a verified seeded fault sooner than v1's learner on applications it has never seen? | `hybrid_bandit` − `v1_dqn` |
| H2 | Does it beat the strongest non-learning baseline and uniform random? | `hybrid_bandit` − `search_S`; `hybrid_bandit` − `random` |
| H3 | Does the learned decision layer help, over the same tester choosing uniformly? | `hybrid_bandit` − `hybrid_nolearn` |
| H4 | Does evidence pooled from the development applications (the cross-app prior) help on unseen ones? | `hybrid_bandit_prior` − `hybrid_bandit` |
| E1 (exploratory) | Do the LLM's page and control labels, which failed their accuracy gates, still help or hurt? | `hybrid_bandit_llm` − `hybrid_bandit` |
| S1 (descriptive) | What does the specification-free oracle find on its own? | per arm, §4 |

## 2. Applications, arms, budget

- **Applications**: the eight suite applications (`e2e_suite/`; 23 seeded faults, all
  admitted with browser and Docker checks): bagstreet, clinicflow, helpline, ledgerly, orbit,
  stackroom, tidewater, wayfarer. Never used for development.
- **Arms** (7), all under v1's hunt protocol and oracle:

| Arm | What it is |
|---|---|
| `random` | uniform over valid actions |
| `search_S` | v1's fixed feature-search score |
| `v1_dqn` | v1's DQN from scratch, as the primary's `dqn_scratch_nollm` (the primary found no benefit from the LLM reward) |
| `hybrid_nolearn` | hybrid tester, uniform choice of untried operators at count-weighted cells |
| `hybrid_bandit` | hybrid tester with the Thompson-sampling decision layer |
| `hybrid_bandit_prior` | as `hybrid_bandit`, starting from the development applications' pooled evidence (`data/v2_prior.json`, built from development runs only, capped at five pseudo-observations per key) |
| `hybrid_bandit_llm` | as `hybrid_bandit`, with labeller v3's page and control labels (exploratory; the labels failed their pre-set accuracy gates) |

- Hybrid settings **[freeze]**: free-exploration mode (`weighted` or `search`) and cell
  depth preference, chosen on development applications only; operator list as in plan §4.
- **Budget** 2,000 interactions per hunt (every browser action, including replays to archived
  states); 40-step episodes; 80 ms settle; seeds 0–4 for every arm on every application
  (280 hunts). Clean-build controls: every arm, seed 0, every application (56 hunts).
- Execution: local machine; jobs resumable; a job that crashes for infrastructure reasons is
  rerun unchanged and the rerun reported.

## 3. Primary metric and statistics (the v1 primary's, unchanged)

- Time to the first verified seeded fault (verification: per-fault control, two
  reproductions, ddmin), right-censored at 2,000.
- Per application: restricted mean time over seeds. Paired difference per application;
  **median over the eight applications** with a 95% percentile bootstrap over applications
  (10,000 resamples); stratified log-rank test (strata = application) as secondary.
- **Decision rule for each of H1–H4 and E1**: *supported* if the median difference is
  negative and its interval excludes 0; *refuted* if it is positive and its interval excludes
  0; otherwise *inconclusive*. Reported with the per-application differences and the count of
  applications favouring each arm.
- Secondary, reported for every arm: discovery rate (runs verifying ≥ 1 fault), faults
  verified per run, distinct faults verified per application.

## 4. The specification-free oracle (S1), reported separately

Generic-invariant candidates (reload stability, typed-value round trip, validation
consistency) reproduced 2/2 in fresh sessions. On faulty builds, a reproduced candidate is
attributed to a seeded fault when that fault's control build does not reproduce it; one
that reproduces on the clean build is not a seeded fault's. Reported per arm: seeded faults
flagged by the invariants alone, and reproduced candidates on clean builds — each of the
latter inspected by hand and classified as a false alarm or a genuine unseeded defect. None
of this enters §3.

## 5. Claim limits

- The suite was built by the same developer as the tester (operators are drawn from standard
  test-design techniques and frozen before any agent ran on the suite; disclosed).
- Eight applications are the unit of inference; intervals will be wide.
- Results establish behaviour on these eight small applications with seeded faults, not on
  arbitrary production sites; the specification-free oracle's numbers are descriptive.

## 6. What is frozen at the freeze commit

The code (`src/web_testing_agent/hybrid/`, the v1 code it reuses, `e2e_suite/`), the prior
file and its hash, this document, the analysis (`hybrid/analyze.py`). Any later change is a
dated amendment stating what changed, why, and whether any held-out result had been seen.
