# Hybrid v2 — Stage 0 gate: LLM page and control labels

Run 2026-10-06 at `e691797` (clean tree), `python -m web_testing_agent.hybrid.stage0`, on the
ctxbench probe states (development data; 343 states, 12 variants), each replayed on the clean
build and labelled by `hybrid/labels.py` (`labels-v2`: entered values removed, one
schema-constrained call per distinct page; 0 failures; 116 model calls for 343 states).
Ground truth and gate thresholds were committed before the run (`3eada20`). Raw output:
`v2_stage0.json`.

## Gate (meaningful vocabularies V0, V1, VS, VU1)

| Measure | Result | Threshold | |
|---|---|---|---|
| Page-kind accuracy | 0.68 | ≥ 0.85 | fail |
| Clickable-role accuracy | 0.78 | ≥ 0.80 | fail |
| Field-meaning accuracy | 0.96 | — | (reported) |
| Agreement with V0's labels on the same states | 0.74 | ≥ 0.80 | fail |

**Decision, by the pre-set rule: the tester uses deterministic labels.** VU2 (pseudo-words):
role accuracy 0.44, agreement with V0 0.66.

## Where the errors are (meaningful vocabularies)

- **Pages**: the contact step — a form with name and email fields — is labelled `info` in
  93 of 106 states. The word "Contact" reads as a "contact us" page: a lexical shortcut.
  Hub pages split `home` / `info`. Every other view is labelled correctly almost always
  (configure, items, review, done, home).
- **Controls**: the model over-uses `commit` for non-final steps (save on configure 27/73,
  advance on items 29/55, "review request" 14/14) and calls the contact step's save `back`
  or `navigate` in 36/106 states. Edit, commit, start, task and help controls are labelled
  correctly in 94–100% of states. By vocabulary, role accuracy is 0.91 (V0), 0.52 (V1),
  0.70 (VS), 0.73 (VU1), 0.44 (VU2).
- The decoy control (no true role) is labelled `commit` in all 41 states where it appears.

## Progress score against ground truth (reported, not gated)

Concordance of the frozen progress score with the app's own progress counter (probability
that Φ orders two states as true progress does): 0.73–0.89 per variant. On this simple
linear workflow the score orders states well; compare its weak alignment with fault pages
on the pristine benchmark apps (`llm_reward_validation.md`).

## Reading

The model is reliable on what is semantically unambiguous (field meanings, edit/commit,
lists, reviews, outcomes) and unreliable where a single word misleads it or where the role
distinction is fine-grained (save vs advance vs commit). Structural facts the page already
states (a visible editable form is a form) are better taken from the markup.
