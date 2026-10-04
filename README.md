# Semantic-Aware Web Testing Agent

An **RL-based automated functional tester for web applications**, with a **dynamic LLM
reward** and a **deterministic, verifiable fault oracle**.

A browser environment (Playwright) exposes a live application. A transferable deep RL
agent explores it. A local language model judges, online, how far each page the agent
actually reaches has progressed through a task, and that judgment shapes the reward.
Candidate faults are confirmed only by deterministic means: replay, per-fault controls and
delta-debugging minimization. A product layer turns a URL, a static site, a Dockerfile
repository or a benchmark application into an exploration session with evidence and a
readable report.

Final-year B.Tech project. Security testing is out of scope; this is functional testing.

> **Status (2026-10-04).**
>
> - **Experiment:** the frozen leave-one-application-out transfer experiment is running and has no results yet. Half runs on four AWS CPU spot instances, two of them finished. The LLM half runs on the local GPU and resumes from checkpoints.
> - **Diagnostic:** a secondary contextual-generalization diagnostic is pre-registered.
> - **Application suite:** an isolated eight-application end-to-end suite is being built on the `e2e-suite` branch.
>
> `PROJECT_CONTEXT.md` §0 is the authoritative state of the project.

---

## How it works

```
 application (URL / static site / Dockerfile repo / benchmark app)
        │
 Playwright environment ── action registry: clicks, typed value categories, selects,
        │                  toggles, navigation; ≤100 actions per page, no ids or slots
        ▼
 features v3.1 ── 95-d per action (type, MiniLM-PCA label, role, flags, value category,
        │          history), 25-d state (episode history, page composition, form fill,
        │          LLM history)
        ▼
 Q(s,a): action-conditioned dueling Double DQN — one scorer shared by every action,
        │ a zero-initialised residual head for adaptation, prioritized 3-step replay,
        │ ε-exploration guided by a fixed search priority
        ▼
 reward = environment terms (count-decayed novelty, capped gate reveals, costs, triggers)
        + dynamic LLM terms (episodic progress record + outcome bonus; qwen2.5:7b-instruct,
          temperature 0, rendered-page snapshot, content-addressed cache, live on misses)
        ▼
 seeded-fault oracle — declarative assertions on recorded pages; a candidate is confirmed
 only if it reproduces twice, does NOT occur on the build without that fault's own patch,
 and survives ddmin minimization → evidence, replay trace, report
```

`docs/llm_reward.md` specifies exactly what the model sees, what it judges, how that
becomes reward, caching, failure behaviour and validation.

## What is built

| Part | Code | Notes |
|---|---|---|
| Browser environment, action registry, deterministic triggers | `src/web_testing_agent/envs/` | headless Chromium; fresh context per episode |
| Learner (DQN and contextual-bandit variants) | `src/web_testing_agent/rl/` | permutation-equivariant; transfer via residual head |
| Dynamic LLM progress reward | `src/web_testing_agent/reward/progress.py`, `rl/reward.py` | prompt `progress-v3`; never sees faults |
| Seeded-fault oracle | `src/web_testing_agent/oracle/` | 8 assertion kinds, per-fault controls, replay, ddmin |
| Six-application benchmark + admission validator | `tests/fixtures/benchmark/`, `src/web_testing_agent/benchmark/` | 12 faults, 5 classes; `reports/benchmark_suite.md` |
| Transfer experiment (resumable, parallel, top-up rule) | `src/web_testing_agent/experiments/transfer.py` | `scripts/run_transfer.py`, `scripts/analyze_transfer.py` |
| Product tester | `src/web_testing_agent/tester/` | URL / site / repo / benchmark → `report.md` + `report.html` + evidence |
| Web UI | `src/web_testing_agent/app/` | sessions, uploads, live progress, reports |
| Repository intake (sandboxed build and run) | `src/web_testing_agent/intake/` | Dockerfile repos; Compose refused by policy |
| Cloud launcher | `cloud/aws/` | restricted IAM, spot only, hard cap, self-termination, S3 sync |

## Quick start

Requirements: Python 3.11, the packages in `requirements.txt`, Playwright Chromium
(`python -m playwright install chromium`), and for the LLM reward,
[Ollama](https://ollama.com) with `qwen2.5:7b-instruct`. Docker is needed only for
repository uploads and container builds.

```bash
pip install -r requirements.txt && pip install -e .
python -m playwright install chromium
ollama pull qwen2.5:7b-instruct          # optional: without it, use --no-llm
```

### Test an application

```bash
python scripts/test_app.py --url https://example.org/            # a live site
python scripts/test_app.py --site "demo app/www"                 # a static site (folder or .zip)
python scripts/test_app.py --repo path/to/repo                   # a repository with a Dockerfile
python scripts/test_app.py --benchmark booking_desk              # a benchmark application
```

Options: `--budget` (interactions, default 600), `--agent rl|search|random`, `--no-llm`,
and `--login-steps FILE` (scripted steps run at the start of every episode, for
applications behind a login). The run writes `session.json`, `report.md`, `report.html`,
`reward_log.jsonl` and `evidence/` under `reports/sessions/`.

### Web UI

```bash
python -m web_testing_agent.app          # http://127.0.0.1:8000
```

New Test → a demo, a live URL, or an upload (a static site, or a Dockerfile repository
when Docker is running). It binds to localhost by default, because it builds and runs
user-supplied code.

### Benchmark and experiment

```bash
python scripts/validate_benchmark.py --help        # admission checks per application
python scripts/benchmark_report.py --validation <dir>
python scripts/run_transfer.py --out-dir runs/dev --folds shop_catalog --arms search_S dqn_scratch --seeds 0 --budget 1000
python scripts/analyze_transfer.py runs/<run> --out reports/<name>
```

`run_transfer.py` is resumable: rerunning the same command skips every finished job. It
refuses to start on applications that `reports/benchmark_validation.json` does not admit.

### Docker image

```bash
docker build -t web-testing-agent .
docker run --rm -p 8000:8000 -e OLLAMA_HOST=http://host.docker.internal:11434 web-testing-agent
```

CPU-only torch is pinned in the image; dependency versions match `docker/requirements-runtime.txt`.

## The experiment (frozen)

**Question:** does pretraining on five applications make verified-fault discovery on a
held-out sixth faster than learning from scratch, against information-matched non-learning
baselines, and with or without the dynamic LLM reward?

- **Pre-registration:**
  - `docs/methodology/2026-09-18-transfer-preregistration.md`
  - amendments `2026-10-01-transfer-amendment.md` and `2026-10-03-transfer-amendment-2.md`
  - Every change was made before the run and states its reason. §7 of amendment 2 is the frozen run specification.
- **Arms:** random, search_A0, search_S, bandit_scratch, bandit_pretrained, dqn_scratch, dqn_scratch_nollm, dqn_pretrained, dqn_zeroshot, dqn_pretrained_nollm.
- **Budget, seeds and statistics:**
  - 2,000 interactions per hunt; seeds 0–2, plus a mechanical top-up to seeds 3–5 when a fold's difference is under 200 interactions.
  - Primary statistic: per-fold paired difference in time to first verified fault, median across folds, with a fold bootstrap.
  - Secondary: stratified log-rank.
- **Folds reported:** every comparison is reported over all six folds **and** over the four pristine folds. `shop_catalog` and `booking_desk` were used for development.
- **Secondary diagnostic:** `docs/methodology/2026-10-04-contextual-diagnostic-preregistration.md` tests whether the policy uses state context or memorizes actions, words or applications. It runs only after the primary experiment and is reported separately.

Results will be reported exactly as the frozen analysis produces them, including negative
or inconclusive results.

## Claim limits

These are stated up front:

- **Context:** the agent's state has no page semantics (no heading, text or URL). It can learn form and interaction-state context, not page meaning. Claims of contextual learning are limited to what the diagnostic shows.
- **Generalization:** six applications from one design contract support at most within-family adaptation. There is no zero-shot or real-world generalization claim.
- **LLM reward:** its contribution comes only from the frozen LLM-vs-no-LLM ablations.
- **Product:** repository intake requires a Dockerfile. Without a specification, findings on arbitrary applications rest on generic triggers and an LLM review marked unverified. The seeded-fault oracle verifies only declared faults.
- **Disclosures:** representation leakage paths and pre-run exposure of the held-out applications are recorded in `PROJECT_CONTEXT.md` §0.4.

## End-to-end application suite (branch `e2e-suite`)

Eight small, genuinely different web applications with real backends: tidewater
(banking SPA), bagstreet (server-rendered shop), helpline (fragment-updated support desk),
clinicflow (branching booking wizard), orbit (modal-heavy kanban), ledgerly (multi-role
expenses), stackroom (inventory data grid) and wayfarer (draft/seal trip planner, with no
Dockerfile by design).

They have zero dependencies, run offline and are deterministic. Each ships seeded faults
that must pass mechanical admission: witness, per-fault control, replay, minimization,
benign traces, and a clean random walk. The suite is a testbed for the product and is kept
separate from the frozen experiment. See `e2e_suite/CONVENTIONS.md` on that branch.

## Repository map

```
src/web_testing_agent/   envs, rl, reward, oracle, benchmark, experiments, tester, app, intake
scripts/                 test_app, run_transfer, analyze_transfer, validate_benchmark, warm_caches, ...
tests/                   unit tests; fixtures (benchmark apps, demo sites)
docs/                    methodology (pre-registrations), llm_reward, literature_review, benchmark contract
reports/                 benchmark validation and suite, development evidence, session reports
cloud/aws/               launcher, IAM policies, setup guide
PROJECT_CONTEXT.md       authoritative project state and full decision history
```

## Tests

```bash
python -m pytest tests -q
```

About 1,240 tests. Browser-, Docker- and torch-dependent modules are skipped automatically
when those are unavailable.

## Stack

Python 3.11, Playwright, PyTorch, sentence-transformers (MiniLM), Ollama (qwen2.5:7b-instruct),
FastAPI, SQLite, Docker, AWS (EC2 spot, S3).

## License

No license file yet: all rights reserved by the author.
