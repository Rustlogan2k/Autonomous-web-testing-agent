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
| **Evaluation harness** — policy-agnostic rollouts, deduplicated findings, random baselines, multi-seed spreads | Built |
| **Trace corpus + window validator** — 13 invariants that refuse to score a self-contradicting input | Built |
| **Application Profile** — schema for source-derived intent, sliced per window | Built |
| **Ground-truth fixtures** — 10-bug toy site, 6-bug seeded Gitea, 4-gate deep-flow site | Built |
| **Build-definition security gate** — rejects privilege escalation, host bind-mounts, and multi-file merges in uploaded compose files | Built |
| **Semantic perception encoders** — CLIP / CodeBERT / MiniLM, frozen, batched, cached | Built (~5% step overhead, 951 MB VRAM) |
| **Repo Intake runner** — detect, gate, build, run, health-check, `base_url`, teardown | Built (trusted input; **not** a security boundary) |
| **Bug report engine** — findings + verdicts to a reproducible report | Built |
| **Go-Explore explorer** — archive, return-then-explore | Built |
| Repo profiler (extract intent from source automatically) | Not built |
| Agent A (security testing, PPO + curiosity) | Out of scope |

582 tests (557 without a browser).

---

## The parts that did not work, and why

**Reinforcement learning has not yet earned its place.** On the shallow fixture, uniform-random exploration over valid actions beat the DQN at equal step budget — re-measured over 3 seeds: **masked random 3/3 seeded bugs [2–3] against the DQN's 0/3 [0–1]**, and 15 unique states [14–16] against 6 [6–7]. The single-seed figure this replaces (DQN 1/3) turned out to be the one seed where *every* arm found a bug, unmasked random included, so it never measured the DQN at all. That fixture trained an unmasked head, so it was not a masking comparison — **corrected 2026-08-27**, it now trains both arms with byte-identical hyperparameters. Masking helps and does not close the gap: **masked DQN 1/3 seeded bugs [0–2] and 9 state [7–14], against masked random's 3/3 [2–3] and 15 [14–16]**. That is the first evidence here that masking buys the agent anything beyond action validity, and the loss to random survives the fair comparison. What is not in doubt is the reward work: training produced four distinct reward exploits in five runs — the agent learned to refresh a 404 page 117 times, to do literally nothing, and to end each episode on step one — each a real defect in the reward design, and each found by the optimizer rather than by review or the test suite.

**Action masking works; the deep flow is still unsolved.** The original comparison was not a fair fight: masked random could sample only legal slots and the flat 100-way Q-head structurally could not. That is now fixed and verified — 100% valid actions against 18% for unmasked random, 0/200 invalid actions in a controlled harness. Measured on a four-gate ordering flow whose defect is reachable only after completing every stage in order:

Measured over **3 training seeds × 5 episodes**, with residual ε=0.05 exploration at evaluation so the trained policy's episodes can differ from each other. Every cell is median [min–max] across seeds:

> **The `distinct findings` column is invalid, corrected 2026-08-27.** Every firing
> behind it was a self-referential link — clicking `Catalog` while on `catalog.html`
> reloads the page, correctly leaves the URL unchanged, and the harness read that as
> a broken link. This fixture's deterministic ceiling is genuinely 0, so nothing in
> that column was ever a bug. The other four columns are unaffected.

| policy | valid actions | mean flow depth | reward | ~~distinct findings~~ |
|---|---|---|---|---|
| dqn_unmasked | 90% [82–96] | 0.17 [0.00–0.22] | −10.2 [−17.5, −4.0] | 0 [0–1] |
| **dqn_masked** | **100%** (every seed) | 0.03 [0.03–**0.36**] | −14.6 [−16.0, −9.5] | 1 [0–2] |
| random_unmasked | 18% [16–18] | 0.00 (every seed) | −28.8 [−29.3, −26.7] | 3 [2–3] |
| **random_masked** | **100%** (every seed) | 0.04 [0.01–0.04] | **+7.3** [6.6, 8.1] | **10 [10–12]** |

> An earlier version of this table reported `dqn_masked` at mean depth **0.33** and concluded masking tripled it. That figure was one seed evaluated greedily, which on a static fixture is a single trajectory replayed five times. Re-measured, the same arm spans **0.03 to 0.36** across seeds — the original number was not wrong so much as *unrepresentative*, one draw from a wide distribution reported as the result.

**Masking works, and it does exactly one thing.** 100% valid actions on every seed, against 82–96% unmasked and 16–18% for unmasked random — no spread at all. But the depth medians are 0.03 masked against 0.17 unmasked, with heavily overlapping ranges and near-identical means. **Masking fixes action validity completely and does nothing measurable for depth.**

**No policy gets past stage 1** — `max_depth ≤ 1` and zero receipts across all 24 runs. Two causes, neither of them the masking:

1. **The novelty bonus rewards breadth where this flow needs depth.** Reaching a static footer page pays the same as advancing a stage and is far easier. `random_masked` maximises exactly that and earns the best reward while going nowhere; `dqn_masked` earns the worst while going furthest. Reward and objective point in different directions, visible here as a rank inversion across policies.
2. **The agent could not see semantics** — the encoders were deterministic hash stubs at the time, so "this is a Continue link" was not representable at all.

**Cause 2 was tested twice — the first test was not sound, and the second is narrower than the first claimed.** The original run reported mean flow depth 0.33 → 0.00 with real encoders and called the perception hypothesis disproven. That was n=1 (see the note above). Re-run over 3 seeds against the identical hash-stub configuration:

| | valid actions | mean flow depth | reward |
|---|---|---|---|
| dqn_masked, hash stub | 100% | 0.03 [0.03–0.36] | −14.6 [−16.0, −9.5] |
| dqn_masked, semantic | 100% | 0.01 [0.00–0.10] | **−23.2 [−31.7, −20.7]** |
| dqn_unmasked, hash stub | 90% [82–96] | 0.17 [0.00–0.22] | −10.2 [−17.5, −4.0] |
| dqn_unmasked, semantic | **60% [52–87]** | 0.05 [0.03–0.08] | **−19.6 [−22.8, −17.5]** |

**Semantic encoders measurably hurt, but not on the metric originally cited.** Return is clearly worse — the seed ranges do not overlap on either arm — and the unmasked agent's valid-action rate collapses from 90% to 60%, which is what you would expect if embeddings that deliberately make similar pages similar also make "which slots are valid here" harder to memorise. **Flow depth is lower too but the ranges overlap and n=3 cannot separate it**, so the specific "0.33 → 0.00" claim is retired rather than confirmed.

The mechanism still looks right: a content hash is a near-perfect state *identifier*, semantic embeddings are deliberately the opposite, and on a 12-page fixture at 4,000 steps memorisation beats generalisation. That predicts encoders should pay off on large diverse targets and cost you here — which is what was measured, and which leaves their value on a real target **untested**, exactly as before. Keep them; do not expect them to move this fixture.

**Cause 1 is the one with strong evidence**, now across seeds rather than from one run: `random_masked` earns the best reward on every seed (+6.6 to +8.1) while going essentially nowhere (depth 0.01–0.04). Reward and objective point in different directions, robustly. A depth-aware exploration term is the next RL change worth making.

**And the harder result: masked random beats the DQN on exploration by a wide margin, on every seed, under both encoder configurations.** This fixture was built precisely to be the regime where RL's real claim (credit assignment over multi-step flows, where random's success probability decays exponentially in sequence length) should finally show. It did not. Masked random wins on the shallow fixture *and* on the deep one. The honest statement is no longer "RL's claim is untested" — it was tested, in the regime chosen to favour it, and it lost.

> **Corrected 2026-08-27.** This paragraph previously read "beats the DQN on **bug discovery** by roughly 10× — 10–12 distinct findings against 0–4". Those findings were all self-link false positives (see the note on the table above), so the count measured how many distinct links on how many distinct pages each policy touched, not bugs. The gap is real and reproduces on every seed; it is an **exploration diversity** result, and the deep-flow verdict now rests on depth, state coverage and flow completion instead. On the **toy** fixture the seeded-bug comparison is unaffected — that site has no self-links and its 3/3-versus-0/3 answer-key result stands.

**Two measurement defects were found in this project's own numbers, and both are corrected above.**

*The deep-flow findings column was false positives.* `_is_navigational` treated every non-fragment link as expected-to-change-the-URL, so clicking `Catalog` while already on `catalog.html` — a reload that correctly leaves the URL unchanged — was recorded as a broken link. Every `broken_navigation` firing ever seen on that fixture was one of these. The corrected run fires the trigger **zero times on every arm and every seed** (before: 71 firings for masked random, 55 for the masked DQN). The fixture's own answer key had asserted that no deterministic trigger could fire on it, which is why 18–20 firings per run went unquestioned for ten days; that claim is corrected too. What dies is the "10× on bug discovery" reading — on a fixture whose deterministic ceiling is genuinely 0, nothing has ever found a bug there and nothing could. What survives is everything the fixture was built to measure: no policy passes stage 1, masking fixes validity and not depth, and masked random still earns the best reward while going least far.

*DQN training is not reproducible at a fixed seed.* The toy fixture has no self-links, so its numbers should have been untouched by the fix. The random baselines were — bit-for-bit, all three seeds. The DQN was not (seed 2 went from 6 unique states to 1). Random policies never read the observation and DQN policies do, and probing directly shows why: across two identical navigations the visual and structural vectors are identical and the **network vector is unrelated** (cosine +0.02), because the HTTP `date` header, a monotonic timestamp and a float `duration_ms` all survive canonicalization and a content-hash encoder maps any byte difference to an orthogonal vector. **384 of 1,664 observation dimensions were clock jitter.** Fixed: the observation channel now canonicalizes the network trace (dropping clock readings, bucketing durations so `slow_response` survives, masking per-session header values while keeping header names), while `info["page"]["network"]` keeps full fidelity for the judge, the trace corpus and the bug report. Two 600-step trainings at an identical seed are now **bit-identical** in actions, rewards, observations and final weights. Re-measured over 3 seeds afterwards, **none of the conclusions moved**: no policy passes stage 1 of the deep flow, masking fixes validity and not depth, and masked random still earns the best reward while going least far. The masked DQN's final policy did improve to 2/3 seeded bugs [1–3] on the toy site — the best this project has produced — and still loses to masked random's 3/3 and 15 states.

**On a real application, the hard part was never the judge.** Pointed at a live Gitea, the pipeline produced a stream of confident, well-argued, wrong findings. Fourteen defects have been found and fixed across three passes; **none was in the judge**. Every one had the same shape — the input stated something true about the *test harness* as though it were true about the *application*, and the model reasoned correctly from a false premise:

- a navigation the harness refused, rendered as a broken link
- a `target="_blank"` link that correctly left its page unchanged, rendered as a dead control
- a popup opened at step 1 and noticed at step 7, blamed on whichever action was running — including a checkbox, which cannot navigate anywhere
- console errors from a third-party analytics script on someone else's website, attributed to the application under test
- uncaught exceptions never reaching the judge at all, because Playwright reports them on a different event than `console.error`
- a password field, deliberately redacted from the observation, whose redaction made typing into it look ignored
- form constraints the page never declared — `minlength="3"` also emitted a bare `min`, a `max-w-full` CSS class emitted a bare `max`, and `data-type` was read as `type`. This one reached **30.7% of all judge inputs**, including every window of the seeded-Gitea corpus, and **zero** windows of the toy site, whose fixture markup is too clean to contain it

Recall, discrimination and evidence-grounding were all satisfied while these were happening. **No metric caught any of them** — they were found by reading verdicts and disbelieving them. Fixing them took the false-positive rate on Gitea from 16.4% to 8.2% without changing the judge at all.

The last of the fourteen is the cleanest illustration, because its fix was measured as a controlled pair — same model, same prompt, same session, only the renderer differing. Correcting a false premise on 30.7% of inputs changed **nothing**: 6/6 seeded bugs either way, one false positive either way. That is worth stating plainly rather than dressing up. A false premise is not automatically load-bearing, and the honest claim for a fix like this is that the input is now true, not that the output improved.

If there is one transferable lesson here, it is that: **on a new target, the component that needs attention is the observation, not the model.**

---

## Web Application

A browser interface over the testing system: upload a Dockerised repository (or pick a
bundled demo), watch the agent explore it live, and read the report it produces. The
application is a **product layer** — it orchestrates the existing pipeline and adds no
testing logic of its own. `src/web_testing_agent/app/` imports from the research packages;
nothing in the research packages imports from it.

### Start

```bash
pip install -r requirements.txt     # adds fastapi, uvicorn, python-multipart
pip install -e .

python -m web_testing_agent.app     # or: python scripts/run_app.py
```

Then open **http://localhost:8000**.

`--port` and `--host` are available. It binds to `127.0.0.1` by default on purpose: this
application builds and runs user-supplied repositories, so it should not be reachable off
the machine without a deliberate decision.

**Docker is required for uploads, not for the demos.** Uploaded repositories are built and
run in a container; the two static demo applications are served locally and need no daemon.
The dashboard says which mode is available.

### Demo

Click **New Test → Try Demo**. Three bundled applications, all existing research fixtures,
used unmodified:

| Demo | Needs Docker | What it shows |
|---|---|---|
| Acme Tools (`toy_site`) | no | The 10-bug validation site. Produces real findings — broken navigation, HTTP errors, console errors. |
| Northwind Supply (`deep_flow_site`) | no | The four-stage gated flow. Deterministic ceiling is 0 by construction, so it demonstrates depth and coverage rather than findings. |
| Northwind static site (`demo_repo`) | **yes** | The full container path: policy gate → image build → sandboxed run → health check. |

A reviewer with a fresh clone can run one command, click *Try Demo* on Acme Tools, and see
a complete report in about a minute.

### Upload a Repository

**New Test → Upload a repository**, or drag a `.zip` onto the drop zone. The archive must
contain a `Dockerfile` in its root or one level down; a single GitHub-style wrapper
directory is unwrapped automatically.

The upload is validated before anything executes — path traversal, absolute and
drive-qualified entries, symlink entries, entry-count and expansion limits, and the
compression ratio are all checked, then the build definition goes through the same policy
gate `scripts/run_repo.py` uses. The page then shows the repository name, size, file count,
detected build file, exposed ports and detected languages, with a validation verdict.

### Run a Test

Pick an agent, the number of episodes, and the steps per episode, then **Start Test**. You
are redirected to `/runs/<run_id>`, which streams the run live over server-sent events:
pipeline component status, progress counters, and an activity log of the actual actions the
agent takes. **Open Tested Application** opens the target while it is running, and
**Stop run** cancels at the next step, tearing the sandbox down through the normal path.

Four agents are selectable:

| Agent | Trained? | Notes |
|---|---|---|
| Search (hand priority) | no | Deterministic, application-agnostic action priority. The default. |
| Random (masked baseline) | no | Uniform over valid actions — the project's reference baseline. |
| AC-DQN | research checkpoint | Trained on one bundled fixture at a 4,000-step budget. |
| Contextual Bandit | research checkpoint | Myopic control, same fixture-specific caveat. |

**The reinforcement-learning agent is an active research component** (see §5 of
`PROJECT_CONTEXT.md`). The two checkpoint-backed agents load read-only and are labelled in
the UI as research-stage; their learned preferences are specific to the fixture they were
trained on. Nothing in this application trains anything — training is an experiment, not a
web request. Adding the finished agent later means one entry in
`app/agents.py`; no route, template or frontend change.

### View Findings

**Findings** lists every finding across every run with severity, application and run;
clicking one opens it in that run's report. **Reports** lists completed reports with
**Download JSON** and **Download Markdown** — these are the *existing* artifacts, written by
`build_report()` and `render_markdown()`, byte-identical to what `scripts/run_repo.py`
produces.

### Security boundary

Uploaded repositories are untrusted input and are treated as such:

- extracted into a temporary directory **outside the project tree**, never over project files
- built as a container image and run with `cap_drop: ALL`, `no-new-privileges`, a read-only
  root filesystem, a tmpfs `/tmp`, 2 CPUs / 2 GB / 256 PIDs, `user 1000:1000`, and an
  internal-only bridge network (`SANDBOX_RUN_ARGS` in `intake/compose_policy.py`)
- no host bind mounts and **no Docker socket** in the target container
- build-time network egress denied by default (`--build-network none`)
- workspace deleted when the run ends, on the success and the failure path
- orphaned workspaces from a killed process are swept at startup

**The known limitation, stated plainly:** `docker build` executes the repository's own
`RUN` commands *before* any `docker run` restriction exists. A hostile repository does not
need to escape the container — it can act during the build. This is the same boundary
`scripts/run_repo.py` documents, and it is recorded in every report's `run_meta.scope_note`.
Point this at a disposable machine if the input is not genuinely trusted. The application's
own process needs Docker access; the target container is never given any.

Run state lives in `var/app/` (gitignored) — one directory per run holding the run record,
the report, the event log and the evidence tree.

---

## Quickstart

Requires Python 3.11 and Docker (for real target applications).

```bash
git clone https://github.com/Rustlogan2k/Autonomous-web-testing-agent.git
cd Autonomous-web-testing-agent

python install.py           # venv, torch, deps, Playwright Chromium
pip install -e ".[dev]"

pytest tests -q             # 864 passed, 14 skipped (full suite, this machine)
```

The suite splits by what each test needs, so a partial environment gives skips rather
than failures. `tests/conftest.py` prints what it found and marks tests accordingly:

```bash
# exactly what CI runs — nothing beyond pip, no daemon, no browser
pytest tests -m "not requires_torch and not requires_docker and not requires_browser" -q
# 694 passed, 5 skipped, 179 deselected
```

`requires_torch` needs torch/SB3/gymnasium, `requires_docker` needs a **reachable
daemon** (not merely the CLI on PATH), `requires_browser` needs Playwright's Chromium.

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

**Run the whole pipeline on a repository** — profile, deploy, explore, judge, report:

```bash
python scripts/run_repo.py --repo tests/fixtures/demo_repo --judge stub
python scripts/run_repo.py --repo tests/fixtures/demo_repo --deploy-only   # just the URL
```

The stages are `repository -> profile -> detect -> policy gate -> build -> run ->
health check -> base_url -> environment -> rollout -> judge -> bug report -> teardown`.
`pipeline.py` is the composition root and every stage is an injectable parameter, so the
wiring is testable in milliseconds with a fake deployer and a stub judge
(`tests/unit/test_pipeline.py`).

### Deployment scope and threat model

**Dockerfile-only, and deliberately so.** A repository is deployed if it has a
`Dockerfile`. **Compose files are detected and policy-validated, then refused** — running
one means generating an override and trusting a merge you did not write, which gives up
the control the gate exists to provide. `deploy_repository` raises `RepoIntakeError` naming the
file; provide a Dockerfile, or run the compose project yourself and point
`scripts/run_judged.py --base-url` at it (`run_repo.py` deploys, so it has no such flag).

**The repository is assumed trusted or controlled.** This is not a sandbox for hostile
input, and the reason is structural: `docker build` executes the repository's own build
commands *before* any `docker run` restriction exists. Resource limits, `cap_drop: ALL`,
`no-new-privileges`, a read-only rootfs and an internal-only network are all applied at
run time and none of them constrain the build. Point this at a disposable machine if the
input is not genuinely trusted.

**Other entry points:**

| Script | Purpose |
|---|---|
| `compare_agents.py` | Masked DQN vs unmasked vs random, on either fixture |
| `run_baseline.py` | Random-policy baseline — the bar any agent must clear |
| `train_toy.py` | Train the DQN and score it against random under an identical reward |
| `capture_traces.py` | Record a corpus for offline judge development |
| `measure_gate.py` | Replay the call gate over a scored corpus: saving *and* losses |
| `seed_gitea_bugs.py` | Install/verify/remove the seeded defects in a running Gitea |

### Evidence, and how a finding is traced back

Every reported bug names the exact transition that produced it. `TraceRecorder`
content-addresses each artifact as it is captured — page bodies to `pages/<sha256>.html`,
frames to `screenshots/<sha256>.png` — and `annotation/evidence.py` reduces a trace to
references for just the steps a report cites. The join key already existed: `run_rollout`
and `TraceRecorder` increment the same global step counter for the same transition, and
both trigger findings and judge verdicts carry it. `tests/unit/test_evidence.py` pins
that alignment rather than trusting the paragraph.

Validated end to end against a real model: **43/43 positive verdicts resolved** — each
verdict's step is a real global step, present in the evidence index, whose recorded URL
matches the verdict's and whose page-body and screenshot digests resolve to files that
exist.

### The judge, and what is currently wrong with it

`qwen2.5:7b-instruct` (version-pinned local), `temperature=0`, `num_ctx=8192`, K=6
windows, gated by the same `reward.gating` filter the live reward path uses. Nothing in
`judge/` imports the environment or Playwright, so prompt changes are tested against
fixed files in seconds.

**Precision is the open problem, and it is worse than the fixture results above suggest.**
On the deep-flow trajectory, scored in a dedicated validation run:

| prompt | detects the seeded bug | false positives on correct behaviour | evidence grounded |
|---|---|---|---|
| `detailed` | **2/2** | 8/15 windows (53%) | 7/20 (35%) |
| `compact` | **0/2** | 11/15 windows (73%) | 23/23 (100%) |

The two prompts trade off cleanly and oppositely, and neither is usable as-is. Worse, on
one of the four bug-bearing combinations the correct detection was **quarantined as
ungrounded** — the model paraphrased a real quote, and `is_grounded` cannot distinguish a
paraphrase of something true from an invention. It is doing its job; the job is not
sufficient.

An offline study of all 43 positives found that roughly half the false positives are
mechanically contradicted by facts in the record the judge was shown, and a filter over
those facts clears its pre-registered bar. **It is designed, not implemented** — see
`PROJECT_CONTEXT.md`. Treat the 10/10 fixture number as the ceiling under favourable
conditions, not as the judge's general precision.

---

## Repository layout

```
src/web_testing_agent/
  envs/          Gymnasium environment, browser session, action space, network capture
  judge/         Windowing, prompts, verdict schema, scoring, backends, validator
  reward/        Deterministic triggers, exploration shaping, call gate, live LLM judge
  agents/        Masked DQN, action-conditioned dueling DQN, PER + n-step replay,
                 per-action features, Go-Explore archive and archive-start curriculum
  perception/    State normalization, fusion MLP, encoder seam
  intake/        Build-definition gate, Docker runner, repo profiler, Application Profile
  evaluation/    Policy-agnostic rollout harness, random and scripted policies
  annotation/    Trace recorder and evidence indexing — content-addressed, replayable
  reporting/     Bug report engine: findings, evidence blocks, ungrounded quarantine
  pipeline.py    Composition root: repository -> deployment -> rollout -> judge -> report

tests/fixtures/
  toy_site/          5 pages, 10 seeded bugs, answer key
  gitea_bugs/        6 defects injected into real Gitea via template overrides
  deep_flow_site/    4-gate ordering flow; the defect is only at the end

data/profiles/       Application Profiles and session-bootstrap macros
data/annotations/    Captured trace corpora
reports/             Measured results
```

---

## CI

`.github/workflows/tests.yml`, three jobs split by what each needs:

| job | needs | what it runs |
|---|---|---|
| `portable` | pip only — `requirements-ci.txt` | the portable subset, on Ubuntu + Windows × Python 3.11/3.12 |
| `integration` | Docker daemon + real Chromium | Linux only |
| `lint` | ruff | the dev extra |

The RL half is deliberately not run in CI: it needs torch, SB3 and transformers — none of
which are in `requirements-ci.txt` — and a meaningful run is hours on a GPU, not minutes
on a runner. Those tests are marked `requires_torch` and are not collected when the stack
is absent.

---

## Design decisions worth knowing

**The judge is developed entirely offline.** Nothing in `judge/` imports the environment or Playwright. Traces are captured once into a content-addressed corpus, and prompt changes are then tested against fixed files in seconds rather than browser runs. This is the single highest-leverage decision in the project.

**Two corpora, because they answer different questions.** A *scripted* corpus walks one repro path per seeded bug plus a known-correct control, measuring accuracy. A *random* corpus is unlabelled exploration, measuring how often the judge speaks up on ordinary traffic. Only the second can reveal rendering bugs, because the first's ground truth was written against the same renderer.

**Evidence grounding is the metric that transfers.** Recall and discrimination need an answer key. Checking that a quoted line really occurs in the model's input needs nothing, so it is the only judge-quality measure that survives contact with an application whose bugs you do not already know.

**Seeded bugs go in through the application's own extension points.** Gitea is not forked or rebuilt — the defects are template overrides in `$GITEA_CUSTOM`, auditable in two files and provably absent once removed. A forked binary invites "you tested your own fork".

**Uploaded build definitions are parsed and rejected before execution, not merely constrained during it.** A `docker-compose.yml` specifies its own security context — `privileged: true`, `network_mode: host`, a `/:/host` bind mount — and no CPU or memory quota prevents any of them.

**A policy over a parsed document is only as strong as the guarantee that the parsed document is the one that runs.** A later review found three ways past that gate, and they shared a shape: `extends` and `include` merge in a second file *after* validation, and `driver_opts: {device: /}` makes a "named" volume a bind mount of host `/` without writing a host path anywhere the volume check looks. Every per-key rule was correct; the category sat one layer above the one being validated. All three are now blocked, and the lesson generalises past this gate — the fix was refusing constructs that change which document is executed, not adding more rules about keys.

**Reward composition is treated as adversarial.** Every magnitude is a documented, retunable parameter, every term is broken out per-step for diagnosis, and each closed exploit is pinned by a regression test.

---

## Limitations

- **Source grounding is not demonstrated, and the consumer did not help.** The Application Profile schema and consumer exist, but a controlled A/B with a version-pinned judge — both arms in one session — found the hand-authored profile changed **recall, false positives, discrimination and evidence grounding by nothing**, while altering 10 of 14 verdicts and shifting bug types toward a single category. An earlier result suggesting it halved false positives used an unversioned hosted model and did not reproduce. No source has been read at any point: "the code defines X, testing observed Y" remains a goal, and it now starts from a measured baseline of zero rather than an assumed benefit.
- **Semantic perception measurably hurts at fixture scale, and is untested at any other.** The encoders run at ~5% step overhead, and CodeBERT needed two corrections to be usable at all (mean pooling and a fixed centering vector — the spec's `[CLS]` gives pairwise cosine ~0.99 across every page). Over 3 seeds they cost the agent return and valid-action rate on a 12-page fixture; whether they help on a large, diverse target — the case they were argued for — remains unmeasured.
- **The RL track is paused by decision, not finished — and the picture changed twice.** The
  earlier headline, *"masked random beats the DQN ~10× on distinct findings"*, is
  **retracted**: every one of those "findings" was a self-link false positive
  (`broken_navigation` firing when a nav link pointed at the page already loaded), and the
  deep-flow fixture's deterministic ceiling is genuinely 0. What replaced it, after an
  action-conditioned dueling DQN, an archive-start curriculum and two reward corrections:
  **the agent went from never passing stage 1 of the four-gate flow to completing it in
  10 of 15 evaluation episodes** — the first flow completions in the project's history.
  A later investigation established that the apparent *seed-level* split behind that
  number was **run-to-run variance**, not a property of the seeds. Bugs found on that
  fixture remain **0**, because its only defect is `llm_required` and these runs use the
  deterministic triggers alone.
- **Every policy comparison in this project is cheap to get wrong in the same way.** A greedy policy against a static fixture produces one trajectory no matter how many episodes you ask for, and the resulting identical numbers look like precision. If you extend this work, check that your evaluation can produce a different number twice before you believe any of it.
- **Small n on the real target.** Six seeded bugs and two control windows is enough to be honest with, not enough to be confident with.
- **Auto-deploy is built for Dockerfiles and refuses compose.** The runner builds, runs,
  health-checks and tears down a Dockerfile repository with `cap_drop: ALL`,
  `no-new-privileges`, a read-only rootfs, pid/memory/CPU limits and an internal-only
  network. None of that makes it safe for untrusted input — `docker build` runs the
  repository's own commands before any run-time restriction exists — so the documented
  scope is trusted/controlled repositories. The compose gate also shipped with three
  bypasses a review found later: now closed, but the useful signal is that a carefully
  written, unit-tested gate still missed an entire category, so assume more remain.
- **The hosted judge is not version-pinned.** See the reproducibility caveat above.

---

## Where RL research resumes

The RL half is frozen at a documented state. Nothing about the agent, reward,
exploration, PER, n-step schedule, epsilon schedule, archive or the deep-flow fixture
should be changed before the next measurement is taken, because that measurement is what
tells you whether any change helped.

**The next action is to measure the run-to-run completion distribution at a fixed seed.**
Flow completions were 10/15 across three runs, and the split looked seed-dependent until
it was found to be run-to-run variance — so the size of that variance is currently
unknown, and no candidate fix can be evaluated against a baseline whose spread nobody has
measured. Repeat one seed N times under the identical protocol and record the
distribution of completions before attempting an exploration fix.

**No candidate RL fix is approved.** The known remaining obstacle is the final
`order-4 -> receipt` transition, where `Place order` and `Back` sit within ~1.7% of each
other in Q — a near-tie decided by noise. Diagnosis pointed at sparse experience rather
than a mis-ordered objective, but **PER sampling of that transition has never been
measured** (the replay buffer is not saved with checkpoints), so that remains a
hypothesis.

Retained for this work: `reports/diag_ac_3seed_deep_{nodueling,dueling,c1}.json` (the
three-arm comparison), `reports/diag_q_spread_{nodueling,dueling}.json` (Q-value
evidence), `reports/diag_order4_bottleneck.json`, and six checkpoints under
`models/checkpoints/` — **gitignored, so they are not in a clone**; re-run
`scripts/compare_agents.py` to regenerate them.

---

## Stack

Playwright · Gymnasium · Stable-Baselines3 · PyTorch · Ollama (GGUF via llama.cpp) · Anthropic API · Docker

Target applications: Gitea, OpenCart, Nextcloud, DVWA, WebGoat — all containerised.

## License

MIT
