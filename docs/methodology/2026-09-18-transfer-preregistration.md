# Transfer experiment: pre-registration

**Committed 2026-09-18.** Frozen before the first multi-application training run and
before any benchmark application beyond the Deep fixture exists. Not to be revised after
seeing transfer results. Infrastructure bug fixes are permitted and must be recorded in
§13 with the commit that made them.

---

## 1. The claim under test

> A learned action prior, trained on several web applications, transfers to an application
> it has never seen — reaching a seeded fault in fewer browser interactions than the same
> learner trained from scratch on that application, and fewer than a non-learning search.

---

## 2. Semantic representation, and an honest account of what supports it

### 2.1 What is used

The policy's action representation is `semantic_action_features.py` v2, 80 dimensions:

| block | width | contents |
|---|---|---|
| action type | 10 | one-hot over `ActionType` |
| semantic label | 48 | frozen `all-MiniLM-L6-v2`, PCA-projected, unit norm |
| element role | 10 | link / submit control / button / select / text entry / rapid click / navigation move / page reload / viewport / no-op |
| flags | 8 | navigational, in viewport, opens new tab, newly revealed, has href, fixed action, form control, repeatable probe |
| numeric | 4 | value ordinal, label length, taken here, taken this episode |

**Excluded from the policy by construction**: hashed labels, URLs or URL hashes, DOM ids or
id hashes, CSS selectors, element indices, action-slot indices, application identifiers.
Hashes remain in use for state identity, deduplication, novelty counting and the archive —
identity is for counting, semantics are for learning.

The primary arm uses the **engineered action-semantic prior**: the projected embedding plus
the anchor construction. The **raw 384-dim MiniLM embedding is an ablation**, not the
primary.

### 2.2 What the evidence actually supports — recorded because it is weaker than it first looked

`reports/probe_semantic_vs_hash.md` and `reports/probe_anchor_audit.json`.

**The pre-registered primary statistic failed for both representations.** Mean
cosine(synonyms sharing no word) − mean cosine(lexical traps): hash **−0.375**, MiniLM
**−0.038** (95% CI [−0.150, +0.075], containing zero). MiniLM scores `Place order` against
`Cancel order` as highly as it scores genuine synonyms. Sentence embeddings encode topic,
not polarity.

**Consequence, treated as a hard design constraint:** raw pairwise label cosine is never a
policy feature. The embedding enters as a feature vector for a learned scorer, or through
an anchor projection. Never as a similarity between two labels.

**The anchor projection's AUC 1.000 is not clean pre-registered evidence.** Audited from
git: the anchors were frozen at 11:38:43 (`0d104f3`), seven minutes before the probe was
committed at 11:45:14 (`8329f51`) — but `COMMIT_LABELS`/`ABANDON_LABELS`, the population
the AUC is computed over, shipped *in the probe commit*, i.e. were written after the
anchors and after the primary statistic came back negative. The population was chosen with
knowledge of both.

**The lexical control is what the result turns on**, and it is unflattering:

| population | provenance | A1 word list alone | anchor on projected 48-d | hash |
|---|---|---|---|---|
| self-chosen (post hoc) | written with the anchors in view | **0.990** `[0.958, 1.000]` | 1.000 `[1.000, 1.000]` | 0.764 `[0.542, 0.944]` |
| lexical traps | strings predate the anchors by two weeks | **0.820** `[0.600, 0.980]` | 0.960 `[0.760, 1.000]` | 0.640 `[0.240, 1.000]` |

CIs bootstrap the **24 (or 10) labels**, not the 144 (or 25) ordered pairs — the labels are
the independent unit.

On the population where the result was first reported, **a plain frozen word list reaches
0.990 where the embedding-based projection reaches 1.000**. The embedding contributes
0.010, which is nothing. On the independent population the gap is larger (0.820 → 0.960)
but the intervals overlap heavily at n=5×5.

**Recorded reading:** most of the commit-versus-abandon separation is attributable to **the
word list**, not to embedding geometry. The defensible description of the representation is
an *engineered action-semantic prior* in which the anchor construction supplies task
structure and the embedding supplies coarse geometry. **No claim is made that sentence
embeddings understand web action semantics.**

**Generalization claim, corrected.** The projection's held-out check covered 28 phrases
absent from the fit vocabulary as *strings*, but only **11 of 28 absent as concepts**. The
earlier "3.85 random SDs on words the projection never saw" is a **held-out-word** result.
It shows the projection is not memorising surface strings. It does **not** show transfer to
unfamiliar concepts.

### 2.3 Frozen anchor vocabulary, with rationale

Not to be extended after inspecting any benchmark application. Each anchor is a generic
web-UI phrase; none was taken from a fixture's DOM.

**Positive** — phrases denoting commitment of a workflow:

| anchor | rationale |
|---|---|
| `submit the form` | the canonical form-completion act, present in almost every application |
| `confirm and place the order` | commerce commit, the most common transactional terminal step |
| `proceed to the next step` | multi-stage wizard advance, independent of domain |
| `complete the purchase` | payment/commit phrasing distinct from "order" vocabulary |
| `save these changes` | CRUD/settings commit, the non-commerce analogue of placing an order |
| `create the account` | registration commit, covering signup vocabulary |

**Negative** — phrases denoting abandonment, reversal, or navigation away:

| anchor | rationale |
|---|---|
| `cancel and discard changes` | the direct antonym of the save/commit anchors |
| `go back to the previous page` | backward navigation, the commonest non-progress action |
| `return to the home page` | exit-to-root, which abandons any in-progress workflow |
| `log out of the account` | session termination, an unrecoverable abandonment |
| `read the terms and conditions` | the archetypal distractor link, present on nearly every page |
| `contact customer support` | second archetypal distractor, distinct in vocabulary from the first |

---

## 3. Benchmark application suite

**Target 6, minimum coherent 4, design capacity 8.** Built sequentially and completely;
never six half-finished applications. **The Deep fixture is excluded from every transfer
and generalization result** and remains available only for existing mechanistic
experiments and as a demo/smoke fixture.

| # | working name | domain | rendering | intended structural contrast |
|---|---|---|---|---|
| 1 | `shop_catalog` | commerce / cart | server-style static multi-page | deep cart workflow, high distractor density |
| 2 | `booking_desk` | reservations | client-rendered, single page with views | client-side routing, no full page loads |
| 3 | `admin_records` | CRUD / admin | table-driven multi-page | list/detail/edit, wide branching, low depth |
| 4 | `support_portal` | ticketing / forms | form-heavy multi-step | long forms, validation gates |
| 5 | `media_library` | content management | grid + modal dialogs | modal interaction, no URL change |
| 6 | `account_hub` | account management | mixed | settings toggles, destructive confirmations |

Applications 5 and 6 are built only if time permits. **Below 4 applications, stop and
consult the advisor.**

Each application ships: distinct vocabulary, a genuinely different DOM structure, at least
one seeded functional fault with deterministic fault metadata and assertion, reproducible
reset, a paired clean build where practical, and characterization metrics
(`d*`, `b̄`, `b̄^d*`, `H`, `D`, `|S|`, `R`).

**Fold assignment is leave-one-application-out**, mechanically: fold *i* trains on every
application except *i* and evaluates on *i*. No fold is chosen; the assignment is the
identity of the suite.

---

## 4. Arms

| arm | definition |
|---|---|
| **A. Search** | `HandPriorityPolicy` — non-learning, fixed hand-designed action priority, no training. Identical code to A0. |
| **B. Bandit from scratch** | `ContextualBandit` trained on the held-out application only, from random initialization, within the target-app budget. |
| **C. Bandit pretrained** | `ContextualBandit` pretrained on the other N−1 applications, then evaluated on the held-out application. Shared trunk frozen; small per-application adaptation head, with the same target-app budget B is given. |
| **D. AC-DQN pretrained** *(secondary)* | Same pretraining protocol, bootstrapped target. An ablation; it must not consume the core schedule. |

Zero-shot (no target-app adaptation) is reported separately from adapted, where feasible.

---

## 5. Primary metric and censoring

**Primary: budget-to-first-verified-fault** — the number of browser interactions consumed
before the bug oracle *confirms* a seeded fault (assertion fired, replay reproduced,
minimization succeeded). A time-to-event quantity, measured in interaction steps.

**Archive replay counts toward the budget.** Route replay is browser interaction, and
excluding it would hand the archive-based arms free actions.

**Right-censoring.** A run that does not produce a verified fault within budget is recorded
as `censored = true`, `time = B`. It is **not** discarded and **not** recoded as "0
completion". The analysis preserves the censoring.

**Interaction budget `B` = 2,000 steps per run per application.** At the measured ~2.5
steps/s this is ~13 minutes per run.

**Supporting metrics** (never primary): fault discovery rate, discovery-by-budget curve,
Review, S1, max depth, unique states, valid-action rate, reset cost, exploration coverage.

**Completion rate is not the primary transfer statistic.** At 3 seeds, per-application
percentages are illustrative only, and no claim is made about any individual application.

---

## 6. Seeds, pairing, and the unit of inference

- **3 seeds** initially: 0, 1, 2.
- **The fold (application) is the unit of inference**, not the seed. Seeds are averaged
  *within* a fold before the fold-level comparison.
- **Paired by seed within a fold**: scratch seed *k* and pretrained seed *k* use the same
  seed, the same budget and the same evaluation.
- With 6 folds the paired fold-level comparison has n=6. With 4 folds, n=4. Both are small
  and will be reported as such.

---

## 7. Statistical analysis

**Primary.** Per fold, the paired difference in mean budget-to-first-verified-fault
(C − B), averaged over the fold's 3 seeds. Across folds, the **paired difference** is
summarised by its median and a **bootstrap 95% CI resampling folds**. With n=6 a
bootstrap over 6 units is itself coarse; this is stated in the result rather than hidden.

**Censoring-aware secondary.** Kaplan–Meier discovery-by-budget curves per arm, pooled
across folds, with a log-rank comparison. Pooling across folds violates independence
(runs within a fold share an application), so the log-rank is reported as **descriptive**
and the paired fold-level statistic remains primary. A paired, censoring-aware alternative
(e.g. a stratified log-rank stratified by fold) is preferred where the data support it.

**Not done**: no test is selected after seeing results; no per-application significance
claim at 3 seeds; no combination of seeded and unknown findings into one metric.

---

## 8. Top-up rule, 3 → 6 seeds

Evaluated **once**, immediately after the 3-seed run completes for *every* fold, and
**before** any fold-level comparison is inspected beyond the quantity named below.

> **Rule.** For each fold, compute the paired fold-level difference
> `Δ_i = mean(C) − mean(B)` in interaction steps over the 3 seeds. If `|Δ_i| < X`, run
> seeds 3, 4 and 5 for **that fold**, for arms B and C, under the identical protocol.

**`X = 200` interaction steps**, which is **10% of the 2,000-step budget**.

**Rationale, tied to the metric's scale rather than picked for convenience.** On these
applications a full workflow traversal — landing page to the fault-triggering terminal
action — is a 4–6 decision chain, and the exploration overhead of attempting one such
traversal once is empirically on the order of 100–200 steps on the Deep fixture at
comparable branching. A fold-level difference smaller than that is smaller than *one extra
attempt at the workflow*, and is therefore not practically meaningful; it is also well
inside what 3 seeds can resolve. Folds separated by more than one workflow attempt do not
need more seeds; folds separated by less cannot be resolved without them.

**Reporting.** The 3-seed analysis and the topped-up analysis are reported **separately and
both**, with the topped-up folds named. The primary result is the topped-up one where a
top-up occurred; the 3-seed result is never deleted.

**What is forbidden.** Choosing folds for extra seeds after seeing which look interesting;
topping up an arm rather than a fold; topping up after inspecting the direction of `Δ_i`
rather than its magnitude.

---

## 9. Fault taxonomy

| class | definition | oracle |
|---|---|---|
| `state_corruption` | a value the user supplied is altered or lost across a transition | deterministic assertion on observed page state |
| `constraint_bypass` | a validation rule the application declares is not enforced | assertion that the guarded state was reached with invalid input |
| `broken_transition` | a control that should change state does not, or reaches the wrong state | assertion on URL/state after the action |
| `calculation_error` | a derived value shown to the user is wrong given its inputs | assertion recomputing the value from inputs |
| `persistence_failure` | a committed change is not reflected on re-read | assertion after a reset-and-revisit |

Every seeded fault declares exactly one class, a trigger condition, an expected behaviour,
an observable violation, and a deterministic assertion.

**Unknown bugs** — anything the oracle did not seed — are **qualitative findings only**.
They are never counted in a quantitative metric and never combined with seeded faults.

---

## 10. Evaluation procedure

1. Deploy the held-out application (deterministic reset).
2. Run the arm for up to `B` = 2,000 interaction steps.
3. Every candidate finding is passed to the deterministic oracle.
4. On assertion pass: replay independently; require reproduction per the fault's rule.
5. On reproduction: minimize the trace; the minimized trace must still reproduce.
6. Record `budget_to_first_verified_fault` = steps consumed at the first confirmed finding;
   otherwise censor at `B`.

The RL reward never receives seeded fault labels. The oracle never uses an LLM.

---

## 11. Exclusions

A run is excluded **only** for demonstrated infrastructure invalidity — browser crash,
container failure to start, oracle harness error — and the exclusion is reported with the
raw result retained. Unfavourable results are never excluded. Seeds are never rerun for
being unfavourable.

---

## 12. What this design cannot establish

- n=6 folds (or 4) is small. Nothing here supports a per-application claim.
- All applications are authored by the same developer for this project. Diversity is
  deliberate but not natural; a real application distribution is not sampled.
- The projection's held-out evidence is held-out-word, not held-out-concept (§2.2).
- The anchor projection's advantage over a plain word list is small and, on the population
  where it was first measured, within noise (§2.2).
- AC-DQN (arm D) is an ablation at reduced priority and may be underpowered.

---

## 13. Amendment log

Infrastructure fixes after freezing are recorded here with commit hashes. Methodology is
not amended on the basis of observed results.

| date | change | commit | reason |
|---|---|---|---|
| — | — | — | — |
