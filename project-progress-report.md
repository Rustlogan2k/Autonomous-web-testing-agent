# Project Progress Report — RL-based Automated Web Testing

**Status as of 7 October 2026.** Deadline: around 27 October 2026.

This report covers five things:

1. What we originally planned.
2. Which reinforcement-learning techniques we built and tested.
3. What the evidence showed, and why it changed the design.
4. The new hybrid pipeline, with the reason for each component.
5. Where the work stands now.

Citation keys in square brackets (e.g. [ecoffet2021first]) refer to the project's literature review, `docs/literature_review.md`. Every number comes from a committed report under `reports/` or from run files under `runs/`; the source is named next to each number.

---

## 1. The original plan

**Original title:** *Semantic-Aware Web Testing Agent — Autonomous Security & Functional Bug Detection using LLM and Reinforcement Learning.*

The plan had two agents, both exploring a live web application deployed from an uploaded repository:

| | Agent A — Security | Agent B — Functional |
|---|---|---|
| Goal | find vulnerabilities (injection, XSS, etc.) | find functional bugs (wrong behaviour, broken flows) |
| RL algorithm | PPO with an Intrinsic Curiosity Module (ICM) | DQN with prioritised experience replay (Stable-Baselines3) |
| Actions | a library of attack payloads | a fixed 100-slot action space over page controls |
| Reward | exploit success, plus a curiosity bonus | an LLM reward model (fine-tuned Phi-3 Mini) queried **every step**: "was this behaviour expected?", combined with deterministic triggers (console errors, HTTP 4xx/5xx, stuck pages), a novelty bonus and a repetition penalty |
| Training | a DVWA / WebGoat curriculum | the application under test |

### First scope decision (August 2026)

- We built Agent B first and treated Agent A as secondary. The reasoning was to get one agent working end to end before expanding, since a functional agent on its own is a complete result.
- **Agent A was later dropped. Security testing is out of scope.**
- The repository-intake part was kept, and a security policy gate was added to it. An uploaded `docker-compose.yml` can ask for host-level privileges, so every build definition is parsed and rejected if unsafe before anything runs.

---

## 2. How Agent B evolved (v1): the RL techniques we used

Each change below was made because a measurement on development applications showed it was needed.

| Technique | What it does here | Why we adopted it | Literature |
|---|---|---|---|
| **DQN** (deep Q-network) | learns the value of each action in each page state | the standard deep value-based learner, already used for web testing | [zheng2021webexplor], [gu2025webrled] |
| **Action-conditioned Q-network, Q(s, φ(a))** | scores each (state, action) pair, where φ(a) describes the control (label embedding, type, position); replaces the fixed 100 slots | pages have different numbers and kinds of controls, so a fixed slot index means nothing across pages or apps | [he2016drrn] (DRRN), [dulacarnold2015large], closest neighbour [fan2026webcq] |
| **Double DQN** | separates choosing an action from evaluating it | plain DQN overestimates action values | [vanhasselt2016double] |
| **Dueling heads** | splits state value from action advantage | pages have many near-equivalent links | [wang2016dueling] |
| **Prioritised replay, 3-step returns** | replays informative transitions more often; propagates reward faster | standard Rainbow components | [schaul2016prioritized], [hessel2018rainbow] |
| **Count-based novelty bonus** | rewards reaching unseen states | the usual curiosity signal in RL testers | [bellemare2016unifying], [tang2017exploration] |
| **Dynamic LLM progress reward** (`qwen2.5:7b-instruct`, local) | the LLM rates task progress (0–10) on each new page; paid as an episodic progress record plus an outcome bonus | to give the agent meaning ("you are closer to completing checkout"), not just novelty | [kwon2023reward], [ma2024eureka], [klissarov2024motif]; shaping theory [ng1999policy] |
| **Search-guided ε-exploration** | random exploratory actions are replaced, 75% of the time, by a hand-written search heuristic's choice | with uniform ε, pretraining never reached the order-confirmation pages, so the outcome reward never paid (`reports/dev_tuning.md` §2) | — |
| **Residual adaptation head** | a small head keeps learning on the new application while the pretrained network stays fixed | to adapt quickly to an unseen app | [kirk2023survey] suggests fast online adaptation |
| **Contextual bandit variant** (γ = 0) | the same network without bootstrapping | to test whether multi-step credit assignment adds anything | — |

**Changes to the LLM:**

- The per-step Phi-3 judge was replaced. We measured candidate models, and a local `qwen2.5:7b` reached 10/10 on our scripted corpus, against 3/10 for deterministic triggers.
- The LLM's job also changed: it scores *progress*, not "expected or not".

**Oracle** (how a bug is confirmed):

- We do not trust console errors or LLM opinions. Each benchmark application has **seeded faults** with a per-fault control build.
- A finding counts only when it replays twice and the matching control build does not show it.
- The trace is then shortened by delta debugging [zeller2002simplifying].
- This follows the fuzzing-evaluation advice to measure against known bugs [klees2018evaluating], [hazimeh2020magma]. The limitation is that seeded faults are only a partial stand-in for real ones [just2014mutants].

---

## 3. What the evidence showed, and what it changed

We ran two **pre-registered** experiments. The hypotheses, analysis code and decision rules were committed before any result existed. After them came one post-hoc check.

### 3.1 Experiment 1 — Does pretraining transfer to a new application?

**Report:** `reports/transfer_final.md`.

**Setup:**

- Leave-one-application-out over 6 benchmark apps.
- 10 arms, 210 runs, 2,000 interactions each.

| Arm | Runs that verified a fault |
|---|---|
| bandit, pretrained | 10 / 24 (0.42) |
| DQN, pretrained, without LLM | 7 / 18 (0.39) |
| DQN, pretrained | 9 / 27 (0.33) |
| DQN, from scratch | 9 / 27 (0.33) |
| search heuristic (search_S) | 3 / 18 (0.17) |
| random | 2 / 18 (0.11) |

**Findings:**

- **Pretraining gave no measurable transfer benefit.** For the same learner, pretrained versus scratch: median difference +0, 95% CI [−749, +290], log-rank p = 0.53.
- **Learned agents found faults in more runs than search or random.** Their fold-level intervals still reach zero, so this is a descriptive advantage, not a proven one.
- **Turning off the LLM reward made no measurable difference** (log-rank p = 0.75).
- **Faults deep in a workflow (9–16 contextual steps) were never found** on two apps by any arm.

This is what the literature predicts. Agents trained on a few environments overfit [cobbe2019quantifying], [cobbe2020procgen], and cross-app pretraining succeeded on Android only with 32 training apps [zhao2024dinodroid]. We had 5 per fold.

### 3.2 Experiment 2 — Does the agent understand context, or memorise?

**Report:** `reports/contextual_diagnostic.md`.

We generated 12 variants of one workflow with synonyms, unseen words, pseudo-words, and changed layouts (tabs, a modal, reordered steps). We then probed whether the network ranks an action higher in states where that action actually helps.

- **Contextual learning was not supported.** Within-label AUROC was 0.51 [0.44, 0.59], which is chance. Only 7% of the Q-value variance came from the label × state interaction: the state shifts every action's value together and almost never reorders the actions.
- The agent did rank useful actions above chance overall (top-1 accuracy 0.56 against random's 0.18). That skill held up under vocabulary changes but dropped under some layout changes.

**Implication:** the network's state representation does not encode what a page and its controls *are*. Whatever is needed to reason about a task stage has to be made explicit.

### 3.3 Post-hoc check — Is the LLM reward rewarding the right thing?

**Report:** `reports/llm_reward_validation.md`.

- On the two development apps, where the prompt was tuned, the progress score peaks exactly where faults appear (AUROC 1.00).
- On the four untouched apps it does not (AUROC 0.38–0.73).
- It rewards the "happy path", while many faults sit on list pages or need deviations (invalid input, edits after review).
- Most of what it pays duplicates the novelty bonus.

This matches the literature's warning that small LLM judges make noisy reward models [lin2024noisy], [klissarov2025modeling].

### 3.4 Summary: why we changed direction

| Finding | Consequence |
|---|---|
| Pretraining didn't transfer; in-app learning helped | keep learning, but **online, inside each application** |
| No state–action contextual learning | make the state **explicit**: page kind, task stage, form status |
| LLM reward misaligned with faults | **remove the LLM from the reward**; use it, if at all, to label pages |
| Deep faults unreached | explore by **remembering and returning** to states (Go-Explore) |
| LLM-as-policy testers are slow and repeat invalid actions [liu2026webgui] | keep the LLM **off the per-step path** |

The design is recorded in `docs/methodology/2026-10-06-hybrid-v2-plan.md`. All later changes are dated addenda; the frozen v1 results were never edited.

---

## 4. The new pipeline: the hybrid tester (v2)

The hybrid tester combines a structured explorer, test-design knowledge and a light online learner. Each step below lists what it does and why it was chosen.

```
 Launch ─► Observe ─► Abstract the page ─► Archive of states ─► Decide (bandit)
                                                                      │
   Report ◄─ Verify (replay ×2, controls, ddmin) ◄─ Check ◄─ Return to a state + apply a test operator
```

### 4.1 Launch and observe (reused from v1)

The tester accepts a URL, a static site, a Dockerfile repository, or a suite app with clean, faulty and per-fault control builds. Each episode uses a fresh browser context, and server state is reset between episodes.

### 4.2 Page abstraction (new)

Each page becomes a small symbolic **cell**:

- the page kind;
- the URL path with ids replaced (`/orders/{n}`);
- the main heading;
- whether the form is empty, partly filled or full;
- whether an error or alert is shown;
- whether a submit action is offered.

Controls get roles from markup cues: submit, edit, remove, add, back, navigate.

- **Why:** Experiment 2 showed the network does not learn this distinction by itself. Both the web-testing literature and the exploration literature find that the choice of state abstraction drives the results: [liu2026webgui] compared six abstractions, WebQT treats pages with the same function as one state [chang2023webqt], and count-based exploration depends on hash granularity [tang2017exploration].
- **How the granularity was tuned:** on development apps only. Exact field combinations gave 150–225 cells on one app, so they were reduced to empty/partial/full, which gave about 20.

### 4.3 LLM page labelling, tested and rejected as the main path

**Plan:** the LLM would label page kinds and control roles once per page (cached, closed vocabulary, JSON schema), never per step.

**What happened:** it had to pass a gate first, with ground truth and thresholds committed beforehand.

| Test | Page kind | Control role | Threshold | Result |
|---|---|---|---|---|
| Stage 0 (`reports/v2_stage0.md`) | 0.68 | 0.78 | ≥ 0.85 / ≥ 0.80 | fail |
| Revised labeller, fresh pages (`reports/v2_label_validation.md`) | 0.61 | 0.75 | same | fail |
| Same revised labeller, on the data it was revised from | 0.93 | 0.83 | same | (would have passed) |

- **Decision:** deterministic labels are the main path. The LLM labeller remains as one **exploratory** arm (`hybrid_bandit_llm`), reported separately.
- The last row is a lesson in itself. Judged on its own revision data, the labeller would have been adopted, and only fresh pages exposed the overfitting.
- **Why we kept the LLM off the decision path:** LLM-driven testers are slow and repeat invalid actions [liu2026webgui], and web-agent benchmarks show LLM agents remain unreliable on realistic sites [zhou2024webarena].

### 4.4 Archive with exact return

The tester keeps an archive of every cell it has seen, with the shortest known path to it, visit counts and the operators already tried. It chooses a cell weighted by novelty, untried opportunities and depth, replays its path exactly, and continues from there.

- **Why:** Go-Explore [ecoffet2021first] traces poor exploration to forgetting how to get back to promising states. Our deep, unfound faults were exactly that case.
- Every replayed action counts toward the interaction budget, so this is not a hidden advantage.
- One replay bug was found and fixed on development apps (failed returns went from 6/14 to 0/28).

### 4.5 Test operators from standard test design

Instead of clicking at random from a cell, the tester applies a **test operator**:

- complete the step;
- choose each option of a dropdown;
- enter invalid input;
- edit an earlier step after reaching review;
- go back and resume;
- reload and resume;
- double-submit;
- add and remove items in a collection.

Each operator then follows the task through to submission.

- **Why:** these are textbook techniques (equivalence partitioning and boundary values from Myers' *The Art of Software Testing*; state-transition testing from Beizer's *Software Testing Techniques*). They are chosen from technique, not from any application's faults.
- This is how the tester deliberately leaves the happy path, which is what the LLM progress reward could not do (§3.3).

### 4.6 Decision layer: Thompson-sampling bandit (the RL part)

A Bayesian bandit learns online, inside each application, which operator works on which kind of target. Its keys are semantic, e.g. *(form page, invalid_input, text field)*, and it falls back to more general keys when data is thin. Thompson sampling handles exploration versus exploitation.

An optional arm adds a **cross-application prior**: counts pooled from development apps and capped at 5 pseudo-observations per key, so the new app's evidence quickly outweighs it.

**Why a bandit and not a deep Q-network:**

- Experiment 1 found no measurable gain from bootstrapped multi-step learning (DQN versus bandit from scratch: median +0).
- Online adaptation helps, and transfer from a handful of apps does not [kirk2023survey].
- A bandit over a few dozen interpretable keys learns within one 2,000-step run, where a deep network does not.
- Its keys use page kinds, never app-specific text, so a prior can carry across apps.

**Honest caveat:** the learner's added value over the same pipeline *without* learning is not yet established. On development apps it was 7/18 versus 4/18 runs (`runs/v2-dev6`). The held-out evaluation includes this ablation as a required comparison.

### 4.7 Checking: two oracles

1. **Seeded-fault oracle (primary).** Unchanged from v1: replay ×2, a per-fault control build, delta-debugging minimisation. Zero false positives on clean builds throughout.
2. **Specification-free invariants (descriptive).** These look for bugs that need no spec:
   - an entered value must survive a reload;
   - an entered value must not come back altered;
   - the same field on the same page must not be rejected once and accepted another time.

   Candidates are replayed twice and attributed to seeded faults through the control builds.

**Why:** RL web testers usually rely on console errors only, an implicit oracle that misses wrong-content bugs [barr2015oracle], [zheng2021webexplor].

**Current status of the invariants:** they found **no** seeded faults on development apps and were noisy on stateful forms (19 false alarms before fixes). They are therefore reported as descriptive only, never as the headline.

### 4.8 Report

The report lists verified findings with minimal reproduction traces and evidence, keeps unverified candidates separate, and includes the archive as an exploration map.

---

## 5. Data and evaluation design

To avoid tuning on the test set, the data split is fixed by dated amendment (plan §12):

| Set | Apps | Use |
|---|---|---|
| Benchmark | 6 apps (from v1) | development |
| Generated variants (ctxbench) | 12 variants | development |
| Gitea (real third-party app) | 1 | development (robustness, false-alarm checks) |
| e2e suite | 8 real small web apps (Python/Node, real backends), 23 seeded faults | **development / tuning** |
| **New validation set** | **8 apps, 24 seeded faults** | **held out**: no agent runs on it until the code is frozen and the evaluation is pre-registered |

The validation apps were planned by a separate process that had no access to the tester's code or results. They differ from the suite in domain, architecture and fault design, and each fault comes from a standard fault class: persistence, state corruption, missing validation, broken transition.

**Planned held-out evaluation (draft pre-registration):**

- **Arms:** random, the v1 search heuristic, the v1 DQN, the hybrid without learning, the hybrid with bandit (primary), the hybrid with prior, and the hybrid with LLM labels (exploratory).
- **Runs:** 2,000 interactions each, several seeds per app.
- **Statistics:** survival analysis (restricted mean time to first verified fault, log-rank, application-clustered bootstrap).
- **Decision rule:** supported / refuted / inconclusive, fixed in advance.

**Disclosed limitation:** the same developer builds both sets and knows the seeded faults.

---

## 6. Current progress

### Done

- **v1:** the complete tester (CLI, web UI, Docker image), 6 benchmark apps, both pre-registered experiments run and reported, and the LLM reward validation.
- **Hybrid v2 core**, all committed with unit tests (about 1,250 tests, passing at last run):
  - abstraction, archive, operators, invariants, bandit, cross-app prior, runner and analysis;
  - an LLM labeller built and gated (it failed the gate and is kept as an exploratory arm).
- **Development results** on the 6 benchmark apps, 2,000 interactions per run. These are *development* numbers used for tuning, not evidence of generalisation.

  | Run | Arm | Runs that verified a fault |
  |---|---|---|
  | `runs/v2-dev3` (6 apps × 3 seeds) | hybrid + bandit + depth bias (became the final configuration) | 11 / 18 |
  | `runs/v2-dev3-clean` | same, on clean builds | 0 / 6 (no false positives) |
  | `runs/v2-dev7` (5 apps; booking_desk excluded, 0 for every arm) | final configuration after fixes | 10 / 15 |
  | v1 reference (`transfer_final.md`, same apps and budget) | DQN pretrained / bandit pretrained | 9 / 27, 10 / 24 |

- **Suite infrastructure test:** 23/23 fault witnesses confirmed, 0 false findings (`reports/v2_suite_smoke.json`).
- **Validation set: all 8 apps built and admitted.** Each passed 20/20 admission checks, with every seeded fault confirmed in a browser and clean on its control build, and a clean random walk raised no false candidates: dutyday, skylark, markbook, sideline, larder, tally, fosterly, quizcraft. The Docker-based admission checks are still to run.

### In progress

- **First tuning round on the 8 suite apps:** 2 arms × 3 seeds, 48 runs, about one third complete.

### Next (in order)

1. Run the Docker admission checks and the cross-set diversity check on the validation apps.
2. Tune on the suite apps (development only), and build the cross-app prior from development runs.
3. Update the draft pre-registration to name the new validation set; get approval; **freeze the code**.
4. Run the held-out evaluation, analyse it with the frozen rule, and report it, whichever way it comes out.
5. Wire the hybrid tester into the product CLI/UI; write the final report and demo.

### Risks

- **Time:** about 3 weeks remain. Anything that slips is dropped as an arm, not by relaxing the evaluation.
- **Statistical power:** 8 apps × 3 faults is small, and inconclusive results will be reported as such.
- **The learner may add nothing over the structured explorer.** That ablation is mandatory and will be reported either way.
- **One app (booking_desk) has resisted every agent so far.**

---

## 7. What we can and cannot claim today

- **Can claim:**
  - A pre-registered negative result: pretraining did not transfer across our apps.
  - A diagnosed reason: the network is not contextual.
  - A validated finding that a small local LLM's progress reward does not generalise beyond the apps it was tuned on.
- **Cannot claim yet:** that the hybrid tester generalises. All its numbers so far are on development apps, and the held-out evaluation decides that.
- Security testing, and any real-world bug-finding rate, are outside this work.
