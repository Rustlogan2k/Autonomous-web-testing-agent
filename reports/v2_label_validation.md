# Hybrid v2 — labeller v3 re-test on a fresh validation set (option A)

Run 2026-10-06 at `d32e535` (clean tree), `python -m web_testing_agent.hybrid.label_validation
evaluate`. Terms fixed before the run (plan addendum §8, `233070f`): labeller v3's changes,
69 fresh pages from the six benchmark applications with hand-written ground truth, thresholds
unchanged, one revision only. Raw output: `v2_label_validation.json`.

## Gate

| Measure | v3 on fresh pages | Threshold | |
|---|---|---|---|
| Page-kind accuracy (69 pages) | 0.61 | ≥ 0.85 | fail |
| Control-role accuracy (732 controls) | 0.75 | ≥ 0.80 | fail |
| Field-meaning accuracy | 0.94 | — | (reported) |

**Decision, by the pre-set rule: the tester uses deterministic labels; there is no further
revision.** The cross-vocabulary condition (ctxbench rerun) is reported for completeness in
`v2_stage0_labels_v3.json`; it cannot change the decision.

## Where the errors are

- **Pages** (by application: support_portal 10/12, account_hub 8/12, admin_records 8/12,
  booking_desk 8/12, shop_catalog 6/9, media_library 2/12): confirmation and record pages
  labelled `info` (8); in the single-page media_library, the list behind an open dialog or
  receipt is labelled instead of the dialog (7 of 10 errors there).
- **Controls**: one distinction dominates — ordinary navigation links labelled `open`
  (93 of 183 role errors). Next: `edit` for plain navigation (16), `other` for help links (14).
- **Fields**: 0.94; the main error is person-name fields ("Custodian", "Recorded by")
  labelled `free_text` rather than `name` (5).

## Reading

Across both gates the model is reliable on field meanings and unreliable on fine-grained
role and page distinctions (save/advance/commit in Stage 0, open/navigate here) and on pages
whose meaning lies in a dialog over other content. A post-hoc observation, **not adopted**:
merging `open` and `navigate` would remove about half the role errors. Adopting it now would
be a second revision fitted to the validation set, which the terms exclude.

For the project's claims: a small local model (7B) did not label pages and controls
accurately enough to be the tester's state, under thresholds set in advance — consistent
with the literature's caution that weak models make weak reward and perception components
(`docs/literature_review.md` §11).
