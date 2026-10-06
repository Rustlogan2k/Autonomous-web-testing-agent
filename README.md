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

> **Status (2026-10-06).** Both frozen evaluations are complete.
>
> - **Transfer experiment** (291 jobs): learned agents find planted faults in more runs than non-learning baselines, but pretraining on other applications does not speed up fault discovery, and the LLM reward shows no detectable benefit. The oracle produced no false positives.
> - **Contextual diagnostic** (228 jobs): the agent's preference for an action does not depend on whether that action is useful in the current state (contextual learning not supported); the memorization hypotheses are inconclusive.
> - **Application suite**: eight apps admitted with 23/23 faults on the `e2e-suite` branch; Docker checks pending.
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
| Contextual diagnostic (generated app family, probes, masked arms) | `src/web_testing_agent/ctxbench/`, `tests/fixtures/ctxbench/` | `python -m web_testing_agent.ctxbench.runner`, `.probe_eval`, `.analyze` |
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

## Results

### Transfer experiment (pre-registered, complete)

**Question:** does pretraining on five applications make verified-fault discovery on a
held-out sixth faster than learning from scratch, against information-matched non-learning
baselines, and with or without the dynamic LLM reward?

- **Method:** `docs/methodology/2026-09-18-transfer-preregistration.md` and amendments
  `2026-10-01` and `2026-10-03` (all before the run). Ten arms; 2,000 interactions per hunt;
  seeds 0–2 with a mechanical top-up; per-fold paired difference in time to first verified
  fault (median across folds, fold bootstrap) and stratified log-rank; all six folds and the
  four pristine folds reported.
- **Run:** 291 jobs, 0 failures; LLM arms on a local RTX 4050, non-LLM arms on four AWS spot
  instances (≈ $1.6 of the $20 cap).

| Comparison | All folds | Pristine folds |
|---|---|---|
| Pretrained vs scratch DQN (primary) | median Δ +0 [−749, +290], p = 0.53 | −310, p = 0.66 |
| Pretrained vs scratch bandit | −280 [−732, +188], p = 0.086 | −280, p = 0.11 |
| With vs without LLM reward | +17 (p = 0.75); −57 (p = 0.62) | — |
| Pretrained DQN vs search_A0 / random | p = 0.006 / 0.02 | p = 0.23 / 0.53 |

Faults found per run: pretrained bandit 42%, both DQN arms 33%, feature search 17%, random
11%, hand-priority search 6%. No verified finding on any fault-free build. Full output:
`reports/transfer_final.md` and `reports/transfer_final_addendum.md`.

### Contextual diagnostic (pre-registered, complete, reported separately)

Does the policy rank an action by the state it is in, or has it memorized actions, words or
layouts? Twelve generated variants of one workflow vary vocabulary (including pseudo-words),
workflow structure and decoys; four agent arms differ only in which inputs the network sees.

| Hypothesis | Verdict |
|---|---|
| Genuine state–action contextual learning | **not supported** (within-label AUROC 0.51, needed ≥ 0.70) |
| Action / lexical / application memorization | inconclusive (three seeds; wide intervals) |

The learned arms that see the state find the faults on held-out variants in 19–20 of 24
runs, against 2 of 24 for search and random; the arm that never sees labels does as well as
the full one. Full output: `reports/contextual_diagnostic.md`.

## Claim limits

These are stated up front:

- **Context:** the agent's state has no page semantics (no heading, text or URL). The diagnostic found no state–action contextual learning; claims are limited to in-application, state-driven learning.
- **Generalization:** six applications from one design contract support at most within-family results, and pretraining showed no transfer benefit. There is no zero-shot or real-world generalization claim.
- **LLM reward:** its contribution comes only from the frozen LLM-vs-no-LLM ablations, which show no detectable benefit.
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
benign traces, and a clean random walk. All eight are admitted (23/23 faults); the Docker
checks are pending. The suite is a testbed for the product and is kept separate from the
frozen experiment. See `e2e_suite/CONVENTIONS.md` and `e2e_suite/admission/suite.md` on
that branch.

## Repository map

```
src/web_testing_agent/   envs, rl, reward, oracle, benchmark, experiments, ctxbench, tester, app, intake
scripts/                 test_app, run_transfer, analyze_transfer, validate_benchmark, warm_caches, ...
tests/                   unit tests; fixtures (benchmark apps, ctxbench variants and probes, demo sites)
docs/                    methodology (pre-registrations), llm_reward, literature_review, benchmark contract
reports/                 experiment and diagnostic results, benchmark validation, development evidence
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
