# Transfer experiment: second amendment, before the frozen run

**Committed 2026-10-03, before the frozen leave-one-application-out run, and before any
learned agent, search arm or pretraining job has been run with any of the four pristine
applications (`admin_records`, `support_portal`, `media_library`, `account_hub`) as its
target.** It amends `2026-10-01-transfer-amendment.md`, which amends
`2026-09-18-transfer-preregistration.md`; everything those fix and this one does not
mention stands unchanged.

Between the first amendment and the frozen run, two independent code reviews (one of the RL
stack, one of the oracle and fixtures) and a development check on `booking_desk` found
defects. Each is recorded here with what changed and why. None of them was informed by a
result on a pristine application: no such result exists.

---

## 1. What changes, in one table

| # | Item | First amendment | This amendment | Found by |
|---|---|---|---|---|
| 1 | Oracle control | the clean build | **per-fault control**: the faulty build minus the candidate fault's own patch | oracle review, §2 |
| 2 | Control that fails to run | treated as passing | rejects (`control_error`) | oracle review |
| 3 | Transient rejections | final | retried up to 3 times per piece of evidence | oracle review |
| 4 | Candidate count | every step a violation stays visible | distinct evidence (page hash) | oracle review |
| 5 | Minimized trace | accepted if each minimization step passed | must meet the full reproduction rule again, else the original is reported | oracle review |
| 6 | Outcome bonus | Φ ≥ 0.9 | the model's `outcome_displayed` stage | RL review, §3 |
| 7 | State | 23-d | 25-d: + best LLM progress this episode, + outcomes already paid (features v3.1) | RL review, §3 |
| 8 | Exploration on the target | scratch ε 1.0 → 0.10 over 60%; pretrained ε 0.20 → 0.05 over 30% | **one schedule for every learning arm**: ε 0.50 → 0.05 over 50% | RL review, §3 |
| 9 | n-step returns | a full window ending on the last step bootstrapped | every window reaching the episode's last step is terminal | RL review |
| 10 | Pretraining | end of an application's budget marked terminal | only real termination is terminal | RL review |
| 11 | Arms | as listed | + `dqn_scratch_nollm` | §4 |
| 12 | `search_S` | three extra terms | four: + `field_already_filled` −2.5 | `booking_desk` development check, §5 |
| 12b | LLM progress prompt | `progress-v2`: progress of the current page | `progress-v3`: progress through the whole task; a later step outranks a completed earlier one; the snapshot names the step a page marks with `aria-current="step"` | `booking_desk` development check, §5 |
| 12c | Gate-reveal bonus | paid for any growth of the action set on an unchanged URL, in full again every episode | requires 80% of the previous controls to survive; decayed across the run by 1/√(1 + earlier payments) like novelty; one control's worth per step (benchmark and tester; the legacy default is unchanged) | `booking_desk` development check, §5 |
| 13 | LLM in the cloud | offline replay of a warmed cache | **live**: the pinned model served on the instance's GPU; the cache is shared, misses are judged on demand | owner requirement, §6 |
| 14 | Experiment screenshots | every step | none during hunts (learners read the DOM); evidence screenshots come from replaying verified findings | performance |

## 2. Per-fault controls (items 1–5)

A faulty build carries **every** seeded patch of its application. The first amendment's
control, the clean build, therefore could not tell "fault F's assertion fires because of
F's patch" from "it fires because another fault's patch opened a page where F's assertion
fails for an unrelated reason": the clean build never reached that page, so the control
was silent and the candidate was confirmed. The oracle reviewer constructed traces that
credited SHOP-01, MEDIA-02 and ADMIN-01 this way.

Fault F is now confirmed only if its trace violates F's assertion on the faulty build, twice
(the pre-registered reproduction rule), **and does not** violate it on the faulty build
with F's own patch removed. The control build is generated from the application's declared
patches and served on its own origin. Every minimization step, and the minimized trace as a
whole, is held to the same rule. The admission validator checks every reachability witness
and every minimized trace against the per-fault control. All six applications were
re-admitted under it (`reports/benchmark_validation.json`, regenerated 2026-10-03; 12
faults, 5 fault classes).

## 3. RL review (items 6–10)

- The outcome bonus paid at a rating of 9 ("between ready to commit and committed"), so an
  unsubmitted edit form could collect it. It now requires the model's own stage label
  `outcome_displayed`.
- Both LLM terms depend on episode history that the observation did not carry, which made
  the reward non-Markov in the agent's state. Two state slots now carry it.
- Exploratory actions are mostly the search guide's (§8 of the first amendment). Under the
  old schedules the scratch arm received about five times as many search-guided decisions
  as the pretrained arm, so `pretrained − scratch` would have measured "less search" as
  much as "transfer". Every learning arm on the target now uses one schedule. This narrows
  the pre-registered comparison to what it claims to measure; it is not tuned on any
  result.

## 4. `dqn_scratch_nollm`

Without it, `dqn_pretrained − dqn_scratch` and `dqn_pretrained − dqn_pretrained_nollm` are
the only DQN contrasts, and neither isolates the LLM reward on a learner without a prior.
`dqn_scratch − dqn_scratch_nollm` does. It is a secondary, descriptive comparison.

## 5. The `booking_desk` development check, and `search_S`'s fourth term

The first amendment ends: "No development run was made on `booking_desk`". On 2026-10-03,
with the owner's agreement that smoke and sanity checks run locally, a local check ran
`search_S` and `dqn_scratch` (2 seeds each, 1,500 interactions; `dqn_pretrained` pretrained
on `shop_catalog`) with `booking_desk` as the target. `booking_desk` is a development
application (§2 of the first amendment), so this is development evidence.

Every run was censored, and **no run reached a seeded fault's page**. A trace of
`search_S` showed why: the form-control bonus (+1.5) says "fill the form before leaving the
page", and with nothing to say a field was already filled it also said "rewrite every
filled field with every generated value and every select option". The first stage of a
reservation has five fields; the search spent about twenty steps on it before its continue
button ranked first, and the 40-step episode ended before the third stage. The learners
inherit this, because their exploratory actions are the search guide's.

The fix is a weight on a feature the learner already receives (`field_is_filled`):
entering a value into a field that already holds one costs 2.5, which cancels the
form-control bonus and the best value ordinal, so an untried control on the page outranks a
rewrite. A select showing its default option counts as filled (it cannot block a step for
being empty); a select on an empty placeholder does not. Edge-case values and other options
are still entered, after the page's untried controls. Like the third term (first amendment,
§4), it is a general statement about forms, conceived after seeing a failure on a
development application, and fixed before any pristine application was run. `search_A0` is
unchanged. The agents' exploration guide and `search_S` now share one scoring function,
so they cannot drift apart.

**The fix to the search was necessary but not sufficient.** Rerun with it, every arm was
still censored without reaching a fault page (`random` and `search_S` 2 seeds each,
`dqn_scratch` 2 seeds; 1,500 interactions). The DQN now reached pages the model rated 8,
but the LLM reward itself had no depth signal: scored along both faults' reachability
witnesses, a *filled first step* of the four-step reservation rated 7, and so did the
second and third steps. The progress-record term pays only for a new episode maximum, so
passing the first step's validation gate, the step no agent passed, earned no LLM reward.
The rubric judged how complete the current page was, not how far through the task the user
was. That is a general property of multi-step flows (checkout, booking, sign-up), not of
this application.

`progress-v3` adds one paragraph to the prompt (judge progress through the whole task; a
later step outranks any earlier step; steps spread over 4–8 in order), and the snapshot
gains a `STEP:` line naming the element a page marks with `aria-current="step"`, the
WAI-ARIA marker for the current step of a process. Along the same witnesses the first step
now tops out at 5 and the second step rates 7, so passing the gate pays; `shop_catalog`'s
witnesses still rise to 10 at the confirmation page. The prompt version is part of the
cache key, so no `progress-v2` judgment is reused. `docs/llm_reward.md` has the full
specification.

**And the environment reward was being farmed.** With the new prompt, three `dqn_scratch`
seeds at the full 2,000 interactions were still censored with no fault page reached. The
run's reward decomposition showed why: of the reward the agent received, the capped
gate-reveal term contributed about 450 per run, paid on about 300 of 2,000 steps, against
about 145 for novelty and 23 for the LLM terms. The reveal rule pays when the action set
grows on an unchanged URL, which on a multi-page site means a gate opened on the page the
user is on. `booking_desk` is a single-page application: every view switch keeps the URL,
so walking from the room list to the help page, or opening a confirmation dialog, was paid
as a gate whenever the new view happened to carry more controls. The fix adds the condition
that defines a gate: the page **stays** and gains a control, so at least 80% of the previous
controls must survive. Real gates still pay (the deep-flow fixtures' gates keep 100%); view
switches no longer do.

Retention alone was not enough: a click from a sparse view (the reservations page) to a
rich one (help, with two dozen controls) keeps every navigation control and adds the rest,
so it passes. The deeper defect is that the reveal ledger resets every episode, so the
same reveals were paid in full in each of a run's 50 episodes. Novelty is decayed across
the run by 1/√(1 + earlier visits) for exactly this reason (an earlier measured defect);
the reveal term was the one exploration reward that never decayed. It now decays the same
way per (page, control), and a step is worth one control's reveal (the least-collected
one), which is what the RL reward's existing cap already intended. A gate opened for the
first time still pays in full. The legacy defaults (no retention, per-episode ledger, one
bonus per control) are unchanged, so every earlier experiment re-runs as it was.

**Where the development check ended.** With all three corrections, `dqn_scratch` (3 seeds,
2,000 interactions, the frozen configuration below):

| application | first verified fault (interactions) | faults verified | reveal term | novelty | LLM terms |
|---|---|---|---|---|---|
| `shop_catalog` | 750, 596, 572 | 2, 2, 1 of 2 | 0 | 145–190 | 90–162 |
| `booking_desk` | censored ×3 | 0 | 88–100 (was ≈450 agent-side) | 143–159 | 22–24 |

Before the corrections, scratch learners on `shop_catalog` took 662–1,305 interactions or
were censored (`reports/dev_tuning.md`). `booking_desk` is still not solved: its two faults
sit behind a 10-step path that minimization cannot shorten (the deepest in the suite; the
four pristine applications' witnesses are 6–9 steps) and a value-dependent gate (the guest
count must fit the chosen room). **No further change is made for it.** It is a development
fold, it is excluded from the pristine claim, and tuning further against one application
would turn a general method into a fitted one. Its result is reported as it comes out.

**The budget is unchanged**: 2,000 interactions, as pre-registered.

What was deliberately **not** changed: the frozen A2 label prior ranks "Save for later"
(+0.35) above "On to catering and extras" (−0.15) on this application. That is a property
of the pre-registered prior. It is reported, not tuned.

## 6. The LLM reward in the cloud (item 13)

The first amendment shipped a warmed cache to CPU workers and replayed it offline. A state
the cache had not seen then got no LLM term, which makes the reward a fixed table over
the states someone else happened to visit, not a judgment of what this agent reached. The
owner's requirement is a dynamic LLM reward. The instance that runs LLM arms now serves
the pinned model (Ollama, `qwen2.5:7b-instruct`, digest `845dbda0ea48`, temperature 0,
seed 7) on its own GPU, and the launcher refuses to proceed if the digest differs. The
cache is still shared and is still the source of reproducibility (a snapshot is judged once,
by any arm or seed, and reused); misses are judged live. `--llm-offline` remains for
re-analysis without a model. Every run's result records lookups, cache hits, live calls,
failures and offline misses.

Arms without the LLM term (`random`, `search_A0`, `search_S`, `bandit_*`,
`dqn_scratch_nollm`) need no model and may run on a CPU-only instance; this changes where
they run, not what they compute.

## 7. The frozen run

Everything below is fixed by the commit that adds this file.

- **Suite and folds**: all six admitted applications, each held out once. Development folds
  `shop_catalog` and `booking_desk`; pristine folds `admin_records`, `support_portal`,
  `media_library`, `account_hub`. Every comparison is reported over all folds and over the
  pristine folds only.
- **Arms**: `random`, `search_A0`, `search_S`, `bandit_scratch`, `bandit_pretrained`,
  `dqn_scratch`, `dqn_scratch_nollm`, `dqn_pretrained`, `dqn_zeroshot`,
  `dqn_pretrained_nollm`. Clean-build false-positive runs (one per fold): `search_S`,
  `dqn_pretrained`.
- **Configuration** (`ExperimentConfig` defaults at this commit): budget 2,000; episodes of
  40; settle 80 ms; pretraining 1,500 interactions per training application; λ = 1;
  target ε 0.50 → 0.05 over 50% of the budget for every learning arm; pretraining
  ε 1.0 → 0.10 over 50%; at most 30 minimization trials. Environment (`EnvSettings`
  defaults): extended registry, reveal retention 0.8, reveal run decay on. Features v3.1,
  prompt `progress-v3`, model `qwen2.5:7b-instruct` digest `845dbda0ea48`.
- **Seeds**: 0, 1, 2, then the top-up rule applied mechanically by the runner
  (`run_transfer.py --top-up`): seeds 3–5 for the two arms of a primary pair on every fold
  where that pair's seeds-0–2 difference is under 200 interactions. The rule is decided on
  seeds 0–2 only and is never re-decided on 0–5.
- **Where it runs** (revised 2026-10-04, before any job of the frozen run started: AWS
  rejected the GPU quota increase, so the planned GPU instance does not exist). Split by
  whether a job calls the language model:
  - **The development laptop** (RTX 4050 Laptop GPU, 6 GB; the pinned model in Ollama,
    measured at 0.78 judgments/s): `dqn_llm` pretraining and the `dqn_scratch`,
    `dqn_pretrained`, `dqn_zeroshot` hunts, the `dqn_pretrained` clean-build runs, and the
    top-up for the DQN pair. Every state the cache has not seen is judged live by the
    pinned model; there is no offline mode in the frozen run.
  - **One AWS spot instance** (c7i.2xlarge, ap-south-1, no model; `NEEDS_LLM=0`):
    `random`, `search_A0`, `search_S`, `bandit_scratch`, `bandit_pretrained`,
    `dqn_scratch_nollm`, `dqn_pretrained_nollm`, their pretraining (`bandit_nollm`,
    `dqn_nollm`), the `search_S` clean-build runs, and the top-up for the bandit pair.

  Each primary pair is on one machine, so each machine applies the top-up rule to its own
  pair. The arms differ only in where their browsers and network run, not in anything they
  compute: same commit, same configuration, same pinned dependency versions (the laptop's
  virtualenv and the image's requirements are the same pins; torch 2.6.0 on CPU in both).
  The two result directories are merged before analysis; `scripts/analyze_transfer.py` is
  unchanged.

  Commits. The local run started at `1fef49d`. A capped AWS validation instance
  (2026-10-04, one 100-step `search_S` hunt, about seven minutes, terminated itself) found
  that cloud results recorded an empty commit, because a worker runs from a `git archive`
  with no `.git`; the next commit passes the commit in from the launcher. It changes only
  how provenance is recorded, so local jobs started after it carry the newer hash and
  nothing they compute differs. The AWS instances also moved from one c7i.2xlarge to four
  m7i-flex.large (2 vCPU each, folds split across them), because the account's Free plan
  allows only free-tier instance types.

  Alternatives rejected, with the measurement behind each: serving the laptop's model to
  AWS browser workers over a tunnel (the GPU, not the browsers, bounds the LLM arms'
  throughput, so it would add no speed; it would take the account's entire 8-vCPU quota,
  need inbound SSH and wider IAM rights, and a network drop would silently remove LLM
  terms); the model on AWS CPUs (15–30 s per judgment against ~10,000 needed); a hosted
  model (a different model than the one validated, and new permissions); everything on
  the laptop (20+ hours, against the owner's direction to keep major runs off it).
- **Exclusions**: only runs that fail for infrastructure reasons, listed in the report; a
  failed job is rerun (the runner resumes), never dropped for its outcome.
