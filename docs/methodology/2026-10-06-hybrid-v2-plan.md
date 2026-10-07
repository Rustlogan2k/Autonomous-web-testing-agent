# Hybrid tester (v2): design and plan

**Status: plan, 2026-10-06.** Written after both frozen evaluations (the transfer experiment
and the contextual diagnostic) and the post-hoc LLM-reward validation had finished, and
before any v2 code or result exists. It fixes the design, the data split, the gates and the
schedule. The **evaluation** is pre-registered separately at the code freeze (2026-10-13),
before any agent touches a held-out application. Nothing here changes v1 or its reports.

## 1. Why v2, from the evidence

| Finding | Source | Consequence for v2 |
|---|---|---|
| Learned agents beat non-learning baselines in-app; pretraining gave no transfer benefit | `reports/transfer_final.md` | learning stays, as online adaptation inside each application |
| No state–action contextual learning; label × state 7% of Q's variance; layout dependence | `reports/contextual_diagnostic.md` | the state must carry what a page and a control *are* |
| The LLM progress score peaks at fault-observable pages only on the development apps (AUROC 1.0 vs 0.38–0.73 pristine); it rewards the happy path and duplicates novelty | `reports/llm_reward_validation.md` | the LLM labels pages and controls; it is not a scalar reward |
| Deep faults (9–16 contextual steps) unfound on admin_records / booking_desk | primary results | archive-and-return exploration [ecoffet2021first] |
| LLM-as-policy testers act slowly and repeat invalid actions | [liu2026webgui] | the LLM stays off the per-step path: one cached call per new page |
| Abstraction choice dominates exploration and testing results | [tang2017exploration, liu2026webgui, chang2023webqt] | abstraction granularity is a tuned, then frozen, design choice |

## 2. Data split

| Set | Role in v2 |
|---|---|
| 6 benchmark apps (`tests/fixtures/benchmark/`) and the 12 ctxbench variants | **development only** (building, tuning, debugging, any prior). Kept unchanged: they are the evidence for v1. |
| Gitea (third-party, Docker) | **development only**: the server-backed app for replay/reset robustness and invariant false-positive checks |
| 8 suite apps (`e2e-suite` branch) | **held-out evaluation only**. Before the freeze, the only permitted contact is a pre-declared infrastructure smoke test (launch, reset, the admission's benign traces; no agent, no fault traces). |

Disclosed limitation: the suite apps and their faults were built by the same developer.

## 3. Pipeline

1. **Launch** (reused): URL, static site, Dockerfile repository, or a suite app's server with
   clean, faulty and per-fault-control builds; fresh browser context per episode; server state
   reset between episodes.
2. **Observe** (reused): rendered page, visible text, form state, the action registry's controls.
3. **Understand** (new): once per distinct page, cached, the LLM returns structured labels from
   closed vocabularies — page function, task stage, and each control's role (commit, edit,
   add/remove, navigate, filter; field meaning such as email, date, quantity). Deterministic
   facts (input types, form membership, ARIA roles, validation messages) are used first; the
   LLM fills only what they cannot decide. Fallback: deterministic labels only.
4. **Remember** (new): an archive of abstract states (page function + stage + form-status
   signature: fields filled / valid, error shown). Each entry stores its shortest known trace,
   visit count, operators tried, and anomalies observed.
5. **Decide** (new): choose an archive entry to return to and an operator × control to apply.
   Decision layer: v1's action-conditioned Q-network over abstraction features (state = archive
   entry, candidates = operator × control), run as a bandit (γ = 0) or full RL (γ > 0); γ chosen
   on development apps. Online, per application. Optional cross-app prior over (stage, role,
   operator) as a stretch arm.
6. **Return and probe** (new): deterministic replay to the entry, then the operator, then a few
   free exploration steps; new abstract states join the archive. **Every browser action,
   replays included, counts toward the interaction budget**; oracle verification does not
   (as in v1).
7. **Check** (expanded): (a) specification assertions when present; (b) generic invariants
   (§5); (c) optional LLM expectations, reported only as unverified.
8. **Verify** (reused): replay ×2, ddmin minimization, per-fault controls on seeded builds.
9. **Report** (reused, extended): verified findings with minimal traces and evidence;
   unverified candidates separately; the archive as an exploration map.

## 4. Operator library (draft; frozen at the code freeze, owner review requested)

Derived from standard test-design techniques, not from any application's faults:

| Technique | Operators |
|---|---|
| Equivalence partitioning / boundary-value analysis | valid typical value; boundary minimum and maximum; just outside a declared bound (min/max/maxlength); empty required field; wrong type or format; very long text; whitespace-only and special characters |
| State-transition testing | go back mid-task then continue; edit an earlier step after reaching the review; reload at a stage; resubmit a commit (double submit); revisit a later step's URL out of order; repeat a completed task |

Sources to be added to `docs/references/` with verification: Myers, Sandler and Badgett,
*The Art of Software Testing* (equivalence partitioning, boundary values); Beizer,
*Software Testing Techniques* (state-transition testing).

## 5. Generic invariants (oracle layer b)

Reload stability; typed-value round-trip (what was entered reappears unchanged where it is
echoed); arithmetic consistency (totals equal their parts); validation consistency (a value
rejected on one path is not accepted on another); idempotent resubmission (a repeated commit
does not duplicate data); no server or console errors. Three exist as suite extensions
(`region_stable_across_reload`, `region_arithmetic`, `input_absent`); per-invariant
normalization (dates, currency, whitespace). Every candidate still needs replay ×2 and
minimization.

## 6. Concerns and mitigations

| # | Concern | Mitigation |
|---|---|---|
| 1 | LLM labels wrong or inconsistent | closed vocabularies, JSON schema, deterministic facts first, cache; **Stage 0 gate** on ctxbench ground truth (incl. pseudo-word labels) and a hand-checked development sample; deterministic fallback |
| 2 | Archive too coarse or too fine | signature defined above; tuned on development apps only; growth logged; frozen |
| 3 | Replay to a state fails on servers (accumulated records, shifting ids) | per-episode server reset; replay success rate reported; Gitea for development; infrastructure-only suite smoke test |
| 4 | Generic invariants raise false alarms | replay ×2 + minimization; normalization; false-positive rate measured on clean builds and Gitea; in evaluation, seeded-fault verification (primary) is scored separately from what invariants alone flag, with their clean-build false-positive rate |
| 5 | Operators biased toward the suite's known faults | operators from standard techniques only, frozen before held-out contact, owner review; disclosed |
| 6 | Hidden budget advantage from replays | replays count toward the budget |
| 7 | The learner adds nothing over the explorer | mandatory ablation (with vs without learner), reported either way |
| 8 | Gains cannot be attributed | arms: random, v1 DQN, hybrid, hybrid without learner, hybrid with deterministic labels only |
| 9 | Low power (8 apps, ~3 faults each) | pre-registered survival statistics with app-clustered bootstrap; inconclusive reported as such |
| 10 | Schedule | build in order of value; hard freeze 2026-10-13; slipping parts drop out as arms, not as rigour |
| 11 | A genuine unseeded bug on a clean build | inspected by hand and classified; reported, never silently counted either way |

## 7. Gates and schedule (deadline ≈ 2026-10-27)

| Dates | Work | Gate |
|---|---|---|
| 06–07 Oct | **Stage 0**: LLM labelling accuracy and consistency; LLM progress vs ctxbench ground truth; near-miss analysis on development apps | labelling accurate enough to abstract on; else deterministic labels |
| 08–11 Oct | Labeller, abstraction, archive, operators, invariants; development apps + Gitea | deep faults on development apps reached by the hybrid without learning |
| 12–13 Oct | Decision layer; suite runner; infrastructure smoke test; **code freeze and evaluation pre-registration** | everything runs end to end on development apps |
| 14–17 Oct | Held-out runs (5 arms × 8 apps × 5 seeds, 2,000 interactions) | — |
| 18 Oct | Frozen analysis | — |
| 19–27 Oct | Final report, presentation, demo; buffer | — |

## 8. Addendum 2026-10-06 (evening): Stage 0 result and the labeller re-test (option A)

The Stage 0 gate failed (`reports/v2_stage0.md`; page kind 0.68, role 0.78, cross-vocabulary
agreement 0.74), so by the pre-set rule the tester uses deterministic labels. The owner chose
to revise the labeller once and re-test it, under these terms fixed before the revision ran:

- **Labeller v3** changes only what the Stage 0 errors motivate: (1) save / advance / commit
  merge into one role, `submit`; (2) the markup decides what it states — a page with two or
  more visible input fields that are not search or filter fields is a `form`, a page with a
  password field is `login`; radio buttons and checkboxes are choice fields, not buttons;
  (3) the prompt tells the model to judge a page by what it contains, not by words in its title.
- **Fresh validation set**: 69 pages from the six benchmark applications (development data),
  never labelled by any labeller version, with hand-written ground truth committed before v3
  ran (`tests/fixtures/v2_label_validation/`).
- **Gate for v3** (thresholds unchanged): page-kind accuracy ≥ 0.85 and control-role accuracy
  ≥ 0.80 on the fresh set, **and** cross-vocabulary agreement ≥ 0.80 on the ctxbench probes
  (the only place it can be measured; v3 was revised from that data's errors, so this
  condition is conservative only in the sense of being an extra requirement).
- Pass: LLM labels become the main arm, deterministic labels the ablation. Fail: deterministic
  labels stay, and the result is reported as a negative finding. No further revision.

## 9. Addendum 2026-10-06 (night): decision layer and cell identity, before any held-out contact

- **Decision layer changed from the plan's §3 step 5.** One return-and-probe per 40-step
  episode gives about fifty decisions in a 2,000-interaction run: too few for v1's
  Q-network to learn anything. The learner is a Thompson-sampling bandit over semantic keys
  — (page kind, operator, target type), backing off to (operator, target type) and
  (operator) — with each general key's evidence as the specific key's discounted prior
  (`hybrid/learner.py`). It is still reinforcement learning (a contextual bandit: one-step
  decisions, reward observed). Its rewards are the tester's own observations (an alert, new
  abstract states, generic-invariant candidates), never the seeded-fault oracle. Its
  learned evidence exports as a prior, which is how the optional cross-app prior is formed.
- **Cell identity gains the page's main heading** (normalised), found on a development app
  (booking_desk) where a single-page application puts its review, outcome and list views on
  one URL and the cells could not tell them apart. Headings identify cells within one
  application only; the learner's keys never contain them.

## 10. Addendum 2026-10-06 (late): the pre-declared suite infrastructure test

Declared before it runs. The only contact with the held-out suite before the freeze is
`hybrid/suite_smoke.py`: for every suite application it builds the variants privately, starts
the servers, and replays **the admission's own benign walks and fault witnesses** (already
public in each manifest) through the v2 runner's serving and monitor path
(`hybrid/suite.py`), with a scripted policy. It passes when every witness is confirmed by
the monitor and no benign walk raises a confirmed finding. No agent, operator, learner or
labeller runs on a suite application; nothing about the agent is changed in response to it.

## 11. Addendum 2026-10-07: development changes (development applications only; no held-out contact)

Found and fixed on the development applications, each in its own commit with the evidence:

- **Replay**: steps that raised a notice were pruned from stored walks (failed returns
  6/14 → 0/28 on support_portal / booking_desk).
- **Cell granularity**: exact field/toggle combinations made 150–225 cells on account_hub;
  cells now record a page's form as empty / partial / full (~20 cells).
- **Operators**: form completion ticks checkboxes and chooses unset radio groups; the primary
  action falls back to an "add" control; new `choose_option` (equivalence partitioning over a
  select's options); every operator follows through (complete and submit, up to four
  submissions) after its probe; six free steps after each operator, then a new decision
  (19 → 55–66 decisions per 800 interactions).
- **Invariants**: validation consistency now compares the same field on the same page across
  walks, single-factor submissions only, "accepted" only when the page moves on (19
  clean-build false alarms before); reload stability narrowed to entered values that vanish
  (whole-page diffs flagged one-time messages and open editors).

The operator list in §4 gains `choose_option` and follow-through; it is frozen with the code.
