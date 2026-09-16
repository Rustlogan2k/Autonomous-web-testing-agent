# Semantic-Aware Web Testing Agent — Project Context

Living reference document. Update this as scope, design decisions, or progress change — it's meant to let anyone (a future session, evaluators) get fully oriented without re-reading the whole conversation history.

Last updated: 2026-09-16.

**Current research checkpoint: Search-vs-Bandit-vs-AC-DQN completed; advisor-approved confound-resolution experiment is next.** The A0/B/C ladder ran over 8 seeds × 25 evaluation episodes and is recorded in §5 (2026-09-16); its A-vs-B/C reading is **confounded by information access** and the corrected interpretation is in that entry, not in the report artefacts. The next milestone is the five-condition **A0/A1/A2/B/C** comparison, whose definitions must be frozen before the fixture is inspected. **A1 and A2 are not implemented and not run.**

---

## 1. What this project is

**Title:** Semantic-Aware Web Testing Agent — Autonomous Security & Functional Bug Detection using LLM and Reinforcement Learning.

**One-line summary:** A system that takes an uploaded website *repository*, automatically understands and deploys it, then sets an RL agent loose on the live app — judged at every step by an LLM that reasons about expected vs. actual behavior — to autonomously discover security vulnerabilities and functional bugs, and produces a comprehensive report with suggested fixes. Existing tools (ZAP, Burp, Cypress) are syntactic/pattern-based; this project's bet is that a shared semantic understanding of the page (visual + structural + network, fused into one vector) plus source-level context lets an agent find bugs that only make sense in context, not just pattern matches.

**B.Tech final year project.** Target GPU: NVIDIA A100/T4, CUDA 12, Python 3.11, PyTorch 2.2+. Every component in §3 is built in this repo — there is no external dependency on anyone else delivering a piece, so the ordering in §9 is a genuine critical path rather than a coordination schedule.

### Why this is novel

Existing tools don't *understand* what a page is trying to do, so they miss bugs that only make sense in context (e.g. "this form claims to update an email but the confirmation shows the old one"). Beyond that, this project's final vision goes one step further than "watch the live app": by first having an LLM read the *source code* of the uploaded repo, the reward model can judge live behavior against actual documented/coded intent, not just infer "expected behavior" from surface signals alone. That turns findings into "the code says X should happen, testing shows Y actually happens" — a code-vs-runtime-behavior consistency check, not just an anomaly detector.

**Positioning against the right comparison set (added 2026-08-02).** Comparing only against ZAP/Burp/Cypress understates the prior art and is the weakest form of the novelty claim. The nearest academic neighbours are (a) **RL-based GUI/web testing** — WebExplor (curiosity-driven RL for web testing) and Q-testing (curiosity-driven RL for Android GUI testing), which already do "RL agent explores an app with a novelty-shaped reward"; and (b) **LLM-driven GUI testing** — GPTDroid, DroidAgent, AXNav, which already do "an LLM decides/judges what the app should do". Both lines must appear in the related-work section and be differentiated explicitly. Verify venues and exact claims directly against the papers before citing; the list here is a starting point, not a bibliography.

The differentiator that survives that comparison is narrower and stronger than "we understand pages": **the reward model is grounded in intent derived from the application's own source code** (§3.0b → §3.4 → §3.5). Neither the RL-testing line nor the LLM-GUI-testing line reads the repo to build an expectation baseline, so "the code defines X, the live test observed Y" is the claim to lead with.

---

## 2. End-to-end pipeline (final vision)

This is the product-level flow, decided 2026-08-01. Everything in §3 (architecture) implements a piece of this.

1. **Upload.** User uploads a website repository (zip or git URL) through the application.
2. **Build-method detection.** System inspects the repo for a `Dockerfile` or `docker-compose.yml`. v1 scope deliberately **requires one of these to exist** — see §6.2 for why generic multi-language build automation is out of scope.
3. **Repo Profiling (LLM).** An LLM-based profiler reads the codebase — via chunked retrieval/summarization, since a whole repo won't fit in one prompt context (see §8) — and produces a structured **Application Profile**: app type/domain, primary user flows, auth mechanism, key routes/endpoints, form validation rules, data models. Persisted as JSON; consumed downstream by both the environment setup and the reward model / report engine.
4. **Auto-deploy.** System builds and runs the app (`docker build` + `docker compose up` or equivalent), waits for the exposed port to become responsive, and captures the resulting `base_url`.
5. **Environment initialization.** `WebFunctionalEnv` is instantiated against `base_url`, optionally with a session-bootstrap macro (e.g. auto-login) derived from the Application Profile if it identifies an auth flow (see §3.1).
6. **Agent B testing loop.** DQN agent explores the live app for N episodes / M total steps, taking actions from the dynamically-built, viewport-aware, budget-truncated action space. Each step, the LLM Reward Model scores the transition using the live before/after state **and** the Application Profile as grounding context, supplemented by deterministic bug triggers.
7. **Bug/inconsistency logging.** Every step whose reward crosses a "notable" threshold (high-severity unexpected judgment, or a deterministic trigger fired) is logged with full context: action sequence, before/after screenshots, network trace, console errors — and, new in this vision, a diff between what the Application Profile says should happen and what was actually observed.
8. **Report generation.** Bug Report Engine aggregates every logged finding into one comprehensive report: type taxonomy, CVSS-inspired severity, human-readable repro steps, evidence, and an LLM-generated suggested fix grounded in both the bug's nature and the relevant source snippet from the Application Profile.
9. **Delivery.** Report presented to the user (and/or exported).

---

## 3. System architecture

### 3.0 Repo Intake — **security policy gate built 2026-08-02; runner and profiler not built** (added 2026-08-01)

Sits upstream of everything else; turns an uploaded repo into a live, understood target.

**(a) Auto-deploy — policy gate built 2026-08-02, runner not built:**
- Detect `Dockerfile` or `docker-compose.yml` in the uploaded repo root (or a shallow search). v1 requires one to exist — no bespoke multi-language build system (see §6.2, §8).
- Run `docker build` (or `docker compose up`) in an isolated context, wait for the exposed port to respond to a health-check request, capture `base_url`.
- **Security-critical, and resource limits alone are not sufficient.** The original plan ("build/run *that*") is a straightforward host compromise, because a `docker-compose.yml` does not merely describe a workload — it specifies that workload's *security context*. An uploaded file can request `privileged: true`, `network_mode: host`, `pid: host`, `cap_add: [SYS_ADMIN]`, or `volumes: ["/:/host"]` / `["/var/run/docker.sock:..."]`, and no CPU/memory quota prevents any of them. The build definition therefore has to be **parsed and rejected before anything is executed**.
- `intake/compose_policy.py` (**built**) is that gate: it blocks every privilege-escalating service key, host-namespace mode, and host bind-mount (named volumes only), parses with `yaml.safe_load` so an untrusted file cannot construct Python objects on parse, and handles the Windows drive-letter mount form (`C:\Users:/host`) that a naive `split(":")` reads as a harmless relative path. It also carries `SANDBOX_RUN_ARGS` — the runtime flags the runner must apply on top of a policy-clean file (`cap_drop: ALL`, `no-new-privileges`, read-only rootfs, pids/mem/cpu limits, internal-only bridge network) — kept beside the policy so the two cannot drift apart.
- **The browser is also a target**, not just the container: Chromium is being pointed at attacker-controlled pages. If Playwright runs in Docker for this, `--no-sandbox` (the usual workaround) must not be used.
- Python `docker` SDK (already in `requirements.txt`) is the natural implementation path for the runner — reuses the same tooling already used for spinning up DVWA/Gitea/etc.

**(b) Repo Profiler (LLM code understanding) — Application Profile *schema and consumer* built 2026-08-09; the extractor is not built:**
- Reads the repo (route/controller files, models/schemas, validation logic, README, existing tests) and produces a structured **Application Profile** — app type, primary flows, auth mechanism, key routes, validation rules, data models.
- **The consumer was built before the producer, deliberately** (`intake/profile.py`, **built**). The two halves answer different questions and only one is on the critical path: *does source-level intent improve judgment?* needs a profile and a judge that reads one, and is testable today against a hand-authored file; *can a profile be extracted from an arbitrary repo?* is weeks of per-framework tree-sitter work whose value depends entirely on the first answer. Building the consumer first means the extractor gets written against a schema already shown to help. `data/profiles/gitea.json` is that hand-authored stand-in, and the A/B it enabled is in §5.
- `provenance` is a required field (`manual` | `profiler`) and is carried into every scoring report. A hand-authored profile and an extracted one produce identical prompts, so without it nothing downstream could distinguish a number obtained from a file a human wrote from one a profiler produced.
- **The profile reaches the judge as a per-window *slice*, not as a whole document** (§3.0b's "relevant slice"), budgeted at 1400 characters and weighted so declared constraints and known-correct behaviour survive ahead of the route list. It is passed as a **separate argument** from the window and is never merged into it — `is_grounded` checks quoted evidence against the window, so a profile folded in would let a judge cite the *specification* as proof the application misbehaved and score as perfectly grounded, silently destroying the one judge-quality metric that works on a target with no answer key.
- **Context strategy — decided 2026-08-02: structure-first extraction, then a long-context model.** Phi-3-mini-4k cannot read a real repo, and a generic embed-and-retrieve RAG pipeline is the wrong fix: it is hard to defend in a viva ("we hoped the retriever found the right chunks") and fails exactly where repos are least uniform. Instead, two stages:
  1. **Static extraction.** Most of the Application Profile is recoverable without an LLM at all — routes from framework conventions (Flask/FastAPI decorators, Express `app.get`, Django `urls.py`, Rails `routes.rb`), models from ORM base-class detection, validation rules from schema definitions (pydantic/zod/serializers). A tree-sitter repo map (the approach `aider` uses) gives the symbol skeleton cheaply and deterministically.
  2. **LLM over the extracted slice only.** Feed the profiler just those files/snippets. That almost always fits in a long context and is far more reliable than chunk retrieval.
- **Model choice:** profiling runs **once per repo with no latency constraint**, so use a long-context code model (e.g. Qwen2.5-Coder-14B/32B-Instruct, 128K) rather than stretching Phi-3-mini. Phi-3 stays the per-step judge, where latency is the binding constraint. Two models for two genuinely different jobs, not a compromise.
- Output feeds two places: (1) environment setup (informs session-bootstrap macros, may inform action-space prioritization/coverage targets), (2) the LLM Reward Model, as grounding context for judging "expected" vs "buggy" (see §3.4).

### 3.1 Browser Environment — **built** (`WebTestingEnv` / `WebFunctionalEnv`)
- Headless Chromium via Playwright (Python), wrapped as a Gymnasium (`gym.Env`) environment.
- `reset()`: fresh browser context (no carried-over cookies/session), navigate to target URL, capture initial state.
- `step(action)`: decode action index → Playwright command → execute → wait for DOM quiescence → adopt any popup the action opened → capture new state → compute reward → return `(obs, reward, terminated, truncated, info)`.
- Episode ends at 200 steps (`truncated`) or when the agent navigates outside the target domain (`terminated`).
- Two subclasses: `WebSecurityEnv` (Agent A, **not built**) and `WebFunctionalEnv` (Agent B, **built**).
- **Viewport-aware action prioritization — gap identified 2026-08-01, fixed 2026-08-02.** The DOM scan now returns `inViewport` alongside CSS visibility, and `build_action_specs` allocates on-screen elements before off-screen ones within each priority bucket. Off-screen elements are still included when budget remains, so nothing becomes unreachable — but a footer full of links can no longer crowd out the form in front of the agent, and `SCROLL` becomes genuinely necessary to reach the rest of a page.
- **Observation space (changed 2026-08-02).** `observation_space` is now `Dict({"screenshot": Box(720,1280,3)})` only. The previous `spaces.Text` fields for html/network/url were not a valid space: Gymnasium's `Text` charset is a finite character set defaulting to 62 alphanumerics, so `Text.contains("<html>…")` is `False`, `check_env` failed, and SB3 cannot consume `Text` in a `Dict` regardless. Page HTML, the network trace, and the URL now travel in `info["page"]`, which is Gymnasium's channel for auxiliary per-step data, and the perception layer reads them from there (§3.2). Verified: `check_observation_space` passes and real observations are `in` the space.
- **Screenshot canonicalization (2026-08-02).** `RESIZE_VIEWPORT` genuinely changes the browser viewport — that is the point of the action, and responsive-layout bugs only appear at 375px — but every capture is now rescaled back to 1280×720 before entering the observation. Previously a mobile-viewport screenshot was `(812, 375, 3)` and violated the declared space from the first resize onward. `VIEWPORT_PRESETS["desktop"]` was also corrected from `(1280, 800)` to `(1280, 720)` so the "restore" action actually restores the starting geometry.
- **Settle wait is DOM-quiescence, not `networkidle` (2026-08-02).** `networkidle` requires 500ms of *zero* network activity, which apps with polling or websockets (Nextcloud, Gitea's notification poller) never reach — every step would pay the full timeout. `wait_settled()` now injects a `MutationObserver` and returns when the DOM has been unmutated for 250ms, or `False` at a 3s ceiling. That boolean *is* the "spinner never goes away" bug signal (§3.3), replacing a threshold that was structurally unreachable.
- **Popup/new-tab handling (2026-08-02).** A `target="_blank"` link or `window.open()` creates a page Playwright does not follow; the env previously kept observing the stale page and reported the correctly-unchanged URL as a broken link. New pages are now adopted as the active page, with console/network listeners rebound and the superseded page closed. Adoption is checked from `context.pages` *after* the settle wait — Playwright's sync API only dispatches the page-created event when it next yields, so an event buffer checked immediately after `click()` returns is reliably empty.
- **Domain check normalization (2026-08-02).** `localhost`/`127.0.0.1` and a leading `www.` are folded together, so a legitimate redirect between those forms no longer ends the episode.
- **Planned addition — session/auth bootstrapping:** an optional scripted setup (e.g. `WebFunctionalEnv(setup_actions=...)` or a login macro, informed by the Repo Profile's identified auth mechanism) so episodes start already authenticated instead of spending exploration budget re-discovering login every episode.
- **Planned addition — training curriculum:** toy validation site (see §3.3) → Gitea (moderate complexity) → OpenCart/Nextcloud (most complex, heaviest DOMs, most dynamic), validating each capability increment before compounding difficulty. Mirrors Agent A's DVWA staged curriculum.

### 3.2 Semantic Understanding Layer (shared perception backbone) — **seam + normalization built 2026-08-02; real CLIP/CodeBERT/MiniLM encoders built 2026-08-10**
Runs every timestep, fuses three modalities into a 256-dim state vector:

- **Visual encoder:** full-page screenshot (1280x720) → CLIP ViT-B/32 → 512-dim (frozen). For complex pages, LLaVA-1.5 7B generates a natural-language description → all-MiniLM-L6-v2 → 768-dim (run async, cached per unique screenshot).
- **Structural encoder:** captured HTML, preprocessed (strip `<style>` and script *bodies* but keep inline event handlers like `onclick`; extract forms with field names/types; extract anchors with href/text) → CodeBERT-base `[CLS]` token → 768-dim (frozen). Note the HTML is **not** raw `page.content()`: since 2026-08-07 the env projects live form state (typed values, checked boxes, selected options) and computed visibility into the serialized markup, because `page.content()` alone left it byte-identical before and after every `TYPE` and every checkbox toggle (§5). Without that projection this encoder could not distinguish an empty form from a completed one.
- **Network encoder:** HTTP request/response pairs since the last timestep (URL, method, headers incl. Authorization/Cookie, body, status, first 512 chars of response) serialized to a structured string → all-MiniLM-L6-v2 → 384-dim (frozen).
- **Fusion MLP:** concat(512+768+384=1664) → `Linear(1664→512) → ReLU → Linear(512→256) → ReLU` → 256-dim state (`perception/fusion.py`, **built**). **This MLP is the only trainable component in the whole perception layer** — CLIP/CodeBERT/sentence-transformers stay frozen throughout RL training.
- Pipeline runs **async** (three concurrent encoding tasks, then sync fusion). Target latency: <500ms/timestep. This is a different concern from the browser env's own synchronicity — see §7.

**Where the encoders live — decided and built 2026-08-02: a `VecEnvWrapper`, not the env.**
The obvious place to encode observations is inside `WebTestingEnv`. That is the wrong place. SB3 parallelizes with `SubprocVecEnv`, forking one process per environment — encoders in the env means N resident copies of CLIP + CodeBERT + MiniLM, each running batch-size-1 forward passes, and N× the VRAM. With the ~8 parallel browsers the training curriculum needs, that is the difference between fitting on one GPU and not.

`perception/vec_wrapper.py` (**built**) sits in the parent process instead: it receives all N observations after they cross back from the subprocesses, encodes each modality in a **single batched forward pass**, and hands SB3 a plain `Box`. One model copy, full GPU utilization, and the raw-dict/`info` plumbing stays confined to the env. `PerceptionEncoder.encode_batch` is the interface the real CLIP/CodeBERT/MiniLM encoders implement; `HashEmbeddingEncoder` is a deterministic, correctly-shaped stand-in (stable content-hash → unit vector, no semantics) so the DQN pipeline, the toy-site A/B harness, and the baseline all run today. Fusion stays *inside* the SB3 policy's features extractor, because it is the trainable piece and must receive gradients from the RL loss — so the wrapper emits the 1664-dim concatenation and `FusionMLP` reduces it to 256.

**State normalization — built 2026-08-02** (`perception/normalization.py`): masks UUIDs, JWTs, ISO/epoch timestamps, long opaque tokens (CSRF/session/API keys), and content-hashed asset names (`app.9f8e7d6c.js`); strips volatile query parameters while *preserving* path ids (`/issues/1` and `/issues/2` are genuinely different states, unlike a rotating token). Also provides `preprocess_for_structural_encoder` — the spec's CodeBERT input prep, dropping `<style>` blocks and script *bodies* while keeping inline event handlers and surfacing forms/anchors explicitly — and `state_fingerprint`, a coarse identity over the canonical URL plus the page's interactive skeleton. The fingerprint is what makes "have I seen this state before?" answerable, so the exploration bonus in §3.3 depends on it: without normalization every revisit looks novel and the bonus degenerates into a constant.

**Auxiliary training signal for the fusion MLP (recommended, not yet wired).** With frozen encoders, the fusion MLP is trained only by TD error — a weak signal for learning a 1664→256 projection. `InverseDynamicsHead` (**built**, `perception/fusion.py`) predicts which action was taken from `(state, next_state)`; training it jointly on transitions the agent already collects forces the representation to retain exactly what distinguishes one action's effect from another's, which is what a bug-detection state vector needs. It reuses the inverse model Agent A's ICM already requires, so the marginal cost is near zero.

### 3.3 Dual RL Agent System
Both agents consume the same 256-dim state and a **100-slot dynamic discrete action space** (max 100 slots; unused = NO-OP; rebuilt per page).

**Agent A — Security (not built, secondary priority, see §6.1):**
- PPO (Stable-Baselines3) + custom ICM (Intrinsic Curiosity Module): forward model predicts next-state embedding from (state, action); inverse model predicts action from (state, next-state); intrinsic reward = L2 prediction error × 0.1.
- Actions: `INJECT_FIELD(field_id, payload_type)`, `SUBMIT_FORM`, `NAVIGATE`, `MANIPULATE_SESSION`, `REPLAY_REQUEST`, `CHANGE_METHOD`, `OBSERVE`.
- Reward: +5 unexpected 5xx, +15 unsanitized reflected input (XSS), +20 DB error text (SQLi), +30 auth bypass (200 with absent/modified token), +25 IDOR, plus LLM semantic reward [-1,+1], ICM intrinsic reward, -0.5/step, -3 repetition penalty.
- Payload library: SecLists + OWASP Testing Guide (200 XSS, 150 SQLi, 50 CSRF, 30 path traversal, 20 command injection).
- PPO hyperparams: lr=3e-4 (linear decay), n_steps=2048, batch_size=64, n_epochs=10, clip_range=0.2, gamma=0.99, gae_lambda=0.95, ent_coef=0.01.
- Curriculum on DVWA (Docker): Stage 1 SQLi-only (100K steps) → Stage 2 +XSS (150K) → Stage 3 +auth/session (150K) → Stage 4 full DVWA (100K) → fine-tune on WebGoat (200K).

**Agent B — Functional (browser env, masked DQN and LLM reward model all built; see §3.4 and §9 item 1):**
- DQN + Prioritized Experience Replay (Stable-Baselines3).
- Actions: `CLICK`, `TYPE(field_id, input_type)`, `SELECT`, `SCROLL`, `RESIZE_VIEWPORT` (375/768/1280px), `RAPID_CLICK` (race-condition probing), `BROWSER_BACK`/`BROWSER_FORWARD`, `REFRESH`.
- Reward driven by an LLM Reward Model (Phi-3 Mini, fine-tuned) queried every step: structured prompt with before/after state description + action + observed events → returns `is_expected` (Yes/No), `severity` (0.0–1.0), one-sentence explanation. **Supplemented** by deterministic bug triggers (implemented — see §5): console errors, unexpected 4xx/5xx, SSIM<0.85 visual diff (design gap, see §7), stuck-page detection, broken navigation.

**Reward rebalance — the spec's `+0.5 if expected` was removed 2026-08-02.** The spec's formula (`+0.5` expected / `+10×severity` unexpected) pays a guaranteed, zero-variance reward for every step where nothing bad happens, which makes *doing nothing* the most reliable source of return. Concretely, with γ=0.99 over a 200-step episode an all-`NO_OP` policy banks ≈ 0.5 × (1−0.99²⁰⁰)/0.01 ≈ **43** discounted reward, against ≈10 for actually finding a bug — and since out-of-range action indices resolve to `NO_OP`, most of the 100-slot space pays that bonus for free. Agent A's spec counterweights its rewards with −0.5/step; Agent B's inherited the bonus without the counterweight. Current composition (`reward/functional_triggers.py`):
  - `expected_reward = 0.0` (was +0.5), `bug_severity_scale = 10.0` (unchanged, matches spec)
  - `step_penalty = −0.05` every step; `failed_action_penalty = −0.1` on a stale selector or timed-out action
  - deterministic bonuses: console error +2, unexpected HTTP error +3, *document*-level HTTP error +2 more, stuck page +1, broken navigation +2
  - **exploration shaping** (`reward/exploration.py`, **built**): `novelty_bonus = +1.0` for reaching a page state unseen this episode, `repetition_penalty = −0.5` per recent repeat of the same semantic action, floored at −3.0
  - progress is now rewarded through *reaching new states*, not through mere survival.
- **HTTP-error baselining (2026-08-02).** Every one of the five training targets 404s on something harmless on every page load (favicon, sourcemap, optional locale bundle). Counting those paid a flat bonus for merely reloading a page, making `REFRESH` an infinite reward pump. `ErrorBaseline` records the errors seen while the episode is still resetting and excludes them from later steps; matching ignores query strings so cache-busted asset URLs don't defeat it.
- **Stuck-page detection is edge-triggered (2026-08-02).** The trigger fires on the *transition* into a non-quiescent page, not on every step while it stays that way. Measured on the toy site before this fix: one runaway `setInterval` (BUG-07) produced four separate "findings", three of them blaming innocent controls (a viewport resize, a working counter button). This is the same class of error as the 2026-08-01 broken-navigation bug — only findable by running the thing.
- **Repetition is keyed on action *semantics*, not action index** — `(action_type, element_id, value category)`. The action space is rebuilt every step, so an index-keyed counter would never detect a genuine repeat. `NO_OP` is excluded (its cost is already the step penalty).
- Served via vLLM, target <200ms latency. **The proposed response cache is unsafe as specified** (flagged 2026-08-02): "cosine similarity >0.95 on (action, before_state, after_state) reuses cached reward" is optimized to hide exactly the bugs being hunted — the buggy page and the correct page differ by one wrong email address and score ~0.99 similar. Use exact hashing over the *normalized* state instead (`state_fingerprint`, §3.2, already built), or drop the cache and rely on batching.
- Prioritized replay: priority = |TD error| + 1e-6.
- DQN hyperparams: lr=1e-4, buffer_size=50000, batch_size=32, target_update_interval=1000, exploration_fraction=0.2 (eps 1.0→0.05), gamma=0.99.
- Training: alternating OpenCart / Gitea / Nextcloud, 50K steps each, 300K total.

**Flat vs. action-conditioned Q-network — now supported by a measurement (2026-08-02):**
The spec's literal design is `state(256) → 100 Q-values`, one scalar per fixed output slot. Two problems, not one:
1. **Non-stationary index semantics.** The action space is rebuilt every step, so an index has no persistent meaning across states (index 47 might be "click login" on one page, "type a boundary value" on the next). Materially harder for value-based RL than the Atari-style framing implies, and it worsens as UI diversity grows.
2. **Exploration waste, now measured.** SB3's DQN has no action masking, so out-of-range indices resolve to `NO_OP`. On the toy validation site the random baseline recorded a **15.0% valid-action rate — 87.5% of steps were no-ops** (`reports/baseline_random.json`). The masked variant on the identical site and seed reached 8 unique states and 6 distinct findings; the unmasked one reached 2 states and 4 findings. Most of the exploration budget is being spent on nothing.

The alternative — `(state, action_embedding) → single Q-value`, scored per available action, where `action_embedding` encodes the action's own semantics (element type, visible text/role, snippet embedding) rather than a bare index — removes both problems at once, at the cost of a custom SB3 policy/feature extractor instead of the default `MultiInputPolicy`. **Decision, now evidenced (2026-08-02):** the flat head was trained and measured (§5, run 5). It learns action *validity* well (84.2% valid actions vs. 15.0% for unmasked random) but loses to **masked** random on every quality metric — 1/3 vs 3/3 seeded bugs, 3 vs 8 states. Because masked random samples only valid slots and the flat head structurally cannot, that gap is a statement about **action masking**, not about RL vs. random.

**Resolved 2026-08-10: problem 2 is fixed, problem 1 is not, and problem 1 turned out not to be the binding one.** `agents/masked_dqn.py` keeps the flat head and masks it — 100% valid actions, 0/200 invalid in a controlled harness (§5) — so the exploration waste is gone without the cost of a custom action-conditioned architecture. Non-stationary index semantics remain: index 47 still means different things on different pages, and the flat head still cannot generalise "this is a Continue link" across states.

That limitation is now moot in practice, because a *deeper* one dominates it. With `HashEmbeddingEncoder` in place of the real encoders, the **observation** cannot express "this is a Continue link" either, so an action-conditioned head would have nothing better to condition on. The deep-flow measurement (§5, 2026-08-10) is the evidence: masking raised valid actions to 100% and mean flow depth by 3×, and no policy still got past stage 1. **Build the perception encoders (§9 item 3) before revisiting the action-conditioned design** — until observations carry semantics, the two architectures are limited by the same thing and any comparison between them measures noise.

**Exploration/coverage incentive — built 2026-08-02** (see the reward rebalance above): episodic novelty bonus plus a windowed repetition penalty. Novelty is *episodic* (reset each episode, as in NGU/RND-style episodic curiosity) rather than persistent, because a persistent bonus decays to zero once the site is covered and stops shaping behaviour, whereas an episodic one keeps rewarding "reach somewhere new from the start state" for the whole run. Persistent counters are still kept, but for reporting coverage metrics.

**Toy validation site with seeded ground-truth bugs — built 2026-08-02** (`tests/fixtures/toy_site/`, 5 pages + `answer_key.json`). **10 seeded bugs** with a machine-readable answer key recording repro steps, expected vs. actual behaviour, severity, and which deterministic triggers *should* fire:

| Class | Bugs |
|---|---|
| Deterministically detectable (3) | BUG-03 404 nav link · BUG-04 uncaught `TypeError` · BUG-07 spinner that never finishes |
| LLM-required (7) | BUG-01 signup silently drops the email · BUG-02 dead "Export data" button · BUG-05 declared-but-unenforced age validation · BUG-06 double-submit under rapid clicks · BUG-08 primary CTA hidden at mobile width · BUG-09 dark-mode setting doesn't persist (write/read key mismatch) · BUG-10 "Back to Widgets" link goes to Home |

The 3/10 split is the point: it is the headroom the LLM reward model has to justify itself, and the baseline any "the LLM adds value" claim must beat. The key also lists explicit **false-positive watch items** (refresh keeping the same URL, the working counter button, the happy-path signup) so precision is measured, not just recall. The archive link is off-screen on load, so reaching it requires `SCROLL` — the site doubles as a check on viewport-aware prioritization.

### 3.4 LLM Reward Model — **offline judge built and measured 2026-08-07; live integration built 2026-08-09**

**Built and validated (§5, 2026-08-06/07).** `judge/` implements the judge end to end offline: K=6 windows over captured traces, a verdict schema whose field order forces analysis before verdict, two prompt styles, and backends for Ollama (local and hosted) and Anthropic behind one `Judge` protocol. Measured on the scripted corpus: **10/10 seeded bugs vs. the deterministic triggers' 3/10, with zero false positives on the known-correct control and every citation quoted verbatim.** Nothing in `judge/` imports the environment or Playwright — the judge is developed against fixed files, so a prompt revision costs a second rather than a browser run.

**Live path — built 2026-08-09** (`reward/llm_judge.py`, `reward/gating.py`). `JudgeRewardModel` keeps a rolling K=6 window, cleared per episode via a default no-op `start_episode()` on the ABC that `reset()` now calls alongside `finding_ledger.start_episode()`. Two things the original sketch got wrong:

- **The seam needed more than an episode-reset signal.** `score(before, action, after)` cannot render the window the judge was measured against: whether the action executed, whether the harness refused it, and whether the document actually changed are invisible in the two observations, and every one of them corresponds to a defect already recorded here (a refusal read as a broken link; a rendered no-change claim contradicting the record). `score()` now takes the `StepContext` the env already assembles, keyword-only and optional so existing implementations are untouched.
- **Live windows are built from the same `StepRecord` type and the same `JudgeWindow.render()` as the offline corpus**, with page bodies held in memory instead of hashed to disk. Every judge number in this document was measured on offline windows; a live path rendering its own variant would make those numbers describe a component no longer in use, and nothing would fail.

**Latency is the binding constraint and is not solved.** Measured at 9-11 s/window hosted. With gating (§9 item 2, built) that is ~6 min of judging per 40-step episode — practical for evaluation and bug-finding, and still two orders of magnitude short of the 30-50K-step training budget §8 plans for. Training against a live judge needs a much faster judge or off-policy relabelling of stored transitions; neither exists. **Treat this as the evaluation path, not the training path.**

A failed judge call returns the neutral signal and is never a finding — a reward model that pays out when it cannot see would teach the agent to break the judge, which is a reward exploit with no code path to fix.

**Model decision (2026-08-08): `gpt-oss:120b` primary, `qwen2.5:7b` as fallback and as a published ablation.** The plan below assumed a fine-tuned Phi-3; measurement replaced it. Both models now reach 10/10 on the scripted corpus, so the decision rests on secondary criteria:

| | `gpt-oss:120b` (hosted) | `qwen2.5:7b` (local) |
|---|---|---|
| total / false positives | 10/10 · **0/7** | 10/10 · 1/7 |
| discrimination · grounded | +44% · 100% | +54% · 100% |
| s/window · VRAM | **6.9** · **0** | 15.8 · ~4.7 GB |

1. **Zero false positives is decisive for the reward path.** The verdict becomes reward, and a DQN found four reward exploits in five runs (§5) — the consumer here is adversarial by construction. A false-positive rate does not degrade gracefully into "slightly noisy reward"; it teaches the agent to seek out whatever produced it, with a fluent justification attached that is far harder to spot than a numeric exploit.
2. **VRAM contention rules local out during training, not by preference but by arithmetic.** The dev card is 6 GB. `qwen2.5:7b` at Q4 is ~4.7 GB, and training additionally needs Chromium plus (once §3.2 lands) CLIP + CodeBERT + MiniLM at ~1–2 GB. The local judge and the perception layer cannot both fit. This applies to *any* local model large enough to do the task, so it is a structural argument rather than one about this model.
3. **Speed compounds**, and a hosted judge runs concurrently with the browser instead of behind it.

`qwen2.5:7b` is kept deliberately, not as a consolation: **"a 7B model on a 6 GB laptop reaches 10/10 where deterministic triggers reach 3/10"** is a stronger claim for the report than the same figure from a 120B, because it shows the result does not depend on frontier scale. It is also genuine insurance — `gpt-oss:120b-cloud` is Ollama-hosted and **its terms, rate limits and availability guarantees are unverified**; check them before depending on it near a deadline.

**This decision is provisional because the benchmark is saturated.** Both models score 10/10, so the toy site can no longer discriminate between judges, and the remaining separation (0/7 vs 1/7 false positives) rests on seven control windows — far too few to be confident in. The choice above is made on secondary evidence. **Re-decide on a real target**, and do not spend further effort on judge selection against the toy site: there is no signal left in it.

Ollama also removes `bitsandbytes` from the critical path entirely — GGUF through llama.cpp, no CUDA extension build on Windows.

**The `:cloud` tag is unversioned, and that was measured the hard way on 2026-08-10 (§5): the same window, the same prompt and byte-identical rendering returned a different verdict weeks apart.** The hosted judge stays — the product argument above is unaffected — but the tag is an instrument that moves, so **no figure may be compared against one captured in a different session**, and every table in the report must be regenerated in a single run with its own baseline. If a dated snapshot of a comparable model becomes available, pin it and this caveat disappears.

**Original fine-tuning plan, now an ablation rather than a dependency:**
- Base: Phi-3 Mini 3.8B (`microsoft/Phi-3-mini-4k-instruct`).
- LoRA fine-tuning: rank=16, alpha=32, target modules `[q_proj, v_proj]`, dropout=0.05.
- Dataset: 2,000 annotated examples — 500 DVWA (manual) + 500 WebGoat (manual) + 500 normal Gitea/Nextcloud/OpenCart usage (labeled non-bug, severity=0) + 500 GPT-4 synthetic edge cases.
- Training: 3 epochs, batch_size=4, lr=2e-4, cosine scheduler.
- Targets: >85% bug/non-bug accuracy, >0.80 Spearman correlation with human severity (held-out 200-example set).
- **Measure prompting before committing to fine-tuning (recommended 2026-08-02).** 1,000 manually annotated examples is a very large cost sitting directly on the critical path, and LoRA on 2K examples has real overfitting risk against a >85% target. Evaluate few-shot prompted Phi-3 (plus one stronger model as a ceiling) on the held-out 200 *first*. If prompting clears the bar, weeks are saved and the fine-tune becomes an ablation rather than a dependency; if it doesn't, the fine-tune now has a measured baseline to be compared against — a better result either way. Annotation cost can also be cut: the deterministic triggers auto-label much of the "normal usage, severity=0" half, and the toy site (§3.3) generates labelled *positives* programmatically, since its ground truth is known.
- Served via vLLM — **doesn't compile natively on Windows** (dev machine), fallback is a HuggingFace `transformers` pipeline locally or Docker/WSL for real serving.
- **New in the final vision — grounded by the Application Profile (§3.0b):** rather than judging "expected vs. actual" purely from live DOM/screenshot/network signals, the reward model is also given the relevant slice of the Application Profile (e.g. "this route's validation logic requires email format X") as context. This directly targets what was identified as the single riskiest part of the whole system — reward judgment quality — by giving it a source-grounded notion of intent instead of one inferred purely from surface behavior. Still needs empirical validation once built: grounding *should* help, but hasn't been tested.

### 3.5 Bug Report Engine — ~~not built yet~~ **built 2026-08-26 (`reporting/report.py`); corrected 2026-08-30**
> The section below is the original design. What shipped covers bug type, CVSS-*inspired* severity, repro steps from the action sequence, and evidence; it does **not** yet include screenshots/HTTP pairs as linked artefacts, an LLM-generated remediation suggestion, or the inconsistency diff. See the 2026-08-30 entry in §5.
On any finding: vulnerability/bug type (taxonomy: XSS, SQLi, CSRF, auth bypass, IDOR, broken flow, UI regression, race condition, etc.), CVSS-inspired severity 0–10 (LLM severity×10), human-readable repro steps from the action sequence, evidence (before/after screenshots, HTTP pairs, console errors), LLM-generated remediation suggestion.
- **New in the final vision — inconsistency detection:** using the Application Profile as a source-level expectation baseline, findings can now include a specific diff — "the code defines X validation/behavior; the live test observed Y" — rather than only "this looked anomalous." Elevates findings from anomaly flags to code-vs-behavior consistency violations, which is a stronger and more differentiated claim than what existing dynamic scanners produce.

---

## 4. Tech stack

| Area | Choice |
|---|---|
| Browser automation | Playwright 1.44+ (installed: 1.61.0), Chromium headless, **sync API** for the env |
| RL | Stable-Baselines3 2.9.0, Gymnasium 1.3.0, Shimmy 2.0.1 |
| Vision | CLIP ViT-B/32, LLaVA-1.5 7B |
| Structural | CodeBERT-base |
| Text/network | all-MiniLM-L6-v2 (sentence-transformers) |
| LLM reward (per-step, fast) | Phi-3 Mini 3.8B |
| Repo profiling (one-shot, needs longer context) | Long-context code model (e.g. Qwen2.5-Coder-14B/32B-Instruct 128K) over a tree-sitter repo map — decided 2026-08-02, see §3.0b |
| Fine-tuning | HuggingFace PEFT (LoRA), Trainer/TRL |
| LLM serving | vLLM (Linux/Docker only — see §9) |
| Deep learning | PyTorch 2.6.0+cu124 |
| Experiment tracking | Weights & Biases |
| Payload library | SecLists |
| Auto-deploy | Docker SDK for Python (`docker` package, already in `requirements.txt`); build definitions gated by `intake/compose_policy.py` |
| Baseline / evaluation | `evaluation/rollout.py` — policy-agnostic harness, `RandomPolicy`, deduplicated findings, JSON reports |
| Training targets | DVWA 1.10, WebGoat 2023.8, OpenCart 4.0, Gitea 1.21, Nextcloud 28 — all Docker |
| Baselines | OWASP ZAP 2.14, Burp Suite Community, Cypress 13+, OWASP Benchmark v1.2 |

Actual installed versions in `venv/` were reconciled with `requirements.txt` on 2026-07-31 (the venv had drifted well past the original conservative ceiling pins — see §6.1).

---

## 5. Progress so far

### 2026-07-31 — project setup fixes
- Fixed `pyproject.toml`'s invalid `build-backend` (`setuptools.backends._legacy:_Backend` → `setuptools.build_meta`) — `pip install -e .` now works.
- Initialized git (`git init`); no commits made — nothing is committed automatically (only commit when explicitly asked).
- Installed the missing Playwright Chromium binary (wasn't downloaded before).
- Reconciled `requirements.txt` ceiling pins with what's actually installed and working (gymnasium, transformers, sentence-transformers, stable-baselines3, numpy were all newer than the original pins allowed; verified the whole stack imports cleanly together with CUDA torch 2.6.0+cu124 before bumping the pins).
- Docker CLI present, daemon not running.

### 2026-08-01 — WebFunctionalEnv (Agent B browser environment), built and verified end-to-end

New modules under `src/web_testing_agent/`:

| File | Purpose |
|---|---|
| `utils/logging.py` | Shared loguru configuration |
| `envs/types.py` | `ActionSpec`, `RawObservation`, `NetworkEvent`, `BugSignals`, `MAX_ACTIONS=100` |
| `envs/input_values.py` | Deterministic TYPE-action payload generation per HTML input type × value category (valid_typical / boundary_min / boundary_max / empty_string / type_mismatch) |
| `envs/network_recorder.py` | Playwright request/response capture per page |
| `envs/action_registry.py` | DOM scan (single JS round-trip) + dynamic, budget-truncated 100-slot action space |
| `envs/browser_session.py` | Playwright lifecycle wrapper (fresh context per episode) |
| `envs/base_env.py` | `WebTestingEnv(gym.Env, ABC)` — shared reset/step orchestration |
| `envs/functional_env.py` | `WebFunctionalEnv` — all 9 action types implemented |
| `reward/base.py` | `FunctionalRewardModel` ABC, `RewardSignal`, `NullRewardModel` default |
| `reward/functional_triggers.py` | Deterministic bug-trigger detection + `compose_reward()` |

**Testing:**
- 53 passing unit tests (`tests/unit/`) covering input-value generation, action-space allocation/budgeting/truncation, and bug-trigger/reward composition — all pure logic, no browser needed.
- A real end-to-end smoke test (`scripts/smoke_test_functional_env.py`) that serves a local fixture site (`tests/fixtures/smoke_site/`) over HTTP, launches actual headless Chromium, and scripts through every action type (TYPE, SELECT, CLICK, checkbox toggle, SCROLL, RESIZE_VIEWPORT, RAPID_CLICK, a deliberately broken link, a JS-error-throwing button, REFRESH, BACK/FORWARD).

**A real bug the live test caught:** the first cut of "broken navigation" detection (`was_navigation_action`) treated *every* CLICK plus REFRESH/BACK/FORWARD as something that should always change the URL — so clicking a checkbox, or just hitting refresh, falsely triggered a "broken navigation" bug signal. Fixed by scoping the check to only CLICKs on links/submit buttons (tracked via `ActionSpec.params["navigational"]`) and dropping REFRESH/BACK/FORWARD from the check entirely (refresh keeping the same URL is *correct*, not a bug). Confirmed fixed by re-running the smoke test.

**Deliberately not built in this pass:** `WebSecurityEnv` (Agent A, out of scope — see §6.1), the semantic perception pipeline (so `WebFunctionalEnv`'s observation is still the raw `{screenshot, html, network, url}` dict, not the fused 256-dim vector), the real Phi-3 LLM reward model (env currently runs on `NullRewardModel` + deterministic triggers only).

### 2026-08-01 (later) — strategic scope discussion, no new code

Walked through: honest confidence assessment of Agent B (§3.3 open architecture question originates here), and the "final vision" reframing (§2) that added Repo Intake (§3.0) as a new upstream component and grounded the reward model / report engine in source-level context (§3.4, §3.5). This file was restructured accordingly. Nothing in §3.0 is implemented yet — it's design-only as of this update.

### 2026-08-02 — code review, correctness fixes, reward rebalance, evaluation harness

A review pass over the 2026-08-01 code found several defects that would have silently corrupted training data, plus a reward-design flaw that would have produced a degenerate policy. All fixed and verified.

**Correctness bugs fixed (all confirmed by running the code, not by reading it):**

| Bug | Effect | Fix |
|---|---|---|
| `_selector_for` positional fallback | `f"{tag} >> nth={index}"` counted within the tag's own match set, but `index` came from the *combined* scan selector. Verified: the 2nd button on a page resolved to a *different* button, the 3rd to no element at all. On any page whose elements lack `id`/`name`, actions silently hit the wrong element — worse than crashing, since bug reports would name the wrong control. | Indexed against the shared `SCAN_SELECTOR` constant |
| `#{id}` selectors | Unescaped; framework ids containing `:`/`.`/`/` (routine in Nextcloud/OpenCart) produce invalid CSS | `[id="…"]` with escaping |
| `RESIZE_VIEWPORT` | Verified: a mobile capture is `(812, 375, 3)` against a declared `(720, 1280, 3)` — every observation after the first resize violated the space | Screenshots rescaled to a canonical 1280×720; `VIEWPORT_PRESETS["desktop"]` corrected to match |
| `observation_space` | `spaces.Text` defaults to a 62-char alphanumeric charset, so `contains("<html>…")` was `False`, `check_env` failed, and SB3 can't consume `Text` regardless | Text modalities moved to `info["page"]`; space is now `Dict({"screenshot": Box})`, verified `contains()` |
| `NetworkRecorder` pending map | Keyed by `id(request)`; CPython reuses ids after GC and nothing held a reference, so metadata could be cross-attributed between unrelated requests. Also never cleaned up. | Keyed by the `Request` object (verified hashable and identity-stable across the request/response events); bounded with eviction |
| `response.text()` | Called on *every* response, downloading and decoding full image/font/JS bundles to slice 512 chars | Gated on `content-type` |
| `slow_response` | Threshold was 5s but the settle wait capped at 3s — structurally unreachable; it only ever meant "the action timed out" | Derived from DOM-quiescence failure instead |
| Popup/`target="_blank"` links | Env kept observing the stale page and reported the correctly-unchanged URL as a broken link | Popups adopted as the active page, listeners rebound |
| Fragment/`javascript:`/`mailto:` links | Counted as navigational, so a `#section` jump was reported as a broken link | Excluded from the navigational check |

**Reward rebalance (§3.3):** removed the spec's `+0.5 if expected`, which made `NO_OP` the highest-value action (≈43 discounted return for doing nothing vs ≈10 for finding a bug); added a step penalty, episodic novelty bonus, windowed repetition penalty, HTTP-error baselining, and edge-triggered stuck-page detection.

**New modules:** `perception/normalization.py`, `perception/fusion.py`, `perception/vec_wrapper.py`, `perception/encoders/base.py`, `reward/exploration.py`, `intake/compose_policy.py`, `evaluation/rollout.py`, `utils/local_server.py`.

**New fixtures/scripts:** `tests/fixtures/toy_site/` (5 pages, 10 seeded bugs, `answer_key.json`), `scripts/run_baseline.py`.

**Testing:** 135 passing unit tests (up from 53), covering normalization/fingerprinting, exploration bookkeeping, the reward rebalance (including executable assertions that doing nothing is not rewarded and that camping on a known-broken element stops paying), compose-policy rejection cases, selector construction, and screenshot canonicalization. The smoke test now *asserts* rather than only printing, including three absence-of-false-positive checks (refresh, fragment link, popup link).

**First empirical results — random-policy baseline on the toy site** (3 episodes × 40 steps, seed 0):

| | full 100-slot space | valid-actions-only |
|---|---|---|
| valid-action rate | **15.0%** | 100% |
| `NO_OP` steps | **87.5%** | 8.3% |
| unique states reached | 2 | 8 |
| distinct findings | 4 | 6 |
| throughput | 2.6 steps/s | 1.4 steps/s |

The masked run found **all 3 deterministically-detectable seeded bugs (BUG-03, BUG-04, BUG-07) with zero false positives**, which is exactly what the answer key predicts is reachable without an LLM judge. The 87.5% no-op figure is direct evidence for the action-conditioned Q-network (§3.3).

**A real bug the baseline run caught:** the first cut of stuck-page detection reported `slow_response` on *every* step after BUG-07's runaway `setInterval` started, producing four separate "findings" that blamed a viewport resize and a working counter button. Made edge-triggered on the transition into a non-quiescent page. Same category as the 2026-08-01 broken-navigation bug: only findable by running the thing.

### 2026-08-02 (later) — first DQN training runs: five attempts, four exploits, one negative result

Five 8,000-step DQN runs on the toy site (`scripts/train_toy.py`, `reports/toy_dqn_comparison*.json`). **Every run was won by an exploit rather than by learning**, until the exploits were closed — at which point RL lost to random exploration. All four exploits were defects in this implementation, and all four were found by the optimizer rather than by review or the (then 139-strong) test suite.

| # | What the agent learned to do | Root cause | Fix |
|---|---|---|---|
| 1 | Click the 404 link once, then `REFRESH` it 117× for +3.95/step (episode return +174.50, 65× the random baseline, from 2 states and 1 bug) | A 404 *document* stacks console+http+document_http = **+7.0/step** against a repetition penalty floored at **−3.0**. Triggers paid out on every step their condition held; the harness deduplicated findings for *reporting* but the reward never deduplicated for *learning*. | `FindingLedger` — each distinct `(trigger, state, element)` pays once per episode. Episodic, not permanent, so the association stays learnable. Same episode replayed: **+174.50 → −100.50** |
| 2 | 100% `NO_OP` — do literally nothing | `NO_OP` was **exempted** from the repetition penalty (to avoid "double-charging"), making it the only action in the space immune to a −3.0 penalty. Once novelty ran out, passivity strictly dominated. | Count `NO_OP` like any other action; rescale the penalty to −0.15/floor −1.0 now that `FindingLedger` owns anti-farming |
| 3 | Learn, then collapse: peak **+10.10** at step 2177 (3.4× baseline) → **−32.50** by step 7871 | Reward is history-dependent (novelty bonus, finding ledger) but the observation was not — the same `(page, action)` pays +7 early and ~0 later with nothing in the state to distinguish them. Non-Markov, so the Q-target is partly noise. | `EPISODE_CONTEXT_DIM=6` features appended to the observation **after** fusion (so the 1664→256 bottleneck can't discard them); plus `KeepBestByTrainingReturn` so an unstable run isn't evaluated at its worst point |
| 4 | Fire `BROWSER_BACK` on step 1 of every episode (`ep_len_mean` → **1.05**) | A fresh Playwright context starts on `about:blank`, so the opening `goto()` leaves it in history. Going back landed there; empty netloc read as "left the target domain"; episode over. With per-step rewards mostly negative, ending immediately capped the loss. | `BrowserSession.history_move()` refuses to leave the app; `_left_target_domain` never terminates on non-http(s) URLs. **Also protects Gitea/Nextcloud**, which are full of download links and `blob:`/`data:` URLs |

**Run 5 — the clean measurement** (3 episodes × 40 steps, all policies scored in-process under the identical reward, same seed):

| metric | DQN (best) | DQN (final) | Random (full) | **Random (masked)** |
|---|---|---|---|---|
| mean episode reward | −17.42 | −16.45 | −30.02 | **+2.68** |
| **seeded bugs found** | 1/3 | 1/3 | 1/3 | **3/3** |
| distinct findings | 1 | 4 | 4 | **6** |
| unique states | 3 | 3 | 2 | **8** |
| valid-action rate | 84.2% | 45.8% | 15.0% | 100.0% |
| NO_OP steps | 15.8% | 54.2% | 87.5% | 8.3% |

**Three conclusions:**

1. **DQN learns action *validity*, not *exploration*.** Against the like-for-like unmasked baseline it wins clearly (−17.42 vs −30.02; 84.2% vs 15.0% valid actions). Against masked random it loses on every quality metric.
2. **The reward is not a good proxy for the objective — measured, not argued.** The best-by-reward checkpoint found *fewer* bugs (1 finding) than the final, lower-reward one (4 findings). Optimizing this reward harder does not find more bugs. After five rounds of hand-tuning, this is the strongest available argument for source-grounded LLM judgment, and it does not depend on any prior reasoning about reward design.
3. **The comparison is not a fair fight, and that is the useful part.** Masked random samples only valid slots; the flat 100-slot head structurally *cannot* (SB3's DQN has no action masking). So "masked random wins" is a statement about **action masking**, not about RL vs. random — and the case for the action-conditioned Q-network (§3.3) now rests on a measured 3/3-vs-1/3 gap rather than on the no-op rate.

**What this does not show:** that RL is useless here. The toy site is 5 shallow pages where nearly every bug is reachable in 1–2 steps from the landing page — precisely the regime where random exploration is strongest and sequential credit assignment is worth nothing. RL's actual claim (chaining multi-step flows) is **untested**; see §9.

> **Superseded twice.** *2026-08-26:* run 5's own numbers are replaced by the 3-seed rerun — unmasked DQN 0/3 [0-1] against masked random 3/3 [2-3], states 6 against 15. The 1/3 above was seed 0, where unmasked random scored identically, so it never measured the DQN. *2026-08-17:* That claim is no longer untested: the deep-flow fixture tested it over 3 seeds and masked random beat the DQN ~10× on distinct findings there too. See the 2026-08-17 entry. Note also that run 5's own numbers carry the single-seed evaluation defect described in the 2026-08-15 entry and are pending a rerun (§9 item 2b).

**Instrumentation lesson:** run 2 shipped with `verbose=0` and no `Monitor` wrapper, so there was no `ep_rew_mean` curve — "learned the wrong thing" was indistinguishable from "learned nothing". Run 3's curve is what exposed the peak-then-collapse. Always wrap training envs in `Monitor`.

**Dev-hardware constraint discovered the same day:** the dev GPU is an **RTX 4050 Laptop, 6 GB VRAM**. Phi-3.5-mini needs 4-bit (~2.3 GB) to fit — which puts the known-problematic Windows `bitsandbytes` dependency on the critical path — and at ~5s/judgment, per-step local LLM judging is not viable at any meaningful step budget (~11h of judging alone for 8K steps). Implication: **gate judge calls behind a cheap filter** rather than judging every step. The filter cannot be "the state changed" (a dead button changes nothing — that *is* BUG-02); it must be "an action that should have changed state was taken". Worth designing deliberately (§9).

**Known limitation:** `gymnasium.utils.env_checker.check_env` still fails `check_step_determinism` — identical actions produce different `load_duration_s`/network timings. That is inherent to driving a live browser, not a defect. The observation/action space checks it previously failed now pass.

### 2026-08-06/08 — trace corpus, offline LLM judge, and the core result: 3/10 → 10/10

The load-bearing measurement now exists. Built in order: trace capture, the judge, the scoring harness; then four judge backends were measured against the same fixed corpus.

**New components** (all under `src/web_testing_agent/`):

| Module | What it does |
|---|---|
| `annotation/trace.py` | `TraceRecorder` — content-addressed corpus (JSONL + deduplicated page blobs + optional PNGs). Hooks `run_rollout` via an optional `recorder=`; no env or policy code knows it exists. |
| `evaluation/scripted.py` | `ScriptedPolicy` — walks answer-key repro paths, matches on DOM id, reports unmatched steps loudly. |
| `judge/window.py` | K=6 windows. Pages reduced to a behavioural surface and rendered as **diffs**, not dumps. |
| `judge/prompt.py` | Two styles: `detailed` (frontier models) and `compact` + few-shot (small local models). |
| `judge/verdict.py` | Output schema and tolerant parser. |
| `judge/ollama.py`, `judge/client.py` | Ollama (local **and** hosted) and Anthropic backends behind one `Judge` protocol. |
| `judge/scoring.py` | Recall, false positives, **discrimination**, **evidence grounding**. |

Scripts: `capture_traces.py`, `score_judge.py`. Tests: 149 → **317**.

**The corpus** (`data/annotations/toy-{scripted,random}`) is two independent sets, because they answer different questions: **scripted** is one episode per seeded bug plus a known-correct `happy_path` control, so judge *accuracy* is measurable; **random** is 160 steps of masked-random exploration with no ground truth, which measures how often the judge speaks up on ordinary traffic. Capturing once means a prompt change costs a second instead of a browser run.

**Headline result — `gpt-oss:120b`, 39 windows, 0 failed calls:**

| | deterministic triggers | LLM judge |
|---|---|---|
| semantic bugs (`llm_required`) | **0 / 7** | **7 / 7** |
| deterministic bugs | 3 / 3 | 3 / 3 |
| **total** | **3 / 10** | **10 / 10** |
| false positives on the control | 0 | **0** |
| evidence quoted verbatim | n/a | **14 / 14** |

14 positives across 39 windows — selective, not trigger-happy, and every one lands on the right episode with a sensible bug type. **This is the claim the project rests on, and it holds.**

**What the window contains matters more than which model reads it — measured 2026-08-08.** Profiling the prompt showed `text gone` was 24% of all window characters and `text added` another 14%, the two largest items by a wide margin. On a *navigation* step both are tautological: replacing the document removes all the old text and introduces all the new text by definition, and the previous page was already shown one step earlier. Suppressing `text gone` across navigations, relabelling `text added` as `page text` there, dropping form fields that went `-> absent` only because the page was left, and capping text diffs at 12 lines cut the mean window 1680 → 1317 characters (−22%).

The same change improved **both** models on every axis at once:

| run | semantic | total | FPs | discrimination | prompt tokens | s/window |
|---|---|---|---|---|---|---|
| `gpt-oss:120b` verbose | 6/7 | 9/10 | 0/7 | +38% | 58,251 | 8.1 |
| **`gpt-oss:120b` lean** | **7/7** | **10/10** | **0/7** | **+44%** | 54,939 | **6.9** |
| `qwen2.5:7b` verbose | 7/7 | 10/10 | 2/7 | +31% | 63,400 | 11.4 |
| **`qwen2.5:7b` lean** | **7/7** | **10/10** | **1/7** | **+54%** | 55,517 | 15.8 |

Two things follow, and the second corrects an earlier conclusion in this document.

1. **BUG-08 was fixed by a change that was not aimed at it.** Tuning had been deliberately stopped with BUG-08 outstanding, specifically to avoid overfitting. Removing tautological content — a general change, made for cost — let `gpt-oss` complete the inference it had been failing: "hidden with no indication of an alternative presentation" (confidence 0.95), where previously it reasoned "a responsive design may hide desktop-only elements on mobile" and returned `none`. A general fix landing a case that was explicitly left alone is the opposite of overfitting, and is much better evidence than tuning for it would have been.
2. **The 7B model's precision ceiling was partly prompt noise, not pure capacity.** Four prompt variants had failed to move its false-positive rate off exactly 2/7, and that was recorded here as a capacity limit. All four had changed the *instructions* while leaving the *window* untouched; cutting the window moved it to 1/7 immediately, and discrimination to +54% — the highest of any run, above `gpt-oss`'s +44%. The remaining false positive is the genuinely borderline one (an in-memory counter resetting on refresh); the model's own reasoning quotes the exception back before flagging it anyway. **Spend effort on the observation before the instructions.**

**Recall alone is worthless, and two models proved it.** `llama3:8b` and `qwen2.5:7b` both scored 7/7 semantic; neither is usable. Two metrics were added because of this:

- **Discrimination** = positive rate on buggy windows − positive rate on known-correct ones. `llama3:8b` reached 7/7 at 88%/71%, i.e. +17%: no signal at all.
- **Evidence grounding** = share of positives whose quoted evidence actually occurs in the window. Objective, **independent of the answer key**, and therefore the only judge-quality metric that transfers to a real target. `qwen2.5:7b` scored 7/7 while fabricating 68% of its citations — including, at confidence 1.0, "after scrolling, the counter value is reset to 0", which `SCROLL` cannot do and no line in the window said.

**Four judge backends, same corpus:**

| judge | semantic | FP rate | discrimination | evidence grounded |
|---|---|---|---|---|
| stub (floor) | 0/7 | 0% | +0% | n/a |
| `llama3:8b` | 7/7 | 71% | +16% | 82% |
| `qwen2.5:7b` (detailed prompt) | 7/7 | 29% | +43% | **32%** |
| `qwen2.5:7b` (compact + few-shot + lean windows) | 7/7 | **14%** | **+54%** | **100%** |
| **`gpt-oss:120b`** | **7/7** | **0%** | **+44%** | **100%** |

**Three defects in the judge, all self-inflicted, all found by running it:**

1. **Schema field order is the reasoning order.** Constrained decoding emits properties in declaration order, and `is_bug` was first — so the verdict was committed before any analysis existed. `llama3:8b` wrote `actual` = "NO OBSERVABLE CHANGE — the page is byte-identical", typed it `dead_control`, and returned `is_bug=false` on 38 of 39 windows. Verdict now comes **last**, after `observed` → `expected` → `discrepancy` → `evidence`. Same model, same corpus: 0/7 → 7/7.
2. **The prompt explicitly excused the easiest bug.** "A button that legitimately has no visible effect… only updates state you cannot observe here" was listed under correct behaviours. That is BUG-02 verbatim.
3. **The renderer showed deltas but never inventories.** "X became hidden" cannot distinguish a hamburger-menu swap from removing the last route to a flow. Hiding is only a defect relative to what survives, so a hide now also lists the still-reachable links.

**Prompt engineering on `qwen2.5:7b`: one clean win, one hard ceiling.** A shorter prompt plus three few-shot turns *as real conversation* (set in a different app — invoices — so nothing about the fixture leaks in) took evidence grounding **32% → 100%**. Fabrication was mechanical and prompting fixed it completely. Precision was not: the false-positive rate is **exactly 2/7 across all four prompt variants**. Every rule added traded one failure for another — an explicit "NO OBSERVABLE CHANGE doesn't apply to SCROLL" rule worked, then produced a new false positive on the *working* counter button and lost BUG-10 outright ("Clicking '← Back to Widgets' navigated back to /index.html… the navigation worked as expected"). Additions displace attention rather than accumulate. **Prompting fixed the failure with a mechanical cause and could not touch the one that needed judgment.**

**Two environment defects, both foundational, both found by capture:**

1. **The env could not perceive form state at all.** `page.content()` serializes DOM *attributes*, but typing and checking write *properties*, so captured HTML was byte-identical before and after every `TYPE` and every checkbox toggle. Consequences: BUG-09 was invisible (its entire evidence is checkbox state); `state_fingerprint` treated an empty and a completed form as one state, so **progressing through a flow earned no novelty reward** — a silent disincentive against exactly the multi-step behaviour RL is meant to justify; and the structural encoder could not tell them apart either. Fixed by projecting live properties onto a **clone** (never the live DOM — writing them back would fire the application's own MutationObservers), with passwords excluded since observations reach disk and an LLM. The same pass marks elements that are present but not rendered (`data-hidden`), which is the only way a text view can see a responsive-layout regression.
2. **Native HTML5 validation blocked the signup submit**, so BUG-01, BUG-05 and BUG-06 — three of the seven semantic bugs — were **unreproducible**, and the answer key's "the submit handler never validates" was contradicted by the browser validating on the application's behalf. Fixed with `novalidate`.

**Two fixture defects the judge exposed:**

1. **The site leaked the answer.** Its own copy read "a deliberately small app with a known set of seeded functional bugs. See answer_key.json for the ground truth" — present in every rendered window. Moved to HTML comments; the site now reads as an ordinary app ("Acme Widgets"). 12 parametrized tests now fail if any such term reaches rendered text, while asserting the `BUG-0x` annotations survive as comments.
2. **BUG-08 was not a bug.** It hid `#cta-signup` while `#nav-signup` pointed at the same page, so signup stayed perfectly reachable. `gpt-oss:120b` declined to report it — "a responsive design may hide desktop-only elements on mobile" — and was right; the answer key was wrong. Now hides every route.

**A metric was voided.** `unique_states` in the run-5 table measured the env's blindness to form state, not the policies: same policy, same seed, 8 → 22 after the fix. Reward comparisons are unaffected. ~~**Rerun `train_toy.py` before citing any state count from §5.**~~ **Done 2026-08-26:** the whole run-5 table is superseded by the 3-seed rerun; cite that instead of anything here.

**Per-bug tuning was stopped deliberately, and that decision paid off.** After three changes made *after* seeing results on the same 10-bug key, a fourth aimed at the single remaining miss (BUG-08) would have been overfitting however defensible each step looked individually — 6/7 with a documented, well-reasoned miss beats 7/7 bought with four rounds of tuning against the ground truth. BUG-08 was then closed anyway by the lean-window trim, a change made for cost and not aimed at it. That is a materially better outcome: a general improvement landing a case that was explicitly left alone is evidence the judge is reading the window rather than the answer key. The random corpus and a real target remain the actual checks.

**Throughput, measured:** `gpt-oss:120b` 6.9 s/window, `qwen2.5:7b` 11–18 s/window locally. A 40-step episode therefore costs ~7–12 minutes of judging. The gating problem (§9 item 2) is now quantified rather than estimated.

### 2026-08-08 — the random corpus earns its keep: three defects the scripted set could not reach

Scoring the 150-window masked-random corpus (`gpt-oss:120b`, 148 windows after idle filtering, 0 failed calls) returned **43 positives, 29.1%** — against 0% on the scripted control. The breakdown, not the headline, is the result:

| action | windows | flagged | rate |
|---|---|---|---|
| **TYPE** | 39 | **21** | **54%** |
| RAPID_CLICK | 8 | 7 | 88% |
| CLICK | 34 | 9 | 26% |
| SCROLL | 17 | 0 | 0% |
| REFRESH | 4 | 0 | 0% |

All 21 TYPE positives were `dead_control`. That single class dominates the rate, and it is a defect in this project's own code rather than a judgment failure.

**1. The renderer asserted byte-identity it had never checked — the serious one.** `render_step` emitted `NO OBSERVABLE CHANGE — the page is byte-identical after this action` whenever the *rendered view* produced no diff, not when the document was actually unchanged. Typing into an already-filled field alters `value` but not occupancy (`filled → filled`), so nothing rendered, and the window then asserted byte-identity while `record.changed["html"]` was `True`. **A rendering gap was manufacturing the strongest bug signal in the entire prompt**, and the judge's confident verdicts were sound inferences from a false premise it had been handed. Two fixes: value replacements now render (`field value: q: 'old' -> 'new'`), and the no-change claim is made only against the hash comparison — when the HTML changed but nothing summarizes it, the window says exactly that instead.

**2. The dead-control rule was scoped to clicks in only one of the two prompts.** The compact prompt had been given "NO OBSERVABLE CHANGE applies to CLICK/RAPID_CLICK, not SCROLL/TYPE/…" during the qwen work; the detailed prompt — the one this run used — never received it, and duly generalized "no change means dead control" across every action type. The two prompt styles had silently diverged on a rule that matters.

**3. A literal control character inside a regex, invisible to inspection.** A shell heredoc converted `\b` into a `0x08` backspace byte in `_field_values`, so the pattern matched a control character followed by `name=` — which occurs in no document — and the extractor silently returned `{}`. Invisible in the editor, in a file read, and in a diff; found only via `cat -A`. A test now scans `src/` and `scripts/` for stray control characters, because this failure mode cannot be caught by reading.

**The methodological point is the durable one.** The scripted corpus is 82% buggy by construction with seven control windows, and it could not have surfaced any of these: its TYPE actions always changed occupancy, so the byte-identity bug never fired. **One run against unlabelled, held-out traffic found three defects.** This is the concrete argument for §8's overfitting risk, and for treating the random corpus (and later a real target) as the actual check rather than the scripted number.

### 2026-08-08 (later) — first contact with Gitea, and a validated rendering pipeline

The full pipeline ran against a real application for the first time: `docker compose up gitea` → `scripts/setup_gitea.py` → capture → judge. **13/13 probed routes are reachable anonymously**, so session bootstrap (§9 item 10) is not the blocker it was assumed to be.

**New component — `judge/validate.py`.** Eleven invariants over `(record, rendered window)`, each derived from a defect that actually occurred, run in `--dry-run` and as a preflight that *refuses to score* when a window contradicts its own record. Rules: `false-no-change`, `block-as-failure`, `unmarked-block`, `block-as-no-change`, `offsite-console`, `silent-elision`, `raw-tool-output`, `overlong-item`, `control-characters`, `over-context`, plus corpus-level dedup per (step, rule).

The motivation is the feedback loop, not the rules. Every judge defect so far had one shape — the window asserted something the record did not support, the judge reasoned correctly from it, and the verdict looked like a model failure — and each cost a 15-minute scoring run plus an hour of reading verdicts to find something decidable offline in milliseconds. It now takes ~2 seconds.

**Defects found on first contact, all in this project's code:**

1. **Off-site links terminated episodes.** Gitea's footer points at github.com and docs.gitea.com; the explorer reached one within a few steps and three 40-step episodes produced **22 steps total**. Now refused and undone (`BrowserSession.restore`, which prefers `go_back` — a `goto` would append a history entry and leave the off-site page one BROWSER_BACK away). Termination now means the restore itself failed. **This was reward-hacking exploit #4 in a new costume**: an early exit is valuable to a policy accumulating negative reward, and on a real application an off-site link is always one click away. 22 → 120 steps.
2. **The trace recorder whitelisted `success`/`error`,** silently dropping `left_application` — so the fix in (1) could never reach the judge. Now preserves every JSON-safe key, because a recorder that discards what it does not recognize will keep doing this each time the env learns to report something new.
3. **A harness refusal rendered as `FAILED: ... did not navigate`,** which the judge read as a broken link: 6 of 11 refusals became `broken_navigation`. Now a distinct `BLOCKED` line stating it is a test-setup restriction, with a matching rule in both prompts.
4. **`BLOCKED` and `NO OBSERVABLE CHANGE` were emitted together.** A blocked step ends where it started *because the harness put it back*; stating the strongest bug signal in the prompt about an action that never ran produced most of the remaining `dead_control`/`broken_navigation` verdicts.
5. **Off-site console errors were attributed to Gitea.** The external page runs its own scripts before the restore lands, and everything it logs stays in the buffer. **Five confident `js_error` verdicts came from someone else's website** — Gitea's Docusaurus docs site, `cloudflareinsights.com`, `hscollectedforms.net`. Console, page-error and network buffers are now cleared after a refusal.
6. **Framework ids, tool output and unmarked truncation.** Twenty `_aria_auto_id_N` entries filled the hidden-element budget on every page (collapsed to `a (unnamed) x20`); Playwright's call log was **78% of the largest window**; console messages ran to 5,937 characters; URLs, element labels and Swagger endpoint names were cut without a marker. All elided explicitly, with element labels marked at the source in `_SCAN_JS`.
7. **`page_view` re-parsed each page once per window it appeared in.** Harmless on a 3 KB fixture, dominant on Gitea's 40 KB pages: validating 110 windows took **163 seconds**. Pages are content-addressed, so caching is exact — **2.2 seconds** after an `lru_cache`.

**Measured effect:** mean Gitea window 5,815 → 3,292 characters; largest 14,679 → 6,971; all three corpora now validate clean.

**The result, honestly stated.** Gitea produced 18 positives on 110 windows (16.4%), against 16.2% on the toy site's random corpus — the rate generalizes. But a manual read of all 18 found roughly **4–5 genuine** (Swagger's `Authorize` accepting an empty `required` field, an `APIError` shown instead of a models view); the rest trace to defects (4) and (5) above. The predicted failure mode — anonymous users hitting 403s read as broken flows — did **not** materialize: 1 of 20 positives touched auth at all.

**The methodological finding, which is the durable one.** *No metric in this project would have caught either defect.* Recall, discrimination and evidence-grounding were all satisfied: the citations were verbatim, the reasoning sound, the premises wrong. A validator can check that a window does not contradict its record; it cannot check that the record describes **the application** rather than **the harness acting on the application**. That distinction is where both defects lived, and only a manual read of unlabelled traffic surfaced it. Budget for that read after every first contact with a new target.

### 2026-08-09 — three more harness-as-application defects, the live reward loop, and the first source-grounding measurement

Started as three tasks: recapture Gitea after the off-site buffer fix (item 5), test source grounding cheaply (§3.0b), and wire the judge into the live loop (items 1e and 2). All three landed. The recapture is the part worth reading first, because it did not go as expected.

**The 16.4% Gitea positive rate was almost entirely this project's own defects, and it took three rounds to find them all.** Each round was a recapture that was supposed to be the clean one.

| # | Defect | How it presented | Fix |
|---|---|---|---|
| 1 | **Off-site console errors survive the buffer clear.** The clear on a refused navigation is a *timing* fix, and it loses a routine race: a third-party beacon the foreign document requested resolves a moment later and lands on the next step, by which point the browser is back inside the app. | Two `static.cloudflareinsights.com` CSP errors from a `code.gitea.io` page attributed to Gitea one step after the refusal — in the recapture meant to prove the clear sufficient. | Filter console and page events on the **origin that emitted them**, not on when they arrived. Provenance closes the race outright; timing cannot. |
| 2 | **A `target="_blank"` click that opened no visible change was rendered as byte-identical.** Popup adoption is racy — `wait_settled()` returns once the *current* page stops mutating, which after such a click is almost immediately, sometimes before Chromium has created the tab. | Clicks on `code.gitea.io/gitea`, `packaged`, `run the binary` and `Powered by Gitea` each drew a **90–100%-confidence `broken_navigation`** verdict. None of those links is broken. | Record `opens_new_tab`/`href` on the action, and render an explicit `NEW TAB` line instead of the no-change claim. Rule added to **both** prompt styles and a `validate.py` invariant. |
| 3 | **A tab opened at step *N* was adopted at step *N+k* and blamed on whatever action was running.** The same race, one layer down: the popup appears in `context.pages` late, and nothing distinguished it from one the current action had just opened. | A click on the **internal** link `/user/login?redirect_to=%2f` and a click on the `remember` **checkbox** were both recorded as `left_application: https://github.com/go-gitea/gitea`. A checkbox cannot navigate anywhere. The harness then "restored" a page it had never left, and the reload **wiped the typed form values** — reported by the judge, at 90–95% confidence, as the application discarding user input. | `mark_pages()` before each action; adopt only tabs that appeared during it; close stale ones; discard (never adopt-then-refuse) an off-site tab and report it as `opened_offsite_tab`. |

**Measured effect on the ungrounded false-positive rate: 16.4% (18/110) → 5.6% (6/109) after defect 2 alone.** Defect 3 was then found by reading those 6: three of them were the form-clearing artifact and one was a `broken_navigation` on a correct link, which is the fourth time a *harness* action has been reported as *application* behaviour.

**This is the same finding as 2026-08-08's, and it is now a pattern rather than an incident.** Every false positive on a real target so far has had one shape: the window stated something true about the harness as though it were true about the application, the judge reasoned correctly from it, and the verdict looked like a model failure. Recall, discrimination and evidence grounding were all satisfied every time. The count is now seven such defects (four on 08-08, three here), and none was found by a metric — 08-08's came from a manual read, these from reading six verdicts and disbelieving them. **The judge is not the component that needs attention on a new target; the record is.**

A fourth, smaller one: `validate.py` carried its own copy of the renderer's elision threshold (55 against the renderer's 60), so any value 55–60 characters long — displayed in full, exactly as intended — was reported as a silent elision. Gitea's Swagger page produced one on the complete 57-character label `GET /version Returns the version of the Gitea application`. The validator now imports the constant, because a validator with its own copy of a number checks a rule the renderer is not following.

**New components.**

| Module | What it does |
|---|---|
| `intake/profile.py` | `ApplicationProfile` — the §3.0b schema, per-window slicing by route, weighted budgeting, required `provenance`. Built before the extractor on purpose (§3.0b). |
| `data/profiles/gitea.json` | Hand-authored Gitea profile: 20 routes, 8 declared constraints, 4 flows, 6 known-correct behaviours, every entry citing either the running container or the Gitea source file that declares it. |
| `reward/gating.py` | The judge-call gate (§9 item 2). |
| `reward/llm_judge.py` | `JudgeRewardModel` — the offline judge in the live reward loop (§9 item 1e). |
| `scripts/measure_gate.py` | Replays the gate over captured corpora *and scored verdicts*, reporting saving **and** loss. |
| `scripts/run_judged.py` | Live browser → env → gate → judge → reward, against a real target. |

**The gate, and the case that shows why it had to be measured rather than reasoned about.** The rule is "was an action taken that *should* have changed something", never "did the state change" — a control that promises an effect and produces none is a dead control, the class the judge is most needed for. Replayed against the scored corpora it skips **38–45% of windows** for a saving of ~6 minutes per 40-step episode.

The first version also skipped any action Playwright reported as failed. Replaying it against the toy scripted corpus showed it would have discarded a **95%-confidence `broken_flow`** — BUG-01. A `RAPID_CLICK` navigates on its first click, so clicks 2–5 time out against a detached element and the burst is recorded `success: False`; that step had already reached `/confirm.html` and carried BUG-01's entire evidence in the URL (`?username=…&age=…`, no `email`). "The action failed" and "the action had no effect" are different claims, and Playwright routinely reports the first when the second is false. With the ordering corrected — *the page changed* is checked before any failure rule — the gate loses **0 of 14** positives on the toy scripted corpus and **0 of 24** on the toy random corpus while still skipping 38%.

**A gate that saves calls without a loss measurement is not a result.** The saving took ten minutes to compute and the loss measurement is what made it trustworthy; `measure_gate.py` refuses to report the first without noting the absence of the second.

**Tests: 317 → 465** (`tests/unit` + `tests/integration`), covering the profile schema and its budgeting, the gate (including the RAPID_CLICK regression above), the live judge path, foreign-origin console filtering, new-tab rendering, stale-tab attribution, and password occupancy.

**Repo hygiene, because it was one bad sync away from mattering.** The `.gitignore` was inverted — it dropped the corpus index files a corpus is unusable without, while committing 13 MB of page blobs; and it ignored `models/checkpoints/*.pt` while SB3 actually writes `*.zip`, leaving 31 MB of stale toy-DQN checkpoints in the tree unnoticed. Rewritten with the policy and its rationale in §10: working tree 46 MB → **3.1 MB**. Still **zero commits** as of this entry.

#### The first source-grounding measurement — modest, positive, and not the claim the project needs

Both arms, same judge (`gpt-oss:120b`), same corpus (`gitea5-random`, 110 windows), same seed; the only difference is whether each window carried its Application Profile slice.

| | positives | rate | + gate | evidence grounded |
|---|---|---|---|---|
| ungrounded | 9 | 8.2% | **3** | 100% |
| **grounded** (hand-authored profile) | **4** | **3.6%** | **2** | 100% |

Restricted to the 102 windows where both calls succeeded, the profile **suppressed 6** verdicts and **added 2**. Reading all eight by hand:

- **All six suppressions are correct.** Three were clicks on Swagger endpoints that *timed out* — explorer failures the prompt already says are not bugs, reported as `broken_navigation` anyway. Two were TYPEs into a password field (see below). One was a `RAPID_CLICK` on `Sign In` with an empty form, which correctly does nothing.
- **Both additions are false positives.** One is another timed-out click, called `dead_control`; the other is Swagger's `Authorize` dialog closing and removing its own fields, called `ui_regression`. Grounding made these worse, not better.

**So the profile roughly halves the false-positive rate and buys no new true positives.** That is a real effect and a modest one, and it is emphatically *not* evidence for the §1 novelty claim. What was tested is whether a **hand-authored** description of an app helps a judge; the claim that matters is whether intent **extracted from source** does, and that still cannot be tested until the extractor exists. The mechanism that did the work here was mostly the `known-correct behaviour` section — the false-positive suppressor — rather than the `declared constraints` section that would carry "the code defines X, testing observed Y". **Do not report this as source grounding.** What it does establish is that the plumbing works end to end and that the profile is worth extracting; what it does *not* establish is the differentiator.

**The gate turns out to matter more than the profile, and for a reason worth stating.** It removes 6 of the 9 ungrounded positives and 2 of the 4 grounded ones — and every one of those is a false positive by manual read. The dominant surviving class in both arms is *the click timed out, so nothing changed, so the control is dead*, which both prompts explicitly forbid. Four prompt revisions across two months never fixed that; a filter that simply declines to ask the question does. **Where a rule is mechanically decidable, decide it in code and do not spend prompt attention on it.**

**An eighth harness-as-application defect, found in those verdicts.** Password values are deliberately never projected into the serialized markup (observations reach disk and a model). But withholding them *silently* left the markup byte-identical across every TYPE into a password field, so the window reported `NO OBSERVABLE CHANGE` on Gitea's login form and drew two confident verdicts on correct behaviour. Fixed by projecting a fixed `[password withheld]` marker, which restores occupancy — the thing state identity and the judge both need — while leaking neither the content nor its length. **Note that the profile *masked* this defect rather than fixing it**, which is a caution about grounding generally: a profile that suppresses a false positive arising from a bad observation hides the bug instead of correcting it. The A/B numbers above predate this fix.

**Operational note:** `gpt-oss:120b-cloud` returned HTTP 500 on 3-5 calls per 110-window run in these sessions. §3.4 flagged its availability and terms as unverified; that risk is now observed, not hypothetical. Failed calls are counted and never read as "no bug", but a run near a deadline should not assume the hosted judge is there.

#### The live loop, measured on Gitea

`scripts/run_judged.py` against the running Gitea with the profile attached: 14 steps, gate passed 9, **0 failed calls, 0 positives** (those steps were mostly footer-link clicks, which is the right answer). Throughput: **0.14 steps/s, with judging accounting for 94% of wall clock** — 93 of 99 seconds.

That 94% is the number to plan against, and it is worse than the per-window figure suggests because the judge runs *behind* the browser rather than beside it. Two consequences: an evaluation rollout on a real target costs roughly 10 s per gated step, which is fine for finding bugs and fine for a demo; and **training against a live judge is not a tuning problem but an architectural one** — at 30-50K steps it is weeks of wall clock. The realistic paths are batching judge calls across parallel envs, or relabelling stored transitions off-policy so the browser never waits. Neither is built; §9 item 9 (`SubprocVecEnv`) is where the first would land.

### 2026-08-10 — ground truth on a real application, and the headline number stops being reproducible

Three of the four planned items landed: the gate now runs in the offline scorer, Gitea has seeded defects with an answer key, and session bootstrap exists. Two findings outrank all of that.

#### The hosted judge changed underneath the project

Re-scoring the *unchanged* toy corpus with the *unchanged* prompt now returns **9/10, not 10/10**. BUG-08 was investigated directly: the rendered window is byte-identical (the old verdict's evidence line, `now hidden : a#nav-signup, a#cta-signup`, is still present verbatim), the gate did not touch that window, and the same window run five times returns `is_bug=false` 5/5 where it previously returned `ui_regression` at 0.95 confidence.

Nothing in this repository changed. **`gpt-oss:120b-cloud` is not a pinned artifact**, and §3.4 recorded its terms and availability as unverified. That risk has now materialised in the worst available form — not an outage, which is loud, but a silent change in the number the project leads with.

Meanwhile the **local, version-pinned `qwen2.5:7b-instruct` scored 10/10** on the same gated corpus (2/6 false positives, 100% evidence grounding).

**Decision (2026-08-10): the hosted judge stays. The problem is the unversioned tag, not the hosting.** §3.4's product argument is unchanged and correct — the user uploads a repo to a service rather than running a model, and the VRAM arithmetic rules local inference out during training regardless. A hosted model behind a *dated snapshot* would have shown none of this drift; `:cloud` is simply a moving target.

**What the drift does and does not invalidate.** It moves verdicts between *sessions*, not within one. The grounded/ungrounded A/B ran both arms about forty minutes apart on the same corpus, so drift applied equally to both and that comparison stands. What broke was comparing a run from 2026-08-08 against one from 2026-08-10. So the measurement rule, which costs nothing architecturally:

> **Every figure quoted in the report comes from a single dated run with its own baseline re-measured inside that run.** Never compare a number against one captured in a different session, and never quote a stored number from this document as though it were current.

Concretely: the 10/10 in the 2026-08-06/08 entry and the 9/10 here are **not** a regression to explain — they are two different instruments, and only same-run pairs are comparable. Any table in the final report must be regenerated in one sitting.

`qwen2.5:7b-instruct` is retained as a **reproducibility anchor**, not as the headline: it is version-pinned, so re-running it is the cheap way to confirm a pipeline change did what it claimed rather than the model having moved. It reaching 10/10 on the same gated corpus is also a genuine secondary result — "a 7B on a 6 GB laptop matches the frontier model here" — but it is reported alongside the hosted judge, not instead of it.

#### First ground truth on a real application

`scripts/seed_gitea_bugs.py` installs six defects into a running Gitea through **Gitea's own `$GITEA_CUSTOM/templates` override path** — no fork, no rebuild, `--remove` restores stock exactly. That matters for credibility: a forked binary invites "you tested your own fork", whereas two template files are auditable and provably absent once deleted. `--install` verifies every defect is actually live and refuses otherwise, because a seeded bug that failed to install is indistinguishable downstream from a judge that missed it.

Four are `llm_required` (every page returns 200, logs nothing, settles); two are deterministic anchors. **Deterministic ceiling 2/6** — the direct analogue of the toy site's 3/10.

| | deterministic triggers | judge (ungrounded) | judge (grounded) |
|---|---|---|---|
| semantic | 0/4 | **2/4** | 2/4 |
| deterministic | 2/2 | 2/2 | 2/2 |
| **total** | **2/6** | **4/6** | **4/6** |
| false positives | 0/2 | **0/2** | 0/2 |
| evidence grounded | n/a | 100% | 100% |

**This is a much weaker result than the toy site's, and it is the more honest one.** 4/6 against a 2/6 ceiling is a real but modest margin, on six bugs — far too few to be confident in, and the toy site's 10/10-vs-3/10 should no longer be quoted without this beside it.

**Grounding changed nothing here** (identical verdicts both arms). Combined with the 2026-08-09 result, where it halved false positives on unlabelled traffic, the emerging picture is that the profile helps *precision* and not *recall* — consistent with the fact that its work was being done by the known-correct-behaviour entries rather than the declared constraints.

#### Three more observation defects, found by having ground truth

Every miss in the first scored run was an observation defect, not a judgment failure. This is the eleventh, twelfth and thirteenth of that shape.

1. **Uncaught exceptions never reached the judge.** Playwright reports `console.error()` on `console` and a genuine uncaught throw on `pageerror`. `detect_bug_signals` has always combined both; `render_step` read only the first. So GITEA-05 — a real uncaught `TypeError` — fired the deterministic trigger while being *completely invisible* in the window. The most classic bug class there is was unjudgeable. Fixed: **3/6 → 4/6** on that change alone.
2. **Hidden controls were anonymised.** Collapsing id-less elements into `(unnamed) xN` was added to stop Gitea's 37 `_aria_auto_id_N` menu entries flooding the budget; it also erased *which* controls a viewport change removed. GITEA-03 hides Register and Sign In at 375px and the window said only `now hidden : a (unnamed) x41`. Hidden controls are now labelled by their visible text, still collapsed by count.
3. **The window could state that a hide was harmless but never that it was harmful.** `links still reachable` lets a judge rule a hide benign; ruling it harmful required noticing something was *absent from a list*, which is an inference from absence. Added `NO LONGER reachable`, the positive complement.

**GITEA-03 and GITEA-04 are still missed after all three fixes, and tuning stopped there.** Both misses are now defensible readings of an ambiguous observation rather than missing evidence: on GITEA-03 the judge reasoned "a responsive UI may hide navigation items, typically moving them to a hamburger menu" — and Gitea's own footer language links *do* appear at mobile width, which makes that inference reasonable. Chasing either one specifically would be per-bug tuning against a key I wrote, which §5 (2026-08-08) already established is the wrong trade.

#### Gate in the offline scorer, and a circular import it exposed

Gating is now the **default** in `score_judge.py` (`--no-gate` reproduces a historical number), so offline and live measure the same pipeline; `decide_for_record` is shared by the scorer, the replay script and the live path so the three cannot drift. On the toy scripted corpus the gate skips 2 of 39 windows and costs **nothing** — both skips are SCROLL steps in the control episode.

Wiring it exposed a real cycle: `reward.base` → `envs.types` → `envs/__init__` → `envs.base_env` → `reward.functional_triggers` → `reward.base`. It had always been there and always worked, because whichever package a script imported first completed — a new import line in `score_judge.py` reversed the order and `RewardSignal` failed to resolve. Fixed by making `envs/__init__` load the two heavy env classes lazily (PEP 562), which breaks the cycle at its source instead of depending on import order. Side benefit worth keeping: importing `web_testing_agent.judge` no longer drags Playwright into the process.

#### Session bootstrap (§9 item 10)

`WebFunctionalEnv(setup_actions=[...])` replays a macro after the landing page and before the first observation. Its steps are **not** counted against `max_steps` and never reach the reward — logging in is test setup, and paying an agent for it would make "log in again" a reward source. Verified against Gitea: 3/3 steps, lands authenticated on the dashboard, and the password does not appear in the observation.

Two things it needed that the repro-script matcher did not have, both forced by a real application:

- **Label matching.** Gitea's explore tabs, footer links and repository tab bar carry no ids at all, so `text` matches against the display label.
- **`nth`.** Gitea's login page has *two* controls reading "Sign In" — the navbar link and the form's submit button. Taking the first navigated to the page the macro was already on, so the bootstrap reported **3/3 steps completed while leaving the session anonymous**. A silent bootstrap failure is the worst kind: every authenticated finding would simply be absent from the corpus, indistinguishable from a judge that found nothing. Hence the loud per-step warning and the `bootstrap_steps`/`authenticated` fields now recorded in `info`.

**Tests: 465 → 478.**

**Not done in this pass:** the deep-flow fixture (§9 item 1c) and DQN action masking (§9 item 1b). Both remain open and are now the highest-value RL work — see the note in §9.

### 2026-08-10 (later) — action masking works; nobody solves the deep flow, and the reason is diagnosable

Both remaining RL items are now built (§9 1b and 1c). The comparison they enable returns a negative result with a specific, actionable cause.

**Action masking (`agents/masked_dqn.py`) — verified.** Both halves had to be masked and each is silent on its own:

- *Greedy* selection reads Q-values, so invalid slots are pushed to a large finite sentinel before argmax. **Finite, not `-inf`**: `-inf` survives argmax but yields NaN the first time it reaches a subtraction in the Huber loss, poisoning the weights rather than biasing the choice.
- *Exploratory* selection never touches Q-values — SB3 calls `action_space.sample()`. Masking only the Q-values leaves ε of every step uniform over 100 slots, and training *begins* at ε=1.0.
- *Warm-up* never reaches `predict` at all: SB3 fills the buffer for `learning_starts` steps via `action_space.sample()` directly. Measured on a 7-valid-action harness, with only `predict` masked **every** invalid action in a 200-step run came from here. Those are also the *first* transitions in the buffer, so the earliest gradients would come almost entirely from no-ops. With all three masked: **0/200 invalid actions**.

The mask rides in the trailing `MAX_ACTIONS` dims of the observation because that is the only channel an SB3 policy can read; `FusionFeaturesExtractor` splits it off and discards it, so the Q-head cannot infer page identity from how many controls a page happens to have.

**The deep-flow fixture (`tests/fixtures/deep_flow_site`)** is a four-gate ordering flow whose single defect — the confirmation reports quantity 1 whatever was ordered — is reachable only after completing every stage in order. Each stage *reveals* its Continue link rather than disabling it, so the action space genuinely grows with progress and nothing reads as a dead control. The review page one step earlier echoes the order back **correctly**, so a judge cannot be right by assuming every echo is broken. Deterministic ceiling is 0/1: every page is 200, quiescent and console-clean.
**(Corrected 2026-08-27: true of the *fixture*, false of the *harness*. `broken_navigation` fired 18–20 times per 200-step run on it, on every policy, because a link resolving to the page already loaded leaves the URL unchanged — the exact condition the trigger read as a broken link. Every deterministic "finding" ever reported on this fixture was one of those. See the 2026-08-27 entry.)**

*A fixture-design trap worth remembering:* the first version gated quantity at 1–10, and the action registry generates exactly five values per numeric field (`42`, `0`, `999999999`, `""`, `not-a-number`). All five were rejected, so **no policy could pass stage 2** and the fixture measured nothing. A gate is only a valid benchmark if it is passable by the explorer's own vocabulary.

**The measurement** (4,000 training steps each, 5 × 40-step evaluation episodes, identical hyperparameters, seed 0):

| policy | valid actions | unique states | mean flow depth | max depth | reward |
|---|---|---|---|---|---|
| dqn_unmasked | 89% | 6 | 0.11 | 1 | −11.19 |
| **dqn_masked** | **100%** | 6 | **0.33** | 1 | −24.34 |
| random_unmasked | 18% | 7 | 0.00 | 0 | −28.81 |
| random_masked | 100% | 9 | 0.04 | 1 | **+7.31** |

**Masking does exactly what it was built to do** — 100% valid actions against 18% for unmasked random — and the masked agent reaches three times the mean flow depth of the unmasked one. **And no policy gets past stage 1.** Nothing reaches the receipt; the seeded bug is never observed by anything.

Two causes, and neither is the masking:

1. **The novelty bonus rewards breadth, and this flow needs depth.** Reaching `terms.html` pays the same +1.0 as advancing to `order-2.html` and is far easier — there are eleven shallow distractor pages. `random_masked` maximises exactly that and earns the best reward in the table (+7.31) while going nowhere (mean depth 0.04). The masked DQN earns the *worst* reward (−24.34) while going furthest. **On this fixture the reward and the objective point in different directions**, which is the same finding as run 5's "best-by-reward checkpoint found fewer bugs", in a sharper form: here it is visible as a rank inversion across policies rather than across checkpoints.
2. **The agent cannot see semantics.** The perception encoders are still `HashEmbeddingEncoder` stubs (§3.2), so two pages that differ by one character have unrelated observations and "this is a Continue link" is not representable at all. A policy that cannot generalise across states cannot learn a four-step chain from 4,000 steps of experience — it would have to memorise each transition, and it never visits stage 2 often enough to memorise anything.

**What this does and does not establish.** It does *not* show RL cannot chain flows; the agent was never given an observation capable of supporting the inference. It does establish that **the perception layer is now the binding constraint on Agent B's RL half**, which moves §9 items 3–4 from "expansion" to "prerequisite" — a reversal of their previous priority. It also establishes that the exploration bonus needs a depth-aware term (progress within a flow, not merely state novelty) before any deep-flow claim is testable.

**Tests: 478 → 485.**

### 2026-08-10 (later still) — the real encoders, and two defects they introduced

§3.2's encoders are built (`perception/encoders/models.py`): CLIP ViT-B/32 vision tower on the screenshot, CodeBERT on the preprocessed structural HTML, all-MiniLM-L6-v2 on the network trace. All three frozen, batched, and content-addressed-cached.

**Throughput is a non-issue, which contradicts the warning given when this was planned.** Measured at batch size 1: visual 22.4 ms on a cache miss and 0.10 ms on a hit, structural 7.8 / 0.00, network 5.1 / 0.00. Against a browser step of roughly 700 ms that is ~5% overhead, not the multi-hour tax that was predicted. Two reasons: the vision tower alone is loaded (the text tower is ~40% of CLIP's parameters and is never used), and a rollout revisits the same handful of pages constantly, so most steps are cache hits. Total VRAM 951 MB, which leaves room for Chromium on the 6 GB card. **The training budget does not need to shrink for this.**

**Defect 1 — the spec's structural encoder does not discriminate between pages.** Measured on 18 real fixture pages, CodeBERT's `[CLS]` token gives pairwise cosine similarity **min 0.973, mean 0.991**: every page looks like every other page, making it barely better than the hash stub it replaced. This is the well-known anisotropy of raw transformer embeddings — they occupy a narrow cone — and it is why sentence-BERT exists. Mean pooling alone barely helped (min 0.934, mean 0.980). Mean pooling **plus centering by a fixed reference vector** reached min 0.288, mean 0.770, and the nearest-neighbour structure became semantically correct: the four order stages became each other's neighbours, the static pages clustered separately, and the two form pages paired up.

The reference vector is computed once at construction from a built-in probe set and frozen. Centering per *batch* is cheaper and obvious, and it is wrong here: it would make the observation depend on which other environments happened to be in the batch, which is a non-stationary observation — the exact defect behind the peak-then-collapse curve in run 3. `pooling="cls"` remains selectable so the spec's literal configuration stays runnable and the comparison above is reproducible rather than merely asserted.

**Defect 2 — swapping the stubs for real models silently broke a scale contract, and made the agent worse.** First measurement with real encoders:

| policy | states (hash → semantic) | mean depth (hash → semantic) |
|---|---|---|
| dqn_masked | 6 → **2** | 0.33 → **0.00** |
| dqn_unmasked | 6 → 4 | 0.11 → 0.15 |

`HashEmbeddingEncoder` emitted **unit** vectors, and `WebTestingEnv._episode_context` says so explicitly — it scales its features into [0, 1] "so no single term dominates the input scale of a network whose other 1664 dims are unit-norm embeddings". The real encoders do not: measured norms are **CLIP 10.6–11.2**, CodeBERT centered 2.4–2.9, MiniLM 1.0. So the visual block outweighed the other two modalities *and* the episode-context features by an order of magnitude. Fixed by L2-normalizing every encoder's output, which keeps the direction — where all the semantic content is — and discards only a magnitude nothing downstream was scaled for.

**The normalized rerun landed, and it disproves the hypothesis this work was built on.** Deep flow, 4,000 training steps, identical hyperparameters, three encoder configurations for the masked agent:

| encoder config | unique states | mean flow depth |
|---|---|---|
| hash stub | **6** | **0.33** |
| semantic, unnormalized | 2 | 0.00 |
| semantic, normalized | 4 | 0.00 |

Normalizing was a genuine defect fix — it recovered half the lost state coverage — and it **did not change the conclusion**. Real CLIP/CodeBERT/MiniLM embeddings leave the masked DQN *worse than the deterministic hash stub* on this fixture. Full table: `reports/compare_agents_deep_semantic.json`.

**The claim that perception was the binding constraint is now measured false at this scale, and it was mine.** It was asserted on the strength of a single null result, and stated as though it followed from that result when it did not.

> **Retracted in turn, 2026-08-15.** The table above is **n=1 per DQN row** and cannot support this paragraph either. Evaluation ran greedily against a static fixture, so each DQN arm is one trajectory replayed five times (`episode_rewards: [-24.45] × 5`), at one training seed — while the random rows it is compared against are genuinely five samples. Correcting a claim with a measurement that has the same defect as the claim is not an improvement. See the 2026-08-15 entry; the reseeded numbers replace this table, and until they land the honest status of *both* the original hypothesis and this retraction of it is **unmeasured**.

**The likely mechanism, and why it matters more than the number.** A content hash is a near-perfect state *identifier*: distinct pages get near-orthogonal vectors, so the Q-function can behave like a lookup table. Semantic embeddings do the opposite by design — they make similar pages *similar*, and on this fixture `order-2.html` and `order-3.html` genuinely are similar (same layout, same nav, one input each). Generalization is a liability when the states you must distinguish are near-neighbours and the budget is small enough to memorize instead. That predicts exactly what was measured: semantic encoders should pay off on large, diverse targets where memorization is impossible, and cost you on a 12-page fixture at 4,000 steps.

So the experiment **cannot distinguish "semantic encoders do not help" from "semantic encoders do not help at this scale"**, and the second is the more likely reading. Their value is *untested*, not disproven — and testing it properly needs a target and a step budget larger than anything this project has run, which puts it beyond the remaining scope rather than one experiment away.

**What this leaves standing.** No policy passes stage 1 under any encoder configuration, so the dominant obstacle is the one diagnosed first: **the novelty bonus rewards breadth where the flow needs depth**. That was listed as cause 1 and treated as the lesser of the two; it is now the only one with evidence behind it. A depth-aware exploration term is therefore the next RL change worth making, and the encoders should be kept (they are built, cheap and correct) but not expected to move this number.

A third, smaller defect the tests caught before it shipped: a batch larger than the encoder cache evicted its own earlier entries while still being filled, so assembling the result raised `KeyError` on exactly the inputs it had just encoded. Harmless at one environment and a hard crash at eight, which is the configuration `SubprocVecEnv` parallelism (§9 item 9) will use.

### 2026-08-15 — external review: a 14th harness-as-application defect, three gate bypasses, and an n=1 measurement

A review pass over the whole repository. Three findings, in descending order of how much they change what can be claimed.

**1. The deep-flow comparison was n=1 and was reported as n=5.** `TrainedPolicy.act` evaluated with `deterministic=True` against a static local fixture, so the trained policy was a deterministic function of the page and all five evaluation episodes replayed **one trajectory**. The 2026-08-10 semantic run recorded `episode_rewards: [-24.45, -24.45, -24.45, -24.45, -24.45]` — five identical numbers, averaged and printed as a mean. The random baselines *are* stochastic and did produce five genuine samples, so the table set n=1 rows beside n=5 rows without marking the difference. On top of that, every arm was a single training seed.

That is the evidence base for "mean flow depth 0.33 → 0.00" and for the entry above calling the perception hypothesis disproven. It does not support a conclusion in either direction. Corrected in `scripts/compare_agents.py`: `--seeds` repeats the whole comparison per training seed and reports median [min-max]; `--eval-epsilon` (default 0.05, the value training ends at) keeps the policy's residual exploration during evaluation so its episodes differ for the same reason the baseline's do, sampling from the agent's *own* action space so the unmasked arm is not handed masking at evaluation time.

**The same defect is in `scripts/train_toy.py`, and it reaches further.** That script produced run 5 — "DQN found 1/3 seeded bugs against masked random's 3/3" — which is cited throughout this document and in the README as the evidence that RL has not earned its place. Its DQN rows were evaluated the same greedy way, so that comparison is also one trajectory against a genuine 3-episode mean, at one seed. The harness is fixed (§9 item 2b); the numbers are not, and **run 5 should be cited as suggestive until it is rerun**. Fixing it could move the result in either direction — the point is that it currently is not a measurement.

A third defect surfaced while fixing these: `evaluation.rollout.TrainedPolicy` never passed `num_valid_actions` into `encode_modalities`, so the action mask reaching the policy at evaluation was **all-valid**. For the unmasked agents evaluated through it this was harmless — `FusionFeaturesExtractor` splits the mask off and discards it — but any masked agent scored through that path would have had its masking silently switched off at evaluation time while every training log still said "masked". Pinned by `tests/unit/test_trained_policy_eval.py`.

### 2026-08-17 — the reseeded deep-flow rerun: what survived, what was noise, and a negative result that is now solid

Three seeds x both encoder configurations, 4,000 training steps each, ε=0.05 at evaluation. 24 evaluation runs, ~7 hours. `reports/compare_agents_deep_{hash,semantic}_3seed.json`. Every cell median [min–max] across seeds.

> **The `distinct findings` column below is invalid, corrected 2026-08-27.** Every
> firing behind it was a self-link `broken_navigation` false positive; on a fixture
> whose deterministic ceiling is genuinely 0, it could not have been anything else.
> The other four columns are unaffected. See the 2026-08-27 entry for the rerun.

| policy | valid actions | mean flow depth | reward | ~~distinct findings~~ |
|---|---|---|---|---|
| dqn_unmasked, hash | 90% [82–96] | 0.17 [0.00–0.22] | −10.2 [−17.5, −4.0] | 0 [0–1] |
| dqn_masked, hash | **100%** | 0.03 [0.03–**0.36**] | −14.6 [−16.0, −9.5] | 1 [0–2] |
| dqn_unmasked, semantic | **60% [52–87]** | 0.05 [0.03–0.08] | −19.6 [−22.8, −17.5] | 1 [0–1] |
| dqn_masked, semantic | **100%** | 0.01 [0.00–0.10] | −23.2 [−31.7, −20.7] | 1 [1–4] |
| random_unmasked | 18% [16–18] | 0.00 | −28.8 [−29.3, −26.7] | 3 [2–3] |
| random_masked | **100%** | 0.04 [0.01–0.04] | **+7.3** [6.6, 8.1] | **10 [10–12]** |

**What the original 0.33 actually was.** `dqn_masked` on hash stubs spans **0.03 to 0.36** across seeds: one seed near 0.35, two at 0.03. The published figure was not wrong, it was *unrepresentative* — one draw from a wide distribution, reported as the result. That is a more useful way to describe the failure than "it did not replicate", and it is the shape to expect from any single-seed RL number at this budget.

**Survives, unchanged and now robust.** Masking gives 100% valid actions on every seed under both encoder configurations — zero spread. No policy passes stage 1 in any of the 24 runs (`max_depth ≤ 1`, zero receipts). And the rank inversion behind cause 1 holds on every seed: `random_masked` earns the best reward while going essentially nowhere, so reward and objective point in different directions.

**Dies.** "The masked agent reaches three times the mean depth of the unmasked one." Medians are 0.03 masked against 0.17 unmasked, ranges overlap heavily, means are ~0.14 against ~0.13. **Masking fixes action validity completely and does nothing measurable for depth.** Both halves of that sentence should be reported; the first is a real contribution and the second stops it being overstated.

**Semantic encoders: the direction of the 2026-08-10 claim survives, the metric it was made on does not.** Return is clearly worse with real encoders — seed ranges do not overlap on either arm (unmasked −19.6 vs −10.2, masked −23.2 vs −14.6) — and the unmasked valid-action rate collapses from 90% to 60%, which is what the "embeddings make similar pages similar" mechanism predicts: it is harder to memorise which slots are valid where. Flow depth is also lower, but the ranges overlap and n=3 cannot separate it, so **the specific "0.33 → 0.00" claim is retired rather than confirmed.** Their value on a large diverse target remains untested, exactly as before.

**The finding that matters most, and it is not about masking or perception.** `random_masked` discovers **10–12 distinct findings on every seed under both encoder configurations**; the DQN manages **0–4**. Roughly 10×, at identical step budget, with no seed overlap.

> **Retracted 2026-08-27 as a bug-discovery claim.** All 10–12 were self-link
> `broken_navigation` false positives — `nav-catalog` clicked on `catalog.html`,
> `sup-returns` on `support.html`. The gap is real and reproducible, but it measures
> **how many distinct links on how many distinct pages each policy touched**, i.e.
> exploration diversity, not bugs found. On this fixture no policy has ever found a
> bug, because DEEP-01 is `llm_required` and nothing reached it. The sentence below
> — "it was tested, in the regime chosen to favour it, and it lost" — still stands,
> but it now rests on depth, coverage and completion rather than on a findings count. This fixture was built specifically to be the regime where RL's real claim — credit assignment over multi-step flows, where random's success probability decays exponentially in sequence length — should finally show. It did not. §8's careful "RL's actual claim remains untested" is no longer the honest phrasing: **it was tested, in the regime chosen to favour it, and it lost.** Report it that way. A measured negative on exploration strategy beside a strong positive on judgment quality is a coherent contribution, and hedging it now would be less defensible than stating it.

Sanity check worth recording: the two `random_*` rows are byte-identical between the hash and semantic runs (`finds` 10/12/10 both times), which is correct — random policies never touch an encoder — and confirms the harness is deterministic exactly where it should be and stochastic exactly where it should be.

The general lesson is the one this project keeps relearning in a new place: **a number that cannot vary is not a measurement.** Five identical episode rewards in a committed report were visible for five days and read as precision rather than as the tell they were.

**2. `judge/window.py` fabricated constraint declarations on 30.7% of judge inputs.** `_constraints` matched each attribute with `\b{attr}(?:="([^"]*)")?` — a word boundary and an *optional* value. Three consequences, all measured on the captured corpora:

- `min` is a prefix of `minlength`, so `minlength="3"` also emitted a bare `min`
- the optional value let any occurrence match, so a utility class like `class="max-w-full"` emitted a bare `max` — 16 times across 187 pages
- `\b` matches after a hyphen, so `data-type="custom"` was rendered to the judge as `type=custom`

The same `\b` flaw was in `_ID_ATTR`, `_TEXT_ATTR` and the inline `name=`/`value=` searches, where `data-id` / `data-value` impersonated the real attributes. All now require `(?<![-\w])` before the name and either a real `="value"` or a lookahead proving the attribute ended there; unquoted and single-quoted values are read correctly as a side effect.

This is **the fourteenth instance of the defect class §8 names**: the window stating something true about the harness as though it were true about the application. It is the first one found on a corpus that produced a *published* number. Rendered-window delta across all five corpora:

| corpus | windows | carried a fabricated declaration |
|---|---|---|
| toy-scripted | 39 | **0** |
| toy-random | 148 | **0** |
| gseed-scripted | 16 | **16 (100%)** |
| gitea-random | 110 | 58 (53%) |
| gitea5-random | 110 | 56 (51%) |

**The toy site could not have caught this, at all.** Its fixture markup hand-writes clean `min`/`max`, so zero of its 187 windows change. Every window of the seeded-Gitea corpus does. This is the same argument the 2026-08-08 random-corpus entry makes, arriving again: the corpus whose ground truth was authored alongside the renderer cannot test the renderer.

**Effect on the verdicts: none worth claiming, measured as a controlled pair.** Both arms run in one session on `qwen2.5:7b-instruct` at `temperature=0` with the `compact` prompt, differing only in the renderer — the published 4/6 used `gpt-oss:120b-cloud` with `detailed`, so it is not a valid comparison point and was not used as one.

| | fabricated constraints | fixed |
|---|---|---|
| seeded bugs found | 6/6 | 6/6 |
| false positives | 1/2 control | 1/2 control |
| positive verdicts | 13 | 12 |
| discrimination | +50% | +42% |
| evidence grounded | 13/13 | 12/12 |

Detection is unchanged. The discrimination drop is not a regression in judgment: the lost positive is a *duplicate* verdict on `account_unreachable_on_narrow_viewport` at step 11, whose bug (GITEA-03) is still detected at step 9. One classification also became more accurate — `user_listing_throws` moved from `ui_regression` to `js_error`, and GITEA-05 genuinely is an uncaught `TypeError`. At 14 judged windows and 2 controls none of these deltas is significant. **The defensible claim is that the judge's input is now factually correct, not that the fix improved accuracy.** Reports: `judge_gseed_v4_oldconstraints.json` (baseline arm) and `judge_gseed_v4_constraintfix.json`.

**3. Three bypasses in the compose security gate (§3.0a).** The gate only ever walked `document["services"]`, and three standard Compose features route around that. All three were confirmed to pass the gate before the fix and to be blocked after it:

- **`driver_opts` bind** — a top-level `volumes: {hostroot: {driver: local, driver_opts: {type: none, device: /, o: bind}}}` is a *named* volume that mounts host `/`. The service-level reference `hostroot:/host` passed the bind check, because the source is a bare name with no leading slash. This defeated the exact rule the gate states in its own violation message, using the volume API as documented.
- **`extends: {file: base.yml, service: x}`** — merges a service definition from a second file at `docker compose up` time. Put `privileged: true` in `base.yml` and nothing the gate inspected contains it.
- **top-level `include:`** — the same, for whole files.

`extends` and `include` are now blocked outright rather than followed: resolving them means reimplementing Compose's merge semantics (relative path bases, recursive extends, per-key merge-vs-replace), and a subtly wrong reimplementation would report "clean" about a document that is not the one being run. v1 requires a self-contained build definition, which every containerised target here already satisfies. `group_add` was also added to the forbidden keys — joining the host `docker` group is the same escalation as mounting the socket, obtained without naming a volume.

**What this says about the gate's design.** It was allowlist-shaped against *keys within a service*, and correct at that. The three misses share one shape — a Compose feature that changes **what document is executed**, which is a layer above the one being validated. That is worth stating in §7 as its own principle: a policy over a parsed document is only as good as the guarantee that the parsed document is the one that runs.

### 2026-08-26/27 — closing the measurement debt: run 5 superseded, and source grounding does not help

Three measurements, run to settle claims rather than to add capability. Two close open
items; the third answers the project's headline question and answers it negatively.

**1. The toy-site comparison, reseeded — run 5 is superseded.** `train_toy.py`, three
seeds, corrected harness (`--eval-epsilon 0.05`, and `TrainedPolicy` now passes the
action mask it had been dropping). 4,000 training steps, 3 eval episodes x 40 steps,
hash encoders, deterministic triggers only.

| arm | seeded bugs /3 | unique states | distinct findings | valid actions |
|---|---|---|---|---|
| dqn (best checkpoint) | 0 [0-1] | 6 [6-7] | 0 [0-4] | 90.8% [85-95.8] |
| dqn (final policy) | 0 [0-1] | 7 [6-9] | 0 [0-4] | 76.7% [30-85.8] |
| random, full space | 0 [0-1] | 2 [2-7] | 0 [0-4] | 11.7% |
| **random, masked** | **3 [2-3]** | **15 [14-16]** | **6 [5-12]** | **100%** |

Median [min-max] over seeds 0, 1, 2. Raw per-seed numbers in
`reports/toy_dqn_comparison_s{0,1,2}.json`.

**Run 5's "DQN found 1/3 seeded bugs" was the lucky seed, and it was not the DQN that
found it.** On seed 0 the DQN, the final-policy DQN *and unmasked random* each found
exactly one bug and four findings; on seeds 1 and 2 all three found none. One arm getting
1/3 on one seed was reported as the DQN's result for three weeks. The corrected median is
**0/3 against masked random's 3/3**, so the direction of run 5 holds and the gap is wider
than it claimed. The voided `unique_states` figures are replaced too: 6 against 15, not
3 against 8.

**What this comparison is not.** `train_toy.py` trains an **unmasked** DQN, so this
corrects the *evaluation* defect and not the *masking* handicap — masked random can
sample only legal slots and the flat head structurally cannot. It is not a fair
masked-vs-unmasked test and must not be cited as one. **The deep-flow 3-seed result
(2026-08-17) remains the evidence for the masking and RL comparison**, because there both
sides had masking.

Also visible and worth keeping: the final policy's valid-action rate spans 30-85.8%
across seeds, which is the peak-then-collapse instability already recorded above, now
with a range rather than an anecdote.

**2. Go-Explore's known-bad columns, and a supplementary n=1 at 4,000 steps.** The two
columns committed as known-bad are now measured. At the **matched 200-step budget over
3 seeds** (`reports/go_explore_deep_matched200.json`):

> **`distinct findings` below is invalid (2026-08-27): self-link false positives on
> both sides of the comparison.** The other two columns stand.

| policy | unique states | mean flow depth | ~~distinct findings~~ |
|---|---|---|---|
| go-explore | 9 [8-11] | 0.10 [0.05-0.18] | 11 [9-13] |
| random, masked | 8 [8-9] | 0.04 | 10 [10-12] |
| dqn, masked | 8 [7-9] | 0.03 | 1 [0-2] |

`distinct_findings` was 0 for every seed — a runner bug reading a key that does not
exist, not a result. `archive_max_depth` undercounted because it inferred stage depth
from route contents; cells now record their URL and it agrees with `max_depth`.

**This weakens the earlier Go-Explore claim and the weakening should be reported.** At a
budget matched to masked random, go-explore is *level* on findings and only modestly
ahead on depth. The dramatic result needs 20x the budget.

> **Amended 2026-08-27.** "Level on findings" is now vacuous rather than wrong:
> both figures counted self-link false positives, and the fixture's true
> deterministic ceiling is 0, so the honest statement is that **neither method finds
> anything on this fixture, and neither could**. The depth half of the sentence is
> unaffected and remains the real content of the comparison.

**Supplementary, n=1** (`reports/go_explore_deep_4000_seed0.json`): at 4,000 env steps,
seed 0 alone reached 21 cells, `max_depth` 5, `mean_depth` 0.38, ~~17 distinct findings~~,
and spent 11 steps on the receipt. **(The findings figure is withdrawn 2026-08-27 — self-link
false positives. The depth and receipt figures are the ones worth citing, and they are
the reason this run matters.)** Seeds 1 and 2 were not run. **This is one seed and must
never be placed in a row beside a 3-seed table without saying so.** 44 of 1,285 returns
failed (3.4%), where the pre-fix run reported 0 — not a regression but the cost of the
route-validity fix: validated routes climb deeper, and a 5-9 step route has more places to
derail. That is what `returns_failed` exists to surface.

*A cost claim withdrawn.* This run was reported mid-flight as taking 7.5 hours per seed.
That was elapsed time on a laptop that slept: three gaps of 388, 150 and 545 minutes
account for 18 of the 19.3 hours. Actual compute was **~75 minutes**, consistent with the
62 minutes the pre-fix run took. The decision to stop after seed 0 was taken partly on the
inflated figure; the honest cost of the remaining two seeds is about 2.5 hours.

**3. Source grounding: measured properly, and it does not help.** This is §1's stated
differentiator and the last major untested claim. Both arms in one session,
`qwen2.5:7b-instruct` (version-pinned local, deliberately **not** the unversioned `:cloud`
tag whose weights moved mid-project), temperature 0, compact prompt, identical corpora and
gate. The only difference is `--profile data/profiles/gitea.json`.

| | ungrounded | grounded |
|---|---|---|
| **gseed-scripted** — 14 judged, 2 controls | | |
| seeded bugs found | 6/6 | 6/6 |
| false positives | 1/2 | 1/2 |
| discrimination | +42% | +42% |
| evidence grounded | 12/12 | 12/12 |
| **gitea5-random** — 60 judged of 110 | | |
| positive verdicts | 44 (73.3%) | **46 (76.7%)** |
| evidence grounded | 44/44 | 46/46 |
| identical verdicts | — | 43/60 |
| `is_bug` flips | — | 10 (4 ungrounded-only, 6 grounded-only) |

**On every metric asked of it — recall, false positives, discrimination, evidence
grounding — the profile changed nothing.** On the random corpus it produced slightly
*more* positives, not fewer.

It is not inert, which is the more interesting part. Only 4 of 14 verdicts match on the
labelled corpus and 43 of 60 on the random one: it changes a great deal while improving
nothing. Bug-type classification shifts toward `ui_regression` in both (labelled: a
four-type spread collapses to 11 of 12 positives; random: 11 to 17). Against an answer key
whose six types are all different that is arguably worse — GITEA-05 is a genuine uncaught
`TypeError`, and only the ungrounded arm ever typed anything `js_error`.

**What this does not establish.** It does **not** contradict the 2026-08-09 result
(9 positives to 4). That used `gpt-oss:120b-cloud` on a different corpus state, and the
tag is unversioned. This is a *failure to reproduce with a pinned model*, which is weaker
than a contradiction and is exactly the situation the reproducibility caveat in §3.4 was
written for. Three further limits: the labelled corpus is **saturated** (both arms at the
6/6 ceiling, so it cannot show an improvement that exists); the random corpus is
**unlabelled**, so 44 against 46 are unattributed positives rather than measured false
positives; and this is one model against one target.

**And the limit that matters most: no source was read.** This tests the *consumer* with a
**hand-authored** profile. §1's actual differentiator — intent derived from an
application's own source code — is still untested, and now carries a harder problem than
before: the plumbing shows no benefit even when the profile is hand-written and correct,
so an extractor would have to beat a baseline that is currently zero. Do not report this
as evidence that source grounding cannot work. Report it as: **the hand-authored profile,
measured properly, did not help.**

### 2026-08-27 — Phase 0: the deep-flow findings column was false positives, the toy site finally gets a fair fight, and DQN training turns out not to be reproducible

Measurement work only. **No agent, reward, hyperparameter or algorithm was changed**,
deliberately: the diagnosis that motivates the next RL change rests on these numbers, so
the numbers had to be corrected before the change, not alongside it.

**1. The fifteenth harness-as-application defect, and the first to invalidate a headline.**
`_is_navigational` called every non-fragment `<a href>` navigational, and
`detect_bug_signals` reports `broken_navigation` when a navigational click leaves the URL
unchanged. A link that resolves to the page it is already on — `nav-catalog` on
`catalog.html`, `sup-returns` on `support.html`, `nav-home` on `index.html` — reloads and
correctly leaves the URL unchanged. The deep-flow fixture is full of them.

Every `broken_navigation` firing ever recorded on that fixture was one of these. The
corrected run fires the trigger **zero times, on every arm, on every seed**:

| trigger firings, summed over 3 seeds | before | after |
|---|---|---|
| dqn_unmasked | 1 | **0** |
| dqn_masked | 55 | **0** |
| random_unmasked | 11 | **0** |
| random_masked | 71 | **0** |

Fixed in `envs/action_registry.py` (`_is_self_link`), using the DOM's own `el.href` so a
`<base href>` is honoured rather than reimplemented. `functional_env` passes `page.url`.
Pinned by `tests/unit/test_deep_flow_fixture.py`, which asserts both halves — that the
fixture really does contain ≥10 self-links, and that none of them is navigational while
every outgoing link still is.

**The fixture's own answer key is what hid this**, and it has been corrected: it claimed
"No trigger in `reward/functional_triggers.py` can fire on this fixture at all", which was
true of the fixture and false of the harness. That sentence is why 18–20 firings per run
were never questioned.

**2. Corrected deep-flow baseline.** 3 seeds × 5 episodes × 40 steps, ε=0.05 at
evaluation, hash encoders, 4,000 training steps — the identical protocol, rerun end to
end. `reports/compare_agents_deep_hash_3seed_v2.json`.

| policy | reward | valid actions | states | findings | mean depth | max depth | flow completions |
|---|---|---|---|---|---|---|---|
| dqn_unmasked | −10.8 [−13.1, −7.1] | 80% [68–82] | 7 [5–8] | **0** | **0.18** [0.18–0.26] | 1 | 0 |
| dqn_masked | −12.3 [−14.9, −7.6] | **100%** | **9** [7–9] | **0** | 0.02 [0.00–0.12] | 1 [0–1] | 0 |
| random_unmasked | −30.0 [−30.1, −29.1] | 18% [16–18] | 6 [6–7] | **0** | 0.00 | 0 | 0 |
| random_masked | **−0.3** [−1.0, +0.1] | **100%** | 8 [8–9] | **0** | 0.04 [0.01–0.04] | 1 | 0 |

**A control worth recording, because it is what makes the rerun trustworthy.** The two
`random_*` arms are byte-identical to the pre-fix run on `unique_states`, `mean_depth` and
`valid_action_rate` — seed for seed, all three seeds — and differ **only** in findings
(→0) and reward. The change did exactly one thing.

**What survives, what dies, what is new:**

- **Dies: the "roughly 10×" bug-discovery claim.** On this fixture nothing has ever found
  a bug and nothing could — its deterministic ceiling is genuinely 0 and DEEP-01 is
  `llm_required`. The exploration-diversity gap behind those counts is real and
  reproducible; the bug-discovery reading of it was not.
- **Survives: no policy passes stage 1.** `max_depth ≤ 1`, zero receipts, zero flow
  completions, and **zero episodes even leaving stage 1**, across all 60 evaluation
  episodes. Unchanged.
- **Survives: masking fixes validity and nothing else.** 100% valid actions on every
  seed; mean depth 0.02 masked against 0.18 unmasked — masked is *lower* again, as in the
  2026-08-17 run.
- **Survives, and is now cleaner: the rank inversion.** `random_masked` still earns the
  best reward (−0.3 against −10.8 and −12.3) while going least far (0.04 against 0.18).
  It used to be driven partly by a +2.0 bonus paid for clicking self-links; with that
  gone the inversion is pure novelty-versus-cost, which is the sharper statement of
  cause 1. **The reward and the objective still point in different directions.**
- **New: `dqn_masked` now leads on state coverage** (9 [7–9] against masked random's
  8 [8–9]) while trailing badly on depth. It covers slightly more of the shallow surface
  and still cannot climb.

**3. The toy site finally has a masked-vs-masked comparison.** `train_toy.py` trained an
**unmasked** head only, so every published toy figure set a DQN that could waste its
budget on out-of-range no-ops beside a baseline that could not. It now trains both arms in
one run, with byte-identical hyperparameters, and reports them side by side. 3 seeds ×
3 episodes × 40 steps. `reports/toy_dqn_comparison_v2_s{0,1,2}.json`.

| arm | reward | seeded bugs /3 | states | findings | valid actions |
|---|---|---|---|---|---|
| dqn_unmasked (best) | −18.8 [−36.2, −6.2] | 0 [0–1] | 4 [1–8] | 0 [0–1] | 94% [84–96] |
| dqn_unmasked (final) | −5.4 [−18.3, −1.4] | 0 [0–1] | 9 [4–9] | 0 [0–1] | 90% [85–94] |
| dqn_masked (best) | −27.2 [−34.1, −15.9] | 1 [0–2] | 3 [2–8] | 4 [0–8] | **100%** |
| **dqn_masked (final)** | −8.0 [−12.2, −7.9] | **1** (every seed) | 9 [7–14] | 4 [1–4] | **100%** |
| random_full | −31.2 [−33.6, −30.0] | 0 [0–1] | 2 [2–7] | 0 [0–4] | 12% [11–15] |
| **random_masked** | **+5.2** [+2.8, +12.0] | **3 [2–3]** | **15 [14–16]** | **6 [5–12]** | **100%** |

**Masking helps the DQN and does not close the gap.** Masked beats unmasked on seeded
bugs (median 1 against 0) and on findings (4 against 0), which is the first evidence in
this project that masking buys the agent anything beyond validity. Against masked random
it still loses **1/3 to 3/3 on seeded bugs and 9 to 15 on state coverage**. The
conclusion of §5's 2026-08-26 entry is unchanged, and is now established on a fair
comparison rather than an acknowledged-unfair one.

**4. `KeepBestByTrainingReturn` is confounded by the ε schedule, and on the masked arm it
selects noise.** The masked agent's "best" checkpoint was taken at step **481–521 of
4,000** on all three seeds — immediately after `learning_starts=500`, while ε is still
≈0.87. Its high rolling training return (+4.7 to +10.4) is what *masked-random* earns on
this reward, credited to a policy that was barely acting. Evaluated greedily at ε=0.05
that checkpoint is an untrained argmax, and it scores **worse than the final policy** on
coverage (3 states against 9). The unmasked arm does not show this because its training
return is bad throughout, so its best checkpoint is late (3841–3961).

Consequence: **the "(best)" rows are not a policy the algorithm achieved**, they are a
snapshot of the exploration schedule. Report the final-policy rows as the DQN result, and
treat the callback as needing a fix (compare at matched ε, or use a held-out greedy
evaluation) before it is cited again.

**5. New defect, found while explaining the reruns and NOT fixed: DQN training is not
reproducible at a fixed seed, because 23% of the observation is unstable.** The toy
fixture contains no self-links, so its numbers should have been unchanged by this work.
The `random_*` arms were — bit-for-bit, all three seeds. The `dqn_unmasked` arm was not:

| seed | before | after |
|---|---|---|
| 0 | 7 states, −6.95, BUG-03 | 4 states, −18.75, none |
| 1 | 6 states, −19.45, none | 8 states, −6.23, BUG-07 |
| 2 | 6 states, −8.42, none | **1 state**, −36.22, none |

Random policies never read the observation and DQN policies do, which locates the cause.
Probed directly (three identical `index.html → order-1.html` clicks): `state_key` is
stable, the visual and structural vectors are **identical**, and the network vector is
**unrelated** — cosine +0.018 and −0.002 between runs. Three fields survive
`canonicalize_text`: the HTTP `date` response header, `NetworkEvent.timestamp` (a
monotonic-clock float, which the `epoch_*` patterns do not match), and `duration_ms`
(differing in the 8th decimal). `HashEmbeddingEncoder` maps any byte difference to an
orthogonal vector, so **384 of 1,664 perception dims are fresh noise on every step that
touched the network.**

Three consequences, and the third is the one that matters:

1. Coverage and depth metrics are safe — `state_fingerprint` reads only URL and HTML.
2. The effect is far milder with the semantic encoders (MiniLM maps small text changes to
   small embedding changes) than with the hash stubs, which is the configuration every
   RL number in this document was measured on.
3. **Seed-to-seed spread understates the real variance.** There is run-to-run variance at
   a *fixed* seed as well, and on seed 2 it was the difference between 1 state and 6. Any
   future A/B on this harness needs the network trace canonicalized first, or it will be
   measuring clock jitter. This is the sixteenth instance of §8's defect class and the
   first to affect the *observation* rather than the judge's window.

**Left unfixed on purpose.** Fixing it changes every DQN number again and belongs with the
RL work rather than in a measurement-correction pass. It is the first thing to do before
any agent change, not after.

**Tests: 563 → 584.** Cost: 6h15m of compute for the two reruns.


### 2026-08-28 — Phase 0b: the observation is stable, training is now bit-reproducible, and none of the conclusions move

The sixteenth harness defect, fixed. `encode_modalities` canonicalized the network trace
with `canonicalize_text`, which left three volatile fields in it: the HTTP `date`
response header, `NetworkEvent.timestamp` (a monotonic float — 7 digits, so the `epoch_*`
patterns never matched it), and `duration_ms`, differing in its eighth decimal between
two identical navigations. `HashEmbeddingEncoder` maps any byte difference to an
orthogonal vector, so **384 of 1,664 observation dimensions were fresh noise on every
step that touched the network**, while the visual and structural blocks were bit-identical.

`canonicalize_network_trace` (`perception/normalization.py`) replaces it **on the
observation channel only**. `info["page"]["network"]` is untouched, so the judge window,
the trace corpus and the bug report keep exact timings and exact headers — verified
against a live navigation. Kept in the observation, because they identify application
state: method, status, resource type, failure, origin-relative URL, header *names*, and
bodies. Dropped or masked: clock timestamps, sub-millisecond precision, per-request and
per-session header values. `duration_ms` is **bucketed** rather than dropped (`<50ms`,
`<200ms`, `<1s`, `<5s`, `>=5s`) so the `slow_response` signal survives at a resolution
the application can actually be responsible for. Same-origin URLs collapse to `<ORIGIN>`
so an ephemeral test-server port stops making one page look like two across processes;
foreign origins are left intact, because on-site versus off-site is the distinction that
matters.

**Training is now bit-reproducible.** Two 600-step DQN runs at an identical seed:
action sequence, reward sequence, every stored observation, and the final Q-network
weights all identical, `max |weight delta| = 0.000e+00`. The observation was the *entire*
source of non-determinism — not CUDA, not browser timing. **Caveat: measured at 600 steps
in one process on a static fixture.** A 4,000-step run has more opportunity for a genuine
browser-timing divergence, and that is not yet tested.

**Corrected deep-flow baseline** (`compare_agents_deep_hash_3seed_v3.json`), same
protocol, 3 seeds:

| policy | reward | valid | states | findings | mean depth | max depth | completions |
|---|---|---|---|---|---|---|---|
| dqn_unmasked | −7.9 [−9.8, −7.4] | 78% [73–82] | 6 [5–8] | 0 | 0.14 [0.13–0.15] | 1 | 0 |
| dqn_masked | −8.8 [−23.7, −2.5] | **100%** | 8 [8–9] | 0 | 0.03 [0.01–0.14] | 1 | 0 |
| random_unmasked | −30.0 | 18% [16–18] | 6 [6–7] | 0 | 0.00 | 0 | 0 |
| random_masked | **−0.3** [−1.0, +0.1] | **100%** | 8 [8–9] | 0 | 0.04 [0.01–0.04] | 1 | 0 |

**The control held exactly.** All six `random_*` cells are byte-identical to the Phase 0
run — same episode rewards, same states, same depth — which is what must happen when a
change touches only the observation. Every difference in the table is in a DQN row.

**Corrected toy baseline** (`toy_dqn_comparison_v3_s{0,1,2}.json`):

| arm | reward | seeded bugs /3 | states | findings | valid |
|---|---|---|---|---|---|
| dqn_unmasked (best) | −18.4 [−24.7, −6.5] | **0** (every seed) | 5 [5–7] | 0 | 90% |
| dqn_unmasked (final) | −26.4 [−27.5, −5.3] | **0** (every seed) | 5 [4–6] | 0 | 94% |
| dqn_masked (best) | −27.3 [−34.1, −15.8] | 1 [0–2] | 3 [2–8] | 4 [0–8] | **100%** |
| **dqn_masked (final)** | **−3.2** [−8.3, −1.7] | **2 [1–3]** | 8 [5–13] | 6 [1–8] | **100%** |
| random_full | −31.2 | 0 [0–1] | 2 [2–7] | 0 [0–4] | 12% |
| **random_masked** | **+5.2** [+2.8, +12.0] | **3 [2–3]** | **15 [14–16]** | 6 [5–12] | **100%** |

**What changed, what did not.**

- **Nothing about the deep flow.** No policy passes stage 1 in any of the 60 evaluation
  episodes — `max_depth ≤ 1`, zero receipts, zero completions, zero episodes even
  reaching stage 2. Masking still fixes validity completely (100%, no spread) and still
  does nothing for depth (0.03 masked against 0.14 unmasked). The rank inversion still
  holds on every seed: `random_masked` earns the best reward while going least far.
- **Variance fell where the fix predicted it would.** `dqn_unmasked` mean depth tightened
  from 0.18 [0.00–0.26] across the two earlier runs to **0.14 [0.13–0.15]**, and its
  reward range from [−13.1, −7.1] to [−9.8, −7.4]. `dqn_masked` did *not* tighten — its
  reward range widened to [−23.7, −2.5], with seed 2 collapsing. Removing observation
  noise does not remove training instability; it just stops the two being confounded.
- **The masked DQN's final policy is the best DQN this project has produced**: 2/3 seeded
  bugs [1–3] and 8 states, against 0/3 and 5 for the unmasked head. It still loses to
  masked random (3/3, 15 states), but the gap on *bugs* is now one bug at the median
  rather than three.
- **An internal consistency check worth recording.** `dqn_masked` (best) is essentially
  unchanged between the two runs — states 3/8/2 both times, findings 8/4/0 both times —
  because its checkpoint is taken at step ~500, before the observation has been used for
  any meaningful learning. `dqn_masked_final`, which trains on 4,000 steps of it, moved
  substantially. Exactly the asymmetry the fix predicts.

**`KeepBestByTrainingReturn` is confirmed broken, on all three seeds of both runs.** The
masked arm's "best" checkpoint is taken at step **481–521 of 4,000**, immediately after
`learning_starts=500`, while ε ≈ 0.87. Its high rolling training return (+4.7 to +10.4) is
what *masked random* earns on this reward, credited to a policy that was barely acting.
The unmasked arm never shows this because its training return is bad throughout, so its
best checkpoint is late (3,961). **Cite the final-policy rows, not the best-checkpoint
rows**, until the callback compares at matched ε or uses a held-out greedy evaluation.

**Tests: 584 → 598.** Cost: 6h17m.


### 2026-08-28 (later) — the reveal bonus was a breadth bonus wearing a depth bonus's name

Phase 4 added a "newly-revealed action" reward term, on the reasoning that a gated flow
reveals its next control once its input is valid, so the action set *grows* in response
to real progress — the one exploration signal that is genuinely depth-correlated. The
implementation diffed consecutive action sets. **A navigation replaces the entire action
set**, so every link on the destination page counted as newly revealed, and the bonus
scaled with how many links that page happened to have.

Found by decomposing the reward rather than by reading the code. Masked random, 200 steps
on the deep-flow fixture:

| term | total | per episode |
|---|---|---|
| **reveal** | **+87.00** | **+17.40** |
| novelty | +16.07 | +3.21 |
| step cost | −11.30 | −2.26 |
| **episode reward** | **+70.62** | **+14.12** |

The reveal term was **123% of the total return**, and splitting its payments by whether
the step changed page gave **81.00 of 87.00 — 93% — from navigation**. The identities
paid for were ordinary nav links: `Filing`, `Delivery times`, `Returns`. So the term
built to reward depth was rewarding breadth, harder than the flat novelty bonus it was
meant to correct, and it produced a perfectly inverse ordering of reward against depth on
seed 0: `random_masked` +14.12 at depth 0.04, `dqn_masked` +4.13 at 0.19, `ac_dqn` −7.51
at **0.47**. The agent that went deepest earned the least.

**The correction.** A gate reveals a control on the page you are *already on*. Three
conditions now all have to hold: the URL is unchanged, the action set genuinely **grew**
(a same-page swap of one control for another is not a reveal), and the identities are new.
On a navigation the baseline is re-anchored to the new page and nothing is paid, so the
next genuine gate on that page is still detected.

**Validated on the live fixture, both directions.** A scripted walk of the whole flow,
`index.html` → `receipt.html`:

| step | page | reveal | revealed |
|---|---|---|---|
| nav index → order-1 | order-1 | **0.00** | |
| **gate: select product** | order-1 | **1.50** | `[CLICK, Continue]` |
| nav order-1 → order-2 | order-2 | **0.00** | |
| **gate: type quantity** | order-2 | **1.50** | `[CLICK, Continue]` |
| nav order-2 → order-3 | order-3 | **0.00** | |
| **gate: type email** | order-3 | **1.50** | `[CLICK, Continue]` |
| nav order-3 → order-4 | order-4 | **0.00** | |
| nav order-4 → receipt | receipt | **0.00** | |

3/3 gates pay, 0/5 navigations pay. (`order-4`'s `place-order` is always visible, so the
flow has three reveal-gates and five plain navigations, not four gates — worth stating
because the answer key calls it a four-stage flow.)

Rerunning the decomposition that found the defect: **navigation reveals 16 payments /
81.00 → 0 payments / 0.00**; same-page gates 1 payment / 1.50, which is masked random
opening one gate by chance. Mean episode reward +14.12 → **−2.98**.

**Nothing else moved, and the check is exact.** `novelty` (16.07), `step_cost` (−11.30)
and `deterministic` (0.00) are identical to the pre-fix run to the last decimal — a
random policy ignores reward, so an identical novelty total means the identical states
were visited in the identical order. On the toy site, masked random still finds **3/3
seeded bugs (BUG-03, 04, 07), 14 unique states, 12 distinct findings, 100% valid
actions**, with byte-identical trigger counts to the v3 baseline. State fingerprints,
archive mechanics, action masking, deterministic detection and the judge window are all
untouched.

**Tests: 658 → 665**, including the four cases that pin the correction: same-page gate
pays, navigation does not, same-page replacement without growth does not, and an already-
known action is never re-revealed.

**Consequence for the Phase 1–4 evaluation.** Every AC-DQN number measured before this
fix was produced against the defective reward and must not be used to judge the
architecture — including the seed-0 result (mean depth 0.47 against `dqn_masked`'s 0.19)
that looked encouraging. That agent was trained to maximise navigation. The comparison
has to be rerun.


### 2026-08-29 — the first flow completions in the project's history, and the one seed that still fails

Two changes, made one at a time and measured separately against a preserved baseline:
**dueling** fixed the value function's inability to discriminate between actions, and
**C1** (a path-keyed reveal ledger) removed a duplicate-payment exploit that was making
the agent refuse to finish the flow. Together they take the deep-flow policy from *never
passing stage 1* to *completing the whole order on two seeds out of three*.

#### 1. Current established result

**Dueling fixed the Q-value discrimination problem.** Before it, the within-state Q
spread fell from 24% of |Q| to 5% over training while mean |Q| grew 23x — the network
had converged on `Q ~= V(s)` with a vestigial action term. The measurable consequence
was that the flow-advancing TYPE action ranked **19th, 24th and 7th of 24** on
`order-2.html` and **23rd, 22nd and 8th of 24** on `order-3.html`. With
`Q = V(s) + A(s,a) - mean_a' A(s,a')` it ranks **1st on every seed at both typing
gates**, and every one of 15 evaluation episodes passed stage 1 (against 5/15 before).

**C1 removed the duplicate-reveal farming exploit.** `Back` drops the query string, so
`order-3.html` and `order-3.html?product=a&quantity=42` are different `state_key`s
despite being the same page with the same gate. The reveal ledger, keyed on `state_key`,
therefore paid the same gate twice, and all three dueling seeds learned the loop
`order-4 -> Back -> order-3 -> TYPE email -> Continue -> order-4` instead of finishing.
Keying the ledger on the URL **path** fixes it. Verified on recorded trajectories through
the shipped code: **FLOW unchanged at +15.20**, BREADTH unchanged at +2.05, FARM 7.00 ->
5.50 losing **exactly one reveal payment** (6.00 -> 4.50) with novelty, step cost and
repetition identical to the decimal.

**C1 produced the first flow completions in this project's history.**

| | dueling baseline | **C1** |
|---|---|---|
| **flow completions** | **0/15** | **10/15** |
| receipt reached (steps) | 0 | 10 |
| policy max depth (s0/s1/s2) | 4 / 4 / 4 | **5 / 5 / 4** |
| per-episode max depth | all `[4,4,4,4,4]` | s0 `[5,5,5,5,5]`, s1 `[5,5,5,5,5]`, s2 `[4,4,4,4,4]` |
| episodes past stage 1 | 15/15 | 15/15 |
| archive max depth | 5 / 5 / 5 | 5 / 5 / 4 |
| unique states | 20 / 16 / 21 | 15 / 22 / 19 |
| eval TYPE actions | 44 / 49 / 48 | 37 / 46 / 44 |
| mean episode reward | 11.34 / 5.96 / 11.73 | 5.92 / 6.85 / 7.16 |
| **distinct findings / DEEP-01** | **0** | **0** |

Seeds 0 and 1 complete the order on **every** evaluation episode; seed 2 completes none.
**The old reward-farming loop is absent from all three greedy traces.** Training reveal
totals fell as predicted (504 -> 321, 232.5 -> 193.5, 381 -> 220.5) while novelty and
repetition moved little, which is what removing only duplicate payments looks like.

**Two things this does not show.** Distinct findings are still **0** and DEEP-01 has
still never been reported: the fixture's deterministic ceiling is 0 and the judge is
disabled in these runs, so reaching the receipt is necessary for finding that bug but not
sufficient. And mean episode reward *fell* on two of three seeds while behaviour
improved — another instance of this project's recurring lesson that reward is a poor
proxy for the objective.

#### 2. Causal evidence

Q-values at `order-4.html`, recorded during evaluation:

| seed | argmax | Q(Place order) | Q(Back) | gap | completions |
|---|---|---|---|---|---|
| 0 | **Place order** | **+0.3094** | +0.1161 | +0.193 | **5/5** |
| 1 | **Place order** | **−2.4210** | −2.4624 | +0.041 | **5/5** |
| 2 | Back | −2.5458 | **−2.4619** | −0.084 | **0/5** |

**The two seeds where `Place order` outranks `Back` complete 5/5; the seed where `Back`
wins completes 0/5.** The behavioural split follows the Q ordering exactly, which is a
direct causal link rather than a correlation.

**This establishes the remaining failure as a small Q-estimation/sampling issue rather
than a demonstrated reward-ordering error.** The objective is correctly ordered after C1
— the 3-step target that `n_step=3` actually fits gives `Place order` 2.822 against
`Back`'s 2.625 — and seed 2's network inverts a correctly-ordered target. Both surviving
margins are tiny: seed 1 completes on **+0.041** and seed 2 fails on **−0.084**, roughly
1.7% of |Q| ~ 2.5. Completion is currently decided by noise around a near-tie.

#### 3. Current unresolved question

**Replay *sampling* of the final `Place order -> receipt` transition has not been
measured.** Only collection counts are known, and only indirectly (steps recorded at
depth 5). The replay buffer is not saved with the checkpoints, so how often that
transition was actually drawn by PER during training is **unmeasured**.

Therefore "seed 2 fails because of sparse experience" is a **hypothesis, not a result**.
It is consistent with the evidence — seed 2 reached the receipt zero times in its own
training, and evaluation visits to `order-4` differ sharply across seeds (5 / 33 / 10) —
but it has not been demonstrated.

#### 4. Next step — diagnostic only, no fix

Diagnose why seed 2 fails the final `Place order` transition while seeds 0 and 1 succeed.
**Do not implement any fix during this diagnostic.**

Required measurements:

* `Place order -> receipt` transitions **collected** during training, per seed.
* The number and fraction of those transitions **sampled by PER**, if reconstructable
  from existing artefacts. **If it is not reconstructable, record it explicitly as
  unmeasured rather than estimating it.**
* `Q(Place order)` and `Q(Back)` at `order-4.html` across training windows/checkpoints,
  for all three seeds.
* The Q-gap `Q(Place order) - Q(Back)` over training.
* Whether seed 2 **ever** had `Place order > Back` at any point during training.
* Whether the target/return associated with `Place order` is correctly ordered relative
  to `Back` — i.e. whether the fault is in the target or in the fit to it.
* All three seeds compared under **identical** measurements.

#### 5. Constraints

No changes to the reward, architecture, exploration, PER, n-step, archive, or
environment/fixture. No new machinery except what is strictly required to take the
measurements above. The C1 baseline and every earlier artefact are preserved
(`diag_ac_3seed_deep_dueling.json`, `diag_q_spread_dueling.json`,
`diag_ac_3seed_deep_c1.json`, and six checkpoints — the `_BASELINE`-suffixed copies were
byte-identical duplicates and were removed in the 2026-08-31 finalization pass, which
renamed nothing and deleted no unique data), so the next change stays
attributable. **No long experiment unless the diagnostic itself requires one, and the
reason is to be stated before it is started** — note that per-window Q at `order-4`
cannot be recovered from the saved artefacts, because only final checkpoints were kept,
so obtaining a Q-gap *trajectory* would require re-training with added logging.

#### 6. Decision rule

After the diagnostic, identify which of these the evidence supports before proposing
anything:

* **A.** insufficient or sparsely sampled `Place order` experience;
* **B.** adequate experience but unstable Q estimation;
* **C.** a systematically incorrect target/objective;
* **D.** another concrete implementation issue.

**Tests: 682 -> 691** (9 executable reward invariants, including that the FLOW return is
unchanged by the ledger scope, that a gate pays once per page per episode however the URL
was decorated, and that `Place order` beats `Back` under the 3-step target).

**Next action: diagnose the final-transition Q-gap and experience/sampling before
changing anything.**

> **Superseded 2026-08-29 (later).** That diagnostic is no longer the next step. An
> audit found the fixture's only seeded bug was not detectable at all, so every RL
> number measured on it was a proxy that could not convert. See the entry below.


### 2026-08-29 (later) — audit + Phase 1: the deep-flow benchmark was measuring nothing, and now it measures something

An audit of the whole project, then one isolated fix to the thing the audit found
broken. **No agent, reward, architecture, PER, n-step, epsilon, archive or evaluation
protocol was touched.** Diagnostics cost ~8 minutes; the fix is 7 behavioural lines.

#### 1. What the audit retracted

| Claim | Status after audit |
|---|---|
| *"The behavioural split follows the Q ordering exactly — a direct causal link rather than a correlation."* | **INVALID as stated.** The table is one record per seed. Seed 1 has **33** `order-4` records and `Place order` is argmax in only **4**; it is absent from the top six in 24. Supportable claim: the ordering *at first arrival* predicts completion, on 3 points. |
| *"The objective is correctly ordered after C1 … seed 2's network inverts a correctly-ordered target."* | **UNSUPPORTED.** See §2 below — the ordering depends on counters the agent cannot observe and inverts in realistic regimes. |
| *"10/15 flow completions"* | **MISLEADING denominator.** First-arrival Q at `order-4` is bit-identical across episodes within a seed, so the decisive choice is made **3 times, not 15**. |
| *"Dueling fixed the Q-value discrimination problem."* | **PARTIALLY TRUE.** Verified at the typing gates (rank 19/24, 24/24, 7/24 → **1/24 on all three seeds** at `order-2`; 23/24, 22/24, 8/24 → **1/24** at `order-3`). But at `order-4` the argmax is `CLICK:Back` on **all three seeds** post-dueling. Dueling did not fix the final step. |
| *"A DQN cannot learn from a reward it never receives — zero successful trajectories."* | **Falsified as a general account.** The dueling baseline archived depth-5 cells on **all three seeds** (2/3/4) — it reached the receipt during training — and still completed **0/15**. Experience was present; behaviour was absent. |
| *"first flow completions in the project's history"* | Needs *"by a learned policy"*. Go-Explore reached the receipt at 4,000 steps (n=1). |
| AC-DQN vs `dqn_masked` headline | **INVALID.** `compare_agents_deep_hash_3seed_v3.json` predates the reveal bonus, count-decayed novelty and the rescaled repetition penalty (its `args` lacks `arms`/`p_return`/`n_step`). The two agents were trained under **different reward functions**, on top of ~10 other differences. |

**Relative Q spread is retired as a metric.** It divides by `|q_mean|`, and post-dueling
`q_mean` crosses zero: seed 2's `order-4` relative spread reads **24.93** against seed
0's 0.85, purely because its mean sits at 0.054. Use the advancing action's rank, or
absolute spread against the reward scale (~1.5–2.5 per novelty+reveal payment).

#### 2. The reward is not Markov, and C1 closed only half the farm

Two of six live reward terms depend on state the agent cannot see. `novelty_bonus`
divides by `state_run_visits`, a **run-level** counter; `reveal_bonus` reads
`_paid_reveals[(url_path, identity)]`, an **episodic hidden ledger**. Neither is in
`EPISODE_CONTEXT`, which carries only step fraction, episode states seen, is-novel,
repeat count and two `FindingLedger` scalars. Both terms were added *after*
`EPISODE_CONTEXT_DIM=6` was introduced to fix exactly this class of defect.

Replaying the shipped invariant's own trajectories through the real
`ExplorationTracker` and `compose_reward`, varying **only** run-level visit counts:

```
 shallow   deep  receipt |  Place order      Back    margin   winner
       0      0        0 |        2.822     2.625    +0.197   Place order   <- the shipped test
       5      2        1 |        1.363     1.370    -0.007   ** BACK **
      10      5        2 |        1.023     0.868    +0.155   Place order
     100     40        8 |        0.381     0.119    +0.262   Place order

FLOW continuing onto pages already seen THIS episode (what a 40-step episode looks like):
  deep visits=  0 | Place +0.802   Back +2.625   margin -1.823   ** BACK **
  deep visits=  5 | Place +0.802   Back +0.868   margin -0.065   ** BACK **
  deep visits= 20 | Place +0.802   Back +0.303   margin +0.499   Place
```

C1 genuinely removed the duplicated **reveal** payment (+1.5) — verified. But `Back`
drops the query string, so `order-3.html` and `order-3.html?product=a&quantity=42`
remain different `state_key`s, and each lap still mints three states that pay episodic
**novelty**. `canonicalize_url` keeps non-volatile query values, which is what makes
them distinct. **C1 changed the hidden state the reward depends on and closed one of two
farm sources.** The shipped invariant pins the single all-zero point, which is the most
favourable framing of a comparison that inverts elsewhere.

Supporting measurement already in the artefacts: mean reward per sampled transition
falls **−0.115 → −0.413** over 1,500 steps while `q_mean` falls −0.08 → −1.88
(`diag_buffer_per`). Identical behaviour pays less later, with nothing marking it.
**Dueling compensates for that drift (V absorbs it, A carries the comparison) rather
than fixing it.**

#### 3. The fifteenth harness-as-application defect — and the first to delete a true positive

No deep-flow corpus existed and the judge had **never been run on this fixture**. A
scripted 9/9-step DEEP-01 walk was captured and judged with the version-pinned
`qwen2.5:7b-instruct` at temperature 0. Result **before any fix**: `compact` flagged
**8/8 windows, 0 correct, DEEP-01 missed** (the receipt step typed `dead_control`,
"no observable change", quoting evidence showing the URL *did* change); `detailed`
flagged 6/8, 0 correct, receipt step **`is_bug=False`**.

The cause was in the record, not the model. At `render_step`, on a navigation the line
labelled `page text` was `added` — the **set difference against the previous page**.
`order-4.html` and `receipt.html` share `Product:` / `Quantity:` / `Contact:` and both
echo product and email correctly, so every shared line was suppressed and the judge was
handed an orphaned `1` with no label attached. The comment there asserted "on a
navigation this is simply the new page"; it was not.

**This generalises past the fixture.** Any bug of the form *"a value carried through a
flow is echoed incorrectly on a later page"* was structurally invisible — the whole
`broken_flow` class. Measured across the committed corpora: **14–23% of steps are
navigations, and 59–100% of those share at least one text line with the page they
replaced** (100% on `gseed-scripted`). The toy site's cross-page BUG-01 survived only
because its evidence happened to sit in the URL.

#### 4. The fix (N1), and nothing else

`judge/window.py`, 7 behavioural lines: on a navigation, render the destination page's
**own** text (`after.text`, already bounded by `_TEXT_LINE_MAX`) instead of the diff.
`text gone` stays suppressed on navigation, which is where the measured lean-window
saving actually came from (24% of characters against `added`'s 14%). Off-navigation
diffing is unchanged. `render_page_context` already printed the *starting* page's full
text, so this also removes an internal inconsistency.

**Tests: 696 → 701** (`tests/unit/test_judge_window.py`), five pinning the pattern: the
label survives a navigation, the wrong value survives, `Quantity: | 1` stays adjacent,
the *correct* echoes survive too (a judge cannot call one field wrong without seeing the
others are right), and in-place diffs are untouched. Full unit suite green.

#### 5. Benchmark results

**DEEP-01, same 8 windows, only the renderer changed:**

| | before fix | after fix |
|---|---|---|
| `compact` — DEEP-01 | missed (`dead_control`) | **still missed** (`is_bug=False`) |
| `detailed` — DEEP-01 | missed (`is_bug=False`) | **DETECTED** — `broken_flow`, sev 1.0, *"the quantity on the receipt page is shown as 1 instead of 42"* |
| windows flagged | 8/8 compact, 6/8 detailed | 6/8 both |

**Second trajectory, the realistic RL case** (`deep01-noisy`: 5 filler steps before
`Place order`, so the review page carrying the correct `42` falls **outside** the K=6
window, leaving only the receipt's `1` and the URL's `quantity=42`):

| | detailed | compact |
|---|---|---|
| DEEP-01 | **DETECTED** — `broken_flow`, *"displayed as 1 instead of 42, which does not match the expected value from /order-4.html"*, evidence grounded | not detected |

So **DEEP-01 detection is 2/2 under `detailed` and 0/2 under `compact`**, and it
survives the review page leaving the window. `detailed` is the configuration of record
for this fixture.

**Toy-scripted regression, both arms in one session** (the project's own rule; the
recorded 2/6 from 2026-08-10 is a different session and is not a valid comparison):

| | before fix | after fix |
|---|---|---|
| judge total | **10/10** | **10/10** |
| semantic / deterministic | 7/7 · 3/3 | 7/7 · 3/3 |
| positive verdicts | 23 | 23 |
| false positives | 3/6 | 3/6 |
| discrimination | +15% | +15% |
| evidence grounded | 23/23 | 23/23 |

**Every aggregate metric is identical.** Individual verdicts moved on 6 of 37 windows
(the control-episode false positive shifted from step 59 to step 57), but a verdict flip
was independently observed between two identical `compact` runs at temperature 0, so
**that movement is inside the run-to-run noise band and is not attributable to the fix.**

Cost: toy-scripted mean window **1317 → 1360 chars (+3.3%)**; all corpora validate clean.
The lean-window result survives.

Reports: `judge_deep01_after_render_fix.json`, `judge_deep01_noisy_window.json`,
`judge_toy_scripted_before_render_fix.json`, `judge_toy_scripted_after_render_fix.json`.

#### 6. What this means

**`distinct_findings = 0` on the deep-flow fixture never carried information about the
RL policy.** The payoff every Phase 1–4 result was optimizing toward did not exist to be
collected. It now does, under `detailed`. The fixture is usable as a bug-discovery
benchmark for the first time — with the precision caveat below.

#### 7. What remains uncertain

* **Precision on this fixture is poor**: 5 false positives on a wholly-correct
  trajectory (detailed, 8 windows). Usable for "did the policy reach a state where
  DEEP-01 is reported", **not** usable as a reward signal.
* **The local judge is not reproducible run-to-run at temperature 0.** One verdict
  flipped between identical `compact` runs; 6/37 toy verdicts moved across the
  controlled pair. All fine-grained judge comparisons inherit ±1 noise.
* **DEEP-01 detection is n=2 trajectories, one model, one prompt style.**
  `gpt-oss:120b-cloud` on this corpus is **UNMEASURED**.
* Still **UNMEASURED** from the audit: PER sampling of the `Place order → receipt`
  transition (the only sampling artefact is a 1,500-step run whose buffer never exceeded
  depth 1, and which shows PER at **1.15× uniform**); its gradient contribution; and
  whether the `order-4` Q ordering develops during training — only end-of-training
  snapshots were kept, so "did seed 2 ever prefer `Place order`?" is unrecoverable.

#### 8. Incidental findings, recorded not fixed

* **`ActionFeatureExtractor.record_taken()` is never called outside tests.** `taken_here`
  and `taken_this_episode` are identically 0.0 in every observation ever produced — two
  of ten scalars dead. The comment in `compare_agents.py` about sharing the extractor so
  visit counts stay consistent describes behaviour that does not occur.
* **A `<select>` exposes exactly one action.** `action_registry` sets
  `target_option = options[-1]`, so only the last option is ever selectable and any flow
  gated on a different one is unreachable by construction. Discovered when a scripted
  step naming `pens-50` could not be matched.
* `ac_dqn.train` applies `gamma ** n_step` to the shorter tail windows `replay._commit`
  flushes at episode end (≤2% bias at γ=0.99).
* **Hypothesis (untested):** `order-4` is unsolved because `Place order` and `Back` are
  both CLICKs with identical scalars, so only a 32-dim label hash separates them — and
  `Place order`'s nearest neighbour in that space is `Cancel order` (cosine **+0.42**),
  the action that abandons the flow. Every gate whose advancing action is
  *type*-distinguishable was solved by dueling; the one gate where it is not, was not.

**Next action: Phase 2 — the contemporaneous baseline.** `random_masked`,
`dqn_masked` and `ac_dqn` on the deep fixture, one session, the **current** reward,
hash encoders, 4,000 steps, 3 seeds, unchanged evaluation protocol. This is the
comparison the audit found missing, and no agent may be modified during it.

**Constraint: nothing else changes until that baseline is complete.** No reward weights,
no architecture, no PER, no n-step, no epsilon, no new fixture.


### 2026-08-29 (later still) — Phase 2: the contemporaneous baseline. AC-DQN survives the reward control, and the reward/objective inversion is now visible inside one arm

The comparison the audit found missing. All three arms in **one session**, the **current**
reward, hash encoders, 4,000 training steps, 3 seeds, 5 × 40-step evaluation episodes at
ε=0.05, every arm evaluated from the landing page. Nothing was modified during the run.
`reports/compare_agents_deep_hash_3seed_v4_contemporaneous.json`, 2h30m of compute.

| arm | reward | valid | states | mean depth | max depth | receipt | **completions** | past stage 1 | finds |
|---|---|---|---|---|---|---|---|---|---|
| random_masked | −0.74 [−1.07, −0.16] | 100% | 8 [8–9] | 0.04 | 1 | 0 | **0/15** | 0 | 0 |
| dqn_masked | 0.85 [−1.12, 5.81] | 100% | 11 [9–13] | 1.20 [0.10–1.55] | 3 [1–3] | 0 | **0/15** | 5 [0–5] | 0 |
| **ac_dqn** | **5.19** [4.71, 10.18] | 100% | **14 [12–23]** | 1.37 [0.90–1.50] | **5 [4–5]** | **5 [0–5]** | **10/15** | 5 | 0 |

Per seed — `flow_completions` / max depth: `random_masked` 0/0/0 at depth 1;
`dqn_masked` 0/0/0 at depth 3/1/3; `ac_dqn` **5/5/0** at depth 5/5/4.

**1. The AC-DQN advantage survives the reward control.** This was the audit's main open
question, and the answer is unambiguous: **10/15 completions against 0/15 and 0/15**,
under an identical reward, in one session. The 2026-08-29 C1 result is now **replicated
in an independent run** — the same 5/5/0 split by seed, the same 10/15 total.

**2. The reward change is also real, and it is not what produces completions.** The flat
head went from the v3 baseline's mean depth 0.03 [0.01–0.14] / max depth 1 to
**1.20 [0.10–1.55] / max depth 3**, and now leaves stage 1 on 2 of 3 seeds where it
previously never did. But it **never once reaches `order-4`** (`episodes_reaching_review`
= 0 on every seed) and completes nothing. So the audit was right that the old comparison
conflated reward with architecture, and wrong to suggest the reward might account for
most of the gap: it accounts for the climb to stage 3, and the architecture accounts for
finishing.

*Corrected mid-run:* seed 0's `dqn_masked` jump to 1.55 was initially read as a robust
reward effect. Seed 1 returned 0.10. The flat head's response to the new reward is
strongly seed-dependent, and only the 3-seed median is quotable.

**3. The control held twice.** `random_masked` returns mean depth 0.04 / 0.04 / 0.01 and
max depth 1, **byte-identical to the v3 baseline** on the first two seeds. A random
policy ignores reward, so this is what must happen, and it confirms the `dqn_masked`
movement is attributable to the reward rather than to environment drift.

**4. `mean_depth` is not a proxy for the objective and must stop being quoted as one.**
`ac_dqn` seed 0 has mean depth **0.90** — lower than `dqn_masked` seed 0's **1.55** —
while completing the flow 5/5 against 0/5. A policy that finishes and leaves scores lower
than one that camps mid-flow without finishing. `flow_completions` and `reached_receipt`
are the objective metrics, exactly as the fixture's answer key says.

**5. The sharpest reward/objective inversion this project has measured, and it is inside
a single arm.** `ac_dqn` seed 2 earns the **highest mean episode reward in the whole
table — 10.18 — while completing 0/5**. The two seeds that complete 5/5 earn 5.19 and
4.71. `dqn_masked` seed 2 earns 5.81, above both completing `ac_dqn` seeds, while never
reaching `order-4`. **Within one arm, under one reward, the highest-reward seed is the
one that fails the objective.** Previous instances of this were across checkpoints or
across policies; this one is across seeds of the same agent, which is much harder to
explain away.

**6. What seed 2 actually does.** From the matched C1 diagnostic, which reproduced the
same failure: seed 2 spends **75 of 200 evaluation steps on `order-2` and 63 on
`order-3`**, reaching `order-4` in all five episodes but only 13 steps, and never the
receipt. Its action repertoire is `CLICK:Continue` ×47, **`BROWSER_BACK` ×42**,
`TYPE:quantity` ×34. It oscillates across the middle gates instead of finishing. The
completing seeds do not: seed 0 is `TYPE:quantity` ×27 / `CLICK:Continue` ×27 spread over
the whole flow, seed 1 concentrates on `order-3`/`order-4`.

**Which reward term funds that oscillation was UNMEASURED here, and is now measured —
see the Phase 3 entry below. The reading offered in this paragraph turned out to be
wrong**: seed 2 is *not* behaving optimally for the reward it was given. Completing the
flow pays considerably more than oscillating. Retained as written so the correction is
visible rather than silently edited away.

**7. Cost, reported rather than absorbed.** `ac_dqn` receives 111–218 replayed browser
actions on top of its 4,000 env steps (4,111–4,218 total, +3–5%), `returns_failed` = 0 on
every seed. Training wall clock: `dqn_masked` ~1,350 s/seed, `ac_dqn` ~1,445 s/seed.

**8. DEEP-01 detection is not directly measured here, deliberately.** `compare_agents.py`
saves no checkpoints, and adding checkpointing mid-comparison would have changed the
harness during the run that exists to be controlled. What can be composed: Phase 1
established that a completed-flow trajectory yields a correct `broken_flow` verdict under
the `detailed` prompt (2/2, including the case where the review page falls outside the
K=6 window), and Phase 2 establishes that `ac_dqn` completes on 10/15 episodes and the
other two arms on 0/15. **Treat that as an inference, not an end-to-end measurement.** A
direct number needs one follow-up run with checkpoint saving.

**Next action: Phase 3 — diagnose seed 2, cheapest instrument first.** The failure is now
**reproducible across two independent runs**, so it is a real target rather than noise.
The evidence points at reward, not at experience scarcity: seed 2 earns the most reward,
visits the most states (23), reaches `order-4` in every episode, and declines to finish.
Before any 3-seed run, record the **per-term reward decomposition** for a single seed-2
rollout and establish which term pays for the oscillation. Do **not** assume sparse
experience: the audit already falsified that as a general account (the dueling baseline
had depth-5 experience on all three seeds and completed 0/15), and PER sampling of the
final transition remains UNMEASURED.

**Constraint: no agent, reward, architecture, PER, n-step, epsilon or archive change
until that decomposition exists.**


### 2026-08-29 (later still) — Phase 3, diagnostic 1: the reward is not why seed 2 fails, and the "reward farm" reading is falsified

The decomposition asked for above, taken the cheap way: script seed 2's behaviour and
the completing behaviour through the **real** env, tracker and composer at an equal
40-step budget, three consecutive episodes each so run-level novelty decay is visible.
No training, no code changes. `reports/diag_phase3_reward_decomposition.json`.

| arm (40 steps) | ep0 | ep1 | ep2 | novelty | reveal | step cost |
|---|---|---|---|---|---|---|
| OSCILLATE — `order-2 ↔ order-3` via BROWSER_BACK, seed 2's repertoire | **−1.25** | −3.66 | −4.18 | +5.00 → +2.07 | **+3.00** | −2.00 |
| FLOW — complete the order, then explore | **+13.70** | +8.30 | +6.54 | +14.00 → +6.84 | +4.50 | −2.00 |

**1. Completing the flow out-earns the oscillation by 11–15 points per episode.** The
reward is *not* inverted between these two behaviours; it strongly and correctly prefers
finishing. **The hypothesis that seed 2 had found a reward-optimal farm is false**, and
the Phase 2 paragraph suggesting it has been struck through above rather than deleted.

**2. C1 is confirmed working in the live environment.** The oscillation re-opens the
`order-2` quantity gate twelve times and `reveal` stays pinned at exactly **+3.00** — one
payment per gate per path per episode. The path-keyed ledger does what it claims. (The
audit's separate point stands: the *novelty* half of the Back-loop was never closed, and
that is visible here as the oscillation still earning +5.00 of novelty on its first
episode. It is simply nowhere near enough to compete with finishing.)

**3. So what is seed 2 doing?** Not a tight farm — a *broad* one. It reaches 23 distinct
states against the completing seeds' 14 and 12, wandering across `order-1..order-4` and
the shallow pages and collecting novelty from many first visits, while never taking the
final click. The scripted FLOW arm does **both** — finishes *and* then explores eight
fresh pages — and earns the most of anything measured. **The reward's global optimum is
"complete, then explore", and no learned seed found it**: seeds 0 and 1 complete but
explore poorly (5.19, 4.71), seed 2 explores well but never completes (10.18).

**4. What this re-points the diagnosis at.** With reward ordering eliminated for the
decisive pair, seed 2's failure is a genuine **value-estimation / credit-assignment**
failure at `order-4` — the agent is leaving reward on the table, not chasing it. That
puts the audit's untested H1 in front: `Place order` and `Back` are both CLICKs with
identical scalar features (`navigational`, `has_href`, `is_form_control`, `value_ordinal`,
`opens_new_tab` all equal; `taken_here`/`taken_this_episode` dead because
`record_taken()` is never called), so **only a 32-dim hashed label separates them** — and
in that space `Place order`'s nearest neighbour is `Cancel order` at cosine **+0.42**,
the action that abandons the flow. Every gate whose advancing action is *type*-
distinguishable was solved by dueling; the one gate where it is not, was not, on 3/3
seeds.

**Still UNMEASURED, and now the next thing to measure:** collection count of
`Place order → receipt` per seed, whether PER actually samples it, its TD error/priority
relative to ordinary transitions, and whether the `order-4` Q ordering develops during
training. The only existing sampling artefact is a 1,500-step run whose buffer never
exceeded depth 1 and which shows PER at **1.15× uniform** — vacuous for this question.

**Next action: Phase 3, diagnostic 2 — retrain seed 2 alone (~25 min) with read-only
instrumentation** logging (a) `Q(Place order)` vs `Q(Back)` at `order-4` per training
window, (b) collection count of the final transition, (c) how often PER draws it. One
seed, one question, no agent change. Do not run a 3-seed experiment until that returns.


### 2026-08-29 (final) — Phase 3, diagnostic 2: seed 2 never collects the final transition, because it never chooses it. The deadlock is diagnosed.

Seed 2 retrained alone, 4,000 steps, configuration-matched to the Phase 2 `ac_dqn` arm
in every hyperparameter, with read-only instrumentation only: a replay-buffer subclass
that tags n-step windows touching `receipt.html` and counts PER draws, plus a callback
probing Q at a **fixed** `order-4` observation captured the first time the agent arrived.
Nothing in `src/` was modified. `reports/diag_phase3_replay_seed2.json`.

| step | Q(Place order) | Q(Back) | gap | winner | receipt transitions stored | PER draws of them | `order-4` visits | receipt visits |
|---|---|---|---|---|---|---|---|---|
| 2000 | −0.4838 | −0.4722 | −0.0116 | back | 0 | 0 | 2 | 0 |
| 2500 | −1.2547 | −1.1943 | −0.0604 | back | 0 | 0 | 58 | 0 |
| 3000 | −1.4114 | −1.3250 | −0.0864 | back | 0 | 0 | 107 | 0 |
| 3500 | −1.7657 | −1.6307 | −0.1350 | back | 0 | 0 | 170 | 0 |
| 3750 | −2.0670 | −1.9444 | −0.1226 | back | **1** | 1 | 184 | 1 |
| 4000 | −2.2973 | −2.1238 | **−0.1734** | back | **9** | 13 | 196 | 6 |

**1. The transition is barely collected at all.** Seed 2 arrives at `order-4` **196
times** during training and clicks `Place order` for the first time at roughly **step
3,750 of 4,000** — 94% of the way through. Total: 9 stored n-step windows, **0.225% of a
4,000-transition buffer**, 6 steps ever spent on the receipt.

**2. PER is not the problem — it did its job.** Mean priority of the tagged transitions
is **1.593** against **0.589** for the rest of the buffer (~2.7×), and they were drawn 14
times where uniform sampling over their short lifetime predicts ~4–5, i.e. roughly **3×
over-sampled**. *(Caution: the `per_over_uniform: 0.111` field in the JSON is misleading —
it divides share-of-all-56,000-draws by share-of-final-buffer, but these transitions only
existed for the last ~250 env steps. Read the priority and lifetime-adjusted figures
instead.)* Prioritisation worked; it simply had ~250 steps left to propagate anything.

**3. The Q ordering never develops — it degrades.** The gap runs
−0.0116 → −0.0500 → −0.0604 → −0.0864 → −0.1350 → −0.1734, **monotonically widening, and
`Place order` never wins at any probe**. Note it was already losing at step 2000, when
**zero** receipt transitions existed: that estimate is pure generalisation from action
features with no supporting data. Both values also drift down together (−0.48 → −2.30),
the reward non-stationarity recorded earlier.

**4. The mechanism is a self-reinforcing deadlock.** `Q(Back) > Q(Place order)` before any
receipt experience exists → the greedy policy takes `Back` → the receipt transition is
never collected → `Q(Place order)` never receives a corrective update → the gap widens.
The only escape is an ε-random `Place order`, which at ~20 valid actions and ε≈0.10–0.20
is a ~0.5–1% event per arrival; across 196 arrivals that predicts ~1–2 occurrences, and 6
were observed, all in the final 250 steps.

**Answering the decision rule posed on 2026-08-29:**

* **A — insufficient / sparsely sampled experience: YES, but as a *symptom*.** The
  experience is nearly absent (1 occurrence at 94% of training), and that is decisive —
  but it is absent *because* of D, not independently of it.
* **B — adequate experience, unstable estimation: NO.** The experience was never adequate.
* **C — systematically incorrect target: NO.** Falsified by diagnostic 1 — completing the
  flow out-earns the oscillation by 11–15 points per episode.
* **D — another concrete implementation issue: YES, and it is the causal driver.** An
  exploration/estimation deadlock at a single decision point. Consistent with the
  untested H1: `Place order` and `Back` are both CLICKs with identical scalar features
  (two of which, `taken_here`/`taken_this_episode`, are dead because `record_taken()` is
  never called), so the untrained estimate rests on a 32-dim hashed label whose nearest
  neighbour for `Place order` is `Cancel order` (cosine +0.42).

**Why seeds 0 and 1 succeed and seed 2 does not** is now a coherent story rather than a
mystery: whichever seed happens to take `Place order` early — before the negative
estimate entrenches — collects the transition, corrects the Q value, and completes
thereafter. It is a race between a rare random action and a widening gap. That also
explains the bimodal 5/5/0 outcome: there is no partial credit, the seed either escapes
the deadlock or it does not.

**Established by this diagnostic:** the final transition is collected once per 4,000
steps at best; PER prioritises it correctly but too late; the Q ordering never inverts;
and the failure is an exploration deadlock, not a reward-ordering error and not a PER
defect.

**Still UNMEASURED:** whether seeds 0 and 1 show an *early* `Place order` (the race
hypothesis predicts they do — this needs the same instrument on those seeds, ~50 min);
and whether H1 is the reason the untrained estimate favours `Back` (needs a controlled
feature or label change, which is an agent modification and is **not** authorised).

**Next action when work resumes — one causal experiment, and it needs approval because
every candidate fix is an agent change:** run the same instrument on seeds 0 and 1 to
confirm or refute the race hypothesis. That is read-only, ~50 minutes, and it decides
which fix is even worth considering. Only after that should any of the following be
weighed, **one at a time**: an exploration bonus targeted at never-taken actions at a
visited state; wiring `record_taken()` so the two dead features can distinguish a
repeatedly-taken `Back` from a never-taken `Place order`; or optimistic initialisation of
unvisited state-action pairs. **Do not implement any of them without a decision.**

**Constraint: nothing else changes.** No reward weights, no architecture, no PER, no
n-step, no epsilon, no archive, no new fixture.


### 2026-08-29 (final, addendum) — Phase 3, diagnostic 3: the deadlock is confirmed in both directions, and 4,000-step training is not reproducible at a fixed seed

The same read-only instrument run on seeds 0 and 1.
`reports/diag_phase3_replay_seed{0,1}.json`.

| seed | `order-4` visits | receipts | transitions stored | share of buffer | PER draws | first gap | last gap | ever favours `Place order` |
|---|---|---|---|---|---|---|---|---|
| **0** | 164 | **128** | **325** | **8.12%** | **2,565** | +0.0532 | **+0.3364** | **yes, at every probe** |
| **1** | **0** | **0** | 0 | 0% | 0 | *(never probed)* | — | n/a |
| **2** | 196 | 6 | 9 | 0.23% | 14 | −0.0116 | −0.1734 | **no, at any probe** |

**1. The deadlock is confirmed, and it runs in both directions.** Seed 0's Q gap is
**positive before any receipt transition exists** (+0.0532 at step 1,250 with
`tagged=0`), the greedy policy therefore takes `Place order`, and it converts **128 of
164** arrivals into receipts — 325 stored transitions, 2,565 draws, the gap widening
monotonically to +0.3364. Seed 2 is the exact mirror: gap negative at zero experience,
6 receipts from 196 arrivals, 9 stored, 14 draws, gap widening to −0.1734. **Neither
seed's gap ever changes sign.** The sign at zero experience decides the run, and the
feedback loop then entrenches it. This is a self-reinforcing exploration deadlock, and
it is now measured rather than argued.

**2. PER behaves correctly on both sides and is not the lever.** On seed 0 the tagged
transitions are 8.12% of the buffer and 4.6% of draws with mean priority *below* the
population (0.520 vs 0.588) — they are plentiful and unsurprising, exactly as they
should be once learned. On seed 2 they are 0.23% of the buffer with mean priority
**2.7× the population** (1.593 vs 0.589) — correctly flagged as surprising, simply too
few and too late. PER is doing its job in both regimes.

**3. Seed 1 did not replicate, and that is the most consequential finding here.** In
Phase 2, `ac_dqn` seed 1 completed **5/5** and reached the receipt in every evaluation
episode. Re-run at the identical seed and configuration, it **never reached `order-4`
even once in 4,000 training steps**. The run trained normally (full 4,000-transition
buffer, ordinary priorities); it simply went somewhere else.

**Consequences, and they are not small:**

* **4,000-step training is not reproducible at a fixed seed.** §5's 2026-08-28 entry
  established bit-reproducibility at **600 steps in one process** and explicitly flagged
  the 4,000-step case as untested. It is now tested, and it fails. The likely mechanism
  is browser/env timing affecting which actions succeed, compounding over 4,000 steps —
  not the observation channel, which was fixed and verified.
* **"Seed 2 fails" is not a property of seed 2.** It is a property of a *run*. The
  5/5/0 pattern in both the C1 diagnostic and Phase 2 is therefore a coincidence of two
  runs agreeing, not a stable per-seed fact — and the fact that they *did* agree twice
  should not be read as reproducibility, because seed 1 has now disagreed with itself.
* **Every per-seed number in this project inherits this.** Seed-to-seed spread has been
  used throughout as the measure of variance; there is also run-to-run variance at a
  fixed seed, of a magnitude that can turn 5/5 completions into never reaching stage 4.
  This is the third time this project has found that a number it treated as a
  measurement was a draw from a wider distribution.

**What survives.** The mechanism (seeds 0 and 2, opposite directions, unambiguous). The
Phase 2 aggregate — `ac_dqn` 10/15 against 0/15 and 0/15 — because that is a comparison
of arms within one session, and the arms differ by far more than run noise. **What does
not survive** is any claim about which *seed* succeeds, and any diagnostic premised on
seed 2 being specifically broken.

**Next action when work resumes.** The diagnosis is complete enough to act on, and every
remaining option is an agent change requiring a decision:

* **The highest-value measurement first, and it is cheap:** re-run the Phase 2 `ac_dqn`
  arm N times *at one fixed seed* to get the true completion distribution. Everything
  currently believed about this agent rests on 3 draws that turn out not to be
  seed-determined. Until that exists, "10/15" is a point estimate of unknown variance.
* **Then, one fix at a time, none yet authorised:** an exploration bonus targeted at
  never-taken actions at a visited state (directly addresses the deadlock); wiring the
  dead `record_taken()` so a repeatedly-taken `Back` is distinguishable from a
  never-taken `Place order` (the two features exist and are always 0.0); or optimistic
  initialisation of unvisited state-action pairs. All three attack the same mechanism
  and must not be combined.

**Constraint: nothing changes until the run-to-run distribution is measured.** Fixing a
deadlock whose base rate is unknown would be untestable — there would be no baseline to
compare against.


### 2026-08-30 — M0: the RL half is frozen, and this document was wrong about what infrastructure exists

**The RL workstream is paused by decision, not by conclusion.** Nothing in §5's RL
entries is retracted; the open question recorded above (measure the run-to-run completion
distribution at a fixed seed before attempting any fix) stands unchanged and is where
work resumes. **Frozen until then:** AC-DQN architecture, reward function, exploration
strategy, PER, n-step, archive, epsilon schedule, action/observation design, training
budget, evaluation protocol, and the deep-flow fixture. All RL artefacts and baselines
are preserved.

Focus moves to the surrounding pipeline:

    Repository -> Repo Profiling -> Sandbox/Container -> Test Runner
               -> RL Testing Agent -> Observations/Evidence
               -> Finding Generator -> Bug Report

#### This document was materially out of date, in exactly the area now being worked on

An audit of the repository against this file found **four substantial components that
exist, are committed and are tested, and that this document still describes as unbuilt.**
Anyone planning from §3.5 or §9 would have rebuilt working code.

| Component | What this document said | What is actually true |
|---|---|---|
| `intake/runner.py` (491 lines, commit `4539c0f`) | §9 item 6: *"the sandbox enforcement is not [built]"* | **Built.** `deploy_repository()` does detection → policy gate → build under a deadline with `--build-network none` → dedicated bridge network → `docker run` with `SANDBOX_RUN_ARGS` → HTTP health check → exhaustive teardown that never raises. 32 unit tests against a fake Docker client, plus a Docker-gated integration smoke test. |
| `reporting/report.py` (370 lines, commit `c5cce3d`) | §3.5: *"Bug Report Engine — **not built yet**"*; §9 item 12 open | **Built.** `build_report()` → `BugReport`/`ReportedBug`, JSON and Markdown. Keeps deterministic findings and judge verdicts structurally separate, derives repro steps from the action trace, quarantines ungrounded verdicts instead of dropping or including them. 15 unit tests. |
| `scripts/run_repo.py` (192 lines) | not mentioned | **Built and demonstrably run.** `repo → detect → gate → build → run → base_url → WebFunctionalEnv → rollout → judge → bug report → teardown`. Its output is committed: `reports/repo_intake_demo_repo_report.{json,md}` — 5 findings in 21.5 s against a real container. |
| `intake/extract.py` (218 lines) | not mentioned | **Built, and deliberately narrow.** A static-HTML `ApplicationProfile` extractor written for one experiment. Its own docstring states it "is **not** the Repo Profiler of §3.0b". **It has no callers anywhere in the repository** — orphaned by design once its experiment concluded. |

**Corrected status of §3.0a.** The policy gate and the runner both exist. What remains
unbuilt there is not the runner but **compose execution**: compose files are detected,
policy-gated, and then deliberately **refused**, because applying `SANDBOX_RUN_ARGS` to a
multi-service file means generating an override and hoping the merge does what you
expect. `runner.py` states that as a scope cut rather than a gap, and that reasoning is
sound — but the practical consequence is that most real multi-service applications
cannot currently be deployed by this pipeline.

**The security scope, restated because it is easy to over-read.** The runner is
resource isolation for *trusted or controlled* repositories. It is **not** a boundary
against a hostile upload, and no option in it makes it one: `docker build` executes every
`RUN` line before any `docker run` restriction exists, and under Docker Desktop's rootful
WSL2 backend that build runs as root inside the VM. `--build-network none` (the default)
denies egress and is the single most useful build-time control, but the code still ran.
This is stated accurately in `runner.py` and `run_repo.py`; it is repeated here so the
limitation is visible from the planning document too.

#### Two things called "profile", and they must not be merged

`ApplicationProfile` (§3.0b, `intake/profile.py`) describes **application intent** —
routes, declared validation rules, flows, known-correct behaviours — and its consumer is
the judge. The new work needs something different: **how a repository is structured,
built, installed and tested**, whose consumers are the deployer and the report. These
have different lifetimes, different consumers and different provenance vocabularies.
They are therefore two schemas, not one: `RepositoryProfile` (new, §M1 below) and
`ApplicationProfile` (existing, unchanged).

**A caution carried forward from §5.** The 2026-08-26 A/B measured that a hand-authored
`ApplicationProfile` changed recall, false positives, discrimination and evidence
grounding **by nothing at all**, and `extract.py`'s docstring says its own results cannot
confirm the source-grounding differentiator. So `RepositoryProfile` is justified by what
the *deployer and the report* need — which is concrete and currently missing — and **not**
by an expectation that profile context improves judgment. That claim remains unproven and
must not be used to motivate this work.

#### M1 — `RepositoryProfile` and deterministic detectors

See the entry below for what was built. Scope deliberately excludes wiring it into
`run_repo.py` (M2), extracting an orchestration module (M3), evidence artefacts in
findings (M4), sandbox/compose changes (M5) and CI (M6).

**Two decisions still open, and M2/M5 are blocked on them:**

1. **Does the Dockerfile-only scope cut (§6.2) hold?** If it does, `RepositoryProfile`'s
   build/install/test section is descriptive metadata for the report and nothing
   executes it. If it does not, a fallback build path is needed and the profile becomes
   load-bearing — a much larger commitment, and the one §6.2 cut for good reason.
2. **Which threat model is wanted?** "Trusted repositories, resource-limited" (what
   exists, honest, cheap) or "hostile upload" (needs rootless BuildKit, gVisor/Kata or a
   disposable VM — a project in its own right, not a hardening pass).


### 2026-08-30 (later) — M1/M2/M3: repository profiling, its integration, and the pipeline extracted from the script

Both open questions above were answered by decision: **the Dockerfile-only deployment
scope holds** (§6.2 unchanged — the profiler may *describe* other ecosystems but no
alternative build path is implemented, and `deploy_repository` remains the sole authority
on deployability), and **the threat model stays "trusted/controlled repositories with
resource limits"** (no rootless BuildKit, gVisor, Kata or disposable VMs; the build-time
execution limitation stays documented rather than papered over).

M1 was recorded when it landed. **M2 shipped without being written here, which is the
exact failure M0 existed to correct**, so it is recorded below alongside M3.

#### M1 — `intake/repo_profile.py`

`profile_repository(root)` → `RepositoryProfile`: languages by byte share, build systems,
frameworks, entry points, test targets, dependency manifests, docs, build definition, and
proposed install/build/test commands. Deterministic — no LLM, network, Docker, browser or
clock — so the same tree always yields the same profile. Supporting types `Detection`
(value + evidence + confidence), `LanguageStat`, `ProfileStats`.

Three conventions worth knowing before extending it:

* **Confidence is a stated three-level scale**, not a score: `1.0` the repository declares
  it, `0.7` a strong filename convention, `0.5` a heuristic. A framework is reported
  **only** when a manifest declares a dependency on it — a repo with `manage.py` and
  `models.py` is not reported as Django, and there is a test pinning that.
* **A command is proposed only when its target exists.** `make test` is not emitted for a
  Makefile with no `test:` rule.
* **`IGNORED_DIRS` is pruned during the walk.** A vendored `node_modules` would otherwise
  make every repository containing one profile as JavaScript.

**Known limitation, measured on this repository:** language share is bytes over recognised
extensions, so committed *data* with a source extension counts as source — this project
profiles as **HTML**, because the seeded-bug fixtures and captured page blobs outweigh the
Python. Excluding "data-looking" paths would be a repository-specific rule dressed as a
general one, so the number stands and the docstring says why.

**One defect found by dogfooding:** `install.py` was reported as documentation (it matched
the `install` doc prefix). Doc detection now requires a prose suffix.

#### M2 — the profile in `run_meta`

`profile_for_run(root)` returns a JSON-safe block that **never raises and never
fabricates**. Both outcomes carry `available` explicitly: success is the full
`to_dict()` plus `available: True`; failure is `{"available": False, "error": "..."}` and
**nothing else**, so an unprofilable repository cannot be mistaken for one that was
profiled and found empty. A diagnostic must not fail the run it describes.

`scripts/run_repo.py` profiles **before** deployment — a pure filesystem read that cannot
be perturbed by the build, and if the build later fails the record of what the repository
*was* has already been captured — and passes the full block as
`run_meta["repository_profile"]`. The `BugReport` schema is untouched; `run_meta` is
free-form by design.

**Issue carried into M4:** `render_markdown` dumps `run_meta` verbatim, so the profile is
now **55% of the demo Markdown report** (47 → 105 lines), and a realistic repository
serializes to ~347 lines / 9.7 kB. The full block is kept because a summary would drop the
confidence and evidence-path fields that make a claim checkable. **M4 should render a
short summary in Markdown while keeping the full block in JSON.**

#### M3 — `web_testing_agent/pipeline.py`

The orchestration that was the body of `run_repo.main()` is now a reusable function.
**No behaviour changed**; the code moved.

* `run_pipeline(repo, settings, deps, observer=…, on_deployment=…)` performs
  profile → detect → deploy → judge → env → policy → rollout → report, and always tears
  down.
* `RunSettings` — a frozen dataclass instead of an `argparse.Namespace`, so the pipeline
  is callable without a command line. Its defaults match the CLI's, and a test pins that
  so drift cannot silently change what a plain invocation does.
* `PipelineDependencies` — eight injectable stages (profiler, detector, deployer,
  env_factory, policy_factory, judge_factory, rollout_runner, reporter), each defaulting
  to the previous behaviour. **Every default resolves its imports lazily inside the
  factory**, so importing the pipeline costs nothing and a test never loads Playwright,
  Docker or a model.
* **The pipeline never prints.** Stages emit events through an optional `observer`; the
  CLI renders them. A pipeline that prints is unusable from a service or a notebook.
* `on_deployment` is the `--deploy-only` hook: returning False stops after deployment
  with `stopped_after_deploy=True` and **no report**, so a run that stopped early and a
  run that found nothing cannot be confused.

`scripts/run_repo.py` keeps exactly what belongs to a command line: argument parsing, the
`print_stage` renderer, the `input()` prompt, and writing the report to a chosen path.

**Behaviour preservation, evidenced rather than asserted.** The same invocation was run
end to end against Docker before and after the extraction
(`--repo tests/fixtures/demo_repo --judge stub --episodes 1 --steps 12`). After
normalising only the inherently per-run fields — host port, image id, timestamp, wall
clock — **the two JSON reports are identical, including `run_meta` key order**. Rollout
metrics matched exactly (reward +4.15, 4 unique states, 1 distinct finding, 100%
valid-action rate, 91.7% exec-success, 3 NO_OPs, `console_error: 1`). `--deploy-only` was
re-verified separately. No `wta-target-*` container, image or network leaked.

**Tests: 741 → 779.** M1 added 45 (`test_repo_profile.py`), M2 added 15
(`test_run_meta_profile.py`), M3 added 23 (`test_pipeline.py`). The M3 tests run the real
`run_pipeline` against fake stages in 0.09 s and cover stage ordering, event emission,
each injection seam, deploy-only, and the failure paths — a rollout that raises must still
close the browser *and* tear down the container, and a detector that refuses must stop
before anything is built.

**One issue found while testing:** a judge-verdict fixture was correctly quarantined as
ungrounded, which surfaced that `is_grounded` refuses to certify a citation below a
minimum word count. That is right — a two-word "quote" cannot be distinguished from
coincidence — and it is documented in the test rather than worked around.

**Not done, and deliberately:** M4 (evidence artefacts, and the Markdown-size decision
above), M5 (sandbox/compose — still detected, policy-gated and refused), M6 (fixtures/CI).
The profiler's Go/Rust/Maven/Gradle/Bundler/Composer detectors are covered only by
synthetic fixture trees, never by a real repository of that kind.


### 2026-08-30 (later still) — M4: findings traceable to the artifacts that produced them

**No new artifact store, and no schema change.** Both were checked before anything was
written, and neither turned out to be necessary.

#### The join key already existed

`run_rollout` calls `recorder.on_step(...)` and *then* increments `report.steps`;
`TraceRecorder.on_step` increments `global_step` first. The two therefore carry the same
value for the same transition. `Finding.first_seen_step` is that counter, `_from_trigger`
already copied it into `ReportedBug.step`, and the live judge records the same global step
on every verdict. **Every reported bug already named the exact transition that produced
it — what was missing was anything to look the number up in.** That invariant spans two
modules and neither states it, so `tests/unit/test_evidence.py` drives the *real*
`run_rollout` and the *real* `TraceRecorder` against a fake environment and asserts it,
rather than trusting the reasoning above.

`TraceRecorder` already content-addresses everything a finding could cite — page bodies to
`pages/<sha256>.html`, frames to `screenshots/<sha256>.png`, network and console events
summarized per record. A second store would have duplicated that and immediately risked
disagreeing with it.

#### How evidence flows

    rollout ──> TraceRecorder ──> trace.jsonl + pages/ + screenshots/   (content-addressed)
                                        │
      findings/verdicts ── cited global steps ──> build_evidence_index()
                                        │
                            run_meta["evidence"]["by_step"]["<global_step>"]
                                        │
             ReportedBug.step ──────────┘   (already present; no new identifier)
                                        │
                        Markdown: "Recorded evidence" per finding
                        JSON:     full digests + full profile

Only the steps a report actually cites are indexed. A 200-step rollout has no business
putting 200 records into `run_meta`; the rest stay in the trace on disk, where the full
record is still available.

#### Files changed

| File | Change |
|---|---|
| `annotation/evidence.py` | **new** — `build_evidence_index`, `evidence_for_record` |
| `annotation/__init__.py` | exports |
| `pipeline.py` | `recorder_factory` dependency, `evidence_dir` parameter, `_cited_steps`, `_evidence_index`, `RunSettings.capture_screenshots`, `PipelineResult.evidence` |
| `reporting/report.py` | `_render_artifacts`, `_summarize_profile`, `_summarize_evidence`; `render_markdown` summarizes instead of dumping |
| `scripts/run_repo.py` | `--no-evidence`, `--no-screenshots`, evidence dir beside the report |
| `tests/unit/test_evidence.py` | **new, 27 tests** |
| `tests/unit/test_pipeline.py` | fakes updated for the `recorder=` parameter |
| `tests/unit/test_run_meta_profile.py` | one assertion updated — see below |

**The `BugReport` schema is untouched.** Evidence rides in `run_meta`, which is free-form
by design, and the join is on a field `ReportedBug` already had.

#### The Markdown-vs-JSON decision

M2 left the full profile being dumped into Markdown, where it was **55% of the demo
report** and would be ~350 lines for a real repository. M4 resolves that as asked:

* **Markdown** — a summary: languages with shares, build systems, frameworks, files
  scanned, whether the pipeline can deploy it, and any notes. Evidence gets the trace
  directory, the number of steps referenced, and per-finding artifact references with
  **12-character abbreviated digests**.
* **JSON** — everything, unchanged: full profile (19 keys), full 64-character digests,
  the complete per-step index.

Everything else in `run_meta` is still dumped verbatim, so **a report with neither profile
nor evidence renders exactly as it did before either existed** — verified end to end with
`--no-evidence`: no `evidence` key, no directory created, no evidence sections, report
renders.

One test assertion changed as a direct consequence: `test_the_profile_reaches_the_rendered_markdown`
asserted the profile was *dumped*. It now asserts the summary is present and the raw
serialization is absent. That is the requested behaviour change, not a regression, and the
test says so in its docstring.

#### A defect found by the end-to-end run

The demo fixture's seeded error is an **uncaught page error**, which Playwright reports on
`pageerror` while `console.error()` arrives on `console`. `detect_bug_signals` has always
merged the two; the trace keeps them apart. `evidence_for_record` captured both, but
`_render_artifacts` rendered only `console_errors` — so the evidence for a finding *caused
by* an uncaught exception silently omitted the exception. Fixed, with a regression test.
This is the same shape as the defects §8 catalogues: the record had it, the view dropped it.

#### Tests and verification

**Tests: 779 → 804.** M4 added 27 (`test_evidence.py`) covering the step-alignment
invariant, content-addressed capture and dedup, digest-resolves-to-bytes, references-not-
contents, cited-steps-only indexing, network-failures-but-not-successes, missing/malformed
traces, relative vs absolute trace paths, Markdown rendering, JSON round-trip, and the
no-evidence path. Full unit suite green.

**End-to-end against the Docker demo fixture**, three runs: with evidence, after the
page-error fix, and with `--no-evidence`. Rollout metrics identical to the M3 baseline
(reward +4.15, 4 unique states, 1 distinct finding, 100% valid actions, 91.7%
exec-success, 3 NO_OPs, `console_error: 1`) — capture is an observer and changes nothing
it observes. `bugs[0].step == 12` joins to `evidence.by_step["12"]`; the digests resolve
to real files. No `wta-target-*` container, image or network leaked.

#### Limitations and unresolved issues

* **The trace directory is absolute when the report is written outside the working
  directory.** `build_evidence_index` makes it relative to `Path.cwd()` when it can and
  falls back to absolute otherwise, because a wrong relative path is worse than a long
  one. In normal use (`reports/`) it is relative.
* **Screenshots are on by default** and are the bulk of the evidence on disk (6 PNGs for a
  12-step run on a 4-page site). `--no-screenshots` keeps the trace without them.
* **Verdict evidence is indexed but untested end to end.** The join is the same global
  step, and the code path is shared, but every end-to-end run so far used `--judge stub`,
  so no run has yet produced a judge verdict *with* an evidence reference.
* **Evidence is not pruned.** Each run writes a new trace directory beside its report and
  nothing removes old ones.

#### Confirmation of scope

**RL untouched:** no change to agents, reward, archive, exploration, PER, n-step, epsilon,
training, the deep-flow fixture, or evaluation behaviour. `evaluation/rollout.py` was not
modified — the `recorder` hook it already had is what M4 uses. **M5 untouched:** the Docker
sandbox threat model, `SANDBOX_RUN_ARGS`, and the compose refusal are all unchanged.
**M6 not started.**


### 2026-08-30 (final) — M5: one real leak in the sandbox lifecycle, closed and measured

**One production change.** The audit found a single genuine defect; everything else in
`intake/runner.py` already met the lifecycle and cleanup requirements and was already
tested. The smallest fix that closes it is what shipped.

#### The defect: a container that fails to *start* was leaked

`docker-py`'s `ContainerCollection.run()` is:

```python
container = self.create(...)   # the container now exists
container.start()              # unguarded — if this raises, run() never returns it
```

There is no cleanup between the two. When `start()` raises, the caller never receives the
object, so `deploy_repository` could not register it in `_Created`, and the container it
had already created was orphaned. **This is not an exotic path.** `SANDBOX_RUN_ARGS` is
deliberately strict enough that `runner.py`'s own docstring says it "breaks many real
images" (read-only rootfs, `user=1000:1000`, all capabilities dropped), and a host port
can be taken between `_free_port()` and the call.

The fix is to create, register, then start:

```python
container = client.containers.create(tag, detach=True, name=..., ports=..., **args)
created.containers.append(container)   # in the ledger before it can fail
container.start()
```

`create()` handles `ports` and `network` through the same `_create_container_args` path
`run()` uses, so nothing about the sandbox configuration changed — only when the
container enters the teardown ledger.

**Both halves were measured against a real daemon, not inferred from the SDK source:**

* `containers.run()` with an occupied host port left `wta-sdkleak-demo` behind —
  the leak, reproduced.
* `deploy_repository` under the identical condition failed at
  `POST /containers/<id>/start` (a genuine post-create failure) and leaked **nothing**.

#### Files changed

| File | Change |
|---|---|
| `intake/runner.py` | `containers.run()` → `create()` + register + `start()` (the only production change) |
| `tests/unit/test_intake_runner.py` | fake client models `create`/`start`; **+10 tests** |

`pipeline.py`, `scripts/run_repo.py`, `reporting/`, `annotation/`, `intake/compose_policy.py`
and `SANDBOX_RUN_ARGS` are **unchanged**. The CLI is unchanged.

#### Lifecycle verified

    repository → detect → policy gate → build → create → start → health check
               → rollout → teardown

Ten new tests, four of which drive the **real** `run_pipeline` and the **real**
`deploy_repository` against the fake daemon, so the pipeline's use of the runner is
covered rather than only the runner in isolation:

* full lifecycle yields a `Deployment` and then removes everything
* a container that fails to start is torn down (the regression for the fix)
* a container that never started is removed anyway
* `KeyboardInterrupt` inside the block still tears down — `finally` catches
  `BaseException`, which a bare `except` would not, and an interrupted run is exactly
  when a leak is least likely to be noticed
* a network that cannot be created still removes the image
* a teardown failure is reported, not raised, and later objects still go
* pipeline: successful run closes browser + trace handle and removes everything
* pipeline: a rollout failure leaks nothing, with the container removed *after* the
  browser stopped pointing at it
* pipeline: a health-check failure never reaches the rollout
* pipeline: a compose repository is refused before anything is built

#### Real Docker end-to-end

Six scenarios, each with a before/after census of `wta-target-*` containers and images
and `wta-net-*` networks. **All six leaked nothing:**

| scenario | outcome |
|---|---|
| normal run | deployed, health-checked, torn down |
| container fails to start | `APIError` at `/containers/create` (invalid cpuset) |
| container fails to start, post-create | `APIError` at `/containers/<id>/start` (port taken) |
| health check times out | `HealthCheckTimeout` after 8s |
| build fails | `RepoIntakeError` with the log tail |
| interrupted inside the block | `KeyboardInterrupt` propagated |
| compose project | refused without building |

Plus the full CLI path (`run_repo.py --judge stub --episodes 1 --steps 12`): metrics
identical to the M3/M4 baselines (100% valid actions, 91.7% exec-success, 3 NO_OPs,
`console_error: 1`), repository profile and evidence capture both intact, containers /
images / networks 0 before and 0 after.

#### Compose behaviour — unchanged, as required

Detected, policy-gated, and **refused**. `deploy_repository` raises rather than executing
it, because applying `SANDBOX_RUN_ARGS` to a multi-service file means generating an
override and trusting the merge, and "approximately applied" is not a claim worth making
about a security control. No compose execution was added.

#### Threat-model boundary — unchanged, and it holds

The scope is **trusted/controlled repositories with resource limits**, and the
implementation honestly supports that scope. Nothing in M5 expanded it: no rootless
BuildKit, no gVisor, no disposable VMs. The stated limitation stands unchanged — `docker
build` executes the repository's own `RUN` lines before any `docker run` restriction
exists, and under Docker Desktop's rootful WSL2 backend that build runs as root inside
the VM. That is a boundary of the *chosen scope*, not a defect within it, and it is
recorded in `runner.py`, `run_repo.py` and every report's `run_meta`.

#### Tests

**804 → 816** (M4's two late evidence tests plus M5's ten). Full unit suite green.
`test_intake_runner.py`: 32 → 42.

#### Newly discovered issues and limitations

* **`_tar_context` builds the whole build context in memory** (`io.BytesIO`), so a very
  large repository can exhaust memory before the build starts. It fails *before* anything
  is created, so it leaks nothing — a robustness limit, not a cleanup gap. Not fixed: out
  of M5's lifecycle/cleanup scope.
* **Dangling build-cache layers are not removed.** `rm=True, forcerm=True` clears
  intermediate containers; layers from a failed build stay in Docker's cache. Removing
  them would mean pruning shared cache, which is not this runner's to do.
* **Teardown reports rather than raises**, so a run whose cleanup partially failed still
  returns normally with a warning logged. Deliberate — a noisy cleanup must not replace
  the result of a run that otherwise succeeded — but it means "no exception" does not by
  itself prove a clean daemon. The census in the verification script is what proves that.
* **A leaked container from an older build would collide on `name=wta-target-<run_id>`.**
  `run_id` is a fresh uuid per run, so this needs an actual duplicate; not defended
  against.

#### Confirmation of scope

**RL untouched:** no change to agents, reward, archive, exploration, PER, n-step, epsilon,
training, the deep-flow fixture, or evaluation. **M6 not started.** The `BugReport` schema
is unchanged; evidence retention is still unimplemented, as instructed.


### 2026-08-30 (final, M6) — fixtures, CI, and a flaky test that would have made CI red at random

The last infrastructure milestone. The non-RL system is now runnable and testable from a
clean checkout without the deep-learning stack, a GPU, Docker or a browser.

#### Files changed

| File | Change |
|---|---|
| `tests/conftest.py` | **new** — capability detection, markers, collection gating, report header |
| `requirements-ci.txt` | **new** — the minimal set found by experiment, not by taste |
| `.github/workflows/tests.yml` | **new** — three jobs; there was no CI before |
| `tests/fixtures/manifests/` | **new** — 8 ecosystem manifests + README, 21 files |
| `tests/unit/test_repo_profile_manifests.py` | **new, 32 tests** |
| `tests/unit/test_encoders.py` | one flaky helper fixed (see below) |

No production code changed in M6.

#### Test separation — what needs what, in one place

Three capabilities are detected in `tests/conftest.py` and turned into markers:
`requires_torch`, `requires_docker`, `requires_browser`. Gating lives centrally rather
than in the test files because most heavy modules are **frozen RL tests**, and making the
suite portable should not mean editing them.

Modules importing `torch`/`stable-baselines3`/`gymnasium` cannot be *skipped* — a missing
import is a collection error, not a skip — so they are not collected when the stack is
absent. `pytest_report_header` prints
`capabilities: torch-stack=… docker=… browser=…` and how many modules were dropped, so a
short run explains itself instead of looking truncated.

**A real gap closed:** `test_form_state_capture.py` and `test_offsite_navigation.py` need
a real Chromium and had **no gate at all** — on a machine without the browser download
they failed with a Playwright error that reads like a broken suite. They now skip with
`run `python -m playwright install chromium`` as the reason.

#### Fixtures — what was added and why

**Nothing was added for the pipeline.** `tests/fixtures/demo_repo` already exercises
profile → build → run → health check → rollout → evidence → report, and M5 verified it end
to end. A second deployable fixture would have duplicated that and added a Docker build to
every run.

**Manifests were added**, because that is where coverage was genuinely thin. The
profiler's Go/Rust/Maven/Gradle/Bundler/Composer detectors were covered only by synthetic
trees written inline beside the detectors — and a one-line `go.mod` written by the author
of the parser is the `go.mod` the parser already handles. The fixtures are realistically
shaped: two `require (…)` blocks with `// indirect` markers, Cargo inline tables with
feature lists, Maven XML, Gemfile groups, Poetry's nested dependency sections, a
`package.json` with real scripts and a lockfile.

The profiler handled **all eight** correctly on first run — Gin, Axum, Spring Boot,
Laravel, FastAPI, Rails, Express detected from real manifest formats. Two things the
fixtures made visible, both now pinned as tests rather than left to be rediscovered:

* **Gradle dependencies are not parsed.** `build.gradle` declares
  `spring-boot-starter-web` and the profiler reports no framework: `_dependency_names`
  reads eight manifest formats and Groovy DSL is not one of them (Maven is handled only by
  a substring check on the XML). The build system *is* detected; its dependencies are not.
* **A manifest-only tree reports its manifests as the primary language** (`node_api` →
  JSON, `rust_api` → TOML). That is the documented byte-share behaviour meeting an
  unrepresentative tree, not a defect — a real repository has source that outweighs its
  `package.json`. Recorded so the reading is not mistaken for a profiler bug.

#### The flaky test, found by running the whole suite

`test_encoders.py::test_outputs_are_unit_norm_by_default` failed once during M1, did not
reproduce, and was reported then as "a pre-existing flake worth watching". M6 caught it
again and this time it was chased down.

The test's fake encoder returned `np.full(dim, float(hash(str(i)) % 97))`. **Python
randomizes string hashing per process**, so for some seeds `hash(item) % 97 == 0`, the row
is all zeros, normalization leaves it zero, and the unit-norm assertion fails.
Demonstrated deterministically: **`PYTHONHASHSEED=15` makes `hash("a") % 97 == 0`** and
the test fails; 1 of 40 seeds, matching an observed rate of roughly 2–3%.

This is a defect in the *test helper*, not in the encoder — the real encoders do not use
`hash()`, and no test asserts on those values (only on call counts). Fixed with
`zlib.crc32(...) % 97 + 1`: stable across processes, and never zero, so a legitimately
encoded item can never be confused with the zero vector that
`test_a_zero_vector_stays_zero_rather_than_becoming_nan` covers on purpose. **Verified
across 40 hash seeds: 0 failures** (previously 1).

Worth stating plainly because it is the whole point of this milestone: an unreproducible
2–3% failure is exactly what turns CI into noise people learn to ignore.

#### CI

There was none. Three jobs in `.github/workflows/tests.yml`:

* **portable** — `ubuntu-latest` and `windows-latest` × Python 3.11 and 3.12. Installs
  `requirements-ci.txt` + `pip install -e .`, runs
  `-m "not requires_torch and not requires_docker and not requires_browser"`. Windows is
  in the matrix because it is the development platform and this project has hit real
  path- and encoding-specific defects there.
* **integration** — Docker + Chromium on Linux. Runs
  `-m "requires_docker or requires_browser"`, then the **full non-RL path end to end**
  (`run_repo.py --judge stub`), then **fails the job if any `wta-target-*` container or
  image or `wta-net-*` network survived**, and uploads the report and its evidence as an
  artifact. A `docker version` precondition step is included deliberately: without it a
  missing daemon would skip the Docker tests and the job would pass having tested nothing.
* **lint** — ruff, non-blocking, so the existing count can come down deliberately rather
  than in one large diff.

**RL is deliberately not in CI.** It needs torch, SB3 and transformers — none of which are
in `requirements-ci.txt` — and a meaningful RL run is hours on a GPU.

#### `requirements-ci.txt`, and why it exists

`requirements.txt` is the *research* environment: it pulls `bitsandbytes`, `trl`, `peft`,
`datasets`, `wandb` and two CLIP packages, and **it does not contain `torch` at all** —
that is installed separately from a CUDA-specific index. Most of it will not install
cleanly on a CPU-only runner and none of it is needed to test profiling, the sandbox,
evidence or reporting.

The CI set was found empirically, not chosen: a clean virtualenv was built and packages
added until collection succeeded. Each of `numpy`, `Pillow`, `PyYAML`, `python-dotenv`,
`loguru`, `gymnasium` and `playwright` (the package; the Chromium download is separate) is
there because removing it produced a collection error.

#### Local results

| suite | result |
|---|---|
| `tests/unit` (dev venv) | **848 passed**, 5 skipped |
| `tests` (dev venv, everything) | **869 passed**, 9 skipped |
| CI subset (dev venv) | **694 passed**, 5 skipped, 179 deselected |
| CI subset (**clean venv**, no torch) | **694 passed**, 5 skipped, 25 deselected |
| `-m requires_docker` | 5 passed, 4 skipped (adversarial suite stays opt-in) |
| `-m requires_browser` | 16 passed |
| `test_encoders` across 40 `PYTHONHASHSEED` values | 0 failures |

Test count 816 → **848** (+32 manifest tests).

#### Clean-checkout verification

A fresh virtualenv was created, `requirements-ci.txt` and `pip install -e .` installed,
and the portable selection run: **694 passed, 5 skipped, 25 deselected**, with the eleven
deep-learning modules not collected and the header saying so. The deselected 25 are the
Docker and browser tests, which that environment could not run.

#### What was validated locally and what is CI-only

**Validated locally:** every pytest command the workflow runs, in both a dev and a clean
environment; the Docker and browser marker selections; the end-to-end `run_repo.py`
invocation; the leak-check shell logic (run against the real daemon, reporting 0/0/0); and
that the workflow YAML parses into the three expected jobs.

**CI-only, not validated:** that GitHub's runners provision as expected — the Ubuntu
Docker daemon, `playwright install --with-deps chromium`, the pip cache, the
`actions/upload-artifact@v4` step, and the Windows and Python 3.12 matrix legs. This
machine is Windows with Python 3.11, so the Linux and 3.12 legs are unexercised. **The
first CI run should be treated as the real test of the workflow**, not as a formality.

#### Limitations

* **`pyproject.toml` declares no runtime dependencies** — only a `dev` extra. `pip install
  -e .` gives the package with nothing to run it, so dependency discovery in a clean
  checkout is via `requirements-ci.txt` or `requirements.txt`, not the package metadata.
  Left alone: fixing it means deciding the real dependency set, which is a packaging
  change, not a CI one.
* **`requirements-ci.txt` is a second dependency list** and can drift from
  `requirements.txt`. It is small, commented per entry, and CI fails loudly if it stops
  being sufficient.
* **Gradle dependency parsing is missing** (above), now pinned by a test.
* The manifest fixtures are **manifests, not applications** — nothing is built or run from
  them, so they exercise the profiler and nothing downstream.
* The **adversarial sandbox suite stays opt-in** behind `WTA_ADVERSARIAL_SANDBOX=1` and is
  not run in CI; it is intended for disposable machines.

#### Confirmation of scope

**RL remains frozen and M6 altered no RL behaviour.** No change to agents, reward,
archive, exploration, PER, n-step, epsilon, training, the deep-flow fixture, or RL
evaluation; no RL experiment was run. The only edit touching an RL-adjacent file is the
`test_encoders.py` helper, which changes a *test* fake's value generator and no product
code. The Dockerfile-only scope and trusted-repository threat model are unchanged, compose
is still refused, the `BugReport` schema is unchanged, and evidence retention remains
unimplemented.


### 2026-08-30 (final, judge validation) — the real Ollama judge on DEEP-01: linkage is perfect, precision is not, and one true positive was quarantined

Closes M4's stated limitation ("verdict evidence is indexed but untested end to end").
**No product code was changed.** One defect was found during the run and it was in the
measurement harness, not the system — diagnosed and corrected before any conclusion was
drawn from it.

#### Configuration of record

`qwen2.5:7b-instruct` (version-pinned local, deliberately **not** the unversioned
`:cloud` tag whose weights moved mid-project), `temperature=0`, `num_ctx=8192`,
`num_predict=512`, `WINDOW_STEPS=6`, gated by `reward.gating.decide_for_record` — the same
filter the live reward path uses. Both shipped prompt styles. **62 judged windows,
0 failed calls, 13.8 minutes of judging.**

Three trajectories captured against the unmodified deep-flow fixture, screenshots on:

| corpus | steps | windows | gated | receipt reached |
|---|---|---|---|---|
| `deep01-scripted` | 11 | 8 | 8 | yes (step 8) |
| `deep01-noisy` — 5 filler steps, review page pushed out of the K=6 window | 16 | 13 | 8 | yes (step 13) |
| `deep01-control` — the flow walked correctly to `order-4`, then Back / Cancel / self-links / static pages; `place-order` never clicked | 17 | 15 | 15 | **no, by construction** |

The control exercises five of the answer key's seven `false_positive_watch` items. DEEP-01
is unreachable there, so **every** positive on it is a false positive.

#### Results

| corpus | prompt | judged | positives | true | false | FP rate | grounded | s/window |
|---|---|---|---|---|---|---|---|---|
| scripted | detailed | 8 | 6 | **1** | 5 | 62% | 2/6 | 14.3 |
| scripted | compact | 8 | 6 | 0 | 6 | 75% | 6/6 | 11.5 |
| noisy | detailed | 8 | 6 | **1** | 5 | 62% | 2/6 | 14.5 |
| noisy | compact | 8 | 6 | 0 | 6 | 75% | 6/6 | 13.1 |
| control | detailed | 15 | 8 | — | **8** | 53% | 3/8 | 14.3 |
| control | compact | 15 | 11 | — | **11** | 73% | 12.4 | 12.4 |

**Detection: `detailed` 2/2, `compact` 0/2.** Both detections carry the answer key's own
bug type, `broken_flow`, at severity 1.0 and confidence 1.0, and both name the quantity
mismatch explicitly ("shows as 1 instead of 42"). Detection survives the review page
leaving the window — on `noisy` the judge works from the receipt text plus the URL's
`quantity=42`.

**Precision is the problem, and it is worse than the toy site ever suggested.** On a
trajectory containing no reachable defect, the judge flags 53% (detailed) to 73%
(compact) of judged windows. The false positives are almost entirely the fixture's own
documented watch items: a gated `Continue` link read as `ui_regression` ("the link to
Step 2 is hidden, making it unreachable"), correct form behaviour read as
`validation_bypass` ("typed 42, but no validation error was shown"), and a `SELECT` that
does reveal its Continue link read as `dead_control`.

#### Step alignment and evidence linkage — perfect, 43/43

Every positive verdict across all six runs resolved end to end: its `step` is a real
global step in the trace, has an entry in the evidence index, whose recorded URL matches
the verdict's URL, and whose page-body **and screenshot** digests resolve to files that
exist. `window_step` (1–6, the model's own within-window index) stayed correctly distinct
from `step` (global), so the verdict-location bug fixed in `c5cce3d` has not regressed.
**M4's limitation is closed: judge verdicts carry working evidence references.**

#### The finding that matters most: a correct detection was quarantined

Reports generate correctly — bugs, ungrounded quarantine, per-finding
`**Recorded evidence**` blocks and the evidence summary all render. But tracing the
receipt verdict through to the document:

| report | receipt verdict ends up |
|---|---|
| `scripted / detailed` | **QUARANTINED as ungrounded** — absent from the findings |
| `noisy / detailed` | **reported as a finding** (`broken_flow`, 10.0) |
| `scripted / compact` | absent — never flagged |
| `noisy / compact` | absent — never flagged |

So of four bug-bearing combinations, **exactly one produces a bug report a reader would
act on.** On `scripted/detailed` the model got the bug right and the report shows two
findings, both false positives, while the true one sits in the excluded section.

The cause is not a defect: `is_grounded` requires the citation's words to occur in the
window, and the model wrote *"On the final step, the page text states: 'Quantity: | 1',
whereas the expected value was 42 based on the form input in previous steps"* — a
paraphrase built around a real quote. The filter exists to stop fabricated citations and
it is doing exactly that job; it simply cannot distinguish a paraphrase of something true
from an invention.

**The prompts trade off cleanly and oppositely.** Across all corpora: `detailed` grounds
**35%** of its positives (7/20) and detects the bug; `compact` grounds **100%** (23/23)
and never detects it. The project's earlier reading — that grounding was a solved,
prompt-fixable property at 100% — held on the toy corpus and does not hold here.

#### Harness defect found and corrected (not a product defect)

The first pass reported 0 bugs in every report from 43 positive verdicts. That looked
like an integration defect and was investigated as one before anything was touched. The
cause was in the validation harness: it filtered successful verdicts with
`"error" not in row`, but `Verdict.to_dict()` always carries an `error` key (`None` on
success), so `build_report` was handed an empty list. The same expression produced a
meaningless `judged=0` column. Corrected, and the reports regenerated from the **same
stored verdicts with no additional judge calls**. No product code was involved.

#### Artefacts

`reports/judge_deep01_ollama_validation.json` (full per-verdict record, latencies, step
alignment, linkage) plus twelve generated reports,
`reports/judge_deep01_ollama_deep01-{scripted,noisy,control}_{detailed,compact}.{json,md}`.
All additive; no previous artefact was modified.

#### What this changes, and what it does not

* **Established:** the judge, gate, window renderer, evidence index and report engine work
  together end to end with a real model; verdict→evidence linkage is exact; DEEP-01 is
  detectable, with the correct bug type, under `detailed`.
* **Established and unwelcome:** precision on this fixture is poor (53–73% FP on
  correct behaviour), and the grounding filter — a genuine safeguard — can suppress a
  correct finding.
* **Not established:** anything about `gpt-oss:120b-cloud`, which was not run here; and
  anything about a *live* run, since these are captured trajectories scored offline. The
  live path shares the code but was not exercised with a real model in this session.

#### Scope

No RL change: agents, reward, exploration, PER, n-step, archive, fixtures, training and RL
evaluation are untouched, and no training was run. The deep-flow fixture was read, never
modified. No product code changed at all.


### 2026-08-30 (design investigation) — the grounding fix is already built, and the data says grounding is the wrong thing to change

Design investigation only. **No code changed, no judge calls made** — every number below
was recomputed offline from `reports/judge_deep01_ollama_validation.json`, which stores
each verdict's `window_text`, so grounding is fully replayable at zero cost.

**1. The proposed "separate verbatim evidence field" already exists.** `VERDICT_SCHEMA`
has had a dedicated `evidence` property since the schema was written — distinct from
`observed` / `expected` / `discrepancy`, and documented as *"The exact line from the
window that supports this… Must appear verbatim in the window."* `is_grounded` already
runs on that field alone and never on the model's prose. There is no structural gap to
close.

**2. The real cause is a prompt divergence, and it is the same class of defect recorded
on 2026-08-08.** The `compact` prompt carries a mechanical rule — *"`evidence` must be ONE
line copied from the window, character for character. Do not summarize, rephrase or
explain it there"* — plus three few-shot examples of literal window lines. The `detailed`
prompt has only the one-line schema hint. The prompt file's own change log records adding
that rule "because 'quote the evidence' was being read as 'summarize the evidence'" — and
it was applied to one prompt only. Measured consequence: **`compact` grounds 23/23
(every score exactly 1.00); `detailed` grounds 7/20**, its ungrounded evidence strings
being 21–24-word narrative sentences.

**3. Relaxing grounding to a "longest verbatim span" rule is refuted by the data.** The
quarantined true positive quotes `'Quantity: | 1'`, which tokenizes to two words, so its
longest verbatim span is **2**. A span rule at N≥3 would **still miss it** while promoting
three control-corpus false positives (spans 11, 7 and 6). The idea fails in both
directions at once.

**4. Grounding is not selecting for correctness on this fixture.** Among the 30 grounded
positives — the ones that actually reach a report — exactly **one is true: 3% precision**
(`detailed` 1/7 = 14%, `compact` 0/23 = 0%). `compact` grounds 100% of 23 false positives
and detects nothing.

**5. Raising `detailed`'s grounding compliance would make the reports worse, quantified.**
On the control trajectory `detailed` produces 8 false positives of which 3 are currently
grounded and reported, 5 quarantined. Perfect compliance would take reported control
false positives from **3 to 8**, in exchange for rescuing at most one true finding.

**Conclusion recorded for the next decision:** the binding constraint on reportability is
**precision, not grounding**. The dominant false-positive classes on the control are
mechanically contradicted by the record the judge was shown — a gated `Continue` link
reported as hidden while the same window says `now shown : a#to-step-3`, correct
validation reported as `validation_bypass`, a `SELECT` that reveals a control reported as
`dead_control`. That is the shape `judge/validate.py` and `reward/gating.py` already
exist to handle: where a rule is mechanically decidable, decide it in code rather than
spending prompt attention on it.

**Scope caveat.** All of the above is one model (`qwen2.5:7b-instruct`), one fixture, and
one trajectory per corpus. The same judge scores 10/10 with 3/6 control false positives on
the toy site, so "3% precision" is a statement about the deep-flow fixture, **not** about
the judge in general.


### 2026-08-30 (offline contradiction study) — the filter clears its pre-registered bar, on rules that had to be rewritten mid-study

Diagnostic only. **No code changed, no judge calls made, nothing implemented.** Every
number was recomputed offline from `reports/judge_deep01_ollama_validation.json`, which
stores each verdict's `window_text` — the exact text the model was shown — so "is this
verdict contradicted by the record it was given?" is fully decidable at zero cost.
Per-case output: `reports/diag_contradiction_study.json`.

#### Established: the pre-registered bar is met on every criterion

All 43 stored positive verdicts were classified against their own recorded window.

| criterion | before | after | target | |
|---|---|---|---|---|
| control false positives, `detailed` | 8 | **4** | ≤4 | **MET** (no margin) |
| control false positives, `compact` | 11 | **5** | ≤6 | **MET** |
| DEEP-01 detections, `detailed` | 2/2 | **2/2** | 2/2 | **MET** |
| evidence/step linkage | 43/43 | 43/43 | 43/43 | **MET** (no rule touches indexing) |

Across all six runs: **23 of 43 positives are mechanically contradicted, 18 are not, and
the 2 true positives are untouched — zero true positives suppressed by any rule.**
Removals by corpus/prompt: scripted 2/4, noisy 3/4, control 4/6 (detailed/compact).

#### Established: one of the three hypothesised classes does not exist

The 2026-08-30 design investigation recorded that a dominant false-positive class was
*"a gated `Continue` link reported as hidden while the same window says
`now shown : a#to-step-3`"*. **A rule for exactly that fired 0 times out of 43.** The
control's `ui_regression` verdicts are not of that shape. `compact` step 1 claims *"the
link to 'Step 2' is hidden, making it unreachable"* and the record **agrees** —
`now hidden : a#to-step-2`. The model is not contradicting the record; it is reading
correct gating as a defect, which is a semantic judgement no mechanical rule can make.
**That earlier characterisation is corrected here.** It was written from the shape of the
verdicts rather than from a case-by-case check against the windows.

#### Established: the originally proposed `validation_bypass` rule is unsafe

The natural rule — *flag `validation_bypass` when the record shows no failure signal* —
fires on **all 14** such verdicts and looks excellent. It is refuted by its own safety
requirement rather than by the data: a **genuine** bypass also has no console error, no
HTTP error and no `FAILED` line, so the rule would suppress real findings. It was
replaced by a constraint-satisfaction test: the record declares
`constraints: quantity declares [type=number min=1 max=100 step=1]` and
`field value: quantity: '' -> '42'`, and 42 satisfies it, so *"no validation error was
shown"* describes correct behaviour. When the value **violates** the constraint the rule
stays silent and the verdict stands — the property the first version lacked. This costs
one removal on `detailed` (5 → 4), which is why that bar is met with **zero margin**.

#### Proposed contradiction classes — design only, not implemented

| rule | fires | distinct situations | statement |
|---|---|---|---|
| **R2′** constraint-satisfied | 8 | 2 | `validation_bypass` claimed, but the quoted `field value` satisfies the declared `constraints` |
| **R3** dead-control-that-acted | 9 | 2 | `dead_control` claimed, but the judged step records `now shown`, a URL change or a form-state change |
| **R5** contradicts-own-evidence | 4 | 1 | the verdict says a field is absent/filled while the `form state` transition it itself quotes records the opposite |
| **R4** evidence-from-another-step | 3 | 3 | the quoted evidence is verbatim in the window but **not in the step being judged** — e.g. `NO OBSERVABLE CHANGE`, which the renderer emits only when `not navigated`, cited on a step that navigated |

#### Not mechanically removable (18), and why

Correct gating read as a defect (`compact` 1); an untouched empty field read as violating
`min=1` (`compact`/`detailed` 3); `state_persistence` and `broken_flow` claims about
behaviour across steps that the record neither confirms nor denies; `dead_control` on
repeated clicks where the record genuinely shows no change. Each needs interpretation, or
a judgement about intent, not a lookup.

#### Hypotheses and limits — not established

* **The rules were partly derived from these 43 verdicts.** R1 was dropped and R2
  rewritten after inspecting the cases. Overfitting to this artefact is a live risk.
* **The 23 removals come from roughly 8 distinct underlying situations** repeated across
  six runs (one `SELECT` reveal, one valid quantity, one valid email, and so on). They are
  not 23 independent pieces of evidence.
* **There is no held-out replayable corpus.** `judge_deep01_ollama_validation.json` is the
  only artefact storing `window_text`; the toy and Gitea judge reports do not, so the
  rules cannot be validated offline on data they were not derived from without capturing a
  new corpus.
* **The filter does not address the two findings that actually block reportability.** It
  does not rescue the quarantined true positive, and it does not make `compact` detect
  DEEP-01 (still 0/2). Precision among *grounded* positives — the ones that reach a report
  — stays dominated by the 18 survivors.

#### Recommendation

**IMPLEMENT — conditionally, and not as a silent suppressor.** The pre-registered bar is
met on all four criteria with no true positive suppressed, and every removal has an
inspectable, record-grounded reason. Two conditions follow from the limits above: the
filter should **quarantine with a stated reason** exactly as the existing ungrounded path
does, rather than dropping verdicts; and because the rules were shaped by this artefact
and no held-out set exists, a second corpus should be captured and scored before the
filter is trusted to silence anything.

**Next action: capture one held-out judged corpus that stores `window_text`, and validate
the four contradiction rules against it before implementing the filter.**



### 2026-08-31 — repository finalization: documentation brought to reality, 656 MB of superseded artefacts removed, one real defect found

Finalization pass, not a milestone. **No RL behaviour changed**: agents, reward,
exploration, PER, n-step, epsilon, the archive and the deep-flow fixture are byte-identical,
and no training was run. One defect was found and fixed, in the test harness.

#### A real defect, found by running the suite rather than trusting it

`tests/integration/test_intake_docker_smoke.py` **failed** rather than skipping on a
machine with Docker Desktop installed but stopped. The cause was in the gating:
`conftest.has_docker()` checked only `shutil.which("docker")` — the CLI on PATH — while
the tests need a *reachable daemon*. Docker Desktop leaves the CLI installed when the
engine is off, so the module was neither skipped nor able to run and failed with
`DockerException: Error while fetching server API version`.

That is worse than it sounds for a repository being handed over: **a skip condition that
does not match what the test needs reports a stopped daemon as broken code**, which is
the first thing a newcomer would see. `has_docker()` now also runs `docker info`
(cached once per session), and `pytest_collection_modifyitems` attaches a skip with a
reason, symmetrically with how the browser modules were already handled.

#### Final verification — measured, not assumed

| check | result |
|---|---|
| full suite (`pytest tests -q`) | **864 passed, 14 skipped, 0 failed** |
| CI portable subset (the exact CI command) | **694 passed, 5 skipped, 179 deselected** |
| every module imports | all of `web_testing_agent.*` import cleanly |
| repository profiling | `demo_repo` profiled: Dockerfile build definition, `CMD` entry point, README detected |
| compose handling | detected as `kind='compose'`, policy-validated, **refused** by `deploy_repository` |
| offline judge scoring | `score_judge.py --judge stub` runs end to end; gate 37/39 windows (95%) |
| pipeline / evidence / profile / report units | 110 passed |
| Docker integration | **not run — daemon stopped.** Environment limitation, now correctly skipped |

#### What was removed, and why

| removed | size | reason |
|---|---|---|
| 43 checkpoints | **656 MB** | superseded families (`dqn_toy*` v1/v2/v3, pre-dueling `diag_ac_seed0_*` and `diag_ac_3seed_s*`) plus 6 byte-identical duplicates. All were gitignored, so **none were ever in the repository** |
| 22 scratch files `reports/_*` | 4.9 MB | gitignored run logs; the real output is the JSON beside each |
| `logs/web_testing_agent.log` | 17 MB | gitignored runtime log |
| 4 duplicate reports | — | `diag_ac_3seed_deep.json`, `diag_q_spread.json` and the two `_BASELINE` copies were **byte-identical** to their arm-named twins (verified by hash). The arm-named one was kept in each case |
| `__pycache__`, `.pytest_cache` | — | caches |

**No unique data was destroyed.** Every duplicate was hash-verified identical before
removal, and every deleted checkpoint's *results* remain in committed JSON reports.

#### What was retained, and why

* **Six checkpoints** (`diag_ac_3seed_{dueling,c1}_s{0,1,2}.zip`, 98 MB) — the two arms of
  the current documented comparison, the only ones from which the `order-4` Q-value
  evidence can be re-derived without retraining. **They are gitignored and therefore not
  in a clone**; `scripts/compare_agents.py` regenerates them.
* **All measured reports** (~3.4 MB of JSON/MD now committed), per the standing policy
  that reports embed their own verdicts and stay readable after their corpus is gone.
* **The full DEEP-01 Ollama validation artefact and its twelve generated reports** — the
  only replayable judge evidence in the project, and the basis of the contradiction study.

#### Documentation corrected against reality

The README claimed **497 tests**; the suite is 864. Eight modules existed with no mention
(`pipeline.py`, `intake/repo_profile.py`, `intake/extract.py`, `annotation/evidence.py`,
`agents/{ac_dqn,action_features,archive_start,replay}.py`). Newly documented: the
repo→report pipeline and its stages, the **Dockerfile-only** deployment scope, the
trusted/controlled threat model and *why* it is structural (`docker build` runs the
repository's commands before any run-time restriction exists), compose detection and
refusal, evidence capture and the 43/43 linkage result, the judge's **precision** problem
with its measured numbers, CI's three jobs and the marker scheme.

Every claim was cross-checked: 7 referenced scripts exist, all referenced reports exist,
and documented CLI flags were verified against `--help`. One error was caught this way —
the README told the reader to pass `--base-url` to `run_repo.py`, which has no such flag
(it deploys); corrected to `run_judged.py`.

**The RL section was materially wrong and is rewritten.** It still claimed *"masked random
beats the DQN ~10× on distinct findings"* — retracted long ago as self-link false
positives — and said nothing about dueling, C1, or the flow completions.

#### RL status, recorded unambiguously: paused by decision, not finished

* **Established:** C1 produced the first flow completions in the project's history —
  **10/15 evaluation episodes**, against 0/15 before.
* **Established:** the apparent *seed-level* split behind that number was later found to
  be **run-to-run variance**, not a property of the seeds.
* **The next RL action is to measure the run-to-run completion distribution at a fixed
  seed**, before attempting any exploration fix. No candidate fix is approved.
* **Still open and explicitly unmeasured:** PER *sampling* of the final
  `Place order -> receipt` transition. The replay buffer is not saved with checkpoints, so
  "sparse experience" remains a hypothesis.

#### Known issues, intentionally deferred

* **`PROJECT_CONTEXT.md` is gitignored and therefore absent from any clone.** That was a
  deliberate decision (§10) and has been left standing rather than reversed unilaterally,
  but it now conflicts with the stated goal of handing the RL research to someone else.
  Mitigated by giving README a full *"Where RL research resumes"* section; **the tracking
  decision itself is the user's.**
* The contradiction filter is designed and validated offline but **not implemented**, and
  its rules were partly derived from the same 43 verdicts with no held-out corpus.
* The Docker integration suite has not been exercised in this pass (daemon stopped).



### 2026-08-31 (handoff) — this file is now tracked

One-line change with one consequence worth recording. `PROJECT_CONTEXT.md` was removed
from `.gitignore` and committed, so the complete project context — every milestone,
negative result, retraction, correction and the exact RL resume point — now travels with
the repository instead of living only on one machine inside a OneDrive folder.

**Nothing was rewritten, condensed or removed to make it presentable.** The 33 dated
entries, the retracted claims and the superseded numbers are all intact, including the
ones that make the project look worse: the self-link false positives that invalidated a
published 10x figure, the reveal-bonus exploit this document's own author introduced, the
run-5 single-seed defect, and the judge's 3% precision on the deep-flow fixture. A handover
document that quietly drops its own negative results is worth less than no document.

Two statements were corrected because tracking the file made them false: §10's *"This file
is not in git, and that is deliberate"*, and the same bullet's open question about backup,
which git now answers. The original reasoning is kept beside both, because it is what the
decision was weighed against.

**Checked before tracking:** no credentials, API keys, tokens or email addresses anywhere
in the file. It does contain the developer's local repository path in §10 (`C:\Users\...`),
which is a dev-environment note rather than a secret — worth knowing if this repository is
ever made public.

**Scope:** documentation only. No RL code, agent, reward, environment, fixture, evaluation
protocol, training configuration or experiment result was touched, and nothing was deleted.



### 2026-09-02 → 09-05 — the September series: the fixed-seed distribution, four nulls, and a myopic control

Six experiments ran between the handoff and the search ladder. They are recorded together
because they form one argument, and because none of them was in this file until now.

**1. The run-to-run completion distribution at a fixed seed — the blocking measurement, done.**
The 2026-08-31 entry above and README's *"Where RL research resumes"* both named this as the
next RL action, and neither has been updated since; **this supersedes both.** Three
replicates of seed 0 at 4,000 steps produced **1, 2 and 5 flow completions out of 5**
(`reports/fixed_seed_completion_distribution.json`). The spread at one seed covers nearly
the entire range the metric can take.

A companion measurement explains why. At the full 4,000-step budget, three nominally
identical uninstrumented runs produced **three distinct trajectories — 0 of 3 pairs
identical, agreement rate 0.0** (`reports/diag_harness_determinism_4000.json`). The
intermittent harness non-determinism recorded in README at 200-600 steps is not intermittent
at 4,000; it is the norm. **A "seed" in this project is a label on a run, not a reproducible
unit**, and every per-seed figure below has to be read that way.

**2. Archive ON vs OFF ablation.** `p_return=0.5` against `p_return=0.0`, 8 seeds, 5
evaluation episodes (`reports/ablate_archive_pr050.json`, `..._pr000.json`):
`[5,0,4,5,5,2,5,0]` = 26/40 with the archive, `[0,5,0,5,5,4,0,5]` = 24/40 without. **Null.**
The Go-Explore start-state curriculum is not what produces flow completions.

**3. The Markov observation fix.** `EPISODE_CONTEXT_DIM` widened 6 → 9, adding the run-level
state-visit count, the novelty decay multiplier `1/sqrt(1+visits)` itself, and the episodic
reveal-ledger depth — the three hidden counters `novelty_bonus` and `reveal_bonus` had
reintroduced after slots 0-5 were built to close exactly that gap. Two further slots were
audited out before shipping (one collinear, one post-action and therefore unable to predict
`r_t`); the reasoning is in `envs/types.py`. Re-baselined at 8 seeds:
`[5,0,5,5,0,0,5,5]` = 25/40 (`reports/postmarkov_baseline_pr050.json`). **Null** against the
24-26/40 the two archive arms already sat at. `tests/unit/test_markov_context.py` pins that
no reward term, counter or scale moved.

**4. Action-representation probe.** Does the 32-dim signed-trigram label hash encode
*semantic* similarity, or only shared characters? Semantic pairs mean cosine **0.249** against
random pairs' **0.018** (permutation p = 0.0002) — but stratified, semantic pairs *sharing a
token* score **0.502** and semantic pairs with *no shared token* score **0.081**
(`reports/probe_action_representation.json`). **The separation is lexical overlap, not
meaning.** `LabelEncoder` is the seam a sentence embedding was always meant to be swapped in
at, and this is the measurement that says the seam is empty.

**5. Trained-checkpoint action-Q diagnostic.** Across 6,096 within-state CLICK-CLICK pairs
scored by the 8 final checkpoints, label cosine correlates with |Q gap| at Pearson
**-0.0396**; mean |Q gap| is **0.2027** against a reward scale of 1.5-2.5, having grown
**33.6x** from initialization (`reports/trained_action_q_diagnostic.md`). At the `order-4`
decision, `Place order` is the argmax in **5/8** seeds and ranks above `Cancel order` and
above `Back` in **6/8** each. Five of the seven requested label pairs have **zero** co-valid
states, so they are unavailable rather than negative evidence. Observational only: it cannot
establish that the representation *caused* the deadlock.

**6. The contextual bandit, and the 25-episode protocol.** `agents/contextual_bandit.py` is a
myopic control that removes exactly one thing from AC-DQN — the bootstrapped future — and
keeps everything else: same env, same 6973-dim observation, same 100-slot masked action
interface, same hash encoder, same action features, same reward, same epsilon schedule, same
archive and `p_return`, same budget, same evaluation. Target is `data.rewards`, full stop;
no target network, no next state, no gamma, `n_step` forced to 1. Four independent tests
enforce that rather than a docstring asserting it.

At 5 evaluation episodes AC-DQN looked perfectly bimodal (`[5,0,5,5,0,0,5,5]`, 8/8 extreme)
and the bandit did not (`[5,2,0,0,5,1,4,3]`, 4/8 intermediate). **Five episodes turned out to
be the instrument, not the finding.** Re-evaluating the same 16 checkpoints at 25 episodes
with no retraining (`reports/offline_eval_25ep.md`) gave AC-DQN `[24,0,25,23,1,2,24,17]`
(mean 0.580) against the bandit's `[17,8,0,0,25,14,24,20]` (mean 0.540) — the arms are not
clearly distinguishable, and the apparent difference in *shape* was an artefact of the
evaluation length. **25 episodes is the canonical evaluation protocol from this point on.**
The seed-1 bandit archive failures were inspected and judged ordinary conservative failed
returns, not infrastructure contamination; nothing was excluded.


### 2026-09-16 — the search ladder: A0 hand-priority search vs contextual bandit vs AC-DQN

**The question.** Five consecutive nulls left one rung unmeasured: what does a *non-learning*
policy with a reasonable hand-designed action priority do at the same budget? Without that
floor, "learning helps" has nothing to be measured against.

Three arms, one fixture, one observation, one action space and mask, one reward, one archive,
one evaluation protocol. They differ only in how an action is scored:

| rung | arm | scoring rule |
|---|---|---|
| A0 | `hand_priority` | `w · φ(a)`, `w` hand-designed and **fixed** |
| B | `contextual_bandit` | `f_θ(s, φ(a))`, θ regressed onto `r_t` |
| C | `ac_dqn` | `Q_θ(s, φ(a))`, θ regressed onto `r_t + γⁿ max_a' Q_target(s_{t+n}, a')` |

φ is the *same* 52-dim `ActionFeatureExtractor` vector in all three rows — A0 is a hand
weighting of the representation the other two learn a weighting of, not a parallel one.

**Budget, and why it is matched.** The repository already measures search in env steps
(`run_go_explore --budget`, `GoExploreStats.env_steps`) and training in the same unit
(`--train-steps 4000`), with route replay outside the step budget but reported as
`replayed_actions`. A0 got 4,000 env steps per seed in 100 × 40-step episodes through the
same `ArchiveStartWrapper` at the same `p_return=0.5` under the same ε schedule
(1.0 → 0.10 over 60%): 32,000 env steps + 2,252 replayed actions = 34,252 browser actions,
against the trained arms' 4,125-4,394 each. Both numbers are reported, neither absorbed.
Arms B and C were **not retrained** — their finalized checkpoints were re-evaluated
contemporaneously, SHA-256 taken before and after, all 16 unchanged.

**Results.** 8 seeds × 25 evaluation episodes, landing page, archive disabled, ε 0.05.

| arm | counts /25 | mean | median | sd | bootstrap 95% CI | review | S1 | max depth |
|---|---|---|---|---|---|---|---|---|
| A0 `hand_priority` | `[0,0,1,0,0,0,0,0]` | **0.005** | 0.000 | 0.014 | `[0.0, 0.015]` | **0.855** | 0.950 | 5 every seed |
| B `contextual_bandit` | `[17,8,0,0,25,14,24,20]` | **0.540** | 0.620 | 0.398 | `[0.28, 0.79]` | 0.755 | 0.875 | 5 |
| C `ac_dqn` | `[24,0,25,23,1,2,24,17]` | **0.580** | 0.800 | 0.458 | `[0.27, 0.855]` | 0.730 | 1.000 | 5 |

B and C reproduced the 25-episode re-evaluation **exactly** — 16/16 checkpoints matched on
completion count and review count, 10/16 to the 4th decimal on mean reward. 32/32 runs
completed, 0 excluded, **0 invalid action selections** across 800 evaluation episodes,
6.34 h wall clock. Artefacts: `reports/search_vs_bandit_vs_ac_dqn.{json,md,csv}`.

#### The A-vs-B/C result is confounded, and the earlier reading of it is retracted

**Do not cite this experiment as "learning action identity/value beats search."** That claim
does not survive inspection, and the report's own auto-generated A-vs-B verdict line
(`contextual_bandit is ahead of hand_priority by 0.535…`) overstates it. This entry is the
authoritative reading; the JSON/Markdown artefacts are left unedited so the raw result and
its wording stay on record.

A0 was built with **all 32 hashed-label dimensions weighted exactly zero**, as a guarantee
that it could not recognise `Place order` or any other control by name. That guarantee did
its job — A0 is provably invariant to relabelling every control on the site — but it also
**denied A0 the one piece of information that resolves the decision the benchmark turns on.**
On `order-4` every candidate is a navigational, in-viewport link carrying an href, so every
feature A0 is permitted to read is identical across them; the only discriminator is the
label. B and C could use it. A0 could not. That is an **information-access confound**, not a
learning-vs-search comparison.

The data say exactly where the gap sits. A0 reaches the final review page in **85.5%** of
episodes — *more often than either learned arm* (75.5% and 73.0%) — and then completes
**0.6%** of the time from there, against AC-DQN's 84.6%. Its search phase reached depth 5 on
every seed and completed the flow 22 times across 800 search episodes. **Generic exploration
and reachability were not the obstacle.** The entire measured gap is one decision on one page.

The defensible conclusion:

> Access to the distinguishing action information is important for resolving the critical
> decision. The experiment does **not** isolate whether the advantage comes from *learning*
> or from *access to label information*.

Recorded as a limitation of the completed experiment, and it is what the next milestone
exists to resolve.

#### The B ≈ C null, stated with its scope

> No detectable advantage for AC-DQN over the contextual bandit was observed **under the
> current reward, archive/reset configuration, benchmark, and tested budget.**

Mean difference **+0.040**, bootstrap 95% CI **`[-0.360, +0.425]`**. Per-seed differences
`[+0.28, -0.32, +1.00, +0.92, -0.96, -0.48, 0.00, -0.12]`: **3 seeds favour C, 4 favour B, 1
tied.** The mean is not a small consistent edge; it is a near-even split of very large
opposite swings.

**This does not show that bootstrapping is useless, and it must not be written that way.**
Two reasons the design does not put bootstrapping under much strain:

* **The reward is overwhelmingly immediate and dense** — novelty, newly-revealed-action,
  repetition penalty, step cost, failed-action penalty. A myopic predictor of `r_t` already
  captures most of the signal by construction.
* **The archive supplies frontier-state returns**, so the credit that would otherwise have to
  propagate backwards over a long horizon is substantially short-circuited by the start-state
  curriculum.

So this benchmark does not strongly test delayed credit assignment, and **"sequential RL is
unnecessary for web testing" is not a conclusion this supports.** That remains unresolved.

**Effective sample size.** The 8 seeds are strongly bimodal at the run level — most
checkpoints sit near 0/25 or near 25/25 — so B vs C rests on **8 independent seed-level
outcomes, not 200 independent episodes.** Pooled episode counts are descriptive only, and
every interval quoted here resamples checkpoints. Combined with the 0.0 harness agreement
rate at 4,000 steps, a seed is one draw from a wide run-level distribution rather than a
reproducible condition.

#### One thing deliberately not done

While tracing where A0 stalls, a generic "page-uniqueness / control-IDF" term was identified
that would plausibly have carried A0 past `order-4`. It was conceived **after** seeing the
result, so adding it would have been fitting the heuristic to the benchmark. It is recorded
in the report as considered-and-rejected rather than used. The same applies to a frontier
term over unvisited href targets.

#### Unresolved research questions, recorded as open

1. Does **learning** contribute beyond simply providing access to semantic/action-label
   information?
2. Under a genuinely **delayed-reward** web-testing formulation, does sequential value
   learning provide an advantage over contextual-bandit learning?
3. Does learned action value **transfer across applications**? (The action-conditioned
   representation exists for this, and a single-fixture benchmark cannot see it.)
4. How **representative** is the current Deep fixture of broader web-testing tasks?


### 2026-09-16 (advisor decision) — resolve the label-information confound before building delayed-credit benchmarks

The advisor reviewed the completed results and approved the sequencing. **Before**
constructing delayed-credit benchmarks or running further major RL comparisons, resolve the
A0 label-information confound identified above.

The next experiment has **five conditions**:

| arm | definition |
|---|---|
| **A0** | current hand-priority search, label dimensions zero-weighted (unchanged) |
| **A1** | hand-priority search **+ a generic lexical commit-verb prior** |
| **A2** | hand-priority search **+ frozen sentence-embedding similarity against generic web-UI anchor phrases** |
| **B** | existing contextual bandit, **unchanged** |
| **C** | existing AC-DQN, **unchanged** |

The logic: A0 → A1/A2 varies *access to label information* while holding *learning* fixed at
zero. If A1/A2 close most of the gap to B, the A-vs-B difference was about access rather than
learning. If they do not, learning is doing something access alone does not.

#### Methodological requirements, recorded as binding

1. **Freeze the exact definitions of A1 and A2 before inspecting or tuning against the
   fixture.** This is the whole point; a lexicon tuned against `order-4` measures nothing.
2. **A1's commit-verb vocabulary must be generic web-UI vocabulary, fixed in advance.**
3. **A2's anchor phrases must be generic web-UI phrases, fixed in advance.**
4. **No tuning** of the lexical list or the semantic anchors after inspecting `order-4`
   behaviour.
5. **Pre-specify the decision rule for what counts as "A2 matches B"** before running.
6. **Primary comparison on per-seed solve counts/rates.** Episodes are not independent
   replicates (see the effective-sample-size note above).
7. **Preserve the existing protocol byte-for-byte wherever applicable:** 8 seeds, 25
   evaluation episodes, same environment, same budget, same archive configuration, same
   epsilon schedule, same reward, same evaluation protocol.
8. **Report mechanistic metrics for every arm:** completion, Review rate, S1 rate, max depth,
   and the relevant reward/coverage metrics — not completion alone. The 13b decomposition in
   the last report is what made the confound visible at all.
9. **Run the learning-onset re-analysis from existing checkpoints in parallel**, if the
   required checkpoint/evaluation history exists.
10. **Commit the A1/A2 definitions before looking at their final results.**

**Status: not started.** A1 and A2 are **not implemented and not run** as of this entry. The
A0/B/C results above are historical and must remain unchanged.

#### Known issues, intentionally deferred

* **The search-ladder experiment's code and reports are not yet in git.**
  `src/web_testing_agent/agents/hand_priority.py`, `tests/unit/test_hand_priority.py`,
  `scripts/search_vs_bandit_vs_ac_dqn.py` and `reports/search_vs_bandit_vs_ac_dqn.*` exist in
  the working tree but are untracked, as is the whole September series
  (`agents/contextual_bandit.py`, `agents/diagnostics.py`, five scripts, and their reports).
  This entry references them by path, so a clone cannot currently follow those references.
  **Committing them is a separate decision and has not been taken.**
* `models/checkpoints/` remains gitignored, so the 16 checkpoints every figure here rests on
  are not in a clone.


---

## 6. Scoping decisions

### 6.1 Agent B first
**Agent B (DQN, functional bug detection via LLM reward shaping) is built first**, not Agent A. Agent A (PPO+ICM security testing) is secondary and can be dropped under time pressure — Agent B alone is considered a complete, publishable result. Rationale: avoid over-engineering, get one thing working end-to-end before expanding.

First real (non-throwaway) target app for Agent B dev: **Gitea** — it's one of the three actual training targets (not a disposable prototype app), SQLite-backed so it boots fast for iteration, and has real auth/CRUD/form flows. OpenCart and Nextcloud come later once the wrapper and perception pipeline are proven. A minimal toy site with seeded bugs (§3.3) now precedes even Gitea, as a cheap sanity/validation step.

### 6.2 Repo auto-deploy: Dockerfile/compose-only for v1
"User uploads a repo" (§2 step 1-2) does **not** mean building a general, arbitrary-language build pipeline — that's a platform-scale problem on its own (it's what Vercel/Railway/Render exist to solve). v1 scope: require the uploaded repo to contain a `Dockerfile` or `docker-compose.yml`, and build/run *that*. This covers a large fraction of real-world repos (anything with modern tooling ships one), and is exactly the mechanism already used for the five existing training targets (`docker/docker-compose.yml`). Repos without one are an explicitly documented v1 limitation, not a gap to silently paper over.

**Amended 2026-08-02:** "build/run *that*" is not safe on its own — an uploaded compose file specifies its own security context and can simply ask for host root. Every uploaded build definition must first pass `intake/compose_policy.py` (§3.0a). The scope cut stands; the execution of it needed a gate.

### 6.3 Evaluation protocol (decided 2026-08-02)

Two decisions that shape what the project can claim:

1. **A random-action baseline is mandatory, and it runs first.** In the GUI-testing literature, uniform-random "monkey" exploration is a notoriously strong baseline. If the DQN does not beat it at equal step budget on bug-discovery rate, there is no RL contribution to claim — and that is far cheaper to discover now than after a 300K-step run. Built and measured (§5); it sits ahead of ZAP/Burp/Cypress in the baseline list because it bounds the *agent*, not the tool category.
2. **Train and evaluate on disjoint applications.** Training and evaluating on the same three apps measures memorization, not the capability being claimed. The defensible framing is **train on {OpenCart, Nextcloud}, evaluate zero-shot on Gitea plus the uploaded-repo pipeline**. This also makes the action-conditioned Q-network's generalization argument testable rather than assumed.

`evaluation/rollout.py` scores any policy identically (random or trained), and deduplicates trigger firings into *distinct* findings — counting raw firings would let one persistently broken element inflate a score arbitrarily.

---

## 7. Key design decisions (and why)

- **Playwright sync API, not async, for the Gym env.** Gymnasium/SB3 expect plain synchronous `step()`/`reset()` (SB3 parallelizes via subprocesses, not asyncio). Bridging an asyncio event loop into every single step would add real complexity for no benefit. The spec's "async pipeline" requirement is specifically about the semantic perception layer running its three encoders concurrently — that's a different call site, downstream of this raw observation capture, and doesn't exist yet.
- **`FunctionalRewardModel` as an injectable interface, not a hard dependency.** The real Phi-3 LLM reward model is a later phase, and the env must not be blocked on it. `NullRewardModel` (always "expected, not a bug") is the default so `WebFunctionalEnv` is fully runnable today off deterministic triggers alone. Swapping in the trained model later is `WebFunctionalEnv(reward_model=...)` — zero env code changes.
- **"Page never reached quiescence" as the proxy for "spinner visible >5s"** (revised 2026-08-02). Detecting an actual spinner icon would need a vision model. The original wiring — elapsed time > 5s — could not fire, because the settle wait capped at 3s; it only ever meant "the Playwright action timed out". The signal is now the DOM-quiescence wait failing, measured at the point a user's patience would run out, and **edge-triggered** so a permanently-stuck page is reported once against the action that caused it rather than against every action afterwards.
- **DOM quiescence, not `networkidle`, as the settle condition.** `networkidle` demands 500ms of zero network traffic, which polling/websocket apps (Nextcloud, Gitea) never satisfy — every step would pay the full timeout for no information. A `MutationObserver`-based quiescence check answers the question actually being asked ("has the page stopped changing?") and is dramatically cheaper on exactly the targets that matter.
- **Text modalities travel in `info`, not the observation space** (2026-08-02). Arbitrary page HTML cannot be honestly expressed as a `gymnasium.spaces.Text` (finite charset), and SB3 cannot consume `Text` in a `Dict` anyway. Since the agent's real observation is the fused 256-dim vector produced downstream, the raw text belongs in Gymnasium's auxiliary-data channel — which also keeps the screenshot from being serialized twice across the `SubprocVecEnv` boundary.
- **Screenshots are canonicalized to a fixed shape even though the viewport genuinely changes.** `RESIZE_VIEWPORT` must really resize — responsive bugs only exist at 375px — but a variable-shape observation is not a valid Box. Resize the *tensor*, not the browser.
- **Perception encoders live in a `VecEnvWrapper`, not the env** (§3.2). The per-env alternative multiplies model copies and VRAM by the number of parallel browsers while degrading every forward pass to batch size 1.
- **The exploration bonus depends on state normalization, not on raw HTML.** Rotating CSRF tokens and timestamps make every revisit look novel; without `state_fingerprint` the novelty bonus silently becomes a constant, which is worse than not having one.
- **Reward composition is a tunable hyperparameter surface, not a fixed formula.** `FunctionalRewardWeights` exposes every magnitude as a retunable default. `bug_severity_scale=10.0` matches the spec; the bonus/penalty terms are this implementation's addition. **`expected_reward` deliberately departs from the spec** (0.0, not +0.5) — see §3.3 for the arithmetic showing why the spec's value makes `NO_OP` the optimal policy. Any future change to it should be accompanied by re-checking that number.
- **Deviating from the spec is recorded, not silent.** Where the implementation disagrees with the original specification (`expected_reward`, the slow-response trigger, the observation space, the response cache), the deviation and its justification are written down here rather than quietly applied — the spec is a design input, not an authority to be contradicted invisibly.
- **"Broken navigation" only applies to link/submit clicks**, not every action type (see the bug fix in §5). This was a genuine design error caught by actually running the code against a live browser, not something derivable by reading the code alone.
- **Dynamic action-space budgeting priority:** fixed page-independent actions (no-op/scroll/resize/back/forward/refresh) → clicks (needed to progress any flow) → valid-value TYPE (so forms can actually be submitted) → SELECT → RAPID_CLICK → boundary/edge-case TYPE variants fill remaining budget. On pages with more interactive elements than fit in 100 slots, this ordering determines what gets dropped. (Still missing viewport-awareness — see §3.1.)
- **Action-conditioned Q-network vs. flat 100-way head — A/B still planned, but the expected outcome is now evidenced** (§3.3). The flat head matches the spec literally and has two structural weaknesses: non-stationary index semantics, and (measured) 87.5% of exploration spent on no-ops because SB3's DQN cannot mask invalid actions. The toy-site A/B remains worth running for a reportable number, not as a genuinely open question.
- **Repo auto-deploy scoped to Dockerfile/compose-only** (§6.2) rather than general multi-language build automation — a deliberate scope cut to keep the "upload any repo" vision tractable within project constraints, not an oversight. The scope cut is separate from the *safety* of executing what's in scope, which needed its own gate (§3.0a).
- **The uploaded build definition is validated before execution, not merely constrained during it.** Resource quotas cannot undo `privileged: true` or a `/:/host` bind mount, because those are requests for a security context rather than for resources. Parsing and rejecting is the only control that works at that layer.
- **A policy over a parsed document is only as strong as the guarantee that the parsed document is the one that runs** (added 2026-08-15). Every rule in `compose_policy.py` was correct about the file it was handed, and three Compose features still walked past all of them — `extends` and `include` merge in a *second* file after validation, and `driver_opts: {device: /}` makes a named volume a host bind mount without ever writing a host path where the volume check looks. The fix is not more per-key rules; it is refusing any construct that changes which document is executed. When adding a rule to this gate, first ask whether the thing being validated is the thing being run.
- **Evaluation must be able to produce a different number twice** (added 2026-08-15). A greedy policy on a static fixture is a deterministic function of the page, so `--eval-episodes 5` reported one trajectory five times and the identical rewards read as precision. Any policy comparison here now carries residual exploration at evaluation and repeats over training seeds, reporting median [min-max]. A metric with no spread is either genuinely converged or not being measured, and the two look the same in a table.
- **Findings are deduplicated by (trigger, url, element) rather than counted per firing.** One persistently broken element would otherwise let any policy inflate its score without exploring anything.

---

## 8. Potential challenges and open risks

**Security of executing arbitrary uploaded code — still the most serious risk; partially mitigated 2026-08-02, three bypasses closed 2026-08-15.** Auto-deploy (§3.0a) means building and running whatever a user uploads. The policy gate (`intake/compose_policy.py`) blocks the class of attack that resource limits cannot touch — privilege escalation and host bind-mounts requested by the compose file itself. A 2026-08-15 review found three ways around it (`extends`, top-level `include`, and a named volume backed by `driver_opts: {device: /}`), all now blocked; see §5 and the new §7 principle. **That the gate shipped with three bypasses is itself the risk signal here** — it was written carefully, unit-tested, and still missed an entire category, because the category sat one layer above the one being validated. Assume more remain. **Remaining exposure:** the runner that applies `SANDBOX_RUN_ARGS` is not built yet, and a policy-clean container still runs untrusted code, so the network-isolation, read-only-rootfs, and timeout enforcement all remain to be implemented and *tested* (a passing policy check is not a sandbox). The browser pointed at that container is also a target and needs its own confinement.

**Repo Profiler context-length constraint — strategy decided 2026-08-02, unimplemented.** Resolved in favour of tree-sitter/static extraction feeding a long-context code model (§3.0b). The risk is now execution, not design: the static extractors are per-framework, and coverage across arbitrary uploaded repos will be uneven.

**Perception layer latency budget.** LLaVA-1.5 7B image captioning is comparatively slow (likely well over 500ms on its own on a single GPU), which conflicts with the <500ms/timestep target even with async concurrency. The spec mitigates this by making LLaVA "for complex pages" only and caching per unique screenshot, but the trigger condition for "complex page" and the cache hit rate during active RL exploration (where screenshots constantly change) both need validation once this layer is built.

**Action-space staleness on dynamic/SPA pages.** Action specs (including CSS selectors) are built once per step from a DOM snapshot and used to execute the *next* action. On highly dynamic pages (AJAX-heavy SPAs — relevant for Nextcloud/OpenCart), the DOM can mutate between build and execution (timers, websockets, lazy-loaded content), causing a selector to go stale. The current design treats a failed action as legitimate data (`exec_success: False`) rather than crashing, which is reasonable, but a high stale-selector rate would degrade the agent's effective action space quality during training.

**Non-stationary action-index semantics for DQN (see §3.3, §7 design decision).** A harder RL problem than the spec's Atari-style framing suggests, and it gets worse as site complexity/UI diversity grows. Compounded by the measured 87.5% no-op rate under the unmasked flat head. Mitigation designed (action-conditioned Q-network) but unbuilt.

**Reward hacking for Agent B — validated under training 2026-08-02, and it was worse than predicted.** This risk was written as a hypothetical; a DQN then found **four distinct exploits in five runs** (§5), each one novel after the previous fix. All four are now closed and pinned by regression tests. The prediction that "a DQN is far more inventive at finding exploits than a test suite is" was correct, and understated: one of the tests written specifically to prevent exploit #1 *passed* while the exploit was live, because it used the cheapest trigger to write rather than the cheapest trigger available to the optimizer.

**The deeper problem is not any individual exploit.** Run 5 measured that the best-by-reward checkpoint found *fewer* bugs than a lower-reward one — so optimizing this reward harder does not find more bugs. Hand-specified shaping is a weak proxy for the objective, which is a measured argument for source-grounded LLM judgment rather than an argued one. Assume any future reward change is exploitable until a training run says otherwise; the per-term breakdown in `info` is what makes diagnosis take minutes instead of days.

**Reward weights are unvalidated defaults.** Every magnitude in `FunctionalRewardWeights` was chosen by reasoning about relative scale, not fitted to data. The relationships (bug ≫ novelty > step cost) matter more than the absolute values. Verified analytically that the intended policy ordering holds (find-bugs +22.80 > explore +1.80 > camp −30.15 > idle −38.15), but note that ordering candidate policies correctly is **not** the same as the reward tracking bug discovery — run 5 showed it does not (§5).

**SSIM-based visual regression detection is underspecified.** The spec calls for "SSIM<0.85 between expected and actual page state → visual bug flag," but "expected page state" implies a baseline reference (e.g. from a prior known-good run), which needs a baseline store — not something the browser env alone can own. Deliberately left as an extension point rather than guessed at. It needs a concrete definition of "expected" before the trigger is worth implementing: the most likely answer is a per-state reference screenshot captured on first visit and compared on revisit, which makes it a *regression* detector rather than the bug detector the spec implies. Resolve that before building it (§9).

**HTML/state non-determinism — addressed 2026-08-02, coverage unproven on real targets.** `perception/normalization.py` masks tokens/timestamps/hashes and canonicalizes URLs, and `state_fingerprint` is deliberately coarse. The pattern list is validated by unit tests against representative markup, but it has not yet met Nextcloud's or OpenCart's real asset pipelines; expect to add patterns once pointed at them. Under-masking makes every revisit look novel (inflating the exploration bonus); over-masking merges genuinely distinct states. Both failure modes are visible in the `unique_states` metric, which is worth watching early.

**Domain-based episode termination — fired on Gitea and is now fixed (§5, 2026-08-08).** Loopback aliases and `www.` prefixes were already folded together, but Gitea's footer links to github.com and docs.gitea.com ended episodes after ~7 steps. Off-site navigation is now refused and undone rather than terminating. OAuth-style login flows and CDN/subdomain redirects remain untested and may need an in-scope host allowlist. Worth testing against each real target before relying on this signal at scale; may need an allowlist of additional in-scope hosts.

**Training throughput at scale.** Each RL step does: a Playwright roundtrip (action + quiescence wait) + PNG screenshot + full HTML dump + DOM scan, and will additionally need CLIP+CodeBERT+MiniLM encoding once the perception layer lands. Measured on the toy site: **1.4–2.6 steps/s single-env** — and the toy site is trivially small, so real targets will be slower. At that rate the specified 300K-step curriculum is **~30–60 hours single-env before any GPU encoding**, which is optimistic rather than pessimistic. Two implications: (1) `SubprocVecEnv` parallelism is not optional, and each Chromium costs ~400MB RAM plus its share of encoder throughput; (2) **the 300K-step budget should be treated as aspirational** — plan for 30–50K and say so explicitly in the report rather than discovering the shortfall in week 10. Repo auto-deploy adds a further cost: builds and container startups are not free, and every new target app pays it.

**Windows/Linux dev-prod split.** vLLM doesn't build natively on Windows (the primary dev machine); `bitsandbytes` (needed for LoRA fine-tuning) also has known Windows friction. If training/serving ultimately happens on Linux/A100 infrastructure, code written and smoke-tested on Windows needs a real cross-platform validation pass before Phase 3, not just an assumption that "it'll work the same."

**Evaluation benchmark applicability.** OWASP Benchmark v1.2 is designed for security-scanner evaluation (more naturally Agent A's territory); Agent B's own 150-bug functional benchmark is still undefined (Phase 4). The toy seeded-bug site (§3.3) is a working 10-bug prototype of it with the same answer-key structure, and `evaluation/rollout.py` is the harness it should extend — but 10 bugs on a 5-page static site is not a benchmark, and the leap to 150 bugs across real applications is the larger part of the work.

**The RL contribution has now been measured in the regime chosen to favour it, and it lost there too — resolved 2026-08-17.** The original entry here said run 5's shallow-site loss did not settle anything, for two good reasons: the flat head could not mask invalid actions while the baseline could, and the toy site is 5 shallow pages where sequential credit assignment is worth nothing. Both objections have been removed. The agent has masking (100% valid actions, every seed), and the deep-flow fixture puts the defect behind four ordered gates so random's success probability decays exponentially in sequence length.

The result over 3 seeds and both encoder configurations (§5): ~~**masked random finds 10–12 distinct findings to the DQN's 0–4**, roughly 10× at equal step budget, with no seed overlap~~ **[findings figure retracted 2026-08-27 — self-link false positives; see that entry for what replaces it]**, and no policy of any kind passes stage 1. **"RL's actual claim remains untested" is no longer accurate and should be removed wherever it appears.** The honest statement is that RL was tested on its own terms and did not beat uniform-random exploration over the valid action set.

One caveat kept deliberately: run 5's shallow-site numbers still carry the single-seed evaluation defect (§9 item 2b) and should be cited as suggestive until rerun. The deep-flow result above does not depend on them.

**The project's thesis does not depend on RL winning — and as of 2026-08-07 it has a number.** The differentiator is source-grounded LLM judgment (§1); RL is the exploration mechanism. The worst-case defensible result was written here as a template with an unknown in it; that unknown is now filled: **deterministic triggers saturate at 3/10; the judge reaches 10/10 with zero false positives on known-correct behaviour.** The report should lead with the judge. RL remains the weakest component by evidence and the one most likely to yield a negative result, which is fine — a measured negative on exploration strategy alongside a strong positive on judgment quality is a coherent contribution.

**The window, not the judge, is where defects live on a real target — now a pattern rather than an incident.** Ten defects have been found on Gitea across two passes (§5, 2026-08-08 and 08-09) and **not one was in the judge**. Every one had the same shape: the window stated something true about the *harness* as though it were true about the *application*, the judge reasoned soundly from it, and the verdict looked like a model failure. Recall, discrimination and evidence grounding were satisfied every time — the citations verbatim, the reasoning correct, the premises wrong. None was caught by a metric; they came from reading verdicts and disbelieving them. `judge/validate.py` catches the subset where a window contradicts its own record, which is a real and cheap defence, but it cannot catch a record that faithfully describes the harness acting on the application. **Budget a manual read of every surviving positive after first contact with any new target, and expect the fixes to be in the env.**

**Judge evaluation is at real risk of overfitting to a 10-bug answer key — now demonstrated, not hypothesized.** The first run against held-out traffic (§5, 2026-08-08) found three defects the scripted corpus structurally could not reach, one of which had the renderer feeding the judge a false premise on every retyped field. Every judge number still comes from one 39-window scripted corpus over one 5-page site whose ground truth was authored alongside the judge. Four changes were made *after* seeing results on it (a fixture correction, the schema reorder, the reachability rendering, the lean-window trim), and per-bug tuning was deliberately stopped for exactly this reason (§5). The last of those was made for cost rather than accuracy and improved both models, which is weak evidence against overfitting — but only weak. Two mitigations exist and one is unused: **evidence grounding** is answer-key-independent and therefore transfers to any target, and the **random corpus** (150 windows, captured, never scored) is a partial held-out check. Neither substitutes for a real application — treat every current judge figure as provisional until it survives Gitea.

**A 7B local judge is now close to usable, and the limiting factor was the window rather than the instructions.** Measured across five variants on `qwen2.5:7b`: evidence grounding moved 32% → 100% via few-shot, and the false-positive rate sat at exactly 2/7 across four *instruction* changes — then dropped to 1/7 with +54% discrimination as soon as the *window* was trimmed (§5). The earlier reading of that plateau as a capacity ceiling was wrong. Implication for the product: a hosted judge is the intended path and is acceptable, since the user uploads a repo to a service rather than running the model themselves. Implication for the report: "prompt engineering fixed the mechanical failure and could not touch the judgment failure" is a finding, not a shortfall. The residual risk is a hard dependency on a hosted model for the system's core claim — worth stating explicitly rather than eliding.

**Reward-model grounding is partly validated, in the weaker of its two senses.** The claim has two halves. *Observation grounding* — that a judge given the right slice of observed behaviour can identify semantic bugs deterministic triggers cannot — is now **measured**: 10/10 vs 3/10 (§5). *Source grounding* — that feeding the Application Profile improves judgment further — was **measured properly on 2026-08-26 and did not reproduce.** With a version-pinned `qwen2.5:7b-instruct`, both arms in one session, the hand-authored profile changed **recall, false positives, discrimination and evidence grounding by nothing at all**, and produced slightly *more* positives on the random corpus (44 → 46). The earlier figure below used `gpt-oss:120b-cloud`, an unversioned tag, on a different corpus state. Original claim, retained because it was a real observation with a model that has since moved: on Gitea it halved the false-positive rate, 9 positives → 4, and bought no new true positives. Read that carefully. It shows the plumbing works and that a profile is worth extracting; it does **not** show that intent *derived from source* helps, because no source was read. The work was done mostly by the profile's `known-correct behaviour` entries, not by its `declared constraints` — and it is the constraints that would produce "the code defines X, testing observed Y". The differentiator in §1 is still untested and stays untested until the extractor exists. Do not let any of these three results be reported as evidence for another.

What the offline work did establish about grounding generally: **what the judge is shown matters at least as much as which model reads it.** Two of the three judge defects were rendering problems, not model problems — BUG-05 was unjudgeable until the window showed the *declared* `min`/`max` alongside the accepted value, and BUG-08 was unjudgeable until a hide also listed what remained reachable. That is direct evidence for the grounding hypothesis's mechanism, even though it is not yet evidence for the Application Profile specifically.

**Timeline ambition vs. scope — the dominant risk.** The final vision (§2) adds a whole new subsystem (Repo Intake) on top of an already-large original spec, and every component of it is on one critical path with no parallelism available. The scoping decisions in §6.1–6.2 already cut in the right places (Agent B only, Dockerfile-only auto-deploy), but that was scoping for a split effort; for a single builder the cuts need to go further. Concretely, the honest minimum for a defensible result is **§3.1 + §3.2 + §3.3 + §3.4 (Agent B end-to-end with a working reward model), evaluated against the random baseline on the toy site and one real target.** Repo Intake (§3.0) and the Bug Report Engine (§3.5) are what make the *vision* compelling but are not what makes the *result* valid — treat them as stretch scope and cut them first, rather than half-building everything in §3.0–3.5.

---

## 9. Open items / next steps

Completed 2026-08-02: the toy validation site (was #1), viewport-aware prioritization (was #3), the Repo Profiler context decision (was #4), HTML/network normalization (was #9), and the exploration incentive (was #10).

This is now a single ordered critical path rather than parallel workstreams, so the ordering matters more than it did: items 1–4 establish that the core claim holds, items 5–9 make it hold on a real application, and everything from 10 down is expansion that should be cut before the earlier items are compromised (§8, "Timeline ambition vs. scope").

**RL critical path — replaced 2026-09-16.** The RL next-step recorded in the 2026-08-31 entry and in README's *"Where RL research resumes"* — *measure the run-to-run completion distribution at a fixed seed* — **is done** (1/2/5 completions of 5 across three replicates of seed 0; `reports/fixed_seed_completion_distribution.json`), and README is now stale on that point. The RL path is no longer "paused pending a measurement". In order:

* **R1. The five-condition A0/A1/A2/B/C comparison** — the advisor-approved next milestone (§5, 2026-09-16). Resolves whether the A-vs-B gap was about *learning* or about *access to action-label information*. **Not started; A1/A2 not implemented.** Definitions must be frozen and committed before the fixture is inspected or their final results are read.
* **R2. A delayed-credit web-testing formulation.** Deferred behind R1 by decision. The current reward is dense and immediate (novelty, reveal, repetition, step cost, failed action) and the archive supplies frontier-state returns, so the present benchmark does not strongly test what bootstrapping is for. Question 2 of §5's open list cannot be answered on it.
* **R3. Cross-application transfer of learned action value** — the property the action-conditioned representation exists for, and the one a single-fixture benchmark structurally cannot see. Depends on item 5/6 (a second real target) more than on any RL change.
* **Not approved, and not to be attempted opportunistically:** any change to the reward, observation, action encoding, archive behaviour or AC-DQN architecture. Five consecutive nulls were obtained under a frozen configuration, and that freeze is what makes them comparable.

**Ordering note added 2026-08-08.** The toy site is now saturated — both candidate judges score 10/10, so it can neither discriminate between models nor detect further regressions in judgment quality. Everything that remains to be learned requires a real target. But **Gitea should be approached with the masked-random explorer and the judge, not with the DQN.** Those are two different questions — "does the pipeline work on a real application?" and "does RL beat random?" — and running them together on first contact means any failure is ambiguous between an environment problem, a judge problem, and an RL problem. The DQN is also the weakest component by evidence (it lost on the toy site), so pointing it at the hardest target first maximizes the chance of an uninterpretable result. Validate env + judge on Gitea first; keep the RL questions (1b, 1c) on fixtures where they can be answered cleanly.

1. ~~Train the first DQN on the toy site and compare it against the random baseline~~ — **done 2026-08-02 (§5).** Result: DQN loses to masked random (1/3 vs 3/3 seeded bugs).
   - ~~**1a. Build the LLM judge, offline first**~~ — **done 2026-08-07, improved 2026-08-08 (§5).** 10/10 vs the 3/10 deterministic ceiling, 0 false positives, 100% evidence grounding. The core claim holds. Superseded by items 1d–1e.
   - ~~**1b. Give the DQN action masking**~~ — **done 2026-08-10 (§5).** `agents/masked_dqn.py`; 100% valid actions, 0/200 invalid in a controlled harness. Original note kept below because the implementation constraint it names is what the fix had to satisfy.
     Original: **Give the DQN action masking** — the single highest-value RL change, and now the fair-fight version of the run-5 comparison. This is the action-conditioned Q-network (§3.3), which provides masking structurally. **Still open as of 2026-08-10.** Note the implementation constraint: SB3's DQN has no masking hook, and the policy only sees the observation, so the per-step valid-action mask has to be *appended to the observation* (alongside `EPISODE_CONTEXT_DIM`) for a custom policy to read it — masking the Q-values is not enough on its own, because ε-greedy also samples uniformly over the full 100 slots.
   - ~~**1c. Build a deep-flow fixture**~~ — **done 2026-08-10 (§5).** `tests/fixtures/deep_flow_site`, four gates, defect only at the end, deterministic ceiling 0/1. No policy yet passes stage 1; see §5 for the two diagnosed causes.
     Original: **Build a deep-flow fixture** — a 4–6 step sequential task with a bug only reachable at the end. The toy site cannot test RL's actual claim (credit assignment over multi-step flows); until this exists, "RL doesn't help" is only established for shallow sites. **Still open as of 2026-08-10, and the two prerequisites it was waiting on now exist**: session bootstrap (item 10) means an episode can *start* deep inside a flow, and label/`nth` matching means the macro can be written against a real application. The fixture is now the last thing standing between this project and a fair test of its exploration claim.
   - ~~**1d. Score the judge on the random corpus**~~ — **done 2026-08-08 (§5).** Found three defects in this project's own code, including a renderer that asserted byte-identity it had not checked. Re-scored after the fixes. Keep doing this after any change to `judge/window.py`: the scripted corpus cannot see rendering bugs, because its ground truth was written against the same rendering.
   - ~~**1e. Wire the judge into the live reward loop**~~ — **done 2026-08-09 (§5).** `reward/llm_judge.py`; `start_episode()` on the ABC, `StepContext` passed to `score()`, live windows rendered by the same code as the offline corpus. Runnable end to end via `scripts/run_judged.py`. **Evaluation path only** — see the latency note in §3.4.
2. ~~**Design the judge-call gating filter**~~ — **done 2026-08-09 (§5).** `reward/gating.py`, replayed and loss-checked by `scripts/measure_gate.py`: skips 38–45% of windows while losing 0 of 14 positives on the toy scripted corpus and 0 of 24 on the toy random corpus. The rule is "an action that should have changed state was taken", and *the page changed* is checked **before** any failure rule — the first version skipped `success: False` steps and would have discarded BUG-01, whose `RAPID_CLICK` reports failure because clicks 2-5 time out after click 1 already navigated.
2b. ~~**Rerun `train_toy.py`, over three seeds**~~ — **done 2026-08-26 (§5).** Result: unmasked DQN **0/3 seeded bugs [0-1]** against masked random's **3/3 [2-3]**; state coverage 6 [6-7] against 15 [14-16]. Run 5's 1/3 was seed 0, the only seed on which *any* arm found a bug — including unmasked random — so it was never a DQN result. Not a fair masked-vs-unmasked test (this script trains an unmasked head); the deep-flow 3-seed run is still the evidence for that. Original note kept below.

    Original: **Rerun `train_toy.py`, over three seeds** — and the reasons have grown since this item was written. (i) Every `unique_states` figure in §5's run-5 table is void (measured 8, actually 22 once the env could perceive form state). (ii) **The run-5 comparison has the same n=1 defect the deep-flow run had** (§5, 2026-08-15): its DQN rows were evaluated with `deterministic=True` against a static fixture, so they are one trajectory reported as `--eval-episodes` samples, while the random rows they lose to are genuinely stochastic. The headline "DQN found 1/3 seeded bugs against masked random's 3/3" is therefore a single trajectory from a single training seed against a real 3-episode mean. The harness is fixed (`--eval-epsilon`, default 0.05, and `TrainedPolicy` now passes the action mask it had been dropping); the numbers are not. ~45 min per seed. **Until this is rerun, cite run 5 as suggestive, not as a result** — and note that fixing it could move the conclusion in either direction.
2c. ~~**Fix the network-trace truncation before any real target.**~~ — **done 2026-08-08.** Was: `WebTestingEnv._page_info` caps `info["page"]["network"]` by slicing the *serialized JSON string* at 100 KB, which can cut mid-object. The trace recorder degrades safely (`test_a_truncated_network_trace_degrades_instead_of_failing`) but degrades by dropping **the entire network trace for that step**. The toy site's payloads are far too small to ever trigger it; Gitea's will, and it will do so on precisely the busiest steps — silently removing all HTTP evidence exactly where a failed request is most likely. Truncate the event *list*, not the string. ~20 lines, and it is the one known defect guaranteed to fire on first contact with a real app.
3. ~~**Semantic perception pipeline**~~ — **built 2026-08-10, remeasured over 3 seeds 2026-08-17 (§5).** `perception/encoders/models.py`; ~5% throughput overhead, 951 MB VRAM. Settled: at fixture scale the encoders **cost** the agent return (non-overlapping seed ranges on both arms) and cost the unmasked agent 30 points of valid-action rate; the flow-depth difference the original claim was made on is not separable at n=3. Keep them — the argument for them was always about large, diverse targets where memorisation is impossible, and that case is still untested rather than refuted. Do not expect them to move any fixture number.
4. **Wire `InverseDynamicsHead` in as an auxiliary loss** (§3.2). The `features_extractor` half of this item was already done and the item was never narrowed: `FusionFeaturesExtractor` is the `features_extractor_class` in both `scripts/train_toy.py` and `scripts/compare_agents.py`, so the fusion MLP has been receiving TD gradients in every run reported here. What remains is the auxiliary objective — `InverseDynamicsHead` is built and unit-tested but is not trained by anything, so the 1664→256 projection is still shaped by TD error alone, which §3.2 notes is a weak signal for a projection that size.
5. ~~**Wire `WebFunctionalEnv` up against real Gitea**~~ — **done 2026-08-08, re-captured and re-scored 2026-08-09 (§5).** Env, capture and judge all run against a live Gitea; 13/13 probed routes reachable anonymously. Ten defects found across the two passes, none of them in the judge. The ungrounded positive rate fell 16.4% → 5.6% as the harness defects were closed. The current corpus is `gitea5-random`. The three intermediate captures from the debug rounds were deleted once their fixes landed — the numbers survive in `reports/judge_gitea4_{grounded,ungrounded}.json`, which embed their own verdicts, and the captures themselves are regenerable in three minutes. `gitea-random` is kept as the pre-fix corpus behind the 16.4% figure in the 2026-08-08 entry.
   - **5a. Re-read the surviving positives on any new target, by hand.** Seven of this project's ten Gitea defects were found this way and none by a metric (§5, 2026-08-08 and 08-09). Budget it after every first contact.
6. ~~**Build the auto-deploy runner** (§3.0a)~~ — **done, commit `4539c0f`; corrected 2026-08-30.** `intake/runner.py`: detection → policy gate → deadlined build → `SANDBOX_RUN_ARGS` → health check → exhaustive teardown, with 32 fake-client unit tests and a Docker-gated integration smoke test. What remains is **compose execution**, which is deliberately refused rather than approximated. Original note kept below.
    Original: detection + build/run + applying `SANDBOX_RUN_ARGS` + health check. The policy gate is built; the sandbox enforcement is not, and must be tested adversarially, not just written.
7. **Build the Repo Profiler module** (§3.0b) — tree-sitter repo map + static route/model/validation extraction, then the long-context model over that slice. **The schema and the consumer already exist** (`intake/profile.py`, built 2026-08-09), so this item is now only the extractor, and it is written against a profile shape that has already been measured. **Reprioritise on the 2026-08-26 A/B, which says the profile contributes nothing measurable** (§5): recall, false positives, discrimination and grounding were all unchanged by a hand-authored, correct profile. An extractor would have to beat a baseline of zero, so this item's value is now an open question rather than an assumed win — and the honest next step is a source-derived A/B, not an extractor built on faith.
8. **Evaluate prompted vs. fine-tuned Phi-3 for the reward model** (§3.4) before committing the 1,000-example manual annotation effort.
9. `SubprocVecEnv` parallelism + a throughput measurement on a real target, to fix a realistic step budget (§8).
10. ~~Session/auth bootstrap support for `WebFunctionalEnv`~~ — **done 2026-08-10 (§5).** `setup_actions=[...]`, replayed per episode, excluded from the step budget and the reward. `data/profiles/gitea_login.json` is the working macro.
11. Resolve the SSIM/"expected state" baseline design question before implementing that trigger for real (§8).
12. ~~Bug Report Engine~~ — **done, commit `c5cce3d`; corrected 2026-08-30.** `reporting/report.py` + 15 unit tests; JSON and Markdown, deterministic findings and judge verdicts kept separate, repro steps from the trace, ungrounded verdicts quarantined. **Still open:** the inconsistency-diffing capability (§3.5), which needs the Application Profile in the report path.
13. Expand the toy site's 10 seeded bugs toward the 150-bug benchmark (§8), reusing `answer_key.json`'s structure.
14. `open-clip-torch`/`openai-clip`, `bitsandbytes`, `trl`, `asyncio-throttle` are in `requirements.txt` but not yet installed — needed for Phase 2 (perception) and Phase 3 (LoRA fine-tuning). Reconsider whether a separate CLIP package is needed at all given `transformers` already ships `CLIPModel`/`CLIPProcessor`.
15. `WebSecurityEnv` (Agent A) remains unbuilt — secondary priority, and the first thing to drop under time pressure (§6.1, §8).
16. Write the related-work section against the RL-GUI-testing and LLM-GUI-testing literature (§1), not just against ZAP/Burp/Cypress.

---

## 10. Dev environment notes

- Repo: `C:\Users\reach\OneDrive\Code files\SEM7\RL web tester`, git initialized 2026-07-31. **Resolved 2026-08-10: the repo is committed** — 7 commits on `main`, from `13d9829` (initial) through `7f01cea`. The `.gitignore` policy below is what those commits were made under.
- **This file IS in git as of 2026-08-31** (commit *Track project context for reproducible handoff*). It was deliberately untracked until then, and the original reasoning is kept below because it is what the decision was weighed against, not because it still holds. The trade-off is worth stating explicitly rather than rediscovering: **the single most information-dense artifact in the project has no version history**, no diff, and no reflog, and it lives in the OneDrive-synced folder the next bullet describes as a known cause of file corruption. Every negative result, every rejected design, and every "measured, don't re-derive this" number is here and nowhere else — losing it costs far more than losing any source file, all of which are recoverable from the object store. If the local-only decision stands, it needs its own backup that is not OneDrive; if it does not, a `docs/` copy or a private branch would give it history. Reviewed 2026-08-15 and left as-is, flagged rather than changed, because it is the user's call. **Resolved 2026-08-31: tracked.** The finalization pass raised it again — a repository being handed to someone else that hides every negative result, retraction and resume point is not a handover — and the call was made to track it. The backup concern the bullet raises is now answered by git itself.
- **The repo lives inside a OneDrive-synced folder.** OneDrive syncing and locking files under `.git/` and `venv/` is a known source of intermittent index corruption and sync conflicts that surface as random git failures. Either move the repo outside OneDrive or mark `.git` and `venv` "Always keep on this device"/excluded from sync.

### What is committed, and why (`.gitignore` rewritten 2026-08-09)

The rules were inverted: `data/annotations/*.json` dropped the tiny corpus **index** files — the entry points a corpus is unusable without — while 13 MB of page blobs and screenshots were committed. Worse, `models/checkpoints/` ignored `*.pt`/`*.pth` but Stable-Baselines3 writes **`.zip`**, so the two toy DQN checkpoints were 31 MB of otherwise-unnoticed repo. Working tree went from ~46 MB to **3.1 MB across 201 files**.

The policy, stated so it can be argued with rather than guessed at: **commit what a claim rests on and what a clone cannot regenerate; ignore what is bulky and reproducible.**

| Committed | Why |
|---|---|
| `reports/*.json` | The measured results. Each embeds its own verdicts and usage, so it stays readable after the corpus that produced it is gone. |
| `data/profiles/*.json` | Application Profiles are *inputs*, not outputs — the judge cannot be grounded without them. Explicitly re-included so no future `data/` rule can swallow them. |
| `data/annotations/toy-{scripted,random}/` minus screenshots | ~800 KB, and the evidence for the headline 10/10-vs-3/10 result. Their fixture site is in this repo, so a clone can replay that claim **offline** — which is the only reason to carry a corpus in git at all. |
| Corpus `*-index.json`, `meta.json` | Tiny, and a corpus is unusable without them. This is the bug that was fixed. |

| Ignored | Why |
|---|---|
| `data/annotations/gitea*/` (~10 MB) | Needs a running container (`docker compose up gitea` + `scripts/setup_gitea.py`); ~3 minutes to recapture. |
| `*/screenshots/` (2.3 MB) | Captured for possible visual work, but the judge is text-only and never reads them. |
| `models/checkpoints/*.zip` (31 MB) | SB3 checkpoints, and §9 item 2b says those runs must be re-run before any figure from them is cited. |
| `reports/_*.{log,json}` | Scratch from piping a run to a file; the real output is the JSON beside it. |
| `.claude/settings.local.json` | Per-machine permissions. Project-level `.claude` config stays tracked. |

Two consequences worth knowing before relying on a clone: the Gitea corpora and the screenshot-dependent parts of the toy corpora are **not** in git, so anything citing them must be regenerated; and because `pages/` is committed for the toy corpora but not the Gitea ones, `StepRecord.resolve_html` returns `""` for a Gitea trace restored from a clone rather than failing loudly — check `data/annotations/` is populated before scoring a corpus you did not capture yourself.
- venv at `<repo>/venv` (not `.venv`), Python 3.11.8, torch 2.6.0+cu124 (CUDA confirmed working).
- Docker Desktop CLI installed, daemon not running as of this writing — start it before any `docker compose up` for training targets, and before any auto-deploy work.
- vLLM does not compile natively on Windows — use a `transformers` pipeline fallback locally, or Docker/WSL for real serving.
- `bitsandbytes` has known Windows compatibility issues (historically needs WSL or a precompiled fork) — revisit at Phase 3 rather than assuming a plain `pip install` works.
- Auto-deploy (§3.0a) will need real container sandboxing (resource/network/timeout limits) before it's safe to run against untrusted uploaded repos — treat as a security requirement, not a later hardening pass.
