# Contextual-generalization diagnostic — report

**Secondary diagnostic**, pre-registered in
`docs/methodology/2026-10-04-contextual-diagnostic-preregistration.md` (commit `c10699c`, before
any primary result existed). Its applications, runs, analysis and report are separate from the
frozen transfer experiment; **no number here is pooled with, or substitutes for, a primary
statistic.** All numbers come from the frozen analysis code, `ctxbench/probe_eval.py` and
`ctxbench/analyze.py` as committed before the run (`6bf7d86`). The full tables are in
`contextual_diagnostic_numbers.md` and `contextual_diagnostic.json`.

## 1. What was run

| | |
|---|---|
| Applications | 12 generated variants of one request workflow (`tests/fixtures/ctxbench/`): 4 training (V0/V1 vocabulary × S1/S2 structure), 8 held-out (synonyms VS, unseen words VU1, pseudo-words VU2; tabs S3, modal S4, reordered S5; decoy D1; combined VU1·S5·D1), plus the 4 pristine primary applications. All 12 admitted by the unchanged primary validator (`reports/ctx_admission.json`); none dropped. Held-out labels share no word token with training labels; mean nearest-neighbour cosine to training labels 0.48 / 0.45 / 0.34 (VS / VU1 / VU2, sentence model) and 0.52 / 0.48 / 0.47 in the 48-D projection the network receives (`reports/ctx_vocabulary.json`). |
| Arms | `ctx_full` (the primary DQN agent, unmasked), `action_only`, `lexical_only`, `label_blind` (same agent, input masks per §3), `search_S`, `random`. Every learned arm has the live LLM reward at the primary's settings. |
| Budgets | Pretraining 1,500 interactions on each training variant (6,000); hunts 1,000 on held-out variants, 2,000 on pristine apps, residual adapting; seeds 0–2; no top-up. |
| Execution | 228 jobs, 0 failures, all from a clean tree at `6bf7d86`; local GPU, no AWS. The host was suspended 23:22–02:54 (lid closed); jobs paused and resumed — every budget is in interactions, not time. |
| Probe sets | 25–34 states per variant (every reachable prefix of the fault witnesses and benign walks, plus invalid-date and invalid-email detours), each action's effect labelled by execution on the clean build; committed (`11cff61`, archives re-stored byte-exact in the next commit) before any arm ran. |

## 2. Pre-registered verdicts (rules applied to `ctx_full`)

| Hypothesis | Verdict | Deciding numbers |
|---|---|---|
| **H-ctx** genuine state–action contextual learning | **not supported** | within-label AUROC 0.51 [0.44, 0.59] (rule: ≥ 0.70 with lower bound > 0.50); does not beat `action_only` (+0.02 [−0.09, 0.15]) or `lexical_only` (+0.01 [−0.12, 0.16]) on it; decoy rate not below `lexical_only`'s |
| **H-act** action memorization | inconclusive | within-label AUROC interval includes 0.50 (holds); decoy probe rate 0.31 [0.00, 0.91] unresolved against ≥ 0.50 |
| **H-lex** lexical memorization | inconclusive | top-1 ratio on unseen vocabulary 0.88 [0.35, 1.18] (rule: < 0.50) unresolved; `ctx_full` beats `lexical_only` on VU2 by 0.16 [−0.13, 0.46], unresolved |
| **H-app** application memorization | inconclusive | mean top-1 ratio on S3–S5 0.63 [0.44, 0.88] (rule: < 0.50) unresolved; VS ratio 1.08 (holds) |

Permutation invariance (diagnostic 4) **passed** for every learned arm and seed (Q within
1.9e-6 of the unpermuted values; identical tied-best action sets in all 2,000 checks).

## 3. What the numbers show

**The frozen policy is not contextual for the same action.** Diagnostic 2 is the direct test:
whether `ctx_full` ranks a label (e.g. "Save", "Add", typing a valid email) higher in states
where it advances the task than in states where it does nothing. It does so at chance (0.51),
indistinguishable from the two arms that cannot see the state at all (`action_only` 0.49,
`lexical_only` 0.50 — their expected calibration). The variance decomposition says why: on
held-out variants `ctx_full`'s Q is 47% label, 48% state and **7% label×state**. The state
moves every action's value up or down together; it almost never reorders them. Rankings
change somewhat more between states whose useful actions differ than between states whose
useful actions agree (Kendall τ 0.67 vs 0.80), but the contrast, 0.14 [0.10, 0.18], is below
the pre-registered 0.20. For reference, the fixed hand-written `search_S` score — which reads
state-relative features such as "field already filled" — reaches 0.62 [0.58, 0.66].

**It does rank useful actions well above chance, and that skill survives vocabulary shift but
not every structure shift.** Top-1 progress accuracy on training probes is 0.56 (`search_S`
0.31, `random` 0.18). On held-out vocabulary it is 0.60 (synonyms), 0.49 (unseen words) and
0.53 (pseudo-words) — little loss even when no word carries meaning. On held-out structures it
is 0.54 for reordered stages (S5) but 0.32 for the modal flow (S4) and 0.19 for single-page
tabs (S3). The point estimates therefore lean away from lexical memorization and toward
dependence on the training layouts' flow, but the pre-registered rules leave both H-lex and
H-app inconclusive at this sample size (three seeds).

**Decoys split by seed, not by arm.** Seed 1 of `ctx_full` takes the decoy in 84–100% of
decoy-bearing probe states; seeds 0 and 2 never do. That is why the interval spans
[0.00, 0.91]: whether the training "advance" label becomes a trap depends on the training run,
and three seeds cannot say how often. In hunts every learned arm, `label_blind` included,
selects the decoy more often than `random` (2.1–3.3 vs 1.3–1.5 per episode), so hunt decoy
selections are not evidence of label-following specifically.

**In hunts, the learned agents with state access find the faults; the label channel is not
what helps.** Faults confirmed on the eight held-out variants (24 runs per arm):

| arm | found | mean RMST (of 1,000) | pristine apps found (12 runs) |
|---|---|---|---|
| `label_blind` | 20/24 | 289 | 1/12 |
| `ctx_full` | 19/24 | 454 | 4/12 (account_hub 3/3, admin_records 1/3) |
| `lexical_only` | 4/24 | 848 | 1/12 |
| `action_only` | 4/24 | 857 | 0/12 |
| `search_S` | 2/24 | 940 | 3/12 |
| `random` | 2/24 | 926 | 2/12 |

The two arms that see the state find faults in about four of five runs; the two that do not
are at baseline level. `label_blind` — which never sees a label — is as successful and faster
than `ctx_full` on held-out variants. Hunts include in-app adaptation, so this measures
pretrained prior plus 1,000 interactions of learning, not the frozen policy alone. On the
pristine primary applications the arms are not separable with 3 runs each (support_portal: no
arm found its faults; media_library: only `label_blind`, `search_S` and `random`).

## 4. Interpretation within the claim limits

- Per the pre-registered rule, **genuine state–action contextual learning is not supported**:
  the frozen policy's preference for an action does not depend on whether that action is
  useful in the current state. This matches claim limit A (the 25-D state carries no page
  semantics); the state the network sees shifts values but does not tell it *which* control
  matters here.
- The memorization hypotheses are **inconclusive**, not refuted. The direction of the point
  estimates — little loss on held-out vocabulary, large loss on two of three held-out
  structures, and the label-blind arm doing as well as the full one — is consistent with the
  policy relying on flow and form-state regularities of the training layouts more than on
  words. That reading is descriptive; the rules required larger effects or narrower intervals.
- The state-aware learned arms' advantage in hunts is large on this family (19–20/24 vs 2/24
  for `search_S`/`random`) and is the diagnostic's clearest effect. It is a **within-family**
  result (claim limit B): one functional core, twelve renderings by one generator.
- Nothing here bears on the LLM reward's value (claim limit C): every learned arm used it.
- Representation leakage (claim limit E) is not tested here: all variants use the same DOM
  ids, so fallback descriptions and composition counts are identical across vocabularies.

## 5. Analysis choices and deviations, stated once

- Fixed in `analyze.py`'s docstring before any run: per-variant units with seeds averaged;
  pooled within-label AUROC, decoy rate and τ over the eight held-out variants; two-way
  bootstrap (states within variant × seeds, 10,000 resamples, shared across arms);
  supported / not supported / inconclusive as defined there.
- Found by the pre-run smoke test and fixed before the run (`6bf7d86`): masked arms tie
  exactly on actions their masks make identical, so greedy metrics are the expectation under
  uniform tie-breaking and the permutation check compares tied-best sets (positional
  tie-break differences are reported in the JSON, not counted as failures).
- Probe states are those reachable on the clean build; CTX-2's witness ends with a submission
  the correct application refuses, so its last step contributes no probe state.
- No change was made to the pre-registration, the probes, the arms or the analysis after any
  diagnostic result existed.
