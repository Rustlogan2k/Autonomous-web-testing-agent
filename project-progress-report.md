# Project Progress Report — RL-based Automated Web Testing

**Status as of 8 October 2026 (afternoon).** Deadline: around 27 October 2026.

This report covers:

1. What we originally planned.
2. Which reinforcement-learning techniques we built and tested.
3. What the evidence showed, and why it changed the design.
4. The new hybrid pipeline, with the reason for each component.
5. **Every agent configuration ("arm") we have run, what it is and why it exists.**
6. **The development rounds: what each tested and what was decided.**
7. The data, the evaluation design, and the rebuild of the test applications now under way.
8. Where the work stands, and the timeline to the deadline.
9. What we can and cannot claim today.

Citation keys in square brackets (e.g. [ecoffet2021first]) refer to the project's literature review, `docs/literature_review.md`. Every number comes from a committed report under `reports/` or from run files under `runs/`; the source is named next to each number.

**How to read the numbers.** Unless stated otherwise, a run is a "hunt" of 2,000 browser interactions on one application. Its outcome is whether a seeded fault was **verified** (see §2, "Oracle"), and if so after how many interactions. "11/18" means 11 of 18 runs verified at least one fault.

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

- We do not trust console errors or LLM opinions. Each test application has **seeded faults**, and for each fault a **control build** with every fault except that one.
- A finding counts only when it replays twice and the matching control build does not show it.
- The trace is then shortened by delta debugging [zeller2002simplifying].
- This follows the fuzzing-evaluation advice to measure against known bugs [klees2018evaluating], [hazimeh2020magma]. The limitation is that seeded faults are only a partial stand-in for real ones [just2014mutants].

---

## 3. What the evidence showed, and what it changed

We ran two **pre-registered** experiments. The hypotheses, analysis code and decision rules were committed before any result existed. After them came one post-hoc check.

### 3.1 Experiment 1 — Does pretraining transfer to a new application?

**Report:** `reports/transfer_final.md`. Leave-one-application-out over the 6 benchmark apps; 10 arms (listed in §5.1), 210 runs, 2,000 interactions each.

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

**Report:** `reports/contextual_diagnostic.md`. We generated 12 variants of one workflow with synonyms, unseen words, pseudo-words, and changed layouts (tabs, a modal, reordered steps). We then probed whether the network ranks an action higher in states where that action actually helps.

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

### 4.2 Page abstraction

Each page becomes a small symbolic **cell**:

- the page kind;
- the URL path with ids replaced (`/orders/{n}`);
- the main heading;
- whether the form is empty, partly filled or full;
- whether an error or alert is shown;
- whether a submit action is offered.

Controls get roles from markup cues: submit, edit, remove, add, back, navigate. Two rules were added after development evidence (§6.2):

- **Site navigation is never "submit".** Links and buttons inside the page's navigation, header or footer start a new journey; they do not complete the current one.
- **A button that submits its form counts as a submit action** even when its label has no known keyword (e.g. "Use voucher"). This is read from the markup, not the wording. It is a development option for now (§5.4).

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
- enter invalid input (empty, wrong type, boundary minimum, boundary maximum);
- edit an earlier step after reaching review;
- go back and resume;
- reload and resume;
- double-submit;
- add and remove items in a collection;
- **new, in development:** combine two deviations on one form, e.g. a dropdown option together with an empty or invalid field (§4.6).

Each operator then follows the task through to submission, for up to four submissions. When a page offers several submit actions (e.g. "Update bag" and "Go to checkout"), the operators take them in turn on repeat visits.

- **Why:** these are textbook techniques: equivalence partitioning and boundary values (Myers, *The Art of Software Testing*), state-transition testing (Beizer, *Software Testing Techniques*) and pairwise/combinatorial testing. They are chosen from technique, not from any application's faults.
- This is how the tester deliberately leaves the happy path, which is what the LLM progress reward could not do (§3.3).

### 4.6 Decision layer: Thompson-sampling bandit (the RL part)

A Bayesian bandit learns online, inside each application, which operator works on which kind of target. Its keys are semantic, e.g. *(form page, invalid_input, text field)*, and it falls back to more general keys when data is thin. Thompson sampling handles exploration versus exploitation.

An optional arm adds a **cross-application prior**: counts pooled from development apps and capped at 5 pseudo-observations per key, so the new app's evidence quickly outweighs it.

**Why a bandit and not a deep Q-network:**

- Experiment 1 found no measurable gain from bootstrapped multi-step learning (DQN versus bandit from scratch: median +0).
- Online adaptation helps, and transfer from a handful of apps does not [kirk2023survey].
- A bandit over a few dozen interpretable keys learns within one 2,000-step run, where a deep network does not.
- Its keys use page kinds, never app-specific text, so a prior can carry across apps.

#### Is the learner helping? Current diagnosis

On the realistic suite apps, the learner is **not yet distinguishable from the same pipeline without it**. Over 42 matched runs (the same 14 apps and seeds) in suite rounds 2–3 (§6.2):

| Decision rule | Runs that verified a fault | Median interactions to first fault | Per-app mean difference vs no learner (95% interval) |
|---|---|---|---|
| No learner (untried operators first, at random) | 32 / 42 | 742 | — |
| Bandit, rewarded for any alert shown | 30 / 42 | 335 | −49 [−275, +189] |
| Bandit, rewarded for new pages only | 31 / 42 | 486 | −35 [−247, +187] |
| Bandit, rewarded for surprising responses | 31 / 42 | 586 | −46 [−246, +148] |

(Missed runs are counted as 2,000 interactions. Negative means faster.)

The learners tend to be faster, but every interval includes zero, and changing what the bandit is rewarded for makes no difference. **The reward is not the bottleneck; the design leaves the learner little to decide:**

1. **On about 5 of the 14 apps, a fault is found within the first few decisions**, before any learning can matter. Every rule often finds it at exactly the same step.
2. **The faults no rule finds need two deviations in one walk**, e.g. express delivery *and* an empty address. Each operator makes one deviation, so choosing among them more cleverly cannot produce the pair.
3. **The option space is small.** About 28 abstract pages × 8 operators can mostly be covered in about 135 decisions per run, so systematically trying untried options already does most of the work.
4. **Techniques with many targets crowd out the rest.** "Invalid input" took 30% of all decisions simply because pages have many fields.

**What we tried** (development round 4, result below):

- the **pairwise operator** (combinatorial testing), aimed at the faults no single deviation reaches; it also makes the option space too large to cover blindly, which is where a learner should earn its place;
- **choose the technique first, then its target**, so the learner's job becomes allocating attention across test techniques per application;
- then more seeds (5 instead of 3), so a real difference can be told apart from noise.

**Round 4 result:** the bundle was worse (26/40 runs without the learner, 25/40 with it, against 30/40 and 28/40 for the round-2 arms on the same 40 runs), so it was not adopted. Within it, the learner recovered part of the loss (faster on 7 apps, slower on 5; −63 interactions, 95% interval [−303, +169]), the pattern expected if the learner helps most when the fixed allocation is poor, but again not significant. Across four rounds the learner has never been distinguishable from no learner.

If the learner still adds nothing, the report will say plainly that the gains come from the structure (abstraction, archive, operators), not from learning. That is consistent with the literature (the Humanoid authors found their learned model "was not the main reason" for their gains [li2019humanoid]) and with Experiment 1. The held-out evaluation tests it either way.

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

## 5. Every arm, and why it exists

An **arm** is one agent configuration run under identical conditions (same apps, budget, seeds and oracle), so that differences between arms can be attributed. Arms come in four groups.

### 5.1 Experiment 1 arms (v1 transfer, frozen; `docs/methodology/2026-10-01-transfer-amendment.md`)

| Arm | What it is | Why it exists | Result (verified runs) |
|---|---|---|---|
| `random` | uniform choice among the page's valid actions | the difficulty floor every other arm must beat | 2 / 18 |
| `search_A0` | a fixed, hand-weighted priority over actions; no learning | the pre-registered non-learning search arm | 1 / 18 |
| `search_S` | a fixed linear score over **the learner's own features**; no learning | separates "learning" from "what the agent can read": same inputs as the learners, no learning | 3 / 18 |
| `bandit_scratch` | the action-conditioned network with γ = 0, trained from random weights on the target app | the pre-registered "learn on the target" arm (B) | 5 / 24 |
| `bandit_pretrained` | the same, pretrained on the other 5 apps; a residual head adapts on the target | the pre-registered transfer arm (C); C − B is the pre-registered transfer test | 10 / 24 |
| `dqn_scratch` | full DQN (γ > 0) from scratch, with the LLM reward | whether multi-step credit assignment helps (vs `bandit_scratch`) | 9 / 27 |
| **`dqn_pretrained`** | DQN pretrained on the other apps, residual head adapting, with the LLM reward | the **primary tester**; its comparison with `dqn_scratch` is the main transfer result | 9 / 27 |
| `dqn_zeroshot` | the pretrained DQN frozen (no adaptation), ε = 0.05 | isolates online adaptation: does the residual head matter? | 4 / 18 |
| `dqn_pretrained_nollm` | as `dqn_pretrained`, without the LLM term in pretraining or adaptation | isolates the LLM reward on the primary tester | 7 / 18 |
| `dqn_scratch_nollm` | as `dqn_scratch`, without the LLM term (added by amendment 2) | isolates the LLM reward on a learner with no prior | 5 / 18 |

**Decisions this group produced:** pretraining and the LLM reward were both dropped from the design (no measurable benefit); learning was kept but moved online, inside each app; the bandit was preferred over the DQN (no gain from bootstrapping).

### 5.2 Experiment 2 arms (contextual diagnostic, frozen; `docs/methodology/2026-10-04-contextual-diagnostic-preregistration.md`)

The same agent was run with parts of its input masked, so each arm answers "what can it do *without* this information?".

| Arm | What it sees | Why it exists |
|---|---|---|
| `ctx_full` | everything (the primary DQN agent, unmasked) | the agent under test |
| `action_only` | only the action's own descriptors; no page state | if `ctx_full` matches this, it is not using the state |
| `lexical_only` | only the action's label text | if `ctx_full` matches this, it is memorising words |
| `label_blind` | everything except the label | tests reliance on wording; should be unaffected by vocabulary changes |
| `search_S`, `random` | as in §5.1 | non-learning references and the chance level |

**Decision this group produced:** `ctx_full` was no better than `action_only` or `lexical_only` on the decisive probe (within-label AUROC 0.51), so the v2 state was made explicit and symbolic (§4.2).

### 5.3 Planned held-out evaluation arms (v2; draft pre-registration `docs/methodology/hybrid-v2-evaluation-preregistration.md`)

| Arm | What it is | Why it exists (the question it answers) |
|---|---|---|
| `random` | uniform over valid actions | the floor |
| `search_S` | v1's fixed feature-search score | v1's strongest non-learning baseline |
| `v1_dqn` | v1's DQN from scratch, without the LLM reward (as `dqn_scratch_nollm`) | **H: is v2 better than v1?** The LLM reward is left off because Experiment 1 found no benefit from it |
| `hybrid_nolearn` | the full hybrid pipeline; operators chosen uniformly among untried ones | the **ablation**: what the structure achieves without any learning |
| **`hybrid_bandit`** | the hybrid pipeline with the Thompson-sampling decision layer | the **primary v2 tester**; vs `hybrid_nolearn` answers "does the learner help?" |
| `hybrid_bandit_prior` | as `hybrid_bandit`, starting from the development apps' pooled evidence (capped at 5 pseudo-observations per key) | does knowledge carry across apps, in the small, safe form a prior allows? |
| `hybrid_bandit_llm` | as `hybrid_bandit`, with the LLM's page and control labels | **exploratory only**: the labels failed their accuracy gates (§4.3); kept at your request ("option A") to show whether they help or hurt anyway |

**Decision rule** for every comparison, fixed in advance: *supported* if the median per-app difference is negative and its 95% bootstrap interval excludes zero; *refuted* if positive and excluding zero; otherwise *inconclusive*.

### 5.4 Development arms (tuning only; results never used as evidence)

| Arm | What it changes | Why it was tried | Outcome and decision |
|---|---|---|---|
| `dev_bandit_search` | free exploration uses v1's search score; no depth preference | could v1's search guide the hybrid's free steps? | 5 / 18; not adopted |
| `dev_bandit_deep` | weighted free exploration plus a preference for deeper archived states (`depth_bias` 2.0) | deep faults were unreached; return to deeper states more often | **11 / 18, best; adopted as the configuration of every hybrid arm** |
| `dev_bandit_search_deep` | v1 search score plus depth preference | separates the two changes above | 10 / 18; the depth preference, not the search score, made the difference |
| `dev_bandit_novelty` | the bandit is rewarded only for genuinely new pages | the default rewards any error message, which rewards *expected* validation messages | 31 / 42; no difference (§4.6) |
| `dev_bandit_surprise` | the bandit is rewarded for *unexpected* responses (invalid input accepted silently, a valid step refused) | the response that points to a bug, by test-oracle reasoning | 31 / 42; no difference; the reward is not the bottleneck |
| `dev_pairwise_nolearn` | pairwise operator + technique-first choice + form-submitting buttons; no learner | reach the faults that need two deviations | 26 / 40, **worse** than without the bundle (slower on 9 of 14 apps); not adopted |
| `dev_pairwise_bandit` | the same, with the learner | does the learner help once it has real choices to make? | 25 / 40; the learner recovers part of the loss (faster on 7 apps, slower on 5) but not significantly; not adopted |

All development arms are options on the same code, so every earlier result stays reproducible. Whatever is adopted goes into all hybrid arms equally before the code freeze.

---

## 6. Development rounds: what each tested and what was decided

All rounds use 2,000 interactions per run and seeds 0–2.

### 6.1 On the 6 benchmark apps (6–7 October)

| Round | What it compared | Result | Decision |
|---|---|---|---|
| `v2-dev` (4 apps) | first end-to-end run: bandit, no learner, random | replay bug found: returns to archived states failed 6/14 (steps that raised a notice were pruned from stored walks); the fix was committed while jobs were starting, so the round mixed code versions | fixed (0/28 failed returns after); round discarded; **rule adopted: no code edits while a round is starting jobs** |
| `v2-dev2` | bandit vs no learner, plus clean builds | 4/18 vs 5/18; clean builds: no false seeded-fault findings, but the invariants raised 19 false alarms | validation-consistency invariant narrowed (same field, same page, one change at a time); fixes from two apps: form completion ticks checkboxes and radio groups, a dropdown operator, follow-through to submission, six free steps between operators |
| `v2-dev3` | exploration variants (§5.4) | `dev_bandit_deep` 11/18, `dev_bandit_search_deep` 10/18, others 4–5/18 | **depth preference + weighted exploration became the configuration of every hybrid arm** |
| `v2-dev3-clean` | the two best variants on clean builds | 0/12 false positives | the oracle stays clean with the new configuration |
| `v2-dev4` | final configuration; cells record *which* submit actions a page offers | 6/18 vs 4/18 | the exact set split one app's states from ~20 to ~50 and halved its discovery |
| `v2-dev5` (3 apps) | cells record only *whether* a page offers a submit action | 4/9 | **kept** |
| `v2-dev6` | bandit vs no learner with whole-word matching of role cues | 7/18 vs 4/18 | whole words missed "edits", "saving" and cut discovery on 4 apps |
| `v2-dev7` (5 apps) | prefix matching of cues; links count as submit only with a progression word | 10/15 (booking_desk excluded: 0 for every arm in every round) | **adopted** |

### 6.2 On the 8 suite apps plus the 6 benchmark apps (7–8 October)

| Round | What it compared | Result | Decision |
|---|---|---|---|
| Suite 1 (`v2-suite-dev1`; 8 suite apps) | bandit vs no learner | 18/24 vs 19/24; bagstreet 0/6 | diagnosis: the header link "Express checkout" (a deliberate decoy) was labelled a submit action and came first on every page; "Update bag" always preceded "Go to checkout". **Fix: site navigation never submits; operators take a page's submit actions in turn** |
| Suite 2 (`v2-suite-dev2`; 14 apps) | the same two arms after the fix | 30/42 vs 32/42; bagstreet 0 → 2/6; benchmark apps no worse | fix kept; the learner still not ahead, and found to value invalid input highest because validation messages counted as success |
| Suite 3 (`v2-suite-dev3`; 14 apps) | two corrected reward signals (§5.4) | 31/42 each; no difference from either earlier arm | **the reward is not the bottleneck**; diagnosis in §4.6 |
| Suite 4 (`v2-suite-dev4`; 14 apps) | pairwise operator + technique-first choice + form-submitting buttons, with and without the learner | 26/40 and 25/40 vs 30/40 and 28/40 for the round-2 arms on the same runs; the hard faults still unfound; 2 runs lost to page-load timeouts while the machine was also running app builds | **not adopted**: giving every technique an equal share moved ~40% of decisions to back/reload/complete steps, which rarely find faults here, and halved the productive invalid-input and dropdown probes; the pairs rarely hit the one combination a fault needs |

---

## 7. Data, evaluation design, and the test-application rebuild

### 7.1 Data split

To avoid tuning on the test set, the split is fixed by dated amendment (plan §12):

| Set | Apps | Use |
|---|---|---|
| Benchmark | 6 apps (from v1) | development |
| Generated variants (ctxbench) | 12 variants | development |
| Gitea (real third-party app) | 1 | development (robustness, false-alarm checks) |
| e2e suite | 8 apps, 23 seeded faults | **development / tuning** |
| **Validation set** | **8 apps, 24 seeded faults** | **held out**: no agent runs on it until the code is frozen and the evaluation is pre-registered |

The validation apps were planned and built by a separate process with no access to the tester's code or results, and differ from the suite in domain, architecture and fault design. All 8 passed admission (20/20 checks each, every fault confirmed in a browser). **Disclosed limitation:** the same developer builds both sets and knows the seeded faults.

### 7.2 Rebuild of all 16 test apps (decided 8 October)

**Why.** The apps used embedded SQLite or JSON files, had no login, and had small data models. They were end-to-end in the testing sense (browser → server → storage → page), but did not look like real deployments. We decided to rebuild all 16 on a real database server, with sign-in and richer data, before the code freeze. The tester only sees the browser, so this adds realism and more realistic bug types rather than changing what the tester can do.

**Design** (`e2e_suite/SUITE_V2_PLAN.md`, branch `suite-v2`):

- **Database by domain, balanced across both sets:**

  | | PostgreSQL (relational data) | MongoDB (document data) |
  |---|---|---|
  | Development | clinicflow, stackroom, tidewater (Python); ledgerly (Node) | bagstreet, helpline (Python); orbit, wayfarer (Node) |
  | Validation | dutyday, markbook, sideline (Python); tally (Node) | quizcraft (Python); fosterly, larder, skylark (Node) |

  MongoDB where the data is naturally a document (revisioned surveys, itineraries, recipes, application forms, sightings); PostgreSQL where it is naturally relational (ledgers, bookings, rotas, standings, elections). A mix rather than MongoDB everywhere, so each set covers both kinds of data and the bug types each brings (e.g. a partial update wiping nested fields; a stale duplicated value).
- **Sign-in on every app**, with seeded users and roles (customers and staff, receptionists and managers, teachers and students). The tester signs in at the start of each episode.
- **Richer data models**, checked at admission with fixed thresholds: for PostgreSQL at least 8 tables, foreign keys, CHECK and UNIQUE constraints and indexes; for MongoDB schema validators, nested arrays, varying record shapes and unique and compound indexes. The database must reject bad data in tests.
- **Every browser session gets its own copy of the data** (a schema or database per session), so each episode still starts from the same seed state.
- **All 47 faults keep their design.** 14 need a database rule deliberately left out so the bug can exist; these are declared in each app's manifest. One validation fault, TALLY-1, needs a small adaptation (ballots become secret). It was recorded in a dated addendum to the validation plan **before any agent touched those apps**.
- **Drivers are vendored** (bundled with each build, verified to work offline), because the product builds apps without network access.

**Status:** two builder agents started on 8 October. One is extending the test harness (database setup per build, data-model checks, login, sign-out and role checks); the other has bundled the MongoDB drivers and is rebuilding the first MongoDB pilot (orbit). The PostgreSQL pilot (stackroom) follows. Both stop after their pilot for review before the other 14 apps are rebuilt.

### 7.3 Planned held-out evaluation

- **Arms:** the 7 in §5.3.
- **Runs:** 2,000 interactions per run; seeds 0–4 on every app (280 runs), plus clean-build controls.
- **Statistics:** restricted mean time to the first verified fault per app; median difference over apps with a 95% bootstrap interval; stratified log-rank as secondary.
- **Decision rule:** supported / refuted / inconclusive, fixed in advance (§5.3).
- The draft pre-registration will be updated to name the rebuilt validation set and the final configuration, and **needs your approval before any agent runs on the validation apps**.

---

## 8. Current progress

### Done

- **v1:** the complete tester (CLI, web UI, Docker image), 6 benchmark apps, both pre-registered experiments run and reported, and the LLM reward validation.
- **Hybrid v2 core**, all committed with unit tests (1,258 passing at the last run): abstraction, archive, operators, invariants, bandit, cross-app prior, runner and analysis; an LLM labeller built and gated (failed; kept as an exploratory arm).
- **Development tuning:** 7 rounds on the benchmark apps and 3 on the suite (§6), with the fixes and decisions listed there. Reference numbers (development data, not evidence of generalisation):

  | Run | Arm | Verified runs |
  |---|---|---|
  | `v2-dev3` (6 benchmark apps) | final hybrid configuration | 11 / 18 |
  | `v2-dev3-clean` | same, on clean builds | 0 / 6 (no false positives) |
  | `v2-suite-dev2` (14 apps) | hybrid with learner / without | 30 / 42, 32 / 42 |
  | v1 reference (`transfer_final.md`, benchmark apps) | DQN pretrained / bandit pretrained | 9 / 27, 10 / 24 |

- **Validation set (current versions):** all 8 apps built and admitted.
- **Suite rebuild plan** written and approved; harness groundwork and PostgreSQL drivers committed on `suite-v2`.
- **Mentor documents:** `run-project.md` (how to run everything) and this report.

### In progress

- **Suite round 4** (pairwise operator; with and without the learner): about 5 hours.
- **Test-app rebuild:** harness extension and the two pilots (orbit on MongoDB, stackroom on PostgreSQL).

### Revised timeline

| Dates | Work |
|---|---|
| 8–10 Oct | pilots admitted and reviewed; learner experiments on the current apps (rounds 4–5) |
| 11–13 Oct | the 8 rebuilt development apps admitted |
| 14–16 Oct | re-tuning on the rebuilt development apps; the 8 rebuilt validation apps admitted |
| ~17 Oct | **code freeze**; pre-registration finalised and approved |
| 18–21 Oct | held-out evaluation runs |
| 22–27 Oct | analysis, final report, presentation and demo |

### Risks

- **Time.** The rebuild moved the freeze from 13 to about 17 October. Anything that slips is dropped as an arm (e.g. the exploratory LLM arm), never by relaxing the evaluation.
- **Builder capacity.** The rebuild is about 136 builder-hours; 1–3 apps may slip into a buffer on 17–18 October.
- **The learner may add nothing over the structured explorer.** The ablation is mandatory and will be reported either way (§4.6).
- **Statistical power.** 8 apps × 3 faults is small; inconclusive results will be reported as such.
- **Sign-in cost.** Signing in through the browser at every episode adds wall-clock time; if the pilots confirm it is large, the runner will sign in by a direct HTTP request (the login page itself is still tested through the browser at admission).
- **One benchmark app (booking_desk) has resisted every agent so far.**

---

## 9. What we can and cannot claim today

- **Can claim:**
  - A pre-registered negative result: pretraining did not transfer across our apps.
  - A diagnosed reason: the network is not contextual.
  - A validated finding that a small local LLM's progress reward does not generalise beyond the apps it was tuned on.
  - On development apps: the structured explorer (abstraction + archive + operators) finds faults in most runs with zero false positives; the learner on top is not yet distinguishable from no learner.
- **Cannot claim yet:** that the hybrid tester generalises, or that its learner helps. All its numbers so far are on development apps, and the held-out evaluation decides both.
- Security testing, and any real-world bug-finding rate, are outside this work.
