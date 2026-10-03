# The dynamic LLM reward

How a language model contributes to the reward the RL tester learns from — what it sees,
what it says, how that becomes reward, when it is asked, how it is cached, what happens when
it fails, and how it interacts with learning. Code: `src/web_testing_agent/reward/progress.py`
(the judgment) and `src/web_testing_agent/rl/reward.py` (the reward terms).

## What the model is asked

**Functional progress, never bugs.** For each state the agent reaches, the model rates how
far a user has progressed through a task in the application, on an anchored 0–10 scale:

| score | anchor | stage |
|---|---|---|
| 0 | landing page, navigation hub, purely informational page | `landing_or_information` |
| 2 | browsing a list, catalogue, table or search | `browsing` |
| 4 | a specific item or task form is open but untouched | `task_opened` |
| 6 | a task is under way (fields filled, or the app is asking for corrections) | `task_in_progress` |
| 8 | ready to commit: a review step echoing the user's entries, or a complete form | `ready_to_commit` |
| 10 | a task has been committed and its outcome is displayed | `outcome_displayed` |

**Progress is through the whole task, not the current page.** Many tasks take several steps
(checkout, booking, sign-up, a wizard). The prompt says a later step is further along than
any earlier step however filled that earlier step was, and spreads the steps over 4–8 in
order (first step 4–5, middle steps 5–7, last step before the final action 7–8, review 8).
Without this (prompt `progress-v2`), a filled first step of a four-step booking scored 7, the
same as the next two steps, so passing the first step's validation gate earned no LLM reward
at all; measured on `booking_desk`, a development application, where no agent passed it.
Current prompt: `progress-v3`.

The model is never asked whether a page is buggy, is never shown a seeded fault, an answer
key, a fault id or which build it is looking at, and its output never enters a verified
finding. Ground truth on the benchmark is the deterministic oracle; on a user's application
it is the deterministic triggers. The separate LLM *review* of outcome pages
(`tester/review.py`) is reported as unverified and is not part of the reward.

## What the model sees

A text snapshot of the page **as rendered**, built by `page_digest(url, html, visible_text)`:

```
APPLICATION: Fernbrook Outfitters                          <- the app's own name
PAGE: confirmation.html                                     <- path (host and port dropped)
TITLE: Order confirmed – Fernbrook Outfitters
HEADINGS: Order confirmed
STEP: <label> (marked as the current step)                 <- only when the page marks one
TEXT: … Order confirmed Thank you. A receipt is on its way. ITEM QUANTITY LINE TOTAL
      Folding windshield 1 £15.25 … Receipt sent to tester@example.com …   (≤ 900 chars)
FORM FIELDS: name [type] = empty | "short value" | long text (N chars)   (≤ 20 fields)
CONTROLS: Store | Shelter | Basket | Continue shopping | …                (≤ 30 labels)
```

- `STEP` is the element the page marks with `aria-current="step"`, the WAI-ARIA marker of
  the current step of a multi-step process. Pages without one simply have no such line.
- The text is the browser's `document.body.innerText`, so CSS-hidden panels are excluded
  (a defect found and fixed: a hidden "Your basket is empty" made a full basket score 0).
- Typed values appear as `empty`, a short literal, or a length: enough to tell a filled form
  from an empty one without making every keystroke a new model call.
- Because the snapshot includes what the agent typed and what the application displays in
  response, the judgment depends on the agent's own interaction, not only on which page it
  is on: the same checkout scores 5 empty, 7 half-filled and 8 complete.

## What the model outputs

Structured JSON, enforced by Ollama's `format` schema:
`{"reason": "<≤12 words>", "stage": "<one of the six>", "progress": <0-10>}`. The reason
comes first so the score is conditioned on it. Model: `qwen2.5:7b-instruct`
(digest `845dbda0ea48`), temperature 0, seed 7, 70 output tokens maximum.

## How it becomes reward

Two terms, added to the environment's own reward (count-decayed novelty, a capped gate-reveal
bonus, step and repetition costs, deterministic trigger bonuses). With Φ(s) = progress/10:

1. **Progress record** (dense): `λ · max(0, Φ(s′) − max Φ so far this episode)`, λ = 1.
   Pays only for a new episode maximum, so it is bounded by λ per episode and cannot be
   farmed by oscillating; it renews every episode, so reaching task-completing pages stays
   worth doing for the whole budget.
2. **Outcome bonus** (sparse): +3.0 the first time in an episode that each distinct state the
   model places in the `outcome_displayed` stage is reached. Bounded by the number of
   outcome states; aimed at exactly the pages where functional faults become observable.

The composed reward is clipped to [−6, 6]. Both terms depend on episode history, so the
agent's state vector carries that history (`llm_best_progress_this_episode`,
`llm_outcomes_paid_this_episode`): the same state and action never pay differently with an
identical observation.

**Why not potential-based shaping**, the textbook choice: it is designed not to change the
optimal policy, and the measured failure was precisely that the optimal policy under the env
reward never went deep (novelty is exhausted once an application is covered). The potential
form remains available (`llm_mode="potential"`) and is used by no arm.
`reports/dev_tuning.md` has the development evidence.

## When it is called

On every state transition of every LLM-enabled arm, for the state reached (and for the
episode's first state). In practice only on states whose snapshot has not been judged before:

- an in-process memo skips the call when the snapshot is unchanged from the previous step;
- a content-addressed cache (`data/llm_cache/`) returns any snapshot judged before, by any
  run, seed or arm, keyed on `(prompt version, model, context, snapshot)`.

A new state the agent discovers is judged **live, during exploration** — on the development
machine by the local GPU, in the cloud run by the same model served on the instance's GPU.
The cache only moves a repeat judgment from "ask again" to "look up".

## Caching, and why it matters for the experiment

- **Reproducibility**: a snapshot is judged once and the judgment is reused everywhere, so
  Φ is a fixed function of the state across arms and seeds, which a controlled comparison
  needs (a model asked twice can answer 7 and 8).
- **Latency**: about 1.9 s per new judgment on the development GPU (median of 1,314); a hit
  costs microseconds. Before the main run the cache is warmed by crawling every application
  with random and search policies (no labels; `scripts/warm_caches.py`).
- **Logging**: every step's terms (`env`, `llm_shaping`, `phi_before`, `phi_after`, novelty,
  repetition, whether the state changed) go to a per-run JSONL reward log; scorer statistics
  (lookups, cache hits, model calls, failures, offline misses, seconds) go into each run's
  result record.

## Failure behaviour

A call that times out (120 s), errors, or returns unparseable or out-of-range output yields
**no judgment**: that transition gets no LLM term, the failure is counted, and the run
continues. A missing judgment is never replaced by a guess. If the model is unreachable when
a run starts, the run fails fast (preflight) rather than silently running without the term;
the product tester instead degrades to no LLM term and says so in its report. In offline
replay mode (no model available at all) a cache miss is logged so it can be judged later.

## Validation against ground truth

`scripts/evaluate_llm_reward.py` scores Φ along each seeded fault's reachability witness and
over random-walk states, reporting how Φ rises along the witness, Φ at the fault-observable
step and its percentile among ordinary states, and an AUROC for fault-observable vs ordinary
states; with an experiment's reward logs it also relates the LLM terms to novelty,
redundancy, page changes and verified faults. `scripts/evaluate_llm_review.py` measures the
separate outcome review's precision and recall on the seeded faults.
