# Literature Review: Reinforcement Learning, LLM-Derived Rewards and Evaluation Method for Automated Web Testing

Prepared 2026-09-30. Citations use the BibTeX keys in `docs/references/references.bib`; the index
and verification record are in `docs/references/README.md`.

**How to read this review.** Every statement about what a paper did or found comes from its abstract
or full text as retrieved on that date, and numbers are quoted only where they appear there.
Paragraphs headed "For this project" are the reviewer's reasoning, not claims made by the cited
authors.

## 1. The system being positioned

The project trains a Double-DQN-style agent that scores each available UI action with a shared
network Q(s, phi(a)), where phi(a) includes a sentence embedding of the action's label. It is
pretrained on several web applications and evaluated leave-one-application-out. The reward is
count-based novelty plus an LLM-assessed functional-progress potential of the page, cached, bounded
and applied as potential-based shaping. Faults are confirmed by a deterministic seeded-fault oracle
with replay and delta-debugging minimisation. The primary metric is budget-to-first-verified-fault
with right-censoring, with the application (fold) as the unit of inference.

## 2. Reinforcement learning for web and GUI testing

**Web.** WebExplor [zheng2021webexplor] is the reference point. It uses curiosity-driven RL to
generate action sequences and builds an automaton online for high-level guidance. The learner is
tabular Q-learning with a curiosity reward computed from a visit-count table over transitions, and
"RL training and web testing are intertwined": the policy is learned on the application under
test, not carried between applications. Its failures are errors reported in the browser console, and
the authors state that this oracle "may not be complete" and may miss UI bugs. WebQT
[chang2023webqt] changes the reward (to encourage human-tester-like interaction) and the state
abstraction (pages with the same functionality are one state), reporting higher code coverage on
seven open-source applications. QExplore [sherin2023qexplore] could not be retrieved beyond its
metadata; WebRLED's related-work section describes it as a tabular Q-learning method with a
text-input generation technique.

WebRLED [gu2025webrled] combines a DQN with a grid-based action-value scheme, a learned action
discriminator, and a curiosity reward that considers novelty both within an episode and over global
history. Liu et al. [liu2024crawler] train a DQN or PPO agent to guide a crawler toward higher
branch coverage. MARG [fan2024marg] runs several Q-learning agents that share or exchange Q-table
data; its successor WebCQ [fan2026webcq] argues that tabular methods do not scale and moves to deep
multi-agent RL. WebCQ is this project's closest architectural neighbour: because "the available UI
events vary across states", it builds a vector for each candidate action (including a GloVe-based
text-similarity feature and an execution-frequency feature), concatenates it with the state vector,
and feeds the pair to the Q-network.

A 2026 empirical study [liu2026webgui] compares model-based, RL-based and LLM-based web testers
under six state abstractions. No single strategy excels on all dimensions, RL-based approaches
achieve the highest coverage most frequently, compact state abstractions suit RL, and code coverage
is only weakly correlated with failure-revealing ability.

**Mobile.** Q-testing [pan2020qtesting] uses a curiosity-driven strategy whose reward comes from a
neural state-comparison module. ARES [romdhana2022ares] reports that deep RL achieves higher
coverage and fault revelation than baselines including Q-testing. DinoDroid [zhao2024dinodroid] is
the clearest precedent for cross-application learning: a deep Q-network learns "a behavior model
from a set of existing apps" and is then used on new apps, evaluated by two-fold cross-validation
over 64 apps, 32 for training in each fold. Humanoid [li2019humanoid] learns from human interaction
traces and reports higher coverage than six generators, but its authors add that "the learned model
was not the main reason of coverage improvement".

*For this project.* The agent design is not itself new. Count-based curiosity is the default reward
in this line of work, per-action scoring over a variable action set is published [fan2026webcq], and
pretraining on some applications to test others has been done on Android [zhao2024dinodroid]. The
project departs from per-application online training, implicit console-error oracles, and coverage
or failure counts as outcomes.

## 3. LLM-driven testing agents and web-navigation benchmarks

A second line replaces the learned policy with a language model. GPTDroid [liu2024gptdroid] frames
mobile GUI testing as a question-answering loop. DroidAgent [yoon2024droidagent] sets its own task
goals and pursues them, reporting 61% activity coverage against 51% for prior techniques on 15 apps.
AXNav [taeb2024axnav] executes natural-language accessibility tests with an LLM. VETL [wang2024vetl]
uses a vision-language model for text inputs and element selection on the web, with exploration
guided by a multi-armed bandit.

The empirical study tempers this line: its LLM-based web tester did not achieve high code coverage,
which the authors attribute to low execution efficiency (every decision invokes the model, so it
completed three to four times fewer actions in the same 30-minute budget) and to the model repeating
failed or invalid actions [liu2026webgui]. That supports keeping the LLM off the action-selection
path, as this project does with a cached potential.

Web-navigation benchmarks measure task completion, not testing, but they quantify how hard it is to
generalise between websites. On the World of Bits tasks [shi2017worldofbits], workflow-guided
exploration [liu2018workflow] observed that RL stagnates under sparse reward and that warm-starting
from demonstrations "is prone to overfitting". In WebArena [zhou2024webarena] the best GPT-4-based
agent reached 14.41% task success against 78.24% for humans. Mind2Web [deng2023mind2web] separates
cross-task, cross-website and cross-domain test splits: the best model's step success rate was 52.0%
cross-task but 38.9% and 39.6% on unseen websites and domains, and the authors conclude that
"generalizing to unseen environments is still a major challenge".

## 4. LLM-derived rewards and shaping theory

**Language models as reward sources.** Kwon et al. [kwon2023reward] prompt an LLM as a proxy reward
function that scores agent behaviour during training. ELLM [du2023ellm] rewards an agent for
achieving goals an LLM suggests for the current state, motivated by the observation that novelty
bonuses help little "in large environments where most discovered novelty is irrelevant". Motif
[klissarov2024motif] elicits LLM preferences over pairs of captions and distils them into an
intrinsic reward. Eureka [ma2024eureka] and Text2Reward [xie2024text2reward] instead have the LLM
write reward code for robotic control. Vision-language models have been used as success detectors
[du2023success] and as zero-shot reward models [rocamonde2024vlmrm]; the latter reports that larger
models are better reward models and that failures trace to capability limits of the model. Klissarov
et al. [klissarov2025modeling], whose domains include a hard subset of MiniWoB, find that LLMs are
better used to build reward models than to act directly, while noting that LLMs have unreliable
zero-shot understanding of environment dynamics.

**Potential-based shaping.** Ng, Harada and Russell [ng1999policy] prove that adding a reward equal
to the difference of a potential function over successive states preserves the optimal policy, and
that this form is also necessary: other transformations may yield suboptimal policies without
further assumptions. Wiewiora [wiewiora2003potential] shows that a learner with potential-based
shaping makes the same updates as one whose Q-values are initialised from the potential. Devlin and
Kudenko [devlin2012dynamic] extend the guarantee to potentials that change during learning, provided
each potential is evaluated at the time its state was visited, and prove that the dynamic case is no
longer equivalent to Q-table initialisation.

**LLM potentials already exist.** Lin et al. [lin2024noisy] train a state-scoring model from LLM
rankings and use score differences as a potential, showing that inconsistent rankings push the
reward toward zero, so uncertain feedback becomes uninformative instead of misleading. They also
report that in their MuJoCo experiments a direct reward outperformed the potential form when
low-performing LLMs were used. Müller and Kudenko [muller2026vlmpbrs] learn the potential from
preferences given by small, cheap VLMs and relate label accuracy to the sample-efficiency gain.
Bhambri et al. [bhambri2024heuristics], a preprint that is not peer reviewed, build a shaping signal
from an LLM-generated plan.

*For this project.* The literature supports using the LLM as a potential, with three limits. First,
policy invariance concerns the optimal policy; it says nothing about behaviour within a finite
budget, which is what the primary metric measures. Shaping is meant to change learning-time
behaviour, and a poor potential can change it for the worse. Second, the count-based novelty term is
not potential-based and is non-stationary, so the total reward does not inherit the guarantee.
Third, the evidence for LLM rewards comes from games, negotiation, robotics and synthetic
web-interaction tasks; none of the retrieved works evaluates an LLM-derived reward for testing.

## 5. Core deep RL components and exploration

DQN [mnih2013atari, mnih2015human] learns action values with a neural network. Double DQN
[vanhasselt2016double] shows that DQN overestimates action values and that decoupling selection from
evaluation reduces this. Dueling networks [wang2016dueling] separate state value from action
advantage and give "better policy evaluation in the presence of many similar-valued actions", which
fits pages with many near-equivalent links. Prioritised replay [schaul2016prioritized] replays
important transitions more often, and Rainbow [hessel2018rainbow] combines six such extensions,
including multi-step targets, with an ablation of each.

For exploration, pseudo-counts [bellemare2016unifying] generalise counting to non-tabular settings,
and hashing-based counts [tang2017exploration] work well with simple hash functions, with the caveat
that a good hash needs appropriate granularity and must encode task-relevant information. That is
the same sensitivity to state abstraction that the web-testing study measured [liu2026webgui].
Random network distillation [burda2019rnd] derives a novelty bonus from prediction error. Go-Explore
[ecoffet2021first] attributes poor exploration to forgetting how to reach visited states and to not
returning to a state before exploring from it, and remedies both by remembering states and first
returning to them.

These components were validated mainly on Atari, and Henderson et al. [henderson2018matters] show
how variance makes deep RL improvements hard to judge. Their benefit in web testing has to be shown
by ablation, not assumed.

## 6. Action representations for variable action sets

Dulac-Arnold et al. [dulacarnold2015large] embed actions in a continuous space so that a policy can
generalise over actions. The deep reinforcement relevance network [he2016drrn] is the direct
ancestor of Q(s, phi(a)): state and text action are embedded separately and combined by an
interaction function to approximate Q, and experiments with paraphrased actions indicate the model
extracts meaning and does not memorise strings. Jain et al. [jain2020generalization] pose zero-shot
generalisation to new actions as its own problem and propose a two-stage framework: action
representations are inferred from separately acquired action information, then a policy is trained
with generalisation objectives. Sentence-BERT [reimers2019sbert] supplies sentence embeddings
comparable by cosine similarity, and MiniLM [wang2020minilm] the distillation method behind compact
encoders.

*For this project.* Label-embedding action features are well founded, but Jain et al. treat
generalisation to unseen actions as needing dedicated training objectives; accepting a variable
action set at inference does not establish it. Humanoid [li2019humanoid] warns that a
learned prior over UI elements may not be the cause of an observed gain.

## 7. Generalisation and transfer

Cobbe et al. [cobbe2019quantifying] find that agents "overfit to surprisingly large training sets",
and the Procgen work [cobbe2020procgen] demonstrates that diverse environment distributions are
essential to train and evaluate RL agents adequately. The survey by Kirk et al. [kirk2023survey]
treats the number of training contexts as a core property of an evaluation protocol, notes that a
policy trained from scratch on a single level cannot reasonably be expected to generalise to
dissimilar levels, and suggests fast online adaptation as a direction.

*For this project.* Leave-one-application-out is the right protocol in kind, but each fold trains on
only a handful of applications, and the literature predicts weak zero-shot transfer at that scale.
Comparing a pretrained agent against one trained from scratch on the held-out application measures
adaptation speed, which the literature makes plausible. A zero-shot claim it does not.

## 8. Oracles, seeded faults and trace minimisation

Barr et al. [barr2015oracle] classify oracles as specified, derived, implicit, or absent (leaving
the human). RL web testers use implicit oracles, namely console errors [zheng2021webexplor,
gu2025webrled], which cannot see a page that renders wrong content without error. A seeded-fault
oracle is a specified oracle for a known fault set. It provides ground truth, which fuzzing
methodology prefers: Klees et al. [klees2018evaluating] recommend measuring against known bugs over
heuristic proxies, and Magma [hazimeh2020magma] introduces real bugs into real software with
instrumentation so that bug-centric metrics are independent of the tool. The cost is validity. Just
et al. [just2014mutants], studying 357 real faults in five Java programs, found mutant detection
significantly correlated with real-fault detection, a coupling effect for 73% of real faults, and
17% of real faults not coupled to any mutant. Seeded faults are a supported but incomplete proxy,
and the evidence is from JUnit suites, not web GUIs.

Delta debugging [zeller2002simplifying] minimises a failing input while preserving the failure; its
case study reduced 95 user actions that crashed a browser to 3, the use made of it here.

## 9. Evaluation statistics

Arcuri and Briand [arcuri2011practical] found that software-engineering studies of randomised
algorithms often did not properly account for randomness, and argue for enough runs and rigorous
statistical tests. Klees et al. [klees2018evaluating] examined 32 fuzzing papers and "found problems
in every evaluation", recommending multiple trials, statistical tests, ground truth and a diverse
benchmark; a follow-up over 150 papers found continued disregard of those guidelines
[schloegel2024sok]. Agarwal et al. [agarwal2021precipice] find substantial discrepancies between
conclusions drawn from point estimates and from fuller statistical analysis, recommend
stratified-bootstrap interval estimates, and state that "statistical concerns cannot be
satisfactorily addressed with few runs".

Time-to-event analysis is established in fuzzing. Magma applies the Kaplan-Meier estimator to
time-to-bug over ten repeated trials, since a fuzzer may not find a bug in the time allowed
[hazimeh2020magma]. Herrera et al. [herrera2021seed] state that survival analysis "allows us to
handle censoring", report restricted mean survival time with confidence intervals over 30 trials,
and compare with the log-rank test. None of the GUI-testing papers whose full text was retrieved
uses censoring-aware analysis; WebExplor averages 15 thirty-minute runs and applies a Mann-Whitney U
test [zheng2021webexplor], and the 2026 study averages five [liu2026webgui].

## 10. Gap this project addresses

Within the literature retrieved for this review:

1. **Cross-application transfer on the web, measured against ground truth.** Web RL testers learn
   online on the application under test and are scored on coverage and console failures
   [zheng2021webexplor, chang2023webqt, gu2025webrled, fan2026webcq]. Cross-application learning
   has been shown for Android [zhao2024dinodroid]. No retrieved work evaluates held-out-application
   transfer of a web-testing policy against a deterministic seeded-fault oracle.
2. **An LLM-derived potential in a testing domain.** LLM potentials exist for grid worlds and
   continuous control [lin2024noisy, muller2026vlmpbrs], and LLMs in GUI testing act as the policy
   [liu2024gptdroid, yoon2024droidagent, wang2024vetl]. Using an LLM only to shape a cheap learned
   policy, which avoids the per-step cost that limited the LLM tester in [liu2026webgui], is
   untested in the retrieved work.
3. **Censoring-aware, fault-centric evaluation for GUI testing.** Budget-to-first-verified-fault
   with right-censoring imports fuzzing's time-to-bug method [hazimeh2020magma, herrera2021seed]
   into a field that reports averaged coverage, and responds to the finding that coverage is weakly
   related to failure discovery [liu2026webgui].

The contribution is the combination and the evaluation discipline. The agent architecture, the
curiosity reward and the LLM potential each have direct precedents and should not be claimed as
novel.

## 11. Threats acknowledged by the literature

- **Too few training applications for zero-shot claims.** Overfitting persists at large training
  set sizes [cobbe2019quantifying], diversity is essential [cobbe2020procgen], unseen websites cost a
  clear performance drop even for large pretrained models [deng2023mind2web], and the one
  cross-application GUI-testing result reviewed here trained on 32 apps [zhao2024dinodroid]. A null
  or small transfer effect is the expected outcome.
- **Shaping guarantees do not cover the measured quantity.** Invariance is a statement about the
  optimal policy [ng1999policy, devlin2012dynamic], not about finite-budget discovery, and the
  novelty bonus falls outside the theorem.
- **LLM reward quality depends on model capability.** With weaker models the potential form lost
  to a direct reward [lin2024noisy]; reward-model failures track model limitations
  [rocamonde2024vlmrm]; zero-shot understanding of unfamiliar dynamics is unreliable
  [klissarov2025modeling]. A small, cached judge is on the weak side of this evidence.
- **Learned priors may not be what helps.** Humanoid's authors found their learned model was not
  the main cause of its gain [li2019humanoid]; ablations against non-learned baselines are required.
- **Seeded faults are an imperfect proxy.** In one study 17% of real faults were not coupled to
  mutants [just2014mutants], and that study was not on web applications. A seeded-fault oracle also
  sees only what was seeded; implicit and human oracles [barr2015oracle] see other classes.
- **Statistical power.** Guidance from software engineering, fuzzing and deep RL converges on many
  repetitions with interval estimates [arcuri2011practical, klees2018evaluating,
  agarwal2021precipice]. With the application as the unit of inference the sample size is the
  number of folds, which is small, and runs pooled across folds are not independent.
- **Component benefits are inherited from other domains.** Double DQN, dueling, prioritised replay
  and n-step returns were established on Atari [hessel2018rainbow], and deep RL results are
  sensitive to codebase and seeds [henderson2018matters].
- **State abstraction can dominate.** Count-based exploration [tang2017exploration] and web
  testing effectiveness [liu2026webgui] both depend strongly on how states are abstracted.
