# RL-based Automated Web Testing Agent

An automated **functional** tester for web applications that uses reinforcement learning.
It drives a real browser (Playwright) through a live application and explores it, looking
for faults. A fault counts only when it is **verified** by deterministic means: it must
replay twice in fresh sessions, be absent from that fault's control build, and survive
delta-debugging minimisation. A product layer turns a URL, a static site, a Dockerfile
repository or a bundled test application into a test run with evidence and a report, from
the command line or a web UI.

Final-year B.Tech project. Security testing is out of scope.

> **Status (2026-10-22).** v1 (deep RL, two pre-registered experiments) and the v2 hybrid
> tester's held-out evaluation are complete and frozen. The 8 development applications have
> been rebuilt as full-stack apps, with PostgreSQL or MongoDB, sign-in and crash-type faults.
> **Tuning round v3** on them is running. Next: freeze the tuned tester, then compare it with
> the published WebExplor / WebRLED results on their subject applications.
> `PROJECT_CONTEXT.md` §0 is the authoritative project state.

---

## How it works

```
 application (URL / static site / Dockerfile repo / suite app with clean, faulty and control builds)
        │
 Playwright environment ── fresh browser context per episode; sign-in macro for apps behind login
        │                   action registry: clicks, typed value categories, selects, toggles,
        │                   ARIA/onclick "custom clickables" (optional)
        ▼
 page abstraction ("cell") ── page kind, path template, heading, form fill, alert, submit offered
        ▼
 archive of cells (Go-Explore) ── choose by novelty / untried options / depth, return by exact replay
        ▼
 test operator from standard test design ── complete the step, each dropdown option, invalid
        │                                    input, edit after review, back/reload and resume,
        │                                    double submit, collection churn, browse a list
        ▼
 decision layer (the learning part) ── Thompson-sampling bandit over operators, or count-based
        │                               curiosity Q-learning over page transitions, or none
        ▼
 oracle ── seeded faults: replay ×2, per-fault control build, ddmin → evidence + report
           crash faults: console / page / failed-request errors attributed the same way
           specification-free invariants (reload stability etc.): reported separately
```

The v1 learner it grew out of is still runnable as an arm. It is an action-conditioned
dueling Double DQN, Q(s, φ(a)), with prioritised 3-step replay, a residual adaptation head,
search-guided ε-exploration and an optional LLM progress reward (`qwen2.5:7b-instruct`).

## Agents (arms)

| Agent | Learning | Where it stands |
|---|---|---|
| `v1_dqn_pretrained` | AC-DQN pretrained on the 6 benchmark apps, adapting online | held-out: 34/40 runs, lowest mean time to first fault |
| `hybrid_bandit` | hybrid tester + Thompson-sampling operator bandit | held-out: 34/40 runs, most distinct faults (19/24) |
| `hybrid_curiosity` (`dev_v3_curiosity`) | hybrid tester + count-based curiosity Q-learning | tuning round v3 |
| `hybrid_nolearn` | hybrid tester, untried operators in turn, no learner | held-out primary arm: 30/40 runs, 18/24 faults |
| `random` | uniform over valid actions | the baseline: 32/40 runs, 13/24 faults |

The web UI offers the RL agents and random. Development-only arms (`dev_*`) are defined in
`src/web_testing_agent/hybrid/run.py`.

## Results so far

| # | Question | Result | Status |
|---|---|---|---|
| 1 | Does pretraining an RL tester on other apps help on a new app? (v1, 6 apps, 210 runs) | No: pretrained − scratch median Δ 0 [−749, +290], p = 0.53 | pre-registered, frozen |
| 2 | Does the v1 network learn which action helps in which state? (228 jobs) | Not supported: within-label AUROC 0.51 (rule ≥ 0.70) | pre-registered, frozen |
| 3 | Does the LLM progress reward point at faults? | Only on the apps it was tuned on (AUROC 1.00 vs 0.38–0.73) | post-hoc |
| 4 | Can a local LLM label pages and controls reliably? | No: gates failed (0.68/0.78; 0.61/0.75 on fresh pages) | gated in advance |
| 5 | Does a learner help on top of the hybrid tester? (development, 5 rounds, 7 variants) | Not distinguishable from no learner | development |
| 6 | Does curiosity-driven RL (WebExplor/WebRLED components) find functional faults? | At random level (17/42 vs 18/42) | development |
| 7 | **Held-out** (8 unseen apps, 160 runs + 32 clean controls): hybrid vs random, vs v1 AC-DQN, bandit vs none | **All four comparisons inconclusive.** Hybrid arms verified the most distinct faults (19, 18 of 24 vs random 13), including 9 no random run found; 0 false positives | pre-registered (tag `v2-eval-freeze`), frozen |

Held-out per arm:

| Arm | Runs that found a fault | Restricted mean time | Distinct faults (of 24) | Clean-build false positives |
|---|---|---|---|---|
| `v1_dqn_pretrained` | 34/40 | **595** | 17 | 0/8 |
| `hybrid_nolearn` | 30/40 | 737 | 18 | 0/8 |
| `hybrid_bandit` | 34/40 | 769 | **19** | 0/8 |
| `random` | 32/40 | 978 | 13 | 0/8 |

The held-out evaluation exposed one blind spot, list paging (skylark's "Load more"). It led
to the `browse_list` operator now being tuned.

The result files are `reports/transfer_final.json`, `reports/contextual_diagnostic.json`,
`reports/v2_evaluation.json` and `runs/*/results/`. The web UI's **Evaluation** page shows
every arm and round live from these files.

## Test applications

| Set | Apps | Role |
|---|---|---|
| Benchmark (`tests/fixtures/benchmark/`) | 6 small apps, 12 seeded faults | v1 experiments; development for v2 |
| Development suite (`e2e_suite/apps/`) | tidewater, clinicflow, stackroom, ledgerly (PostgreSQL); bagstreet, helpline, orbit, wayfarer (MongoDB) | tuning. Full stack with sign-in (clinicflow, helpline, wayfarer, ledgerly and bagstreet enlarged to ≥ 12 views and ≥ 3 CRUD entities); 41 seeded faults, 8 of them crash-type (orbit, helpline, stackroom, wayfarer) |
| Validation suite (`validation_suite/apps/`) | dutyday, fosterly, larder, markbook, quizcraft, sideline, skylark, tally | held-out evaluation (frozen as evaluated); 24 faults |
| External (planned) | the WebExplor / WebRLED subject apps | final comparison with published numbers. Nobody looks at them before the tuned tester is frozen |

Every suite app has a clean build, a build with all faults, and one control build per fault.
Each must pass mechanical admission (witness, control, replay, minimisation, benign traces,
a clean random walk, database contract, Docker build) before any agent runs on it. The
database drivers are vendored (`e2e_suite/vendor/`), so builds need no network.

## Quick start

Requirements: Python 3.11 and Playwright Chromium. Node.js 22 is needed for the Node suite
apps, and PostgreSQL and MongoDB (or Docker) for the development suite. Ollama with
`qwen2.5:7b-instruct` is optional, only for the LLM components. Docker is optional, for
repository uploads and container checks.

```bash
python -m venv venv
./venv/Scripts/python.exe -m pip install -r requirements.txt
./venv/Scripts/python.exe -m pip install -e .
./venv/Scripts/python.exe -m playwright install chromium
ollama pull qwen2.5:7b-instruct            # optional
./venv/Scripts/python.exe -m pytest tests -q   # ~1,300 tests; browser/Docker/torch tests skip when unavailable
```

### Web UI

```bash
./venv/Scripts/python.exe -m web_testing_agent.app      # http://127.0.0.1:8000
```

New Test → a demo app, a live URL, or an upload (a static site, or a Dockerfile repository
while Docker is running). Pick an agent, watch the live event stream, then open or download
the report (HTML, Markdown, JSON). Evaluation shows the research results. It binds to
localhost because it builds and runs uploaded code.

### Command-line tester

```bash
./venv/Scripts/python.exe scripts/test_app.py --url https://example.org/
./venv/Scripts/python.exe scripts/test_app.py --site "demo app/www"
./venv/Scripts/python.exe scripts/test_app.py --repo path/to/repo
./venv/Scripts/python.exe scripts/test_app.py --benchmark booking_desk
```

Options: `--budget N`, `--agent`, `--no-llm`, `--login-steps FILE`, `--seed N`, `--out DIR`.

### Hybrid tester runs (development and evaluation)

```bash
# Hunts: any benchmark or suite apps by name, several arms and seeds (resumable)
./venv/Scripts/python.exe -m web_testing_agent.hybrid.run --out-dir runs/v3-tune \
    --apps orbit helpline stackroom wayfarer tidewater clinicflow bagstreet ledgerly \
    --arms random hybrid_bandit v1_dqn_pretrained dev_v3_curiosity --seeds 0 1 2 --workers 3
#   add --clean for clean-build control runs

# Survival analysis per arm and app (the held-out protocol is frozen as freeze-2026-10-08)
./venv/Scripts/python.exe -m web_testing_agent.hybrid.analyze --runs runs/v2-eval \
    --out reports/v2_evaluation --protocol freeze-2026-10-08
```

Each result records verified faults, unique failures (error signatures) and V8 JavaScript
coverage.

### Suite admission (no agent)

```bash
./venv/Scripts/python.exe -m e2e_suite.harness.validate_suite --browser            # add --docker
WTA_SUITE_ROOT=validation_suite ./venv/Scripts/python.exe -m e2e_suite.harness.validate_suite --apps dutyday --browser
```

### v1 experiments

```bash
./venv/Scripts/python.exe scripts/run_transfer.py --out-dir runs/dev --folds shop_catalog --arms search_S dqn_scratch --seeds 0 --budget 1000
./venv/Scripts/python.exe scripts/analyze_transfer.py runs/<run> --out reports/<name>
./venv/Scripts/python.exe -m web_testing_agent.ctxbench.runner --out-dir runs/ctx-main --workers 4
```

The v1 AC-DQN checkpoints are loaded from `runs/final-cpu-*/checkpoints`. They are not in
git; copy them from the machine that ran the experiment.

### Docker image

```bash
docker build -t web-testing-agent .
docker run --rm -p 8000:8000 -e OLLAMA_HOST=http://host.docker.internal:11434 web-testing-agent
```

### Practical notes

- **Long runs on a Windows laptop:** Modern Standby suspends processes when the lid closes
  or the screen sleeps. Stay plugged in, turn the screen timeout off and set lid-close to "do
  nothing". Every runner is resumable: rerunning the command skips finished jobs.
- Never edit `src/` while a round is starting jobs, because each job imports the code when
  it starts. Use a git worktree instead.
- AWS (`cloud/aws/`): restricted IAM user only, spot instances, hard $20 cap,
  self-terminating workers.

## Claim limits

- **No transfer claim for v1**, and no state–action contextual learning (Experiments 1–2).
- **Learner:** on functional faults, no learning component has been shown to beat the
  hybrid tester's structure (abstraction, archive with exact returns, test operators).
- **Held-out:** every pre-registered comparison is inconclusive. The descriptive advantages
  above are not significance claims.
- **Generalisation:** the suite apps and seeded faults were built by the same developer as
  the tester (disclosed). The external WebExplor/WebRLED comparison is what addresses this.
- **LLM:** the progress reward and the page labeller did not generalise beyond their tuning
  data, so the LLM is not on the decision path.
- Tuning-round results (v3) are development data, never evidence.

## Repository map

```
src/web_testing_agent/   envs, rl (v1 AC-DQN), reward, oracle, benchmark, experiments, ctxbench,
                         hybrid (v2 tester, runner, analysis, metrics), tester, app (web UI), intake
e2e_suite/               development suite: apps, harness (builds, admission, DB control), vendored drivers
validation_suite/        held-out validation apps and their admission reports
tests/                   unit tests and fixtures (benchmark apps, ctxbench variants, demo sites)
scripts/                 command-line entry points and v1 analyses
reports/                 result files (JSON) of every experiment and evaluation
docs/references/         bibliography (references.bib)
cloud/aws/               launcher and IAM policies
PROJECT_CONTEXT.md       authoritative project state and full decision history
```

Earlier documents were removed on 2026-10-22: the pre-registrations, mentor reports,
literature review, suite plans and Markdown reports. They are preserved at git tag
`docs-archive-2026-10-22`, for example
`git show docs-archive-2026-10-22:docs/methodology/hybrid-v2-evaluation-preregistration.md`.

## Stack

Python 3.11, Playwright, PyTorch, sentence-transformers (MiniLM), Ollama
(qwen2.5:7b-instruct), FastAPI, PostgreSQL, MongoDB, Node.js, Docker, AWS (EC2 spot, S3).

## License

No license file yet: all rights reserved by the author.
