# Semantic-Aware Web Testing Agent

**Autonomous functional bug detection using an LLM judge and reinforcement learning.**

An agent that explores a live web application in a real browser and, at every step, asks a language model a single question: *given what the application was just asked to do, did it behave correctly?* Findings are reported with the evidence that justifies them.

The bet is simple. Existing dynamic tools are pattern matchers — OWASP ZAP looks for injection signatures, Cypress checks assertions a human wrote in advance. Neither *understands what a page is trying to do*, so neither can catch a bug like "this form claims to update your email address, and the confirmation page shows the old one." That bug is invisible to a signature and invisible to any assertion nobody thought to write. It is obvious to a reader who understands the intent.

---

## The headline result

The claim the project rests on is that semantic judgment reaches bugs that deterministic rules cannot. On a purpose-built validation site with 10 seeded bugs and a machine-readable answer key:

| | deterministic triggers | LLM judge |
|---|---|---|
| semantic bugs (require understanding) | **0 / 7** | **7 / 7** |
| mechanically detectable bugs | 3 / 3 | 3 / 3 |
| **total** | **3 / 10** | **10 / 10** |
| false positives on known-correct behaviour | 0 | **0** |
| findings whose quoted evidence really occurs in the input | n/a | **100%** |

14 positive verdicts across 39 windows — selective rather than trigger-happy, every one landing on the right episode with a sensible bug type.

The 3/10 ceiling is not a strawman. It is this project's own deterministic detector — console errors, unexpected HTTP status codes, stuck pages, broken navigation — run over the identical traces. It is what you can get without a language model, and it is the number the judge has to beat to justify existing.

---

## How it works

```
repository ──► build gate ──► deploy ──► live app
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    │                                               │
              RL agent explores                          LLM judge scores
              (Gymnasium + DQN)  ────── every step ─────► each transition
                    │                                               │
                    │                                    verdict + evidence
                    │                                               │
                    └──────────────► reward ◄───────────────────────┘
                                       │
                                  bug report
```

Four ideas do the work.

**1. The judge sees a window, not a snapshot.** Most interesting bugs are not visible in a single transition. "The signup form silently dropped your email" can only be seen by comparing what was typed several steps ago against what the confirmation page echoes back. The judge is given a rolling window of six consecutive interactions and asked about the last one in context.

**2. The judge sees a behavioural diff, not raw HTML.** Six steps of before-and-after markup is enormous and mostly irrelevant. Each page is reduced to what a tester would actually look at — title, controls and whether they are filled, hidden elements, links, visible text, console errors, failed requests — and consecutive pages are rendered as a *diff*. A dead control then appears as the conspicuous absence of any change, which is exactly the signal it needs to be.

**3. Verdicts are ordered so analysis precedes judgment.** The output schema forces the model to write down what it observed, what a correct application should have done, and whether those contradict — and only then to answer yes or no. This is not cosmetic. An earlier version put the boolean first and a model duly wrote "NO OBSERVABLE CHANGE — the page is byte-identical", typed it `dead_control`, and answered `is_bug: false` on 38 of 39 windows. Same model, same data, reordered fields: 0/7 → 7/7.

**4. Every finding must quote its evidence, and the quote is checked.** A verdict whose cited line does not occur in what the model was shown is flagged automatically. This matters more than recall: a reward model that pays out for invented findings is worse than no reward model at all. One 7B model scored 7/7 on recall while fabricating 68% of its citations — including, at full confidence, a claim that scrolling reset a counter, which scrolling cannot do and which no line in its input said.

---

## What is built

| Component | Status |
|---|---|
| **Browser environment** — Playwright + Gymnasium, 9 action types, dynamic 100-slot action space | Built |
| **LLM judge** — windowing, prompts, verdict schema, scoring, 4 backends | Built and measured |
| **Live reward loop** — judge wired into the RL reward signal, with call gating | Built |
| **Deterministic triggers** — the baseline the judge must beat | Built |
| **Evaluation harness** — policy-agnostic rollouts, deduplicated findings, random baselines | Built |
| **Trace corpus** — capture once, iterate on prompts against fixed files | Built |
| **Window validator** — 13 invariants that refuse to score a self-contradicting input | Built |
| **Application Profile** — schema for source-derived intent, sliced per window | Built |
| **Agent B (DQN)** — functional-bug agent | Built; see the honest results below |
| **Build-definition security gate** — rejects privilege escalation in uploaded compose files | Built |
| Repo profiler (extract intent from source automatically) | Not built |
| Auto-deploy runner | Not built |
| Semantic perception encoders (CLIP / CodeBERT / MiniLM) | Seam built, encoders not |
| Bug report engine | Not built |
| Agent A (security testing, PPO + curiosity) | Not built |

465 tests.

---

## Results, stated honestly

**The judge works, and it is the strongest component.** 10/10 against a 3/10 deterministic ceiling, zero false positives on known-correct behaviour, 100% evidence grounding. It holds on a held-out corpus of unlabelled exploration, not just on the scripted repro paths.

**Reinforcement learning has not yet earned its place.** At equal step budget on the validation site, the DQN found 1 of 3 mechanically-detectable bugs; uniform-random exploration over valid actions found 3 of 3. Training also produced four distinct reward exploits in five runs — the agent learned to refresh a 404 page 117 times, to do literally nothing, and to end each episode on step one — each one a real defect in the reward design, and each found by the optimizer rather than by review or the test suite.

That result comes with a caveat that cuts both ways: the validation site is five shallow pages where nearly every bug sits one or two steps from the landing page, which is precisely the regime where random exploration is strongest and sequential credit assignment is worth nothing. RL's actual claim — chaining multi-step flows where random's success probability decays exponentially with sequence length — remains untested. The honest statement is *"DQN did not beat random on shallow sites"*, not *"RL does not help"*.

**On a real application, the hard part was not the judge.** Pointed at a live Gitea instance, the pipeline produced a stream of confident, well-argued, wrong findings. Ten defects were found and fixed; **none of them was in the judge**. Every one had the same shape — the input stated something true about the *test harness* as though it were true about the *application*, and the model reasoned correctly from a false premise:

- a navigation the harness refused, rendered as a broken link
- a `target="_blank"` link that correctly left its page unchanged, rendered as a dead control
- a popup opened at step 1 and noticed at step 7, blamed on whichever action was running — including a checkbox, which cannot navigate anywhere
- console errors emitted by a third-party analytics script on someone else's website, attributed to the application under test
- a password field, deliberately redacted from the observation, whose redaction made typing into it look like it had been ignored

Recall, discrimination and evidence-grounding were all satisfied while these were happening. The citations were verbatim and the reasoning was sound. **No metric caught any of them** — they were found by reading verdicts and disbelieving them. Fixing them took the false-positive rate on Gitea from 16.4% to 8.2% without changing the judge at all.

If there is one transferable lesson here, it is that: **on a new target, the component that needs attention is the observation, not the model.**

**Call gating.** A judge call costs ~10 seconds, so judging every step is not slow but impossible at training scale. The filter asks "was an action taken that *should* have changed something", never "did the state change" — a control that promises an effect and produces none is exactly the bug class the judge is most needed for. It skips 38–45% of steps while losing **0 of 38** positive verdicts across two labelled corpora.

---

## Quickstart

Requires Python 3.11 and Docker (for real target applications).

```bash
git clone https://github.com/Rustlogan2k/Autonomous-web-testing-agent.git
cd Autonomous-web-testing-agent

python install.py           # venv, torch, deps, Playwright Chromium
pip install -e ".[dev]"
pytest tests/unit -q        # 449 tests, no browser needed
```

**Watch it drive a browser** against the bundled validation site:

```bash
python scripts/smoke_test_functional_env.py
```

**Reproduce the headline result.** The validation corpus is committed, so this runs offline against fixed files — no browser, no capture:

```bash
# Needs a judge backend. Ollama is the default:
ollama pull qwen2.5:7b-instruct

python scripts/score_judge.py --corpus scripted --judge ollama --model qwen2.5:7b-instruct
```

Add `--profile data/profiles/gitea.json` to ground the judge in an application description. Use `--judge stub` to exercise the whole pipeline with no model at all, or `--dry-run` to print the exact input the judge would see.

**Run against a real application:**

```bash
cd docker && docker compose up -d gitea && cd ..
python scripts/setup_gitea.py                     # create a user and seed a repo

python scripts/run_judged.py \
    --base-url http://localhost:3000/ \
    --profile data/profiles/gitea.json \
    --episodes 3 --steps 40
```

**Other entry points:**

| Script | Purpose |
|---|---|
| `run_baseline.py` | Random-policy baseline — the bar any agent must clear |
| `train_toy.py` | Train the DQN and score it against random under an identical reward |
| `capture_traces.py` | Record a corpus for offline judge development |
| `measure_gate.py` | Replay the call gate over a scored corpus: saving *and* losses |

---

## Repository layout

```
src/web_testing_agent/
  envs/          Gymnasium environment, browser session, action space, network capture
  judge/         Windowing, prompts, verdict schema, scoring, backends, validator
  reward/        Deterministic triggers, exploration shaping, call gate, live LLM judge
  perception/    State normalization, fusion MLP, encoder seam
  intake/        Build-definition security gate, Application Profile schema
  evaluation/    Policy-agnostic rollout harness, random and scripted policies
  annotation/    Trace recorder — content-addressed, replayable corpora

tests/fixtures/toy_site/    5-page validation site, 10 seeded bugs, answer key
data/profiles/              Application Profiles
data/annotations/           Captured trace corpora
reports/                    Measured results
```

---

## Design decisions worth knowing

**The judge is developed entirely offline.** Nothing in `judge/` imports the environment or Playwright. Traces are captured once into a content-addressed corpus, and prompt changes are then tested against fixed files in seconds rather than browser runs. This is the single highest-leverage decision in the project.

**Two corpora, because they answer different questions.** A *scripted* corpus walks one repro path per seeded bug plus a known-correct control, measuring accuracy. A *random* corpus is unlabelled exploration, measuring how often the judge speaks up on ordinary traffic. Only the second can reveal rendering bugs, because the first's ground truth was written against the same renderer.

**Evidence grounding is the metric that transfers.** Recall and discrimination need an answer key. Checking that a quoted line really occurs in the model's input needs nothing, so it is the only judge-quality measure that survives contact with an application whose bugs you do not already know.

**Uploaded build definitions are parsed and rejected before execution, not merely constrained during it.** A `docker-compose.yml` specifies its own security context — `privileged: true`, `network_mode: host`, a `/:/host` bind mount — and no CPU or memory quota prevents any of them. Resource limits are not a sandbox.

**Reward composition is treated as adversarial.** Every magnitude is a documented, retunable parameter, every term is broken out per-step for diagnosis, and each closed exploit is pinned by a regression test. The assumption is that any future reward change is exploitable until a training run says otherwise.

---

## Limitations

- **Source grounding is not yet demonstrated.** The Application Profile schema and consumer exist and measurably help, but profiles are currently hand-authored; the extractor that would derive them from a repository is not built. "The code defines X, testing observed Y" is the goal, not a claim.
- **Semantic perception is stubbed.** The fusion architecture and normalization are built, but the visual and structural encoders are not, so the RL agent currently observes a deterministic content hash rather than a semantic embedding.
- **The RL contribution is an open question**, and on current evidence a negative one. See the results above.
- **Auto-deploy is gated but not built.** Uploaded build definitions are validated; the sandboxed runner that would execute them is not, and a passing policy check is not a sandbox.
- **A hosted judge is currently the practical default.** A 7B model on a 6 GB laptop reaches the same 10/10, which is a stronger result than the same figure from a frontier model — but VRAM contention with the perception layer rules local inference out during training by arithmetic rather than preference.

---

## Stack

Playwright · Gymnasium · Stable-Baselines3 · PyTorch · Ollama (GGUF via llama.cpp) · Anthropic API · Docker

Target applications: Gitea, OpenCart, Nextcloud, DVWA, WebGoat — all containerised.

## License

MIT
