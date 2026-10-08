# e2e_suite conventions (v2)

Every application in this suite is a small, coherent web application with a real backend,
built so that the project's tester (browser exploration, replay, minimization, verification)
can be validated against known, mechanically verified seeded faults. **No RL.** Nothing here
is part of the frozen primary benchmark (`tests/fixtures/benchmark/`), which discovers only
directories containing a `faults.json`; this suite never contains a file of that name.

These rules apply to both sets: the development apps here (`e2e_suite/apps/`) and the
held-out validation apps (`validation_suite/apps/`, admitted with `WTA_SUITE_ROOT`).
The design they implement is `e2e_suite/SUITE_V2_PLAN.md`.

## What changed from v1

Suite v2 puts every app on a **database server** (PostgreSQL or MongoDB) behind a
**sign-in**, with a **richer data model**. Faults, workflows, decoys, state-dependent
actions, benign traces and seed facts are unchanged; every trace now starts right after the
sign-in macro, on the same entry page as before. In this document:

- **[v2]** marks a rule that is new; **[v2, replaces v1]** marks one that changes a v1 rule.
- Unmarked rules are v1 rules that still hold.

| v1 | v2 |
|---|---|
| Zero dependencies: stdlib or Node built-ins only | Stdlib or Node built-ins **plus the suite's vendored, pinned drivers** (§2, §11) |
| SQLite or a JSON file per session, under `WTA_DATA_DIR` | One PostgreSQL schema or MongoDB database per **signed-in browser context** (§3) |
| A session is created by the first request | A partition is created by the **first successful sign-in**; anonymous requests create nothing (§3) |
| No sign-in | `/login`, a 3-step sign-in macro, roles, a two-step sign-out, an access notice (§4) |
| Manifest without `database` / `login` | Both blocks are required; `stack` and `persistence` describe the server (§5) |
| 22 admission checks | About 37, including the data-model contract and the sign-in and session checks (§9, §12) |

## 1. Layout

```
e2e_suite/apps/<app_id>/
  app.json          manifest (section 5)
  README.md         what it is, how to run it, its workflows, and [v2] the demo accounts
  src/              the clean application (the only code that is patched)
  src/schema.sql    [v2] PostgreSQL apps: the tables of one session partition
  src/<validators>  [v2] MongoDB apps: the module that defines every collection's
                    $jsonSchema validator and indexes (named by `database.schema`)
  vocab/default.json, vocab/alt.json   every user-visible label, by key
  seed/             deterministic seed data (JSON), [v2] including seed/users.json
  tests/            API tests against the clean build (stdlib: unittest or node:test)
  Dockerfile        (every app except wayfarer and quizcraft)
```

- **[v2]** `vendor/` is **not** in the app directory: the harness copies the suite's
  `e2e_suite/vendor/<language>` into every build as `vendor/<language>` (`variants.py`). Run
  an app from its build (`e2e_suite/.build/<app>/clean`), or put the vendor tree on the
  import path yourself.

## 2. Runtime contract

- **[v2, replaces v1] Dependencies.** Python apps use the standard library plus the suite's
  vendored drivers (pg8000, pymongo); Node apps use Node 22 built-ins plus the vendored `pg`
  or `mongodb`. Nothing is installed at build time: the product's sandbox builds with no
  network. Never vendor a driver into an app; `scripts/vendor_drivers.py` owns
  `e2e_suite/vendor/` (and `vendor/manifest.json` records versions and hashes).
- **Start**: `python src/server.py` or `node src/server.js` from the build directory.
  Environment:
  - `PORT` (required) — listen on 127.0.0.1:`PORT` (and `0.0.0.0` when `HOST=0.0.0.0`).
  - **[v2]** `DATABASE_URL` (PostgreSQL apps) or `MONGODB_URL` (MongoDB apps), **required**.
    The app exits at once with a clear message when it is missing. It never has a default
    and never appears in `src/` or the Dockerfile (`database_url_from_environment`). The
    database it names belongs to this server alone (its *home*).
  - **[v2]** `WTA_SESSION_CAP` — how many session partitions to keep (default 32, §3).
  - `WTA_VOCAB` — vocabulary profile, `default` (default) or `alt`.
  - `WTA_FIXED_NOW` — ISO date-time the app treats as "now"; default `2026-06-10T09:00:00`.
  - `WTA_TEST_HOOKS=1` — enables `GET /__wta/state` (section 3). Off by default.
  - **[v2]** `WTA_DATA_DIR` is no longer used by database apps.
- **`GET /healthz`** → `200 {"ok": true, "app": "<app_id>"}`. **[v2]** `ok` is true only
  while the database answers a ping; otherwise `503 {"ok": false, "app": ...}`. At start the
  app waits (up to about 30 s) for the database before it listens.
- **[v2] Concurrency.** Requests are **serialised per session**, which keeps a rapid double
  click deterministic and reproduces v1's "no interleaving":
  - Python: one connection plus one lock; each request is `BEGIN; SET LOCAL search_path TO
    <partition>; ...; COMMIT` (PostgreSQL) with nothing else in between.
  - Node: a pool plus a per-session promise queue (one request per `wta_sid` at a time).
  - Request p95 ≤ 100 ms (admission enforces p95 ≤ 150 ms and max ≤ 1000 ms on the clean
    random walk, sign-in excepted: `responses_fast`).
- **No external network**: no CDN, web font, analytics or remote URL anywhere in `src/`.
  All assets are served by the app itself.
- **Deterministic**: no `Math.random`, `random`, `Date.now()`/`datetime.now()` except through
  the app's clock (which reads `WTA_FIXED_NOW`); stable orderings everywhere; ids derived
  from counters, never from time or randomness. **[v2]** That includes the database: no
  `now()`, `current_timestamp`, `random()`, `gen_random_uuid()`, `$$NOW`, `$currentDate`,
  `$rand`, `$sample`, `ObjectId()`, `secrets`, `os.urandom`, `crypto.randomBytes`, and no
  `CREATE TRIGGER` / `CREATE RULE` (`deterministic_source` scans `.sql` too).

## 3. State and reset

- **[v2, replaces v1] Partitions.** All mutable state belongs to a **partition** owned by
  one browser context:
  - PostgreSQL: a schema `s_` + six digits (`s_000017`) in the home database.
  - MongoDB: a database `<home>_s` + six digits (`e2e_orbit_51234_s000017`), where `<home>`
    is the path of `MONGODB_URL` (decision D1). The tenant-field fallback
    (`database.isolation: "tenant-field"`) is reserved for the case the pilot rules on.
  - The names are fixed: the harness counts and drops partitions by them.
- **[v2] The registry.** The `wta_sid` cookie (HttpOnly, SameSite=Lax, counter-derived, e.g.
  `sr000017`) maps to a registry row `{sid, partition, user_id, touched}` in the home (the
  `app_meta` schema, or collections of the home database). `touched` is a counter bumped on
  every request of the session (LRU order), never a time.
- **[v2] Lifecycle.**
  - The app resets its home at start: registry and seed template rebuilt, leftover
    partitions dropped. A restarted server starts from the seed, as v1's temp directories did.
  - A partition is created at the **first successful sign-in** of a browser context, from
    the seed (PostgreSQL: copied from a `tmpl` schema built from `src/schema.sql` + `seed/`
    in one simple-protocol round trip; MongoDB: create each collection with its validator,
    then its indexes, then `insertMany` from the seed held in memory).
  - **Anonymous requests create nothing**: no cookie, no partition, no registry row.
  - **Sign-out clears `user_id` and keeps the partition**: signing in again in the same
    browser context resumes the same data. A new browser context is a new partition.
  - When a new partition takes the count above `WTA_SESSION_CAP`, the least recently used
    one is dropped with its registry row; its cookie then simply reads as signed out (the
    page redirects to the sign-in page, never an error). Episodes and replays never reuse a
    closed context, so this is safe.
- **`GET /__wta/state`** (only with `WTA_TEST_HOOKS=1`) returns
  `{"session": <sid>, "user": <username or null>, "committed": {...}, "sha": "<sha256 of
  canonical JSON of committed>"}` for the caller's partition. **[v2]**:
  - Without a partition (no cookie, or an unknown or evicted one) it answers
    `{"session": null, "user": null, "committed": null, "sha": null}`.
  - `committed` covers only the caller's partition and only business data: never passwords
    or hashes, flash notices, counters, the registry, partition names or ObjectIds.
  - `user` is outside `committed`: signing out and in again leaves the sha unchanged.
- "Committed" means server-side business state (orders, balances, tickets, ...), not UI
  state such as the open tab. The harness uses it to prove that decoys change nothing and
  that state-dependent actions change something only in the state where they are useful.

## 4. [v2] Sign-in and roles

- **The sign-in page** is at `login.path` (normally `/login`): a form with a username field,
  a password field (`type="password"`), a submit button, and an informational "Forgot
  password?" link to a public page. Every control has a stable id (`login-username`,
  `login-password`, `login-submit` by convention). Labels come from the vocab like any other.
- **The form posts** `application/x-www-form-urlencoded` to `login.form.action` with the
  fields named in `login.form` (and an optional `next`). This is a contract: the harness and
  the Docker check sign in over plain HTTP.
  - Success: **303** to `next` (or the entry) with `Set-Cookie: wta_sid=...`.
  - Refusal: **200**, the form again, with one generic message in the `login.error_region`
    element, whatever the reason (unknown user, wrong or empty password). No cookie.
  - `next` is honoured only for a same-origin path (starts with one `/`, not `//`, no
    backslash) other than the sign-in page itself; anything else goes to the entry.
- **A signed-in GET of the sign-in page redirects** (303) to `next` or the entry, so BACK to
  it after signing in is harmless. Every response carries `Cache-Control: no-store`, so no
  page is served from the back/forward cache.
- **Signed out**: a page answers **303** to `login.path?next=<the path asked for>`; an API
  answers **200** `{"ok": false, "reason": "signed-out"}` (the client then goes to the
  sign-in page).
- **Never 401 or 403.** A role that may not do something gets:
  - a page: **200** with an access notice in a region (`#access-notice` by convention);
  - an API: **200** `{"ok": false, "reason": "forbidden"}`, and nothing changes.
- **Navigation never links a role to a forbidden page.** Multi-role witnesses use act-as or
  delegation within one sign-in (ledgerly, sideline), preview-as (markbook) or acting on
  someone's behalf (dutyday), never a second sign-in (decision D5).
- **The header** shows who is signed in (`#signed-in-as`: the user's name and role) and one
  Account control: a `<button aria-expanded>` that opens a menu holding **Sign out**, which
  is a POST (a form). Zero-JS apps link to `/account`, a page with the sign-out form.
  Sign-out is therefore always two steps (decision D3); it answers 303 to the sign-in page.
- **Credentials.** PBKDF2-HMAC-SHA256 at 20,000 iterations with seeded salts (a documented
  fixture choice, decision D10); one fixture password per app, shared by its demo users and
  listed in the README. Neither the password nor any username may equal a value the tester
  can type (`envs/input_values.py`, every input type and category, compared trimmed and
  case-folded): exploration must never be able to sign in again (decision D4). The
  manifest check rejects them. An unknown username still pays for one PBKDF2 derivation.
- **The macro** is exactly 3 steps: TYPE username, TYPE password (literal values in
  `params.value`), CLICK submit. The tester's session bootstrap runs it (`setup_actions`)
  before the first observation of every episode and replay; the harness does the same.

## 5. Manifest `app.json`

```json
{
  "app_id": "dutyday", "name": "...", "domain": "...",
  "architecture": "server-rendered", "stack": "python-stdlib+pg8000+postgresql",
  "persistence": "postgresql schema per signed-in session", "entry": "/",
  "start": ["python", "src/server.py"], "dockerfile": true,
  "database": {...}, "login": {...},
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

- **[v2] `stack` / `persistence`** name the server and the partitioning
  (`node22+mongodb-driver+mongodb`, `mongodb database per signed-in session`).
- **[v2] `database`** (`manifest.validate_database`):

  ```json
  "database": {
    "engine": "postgres" | "mongodb",
    "isolation": "schema-per-session" | "database-per-session" | "tenant-field",
    "schema": "src/schema.sql",
    "entities": ["users", "items", ...],      // business tables or collections: what the
                                              // data-model contract counts (§9)
    "auxiliary": ["counters", "notices"],     // every other table/collection of a partition
    "references": [{"from": "cards.project_id", "to": "projects._id"}, ...],
    "probes": [...],
    "absent_constraints": [{"fault_id": "STK-3", "constraint": "...", "reason": "..."}]
  }
  ```

  - Every table or collection of a partition is either an entity or auxiliary.
  - `references` (MongoDB, at least 4): `collection.path`, where `name[]` means each
    element of an array (`projects.members[].user_id`), and `to` names the referenced
    field (`users._id`). PostgreSQL declares its references as foreign keys in the schema
    and leaves this empty.
  - `probes` (at least 3, covering each kind): writes the database must refuse.
    - PostgreSQL: `{"name", "kind": "foreign_key" | "check" | "unique", "sql"}`; each runs in
      its own rolled-back transaction in a fresh partition and must fail with SQLSTATE
      23503 / 23514 / 23505.
    - MongoDB: `{"name", "kind": "required" | "enum" | "validation" | "duplicate",
      "collection", "insert": {...}}` (or `"update": {"filter", "update"}`), refused with
      code 121 (document failed validation) or 11000 (duplicate key).
  - `absent_constraints`: every constraint a seeded fault needs to be missing (§7).
- **[v2] `login`** (`manifest.validate_login`):

  ```json
  "login": {"path": "/login", "protected": ["/", "/rota"], "public": ["/help"],
   "users": [{"username": "sam.okoye", "name": "Sam Okoye", "roles": ["coordinator"], "macro": true},
             {"username": "ana.silva", "name": "Ana Silva", "roles": ["volunteer"]}],
   "password_value": "Dutyday-rota-26",
   "steps": [{"type": "TYPE", "id": "login-username", "params": {"value": "sam.okoye"}},
             {"type": "TYPE", "id": "login-password", "params": {"value": "Dutyday-rota-26"}},
             {"type": "CLICK", "id": "login-submit"}],
   "form": {"action": "/login", "username_field": "username", "password_field": "password"},
   "signed_in_region": "signed_in_as", "error_region": "login_error",
   "sign_out": [{"type": "CLICK", "id": "nav-account"}, {"type": "CLICK", "id": "sign-out"}],
   "restricted": [{"user": "ana.silva", "path": "/volunteers/ben", "region": "access_notice"}]}
  ```

  - Exactly one user is `macro: true`; the macro TYPEs that username and the password
    once each and ends with the CLICK; `sign_out` is two CLICKs.
  - `protected` paths (hash routes included) must land on `login.path` when signed out;
    `public` paths answer 200 without a redirect.
  - `restricted`: a listed user, a path that user may not use, and the region of its access
    notice (empty only for an app without roles, e.g. larder).
  - The evaluation runner passes `login.steps` as `setup_actions`, exactly as the harness.

Trace steps use the replay schema the tester already uses:
`{"type": "CLICK"|"TYPE"|"SELECT"|"REFRESH"|"BROWSER_BACK", "id": "<DOM id>",
"params": {"category": "valid_typical"} | {"option": "<value>"}}`. Only the sign-in macro
uses `params.value` (a literal); traces never do.

## 6. Markup rules (so the tester can act and the oracle can read)

- **Every control a trace uses has a stable DOM `id`**; every input has a `name` (the oracle
  records typed values by field name) and a visible `<label for>`.
- **Labels come from the vocab file**, never hard-coded, so the `alt` profile renders the
  same app with different words. Fault patches never touch labels.
- **Regions** (what assertions read) are elements addressed by `#id` whose content contains
  **no nested element with the same tag name** (the oracle reads a region by regex up to
  its first closing tag). Prefer `<output>`, `<strong>`, `<span>`, `<td>` for regions.
- **[v2]** New regions in every app: `signed_in_as`, `login_error`, `access_notice`.
- A region carrying a number that an assertion checks should contain only that number and
  its label/currency, so no other number can match by accident.
- **Typed values are the tester's own**: text `Test User Input`, number `42`, date
  `2026-06-15`, email `tester@example.com` (`envs/input_values.py`). Triggers must be
  reachable with these and with clicks/selects/toggles — never a secret code.
- **Selects**: the tester offers the *last* option and the first three. An option a trace
  needs must be in one of those positions.
- **At most ~40 controls per view** (each text field yields five actions; the tester's
  action list is capped at 100). **[v2]** The header adds at most one control (Account; its
  Sign out appears only when the menu is open), and it counts toward the limit.

## 7. Faults

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
- **[v2]** A fault may live in the database layer (a query filter, an update operator, a
  compare-and-set predicate, an upsert's column list). Its anchor is SQL or query text in
  application code, unique like any other; the replacement text must not already exist in
  the clean build (`audit_diff` checks both).
- **[v2] No database rule may pre-empt a seeded fault.** A CHECK, unique index, trigger or
  validator that would make the faulty write impossible is left out, and the omission is
  declared in `database.absent_constraints` with the reason. Plan §1d lists them.

## 8. Decoys and state-dependent actions

- A **decoy** is an ordinary, plausibly labelled control that does not advance the workflow
  and changes no committed state. Nothing in its markup, id or label says it is a decoy.
- A **state-dependent action** is the same control (same id, same label) that changes
  committed state in one state and not in another, for a real domain reason.

## 9. [v2] The data-model contract

Counted by `db_checks.check_database` in **one freshly seeded partition** (the first
sign-in of a fresh variant), over the manifest's `entities` only; exact thresholds:

- **PostgreSQL**
  - ≥ 8 tables; ≥ 6 foreign keys, ≥ 80% of them backed by an index that leads with
    exactly their columns; ≥ 5 CHECKs, including ≥ 2 enumerations (`col IN (...)` or an
    enum-typed column).
  - ≥ 2 UNIQUE besides primary keys; ≥ 3 non-unique secondary indexes; ≥ 60% of columns
    NOT NULL.
  - **0 user triggers, rules or event triggers, and 0 clock or random column defaults**,
    anywhere in the database (identity columns are reported, allowed).
  - ≥ 40 seed rows across ≥ 5 non-empty tables.
  - The probes: every one refused with its SQLSTATE, the three kinds covered.
- **MongoDB**
  - ≥ 4 collections, every one with a `$jsonSchema` validator, `validationLevel: strict`,
    `validationAction: error` and `required` ≥ 2 fields; ≥ 2 with
    `additionalProperties: false`.
  - ≥ 3 `enum`s and ≥ 1 `oneOf`/`anyOf` across the validators.
  - ≥ 2 collections whose seed holds non-empty arrays of embedded documents; document
    depth ≥ 3 somewhere, counting the document as level 1 and each embedded document one
    deeper, **arrays transparent** (an object in `days[].stops[]` is level 3; an object in
    `checklist[]` is only level 2).
  - ≥ 2 unique indexes besides `_id`; ≥ 3 secondary indexes (every index but `_id`, unique
    or not), including ≥ 1 compound.
  - Every declared reference resolves (0 dangling; a null or missing value is an absent
    optional reference), and ≥ 3 of them hold seeded values.
  - **No ObjectId** anywhere in the seed; ≥ 60 documents plus array elements (any type,
    any depth) across ≥ 4 non-empty collections.
  - The probes: every one refused with its code, the three kinds covered.
- The volume thresholds are deliberately modest: the fault-pinned seed figures limit
  breadth, so richness comes from structure. Keep seed facts; enrich depth, not breadth.

## 10. Lessons — read before writing a fault

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
9. **[v2, replaces v1]** Python 3.12 stdlib / Node 22 built-ins plus the vendored drivers.
   API tests run against `E2E_BASE_URL` and sign in first, through the form, like a browser.
   Node apps: test files are named `tests/*.test.mjs` (or `.test.js`); the harness runs
   `node --test "tests/**/*.test.mjs" "tests/**/*.test.js"` from the app directory, because
   Node 22 does not accept a directory (`node --test tests/` fails). Python apps:
   `python -m unittest discover -s tests`.
10. **Extension kinds** (`harness/ext_oracle.py`): `region_stable_across_reload`,
    `input_absent`, `region_arithmetic` (with `operands` and optional `guard`). Use them
    only when the primary kinds cannot state the expected behaviour path-independently.
11. **[v2] node-pg type parsers.** node-pg returns `int8` and `numeric` as strings and turns
    DATE and TIMESTAMP into JS `Date`s in the process's zone (a timezone trap): set type
    parsers for 20 (int8), 1700 (numeric), 1082 (date) and 1114 (timestamp). pg8000 returns
    `int`, `Decimal`, `date` and `datetime`; convert dates with `.isoformat()` for JSON.
12. **[v2] Store business dates as ISO strings in MongoDB**, never BSON dates from a clock.
13. **[v2] Every ORDER BY or sort ends in a unique key**, and text compares byte-wise
    (`COLLATE "C"` in PostgreSQL; MongoDB string order is already byte-wise). A tie that
    the database may break either way is a nondeterministic page.
14. **[v2] Never use the database's clock**: stamps are passed in from the app's clock.
    The PostgreSQL server runs in UTC anyway.
15. **[v2] Guard an empty `$set`**: legal on MongoDB 5.0+, but skip the write anyway.
16. **[v2] Search text is data, not a pattern**: match with `strpos`/`position` or escape
    `LIKE` wildcards (`%`, `_`); in MongoDB escape a typed `$regex`.
17. **[v2] Counters, not identity columns**, for ids the user sees: a counter row updated in
    the request's transaction never skips a number when a request is refused.
18. **[v2] pg8000 `run()` without parameters uses the simple protocol** and accepts several
    statements in one round trip (the partition copy); with parameters it is one statement.
    node-pg `query(text)` without values behaves the same.
19. **[v2] Keep the seed facts.** Pinned figures (counts, ids, next numbers, a workflow's
    expected values) are listed in each app card of the plan; enrich around them.

## 11. [v2] Drivers, imports and the Dockerfile

- **Python** (pg8000 1.31.5, pymongo 4.18.2 pure Python with dnspython 2.8.0): before
  importing a driver,

  ```python
  sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "python"))
  import pg8000.native          # or: import pymongo
  ```

  pymongo runs without its C extensions (`pymongo.has_c()` is False); that is expected.
- **Node** (`pg` 8.x, `mongodb` 7.x): ESM ignores `NODE_PATH` and package `imports` cannot
  point outside `src/`, so

  ```js
  import { createRequire } from "node:module";
  const require = createRequire(import.meta.url);
  const { MongoClient } = require("../vendor/node/node_modules/mongodb");
  ```

- **Dockerfile**: copy the vendor tree with everything else; the URL comes at run time.

  ```dockerfile
  FROM python:3.12-slim            # or node:22-slim
  WORKDIR /srv/<app>
  COPY vendor ./vendor
  COPY src ./src
  COPY seed ./seed
  COPY vocab ./vocab
  ENV PORT=8080 HOST=0.0.0.0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
  EXPOSE 8080
  USER 65534
  CMD ["python", "src/server.py"]
  ```

  The app puts `vendor/python` on its own import path, so no `PYTHONPATH` is needed.
  `dockerfile_copies_vendor` checks the `COPY vendor` line.

## 12. [v2] The database servers and the Docker check

- **Servers** (`harness/postgres.py`, `harness/mongo.py`; `python -m
  e2e_suite.harness.dbctl up|status|reset|down`; run `up` before parallel admissions):
  - PostgreSQL: `postgres:16-alpine` (pinned digest), container `wta-postgres`,
    `127.0.0.1:55432`, user `wta` / `wta-local-test`, tmpfs 2 GB, `--memory 2g`, UTC,
    durability off.
  - MongoDB: `mongo:7.0` (pinned digest), **standalone** (decision D2), container
    `wta-mongo`, `127.0.0.1:57017`, root `wta` / `wta-local-test`, tmpfs 3 GB,
    `--wiredTigerCacheSizeGB 0.75`, no diagnostic data. URLs carry
    `authSource=admin&directConnection=true&retryWrites=false`.
  - Each running variant gets its own home, `e2e_<app>_<port>`, dropped before it starts
    and after it stops; `variant_storage_released` proves nothing is left.
- **The Docker check** builds with `--network none` and runs the image with
  `WTA_TEST_HOOKS=1` against a home of its own, `e2e_<app>_docker`:
  1. Route `host`: `--add-host host.docker.internal:host-gateway` and the published port
     (`...@host.docker.internal:55432/...` or `...:57017/...`).
  2. Fallback route `network` when the container cannot become healthy that way:
     `--network wta-net` and the servers by name (`wta-postgres:5432`, `wta-mongo:27017`).
  3. `/healthz` must be `ok` (the database reachable); then an HTTP sign-in round trip (form
     POST → 303 with the cookie → the entry page 200), and the container's seed sha must
     equal the host clean build's.
- **Admission checks added in v2** (beside v1's): `suite_v2_contract`,
  `database_url_from_environment`, `dockerfile_copies_vendor`, `login_http_round_trip`,
  `data_model_contract`, `session_seed_cost` (p95 ≤ 300 ms over 20 sign-ins with
  `WTA_SESSION_CAP=4`, so most also evict; warn above 150 ms), `session_cap_honoured`,
  `responses_fast`, `login_protected`, `login_macro_signs_in` (lands on the seed sha),
  `login_wrong_password_refused`, `login_sign_out_sound`, `role_restrictions_sound`,
  `docker_sign_in_and_seed_state`, `variant_storage_released`.
