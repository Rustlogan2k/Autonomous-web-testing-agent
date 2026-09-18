# The out-of-list vocabulary probe, pre-registered

**Committed before the probe was run. The commit that adds this file is the timestamp.**

This document and `scripts/probe_out_of_list_vocabulary.py` are committed together, before
either has produced a number. Everything below — the label sets, the scorers, the
statistic, the interval, the reading — is fixed at that commit and is not revised
afterwards.

---

## 1. The question, and why the previous probe cannot answer it

`reports/probe_anchor_audit.json` established two things and refused to conclude a third.

Established: the anchor projection's **AUC 1.000** was measured on a population written in
the same commit as the anchor-axis analysis, *after* the pre-registered primary statistic
came back negative. It is post-hoc and self-chosen, and must be labelled exploratory.

Established: on that same population, A1 — a plain frozen word list, no embedding, no model
loaded — reached **AUC 0.990**.

Refused: that 1.000 versus 0.990 shows the two are equivalent. It does not. A near-tie on
a population that is *keyword-separable by construction* shows only that the population
never asked the word list to do the thing a word list cannot do.

A lexical prior has exactly one expected failure mode, and it is **coverage**. If the
vocabulary that matters on an application is absent from the hand-written list, the list
should degrade. Whether the embedding degrades with it is the open question, and it is the
question that decides which representation is carried into the benchmark suite.

**This probe manufactures the coverage failure deliberately and measures who survives.**

## 2. What it is not

It is not an experiment about policies. No environment, no browser, no learner, no RL
training, no fixture. Four scorers, two frozen label sets, one AUC each. It runs in
seconds.

It also does not feed the A0/A1/A2 decision rule. That rule is frozen in
`2026-09-18-a0-a1-a2-frozen.md` §5 and is untouched by anything here.

## 3. The populations, frozen

Both tiers are listed in full in `scripts/probe_out_of_list_vocabulary.py` and pinned by
`tests/unit/test_label_priors.py`. The tests are the enforcement, not the prose.

### Tier 1 — `out_of_list` (primary)

12 commit-like and 12 navigate-like labels in which **every token** is absent from A1's
positive and negative lists. Not merely the head word: a label like `Finalise the order`
would leak `order` into a tier that claims no lexical signal at all.

Head words include the four the advisor named — **Finalise, Authorise, Ratify, Dispatch** —
plus eight more of the same kind. `test_required_head_words_are_absent_from_a1` fails if
any of them is ever added to A1, and `test_tier1_is_fully_outside_a1` fails if any label
acquires an A1 token.

**A1 scores 0.0 on all 24 labels by construction, so its AUC is exactly 0.500.** That is
arithmetic, not a discovery, and the probe's own output says so. Reporting a definitional
result as a measured one would be the same error this milestone exists to correct. **The
measurement is A2's.**

### Tier 2 — `partial_coverage` (secondary)

8 commit-like and 8 navigate-like labels whose head verb is again outside A1, but which
each carry exactly one A1 **positive** token — and the same token multiset on both sides
(four `order`, two `purchase`, two `checkout` per category).

Tier 2 exists because tier 1 alone invites the objection that it only shows a constant
scorer cannot rank. Here the word list is **loud and uninformative**: it fires on every
label, scores every one of them `+1.0`, and is therefore actively misled rather than
merely silent. This is the regime a real application produces, where a control's topic
words are familiar and its verb is not.

A1's AUC is again exactly 0.500 by construction. Again, the measurement is A2's.

## 4. Scorers

| scorer | what it is | why it is in the table |
|---|---|---|
| `A1_lexical_frozen_indicator` | the frozen word list, indicator form | the arm as specified |
| `A1_lexical_count_variant` | the same list, `+1` per matching token | what `probe_anchor_audit.py` actually shipped; see §6 |
| `A2_anchor_on_projected_48d` | anchor axis on the fitted 48-D projection | **the primary scorer** — what the policy receives |
| `A2_anchor_on_raw_minilm_384d` | anchor axis on the unprojected embedding | separates "the embedding sees it" from "the projection kept it" |
| `anchor_on_trigram_hash_32d` | anchor axis on the old character-trigram hash | **floor control** — if the hash also separates these labels, the population is separable by surface form and no scorer here is seeing meaning |

## 5. Statistic, interval, and the reading

**Statistic:** rank AUC — the probability a randomly chosen commit-like label outranks a
randomly chosen navigate-like one, ties at 0.5. The same statistic
`probe_semantic_vs_hash` and `probe_anchor_audit` report, so these numbers sit on the same
scale as the 1.000 and 0.990 they qualify.

**Interval:** bootstrap over **labels**, 10,000 draws, seed 0. Resampling the 144 ordered
pairs would treat 24 labels as 144 independent observations and produce an interval far
narrower than the evidence supports.

**The reading, fixed now:**

> A2 on the 48-D projection **retains discrimination outside A1's vocabulary** if its
> tier-1 AUC is **≥ 0.80** *and* its bootstrap 95% lower bound is **above 0.5**.
>
> If the lower bound includes 0.5, the probe **does not establish** generalisation past
> A1's vocabulary. At n=24 items that is underpowered, not negative, and must not be read
> as evidence of equality.
>
> Between the two — above chance, below 0.80 — the reading is **partial**: some
> discrimination survives, less than a clean result requires.

## 6. A discrepancy found while implementing this, recorded rather than resolved quietly

The frozen A1 definition is an **indicator**: "`1.0` when the action's visible label
contains *any* token from the frozen vocabulary … `0.0` otherwise", worked through with the
example `Cancel order → +1.2 − 1.2 = 0`.

`scripts/probe_anchor_audit.py` transcribed the word list correctly and then implemented a
**count**: `len(words & POSITIVE) - len(words & NEGATIVE)`. Under that form `Place order`
scores +2 rather than +1, because both `place` and `order` are positives.

`reports/probe_anchor_audit.json`'s published A1 AUC of 0.990 was computed with the count
form. Both forms now live in `agents/label_priors.py`, the indicator is what the A1 policy
arm scores, and `test_a1_is_an_indicator_not_a_tally` pins which is which. Both are
reported in this probe's table so the discrepancy is visible rather than settled by fiat.

## 7. Asymmetries that favour A2, disclosed because they would otherwise be invisible

1. **A2's projection was fitted on a corpus that contains some of these tokens; A1's list
   was not.** `semantic_vocabulary.FIT_VOCABULARY` includes `Finalize` (US spelling),
   `Invoice`, `Archive` and `Approve`. The probe prints the exact overlap. A tier-1 win for
   A2 is evidence about **this fitted projection**, not about frozen sentence embeddings in
   general.
2. **`Dispatch the parcel` and `Authorise the transfer` appear in `HELD_OUT_PROBE`**, which
   is excluded from the projection fit but was written on 2026-09-18 alongside the anchors.
   Their head words were named by the advisor, so they stay; the overlap is printed.
3. **The label sets were written by one author who knew A2's anchors.** Freezing them in
   git before the first run rules out tuning against results. It does not rule out phrasing
   that unconsciously suits the anchors — which is what the trigram-hash floor control is
   in the table to expose.
4. **Category assignment is a judgement.** Commit-like versus navigate-like was assigned
   before scoring and is not revised.

## 8. What a result here does and does not license

It **may** decide which fixed prior is carried into the benchmark suite as primary, per the
representation decision already recorded: the winner on out-of-list vocabulary, subject to
the A0/A1/A2 evidence, with the loser retained as an ablation on a subset of transfer
folds.

It **may not** be described as showing that "embeddings understand semantics". The largest
claim tier 1 can support is narrow and concrete: *this frozen projection, scored against
these six anchor pairs, ranks these 12 commit-like labels above these 12 navigate-like ones
when a word list scores all 24 identically.*
