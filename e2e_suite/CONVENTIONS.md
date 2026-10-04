# e2e_suite conventions

Every application in this suite is a small, coherent web application with a real backend,
built so that the project's tester (browser exploration, replay, minimization, verification)
can be validated against known, mechanically verified seeded faults. **No RL.** Nothing here
is part of the frozen primary benchmark (`tests/fixtures/benchmark/`), which discovers only
directories containing a `faults.json`; this suite never contains a file of that name.

## 1. Layout

```
e2e_suite/apps/<app_id>/
  app.json          manifest (section 4)
  README.md         what it is, how to run it, its workflows
  src/              the clean application (the only code that is patched)
  vocab/default.json, vocab/alt.json   every user-visible label, by key
  seed/             deterministic seed data (JSON)
  tests/            API tests against the clean build (stdlib: unittest or node:test)
  Dockerfile        (every app except wayfarer)
```

## 2. Runtime contract

- **Zero dependencies.** Python apps use only the standard library (`http.server`, `sqlite3`,
  `json`, `html`); Node apps only Node 22 built-ins (`node:http`, `node:fs`, ...). The
  product's sandbox builds with no network, so nothing may be installed at build time.
- **Start**: `python src/server.py` or `node src/server.js` from the app directory.
  Environment:
  - `PORT` (required) — listen on 127.0.0.1:`PORT` (and `0.0.0.0` when `HOST=0.0.0.0`).
  - `WTA_VOCAB` — vocabulary profile, `default` (default) or `alt`.
  - `WTA_FIXED_NOW` — ISO date-time the app treats as "now"; default `2026-06-10T09:00:00`.
  - `WTA_DATA_DIR` — where persistent files go; default a temp directory.
  - `WTA_TEST_HOOKS=1` — enables `GET /__wta/state` (section 3). Off by default.
- **`GET /healthz`** → `200 {"ok": true, "app": "<app_id>"}`.
- **No external network**: no CDN, web font, analytics or remote URL anywhere in `src/`.
  All assets are served by the app itself.
- **Deterministic**: no `Math.random`, `random`, `Date.now()`/`datetime.now()` except through
  the app's clock (which reads `WTA_FIXED_NOW`); stable orderings everywhere; ids derived
  from counters, never from time or randomness.

## 3. State and reset

- **All mutable state is scoped to a session cookie** `wta_sid` (HttpOnly, SameSite=Lax),
  created on the first request and initialised from `seed/`. The tester opens a fresh
  browser context per episode and per replay, so every episode, replay and control run
  starts from the seed state with no reset hook.
- State is stored in SQLite (Python apps) or a JSON file per session (Node apps) under
  `WTA_DATA_DIR`, so it survives page reloads and server-side navigation.
- **`GET /__wta/state`** (only with `WTA_TEST_HOOKS=1`) returns
  `{"session": <sid>, "committed": {...}, "sha": "<sha256 of canonical JSON of committed>"}`
  for the caller's session. "Committed" means server-side business state (orders, balances,
  tickets, ...), not UI state such as the open tab. The harness uses it to prove that
  decoys change nothing and that state-dependent actions change something only in the
  state where they are useful.

## 4. Manifest `app.json`

```json
{
  "app_id": "tidewater", "name": "...", "domain": "...",
  "architecture": "spa-hash-router+json-api", "stack": "python-stdlib+sqlite",
  "persistence": "sqlite per session", "entry": "/", "start": ["python", "src/server.py"],
  "dockerfile": true,
  "workflow_graph": {"kind": ["hub-and-spoke", "linear-wizard"], "nodes": [...], "edges": [...]},
  "features": ["modal", "tabs", "conditional-section", ...],
  "regions": {"name": "#css-id"},
  "workflows": [{"name": "...", "trace": [...], "expect": [{"region": "...", "contains": "..."}]}],
  "state_dependent": [{"name": "...", "useless": {"trace": [...], "action": {...}},
                       "useful": {"trace": [...], "action": {...}}}],
  "decoys": [{"name": "...", "trace": [...], "decoy": {...}}],
  "faults": [ { ...the primary oracle's FaultSpec fields..., "subtype": "...",
                "oracle": "existing" | "extension",
                "patch": {"file": "src/...", "find": "...", "replace": "..."} } ],
  "benign_traces": [[...]]
}
```

Trace steps use the replay schema the tester already uses:
`{"type": "CLICK"|"TYPE"|"SELECT"|"REFRESH"|"BROWSER_BACK", "id": "<DOM id>",
"params": {"category": "valid_typical"} | {"option": "<value>"}}`.

## 5. Markup rules (so the tester can act and the oracle can read)

- **Every control a trace uses has a stable DOM `id`**; every input has a `name` (the oracle
  records typed values by field name) and a visible `<label for>`.
- **Labels come from the vocab file**, never hard-coded, so the `alt` profile renders the
  same app with different words. Fault patches never touch labels.
- **Regions** (what assertions read) are elements addressed by `#id` whose content contains
  **no nested element with the same tag name** (the oracle reads a region by regex up to
  its first closing tag). Prefer `<output>`, `<strong>`, `<span>`, `<td>` for regions.
- A region carrying a number that an assertion checks should contain only that number and
  its label/currency, so no other number can match by accident.
- **Typed values are the tester's own**: text `Test User Input`, number `42`, date
  `2026-06-15`, email `tester@example.com` (`envs/input_values.py`). Triggers must be
  reachable with these and with clicks/selects/toggles — never a secret code.
- **Selects**: the tester offers the *last* option and the first three. An option a trace
  needs must be in one of those positions.
- **At most ~40 controls per view** (each text field yields five actions; the tester's
  action list is capped at 100).

## 6. Faults

- One fault = one literal substitution (`patch.find` occurs **exactly once** in
  `patch.file`). Clean is `src/`; buggy is clean with every patch; the per-fault control is
  buggy without that fault's patch.
- Every fault has an executable `trigger_trace`, assertions in the existing grammar where
  possible (`oracle: "existing"`), and the suite's isolated extension grammar otherwise
  (`oracle: "extension"`, see `harness/ext_oracle.py`).
- Faults must be realistic functional defects (stale state, wrong transition, missing
  validation, displayed-vs-submitted, ...), triggered by several contextual steps where
  that is natural — never a hard-coded wrong number behind one click.
- Assertions state **expected behaviour**, so they are silent on the clean build for every
  path, not only the witness.

## 7. Decoys and state-dependent actions

- A **decoy** is an ordinary, plausibly labelled control that does not advance the workflow
  and changes no committed state. Nothing in its markup, id or label says it is a decoy.
- A **state-dependent action** is the same control (same id, same label) that changes
  committed state in one state and not in another, for a real domain reason.

## 8. Lessons from the pilots (tidewater, bagstreet) — read before writing a fault

1. **An assertion must be silent on the clean build for every path, not just the witness.**
   The harness runs a clean-build random walk and benign traces against every assertion.
   Think about: revisiting an older page (an order confirmation reached again from history
   with different inputs since), a value typed but never saved, the tester's boundary
   values (`a`, 2048×`A`, `""`, `12345`, `0`, `999999999`). Examples: store typed text
   exactly as typed if an `echoes_input` reads it back (no silent truncation); scope an
   assertion to the moment it applies by conjoining a one-time marker (bagstreet's
   "Order placed: BS-1001" banner shown only on the first render after placing).
2. **`where` is a regex on the full URL** (path, query and `#hash`). The *last* matching
   step is judged, with the inputs accumulated *up to that step*.
3. **Workflow expectations check data, not wording**: every workflow also runs under the
   `alt` vocabulary, so expect numbers, names, dates — never a label. `{"region": ..,
   "absent": ..}` is supported for "must not contain".
4. **Benign traces must not trigger any fault on the buggy build.** A trace that exercises
   a fault's trigger is a witness, not a benign walk.
5. **The tester waits only for the DOM to be quiet** (harness: 400 ms), not for in-flight
   requests. Keep API handlers fast and re-render as soon as a response arrives; never
   chain several sequential requests behind one click.
6. **Generated ids** (`edit-${id}`) are fine; their fixed prefix (`edit-`) must appear
   literally in the source.
7. **Patches must leave valid code**: prefer changing a condition or a value over deleting
   a statement; an anchor must be unique (include a distinctive comment if needed).
8. **Faults in one app must not mask each other**: the per-fault control is the buggy
   build minus that fault's patch, and the fault's witness must be silent there. Check
   that no other fault's patch alone produces the same symptom on that witness.
9. **Python 3.12 stdlib / Node 22 built-ins only**; Node apps: `node --test tests/` with
   `E2E_BASE_URL`; Python apps: `unittest` with `E2E_BASE_URL`.
10. **Extension kinds** (`harness/ext_oracle.py`): `region_stable_across_reload`,
    `input_absent`, `region_arithmetic` (with `operands` and optional `guard`). Use them
    only when the primary kinds cannot state the expected behaviour path-independently.
