# Development-set evidence behind the frozen method

Every run here used the development application `shop_catalog` only. No learned agent was
run on any other application before the transfer experiment. Budget 1,500 interactions per
hunt, 40-step episodes, 80 ms settle window, seeds 0 and 1.

## 1. LLM weight, scratch DQN (potential-based shaping, uniform epsilon-greedy)

| λ | seed 0 | seed 1 |
|---|---|---|
| 0 | verified at 662 | censored |
| 1 | verified at 839 | verified at 1,277 (both faults) |
| 2 | censored | censored |

Two seeds per setting; read as weak evidence. λ = 1 was the only setting to verify on both.

## 2. The in-distribution check: does training on an application help on that application?

Pretrain 3,000 interactions on `shop_catalog`, then hunt 1,500 on `shop_catalog` with the
residual head adapting (ε 0.2 → 0.05). A learner that fails this cannot transfer.

| reward / exploration | seed 0 | seed 1 | diagnosis |
|---|---|---|---|
| potential shaping, uniform ε | censored, never reached checkout | censored, never reached checkout | shaping cannot change the optimal policy; novelty exhausted once covered |
| progress record, uniform ε | censored | verified at 998 | record collected on any form (product page rated 8/10) |
| record + outcome bonus, uniform ε | censored | verified at 188 (both faults) | seed 0's checkpoint **bit-identical** to the record-only one: pretraining never reached the confirmation, so the bonus never paid |
| record + outcome bonus, **search-guided ε** | **verified at 147** (both faults) | **verified at 195** (both faults) | pretraining reaches the outcome pages; the learned policy exploits them |

Reference, same application and budget, from scratch: verified at 662, 839, 1,277, 1,305,
or censored. Uniform-random exploration verified nothing in 600 steps (validator).

## 3. What was frozen from this

- LLM reward: episodic progress record (λ = 1) + sparse outcome bonus (3.0 at Φ ≥ 0.9);
  potential shaping kept as an option, not used by any arm.
- Exploration: ε-greedy whose exploratory action is the search priority's choice with
  probability 0.75, identical in every learning arm.
- Pretrained arms: ε 0.2 → 0.05 over 30% of the budget, residual head adapting.
- Scratch arms: ε 1.0 → 0.10 over 60% of the budget.

Raw run files are under `runs/dev/` and `runs/sanity_*` (not committed: raw per-step logs
and checkpoints); the numbers above were read from their result JSON.
