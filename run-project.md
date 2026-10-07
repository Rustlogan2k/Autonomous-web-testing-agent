# Running the RL Web Testing Agent

How to set up and run every part of the project: the tester on a website, the web UI, the
benchmark and experiments, and the new hybrid tester. Commands are for Windows with Git Bash
(PowerShell equivalents work the same way); paths are relative to the repository root.

---

## 1. Requirements

| Need | Version / note |
|---|---|
| Python | 3.11 (3.11–3.12 supported) |
| Playwright Chromium | installed by the setup below |
| Node.js | 22 (only for the Node-based test applications) |
| Ollama + `qwen2.5:7b-instruct` | optional — only for the LLM components (progress reward, page labeller) |
| Docker Desktop | optional — only for testing uploaded repositories and building the image |
| GPU | optional; the LLM runs faster on one (developed on an RTX 4050, 6 GB) |

## 2. Setup (once)

```bash
python -m venv venv
./venv/Scripts/python.exe -m pip install -r requirements.txt
./venv/Scripts/python.exe -m pip install -e .
./venv/Scripts/python.exe -m playwright install chromium

# Optional, for the LLM components:
ollama pull qwen2.5:7b-instruct
```

Check the installation by running the tests (about 1,250; a few are skipped when Docker or a
GPU is absent):

```bash
./venv/Scripts/python.exe -m pytest tests/unit -q
```

## 3. Test a website with the agent (command line)

```bash
# A live site
./venv/Scripts/python.exe scripts/test_app.py --url https://example.org/

# A static site (folder or .zip)
./venv/Scripts/python.exe scripts/test_app.py --site "demo app/www"

# A repository with a Dockerfile (needs Docker Desktop running)
./venv/Scripts/python.exe scripts/test_app.py --repo path/to/repo

# One of the six benchmark applications
./venv/Scripts/python.exe scripts/test_app.py --benchmark booking_desk
```

Useful options:

| Option | Meaning |
|---|---|
| `--budget N` | browser interactions to spend (default 600) |
| `--agent rl\|search\|random` | the learning agent, the fixed search heuristic, or random clicking |
| `--no-llm` | run without the language model |
| `--login-steps FILE` | a JSON list of steps run at the start of every episode (for sites behind a login) |
| `--seed N`, `--out DIR` | reproducibility and output location |

Each run writes `session.json`, `report.md`, `report.html`, a reward log and an `evidence/`
folder under `reports/sessions/`.

> The command-line tester and the web UI currently use the v1 learning agent. The new hybrid
> tester (section 7) runs through its own runner; wiring it into the product is planned work.

## 4. Web interface

```bash
./venv/Scripts/python.exe -m web_testing_agent.app      # then open http://127.0.0.1:8000
```

New Test → a demo, a live URL, or an upload (a static site, or a Dockerfile repository when
Docker is running). It listens on localhost only, because it builds and runs uploaded code.

## 5. Benchmark applications and the v1 experiments

```bash
# Admission checks for the six benchmark applications
./venv/Scripts/python.exe scripts/validate_benchmark.py --help

# The leave-one-application-out transfer experiment (resumable; reruns skip finished jobs)
./venv/Scripts/python.exe scripts/run_transfer.py --out-dir runs/dev --folds shop_catalog \
    --arms search_S dqn_scratch --seeds 0 --budget 1000
./venv/Scripts/python.exe scripts/analyze_transfer.py runs/<run> --out reports/<name>

# The contextual diagnostic (generated applications, masked agents, probe evaluation)
./venv/Scripts/python.exe -m web_testing_agent.ctxbench.runner --out-dir runs/ctx-main --workers 4
./venv/Scripts/python.exe -m web_testing_agent.ctxbench.probe_eval --out-dir runs/ctx-main
./venv/Scripts/python.exe -m web_testing_agent.ctxbench.analyze --out-dir runs/ctx-main
```

The finished results of both are in `reports/transfer_final.md` and
`reports/contextual_diagnostic.md`.

## 6. The application suites (no agent involved)

```bash
# Admit / re-check the 8 suite applications (browser checks; add --docker with Docker running)
./venv/Scripts/python.exe -m e2e_suite.harness.validate_suite --browser

# The same harness for the new validation set
WTA_SUITE_ROOT=validation_suite ./venv/Scripts/python.exe -m e2e_suite.harness.validate_suite --apps dutyday --browser
```

Each suite application is a small real web app (Python or Node, zero dependencies) with a
clean build and a build carrying seeded faults. Admission reports go to
`e2e_suite/admission/` and `validation_suite/admission/`.

## 7. The hybrid tester (v2)

```bash
# Hunts on development applications (benchmark or suite apps by name), several arms and seeds
./venv/Scripts/python.exe -m web_testing_agent.hybrid.run --out-dir runs/v2-dev \
    --apps shop_catalog tidewater --arms hybrid_bandit hybrid_nolearn random \
    --seeds 0 1 2 --workers 4            # add --clean for bug-free control runs

# Analysis (survival statistics per arm and application)
./venv/Scripts/python.exe -m web_testing_agent.hybrid.analyze --runs runs/v2-dev --out reports/v2_dev

# Infrastructure check of the suite path (replays the admission's own traces; no agent)
./venv/Scripts/python.exe -m web_testing_agent.hybrid.suite_smoke

# Build the cross-application prior from development runs
./venv/Scripts/python.exe -m web_testing_agent.hybrid.prior --runs runs/v2-dev --out data/v2_prior.json
```

Arms: `random`, `search_S` (v1 heuristic), `v1_dqn` (v1 learner), `hybrid_nolearn`,
`hybrid_bandit` (main), `hybrid_bandit_prior` (with the cross-app prior), `hybrid_bandit_llm`
(exploratory, LLM labels). Results land in `<out-dir>/results/*.json`.

## 8. Docker image

```bash
docker build -t web-testing-agent .
docker run --rm -p 8000:8000 -e OLLAMA_HOST=http://host.docker.internal:11434 web-testing-agent
```

## 9. Practical notes

- **Long runs on a laptop**: Windows Modern Standby suspends processes when the screen turns
  off, the lid closes or the battery budget runs out. For unattended runs: plugged in, screen
  timeout off, lid-close set to "do nothing". Every runner is resumable — rerunning the same
  command skips finished jobs.
- **Ollama** must be running (`127.0.0.1:11434`) before any LLM arm starts; LLM runs fail fast
  if it is unreachable rather than silently running without the model.
- **Where things are**: `PROJECT_CONTEXT.md` (authoritative project state and history),
  `docs/methodology/` (every pre-registration and amendment), `reports/` (all results).
