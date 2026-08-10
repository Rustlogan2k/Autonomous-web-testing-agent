# Semantic-Aware Web Testing Agent

**Autonomous functional bug detection using an LLM judge and reinforcement learning.**

An agent that explores a live web application in a real browser and, at every step, asks a language model a single question: *given what the application was just asked to do, did it behave correctly?* Findings are reported with the evidence that justifies them.

The bet is simple. Existing dynamic tools are pattern matchers — OWASP ZAP looks for injection signatures, Cypress checks assertions a human wrote in advance. Neither *understands what a page is trying to do*, so neither can catch a bug like "this form claims to update your email address, and the confirmation page shows the old one." That bug is invisible to a signature and invisible to any assertion nobody thought to write. It is obvious to a reader who understands the intent.

---

## Results

Two measurements, on two targets, against the same baseline: **this project's own deterministic detector** — console errors, unexpected HTTP status codes, stuck pages, broken navigation — run over the identical traces. That is what you can get without a language model, and it is the number the judge has to beat.

**On a purpose-built fixture** with 10 seeded bugs and a machine-readable answer key:

| | deterministic triggers | LLM judge |
|---|---|---|
| semantic bugs (require understanding) | **0 / 7** | **7 / 7** |
| mechanically detectable bugs | 3 / 3 | 3 / 3 |
| **total** | **3 / 10** | **10 / 10** |
| false positives on known-correct behaviour | 0 | **0** |
| findings whose quoted evidence really occurs in the input | n/a | **100%** |

**On a real application** — Gitea 1.21, with six defects injected through Gitea's own supported template-override path (no fork, no rebuild, removable by deleting a directory):

| | deterministic triggers | LLM judge |
|---|---|---|
| semantic | 0 / 4 | **2 / 4** |
| mechanically detectable | 2 / 2 | 2 / 2 |
| **total** | **2 / 6** | **4 / 6** |
| false positives on the control | 0 | **0** |

**The second table is the honest one.** 4/6 against a 2/6 ceiling is a real but modest margin on six bugs — far too few to be confident in. The fixture result should not be quoted without this beside it.

> **Reproducibility caveat.** The hosted judge (`gpt-oss:120b-cloud`) is an unversioned tag and its weights changed mid-project: the same window, same prompt and byte-identical rendering returned a different verdict weeks apart. Every figure here comes from a single dated run with its own baseline re-measured inside it. Never compare a number against one captured in a different session. The local, version-pinned `qwen2.5:7b-instruct` reaches the same 10/10 on the fixture and is retained as a reproducibility anchor.

---

## How it works

```
repository ──► build gate ──► deploy ──► live app
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    │                                               │
              RL agent explores                          LLM judge scores
              (Gymnasium + masked DQN) ──── gated ─────► each transition
                    │                                               │
                    │                                    verdict + evidence
                    │                                               │
                    └──────────────► reward ◄───────────────────────┘
                                       │
                                  bug report
```

Five ideas do the work.

**1. The judge sees a window, not a snapshot.** Most interesting bugs are not visible in a single transition. "The signup form silently dropped your email" can only be seen by comparing what was typed several steps ago against what the confirmation echoes back. The judge is given a rolling window of six consecutive interactions and asked about the last one in context.

**2. The judge sees a behavioural diff, not raw HTML.** Each page is reduced to what a tester would actually look at — title, controls and whether they are filled, hidden elements, links, visible text, console errors, failed requests — and consecutive pages are rendered as a *diff*. A dead control then appears as the conspicuous absence of any change, which is exactly the signal it needs to be.

**3. Verdicts are ordered so analysis precedes judgment.** The output schema forces the model to write down what it observed, what a correct application should have done, and whether those contradict — and only then to answer yes or no. An earlier version put the boolean first and a model duly wrote "NO OBSERVABLE CHANGE — the page is byte-identical", typed it `dead_control`, and answered `is_bug: false` on 38 of 39 windows. Same model, same data, reordered fields: 0/7 → 7/7.

**4. Every finding must quote its evidence, and the quote is checked.** A verdict whose cited line does not occur in what the model was shown is flagged automatically. This matters more than recall: a reward model that pays out for invented findings is worse than no reward model at all. One 7B model scored 7/7 on recall while fabricating 68% of its citations.

**5. Calls are gated, because judgment is the budget.** A judge call costs ~10 seconds, so judging every step is impossible at training scale. The filter asks "was an action taken that *should* have changed something", never "did the state change" — a control that promises an effect and produces none is exactly the bug class the judge is most needed for. It skips 38–45% of steps while losing **0 of 38** positive verdicts across two labelled corpora.

---

## What is built

| Component | Status |
|---|---|
| **Browser environment** — Playwright + Gymnasium, 9 action types, dynamic 100-slot action space | Built |
| **LLM judge** — windowing, prompts, verdict schema, scoring, 4 backends | Built and measured |
| **Live reward loop** — judge wired into the RL reward signal | Built |
| **Judge-call gate** — with its losses measured, not assumed | Built |
| **Session bootstrap** — start episodes authenticated, outside the step budget | Built |
| **Action masking** — masked DQN over the valid-action set | Built and verified |
| **Deterministic triggers** — the baseline the judge must beat | Built |
| **Evaluation harness** — policy-agnostic rollouts, deduplicated findings, random baselines | Built |
| **Trace corpus + window validator** — 13 invariants that refuse to score a self-contradicting input | Built |
| **Application Profile** — schema for source-derived intent, sliced per window | Built |
| **Ground-truth fixtures** — 10-bug toy site, 6-bug seeded Gitea, 4-gate deep-flow site | Built |
| **Build-definition security gate** — rejects privilege escalation in uploaded compose files | Built |
| **Semantic perception encoders** — CLIP / CodeBERT / MiniLM, frozen, batched, cached | Built (~5% step overhead, 951 MB VRAM) |
| Repo profiler (extract intent from source automatically) | Not built |
| Auto-deploy runner | Not built |
| Bug report engine | Not built |
| Agent A (security testing, PPO + curiosity) | Out of scope |

485 tests (469 without a browser).

---

## The parts that did not work, and why

**Reinforcement learning has not yet earned its place.** On the shallow fixture, uniform-random exploration over valid actions beat the DQN at equal step budget. Training also produced four distinct reward exploits in five runs — the agent learned to refresh a 404 page 117 times, to do literally nothing, and to end each episode on step one — each a real defect in the reward design, and each found by the optimizer rather than by review or the test suite.

**Action masking works; the deep flow is still unsolved.** The original comparison was not a fair fight: masked random could sample only legal slots and the flat 100-way Q-head structurally could not. That is now fixed and verified — 100% valid actions against 18% for unmasked random, 0/200 invalid actions in a controlled harness. Measured on a four-gate ordering flow whose defect is reachable only after completing every stage in order:

| policy | valid actions | mean flow depth | reward |
|---|---|---|---|
| dqn_unmasked | 89% | 0.11 | −11.19 |
| **dqn_masked** | **100%** | **0.33** | −24.34 |
| random_unmasked | 18% | 0.00 | −28.81 |
| random_masked | 100% | 0.04 | **+7.31** |

The masked agent reaches three times the mean depth of the unmasked one — **and no policy gets past stage 1.** Two causes, neither of them the masking:

1. **The novelty bonus rewards breadth where this flow needs depth.** Reaching a static footer page pays the same as advancing a stage and is far easier. `random_masked` maximises exactly that and earns the best reward while going nowhere; `dqn_masked` earns the worst while going furthest. Reward and objective point in different directions, visible here as a rank inversion across policies.
2. **The agent could not see semantics** — the encoders were deterministic hash stubs at the time, so "this is a Continue link" was not representable at all.

**Cause 2 was then tested and rejected.** With the real CLIP/CodeBERT/MiniLM encoders in place the masked agent gets *worse*, not better — mean flow depth 0.33 → 0.00, still nothing past stage 1. The likely reason is that a content hash is a near-perfect state *identifier* while semantic embeddings deliberately make similar pages similar, and on a 12-page fixture at 4,000 steps memorisation beats generalisation. So the experiment cannot separate "semantic encoders do not help" from "do not help at this scale", and their value on a large target remains untested rather than disproven.

That leaves **cause 1 as the only one with evidence behind it**: the exploration bonus rewards breadth where the flow needs depth. A depth-aware term is the next RL change worth making.

**On a real application, the hard part was never the judge.** Pointed at a live Gitea, the pipeline produced a stream of confident, well-argued, wrong findings. Thirteen defects were found and fixed across two passes; **none was in the judge**. Every one had the same shape — the input stated something true about the *test harness* as though it were true about the *application*, and the model reasoned correctly from a false premise:

- a navigation the harness refused, rendered as a broken link
- a `target="_blank"` link that correctly left its page unchanged, rendered as a dead control
- a popup opened at step 1 and noticed at step 7, blamed on whichever action was running — including a checkbox, which cannot navigate anywhere
- console errors from a third-party analytics script on someone else's website, attributed to the application under test
- uncaught exceptions never reaching the judge at all, because Playwright reports them on a different event than `console.error`
- a password field, deliberately redacted from the observation, whose redaction made typing into it look ignored

Recall, discrimination and evidence-grounding were all satisfied while these were happening. **No metric caught any of them** — they were found by reading verdicts and disbelieving them. Fixing them took the false-positive rate on Gitea from 16.4% to 8.2% without changing the judge at all.

If there is one transferable lesson here, it is that: **on a new target, the component that needs attention is the observation, not the model.**

---

## Quickstart

Requires Python 3.11 and Docker (for real target applications).

```bash
git clone https://github.com/Rustlogan2k/Autonomous-web-testing-agent.git
cd Autonomous-web-testing-agent

python install.py           # venv, torch, deps, Playwright Chromium
pip install -e ".[dev]"
pytest tests/unit -q        # 469 tests, no browser needed
```

**Watch it drive a browser** against the bundled validation site:

```bash
python scripts/smoke_test_functional_env.py
```

**Reproduce the headline result.** The validation corpus is committed, so this runs offline against fixed files — no browser, no capture:

```bash
ollama pull qwen2.5:7b-instruct     # or use --judge stub for a model-free pipeline check

python scripts/score_judge.py --corpus scripted --judge ollama --model qwen2.5:7b-instruct --prompt compact
```

**Reproduce the real-application result.** The seeded-Gitea corpus is committed too:

```bash
python scripts/score_judge.py --run-id gseed --corpus scripted \
    --answer-key tests/fixtures/gitea_bugs/answer_key.json \
    --judge ollama --model qwen2.5:7b-instruct --prompt compact
```

**Run against a live application:**

```bash
cd docker && docker compose up -d gitea && cd ..
python scripts/setup_gitea.py                    # create a user and seed a repo
python scripts/seed_gitea_bugs.py --install      # optional: install the 6 known defects

python scripts/run_judged.py \
    --base-url http://localhost:3000/ \
    --profile data/profiles/gitea.json \
    --episodes 3 --steps 40
```

**Other entry points:**

| Script | Purpose |
|---|---|
| `compare_agents.py` | Masked DQN vs unmasked vs random, on either fixture |
| `run_baseline.py` | Random-policy baseline — the bar any agent must clear |
| `train_toy.py` | Train the DQN and score it against random under an identical reward |
| `capture_traces.py` | Record a corpus for offline judge development |
| `measure_gate.py` | Replay the call gate over a scored corpus: saving *and* losses |
| `seed_gitea_bugs.py` | Install/verify/remove the seeded defects in a running Gitea |

---

## Repository layout

```
src/web_testing_agent/
  envs/          Gymnasium environment, browser session, action space, network capture
  judge/         Windowing, prompts, verdict schema, scoring, backends, validator
  reward/        Deterministic triggers, exploration shaping, call gate, live LLM judge
  agents/        Masked DQN, SB3 features extractor
  perception/    State normalization, fusion MLP, encoder seam
  intake/        Build-definition security gate, Application Profile schema
  evaluation/    Policy-agnostic rollout harness, random and scripted policies
  annotation/    Trace recorder — content-addressed, replayable corpora

tests/fixtures/
  toy_site/          5 pages, 10 seeded bugs, answer key
  gitea_bugs/        6 defects injected into real Gitea via template overrides
  deep_flow_site/    4-gate ordering flow; the defect is only at the end

data/profiles/       Application Profiles and session-bootstrap macros
data/annotations/    Captured trace corpora
reports/             Measured results
```

---

## Design decisions worth knowing

**The judge is developed entirely offline.** Nothing in `judge/` imports the environment or Playwright. Traces are captured once into a content-addressed corpus, and prompt changes are then tested against fixed files in seconds rather than browser runs. This is the single highest-leverage decision in the project.

**Two corpora, because they answer different questions.** A *scripted* corpus walks one repro path per seeded bug plus a known-correct control, measuring accuracy. A *random* corpus is unlabelled exploration, measuring how often the judge speaks up on ordinary traffic. Only the second can reveal rendering bugs, because the first's ground truth was written against the same renderer.

**Evidence grounding is the metric that transfers.** Recall and discrimination need an answer key. Checking that a quoted line really occurs in the model's input needs nothing, so it is the only judge-quality measure that survives contact with an application whose bugs you do not already know.

**Seeded bugs go in through the application's own extension points.** Gitea is not forked or rebuilt — the defects are template overrides in `$GITEA_CUSTOM`, auditable in two files and provably absent once removed. A forked binary invites "you tested your own fork".

**Uploaded build definitions are parsed and rejected before execution, not merely constrained during it.** A `docker-compose.yml` specifies its own security context — `privileged: true`, `network_mode: host`, a `/:/host` bind mount — and no CPU or memory quota prevents any of them.

**Reward composition is treated as adversarial.** Every magnitude is a documented, retunable parameter, every term is broken out per-step for diagnosis, and each closed exploit is pinned by a regression test.

---

## Limitations

- **Source grounding is not yet demonstrated.** The Application Profile schema and consumer exist and measurably help precision, but profiles are currently hand-authored; the extractor that would derive them from a repository is not built. "The code defines X, testing observed Y" is the goal, not a claim.
- **Semantic perception is built but unproven.** The encoders run at ~5% step overhead, and CodeBERT needed two corrections to be usable at all (mean pooling and a fixed centering vector — the spec's `[CLS]` gives pairwise cosine ~0.99 across every page). On the small fixtures they measurably *hurt* the RL agent; whether they help on a large, diverse target is untested.
- **The RL contribution is an open question**, and on current evidence a negative one. See above.
- **Small n on the real target.** Six seeded bugs and two control windows is enough to be honest with, not enough to be confident with.
- **Auto-deploy is gated but not built.** Uploaded build definitions are validated; the sandboxed runner that would execute them is not, and a passing policy check is not a sandbox.
- **The hosted judge is not version-pinned.** See the reproducibility caveat above.

---

## Stack

Playwright · Gymnasium · Stable-Baselines3 · PyTorch · Ollama (GGUF via llama.cpp) · Anthropic API · Docker

Target applications: Gitea, OpenCart, Nextcloud, DVWA, WebGoat — all containerised.

## License

MIT
