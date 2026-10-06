# LLM progress reward — validation against ground truth (post-hoc)

**Post-hoc and descriptive.** Run on 2026-10-06, after both frozen evaluations had finished,
with the validation script written before the primary run (`scripts/evaluate_llm_reward.py`,
unchanged) on the frozen scorer (`qwen2.5:7b-instruct`, digest `845dbda0ea48`, prompt
`progress-v3`, temperature 0). It measures the reward; it changes nothing in either
evaluation and is not a pre-registered test. Raw output: `llm_reward_validation.json`.

## Part A — where the score is high, versus where faults become observable

For each benchmark fault, Φ (progress / 10) along its reproduction witness on the faulty
build, and Φ at the step where the fault is observable compared with states a 300-step
uniform-random walk reaches.

| App | Role | Spearman (witness position vs Φ), per fault | Φ at fault-observable step | Random-walk Φ mean | AUROC observable vs random |
|---|---|---|---|---|---|
| shop_catalog | development | 0.84, 0.80 | 1.0, 1.0 | 0.23 | **1.00** |
| booking_desk | development | 0.86, 0.88 | 0.8, 1.0 | 0.31 | **1.00** |
| support_portal | pristine | 0.30, 0.65 | 0.5, 0.5 | 0.29 | 0.73 |
| account_hub | pristine | 0.59, 0.55 | 0.5, 0.7 | 0.58 | 0.52 |
| media_library | pristine | 0.13, −0.41 | 0.2, 0.2 | 0.22 | 0.47 |
| admin_records | pristine | 0.46, 0.82 | 0.5, 0.3 | 0.50 | 0.38 |

On the two development applications — where the prompt was revised (`progress-v3` came
from the booking_desk development check) — Φ rises steadily along every witness and
peaks exactly where the fault shows. On the four pristine applications it does not: the
fault-observable pages score no higher than ordinary pages (AUROC 0.38–0.73), and two
media_library and admin_records faults appear on pages the rubric places low (browsing,
lists) or below the pages the agent passed on the way.

## Part B — what the reward did in the primary run's LLM hunts

From `runs/final-local` reward logs (LLM arms; 120,000 steps).

- The LLM terms pay almost only on steps that reach a new state (mean 0.075 vs 0.002) or
  change the page (0.044 vs 0.002): they largely coincide with what the novelty term
  already rewards.
- They pay *more* on steps whose action failed (0.115 vs 0.028).
- The script's pooled per-run statistics (Spearman of total shaping vs time to fault −0.40;
  max-Φ AUROC 0.68) include the six clean-build control runs, where no fault can be found,
  and pool applications with very different Φ levels. Recomputed on the 54 faulty-build
  runs **within each application**:

| App | Runs | Found | Mean total LLM shaping | Spearman (shaping vs time to fault) |
|---|---|---|---|---|
| shop_catalog (dev) | 12 | 12 | 133.8 | −0.72 |
| account_hub | 6 | 4 | 38.0 | 0.12 |
| support_portal | 6 | 1 | 40.9 | −0.13 |
| media_library | 6 | 1 | 5.9 | 0.27 |
| admin_records | 12 | 0 | 110.1 | — (none found) |
| booking_desk (dev) | 12 | 0 | 24.2 | — (none found) |

  Only on the development application does more LLM reward go with earlier discovery. On
  admin_records the agents collected the second-largest LLM reward of any application and
  found nothing.

## Reading

- The reward tracks task progress well **where it was tuned** and poorly elsewhere. This is
  consistent with the null LLM ablations in the primary experiment and with the literature's
  warning that small judges make weak reward models (`docs/literature_review.md` §11).
- It is aimed at the wrong target for testing: it rewards progress toward completing a
  task, while several seeded faults are observable on browsing or listing pages, or need
  deviations (invalid input, edits after review) that lower progress.
- Much of what it pays duplicates the novelty bonus.

## Caveats

Two faults per application; 13–64 random-walk states per application; one judgment per
snapshot (cached), so model variance is not measured; the development/pristine contrast is
over two versus four applications. Descriptive only.
