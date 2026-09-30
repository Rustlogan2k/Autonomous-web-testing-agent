# Transfer experiment: amendment before the first transfer run

**Committed 2026-10-01, before any leave-one-application-out run, any pretraining for a
transfer fold, and any learned agent has been run on any application other than the two
development applications named in §2.** The git commit that adds this file is the
timestamp. It amends `2026-09-18-transfer-preregistration.md`; everything that document
fixes and this one does not mention stands unchanged.

Why an amendment at all. On 2026-09-30 the project owner set requirements the
pre-registration did not contain: the primary tester must be a genuinely sequential RL
agent, it must use a **dynamic LLM-derived reward**, and the comparison against search must
give search the same information the learner has. Adding those after seeing transfer
results would be exactly what §13 forbids. Adding them before any transfer result exists,
and saying so, is not.

---

## 1. What changes, in one table

| # | Item | Pre-registration | This amendment | Why |
|---|---|---|---|---|
| 1 | Primary tester | bandit, pretrained (arm C) | **DQN, pretrained, adapting, with the LLM progress reward** (`dqn_pretrained`) | owner requirement: sequential RL + dynamic LLM reward |
| 2 | Pre-registered comparison | C − B | **kept, unchanged in definition**, reported alongside | nothing pre-registered is dropped |
| 3 | Representation | `semantic_action_features` v2 (80-d) | `rl.features` v3 (95-d actions, 23-d state): v2's blocks unchanged + value category, control state, outcome memory, the A1/A2 label scalars | v2 had no consumer; v3's additions are what equal information access requires (§4) |
| 4 | Arms | A, B, C, D | A0, S, random, B, C, `dqn_scratch`, `dqn_pretrained`, `dqn_zeroshot`, `dqn_pretrained_nollm` | §3 |
| 5 | Reward | env reward | env reward (gate bonus capped at one control per step) + λ·(γΦ(s′) − Φ(s)), Φ = LLM functional-progress potential | owner requirement; §5 |
| 6 | Oracle | as built | + clean-build control, replayable history actions, `nth` disambiguation, no input recorded for a refused fill, iteration-bounded replay | infrastructure soundness fixes, §6 |
| 7 | Action registry | original | benchmark and product use `EXTENDED_OPTIONS` (4 select options, visible field labels, shared-name disambiguation) | infrastructure, §6 |
| 8 | Settle window | 250 ms | 80 ms on the static benchmark apps (identical oracle results measured on `shop_catalog`, 2× faster) | infrastructure, §6 |
| 9 | Archive | "archive replay counts toward the budget" | no archive in any arm | removes a moving part; the statement remains true vacuously |
| 10 | Held-out discipline | all folds are held-out | two named **development** applications; results reported for all folds **and** for the pristine folds only | §2 |

## 2. Development versus held-out applications

The suite has six applications. Two were used to design, debug and tune the system and are
therefore **not transfer evidence**, although they remain folds of the experiment (as
training applications for the other folds, and as reported folds with the caveat attached):

- `shop_catalog` — built 2026-09-18; every smoke run of the new stack.
- `booking_desk` — the second development application.

The other four — `admin_records`, `support_portal`, `media_library`, `account_hub` — were
built from a written contract (`docs/benchmark/CONTRACT.md`) by builders who were not told
which agent would run on them, and **no learned agent is run on them before the frozen
run.** Only the admission validator and uniform-random exploration (the difficulty floor)
touch them beforehand. The analysis reports every comparison twice: over all folds, and
over these four pristine folds only. The pristine result is the generalization claim.

## 3. Arms

| arm | policy | learns on the target | prior | LLM reward |
|---|---|---|---|---|
| `random` | uniform over valid actions | no | none | no |
| `search_A0` | `HandPriorityPolicy`, unchanged from the pre-registration | no | hand weights | no |
| `search_S` | fixed linear score over **the learner's own features** (§4) | no | hand weights + frozen A2 anchor | no |
| `bandit_scratch` (B) | `QAgent(algorithm="bandit")` | yes, from random init | none | no |
| `bandit_pretrained` (C) | same, pretrained on the other folds, residual head adapts | yes (residual) | pretrained | no |
| `dqn_scratch` | `QAgent(algorithm="dqn")` | yes, from random init | none | yes |
| **`dqn_pretrained`** | same, pretrained on the other folds, residual head adapts | yes (residual) | pretrained | yes |
| `dqn_zeroshot` | pretrained weights, frozen, ε = 0.05 | no | pretrained | no |
| `dqn_pretrained_nollm` | as `dqn_pretrained`, pretrained and adapted without the LLM term | yes (residual) | pretrained | no |

B and C are the pre-registration's arms. The pre-registration says C's "shared trunk
[is] frozen; small per-application adaptation head": that is exactly the residual head
(`rl.network.TransferQNetwork`), which is zero-initialised so the first action on the
target is the pretrained policy's.

Pretraining: every pretrained arm of fold *i*, seed *k*, is pretrained on every application
except *i*, interleaved one episode at a time, **1,500 interactions per application**, with
seed *k*. Scratch and pretrained arms of the same fold and seed share the seed.

Exploration schedules (fixed here, not tuned on held-out folds): scratch ε 1.0 → 0.10
over 60% of the budget, the schedule every earlier learned arm in this project used;
pretrained ε 0.20 → 0.05 over 30%; pretraining ε 1.0 → 0.10 over 50%.

## 4. Equal information access

The earlier search-versus-learning comparison varied *learning* and *what was read* at
once: the search arm could not read control labels and the learners never received visit
counts. `search_S` removes both halves of that confound. It scores the **identical**
feature vector the learner receives (`rl.features`) — labels (as the frozen A2 anchor
scalar), visit counts, outcome memory, control state — with a fixed hand-written weight
vector. The learner and `search_S` differ only in how they turn that vector into a choice.

Recorded in full, as the A1/A2 document recorded a rejected term: `search_S`'s weights are
the pre-registered A0 weights carried over term for term, plus the frozen A2 label prior at
1.2, plus three terms (`value_already_set` −3, observed-no-effect −2, `taken_anywhere` −3).
**The third was added on 2026-10-01 after observing, on `shop_catalog` (a development
application), that without it the search followed site-wide navigation indefinitely and
never pressed an in-page button in 400 steps.** It is a general exploration principle and
was fixed before any held-out application was run, but it was conceived after seeing a
failure, and a reader should know that. A0 is run unchanged, so the pre-registered search
arm is also reported.

## 5. The dynamic LLM reward

`reward/progress.py` and `rl/reward.py`. For each state the agent reaches, a local model
(`qwen2.5:7b-instruct`, digest `845dbda0ea48`, temperature 0, seed 7, structured output)
rates *functional progress* on an anchored 0–10 scale from a text snapshot of the rendered
page. It is never asked about bugs. Φ(s) = rating / 10. The shaping term is
λ·(γ·Φ(s′) − Φ(s)) with γ equal to the agent's discount (1.0 for the bandit), bounded in
[−λ, λ]. Judgments are cached by content, so Φ is a fixed function of the state, which the
shaping guarantee requires. A missing judgment yields no shaping for that transition.

λ was chosen on the development application only; the value and the evidence for it are
in §8.

The LLM never touches the oracle, never receives a seeded-fault label, and its output never
enters a verified-finding count. The pre-registration's "The RL reward never receives seeded
fault labels. The oracle never uses an LLM" holds unchanged.

LLM judgments for held-out applications may be computed before the frozen run to warm the
cache. This is not a leak: the model sees only page snapshots the agent itself will reach,
receives no labels, and the cache only removes latency.

## 6. Infrastructure changes (no metric, arm, budget, fold or statistical decision moved)

1. **Clean-build control.** A candidate is confirmed only if its trace violates the
   assertion on the faulty build **and not** on the clean build; every accepted
   minimization step is held to the same test. Found necessary because some traces trip
   `echoes_input` on a correct build (a quantity typed and then a second, untyped product
   ordered). Without it those would be confirmed as findings.
2. **History actions are replayable** (REFRESH, BACK, FORWARD). Persistence faults are
   defined by a reload; the archive's serializer dropped them.
3. **`nth` disambiguation** in replay steps, for repeated labels.
4. **A refused fill records no input** (e.g. text into a number field).
5. **The replay loop is bounded by iterations, not scripted steps**, so a retried step can
   no longer push the triggering action off the end.
6. **Registry options** for the benchmark (`EXTENDED_OPTIONS`); the legacy default is
   unchanged so every earlier result re-runs as it was.
7. **Settle window 80 ms** on the static benchmark apps; measured identical oracle outcomes
   and 2.0–2.9× throughput on `shop_catalog`.
8. **The LLM snapshot uses the browser's rendered text** (`innerText`), not markup that
   includes CSS-hidden panels.

## 7. Statistics (unchanged; restated for the new primary)

Primary: per fold, paired mean time-to-first-verified-fault difference (seeds averaged
within the fold), median across folds with a bootstrap CI resampling folds; secondary: the
fold-stratified log-rank test. Applied to `dqn_pretrained − dqn_scratch` (primary tester)
and to `bandit_pretrained − bandit_scratch` (pre-registered), each reported over all folds
and over the pristine folds. The top-up rule (§8 of the pre-registration, X = 200) applies
to both. Every other comparison is secondary and descriptive.

What the literature review (`docs/literature_review.md`) makes explicit and this design
cannot escape: with 4–6 folds, confidence intervals are wide; with five training
applications, zero-shot transfer is not expected and is reported only as an ablation
(`dqn_zeroshot`); the policy-invariance guarantee of potential-based shaping concerns the
optimal policy, not a finite budget.

## 8. Development-set evidence behind the choices above

Filled in from runs on `shop_catalog` and `booking_desk` only, before this file was
committed; the runs are in `reports/dev_tuning.md`.
