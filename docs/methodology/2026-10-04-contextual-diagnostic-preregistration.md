# Contextual-generalization diagnostic: pre-registration

**Committed 2026-10-04, before any result of the frozen primary experiment exists** (at the
time of writing `runs/final-local/results/` is empty and the AWS half has not been
launched), **and before any diagnostic application, arm or analysis code exists.** The git
commit that adds this file is the timestamp. Nothing below may be changed after primary
results are inspected; any change after this commit is made by a dated amendment that
says what changed, why, and whether any result had been seen.

## 0. Status and separation from the primary experiment

This is a **secondary, diagnostic evaluation**. It is not part of the frozen transfer
experiment (`2026-09-18-transfer-preregistration.md` and its amendments of 2026-10-01 and
2026-10-03), and neither of those documents provides for integrating it. Concretely:

- its applications live in their own directory (`tests/fixtures/ctxbench/`), which the
  primary suite's `discover()` never reads; the primary suite, its folds, arms, budgets and
  analysis are untouched;
- its runs write to their own directories (`runs/ctx-*`), its analysis is its own script,
  and its results are reported in their own report (`reports/contextual_diagnostic.md`)
  and their own section of the final report, never pooled with primary statistics;
- nothing is built or run until the primary experiment has finished.

## 1. The question

Does the learned policy rank an action by **the state it occurs in**, or has it learned
that certain actions, words or applications correlate with reward? Four hypotheses, which
are not mutually exclusive and are assessed separately:

| | Hypothesis | What it predicts |
|---|---|---|
| H-act | **Action memorization**: value attached to particular actions regardless of state | a training-useful action keeps its rank where it is useless (decoys); a label's value does not depend on whether it is useful in the current state |
| H-lex | **Lexical memorization**: value attached to words | collapse on held-out vocabulary, with structure unchanged |
| H-app | **Application memorization**: value attached to one application's layout and flow | collapse on held-out workflow structures, with vocabulary unchanged |
| H-ctx | **Genuine state-action contextual learning** | a label's value tracks its usefulness in the current state; decoys avoided; performance retained under vocabulary and structure shifts |

## 2. The controlled application family (`ctxbench`)

One functional core, rendered under independently controlled factors by a **seeded
generator committed before any agent is run on its output**.

**Functional core.** A request task in four stages: choose an item; configure it (fields
with a validation gate); contact details; review; commit (outcome page). Every variant
implements the same functionality and the same seeded faults.

**Factor 1: vocabulary.**
- `V0`, `V1`: two training vocabularies (two different label sets for the same controls),
  so that a learner *can* learn that function is not tied to one word.
- `VS` (held-out, synonyms): common English synonyms, none used in `V0`/`V1`.
- `VU1` (held-out, unseen real words): meaningful English labels sharing **no word token**
  (case-folded) with any training label.
- `VU2` (held-out, pseudo-words): pronounceable non-words with no lexical meaning, so only
  role, structure and state can identify the useful control.
- Verified at build time and reported: zero token overlap of `VS`, `VU1`, `VU2` with the
  training labels; mean nearest-neighbour cosine similarity of their label embeddings to
  the training labels (reported, not thresholded).

**Factor 2: workflow structure.**
- `S1` linear multi-page wizard; `S2` hub-and-spoke (task-list hub, sections completed in
  any order, then submit). Training structures.
- `S3` single-page tabs on one URL; `S4` modal-dialog flow; `S5` reordered stages (details
  before configure). Held-out structures.

**Factor 3: decoys.**
- `D0`: none (training).
- `D1` (held-out): a label that advances the task in training appears on a control that is
  useless (does nothing, or returns to an earlier stage), while the real progress control
  carries a different label.

**Factor 4: state-dependent action usefulness (present in every variant).** Controls with
identical labels occur in several states with different effects, by construction: e.g.
"Save" advances only when the required fields are valid and otherwise shows errors; "Add"
is useful at the item stage and useless once the selection is full; "Edit" is useful only
on the review page when an entry must be corrected. The generator records, for every probe
state and every control, its ground-truth effect: **progress**, **no-op**, **regress** or
**decoy**.

**Seeded faults.** Every variant carries the same two seeded faults (classes from the
existing taxonomy), placed in the same functional stage, each requiring **at least four
steps of contextual interaction** (minimized witness length ≥ 4), with per-fault controls.
Every variant must be admitted by the existing validator (`scripts/validate_benchmark.py`)
under the same admission rules as the primary suite; a variant that fails admission is
fixed or dropped before any agent run, and the drop is reported.

**Variants.**

| Role | Variants |
|---|---|
| Training | `V0·S1·D0`, `V0·S2·D0`, `V1·S1·D0`, `V1·S2·D0` |
| Held-out: vocabulary | `VS·S1·D0`, `VU1·S1·D0`, `VU2·S1·D0` |
| Held-out: structure | `V0·S3·D0`, `V0·S4·D0`, `V0·S5·D0` |
| Held-out: decoy | `V0·S1·D1` |
| Held-out: combined | `VU1·S5·D1` |
| Genuinely held-out applications | the four pristine primary applications (`admin_records`, `support_portal`, `media_library`, `account_hub`), which no diagnostic agent is trained on |

## 3. Arms

Every learned arm is the primary DQN agent (`QAgent`, features v3.1, the dynamic LLM
reward at the primary settings, search-guided ε-exploration), pretrained on the four
training variants and nothing else, and differs **only** in which inputs the network sees.
Masking zeroes the named columns at every forward pass, in training and evaluation.

| Arm | Sees | Masked |
|---|---|---|
| `ctx_full` | everything | nothing |
| `action_only` | intrinsic action descriptors: type one-hot, label embedding, role, value category, and the columns `navigational`, `opens_new_tab`, `has_href`, `is_fixed_action`, `is_form_control`, `is_repeatable_probe`, `value_ordinal`, `label_length`, `anchor_commit_score`, `lexical_commit_score` | the whole state vector, and every state-relative action column: `in_viewport`, `is_newly_revealed`, `taken_here`, `taken_this_episode`, `taken_anywhere`, `tried_here`, `changed_state_rate`, `reached_new_rate`, `field_is_filled`, `value_already_set`, `toggle_is_on` |
| `lexical_only` | the label embedding and `label_length`, `anchor_commit_score`, `lexical_commit_score` | everything else, including the state vector |
| `label_blind` | everything except the label | the label embedding and `label_length`, `anchor_commit_score`, `lexical_commit_score` |
| `search_S` | the fixed hand-written score over the full features, unchanged from the primary | — (not learned) |
| `random` | uniform over valid actions | — |

Exploration: the learned arms' search guide reads the *unmasked* features in every arm
(it is the same fixed guide everywhere), so all learned arms receive the same exploratory
actions in distribution; masking applies to the learned Q-network only.

**Budgets and seeds.** Pretraining: 1,500 interactions per training variant (6,000 in
total), the primary's pretraining ε schedule. Held-out hunts: 1,000 interactions per
held-out variant, the primary's target ε schedule, residual head adapting; 2,000 on the
genuinely held-out applications (the primary budget). Episodes of 40. Seeds 0, 1, 2 for
every learned arm, paired across arms (same seed, same variant). No top-up rule.

## 4. Two evaluations

**(a) Probe evaluation: frozen pretrained weights, no learning, no exploration.** For each
held-out variant, a fixed probe set of states reached by scripted traces (every prefix of
each fault's witness plus scripted detours into states where the state-dependent labels
are useless), generated once and committed before any arm is evaluated. For every probe
state, every arm's Q-value for every available action is recorded with the generator's
ground-truth effect labels. Deterministic: the same weights give the same numbers.

**(b) Hunt evaluation.** Budget-to-first-verified-fault on every held-out variant and on the
genuinely held-out applications, exactly as in the primary (right-censored, per-fault
controls, replay and minimization), with adaptation.

## 5. Diagnostics and pre-specified decision rules

Unit of analysis: per arm, per seed, per variant; seeds averaged within a variant.
Intervals are 95% percentile bootstraps (10,000 resamples) over probe states clustered by
variant and seed.

1. **Progress ranking (probe).** Per probe state, AUROC of Q separating progress actions
   from the rest, and top-1 progress accuracy (greedy choice is a progress action).
2. **Q(s,a) contextuality for the same action (probe).** For every label occurring in
   probe states where it is progress and in probe states where it is not: the
   *within-label AUROC*, i.e. the probability that Q ranks the label higher in a state
   where it is useful than in one where it is not. **Contextual** if the arm's pooled
   within-label AUROC is ≥ 0.70 with the interval's lower bound > 0.50. Also reported: a
   variance decomposition of Q into label, state and label×state components (share of
   variance).
3. **Ranking changes across contexts (probe).** For pairs of probe states sharing ≥ 3
   labels, Kendall's τ between the arm's orderings of the shared labels; reported
   separately for pairs whose ground-truth useful sets differ and pairs where they agree.
   Context-sensitive if τ(differ) is lower than τ(agree) by ≥ 0.20 with an interval
   excluding 0.
4. **Permutation invariance (probe).** For 100 probe states, 20 random permutations of the
   action list each: **pass** if, after undoing each permutation, every Q-value matches to
   within 1e-5 and the greedy choice is identical in every case. A failure is a defect and
   is reported as such before any other diagnostic is interpreted.
5. **Decoy rate (probe and hunt).** In `D1` variants: share of probe states containing the
   decoy where the greedy choice is the decoy, and decoy selections per episode in hunts.
6. **Transfer by factor (probe and hunt).** Each held-out condition's top-1 progress
   accuracy and hunt restricted mean time-to-first-fault, as a ratio to the same arm's
   value on the training variants' probe sets (accuracy) and against `search_S` and
   `random` on the same variant (hunts).
7. **Genuinely held-out applications (hunt).** Restricted mean time-to-first-fault and
   verified faults on the four pristine primary applications for every arm.

**Interpretation, fixed now.** Each hypothesis is reported as supported, not supported or
inconclusive by these rules, per arm (the rules are stated for `ctx_full`; the baselines
are their calibration):

- **H-act supported** if the decoy probe rate is ≥ 0.50 **and** the within-label AUROC's
  interval includes 0.50.
- **H-lex supported** if top-1 accuracy on `VU1` or `VU2` falls below 50% of the training
  accuracy while accuracy on the held-out structures with training vocabulary stays at or
  above 70% of it, **or** if `ctx_full` and `lexical_only` do not differ on the training
  probes but `ctx_full` is no better than `lexical_only` on `VU2`.
- **H-app supported** if top-1 accuracy on `S3`–`S5` falls below 50% of the training
  accuracy while accuracy on `VS` stays at or above 70% of it.
- **H-ctx supported** if all of: the within-label AUROC criterion (rule 2) holds; the
  decoy probe rate is ≤ 0.25 **and** at least 0.25 below `lexical_only`'s; accuracy on `VS`
  and on each of `S3`–`S5` is at least 70% of training accuracy; and `ctx_full` exceeds both
  `action_only` and `lexical_only` on the decoy and state-dependent probes (paired by seed,
  interval excluding 0).
- Anything else for a hypothesis is **inconclusive**, and is reported with the numbers.

Calibration expectations, stated so a reversal is visible: `action_only` and
`lexical_only` should show within-label AUROC near 0.50 (they cannot see the state);
`label_blind` should be unaffected by the vocabulary factor; `random` gives the chance
levels of every probe metric.

## 6. What is fixed now and what is left to the build

Fixed by this document: the hypotheses, factors and levels, the variant table, the arms and
their exact masks, budgets, seeds, both evaluations, every metric, every threshold and the
interpretation rules.

Left to the build, under these constraints: the generator's concrete labels, markup,
pseudo-words and fault placement. All of it is produced by a seeded generator and committed,
with its admission report and the probe sets, **before any arm is pretrained or evaluated
on it**. The build may not look at any diagnostic result (none will exist) and may not be
adjusted after the first diagnostic run except by a dated amendment.

## 7. Compute and cost

Local: the LLM reward on the local GPU, as in the primary. Any AWS use stays within the
project's total $20 cap, counting the primary experiment's spend.
