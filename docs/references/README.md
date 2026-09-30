# References for the literature review

Compiled 2026-09-30 for `docs/literature_review.md`.

- `references.bib`: 62 BibTeX entries, one per paper.
- `pdf/`: 45 PDFs, about 97 MB in total, all from open-access sources (arXiv, PMLR, the IFAAMAS
  proceedings site, UCL Discovery, and authors' own university pages).
- This file: index, local-copy status, and the verification record.

## Rules followed

- **Nothing is cited from memory.** Every entry's title, authors, year and venue were taken from a
  record fetched in this session. Fields that could not be confirmed (some page ranges, conference
  ordinals) are left out of the BibTeX entry.
- **Open access only.** No paywall-bypass source was used. Where a paper is not openly available,
  or its open-access status could not be confirmed, it is cited with its DOI and not downloaded.
- **Every PDF was checked** after download: it starts with `%PDF` and is larger than 50 KB.
- **Size cap.** Ten open-access PDFs larger than 7 MB were downloaded, read, and then removed to
  keep the folder small. They are marked "not stored (size)" below with a direct link.
- Downloads used `curl` with a descriptive User-Agent and a 2-second pause between requests.

## Verification sources (codes used in the table)

| Code | Source |
|------|--------|
| A | arXiv API record (`export.arxiv.org/api/query`): title, authors, date, abstract, and the authors' comment / journal-reference fields |
| C | Crossref DOI record (`api.crossref.org`): title, authors, venue, year, pages, licence |
| P | Proceedings record: PMLR page metadata, `papers.nips.cc` listing, or the OpenReview API venue field |
| S | Semantic Scholar API record for the DOI (used for abstracts of papers whose publisher page blocks automated access) |
| F | Text of the PDF itself (first page and, where the review quotes it, the body) |
| W | A fetched web page: publisher, conference, institutional repository or author page |

## Local-copy status labels

- a filename: stored in `pdf/`.
- **not stored (size)**: open access, but larger than 7 MB; use the link.
- **not open access**: publisher copy is paywalled and no legitimate open copy was found.
- **not downloaded (status unconfirmed)**: publisher page refused automated access, so neither the
  PDF nor its open-access status could be confirmed in this session.
- **not stored (download blocked)**: the Crossref record carries a CC BY licence, but the publisher
  refused automated download.

## Index

### 1. RL for web application testing and crawling

| Key | Title | Authors | Venue, year | URL | Local PDF | Why it matters here | Verified |
|-----|-------|---------|-------------|-----|-----------|---------------------|----------|
| `zheng2021webexplor` | Automatic Web Testing Using Curiosity-Driven Reinforcement Learning | Zheng, Liu, Xie, Liu, Ma, Hao, Liu | ICSE 2021 | https://arxiv.org/abs/2103.06018 | `zheng2021_webexplor.pdf` | The reference RL web tester: tabular Q-learning, count-based curiosity, console-error oracle, per-application online training | A, C, F |
| `sherin2023qexplore` | QExplore: An Exploration Strategy for Dynamic Web Applications Using Guided Search | Sherin, Muqeet, Khan, Iqbal | Journal of Systems and Software 195, 2023 | https://doi.org/10.1016/j.jss.2022.111512 | not open access | Q-learning-style web exploration baseline; only metadata could be retrieved | C, S (no abstract available); described via the related-work text of `gu2025webrled` |
| `chang2023webqt` | A Reinforcement Learning Approach to Generating Test Cases for Web Applications (WebQT) | Chang et al. (10 authors) | AST 2023 | https://doi.org/10.1109/AST58925.2023.00006 | not open access | Shows reward design and state abstraction, not the RL algorithm, drive gains in web testing | C, S |
| `fan2024marg` | Can Cooperative Multi-Agent Reinforcement Learning Boost Automatic Web Testing? An Exploratory Study (MARG) | Fan, Wang, Fei, Qin, Li, Liu | ASE 2024 | https://doi.org/10.1145/3691620.3694983 | not downloaded (status unconfirmed) | Multi-agent tabular Q-learning for web testing; the baseline WebCQ replaces | C, W (ASE 2024 programme page) |
| `gu2025webrled` | Deep Reinforcement Learning for Automated Web GUI Testing (WebRLED) | Gu, Liu, Wu, Zhang, Yang, Liang, Chen, Wei | arXiv 2025 | https://arxiv.org/abs/2504.19237 | `gu2025_webrled.pdf` | DQN-based web tester with episodic plus global curiosity; nearest deep-RL comparison | A, F |
| `wang2024vetl` | Leveraging Large Vision-Language Model for Better Automatic Web GUI Testing (VETL) | Wang, Wang, Fan, Li, Liu | ICSME 2024 | https://arxiv.org/abs/2410.12157 | `wang2024_vetl.pdf` | LLM/VLM as the acting policy for web testing, with bandit-guided exploration | A, C |
| `fan2026webcq` | WebCQ: Cooperative Multi-Agent Deep Reinforcement Learning for Scalable Web GUI Testing | Fan, Wang, Fei, Qin, Li, Liu | arXiv 2026 (accepted to ASE 2026 per arXiv record) | https://arxiv.org/abs/2606.22502 | `fan2026_webcq.pdf` | Closest published architecture: per-action vector concatenated with state vector into a DQN over a variable action set | A, F |
| `liu2026webgui` | Understanding Automated Web GUI Testing: An Empirical Study Across Exploration Strategies and State Abstractions | Liu, Yang, Zhang, Xie | arXiv 2026 | https://arxiv.org/abs/2606.16650 | `liu2026_webgui_empirical.pdf` | Independent comparison of RL, model-based and LLM web testers; coverage weakly related to failures; per-step LLM is slow | A, F |
| `liu2024crawler` | A Reinforcement Learning Approach to Guide Web Crawler to Explore Web Applications for Improving Code Coverage | Liu, You, Chiu | Electronics 13(2), 2024 | https://doi.org/10.3390/electronics13020427 | not stored (download blocked) | DQN/PPO-guided web crawling for coverage | C (includes abstract) |

### 2. RL and deep learning for mobile GUI testing

| Key | Title | Authors | Venue, year | URL | Local PDF | Why it matters here | Verified |
|-----|-------|---------|-------------|-----|-----------|---------------------|----------|
| `pan2020qtesting` | Reinforcement Learning Based Curiosity-Driven Testing of Android Applications (Q-testing) | Pan, Huang, Wang, Zhang, Li | ISSTA 2020 | https://doi.org/10.1145/3395363.3397354 | not downloaded (status unconfirmed) | Curiosity reward from a learned state-comparison module | C, S |
| `li2019humanoid` | Humanoid: A Deep Learning-Based Approach to Automated Black-box Android App Testing | Li, Yang, Guo, Chen | ASE 2019 | https://arxiv.org/abs/1901.02633 | `li2019_humanoid.pdf` | Authors report their learned model was not the main cause of the coverage gain | A, C |
| `romdhana2022ares` | Deep Reinforcement Learning for Black-Box Testing of Android Apps (ARES) | Romdhana, Merlo, Ceccato, Tonella | ACM TOSEM 31(4), 2022 | https://arxiv.org/abs/2101.02636 | `romdhana2022_ares.pdf` | Deep RL versus tabular RL and random for GUI testing | A, F; volume and issue from the reference list of `gu2025webrled` |
| `zhao2024dinodroid` | DinoDroid: Testing Android Apps Using Deep Q-Networks | Zhao, Harrison, Yu | ACM TOSEM 33(5), 2024 | https://arxiv.org/abs/2210.06307 | `zhao2024_dinodroid.pdf` | Precedent for training a DQN on some apps and testing on others | A, C, F |

### 3. LLM-driven GUI/web testing agents and web-navigation benchmarks

| Key | Title | Authors | Venue, year | URL | Local PDF | Why it matters here | Verified |
|-----|-------|---------|-------------|-----|-----------|---------------------|----------|
| `liu2024gptdroid` | Make LLM a Testing Expert: Bringing Human-like Interaction to Mobile GUI Testing via Functionality-aware Decisions (GPTDroid) | Liu et al. (8 authors) | ICSE 2024 | https://arxiv.org/abs/2310.15780 | `liu2024_gptdroid.pdf` | LLM as the testing policy | A, C |
| `yoon2024droidagent` | Intent-Driven Mobile GUI Testing with Autonomous Large Language Model Agents (DroidAgent) | Yoon, Feldt, Yoo | ICST 2024 | https://arxiv.org/abs/2311.08649 | `yoon2024_droidagent.pdf` | Goal-setting LLM test agent; the arXiv version has a different title | A, C |
| `taeb2024axnav` | AXNav: Replaying Accessibility Tests from Natural Language | Taeb, Swearngin, Schoop, Cheng, Jiang, Nichols | CHI 2024 | https://arxiv.org/abs/2310.02424 | `taeb2024_axnav.pdf` | LLM-executed natural-language UI tests | A, C |
| `zhou2024webarena` | WebArena: A Realistic Web Environment for Building Autonomous Agents | Zhou et al. (12 authors) | ICLR 2024 | https://arxiv.org/abs/2307.13854 | not stored (size): https://arxiv.org/pdf/2307.13854 | How far LLM agents are from human performance on realistic web apps | A, P |
| `deng2023mind2web` | Mind2Web: Towards a Generalist Agent for the Web | Deng et al. (8 authors) | NeurIPS 2023 (Datasets and Benchmarks) | https://arxiv.org/abs/2306.06070 | not stored (size): https://arxiv.org/pdf/2306.06070 | Quantifies the performance drop on unseen websites | A, P, F |
| `shi2017worldofbits` | World of Bits: An Open-Domain Platform for Web-Based Agents | Shi, Karpathy, Fan, Hernandez, Liang | ICML 2017 (PMLR 70) | https://proceedings.mlr.press/v70/shi17a.html | `shi2017_world_of_bits.pdf` | Origin of the MiniWoB-style web RL tasks | P, W |
| `liu2018workflow` | Reinforcement Learning on Web Interfaces Using Workflow-Guided Exploration | Liu, Guu, Pasupat, Shi, Liang | ICLR 2018 | https://arxiv.org/abs/1802.08802 | `liu2018_workflow_guided.pdf` | Sparse-reward stagnation of RL on web tasks | A |

### 4. LLM- and VLM-derived rewards

| Key | Title | Authors | Venue, year | URL | Local PDF | Why it matters here | Verified |
|-----|-------|---------|-------------|-----|-----------|---------------------|----------|
| `kwon2023reward` | Reward Design with Language Models | Kwon, Xie, Bullard, Sadigh | ICLR 2023 | https://arxiv.org/abs/2303.00001 | `kwon2023_reward_design_lm.pdf` | LLM as a proxy reward function queried during training | A |
| `du2023ellm` | Guiding Pretraining in Reinforcement Learning with Large Language Models (ELLM) | Du et al. (8 authors) | ICML 2023 (PMLR 202) | https://arxiv.org/abs/2302.06692 | `du2023_ellm.pdf` | Argues plain novelty is often irrelevant; LLM steers exploration | A, P |
| `ma2024eureka` | Eureka: Human-Level Reward Design via Coding Large Language Models | Ma et al. (9 authors) | ICLR 2024 | https://arxiv.org/abs/2310.12931 | `ma2024_eureka.pdf` | LLM-written reward code; a different mechanism from a learned potential | A |
| `xie2024text2reward` | Text2Reward: Reward Shaping with Language Models for Reinforcement Learning | Xie et al. (8 authors) | ICLR 2024 | https://arxiv.org/abs/2309.11489 | not stored (size): https://arxiv.org/pdf/2309.11489 | LLM-generated dense reward programs | A, P |
| `klissarov2024motif` | Motif: Intrinsic Motivation from Artificial Intelligence Feedback | Klissarov et al. (8 authors) | ICLR 2024 | https://arxiv.org/abs/2310.00166 | `klissarov2024_motif.pdf` | Intrinsic reward distilled from LLM preferences | A, P |
| `du2023success` | Vision-Language Models as Success Detectors | Du et al. (8 authors) | arXiv 2023 | https://arxiv.org/abs/2303.07280 | not stored (size): https://arxiv.org/pdf/2303.07280 | Pretrained model as a success/reward detector and its generalisation limits | A, P |
| `rocamonde2024vlmrm` | Vision-Language Models are Zero-Shot Reward Models for Reinforcement Learning | Rocamonde, Montesinos, Nava, Perez, Lindner | ICLR 2024 | https://arxiv.org/abs/2310.12921 | `rocamonde2024_vlm_reward_models.pdf` | Reward quality scales with model capability | A |
| `bhambri2024heuristics` | Extracting Heuristics from Large Language Models for Reward Shaping in Reinforcement Learning | Bhambri et al. (6 authors) | arXiv 2024 (not peer reviewed) | https://arxiv.org/abs/2405.15194 | not stored (size): https://arxiv.org/pdf/2405.15194 | LLM-derived shaping heuristics, with and without a verifier | A, P (OpenReview lists a withdrawn ICLR 2025 submission) |
| `lin2024noisy` | Navigating Noisy Feedback: Enhancing Reinforcement Learning with Error-Prone Language Models | Lin et al. (9 authors) | EMNLP 2024 | https://arxiv.org/abs/2410.17389 | `lin2024_noisy_llm_feedback.pdf` | LLM feedback used as a potential function; analysis of noisy LLM rewards | A, F |
| `klissarov2025modeling` | On the Modeling Capabilities of Large Language Models for Sequential Decision Making | Klissarov, Hjelm, Toshev, Mazoure | ICLR 2025 | https://arxiv.org/abs/2410.05656 | `klissarov2025_llm_modeling.pdf` | LLMs as reward models versus policies, including web-interaction tasks | A, P, F |
| `muller2026vlmpbrs` | Automating Potential-based Reward Shaping with Vision Language Model Guidance | Müller, Kudenko | arXiv 2026 | https://arxiv.org/abs/2606.27180 | not stored (size): https://arxiv.org/pdf/2606.27180 | Potential function learned from small-VLM preferences | A, F (read before removal) |

### 5. Reward-shaping theory

| Key | Title | Authors | Venue, year | URL | Local PDF | Why it matters here | Verified |
|-----|-------|---------|-------------|-----|-----------|---------------------|----------|
| `ng1999policy` | Policy Invariance Under Reward Transformations: Theory and Application to Reward Shaping | Ng, Harada, Russell | ICML 1999 | https://people.eecs.berkeley.edu/~russell/papers/icml99-shaping.pdf | `ng1999_policy_invariance.pdf` | The policy-invariance theorem the LLM reward relies on | F, W (S. Russell's publication list); pages from the reference list of `devlin2012dynamic` |
| `wiewiora2003potential` | Potential-Based Shaping and Q-Value Initialization are Equivalent | Wiewiora | JAIR 19, 2003 | https://arxiv.org/abs/1106.5267 | `wiewiora2003_shaping_qinit.pdf` | Shaping equals Q-initialisation for static potentials | A, W (JAIR article page) |
| `devlin2012dynamic` | Dynamic Potential-Based Reward Shaping | Devlin, Kudenko | AAMAS 2012 | https://www.ifaamas.org/Proceedings/aamas2012/papers/2C_3.pdf | `devlin2012_dynamic_pbrs.pdf` | Extends the guarantee to potentials that change during learning | F, C |

### 6. Core deep RL components

| Key | Title | Authors | Venue, year | URL | Local PDF | Why it matters here | Verified |
|-----|-------|---------|-------------|-----|-----------|---------------------|----------|
| `mnih2013atari` | Playing Atari with Deep Reinforcement Learning | Mnih et al. (7 authors) | NIPS Deep Learning Workshop 2013 (arXiv) | https://arxiv.org/abs/1312.5602 | `mnih2013_atari_dqn.pdf` | DQN, open-access version | A |
| `mnih2015human` | Human-Level Control Through Deep Reinforcement Learning | Mnih et al. (19 authors) | Nature 518, 2015 | https://doi.org/10.1038/nature14236 | not open access | DQN, archival version | C |
| `vanhasselt2016double` | Deep Reinforcement Learning with Double Q-Learning | van Hasselt, Guez, Silver | AAAI 2016 | https://arxiv.org/abs/1509.06461 | `vanhasselt2016_double_dqn.pdf` | Overestimation correction used by the agent | A, C |
| `wang2016dueling` | Dueling Network Architectures for Deep Reinforcement Learning | Wang, Schaul, Hessel, van Hasselt, Lanctot, de Freitas | ICML 2016 (PMLR 48) | https://arxiv.org/abs/1511.06581 | `wang2016_dueling.pdf` | Value/advantage split for many similar-valued actions | A, P |
| `schaul2016prioritized` | Prioritized Experience Replay | Schaul, Quan, Antonoglou, Silver | ICLR 2016 | https://arxiv.org/abs/1511.05952 | `schaul2016_per.pdf` | Replay prioritisation | A |
| `hessel2018rainbow` | Rainbow: Combining Improvements in Deep Reinforcement Learning | Hessel et al. (10 authors) | AAAI 2018 | https://arxiv.org/abs/1710.02298 | `hessel2018_rainbow.pdf` | Combination and ablation of DQN extensions including n-step returns | A, C, F |
| `bellemare2016unifying` | Unifying Count-Based Exploration and Intrinsic Motivation | Bellemare, Srinivasan, Ostrovski, Schaul, Saxton, Munos | NIPS 2016 | https://arxiv.org/abs/1606.01868 | `bellemare2016_pseudocounts.pdf` | Pseudo-counts as intrinsic reward | A, P |
| `tang2017exploration` | #Exploration: A Study of Count-Based Exploration for Deep Reinforcement Learning | Tang et al. (9 authors) | NIPS 2017 | https://arxiv.org/abs/1611.04717 | `tang2017_hash_exploration.pdf` | Hash-based counts; hash granularity matters | A, P |
| `burda2019rnd` | Exploration by Random Network Distillation | Burda, Edwards, Storkey, Klimov | ICLR 2019 | https://arxiv.org/abs/1810.12894 | `burda2019_rnd.pdf` | Alternative novelty bonus | A, P |
| `ecoffet2021first` | First Return, Then Explore (Go-Explore) | Ecoffet, Huizinga, Lehman, Stanley, Clune | Nature 590, 2021 | https://arxiv.org/abs/2004.12919 | not stored (size): https://arxiv.org/pdf/2004.12919 | Return-then-explore principle | A, C |

### 7. Action representations for large or variable action sets

| Key | Title | Authors | Venue, year | URL | Local PDF | Why it matters here | Verified |
|-----|-------|---------|-------------|-----|-----------|---------------------|----------|
| `dulacarnold2015large` | Deep Reinforcement Learning in Large Discrete Action Spaces | Dulac-Arnold et al. (10 authors) | arXiv 2015 | https://arxiv.org/abs/1512.07679 | `dulacarnold2015_large_discrete.pdf` | Action embeddings for generalising over actions | A |
| `he2016drrn` | Deep Reinforcement Learning with a Natural Language Action Space (DRRN) | He, Chen, He, Gao, Li, Deng, Ostendorf | ACL 2016 | https://arxiv.org/abs/1511.04636 | `he2016_drrn.pdf` | Direct ancestor of Q(s, phi(a)) over text actions | A |
| `jain2020generalization` | Generalization to New Actions in Reinforcement Learning | Jain, Szot, Lim | ICML 2020 (PMLR 119) | https://arxiv.org/abs/2011.01928 | `jain2020_new_actions.pdf` | What zero-shot generalisation to unseen actions requires | A, P |

### 8. Generalisation and transfer in RL

| Key | Title | Authors | Venue, year | URL | Local PDF | Why it matters here | Verified |
|-----|-------|---------|-------------|-----|-----------|---------------------|----------|
| `kirk2023survey` | A Survey of Zero-shot Generalisation in Deep Reinforcement Learning | Kirk, Zhang, Grefenstette, Rocktäschel | JAIR 76, 2023 | https://arxiv.org/abs/2111.09794 | `kirk2023_zsg_survey.pdf` | Framework and expectations for train/test-context protocols | A, F |
| `cobbe2019quantifying` | Quantifying Generalization in Reinforcement Learning (CoinRun) | Cobbe, Klimov, Hesse, Kim, Schulman | ICML 2019 (PMLR 97) | https://arxiv.org/abs/1812.02341 | not stored (size): https://arxiv.org/pdf/1812.02341 | Agents overfit even to large training sets | A, P |
| `cobbe2020procgen` | Leveraging Procedural Generation to Benchmark Reinforcement Learning (Procgen) | Cobbe, Hesse, Hilton, Schulman | ICML 2020 (PMLR 119) | https://arxiv.org/abs/1912.01588 | `cobbe2020_procgen.pdf` | Diversity of training environments is essential | A, P |

### 9. Test oracles, debugging and seeded-fault validity

| Key | Title | Authors | Venue, year | URL | Local PDF | Why it matters here | Verified |
|-----|-------|---------|-------------|-----|-----------|---------------------|----------|
| `barr2015oracle` | The Oracle Problem in Software Testing: A Survey | Barr, Harman, McMinn, Shahbaz, Yoo | IEEE TSE 41(5), 2015 | https://doi.org/10.1109/TSE.2014.2372785 | `barr2015_oracle_problem.pdf` (UCL Discovery copy; Crossref lists a CC BY licence) | Taxonomy that places seeded-fault, console-error and LLM-judge oracles | C, F |
| `zeller2002simplifying` | Simplifying and Isolating Failure-Inducing Input | Zeller, Hildebrandt | IEEE TSE 28(2), 2002 | https://www.st.cs.uni-saarland.de/papers/tse2002/ | `zeller2002_delta_debugging.pdf` (authors' page) | Delta debugging, used for trace minimisation | W, C |
| `just2014mutants` | Are Mutants a Valid Substitute for Real Faults in Software Testing? | Just, Jalali, Inozemtseva, Ernst, Holmes, Fraser | FSE 2014 | https://doi.org/10.1145/2635868.2635929 | `just2014_mutants.pdf` (author's page) | Evidence for and limits of seeded faults as a proxy | C, F |

### 10. Evaluation statistics

| Key | Title | Authors | Venue, year | URL | Local PDF | Why it matters here | Verified |
|-----|-------|---------|-------------|-----|-----------|---------------------|----------|
| `arcuri2011practical` | A Practical Guide for Using Statistical Tests to Assess Randomized Algorithms in Software Engineering | Arcuri, Briand | ICSE 2011 | https://doi.org/10.1145/1985793.1985795 | not open access | Standard guidance for evaluating randomised testing tools | C, W (University of Limerick research record); abstract wording from the search-result excerpt, not from the publisher page |
| `agarwal2021precipice` | Deep Reinforcement Learning at the Edge of the Statistical Precipice | Agarwal, Schwarzer, Castro, Courville, Bellemare | NeurIPS 2021 | https://arxiv.org/abs/2108.13264 | not stored (size): https://arxiv.org/pdf/2108.13264 | Few-run RL evaluation; bootstrap interval estimates | A, P, F (read before removal) |
| `henderson2018matters` | Deep Reinforcement Learning That Matters | Henderson, Islam, Bachman, Pineau, Precup, Meger | AAAI 2018 | https://arxiv.org/abs/1709.06560 | not stored (size): https://arxiv.org/pdf/1709.06560 | Seed and implementation variance in deep RL | A, C, F (read before removal) |
| `klees2018evaluating` | Evaluating Fuzz Testing | Klees, Ruef, Cooper, Wei, Hicks | CCS 2018 | https://arxiv.org/abs/1808.09700 | `klees2018_evaluating_fuzz.pdf` | Evaluation guidelines for randomised bug-finding tools; ground truth | A, F |
| `hazimeh2020magma` | Magma: A Ground-Truth Fuzzing Benchmark | Hazimeh, Herrera, Payer | POMACS 4(3), 2020 | https://arxiv.org/abs/2009.01120 | `hazimeh2020_magma.pdf` | Injected-bug ground truth and survival analysis of time-to-bug | A, F |
| `schloegel2024sok` | SoK: Prudent Evaluation Practices for Fuzzing | Schloegel et al. (10 authors) | IEEE S&P 2024 | https://arxiv.org/abs/2405.10220 | `schloegel2024_sok_fuzzing.pdf` | How often evaluation guidelines are ignored | A, C, F |
| `herrera2021seed` | Seed Selection for Successful Fuzzing | Herrera, Gunadi, Magrath, Norrish, Payer, Hosking | ISSTA 2021 | https://doi.org/10.1145/3460319.3464795 | `herrera2021_seed_selection.pdf` (author group's page) | Censoring-aware time-to-bug analysis (Kaplan-Meier, RMST, log-rank) | C, F |

### 11. Sentence embeddings

| Key | Title | Authors | Venue, year | URL | Local PDF | Why it matters here | Verified |
|-----|-------|---------|-------------|-----|-----------|---------------------|----------|
| `reimers2019sbert` | Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks | Reimers, Gurevych | EMNLP 2019 | https://arxiv.org/abs/1908.10084 | `reimers2019_sbert.pdf` | Sentence embeddings for action labels | A |
| `wang2020minilm` | MiniLM: Deep Self-Attention Distillation for Task-Agnostic Compression of Pre-Trained Transformers | Wang, Wei, Dong, Bao, Yang, Zhou | NeurIPS 2020 | https://arxiv.org/abs/2002.10957 | `wang2020_minilm.pdf` | Compact encoder family used for the embeddings | A, P |

## Verification notes and known weak spots

- **Venue fields.** Where the arXiv record's comment or journal-reference field names the venue,
  that was accepted; otherwise the venue was confirmed against Crossref, PMLR, `papers.nips.cc` or
  OpenReview. Three papers are cited as arXiv preprints because no peer-reviewed venue could be
  confirmed: `du2023success`, `dulacarnold2015large`, `bhambri2024heuristics` (the last has a
  withdrawn ICLR 2025 submission on OpenReview). `gu2025webrled`, `liu2026webgui` and
  `muller2026vlmpbrs` are recent preprints; `fan2026webcq` is a preprint whose arXiv record states
  acceptance at ASE 2026.
- **QExplore** (`sherin2023qexplore`): bibliographic metadata confirmed by Crossref, but no abstract
  could be retrieved (publisher page refused access; Semantic Scholar returned none). The review
  describes it only through the related-work text of `gu2025webrled`.
- **WebQT** (`chang2023webqt`) and **Q-testing** (`pan2020qtesting`): metadata from Crossref,
  abstracts from the Semantic Scholar API; full texts were not read.
- **MARG** (`fan2024marg`): metadata from Crossref; abstract from the ASE 2024 programme page.
  The review quotes no numbers from it.
- **Arcuri and Briand** (`arcuri2011practical`): metadata from Crossref and a university research
  record. The publisher withholds the abstract from Semantic Scholar, so the review's one-sentence
  description rests on the institutional record and a search-result excerpt. Read the paper itself
  before relying on any specific recommendation from it.
- **WebRLED** (`gu2025webrled`): cited as the arXiv preprint that was read. Crossref also lists a
  2026 ACM TOSEM article on WebRLED with an overlapping author list and a different title
  (doi:10.1145/3843235); it was not read.
- **DroidAgent** (`yoon2024droidagent`): the published ICST title differs from the arXiv title; the
  BibTeX entry uses the published title and records the arXiv title in a note.
- **ACM Digital Library items** (`pan2020qtesting`, `fan2024marg`, `arcuri2011practical`): the ACM
  site refused automated requests, so these were not downloaded. They may be readable in a browser.
- **PDFs read and then removed for size** are still the basis for statements in the review:
  `agarwal2021precipice`, `henderson2018matters`, `deng2023mind2web`, `muller2026vlmpbrs`.

## Named starting points that were not included

- The original Go-Explore preprint ("Go-Explore: a New Approach for Hard-Exploration Problems",
  arXiv:1901.10995) was verified on arXiv but is represented by the later Nature paper
  `ecoffet2021first`, which the arXiv record of the preprint points readers to.
- No separate paper was found for "fine-tuning versus zero-shot transfer" in RL; the topic is
  covered through `kirk2023survey`.
- Additional works verified on arXiv or Crossref but left out to keep the set small: Choudhary et
  al., "Automated Test Input Generation for Android: Are We There Yet?" (arXiv:1503.07217); Huang
  and Ontañón, "A Closer Look at Invalid Action Masking in Policy Gradient Algorithms"
  (arXiv:2006.14171); Yu et al., "Effective, Platform-Independent GUI Testing via Image Embedding
  and Reinforcement Learning" (arXiv:2208.09116); Shahbandeh et al., "NaviQAte"
  (arXiv:2409.10741); Wang et al., "Software Testing with Large Language Models: Survey, Landscape,
  and Vision" (arXiv:2307.07221).
